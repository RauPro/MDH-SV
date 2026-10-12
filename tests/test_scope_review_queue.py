import base64
from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from mdh_pipeline.gemini_budget import QuotaDeferred
from mdh_pipeline.scope_review_queue import recheck_scope
from test_news_selection import archive, draft, reference


class Inbox:
    base = '/repos/example/private'
    def __init__(self): self.files = {}
    def get_file(self, path): return self.files.get(path)
    def call(self, method, path, **kwargs):
        assert method == 'PUT'
        encoded = kwargs['json']['content']
        self.files[path] = {'sha': 'a' * 40, 'encoding': 'base64', 'size': len(base64.b64decode(encoded)), 'content': encoded}
        return {'content': {'sha': 'a' * 40}}


class Budget:
    config = SimpleNamespace(model='gemini-3.5-flash-lite', batch_ceiling=50)
    def __init__(self): self.requests_this_run = 0
    def snapshot(self): return {'remaining_requests': 10}
    def set_request_limit(self, value): self.limit = value


def fixtures(count=1):
    refs = [reference(i) for i in range(1, count + 1)]
    drafts = [draft(r) for r in refs]
    for d in drafts: d['extraction']['prompt_version'] = 'mdh-universal-news-grounded-7-field-privacy'
    return refs, drafts, archive(*refs)


def fetched(ref):
    return {'content_sha256': 'c' * 64, 'paragraphs': [{'id': 'p1', 'text': 'Synthetic private text'}]}


def classify(items, *, budget, model, **kwargs):
    assert budget.requests_this_run < budget.limit
    budget.requests_this_run += 1
    return [{'schema_version': 1, 'candidate_id': i['reference']['id'],
        **{k: i['reference'][k] for k in ('source_url', 'publisher', 'published_date')},
        'classification': {'jurisdiction': 'SV', 'relevance': 'relevant'},
        'evidence': {'content_sha256': 'c' * 64, 'jurisdiction_paragraph_ids': ['p1'], 'relevance_paragraph_ids': ['p1']},
        'extraction': {'provider': 'gemini', 'model': model, 'prompt_version': 'mdh-news-scope-1', 'prepared_at': '2026-10-12T16:00:00+00:00'}} for i in items]


def run(inbox, budget, drafts, data, **kwargs):
    return recheck_scope(inbox, drafts, data, api_key='fake-unit-test-key', model=budget.config.model,
                        budget=budget, fetch=fetched, classify=classify, **kwargs)


def test_eight_legacy_articles_share_one_request_and_are_not_reprocessed():
    _, drafts, data = fixtures(8); inbox, budget = Inbox(), Budget()
    result = run(inbox, budget, drafts, data)
    assert result['reviewed'] == 8 and result['http_requests'] == 1
    assert len(inbox.files) == 8 and all('scope-reviews/' in p for p in inbox.files)
    assert all('Synthetic private text' not in base64.b64decode(f['content']).decode() for f in inbox.files.values())
    again = run(inbox, Budget(), drafts, data)
    assert again['cached'] == 8 and again['http_requests'] == again['sources_checked'] == 0


def test_request_limit_keeps_remaining_drafts_for_a_later_batch():
    _, drafts, data = fixtures(16)
    result = run(Inbox(), Budget(), drafts, data, max_requests=1)
    assert result['reviewed'] == 8 and result['http_requests'] == 1
    assert result['stopped_reason'] == 'run_limit'


def test_fresh_scope_policy_and_human_exclusions_do_not_call_sources():
    refs, drafts, data = fixtures(2)
    drafts[0]['extraction']['prompt_version'] = 'mdh-universal-news-grounded-8-relevance-scope'
    result = run(Inbox(), Budget(), drafts, data, excluded_ids=[refs[1]['id']])
    assert result['candidates'] == result['sources_checked'] == result['http_requests'] == 0


def test_changed_source_is_not_reinterpreted_as_the_original_model_input():
    _, drafts, data = fixtures()
    result = recheck_scope(Inbox(), drafts, data, api_key='fake', model=Budget.config.model, budget=Budget(),
                          fetch=lambda _: {'content_sha256': 'd' * 64}, classify=classify)
    assert result['source_changed'] == 1 and result['http_requests'] == result['reviewed'] == 0


def test_project_quota_deferral_persists_no_decision_and_never_retries_here():
    _, drafts, data = fixtures(); inbox = Inbox()
    def deferred(*a, **kw): raise QuotaDeferred('daily_budget_exhausted', datetime.now(timezone.utc))
    result = recheck_scope(inbox, drafts, data, api_key='fake', model=Budget.config.model, budget=Budget(), fetch=fetched, classify=deferred)
    assert result['stopped_reason'] == 'budget_deferred' and result['quota_reason'] == 'daily_budget_exhausted'
    assert inbox.files == {}


def test_corrupt_saved_assessment_fails_closed_without_fresh_provider_call():
    refs, drafts, data = fixtures(); inbox = Inbox()
    run(inbox, Budget(), drafts, data)
    path = inbox.base + '/contents/scope-reviews/' + refs[0]['id'] + '.json'
    payload = json.loads(base64.b64decode(inbox.files[path]['content']))
    payload['evidence']['content_sha256'] = 'e' * 64
    raw = json.dumps(payload).encode()
    inbox.files[path].update(content=base64.b64encode(raw).decode(), size=len(raw))
    with pytest.raises(ValueError, match='invalid_saved_scope_review'):
        run(inbox, Budget(), drafts, data)
