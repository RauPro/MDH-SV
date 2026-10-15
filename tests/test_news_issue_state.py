"""Closing a queue item requires terminal evidence, not an empty public lookup."""
from copy import deepcopy
from datetime import timedelta

import pytest

from mdh_pipeline.news_issue_state import (
    build_issue_state_indexes, build_reopen_index, build_terminal_index, candidate_matches_terminal,
)
from mdh_pipeline.news_selection import project_news_selection
from mdh_pipeline.news_scope_policy import SCOPE_PROMPT
from test_news_exclusions import receipt_fixture
from test_news_selection import (
    NOW, draft, inclusive_review, reference, reviewed, rights_review, scope_review, source_audit,
)


def bibliography(*rows):
    return {"schema_version": 1, "records": list(rows)}


def state(item=None, **overrides):
    item = item or reference()
    arguments = {"archive": bibliography(item), "published_archive": bibliography(),
                 "published_dossiers": {"schema_version": 1, "dossiers": []}, "now": NOW}
    arguments.update(overrides)
    return build_terminal_index(**arguments)


def reopening(item=None, **overrides):
    item = item or reference()
    arguments = {"archive": bibliography(item), "published_archive": bibliography(),
                 "published_dossiers": {"schema_version": 1, "dossiers": []}, "now": NOW}
    arguments.update(overrides)
    return build_reopen_index(**arguments)


def published(item=None, *, automatic=True):
    item = deepcopy(item or reference())
    document = reviewed(item)["dossiers"][0]
    if automatic:
        document.update(schema_version=2, human_reviewed=False, reviewed_by=None,
                        reviewed_at=None, review_notes=None)
        document["fields"]["summary"] = {"value": "La fuente informa sobre derechos humanos.",
            "status": "reported", "evidence_refs": ["primary"], "note": None}
        document["automation"] = {"policy_version": "auto-news-v1", "source_content_sha256": "c" * 64,
            "validated_at": NOW.isoformat(), "method": "gemini_evidence_review",
            "evaluator_model": "gemini-3.5-flash-lite", "corroboration": "single_source"}
        item["selection"] = project_news_selection([draft(item)], bibliography(item), now=NOW)["selections"][0]["selection"]
    return {"published_archive": bibliography(item),
            "published_dossiers": {"schema_version": 1, "dossiers": [document]}}


def negative(item=None, **classification):
    return rights_review(item, relevance="not_relevant", **classification)


def current_review(item=None, **classification):
    value = rights_review(item, **classification)
    value["extraction"]["prompt_version"] = SCOPE_PROMPT
    return value


def confirmed(item=None, **classification):
    item = item or reference()
    value = current_review(item, **{"relevance": "not_relevant", **classification})
    receipt = receipt_fixture(item, value, now=NOW)
    return {"scope_reviews": [receipt["first_review"]], "scope_verifications": [receipt]}


def audit_recheck(item=None, *, reason="confirmed_false_exclusion", **changes):
    item = item or reference()
    return {"schema_version": 1, "requests": [{"candidate_id": item["id"],
        "source_content_sha256": "c" * 64, "issue_number": 1187,
        "reason_code": reason, "requested_at": "2026-10-12T15:00:00Z", **changes}]}


def change_hash(document, digest):
    document["evidence"]["content_sha256"] = digest
    document["source_read"]["content_sha256"] = digest


def test_absence_pending_generation_and_unclear_scope_are_not_terminal():
    assert state() == {}
    assert state(drafts=[draft()]) == {}
    assert state(scope_reviews=[rights_review(jurisdiction="unclear", relevance="unclear")]) == {}
    assert state(scope_reviews=[rights_review()]) == {}


@pytest.mark.parametrize("automatic", [True, False])
def test_actual_valid_production_dossier_is_terminal_without_inventing_review(automatic):
    item = reference()
    result = state(**published(automatic=automatic))[item["id"]]
    assert result == {"candidate_id": item["id"], "status": "published",
        "reason": "published_automatic" if automatic else "published_reviewed",
        **{key: item[key] for key in ("source_url", "publisher", "published_date")},
        "content_sha256": "c" * 64 if automatic else None,
        "evidence_at": NOW.isoformat() if automatic else "2026-10-12"}
    assert "human_reviewed" not in result and "summary" not in result


def test_valid_current_publication_can_match_private_positive_snapshots():
    assert state(**published(), drafts=[draft()], scope_reviews=[rights_review()])


@pytest.mark.parametrize("prompt", ["mdh-private-grounded-5", "mdh-universal-news-grounded-6",
                                   "mdh-universal-news-grounded-7-field-privacy"])
