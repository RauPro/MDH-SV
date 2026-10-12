"""Import an explicitly approved proposal; never bypasses private review or pushes Git."""
import argparse
import base64
import binascii
import json
import os
import re
import subprocess
import tempfile
from datetime import date, datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from .publication import CATALOG, Catalog, Publication, export_catalog
from .review_queue import GitHubInbox
from .news_publication import ROOT, SOURCE as NEWS_SOURCE, PUBLIC as NEWS_PUBLIC, ARCHIVE as NEWS_ARCHIVE

NEWS_SELECTION = ROOT / "data/selection/news.json"
HISTORY_SOURCE = ROOT / "data/history"


def github_token():
    if os.environ.get('GH_TOKEN'): return os.environ['GH_TOKEN']
    credential = subprocess.run(['git','credential','fill'], input='protocol=https\nhost=github.com\n\n',
        text=True, capture_output=True, check=True, env={**os.environ,'GIT_TERMINAL_PROMPT':'0'})
    fields = dict(line.split('=',1) for line in credential.stdout.splitlines() if '=' in line)
    return fields['password']


def import_approved(inbox, candidate_id, source=CATALOG, export=True):
    if not re.fullmatch(r'[a-f0-9]{64}', candidate_id): raise ValueError('Invalid candidate ID')
    file = inbox.call('GET', inbox.base + '/contents/approved/' + candidate_id + '.json')
    proposal = json.loads(base64.b64decode(file['content'], validate=False))
    if proposal['review_repository'] != inbox.repository: raise ValueError('Wrong review repository')
    record = Publication.model_validate(proposal['record'])
    if not record.human_reviewed or record.origin_candidate_id != candidate_id:
        raise ValueError('Missing human approval')
    issue = inbox.call('GET', inbox.base + '/issues/' + str(int(proposal['review_issue'])))
    if 'review/approved' not in {label['name'] for label in issue['labels']}:
        raise ValueError('Approval withdrawn or missing')
    if f'<!-- mdh-candidate:{candidate_id} -->' not in (issue.get('body') or ''):
        raise ValueError('Review issue mismatch')
    catalog = Catalog.model_validate_json(Path(source).read_text())
    existing = next((r for r in catalog.records if r.id == record.id or r.source_record_key == record.source_record_key), None)
    if existing:
        if existing == record: return False
        raise ValueError('Conflicting existing record; review the update explicitly')
    catalog.records.append(record)
    catalog.updated_at = datetime.now(ZoneInfo('America/El_Salvador')).date()
    Catalog.model_validate(catalog.model_dump())
    Path(source).write_text(catalog.model_dump_json(indent=2)+'\n', encoding='utf-8')
    if export: export_catalog(source=source)
    return True


def _json_document(value):
    return json.loads(Path(value).read_text()) if isinstance(value, (str, Path)) else value


def _write_news_outputs(outputs):
    """Stage every validated document, then replace files atomically with rollback on IO failure."""
    destinations = [Path(path) for path, _ in outputs]
    if len({path.resolve() for path in destinations}) != len(destinations):
        raise ValueError('News import destinations must be distinct')
    encoded = [(Path(path), (json.dumps(document, ensure_ascii=False, indent=2) + '\n').encode())
               for path, document in outputs]
    staged, replaced = [], []
    try:
        for target, content in encoded:
            if target.is_symlink():
                raise ValueError('News import destinations cannot be symlinks')
            target.parent.mkdir(parents=True, exist_ok=True)
            old = target.read_bytes() if target.exists() else None
            mode = target.stat().st_mode & 0o777 if target.exists() else 0o644
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.' + target.name + '.', delete=False) as stream:
                temporary = Path(stream.name)
                staged.append((target, temporary, old, mode))
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.chmod(mode)
        for target, temporary, old, mode in staged:
            temporary.replace(target)
            replaced.append((target, old, mode))
    except OSError:
        for target, old, mode in reversed(replaced):
            if old is None:
                target.unlink(missing_ok=True)
            else:
                with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.' + target.name + '.restore.', delete=False) as stream:
                    backup = Path(stream.name)
                    stream.write(old)
                backup.chmod(mode)
                backup.replace(target)
        raise
    finally:
        for _, temporary, _, _ in staged:
            temporary.unlink(missing_ok=True)


