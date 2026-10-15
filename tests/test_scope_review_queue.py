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
    assert result["cached"] == 0 and result["classified"] == 7
    stored = {review["candidate_id"]: review for review in inbox.documents("scope-reviews")}
    assert set(stored) == {ref["id"] for ref in refs}
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


def test_internal_updated_receipts_include_negative_decisions_and_only_confirmed_writes():
    refs, _, data = fixtures(2)
    def negative(items, **kwargs):
        results = classify(items, **kwargs)
        results[0]['classification']['relevance'] = 'not_relevant'
        return results
    result = run(Inbox(), Budget(), [], data, classify=negative, max_requests=1)
    assert len(result['updated_reviews']) == 2
    negative_ids = {row['candidate_id'] for row in result['updated_reviews'] if row['classification']['relevance'] == 'not_relevant'}
    assert len(negative_ids) == 1
    assert not negative_ids.intersection(result['selected_ids'])
    assert all('hacinamiento' not in json.dumps(row) for row in result['updated_reviews'])


def negative_classifier(items, **kwargs):
    results = classify(items, **kwargs)
    for result in results:
        result["classification"]["relevance"] = "not_relevant"
    return results


def verify_fixture(items, *, budget, now=NOW, outcome="confirmed_excluded", **kwargs):
    from test_news_exclusions import receipt_fixture
    from mdh_pipeline.news_exclusions import prepare_verification_batch
    assert budget.requests_this_run < budget.request_limit
    prepare_verification_batch(items)
    budget.requests_this_run += 1
    rows = []
    for item in items:
        row = receipt_fixture(item["reference"], item["first_review"], outcome=outcome, now=now)
        row["second_review"]["source_read"] = {key: item["article"][key]
            for key in ("fetched_at", "extraction_method", "truncated", "content_sha256")}
        rows.append(row)
    return rows


def matching_negative(ref):
    # A real current first decision on this exact fetched source, no fake digest.
    return negative_classifier([{"reference": ref, "article": fetched(ref)}], budget=SimpleNamespace(
        requests_this_run=0, request_limit=1), model=Budget.config.model)[0]


def test_one_scope_call_leaves_new_negatives_pending_without_a_second_call():
    refs, _, data = fixtures(8)
    result = run(Inbox(), Budget(), [], data, classify=negative_classifier, max_requests=1,
        verify=lambda *a, **k: pytest.fail("no grant for a second assessment"))
    assert result["classified"] == 8 and result["verified"] == 0 and result["remaining"] == 8
    assert result["http_requests"] == 1 and result["selected_ids"] == []
    assert result["updated_verifications"] == []


def test_new_negative_then_second_pass_share_exact_two_call_allowance():
    refs, _, data = fixtures(8)
    inbox = Inbox()
    result = run(inbox, Budget(), [], data, classify=negative_classifier, verify=verify_fixture)
    assert result["classified"] == result["verified"] == result["confirmed_excluded"] == 8
    assert result["http_requests"] == 2 and result["remaining"] == 0
    assert result["sources_checked"] == 8  # reuse the identical in-memory source
    assert len(inbox.documents("scope-verifications")) == 8
    assert result["selected_ids"] == []


def test_second_positive_is_persisted_after_provenance_and_selected_without_reextraction():
    refs, _, data = fixtures(2)
    inbox = Inbox()
    result = run(inbox, Budget(), [], data, classify=negative_classifier,
        verify=lambda *a, **k: verify_fixture(*a, outcome="retained", **k))
    assert result["retained"] == result["accepted"] == 2 and result["remaining"] == 0
    assert result["http_requests"] == 2
    assert result["selected_ids"] == sorted(ref["id"] for ref in refs)
    for ref in refs:
        paths = [path for path, _ in inbox.puts if ref["id"] in path]
        assert [path.split("/contents/")[1].split("/")[0] for path in paths] == [
            "scope-reviews", "scope-verifications", "scope-reviews"]


