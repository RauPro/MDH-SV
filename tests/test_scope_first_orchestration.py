"""Shared hourly grants are HTTP attempt limits, not successful article counts."""
from datetime import datetime, timezone
import json
import re
from types import SimpleNamespace

import pytest

from mdh_pipeline import gemini_review
from mdh_pipeline.gemini_budget import QuotaStateError


NOW = datetime(2026, 10, 12, 7, tzinfo=timezone.utc)


class Budget:
    def __init__(self, state, config):
        self.state, self.config = state, config
        self.requests_this_run = 0
        self.request_limit = None

    def snapshot(self):
        return {'remaining_requests': self.state['remaining'], 'cooldown_until': self.state.get('cooldown'),
                'used_requests': 240 - self.state['remaining'], 'day': '2026-10-12'}

    def set_request_limit(self, limit):
        assert self.request_limit in (None, limit)
        self.request_limit = limit

    def charge(self):
        assert self.requests_this_run < self.request_limit
        assert self.state['remaining'] > 0
        self.requests_this_run += 1
        self.state['remaining'] -= 1


def arrange(monkeypatch, *, remaining=240, consumed=None, selected=(), scope_error=None, cooldown=None):
    config = SimpleNamespace(batch_ceiling=50)
    state = {'remaining': remaining, 'cooldown': cooldown}
    scope_budget = Budget(state, config)
    calls = []

    def classify(inbox, drafts, archive, **kwargs):
        calls.append(('scope', kwargs))
        assert kwargs['max_seconds'] == 720
        budget = kwargs['budget']
        for _ in range(kwargs['max_requests'] if consumed is None else consumed):
            budget.charge()
        if scope_error:
            raise scope_error
        return {'http_requests': budget.requests_this_run, 'classified': budget.requests_this_run * 8,
                'accepted': len(selected), 'selected_ids': list(selected), 'updated_reviews': [],
                'unrecognized_private_field': 'PRIVATE_SENTINEL', 'stopped_reason': None}

    def full(inbox, **kwargs):
        calls.append(('full', kwargs))
        budget = kwargs['budget']
        count = min(kwargs['phase_http_limit'], state['remaining']) if not cooldown else 0
        if count:
            budget.set_request_limit(kwargs['phase_http_limit'])
            for _ in range(count):
                budget.charge()
        return {'attempted': count, 'news_dossiers': count, 'errors': 0, 'migrated': 0,
                'http_requests': count, 'stopped_reason': None, 'publications': 0}

    monkeypatch.setattr(gemini_review, 'editorial_excluded_ids', lambda _: set())
    monkeypatch.setattr(gemini_review, '_allocation_workload', lambda *_args, **kwargs: {
        'selected_ids': set(), 'excluded_ids': kwargs['excluded'],
        'extraction_pending': 1000, 'validation_ready': 1000, 'scope_pending': 1000, 'scope_reprocessing_ids': set(), 'scope_priority_ids': set()})
    def refresh(*args, **kwargs):
        prior = gemini_review._allocation_workload(*args[:5], **kwargs)
        return {**prior, 'selected_ids': (prior['selected_ids'] | set(selected)) - prior['excluded_ids']}
    monkeypatch.setattr(gemini_review, '_refresh_scope_workload', refresh)
    monkeypatch.setattr(gemini_review, 'process_queue', full)
    return config, scope_budget, state, calls, classify


def execute(tmp_path, config, budget, classify, *, now=NOW, archive=None, inbox=None):
    return gemini_review.process_scope_first(inbox if inbox is not None else SimpleNamespace(), api_key='fake-test-key', model='gemini-3.5-flash-lite',
        config=config, budget=budget, archive=archive or {'schema_version': 1, 'records': []},
        inbox_dir=tmp_path, limit=50, now=now, scope_worker=classify,
        budget_factory=lambda inbox, conf: Budget(budget.state, conf))


def test_normal_hour_reserves_two_validation_calls_and_splits_two_scope_six_dossiers(monkeypatch, tmp_path):
    config, budget, state, calls, classify = arrange(monkeypatch)
    report = execute(tmp_path, config, budget, classify)
    assert [phase for phase, _ in calls] == ['scope', 'full']
    assert report['hourly_request_limit'] == 10 and report['http_requests'] == 8
    assert report['validation_request_reservation'] == report['validation_request_limit'] == 2
    assert report['scope_request_limit'] == report['scope_phase']['http_requests'] == 2
    assert report['dossier_request_limit'] == report['dossier_http_requests'] == 6
    assert report['scope_phase']['classified'] == 16
    assert state['remaining'] == 232
    assert 'selected_ids' not in json.dumps(report) and 'PRIVATE_SENTINEL' not in json.dumps(report)


