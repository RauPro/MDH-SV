"""Partial news dossiers require explicit human approval without becoming cases."""
import base64
import copy
import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from mdh_pipeline.history import SOURCES, export_archive, reference_from_post
from mdh_pipeline.news_selection import FULL_SCOPE_PROMPT, select_archive
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
    bibliography, _ = candidate_archive(tmp_path)
    manifest = selection_manifest([])
    archive = tmp_path / 'news.json'
    archive.write_text(json.dumps(select_archive(bibliography, manifest)))
    public = tmp_path / 'public-dossiers.json'
    options = dict(archive=archive, destination=public, candidates=bibliography, selection=manifest)
    assert import_approved_news(inbox, ID, source, **options)
    assert not import_approved_news(inbox, ID, source, **options)
    assert len(json.loads(source.read_text())["dossiers"]) == 1
    assert json.loads(public.read_text()) == {"schema_version": 1, "dossiers": []}
    assert 'DO_NOT_EXPORT' not in public.read_text()
    inbox.labels = ['review/rejected']
    before = source.read_bytes()
    with pytest.raises(ValueError):
        import_approved_news(inbox, ID, source, **options)
    assert source.read_bytes() == before


def test_mismatched_archive_prevents_export_and_does_not_overwrite_public_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    inbox = Inbox(); review(inbox, event())
    dossier = json.loads(Path('proposal.json').read_text())['news_dossier']
    dossier['classification'].update(jurisdiction='SV', relevance='relevant')
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


STAMP = '2026-10-12T12:00:00+00:00'
GATO = next(source for source in SOURCES if source['id'] == 'gatoencerrado')


def candidate_reference(number=1):
    return reference_from_post({'id': number, 'date': '2026-09-01T12:00:00',
        'link': URL if number == 1 else f'https://gatoencerrado.news/2026/09/01/noticia-de-contexto-{number}/',
        'title': {'rendered': 'Derechos humanos en El Salvador'}}, GATO,
        date(2026, 9, 1), date(2026, 9, 30), STAMP)


def candidate_archive(tmp_path, references=None):
    references = references or [candidate_reference()]
    folder = tmp_path / 'history'
    folder.mkdir(exist_ok=True)
    (folder / 'gatoencerrado.json').write_text(json.dumps({'records': references, 'months': {}}))
    document = export_archive(folder, tmp_path / 'unused.json', date(2026, 9, 1), date(2026, 9, 30),
                              generated_at=STAMP, write=False)
    assert not (tmp_path / 'unused.json').exists()
    return document, folder


def selection_manifest(references):
    return {'schema_version': 1, 'generated_at': STAMP, 'selections': [
        {'candidate_id': reference['id'], **{key: reference[key] for key in ('source_url', 'publisher', 'published_date')},
         'selection': {'method': 'gemini_grounded_scope', 'review_status': 'automated_unreviewed',
            'human_reviewed': False, 'jurisdiction': 'SV', 'relevance': 'relevant', 'classified_at': STAMP,
            'model': 'gemini-3.5-flash-lite', 'prompt_version': FULL_SCOPE_PROMPT,
            'evidence': {'content_sha256': 'a' * 64, 'jurisdiction_paragraph_ids': ['p1'], 'relevance_paragraph_ids': ['p2']}}}
        for reference in references]}


def human_dossier(reference, relevant=True):
    dossier = empty_news_dossier(reference, extraction_status='processed')
    dossier.update(human_reviewed=True, reviewed_by='editor', reviewed_at='2026-10-12',
                   review_notes='Fixture: revisé el contenido, la pertinencia y los datos protegidos.')
    dossier['classification'].update(jurisdiction='SV', relevance='relevant' if relevant else 'not_relevant')
    return dossier


