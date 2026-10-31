"""Publish worksheet processing state without exposing private worksheet contents.

Scope selection, source location checks and a populated case template are not
evidence that a news worksheet was generated. Only a validated private dossier
bound to the selected source text can supply that state.
"""
from __future__ import annotations

import argparse
from .json_files import read_processing_json as _read_json, write_processing_json as _atomic_json
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

from .contracts.drafts import validate_envelope as validate_envelope
from .contracts.reference import validate_reference as validate_reference
from .contracts.selection import validate_selection as validate_selection
from .contracts.catalog import Publication as Publication

from .contracts.processing import (
    HEX as HEX,
    STAMP as STAMP,
    STATUSES as STATUSES,
    ENTRY_KEYS as ENTRY_KEYS,
    MAX_ENTRIES as MAX_ENTRIES,
    NewsProcessingError as NewsProcessingError,
    parse_timestamp as _timestamp,
    selected_references as _references,
    selection_content_hash as _selection_hash,
    validate_collection as validate_collection,
)

MAX_DRAFT_BYTES = 512_000
MAX_JSON_BYTES = 30_000_000


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