def test_verified_production_and_current_scope_can_bind_a_legacy_extraction(prompt):
    document = draft(); document["extraction"]["prompt_version"] = prompt
    assert state(**published(), drafts=[document], scope_reviews=[rights_review()])[reference()["id"]]["status"] == "published"
    assert state(drafts=[document], scope_reviews=[negative()]) == {}
    assert reopening(drafts=[document], scope_reviews=[rights_review()]) == {}


@pytest.mark.parametrize("mutate", [
    lambda d: d["extraction"].update(content_sha256="d" * 64),
    lambda d: d["extraction"].update(prompt_version="unrecognized-extraction-policy"),
    lambda d: d["extraction"].update(provider="another-provider"),
    lambda d: d["extraction"].update(prepared_at="2026-10-12T16:01:00Z"),
    lambda d: d["extraction"].update(fetched_at="2026-10-12T16:01:00Z"),
    lambda d: d["news_dossier"].update(publisher="Wrong publisher"),
    lambda d: d.update(status="error"),
])
def test_legacy_publication_exception_never_accepts_wrong_identity_hash_policy_or_time(mutate):
    document = draft(); document["extraction"]["prompt_version"] = "mdh-private-grounded-5"
    mutate(document)
    assert state(**published(), drafts=[document], scope_reviews=[rights_review()]) == {}


def test_legacy_publication_requires_complete_positive_current_scope_and_positive_full_draft():
    document = draft(); document["extraction"]["prompt_version"] = "mdh-private-grounded-5"
    assert state(**published(), drafts=[document]) == {}
    assert state(**published(), drafts=[document], scope_reviews=[inclusive_review()]) == {}
    assert state(**published(), drafts=[document], scope_reviews=[negative()]) == {}
    assessment = rights_review(); assessment["source_read"]["truncated"] = True
    assert state(**published(), drafts=[document], scope_reviews=[assessment]) == {}
    assessment = rights_review(); change_hash(assessment, "d" * 64)
    assert state(**published(), drafts=[document], scope_reviews=[assessment]) == {}
    document["classification"]["relevance"] = "not_relevant"
    document["news_dossier"]["classification"]["relevance"] = "not_relevant"
    assert state(**published(), drafts=[document], scope_reviews=[rights_review()]) == {}


def test_actual_editorial_publication_precedes_automatic_negatives_or_failed_private_retries():
    assert state(**published(automatic=False), scope_reviews=[negative()], scope_audits=source_audit())
    invalid = draft(); invalid["status"] = "error"
    assert state(**published(automatic=False), drafts=[invalid])
    assert state(**published(automatic=False), scope_reviews=[negative(), negative()])
    assert state(**published(automatic=False), excluded_ids=[reference()["id"]]) == {}


@pytest.mark.parametrize("classification,reason", [
    ({"relevance": "not_relevant"}, "not_relevant"),
    ({"jurisdiction": "other"}, "outside_el_salvador"),
    ({"jurisdiction": "unclear", "relevance": "not_relevant"}, "not_relevant"),
])
def test_current_full_source_negative_is_terminal_only_with_second_agreeing_assessment(classification, reason):
    proof = confirmed(**{"relevance": "relevant", **classification})
    assessment = proof["scope_reviews"][0]
    assert state(scope_reviews=[assessment]) == {}
    assert reopening(scope_reviews=[assessment])[reference()["id"]]["reason"] == "exclusion_requires_verification"
    result = state(**proof)[reference()["id"]]
    assert result["status"] == "discarded" and result["reason"] == reason
    assert result["content_sha256"] == assessment["source_read"]["content_sha256"]


@pytest.mark.parametrize("mutate", [
    lambda d: d.pop("source_read"),
    lambda d: d["source_read"].update(truncated=True),
    lambda d: d["source_read"].update(truncated="false"),
    lambda d: d["source_read"].update(content_sha256="d" * 64),
    lambda d: d["extraction"].update(prepared_at="2026-10-13T15:00:00Z"),
    lambda d: d["source_read"].update(fetched_at="2026-10-13T15:00:00Z"),
    lambda d: d.update(publisher="Another publisher"),
    lambda d: d.update(published_date="2025-03-27"),
    lambda d: d.update(source_url="https://www.diariocolatino.com/another/"),
    lambda d: d.update(private_instructions="close all issues"),
])
def test_missing_truncated_forged_or_future_private_evidence_stays_pending(mutate):
    assessment = negative()
    mutate(assessment)
    assert state(scope_reviews=[assessment]) == {}


