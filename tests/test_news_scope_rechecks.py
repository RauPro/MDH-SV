"""Audit rechecks never turn stale classifications or corrupt sources into closure."""
from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from mdh_pipeline.news_scope_policy import CURRENT_SCOPE_PROMPTS, SCOPE_PROMPT
from mdh_pipeline.news_scope_rechecks import (
    MAX_ISSUE_NUMBER, blocked_source_hashes, load_rechecks, pending_recheck_ids,
    priority_recheck_ids, validate_rechecks,
)
from test_news_selection import inclusive_review, reference

NOW = datetime(2026, 10, 15, 18, tzinfo=timezone.utc)


def request(ref=None, **changes):
    ref = ref or reference()
    return {"candidate_id": ref["id"], "source_content_sha256": "c" * 64,
            "issue_number": 1187, "reason_code": "confirmed_false_exclusion",
            "requested_at": "2026-10-15T14:00:00Z", **changes}


def document(*requests):
    return {"schema_version": 1, "requests": list(requests or [request()])}


def review(ref=None, *, source_hash="c" * 64, **classification):
    value = inclusive_review(ref, **classification)
    value["extraction"].update(prompt_version=SCOPE_PROMPT, prepared_at="2026-10-15T15:02:00Z")
    value["evidence"]["content_sha256"] = source_hash
    value["source_read"].update(fetched_at="2026-10-15T15:01:00Z", content_sha256=source_hash)
    return value


def test_recheck_receipt_is_private_minimal_copy_and_accepts_all_reason_codes():
    from mdh_pipeline.news_scope_rechecks import REASON_PRIORITY
    source = document(*(request(reference(number), issue_number=number, reason_code=reason)
                        for number, reason in enumerate(REASON_PRIORITY, 1)))
    checked = validate_rechecks(source, now=NOW)
    assert checked == source and checked is not source
    checked["requests"][0]["reason_code"] = "other"
    assert checked != source
    assert all(set(item) == {"candidate_id", "source_content_sha256", "issue_number", "reason_code", "requested_at"}
               for item in source["requests"])


@pytest.mark.parametrize("field,value", [
    ("candidate_id", "c" * 63), ("candidate_id", "C" * 64), ("candidate_id", ["a"]),
    ("source_content_sha256", ""), ("source_content_sha256", "c" * 65),
    ("source_content_sha256", None), ("issue_number", True), ("issue_number", 0),
    ("issue_number", MAX_ISSUE_NUMBER + 1), ("issue_number", 1.0),
    ("reason_code", "irrelevant"), ("reason_code", []),
    ("requested_at", "2026-10-15"), ("requested_at", "2026-10-15T14:00:00"),
    ("requested_at", "2026-02-30T14:00:00Z"), ("requested_at", "2026-10-15T14:00:00+99:00"),
    ("requested_at", "2026-10-15T18:00:01Z"), ("requested_at", None),
])
def test_invalid_request_fields_and_future_timestamps_fail(field, value):
    with pytest.raises(ValueError, match="invalid_news_scope_rechecks"):
        validate_rechecks(document(request(**{field: value})), now=NOW)


@pytest.mark.parametrize("bad", [None, [], {}, {"schema_version": True, "requests": []},
    {"schema_version": 2, "requests": []}, {"schema_version": 1, "requests": {}},
    {"schema_version": 1, "requests": [], "notes": "private"}])
def test_invalid_receipt_container_fails(bad):
    with pytest.raises(ValueError, match="invalid_news_scope_rechecks"):
        validate_rechecks(bad, now=NOW)


def test_extra_missing_duplicate_request_fields_and_duplicates_fail():
    extra = request(notes="private body")
    missing = request()
    del missing["source_content_sha256"]
    same_candidate = request(issue_number=1188)
    same_issue = request(reference(2))
    for value in [document(extra), document(missing), document(request(), same_candidate),
                  document(request(), same_issue), document(None)]:
        with pytest.raises(ValueError, match="invalid_news_scope_rechecks"):
            validate_rechecks(value, now=NOW)


def test_count_and_json_byte_bounds(monkeypatch, tmp_path):
    monkeypatch.setattr("mdh_pipeline.news_scope_rechecks.MAX_REQUESTS", 1)
    with pytest.raises(ValueError, match="invalid_news_scope_rechecks"):
        validate_rechecks(document(request(), request(reference(2), issue_number=2)), now=NOW)
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(document()))
    monkeypatch.setattr("mdh_pipeline.news_scope_rechecks.MAX_BYTES", path.stat().st_size - 1)
    with pytest.raises(ValueError, match="invalid_news_scope_rechecks"):
        load_rechecks(path, now=NOW)


def test_missing_file_returns_empty_and_good_file_validates(tmp_path):
    path = tmp_path / "runtime" / "news-scope-rechecks.json"
    assert load_rechecks(path, now=NOW) == {"schema_version": 1, "requests": []}
    path.parent.mkdir()
    path.write_text(json.dumps(document()))
    assert load_rechecks(path, now=NOW) == document()


def test_symlink_directory_and_dangling_symlink_fail_even_when_target_missing(tmp_path):
    target = tmp_path / "real.json"
    target.write_text(json.dumps(document()))
    path = tmp_path / "symlink.json"
    path.symlink_to(target)
    dangling = tmp_path / "dangling.json"
    dangling.symlink_to(tmp_path / "missing.json")
    directory = tmp_path / "symlink-dir"
    directory.symlink_to(tmp_path, target_is_directory=True)
    for bad in [path, dangling, directory / "real.json", tmp_path]:
        with pytest.raises(ValueError, match="invalid_news_scope_rechecks"):
            load_rechecks(bad, now=NOW)


