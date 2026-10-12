"""Full bibliography classification, durable source retry and bounded private writes."""
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace

import pytest

from mdh_pipeline.article_source import SourceFetchError
from mdh_pipeline.gemini_budget import QuotaDeferred
from mdh_pipeline.gemini_draft import GeminiDraftError
from mdh_pipeline.news_scope import MAX_BATCH_ITEMS, SCOPE_PROMPT, prepare_scope_batch
from mdh_pipeline.scope_review_queue import recheck_scope, load_scope_cache, validate_scope_attempt
from test_news_selection import archive, draft, reference, scope_review, inclusive_review

NOW = datetime(2026, 10, 12, 16, 0, tzinfo=timezone.utc)


def current_review(ref, **classification):
    value = inclusive_review(ref, **classification)
    value["extraction"]["prompt_version"] = SCOPE_PROMPT
    return value


class Inbox:
    base = "/repos/example/private"

    def __init__(self):
        self.files, self.gets, self.puts = {}, [], []
        self.invalid_receipt = False

    def get_file(self, path):
        self.gets.append(path)
        return deepcopy(self.files.get(path))

    def call(self, method, path, **kwargs):
        assert method == "PUT"
        payload = kwargs["json"]
        if path in self.files:
            assert payload["sha"] == self.files[path]["sha"]
        self.puts.append((path, deepcopy(payload)))
        raw = base64.b64decode(payload["content"])
        self.files[path] = {"sha": hashlib.sha256(raw).hexdigest(), "encoding": "base64",
            "size": len(raw), "content": payload["content"], "type": "file",
            "path": path.split("/contents/", 1)[1]}
        return {} if self.invalid_receipt else {"content": {"sha": self.files[path]["sha"]}}

    def documents(self, folder):
        return [json.loads(base64.b64decode(value["content"])) for path, value in self.files.items()
                if f"/contents/{folder}/" in path]


class Budget:
    config = SimpleNamespace(model="gemini-3.5-flash-lite", batch_ceiling=50, input_tpm=100_000)

    def __init__(self):
        self.requests_this_run = 0
        self.request_limit = None

    def snapshot(self):
        return {"remaining_requests": 50}

    def set_request_limit(self, value):
        assert self.request_limit is None or self.request_limit == value
        self.request_limit = value


def fixtures(count=1):
    refs = [reference(i) for i in range(1, count + 1)]
    drafts = [draft(r) for r in refs]
    for value in drafts:
        value["extraction"]["prompt_version"] = "mdh-universal-news-grounded-7-field-privacy"
    return refs, drafts, archive(*refs)