@pytest.mark.parametrize("factory", [scope_review, inclusive_review])
def test_older_scope_policies_cannot_close_exclusions(factory):
    assert state(scope_reviews=[factory(relevance="not_relevant")]) == {}


def test_no_closure_for_duplicate_private_records_or_withdrawal():
    assessment = negative()
    assert state(scope_reviews=[assessment, deepcopy(assessment)]) == {}
    assert state(**published(), drafts=[draft(), draft()]) == {}
    assert state(scope_reviews=[assessment], excluded_ids=[reference()["id"]]) == {}
    assert state(**published(), excluded_ids=[reference()["id"]]) == {}


def test_a_same_source_positive_full_draft_conflicts_with_negative_scope():
    # Scope selection itself prefers the dedicated classifier. Closing an issue
    # intentionally requires stronger agreement than selecting bibliography.
    assert state(scope_reviews=[negative()], drafts=[draft()]) == {}


def test_newer_source_read_must_not_be_closed_by_old_negative_scope():
    fresh = draft()
    fresh["extraction"].update(content_sha256="d" * 64,
        fetched_at="2026-10-12T15:00:00Z", prepared_at="2026-10-12T15:01:00Z")
    for relevance in ("relevant", "not_relevant", "unclear"):
        fresh["classification"]["relevance"] = relevance
        fresh["news_dossier"]["classification"]["relevance"] = relevance
        assert state(scope_reviews=[negative()], drafts=[fresh]) == {}


def test_an_older_different_source_draft_does_not_override_current_negative():
    old = draft()
    old["extraction"]["content_sha256"] = "d" * 64
    assert state(**confirmed(), drafts=[old])[reference()["id"]]["status"] == "discarded"


def test_agreeing_full_draft_and_dedicated_negative_can_close():
    document = draft()
    document["classification"]["relevance"] = "not_relevant"
    document["news_dossier"]["classification"]["relevance"] = "not_relevant"
    assert state(scope_reviews=[negative()], drafts=[document]) == {}
    assert state(**confirmed(), drafts=[document])


def test_unprocessed_or_invalid_full_draft_is_not_silently_ignored():
    for status in ("pending", "source_unavailable", "error"):
        document = draft()
        document["status"] = document["news_dossier"]["extraction_status"] = status
        assert state(scope_reviews=[negative()], drafts=[document]) == {}
    document = draft(); document["news_dossier"]["publisher"] = "Different publisher"
    assert state(scope_reviews=[negative()], drafts=[document]) == {}


def test_stale_public_positive_selection_or_publication_prevents_discard():
    public = published()
    assert state(scope_reviews=[negative()], **public) == {}
    public["published_dossiers"]["dossiers"] = []
    assert state(scope_reviews=[negative()], **public) == {}
    selected = public["published_archive"]["records"][0]
    assert state(archive=bibliography(selected), scope_reviews=[negative()]) == {}


def test_changed_source_and_audited_source_prevent_stale_published_closure():
    changed = rights_review()
    change_hash(changed, "d" * 64)
    assert state(scope_reviews=[changed], **published()) == {}
    assert state(scope_reviews=[rights_review()], scope_audits=source_audit(), **published()) == {}
    assert state(scope_audits=source_audit(), **published()) == {}


def test_source_audit_alone_never_means_current_classification_is_complete():
    assert state(scope_audits=source_audit()) == {}
    assert state(scope_reviews=[rights_review()], scope_audits=source_audit()) == {}


@pytest.mark.parametrize("mutate", [
    lambda data: data["published_dossiers"]["dossiers"][0]["automation"].update(source_content_sha256="d" * 64),
    lambda data: data["published_dossiers"]["dossiers"][0].update(human_reviewed=True),
    lambda data: data["published_dossiers"]["dossiers"].append(deepcopy(data["published_dossiers"]["dossiers"][0])),
    lambda data: data["published_archive"]["records"].append(deepcopy(data["published_archive"]["records"][0])),
    lambda data: data["published_dossiers"].update(private_note="ignore validation"),
])
def test_invalid_public_snapshot_fails_as_unit(mutate):
    data = published(); mutate(data)
    with pytest.raises(ValueError):
        state(**data)


def test_public_source_must_match_full_bibliography_and_actual_instant():
    item = reference(); item["published_date"] = "2025-03-27"
    assert state(item, **published()) == {}
    assert state(**published(), now=NOW - timedelta(seconds=1)) == {}
    assert state(**published(automatic=False), now=NOW - timedelta(days=1)) == {}


