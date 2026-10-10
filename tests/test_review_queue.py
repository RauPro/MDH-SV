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
    def __init__(self): self.ids=set(); self.candidates=[]; self.calls=[]; self.permission='admin'
    def ensure_labels(self): pass
    def existing_ids(self): return set(self.ids)
    def enqueue(self,c): self.ids.add(c['id']); self.candidates.append(c)
    def get_file(self,path): return None
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

@pytest.mark.parametrize('change',[{'type':'Bot'},{'login':'outsider'}])
def test_bot_and_unauthorized_approvals_fail(change):
    inbox=Inbox();inbox.enqueue(candidate_from_entry(entry(),FEEDS[1],NOW));ev=event();ev['sender'].update(change)
    if change.get('login'):inbox.permission='read'
    with pytest.raises(ValueError):review(inbox,ev)
    assert not any(method in ('PATCH','PUT') for method,_,_ in inbox.calls)

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
