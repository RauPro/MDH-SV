"""Bounded, resumable Gemini extraction into the PRIVATE editorial repository only."""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import time
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .review_queue import GitHubInbox, GitHubRequestError, canonical_url

AI_LABELS = {"ai/ready": "16815D", "ai/scope-review": "D97706",
             "ai/privacy-review": "9333EA", "ai/error": "B91C1C", "ai/pending": "D97706"}
MAX_FILE_BYTES = 180_000
DOSSIER_VERSION = 1
PRIVACY_POLICY_VERSION = 2
COMPLETE_MARKER = re.compile(r"<!-- mdh-news-dossier:v([12]):([a-f0-9]{40,64}) -->")
PRIVACY_RETRY_MARKER = "<!-- mdh-field-privacy-retry:v2 -->"
MIN_WRITE_INTERVAL = 2.0
MAX_RUN_SECONDS = 720


class _PacedInbox:
    """Avoid GitHub secondary write bursts, including offline migration/repairs.

    At most about 30 writes/minute leaves room below 80 for the collection steps
    immediately before this runner. The original authenticated client is never
    forwarded to a source or model, and no rate state contains article contents.
    """
    def __init__(self, inbox, sleep, clock=time.monotonic):
        self._inbox, self._sleep, self._clock = inbox, sleep, clock
        self._last_write = None
        self.write_calls = 0

    def __getattr__(self, name):
        return getattr(self._inbox, name)

    def call(self, method, path, **kwargs):
        if method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            if self._last_write is not None:
                delay = MIN_WRITE_INTERVAL - (self._clock() - self._last_write)
                if delay > 0:
                    self._sleep(delay)
            self._last_write = self._clock()
            self.write_calls += 1
        return self._inbox.call(method, path, **kwargs)


def utcnow():
    return datetime.now(timezone.utc)


def is_pending(issue):
    labels = {label["name"] for label in issue.get("labels", [])}
    return (issue.get("state") == "open" and "pull_request" not in issue
            and "review/pending" in labels and not labels.intersection({"review/approved", "review/rejected"}))


def candidate_from_issue(issue):
    body = issue.get("body") or ""
    marker = re.search(r"<!-- mdh-candidate:([0-9a-f]{64}) -->", body)
    blocks = re.findall(r"```json\n(.*?)\n```", body, re.S)
    if not marker or not blocks or len(blocks[0].encode()) > 16_384:
        raise ValueError("invalid_candidate_metadata")
    candidate = json.loads(blocks[0])
    if not isinstance(candidate, dict):
        raise ValueError("invalid_candidate_metadata")
    url = canonical_url(candidate.get("source_url", ""))
    identifier = hashlib.sha256(url.encode()).hexdigest()
    if marker.group(1) != identifier or candidate.get("id") != identifier or candidate.get("status") != "pending":
        raise ValueError("candidate_identity_mismatch")
    # Do not forward arbitrary issue fields, instructions, files or attachments to a model.
    return {key: candidate[key] for key in ("id", "source_url", "source_title", "publisher",
            "published_date", "discovered_at", "matched_terms", "status", "record_type",
            "discovery_method", "scope_hint", "scope_terms") if key in candidate}


def pending_issues(inbox):
    results = []
    for page in range(1, 201):
        rows = inbox.call("GET", inbox.base + "/issues", params={"state": "open", "labels": "review/pending", "per_page": 100, "page": page})
        results.extend(issue for issue in rows if is_pending(issue))
        if len(rows) < 100:
            return results
    raise RuntimeError("incomplete_queue_pagination")


def read_private_file(inbox, folder, identifier):
    if folder not in ("drafts", "processing") or not re.fullmatch(r"[a-f0-9]{64}", identifier):
        raise ValueError("invalid_private_path")
    file = inbox.get_file(inbox.base + f"/contents/{folder}/{identifier}.json")
    if file is None:
        return None
    expected_path = f"{folder}/{identifier}.json"
    if (not isinstance(file, dict) or file.get("type") != "file" or file.get("path") != expected_path
            or file.get("encoding") != "base64" or type(file.get("size")) is not int
            or not 0 < file["size"] <= MAX_FILE_BYTES or not isinstance(file.get("content"), str)
            or len(file["content"]) > MAX_FILE_BYTES * 2
            or not isinstance(file.get("sha"), str) or not re.fullmatch(r"[a-f0-9]{40,64}", file["sha"])):
        raise ValueError("invalid_private_file")
    try:
        raw = base64.b64decode(file["content"].replace("\n", ""), validate=True)
        if len(raw) != file["size"]:
            raise ValueError("invalid_private_file_size")
        content = json.loads(raw)
        if not isinstance(content, dict):
            raise ValueError("invalid_private_file_object")
        return content, file["sha"]
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("invalid_private_file_content") from None


def existing_draft_ids(inbox):
    tree = inbox.call("GET", inbox.base + "/git/trees/main", params={"recursive": "1"})
    if not isinstance(tree, dict) or tree.get("truncated") is not False or not isinstance(tree.get("tree"), list):
        raise ValueError("incomplete_private_draft_index")
    identifiers = {}
    for entry in tree["tree"]:
        match = re.fullmatch(r"drafts/([a-f0-9]{64})\.json", entry.get("path", ""))
        if match and entry.get("type") == "blob":
            sha = entry.get("sha")
            if not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{40,64}", sha):
                raise ValueError("invalid_private_draft_index_sha")
            identifiers[match.group(1)] = sha
    return identifiers