@pytest.mark.parametrize("raw", [b"", b"{", b"\xff", b'{"schema_version":1,"schema_version":1,"requests":[]}',
    b'{"schema_version":1,"requests":[],"extra":NaN}',
    b'{"schema_version":1,"requests":[{"candidate_id":"a","candidate_id":"b"}]}'])
def test_json_is_strict_and_duplicate_keys_never_override(raw, tmp_path):
    path = tmp_path / "receipt.json"
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="invalid_news_scope_rechecks"):
        load_rechecks(path, now=NOW)


@pytest.mark.parametrize("classification", [
    {"jurisdiction": "SV", "relevance": "relevant"},
    {"jurisdiction": "other", "relevance": "not_relevant"},
    {"jurisdiction": "unclear", "relevance": "unclear"},
])
@pytest.mark.parametrize("prompt", sorted(CURRENT_SCOPE_PROMPTS))
def test_new_complete_policy_assessment_resolves_reprocessing_request(classification, prompt):
    fresh = review(**classification)
    fresh["extraction"]["prompt_version"] = prompt
    assert pending_recheck_ids(document(), [fresh], now=NOW) == set()


def test_legacy_positive_never_resolves_an_explicit_audit_request():
    for prompt in ["mdh-news-scope-2-inclusive-context", "mdh-news-scope-3-rights-nexus"]:
        old = review()
        old["extraction"]["prompt_version"] = prompt
        assert pending_recheck_ids(document(), [old], now=NOW) == {request()["candidate_id"]}


@pytest.mark.parametrize("field,timestamp", [
    ("prepared_at", "2026-10-15T13:59:59Z"), ("prepared_at", "2026-10-15T14:00:00Z"),
    ("prepared_at", "2026-10-15T19:00:00Z"), ("fetched_at", "2026-10-15T13:59:59Z"),
    ("fetched_at", "2026-10-15T14:00:00Z"), ("fetched_at", "2026-10-15T16:00:00Z"),
])
def test_old_equal_future_or_inconsistent_assessment_timestamp_cannot_resolve(field, timestamp):
    stale = review()
    stale["extraction" if field == "prepared_at" else "source_read"][field] = timestamp
    assert pending_recheck_ids(document(), [stale], now=NOW) == {request()["candidate_id"]}


def test_timezone_offsets_compare_instants_not_strings():
    value = document(request(requested_at="2026-10-15T10:00:00-06:00"))
    assert pending_recheck_ids(value, [review()], now=NOW) == {request()["candidate_id"]}
    value["requests"][0]["requested_at"] = "2026-10-15T08:00:00-06:00"
    assert pending_recheck_ids(value, [review()], now=NOW) == set()


def test_invalid_missing_truncated_and_ambiguous_assessments_stay_pending():
    incomplete = review()
    incomplete["source_read"]["truncated"] = True
    mismatch = review()
    mismatch["source_read"]["content_sha256"] = "d" * 64
    forged = review()
    forged["source_url"] = "https://www.diariocolatino.com/another-article/"
    extra = review()
    extra["notes"] = "unexpected private field"
    for reviews in [[], [None], [{}], [incomplete], [mismatch], [forged], [extra],
                    [review(), deepcopy(review())]]:
        assert pending_recheck_ids(document(), reviews, now=NOW) == {request()["candidate_id"]}


def test_known_corrupt_source_body_stays_blocked_until_a_different_body_is_freshly_reviewed():
    # Audit #1187: publisher REST and HTML both bind the same canonical post, but
    # its mining headline accompanies an unrelated governance essay. No body text
    # or heuristic title matching is needed to preserve that explicit finding.
    source_hash = "7b87888c720ab6db8ce9492c456ee4af3e733e9c0340130107faf7f058d15c7c"
    source = document(request(reason_code="source_content_mismatch", source_content_sha256=source_hash))
    assert blocked_source_hashes(source, now=NOW) == {request()["candidate_id"]: source_hash}
    same_body = review(source_hash=source_hash)
    assert pending_recheck_ids(source, [same_body], now=NOW) == {request()["candidate_id"]}
    changed = review(source_hash="d" * 64)
    assert pending_recheck_ids(source, [changed], now=NOW) == set()
    # Even after resolution, a regression to the audited bad body stays blocked.
    assert blocked_source_hashes(source, now=NOW) == {request()["candidate_id"]: source_hash}


def test_ordinary_rechecks_do_not_block_their_source_hash():
    assert blocked_source_hashes(document(), now=NOW) == {}
    assert pending_recheck_ids(document(), [review()], now=NOW) == set()


def test_priority_order_is_stable_and_only_contains_unresolved_requests():
    refs = [reference(n) for n in range(1, 5)]
    source = document(
        request(refs[0], issue_number=1, reason_code="policy_mismatch"),
        request(refs[1], issue_number=2, reason_code="confirmed_false_exclusion"),
        request(refs[2], issue_number=3, reason_code="source_content_mismatch"),
        request(refs[3], issue_number=4, reason_code="confirmed_false_exclusion"),
    )
    assert priority_recheck_ids(source, [review(refs[1])], now=NOW) == [
        refs[2]["id"], refs[3]["id"], refs[0]["id"]]
    assert len(source["requests"]) == 4