def fetched(ref, *, length=110):
    text = "La fuente describe en El Salvador las condiciones de hacinamiento y reinserción de personas detenidas. "
    paragraphs = [{"id": f"p{n + 1}", "text": (text + "x" * 4000)[:min(3990, length - n * 3990)]}
                  for n in range((length + 3989) // 3990)]
    body = "\n\n".join(p["text"] for p in paragraphs)
    return {"source_url": ref["source_url"], "published_date": ref["published_date"],
        "title": ref["title"], "text": body, "paragraphs": paragraphs,
        "content_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "fetched_at": "2026-10-12T14:00:00+00:00", "extraction_method": "wordpress_rest_content", "truncated": False}


def classify(items, *, budget, model, **kwargs):
    assert budget.requests_this_run < budget.request_limit
    prepare_scope_batch(items)
    budget.requests_this_run += 1
    return [{"schema_version": 2, "candidate_id": value["reference"]["id"],
        **{key: value["reference"][key] for key in ("source_url", "publisher", "published_date")},
        "classification": {"jurisdiction": "SV", "relevance": "relevant"},
        "evidence": {"content_sha256": value["article"]["content_sha256"],
                     "jurisdiction_paragraph_ids": ["p1"], "relevance_paragraph_ids": ["p1"]},
        "source_read": {key: value["article"][key]
                       for key in ("fetched_at", "extraction_method", "truncated", "content_sha256")},
        "extraction": {"provider": "gemini", "model": model, "prompt_version": SCOPE_PROMPT,
                       "prepared_at": NOW.isoformat()}} for value in items]


def run(inbox, budget, drafts, data, **kwargs):
    options = dict(fetch=fetched, classify=classify, now=NOW, sleep=lambda _: None)
    options.update(kwargs)
    return recheck_scope(inbox, drafts, data, api_key="fake-unit-test-key", model=budget.config.model,
                         budget=budget, **options)


def test_eight_archive_articles_without_dossiers_or_issues_share_one_request():
    refs, _, data = fixtures(8)
    inbox, budget = Inbox(), Budget()
    result = run(inbox, budget, [], data)
    assert result["reviewed"] == result["classified"] == result["accepted"] == 8
    assert result["http_requests"] == 1 and result["sources_checked"] == 8
    assert result["selected_ids"] == sorted(r["id"] for r in refs)
    assert result["remaining"] == 0
    assert len(inbox.files) == 8 and all("scope-reviews/" in path for path in inbox.files)
    assert all("hacinamiento" not in base64.b64decode(value["content"]).decode() for value in inbox.files.values())


def test_default_sixteen_item_batch_respects_single_request_budget_and_leaves_seventeenth_pending():
    _, _, data = fixtures(17)
    result = run(Inbox(), Budget(), [], data, max_requests=1)
    assert result["classified"] == result["sources_checked"] == MAX_BATCH_ITEMS == 16
    assert result["http_requests"] == 1 and result["remaining"] == 1
    assert result["stopped_reason"] == "run_limit"


def test_seventeenth_short_article_spills_into_a_second_request_without_mixing_identities():
    refs, _, data = fixtures(17)
    batches, inbox = [], Inbox()
    def checked(items, **kwargs):
        batches.append([value["reference"]["id"] for value in items])
        return classify(items, **kwargs)
    result = run(inbox, Budget(), [], data, classify=checked)
    assert [len(batch) for batch in batches] == [16, 1]
    assert result["classified"] == 17 and result["http_requests"] == 2 and result["remaining"] == 0
    assert {identifier for batch in batches for identifier in batch} == {r["id"] for r in refs}
    assert {value["candidate_id"] for value in inbox.documents("scope-reviews")} == {r["id"] for r in refs}


def test_sixteen_long_articles_adapt_to_all_existing_payload_and_source_caps():
    refs, _, data = fixtures(16)
    batches = []
    def checked(items, **kwargs):
        from mdh_pipeline.gemini_budget import estimate_input_tokens
        _, payload = prepare_scope_batch(items)
        assert estimate_input_tokens(payload) <= 100_000
        assert sum(len(value["article"]["text"].encode()) for value in items) <= 192_000
        assert all(len(value["article"]["text"]) <= 24_000 for value in items)
        assert payload["generationConfig"]["maxOutputTokens"] == 4096
        batches.append([value["reference"]["id"] for value in items])
        return classify(items, **kwargs)
    result = run(Inbox(), Budget(), [], data, max_requests=8,
                 fetch=lambda r: fetched(r, length=23_000), classify=checked)
    assert result["classified"] == 16 and result["remaining"] == 0
    assert result["http_requests"] == len(batches) > 1
    assert all(len(batch) < MAX_BATCH_ITEMS for batch in batches)
    assert sorted(identifier for batch in batches for identifier in batch) == sorted(r["id"] for r in refs)


def test_incomplete_sixteen_item_result_persists_no_partial_private_decisions():
    _, _, data = fixtures(16)
    inbox = Inbox()
    def incomplete(items, **kwargs):
        assert len(items) == 16
        kwargs["budget"].requests_this_run += 1
        raise GeminiDraftError("incomplete_model_response")
    result = run(inbox, Budget(), [], data, classify=incomplete, max_requests=1)
    assert result["stopped_reason"] == "model_error" and result["error_code"] == "incomplete_model_response"
    assert result["classified"] == 0 and result["http_requests"] == 1 and inbox.puts == []
    assert result["model_errors"] == 1 and result["model_deferred_articles"] == 16


def test_identity_failure_recovers_only_after_both_halves_pass_all_checks():
    refs, _, data = fixtures(16)
    inbox, sizes = Inbox(), []
    def mismatched_parent(items, **kwargs):
        sizes.append(len(items))
        if len(items) == 16:
            kwargs["budget"].requests_this_run += 1
            raise GeminiDraftError("scope_identity_mismatch")
        return classify(items, **kwargs)
    result = run(inbox, Budget(), [], data, classify=mismatched_parent, max_requests=3)
    assert sizes == [16, 8, 8] and result["http_requests"] == 3
    assert result["classified"] == 16 and len(inbox.puts) == 16
    assert result["selected_ids"] == sorted(ref["id"] for ref in refs)
    assert result["model_errors"] == result["model_recovery_splits"] == 1
    assert result["model_recovered_articles"] == 16
    assert result["model_failed_articles"] == result["model_deferred_articles"] == 0
    assert result["stopped_reason"] is None


def test_successful_first_batch_is_preserved_when_next_batch_needs_recovery():
    refs, _, data = fixtures(32)
    inbox, sizes = Inbox(), []
    def one_bad_batch(items, **kwargs):
        sizes.append(len(items))
        if len(sizes) == 2:
            assert len(inbox.puts) == 16
            kwargs["budget"].requests_this_run += 1
            raise GeminiDraftError("scope_identity_mismatch")
        return classify(items, **kwargs)
    result = run(inbox, Budget(), [], data, classify=one_bad_batch, max_requests=4)
    assert sizes == [16, 16, 8, 8]
    assert result["classified"] == len(inbox.puts) == 32
    assert result["http_requests"] == 4 and result["model_recovered_articles"] == 16
    assert result["selected_ids"] == sorted(ref["id"] for ref in refs)


def test_recursive_split_reaches_singletons_without_exceeding_shared_allocation():
    _, _, data = fixtures(16)
    sizes = []
    def only_singletons(items, **kwargs):
        sizes.append(len(items))
        if len(items) > 1:
            kwargs["budget"].requests_this_run += 1
            raise GeminiDraftError("invalid_scope_response")
        return classify(items, **kwargs)
    result = run(Inbox(), Budget(), [], data, classify=only_singletons, max_requests=31)
    assert set(sizes) == {16, 8, 4, 2, 1}
    assert len(sizes) == result["http_requests"] == 31
    assert result["classified"] == result["model_recovered_articles"] == 16
    assert result["model_errors"] == result["model_recovery_splits"] == 15
    assert result["stopped_reason"] is None


def test_singleton_failure_stays_visible_while_unaffected_halves_and_later_news_continue():
    refs, _, data = fixtures(17)
    bad = sorted(refs, key=lambda ref: (ref["published_date"], ref["id"]))[0]["id"]
    inbox = Inbox()
    def one_bad_article(items, **kwargs):
        if any(item["reference"]["id"] == bad for item in items):
            kwargs["budget"].requests_this_run += 1
            raise GeminiDraftError("ungrounded_scope_evidence")
        return classify(items, **kwargs)
    result = run(inbox, Budget(), [], data, classify=one_bad_article, max_requests=20)
    assert result["classified"] == 16 and len(inbox.puts) == 16
    assert bad not in result["selected_ids"] and len(result["selected_ids"]) == 16
    assert result["model_failed_articles"] == 1 and result["model_deferred_articles"] == 0
    assert result["model_recovered_articles"] == 15
    assert result["stopped_reason"] == "model_error" and result["error_code"] == "ungrounded_scope_evidence"


def test_recovery_preserves_valid_half_and_defers_the_other_at_request_ceiling():
    _, _, data = fixtures(16)
    sizes = []
    def broken_parent(items, **kwargs):
        sizes.append(len(items))
        if len(items) == 16:
            kwargs["budget"].requests_this_run += 1
            raise GeminiDraftError("scope_identity_mismatch")
        return classify(items, **kwargs)
    result = run(Inbox(), Budget(), [], data, classify=broken_parent, max_requests=2)
    assert sizes == [16, 8] and result["http_requests"] == 2
    assert result["classified"] == result["model_recovered_articles"] == 8
    assert result["model_failed_articles"] == 0 and result["model_deferred_articles"] == 8
    assert result["stopped_reason"] == "model_error"


def test_model_recovery_checks_the_same_deadline_before_splitting():
    _, _, data = fixtures(16)
    tick = [0]
    def late_failure(items, **kwargs):
        kwargs["budget"].requests_this_run += 1
        tick[0] = 721
        raise GeminiDraftError("scope_identity_mismatch")
    result = run(Inbox(), Budget(), [], data, classify=late_failure, max_requests=20, clock=lambda: tick[0])
    assert result["http_requests"] == 1 and result["model_recovery_splits"] == 0
    assert result["model_deferred_articles"] == 16 and result["classified"] == 0
    assert result["stopped_reason"] == "model_error"


def test_global_quota_pause_during_recovery_never_attempts_the_second_half():
    _, _, data = fixtures(16)
    sizes = []
    def quota_after_parent(items, **kwargs):
        sizes.append(len(items))
        kwargs["budget"].requests_this_run += 1
        if len(items) == 16:
            raise GeminiDraftError("scope_identity_mismatch")
        raise QuotaDeferred("provider_cooldown", NOW)
    result = run(Inbox(), Budget(), [], data, classify=quota_after_parent, max_requests=20)
    assert sizes == [16, 8] and result["http_requests"] == 2
    assert result["quota_reason"] == "provider_cooldown" and result["halt_reason"] == "budget_deferred"
    assert result["stopped_reason"] == "model_error" and result["model_deferred_articles"] == 16


@pytest.mark.parametrize("code", ["provider_http_error", "provider_blocked", "provider_unavailable", "invalid_provider_configuration"])
def test_provider_failures_are_not_retried_by_splitting(code):
    _, _, data = fixtures(16)
    sizes = []
    def provider_failure(items, **kwargs):
        sizes.append(len(items))
        kwargs["budget"].requests_this_run += 1
        raise GeminiDraftError(code)
    result = run(Inbox(), Budget(), [], data, classify=provider_failure, max_requests=20)
    assert sizes == [16] and result["http_requests"] == 1
    assert result["stopped_reason"] == "model_error" and result["error_code"] == code
    assert result["model_recovery_splits"] == result["classified"] == 0


def test_storage_validation_failure_is_not_repaired_or_salvaged_as_a_model_split():
    _, _, data = fixtures(16)
    inbox, calls = Inbox(), []
    def mutated_envelope(items, **kwargs):
        calls.append(len(items))
        results = classify(items, **kwargs)
        results[-1]["source_read"]["truncated"] = True
        return results
    with pytest.raises(ValueError, match="scope_source_receipt_mismatch"):
        run(inbox, Budget(), [], data, classify=mutated_envelope, max_requests=20)
    assert calls == [16] and inbox.puts == []


def test_negative_or_missing_old_drafts_do_not_exclude_raw_archive_references():
    refs, drafts, data = fixtures(3)
    drafts[0]["news_dossier"]["classification"]["relevance"] = "not_relevant"
    drafts[1]["extraction"]["prompt_version"] = "mdh-universal-news-grounded-8-relevance-scope"
    result = run(Inbox(), Budget(), drafts[:2], data)
    assert result["classified"] == 3 and result["selected_ids"] == sorted(r["id"] for r in refs)


def test_cache_loaded_once_skips_all_6000_records_without_a_single_github_get():
    refs = [reference(n) for n in range(1, 6001)]
    reviews = [current_review(r) for r in refs]
    inbox = Inbox()
    result = run(inbox, Budget(), [], archive(*refs), scope_reviews=reviews,
                 fetch=lambda _: pytest.fail("cache should not fetch sources"))
    assert result["cached"] == result["classified_total"] == 6000
    assert result["sources_checked"] == result["http_requests"] == 0
    assert inbox.gets == inbox.puts == []


def test_all_prior_policy_decisions_including_positives_are_reclassified_only_once():
    refs, _, data = fixtures(7)
    reviews = [scope_review(refs[0]), scope_review(refs[1], relevance="not_relevant"),
               scope_review(refs[2], jurisdiction="unclear"), inclusive_review(refs[3]),
               inclusive_review(refs[4], relevance="not_relevant"), inclusive_review(refs[5], jurisdiction="unclear"),
               current_review(refs[6], relevance="not_relevant")]
    inbox = Inbox()
    result = run(inbox, Budget(), [], data, scope_reviews=reviews)
    assert result["cached"] == 1 and result["classified"] == 6
    stored = {review["candidate_id"]: review for review in inbox.documents("scope-reviews")}
    assert set(stored) == {ref["id"] for ref in refs[:6]}
    assert all(value["extraction"]["prompt_version"] == SCOPE_PROMPT for value in stored.values())
    combined = [stored.get(value["candidate_id"], value) for value in reviews]
    result = run(inbox, Budget(), [], data, scope_reviews=combined,
        fetch=lambda _: pytest.fail("updated decisions must be cached, including negatives"))
    assert result["cached"] == 7 and result["http_requests"] == result["sources_checked"] == 0


def test_prior_positive_policies_are_revisited_before_unseen_news_without_overriding_human_exclusions():
    fresh, v1, v2, excluded, cached = [reference(n) for n in range(1, 6)]
    fresh["published_date"] = "2010-01-01"
    v1["published_date"] = "2016-01-01"
    v2["published_date"] = "2020-01-01"
    excluded["published_date"] = "2015-01-01"
    cached["published_date"] = "2014-01-01"
    reviews = [scope_review(v1), inclusive_review(v2), scope_review(excluded), current_review(cached)]
    fetched_ids = []
    def read(ref):
        fetched_ids.append(ref["id"])
        return fetched(ref)
    result = run(Inbox(), Budget(), [], archive(fresh, v1, v2, excluded, cached),
        scope_reviews=reviews, excluded_ids=[excluded["id"]], max_requests=1, batch_size=2, fetch=read)
    assert fetched_ids == [v1["id"], v2["id"]]
    assert result["cached"] == 1 and result["classified"] == 2
    assert result["remaining"] == 1
    assert excluded["id"] not in result["selected_ids"]


def test_outdated_positive_with_failed_source_stays_pending_and_respects_backoff():
    refs, _, data = fixtures()
    prior = inclusive_review(refs[0])
    inbox = Inbox()
    def unavailable(_):
        raise SourceFetchError("source_access_restricted")
    result = run(inbox, Budget(), [], data, scope_reviews=[prior], fetch=unavailable)
    assert result["cached"] == result["classified"] == result["http_requests"] == 0
    assert result["source_unavailable"] == result["remaining"] == 1
    assert result["selected_ids"] == []
    result = run(inbox, Budget(), [], data, scope_reviews=[prior], attempts=inbox.documents("scope-attempts"),
        fetch=lambda _: pytest.fail("source restriction must keep its backoff after a policy update"))
    assert result["retry_deferred"] == 1 and result["sources_checked"] == result["http_requests"] == 0
    assert result["selected_ids"] == []


def test_only_explicit_human_excluded_ids_skip_classification():
    refs, _, data = fixtures(2)
    result = run(Inbox(), Budget(), [], data, excluded_ids=[refs[1]["id"]])
    assert result["candidates"] == result["classified"] == 1 and result["selected_ids"] == [refs[0]["id"]]


def test_request_limit_keeps_remaining_archive_for_a_later_batch():
    _, _, data = fixtures(16)
    result = run(Inbox(), Budget(), [], data, max_requests=1, batch_size=8)
    assert result["classified"] == result["sources_checked"] == 8 and result["http_requests"] == 1
    assert result["remaining"] == 8 and result["stopped_reason"] == "run_limit"


def test_new_source_hash_does_not_require_an_old_dossier_hash_match():
    refs, drafts, data = fixtures()
    assert drafts[0]["extraction"]["content_sha256"] != fetched(refs[0])["content_sha256"]
    inbox = Inbox()
    result = run(inbox, Budget(), drafts, data)
    assert result["classified"] == 1 and result["source_changed"] == 0
    assert inbox.documents("scope-reviews")[0]["evidence"]["content_sha256"] == fetched(refs[0])["content_sha256"]


@pytest.mark.parametrize("code,days", [("source_network_error", 1), ("source_access_restricted", 7)])
def test_source_failures_have_durable_backoff_and_no_sensitive_error_contents(code, days):
    refs, _, data = fixtures()
    inbox = Inbox()
    def unavailable(_):
        raise SourceFetchError(code)
    result = run(inbox, Budget(), [], data, fetch=unavailable)
    assert result["source_unavailable"] == 1 and result["http_requests"] == 0
    saved = inbox.documents("scope-attempts")
    assert len(saved) == 1 and saved[0]["error_code"] == code
    assert datetime.fromisoformat(saved[0]["next_retry_at"]) - NOW == timedelta(days=days)
    assert validate_scope_attempt(saved[0], reference=refs[0], now=NOW) == saved[0]
    inbox.gets.clear()
    result = run(inbox, Budget(), [], data, attempts=saved,
                 fetch=lambda _: pytest.fail("retry backoff must suppress source read"))
    assert result["retry_deferred"] == 1 and result["sources_checked"] == 0 and inbox.gets == []
    result = run(inbox, Budget(), [], data, attempts=saved, now=NOW + timedelta(days=days, seconds=1))
    assert result["classified"] == 1


def test_unexpected_source_exception_persists_only_safe_generic_code():
    _, _, data = fixtures()
    inbox = Inbox()
    def unavailable(_):
        raise RuntimeError("PRIVATE_SOURCE_BODY_SENTINEL")
    run(inbox, Budget(), [], data, fetch=unavailable)
    saved = inbox.documents("scope-attempts")
    assert saved[0]["error_code"] == "source_fetch_failed"
    assert "PRIVATE_SOURCE_BODY_SENTINEL" not in json.dumps(saved)


def test_failed_sources_cannot_exhaust_unbounded_reads_or_starve_next_batch():
    refs, _, data = fixtures(40)
    inbox = Inbox()
    def unavailable(_):
        raise SourceFetchError("source_network_error")
    first = run(inbox, Budget(), [], data, max_requests=1, batch_size=8, fetch=unavailable)
    assert first["sources_checked"] == first["source_unavailable"] == 16
    assert first["stopped_reason"] == "source_check_limit" and first["http_requests"] == 0
    second = run(inbox, Budget(), [], data, max_requests=1, batch_size=8, attempts=inbox.documents("scope-attempts"))
    assert second["retry_deferred"] == 16 and second["classified"] == 8
    failed = {value["candidate_id"] for value in inbox.documents("scope-attempts")}
    assert not failed.intersection(second["selected_ids"])


def test_source_check_cap_is_200_even_with_50_request_allocation():
    _, _, data = fixtures(220)
    def unavailable(_):
        raise SourceFetchError("source_access_restricted")
    result = run(Inbox(), Budget(), [], data, max_requests=50, fetch=unavailable)
    assert result["sources_checked"] == 200 and result["http_requests"] == 0


def test_full_serialized_payload_limit_splits_batch_before_gemini():
    _, _, data = fixtures(8)
    sizes = []
    def checked(items, **kwargs):
        from mdh_pipeline.gemini_budget import estimate_input_tokens
        _, payload = prepare_scope_batch(items)
        sizes.append((len(items), estimate_input_tokens(payload)))
        return classify(items, **kwargs)
    result = run(Inbox(), Budget(), [], data, fetch=lambda r: fetched(r, length=23_000), classify=checked)
    assert result["classified"] == 6 and result["http_requests"] == 2
    assert result["remaining"] == 2  # The larger policy prompt still cannot exceed the request grant.
    assert len(sizes) == 2 and all(count <= 8 and size <= 100_000 for count, size in sizes)


def test_source_read_time_is_bounded_and_expired_run_never_calls_model():
    _, _, data = fixtures()
    tick = [0]
    def slow_read(ref):
        tick[0] = 721
        return fetched(ref)
    result = run(Inbox(), Budget(), [], data, fetch=slow_read, clock=lambda: tick[0])
    assert result["sources_checked"] == 1 and result["http_requests"] == result["classified"] == 0
    assert result["stopped_reason"] == "run_limit"


def test_quota_deferral_persists_no_decision_and_preserves_budget_reason():
    _, _, data = fixtures()
    inbox = Inbox()
    def deferred(*args, **kwargs):
        raise QuotaDeferred("daily_budget_exhausted", NOW)
    result = run(inbox, Budget(), [], data, classify=deferred)
    assert result["stopped_reason"] == "budget_deferred" and result["quota_reason"] == "daily_budget_exhausted"
    assert inbox.files == {}


def test_preallocated_shared_budget_is_not_reset_and_http_report_is_a_delta():
    _, _, data = fixtures(16)
    budget = Budget()
    budget.requests_this_run, budget.request_limit = 3, 4
    result = run(Inbox(), budget, [], data, max_requests=20, batch_size=8)
    assert budget.request_limit == budget.requests_this_run == 4
    assert result["http_requests"] == 1 and result["classified"] == 8


@pytest.mark.parametrize("mutation", [
    lambda review: review.update(source_url="https://www.diariocolatino.com/wrong-reference/"),
    lambda review: review["evidence"].update(content_sha256="invalid"),
    lambda review: review.update(extra_private_text="SENSITIVE"),
    lambda review: review["source_read"].update(content_sha256="e" * 64),
])
def test_corrupt_cache_fails_before_sources_model_or_storage(mutation):
    refs, _, data = fixtures()
    cached = inclusive_review(refs[0])
    mutation(cached)
    inbox = Inbox()
    with pytest.raises(ValueError, match="invalid_saved_scope_review"):
        run(inbox, Budget(), [], data, scope_reviews=[cached],
            fetch=lambda _: pytest.fail("invalid cache must fail before fetch"))
    assert inbox.gets == inbox.puts == []


def test_duplicate_cache_entries_are_rejected_even_if_identical():
    refs, _, data = fixtures()
    cached = inclusive_review(refs[0])
    with pytest.raises(ValueError, match="invalid_saved_scope_review"):
        run(Inbox(), Budget(), [], data, scope_reviews=[cached, deepcopy(cached)])


def test_invalid_attempt_retry_interval_is_not_accepted_as_permanent_exclusion():
    refs, _, data = fixtures()
    document = {"schema_version": 1, "candidate_id": refs[0]["id"], "source_url": refs[0]["source_url"],
                "status": "source_unavailable", "error_code": "source_network_error", "attempted_at": NOW.isoformat(),
                "next_retry_at": (NOW + timedelta(days=99)).isoformat()}
    with pytest.raises(ValueError, match="invalid_saved_scope_attempt"):
        run(Inbox(), Budget(), [], data, attempts=[document])


def test_all_result_receipts_are_validated_before_any_scope_write():
    _, _, data = fixtures(2)
    inbox = Inbox()
    def bad_receipt(items, **kwargs):
        values = classify(items, **kwargs)
        values[-1]["source_read"]["truncated"] = True
        return values
    with pytest.raises(ValueError, match="scope_source_receipt_mismatch"):
        run(inbox, Budget(), [], data, classify=bad_receipt)
    assert inbox.puts == []


def test_invalid_put_receipt_is_not_counted_as_success():
    _, _, data = fixtures()
    inbox = Inbox()
    inbox.invalid_receipt = True
    with pytest.raises(ValueError, match="scope_write_receipt_invalid"):
        run(inbox, Budget(), [], data)


def test_source_failure_writes_are_paced():
    _, _, data = fixtures(3)
    sleeps = []
    def unavailable(_):
        raise SourceFetchError("source_network_error")
    run(Inbox(), Budget(), [], data, fetch=unavailable, sleep=sleeps.append, clock=lambda: 0)
    assert sleeps == [2.0, 2.0]


def test_load_scope_cache_reads_valid_private_files_once_and_rejects_filename_mismatch(tmp_path):
    value = inclusive_review(reference())
    path = tmp_path / f"{value['candidate_id']}.json"
    path.write_text(json.dumps(value))
    assert load_scope_cache(tmp_path) == [value]
    path.rename(tmp_path / ("a" * 64 + ".json"))
    with pytest.raises(ValueError, match="invalid_scope_cache_identity"):
        load_scope_cache(tmp_path)


@pytest.mark.parametrize("arguments", [{"batch_size": 17}, {"batch_size": True}, {"max_requests": 51}, {"max_seconds": 721}])
def test_invalid_worker_limits_fail_before_source_access(arguments):
    _, _, data = fixtures()
    with pytest.raises(ValueError):
        run(Inbox(), Budget(), [], data, **arguments)