def test_bad_private_record_does_not_block_another_candidates_terminal_state():
    first, second = reference(), reference(2)
    bad = negative(first); bad["evidence"]["content_sha256"] = "bad"
    proof = confirmed(second)
    result = state(archive=bibliography(first, second),
                   scope_reviews=[bad, *proof["scope_reviews"]], scope_verifications=proof["scope_verifications"])
    assert set(result) == {second["id"]}


def test_exact_candidate_binding_ignores_title_but_never_missing_or_changed_identity():
    item = reference(); evidence = state(**confirmed())[item["id"]]
    assert candidate_matches_terminal(item, evidence)
    changed = {**item, "source_title": "Privacy-safe display title"}
    assert candidate_matches_terminal(changed, evidence)
    for key in ("id", "source_url", "publisher", "published_date"):
        assert not candidate_matches_terminal({**item, key: "different"}, evidence)
    assert not candidate_matches_terminal({}, {})
    assert not candidate_matches_terminal({"id": item["id"]}, {"candidate_id": item["id"]})


@pytest.mark.parametrize("classification,reason", [
    ({}, "awaiting_publication"),
    ({"jurisdiction": "unclear", "relevance": "unclear"}, "scope_unclear"),
])
def test_explicit_current_positive_or_unclear_source_read_can_reopen(classification, reason):
    result = reopening(scope_reviews=[current_review(**classification)])[reference()["id"]]
    assert result["status"] == "pending" and result["reason"] == reason
    assert result["content_sha256"] == "c" * 64


def test_old_negative_supplies_explicit_reopen_evidence_but_missing_and_corrupt_records_do_not():
    assert reopening() == {}
    assert reopening(scope_reviews=[negative()])[reference()["id"]]["reason"] == "exclusion_requires_verification"
    invalid = rights_review(); invalid["source_read"]["content_sha256"] = "broken"
    assert reopening(scope_reviews=[invalid]) == {}
    assert reopening(scope_reviews=[rights_review(), rights_review()]) == {}
    invalid = draft(); invalid["status"] = "error"
    assert reopening(scope_reviews=[rights_review()], drafts=[invalid]) == {}


def test_reopen_requires_current_complete_and_unconflicted_evidence():
    for value in (inclusive_review(), scope_review()):
        assert reopening(scope_reviews=[value]) == {}
    truncated = rights_review(); truncated["source_read"]["truncated"] = True
    assert reopening(scope_reviews=[truncated]) == {}
    assert reopening(scope_reviews=[rights_review()], scope_audits=source_audit()) == {}
    assert reopening(scope_reviews=[rights_review()], excluded_ids=[reference()["id"]]) == {}
    conflicting = draft()
    conflicting["classification"]["relevance"] = "not_relevant"
    conflicting["news_dossier"]["classification"]["relevance"] = "not_relevant"
    assert reopening(scope_reviews=[rights_review()], drafts=[conflicting]) == {}


def test_actual_publication_does_not_reopen_on_matching_or_older_private_snapshot():
    assert reopening(**published(), scope_reviews=[rights_review()]) == {}
    assert reopening(**published(), scope_reviews=[negative()]) == {}
    assert reopening(**published(automatic=False), scope_reviews=[negative()]) == {}
    changed = rights_review(); change_hash(changed, "d" * 64)
    assert reopening(**published(), scope_reviews=[changed]) == {}


def test_newer_changed_source_can_reopen_while_old_automatic_dossier_is_still_deployed():
    data = published()
    data["published_dossiers"]["dossiers"][0]["automation"]["validated_at"] = "2026-10-12T14:00:00Z"
    changed = rights_review(); change_hash(changed, "d" * 64)
    assert reopening(**data, scope_reviews=[changed])[reference()["id"]]["reason"] == "awaiting_publication"
    assert state(**data, scope_reviews=[changed]) == {}


def test_combined_projection_returns_disjoint_terminal_and_explicit_reopen_indexes():
    first, second = reference(), reference(2)
    proof = confirmed(first)
    terminal, pending = build_issue_state_indexes(archive=bibliography(first, second),
        published_archive=bibliography(), published_dossiers={"schema_version": 1, "dossiers": []},
        scope_reviews=[*proof["scope_reviews"], rights_review(second)],
        scope_verifications=proof["scope_verifications"], now=NOW)
    assert set(terminal) == {first["id"]} and set(pending) == {second["id"]}


@pytest.mark.parametrize("factory", [scope_review, inclusive_review, rights_review, current_review])
def test_no_single_pass_negative_can_automatically_close_or_reclose_an_issue(factory):
    assessment = factory(relevance="not_relevant")
    assert state(scope_reviews=[assessment]) == {}
    if "source_read" in assessment:
        assert reopening(scope_reviews=[assessment])[reference()["id"]]["reason"] == "exclusion_requires_verification"