def test_uncertain_scope_failure_defers_extraction_and_releases_only_acknowledged_usage(monkeypatch, tmp_path):
    config, budget, state, calls, classify = arrange(monkeypatch, consumed=1,
        scope_error=ValueError('PRIVATE_SENTINEL: model output cannot be logged'))
    report = execute(tmp_path, config, budget, classify)
    assert report['scope_phase']['http_requests'] == 1
    assert report['scope_phase']['stopped_reason'] == 'scope_failed'
    assert report['dossier_request_limit'] == report['dossier_http_requests'] == 0
    assert report['http_requests'] == 1 and state['remaining'] == 239
    assert report['validation_request_limit'] == 9
    assert 'PRIVATE_SENTINEL' not in json.dumps(report)
    assert calls[1][1]['phase_http_limit'] == 0


@pytest.mark.parametrize(('remaining', 'now', 'expected_hour', 'expected_scope', 'expected_full'), [
    (1200, datetime(2026, 10, 12, 6, tzinfo=timezone.utc), 50, 2, 38),
    (1, NOW, 1, 0, 0),
    (48, NOW, 2, 1, 0),
    (0, NOW, 0, 0, 0),
])
def test_edge_grants_are_bounded_and_reserve_a_dossier_when_possible(
        monkeypatch, tmp_path, remaining, now, expected_hour, expected_scope, expected_full):
    config, budget, state, calls, classify = arrange(monkeypatch, remaining=remaining)
    report = execute(tmp_path, config, budget, classify, now=now)
    assert report['hourly_request_limit'] == expected_hour
    assert report['http_requests'] == expected_scope + expected_full
    assert report['scope_phase']['http_requests'] == expected_scope
    assert report['dossier_http_requests'] == expected_full
    assert state['remaining'] == remaining - expected_scope - expected_full
    assert bool(any(phase == 'scope' for phase, _ in calls)) is bool(expected_scope)


def test_uncertain_quota_state_cannot_be_downgraded_to_an_ordinary_scope_failure(monkeypatch, tmp_path):
    config, budget, _, calls, classify = arrange(monkeypatch, consumed=0,
        scope_error=QuotaStateError('budget_write_receipt_invalid'))
    with pytest.raises(QuotaStateError, match='receipt'):
        execute(tmp_path, config, budget, classify)
    assert [phase for phase, _ in calls] == ['scope']


def test_provider_cooldown_skips_both_phases(monkeypatch, tmp_path):
    config, budget, state, calls, classify = arrange(monkeypatch, cooldown='2026-10-13T07:00:00Z')
    report = execute(tmp_path, config, budget, classify)
    assert report['http_requests'] == 0 and state['remaining'] == 240
    assert [phase for phase, _ in calls] == ['full']


def test_selected_candidates_are_enqueued_and_prioritized_without_exposing_ids(monkeypatch, tmp_path):
    from mdh_pipeline import history_review
    identifier = 'a' * 64
    config, budget, _, calls, classify = arrange(monkeypatch, selected=[identifier])
    enqueued = []
    def enqueue(inbox, archive, **kwargs):
        enqueued.append((archive, kwargs))
        return {'added': 1}
    monkeypatch.setattr(history_review, 'enqueue_history', enqueue)
    report = execute(tmp_path, config, budget, classify, archive={'schema_version': 1, 'records': [{'id': identifier}]})
    assert enqueued[0][1] == {'limit': 50, 'include_international': True}
    assert calls[1][1]['priority_ids'] == [identifier]
    assert report['prioritized_candidates'] == 1
    assert identifier not in json.dumps(report)


class MetadataInbox:
    """Use the actual GitHub helpers over a recording transport, never the network."""
    from mdh_pipeline.review_queue import GitHubInbox
    base = '/repos/example/private'
    enqueue = GitHubInbox.enqueue
    ensure_labels = GitHubInbox.ensure_labels

    def __init__(self):
        self.seen, self.created = set(), []

    def existing_ids(self):
        return self.seen.copy()

    def call(self, method, path, **kwargs):
        from mdh_pipeline.review_queue import LABELS
        if method == 'GET' and path.endswith('/labels'):
            return [{'name': name} for name in LABELS]
        assert method == 'POST' and path.endswith('/issues')
        identifier = re.search(r'<!-- mdh-candidate:([a-f0-9]{64}) -->', kwargs['json']['body']).group(1)
        self.seen.add(identifier)
        self.created.append(identifier)
        return {'number': len(self.created)}


