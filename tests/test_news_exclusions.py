"""Second scope checks are blind, grounded, source-isolated and quota-accounted."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from mdh_pipeline.gemini_budget import MODEL, QuotaDeferred
from mdh_pipeline.gemini_draft import GeminiDraftError
from mdh_pipeline.news_exclusions import (
    EXCLUSION_PROMPT, MAX_VERIFICATION_ITEMS, confirmed_exclusion, prepare_verification_batch,
    review_sha256, scope_needs_work, validate_verification, verify_exclusions_batch,
)
from mdh_pipeline.news_scope_policy import SCOPE_PROMPT
from test_gemini_budget import ready
from test_news_scope import item, Session, Response, KEY

NOW = datetime(2026, 10, 12, 16, tzinfo=timezone.utc)


def first_review(value, *, jurisdiction="SV", relevance="not_relevant"):
    ref, article = value["reference"], value["article"]
    return {"schema_version": 2, "candidate_id": ref["id"],
        **{key: ref[key] for key in ("source_url", "publisher", "published_date")},
        "classification": {"jurisdiction": jurisdiction, "relevance": relevance},
        "evidence": {"content_sha256": article["content_sha256"],
                     "jurisdiction_paragraph_ids": ["p1"] if jurisdiction == "SV" else [],
                     "relevance_paragraph_ids": ["p2"] if relevance == "relevant" else []},
        "source_read": {key: article[key] for key in ("fetched_at", "extraction_method", "truncated", "content_sha256")},
        "extraction": {"provider": "gemini", "model": MODEL, "prompt_version": SCOPE_PROMPT,
                       "prepared_at": NOW.isoformat()}}


def verification_item(n=1, **classification):
    source = item(n)
    source["first_review"] = first_review(source, **classification)
    return source


def assessment(value, n=1, *, jurisdiction="SV", relevance="not_relevant"):
    reason = ("within_scope" if jurisdiction == "SV" and relevance == "relevant" else "unrelated_country"
              if jurisdiction == "other" else "unrelated_subject" if relevance == "not_relevant" else "insufficient_evidence")
    return {"candidate_id": value["reference"]["id"], "content_sha256": value["article"]["content_sha256"],
        "jurisdiction": jurisdiction, "relevance": relevance,
        "jurisdiction_paragraph_ids": [f"n{n}-p1"] if jurisdiction != "unclear" else [],
        "relevance_paragraph_ids": [f"n{n}-p2"] if relevance != "unclear" else [],
        "reason_code": reason, "reason_paragraph_ids": [f"n{n}-p2"] if reason != "insufficient_evidence" else []}


def response(rows, finish="STOP"):
    return Response({"candidates": [{"finishReason": finish, "content": {"parts": [{"text": json.dumps({"assessments": rows})}]}}]})


def run(values=None, rows=None, *, session=None, budget=None):
    values = values or [verification_item()]
    budget = budget or ready()[0]
    session = session or Session([response(rows if rows is not None else [assessment(value, n) for n, value in enumerate(values, 1)])])
    return verify_exclusions_batch(values, api_key=KEY, model=MODEL, budget=budget, request_session=session, now=NOW)


def receipt_fixture(reference, first=None, *, outcome="confirmed_excluded", now=NOW):
    """Source-free valid fixture for receipt consumers; does not call any model."""
    from mdh_pipeline.news_exclusions import POLICY_VERSION
    if first is None:
        from test_news_selection import inclusive_review
        first = inclusive_review(reference, relevance="not_relevant")
        first["extraction"]["prompt_version"] = SCOPE_PROMPT
    first = deepcopy(first)
    first["extraction"]["prompt_version"] = SCOPE_PROMPT
    first["extraction"]["prepared_at"] = now.isoformat()
    second = deepcopy(first)
    second["extraction"]["prompt_version"] = EXCLUSION_PROMPT
    second["evidence"]["jurisdiction_paragraph_ids"] = ["p1"]
    second["evidence"]["relevance_paragraph_ids"] = ["p1"]
    if outcome == "retained":
        second["classification"] = {"jurisdiction": "SV", "relevance": "relevant"}
    elif outcome == "uncertain":
        second["classification"] = {"jurisdiction": "unclear", "relevance": "unclear"}
    reason = ("within_scope" if outcome == "retained" else "insufficient_evidence" if outcome == "uncertain"
              else "unrelated_country" if second["classification"]["jurisdiction"] == "other" else "unrelated_subject")
    return {"schema_version": 1, "candidate_id": reference["id"],
        **{key: reference[key] for key in ("source_url", "publisher", "published_date")},
        "policy_version": POLICY_VERSION, "content_sha256": first["evidence"]["content_sha256"],
        "first_review_sha256": review_sha256(first), "first_review": first, "second_review": second,
        "outcome": outcome, "reason_code": reason, "reason_paragraph_ids": [] if outcome == "uncertain" else ["p1"],
        "checked_at": now.isoformat(), "next_retry_at": (now + timedelta(days=7)).isoformat() if outcome == "uncertain" else None}


def test_eight_independent_second_assessments_use_one_shared_request_and_restore_ids():
    values = [verification_item(n) for n in range(1, 9)]
    budget, ledger, _ = ready()
    session = Session([response([assessment(value, n) for n, value in reversed(list(enumerate(values, 1)))])])
    result = run(values, session=session, budget=budget)
    assert len(result) == MAX_VERIFICATION_ITEMS == 8
    assert budget.requests_this_run == ledger.document()["used_requests"] == len(session.calls) == 1
    for receipt, value in zip(result, values):
        assert validate_verification(receipt, value["first_review"], value["reference"], now=NOW) == receipt
        assert confirmed_exclusion(receipt, value["first_review"], value["reference"], now=NOW)
        assert receipt["second_review"]["evidence"]["relevance_paragraph_ids"] == ["p2"]
    payload = session.calls[0][1]["json"]
    sent = json.loads(payload["contents"][0]["parts"][0]["text"])
    assert "first_review" not in json.dumps(sent) and "classification" not in json.dumps(sent)
    assert sent["untrusted_articles"][0]["paragraphs"][0]["id"] == "n1-p1"
    assert "PRIVATE_QUOTE_OR_IDENTITY_SENTINEL" not in json.dumps(result)
    assert "PRIVATE_QUOTE_OR_IDENTITY_SENTINEL" not in json.dumps(ledger.document())


@pytest.mark.parametrize("mutate", [
    lambda rows: rows[1].update(content_sha256="f" * 64),
    lambda rows: rows[1].update(candidate_id=rows[0]["candidate_id"]),
    lambda rows: rows[1].update(relevance_paragraph_ids=["n1-p2"]),
    lambda rows: rows[1].update(reason_paragraph_ids=["n1-p2"]),
    lambda rows: rows[1].update(reason_paragraph_ids=[]),
    lambda rows: rows[1].update(reason_code="within_scope"),
    lambda rows: rows[1].update(relevance_paragraph_ids=[]),
    lambda rows: rows.pop(),
])
def test_malformed_second_response_never_returns_a_valid_prefix(mutate):
    values = [verification_item(1), verification_item(2)]
    rows = [assessment(value, n) for n, value in enumerate(values, 1)]
    mutate(rows)
    with pytest.raises(GeminiDraftError):
        run(values, rows)


@pytest.mark.parametrize("mutation", [
    lambda value: value["article"].update(truncated=True),
    lambda value: value["first_review"]["source_read"].update(truncated=True),
    lambda value: value["first_review"]["extraction"].update(prompt_version="mdh-news-scope-3-rights-nexus"),
    lambda value: value["first_review"]["evidence"].update(content_sha256="e" * 64),
    lambda value: value["first_review"]["classification"].update(relevance="relevant"),
])
def test_invalid_first_source_binding_never_spends_quota(mutation):
    value = verification_item()
    mutation(value)
    budget, ledger, _ = ready()
    session = Session([])
    with pytest.raises(GeminiDraftError):
        run([value], session=session, budget=budget)
    assert session.calls == [] and ledger.document()["used_requests"] == 0


def test_second_positive_retains_story_and_does_not_confirm_exclusion():
    value = verification_item()
    receipt = run([value], [assessment(value, relevance="relevant")])[0]
    assert receipt["outcome"] == "retained"
    assert not confirmed_exclusion(receipt, value["first_review"], value["reference"], now=NOW)
    assert validate_verification(receipt, receipt["second_review"], value["reference"], now=NOW) == receipt


def test_different_negative_axes_remain_uncertain_with_seven_day_backoff():
    value = verification_item(jurisdiction="SV", relevance="not_relevant")
    receipt = run([value], [assessment(value, jurisdiction="other", relevance="relevant")])[0]
    assert receipt["outcome"] == "uncertain"
    assert not scope_needs_work(value["first_review"], receipt, value["reference"], now=NOW + timedelta(days=6))
    assert scope_needs_work(value["first_review"], receipt, value["reference"], now=NOW + timedelta(days=7))
    changed = deepcopy(value["first_review"])
    changed["evidence"]["content_sha256"] = changed["source_read"]["content_sha256"] = "d" * 64
    assert scope_needs_work(changed, receipt, value["reference"], now=NOW)


@pytest.mark.parametrize("field,value", [
    ("outcome", "retained"), ("policy_version", "old"), ("first_review_sha256", "a" * 64),
    ("content_sha256", "a" * 64), ("reason_paragraph_ids", []), ("next_retry_at", NOW.isoformat()),
    ("checked_at", (NOW + timedelta(days=1)).isoformat()),
])
def test_receipt_tampering_never_authorizes_a_close(field, value):
    source = verification_item()
    receipt = run([source])[0]
    receipt[field] = value
    assert not confirmed_exclusion(receipt, source["first_review"], source["reference"], now=NOW)


def test_one_initial_negative_is_work_pending_and_legacy_positive_is_preserved():
    value = verification_item()
    assert scope_needs_work(value["first_review"], None, value["reference"], now=NOW)
    assert not confirmed_exclusion(None, value["first_review"], value["reference"], now=NOW)
    prior = deepcopy(value["first_review"])
    prior["extraction"]["prompt_version"] = "mdh-news-scope-3-rights-nexus"
    assert scope_needs_work(prior, None, value["reference"], now=NOW)
    prior["classification"]["relevance"] = "relevant"
    prior["evidence"]["relevance_paragraph_ids"] = ["p2"]
    assert not scope_needs_work(prior, None, value["reference"], now=NOW)


def test_two_passes_cannot_reserve_past_shared_request_grant():
    value = verification_item()
    budget, ledger, _ = ready()
    budget.set_request_limit(1)
    run([value], budget=budget)
    blocked = Session([])
    with pytest.raises(QuotaDeferred):
        run([value], session=blocked, budget=budget)
    assert blocked.calls == [] and ledger.document()["used_requests"] == 1
