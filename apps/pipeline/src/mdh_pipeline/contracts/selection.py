"""Pure selection contracts, extracted without changing validation or normalization."""
from __future__ import annotations
from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import re
from ..news_scope_policy import SCOPE_PROMPT, EXCLUSION_PROMPT

_HEX = r"[a-f0-9]{64}"

_PARAGRAPH = r"p[1-9][0-9]{0,2}"

_MODEL = r"gemini-[A-Za-z0-9_.-]{1,90}"

LEGACY_FULL_SCOPE_PROMPT = "mdh-universal-news-grounded-8-relevance-scope"

PREVIOUS_FULL_SCOPE_PROMPT = "mdh-universal-news-grounded-9-inclusive-context"

RIGHTS_FULL_SCOPE_PROMPT = "mdh-universal-news-grounded-10-rights-nexus"

FULL_SCOPE_PROMPT = "mdh-universal-news-grounded-11-inclusive-violence"

SCOPE_REVIEW_PROMPT = "mdh-news-scope-1"

INCLUSIVE_SCOPE_PROMPT = "mdh-news-scope-2-inclusive-context"

RIGHTS_SCOPE_PROMPT = "mdh-news-scope-3-rights-nexus"

_SELECTION_PROMPTS = {FULL_SCOPE_PROMPT, LEGACY_FULL_SCOPE_PROMPT, PREVIOUS_FULL_SCOPE_PROMPT,
                      SCOPE_REVIEW_PROMPT, INCLUSIVE_SCOPE_PROMPT, RIGHTS_SCOPE_PROMPT, RIGHTS_FULL_SCOPE_PROMPT,
                      SCOPE_PROMPT, EXCLUSION_PROMPT}

_STAMP = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)"

_COMMON = {"method", "review_status", "human_reviewed", "jurisdiction", "relevance"}

_AUTOMATIC = _COMMON | {"classified_at", "model", "prompt_version", "evidence"}

_HUMAN = _COMMON | {"reviewed_by", "reviewed_at"}

_ENTRY = {"candidate_id", "source_url", "publisher", "published_date", "selection"}

MAX_SELECTIONS = 50_000

def _fail():
    raise ValueError("Invalid news selection or bibliography binding")

