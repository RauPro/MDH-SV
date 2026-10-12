"""Shared hourly grants are HTTP attempt limits, not successful article counts."""
from datetime import datetime, timezone
import json
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
                'accepted': len(selected), 'selected_ids': list(selected),
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
    monkeypatch.setattr(gemini_review, 'process_queue', full)
    return config, scope_budget, state, calls, classify


def execute(tmp_path, config, budget, classify, *, now=NOW, archive=None):
    return gemini_review.process_scope_first(SimpleNamespace(), api_key='fake-test-key', model='gemini-3.5-flash-lite',
        config=config, budget=budget, archive=archive or {'schema_version': 1, 'records': []},
        inbox_dir=tmp_path, limit=50, now=now, scope_worker=classify,
        budget_factory=lambda inbox, conf: Budget(budget.state, conf))


def test_normal_hour_splits_seven_bulk_requests_and_three_dossiers(monkeypatch, tmp_path):
    config, budget, state, calls, classify = arrange(monkeypatch)
    report = execute(tmp_path, config, budget, classify)
    assert [phase for phase, _ in calls] == ['scope', 'full']
    assert report['hourly_request_limit'] == report['http_requests'] == 10
    assert report['scope_request_limit'] == report['scope_phase']['http_requests'] == 7
    assert report['dossier_request_limit'] == report['dossier_http_requests'] == 3
    assert report['scope_phase']['classified'] == 56
    assert state['remaining'] == 230
    assert 'selected_ids' not in json.dumps(report) and 'PRIVATE_SENTINEL' not in json.dumps(report)


def test_unused_scope_capacity_is_released_but_failed_http_attempts_are_not(monkeypatch, tmp_path):
    config, budget, state, calls, classify = arrange(monkeypatch, consumed=2,
        scope_error=ValueError('PRIVATE_SENTINEL: model output cannot be logged'))
    report = execute(tmp_path, config, budget, classify)
    assert report['scope_phase']['http_requests'] == 2
    assert report['scope_phase']['stopped_reason'] == 'scope_failed'
    assert report['dossier_request_limit'] == report['dossier_http_requests'] == 8
    assert report['http_requests'] == 10 and state['remaining'] == 230
    assert 'PRIVATE_SENTINEL' not in json.dumps(report)
    assert calls[1][1]['phase_http_limit'] == 8


@pytest.mark.parametrize(('remaining', 'now', 'expected_hour', 'expected_scope', 'expected_full'), [
    (1200, datetime(2026, 10, 12, 6, tzinfo=timezone.utc), 50, 8, 42),
    (1, NOW, 1, 1, 0),
    (48, NOW, 2, 1, 1),
    (0, NOW, 0, 0, 0),
])
def test_edge_grants_are_bounded_and_reserve_a_dossier_when_possible(
        monkeypatch, tmp_path, remaining, now, expected_hour, expected_scope, expected_full):
    config, budget, state, calls, classify = arrange(monkeypatch, remaining=remaining)
    report = execute(tmp_path, config, budget, classify, now=now)
    assert report['hourly_request_limit'] == report['http_requests'] == expected_hour
    assert report['scope_phase']['http_requests'] == expected_scope
    assert report['dossier_http_requests'] == expected_full
    assert state['remaining'] == remaining - expected_hour
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
    assert enqueued[0][1] == {'limit': 3, 'include_international': True}
    assert calls[1][1]['priority_ids'] == [identifier]
    assert report['prioritized_candidates'] == 1
    assert identifier not in json.dumps(report)


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

