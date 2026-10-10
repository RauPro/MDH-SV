"""Private GitHub editorial inbox. Collection cannot write to the public monitor."""
from __future__ import annotations
import hashlib
import html
import ipaddress
import json
import logging
import os
import re
import socket
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urlunsplit, parse_qsl
from zoneinfo import ZoneInfo

import feedparser
import requests

FEEDS = [
    {'name': 'Diario Co Latino', 'url': 'https://www.diariocolatino.com/feed/', 'hosts': ['www.diariocolatino.com', 'diariocolatino.com']},
    {'name': 'GatoEncerrado', 'url': 'https://gatoencerrado.news/feed/', 'hosts': ['gatoencerrado.news']},
    {'name': 'Revista Factum', 'url': 'https://www.revistafactum.com/feed/', 'hosts': ['www.revistafactum.com', 'revistafactum.com']},
]
HOSTS = {h for feed in FEEDS for h in feed['hosts']}
KEYWORDS = ('derechos humanos', 'regimen de excepcion', 'detencion', 'detenido', 'detenida',
    'tortura', 'custodia', 'desaparecid', 'desaparicion', 'periodista', 'prensa', 'censura',
    'desalojo', 'defensor', 'feminicidio', 'violencia contra', 'libertad', 'cristosal',
    'socorro juridico', 'oudh', 'habeas corpus', 'carcel', 'reclusion', 'salud y descanso')
LABELS = {'review/pending': 'D97706', 'review/approved': '16815D', 'review/rejected': '6B7280'}
LOG = logging.getLogger(__name__)


def normalize(value):
    import unicodedata
    return ''.join(c for c in unicodedata.normalize('NFD', value.lower()) if not unicodedata.combining(c))


def canonical_url(value, allowed_hosts=HOSTS):
    if not isinstance(value, str) or any(ord(c) < 32 or c in '<>\\' for c in value):
        raise ValueError('Invalid source URL')
    parts = urlsplit(value)
    if parts.scheme != 'https' or parts.hostname not in allowed_hosts or parts.username or parts.password or parts.port:
        raise ValueError('Source URL outside approved HTTPS hosts')
    if any(not (key.startswith('utm_') or key in ('fbclid', 'gclid')) for key, _ in parse_qsl(parts.query)):
        raise ValueError('Unexpected source query parameters')
    if len(parts.path) < 2:
        raise ValueError('Article URL cannot be a homepage')
    return urlunsplit(('https', parts.hostname, parts.path, '', ''))


def require_public_dns(host):
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError('Source resolved to a non-public network')


def fetch_feed(feed, session=None):
    session = session or requests.Session()
    # No unrestricted redirects, network fallback in feedparser, or article-body scraping.
    url = feed['url']
    for _ in range(4):
        canonical_url(url, set(feed['hosts']))
        require_public_dns(urlsplit(url).hostname)
        with session.get(url, timeout=(8, 20), stream=True, allow_redirects=False,
                headers={'User-Agent': 'MDH-SV/2.1 public-news-review (+https://github.com/RauPro/MDH-SV)'}) as response:
            if response.is_redirect:
                from urllib.parse import urljoin
                url = urljoin(url, response.headers['Location'])
                continue
            response.raise_for_status()
            content = bytearray()
            for chunk in response.iter_content(65536):
                content.extend(chunk)
                if len(content) > 2_000_000: raise ValueError('Feed exceeds size limit')
            parsed = feedparser.parse(bytes(content))
            if not parsed.version or not parsed.entries:
                raise ValueError('Not a usable RSS/Atom feed')
            return parsed.entries[:40]
    raise ValueError('Too many feed redirects')


