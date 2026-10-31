"""Pure processing contracts, extracted without changing validation or normalization."""
from __future__ import annotations
from copy import deepcopy
from datetime import date, datetime
import hashlib
import re
from ..publishers import SOURCES
from .reference import validate_reference
from .selection import validate_selection
from .catalog import Publication

HEX = re.compile(r"^[a-f0-9]{64}$")

STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$")

STATUSES = frozenset({"pending", "generated", "source_unavailable", "error", "unknown"})

ENTRY_KEYS = {"candidate_id", "source_url", "publisher", "published_date", "selection_content_sha256", "status"}

MAX_ENTRIES = 100_000

class NewsProcessingError(ValueError):
    """Stable diagnostics never include source text, personal values or file paths."""

    def __init__(self, code="invalid_news_processing"):
        self.code = code
        super().__init__(code)

def parse_timestamp(value):
    if not isinstance(value, str) or not STAMP.fullmatch(value):
        raise NewsProcessingError()
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise NewsProcessingError() from None
    if result.utcoffset() is None:
        raise NewsProcessingError()
    return result

def selected_references(archive):
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

def selection_content_hash(reference):
    return reference["selection"].get("evidence", {}).get("content_sha256")

def validate_collection(document, *, archive=None):
    """Validate the exact public allowlist, optionally against current selection."""
    try:
        if (not isinstance(document, dict) or set(document) != {"schema_version", "generated_at", "entries"}
                or type(document["schema_version"]) not in (int, float) or document["schema_version"] != 1
                or not isinstance(document["entries"], list) or len(document["entries"]) > MAX_ENTRIES):
            raise ValueError()
        parse_timestamp(document["generated_at"])
        references = selected_references(archive) if archive is not None else None
        identifiers, urls = set(), set()
        for entry in document["entries"]:
            if (not isinstance(entry, dict) or set(entry) != ENTRY_KEYS
                    or not isinstance(entry["candidate_id"], str) or not HEX.fullmatch(entry["candidate_id"])
                    or not isinstance(entry["source_url"], str) or not 1 <= len(entry["source_url"]) <= 4096
                    or hashlib.sha256(entry["source_url"].encode()).hexdigest() != entry["candidate_id"]
                    or not isinstance(entry["publisher"], str) or len(entry["publisher"].strip()) < 3 or len(entry["publisher"]) > 200
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
            Publication.primary_source(entry["source_url"])
            if references is not None:
                reference = references.get(entry["candidate_id"])
                if (reference is None or any(entry[key] != reference[key] for key in ("source_url", "publisher", "published_date"))
                        or entry["selection_content_sha256"] != selection_content_hash(reference)):
                    raise ValueError()
            identifiers.add(entry["candidate_id"])
            urls.add(entry["source_url"])
        return {"schema_version": 1, "generated_at": document["generated_at"],
                "entries": sorted(deepcopy(document["entries"]), key=lambda item: item["candidate_id"])}
    except (ValueError, KeyError, TypeError, AttributeError):
        raise NewsProcessingError() from None