def validate_state(state, candidate):
    if (not isinstance(state, dict) or state.get("candidate_id") != candidate["id"]
            or state.get("source_url") != candidate["source_url"]
            or state.get("status") not in ("error", "completed")
            or type(state.get("attempts")) is not int or state["attempts"] < 0):
        raise ValueError("invalid_processing_state")
    if "privacy_reprocessing_version" in state or "privacy_reprocessing_attempts" in state:
        if (type(state.get("privacy_reprocessing_version")) is not int
                or state["privacy_reprocessing_version"] != PRIVACY_POLICY_VERSION
                or type(state.get("privacy_reprocessing_attempts")) is not int
                or state["privacy_reprocessing_attempts"] < 0):
            raise ValueError("invalid_privacy_reprocessing_state")
    for key in ("updated_at", "next_retry_at"):
        if state.get(key) is not None:
            try:
                parsed = datetime.fromisoformat(state[key])
                if parsed.tzinfo is None:
                    raise ValueError()
            except (ValueError, TypeError):
                raise ValueError("invalid_processing_state_time") from None


def put_private_file(inbox, folder, identifier, data, existing_sha=None):
    if folder not in ("drafts", "processing") or not re.fullmatch(r"[a-f0-9]{64}", identifier):
        raise ValueError("invalid_private_path")
    raw = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError("oversized_private_file")
    payload = {"message": f"Store private {folder} for candidate {identifier[:12]}", "content": base64.b64encode(raw).decode()}
    if existing_sha:
        payload["sha"] = existing_sha
    result = inbox.call("PUT", inbox.base + f"/contents/{folder}/{identifier}.json", json=payload)
    sha = (result or {}).get("content", {}).get("sha")
    if not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{40,64}", sha):
        raise ValueError("invalid_private_write_receipt")
    return sha


def validate_envelope(document, candidate, *, allow_legacy=False):
    if (not isinstance(document, dict) or type(document.get("schema_version")) is not int or document.get("schema_version") != 1 or document.get("candidate_id") != candidate["id"]
            or document.get("source_url") != candidate["source_url"] or document.get("publishable") is not False
            or document.get("human_reviewed") is not False
            or document.get("status") not in ("draft", "needs_scope_review", "needs_privacy_review", "pending", "source_unavailable", "error")):
        raise ValueError("invalid_draft_envelope")
    if not allow_legacy or "news_dossier" in document:
        from .news_dossier import validate_news_dossier
        try:
            news = validate_news_dossier(document.get("news_dossier"))
        except (ValueError, TypeError):
            raise ValueError("invalid_news_dossier") from None
        if (news.get("candidate_id") != candidate["id"] or news.get("source_url") != candidate["source_url"]
                or news.get("human_reviewed") is not False):
            raise ValueError("invalid_news_dossier_identity")
        expected = document["status"] if document["status"] in ("pending", "source_unavailable", "error") else "processed"
        if news.get("extraction_status") != expected:
            raise ValueError("invalid_news_dossier_status")
        document["news_dossier"] = news
    record = document.get("record")
    if document["status"] == "draft" and record is not None:
        from .publication import Publication
        if (not isinstance(record, dict) or record.get("human_reviewed") is not False
                or record.get("origin_candidate_id") != candidate["id"]
                or record.get("source_url") != candidate["source_url"] or document.get("relevant") is not True
                or document.get("classification", {}).get("jurisdiction") != "SV"
                or document.get("classification", {}).get("relevance") != "relevant"):
            raise ValueError("invalid_unreviewed_record")
        # Validate the field structure without removing the publication guard from the stored draft.
        try:
            Publication.model_validate({**record, "origin_candidate_id": None})
        except ValueError:
            raise ValueError("invalid_unreviewed_record_fields") from None
    elif record is not None:
        raise ValueError("unsafe_scope_or_privacy_record")


def complete_document(document):
    return document.get("news_dossier", {}).get("extraction_status") == "processed"


def needs_field_privacy_reprocessing(document):
    """Only the former blanket redaction warrants a new bounded model budget.

    A valid new-policy dossier may legitimately protect every field. It must not
    be repeatedly extracted, and older safe dossiers need no model call either.
    """
    from .news_dossier import FIELD_NAMES
    fields = document.get("news_dossier", {}).get("fields", {})
    return (complete_document(document) and document.get("status") == "needs_privacy_review"
            and document.get("extraction", {}).get("privacy_policy_version") != PRIVACY_POLICY_VERSION
            and set(fields) == set(FIELD_NAMES)
            and all(field.get("status") == "withheld" for field in fields.values()))


def skeleton_envelope(candidate, status="pending", *, error_code=None, model=None):
    from .news_dossier import empty_news_dossier
    result = {"schema_version": 1, "candidate_id": candidate["id"], "source_url": candidate["source_url"],
              "status": status, "publishable": False, "human_reviewed": False,
              "relevant": None, "classification": {"jurisdiction": "unclear", "relevance": "unclear", "record_type": "undetermined"},
              "record": None, "news_dossier": empty_news_dossier(candidate, extraction_status=status),
              "grounding": {}, "extraction": {"provider": "none", "model": model,
                  "prepared_at": utcnow().isoformat(), "dossier_schema_version": DOSSIER_VERSION}}
    if error_code:
        result["extraction"]["error_code"] = error_code
    validate_envelope(result, candidate)
    return result


def ensure_ai_labels(inbox):
    existing = {label["name"] for label in inbox.call("GET", inbox.base + "/labels", params={"per_page": 100})}
    for name, color in AI_LABELS.items():
        if name not in existing:
            inbox.call("POST", inbox.base + "/labels", json={"name": name, "color": color})