def candidate_from_entry(entry, feed, now=None):
    now = now or datetime.now(timezone.utc)
    url = canonical_url(entry.get('link', ''), set(feed['hosts']))
    title = html.unescape(re.sub(r'<[^>]*>', ' ', entry.get('title', '')))
    text = normalize(title + ' ' + html.unescape(re.sub(r'<[^>]*>', ' ', entry.get('summary', ''))))
    matched = [k for k in KEYWORDS if k in text]
    if not matched or not title.strip(): return None
    published = None
    if entry.get('published_parsed'):
        from calendar import timegm
        published_dt = datetime.fromtimestamp(timegm(entry['published_parsed']), timezone.utc)
        if (now - published_dt).days > 45 or (published_dt - now).total_seconds() > 86400:
            return None
        published = published_dt.date().isoformat()
    # Minimal quotation only. No article body, images, attachments or identity extraction.
    title = ' '.join(title.split()[:24])[:180]
    return {'id': hashlib.sha256(url.encode()).hexdigest(), 'source_url': url,
        'source_title': title, 'publisher': feed['name'], 'published_date': published,
        'discovered_at': now.isoformat(), 'matched_terms': matched,
        'status': 'pending', 'record_type': 'news_candidate'}


def issue_body(candidate):
    safe = json.dumps(candidate, ensure_ascii=False, indent=2).replace('`', '\\u0060').replace('<', '\\u003c').replace('@', '\\u0040')
    return (f"<!-- mdh-candidate:{candidate['id']} -->\n"
        f"## Noticia pendiente de revisión\n\nFuente: {candidate['publisher']}\n\n"
        f"Enlace original: <{candidate['source_url']}>\n\n"
        "La selección por palabras clave no determina que exista una violación. No se publica automáticamente.\n\n"
        "- [ ] Comprobar el texto original, alcance y jurisdicción.\n"
        "- [ ] Distinguir fecha de publicación y fecha del hecho.\n"
        "- [ ] Comprobar duplicados y actualizaciones de un caso existente.\n"
        "- [ ] Revisar atribución, minimización de datos y riesgo para personas.\n\n"
        "Usa Actions → Revisar noticia para aprobar una ficha preparada o rechazar la noticia. "
        "No pegues testimonios privados, credenciales ni documentos personales.\n\n"
        f"Si se ha generado un borrador asistido, está en `drafts/{candidate['id']}.json` "
        "dentro de este repositorio privado. Contrástalo y corrígelo antes de aprobar; "
        "deja `record_json` vacío para usarlo y escribe tus propias notas de revisión.\n\n"
        f"### Metadatos de captura\n```json\n{safe}\n```\n")


class GitHubInbox:
    def __init__(self, repository, token, session=None):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
            raise ValueError('Invalid review repository')
        self.repository = repository
        self.base = '/repos/' + repository
        self.session = session or requests.Session()
        self.session.headers.update({'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'})
        repo = self.call('GET', self.base)
        if repo.get('private') is not True or repo.get('full_name', '').lower() != repository.lower():
            raise ValueError('The review inbox MUST be a private repository')

    def call(self, method, path, **kwargs):
        response = self.session.request(method, 'https://api.github.com' + path, timeout=(8, 30), **kwargs)
        if response.status_code >= 400:
            raise RuntimeError(f'GitHub request failed ({response.status_code}); candidate content withheld')
        return response.json() if response.content else None

    def get_file(self, path):
        response = self.session.get("https://api.github.com" + path, timeout=(8, 30))
        if response.status_code == 404: return None
        response.raise_for_status()
        return response.json()

    def ensure_labels(self):
        existing = {label['name'] for label in self.call('GET', self.base + '/labels', params={'per_page': 100})}
        for name, color in LABELS.items():
            if name not in existing: self.call('POST', self.base + '/labels', json={'name': name, 'color': color})

    def existing_ids(self):
        ids = set()
        for page in range(1, 201):
            issues = self.call('GET', self.base + '/issues', params={'state': 'all', 'per_page': 100, 'page': page})
            for issue in issues:
                match = re.search(r'<!-- mdh-candidate:([0-9a-f]{64}) -->', issue.get('body') or '')
                if match: ids.add(match.group(1))
            if len(issues) < 100: return ids
        raise RuntimeError('Inbox pagination limit reached; refusing incomplete deduplication')

    def enqueue(self, candidate):
        title = re.sub(r'[@`<>\[\]]', '', candidate['source_title'])
        return self.call('POST', self.base + '/issues', json={'title': '[Revisar] ' + title,
            'body': issue_body(candidate), 'labels': ['review/pending']})


