"""Pure locations contracts, extracted without changing validation or normalization."""
from __future__ import annotations
import hashlib
import re
from datetime import date, datetime
from urllib.parse import urlsplit
from mdh_schema import DepartmentEnum
from ..publishers import SOURCES
from .reference import validate_reference
from ..source_urls import canonical_url
from ..municipalities import resolve_municipality, REFERENCE_VERSION

HEX = re.compile(r"^[a-f0-9]{64}$")

DEPARTMENTS = {item.value for item in DepartmentEnum if item is not DepartmentEnum.DESCONOCIDO}

ENTRY_KEYS = {"candidate_id", "source_url", "publisher", "published_date", "department",
              "review_status", "human_reviewed", "method", "extracted_at", "source_checked_at", "evidence"}

MAX_ENTRIES = 100_000

MUNICIPAL_METHOD = "gemini_municipality_reference"

CORROBORATED_METHOD = "gemini_department_corroborated"

SOURCE_SINGLE_METHODS = {"source_explicit_department", "source_municipality_reference"}

MULTI_SOURCE_METHOD = "source_administrative_mentions"

SOURCE_METHODS = SOURCE_SINGLE_METHODS | {MULTI_SOURCE_METHOD}

SOURCE_LOCATION_RULE_VERSION = 2

CORROBORATION_KEYS = {"strategy", "model_paragraph_id", "explicit_paragraph_id", "rule_version"}

RESOLUTION_KEYS = {"type", "municipality", "municipality_kind", "reference_version", "source_url"}

def municipality_resolution(place):
    return {"type": "municipality_to_department", "municipality": place["name"],
            "municipality_kind": place["kind"], "reference_version": REFERENCE_VERSION,
            "source_url": place["source_url"]}

class AutomaticLocationError(ValueError):
    """Stable error with no private article values or filesystem paths."""

    def __init__(self, code="invalid_automatic_locations"):
        self.code = code
        super().__init__(code)