def update_issue(inbox, issue_number, candidate, document=None, error_code=None, draft_sha=None):
    # A human may have changed the decision while Gemini was running. Never undo it.
    issue = inbox.call("GET", inbox.base + f"/issues/{issue_number}")
    if not is_pending(issue):
        return False
    if candidate_from_issue(issue)["id"] != candidate["id"]:
        raise ValueError("changed_candidate_identity")
    body = re.sub(r"\n<!-- mdh-ai:start -->.*?<!-- mdh-ai:end -->\n?", "", issue.get("body") or "", flags=re.S)
    if document:
        statuses = {"draft": ("ai/ready", "Ficha de noticia preparada; revisión editorial pendiente."),
                    "needs_scope_review": ("ai/scope-review", "Ficha de noticia disponible; su pertinencia o jurisdicción requiere revisión. No equivale a un caso validado."),
                    "needs_privacy_review": ("ai/privacy-review", "Ficha de noticia disponible con campos protegidos; requiere revisión de privacidad."),
                    "pending": ("ai/pending", "Ficha de noticia pendiente de extracción; todavía no se ha evaluado el contenido."),
                    "source_unavailable": ("ai/error", "Ficha de noticia disponible; no fue posible consultar la fuente y sus campos siguen pendientes."),
                    "error": ("ai/error", "Ficha de noticia disponible; la extracción falló y sus campos siguen pendientes.")}
        label, notice = statuses[document["status"]]
        detail = f"[Abrir borrador privado](https://github.com/{inbox.repository}/blob/main/drafts/{candidate['id']}.json)"
        if document.get("extraction", {}).get("truncated"):
            notice += " La entrada fue recortada por tamaño: revise el artículo completo."
        if error_code:
            detail += f"\n\nCódigo de diagnóstico: `{error_code}`. El reintento conserva sus límites."
        if complete_document(document) and isinstance(draft_sha, str) and re.fullmatch(r"[a-f0-9]{40,64}", draft_sha):
            detail += f"\n<!-- mdh-news-dossier:v2:{draft_sha} -->"
        elif document.get("extraction", {}).get("privacy_reprocessing_version") == PRIVACY_POLICY_VERSION:
            detail += f"\n{PRIVACY_RETRY_MARKER}"
    else:
        label, notice = "ai/error", "La extracción no se completó. No se inventó una ficha ni se publicó contenido."
        detail = f"Código de diagnóstico: `{error_code}`. Consulte el estado privado de procesamiento."
    section = ("\n<!-- mdh-ai:start -->\n## Extracción asistida\n\n" + notice + "\n\n" + detail
               + "\n\nEl borrador no acredita los hechos. Revise fuente, citas, personas y fechas antes de aprobar. "
               "La ficha de noticia y una eventual propuesta de caso son unidades distintas; ningún resultado se publica automáticamente.\n<!-- mdh-ai:end -->\n")
    labels = [value["name"] for value in issue.get("labels", []) if not value["name"].startswith("ai/")]
    inbox.call("PATCH", inbox.base + f"/issues/{issue_number}", json={"body": body + section, "labels": [*labels, label]})
    return True


def error_category(error):
    status = getattr(error, "status_code", None)
    code = getattr(error, "code", None)
    if status == 429 or code in ("quota_exceeded", "rate_limited"):
        return "quota_or_rate_limit", True
    if status in (401, 403) or code in ("authentication_failed", "invalid_api_key"):
        return "provider_authentication", True
    if code == "provider_http_error" and status in (400, 404):
        return "provider_configuration", True
    if code == "provider_unavailable" or (code == "provider_http_error" and status and status >= 500):
        return "provider_unavailable", True
    # Never persist str(error): HTTP/validation exceptions can contain content or credentials.
    if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code):
        return code, False
    return "extraction_failed", False



def safe_validation_issues(error):
    """Retain schema locations/types only, never model values or validator messages."""
    from .gemini_draft import ExtractedRecord, WireOutput, WireScopeEvidence, WireClaim
    allowed = set(ExtractedRecord.model_fields) | set(WireOutput.model_fields)
    allowed |= set(WireScopeEvidence.model_fields) | set(WireClaim.model_fields)
    allowed |= {"value", "status", "citations", "paragraph_id", "quote", "str", "bool", "list[str]", "unknown"}
    types = {"too_long", "too_short", "missing", "literal_error", "list_type", "string_type", "bool_type",
             "string_pattern_mismatch", "extra_forbidden", "value_error", "model_type", "int_type"}
    issues = getattr(error, "validation_issues", [])
    if not isinstance(issues, list):
        return []
    result = []
    for item in issues[:8]:
        if not isinstance(item, dict):
            continue
        value = item.get("field")
        tokens = value.split(".")[:10] if isinstance(value, str) and len(value) <= 200 else ["unknown"]
        field = ".".join(token if token in allowed or (token.isdigit() and len(token) <= 4) else "unknown" for token in tokens)
        kind = item.get("type")
        result.append({"field": field, "type": kind if isinstance(kind, str) and kind in types else "validation_error"})
    return result