def collect(inbox, fetch=fetch_feed, feeds=FEEDS, max_new=20):
    inbox.ensure_labels()
    seen = inbox.existing_ids()
    results, added, failed, read = [], 0, 0, 0
    for feed in feeds:
        try:
            entries = fetch(feed)
            read += len(entries)
            count = 0
            for entry in entries:
                try: candidate = candidate_from_entry(entry, feed)
                except ValueError: continue
                if candidate and candidate['id'] not in seen and added < max_new:
                    inbox.enqueue(candidate)
                    seen.add(candidate['id'])
                    added += 1
                    count += 1
            results.append({'source': feed['name'], 'entries': len(entries), 'added': count, 'status': 'ok'})
        except Exception as error:
            failed += 1
            # URLs, feed titles and credentials never enter Action logs.
            results.append({'source': feed['name'], 'status': 'failed', 'error': type(error).__name__})
    report = {'collected_at': datetime.now(timezone.utc).isoformat(), 'read': read, 'added': added,
              'failed_sources': failed, 'sources': results, 'publications': 0}
    if failed == len(feeds): raise RuntimeError('All sources failed; no successful collection')
    return report


def private_draft_record(inbox, candidate):
    """Read only an explicitly unapproved draft from this already-verified private inbox."""
    import base64
    import binascii
    relative_path = 'drafts/' + candidate['id'] + '.json'
    response = inbox.get_file(inbox.base + '/contents/' + relative_path)
    if response is None:
        raise ValueError('No private draft exists; prepare a draft or provide record_json')
    limit = 512_000
    if (not isinstance(response, dict) or response.get('type') != 'file'
            or response.get('encoding') != 'base64' or response.get('path') != relative_path
            or type(response.get('size')) is not int or not 0 < response['size'] <= limit
            or not isinstance(response.get('content'), str) or len(response['content']) > limit * 2):
        raise ValueError('Malformed private draft file metadata')
    try:
        raw = base64.b64decode(''.join(response['content'].splitlines()), validate=True)
        if not raw or len(raw) > limit or len(raw) != response['size']:
            raise ValueError('Invalid private draft file size')
        draft = json.loads(raw.decode('utf-8'))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError('Malformed private draft content') from error
    if (not isinstance(draft, dict) or type(draft.get('schema_version')) is not int or draft['schema_version'] != 1
            or draft.get('candidate_id') != candidate['id']
            or draft.get('source_url') != candidate['source_url']):
        raise ValueError('Private draft identity does not match the candidate')
    if (draft.get('status') != 'draft' or draft.get('relevant') is not True
            or draft.get('publishable') is not False or draft.get('human_reviewed') is not False):
        raise ValueError('Private draft requires scope or privacy review before approval')
    classification = draft.get('classification')
    if (not isinstance(classification, dict) or classification.get('jurisdiction') != 'SV'
            or classification.get('relevance') != 'relevant'):
        raise ValueError('Private draft has unresolved jurisdiction or relevance')
    record = draft.get('record')
    if (not isinstance(record, dict) or record.get('source_url') != candidate['source_url']
            or record.get('origin_candidate_id') != candidate['id']
            or record.get('human_reviewed') is not False
            or record.get('review_status') != 'source_checked'
            or record.get('record_type') != classification.get('record_type')):
        raise ValueError('Private draft record is missing, mismatched or already marked reviewed')
    return record.copy()