def test_existing_second_pass_and_first_pass_each_receive_one_shared_call():
    refs, _, data = fixtures(24)
    reviews = [matching_negative(ref) for ref in refs[:8]]
    result = run(Inbox(), Budget(), [], data, scope_reviews=reviews, classify=classify, verify=verify_fixture)
    assert result["http_requests"] == 2
    assert result["classified"] == 16 and result["verified"] == 8
    assert result["accepted"] == 16 and result["confirmed_excluded"] == 8 and result["remaining"] == 0


def test_verification_only_work_uses_idle_first_pass_capacity_without_extra_grants():
    refs, _, data = fixtures(16)
    reviews = [matching_negative(ref) for ref in refs]
    result = run(Inbox(), Budget(), [], data, scope_reviews=reviews, verify=verify_fixture,
        classify=lambda *a, **k: pytest.fail("all first assessments exist"))
    assert result["verified"] == result["confirmed_excluded"] == 16
    assert result["http_requests"] == 2 and result["classified"] == 0 and result["remaining"] == 0


def test_changed_source_invalidates_first_negative_and_reclassifies_before_verification():
    refs, _, data = fixtures()
    prior = matching_negative(refs[0])
    result = run(Inbox(), Budget(), [], data, scope_reviews=[prior],
        fetch=lambda r: fetched(r, length=120), verify=lambda *a, **k: pytest.fail("source changed"))
    assert result["source_changed"] == 1 and result["classified"] == result["accepted"] == 1
    assert result["verified"] == 0 and result["http_requests"] == 1


def test_cached_confirmed_exclusion_needs_no_source_reads_or_calls():
    from test_news_exclusions import receipt_fixture
    refs, _, data = fixtures()
    receipt = receipt_fixture(refs[0], matching_negative(refs[0]), now=NOW)
    result = run(Inbox(), Budget(), [], data, scope_reviews=[receipt["first_review"]], verifications=[receipt],
        fetch=lambda r: pytest.fail("confirmed cache should not fetch"))
    assert result["cached"] == 1 and result["remaining"] == result["http_requests"] == 0


def test_cached_uncertain_verification_does_not_retry_before_seven_days():
    from test_news_exclusions import receipt_fixture
    refs, _, data = fixtures()
    receipt = receipt_fixture(refs[0], matching_negative(refs[0]), outcome="uncertain", now=NOW)
    result = run(Inbox(), Budget(), [], data, scope_reviews=[receipt["first_review"]], verifications=[receipt],
        now=NOW + timedelta(days=6), fetch=lambda r: pytest.fail("uncertain cooldown"))
    assert result["http_requests"] == 0 and result["selected_ids"] == []


def test_current_positive_from_preceding_policy_is_not_reclassified():
    refs, _, data = fixtures()
    prior = current_review(refs[0])
    prior["extraction"]["prompt_version"] = "mdh-news-scope-3-rights-nexus"
    result = run(Inbox(), Budget(), [], data, scope_reviews=[prior], fetch=lambda r: pytest.fail("positive still valid"))
    assert result["cached"] == 1 and result["selected_ids"] == [refs[0]["id"]]
    assert result["http_requests"] == 0


def test_priority_news_are_classified_before_the_ordinary_historical_tail():
    refs, _, data = fixtures(17)
    refs[16]["published_date"] = "2026-10-01"
    seen = []
    def remember(items, **kwargs):
        seen.extend(item["reference"]["id"] for item in items)
        return classify(items, **kwargs)
    result = run(Inbox(), Budget(), [], data, priority_ids=[refs[16]["id"]], classify=remember, max_requests=1)
    assert seen[0] == refs[16]["id"] and result["http_requests"] == 1


def test_retained_receipt_recovers_interrupted_positive_write_without_another_model_call():
    from test_news_exclusions import receipt_fixture
    refs, _, data = fixtures()
    receipt = receipt_fixture(refs[0], matching_negative(refs[0]), outcome="retained", now=NOW)
    inbox = Inbox()
    result = run(inbox, Budget(), [], data, scope_reviews=[receipt["first_review"]], verifications=[receipt],
        fetch=lambda r: pytest.fail("persisted second assessment should be reused"))
    assert result["retained"] == 1 and result["http_requests"] == 0
    assert result["selected_ids"] == [refs[0]["id"]]
    assert inbox.documents("scope-reviews") == [receipt["second_review"]]
    from mdh_pipeline.news_exclusions import scope_needs_work
    assert scope_needs_work(receipt["first_review"], receipt, refs[0], now=NOW)
    assert not scope_needs_work(receipt["second_review"], receipt, refs[0], now=NOW)