class ApprovedInbox(Inbox):
    def __init__(self, dossier):
        super().__init__()
        self.identifier = dossier['candidate_id']
        self.labels = ['review/approved']
        path = 'approved-news/' + self.identifier + '.json'
        self.files[path] = file_response(path, {'schema_version': 1, 'proposal_kind': 'news_dossier',
            'review_repository': self.repository, 'review_issue': 1, 'news_dossier': dossier})

    def call(self, method, path, **kwargs):
        if method == 'GET' and '/issues/' in path:
            return {'body': f'<!-- mdh-candidate:{self.identifier} -->',
                    'labels': [{'name': label} for label in self.labels]}
        return super().call(method, path, **kwargs)


def import_paths(tmp_path, bibliography, manifest, approved=None):
    source = tmp_path / 'approved.json'
    source.write_text(json.dumps(approved or {'schema_version': 1, 'dossiers': []}))
    archive = tmp_path / 'selected.json'
    archive.write_text(json.dumps(select_archive(bibliography, manifest, reviewed_dossiers=approved)))
    public = tmp_path / 'public-dossiers.json'
    public.write_text(json.dumps({'schema_version': 1, 'dossiers': []}))
    locations = tmp_path / 'locations.json'
    locations.write_text(json.dumps({'schema_version': 3, 'generated_at': STAMP, 'locations': []}))
    return source, dict(archive=archive, destination=public, candidates=bibliography,
                        selection=manifest, locations=locations)


def test_positive_human_approval_can_admit_a_candidate_excluded_by_ai(tmp_path):
    bibliography, history_folder = candidate_archive(tmp_path)
    manifest = selection_manifest([])
    source, options = import_paths(tmp_path, bibliography, manifest)
    assert json.loads(options['archive'].read_text())['records'] == []
    # Exercise the production default: rebuild all candidates from history without
    # writing the unselected candidate archive to the public destination.
    options.pop('candidates')
    options['history_folder'] = history_folder
    dossier = human_dossier(candidate_reference())
    inbox = ApprovedInbox(dossier)
    assert import_approved_news(inbox, ID, source, **options)
    selected = json.loads(options['archive'].read_text())
    assert selected['policy']['selection_gate']['selected'] == 1
    assert selected['records'][0]['selection'] == {
        'method': 'editorial_scope', 'review_status': 'editorially_reviewed', 'human_reviewed': True,
        'jurisdiction': 'SV', 'relevance': 'relevant', 'reviewed_by': 'editor', 'reviewed_at': '2026-10-12'}
    assert json.loads(options['destination'].read_text())['dossiers'] == [dossier]
    assert json.loads(source.read_text())['dossiers'] == [dossier]
    snapshots = {path: path.read_bytes() for path in (source, options['archive'], options['destination'], options['locations'])}
    assert not import_approved_news(inbox, ID, source, **options)
    assert all(path.read_bytes() == content for path, content in snapshots.items())
    assert inbox.writes == []


def test_retained_negative_decision_does_not_block_later_positive_import(tmp_path):
    first, second = candidate_reference(), candidate_reference(2)
    bibliography, _ = candidate_archive(tmp_path, [first, second])
    rejected = human_dossier(first, relevant=False)
    manifest = selection_manifest([first])
    source, options = import_paths(tmp_path, bibliography, manifest,
        approved={'schema_version': 1, 'dossiers': [rejected]})
    accepted = human_dossier(second)
    assert import_approved_news(ApprovedInbox(accepted), second['id'], source, **options)
    assert len(json.loads(source.read_text())['dossiers']) == 2
    assert [item['id'] for item in json.loads(options['archive'].read_text())['records']] == [second['id']]
    assert json.loads(options['destination'].read_text())['dossiers'] == [accepted]


def automatic_location(reference):
    return {'candidate_id': reference['id'], **{key: reference[key] for key in ('source_url', 'publisher', 'published_date')},
        'department': 'San Salvador', 'review_status': 'automated_unreviewed', 'human_reviewed': False,
        'method': 'gemini_explicit_department', 'extracted_at': STAMP, 'source_checked_at': STAMP,
        'evidence': {'paragraph_id': 'p1', 'content_sha256': 'a' * 64}}


