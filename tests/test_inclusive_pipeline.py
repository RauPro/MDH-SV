"""Inclusive selection routes only grounded positives and twice-confirmed exclusions."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from mdh_pipeline import gemini_review
from test_news_selection import (
    NOW, reference, current_review, rights_review, draft, source_audit, verification,
    RIGHTS_FULL_SCOPE_PROMPT,
)


def workload(refs, *, reviews=(), drafts=(), checks=(), audits=None, rechecks=None, excluded=()):
    inbox = SimpleNamespace(base='/repos/private', call=lambda *args, **kwargs: [])
    return gemini_review._allocation_workload(inbox, {'schema_version': 1, 'records': refs},
        list(drafts), list(reviews), [], excluded=set(excluded), now=NOW,
        scope_audits=audits, verifications=list(checks), rechecks=rechecks)


def recheck(ref, *, reason='confirmed_false_exclusion', digest='c' * 64):
    return {'schema_version': 1, 'requests': [{
        'candidate_id': ref['id'], 'source_content_sha256': digest, 'issue_number': 1,
        'reason_code': reason, 'requested_at': '2026-10-12T15:00:00Z'}]}


def test_first_negative_counts_as_scope_work_and_never_as_terminal_discard():
    ref = reference()
    first = current_review(ref, relevance='not_relevant')
    result = workload([ref], reviews=[first])
    assert result['scope_pending'] == 1
    assert result['selected_ids'] == result['excluded_ids'] == set()
    assert result['extraction_pending'] == result['validation_ready'] == 0


def test_valid_same_source_second_negative_is_blocked_and_no_longer_spends_scope_capacity():
    ref = reference()
    first = current_review(ref, relevance='not_relevant')
    result = workload([ref], reviews=[first], checks=[verification(first)])
    assert result['scope_pending'] == 0
    assert result['selected_ids'] == set() and result['excluded_ids'] == {ref['id']}
    assert result['extraction_pending'] == result['validation_ready'] == 0


def test_second_disagreement_keeps_news_unexcluded_and_waits_for_retry_backoff():
    ref = reference()
    first = current_review(ref, relevance='not_relevant')
    second = current_review(ref, jurisdiction='other', relevance='relevant')
    checked = verification(first, second, outcome='uncertain', reason='unrelated_country')
    result = workload([ref], reviews=[first], checks=[checked])
    assert result['scope_pending'] == 0
    assert result['excluded_ids'] == result['selected_ids'] == set()


def test_updated_second_check_replaces_first_pass_pending_routing_in_same_hour():
    ref = reference()
    first = current_review(ref, relevance='not_relevant')
    inbox = SimpleNamespace(base='/repos/private', call=lambda *args, **kwargs: [])
    result = gemini_review._refresh_scope_workload(inbox, {'schema_version': 1, 'records': [ref]},
        [], [first], [], [], excluded=set(), now=NOW, scope_audits=None,
        verifications=[], updated_verifications=[verification(first)])
    assert result['scope_pending'] == 0 and result['excluded_ids'] == {ref['id']}


def test_stale_second_receipt_never_blocks_newer_first_classification():
    ref = reference()
    first = current_review(ref, relevance='not_relevant')
    checked = verification(first)
    fresh = deepcopy(first)
    fresh['extraction']['prepared_at'] = '2026-10-12T15:30:00Z'
    result = workload([ref], reviews=[fresh], checks=[checked])
    assert result['scope_pending'] == 1 and result['excluded_ids'] == set()


@pytest.mark.parametrize('scope', ['positive', 'unclear'])
def test_audit_recheck_has_priority_even_when_old_classification_would_otherwise_be_cached(scope):
    ref = reference()
    old = rights_review(ref, relevance='relevant' if scope == 'positive' else 'unclear')
    result = workload([ref], reviews=[old], rechecks=recheck(ref))
    assert result['scope_priority_ids'] == {ref['id']}
    assert result['scope_pending'] == 1
    assert not result['excluded_ids']


@pytest.mark.parametrize('with_scope_review', [False, True])
def test_known_bad_body_cannot_route_to_extraction_or_validation_via_a_positive_dossier(with_scope_review):
    ref = reference()
    reviews = [current_review(ref)] if with_scope_review else []
    result = workload([ref], drafts=[draft(ref)], reviews=reviews,
        rechecks=recheck(ref, reason='source_content_mismatch'))
    assert result['selected_ids'] == set()
    assert result['excluded_ids'] == {ref['id']}
    assert result['extraction_pending'] == result['validation_ready'] == 0
    assert result['scope_priority_ids'] == {ref['id']}


def test_old_audit_does_not_override_fresh_inclusive_positive():
    ref = reference()
    current = current_review(ref)
    result = workload([ref], reviews=[current], audits=source_audit(ref))
    assert result['selected_ids'] == {ref['id']}
    assert result['excluded_ids'] == result['scope_priority_ids'] == set()
    assert result['scope_pending'] == 0


def test_fresh_good_dossier_is_not_blocked_by_an_older_bad_scope_body():
    ref = reference()
    old = rights_review(ref)
    fresh = draft(ref)
    fresh['extraction'].update(content_sha256='d' * 64,
        fetched_at='2026-10-12T15:30:00Z', prepared_at='2026-10-12T15:31:00Z')
    result = workload([ref], reviews=[old], drafts=[fresh],
        rechecks=recheck(ref, reason='source_content_mismatch'))
    assert result['selected_ids'] == {ref['id']}
    assert result['excluded_ids'] == set()
    assert result['validation_ready'] == 1


def test_old_positive_v3_stays_eligible_without_new_scope_calls():
    ref = reference()
    result = workload([ref], reviews=[rights_review(ref)])
    assert result['selected_ids'] == {ref['id']}
    assert result['scope_pending'] == 0 and not result['excluded_ids']


def test_new_inclusive_selection_reextracts_old_negative_instead_of_judging_its_empty_dossier():
    ref = reference()
    old = draft(ref)
    old['extraction']['prompt_version'] = RIGHTS_FULL_SCOPE_PROMPT
    for classification in (old['classification'], old['news_dossier']['classification']):
        classification['relevance'] = 'not_relevant'
    result = workload([ref], drafts=[old], reviews=[current_review(ref)])
    assert result['selected_ids'] == {ref['id']}
    assert result['scope_reprocessing_ids'] == {ref['id']}
    assert result['extraction_pending'] == 1 and result['validation_ready'] == 0


def test_old_positive_dossier_does_not_require_reextraction_for_new_inclusive_selection():
    ref = reference()
    old = draft(ref)
    old['extraction']['prompt_version'] = RIGHTS_FULL_SCOPE_PROMPT
    result = workload([ref], drafts=[old], reviews=[current_review(ref)])
    assert result['scope_reprocessing_ids'] == set()
    assert result['extraction_pending'] == 0 and result['validation_ready'] == 1


def test_human_withdrawal_blocks_audit_priority_and_current_positive():
    ref = reference()
    result = workload([ref], reviews=[current_review(ref)], rechecks=recheck(ref), excluded=[ref['id']])
    assert not result['selected_ids'] and result['excluded_ids'] == {ref['id']}
    assert result['scope_pending'] == 0 and result['extraction_pending'] == 0


def test_coordinator_loads_audit_requests_and_second_checks_without_an_extra_provider_grant(tmp_path, monkeypatch):
    from test_scope_first_orchestration import Budget
    refs = [reference(n) for n in range(1, 19)]
    reviews = [current_review(ref, relevance='not_relevant') for ref in refs]
    existing = verification(reviews[1])
    fresh = verification(reviews[2])
    for folder, documents in [('scope-reviews', reviews), ('scope-verifications', [existing])]:
        path = tmp_path / folder; path.mkdir()
        for document in documents:
            (path / (document['candidate_id'] + '.json')).write_text(json.dumps(document))
    request = recheck(refs[0])
    (tmp_path / 'runtime').mkdir()
    (tmp_path / 'runtime/news-scope-rechecks.json').write_text(json.dumps(request))
    audits = tmp_path / 'audits.json'; audits.write_text(json.dumps({'schema_version': 1, 'audits': []}))
    exceptions = tmp_path / 'exceptions.json'; exceptions.write_text(json.dumps({'schema_version': 1, 'decisions': []}))
    config = SimpleNamespace(batch_ceiling=50)
    state = {'remaining': 240}
    shared_scope_budget = Budget(state, config)
    recorded = []
    inbox = SimpleNamespace(base='/repos/private', call=lambda *args, **kwargs: [])
    monkeypatch.setattr(gemini_review, 'editorial_excluded_ids', lambda _: set())
    def classify(_inbox, _drafts, _archive, **kwargs):
        assert kwargs['budget'] is shared_scope_budget
        assert kwargs['max_requests'] == 2
        assert kwargs['verifications'] == [existing]
        assert kwargs['rechecks'] == request and kwargs['priority_ids'] == [refs[0]['id']]
        # Initial classification and second negative check share this same cap.
        kwargs['budget'].charge(); kwargs['budget'].charge()
        return {'selected_ids': [], 'updated_reviews': [], 'updated_verifications': [fresh],
            'http_requests': 2, 'classified': 1, 'verified': 1, 'confirmed_excluded': 1, 'stopped_reason': None}
    def extract(_inbox, **kwargs):
        recorded.append(kwargs)
        assert kwargs['eligible_ids'] == []
        assert kwargs['excluded_ids'] == {refs[1]['id'], refs[2]['id']}
        assert kwargs['scope_reprocessing_ids'] == set()
        return {'attempted': 0, 'http_requests': 0, 'errors': 0, 'news_dossiers': 0, 'stopped_reason': None}
    monkeypatch.setattr(gemini_review, 'process_queue', extract)
    report = gemini_review.process_scope_first(inbox, api_key='unit-test', model='gemini-3.5-flash-lite',
        config=config, budget=shared_scope_budget, archive={'schema_version': 1, 'records': refs},
        inbox_dir=tmp_path, limit=50, now=NOW, scope_worker=classify,
        budget_factory=lambda _inbox, conf: Budget(state, conf),
        exceptions_path=exceptions, scope_audits_path=audits)
    assert report['http_requests'] == report['scope_request_limit'] == 2
    assert report['scope_phase']['verified'] == report['scope_phase']['confirmed_excluded'] == 1
    assert report['http_requests'] + report['validation_request_limit'] <= report['hourly_request_limit']
    assert state['remaining'] == 238
    assert recorded
    rendered = json.dumps(report)
    assert 'updated_reviews' not in rendered and 'updated_verifications' not in rendered
    assert all(ref['id'] not in rendered for ref in refs)