def _matches(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None

def parse_timestamp(value):
    if not _matches(_STAMP, value):
        _fail()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail()

def parse_day(value):
    if not _matches(r"\d{4}-\d{2}-\d{2}", value):
        _fail()
    try:
        return date.fromisoformat(value)
    except ValueError:
        _fail()

def _paragraphs(value):
    if (not isinstance(value, list) or not 1 <= len(value) <= 25
            or any(not _matches(_PARAGRAPH, item) for item in value)
            or len(set(value)) != len(value)):
        _fail()
    return value

def validate_selection(value):
    """Return a copy of the exact public union; reject extra or private fields."""
    if (not isinstance(value, dict) or value.get("jurisdiction") != "SV"
            or value.get("relevance") != "relevant" or type(value.get("human_reviewed")) is not bool):
        _fail()
    if value.get("method") == "gemini_grounded_scope":
        if (set(value) != _AUTOMATIC or value["review_status"] != "automated_unreviewed"
                or value["human_reviewed"] is not False or not _matches(_MODEL, value["model"])
                or not isinstance(value["prompt_version"], str) or value["prompt_version"] not in _SELECTION_PROMPTS):
            _fail()
        parse_timestamp(value["classified_at"])
        evidence = value["evidence"]
        if (not isinstance(evidence, dict) or set(evidence) != {
                "content_sha256", "jurisdiction_paragraph_ids", "relevance_paragraph_ids"}
                or not _matches(_HEX, evidence["content_sha256"])):
            _fail()
        _paragraphs(evidence["jurisdiction_paragraph_ids"])
        _paragraphs(evidence["relevance_paragraph_ids"])
    elif value.get("method") == "editorial_scope":
        if (set(value) != _HUMAN or value["review_status"] != "editorially_reviewed"
                or value["human_reviewed"] is not True
                or not _matches(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", value["reviewed_by"])):
            _fail()
        parse_day(value["reviewed_at"])
    else:
        _fail()
    return deepcopy(value)

def index_archive(archive):
    # Local import allows history.export_archive to call select_archive safely.
    from .reference import validate_reference
    from ..publishers import SOURCES
    if (not isinstance(archive, dict) or type(archive.get("schema_version")) is not int
            or archive["schema_version"] != 1 or not isinstance(archive.get("records"), list)):
        _fail()
    sources = {source["id"]: source for source in SOURCES}
    references = {}
    try:
        for item in archive["records"]:
            if not isinstance(item, dict):
                _fail()
            reference = {key: value for key, value in item.items() if key != "selection"}
            validate_reference(reference, sources[reference["source_id"]])
            if "selection" in item:
                validate_selection(item["selection"])
            if reference["id"] in references:
                _fail()
            references[reference["id"]] = reference
    except (KeyError, TypeError, ValueError, AttributeError):
        _fail()
    return references

def _bound(document, reference):
    return (reference is not None and document.get("candidate_id") == reference["id"]
            and all(document.get(key) == reference[key]
                    for key in ("source_url", "publisher", "published_date")))

def _assessment_counts(value):
    if (not isinstance(value, dict) or set(value) != {
            "candidates", "evaluated", "relevant", "excluded", "uncertain", "pending"}
            or any(type(number) is not int or not 0 <= number <= 9_007_199_254_740_991 for number in value.values())
            or value["evaluated"] != value["relevant"] + value["excluded"] + value["uncertain"]
            or value["pending"] != value["candidates"] - value["evaluated"]):
        _fail()
    return value

def validate_manifest(document, archive=None):
    """A stale or malformed manifest fails as a unit, without echoing its content."""
    from ..source_urls import canonical_url
    if (not isinstance(document, dict) or type(document.get("schema_version")) is not int
            or document["schema_version"] not in {1, 2}
            or not isinstance(document.get("selections"), list) or len(document["selections"]) > MAX_SELECTIONS):
        _fail()
    expected = {"schema_version", "generated_at", "selections"}
    if document["schema_version"] == 2:
        expected.add("assessment_counts")
    if set(document) != expected:
        _fail()
    generated = parse_timestamp(document["generated_at"])
    references = index_archive(archive) if archive is not None else None
    if document["schema_version"] == 2:
        counts = _assessment_counts(document["assessment_counts"])
        if (counts["relevant"] < len(document["selections"])
                or references is not None and counts["candidates"] > len(references)):
            _fail()
    identifiers = set()
    for entry in document["selections"]:
        if not isinstance(entry, dict) or set(entry) != _ENTRY:
            _fail()
        identifier, url = entry["candidate_id"], entry["source_url"]
        if (not _matches(_HEX, identifier) or not isinstance(url, str) or len(url) > 4096
                or not isinstance(entry["publisher"], str) or not 3 <= len(entry["publisher"].strip()) <= 200):
            _fail()
        try:
            if canonical_url(url) != url or hashlib.sha256(url.encode()).hexdigest() != identifier:
                _fail()
        except (ValueError, TypeError):
            _fail()
        published = parse_day(entry["published_date"])
        selection = validate_selection(entry["selection"])
        # A manifest carries automated decisions only. Human decisions must
        # come from validate_collection(require_reviewed) inside select_archive.
        if selection["method"] != "gemini_grounded_scope":
            _fail()
        classified = parse_timestamp(selection["classified_at"])
        if classified > generated or classified.date() < published:
            _fail()
        if identifier in identifiers or (references is not None and not _bound(entry, references.get(identifier))):
            _fail()
        identifiers.add(identifier)
    return deepcopy(document)

def _scope_paragraphs(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 25:
        _fail()
    seen = set()
    for item in value:
        if (not isinstance(item, dict) or set(item) != {"paragraph_id", "quote_sha256"}
                or not _matches(_PARAGRAPH, item["paragraph_id"]) or not _matches(_HEX, item["quote_sha256"])):
            _fail()
        key = (item["paragraph_id"], item["quote_sha256"])
        if key in seen:
            _fail()
        seen.add(key)
    return sorted({item["paragraph_id"] for item in value}, key=lambda item: int(item[1:]))

def _validate_scope_review_shape(document):
    required = {
            "schema_version", "candidate_id", "source_url", "publisher", "published_date",
            "classification", "evidence", "extraction"}
    if (not isinstance(document, dict) or type(document.get("schema_version")) is not int
            or document["schema_version"] not in {1, 2}):
        _fail()
    if document["schema_version"] == 2 or "source_read" in document:
        required.add("source_read")
    if set(document) != required:
        _fail()
    scope, evidence, extraction = document["classification"], document["evidence"], document["extraction"]
    if (not isinstance(scope, dict) or set(scope) != {"jurisdiction", "relevance"}
            or scope["jurisdiction"] not in {"SV", "other", "unclear"}
            or scope["relevance"] not in {"relevant", "not_relevant", "unclear"}
            or not isinstance(evidence, dict) or set(evidence) != {
                "content_sha256", "jurisdiction_paragraph_ids", "relevance_paragraph_ids"}
            or not _matches(_HEX, evidence["content_sha256"])
            or not isinstance(extraction, dict) or set(extraction) != {"provider", "model", "prompt_version", "prepared_at"}
            or extraction["provider"] != "gemini" or not _matches(_MODEL, extraction["model"])
            or extraction["prompt_version"] not in (
                {INCLUSIVE_SCOPE_PROMPT, RIGHTS_SCOPE_PROMPT, SCOPE_PROMPT, EXCLUSION_PROMPT} if document["schema_version"] == 2 else {SCOPE_REVIEW_PROMPT})):
        _fail()
    prepared = parse_timestamp(extraction["prepared_at"])
    if "source_read" in document:
        receipt = document["source_read"]
        if (not isinstance(receipt, dict) or set(receipt) != {
                "fetched_at", "extraction_method", "truncated", "content_sha256"}
                or receipt["extraction_method"] not in {"wordpress_rest_content", "html_article_content"}
                or type(receipt["truncated"]) is not bool
                or receipt["content_sha256"] != evidence["content_sha256"]
                or parse_timestamp(receipt["fetched_at"]) > prepared
                or parse_timestamp(receipt["fetched_at"]).date() < parse_day(document["published_date"])):
            _fail()
    for field in ("jurisdiction", "relevance"):
        ids = evidence[field + "_paragraph_ids"]
        # A private exclusion can describe absence of qualifying evidence; it
        # cannot cite an absent passage. Every positive still needs its anchors.
        positive = "SV" if field == "jurisdiction" else "relevant"
        if ids == [] and scope[field] != positive:
            continue
        _paragraphs(ids)
    return document

def validate_scope_review(document, *, reference, content_sha256, now=None):
    """Validate a private scope assessment, including negative/unclear decisions."""
    from ..source_urls import canonical_url
    try:
        _validate_scope_review_shape(document)
        instant = now or datetime.now(timezone.utc)
        instant = parse_timestamp(instant.isoformat() if isinstance(instant, datetime) else instant)
        prepared = parse_timestamp(document["extraction"]["prepared_at"])
        if (not _bound(document, reference) or not _matches(_HEX, content_sha256)
                or document["evidence"]["content_sha256"] != content_sha256
                or not _matches(_HEX, document["candidate_id"])
                or canonical_url(document["source_url"]) != document["source_url"]
                or hashlib.sha256(document["source_url"].encode()).hexdigest() != document["candidate_id"]
                or prepared > instant or prepared.date() < parse_day(document["published_date"])):
            _fail()
    except (ValueError, TypeError, KeyError, AttributeError):
        _fail()
    return deepcopy(document)

def validate_scope_audits(document, archive=None, *, now=None):
    """Validate minimal source-audit provenance; no source text or human attribution."""
    from ..source_urls import canonical_url
    required = {"candidate_id", "source_url", "publisher", "published_date", "content_sha256",
                "decision", "method", "human_reviewed", "checked_at", "reason"}
    if (not isinstance(document, dict) or set(document) != {"schema_version", "audits"}
            or type(document["schema_version"]) is not int or document["schema_version"] != 1
            or not isinstance(document["audits"], list) or len(document["audits"]) > MAX_SELECTIONS):
        _fail()
    references = index_archive(archive) if archive is not None else None
    instant = now or datetime.now(timezone.utc)
    instant = parse_timestamp(instant.isoformat() if isinstance(instant, datetime) else instant)
    seen = set()
    for entry in document["audits"]:
        if (not isinstance(entry, dict) or set(entry) != required
                or not _matches(_HEX, entry["candidate_id"]) or not _matches(_HEX, entry["content_sha256"])
                or entry["method"] != "automated_source_audit" or entry["human_reviewed"] is not False
                or entry["decision"] != "not_relevant" or entry["reason"] != "no_substantive_human_rights_nexus"
                or not isinstance(entry["source_url"], str) or len(entry["source_url"]) > 4096
                or not isinstance(entry["publisher"], str) or not 3 <= len(entry["publisher"].strip()) <= 200):
            _fail()
        try:
            if (canonical_url(entry["source_url"]) != entry["source_url"]
                    or hashlib.sha256(entry["source_url"].encode()).hexdigest() != entry["candidate_id"]
                    or not parse_day(entry["published_date"]) <= parse_timestamp(entry["checked_at"]).date()
                    or parse_timestamp(entry["checked_at"]) > instant):
                _fail()
        except (ValueError, TypeError):
            _fail()
        identity = (entry["candidate_id"], entry["content_sha256"])
        if identity in seen:
            _fail()
        seen.add(identity)
        # A collection may contain historical audits outside this archive slice.
        # Present references must match exactly; absent references are not selected.
        if references is not None and entry["candidate_id"] in references and not _bound(entry, references[entry["candidate_id"]]):
            _fail()
    return deepcopy(document)

def _excluded_set(values):
    if isinstance(values, (str, bytes)):
        _fail()
    try:
        result = set(values)
    except TypeError:
        _fail()
    if any(not _matches(_HEX, identifier) for identifier in result):
        _fail()
    return result
