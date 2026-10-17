"""Import an explicitly approved proposal; never bypasses private review or pushes Git."""
import argparse
import base64
import binascii
import json
import os
import re
import subprocess
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .file_outputs import write_outputs
from .instance import load_instance
from .news_publication import ARCHIVE as NEWS_ARCHIVE
from .news_publication import PUBLIC as NEWS_PUBLIC
from .news_publication import ROOT
from .news_publication import SOURCE as NEWS_SOURCE
from .publication import (
    CATALOG,
    CATALOG_EXPORT_NAMES,
    PUBLIC,
    Catalog,
    Publication,
    build_catalog_exports,
)
from .review_queue import GitHubInbox

NEWS_SELECTION = ROOT / "data/selection/news.json"
HISTORY_SOURCE = ROOT / "data/history"


def github_token():
    if os.environ.get('GH_TOKEN'): return os.environ['GH_TOKEN']
    credential = subprocess.run(['git','credential','fill'], input='protocol=https\nhost=github.com\n\n',
        text=True, capture_output=True, check=True, env={**os.environ,'GIT_TERMINAL_PROMPT':'0'})
    fields = dict(line.split('=',1) for line in credential.stdout.splitlines() if '=' in line)
    return fields['password']


def _snapshot(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Import paths cannot be symlinks')
    return path.read_bytes() if path.exists() else None


def import_approved(inbox, candidate_id, source=CATALOG, export=True, *, destination=PUBLIC):
    if not re.fullmatch(r'[a-f0-9]{64}', candidate_id): raise ValueError('Invalid candidate ID')
    file = inbox.call('GET', inbox.base + '/contents/approved/' + candidate_id + '.json')
    proposal = json.loads(base64.b64decode(file['content'], validate=False))
    if proposal['review_repository'] != inbox.repository: raise ValueError('Wrong review repository')
    record = Publication.model_validate(proposal['record'])
    if not record.human_reviewed or record.origin_candidate_id != candidate_id:
        raise ValueError('Missing human approval')
    issue = inbox.call('GET', inbox.base + '/issues/' + str(int(proposal['review_issue'])))
    labels = {label['name'] for label in issue['labels']}
    if 'review/approved' not in labels or 'review/rejected' in labels:
        raise ValueError('Approval withdrawn or missing')
    if f'<!-- mdh-candidate:{candidate_id} -->' not in (issue.get('body') or ''):
        raise ValueError('Review issue mismatch')
    source, destination = Path(source), Path(destination)
    source_bytes = _snapshot(source)
    catalog = Catalog.model_validate_json(source_bytes)
    expected = {source: source_bytes}
    if export:
        expected.update({destination / name: _snapshot(destination / name)
                         for name in CATALOG_EXPORT_NAMES})
    existing = next((r for r in catalog.records if r.id == record.id or r.source_record_key == record.source_record_key), None)
    if existing is not None:
        if existing != record:
            raise ValueError('Conflicting existing record; review the update explicitly')
        # Keep the exact source bytes and review date when repairing an earlier
        # import whose exports were not completed.
        prepared_source = source_bytes
    else:
        catalog.records.append(record)
        catalog.updated_at = datetime.now(ZoneInfo('America/El_Salvador')).date()
        Catalog.model_validate(catalog.model_dump())
        prepared_source = (catalog.model_dump_json(indent=2) + '\n').encode('utf-8')
    outputs = []
    if export:
        _, files = build_catalog_exports(prepared_source)
        outputs.extend((destination / name, content) for name, content in files.items())
    # Validate and render every projection first; the authority is installed last.
    outputs.append((source, prepared_source))
    return write_outputs(outputs, expected=expected)


def _json_document(value):
    return json.loads(Path(value).read_text()) if isinstance(value, (str, Path)) else value


def _json_bytes(document):
    return (json.dumps(document, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def _write_news_outputs(outputs, *, expected=None):
    """Write JSON projections and already-validated source bytes with one writer."""
    encoded = [(Path(path), document if isinstance(document, bytes) else _json_bytes(document))
               for path, document in outputs]
    return write_outputs(encoded, expected=expected)


def _preserve_generated_at(document, previous_bytes):
    """A healthy retry must not refresh a projection solely because time passed."""
    from .news_selection import _timestamp
    if previous_bytes is None:
        return
    try:
        previous = json.loads(previous_bytes)
        _timestamp(previous['generated_at'])
        if ({key: value for key, value in previous.items() if key != 'generated_at'}
                == {key: value for key, value in document.items() if key != 'generated_at'}):
            document['generated_at'] = previous['generated_at']
    except (ValueError, TypeError, KeyError, AttributeError):
        # A malformed output can be rebuilt when the validated inputs are available.
        pass


def import_approved_news(inbox, candidate_id, source=NEWS_SOURCE, *, archive=NEWS_ARCHIVE,
                         destination=NEWS_PUBLIC, export=True, candidates=None,
                         selection=NEWS_SELECTION, locations=None, history_folder=HISTORY_SOURCE):
    """Import a human decision against all candidates and regenerate selected public snapshots."""
    from .automatic_locations import validate_collection as validate_locations
    from .history import export_archive
    from .news_dossier import validate_news_dossier
    from .news_publication import validate_collection
    from .news_selection import select_archive
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
    source, archive, destination = Path(source), Path(archive), Path(destination)
    source_bytes = _snapshot(source)
    archive_bytes = _snapshot(archive)
    expected = {source: source_bytes, archive: archive_bytes}
    if export:
        locations = Path(locations) if locations is not None else archive.with_name('locations.json')
        expected.update({destination: _snapshot(destination), locations: _snapshot(locations)})
    if candidates is None:
        # The public archive intentionally excludes candidates with no positive scope
        # decision. Human review must still be able to admit or reject those candidates.
        current = json.loads(archive_bytes)
        start = date.fromisoformat(current['coverage']['start_date'])
        end = date.fromisoformat(current['coverage']['end_date'])
        bibliography = export_archive(Path(history_folder), Path(archive), start, end, write=False)
    else:
        bibliography = _json_document(candidates)
    document = validate_collection(json.loads(source_bytes), archive=bibliography)
    existing = next((item for item in document['dossiers'] if item['candidate_id'] == candidate_id), None)
    if existing is not None:
        if existing != dossier:
            raise ValueError('Conflicting news dossier; review the update explicitly')
        prepared_source = source_bytes
    else:
        document['dossiers'].append(dossier)
        document = validate_collection(document, archive=bibliography)
        prepared_source = _json_bytes(document)
    outputs = []
    if export:
        selected = select_archive(bibliography, _json_document(selection), reviewed_dossiers=document)
        _preserve_generated_at(selected, archive_bytes)
        eligible = {'schema_version': 1, 'dossiers': [item for item in document['dossiers']
            if item['classification']['jurisdiction'] == 'SV' and item['classification']['relevance'] == 'relevant']}
        public_dossiers = validate_collection(eligible, archive=selected)
        previous_locations = (json.loads(expected[locations]) if expected[locations] is not None else
            {'schema_version': 3, 'generated_at': bibliography['generated_at'], 'locations': []})
        # Validate against the complete bibliography before pruning. A malformed or
        # unrelated entry must fail, not silently disappear during an approval import.
        projected = validate_locations(previous_locations, archive=bibliography)
        selected_ids = {reference['id'] for reference in selected['records']}
        reviewed_ids = {item['candidate_id'] for item in document['dossiers']}
        projected['locations'] = [item for item in projected['locations']
            if item['candidate_id'] in selected_ids and item['candidate_id'] not in reviewed_ids]
        projected = validate_locations(projected, archive=selected)
        outputs.extend([(archive, selected), (destination, public_dossiers), (locations, projected)])
    # All sources, scope decisions, dossiers and remaining locations are validated
    # before any destination changes. Keep the authoritative approved source last.
    outputs.append((source, prepared_source))
    return _write_news_outputs(outputs, expected=expected)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--repository', default=load_instance().private_repository)
    parser.add_argument('--kind', choices=('case', 'news_dossier'), default='case')
    args=parser.parse_args()
    try:
        importer = import_approved_news if args.kind == 'news_dossier' else import_approved
        changed=importer(GitHubInbox(args.repository,github_token()),args.candidate)
    except (ValueError, TypeError, KeyError, OSError):
        raise SystemExit('No se completó la importación: revise identidad, aprobación y validación; '
                         'si el fallo es temporal, repita el mismo comando.') from None
    print('Ficha incorporada o exportación reparada localmente. Revise el diff y CI antes de publicar.'
          if changed else 'La ficha y sus exportaciones ya están actualizadas; sin cambios.')

if __name__=='__main__':main()