def review(inbox, event):
    from .publication import Publication
    sender = event['sender']
    if sender.get('type') != 'User': raise ValueError('Approval requires a human GitHub account')
    username = sender['login']
    permission = inbox.call('GET', inbox.base + '/collaborators/' + username + '/permission')['permission']
    if permission not in ('write', 'maintain', 'admin'): raise ValueError('Reviewer requires write access')
    inputs = event['inputs']
    issue_number = int(inputs['issue_number'])
    if issue_number < 1: raise ValueError('Invalid issue number')
    issue = inbox.call('GET', inbox.base + f'/issues/{issue_number}')
    if 'pull_request' in issue: raise ValueError('Expected a news candidate issue')
    body = issue.get('body') or ''
    match = re.search(r'<!-- mdh-candidate:([0-9a-f]{64}) -->', body)
    if not match: raise ValueError('Issue is not a collected candidate')
    metadata = re.search(r'```json\n(.*?)\n```', body, re.S)
    if not metadata: raise ValueError('Candidate metadata is missing')
    candidate = json.loads(metadata.group(1))
    if (not isinstance(candidate, dict) or candidate.get('id') != match.group(1)
            or hashlib.sha256(canonical_url(candidate.get('source_url')).encode()).hexdigest() != match.group(1)):
        raise ValueError('Candidate identity mismatch')
    decision = inputs['decision']
    if decision == 'reject':
        inbox.call('PATCH', inbox.base + f'/issues/{issue_number}', json={'state':'closed', 'labels':['review/rejected']})
        return {'decision':'rejected','issue':issue_number}
    if decision != 'approve' or str(inputs.get('privacy_reviewed', '')).lower() != 'true':
        raise ValueError('Explicit editorial and privacy approval required')
    manual_json = inputs.get('record_json') or ''
    if not isinstance(manual_json, str): raise ValueError('record_json must be JSON text')
    manual_json = manual_json.strip()
    notes = inputs.get('review_notes') or ''
    if not isinstance(notes, str): raise ValueError('Review notes must be text')
    notes = notes.strip()
    if not manual_json and len(notes) < 20:
        raise ValueError('Draft approval requires human review_notes of at least 20 characters')
    proposed = json.loads(manual_json) if manual_json else private_draft_record(inbox, candidate)
    if not isinstance(proposed, dict): raise ValueError('Public draft must be a record object')
    # Compatibility for the existing manual workflow; AI draft notes can never satisfy approval.
    if manual_json and not notes:
        notes = proposed.get('review_notes') or ''
        if not isinstance(notes, str): raise ValueError('Review notes must be text')
        notes = notes.strip()
    if len(notes) < 20: raise ValueError('Approval requires human review_notes of at least 20 characters')
    if proposed.get('source_url') != candidate['source_url']:
        raise ValueError('Public draft must refer to the collected source')
    today = datetime.now(ZoneInfo('America/El_Salvador')).date().isoformat()
    proposed.update(human_reviewed=True, review_status='editorially_reviewed', reviewed_by=username,
        reviewed_at=today, review_notes=notes, origin_candidate_id=candidate['id'], source_checked_at=today,
        source_check_method=f'Revisión editorial de la fuente por {username}; no implica corroboración independiente de los hechos.')
    try:
        record = Publication.model_validate(proposed)
    except ValueError:
        # Validation errors can otherwise include the entire private draft in Action logs.
        raise ValueError('Public draft failed publication validation; inspect the private draft and schema') from None
    approval = {'schema_version': 1, 'record': record.model_dump(mode='json'),
        'review_repository': inbox.repository, 'review_issue': issue_number,
        'review_run_id': os.environ.get('GITHUB_RUN_ID'), 'approved_at': datetime.now(timezone.utc).isoformat()}
    from pathlib import Path
    # Only public fields appear in the proposal; private issue body is never exported.
    output = Path('proposal.json')
    output.write_text(json.dumps(approval, ensure_ascii=False, indent=2), encoding='utf-8')
    # Idempotent audit file in the private repository; only the human workflow can write it.
    import base64
    path = inbox.base + '/contents/approved/' + candidate['id'] + '.json'
    existing = inbox.get_file(path)
    payload = {'message': f'Approve candidate {candidate["id"][:12]} for editorial publication',
        'content': base64.b64encode(output.read_bytes()).decode()}
    if existing: payload['sha'] = existing['sha']
    inbox.call('PUT', path, json=payload)
    inbox.call('PATCH', inbox.base + f'/issues/{issue_number}', json={'labels':['review/approved']})
    return {'decision':'approved_for_proposal','issue':issue_number,'publications':0}


def main():
    import sys
    from pathlib import Path
    inbox = GitHubInbox(os.environ['GITHUB_REPOSITORY'], os.environ['GH_TOKEN'])
    if sys.argv[1:] == ['review']:
        report = review(inbox, json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text()))
    else:
        report = collect(inbox)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
            f.write('## MDH-SV · revisión privada\n\n```json\n' + json.dumps(report, indent=2) + '\n```\n')

if __name__ == '__main__': main()