def test_missing_verification_write_receipt_prevents_positive_overwrite_and_selection():
    refs, _, data = fixtures()
    prior = matching_negative(refs[0])
    inbox = Inbox()
    inbox.invalid_receipt = True
    with pytest.raises(ValueError, match="scope_write_receipt_invalid"):
        run(inbox, Budget(), [], data, scope_reviews=[prior],
            verify=lambda *a, **k: verify_fixture(*a, outcome="retained", **k))
    assert inbox.documents("scope-reviews") == []


def test_audited_legacy_positive_is_reclassified_once_while_new_policy_positive_is_cached():
    refs, _, data = fixtures()
    prior = current_review(refs[0])
    prior["extraction"]["prompt_version"] = "mdh-news-scope-3-rights-nexus"
    inbox = Inbox()
    result = run(inbox, Budget(), [], data, scope_reviews=[prior], priority_ids=[refs[0]["id"]])
    assert result["classified"] == result["http_requests"] == 1
    result = run(Inbox(), Budget(), [], data, scope_reviews=inbox.documents("scope-reviews"),
        priority_ids=[refs[0]["id"]], fetch=lambda r: pytest.fail("current audit review already processed"))
    assert result["cached"] == 1 and result["http_requests"] == 0


def test_audited_corrupt_source_hash_stays_pending_without_spending_gemini_calls():
    from test_news_scope_rechecks import document, request
    refs, _, data = fixtures()
    body_hash = fetched(refs[0])["content_sha256"]
    receipt = document(request(refs[0], source_content_sha256=body_hash,
        requested_at=(NOW - timedelta(hours=1)).isoformat(), reason_code="source_content_mismatch"))
    inbox = Inbox()
    result = run(inbox, Budget(), [], data, rechecks=receipt,
        classify=lambda *a, **k: pytest.fail("known corrupt source cannot reach Gemini"))
    assert result["source_unavailable"] == 1 and result["http_requests"] == 0
    attempt = inbox.documents("scope-attempts")[0]
    assert attempt["error_code"] == "source_content_mismatch"
    assert datetime.fromisoformat(attempt["next_retry_at"]) == NOW + timedelta(days=7)
    assert result["selected_ids"] == [] and result["remaining"] == 1


def test_audit_request_bypasses_older_source_backoff_once_but_new_failure_keeps_backoff():
    from test_news_scope_rechecks import document, request
    refs, _, data = fixtures()
    audit = document(request(refs[0], requested_at=(NOW - timedelta(hours=1)).isoformat()))
    older = {"schema_version": 1, "candidate_id": refs[0]["id"], "source_url": refs[0]["source_url"],
        "status": "source_unavailable", "error_code": "source_access_restricted",
        "attempted_at": (NOW - timedelta(hours=2)).isoformat(),
        "next_retry_at": (NOW - timedelta(hours=2) + timedelta(days=7)).isoformat()}
    inbox = Inbox()
    def blocked(ref):
        raise SourceFetchError("source_access_restricted")
    result = run(inbox, Budget(), [], data, rechecks=audit, attempts=[older], fetch=blocked)
    assert result["sources_checked"] == result["source_unavailable"] == 1 and result["retry_deferred"] == 0
    result = run(Inbox(), Budget(), [], data, rechecks=audit, attempts=inbox.documents("scope-attempts"),
        fetch=lambda ref: pytest.fail("fresh audit failure must retain bounded backoff"))
    assert result["retry_deferred"] == 1 and result["sources_checked"] == result["http_requests"] == 0


