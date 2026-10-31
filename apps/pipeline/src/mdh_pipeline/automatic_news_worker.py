"""Bounded batch orchestration; one private receipt per news item, shared HTTP quota."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import time
from datetime import datetime, timezone

from . import automatic_news as single
from .json_files import write_processing_json
from .contracts.processing import selected_references, selection_content_hash
from .instance import load_instance
from .automatic_news_batch import batch_input_limit, evaluate_batch, prepare_evaluation_batch
from .gemini_budget import QuotaDeferred, QuotaStateError


class _DeadlineBudget:
    """Keep retry dispatches inside this worker's time bound, not just its first POST."""
    def __init__(self, budget, deadline, clock):
        self.budget, self.deadline, self.run_clock = budget, deadline, clock

    def __getattr__(self, name):
        return getattr(self.budget, name)

    def _check(self):
        if self.run_clock() >= self.deadline:
            raise QuotaDeferred("run_time_limit", datetime.now(timezone.utc))

    def reserve(self, payload):
        self._check()
        return self.budget.reserve(payload)

    def assert_dispatch_allowed(self, reservation):
        self._check()
        return self.budget.assert_dispatch_allowed(reservation)


def ready_items(inbox_dir, archive, *, excluded_ids=(), request_generations=None):
    excluded, generations = single._requests(excluded_ids, request_generations or {})
    references = selected_references(archive)
    drafts = single._inventory(Path(inbox_dir) / "drafts", required=True)
    cached = single._inventory(Path(inbox_dir) / "automatic-news")
    ready = []
    for identifier, reference in references.items():
        if identifier in excluded or identifier not in drafts:
            continue
        try:
            document = single._bound_draft(drafts[identifier], reference)
        except ValueError:
            continue
        generation = generations.get(identifier, 0)
        key = single.fingerprint(document, generation)
        prior = cached.get(identifier)
        if prior is not None:
            prior = single.validate_receipt(prior)
            if single._cached(prior, key, document, reference):
                continue
        ready.append({"document": document, "reference": reference, "generation": generation, "fingerprint": key})
    # Explicit requests first, then oldest prepared work. A stable queue avoids
    # letting newly generated low-hash IDs continually jump ahead of old work.
    return sorted(ready, key=lambda item: (not bool(item["generation"]),
        item["document"].get("extraction", {}).get("prepared_at", ""), item["reference"]["id"]))