def import_approved_news(inbox, candidate_id, source=NEWS_SOURCE, *, archive=NEWS_ARCHIVE,
                         destination=NEWS_PUBLIC, export=True, candidates=None,
                         selection=NEWS_SELECTION, locations=None, history_folder=HISTORY_SOURCE):
    """Import a human decision against all candidates and regenerate selected public snapshots."""
    from .news_dossier import validate_news_dossier
    from .news_publication import validate_collection
    from .news_selection import select_archive
    from .automatic_locations import validate_collection as validate_locations
    from .history import export_archive
    if not re.fullmatch(r'[a-f0-9]{64}', candidate_id):
        raise ValueError('Invalid candidate ID')
    relative = 'approved-news/' + candidate_id + '.json'
    file = inbox.call('GET', inbox.base + '/contents/' + relative)
    limit = 512_000
    if (not isinstance(file, dict) or file.get('type') != 'file' or file.get('path') != relative
            or file.get('encoding') != 'base64' or type(file.get('size')) is not int
            or not 0 < file['size'] <= limit or not isinstance(file.get('content'), str)
            or len(file['content']) > limit * 2):
        raise ValueError('Invalid private approval file metadata')
    try:
        raw = base64.b64decode(''.join(file['content'].splitlines()), validate=True)
        if len(raw) != file['size']:
            raise ValueError('Invalid private approval file size')
        proposal = json.loads(raw.decode('utf-8'))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError('Invalid private approval file') from None
    if (not isinstance(proposal, dict) or type(proposal.get('schema_version')) is not int
            or proposal['schema_version'] != 1 or proposal.get('proposal_kind') != 'news_dossier'
            or proposal.get('review_repository') != inbox.repository
            or type(proposal.get('review_issue')) is not int or proposal['review_issue'] < 1):
        raise ValueError('Invalid news approval provenance')
    dossier = validate_news_dossier(proposal.get('news_dossier'), require_reviewed=True)
    if dossier['candidate_id'] != candidate_id:
        raise ValueError('News approval identity mismatch')
    issue = inbox.call('GET', inbox.base + '/issues/' + str(proposal['review_issue']))
    labels = {label['name'] for label in issue['labels']}
    if ('review/approved' not in labels or 'review/rejected' in labels
            or f'<!-- mdh-candidate:{candidate_id} -->' not in (issue.get('body') or '')):
        raise ValueError('Approval withdrawn, missing or mismatched')
    if candidates is None:
        # The public archive intentionally excludes candidates with no positive scope
        # decision. Human review must still be able to admit or reject those candidates.
        current = _json_document(archive)
        start = date.fromisoformat(current['coverage']['start_date'])
        end = date.fromisoformat(current['coverage']['end_date'])
        bibliography = export_archive(Path(history_folder), Path(archive), start, end, write=False)
    else:
        bibliography = _json_document(candidates)
    document = validate_collection(_json_document(source), archive=bibliography)
    existing = next((item for item in document['dossiers'] if item['candidate_id'] == candidate_id), None)
    if existing is not None:
        if existing == dossier:
            return False
        raise ValueError('Conflicting news dossier; review the update explicitly')
    document['dossiers'].append(dossier)
    document = validate_collection(document, archive=bibliography)
    outputs = []
    if export:
        selected = select_archive(bibliography, _json_document(selection), reviewed_dossiers=document)
        eligible = {'schema_version': 1, 'dossiers': [item for item in document['dossiers']
            if item['classification']['jurisdiction'] == 'SV' and item['classification']['relevance'] == 'relevant']}
        public_dossiers = validate_collection(eligible, archive=selected)
        locations = Path(locations) if locations is not None else Path(archive).with_name('locations.json')
        previous_locations = (_json_document(locations) if locations.exists() else
            {'schema_version': 3, 'generated_at': bibliography['generated_at'], 'locations': []})
        # Validate against the complete bibliography before pruning. A malformed or
        # unrelated entry must fail, not silently disappear during an approval import.
        projected = validate_locations(previous_locations, archive=bibliography)
        selected_ids = {reference['id'] for reference in selected['records']}
        reviewed_ids = {item['candidate_id'] for item in document['dossiers']}
        projected['locations'] = [item for item in projected['locations']
            if item['candidate_id'] in selected_ids and item['candidate_id'] not in reviewed_ids]
        projected = validate_locations(projected, archive=selected)
        outputs.extend([(Path(archive), selected), (Path(destination), public_dossiers), (locations, projected)])
    # All sources, scope decisions, dossiers and remaining locations are validated
    # before any destination changes. Keep the authoritative approved source last.
    outputs.append((Path(source), document))
    _write_news_outputs(outputs)
    return True


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--repository', default='RauPro/MDH-SV-review')
    parser.add_argument('--kind', choices=('case', 'news_dossier'), default='case')
    args=parser.parse_args()
    try:
        importer = import_approved_news if args.kind == 'news_dossier' else import_approved
        changed=importer(GitHubInbox(args.repository,github_token()),args.candidate)
    except (ValueError, TypeError, KeyError, OSError):
        raise SystemExit('No se importó la propuesta: revise su identidad, aprobación y validación en la cola privada.') from None
    print('Ficha incorporada localmente. Revise el diff y CI antes de publicar.' if changed else 'La ficha ya está incorporada; sin cambios.')

if __name__=='__main__':main()