def test_scope_deadline_proxy_guards_every_reservation_and_dispatch():
    from mdh_pipeline.scope_review_queue import _ScopeDeadlineBudget
    clock = [0]
    calls = []
    base = SimpleNamespace(requests_this_run=0, reserve=lambda value: calls.append("reserve") or value,
        assert_dispatch_allowed=lambda value: calls.append("dispatch"), record_429=lambda: "cooldown")
    proxy = _ScopeDeadlineBudget(base, 60, lambda: clock[0])
    proxy.reserve({"request": 1})
    proxy.assert_dispatch_allowed({})
    proxy.requests_this_run += 1
    assert base.requests_this_run == 1 and proxy.record_429() == "cooldown"
    clock[0] = 60
    with pytest.raises(QuotaDeferred, match="run_time_limit"):
        proxy.reserve({"retry": 2})
    with pytest.raises(QuotaDeferred, match="run_time_limit"):
        proxy.assert_dispatch_allowed({})
    assert calls == ["reserve", "dispatch"]


def test_scope_phase_cap_stops_http_retry_even_when_global_run_allowance_is_larger(monkeypatch):
    from mdh_pipeline.news_scope import classify_scope_batch
    from test_gemini_budget import ready
    from test_news_scope import Session, Response
    refs, _, data = fixtures()
    budget, ledger, _ = ready()
    budget.set_request_limit(50)
    session = Session([Response({}, status=503)])
    monkeypatch.setattr("mdh_pipeline.gemini_draft.time.sleep", lambda seconds: None)
    def real_classifier(items, **kwargs):
        return classify_scope_batch(items, request_session=session, **kwargs)
    result = run(Inbox(), budget, [], data, max_requests=1, classify=real_classifier)
    assert result["http_requests"] == ledger.document()["used_requests"] == len(session.calls) == 1
    assert result["stopped_reason"] == "budget_deferred"
    assert result["quota_reason"] == "run_budget_exhausted"
    assert budget.request_limit == 50


def test_same_run_retained_second_assessment_routes_latest_unique_review_to_extraction():
    from mdh_pipeline.gemini_review import _refresh_scope_workload
    from mdh_pipeline.news_scope_policy import EXCLUSION_PROMPT
    refs, _, data = fixtures(2)
    inbox = Inbox()
    result = run(inbox, Budget(), [], data, classify=negative_classifier,
        verify=lambda *a, **k: verify_fixture(*a, outcome="retained", **k))
    updated = result["updated_reviews"]
    assert len(updated) == len({row["candidate_id"] for row in updated}) == 2
    assert all(row["extraction"]["prompt_version"] == EXCLUSION_PROMPT for row in updated)
    assert all(row["classification"] == {"jurisdiction": "SV", "relevance": "relevant"} for row in updated)
    # Both writes remain tracked, but only the latest acknowledged value is routed.
    assert len([path for path, _ in inbox.puts if "/scope-reviews/" in path]) == 4
    class RoutingInbox:
        base = "/repos/example/private"
        def call(self, method, path, **kwargs):
            assert method == "GET" and path.endswith("/issues")
            return []
    workload = _refresh_scope_workload(RoutingInbox(), data, [], [], [], updated,
        excluded=set(), now=NOW, scope_audits=None, updated_verifications=result["updated_verifications"])
    assert workload["selected_ids"] == {ref["id"] for ref in refs}
    assert workload["extraction_pending"] == 2 and workload["scope_pending"] == 0
    assert workload["excluded_ids"] == set()


def test_recovered_and_fresh_reviews_share_one_bounded_update_inventory():
    from test_news_exclusions import receipt_fixture
    refs, _, data = fixtures(53)
    receipts = [receipt_fixture(ref, matching_negative(ref), outcome="retained", now=NOW) for ref in refs[:21]]
    result = run(Inbox(), Budget(), [], data, scope_reviews=[row["first_review"] for row in receipts],
        verifications=receipts, max_requests=1)
    assert result["retained"] == 21
    assert result["sources_checked"] == result["classified"] == 11
    assert len(result["updated_reviews"]) == 32  # max_requests * batch_size * 2
    assert result["http_requests"] == 1 and result["stopped_reason"] == "source_check_limit"