def evaluate_queue_batched(inbox, inbox_dir, archive, *, api_key, model, budget, limit=10,
                           batch_size=4, fetch=None, batch_evaluate=None, extract=None,
                           excluded_ids=(), request_generations=None, now=None,
                           max_seconds=720, run_clock=time.monotonic):
    from .article_source import fetch_article, SourceFetchError
    if (inbox.repository.lower() != load_instance().private_repository.lower() or budget is None
            or model != budget.config.model or type(limit) is not int or not 1 <= limit <= 50
            or type(batch_size) is not int or not 1 <= batch_size <= 4
            or type(max_seconds) is not int or not 1 <= max_seconds <= 1800):
        raise single.AutomaticNewsError("invalid_automatic_worker_configuration")
    ready = ready_items(inbox_dir, archive, excluded_ids=excluded_ids, request_generations=request_generations)
    report = dict(candidates=len(ready), attempted=0, cached=0, evaluated=0, publishable=0,
                  source_refreshed=0, source_changed=0, source_unavailable=0, errors=0,
                  deferred=0, http_requests=0, stopped_reason=None, batch_size=batch_size,
                  batches=0, batch_sizes=[], recovery_splits=0, sources_checked=0)
    snapshot = budget.snapshot()
    grant = min(limit, budget.config.batch_ceiling, snapshot["remaining_requests"])
    if snapshot.get("cooldown_until") or not grant:
        report.update(deferred=len(ready), stopped_reason="budget_deferred")
        return report
    budget.set_request_limit(grant)
    fetch = fetch or fetch_article
    batch_evaluate = batch_evaluate or evaluate_batch
    extract = extract or single.extract_draft
    deadline = run_clock() + max_seconds
    dispatch_budget = _DeadlineBudget(budget, deadline, run_clock)
    attempted, evaluated, published, failed, unavailable, deferred = (set() for _ in range(6))
    maximum_sources = min(200, grant * batch_size * 2)
    batch = []

    def available():
        return (not report["stopped_reason"] and run_clock() < deadline
                and budget.requests_this_run < grant)

    def store(item):
        try:
            single.validate_receipt(item["receipt"])
            item["sha"] = single._store(inbox, item["receipt"], item["sha"], inbox_dir)
        except Exception:
            # Uncertain private writes cannot be treated as recoverable model
            # errors or followed by another provider request.
            raise QuotaStateError("batch_receipt_write_failed") from None

    def begin(item):
        identifier = item["reference"]["id"]
        receipt = item["receipt"]
        if receipt["attempts"] >= single.MAX_ATTEMPTS:
            return False
        receipt.update(status="evaluating", attempts=receipt["attempts"] + 1,
                       dossier=None, judgment=None, usage=None, error_code=None,
                       updated_at=single._instant(now).isoformat())
        store(item)  # acknowledged before any HTTP call for this item
        attempted.add(identifier)
        return True

    def error_result(items, error, requests_before):
        code = single._safe_error_code(error)
        for item in items:
            identifier = item["reference"]["id"]
            if isinstance(error, QuotaDeferred):
                # One shared reservation charges every member's attempt. A
                # pre-dispatch deferral alone refunds the candidate attempt.
                item["receipt"].update(status="pending", error_code="budget_deferred",
                    attempts=item["receipt"]["attempts"] - int(budget.requests_this_run == requests_before))
                deferred.add(identifier)
                failed.discard(identifier)
            else:
                source_failure = isinstance(error, SourceFetchError)
                item["receipt"].update(status="source_unavailable" if source_failure else "error", error_code=code)
                (unavailable if source_failure else failed).add(identifier)
            store(item)
        if isinstance(error, QuotaDeferred):
            report["stopped_reason"] = "run_time_limit" if error.reason == "run_time_limit" else "budget_deferred"
        elif code in {"provider_http_400", "provider_http_404"}:
            report["stopped_reason"] = "provider_configuration"
        elif code in {"provider_http_401", "provider_http_403"}:
            report["stopped_reason"] = "provider_authentication"
        report["last_error_code"] = "budget_deferred" if isinstance(error, QuotaDeferred) else code

    def submit(items):
        if not items:
            return
        if not available():
            deferred.update(item["reference"]["id"] for item in items)
            return
        active = [item for item in items if item["receipt"]["attempts"] < single.MAX_ATTEMPTS]
        if not active:
            return
        for item in active:
            begin(item)
        # Receipt writes/source preparation also take time. No late POST after
        # the deadline, even if the outer iteration began inside its allowance.
        before = budget.requests_this_run
        if not available():
            for item in active:
                item["receipt"].update(status="pending", attempts=item["receipt"]["attempts"] - 1,
                                       error_code="run_time_limit")
                store(item)
                deferred.add(item["reference"]["id"])
            return
        report["batches"] += 1
        report["batch_sizes"].append(len(active))
        try:
            results = batch_evaluate([{key: item[key] for key in ("document", "reference", "article")} for item in active],
                api_key=api_key, model=model, budget=dispatch_budget, now=now)
            ids = {item["reference"]["id"] for item in active}
            if not isinstance(results, dict) or set(results) != ids:
                raise single.AutomaticNewsError("invalid_automatic_batch_response")
            receipts = []
            # Validate every row before persisting a successful prefix. An
            # invalid/missing/foreign row never becomes a partially approved batch.
            for item in active:
                result = results[item["reference"]["id"]]
                if not isinstance(result, dict) or set(result) != {"judgment", "dossier", "usage"}:
                    raise single.AutomaticNewsError("invalid_automatic_batch_response")
                judgment = single._validate_judgment(result["judgment"], {p["id"]: p["text"] for p in item["article"]["paragraphs"]})
                receipt = deepcopy(item["receipt"])
                receipt.update(status="evaluated" if result["dossier"] is not None else "no_supported_fields",
                               judgment=judgment, dossier=result["dossier"], usage=result["usage"])
                if any(judgment[key]["verdict"] != "supported" for key in ("jurisdiction", "relevance")):
                    receipt.update(status="scope_rejected", dossier=None)
                single.validate_receipt(receipt)
                if receipt["dossier"] is not None and receipt["dossier"] != single._canonical_receipt_projection(item["document"], receipt):
                    raise single.AutomaticNewsError("automatic_projection_not_canonical")
                receipts.append(receipt)
        except QuotaStateError:
            raise
        except Exception as error:
            error_result(active, error, before)
            recoverable = getattr(error, "code", None) in {
                "invalid_automatic_batch_response", "automatic_batch_response_incomplete"}
            if recoverable and len(active) > 1 and available():
                report["recovery_splits"] += 1
                middle = len(active) // 2
                submit(active[:middle])
                submit(active[middle:])
            return
        for item, receipt in zip(active, receipts, strict=True):
            item["receipt"] = receipt
            store(item)
            identifier = item["reference"]["id"]
            evaluated.add(identifier)
            failed.discard(identifier)
            deferred.discard(identifier)
            if receipt["dossier"] is not None:
                published.add(identifier)

    for queued in ready:
        if not available() or report["sources_checked"] >= maximum_sources:
            break
        item = dict(queued)
        identifier, reference, document = item["reference"]["id"], item["reference"], item["document"]
        previous, sha = single._remote_file(inbox, "automatic-news", identifier)
        if previous is not None:
            previous = single.validate_receipt(previous)
            if single._cached(previous, item["fingerprint"], document, reference):
                write_processing_json(Path(inbox_dir) / "automatic-news" / (identifier + ".json"), previous)
                report["cached"] += 1
                continue
        receipt = deepcopy(previous) if previous and previous["fingerprint"] == item["fingerprint"] else single._receipt(document, reference, item["generation"], now=now)
        if (single.needs_scope_reassessment(receipt, reference)
                or (receipt["status"] == "source_changed" and selection_content_hash(reference) == document["extraction"]["content_sha256"])
                or (receipt["status"] in single.TERMINAL and selection_content_hash(reference) != document["extraction"]["content_sha256"])):
            receipt = single._receipt(document, reference, item["generation"], now=now)
        item.update(receipt=receipt, sha=sha)
        before = budget.requests_this_run
        try:
            report["sources_checked"] += 1
            article = fetch(reference)
            single._input(reference, article)
            item["article"] = article
            if not available():
                deferred.add(identifier)
                break
            if (article["content_sha256"] != document["extraction"]["content_sha256"]
                    or article["content_sha256"] != selection_content_hash(reference)):
                if not begin(item):
                    continue
                report["source_changed"] += 1
                item["receipt"]["status"] = "source_changed"
                if article["content_sha256"] != document["extraction"]["content_sha256"]:
                    current, draft_sha = single._remote_file(inbox, "drafts", identifier)
                    if current is None or single._hash(current) != single._hash(document):
                        raise single.AutomaticNewsError("automatic_draft_changed")
                    if not available():
                        raise QuotaDeferred("run_time_limit", single._instant(now))
                    updated = extract(reference, article, api_key=api_key, model=model, budget=dispatch_budget)
                    single._bound_draft(updated, reference)
                    if updated["extraction"]["content_sha256"] != article["content_sha256"]:
                        raise single.AutomaticNewsError("automatic_refresh_hash_mismatch")
                    single.put_private_file(inbox, "drafts", identifier, updated, draft_sha)
                    write_processing_json(Path(inbox_dir) / "drafts" / (identifier + ".json"), updated)
                    item["receipt"]["status"] = "source_refreshed"
                    report["source_refreshed"] += 1
                store(item)
                continue
            try:
                prepare_evaluation_batch([{key: row[key] for key in ("document", "reference", "article")} for row in [*batch, item]],
                                         max_input_tokens=batch_input_limit(budget))
            except single.AutomaticNewsError as error:
                if getattr(error, "code", None) != "automatic_batch_input_too_large" or not batch:
                    raise
                submit(batch)
                batch = []
                if not available():
                    deferred.add(identifier)
                    break
                prepare_evaluation_batch([{key: item[key] for key in ("document", "reference", "article")}],
                                         max_input_tokens=batch_input_limit(budget))
            batch.append(item)
            if len(batch) == batch_size:
                submit(batch)
                batch = []
        except QuotaStateError:
            raise
        except Exception as error:
            if item["receipt"]["status"] != "evaluating":
                if not begin(item):
                    continue
            error_result([item], error, before)
    submit(batch)
    if report["stopped_reason"] is None and (run_clock() >= deadline or budget.requests_this_run >= grant):
        report["stopped_reason"] = "run_time_limit" if run_clock() >= deadline else "run_limit"
    report.update(attempted=len(attempted), evaluated=len(evaluated), publishable=len(published),
                  errors=len(failed), source_unavailable=len(unavailable), deferred=len(deferred),
                  http_requests=budget.requests_this_run)
    return report
