"""One shared hourly grant, durable validation handoff, no provider calls."""
import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json

import pytest

from mdh_pipeline.pipeline_allocation import AllocationLedger, STATE_PATH, plan_pipeline_requests
from mdh_pipeline.gemini_budget import QuotaStateError


NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize('scope,extraction,ready,expected', [
    (1000, 1000, 1000, (2, 6, 2, 0)),
    (0, 1000, 1000, (0, 8, 2, 0)),
    (0, 1000, 0, (0, 8, 2, 0)),
    (1000, 0, 1000, (2, 0, 8, 0)),
    (0, 0, 1000, (0, 0, 10, 0)),
    (0, 0, 3, (0, 0, 1, 9)),
    (0, 2, 1000, (0, 2, 8, 0)),
    (0, 1, 0, (0, 1, 1, 8)),
    (0, 0, 0, (0, 0, 0, 10)),
])
def test_backlog_allocation(scope, extraction, ready, expected):
    plan = plan_pipeline_requests(10, scope_pending=scope, extraction_pending=extraction, validation_ready=ready)
    assert tuple(plan.values()) == expected


def test_all_grants_are_integer_bounded_and_reserve_attempts_not_candidate_successes():
    for grant in range(51):
        for scope in (0, 1, 1000):
            for extraction in (0, 1, 7, 1000):
                for ready in (0, 1, 10, 1000):
                    result = plan_pipeline_requests(grant, scope_pending=scope,
                        extraction_pending=extraction, validation_ready=ready)
                    assert sum(result.values()) == grant
                    assert all(type(value) is int and value >= 0 for value in result.values())
                    assert result['scope_requests'] <= 2
                    assert result['extraction_requests'] <= extraction
                    assert result['validation_requests'] * 4 <= ready + result['extraction_requests'] + 3


@pytest.mark.parametrize('kwargs', [dict(hourly_grant=True), dict(hourly_grant=51),
    dict(extraction_pending=-1), dict(validation_ready=1.5), dict(scope_pending=100001), dict(batch_size=5)])
def test_invalid_workloads_fail_closed(kwargs):
    args = dict(hourly_grant=10, extraction_pending=1000, validation_ready=1000, scope_pending=1000)
    args.update(kwargs)
    with pytest.raises(ValueError):
        plan_pipeline_requests(**args)


class Inbox:
    repository = 'RauPro/MDH-SV-review'
    base = '/repos/RauPro/MDH-SV-review'
    def __init__(self):
        self.file = None
        self.writes = 0
        self.fail = None
        self.unconfirmed = False

    def get_file(self, path):
        assert path == self.base + '/contents/' + STATE_PATH
        return deepcopy(self.file)

    def call(self, method, path, *, json):
        assert method == 'PUT' and path == self.base + '/contents/' + STATE_PATH
        if self.fail:
            raise self.fail
        assert json.get('sha') == (self.file['sha'] if self.file else None)
        raw = base64.b64decode(json['content'])
        self.file = dict(type='file', path=STATE_PATH, encoding='base64', size=len(raw),
                         content=json['content'], sha=hashlib.sha256(raw).hexdigest())
        self.writes += 1
        return {'content': {'sha': self.file['sha']}} if not self.unconfirmed else {}


@pytest.fixture
def configured():
    inbox = Inbox()
    ledger = AllocationLedger(inbox, project_id='test-project', model='gemini-3.5-flash-lite', clock=lambda: NOW)
    return inbox, ledger


def create(ledger, run_id='123'):
    ledger.create(run_id=run_id, day='2026-10-14', hourly_grant=10,
                  plan=plan_pipeline_requests(10, extraction_pending=1000, validation_ready=1000, scope_pending=1000))


def test_unfinished_run_only_grants_reserved_validation_once(configured):
    inbox, ledger = configured
    create(ledger)
    assert ledger.claim_validation(source_run_id='123', consumer_run_id='456', day='2026-10-14') == 2
    assert ledger.claim_validation(source_run_id='123', consumer_run_id='456', day='2026-10-14') == 0
    assert ledger.claim_validation(source_run_id='123', consumer_run_id='789', day='2026-10-14') == 0
    assert inbox.writes == 2


def test_completion_only_releases_actual_unused_http_capacity(configured):
    _, ledger = configured
    create(ledger)
    ledger.finish(run_id='123', scope_used=1, extraction_used=4)
    assert ledger.claim_validation(source_run_id='123', consumer_run_id='456', day='2026-10-14') == 5


