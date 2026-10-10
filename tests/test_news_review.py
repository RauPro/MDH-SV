"""Partial news dossiers require explicit human approval without becoming cases."""
import base64
import copy
import hashlib
import json
from pathlib import Path

import pytest

from mdh_pipeline.news_dossier import empty_news_dossier
from mdh_pipeline.news_publication import export_news_dossiers, validate_collection
from mdh_pipeline.review_import import import_approved_news
from mdh_pipeline.review_queue import issue_body, review


URL = 'https://gatoencerrado.news/2026/09/01/noticia-de-contexto/'
ID = hashlib.sha256(URL.encode()).hexdigest()
CANDIDATE = {'id': ID, 'source_url': URL, 'source_title': 'Una noticia de contexto',
             'publisher': 'GatoEncerrado', 'published_date': '2026-09-01', 'status': 'pending',
             'matched_terms': ['derechos humanos']}


def processed():
    dossier = empty_news_dossier(CANDIDATE)
    dossier['extraction_status'] = 'processed'
    dossier['classification'].update(jurisdiction='other', relevance='not_relevant', record_type='publication')
    for field in dossier['fields'].values():
        field.update(status='not_reported', note='Dato no mencionado en el texto consultado.')
    return dossier


def file_response(path, document):
    raw = json.dumps(document).encode()
    return {'type': 'file', 'path': path, 'encoding': 'base64', 'size': len(raw),
            'content': base64.b64encode(raw).decode(), 'sha': 'a' * 40}


class Inbox:
    repository = 'RauPro/MDH-SV-review'
    base = '/repos/' + repository

    def __init__(self, dossier=None):
        self.writes = []
        self.files = {}
        self.labels = ['review/pending']
        self.envelope = {'schema_version': 1, 'candidate_id': ID, 'source_url': URL,
                         'status': 'needs_scope_review', 'publishable': False, 'human_reviewed': False,
                         'record': None, 'relevant': False, 'news_dossier': dossier or processed(),
                         'grounding': {'private_marker': 'DO_NOT_EXPORT_PRIVATE_ENVELOPE'}}
        path = 'drafts/' + ID + '.json'
        self.files[path] = file_response(path, self.envelope)

    def get_file(self, path):
        return self.files.get(path.split('/contents/')[-1])

    def call(self, method, path, **kwargs):
        if method == 'GET' and path.endswith('/permission'):
            return {'permission': 'admin'}
        if method == 'GET' and '/contents/' in path:
            return self.files[path.split('/contents/')[-1]]
        if method == 'GET':
            return {'body': issue_body(CANDIDATE), 'labels': [{'name': label} for label in self.labels]}
        self.writes.append((method, path, kwargs))
        if method == 'PUT':
            relative = path.split('/contents/')[-1]
            value = json.loads(base64.b64decode(kwargs['json']['content']))
            self.files[relative] = file_response(relative, value)
        if method == 'PATCH':
            self.labels = kwargs['json']['labels']
        return {}


def event():
    return {'sender': {'type': 'User', 'login': 'RauPro'}, 'inputs': {
        'issue_number': '1', 'decision': 'approve', 'proposal_kind': 'news_dossier',
        'privacy_reviewed': 'true', 'record_json': '',
        'review_notes': 'He leído la fuente y contrastado la pertinencia, los datos ausentes y la privacidad.'}}


def test_human_can_approve_partial_international_news_without_creating_a_case(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    inbox = Inbox()
    result = review(inbox, event())
    assert result['publications'] == 0
    proposal = json.loads(Path('proposal.json').read_text())
    assert proposal['proposal_kind'] == 'news_dossier' and 'record' not in proposal
    dossier = proposal['news_dossier']
    assert dossier['human_reviewed'] is True and dossier['reviewed_by'] == 'RauPro'
    assert dossier['fields']['alleged_violation']['value'] is None
    assert dossier['fields']['alleged_violation']['status'] == 'not_reported'
    assert '/contents/approved-news/' in inbox.writes[0][1]
    assert 'DO_NOT_EXPORT' not in Path('proposal.json').read_text()


@pytest.mark.parametrize('extraction_status', ['pending', 'source_unavailable', 'error'])
def test_unread_or_failed_source_is_not_approved_as_read(extraction_status, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    dossier = empty_news_dossier(CANDIDATE, extraction_status=extraction_status)
    inbox = Inbox(dossier)
    with pytest.raises(ValueError):
        review(inbox, event())
    assert inbox.writes == [] and not Path('proposal.json').exists()


@pytest.mark.parametrize('mutation', [
    lambda e: e['inputs'].update(privacy_reviewed='false'),
    lambda e: e['inputs'].update(review_notes=''),
    lambda e: e['sender'].update(type='Bot'),
])
def test_news_approval_needs_a_human_and_explicit_privacy_and_notes(mutation, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    inbox = Inbox(); ev = event(); mutation(ev)
    with pytest.raises(ValueError):
        review(inbox, ev)
    assert inbox.writes == [] and not Path('proposal.json').exists()


def test_private_dossiers_cannot_be_exported_even_when_fields_are_complete(tmp_path):
    with pytest.raises(ValueError):
        validate_collection({'schema_version': 1, 'dossiers': [processed()]})
    assert not list(tmp_path.iterdir())


def test_approved_news_import_is_idempotent_and_keeps_the_case_catalog_untouched(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    inbox = Inbox(); review(inbox, event())
    source = tmp_path / 'news-dossiers.json'
    source.write_text(json.dumps({'schema_version': 1, 'dossiers': []}))
    archive = tmp_path / 'news.json'
    archive.write_text(json.dumps({'records': [{**CANDIDATE, 'title': CANDIDATE['source_title']}]}))
    public = tmp_path / 'public-dossiers.json'
    assert import_approved_news(inbox, ID, source, archive=archive, destination=public)
    assert not import_approved_news(inbox, ID, source, archive=archive, destination=public)
    assert json.loads(public.read_text()) == json.loads(source.read_text())
    assert 'DO_NOT_EXPORT' not in public.read_text()
    inbox.labels = ['review/rejected']
    before = source.read_bytes()
    with pytest.raises(ValueError):
        import_approved_news(inbox, ID, source, archive=archive, destination=public)
    assert source.read_bytes() == before


def test_mismatched_archive_prevents_export_and_does_not_overwrite_public_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    inbox = Inbox(); review(inbox, event())
    dossier = json.loads(Path('proposal.json').read_text())['news_dossier']
    source = tmp_path / 'approved.json'
    source.write_text(json.dumps({'schema_version': 1, 'dossiers': [dossier]}))
    archive = tmp_path / 'archive.json'; archive.write_text('{"records":[]}')
    output = tmp_path / 'public.json'; output.write_text('previous')
    with pytest.raises(ValueError):
        export_news_dossiers(source, output, archive)
    assert output.read_text() == 'previous'


@pytest.mark.parametrize('change', [
    lambda f: f.update(type='dir'),
    lambda f: f.update(path='approved-news/wrong.json'),
    lambda f: f.update(size=1),
    lambda f: f.update(content='malformed'),
    lambda f: f.update(encoding='none', download_url='https://unapproved.example/data'),
])
def test_import_rejects_malformed_private_approval_files(change, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    inbox = Inbox(); review(inbox, event())
    file = copy.deepcopy(inbox.files['approved-news/' + ID + '.json']); change(file)
    inbox.files['approved-news/' + ID + '.json'] = file
    with pytest.raises(ValueError):
        import_approved_news(inbox, ID, tmp_path / 'not-created.json', archive=tmp_path / 'no-archive.json')
    assert not (tmp_path / 'not-created.json').exists()