@pytest.mark.parametrize('remaining,expected_full', [(240, 6)])
def test_classified_unknown_title_hints_are_synchronized_beyond_small_dossier_grant(
        monkeypatch, tmp_path, remaining, expected_full):
    from test_history_review import reference
    refs = [reference(n) for n in range(1, 15)]
    assert all(row['scope_hint'] == 'undetermined' for row in refs)
    selected = [row['id'] for row in refs]
    config, budget, state, calls, classify = arrange(monkeypatch, remaining=remaining, selected=selected)
    monkeypatch.setattr(gemini_review.time, 'sleep', lambda _: None)
    inbox = MetadataInbox()
    report = execute(tmp_path, config, budget, classify, inbox=inbox,
                     archive={'schema_version': 1, 'records': refs})
    assert len(inbox.created) == report['prioritized_candidates'] == 14
    assert set(inbox.created) == set(selected)
    assert calls[-1][1]['priority_ids'] == sorted(selected)
    assert report['dossier_request_limit'] == report['dossier_http_requests'] == expected_full
    assert report['http_requests'] == 8
    assert state['remaining'] == remaining - report['http_requests']


def test_selected_sync_is_idempotent_excludes_human_withdrawals_and_never_adds_unselected_ids(monkeypatch, tmp_path):
    from test_history_review import reference
    refs = [reference(n) for n in range(1, 17)]
    selected = [row['id'] for row in refs[:14]]
    excluded = {selected[-1]}
    inbox = MetadataInbox()
    monkeypatch.setattr(gemini_review.time, 'sleep', lambda _: None)
    for iteration in range(2):
        config, budget, _, calls, classify = arrange(monkeypatch, selected=selected)
        monkeypatch.setattr(gemini_review, 'editorial_excluded_ids', lambda _: excluded)
        report = execute(tmp_path, config, budget, classify, inbox=inbox,
                         archive={'schema_version': 1, 'records': refs})
        assert report['prioritized_candidates'] == (13 if iteration == 0 else 0)
        assert set(calls[-1][1]['priority_ids']) == set(selected) - excluded
    assert len(inbox.created) == 13 and set(inbox.created) == set(selected) - excluded


def test_selected_metadata_sync_is_capped_at_fifty_without_increasing_http_grant(monkeypatch, tmp_path):
    from test_news_selection import reference
    refs = [reference(n) for n in range(1, 56)]
    config, budget, state, _, classify = arrange(monkeypatch, selected=[row['id'] for row in refs])
    monkeypatch.setattr(gemini_review.time, 'sleep', lambda _: None)
    inbox = MetadataInbox()
    report = execute(tmp_path, config, budget, classify, inbox=inbox,
                     archive={'schema_version': 1, 'records': refs})
    assert len(inbox.created) == report['prioritized_candidates'] == gemini_review.SELECTED_QUEUE_SYNC_LIMIT == 50
    assert report['http_requests'] == 8 and state['remaining'] == 232


def test_real_enqueue_helper_cannot_bypass_the_proxy_write_pacing():
    from mdh_pipeline.history_review import enqueue_history
    from test_history_review import reference
    refs = [reference(n) for n in range(1, 15)]
    inbox, waits = MetadataInbox(), []
    paced = gemini_review._PacedInbox(inbox, waits.append, clock=lambda: 0)
    enqueue_history(paced, {'schema_version': 1, 'records': refs}, limit=50, include_international=True)
    assert len(inbox.created) == paced.write_calls == 14
    assert waits == [gemini_review.MIN_WRITE_INTERVAL] * 13


def test_human_exclusions_cannot_be_reintroduced_by_cached_positive_scope(monkeypatch, tmp_path):
    from mdh_pipeline import history_review
    identifier = 'a' * 64
    config, budget, _, calls, classify = arrange(monkeypatch, selected=[identifier])
    monkeypatch.setattr(gemini_review, 'editorial_excluded_ids', lambda _: {identifier})
    monkeypatch.setattr(history_review, 'enqueue_history', lambda *_args, **_kwargs: pytest.fail('no rejected candidate can be enqueued'))
    execute(tmp_path, config, budget, classify, archive={'schema_version': 1, 'records': [{'id': identifier}]})
    assert calls[0][1]['excluded_ids'] == {identifier}
    assert calls[1][1]['priority_ids'] == []


