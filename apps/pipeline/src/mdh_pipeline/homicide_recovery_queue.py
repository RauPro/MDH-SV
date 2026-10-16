"""Store one private recovery batch for manual metadata review; never publish it.

The batch marker and labels intentionally differ from the individual news inbox.
Nothing in this module creates extractor candidates, approves records, calls an AI
provider, or writes the public bibliography. Approved metadata must be imported in
a separately reviewed change before any ordinary news processing begins.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path

from .homicide_recovery import identity_keys, validate_recovery_report
from .publishers import SOURCES
from .review_queue import GitHubInbox, GitHubRequestError

from .instance import load_instance

REPOSITORY = load_instance().private_repository
ROOT = "discovery/homicides-2015"
MAX_BYTES = 900_000  # Contents API must return inline base64, including on retries.
LABELS = {"discovery/homicides-2015": "1D76DB", "discovery/pending": "D97706"}


def _bytes(document):
    return (json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_recovery_json_key")
            result[key] = value
        return result
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BYTES:
        raise ValueError("invalid_recovery_file_size")
    try:
        return json.loads(raw, object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, ValueError):
        raise ValueError("invalid_recovery_json") from None


def batch_id(candidates):
    """Content identity, stable across ordering and collection timestamps."""
    records = []
    for reference in candidates["records"]:
        records.append({**reference, "discovery": {
            key: value for key, value in reference["discovery"].items() if key != "retrieved_at"}})
    stable = {"schema_version": 1, "year": 2015,
              "records": sorted(records, key=lambda reference: reference["id"])}
    return hashlib.sha256(_bytes(stable)).hexdigest()


def _validate(candidates, report):
    validate_recovery_report(report, candidates)
    if report["year"] != 2015:
        raise ValueError("recovery_queue_requires_2015")
    if not candidates["records"]:
        raise ValueError("empty_recovery_batch")
    if len(_bytes(candidates)) > MAX_BYTES or len(_bytes(report)) > MAX_BYTES:
        raise ValueError("recovery_batch_too_large")


def _private(inbox):
    if inbox.repository != REPOSITORY or inbox.base != "/repos/" + REPOSITORY:
        raise ValueError("recovery_repository_not_authorized")
    repo = inbox.call("GET", inbox.base)
    if not isinstance(repo, dict) or repo.get("private") is not True or repo.get("full_name") != REPOSITORY:
        raise ValueError("recovery_repository_must_be_private")


def _read(inbox, relative):
    response = inbox.get_file(inbox.base + "/contents/" + relative)
    if response is None:
        return None
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
        return raw
    except (ValueError, TypeError, AttributeError):
        raise ValueError("invalid_recovery_storage_file") from None


def _record(raw, revision):
    record = _json(raw)
    if (not isinstance(record, dict) or set(record) != {"schema_version", "batch_id", "year", "status", "review_required", "candidates", "report", "candidates_sha256", "report_sha256"}
            or type(record["schema_version"]) is not int or record["schema_version"] != 1
            or record["batch_id"] != revision or record["year"] != 2015
            or record["status"] != "pending" or record["review_required"] is not True):
        raise ValueError("invalid_recovery_envelope")
    _validate(record["candidates"], record["report"])
    if (batch_id(record["candidates"]) != revision
            or record["candidates_sha256"] != hashlib.sha256(_bytes(record["candidates"])).hexdigest()
            or record["report_sha256"] != hashlib.sha256(_bytes(record["report"])).hexdigest()):
        raise ValueError("recovery_envelope_hash_mismatch")
    return record


def _reject_overlap(inbox, candidates, revision):
    """Never repack a previously submitted reference into a different batch."""
    try:
        entries = inbox.call("GET", inbox.base + "/contents/" + ROOT, params={"ref": "main"})
    except GitHubRequestError as error:
        if error.status_code == 404:
            return
        raise
    # Contents directory listings stop at 1,000 entries without pagination.
    if not isinstance(entries, list) or len(entries) >= 1000:
        raise ValueError("incomplete_recovery_batch_listing")
    source_map = {source["id"]: source for source in SOURCES}
    candidate_keys = set().union(*(identity_keys(ref, source_map[ref["source_id"]]) for ref in candidates["records"]))
    names, batches = set(), []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("invalid_recovery_batch_listing")
        name = entry.get("name")
        if not isinstance(name, str) or name in names or entry.get("path") != f"{ROOT}/{name}":
            raise ValueError("invalid_recovery_batch_listing")
        names.add(name)
        if name == "state" and entry.get("type") == "dir":
            continue
        if entry.get("type") != "file" or not re.fullmatch(r"[a-f0-9]{64}\.json", name):
            raise ValueError("invalid_recovery_batch_listing")
        batches.append(name[:-5])
    for saved_revision in batches:
        if saved_revision == revision:
            # It appeared after our earlier GET: require a retry to reconcile.
            raise ValueError("recovery_batch_changed_during_check")
        raw = _read(inbox, f"{ROOT}/{saved_revision}.json")
        if raw is None:
            raise ValueError("incomplete_recovery_batch_listing")
        saved = _record(raw, saved_revision)
        if any(candidate_keys & identity_keys(ref, source_map[ref["source_id"]])
               for ref in saved["candidates"]["records"]):
            raise ValueError("recovery_batch_overlaps_existing")


def _write(inbox, relative, document):
    raw = _bytes(document)
    _json(raw)
    current = _read(inbox, relative)
    if current == raw:
        return
    if current is not None:
        raise ValueError("immutable_recovery_batch_conflict")
    # GitHubInbox reconciles uncertain PUTs by reading and comparing exact bytes.
    # Never replace an existing file or an unknown concurrent writer's content.
    receipt = inbox.call("PUT", inbox.base + "/contents/" + relative, json={
        "message": "Store private homicide recovery batch for manual review",
        "branch": "main", "content": base64.b64encode(raw).decode()})
    if (not isinstance(receipt, dict) or not isinstance(receipt.get("content"), dict)
            or not re.fullmatch(r"[a-f0-9]{40,64}", receipt["content"].get("sha", ""))):
        raise ValueError("invalid_recovery_write_receipt")


def _labels(inbox):
    names = set()
    for page in range(1, 11):
        items = inbox.call("GET", inbox.base + "/labels", params={"per_page": 100, "page": page})
        if not isinstance(items, list) or len(items) > 100:
            raise ValueError("invalid_recovery_labels")
        names.update(item["name"] for item in items)
        if len(items) < 100:
            break
    else:
        raise ValueError("recovery_labels_pagination_limit")
    for name, color in LABELS.items():
        if name not in names:
            inbox.call("POST", inbox.base + "/labels", json={"name": name, "color": color})


def _checked_issue(issue, revision):
    marker = f"<!-- mdh-homicide-recovery:{revision} -->"
    if (not isinstance(issue, dict) or "pull_request" in issue
            or type(issue.get("number")) is not int or issue["number"] < 1
            or marker not in (issue.get("body") or "")):
        raise ValueError("recovery_issue_identity_mismatch")
    return issue


def _issue(inbox, revision):
    raw = _read(inbox, f"{ROOT}/state/{revision}.issue.json")
    if raw is not None:
        saved = _json(raw)
        if (not isinstance(saved, dict) or set(saved) != {"schema_version", "batch_id", "issue"}
                or type(saved["schema_version"]) is not int or saved["schema_version"] != 1
                or saved["batch_id"] != revision or type(saved["issue"]) is not int or saved["issue"] < 1):
            raise ValueError("invalid_recovery_issue_state")
        return _checked_issue(inbox.call("GET", inbox.base + f"/issues/{saved['issue']}"), revision)
    marker = f"<!-- mdh-homicide-recovery:{revision} -->"
    found = []
    # Include closed issues and missing labels: neither reopens nor duplicates a
    # reviewed batch. An uncertain POST is resolved here on the next invocation.
    for page in range(1, 201):
        items = inbox.call("GET", inbox.base + "/issues", params={"state": "all", "per_page": 100, "page": page})
        if not isinstance(items, list) or len(items) > 100:
            raise ValueError("invalid_recovery_issues")
        found.extend(_checked_issue(item, revision) for item in items
                     if "pull_request" not in item and marker in (item.get("body") or ""))
        if len(items) < 100:
            if len(found) > 1:
                raise ValueError("duplicate_recovery_issues")
            return found[0] if found else None
    raise ValueError("recovery_issues_pagination_limit")


def issue_body(record):
    revision = record["batch_id"]
    counts = Counter(reference["publisher"] for reference in record["candidates"]["records"])
    link = f"https://github.com/{REPOSITORY}/blob/main/{ROOT}/{revision}.json"
    lines = [f"<!-- mdh-homicide-recovery:{revision} -->", "## Recuperación de noticias de 2015 pendiente de revisión", "",
             "Este lote permanece en el repositorio privado. **No publica noticias, fichas ni ubicaciones.**",
             "Las fechas corresponden a publicación, no necesariamente a la fecha del hecho. La selección por metadatos puede incluir otros países, memoria, opinión o errores; no es un recuento de homicidios ni de víctimas.", "",
             "| Periódico | Referencias nuevas |", "|---|---:|"]
    lines += [f"| {publisher} | {count} |" for publisher, count in sorted(counts.items())]
    lines += ["", f"Total: **{sum(counts.values())}**. Cobertura parcial.",
              f"[Manifiesto privado con URLs originales y reporte]({link})", f"Identidad del lote: `{revision}`",
              f"SHA-256 de candidatas: `{record['candidates_sha256']}`", f"SHA-256 del reporte: `{record['report_sha256']}`", "",
              "### Revisión humana requerida", "",
              "- [ ] Contrastar cada fuente y confirmar año, pertinencia en derechos humanos y alcance salvadoreño.",
              "- [ ] Comprobar URLs equivalentes, republicaciones y seguimientos sin fusionar artículos distintos.",
              "- [ ] Revisar privacidad y atribución; registrar cuáles referencias se admiten o rechazan.",
              "- [ ] Preparar un PR posterior que importe únicamente los metadatos aprobados.", "",
              "Ninguna referencia de este lote entra automáticamente al extractor ni al archivo público. Este issue usa un circuito separado de la cola de fichas; cerrar el issue o cambiar etiquetas no aprueba ni publica datos."]
    return "\n".join(lines) + "\n"


def enqueue_recovery(inbox, candidates, report):
    _validate(candidates, report)
    revision = batch_id(candidates)
    _private(inbox)
    path = f"{ROOT}/{revision}.json"
    existing = _read(inbox, path)
    if existing is None:
        _reject_overlap(inbox, candidates, revision)
        record = {"schema_version": 1, "batch_id": revision, "year": 2015,
                  "status": "pending", "review_required": True,
                  "candidates": candidates, "report": report,
                  "candidates_sha256": hashlib.sha256(_bytes(candidates)).hexdigest(),
                  "report_sha256": hashlib.sha256(_bytes(report)).hexdigest()}
        _json(_bytes(record))  # Fail closed before the first mutation if oversized.
        _write(inbox, path, record)
    else:
        record = _record(existing, revision)
    _labels(inbox)
    issue = _issue(inbox, revision)
    created = issue is None
    if issue is None:
        issue = _checked_issue(inbox.call("POST", inbox.base + "/issues", json={
            "title": "[Recuperación 2015] Revisar metadatos de homicidios · " + revision[:12],
            "body": issue_body(record), "labels": list(LABELS)}), revision)
    _write(inbox, f"{ROOT}/state/{revision}.issue.json", {"schema_version": 1, "batch_id": revision, "issue": issue["number"]})
    return {"batch_id": revision, "created": created, "issue": issue["number"],
            "issue_url": f"https://github.com/{REPOSITORY}/issues/{issue['number']}",
            "new_candidates": len(record["candidates"]["records"]),
            "by_source": dict(sorted(Counter(ref["source_id"] for ref in record["candidates"]["records"]).items())),
            "publications": 0, "gemini_requests": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        documents = []
        for path in (args.candidates, args.report):
            if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_BYTES:
                raise ValueError("invalid_recovery_input_file")
            documents.append(_json(path.read_bytes()))
        _validate(*documents)
        if os.environ.get("GITHUB_REPOSITORY") != REPOSITORY:
            raise ValueError("recovery_repository_not_authorized")
        inbox = GitHubInbox(REPOSITORY, os.environ["GH_TOKEN"])
        result = enqueue_recovery(inbox, *documents)
    except Exception as error:
        print(json.dumps({"error": type(error).__name__, "publications": 0}))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
