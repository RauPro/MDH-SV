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
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .review_queue import GitHubInbox, canonical_url

AI_LABELS = {"ai/ready": "16815D", "ai/scope-review": "D97706",
             "ai/privacy-review": "9333EA", "ai/error": "B91C1C", "ai/pending": "D97706"}
MAX_FILE_BYTES = 180_000
DOSSIER_VERSION = 1
COMPLETE_MARKER = re.compile(r"<!-- mdh-news-dossier:v1:([a-f0-9]{40,64}) -->")
MIN_WRITE_INTERVAL = 2.0


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
            detail += f"\n<!-- mdh-news-dossier:v1:{draft_sha} -->"
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

def process_queue(inbox, *, api_key, model, limit=20, max_attempts=3, pause_seconds=4,
                  retry_failed=False, archive=None, fetch=None, extract=None, now=None, sleep=time.sleep):
    if not api_key or not isinstance(model, str) or not re.fullmatch(r"gemini-[a-zA-Z0-9.-]+", model):
        raise ValueError("missing_or_invalid_gemini_configuration")
    if not 1 <= limit <= 50 or not 1 <= max_attempts <= 5 or not 0 <= pause_seconds <= 60:
        raise ValueError("invalid_processing_budget")
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
              "partial_dossiers": 0, "stopped_reason": None, "publications": 0}
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
    def schedule():
        remainder = []
        new_quota = min(5, max(1, limit // 4)) if old else limit
        for index, item in enumerate(new):
            if report["attempted"] >= new_quota:
                remainder = new[index:]
                break
            yield item
        yield from old
        yield from remainder
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

    def done_state(candidate, attempts, document):
        return {"candidate_id": candidate["id"], "source_url": candidate["source_url"],
                "status": "completed", "attempts": attempts, "updated_at": utcnow().isoformat(),
                "model": document.get("extraction", {}).get("model", model),
                "content_sha256": document.get("extraction", {}).get("content_sha256"),
                "draft_status": document["status"], "dossier_schema_version": DOSSIER_VERSION}

    for issue, candidate in schedule():
        identifier = candidate["id"]
        marker = COMPLETE_MARKER.search(issue.get("body") or "")
        if (marker and identifier in draft_index and marker.group(1) == draft_index[identifier]
                and {"ai/ready", "ai/scope-review", "ai/privacy-review"}.intersection(label["name"] for label in issue.get("labels", []))):
            # This marker is written only after validating a processed v1 news
            # dossier, and is tied to its exact Git blob. Old ai/* badges do not qualify.
            report["skipped_existing"] += 1
            continue
        existing_document, draft_sha = None, None
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
            if complete_document(existing_document):
                if not begin_offline_work(issue, candidate):
                    report["deferred"] += 1
                    continue
                previous = read_private_file(inbox, "processing", identifier)
                previous_state, previous_sha = previous if previous else ({}, None)
                if previous:
                    validate_state(previous_state, candidate)
                put_private_file(inbox, "processing", identifier,
                    done_state(candidate, previous_state.get("attempts", 0), existing_document), previous_sha)
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
        if state.get("status") == "completed" and state.get("dossier_schema_version") != DOSSIER_VERSION:
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
        if not retry_failed and (attempts >= max_attempts or (retry_at and datetime.fromisoformat(retry_at) > now)):
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
            document = extract(candidate, article, api_key=api_key, model=model)
            validate_envelope(document, candidate)
            if not complete_document(document):
                raise ValueError("incomplete_news_dossier")
        except Exception as error:
            code, stop = error_category(error)
            if not current_pending(issue, candidate):
                report["deferred"] += 1
                continue
            document = skeleton_envelope(candidate, "source_unavailable" if stage == "source" else "error",
                                         error_code=code, model=model)
            draft_sha = put_private_file(inbox, "drafts", identifier, document, draft_sha)
            draft_index[identifier] = draft_sha
            next_retry = now + timedelta(hours=6 if stop else 1)
            failed = {"candidate_id": identifier, "source_url": candidate["source_url"], "status": "error",
                      "attempts": attempts if stop else attempts + 1, "error_code": code,
                      "next_retry_at": next_retry.isoformat(), "updated_at": utcnow().isoformat(), "model": model,
                      "dossier_schema_version": DOSSIER_VERSION}
            if code == "invalid_extraction_schema":
                failed["validation_issues"] = safe_validation_issues(error)
            put_private_file(inbox, "processing", identifier, failed, state_sha)
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
        done = done_state(candidate, attempts + 1, document)
        put_private_file(inbox, "processing", identifier, done, state_sha)
        update_issue(inbox, issue["number"], candidate, document=document, draft_sha=draft_sha)
        report["news_dossiers"] += 1
        report[{"draft": "drafts", "needs_scope_review": "scope_review", "needs_privacy_review": "privacy_review"}[document["status"]]] += 1
    report["github_write_calls"] = inbox.write_calls
    report["offline_candidates"] = len(offline_candidates)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args()
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise SystemExit("GEMINI_API_KEY secret is not configured; no extraction attempted.")
    try:
        inbox = GitHubInbox(os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"])
        archive = json.loads(args.archive.read_text()) if args.archive else None
        report = process_queue(inbox, api_key=key, model=os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite"),
                               limit=args.limit, retry_failed=args.retry_failed, archive=archive)
    except Exception as error:
        # Do not print traceback chains from private metadata or third-party libraries.
        print(json.dumps({"status": "failed", "error_type": type(error).__name__, "publications": 0}))
        raise SystemExit(1) from None
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    print(serialized)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
            stream.write("## Extracción privada con Gemini\n\n```json\n" + serialized + "\n```\n\nNo se publicaron fichas. Revise Issues y drafts/ en este repositorio privado.\n")
    if report["stopped_reason"] or (report["attempted"] and report["errors"] == report["attempted"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