def test_duplicate_hour_returns_before_loading_scope_caches_or_running_either_phase(monkeypatch, capsys, tmp_path):
    from mdh_pipeline import gemini_budget, hourly_slot
    config = SimpleNamespace(model='gemini-3.5-flash-lite', batch_ceiling=50)
    budget = Budget({'remaining': 240}, config)
    monkeypatch.setattr('sys.argv', ['gemini_review', '--scope-first', '--inbox-dir', str(tmp_path / 'missing')])
    for name, value in {'GEMINI_API_KEY': 'fake-key', 'MDH_GEMINI_BUDGET_CONFIG': '{}',
                        'GEMINI_MODEL': config.model, 'GITHUB_REPOSITORY': 'RauPro/MDH-SV-review',
                        'GH_TOKEN': 'test', 'GITHUB_RUN_ID': '123', 'GITHUB_EVENT_NAME': 'workflow_dispatch'}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(gemini_budget, 'BudgetConfig', lambda **_: config)
    monkeypatch.setattr(gemini_budget, 'GeminiBudget', lambda *_: budget)
    monkeypatch.setattr(gemini_review, 'GitHubInbox', lambda *_: SimpleNamespace())
    monkeypatch.setattr(hourly_slot, 'HourlySlots', lambda *_: SimpleNamespace(claim=lambda **_: None))
    monkeypatch.setattr(gemini_review, 'process_scope_first', lambda *_args, **_kwargs: pytest.fail('duplicate must not classify'))
    gemini_review.main()
    assert json.loads(capsys.readouterr().out)['status'] == 'hour_already_claimed'
def test_scope_failure_is_visible_even_when_the_dossier_stage_finishes():
    from mdh_pipeline.gemini_review import failed_report
    assert failed_report({"stopped_reason": None, "attempted": 1, "errors": 0,
                          "scope_phase": {"stopped_reason": "scope_failed"}})
    assert not failed_report({"stopped_reason": None, "attempted": 1, "errors": 0,
                              "scope_phase": {"stopped_reason": "run_limit"}})



def test_cached_positives_are_synchronized_when_scope_grant_is_zero(monkeypatch, tmp_path):
    from test_history_review import reference
    refs = [reference(n) for n in range(1, 5)]
    selected = {row['id'] for row in refs}
    config, budget, _, calls, classify = arrange(monkeypatch)
    monkeypatch.setattr(gemini_review, '_allocation_workload', lambda *_args, **kwargs: {
        'selected_ids': selected, 'excluded_ids': set(), 'extraction_pending': 4,
        'validation_ready': 20, 'scope_pending': 0, 'scope_reprocessing_ids': set(), 'scope_priority_ids': set()})
    monkeypatch.setattr(gemini_review.time, 'sleep', lambda _: None)
    inbox = MetadataInbox()
    report = execute(tmp_path, config, budget, classify, inbox=inbox,
                     archive={'schema_version': 1, 'records': refs})
    assert [phase for phase, _ in calls] == ['full']
    assert set(inbox.created) == selected
    assert set(calls[0][1]['eligible_ids']) == selected
    assert report['validation_request_reservation'] == 6
    assert report['http_requests'] == 4


def test_durable_plan_precedes_model_work_and_completion_records_http_attempts(monkeypatch, tmp_path):
    from mdh_pipeline.pipeline_allocation import AllocationLedger
    from test_pipeline_allocation import Inbox
    config, budget, _, calls, classify = arrange(monkeypatch, consumed=1)
    store = Inbox()
    ledger = AllocationLedger(store, project_id='test', model='gemini-3.5-flash-lite', clock=lambda: NOW)
    def guarded(*args, **kwargs):
        assert store.writes == 1
        return classify(*args, **kwargs)
    report = gemini_review.process_scope_first(SimpleNamespace(), api_key='test', model='gemini-3.5-flash-lite',
        config=config, budget=budget, archive={'schema_version': 1, 'records': []}, inbox_dir=tmp_path,
        limit=50, now=NOW, scope_worker=guarded,
        budget_factory=lambda _, conf: Budget(budget.state, conf), allocation_ledger=ledger, run_id='123')
    assert report['http_requests'] == 8
    assert ledger.claim_validation(source_run_id='123', consumer_run_id='456', day='2026-10-12') == 2


