"""Publish worksheet processing state without exposing private worksheet contents.

Scope selection, source location checks and a populated case template are not
evidence that a news worksheet was generated. Only a validated private dossier
bound to the selected source text can supply that state.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import tempfile

from .gemini_review import validate_envelope
from .history import SOURCES, validate_reference
from .news_selection import validate_selection

HEX = re.compile(r"^[a-f0-9]{64}$")
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$")
STATUSES = frozenset({"pending", "generated", "source_unavailable", "error", "unknown"})
ENTRY_KEYS = {"candidate_id", "source_url", "publisher", "published_date", "selection_content_sha256", "status"}
MAX_DRAFT_BYTES = 512_000
MAX_JSON_BYTES = 30_000_000
MAX_ENTRIES = 100_000


class NewsProcessingError(ValueError):
    """Stable diagnostics never include source text, personal values or file paths."""

    def __init__(self, code="invalid_news_processing"):
        self.code = code
        super().__init__(code)


def _timestamp(value):
    if not isinstance(value, str) or not STAMP.fullmatch(value):
        raise NewsProcessingError()
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise NewsProcessingError() from None
    if result.utcoffset() is None:
        raise NewsProcessingError()
    return result


def _references(archive):
    """Index only selected bibliography; unselected candidates never leave here."""
    try:
        if (not isinstance(archive, dict) or type(archive.get("schema_version")) is not int
                or archive["schema_version"] != 1 or not isinstance(archive.get("records"), list)
                or len(archive["records"]) > MAX_ENTRIES):
            raise ValueError()
        sources = {source["id"]: source for source in SOURCES}
        references, identifiers, urls = {}, set(), set()
        for reference in archive["records"]:
            validate_reference(reference, sources[reference["source_id"]])
            if (reference["id"] in identifiers or reference["source_url"] in urls
                    or hashlib.sha256(reference["source_url"].encode()).hexdigest() != reference["id"]
                    or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", reference["published_date"])):
                raise ValueError()
            identifiers.add(reference["id"])
            urls.add(reference["source_url"])
            if "selection" in reference:
                validate_selection(reference["selection"])
                references[reference["id"]] = reference
        return references
    except (ValueError, KeyError, TypeError, AttributeError):
        raise NewsProcessingError("invalid_processing_bibliography") from None


def _selection_hash(reference):
    return reference["selection"].get("evidence", {}).get("content_sha256")


def validate_collection(document, *, archive=None):
    """Validate the exact public allowlist, optionally against current selection."""
    try:
        if (not isinstance(document, dict) or set(document) != {"schema_version", "generated_at", "entries"}
                or type(document["schema_version"]) is not int or document["schema_version"] != 1
                or not isinstance(document["entries"], list) or len(document["entries"]) > MAX_ENTRIES):
            raise ValueError()
        _timestamp(document["generated_at"])
        references = _references(archive) if archive is not None else None
        identifiers, urls = set(), set()
        for entry in document["entries"]:
            if (not isinstance(entry, dict) or set(entry) != ENTRY_KEYS
                    or not isinstance(entry["candidate_id"], str) or not HEX.fullmatch(entry["candidate_id"])
                    or not isinstance(entry["source_url"], str) or not 1 <= len(entry["source_url"]) <= 4096
                    or hashlib.sha256(entry["source_url"].encode()).hexdigest() != entry["candidate_id"]
                    or not isinstance(entry["publisher"], str) or not 3 <= len(entry["publisher"]) <= 200
                    or not isinstance(entry["published_date"], str)
                    or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry["published_date"])
                    or date.fromisoformat(entry["published_date"]).isoformat() != entry["published_date"]
                    or not isinstance(entry["status"], str) or entry["status"] not in STATUSES
                    or (entry["selection_content_sha256"] is not None
                        and (not isinstance(entry["selection_content_sha256"], str)
                             or not HEX.fullmatch(entry["selection_content_sha256"])))
                    or (entry["status"] == "generated" and entry["selection_content_sha256"] is None)
                    or entry["candidate_id"] in identifiers or entry["source_url"] in urls):
                raise ValueError()
            if references is not None:
                reference = references.get(entry["candidate_id"])
                if (reference is None or any(entry[key] != reference[key] for key in ("source_url", "publisher", "published_date"))
                        or entry["selection_content_sha256"] != _selection_hash(reference)):
                    raise ValueError()
            identifiers.add(entry["candidate_id"])
            urls.add(entry["source_url"])
        return {"schema_version": 1, "generated_at": document["generated_at"],
                "entries": sorted(deepcopy(document["entries"]), key=lambda item: item["candidate_id"])}
    except (ValueError, KeyError, TypeError, AttributeError):
        raise NewsProcessingError() from None


def _status(original, reference, instant):
    try:
        document = deepcopy(original)
        validate_envelope(document, reference)
        dossier, extraction = document["news_dossier"], document.get("extraction", {})
        if (not isinstance(extraction, dict)
                or any(dossier[key] != reference[key] for key in ("source_url", "publisher", "published_date"))):
            return "unknown"
        # Public titles can be truncated; they do not establish text identity.
        if "prepared_at" in extraction and _timestamp(extraction["prepared_at"]) > instant:
            return "unknown"
        digest, selected = extraction.get("content_sha256"), _selection_hash(reference)
        if digest is not None and (not isinstance(digest, str) or not HEX.fullmatch(digest) or digest != selected):
            return "unknown"
        if dossier["extraction_status"] == "processed":
            # Historical wrappers without a source digest cannot establish that
            # their generated worksheet belongs to today's selected source text.
            return "generated" if digest is not None and digest == selected else "unknown"
        # These states describe a processing attempt, never source content. Older
        # skeletons have no text digest because the source could not be read.
        return dossier["extraction_status"]
    except (ValueError, KeyError, TypeError, AttributeError):
        return "unknown"


def project_news_processing(drafts, archive, *, snapshot_complete=False, previous=None, now=None):
    """Project a complete private inventory; no network calls or model requests.

    Missing drafts mean pending only when the caller has verified that the
    inventory is complete. A partial inventory defaults to unknown instead.
    """
    try:
        instant = now if now is not None else datetime.now(timezone.utc)
        if not isinstance(instant, datetime) or instant.utcoffset() is None or type(snapshot_complete) is not bool:
            raise ValueError()
        references = _references(archive)
        if not isinstance(drafts, list) or len(drafts) > MAX_ENTRIES:
            raise ValueError()
        indexed = {}
        for document in drafts:
            identifier = document.get("candidate_id") if isinstance(document, dict) else None
            if not isinstance(identifier, str) or not HEX.fullmatch(identifier) or identifier in indexed:
                raise ValueError()
            indexed[identifier] = document
        entries = []
        for identifier, reference in sorted(references.items()):
            status = (_status(indexed[identifier], reference, instant) if identifier in indexed
                      else "pending" if snapshot_complete else "unknown")
            entries.append({"candidate_id": identifier,
                            **{key: reference[key] for key in ("source_url", "publisher", "published_date")},
                            "selection_content_sha256": _selection_hash(reference), "status": status})
        result = validate_collection({"schema_version": 1, "generated_at": instant.isoformat(), "entries": entries}, archive=archive)
        if previous is not None:
            prior = validate_collection(previous)
            if prior["entries"] == result["entries"] and _timestamp(prior["generated_at"]) <= instant:
                result["generated_at"] = prior["generated_at"]
        return result
    except (ValueError, KeyError, TypeError, AttributeError):
        raise NewsProcessingError("processing_projection_failed") from None


def _read_json(path, maximum):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum:
        raise NewsProcessingError("invalid_processing_file")
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise NewsProcessingError("invalid_processing_file")
            result[key] = value
        return result

    def invalid_constant(_):
        raise NewsProcessingError("invalid_processing_file")

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object,
                      parse_constant=invalid_constant)


def _atomic_json(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".processing-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def export_news_processing(drafts_dir, archive_path, output_path, *, snapshot_complete=False, now=None):
    """Atomic export from a bounded directory; invalid JSON cannot become pending."""
    try:
        drafts_dir, archive_path, output_path = Path(drafts_dir), Path(archive_path), Path(output_path)
        if (not drafts_dir.is_dir() or drafts_dir.is_symlink() or output_path.is_symlink()
                or output_path.resolve() == archive_path.resolve()
                or output_path.resolve().is_relative_to(drafts_dir.resolve())):
            raise ValueError()
        paths = sorted(drafts_dir.glob("*.json"))
        if len(paths) > MAX_ENTRIES:
            raise ValueError()
        drafts = []
        for path in paths:
            if not HEX.fullmatch(path.stem):
                raise ValueError()
            document = _read_json(path, MAX_DRAFT_BYTES)
            if not isinstance(document, dict) or document.get("candidate_id") != path.stem:
                raise ValueError()
            drafts.append(document)
        archive = _read_json(archive_path, MAX_JSON_BYTES)
        previous = _read_json(output_path, MAX_JSON_BYTES) if output_path.exists() else None
        result = project_news_processing(drafts, archive, snapshot_complete=snapshot_complete, previous=previous, now=now)
        changed = result != previous
        if changed:
            _atomic_json(output_path, result)
        counts = Counter(entry["status"] for entry in result["entries"])
        return {"entries": len(result["entries"]), "changed": changed,
                "statuses": {status: counts[status] for status in sorted(STATUSES)}}
    except (ValueError, KeyError, TypeError, AttributeError, OSError):
        raise NewsProcessingError("processing_export_failed") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--drafts", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--snapshot-complete", action="store_true",
                        help="Caller verified a complete private Git checkout and draft inventory")
    args = parser.parse_args()
    try:
        result = export_news_processing(args.drafts, args.archive, args.output, snapshot_complete=args.snapshot_complete)
    except (ValueError, TypeError, OSError):
        raise SystemExit("No se exportaron estados: instantánea o proyección inválida; revise la cola privada.") from None
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
