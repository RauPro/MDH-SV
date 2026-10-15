"""Real worker state transitions with isolated sources and a shared request cap."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from mdh_pipeline import automatic_news_worker as worker
from mdh_pipeline.automatic_news import evaluate_queue, project_dossier, AutomaticNewsError, allocated_validation_grant
from mdh_pipeline.article_source import SourceFetchError
from mdh_pipeline.gemini_budget import QuotaDeferred, QuotaStateError
from mdh_pipeline.gemini_draft import GeminiDraftError
from test_automatic_news import Inbox, KEY, MODEL, NOW, archive, judgment
from test_automatic_news_batch import Budget, items


def setup(tmp_path, count=4):
    rows = items(count)
    folder = tmp_path / 'inbox'
    (folder / 'drafts').mkdir(parents=True)
    remote = {}
    for row in rows:
        identifier = row['reference']['id']
        (folder / 'drafts' / (identifier + '.json')).write_text(json.dumps(row['document']))
        remote['drafts/' + identifier + '.json'] = row['document']
    return rows, Inbox(remote), folder


def evaluated(rows, *, budget, **kwargs):
    budget.reserve({'count': len(rows)})
    return {row['reference']['id']: {'judgment': judgment(), 'usage': None,
        'dossier': project_dossier(row['document']['news_dossier'], judgment(),
            source_hash=row['article']['content_sha256'], model=MODEL, now=NOW)} for row in rows}


def run(rows, inbox, folder, *, budget=None, limit=1, fetch=None, evaluate=evaluated, **kwargs):
    sources = {row['reference']['id']: row['article'] for row in rows}
    return evaluate_queue(inbox, folder, archive(*(row['reference'] for row in rows)),
        api_key=KEY, model=MODEL, budget=budget or Budget(), limit=limit, batch_size=4,
        fetch=fetch or (lambda ref: deepcopy(sources[ref['id']])), batch_evaluate=evaluate, now=NOW, **kwargs)


def receipts(inbox):
    return {path: value[0] for path, value in inbox.files.items() if path.startswith('automatic-news/')}


def test_four_candidates_one_http_reservation_and_four_durable_receipts(tmp_path):
    rows, inbox, folder = setup(tmp_path)
    budget = Budget()
    def check_durable(batch, **kwargs):
        assert len(receipts(inbox)) == 4
        assert all(r['status'] == 'evaluating' and r['attempts'] == 1 for r in receipts(inbox).values())
        return evaluated(batch, **kwargs)
    report = run(rows, inbox, folder, budget=budget, evaluate=check_durable)
    assert report['http_requests'] == budget.requests_this_run == 1
    assert report['evaluated'] == report['publishable'] == 4
    assert report['batch_sizes'] == [4]
    assert all(r['status'] == 'evaluated' and r['dossier']['human_reviewed'] is False for r in receipts(inbox).values())
    assert run(rows, inbox, folder)['http_requests'] == 0


def test_missing_batch_row_cannot_publish_valid_prefix_or_overrun_one_call(tmp_path):
    rows, inbox, folder = setup(tmp_path)
    def missing(batch, **kwargs):
        value = evaluated(batch, **kwargs)
        value.pop(next(iter(value)))
        return value
    report = run(rows, inbox, folder, evaluate=missing)
    assert report['http_requests'] == 1 and report['evaluated'] == 0
    assert report['errors'] == 4
    assert all(r['dossier'] is None and r['attempts'] == 1 for r in receipts(inbox).values())


def test_malformed_batch_splits_only_with_remaining_shared_capacity(tmp_path):
    rows, inbox, folder = setup(tmp_path)
    def splitting(batch, **kwargs):
        if len(batch) == 4:
            kwargs['budget'].reserve({})
            raise AutomaticNewsError('invalid_automatic_batch_response')
        return evaluated(batch, **kwargs)
    report = run(rows, inbox, folder, limit=2, evaluate=splitting)
    assert report['http_requests'] == 2 and report['evaluated'] == 2
    assert report['batch_sizes'] == [4, 2] and report['recovery_splits'] == 1
    assert sorted(r['attempts'] for r in receipts(inbox).values()) == [1, 1, 2, 2]


@pytest.mark.parametrize('consume', [False, True])
def test_quota_deferral_never_refunds_a_shared_http_attempt(tmp_path, consume):
    rows, inbox, folder = setup(tmp_path)
    def deferred(batch, **kwargs):
        if consume:
            kwargs['budget'].reserve({})
        raise QuotaDeferred('provider_cooldown', NOW + timedelta(hours=1))
    report = run(rows, inbox, folder, evaluate=deferred)
    assert report['stopped_reason'] == 'budget_deferred'
    assert report['http_requests'] == int(consume)
    assert all(r['attempts'] == int(consume) and r['dossier'] is None for r in receipts(inbox).values())


def test_source_failure_does_not_poison_other_items_or_use_a_model_call(tmp_path):
    rows, inbox, folder = setup(tmp_path)
    failed = rows[0]['reference']['id']
    sources = {row['reference']['id']: row['article'] for row in rows}
    def fetch(ref):
        if ref['id'] == failed:
            raise SourceFetchError('source_access_restricted')
        return sources[ref['id']]
    report = run(rows, inbox, folder, fetch=fetch)
    assert report['source_unavailable'] == 1 and report['evaluated'] == 3
    assert report['http_requests'] == 1 and report['batch_sizes'] == [3]


def test_source_refresh_and_validation_share_one_grant(tmp_path):
    rows, inbox, folder = setup(tmp_path)
    changed = deepcopy(rows[0]['article'])
    changed['paragraphs'][0]['text'] += ' La fuente publicó una actualización.'
    changed['text'] = '\n\n'.join(p['text'] for p in changed['paragraphs'])
    changed['content_sha256'] = hashlib.sha256(changed['text'].encode()).hexdigest()
    sources = {row['reference']['id']: row['article'] for row in rows}
    sources[rows[0]['reference']['id']] = changed
    def extract(ref, article, *, budget, **kwargs):
        budget.reserve({})
        result = deepcopy(rows[0]['document'])
        result['extraction']['content_sha256'] = article['content_sha256']
        return result
    report = run(rows, inbox, folder, limit=2, fetch=lambda ref: sources[ref['id']], extract=extract)
    assert report['source_refreshed'] == 1 and report['evaluated'] == 3
    assert report['http_requests'] == 2


def test_oversize_pack_is_split_before_spending_quota(tmp_path, monkeypatch):
    rows, inbox, folder = setup(tmp_path)
    original = worker.prepare_evaluation_batch
    def bounded(batch, **kwargs):
        if len(batch) > 2:
            raise AutomaticNewsError('automatic_batch_input_too_large')
        return original(batch, **kwargs)
    monkeypatch.setattr(worker, 'prepare_evaluation_batch', bounded)
    report = run(rows, inbox, folder, limit=2)
    assert report['batch_sizes'] == [2, 2] and report['http_requests'] == 2
    assert report['evaluated'] == 4 and report['recovery_splits'] == 0


def test_deadline_after_source_fetch_prevents_dispatch(tmp_path):
    rows, inbox, folder = setup(tmp_path)
    clock = [0]
    def fetch(ref):
        clock[0] = 61
        return next(row['article'] for row in rows if row['reference']['id'] == ref['id'])
    report = run(rows, inbox, folder, fetch=fetch, max_seconds=60, run_clock=lambda: clock[0])
    assert report['http_requests'] == 0 and report['stopped_reason'] == 'run_time_limit'
    assert not receipts(inbox)


def test_retry_dispatch_checks_deadline_again(tmp_path):
    rows, inbox, folder = setup(tmp_path)
    clock = [0]
    def expires(batch, *, budget, **kwargs):
        budget.reserve({})
        clock[0] = 61
        budget.reserve({})
        pytest.fail('A retry must not be reserved after the deadline')
    report = run(rows, inbox, folder, limit=2, evaluate=expires, max_seconds=60, run_clock=lambda: clock[0])
    assert report['http_requests'] == 1 and report['stopped_reason'] == 'run_time_limit'
    assert all(r['attempts'] == 1 for r in receipts(inbox).values())


def test_uncertain_attempt_write_stops_before_provider_call(tmp_path):
    rows, inbox, folder = setup(tmp_path)
    inbox.call = lambda *args, **kwargs: {'content': {}}
    budget = Budget()
    with pytest.raises(QuotaStateError, match='batch_receipt_write_failed'):
        run(rows, inbox, folder, budget=budget)
    assert budget.requests_this_run == 0


def test_provider_configuration_failure_does_not_split(tmp_path):
    rows, inbox, folder = setup(tmp_path)
    def bad(batch, **kwargs):
        kwargs['budget'].reserve({})
        raise GeminiDraftError('provider_http_error', status_code=400)
    report = run(rows, inbox, folder, limit=4, evaluate=bad)
    assert report['http_requests'] == 1 and report['recovery_splits'] == 0
    assert report['stopped_reason'] == 'provider_configuration'


class AllocationBudget(Budget):
    def __init__(self):
        super().__init__()
        self.config.project_id = 'unit-test-project'
        self.remaining = 240
    def snapshot(self):
        return {**super().snapshot(), 'day': '2026-10-15'}


def allocate(rows, inbox, folder, budget, **kwargs):
    return allocated_validation_grant(inbox, folder, archive(*(row['reference'] for row in rows)),
        config=budget.config, budget=budget, limit=kwargs.pop('limit', 50), batch_size=4,
        source_run_id=kwargs.pop('source_run_id', ''), consumer_run_id='123',
        now=datetime(2026, 10, 15, 7, tzinfo=timezone.utc), **kwargs)


def test_manual_validation_claim_is_shared_fair_share_and_cannot_repeat(tmp_path):
    rows, inbox, folder = setup(tmp_path)
    budget = AllocationBudget()
    assert allocate(rows, inbox, folder, budget) == (10, 'manual_allocation')
    assert allocate(rows, inbox, folder, budget) == (0, 'allocation_unavailable')
    assert budget.requests_this_run == 0  # allocation is not provider consumption


def test_missing_upstream_allocation_never_borrows_manual_capacity(tmp_path):
    rows, inbox, folder = setup(tmp_path)
    budget = AllocationBudget()
    assert allocate(rows, inbox, folder, budget, source_run_id='111') == (0, 'allocation_unavailable')
    assert not inbox.writes


def test_publisher_honors_upstream_reservation_without_recomputing_larger_grant(tmp_path):
    from mdh_pipeline.pipeline_allocation import AllocationLedger
    rows, inbox, folder = setup(tmp_path)
    budget = AllocationBudget()
    ledger = AllocationLedger(inbox, project_id=budget.config.project_id, model=MODEL)
    ledger.create(run_id='111', day='2026-10-15', hourly_grant=10,
        plan={'scope_requests': 2, 'extraction_requests': 6, 'validation_requests': 2, 'unused_requests': 0})
    assert allocate(rows, inbox, folder, budget, source_run_id='111') == (2, 'upstream_allocation')
    assert allocate(rows, inbox, folder, budget, source_run_id='111') == (0, 'allocation_unavailable')


def test_daily_exhaustion_or_no_ready_dossier_does_not_claim_capacity(tmp_path):
    rows, inbox, folder = setup(tmp_path)
    budget = AllocationBudget()
    budget.remaining = 0
    assert allocate(rows, inbox, folder, budget) == (0, 'budget_deferred')
    assert not inbox.writes
    run(rows, inbox, folder)
    budget.remaining = 240
    before = len(inbox.writes)
    assert allocate(rows, inbox, folder, budget) == (0, 'no_validation_work')
    assert len(inbox.writes) == before


def test_explicit_single_item_batches_support_full_allocated_ceiling(tmp_path):
    rows, inbox, folder = setup(tmp_path)
    sources = {row['reference']['id']: row['article'] for row in rows}
    result = evaluate_queue(inbox, folder, archive(*(row['reference'] for row in rows)),
        api_key=KEY, model=MODEL, budget=Budget(), limit=50, batch_size=1,
        batch_evaluate=evaluated, fetch=lambda ref: sources[ref['id']], now=NOW)
    assert result['evaluated'] == 4 and result['batch_sizes'] == [1, 1, 1, 1]
