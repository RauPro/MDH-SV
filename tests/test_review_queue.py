import base64
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock
import pytest
from mdh_pipeline.review_queue import (FEEDS, HOSTS, GitHubInbox, canonical_url,
    candidate_from_entry, collect, issue_body, review, require_public_dns)
from mdh_pipeline.publication import Publication, load_catalog

NOW = datetime(2026,10,9,20,tzinfo=timezone.utc)

def entry(title='Denuncian detención en El Salvador', url='https://gatoencerrado.news/2026/10/09/noticia/'):
    return {'title':title,'link':url,'summary':'Reporte para revisión'}

class Inbox:
    base='/repos/RauPro/MDH-SV-review'
    repository='RauPro/MDH-SV-review'
    def __init__(self): self.ids=set(); self.candidates=[]; self.calls=[]; self.permission='admin'; self.files={}; self.reads=[]
    def ensure_labels(self): pass
    def existing_ids(self): return set(self.ids)
    def enqueue(self,c): self.ids.add(c['id']); self.candidates.append(c)
    def get_file(self,path): self.reads.append(path); return self.files.get(path)
    def call(self,method,path,**kw):
        self.calls.append((method,path,kw))
        if path.endswith('/permission'):return {'permission':self.permission}
        if method=='GET':return {'body':issue_body(self.candidates[0]),'labels':[{'name':'review/pending'}]}
        return {}

@pytest.mark.parametrize('url',['http://gatoencerrado.news/a','https://evil.test/a','https://gatoencerrado.news.evil.test/a','https://user:pass@gatoencerrado.news/a','https://127.0.0.1/a','https://gatoencerrado.news/a?token=secret','https://gatoencerrado.news:444/a'])
def test_reject_unsafe_feed_links(url):
    with pytest.raises(ValueError):canonical_url(url)

def test_tracking_dedup_and_metadata_minimization():
    a=candidate_from_entry(entry(),FEEDS[1],NOW)
    b=candidate_from_entry(entry(url=entry()['link']+'?utm_source=rss#read'),FEEDS[1],NOW)
    assert a['id']==b['id']
    assert a['published_date'] is None
    assert a['status']=='pending'
    assert 'summary' not in a and 'full_text' not in a
    assert len(a['source_title'].split())<=24
    assert candidate_from_entry(entry(title='Resultados del partido de fútbol'),FEEDS[1],NOW) is None

def test_collection_is_idempotent_and_preserves_review_decisions():
    inbox=Inbox()
    fetch=lambda _: [entry(),entry()]
    assert collect(inbox,fetch,[FEEDS[1]])['added']==1
    assert collect(inbox,fetch,[FEEDS[1]])['added']==0
    assert len(inbox.candidates)==1

def test_partial_and_total_source_failure():
    def fetch(feed):
        if feed==FEEDS[0]:raise TimeoutError()
        return [entry()]
    assert collect(Inbox(),fetch,FEEDS[:2])['failed_sources']==1
    with pytest.raises(RuntimeError):collect(Inbox(),fetch,[FEEDS[0]])

def test_inbox_must_be_private():
    session=MagicMock();session.request.return_value.json.return_value={'full_name':'RauPro/MDH-SV','private':False}
    session.request.return_value.status_code=200
    with pytest.raises(ValueError):GitHubInbox('RauPro/MDH-SV','test',session)

def test_private_network_is_rejected(monkeypatch):
    monkeypatch.setattr('socket.getaddrinfo',lambda *a,**k:[(2,1,6,'',('127.0.0.1',443))])
    with pytest.raises(ValueError):require_public_dns('gatoencerrado.news')

def proposed():
    r=load_catalog().records[0].model_dump(mode='json')
    r.update(source_url=entry()['link'],review_notes='Fuente, fecha, duplicados y privacidad contrastados por el revisor.')
    r['dossier']['evidence'][0]['url'] = r['source_url']
    return r