@pytest.mark.parametrize("mutation", [
    lambda receipt: receipt.update(content_sha256="d" * 64),
    lambda receipt: receipt.update(candidate_id="d" * 64),
    lambda receipt: receipt.update(first_review_sha256="d" * 64),
    lambda receipt: receipt.update(source_url="https://www.diariocolatino.com/wrong/"),
    lambda receipt: receipt.update(publisher="Another publisher"),
    lambda receipt: receipt.update(published_date="2025-03-27"),
    lambda receipt: receipt.update(checked_at="2026-10-13T16:00:00Z"),
    lambda receipt: receipt.update(reason_paragraph_ids=[]),
    lambda receipt: receipt["second_review"]["source_read"].update(truncated=True),
    lambda receipt: receipt["second_review"]["classification"].update(relevance="relevant"),
    lambda receipt: receipt["second_review"]["extraction"].update(prompt_version=SCOPE_PROMPT),
    lambda receipt: receipt.update(private_note="close it"),
])
def test_second_receipt_requires_exact_identity_evidence_policy_and_full_source(mutation):
    proof = confirmed()
    mutation(proof["scope_verifications"][0])
    assert state(**proof) == {}
    assert reopening(**proof)[reference()["id"]]["reason"] == "exclusion_requires_verification"


@pytest.mark.parametrize("outcome", ["retained", "uncertain"])
def test_disagreeing_or_unclear_second_assessment_never_closes(outcome):
    receipt = receipt_fixture(reference(), outcome=outcome, now=NOW)
    proof = {"scope_reviews": [receipt["first_review"]], "scope_verifications": [receipt]}
    assert state(**proof) == {}
    assert reopening(**proof)[reference()["id"]]["reason"] == "exclusion_requires_verification"


@pytest.mark.parametrize("reason", ["confirmed_false_exclusion", "policy_mismatch", "source_content_mismatch"])
def test_explicit_audit_recheck_precedes_old_negative_and_existing_automatic_publication(reason):
    request = audit_recheck(reason=reason)
    for snapshot in [{}, published()]:
        assert state(scope_reviews=[negative()], rechecks=request, **snapshot) == {}
        pending = reopening(scope_reviews=[negative()], rechecks=request, **snapshot)[reference()["id"]]
        assert pending["reason"] == ("source_content_mismatch" if reason == "source_content_mismatch" else "audit_recheck")
        assert pending["content_sha256"] == request["requests"][0]["source_content_sha256"]
        assert pending["evidence_at"] == request["requests"][0]["requested_at"]
        assert all(pending[key] == reference()[key] for key in ("source_url", "publisher", "published_date"))


def test_explicit_recheck_does_not_resolve_from_legacy_positive_or_pre_request_current_negative():
    request = audit_recheck()
    assert reopening(scope_reviews=[rights_review()], rechecks=request)[reference()["id"]]["reason"] == "audit_recheck"
    assert reopening(scope_reviews=[current_review(relevance="not_relevant")],
                     rechecks=request)[reference()["id"]]["reason"] == "audit_recheck"
    # Full fresh evidence plus an independent confirmation can resolve the request.
    proof = confirmed()
    for assessment in [proof["scope_reviews"][0], proof["scope_verifications"][0]["second_review"]]:
        assessment["source_read"]["fetched_at"] = "2026-10-12T15:30:00Z"
    from mdh_pipeline.news_exclusions import review_sha256
    proof["scope_verifications"][0]["first_review_sha256"] = review_sha256(proof["scope_reviews"][0])
    assert state(**proof, rechecks=request)[reference()["id"]]["status"] == "discarded"
    assert reopening(**proof, rechecks=request) == {}


def test_known_corrupt_body_never_resolves_recheck_even_with_two_new_agreeing_assessments():
    proof = confirmed()
    for assessment in [proof["scope_reviews"][0], proof["scope_verifications"][0]["second_review"]]:
        assessment["source_read"]["fetched_at"] = "2026-10-12T15:30:00Z"
    from mdh_pipeline.news_exclusions import review_sha256
    proof["scope_verifications"][0]["first_review_sha256"] = review_sha256(proof["scope_reviews"][0])
    request = audit_recheck(reason="source_content_mismatch")
    assert state(**proof, rechecks=request) == {}
    assert reopening(**proof, rechecks=request)[reference()["id"]]["reason"] == "source_content_mismatch"


def test_recheck_for_unknown_candidate_cannot_borrow_another_bibliographic_identity():
    assert reopening(rechecks=audit_recheck(reference(2))) == {}
    assert state(rechecks=audit_recheck(reference(2))) == {}
