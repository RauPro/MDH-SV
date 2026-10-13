"""Private, immutable review inbox for official statistics; never publishes data."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

from .review_queue import GitHubInbox, GitHubRequestError

ROOT = "official-statistics"
MAX_BYTES = 2_000_000
HEX = re.compile(r"[a-f0-9]{64}\Z")
ID = re.compile(r"[a-z][a-z0-9_-]{0,99}\Z")
HOSTS = {"transparencia.oj.gob.sv", "transparencia.pnc.gob.sv", "fiscalia.gob.sv", "www.fiscalia.gob.sv", "data.unodc.org"}
LABELS = {"statistics/source-update": "1D76DB", "statistics/pending": "D97706",
          "statistics/approved": "16815D", "statistics/rejected": "6B7280"}
REPORT_KEYS = {"schema_version", "checked_at", "as_of", "baseline_sha256", "status",
               "review_required", "proposal_file", "proposal_sha256", "sources", "changes", "errors"}
SOURCE_KEYS = {"source_id", "url", "format", "status", "previous_sha256", "content_sha256",
               "error_code", "manual_review"}
CHANGE_KEYS = {"id", "kind", "source_id", "source_url", "content_sha256", "details"}


def _instant(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or len(value) > 40:
            raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        raise ValueError("invalid_statistics_timestamp") from None


def _hash(value):
    return isinstance(value, str) and HEX.fullmatch(value) is not None


def _identifier(value):
    return isinstance(value, str) and ID.fullmatch(value) is not None


def _url(value):
    try:
        parts = urlsplit(value)
        return (isinstance(value, str) and len(value) <= 4096 and not any(ord(c) < 33 or c in '<>\\`' for c in value)
                and parts.scheme == "https" and parts.hostname in HOSTS and not parts.username
                and not parts.password and not parts.port and parts.path not in ("", "/")
                and not parts.query and not parts.fragment)
    except (TypeError, ValueError, AttributeError):
        return False


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate_statistics_key")
            result[key] = value
        return result
    def invalid(_):
        raise ValueError("invalid_statistics_number")
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BYTES:
        raise ValueError("invalid_statistics_file_size")
    return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid)


def _bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def validate_report(report):
    """Reject malformed reports before any private repository mutation."""
    try:
        if (not isinstance(report, dict) or set(report) != REPORT_KEYS
                or type(report["schema_version"]) is not int or report["schema_version"] != 1
                or not _hash(report["baseline_sha256"]) or report["status"] not in {"unchanged", "changed", "error"}
                or type(report["review_required"]) is not bool):
            raise ValueError()
        _instant(report["checked_at"])
        if date.fromisoformat(report["as_of"]).isoformat() != report["as_of"]:
            raise ValueError()
        if not isinstance(report["sources"], list) or not 1 <= len(report["sources"]) <= 300:
            raise ValueError()
        seen = set()
        for source in report["sources"]:
            if (not isinstance(source, dict) or set(source) != SOURCE_KEYS or not _identifier(source["source_id"])
                    or source["source_id"] in seen or not _url(source["url"])
                    or source["format"] not in {"pdf", "html", "xlsx"}
                    or source["status"] not in {"unchanged", "changed", "error"}
                    or type(source["manual_review"]) is not bool
                    or any(source[key] is not None and not _hash(source[key]) for key in ("previous_sha256", "content_sha256"))
                    or source["error_code"] is not None and not _identifier(source["error_code"])):
                raise ValueError()
            if ((source["status"] == "error") != (source["error_code"] is not None)
                    or source["status"] != "error" and source["content_sha256"] is None):
                raise ValueError()
            seen.add(source["source_id"])
        if (not isinstance(report["changes"], list) or len(report["changes"]) > 1000
                or not isinstance(report["errors"], list) or len(report["errors"]) > 300):
            raise ValueError()
        changed_ids = set()
        for change in report["changes"]:
            if (not isinstance(change, dict) or set(change) != CHANGE_KEYS or not _hash(change["id"])
                    or change["id"] in changed_ids or change["kind"] not in {"fgr_values", "coverage_period", "source_revision", "catalog_document"}
                    or change["source_id"] not in seen or not _url(change["source_url"])
                    or not _hash(change["content_sha256"]) or not isinstance(change["details"], dict)
                    or len(_bytes(change["details"])) > 100_000):
                raise ValueError()
            changed_ids.add(change["id"])
        for error in report["errors"]:
            if (not isinstance(error, dict) or set(error) != {"source_id", "code"}
                    or error["source_id"] not in seen or not _identifier(error["code"])):
                raise ValueError()
        errors = {(entry["source_id"], entry["code"]) for entry in report["errors"]}
        if len(errors) != len(report["errors"]) or errors != {
                (entry["source_id"], entry["error_code"]) for entry in report["sources"] if entry["status"] == "error"}:
            raise ValueError()
        needs_review = bool(report["changes"] or report["errors"])
        expected_status = "error" if report["errors"] else "changed" if report["changes"] else "unchanged"
        if report["review_required"] != needs_review or report["status"] != expected_status:
            raise ValueError()
        if report["proposal_file"] is None:
            if report["proposal_sha256"] is not None:
                raise ValueError()
        elif (report["proposal_file"] != "proposed-homicides.json" or not _hash(report["proposal_sha256"])
              or not any(change["kind"] in {"fgr_values", "coverage_period"} for change in report["changes"])):
            raise ValueError()
        if len(_bytes(report)) > MAX_BYTES:
            raise ValueError()
        return report
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError("invalid_official_statistics_report") from None


def revision_id(report):
    validate_report(report)
    stable = {"baseline_sha256": report["baseline_sha256"],
              "changes": sorted(change["id"] for change in report["changes"]),
              "errors": sorted((entry["source_id"], entry["code"]) for entry in report["errors"])}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_proposal(raw, report):
    if report["proposal_file"] is None:
        if raw is not None:
            raise ValueError("unexpected_statistics_proposal")
        return
    document = _json(raw)
    if (hashlib.sha256(raw).hexdigest() != report["proposal_sha256"] or not isinstance(document, dict)
            or type(document.get("schema_version")) is not int or document["schema_version"] != 1
            or document.get("dataset_id") != "sv-official-homicides"
            or not isinstance(document.get("periods"), list) or not isinstance(document.get("sources"), list)):
        raise ValueError("statistics_proposal_identity_mismatch")


def validate_source_state(state):
    if (not isinstance(state, dict) or set(state) != {"schema_version", "catalogs"}
            or type(state["schema_version"]) is not int or state["schema_version"] != 1
            or not isinstance(state["catalogs"], dict)
            or set(state["catalogs"]) - {"iml-homicides", "pnc-reports", "fgr-memorias"}):
        raise ValueError("invalid_statistics_source_state")
    for catalog, urls in state["catalogs"].items():
        hosts = ({"transparencia.oj.gob.sv"} if catalog == "iml-homicides"
                 else {"transparencia.pnc.gob.sv"} if catalog == "pnc-reports"
                 else {"www.fiscalia.gob.sv", "fiscalia.gob.sv"})
        if (not isinstance(urls, list) or len(urls) > 1000 or any(not _url(url)
                or urlsplit(url).hostname not in hosts
                or catalog == "iml-homicides" and not urlsplit(url).path.startswith("/es/descargar/")
                or catalog == "fgr-memorias" and not re.fullmatch(
                    r"/(?:informe|memoria)-de-labores-20\d{2}-20\d{2}/?", urlsplit(url).path)
                for url in urls)
                or len(urls) != len(set(urls))):
            raise ValueError("invalid_statistics_source_state")
    return state


def _read(inbox, relative):
    response = inbox.get_file(inbox.base + "/contents/" + relative)
    if response is None:
        return None, None
    try:
        if (response.get("type") != "file" or response.get("path") != relative
                or response.get("encoding") != "base64" or type(response.get("size")) is not int
                or not 0 < response["size"] <= MAX_BYTES or not isinstance(response.get("content"), str)
                or len(response["content"]) > MAX_BYTES * 2
                or not re.fullmatch(r"[a-f0-9]{40,64}", response.get("sha", ""))):
            raise ValueError()
        raw = base64.b64decode("".join(response["content"].splitlines()), validate=True)
        if len(raw) != response["size"]:
            raise ValueError()
        _json(raw)
        return raw, response["sha"]
    except (ValueError, TypeError, AttributeError):
        raise ValueError("invalid_statistics_storage_file") from None


def _write(inbox, relative, raw, *, immutable=False):
    _json(raw)
    current, sha = _read(inbox, relative)
    if current == raw:
        return False
    if current is not None and immutable:
        raise ValueError("immutable_statistics_revision_conflict")
    payload = {"message": "Store official statistics review state", "branch": "main",
               "content": base64.b64encode(raw).decode()}
    if sha:
        payload["sha"] = sha
    receipt = inbox.call("PUT", inbox.base + "/contents/" + relative, json=payload)
    if not isinstance(receipt, dict) or not re.fullmatch(r"[a-f0-9]{40,64}", receipt.get("content", {}).get("sha", "")):
        raise ValueError("statistics_write_receipt_invalid")
    return True


def _labels(inbox):
    existing = set()
    for page in range(1, 11):
        entries = inbox.call("GET", inbox.base + "/labels", params={"per_page": 100, "page": page})
        existing.update(item["name"] for item in entries)
        if len(entries) < 100:
            break
    else:
        raise ValueError("statistics_labels_pagination_limit")
    for name, color in LABELS.items():
        if name not in existing:
            inbox.call("POST", inbox.base + "/labels", json={"name": name, "color": color})


def _issue(inbox, revision):
    marker = f"<!-- mdh-official-statistics:{revision} -->"
    reference, _ = _read(inbox, f"{ROOT}/state/{revision}.issue.json")
    if reference is not None:
        stored = _json(reference)
        if (not isinstance(stored, dict) or set(stored) != {"schema_version", "revision", "issue"}
                or type(stored["schema_version"]) is not int or stored["schema_version"] != 1
                or stored["revision"] != revision or type(stored["issue"]) is not int or stored["issue"] < 1):
            raise ValueError("invalid_statistics_issue_reference")
        issue = inbox.call("GET", inbox.base + f"/issues/{stored['issue']}")
        if not isinstance(issue, dict) or "pull_request" in issue or marker not in (issue.get("body") or ""):
            raise ValueError("statistics_issue_identity_mismatch")
        return issue
    found = []
    for page in range(1, 101):
        issues = inbox.call("GET", inbox.base + "/issues", params={"state": "all", "per_page": 100,
                           "page": page, "labels": "statistics/source-update"})
        found.extend(issue for issue in issues if "pull_request" not in issue and marker in (issue.get("body") or ""))
        if len(issues) < 100:
            if len(found) > 1:
                raise ValueError("duplicate_statistics_review_issues")
            return found[0] if found else None
    raise ValueError("statistics_issues_pagination_limit")


def issue_body(record):
    report, revision = record["report"], record["revision"]
    lines = [f"<!-- mdh-official-statistics:{revision} -->", "## Estadísticas oficiales pendientes de revisión", "",
             "Estos cambios están guardados en este repositorio privado. **No se publican automáticamente.**", "",
             f"Revisión: `{revision}`", f"Consulta: `{report['checked_at']}`",
             f"Base pública SHA-256: `{report['baseline_sha256']}`", "", "### Fuentes y cambios"]
    for change in report["changes"]:
        lines.append(f"- `{change['kind']}` · `{change['source_id']}` · <{change['source_url']}>")
    for error in report["errors"]:
        lines.append(f"- Error de consulta: `{error['source_id']}` · `{error['code']}`. No convierte datos en cero.")
    lines += ["", f"Informe íntegro: `{ROOT}/pending/{revision}.json`."]
    if record["proposal_path"]:
        lines.append(f"Propuesta sin aprobar: `{record['proposal_path']}` (SHA-256 `{report['proposal_sha256']}`).")
    else:
        lines.append("No hay propuesta numérica automática: el documento requiere extracción o comprobación manual.")
    lines += ["", "- [ ] Contrastar valores, unidades, cobertura y metodología con las fuentes.",
              "- [ ] Revisar revisiones históricas, períodos sin datos y totales departamentales.",
              "- [ ] Resolver los errores de consulta y comprobar la propuesta antes de aprobar.", "",
              "Usa Actions → Revisar estadísticas oficiales con este número de issue y revisión. "
              "Aprobar registra la decisión humana y prepara un artefacto; todavía requiere un PR público revisado. "
              "Cerrar el issue o cambiar sus etiquetas no equivale a aprobar datos."]
    return "\n".join(lines) + "\n"


def _record(inbox, revision):
    raw, _ = _read(inbox, f"{ROOT}/pending/{revision}.json")
    if raw is None:
        return None
    record = _json(raw)
    if (not isinstance(record, dict) or set(record) != {"schema_version", "revision", "report", "proposal_path", "proposal_content"}
            or type(record["schema_version"]) is not int or record["schema_version"] != 1
            or record["revision"] != revision or revision_id(record["report"]) != revision
            or record["proposal_path"] != (f"{ROOT}/pending/{revision}.proposal.json" if record["report"]["proposal_file"] else None)):
        raise ValueError("invalid_statistics_pending_record")
    if record["proposal_content"] is not None and not isinstance(record["proposal_content"], str):
        raise ValueError("invalid_statistics_pending_proposal")
    validate_proposal(record["proposal_content"].encode() if record["proposal_content"] is not None else None, record["report"])
    return record


def enqueue_report(inbox, report, *, proposal=None, source_state=None):
    validate_report(report)
    validate_proposal(proposal, report)
    if source_state is not None:
        validate_source_state(source_state)
    revision, created, issue_number = revision_id(report), False, None
    if report["review_required"]:
        record = _record(inbox, revision)
        if record is None:
            proposal_path = f"{ROOT}/pending/{revision}.proposal.json" if proposal is not None else None
            # The authoritative envelope includes the exact candidate bytes.
            # A retry reuses them even if a new fetch has a newer timestamp.
            record = {"schema_version": 1, "revision": revision, "report": report, "proposal_path": proposal_path,
                      "proposal_content": proposal.decode() if proposal is not None else None}
            _write(inbox, f"{ROOT}/pending/{revision}.json", _bytes(record), immutable=True)
        if record["proposal_path"]:
            _write(inbox, record["proposal_path"], record["proposal_content"].encode(), immutable=True)
        _labels(inbox)
        issue = _issue(inbox, revision)
        if issue is None:
            issue = inbox.call("POST", inbox.base + "/issues", json={
                "title": "[Estadísticas] Revisar fuentes oficiales · " + revision[:12],
                "body": issue_body(record), "labels": ["statistics/source-update", "statistics/pending"]})
            created = True
        if not isinstance(issue, dict) or type(issue.get("number")) is not int or issue["number"] < 1:
            raise ValueError("invalid_statistics_issue_receipt")
        issue_number = issue["number"]
        _write(inbox, f"{ROOT}/state/{revision}.issue.json",
               _bytes({"schema_version": 1, "revision": revision, "issue": issue_number}), immutable=True)
    # Catalog observations advance only after every needed change is durable.
    if source_state is not None:
        _write(inbox, f"{ROOT}/state/sources.json", _bytes(source_state))
    latest = {"schema_version": 1, "checked_at": report["checked_at"], "status": report["status"],
              "revision": revision if report["review_required"] else None, "issue": issue_number,
              "changes": len(report["changes"]), "errors": len(report["errors"]), "publications": 0}
    _write(inbox, f"{ROOT}/state/latest.json", _bytes(latest))
    return {**latest, "created": created}


def review_revision(inbox, event, *, now=None, validate_candidate=None, baseline=None):
    """Record an authenticated human decision; return an artifact, never publish."""
    sender, inputs = event.get("sender", {}), event.get("inputs", {})
    username = sender.get("login")
    if sender.get("type") != "User" or not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,39}", username):
        raise ValueError("statistics_review_requires_human")
    permission = inbox.call("GET", inbox.base + "/collaborators/" + quote(username, safe="") + "/permission")
    if permission.get("permission") not in {"write", "maintain", "admin"}:
        raise ValueError("statistics_review_requires_write_permission")
    revision, decision, notes = inputs.get("revision"), inputs.get("decision"), inputs.get("review_notes")
    issue_text = str(inputs.get("issue_number", ""))
    if (not _hash(revision) or decision not in {"approve", "reject"} or not re.fullmatch(r"[1-9][0-9]{0,9}", issue_text)
            or not isinstance(notes, str) or not 20 <= len(notes.strip()) <= 4000
            or any(ord(c) < 32 and c not in "\n\t" for c in notes)):
        raise ValueError("invalid_statistics_review_inputs")
    issue_number = int(issue_text)
    issue = inbox.call("GET", inbox.base + f"/issues/{issue_number}")
    if "pull_request" in issue or f"<!-- mdh-official-statistics:{revision} -->" not in (issue.get("body") or ""):
        raise ValueError("statistics_issue_identity_mismatch")
    record = _record(inbox, revision)
    if record is None:
        raise ValueError("statistics_pending_revision_missing")
    proposal = None
    if record["proposal_path"]:
        proposal = record["proposal_content"].encode()
        validate_proposal(proposal, record["report"])
    if decision == "approve" and str(inputs.get("confirm_source", "")).lower() != "true":
        raise ValueError("statistics_explicit_source_check_required")
    if decision == "approve":
        if proposal is None:
            raise ValueError("statistics_numeric_proposal_required")
        if not isinstance(baseline, bytes) or hashlib.sha256(baseline).hexdigest() != record["report"]["baseline_sha256"]:
            raise ValueError("statistics_public_baseline_changed")
        failed_sources = {error["source_id"] for error in record["report"]["errors"]}
        if any(change["source_id"] in failed_sources for change in record["report"]["changes"]
               if change["kind"] in {"fgr_values", "coverage_period"}):
            raise ValueError("statistics_proposal_source_has_errors")
        if validate_candidate is None:
            raise ValueError("statistics_schema_validator_required")
        validate_candidate(proposal)
    stored, _ = _read(inbox, f"{ROOT}/state/{revision}.json")
    if stored is not None:
        receipt = _json(stored)
        receipt_keys = {"schema_version", "revision", "issue", "decision", "reviewed_by", "reviewed_at",
                        "review_notes", "baseline_sha256", "proposal_sha256", "unresolved_errors", "publications"}
        if (not isinstance(receipt, dict) or set(receipt) != receipt_keys
                or type(receipt["schema_version"]) is not int or receipt["schema_version"] != 1
                or receipt["revision"] != revision or type(receipt["issue"]) is not int or receipt["issue"] != issue_number
                or not isinstance(receipt["reviewed_by"], str) or not re.fullmatch(r"[A-Za-z0-9-]{1,39}", receipt["reviewed_by"])
                or not isinstance(receipt["review_notes"], str) or not 20 <= len(receipt["review_notes"]) <= 4000
                or receipt["baseline_sha256"] != record["report"]["baseline_sha256"]
                or receipt["proposal_sha256"] != record["report"]["proposal_sha256"]
                or receipt["unresolved_errors"] != record["report"]["errors"]
                or type(receipt["publications"]) is not int or receipt["publications"] != 0):
            raise ValueError("invalid_statistics_review_receipt")
        _instant(receipt["reviewed_at"])
        if receipt["decision"] != decision:
            raise ValueError("statistics_revision_already_reviewed")
    else:
        receipt = {"schema_version": 1, "revision": revision, "issue": issue_number, "decision": decision,
                   "reviewed_by": username, "reviewed_at": (now or datetime.now(timezone.utc)).isoformat(),
                   "review_notes": notes.strip(), "baseline_sha256": record["report"]["baseline_sha256"],
                   "proposal_sha256": record["report"]["proposal_sha256"],
                   "unresolved_errors": record["report"]["errors"], "publications": 0}
        _write(inbox, f"{ROOT}/state/{revision}.json", _bytes(receipt), immutable=True)
    decision_note = "\n\n<!-- mdh-statistics-decision -->\n"
    decision_note += ("Aprobadas **sólo las cifras de la propuesta adjunta**; ninguna publicación automática."
                      if decision == "approve" else "Revisión rechazada; ninguna publicación.")
    if record["report"]["errors"]:
        decision_note += " Los errores de consulta anteriores **siguen sin resolverse** y esta decisión no valida esas fuentes."
    body = (issue.get("body") or "").split("\n\n<!-- mdh-statistics-decision -->", 1)[0] + decision_note + "\n"
    inbox.call("PATCH", inbox.base + f"/issues/{issue_number}", json={"state": "closed", "body": body,
               "labels": ["statistics/source-update", "statistics/approved" if decision == "approve" else "statistics/rejected"]})
    return {"review": receipt, "report": record["report"],
            "proposal": _json(proposal) if proposal is not None and decision == "approve" else None,
            "proposal_bytes": proposal if decision == "approve" else None, "publications": 0}


def _local(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_BYTES:
        raise ValueError("invalid_statistics_local_file")
    raw = path.read_bytes()
    _json(raw)
    return raw


def export_source_state(inbox):
    raw, _ = _read(inbox, f"{ROOT}/state/sources.json")
    return validate_source_state(_json(raw) if raw is not None else {"schema_version": 1, "catalogs": {}})


def validate_candidate_schema(raw, module_path):
    """Use the same pinned schema as the public API, before recording approval."""
    path = Path(module_path)
    if path.is_symlink() or not path.is_file() or path.name != "official-statistics.mjs":
        raise ValueError("invalid_statistics_schema_validator")
    script = ("import { readFileSync } from 'node:fs'; "
              "const {parseOfficialStatistics}=await import(process.argv[1]); "
              "parseOfficialStatistics(JSON.parse(readFileSync(0, 'utf8')));")
    try:
        result = subprocess.run(["node", "--input-type=module", "-e", script, path.resolve().as_uri()],
                                input=raw, capture_output=True, timeout=20, check=False)
        if result.returncode != 0:
            raise ValueError("invalid_statistics_candidate_schema")
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("statistics_schema_validation_unavailable") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    enqueue = commands.add_parser("enqueue")
    enqueue.add_argument("--report", required=True, type=Path)
    enqueue.add_argument("--proposal", type=Path)
    enqueue.add_argument("--source-state", type=Path)
    export = commands.add_parser("export-state")
    export.add_argument("--output", required=True, type=Path)
    review = commands.add_parser("review")
    review.add_argument("--event", required=True, type=Path)
    review.add_argument("--output", required=True, type=Path)
    review.add_argument("--schema-validator", type=Path)
    review.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    try:
        inbox = GitHubInbox(os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"])
        if args.command == "enqueue":
            result = enqueue_report(inbox, _json(_local(args.report)),
                proposal=_local(args.proposal) if args.proposal else None,
                source_state=_json(_local(args.source_state)) if args.source_state else None)
        elif args.command == "export-state":
            if args.output.is_symlink() or args.output.exists():
                raise ValueError("statistics_review_output_already_exists")
            args.output.write_bytes(_bytes(export_source_state(inbox)))
            result = {"status": "exported", "publications": 0}
        else:
            if args.output.is_symlink() or args.output.exists():
                raise ValueError("statistics_review_output_already_exists")
            event = _json(_local(args.event))
            validator = (lambda raw: validate_candidate_schema(raw, args.schema_validator)) if args.schema_validator else None
            artifact = review_revision(inbox, event, validate_candidate=validator,
                                       baseline=_local(args.baseline) if args.baseline else None)
            proposal_bytes = artifact.pop("proposal_bytes")
            args.output.mkdir(parents=True)
            (args.output / "review.json").write_bytes(_bytes(artifact))
            if artifact["proposal"] is not None:
                (args.output / "approved-homicides.json").write_bytes(proposal_bytes)
            result = {"decision": artifact["review"]["decision"], "issue": artifact["review"]["issue"], "publications": 0}
        print(json.dumps(result))
    except Exception as error:
        failure = {"status": "failed", "error_type": type(error).__name__, "publications": 0}
        if isinstance(error, GitHubRequestError):
            failure["error_code"] = error.code
        print(json.dumps(failure))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