def hourly_request_allowance(snapshot, config, requested_limit, now):
    pacific = now.astimezone(ZoneInfo("America/Los_Angeles"))
    reset_local = (pacific + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    remaining_hours = max(1, math.ceil((reset_local.astimezone(timezone.utc) - now).total_seconds() / 3600))
    share = math.ceil(snapshot["remaining_requests"] / remaining_hours)
    return min(requested_limit, config.batch_ceiling, snapshot["remaining_requests"], share), remaining_hours


def process_queue(inbox, *, api_key, model, limit=20, max_attempts=3, pause_seconds=4,
                  retry_failed=False, archive=None, fetch=None, extract=None, now=None, sleep=time.sleep,
                  budget=None, phase_http_limit=None, priority_ids=(), max_seconds=MAX_RUN_SECONDS,
                  run_clock=time.monotonic):
    if not api_key or not isinstance(model, str) or not re.fullmatch(r"gemini-[a-zA-Z0-9.-]+", model):
        raise ValueError("missing_or_invalid_gemini_configuration")
    if not 1 <= limit <= 50 or not 1 <= max_attempts <= 5 or not 0 <= pause_seconds <= 60:
        raise ValueError("invalid_processing_budget")
    if type(max_seconds) is not int or not 1 <= max_seconds <= 1800:
        raise ValueError("invalid_processing_time_limit")
    deadline = run_clock() + max_seconds
    if phase_http_limit is not None and (budget is None or type(phase_http_limit) is not int
                                       or not 0 <= phase_http_limit <= limit):
        raise ValueError("invalid_phase_budget")
    if (not isinstance(priority_ids, (list, tuple, set, frozenset)) or len(priority_ids) > 50_000
            or any(not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value) for value in priority_ids)):
        raise ValueError("invalid_priority_candidates")
    priority_ids = set(priority_ids)
    inbox = _PacedInbox(inbox, sleep)
    offline_budget = min(limit, 20)
    offline_candidates = set()
    if fetch is None:
        from .article_source import fetch_article
        fetch = fetch_article
    if extract is None:
        from .gemini_draft import extract_draft
        extract = extract_draft
    now = now or utcnow()
    references = {row["id"]: row for row in (archive or {}).get("records", [])}
    report = {"started_at": now.isoformat(), "model": model, "pending": 0, "attempted": 0, "drafts": 0,
              "scope_review": 0, "privacy_review": 0, "errors": 0, "skipped_existing": 0,
              "deferred": 0, "invalid_metadata": 0, "migrated": 0, "news_dossiers": 0,
              "partial_dossiers": 0, "privacy_reprocessed": 0, "stopped_reason": None, "publications": 0}
    if budget is not None:
        snapshot = budget.snapshot()
        # Reserve a fair share for later arrivals instead of exhausting the
        # whole free daily allowance during the first backlog batches.
        hourly_allowance, remaining_hours = hourly_request_allowance(snapshot, budget.config, limit, now)
        limit = (hourly_allowance if phase_http_limit is None else
                 min(limit, budget.config.batch_ceiling, snapshot["remaining_requests"], phase_http_limit))
        report["effective_limit"] = limit
        report["remaining_hourly_slots"] = remaining_hours
        report["budget"] = snapshot
        if snapshot["cooldown_until"] is not None:
            report.update(stopped_reason="budget_deferred", quota_reason="provider_cooldown",
                          quota_retry_at=snapshot["cooldown_until"], github_write_calls=0,
                          offline_candidates=0, http_requests=0)
            return report
        if limit == 0:
            reason = "run_budget_exhausted" if phase_http_limit == 0 and snapshot["remaining_requests"] else "daily_budget_exhausted"
            report.update(stopped_reason="budget_deferred", quota_reason=reason,
                          github_write_calls=0, offline_candidates=0, http_requests=0)
            return report
        # This is an HTTP-attempt allowance, not merely a successful-news cap:
        # retries and timeouts must consume the same hour's share.
        budget.set_request_limit(limit)
    ensure_ai_labels(inbox)
    candidates = []
    for issue in pending_issues(inbox):
        try:
            candidate = candidate_from_issue(issue)
            candidates.append((issue, candidate))
        except (ValueError, KeyError, TypeError):
            report["invalid_metadata"] += 1
    report["pending"] = len(candidates)
    # A bounded share of each batch is reserved for new RSS arrivals; historical work cannot starve them.
    new = sorted((row for row in candidates if row[1].get("discovery_method") != "historical_bibliography"), key=lambda row: row[0].get("created_at", ""))
    from .history_review import fair_scope_order
    draft_index = existing_draft_ids(inbox)
    old = list(fair_scope_order(
        (row for row in candidates if row[1].get("discovery_method") == "historical_bibliography"),
        scope=lambda row: row[1].get("scope_hint"),
        key=lambda row: (row[1].get("published_date") or "", row[1]["id"]),
        offset=len(draft_index)))
    # Give the former blanket policy a small migration share without waiting
    # behind years of untouched historical news. A v1 badge is only a hint:
    # the actual dossier is validated before any model work or budget reset.
    privacy_candidates = []
    for row in [*new, *old]:
        issue, candidate = row
        marker = COMPLETE_MARKER.search(issue.get("body") or "")
        if (candidate["id"] in draft_index
                and ("ai/privacy-review" in {label["name"] for label in issue.get("labels", [])}
                     or PRIVACY_RETRY_MARKER in (issue.get("body") or ""))
                and not (marker and marker.group(1) == "2" and marker.group(2) == draft_index[candidate["id"]])):
            privacy_candidates.append(row)
    privacy_issue_numbers = {row[0]["number"] for row in privacy_candidates}
    new = [row for row in new if row[0]["number"] not in privacy_issue_numbers]
    old = [row for row in old if row[0]["number"] not in privacy_issue_numbers]
    priority_candidates = sorted((row for row in candidates if row[1]["id"] in priority_ids),
                                 key=lambda row: (row[1].get("published_date") or "", row[1]["id"]))
    prioritized = {row[0]["number"] for row in priority_candidates}
    new = [row for row in new if row[0]["number"] not in prioritized]
    old = [row for row in old if row[0]["number"] not in prioritized]
    privacy_candidates = [row for row in privacy_candidates if row[0]["number"] not in prioritized]
    def schedule():
        yield from priority_candidates
        privacy_remainder = []
        privacy_quota = min(5, max(1, limit // 4))
        for index, item in enumerate(privacy_candidates):
            if report["attempted"] >= privacy_quota:
                privacy_remainder = privacy_candidates[index:]
                break
            yield item
        remainder = []
        new_quota = min(5, max(1, limit // 4)) if old else limit
        rss_start_attempts = report["attempted"]
        for index, item in enumerate(new):
            if report["attempted"] - rss_start_attempts >= new_quota or report["attempted"] >= limit:
                remainder = new[index:]
                break
            yield item
        yield from old
        yield from remainder
        yield from privacy_remainder
    def current_pending(issue, candidate):
        current = inbox.call("GET", inbox.base + f"/issues/{issue['number']}")
        if not is_pending(current):
            return False
        try:
            return candidate_from_issue(current) == candidate
        except (ValueError, TypeError, KeyError):
            return False

    def begin_offline_work(issue, candidate):
        """One shared slot per candidate for migration, skeletons and repairs."""
        identifier = candidate["id"]
        if identifier not in offline_candidates and len(offline_candidates) >= offline_budget:
            return False
        if not current_pending(issue, candidate):
            return False
        offline_candidates.add(identifier)
        return True

    def done_state(candidate, attempts, document, *, privacy_attempts=None):
        result = {"candidate_id": candidate["id"], "source_url": candidate["source_url"],
                "status": "completed", "attempts": attempts, "updated_at": utcnow().isoformat(),
                "model": document.get("extraction", {}).get("model", model),
                "content_sha256": document.get("extraction", {}).get("content_sha256"),
                "draft_status": document["status"], "dossier_schema_version": DOSSIER_VERSION}
        if privacy_attempts is not None:
            result.update(privacy_reprocessing_version=PRIVACY_POLICY_VERSION,
                          privacy_reprocessing_attempts=privacy_attempts)
        return result

    for issue, candidate in schedule():
        if run_clock() >= deadline:
            report["stopped_reason"] = "run_time_limit"
            break
        identifier = candidate["id"]
        marker = COMPLETE_MARKER.search(issue.get("body") or "")
        labels = {label["name"] for label in issue.get("labels", [])}
        if (marker and identifier in draft_index and marker.group(2) == draft_index[identifier]
                and {"ai/ready", "ai/scope-review", "ai/privacy-review"}.intersection(labels)
                and (marker.group(1) == "2" or "ai/privacy-review" not in labels)):
            # Existing safe v1 dossiers keep their fast path. Only old privacy
            # markers require reading once to detect the blanket-redaction policy.
            # Both marker versions bind the decision to the exact Git blob.
            report["skipped_existing"] += 1
            continue
        existing_document, draft_sha = None, None
        privacy_reprocessing = False
        if identifier in draft_index:
            existing = read_private_file(inbox, "drafts", identifier)
            if existing is None:
                raise ValueError("private_draft_index_changed")
            existing_document, draft_sha = existing
            legacy = "news_dossier" not in existing_document
            validate_envelope(existing_document, candidate, allow_legacy=legacy)
            if legacy:
                if not begin_offline_work(issue, candidate):
                    report["deferred"] += 1
                    continue
                from .news_dossier import empty_news_dossier, from_legacy_record
                existing_document = dict(existing_document)
                if isinstance(existing_document.get("record"), dict):
                    existing_document["news_dossier"] = from_legacy_record(existing_document)
                else:
                    previous_status = existing_document["status"]
                    existing_document["status"] = "pending"
                    existing_document["news_dossier"] = empty_news_dossier(candidate)
                    existing_document["extraction"] = {**existing_document.get("extraction", {}),
                        "legacy_status": previous_status, "migration": "awaiting_new_extraction"}
                    report["partial_dossiers"] += 1
                validate_envelope(existing_document, candidate)
                draft_sha = put_private_file(inbox, "drafts", identifier, existing_document, draft_sha)
                draft_index[identifier] = draft_sha
                report["migrated"] += 1
            privacy_reprocessing = needs_field_privacy_reprocessing(existing_document)
            if complete_document(existing_document) and not privacy_reprocessing:
                if not begin_offline_work(issue, candidate):
                    report["deferred"] += 1
                    continue
                previous = read_private_file(inbox, "processing", identifier)
                previous_state, previous_sha = previous if previous else ({}, None)
                if previous:
                    validate_state(previous_state, candidate)
                put_private_file(inbox, "processing", identifier,
                    done_state(candidate, previous_state.get("attempts", 0), existing_document,
                               privacy_attempts=previous_state.get("privacy_reprocessing_attempts")), previous_sha)
                update_issue(inbox, issue["number"], candidate, document=existing_document, draft_sha=draft_sha)
                report["skipped_existing"] += 1
                continue
        if report["attempted"] >= limit:
            report["deferred"] += 1
            continue
        state_file = read_private_file(inbox, "processing", identifier)
        state, state_sha = state_file if state_file else ({}, None)
        if state_file:
            validate_state(state, candidate)
        attempts = state.get("attempts", 0)
        retry_at = state.get("next_retry_at")
        privacy_reprocessing = (privacy_reprocessing
            or state.get("privacy_reprocessing_version") == PRIVACY_POLICY_VERSION
            or (existing_document or {}).get("extraction", {}).get("privacy_reprocessing_version") == PRIVACY_POLICY_VERSION)
        privacy_attempts = None
        if privacy_reprocessing:
            # The policy change gets one separate budget, without erasing prior
            # attempts. Persist it on both success and failure so reruns cannot
            # reopen a fresh budget or bypass the recorded retry timestamp.
            privacy_attempts = state.get("privacy_reprocessing_attempts", 0)
        elif state.get("status") == "completed" and state.get("dossier_schema_version") != DOSSIER_VERSION:
            # A legacy triage completion did not extract the news fields. Give
            # the new prompt its own bounded attempt budget once, preserving Git history.
            attempts, retry_at = 0, None
        if state_file and state["status"] == "error" and existing_document is None:
            if not begin_offline_work(issue, candidate):
                report["deferred"] += 1
                continue
            status = "source_unavailable" if state.get("error_code", "").startswith("source_") else "error"
            existing_document = skeleton_envelope(candidate, status, error_code=state.get("error_code"), model=model)
            draft_sha = put_private_file(inbox, "drafts", identifier, existing_document)
            draft_index[identifier] = draft_sha
            update_issue(inbox, issue["number"], candidate, document=existing_document, error_code=state.get("error_code"))
            report["partial_dossiers"] += 1
        budget_attempts = privacy_attempts if privacy_reprocessing else attempts
        if not retry_failed and (budget_attempts >= max_attempts or (retry_at and datetime.fromisoformat(retry_at) > now)):
            report["deferred"] += 1
            continue
        if not current_pending(issue, candidate):
            report["deferred"] += 1
            continue
        if report["attempted"]:
            sleep(pause_seconds)
        report["attempted"] += 1
        reference = references.get(identifier)
        metadata = dict(candidate)
        if reference and reference.get("source_url") == candidate["source_url"]:
            metadata["discovery"] = reference.get("discovery")
        stage = "source"
        try:
            # Each destination uses its own client: GitHub auth never reaches a publisher or Google.
            article = fetch(metadata)
            stage = "model"
            kwargs = {"api_key": api_key, "model": model}
            if budget is not None:
                kwargs["budget"] = budget
            document = extract(candidate, article, **kwargs)
            validate_envelope(document, candidate)
            if not complete_document(document):
                raise ValueError("incomplete_news_dossier")
            if privacy_reprocessing and document.get("extraction", {}).get("privacy_policy_version") != PRIVACY_POLICY_VERSION:
                raise ValueError("outdated_privacy_policy")
        except Exception as error:
            if budget is not None:
                from .gemini_budget import QuotaDeferred, QuotaStateError
                if isinstance(error, QuotaDeferred):
                    # Exhausted scheduling capacity is not a failed news item.
                    # Retain any former draft and its candidate retry state.
                    report["attempted"] -= 1
                    report["deferred"] += 1
                    report["stopped_reason"] = "budget_deferred"
                    report["quota_reason"] = error.reason
                    report["quota_retry_at"] = error.retry_at.isoformat()
                    break
                if isinstance(error, QuotaStateError):
                    # A missing/corrupt/racing budget must never be downgraded
                    # to an ordinary candidate failure or another model attempt.
                    raise
            code, stop = error_category(error)
            if not current_pending(issue, candidate):
                report["deferred"] += 1
                continue
            document = skeleton_envelope(candidate, "source_unavailable" if stage == "source" else "error",
                                         error_code=code, model=model)
            if privacy_reprocessing:
                document["extraction"]["privacy_reprocessing_version"] = PRIVACY_POLICY_VERSION
            next_retry = now + timedelta(hours=6 if stop else 1)
            failed = {"candidate_id": identifier, "source_url": candidate["source_url"], "status": "error",
                      "attempts": attempts if stop else attempts + 1, "error_code": code,
                      "next_retry_at": next_retry.isoformat(), "updated_at": utcnow().isoformat(), "model": model,
                      "dossier_schema_version": DOSSIER_VERSION}
            if privacy_reprocessing:
                failed.update(privacy_reprocessing_version=PRIVACY_POLICY_VERSION,
                              privacy_reprocessing_attempts=privacy_attempts if stop else privacy_attempts + 1)
            if code == "invalid_extraction_schema":
                failed["validation_issues"] = safe_validation_issues(error)
            # Persist cooldown/budget before replacing the old blanket draft.
            # A failed subsequent draft write must not reopen a fresh budget.
            put_private_file(inbox, "processing", identifier, failed, state_sha)
            draft_sha = put_private_file(inbox, "drafts", identifier, document, draft_sha)
            draft_index[identifier] = draft_sha
            update_issue(inbox, issue["number"], candidate, document=document, error_code=code)
            report["partial_dossiers"] += 1
            report["errors"] += 1
            if stop:
                report["stopped_reason"] = code
                break
            continue
        # Persist before decorating the issue: a retry after a GitHub interruption will not call Gemini again.
        if not current_pending(issue, candidate):
            report["deferred"] += 1
            continue
        provenance = {}
        code_ref = os.environ.get("MDH_CODE_REF", "")
        run_id = os.environ.get("GITHUB_RUN_ID", "")
        if re.fullmatch(r"[a-f0-9]{40}", code_ref):
            provenance["code_ref"] = code_ref
        if re.fullmatch(r"[0-9]{1,20}", run_id):
            provenance["run_id"] = run_id
        document.setdefault("extraction", {}).update(provenance)
        draft_sha = put_private_file(inbox, "drafts", identifier, document, draft_sha)
        draft_index[identifier] = draft_sha
        done = done_state(candidate, attempts + 1, document,
                          privacy_attempts=privacy_attempts + 1 if privacy_reprocessing else None)
        put_private_file(inbox, "processing", identifier, done, state_sha)
        update_issue(inbox, issue["number"], candidate, document=document, draft_sha=draft_sha)
        report["news_dossiers"] += 1
        if privacy_reprocessing:
            report["privacy_reprocessed"] += 1
        report[{"draft": "drafts", "needs_scope_review": "scope_review", "needs_privacy_review": "privacy_review"}[document["status"]]] += 1
    report["github_write_calls"] = inbox.write_calls
    report["offline_candidates"] = len(offline_candidates)
    if budget is not None:
        report["budget"] = budget.snapshot()
        report["http_requests"] = budget.requests_this_run
    return report


def editorial_excluded_ids(inbox, *, imported_ids=()):
    """Only explicit human decisions exclude unqueued archive candidates."""
    excluded = set()
    imported_ids = set(imported_ids)
    for label in ("review/approved", "review/rejected"):
        for page in range(1, 201):
            rows = inbox.call("GET", inbox.base + "/issues", params={
                "state": "all", "labels": label, "per_page": 100, "page": page})
            if not isinstance(rows, list):
                raise ValueError("invalid_editorial_decisions")
            for row in rows:
                if "pull_request" in row:
                    continue
                match = re.search(r"<!-- mdh-candidate:([a-f0-9]{64}) -->", row.get("body") or "")
                if match:
                    identifier = match.group(1)
                    if label == "review/rejected" or identifier not in imported_ids:
                        excluded.add(identifier)
            if len(rows) < 100:
                break
        else:
            raise ValueError("incomplete_editorial_decisions")
    return excluded


def _load_phase_documents(inbox_dir, folder, *, maximum_bytes=512_000):
    if inbox_dir.is_symlink() or not inbox_dir.is_dir():
        raise ValueError("invalid_private_checkout")
    directory = inbox_dir / folder
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        raise ValueError("invalid_phase_cache")
    paths = sorted(directory.glob("*.json"))
    if len(paths) > 50_000:
        raise ValueError("oversized_phase_cache")
    values, total = [], 0
    for path in paths:
        if path.is_symlink() or not path.is_file() or not re.fullmatch(r"[a-f0-9]{64}\.json", path.name):
            raise ValueError("invalid_phase_cache_file")
        size = path.stat().st_size
        total += size
        if not 0 < size <= maximum_bytes or total > 256 * 1024 * 1024:
            raise ValueError("oversized_phase_cache_file")
        value = json.loads(path.read_text())
        if not isinstance(value, dict) or value.get("candidate_id") != path.stem:
            raise ValueError("invalid_phase_cache_identity")
        values.append(value)
    return values


def process_scope_first(inbox, *, api_key, model, config, budget, archive, inbox_dir,
                        limit, retry_failed=False, now=None, scope_worker=None, budget_factory=None):
    """Share one hourly grant between bulk classification and complete dossiers."""
    from .gemini_budget import GeminiBudget, QuotaStateError
    from .scope_review_queue import recheck_scope
    from .history_review import enqueue_history
    now = now or utcnow()
    snapshot = budget.snapshot()
    allowance, _ = hourly_request_allowance(snapshot, config, limit, now)
    scope_limit = min(8, allowance - 1, math.ceil(allowance * 0.7)) if allowance >= 2 else allowance
    scope_worker = scope_worker or recheck_scope
    budget_factory = budget_factory or GeminiBudget
    scope_report = {"http_requests": 0, "classified": 0, "accepted": 0, "stopped_reason": None}
    selected = []
    excluded = set()
    if scope_limit and snapshot["cooldown_until"] is None:
        if archive is None:
            raise ValueError("scope_archive_required")
        drafts = _load_phase_documents(inbox_dir, "drafts")
        cached = _load_phase_documents(inbox_dir, "scope-reviews", maximum_bytes=30_000)
        attempts = _load_phase_documents(inbox_dir, "scope-attempts", maximum_bytes=30_000)
        excluded = editorial_excluded_ids(inbox)
        budget.set_request_limit(scope_limit)
        try:
            scope_report = scope_worker(inbox, drafts, archive, api_key=api_key, model=model,
                budget=budget, max_requests=scope_limit, max_seconds=480,
                scope_reviews=cached, attempts=attempts, excluded_ids=excluded)
            selected = scope_report.get("selected_ids", [])
        except QuotaStateError:
            raise  # Uncertain accounting must fail closed for both stages.
        except Exception as error:
            scope_report = {"http_requests": budget.requests_this_run, "stopped_reason": "scope_failed",
                            "error_type": type(error).__name__}
            if isinstance(error, GitHubRequestError):
                scope_report["error_code"] = error.code
        if (not isinstance(selected, list) or len(selected) > 50_000
                or any(not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value) for value in selected)):
            raise ValueError("invalid_scope_selection_ids")
        selected = sorted(set(selected) - excluded)
    scope_used = budget.requests_this_run
    if type(scope_used) is not int or not 0 <= scope_used <= scope_limit:
        raise QuotaStateError("scope_phase_limit_exceeded")
    # Source failures release unused scope capacity. Failed/retried HTTP
    # attempts remain consumed and can never be released to the next stage.
    dossier_limit = allowance - scope_used
    prioritized = 0
    enqueue_error = None
    full_budget = budget_factory(inbox, config)
    full_snapshot = full_budget.snapshot()
    if selected and dossier_limit and full_snapshot["cooldown_until"] is None:
        references = {row["id"]: row for row in archive["records"]}
        if any(identifier not in references for identifier in selected):
            raise ValueError("unknown_scope_selection_id")
        try:
            queued = enqueue_history(_PacedInbox(inbox, time.sleep),
                {"schema_version": 1, "records": [references[identifier] for identifier in selected]},
                limit=min(dossier_limit, 100), include_international=True)
            prioritized = queued["added"]
        except GitHubRequestError as error:
            enqueue_error = error.code  # Existing eligible queue items can still progress.
    report = process_queue(inbox, api_key=api_key, model=model,
        limit=max(1, dossier_limit), phase_http_limit=dossier_limit,
        retry_failed=retry_failed, archive=archive, budget=full_budget,
        priority_ids=selected, now=now)
    full_used = full_budget.requests_this_run
    if type(full_used) is not int or not 0 <= full_used <= dossier_limit:
        raise QuotaStateError("dossier_phase_limit_exceeded")
    safe_scope_keys = {"candidates", "cached", "sources_checked", "source_unavailable", "source_changed",
                       "reviewed", "relevant_sv", "classified", "accepted", "http_requests", "stopped_reason",
                       "quota_reason", "error_code", "error_type", "deferred", "excluded", "attempts_saved",
                       "classified_total", "accepted_total", "remaining", "retry_deferred"}
    report["scope_phase"] = {key: value for key, value in scope_report.items() if key in safe_scope_keys}
    report["scope_phase"]["http_requests"] = scope_used
    report.update(hourly_request_limit=allowance, scope_request_limit=scope_limit,
                  dossier_request_limit=dossier_limit, dossier_http_requests=full_used,
                  http_requests=scope_used + full_used, prioritized_candidates=prioritized)
    if enqueue_error:
        report["priority_enqueue_error"] = enqueue_error
    return report


def failed_report(report):
    return (report["stopped_reason"] not in (None, "budget_deferred", "run_time_limit")
            or report.get("scope_phase", {}).get("stopped_reason") in {"scope_failed", "model_error"}
            or bool(report["attempted"] and report["errors"] == report["attempted"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--scope-first", action="store_true", help="Classify raw candidates before generating complete dossiers")
    parser.add_argument("--inbox-dir", type=Path, help="Current private checkout containing draft and scope caches")
    args = parser.parse_args()
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise SystemExit("GEMINI_API_KEY secret is not configured; no extraction attempted.")
    claim = None
    slots = None
    try:
        from .gemini_budget import BudgetConfig, GeminiBudget, QuotaStateError
        from .hourly_slot import HourlySlots
        raw_config = os.environ.get("MDH_GEMINI_BUDGET_CONFIG")
        if not raw_config:
            raise QuotaStateError("gemini_budget_configuration_required")
        config = BudgetConfig(**json.loads(raw_config))
        model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
        if model != config.model:
            raise QuotaStateError("gemini_budget_model_mismatch")
        if not 1 <= args.limit <= 50:
            raise QuotaStateError("invalid_requested_batch_size")
        inbox = GitHubInbox(os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"])
        budget = GeminiBudget(inbox, config)
        budget.snapshot()  # Missing budget state fails closed before claiming work.
        slots = HourlySlots(inbox)
        source = ("github_schedule" if os.environ.get("GITHUB_EVENT_NAME") == "schedule"
                  else os.environ.get("MDH_TRIGGER_SOURCE") or "manual")
        claim = slots.claim(run_id=os.environ.get("GITHUB_RUN_ID", ""), trigger_source=source,
                            requested_hour=os.environ.get("MDH_SCHEDULED_HOUR") or None)
        if claim is None:
            print(json.dumps({"status": "hour_already_claimed", "attempted": 0, "publications": 0}))
            return
        archive = json.loads(args.archive.read_text()) if args.archive else None
        if args.scope_first:
            if args.inbox_dir is None:
                raise ValueError("scope_private_checkout_required")
            report = process_scope_first(inbox, api_key=key, model=model, config=config, budget=budget,
                archive=archive, inbox_dir=args.inbox_dir, limit=min(args.limit, config.batch_ceiling),
                retry_failed=args.retry_failed)
        else:
            report = process_queue(inbox, api_key=key, model=model, budget=budget,
                                   limit=min(args.limit, config.batch_ceiling), retry_failed=args.retry_failed, archive=archive)
        status = ("failed" if failed_report(report) else
                  "deferred" if report["stopped_reason"] in {"budget_deferred", "run_time_limit"} else "completed")
        slots.finish(claim, status=status,
                     counts={name: report[name] for name in ("attempted", "news_dossiers", "errors", "migrated", "http_requests")})
        report["hourly_slot"] = claim["hour"]
    except Exception as error:
        if claim is not None and slots is not None:
            try:
                slots.finish(claim, status="failed")
            except Exception:
                pass  # An unfinished claim still prevents duplicate work this hour.
        # Do not print traceback chains from private metadata or third-party libraries.
        failure = {"status": "failed", "error_type": type(error).__name__, "publications": 0}
        if not os.environ.get("MDH_GEMINI_BUDGET_CONFIG"):
            failure["error_code"] = "gemini_budget_configuration_required"
        elif type(error).__name__ in {"QuotaStateError", "HourlySlotError"} and re.fullmatch(r"[a-z][a-z0-9_]{0,80}", str(error)):
            failure["error_code"] = str(error)
        elif isinstance(error, GitHubRequestError):
            failure["error_code"] = error.code
        print(json.dumps(failure))
        raise SystemExit(1) from None
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    print(serialized)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
            stream.write("## Extracción privada con Gemini\n\n```json\n" + serialized + "\n```\n\nNo se publicaron fichas. Revise Issues y drafts/ en este repositorio privado.\n")
    if failed_report(report):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