def event(decision='approve',record=None):
    return {'sender':{'login':'RauPro','type':'User'},'inputs':{'issue_number':'1','decision':decision,'privacy_reviewed':'true','record_json':json.dumps(record or proposed())}}

def test_only_explicit_human_review_can_prepare_public_proposal(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    inbox=Inbox();inbox.enqueue(candidate_from_entry(entry(),FEEDS[1],NOW))
    result=review(inbox,event())
    assert result['publications']==0
    proposal=json.loads((tmp_path/'proposal.json').read_text())
    assert proposal['record']['human_reviewed'] is True
    assert proposal['record']['reviewed_by']=='RauPro'
    assert proposal['record']['reviewed_at']==proposal['record']['source_checked_at']
    assert 'RauPro' in proposal['record']['source_check_method']
    assert proposal['record']['origin_candidate_id']==inbox.candidates[0]['id']
    assert all('RauPro/MDH-SV-review/' in path for _,path,_ in inbox.calls)
    assert all('RauPro/MDH-SV/' not in path for _,path,_ in inbox.calls)

def draft_envelope(candidate):
    record=proposed()
    record.update(origin_candidate_id=candidate['id'],human_reviewed=False,
                  review_status='source_checked',review_notes=None,reviewed_by=None,reviewed_at=None)
    return {'schema_version':1,'candidate_id':candidate['id'],'source_url':candidate['source_url'],
        'status':'draft','relevant':True,'publishable':False,'human_reviewed':False,
        'classification':{'jurisdiction':'SV','relevance':'relevant','record_type':record['record_type']},
        'record':record,'grounding':{'facts':[{'paragraph_id':'p1','quote':'Cita privada de trabajo'}]},
        'extraction':{'provider':'gemini','model':'test-model','content_sha256':'a'*64}}

def draft_file(candidate, envelope):
    raw=json.dumps(envelope).encode()
    return {'type':'file','path':f'drafts/{candidate["id"]}.json','encoding':'base64',
            'size':len(raw),'content':base64.b64encode(raw).decode(),'sha':'file-revision'}

def prepared_draft_inbox():
    inbox=Inbox();candidate=candidate_from_entry(entry(),FEEDS[1],NOW);inbox.enqueue(candidate)
    envelope=draft_envelope(candidate)
    path=inbox.base+'/contents/drafts/'+candidate['id']+'.json'
    inbox.files[path]=draft_file(candidate,envelope)
    ev=event();ev['inputs']['record_json']='';ev['inputs']['review_notes']='He leído la fuente y contrastado fechas, atribución, duplicados y privacidad.'
    return inbox,envelope,path,ev

def assert_no_approval(inbox,tmp_path):
    assert not (tmp_path/'proposal.json').exists()
    assert not any(method in ('POST','PATCH','PUT','DELETE') for method,_,_ in inbox.calls)

def test_stored_private_draft_requires_and_records_human_review_without_exporting_wrapper(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    inbox,envelope,path,ev=prepared_draft_inbox()
    with pytest.raises(ValueError):Publication.model_validate(envelope['record'])
    result=review(inbox,ev)
    assert result=={'decision':'approved_for_proposal','issue':1,'publications':0}
    assert inbox.reads[0]==path
    proposal=json.loads((tmp_path/'proposal.json').read_text())
    assert proposal['record']['review_notes']==ev['inputs']['review_notes']
    assert proposal['record']['reviewed_by']=='RauPro'
    assert proposal['record']['human_reviewed'] is True
    assert proposal['record']['origin_candidate_id']==envelope['candidate_id']
    assert 'grounding' not in proposal and 'extraction' not in proposal
    assert 'Cita privada de trabajo' not in (tmp_path/'proposal.json').read_text()
    assert all('RauPro/MDH-SV-review/' in requested for _,requested,_ in inbox.calls)
    assert envelope['publishable'] is False and envelope['human_reviewed'] is False

@pytest.mark.parametrize('notes',[None,'','   ','Revisado'])
def test_stored_draft_cannot_use_ai_notes_as_human_approval(notes,tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    inbox,envelope,path,ev=prepared_draft_inbox()
    envelope['record']['review_notes']='Texto atribuido por una máquina que no es una revisión humana.'
    inbox.files[path]=draft_file(inbox.candidates[0],envelope)
    ev['inputs']['review_notes']=notes
    with pytest.raises(ValueError,match='review_notes'):review(inbox,ev)
    assert not inbox.reads
    assert_no_approval(inbox,tmp_path)

@pytest.mark.parametrize('change',[
    lambda d:d.update(candidate_id='0'*64),
    lambda d:d.update(source_url='https://gatoencerrado.news/otra-noticia/'),
    lambda d:d.update(schema_version=2),
    lambda d:d.update(schema_version=True),
    lambda d:d.update(status='needs_scope_review'),
    lambda d:d.update(status='needs_privacy_review'),
    lambda d:d.update(relevant=False),
    lambda d:d.update(publishable=True),
    lambda d:d.update(human_reviewed=True),
    lambda d:d.update(record=None),
    lambda d:d['classification'].update(jurisdiction='other'),
    lambda d:d['classification'].update(relevance='unclear'),
    lambda d:d['classification'].update(record_type=None),
    lambda d:d['record'].update(origin_candidate_id='0'*64),
    lambda d:d['record'].update(source_url='https://gatoencerrado.news/otra-noticia/'),
    lambda d:d['record'].update(human_reviewed=True),
    lambda d:d['record'].update(review_status='editorially_reviewed'),
    lambda d:d['record'].update(dossier=None),
])
def test_mismatched_or_unresolved_drafts_never_prepare_a_proposal(change,tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    inbox,envelope,path,ev=prepared_draft_inbox();change(envelope)
    inbox.files[path]=draft_file(inbox.candidates[0],envelope)
    with pytest.raises(ValueError):review(inbox,ev)
    assert_no_approval(inbox,tmp_path)

@pytest.mark.parametrize('change',[
    lambda f:None,
    lambda f:[],
    lambda f:{**f,'type':'dir'},
    lambda f:{**f,'path':'drafts/other.json'},
    lambda f:{**f,'encoding':'none','download_url':'https://unapproved.example/draft.json'},
    lambda f:{**f,'size':512001},
    lambda f:{**f,'size':1},
    lambda f:{**f,'content':'not-valid-base64$$'},
    lambda f:{**f,'content':base64.b64encode(b'not-json').decode(),'size':8},
    lambda f:{**f,'content':base64.b64encode(b'[]').decode(),'size':2},
    lambda f:{**f,'content':base64.b64encode(b'\xff').decode(),'size':1},
])
def test_malformed_private_contents_responses_fail_without_writes(change,tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    inbox,_,path,ev=prepared_draft_inbox();inbox.files[path]=change(inbox.files[path])
    with pytest.raises(ValueError):review(inbox,ev)
    assert_no_approval(inbox,tmp_path)

def test_manual_json_accepts_explicit_human_notes_and_keeps_existing_notes_compatibility(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    inbox=Inbox();inbox.enqueue(candidate_from_entry(entry(),FEEDS[1],NOW))
    ev=event();ev['inputs']['review_notes']='La fuente original fue contrastada; no se detectaron datos privados.'
    review(inbox,ev)
    assert json.loads((tmp_path/'proposal.json').read_text())['record']['review_notes']==ev['inputs']['review_notes']
    assert not any('/contents/drafts/' in path for path in inbox.reads)
    (tmp_path/'proposal.json').unlink()
    r=proposed();r['review_notes']=None;ev=event(record=r)
    with pytest.raises(ValueError,match='review_notes'):review(inbox,ev)
    assert not (tmp_path/'proposal.json').exists()

def test_stored_draft_approval_requires_privacy_checkbox_before_fetch(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    inbox,_,_,ev=prepared_draft_inbox();ev['inputs']['privacy_reviewed']='false'
    with pytest.raises(ValueError,match='privacy'):review(inbox,ev)
    assert not inbox.reads
    assert_no_approval(inbox,tmp_path)

def test_real_contents_http_malformed_response_cannot_fall_back_to_external_download(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    candidate=candidate_from_entry(entry(),FEEDS[1],NOW)
    session=MagicMock()
    def response(value):
        result=MagicMock();result.status_code=200;result.content=b'{}';result.json.return_value=value
        return result
    session.request.side_effect=[
        response({'full_name':'RauPro/MDH-SV-review','private':True}),
        response({'permission':'admin'}),
        response({'body':issue_body(candidate),'labels':[{'name':'review/pending'}]}),
    ]
    session.get.return_value=response({'type':'file','encoding':'none','download_url':'https://unapproved.example/private-draft'})
    inbox=GitHubInbox('RauPro/MDH-SV-review','test-token',session)
    ev=event();ev['inputs'].update(record_json='',review_notes='He contrastado la fuente y la privacidad de la ficha.')
    with pytest.raises(ValueError,match='metadata'):review(inbox,ev)
    session.get.assert_called_once_with('https://api.github.com/repos/RauPro/MDH-SV-review/contents/drafts/'+candidate['id']+'.json',timeout=(8,30))
    assert all(call.args[0]=='GET' for call in session.request.call_args_list)
    assert not (tmp_path/'proposal.json').exists()

@pytest.mark.parametrize('change',[{'type':'Bot'},{'login':'outsider'}])
def test_bot_and_unauthorized_approvals_fail(change):
    inbox=Inbox();inbox.enqueue(candidate_from_entry(entry(),FEEDS[1],NOW));ev=event();ev['sender'].update(change)
    if change.get('login'):inbox.permission='read'
    with pytest.raises(ValueError):review(inbox,ev)
    assert not any(method in ('PATCH','PUT') for method,_,_ in inbox.calls)


@pytest.mark.parametrize('decision', ['approve', 'reject'])
@pytest.mark.parametrize(('triggering_actor', 'sender'), [
    (None, {'type': 'User', 'login': 'RauPro'}),
    ('', {'type': 'User', 'login': 'RauPro'}),
    ('mdh-hourly-dispatch[bot]', {'type': 'User', 'login': 'RauPro'}),
    ('AnotherReviewer', {'type': 'User', 'login': 'RauPro'}),
    ('RauPro', {'type': 'Bot', 'login': 'RauPro'}),
    ('mdh-hourly-dispatch[bot]', {'type': 'User', 'login': 'mdh-hourly-dispatch[bot]'}),
    ('RauPro', None),
])
def test_review_cli_rejects_bot_replays_and_missing_runtime_actor_before_inbox_access(
        decision, triggering_actor, sender, tmp_path, monkeypatch):
    from mdh_pipeline import review_queue
    ev = event(decision)
    ev['sender'] = sender
    path = tmp_path / 'event.json'
    path.write_text(json.dumps(ev))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr('sys.argv', ['review_queue', 'review'])
    monkeypatch.setenv('GITHUB_EVENT_PATH', str(path))
    if triggering_actor is None:
        monkeypatch.delenv('GITHUB_TRIGGERING_ACTOR', raising=False)
    else:
        monkeypatch.setenv('GITHUB_TRIGGERING_ACTOR', triggering_actor)
    # These would be required for any private read or mutation. Actor rejection
    # must happen first, including outside Actions where no default env exists.
    monkeypatch.delenv('GITHUB_REPOSITORY', raising=False)
    monkeypatch.delenv('GH_TOKEN', raising=False)
    inbox_factory = MagicMock()
    monkeypatch.setattr(review_queue, 'GitHubInbox', inbox_factory)
    with pytest.raises(ValueError, match='human'):
        review_queue.main()
    inbox_factory.assert_not_called()
    assert not (tmp_path / 'proposal.json').exists()


@pytest.mark.parametrize('decision', ['approve', 'reject'])
def test_review_cli_preserves_explicit_reviews_started_by_the_original_human(
        decision, tmp_path, monkeypatch, capsys):
    from mdh_pipeline import review_queue
    inbox = Inbox()
    inbox.enqueue(candidate_from_entry(entry(), FEEDS[1], NOW))
    path = tmp_path / 'event.json'
    path.write_text(json.dumps(event(decision)))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr('sys.argv', ['review_queue', 'review'])
    monkeypatch.setenv('GITHUB_EVENT_PATH', str(path))
    monkeypatch.setenv('GITHUB_TRIGGERING_ACTOR', 'RauPro')
    monkeypatch.setenv('GITHUB_REPOSITORY', inbox.repository)
    monkeypatch.setenv('GH_TOKEN', 'test-token')
    monkeypatch.delenv('GITHUB_STEP_SUMMARY', raising=False)
    monkeypatch.setattr(review_queue, 'GitHubInbox', lambda *_: inbox)
    review_queue.main()
    report = json.loads(capsys.readouterr().out)
    assert report['decision'] == ('approved_for_proposal' if decision == 'approve' else 'rejected')
    assert (tmp_path / 'proposal.json').exists() is (decision == 'approve')
    assert any(method in ('PATCH', 'PUT') for method, _, _ in inbox.calls)

def test_missing_privacy_or_changed_source_fails():
    inbox=Inbox();inbox.enqueue(candidate_from_entry(entry(),FEEDS[1],NOW))
    ev=event();ev['inputs']['privacy_reviewed']='false'
    with pytest.raises(ValueError):review(inbox,ev)
    record=proposed();record['source_url']='https://www.hrw.org/some-other-report'
    with pytest.raises(ValueError):review(inbox,event(record=record))

def test_queue_candidate_cannot_bypass_human_review():
    record=proposed();record['review_notes']=None;record['origin_candidate_id']='a'*64
    with pytest.raises(ValueError):Publication.model_validate(record)

def test_rejection_closes_only_the_private_issue():
    inbox=Inbox();inbox.enqueue(candidate_from_entry(entry(),FEEDS[1],NOW))
    assert review(inbox,event('reject'))['decision']=='rejected'
    assert inbox.calls[-1][2]['json']=={'state':'closed','labels':['review/rejected']}

def test_import_requires_current_approval_and_is_idempotent(tmp_path,monkeypatch):
    import base64
    from mdh_pipeline.review_import import import_approved
    monkeypatch.chdir(tmp_path)
    inbox=Inbox();candidate=candidate_from_entry(entry(),FEEDS[1],NOW);inbox.enqueue(candidate)
    r=proposed();r['id']='reviewed-example';r['source_record_key']='reviewed-example'
    review(inbox,event(record=r))
    content=base64.b64encode((tmp_path/'proposal.json').read_bytes()).decode()
    source=tmp_path/'catalog.json';source.write_text(load_catalog().model_dump_json())
    class ApprovedInbox:
        repository=inbox.repository
        base=inbox.base
        def call(self, method, path):
            if '/contents/' in path:return {'content':content}
            return {'labels':[{'name':'review/approved'}],'body':issue_body(candidate)}
    approved=ApprovedInbox()
    assert import_approved(approved,candidate['id'],source,export=False) is True
    assert import_approved(approved,candidate['id'],source,export=False) is False
    assert len(json.loads(source.read_text())['records'])==len(load_catalog().records)+1
    approved.call=lambda method,path: {'content':content} if '/contents/' in path else {'labels':[],'body':issue_body(candidate)}
    with pytest.raises(ValueError):import_approved(approved,candidate['id'],source,export=False)

def test_collector_does_not_import_optional_ingestion_stack():
    import subprocess,sys,os
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    code='import sys; import mdh_pipeline.review_queue; import mdh_pipeline.publication; assert not any(x in sys.modules for x in ["bs4","google.genai","pyarrow","dotenv"])'
    subprocess.run([sys.executable,'-c',code],check=True,env={**os.environ,'PYTHONPATH':str(root/'apps/pipeline/src')+os.pathsep+str(root/'packages/schema/src')})