def test_routing_uses_all_validated_positive_caches_not_title_hints():
    from test_news_selection import reference, rights_review, NOW as instant
    refs = [reference(n) for n in range(1, 5)]
    reviews = [rights_review(refs[0]), rights_review(refs[1], relevance='not_relevant'),
               rights_review(refs[2], jurisdiction='other'), rights_review(refs[3], relevance='unclear')]
    inbox = SimpleNamespace(base='/repos/private', call=lambda *args, **kwargs: [])
    result = gemini_review._allocation_workload(inbox, {'schema_version': 1, 'records': refs}, [], reviews, [],
                                               excluded=set(), now=instant)
    assert result['selected_ids'] == {refs[0]['id']}
    assert result['excluded_ids'] == set()
    assert result['scope_reprocessing_ids'] == set()
    assert result['extraction_pending'] == 1
    assert result['scope_pending'] == 3


def test_routing_preserves_human_exclusions_and_rejects_stale_selected_hash():
    from test_news_selection import reference, rights_review, project_news_selection, NOW as instant
    refs = [reference(n) for n in range(1, 4)]
    reviews = [rights_review(ref) for ref in refs]
    manifest = project_news_selection([], {'schema_version': 1, 'records': refs}, scope_reviews=reviews, now=instant)
    refs[0]['selection'] = next(row['selection'] for row in manifest['selections'] if row['candidate_id'] == refs[0]['id'])
    refs[0]['selection']['evidence']['content_sha256'] = 'd' * 64
    inbox = SimpleNamespace(base='/repos/private', call=lambda *args, **kwargs: [])
    result = gemini_review._allocation_workload(inbox, {'schema_version': 1, 'records': refs}, [], reviews, [],
                                               excluded={refs[1]['id']}, now=instant)
    assert result['selected_ids'] == {refs[2]['id']}
    assert refs[1]['id'] in result['excluded_ids']


@pytest.mark.parametrize('mutation', ['wrong_url', 'future', 'extra'])
def test_invalid_scope_cache_never_becomes_extraction_permission(mutation):
    from test_news_selection import reference, rights_review, NOW as instant
    ref = reference()
    review = rights_review(ref)
    if mutation == 'wrong_url':
        review['source_url'] += 'other/'
    elif mutation == 'future':
        review['extraction']['prepared_at'] = '2027-01-01T00:00:00Z'
    else:
        review['approve'] = True
    inbox = SimpleNamespace(base='/repos/private', call=lambda *args, **kwargs: [])
    with pytest.raises(ValueError, match='invalid_saved_scope_review'):
        gemini_review._allocation_workload(inbox, {'schema_version': 1, 'records': [ref]}, [], [review], [],
                                           excluded=set(), now=instant)


def test_generated_and_cached_dossiers_are_not_counted_as_pending_extraction():
    from test_news_selection import reference, rights_review, draft, NOW as instant
    ref = reference()
    document = draft(ref)
    inbox = SimpleNamespace(base='/repos/private', call=lambda *args, **kwargs: [])
    result = gemini_review._allocation_workload(inbox, {'schema_version': 1, 'records': [ref]},
        [document], [rights_review(ref)], [], excluded=set(), now=instant)
    assert result['extraction_pending'] == 0
    assert result['validation_ready'] == 1


def test_old_source_audit_with_matching_hash_prioritizes_recheck_without_final_exclusion():
    from test_news_selection import reference, rights_review, source_audit, NOW as instant
    ref = reference()
    inbox = SimpleNamespace(base='/repos/private', call=lambda *args, **kwargs: [])
    review = rights_review(ref)
    result = gemini_review._allocation_workload(inbox, {'schema_version': 1, 'records': [ref]}, [], [review], [],
        excluded=set(), now=instant, scope_audits=source_audit(ref))
    assert not result['selected_ids'] and not result['excluded_ids']
    assert result['scope_priority_ids'] == {ref['id']}
    assert result['scope_pending'] == 1
    review['evidence']['content_sha256'] = review['source_read']['content_sha256'] = 'd' * 64
    updated = gemini_review._allocation_workload(inbox, {'schema_version': 1, 'records': [ref]}, [], [review], [],
        excluded=set(), now=instant, scope_audits=source_audit(ref))
    assert updated['selected_ids'] == {ref['id']} and not updated['excluded_ids']