def test_import_prunes_locations_for_human_overrides_and_unselected_references(tmp_path):
    first, second, third = candidate_reference(), candidate_reference(2), candidate_reference(3)
    bibliography, _ = candidate_archive(tmp_path, [first, second, third])
    manifest = selection_manifest([first, second])
    source, options = import_paths(tmp_path, bibliography, manifest)
    options['locations'].write_text(json.dumps({'schema_version': 3, 'generated_at': STAMP,
        'locations': [automatic_location(reference) for reference in (first, second, third)]}))
    dossier = human_dossier(first)
    assert import_approved_news(ApprovedInbox(dossier), first['id'], source, **options)
    assert json.loads(options['locations'].read_text())['locations'] == [automatic_location(second)]


@pytest.mark.parametrize('invalid', ['manifest', 'candidate', 'location', 'approval', 'same_output'])
def test_all_snapshots_are_prevalidated_before_any_approval_or_public_output_changes(invalid, tmp_path):
    reference = candidate_reference()
    bibliography, _ = candidate_archive(tmp_path)
    manifest = selection_manifest([])
    source, options = import_paths(tmp_path, bibliography, manifest)
    dossier = human_dossier(reference)
    if invalid == 'manifest':
        manifest['selections'] = [{'invalid': 'PRIVATE_VALUE'}]
    elif invalid == 'candidate':
        options['candidates'] = copy.deepcopy(bibliography)
        options['candidates']['records'][0]['publisher'] = 'Unrelated publisher'
    elif invalid == 'location':
        location = automatic_location(reference); location['publisher'] = 'Unrelated publisher'
        options['locations'].write_text(json.dumps({'schema_version': 3, 'generated_at': STAMP, 'locations': [location]}))
    elif invalid == 'approval':
        dossier['publisher'] = 'Unrelated publisher'
    paths = (source, options['archive'], options['destination'], options['locations'])
    snapshots = {path: path.read_bytes() for path in paths}
    if invalid == 'same_output':
        options['destination'] = source
    with pytest.raises(ValueError):
        import_approved_news(ApprovedInbox(dossier), reference['id'], source, **options)
    assert all(path.read_bytes() == content for path, content in snapshots.items())
    assert not list(tmp_path.glob('.*.json.*'))


def test_conflicting_reapproval_cannot_silently_replace_a_human_decision(tmp_path):
    bibliography, _ = candidate_archive(tmp_path)
    manifest = selection_manifest([])
    dossier = human_dossier(candidate_reference())
    source, options = import_paths(tmp_path, bibliography, manifest, approved={'schema_version': 1, 'dossiers': [dossier]})
    changed = copy.deepcopy(dossier); changed['review_notes'] = 'Otro resultado de una revisión humana que requiere resolver el conflicto.'
    snapshots = {path: path.read_bytes() for path in (source, options['archive'], options['destination'], options['locations'])}
    with pytest.raises(ValueError, match='Conflicting'):
        import_approved_news(ApprovedInbox(changed), ID, source, **options)
    assert all(path.read_bytes() == content for path, content in snapshots.items())


def test_partial_file_replace_failure_restores_all_existing_snapshots(tmp_path, monkeypatch):
    bibliography, _ = candidate_archive(tmp_path)
    source, options = import_paths(tmp_path, bibliography, selection_manifest([]))
    snapshots = {path: path.read_bytes() for path in (source, options['archive'], options['destination'], options['locations'])}
    original = Path.replace
    failed = False

    def fail_once(path, target):
        nonlocal failed
        if Path(target) == options['destination'] and not failed:
            failed = True
            raise OSError('Simulated output replacement failure')
        return original(path, target)

    monkeypatch.setattr(Path, 'replace', fail_once)
    with pytest.raises(OSError):
        import_approved_news(ApprovedInbox(human_dossier(candidate_reference())), ID, source, **options)
    assert all(path.read_bytes() == content for path, content in snapshots.items())
    assert not list(tmp_path.glob('.*.json.*'))