def test_unused_scope_can_go_to_extraction_without_spending_validation_reserve(configured):
    _, ledger = configured
    create(ledger)
    ledger.finish(run_id='123', scope_used=0, extraction_used=8)
    assert ledger.claim_validation(source_run_id='123', consumer_run_id='456', day='2026-10-14') == 2


@pytest.mark.parametrize('scope,extraction', [(3, 5), (2, 7), (-1, 0), (1, True)])
def test_overrun_or_ambiguous_accounting_cannot_complete(configured, scope, extraction):
    _, ledger = configured
    create(ledger)
    with pytest.raises(QuotaStateError):
        ledger.finish(run_id='123', scope_used=scope, extraction_used=extraction)


def test_missing_old_day_and_duplicate_run_never_create_more_capacity(configured):
    inbox, ledger = configured
    assert ledger.claim_validation(source_run_id='123', consumer_run_id='456', day='2026-10-14') == 0
    create(ledger)
    assert ledger.claim_validation(source_run_id='123', consumer_run_id='456', day='2026-10-15') == 0
    with pytest.raises(QuotaStateError, match='already_exists'):
        create(ledger)
    assert inbox.writes == 1


def test_unconfirmed_claim_does_not_allow_provider_work_or_reclaim(configured):
    inbox, ledger = configured
    create(ledger)
    inbox.unconfirmed = True
    with pytest.raises(QuotaStateError, match='unconfirmed'):
        ledger.claim_validation(source_run_id='123', consumer_run_id='456', day='2026-10-14')
    inbox.unconfirmed = False
    assert ledger.claim_validation(source_run_id='123', consumer_run_id='456', day='2026-10-14') == 0


def test_cas_failure_propagates_without_grant(configured):
    inbox, ledger = configured
    create(ledger)
    inbox.fail = RuntimeError('revision conflict')
    with pytest.raises(RuntimeError):
        ledger.claim_validation(source_run_id='123', consumer_run_id='456', day='2026-10-14')


def test_manual_grant_is_claimed_once_per_run(configured):
    inbox, ledger = configured
    assert ledger.claim_manual_validation(consumer_run_id='456', day='2026-10-14', grant=7) == 7
    assert ledger.claim_manual_validation(consumer_run_id='456', day='2026-10-14', grant=10) == 0
    assert inbox.writes == 2


def test_wrong_project_model_and_malformed_state_fail_closed(configured):
    inbox, ledger = configured
    create(ledger)
    other = AllocationLedger(inbox, project_id='other', model='gemini-3.5-flash-lite')
    with pytest.raises(QuotaStateError, match='invalid_allocation_state'):
        other.claim_validation(source_run_id='123', consumer_run_id='456', day='2026-10-14')
    inbox.file['size'] -= 1
    with pytest.raises(QuotaStateError, match='file_size'):
        ledger.claim_validation(source_run_id='123', consumer_run_id='456', day='2026-10-14')


def test_retention_is_bounded_and_cannot_change_consumed_run(configured):
    inbox, ledger = configured
    for number in range(1, 80):
        create(ledger, str(number))
    state = json.loads(base64.b64decode(inbox.file['content']))
    assert len(state['runs']) == 72
    assert ledger.claim_validation(source_run_id='1', consumer_run_id='1000', day='2026-10-14') == 0
    assert ledger.claim_validation(source_run_id='79', consumer_run_id='1000', day='2026-10-14') == 2
    with pytest.raises(QuotaStateError, match='completion_changed'):
        ledger.finish(run_id='79', scope_used=2, extraction_used=6)


@pytest.mark.parametrize('repository', ['RauPro/MDH-SV', 'other/private', '', None])
def test_ledger_rejects_wrong_repository(repository):
    inbox = Inbox()
    inbox.repository = repository
    with pytest.raises(QuotaStateError):
        AllocationLedger(inbox, project_id='test', model='gemini-3.5-flash-lite')
    assert inbox.writes == 0


@pytest.mark.parametrize('run_id,grant', [('0', 1), ('', 1), ('123', 0), ('123', -1), ('123', True)])
def test_invalid_manual_claims_fail_before_any_storage_access(configured, run_id, grant):
    inbox, ledger = configured
    with pytest.raises(QuotaStateError):
        ledger.claim_manual_validation(consumer_run_id=run_id, day='2026-10-14', grant=grant)
    assert inbox.writes == 0