def test_scope_worker_cannot_reintroduce_source_mismatch_or_human_block(monkeypatch, tmp_path):
    from test_history_review import reference
    ref = reference(1)
    config, budget, _, calls, classify = arrange(monkeypatch, selected=[ref['id']])
    monkeypatch.setattr(gemini_review, '_allocation_workload', lambda *_args, **kwargs: {
        'selected_ids': set(), 'excluded_ids': {ref['id']}, 'extraction_pending': 1000,
        'validation_ready': 1000, 'scope_pending': 1000, 'scope_reprocessing_ids': set(), 'scope_priority_ids': set()})
    execute(tmp_path, config, budget, classify, archive={'schema_version': 1, 'records': [ref]})
    assert calls[-1][1]['eligible_ids'] == []
    assert calls[-1][1]['excluded_ids'] == {ref['id']}


def test_new_scope_rejection_replaces_old_positive_draft_for_same_source():
    from test_news_selection import reference, current_review, draft, NOW as instant
    ref = reference()
    inbox = SimpleNamespace(base='/repos/private', call=lambda *args, **kwargs: [])
    before = gemini_review._allocation_workload(inbox, {'schema_version': 1, 'records': [ref]},
        [draft(ref)], [], [], excluded=set(), now=instant)
    assert before['selected_ids'] == {ref['id']}
    updated = gemini_review._refresh_scope_workload(inbox, {'schema_version': 1, 'records': [ref]},
        [draft(ref)], [], [], [current_review(ref, relevance='not_relevant')], excluded=set(), now=instant,
        scope_audits=None)
    assert updated['selected_ids'] == set()
    assert updated['excluded_ids'] == set()
    assert updated['scope_pending'] == 1


def test_new_source_positive_replaces_old_cached_negative_and_old_source_audit():
    from test_news_selection import reference, rights_review, draft, source_audit, NOW as instant
    ref = reference()
    inbox = SimpleNamespace(base='/repos/private', call=lambda *args, **kwargs: [])
    old = rights_review(ref, relevance='not_relevant')
    current = rights_review(ref)
    current['evidence']['content_sha256'] = current['source_read']['content_sha256'] = 'd' * 64
    current['source_read']['fetched_at'] = '2026-10-12T15:00:00Z'
    current['extraction']['prepared_at'] = '2026-10-12T15:01:00Z'
    updated = gemini_review._refresh_scope_workload(inbox, {'schema_version': 1, 'records': [ref]},
        [draft(ref)], [old], [], [current], excluded=set(), now=instant, scope_audits=source_audit(ref))
    assert updated['selected_ids'] == {ref['id']}
    assert updated['excluded_ids'] == set()


def test_explicit_current_exception_and_audit_paths_override_pinned_defaults(monkeypatch, tmp_path):
    from test_news_selection import reference, source_audit
    from mdh_pipeline import history_review
    ref = reference()
    config, budget, _, calls, classify = arrange(monkeypatch, selected=[ref['id']])
    exceptions = tmp_path / 'current-exceptions.json'
    exceptions.write_text(json.dumps({'schema_version': 1, 'decisions': [{
        'candidate_id': ref['id'], 'withdrawn': True, 'request_generation': 1,
        'issue_number': 22, 'decided_by': 'editor', 'decided_at': '2026-10-12T00:00:00Z'}]}))
    audits = tmp_path / 'current-audits.json'
    audits.write_text(json.dumps(source_audit(ref)))
    seen = []
    def workload(*args, **kwargs):
        seen.append(kwargs)
        return {'selected_ids': set(), 'excluded_ids': kwargs['excluded'], 'extraction_pending': 1000,
                'validation_ready': 1000, 'scope_pending': 1000, 'scope_reprocessing_ids': set(), 'scope_priority_ids': set()}
    monkeypatch.setattr(gemini_review, '_allocation_workload', workload)
    monkeypatch.setattr(history_review, 'enqueue_history', lambda *_args, **_kwargs: pytest.fail('withdrawn'))
    gemini_review.process_scope_first(SimpleNamespace(), api_key='test', model='gemini-3.5-flash-lite',
        config=config, budget=budget, archive={'schema_version': 1, 'records': [ref]}, inbox_dir=tmp_path,
        limit=50, now=NOW, scope_worker=classify, budget_factory=lambda _, conf: Budget(budget.state, conf),
        exceptions_path=exceptions, scope_audits_path=audits)
    assert seen[0]['scope_audits'] == source_audit(ref)
    assert ref['id'] in calls[0][1]['excluded_ids']
    assert calls[-1][1]['eligible_ids'] == []
