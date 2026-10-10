"""Import an explicitly approved proposal; never bypasses private review or pushes Git."""
import argparse
import base64
import json
import os
import re
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from .publication import CATALOG, Catalog, Publication, export_catalog
from .review_queue import GitHubInbox


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


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--repository', default='RauPro/MDH-SV-review')
    args=parser.parse_args()
    changed=import_approved(GitHubInbox(args.repository,github_token()),args.candidate)
    print('Ficha incorporada localmente. Revise el diff y CI antes de publicar.' if changed else 'La ficha ya está incorporada; sin cambios.')

if __name__=='__main__':main()