def parse_timestamp(value):
    if not isinstance(value, str) or len(value) > 40 or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)", value):
        raise AutomaticLocationError("invalid_location_timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise AutomaticLocationError("invalid_location_timestamp")
    return value

def is_calendar_date(value):
    return (isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
            and date.fromisoformat(value).isoformat() == value)

def index_references(archive):
    try:
        if (not isinstance(archive, dict) or type(archive.get("schema_version")) is not int
                or archive["schema_version"] != 1 or not isinstance(archive.get("records"), list)
                or len(archive["records"]) > MAX_ENTRIES):
            raise ValueError()
        sources = {source["id"]: source for source in SOURCES}
        records = {}
        urls = set()
        for item in archive["records"]:
            validate_reference(item, sources[item["source_id"]])
            if (not is_calendar_date(item["published_date"]) or item["id"] in records
                    or item["source_url"] in urls):
                raise ValueError()
            records[item["id"]] = item
            urls.add(item["source_url"])
        return records
    except (ValueError, TypeError, KeyError, AttributeError):
        raise AutomaticLocationError("invalid_location_bibliography") from None

def validate_collection(document, *, archive=None):
    """Validate the exact public allowlist; neither private wrapper nor quotes fit it."""
    try:
        if (not isinstance(document, dict) or set(document) != {"schema_version", "generated_at", "locations"}
                or type(document["schema_version"]) not in (int, float) or document["schema_version"] not in {1, 2, 3, 4, 5}
                or not isinstance(document["locations"], list) or len(document["locations"]) > MAX_ENTRIES):
            raise ValueError()
        parse_timestamp(document["generated_at"])
        references = index_references(archive) if archive is not None else None
        identifiers, urls = set(), set()
        for entry in document["locations"]:
            municipal = isinstance(entry, dict) and entry.get("method") in {MUNICIPAL_METHOD, "source_municipality_reference"}
            corroborated = isinstance(entry, dict) and entry.get("method") == CORROBORATED_METHOD
            source_based = isinstance(entry, dict) and entry.get("method") in SOURCE_METHODS
            multiple = isinstance(entry, dict) and entry.get("method") == MULTI_SOURCE_METHOD
            extra_keys = {"resolution"} if municipal else {"corroboration"} if corroborated else set()
            if source_based:
                extra_keys |= {"rule_version"}
            expected_keys = ENTRY_KEYS | extra_keys
            if multiple:
                expected_keys = (expected_keys - {"department"}) | {"mentions"}
            if (not isinstance(entry, dict) or set(entry) != expected_keys
                    or not isinstance(entry["candidate_id"], str) or not HEX.fullmatch(entry["candidate_id"])
                    or not isinstance(entry["source_url"], str) or len(entry["source_url"]) > 4096
                    or hashlib.sha256(entry["source_url"].encode()).hexdigest() != entry["candidate_id"]
                    or not isinstance(entry["publisher"], str) or not 3 <= len(entry["publisher"]) <= 200
                    or not is_calendar_date(entry["published_date"])
                    or (not multiple and entry["department"] not in DEPARTMENTS)
                    or entry["review_status"] != "automated_unreviewed" or entry["human_reviewed"] is not False
                    or entry["method"] not in {"gemini_explicit_department", MUNICIPAL_METHOD, CORROBORATED_METHOD} | SOURCE_METHODS
                    or entry["candidate_id"] in identifiers or entry["source_url"] in urls):
                raise ValueError()
            if source_based and (document["schema_version"] not in {4, 5} or type(entry["rule_version"]) not in (int, float)
                                 or entry["rule_version"] not in {1, 2}
                                 or (entry["rule_version"] == 2 and document["schema_version"] != 5)):
                raise ValueError()
            if multiple:
                if (document["schema_version"] != 5 or entry["rule_version"] != 2
                        or not isinstance(entry["mentions"], list) or not 2 <= len(entry["mentions"]) <= 14):
                    raise ValueError()
                departments = set()
                for mention in entry["mentions"]:
                    validate_source_mention(mention)
                    if mention["department"] in departments:
                        raise ValueError()
                    departments.add(mention["department"])
            if municipal:
                resolution = entry["resolution"]
                if not isinstance(resolution, dict) or set(resolution) != RESOLUTION_KEYS:
                    raise ValueError()
                place = resolve_municipality(resolution["municipality"], entry["department"])
                if place is None or resolution != municipality_resolution(place):
                    raise ValueError()
            if corroborated:
                proof = entry["corroboration"]
                if (document["schema_version"] not in {3, 4, 5} or not isinstance(proof, dict)
                        or set(proof) != CORROBORATION_KEYS
                        or proof["strategy"] != "same_article_explicit_department"
                        or type(proof["rule_version"]) not in (int, float) or proof["rule_version"] != 1
                        or any(not isinstance(proof[key], str) or not re.fullmatch(r"p[1-9][0-9]{0,2}", proof[key])
                               for key in ("model_paragraph_id", "explicit_paragraph_id"))
                        or proof["model_paragraph_id"] == proof["explicit_paragraph_id"]):
                    raise ValueError()
            parse_timestamp(entry["extracted_at"])
            parse_timestamp(entry["source_checked_at"])
            source = next((value for value in SOURCES if urlsplit(entry["source_url"]).hostname in value["hosts"]), None)
            if (source is None or source["name"] != entry["publisher"]
                    or canonical_url(entry["source_url"], set(source["hosts"])) != entry["source_url"]):
                raise ValueError()
            evidence = entry["evidence"]
            if (not isinstance(evidence, dict) or set(evidence) != ({"content_sha256"} if multiple else {"paragraph_id", "content_sha256"})
                    or (not multiple and (not isinstance(evidence["paragraph_id"], str)
                        or not re.fullmatch(r"p[1-9][0-9]{0,2}", evidence["paragraph_id"])))
                    or not isinstance(evidence["content_sha256"], str) or not HEX.fullmatch(evidence["content_sha256"])):
                raise ValueError()
            if corroborated and evidence["paragraph_id"] != entry["corroboration"]["explicit_paragraph_id"]:
                raise ValueError()
            if references is not None:
                reference = references.get(entry["candidate_id"])
                if reference is None or any(reference[key] != entry[key] for key in ("source_url", "publisher", "published_date")):
                    raise ValueError()
                if "selection" in reference and selection_content_hash(reference) != evidence["content_sha256"]:
                    raise ValueError()
            identifiers.add(entry["candidate_id"])
            urls.add(entry["source_url"])
        return {"schema_version": document["schema_version"], "generated_at": document["generated_at"],
                "locations": sorted(document["locations"], key=lambda value: value["candidate_id"])}
    except (ValueError, TypeError, KeyError, AttributeError):
        raise AutomaticLocationError() from None

def validate_source_mention(mention):
    """A plural projection contains only the same minimal evidence as a single one."""
    if not isinstance(mention, dict) or mention.get("method") not in SOURCE_SINGLE_METHODS:
        raise ValueError()
    municipal = mention["method"] == "source_municipality_reference"
    if (set(mention) != {"method", "department", "paragraph_id"} | ({"resolution"} if municipal else set())
            or mention["department"] not in DEPARTMENTS
            or not isinstance(mention["paragraph_id"], str)
            or not re.fullmatch(r"p[1-9][0-9]{0,2}", mention["paragraph_id"])):
        raise ValueError()
    if municipal:
        resolution = mention["resolution"]
        if not isinstance(resolution, dict) or set(resolution) != RESOLUTION_KEYS:
            raise ValueError()
        place = resolve_municipality(resolution["municipality"], mention["department"])
        if place is None or resolution != municipality_resolution(place):
            raise ValueError()

def selection_content_hash(reference):
    from .selection import validate_selection
    if "selection" not in reference:
        return None
    selection = validate_selection(reference["selection"])
    if selection["method"] != "gemini_grounded_scope":
        return None  # Human geography is managed by the editorial projection.
    return selection["evidence"]["content_sha256"]
