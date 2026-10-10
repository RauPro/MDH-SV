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
             "ai/privacy-review": "9333EA", "ai/error": "B91C1C"}
MAX_FILE_BYTES = 180_000


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
    identifiers = set()
    for entry in tree["tree"]:
        match = re.fullmatch(r"drafts/([a-f0-9]{64})\.json", entry.get("path", ""))
        if match and entry.get("type") == "blob":
            identifiers.add(match.group(1))
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
    inbox.call("PUT", inbox.base + f"/contents/{folder}/{identifier}.json", json=payload)


def validate_envelope(document, candidate):
    if (not isinstance(document, dict) or type(document.get("schema_version")) is not int or document.get("schema_version") != 1 or document.get("candidate_id") != candidate["id"]
            or document.get("source_url") != candidate["source_url"] or document.get("publishable") is not False
            or document.get("human_reviewed") is not False
            or document.get("status") not in ("draft", "needs_scope_review", "needs_privacy_review")):
        raise ValueError("invalid_draft_envelope")
    record = document.get("record")
    if document["status"] == "draft":
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


def ensure_ai_labels(inbox):
    existing = {label["name"] for label in inbox.call("GET", inbox.base + "/labels", params={"per_page": 100})}
    for name, color in AI_LABELS.items():
        if name not in existing:
            inbox.call("POST", inbox.base + "/labels", json={"name": name, "color": color})


def update_issue(inbox, issue_number, candidate, document=None, error_code=None):
    # A human may have changed the decision while Gemini was running. Never undo it.
    issue = inbox.call("GET", inbox.base + f"/issues/{issue_number}")
    if not is_pending(issue):
        return False
    if candidate_from_issue(issue)["id"] != candidate["id"]:
        raise ValueError("changed_candidate_identity")
    body = re.sub(r"\n<!-- mdh-ai:start -->.*?<!-- mdh-ai:end -->\n?", "", issue.get("body") or "", flags=re.S)
    if document:
        statuses = {"draft": ("ai/ready", "Ficha generada por Gemini; revisión editorial pendiente."),
                    "needs_scope_review": ("ai/scope-review", "La pertinencia o jurisdicción necesita revisión; no se ha creado una ficha de caso."),
                    "needs_privacy_review": ("ai/privacy-review", "Se requiere revisión de privacidad antes de preparar una ficha utilizable.")}
        label, notice = statuses[document["status"]]
        detail = f"[Abrir borrador privado](https://github.com/{inbox.repository}/blob/main/drafts/{candidate['id']}.json)"
        if document.get("extraction", {}).get("truncated"):
            notice += " La entrada fue recortada por tamaño: revise el artículo completo."
    else:
        label, notice = "ai/error", "La extracción no se completó. No se inventó una ficha ni se publicó contenido."
        detail = f"Código de diagnóstico: `{error_code}`. Consulte el estado privado de procesamiento."
    section = ("\n<!-- mdh-ai:start -->\n## Extracción asistida\n\n" + notice + "\n\n" + detail
               + "\n\nEl borrador no acredita los hechos. Revise fuente, citas, personas y fechas antes de aprobar. "
               "Actions → **Revisar noticia** permite usar este borrador dejando `record_json` vacío y añadiendo una nota editorial.\n<!-- mdh-ai:end -->\n")
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
              "deferred": 0, "invalid_metadata": 0, "stopped_reason": None, "publications": 0}
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
    old = sorted((row for row in candidates if row[1].get("discovery_method") == "historical_bibliography"), key=lambda row: (row[1].get("scope_hint") != "el_salvador", row[1].get("published_date") or "", row[1]["id"]))
    def schedule():
        remainder = []
        for index, item in enumerate(new):
            if report["attempted"] >= min(5, limit):
                remainder = new[index:]
                break
            yield item
        yield from old
        yield from remainder
    completed_ids = existing_draft_ids(inbox)
    for issue, candidate in schedule():
        identifier = candidate["id"]
        if identifier in completed_ids:
            expected_badges = {"ai/ready", "ai/scope-review", "ai/privacy-review"}
            if (not expected_badges.intersection(label["name"] for label in issue.get("labels", []))
                    or "<!-- mdh-ai:start -->" not in (issue.get("body") or "")):
                existing = read_private_file(inbox, "drafts", identifier)
                if existing is None:
                    raise ValueError("private_draft_index_changed")
                validate_envelope(existing[0], candidate)
                update_issue(inbox, issue["number"], candidate, document=existing[0])
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
        if not retry_failed and (attempts >= max_attempts or (retry_at and datetime.fromisoformat(retry_at) > now)):
            report["deferred"] += 1
            continue
        current = inbox.call("GET", inbox.base + f"/issues/{issue['number']}")
        if not is_pending(current):
            report["deferred"] += 1
            continue
        current_candidate = candidate_from_issue(current)
        if current_candidate != candidate:
            report["deferred"] += 1
            continue
        if report["attempted"]:
            sleep(pause_seconds)
        report["attempted"] += 1
        reference = references.get(identifier)
        metadata = dict(candidate)
        if reference and reference.get("source_url") == candidate["source_url"]:
            metadata["discovery"] = reference.get("discovery")
        try:
            # Each destination uses its own client: GitHub auth never reaches a publisher or Google.
            article = fetch(metadata)
            document = extract(candidate, article, api_key=api_key, model=model)
            validate_envelope(document, candidate)
        except Exception as error:
            code, stop = error_category(error)
            next_retry = now + timedelta(hours=6 if stop else 1)
            failed = {"candidate_id": identifier, "source_url": candidate["source_url"], "status": "error",
                      "attempts": attempts if stop else attempts + 1, "error_code": code,
                      "next_retry_at": next_retry.isoformat(), "updated_at": utcnow().isoformat(), "model": model}
            if code == "invalid_extraction_schema":
                failed["validation_issues"] = safe_validation_issues(error)
            put_private_file(inbox, "processing", identifier, failed, state_sha)
            update_issue(inbox, issue["number"], candidate, error_code=code)
            report["errors"] += 1
            if stop:
                report["stopped_reason"] = code
                break
            continue
        # Persist before decorating the issue: a retry after a GitHub interruption will not call Gemini again.
        provenance = {}
        code_ref = os.environ.get("MDH_CODE_REF", "")
        run_id = os.environ.get("GITHUB_RUN_ID", "")
        if re.fullmatch(r"[a-f0-9]{40}", code_ref):
            provenance["code_ref"] = code_ref
        if re.fullmatch(r"[0-9]{1,20}", run_id):
            provenance["run_id"] = run_id
        document.setdefault("extraction", {}).update(provenance)
        put_private_file(inbox, "drafts", identifier, document)
        completed_ids.add(identifier)
        done = {"candidate_id": identifier, "source_url": candidate["source_url"], "status": "completed",
                "attempts": attempts + 1, "updated_at": utcnow().isoformat(), "model": model,
                "content_sha256": article["content_sha256"], "draft_status": document["status"]}
        put_private_file(inbox, "processing", identifier, done, state_sha)
        update_issue(inbox, issue["number"], candidate, document=document)
        report[{"draft": "drafts", "needs_scope_review": "scope_review", "needs_privacy_review": "privacy_review"}[document["status"]]] += 1
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
