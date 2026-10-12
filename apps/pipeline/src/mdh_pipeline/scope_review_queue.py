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
from .news_selection import _archive_index, validate_scope_review
from .news_scope import SCOPE_PROMPT, classify_scope_batch, prepare_scope_batch
from .review_queue import GitHubInbox, GitHubRequestError

HEX = re.compile(r"^[a-f0-9]{64}$")
ATTEMPT_KEYS = {"schema_version", "candidate_id", "source_url", "status", "error_code", "attempted_at", "next_retry_at"}
SOURCE_ERRORS = {"source_access_restricted", "source_not_found", "source_network_error", "source_text_too_short",
                 "source_article_container_missing", "source_http_error", "source_too_large", "source_fetch_failed",
                 "source_identity_mismatch", "source_publication_date_missing", "source_input_invalid",
                 "source_input_too_large", "unsupported_source_content_type", "invalid_source_document",
                 "invalid_source_url", "redirect_outside_source", "unsafe_source_dns", "too_many_redirects"}
MAX_CACHE_ITEMS = 100_000


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
        expected = timedelta(days=7 if document["error_code"] == "source_access_restricted" else 1)
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
    payload = {"message": "Store private news scope assessment" if folder == "scope-reviews" else "Record bounded source retry",
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
                  batch_size=8, max_seconds=720, excluded_ids=(), scope_reviews=(), attempts=(),
                  fetch=fetch_article, classify=classify_scope_batch, clock=time.monotonic,
                  sleep=time.sleep, now=None):
    """Classify all bibliography references independently of drafts or issues.

    drafts remains for caller compatibility. Load scope_reviews and attempts once
    from the private checkout; only actual writes read the latest GitHub SHA.
    selected_ids is internal orchestration data and must be omitted from logs.
    """
    if (type(max_requests) is not int or not 1 <= max_requests <= 50
            or type(batch_size) is not int or not 1 <= batch_size <= 8):
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
                  http_requests=0, stopped_reason=None, selected_ids=[])
    selected, pending = set(), []
    for identifier, reference in references.items():
        if identifier in excluded:
            continue
        prior = reviews.get(identifier)
        if prior and prior["extraction"]["prompt_version"] == SCOPE_PROMPT:
            report["cached"] += 1
            if _positive(prior):
                selected.add(identifier)
            continue
        if identifier in retries and _instant(retries[identifier]["next_retry_at"]) > instant:
            report["retry_deferred"] += 1
            continue
        pending.append(reference)
    deadline = clock() + max_seconds
    last_write, batch = None, []

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
                    "next_retry_at": (timestamp + timedelta(days=7 if code == "source_access_restricted" else 1)).isoformat()}
        validate_scope_attempt(document, reference=reference, now=timestamp)
        _put_private(inbox, "scope-attempts", document, before_write=pace_write)
        report["source_unavailable"] += 1

    def submit():
        nonlocal batch
        if not batch:
            return
        results = classify(batch, api_key=api_key, model=model, budget=budget)
        if not isinstance(results, list) or len(results) != len(batch):
            raise ValueError("invalid_scope_batch_results")
        # Validate all results before storing any prefix of a malformed batch.
        for result, item in zip(results, batch, strict=True):
            validate_scope_review(result, reference=item["reference"],
                content_sha256=item["article"]["content_sha256"], now=now)
            receipt = {key: item["article"][key] for key in ("fetched_at", "extraction_method", "truncated", "content_sha256")}
            if (result["schema_version"] != 2 or result["extraction"]["prompt_version"] != SCOPE_PROMPT
                    or result.get("source_read") != receipt):
                raise ValueError("scope_source_receipt_mismatch")
        for result in results:
            _put_private(inbox, "scope-reviews", result, before_write=pace_write)
            report["reviewed"] += 1
            report["classified"] += 1
            if _positive(result):
                selected.add(result["candidate_id"])
                report["relevant_sv"] += 1
                report["accepted"] += 1
        batch = []

    try:
        # Revisit every finite older-policy decision, including positives,
        # before advancing through never-classified articles chronologically.
        # Failed reads get durable backoff and cannot starve the remaining tail.
        def priority(value):
            prior = reviews.get(value["id"])
            outdated = prior and prior["extraction"]["prompt_version"] != SCOPE_PROMPT
            return (0 if outdated else 1, value["published_date"], value["id"])
        for reference in sorted(pending, key=priority):
            if not available():
                report["stopped_reason"] = "run_limit"
                break
            if report["sources_checked"] >= maximum_sources:
                report["stopped_reason"] = "source_check_limit"
                break
            report["sources_checked"] += 1
            try:
                article = fetch({**reference, "source_title": reference["title"]})
                item = {"reference": reference, "article": article}
                prepare_scope_batch([item])
            except Exception as error:
                if isinstance(error, GeminiDraftError):
                    error = GeminiDraftError("source_input_too_large" if error.code == "scope_request_too_large" else "source_input_invalid")
                failed_source(reference, error)
                continue
            if not available():
                report["stopped_reason"] = "run_limit"
                break
            try:
                _, prospective = prepare_scope_batch([*batch, item])
                fits = estimate_input_tokens(prospective) <= getattr(budget.config, "input_tpm", 100_000)
            except GeminiDraftError as error:
                if error.code not in {"scope_request_too_large", "invalid_scope_batch", "invalid_scope_input"}:
                    raise
                fits = False
            if batch and (len(batch) >= batch_size or not fits):
                submit()
                if not available():
                    report["stopped_reason"] = "run_limit"
                    break
                _, prospective = prepare_scope_batch([item])
                fits = estimate_input_tokens(prospective) <= getattr(budget.config, "input_tpm", 100_000)
            if not fits:
                failed_source(reference, GeminiDraftError("source_input_too_large"))
                continue
            batch.append(item)
            if len(batch) == batch_size:
                submit()
        if batch and available():
            submit()
    except QuotaDeferred as error:
        report["stopped_reason"] = "budget_deferred"
        report["quota_reason"] = error.reason
    except GeminiDraftError as error:
        report["stopped_reason"] = "model_error"
        report["error_code"] = error.code
    report["http_requests"] = budget.requests_this_run - starting_requests
    report["selected_ids"] = sorted(selected)
    report["classified_total"] = report["cached"] + report["classified"]
    report["accepted_total"] = len(selected)
    report["remaining"] = report["candidates"] - report["classified_total"]
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
    parser.add_argument("--excluded-ids", type=Path, help="JSON array of explicit human exclusions only")
    parser.add_argument("--max-requests", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--max-seconds", type=int, default=720)
    args = parser.parse_args()
    try:
        inbox = GitHubInbox(os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"])
        config = BudgetConfig(**json.loads(os.environ["MDH_GEMINI_BUDGET_CONFIG"]))
        budget = GeminiBudget(inbox, config)
        root = args.drafts.parent if args.drafts else Path.cwd()
        reviews = load_scope_cache(args.scope_reviews or root / "scope-reviews")
        attempts = load_scope_cache(args.attempts or root / "scope-attempts", maximum_bytes=10_000)
        excluded = json.loads(args.excluded_ids.read_text()) if args.excluded_ids else []
        if args.archive.is_symlink() or not 0 < args.archive.stat().st_size <= 30_000_000:
            raise ValueError("invalid_scope_archive_file")
        report = recheck_scope(inbox, [], json.loads(args.archive.read_text()),
            api_key=os.environ["GEMINI_API_KEY"], model=config.model, budget=budget,
            max_requests=args.max_requests, batch_size=args.batch_size, max_seconds=args.max_seconds,
            excluded_ids=excluded, scope_reviews=reviews, attempts=attempts)
    except Exception as error:
        failure = {"status": "failed", "error_type": type(error).__name__, "publications": 0}
        if isinstance(error, GitHubRequestError):
            failure["error_code"] = error.code
        print(json.dumps(failure))
        raise SystemExit(1) from None
    print(json.dumps({key: value for key, value in report.items() if key != "selected_ids"}))
    if report["stopped_reason"] == "model_error":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
