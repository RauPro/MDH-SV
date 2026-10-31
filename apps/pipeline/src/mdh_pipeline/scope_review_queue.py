"""Classify the full news bibliography with bounded reads and one private cache load."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import re
import time
from datetime import datetime, timedelta, timezone

from .article_source import fetch_article
from .gemini_budget import BudgetConfig, GeminiBudget, QuotaDeferred, estimate_input_tokens
from .gemini_draft import GeminiDraftError
from .contracts.selection import index_archive as _archive_index, validate_scope_review as validate_scope_review
from .news_scope import MAX_BATCH_ITEMS, SCOPE_PROMPT, classify_scope_batch, prepare_scope_batch
from .review_queue import GitHubInbox, GitHubRequestError
from .news_exclusions import (MAX_VERIFICATION_ITEMS, negative, scope_needs_work, validate_verification,
    verify_exclusions_batch, prepare_verification_batch, is_current_positive)
from .news_scope_policy import CURRENT_SCOPE_PROMPTS

HEX = re.compile(r"^[a-f0-9]{64}$")
ATTEMPT_KEYS = {"schema_version", "candidate_id", "source_url", "status", "error_code", "attempted_at", "next_retry_at"}
SOURCE_ERRORS = {"source_access_restricted", "source_not_found", "source_network_error", "source_text_too_short",
                 "source_article_container_missing", "source_http_error", "source_too_large", "source_fetch_failed",
                 "source_identity_mismatch", "source_content_mismatch", "source_publication_date_missing", "source_input_invalid",
                 "source_input_too_large", "unsupported_source_content_type", "invalid_source_document",
                 "invalid_source_url", "redirect_outside_source", "unsafe_source_dns", "too_many_redirects"}
MAX_CACHE_ITEMS = 100_000
RECOVERABLE_SCOPE_ERRORS = {
    "scope_identity_mismatch", "invalid_scope_response", "ungrounded_scope_evidence",
    "invalid_json_response", "duplicate_json_key", "invalid_json_number",
    "invalid_candidate_count", "invalid_response_parts", "incomplete_model_response", "response_too_large",
}


class _ScopeDeadlineBudget(GeminiBudget):
    """Delegate the same ledger, but check the worker deadline on every retry."""
    def __init__(self, budget, deadline, clock, request_ceiling=None):
        self.budget, self.deadline, self.run_clock = budget, deadline, clock
        self.request_ceiling = request_ceiling

    def __getattr__(self, name):
        return getattr(self.budget, name)

    @property
    def requests_this_run(self):
        return self.budget.requests_this_run

    @requests_this_run.setter
    def requests_this_run(self, value):
        self.budget.requests_this_run = value

    def _check(self, *, dispatch=False):
        if (self.request_ceiling is not None
                and self.budget.requests_this_run >= self.request_ceiling + int(dispatch)):
            raise QuotaDeferred("run_budget_exhausted", datetime.now(timezone.utc))
        if self.run_clock() >= self.deadline:
            raise QuotaDeferred("run_time_limit", datetime.now(timezone.utc))

    def reserve(self, payload):
        self._check()
        return self.budget.reserve(payload)

    def assert_dispatch_allowed(self, reservation):
        self._check(dispatch=True)
        return self.budget.assert_dispatch_allowed(reservation)

    def record_429(self):
        return self.budget.record_429()

    def set_request_limit(self, limit):
        return self.budget.set_request_limit(limit)

    def snapshot(self):
        return self.budget.snapshot()


def _instant(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("invalid_scope_timestamp")
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError()
        return instant.astimezone(timezone.utc)
    except ValueError:
        raise ValueError("invalid_scope_timestamp") from None


def _positive(document):
    return document["classification"] == {"jurisdiction": "SV", "relevance": "relevant"}


def validate_scope_attempt(document, *, reference, now=None):
    """Keep only identity, a safe error code and the retry time for a failed read."""
    try:
        if (not isinstance(document, dict) or set(document) != ATTEMPT_KEYS
                or type(document["schema_version"]) is not int or document["schema_version"] != 1
                or document["candidate_id"] != reference["id"] or not HEX.fullmatch(document["candidate_id"])
                or document["source_url"] != reference["source_url"]
                or document["status"] != "source_unavailable" or document["error_code"] not in SOURCE_ERRORS):
            raise ValueError()
        attempted, retry = _instant(document["attempted_at"]), _instant(document["next_retry_at"])
        expected = timedelta(days=7 if document["error_code"] in {"source_access_restricted", "source_content_mismatch"} else 1)
        if retry - attempted != expected or attempted > (now or datetime.now(timezone.utc)):
            raise ValueError()
        return dict(document)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError("invalid_saved_scope_attempt") from None


def _cache_index(documents, references, *, attempts=False, now=None):
    if not isinstance(documents, (list, tuple)) or len(documents) > MAX_CACHE_ITEMS:
        raise ValueError("invalid_scope_cache")
    result = {}
    for document in documents:
        try:
            identifier = document["candidate_id"]
            if not isinstance(identifier, str) or not HEX.fullmatch(identifier) or identifier in result:
                raise ValueError()
            reference = references[identifier]
            if attempts:
                checked = validate_scope_attempt(document, reference=reference, now=now)
            else:
                checked = validate_scope_review(document, reference=reference,
                    content_sha256=document["evidence"]["content_sha256"], now=now)
            result[identifier] = checked
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ValueError("invalid_saved_scope_attempt" if attempts else "invalid_saved_scope_review") from None
    return result


def _put_private(inbox, folder, document, *, before_write):
    relative = f"{folder}/{document['candidate_id']}.json"
    endpoint = inbox.base + "/contents/" + relative
    previous = inbox.get_file(endpoint)
    payload = {"message": {"scope-reviews": "Store private news scope assessment",
                           "scope-verifications": "Store independent news exclusion verification"}.get(folder, "Record bounded source retry"),
               "content": base64.b64encode((json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode()).decode(),
               "branch": "main"}
    if previous is not None:
        if (not isinstance(previous, dict) or previous.get("type") != "file" or previous.get("path") != relative
                or not isinstance(previous.get("sha"), str) or not re.fullmatch(r"[a-f0-9]{40,64}", previous["sha"])):
            raise ValueError("invalid_scope_storage_file")
        payload["sha"] = previous["sha"]
    before_write()
    receipt = inbox.call("PUT", endpoint, json=payload)
    if (not isinstance(receipt, dict) or not isinstance(receipt.get("content"), dict)
            or not isinstance(receipt["content"].get("sha"), str)
            or not re.fullmatch(r"[a-f0-9]{40,64}", receipt["content"]["sha"])):
        raise ValueError("scope_write_receipt_invalid")


def recheck_scope(inbox, drafts, archive, *, api_key, model, budget, max_requests=2,
                  batch_size=MAX_BATCH_ITEMS, max_seconds=720, excluded_ids=(), scope_reviews=(), attempts=(),
                  verifications=(), priority_ids=(), rechecks=None,
                  fetch=fetch_article, classify=classify_scope_batch, verify=verify_exclusions_batch, clock=time.monotonic,
                  sleep=time.sleep, now=None):
    """Classify all bibliography references independently of drafts or issues.

    drafts remains for caller compatibility. Load scope_reviews and attempts once
    from the private checkout; only actual writes read the latest GitHub SHA.
    selected_ids and updated_reviews are internal orchestration data and must
    be omitted from logs. Updated receipts are authoritative for routing this
    run, including negative decisions that supersede older positive drafts.
    """
    if (type(max_requests) is not int or not 1 <= max_requests <= 50
            or type(batch_size) is not int or not 1 <= batch_size <= MAX_BATCH_ITEMS):
        raise ValueError("invalid_scope_batch_limit")
    if type(max_seconds) is not int or not 60 <= max_seconds <= 720:
        raise ValueError("invalid_scope_time_limit")
    if budget is None or model != budget.config.model:
        raise ValueError("scope_budget_required")
    if (not isinstance(excluded_ids, (list, tuple, set))
            or any(not isinstance(value, str) or not HEX.fullmatch(value) for value in excluded_ids)):
        raise ValueError("invalid_scope_exclusions")
    instant = _instant((now or datetime.now(timezone.utc)).isoformat())
    references = _archive_index(archive)
    # Validate every cached entry before any source read, reservation or mutation.
    reviews = _cache_index(scope_reviews, references, now=instant)
    retries = _cache_index(attempts, references, attempts=True, now=instant)
    if not isinstance(priority_ids, (list, tuple, set)) or any(not isinstance(x, str) or not HEX.fullmatch(x) for x in priority_ids):
        raise ValueError("invalid_scope_priorities")
    if not isinstance(verifications, (list, tuple)) or len(verifications) > MAX_CACHE_ITEMS:
        raise ValueError("invalid_scope_verifications")
    verified = {}
    for value in verifications:
        try:
            identifier = value["candidate_id"]
            if identifier in verified or identifier not in references:
                raise ValueError()
            # Stale first hashes are expected after source/reclassification changes.
            # Validate provenance against its archived first, then bind to the
            # current review only when using the receipt below.
            checked = validate_verification(value, value["first_review"], references[identifier], now=instant)
            verified[identifier] = checked
        except (ValueError, KeyError, TypeError):
            raise ValueError("invalid_saved_scope_verification") from None
    forced, blocked = set(), {}
    if rechecks is not None:
        from .news_scope_rechecks import pending_recheck_ids, blocked_source_hashes
        forced = set(pending_recheck_ids(rechecks, list(reviews.values()), now=instant))
        blocked = blocked_source_hashes(rechecks, now=instant)
    forced.update(identifier for identifier in priority_ids if identifier in reviews
        and reviews[identifier]["extraction"]["prompt_version"] not in CURRENT_SCOPE_PROMPTS)
    recheck_times = {item["candidate_id"]: _instant(item["requested_at"])
        for item in (rechecks or {}).get("requests", [])}
    priorities = set(priority_ids) | forced
    excluded = set(excluded_ids)
    budget.snapshot()
    starting_requests = budget.requests_this_run
    request_cap = min(max_requests, budget.config.batch_ceiling)
    if getattr(budget, "request_limit", None) is None:
        budget.set_request_limit(min(starting_requests + request_cap, budget.config.batch_ceiling))
    maximum_http = min(request_cap, max(0, budget.request_limit - starting_requests))
    maximum_sources = min(max_requests * batch_size * 2, 200)
    report = dict(candidates=len(references) - sum(identifier in references for identifier in excluded),
                  cached=0, retry_deferred=0, sources_checked=0, source_unavailable=0,
                  source_changed=0, reviewed=0, classified=0, relevant_sv=0, accepted=0,
                  http_requests=0, stopped_reason=None, selected_ids=[], updated_reviews=[], model_errors=0,
                  model_recovery_splits=0, model_recovered_articles=0,
                  model_failed_articles=0, model_deferred_articles=0, updated_verifications=[],
                  verified=0, confirmed_excluded=0, retained=0, uncertain_verifications=0)
    selected, pending, verification_pending = set(), [], []
    retained_recovery = []
    recovery_ids, recovered_ids, singleton_failures = set(), set(), set()
    updated_reviews = {}
    for identifier, reference in references.items():
        if identifier in excluded:
            continue
        prior = reviews.get(identifier)
        if prior and negative(prior) and identifier not in forced:
            try:
                saved = validate_verification(verified.get(identifier), prior, reference, now=instant)
            except ValueError:
                saved = None
            if saved and saved["outcome"] == "retained":
                retained_recovery.append(saved["second_review"])
                continue
        if identifier not in forced and not scope_needs_work(prior, verified.get(identifier), reference, now=instant):
            report["cached"] += 1
            if prior and is_current_positive(prior):
                selected.add(identifier)
            continue
        if (identifier in retries and _instant(retries[identifier]["next_retry_at"]) > instant
                and not (identifier in forced and identifier in recheck_times
                         and _instant(retries[identifier]["attempted_at"]) < recheck_times[identifier])):
            report["retry_deferred"] += 1
            continue
        if (identifier not in forced and prior and negative(prior)
                and prior["extraction"]["prompt_version"] == SCOPE_PROMPT
                and prior.get("source_read", {}).get("truncated") is False):
            verification_pending.append(reference)
        else:
            pending.append(reference)
    deadline = clock() + max_seconds
    dispatch_budget = _ScopeDeadlineBudget(budget, deadline, clock, starting_requests + maximum_http)
    last_write = None
    fresh_articles = {}

    def available():
        return clock() < deadline and budget.requests_this_run - starting_requests < maximum_http

    def pace_write():
        nonlocal last_write
        if last_write is not None:
            delay = 2.0 - (clock() - last_write)
            if delay > 0:
                sleep(delay)
        last_write = clock()

    def failed_source(reference, error):
        code = getattr(error, "code", "source_fetch_failed")
        if not isinstance(code, str) or code not in SOURCE_ERRORS:
            code = "source_fetch_failed"
        timestamp = instant if now is not None else datetime.now(timezone.utc)
        document = {"schema_version": 1, "candidate_id": reference["id"], "source_url": reference["source_url"],
                    "status": "source_unavailable", "error_code": code, "attempted_at": timestamp.isoformat(),
                    "next_retry_at": (timestamp + timedelta(days=7 if code in {"source_access_restricted", "source_content_mismatch"} else 1)).isoformat()}
        validate_scope_attempt(document, reference=reference, now=timestamp)
        _put_private(inbox, "scope-attempts", document, before_write=pace_write)
        report["source_unavailable"] += 1

    def classify_and_store(items):
        # Retries use the same object and remaining allocation. A malformed
        # parent response contributes no saved rows and is never repaired.
        if not available():
            return
        try:
            results = classify(items, api_key=api_key, model=model, budget=dispatch_budget)
        except GeminiDraftError as error:
            if error.code not in RECOVERABLE_SCOPE_ERRORS:
                # HTTP/provider/configuration failures are not grounds for more
                # model calls. QuotaDeferred is deliberately never caught here.
                raise
            report["model_errors"] += 1
            report["last_model_error_code"] = error.code
            identities = {item["reference"]["id"] for item in items}
            recovery_ids.update(identities)
            if len(items) == 1:
                singleton_failures.update(identities)
                return
            if not available():
                return
            report["model_recovery_splits"] += 1
            middle = len(items) // 2
            classify_and_store(items[:middle])
            classify_and_store(items[middle:])
            return
        if not isinstance(results, list) or len(results) != len(items):
            raise ValueError("invalid_scope_batch_results")
        # Validate all results before storing any prefix of a malformed batch.
        for result, item in zip(results, items, strict=True):
            validate_scope_review(result, reference=item["reference"],
                content_sha256=item["article"]["content_sha256"], now=now)
            receipt = {key: item["article"][key] for key in ("fetched_at", "extraction_method", "truncated", "content_sha256")}
            if (result["schema_version"] != 2 or result["extraction"]["prompt_version"] != SCOPE_PROMPT
                    or result.get("source_read") != receipt):
                raise ValueError("scope_source_receipt_mismatch")
        for result in results:
            _put_private(inbox, "scope-reviews", result, before_write=pace_write)
            updated_reviews[result["candidate_id"]] = result
            reviews[result["candidate_id"]] = result
            if negative(result) and result.get("source_read", {}).get("truncated") is False:
                verification_pending.append(references[result["candidate_id"]])
            report["reviewed"] += 1
            report["classified"] += 1
            if result["candidate_id"] in recovery_ids:
                recovered_ids.add(result["candidate_id"])
            if _positive(result):
                selected.add(result["candidate_id"])
                report["relevant_sv"] += 1
                report["accepted"] += 1

    def verify_and_store(items):
        if not available():
            return
        results = verify(items, api_key=api_key, model=model, budget=dispatch_budget, now=now)
        if not isinstance(results, list) or len(results) != len(items):
            raise ValueError("invalid_scope_verification_results")
        for result, item in zip(results, items, strict=True):
            validate_verification(result, item["first_review"], item["reference"], now=now)
            receipt = {key: item["article"][key] for key in ("fetched_at", "extraction_method", "truncated", "content_sha256")}
            if result["second_review"]["source_read"] != receipt:
                raise ValueError("scope_source_receipt_mismatch")
        for result in results:
            # Persist provenance before publishing a retained reassessment or
            # allowing a later reconciliation worker to close an exclusion.
            _put_private(inbox, "scope-verifications", result, before_write=pace_write)
            report["updated_verifications"].append(result)
            verified[result["candidate_id"]] = result
            report["verified"] += 1
            outcome = result["outcome"]
            if outcome == "retained":
                second = result["second_review"]
                _put_private(inbox, "scope-reviews", second, before_write=pace_write)
                reviews[result["candidate_id"]] = second
                updated_reviews[second["candidate_id"]] = second
                selected.add(result["candidate_id"])
                report["retained"] += 1
                report["relevant_sv"] += 1
                report["accepted"] += 1
            elif outcome == "confirmed_excluded":
                report["confirmed_excluded"] += 1
            else:
                report["uncertain_verifications"] += 1

    def priority(value):
        prior = reviews.get(value["id"])
        outdated = prior and prior["extraction"]["prompt_version"] not in CURRENT_SCOPE_PROMPTS
        return (0 if value["id"] in priorities else 1, 0 if outdated else 1, value["published_date"], value["id"])

    try:
        for index, retained in enumerate(retained_recovery):
            if index >= maximum_sources or clock() >= deadline:
                report["stopped_reason"] = "source_check_limit" if index >= maximum_sources else "run_limit"
                break
            _put_private(inbox, "scope-reviews", retained, before_write=pace_write)
            reviews[retained["candidate_id"]] = retained
            selected.add(retained["candidate_id"])
            updated_reviews[retained["candidate_id"]] = retained
            report["cached"] += 1
            report["retained"] += 1
        # Recovered writes and newly read candidates share the same bounded
        # update inventory expected by the extraction coordinator.
        maximum_sources -= len(updated_reviews)
        # Alternate one first-pass batch and one blind second-pass batch while
        # both have work. Both spend the exact same existing HTTP allowance.
        # If only one queue has work it can consume the otherwise idle capacity.
        pending.sort(key=priority)
        verification_pending.sort(key=priority)
        turn = "classify" if pending else "verify"
        while pending or verification_pending:
            if not available():
                report["stopped_reason"] = "run_limit"
                break
            verification_pending.sort(key=priority)
            use_verification = bool(verification_pending) and (turn == "verify" or not pending)
            queue = verification_pending if use_verification else pending
            limit = min(batch_size, MAX_VERIFICATION_ITEMS) if use_verification else batch_size
            prepare = prepare_verification_batch if use_verification else prepare_scope_batch
            current = []
            while queue and len(current) < limit:
                if not available():
                    break
                reference = queue.pop(0)
                identifier = reference["id"]
                if identifier in fresh_articles:
                    article = fresh_articles[identifier]
                else:
                    if report["sources_checked"] >= maximum_sources:
                        queue.insert(0, reference)
                        report["stopped_reason"] = "source_check_limit"
                        break
                    report["sources_checked"] += 1
                    try:
                        article = fetch({**reference, "source_title": reference["title"]})
                        prepare_scope_batch([{"reference": reference, "article": article}])
                        if article["content_sha256"] == blocked.get(identifier):
                            raise GeminiDraftError("source_content_mismatch")
                        fresh_articles[identifier] = article
                    except Exception as error:
                        if isinstance(error, GeminiDraftError) and error.code != "source_content_mismatch":
                            error = GeminiDraftError("source_input_too_large" if error.code == "scope_request_too_large" else "source_input_invalid")
                        failed_source(reference, error)
                        continue
                item = {"reference": reference, "article": article}
                if use_verification:
                    prior = reviews[identifier]
                    if article["content_sha256"] != prior["evidence"]["content_sha256"]:
                        report["source_changed"] += 1
                        pending.insert(0, reference)
                        continue
                    item["first_review"] = prior
                try:
                    _, prospective = prepare([*current, item])
                    fits = estimate_input_tokens(prospective) <= getattr(budget.config, "input_tpm", 100_000)
                except GeminiDraftError as error:
                    if error.code not in {"scope_request_too_large", "invalid_scope_batch", "invalid_scope_input", "invalid_verification_batch", "invalid_verification_input"}:
                        raise
                    fits = False
                if not fits and current:
                    queue.insert(0, reference)
                    break
                if not fits:
                    failed_source(reference, GeminiDraftError("source_input_too_large"))
                    continue
                current.append(item)
            if current and not available():
                report["stopped_reason"] = "run_limit"
                break
            if current and available():
                if use_verification:
                    verify_and_store(current)
                else:
                    classify_and_store(current)
            turn = "classify" if use_verification else "verify"
            if report["stopped_reason"] == "source_check_limit":
                break
        if not available() and (pending or verification_pending) and report["stopped_reason"] is None:
            report["stopped_reason"] = "run_limit"
    except QuotaDeferred as error:
        report["stopped_reason"] = "budget_deferred"
        report["quota_reason"] = error.reason
    except GeminiDraftError as error:
        report["stopped_reason"] = "model_error"
        report["error_code"] = error.code
    unresolved = recovery_ids - recovered_ids
    report["model_recovered_articles"] = len(recovered_ids)
    report["model_failed_articles"] = len(unresolved & singleton_failures)
    report["model_deferred_articles"] = len(unresolved - singleton_failures)
    if unresolved:
        if report["stopped_reason"] not in {None, "model_error"}:
            report["halt_reason"] = report["stopped_reason"]
        report["stopped_reason"] = "model_error"
        report.setdefault("error_code", report["last_model_error_code"])
    # A source may be classified negatively and retained by the second pass
    # in this invocation. Consumers need its latest acknowledged assessment,
    # never two conflicting rows for the same identity.
    report["updated_reviews"] = list(updated_reviews.values())
    report["http_requests"] = budget.requests_this_run - starting_requests
    report["selected_ids"] = sorted(selected)
    report["classified_total"] = report["cached"] + report["classified"]
    report["accepted_total"] = len(selected)
    report["remaining"] = sum(1 for identifier, reference in references.items()
        if identifier not in excluded and scope_needs_work(reviews.get(identifier), verified.get(identifier), reference, now=instant))
    return report


def load_scope_cache(directory, *, maximum_bytes=30_000):
    """Read a bounded private directory once, without per-record GitHub requests."""
    directory = Path(directory)
    if directory.is_symlink():
        raise ValueError("invalid_scope_cache_directory")
    if not directory.exists():
        return []
    if not directory.is_dir():
        raise ValueError("invalid_scope_cache_directory")
    paths = sorted(directory.glob("*.json"))
    if len(paths) > MAX_CACHE_ITEMS:
        raise ValueError("invalid_scope_cache_directory")
    documents = []
    for path in paths:
        if (path.is_symlink() or not path.is_file() or not HEX.fullmatch(path.stem)
                or not 0 < path.stat().st_size <= maximum_bytes):
            raise ValueError("invalid_scope_cache_file")
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or document.get("candidate_id") != path.stem:
            raise ValueError("invalid_scope_cache_identity")
        documents.append(document)
    return documents


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--drafts", type=Path, help="Compatibility path; dossiers are not a classification prerequisite")
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--scope-reviews", type=Path)
    parser.add_argument("--attempts", type=Path)
    parser.add_argument("--verifications", type=Path)
    parser.add_argument("--rechecks", type=Path)
    parser.add_argument("--excluded-ids", type=Path, help="JSON array of explicit human exclusions only")
    parser.add_argument("--max-requests", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=MAX_BATCH_ITEMS)
    parser.add_argument("--max-seconds", type=int, default=720)
    args = parser.parse_args()
    try:
        inbox = GitHubInbox(os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"])
        config = BudgetConfig(**json.loads(os.environ["MDH_GEMINI_BUDGET_CONFIG"]))
        budget = GeminiBudget(inbox, config)
        root = args.drafts.parent if args.drafts else Path.cwd()
        reviews = load_scope_cache(args.scope_reviews or root / "scope-reviews")
        attempts = load_scope_cache(args.attempts or root / "scope-attempts", maximum_bytes=10_000)
        verifications = load_scope_cache(args.verifications or root / "scope-verifications")
        from .news_scope_rechecks import load_rechecks
        rechecks = load_rechecks(args.rechecks or root / "runtime" / "news-scope-rechecks.json")
        excluded = json.loads(args.excluded_ids.read_text()) if args.excluded_ids else []
        if args.archive.is_symlink() or not 0 < args.archive.stat().st_size <= 30_000_000:
            raise ValueError("invalid_scope_archive_file")
        report = recheck_scope(inbox, [], json.loads(args.archive.read_text()),
            api_key=os.environ["GEMINI_API_KEY"], model=config.model, budget=budget,
            max_requests=args.max_requests, batch_size=args.batch_size, max_seconds=args.max_seconds,
            excluded_ids=excluded, scope_reviews=reviews, attempts=attempts, verifications=verifications, rechecks=rechecks)
    except Exception as error:
        failure = {"status": "failed", "error_type": type(error).__name__, "publications": 0}
        if isinstance(error, GitHubRequestError):
            failure["error_code"] = error.code
        print(json.dumps(failure))
        raise SystemExit(1) from None
    print(json.dumps({key: value for key, value in report.items() if key not in {"selected_ids", "updated_reviews", "updated_verifications"}}))
    if report["stopped_reason"] == "model_error":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
