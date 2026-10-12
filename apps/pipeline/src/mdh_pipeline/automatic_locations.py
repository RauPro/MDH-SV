"""Project explicitly sourced departments from private AI drafts, without publishing dossiers.

This is an unreviewed department-mention layer, never an incident or victim count.
Only the minimal bibliographic/provenance allowlist below may leave the private
queue. Source text is fetched into memory and never written into the projection.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from mdh_schema import DepartmentEnum

from .article_source import fetch_article
from .history import SOURCES, validate_reference
from .news_dossier import validate_news_dossier
from .review_queue import canonical_url, normalize
from .municipalities import resolve_municipality, REFERENCE_VERSION

HEX = re.compile(r"^[a-f0-9]{64}$")
PARAGRAPH = re.compile(r"^article-(p[1-9][0-9]{0,2})$")
DEPARTMENTS = {item.value for item in DepartmentEnum if item is not DepartmentEnum.DESCONOCIDO}
ENTRY_KEYS = {"candidate_id", "source_url", "publisher", "published_date", "department",
              "review_status", "human_reviewed", "method", "extracted_at", "source_checked_at", "evidence"}
MAX_DRAFT_BYTES = 512_000
MAX_ARCHIVE_BYTES = 30_000_000
MAX_ENTRIES = 100_000
MUNICIPAL_METHOD = "gemini_municipality_reference"
CORROBORATED_METHOD = "gemini_department_corroborated"
CORROBORATION_KEYS = {"strategy", "model_paragraph_id", "explicit_paragraph_id", "rule_version"}
RESOLUTION_KEYS = {"type", "municipality", "municipality_kind", "reference_version", "source_url"}


def _resolution(place):
    return {"type": "municipality_to_department", "municipality": place["name"],
            "municipality_kind": place["kind"], "reference_version": REFERENCE_VERSION,
            "source_url": place["source_url"]}


class AutomaticLocationError(ValueError):
    """Stable error with no private article values or filesystem paths."""

    def __init__(self, code="invalid_automatic_locations"):
        self.code = code
        super().__init__(code)


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 40 or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})", value):
        raise AutomaticLocationError("invalid_location_timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise AutomaticLocationError("invalid_location_timestamp")
    return value


def _calendar_date(value):
    return (isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
            and date.fromisoformat(value).isoformat() == value)


def _references(archive):
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
            if (not _calendar_date(item["published_date"]) or item["id"] in records
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
                or type(document["schema_version"]) is not int or document["schema_version"] not in {1, 2, 3}
                or not isinstance(document["locations"], list) or len(document["locations"]) > MAX_ENTRIES):
            raise ValueError()
        _timestamp(document["generated_at"])
        references = _references(archive) if archive is not None else None
        identifiers, urls = set(), set()
        for entry in document["locations"]:
            municipal = isinstance(entry, dict) and entry.get("method") == MUNICIPAL_METHOD
            corroborated = isinstance(entry, dict) and entry.get("method") == CORROBORATED_METHOD
            extra_keys = {"resolution"} if municipal else {"corroboration"} if corroborated else set()
            if (not isinstance(entry, dict) or set(entry) != ENTRY_KEYS | extra_keys
                    or not isinstance(entry["candidate_id"], str) or not HEX.fullmatch(entry["candidate_id"])
                    or not isinstance(entry["source_url"], str) or len(entry["source_url"]) > 4096
                    or hashlib.sha256(entry["source_url"].encode()).hexdigest() != entry["candidate_id"]
                    or not isinstance(entry["publisher"], str) or not 3 <= len(entry["publisher"]) <= 200
                    or not _calendar_date(entry["published_date"])
                    or entry["department"] not in DEPARTMENTS
                    or entry["review_status"] != "automated_unreviewed" or entry["human_reviewed"] is not False
                    or entry["method"] not in {"gemini_explicit_department", MUNICIPAL_METHOD, CORROBORATED_METHOD}
                    or entry["candidate_id"] in identifiers or entry["source_url"] in urls):
                raise ValueError()
            if municipal:
                resolution = entry["resolution"]
                if not isinstance(resolution, dict) or set(resolution) != RESOLUTION_KEYS:
                    raise ValueError()
                place = resolve_municipality(resolution["municipality"], entry["department"])
                if place is None or resolution != _resolution(place):
                    raise ValueError()
            if corroborated:
                proof = entry["corroboration"]
                if (document["schema_version"] != 3 or not isinstance(proof, dict)
                        or set(proof) != CORROBORATION_KEYS
                        or proof["strategy"] != "same_article_explicit_department"
                        or type(proof["rule_version"]) is not int or proof["rule_version"] != 1
                        or any(not isinstance(proof[key], str) or not re.fullmatch(r"p[1-9][0-9]{0,2}", proof[key])
                               for key in ("model_paragraph_id", "explicit_paragraph_id"))
                        or proof["model_paragraph_id"] == proof["explicit_paragraph_id"]):
                    raise ValueError()
            _timestamp(entry["extracted_at"])
            _timestamp(entry["source_checked_at"])
            source = next((value for value in SOURCES if urlsplit(entry["source_url"]).hostname in value["hosts"]), None)
            if (source is None or source["name"] != entry["publisher"]
                    or canonical_url(entry["source_url"], set(source["hosts"])) != entry["source_url"]):
                raise ValueError()
            evidence = entry["evidence"]
            if (not isinstance(evidence, dict) or set(evidence) != {"paragraph_id", "content_sha256"}
                    or not isinstance(evidence["paragraph_id"], str)
                    or not re.fullmatch(r"p[1-9][0-9]{0,2}", evidence["paragraph_id"])
                    or not isinstance(evidence["content_sha256"], str) or not HEX.fullmatch(evidence["content_sha256"])):
                raise ValueError()
            if corroborated and evidence["paragraph_id"] != entry["corroboration"]["explicit_paragraph_id"]:
                raise ValueError()
            if references is not None:
                reference = references.get(entry["candidate_id"])
                if reference is None or any(reference[key] != entry[key] for key in ("source_url", "publisher", "published_date")):
                    raise ValueError()
            identifiers.add(entry["candidate_id"])
            urls.add(entry["source_url"])
        return {"schema_version": document["schema_version"], "generated_at": document["generated_at"],
                "locations": sorted(document["locations"], key=lambda value: value["candidate_id"])}
    except (ValueError, TypeError, KeyError, AttributeError):
        raise AutomaticLocationError() from None


def _eligible(draft, references):
    """No inference from a title, place-name homonym or bibliography scope hint."""
    if (not isinstance(draft, dict) or type(draft.get("schema_version")) is not int
            or draft["schema_version"] != 1 or draft.get("publishable") is not False
            or draft.get("human_reviewed") is not False
            or draft.get("status") not in {"draft", "needs_scope_review", "needs_privacy_review"}):
        return None
    extraction = draft.get("extraction", {})
    flags = draft.get("privacy_flags", [])
    if not isinstance(flags, list) or any(flag not in {"minor_identifiers", "clinical_details", "precise_address", "private_contact"} for flag in flags):
        return None
    # Old blanket-redaction drafts must first be processed with field-level privacy.
    if (flags or draft.get("status") == "needs_privacy_review") and (type(extraction.get("privacy_policy_version")) is not int or extraction["privacy_policy_version"] != 2):
        return None
    protected = extraction.get("protected_fields", [])
    if not isinstance(protected, list) or any(not isinstance(name, str) or name not in {*draft.get("news_dossier", {}).get("fields", {}), "title", "penitentiary_related"} for name in protected):
        return None
    dossier = validate_news_dossier(draft.get("news_dossier"))
    if (dossier["human_reviewed"] is not False or dossier["extraction_status"] != "processed"
            or dossier["classification"]["jurisdiction"] != "SV"
            or dossier["classification"]["relevance"] != "relevant"
            or draft.get("classification") != dossier["classification"]):
        return None
    country = dossier["fields"]["country"]
    if country["status"] == "reported" and normalize(country["value"]).strip() not in {"el salvador", "republica de el salvador"}:
        return None
    identifier = dossier["candidate_id"]
    reference = references.get(identifier)
    if (reference is None or draft.get("candidate_id") != identifier or draft.get("source_url") != dossier["source_url"]
            or any(reference[key] != dossier[key] for key in ("source_url", "publisher", "published_date"))):
        return None
    department = dossier["fields"]["department"]
    municipality = dossier["fields"]["municipality"]
    if department["status"] == "withheld" or "department" in protected:
        return None
    digest = extraction.get("content_sha256")
    if extraction.get("provider") != "gemini" or not isinstance(digest, str) or not HEX.fullmatch(digest):
        return None
    extracted_at = _timestamp(extraction.get("prepared_at"))
    evidence = {item["id"]: item for item in dossier["evidence"]}
    def cited(field):
        paragraphs = []
        for ref in field["evidence_refs"]:
            match = PARAGRAPH.fullmatch(ref)
            if match and evidence[ref]["url"] == reference["source_url"] and digest in evidence[ref]["locator"]:
                paragraphs.append(match[1])
        return paragraphs
    declared = department["value"] if department["status"] == "reported" and department["value"] in DEPARTMENTS else None
    department_paragraphs = cited(department) if declared else []
    municipal_place = None
    municipal_paragraphs = []
    if municipality["status"] == "reported" and "municipality" not in protected:
        municipal_place = resolve_municipality(municipality["value"], declared)
        if municipal_place:
            municipal_paragraphs = cited(municipality)
    if not department_paragraphs and not municipal_paragraphs:
        return None
    # A unique locality contradicted by the model's department remains unresolved.
    if municipality["status"] == "reported" and declared:
        unique = resolve_municipality(municipality["value"])
        if unique and unique["department"] != declared:
            return None
    return {"reference": reference, "department": declared, "digest": digest,
            "extracted_at": extracted_at, "department_paragraphs": department_paragraphs,
            "municipal_place": municipal_place, "municipal_paragraphs": municipal_paragraphs,
            "municipality": municipality["value"] if municipal_place else None,
            "ambiguous_municipality": bool(municipal_place and resolve_municipality(municipality["value"]) is None)}


def _entry(candidate, selection, checked_at):
    reference = candidate["reference"]
    method, paragraph, department = selection[:3]
    result = {"candidate_id": reference["id"],
            **{key: reference[key] for key in ("source_url", "publisher", "published_date")},
            "department": department, "review_status": "automated_unreviewed",
            "human_reviewed": False, "method": method,
            "extracted_at": candidate["extracted_at"], "source_checked_at": checked_at,
            "evidence": {"paragraph_id": paragraph, "content_sha256": candidate["digest"]}}
    if method == MUNICIPAL_METHOD:
        result["resolution"] = _resolution(candidate["municipal_place"])
    if method == CORROBORATED_METHOD:
        result["corroboration"] = {"strategy": "same_article_explicit_department",
                                   "model_paragraph_id": selection[3], "explicit_paragraph_id": paragraph,
                                   "rule_version": 1}
    return result


def _negated_location(source, name):
    # A literal place name is not evidence when the article denies that location.
    verb = r"(?:ocurri\w*|sucedi\w*|aconteci\w*|produj\w*|registr\w*|ubic\w*|tuvo lugar|tuvieron lugar|fue|fueron|era|eran|estaba|estaban)"
    return bool(re.search(rf"\b(?:no|nunca)\s+(?:(?:se|ha|han|habia|habian|habria)\s+)*{verb}[^.;!?]{{0,60}}\ben\s+(?:el\s+|la\s+)?(?:(?:municipio|distrito|ciudad|departamento)\s+de\s+)?{name}\b", source))


def _municipal_context(text, candidate):
    """Require cited locality context, not an author dateline, route or institution seat."""
    source = " ".join(normalize(text).split())
    place = candidate["municipal_place"]
    name, department = re.escape(normalize(place["name"])), re.escape(normalize(place["department"]))
    foreign = r"(?:california|texas|florida|nueva york|jujuy|estados unidos|ee\.?\s?uu\.?|mexico|guatemala|honduras|nicaragua|costa rica|panama|colombia|ecuador|peru|bolivia|argentina|chile|espana|canada|australia|cuba|venezuela|uruguay|brasil|republica dominicana|puerto rico)"
    if re.search(rf"\b{name}\s*(?:,|\(|\ben\b|\bde\b)\s*{foreign}\b", source):
        return False
    if _negated_location(source, name):
        return False
    # These ambiguous roles do not locate the event/topic. No fuzzy geocoding.
    if re.search(r"\b(?:sala de redaccion|sede(?: editorial| (?:de|del))|oficinas (?:de|del)|carretera (?:que conduce )?a|tribunal|juzgado)\b", source):
        return False
    if re.search(rf"\b{name}\s+(?:norte|sur|este|oeste|centro|oriental|occidental)\b", source):
        return False
    context = (rf"\b(?:municipio|distrito|ciudad|localidad)\s+(?:de\s+)?{name}\b",
               rf"\b(?:alcaldia|concejo municipal)\s+(?:municipal\s+)?de\s+{name}\b",
               rf"\ben\s+{name}\b",
               rf"\b{name}\s*,\s*(?:departamento\s+(?:de\s+)?)?{department}\b")
    if not any(re.search(pattern, source) for pattern in context):
        return False
    if candidate["ambiguous_municipality"]:
        # A model hint alone cannot disambiguate repeated municipality names.
        return bool(re.search(rf"\b{name}\s*,\s*(?:departamento\s+(?:de\s+)?)?{department}\b", source)
                    or re.search(rf"\bdepartamento\s+de\s+{department}\b", source))
    return True


def _department_context(text, department, *, explicit=False):
    """Positive geographic context for two-paragraph support, never a bare mention.

    This supplements an existing model value; it neither repairs that model's
    stored citations nor claims that Gemini supplied the additional paragraph.
    Both original and supplemental paragraphs must pass these context guards.
    """
    source = " ".join(normalize(text).split())
    name = re.escape(normalize(department))
    foreign = r"(?:california|texas|florida|nueva york|jujuy|estados unidos|ee\.?\s?uu\.?|mexico|guatemala|honduras|nicaragua|costa rica|panama|colombia|ecuador|peru|bolivia|argentina|chile|espana|canada|australia|cuba|venezuela|uruguay|brasil|republica dominicana|puerto rico)"
    operation = r"(?:cerco|asedio|operativo|despliegue|incursion)(?:\s+(?:militar|policial))?"
    qualified_name = rf"(?:(?:el\s+)?departamento\s+de\s+)?{name}\b"
    if (re.search(rf"\b{name}\s*(?:,|\(|\ben\b|\bde\b)\s*(?:en\s+)?(?:(?:el|la|los|las)\s+)?{foreign}\b", source)
            or re.search(r"\b(?:sala de redaccion|sede|oficinas|carretera|tribunal|juzgado)\b", source)
            or re.search(rf"^{name}\s*[:—–-]", source)
            or _negated_location(source, name)
            or re.search(rf"\b(?:no|nunca)\s+en\s+{qualified_name}", source)
            or re.search(rf"\b(?:no|nunca|sin)\b[^.;!?]{{0,55}}\b{operation}\s+(?:a|en|sobre)\s+{qualified_name}", source)):
        return False
    explicit_name = rf"\bdepartamento\s+de\s+{name}\b"
    if explicit and not re.search(explicit_name, source):
        return False
    if re.search(rf"\ben\s+{qualified_name}", source):
        return True
    if re.search(rf"\b{operation}\s+(?:a|en|sobre)\s+{qualified_name}", source):
        return True
    # A locative administrative chain, with or without an explicit place type:
    # "en Victoria, departamento de Cabañas". Require the exact official pair;
    # arbitrary words and municipalities from a different department fail.
    if explicit:
        chain = (rf"\ben\s+(?:el\s+|la\s+)?(?:(?:municipio|distrito|ciudad|localidad)\s+de\s+)?"
                 rf"(?P<place>[a-z]+(?:\s+[a-z]+){{0,5}})\s*,\s*(?:en\s+)?(?:el\s+)?{explicit_name}")
        for match in re.finditer(chain, source):
            place = match.group("place")
            named_place = re.escape(place)
            if (resolve_municipality(place, department) is not None
                    and not _negated_location(source, named_place)
                    and not re.search(rf"\b(?:no|nunca)\s+en\s+{named_place}\b", source)
                    and not re.search(rf"\b(?:no|nunca|sin)\b[^.;!?]{{0,55}}\b{operation}\s+(?:a|en|sobre)\s+{named_place}\b", source)):
                return True
    return False


def _source_paragraph(candidate, article):
    reference = candidate["reference"]
    if (not isinstance(article, dict) or article.get("source_url") != reference["source_url"]
            or article.get("published_date") != reference["published_date"]
            or article.get("content_sha256") != candidate["digest"]
            or not isinstance(article.get("text"), str) or len(article["text"]) > 24_000
            or hashlib.sha256(article["text"].encode()).hexdigest() != candidate["digest"]
            or not isinstance(article.get("paragraphs"), list) or not 1 <= len(article["paragraphs"]) <= 160):
        return None
    paragraphs = {}
    for number, paragraph in enumerate(article["paragraphs"], 1):
        if (not isinstance(paragraph, dict) or paragraph.get("id") != f"p{number}"
                or not isinstance(paragraph.get("text"), str) or not 1 <= len(paragraph["text"]) <= 4000):
            return None
        paragraphs[paragraph["id"]] = paragraph["text"]
    if "\n\n".join(paragraphs.values()) != article["text"]:
        return None
    if candidate["department"]:
        explicit = re.compile(r"\bdepartamento\s+de\s+" + re.escape(normalize(candidate["department"])) + r"\b")
        for identifier in candidate["department_paragraphs"]:
            normalized = " ".join(normalize(paragraphs.get(identifier, "")).split())
            if explicit.search(normalized) and not _negated_location(normalized, re.escape(normalize(candidate["department"]))):
                return "gemini_explicit_department", identifier, candidate["department"]
    if candidate["municipal_place"]:
        for identifier in candidate["municipal_paragraphs"]:
            if identifier in paragraphs and _municipal_context(paragraphs[identifier], candidate):
                return MUNICIPAL_METHOD, identifier, candidate["municipal_place"]["department"]
    if candidate["department"]:
        for model_id in candidate["department_paragraphs"]:
            if not _department_context(paragraphs.get(model_id, ""), candidate["department"]):
                continue
            for explicit_id, text in paragraphs.items():
                if explicit_id != model_id and _department_context(text, candidate["department"], explicit=True):
                    return CORROBORATED_METHOD, explicit_id, candidate["department"], model_id
    return None


def project_automatic_locations(drafts, archive, *, previous=None, fetch=fetch_article,
                                limit=20, now=None, excluded_ids=()):
    """Return a minimal snapshot and aggregate counters; fetch at most ``limit`` sources.

    A prior source check is reusable only while the current private draft still
    qualifies and its exact department, source hash, citation and metadata match.
    Removed/changed/private-protected drafts are withdrawn, even with no fetch budget.
    """
    if type(limit) is not int or not 1 <= limit <= 100:
        raise AutomaticLocationError("invalid_location_limit")
    if not isinstance(drafts, (list, tuple)) or len(drafts) > MAX_ENTRIES:
        raise AutomaticLocationError("invalid_location_drafts")
    if (not isinstance(excluded_ids, (list, tuple, set))
            or any(not isinstance(value, str) or not HEX.fullmatch(value) for value in excluded_ids)):
        raise AutomaticLocationError("invalid_location_exclusions")
    excluded_ids = set(excluded_ids)
    moment = now or datetime.now(timezone.utc)
    stamp = _timestamp(moment.isoformat())
    references = _references(archive)
    previous = validate_collection(previous) if previous is not None else {"locations": []}
    # A v1 positive predates context/negation and municipality checks. Revalidate
    # it once rather than carrying an old acceptance across changed rules.
    cache = {item["candidate_id"]: item for item in previous["locations"]} if previous.get("schema_version") in {2, 3} else {}
    candidates, identifiers = [], set()
    stats = {"drafts": len(drafts), "eligible": 0, "excluded": 0, "invalid": 0, "cached": 0,
             "sources_checked": 0, "source_failures": 0, "unsupported": 0, "deferred": 0,
             "rotation_offset": 0, "rotation_candidates": 0,
             "locations": 0, "withdrawn": 0, "case_publications": 0, "dossier_publications": 0}
    for draft in drafts:
        identifier = draft.get("candidate_id") if isinstance(draft, dict) else None
        if identifier is not None and (not isinstance(identifier, str) or not HEX.fullmatch(identifier)):
            stats["invalid"] += 1
            continue
        if identifier in identifiers:
            raise AutomaticLocationError("duplicate_location_draft")
        if identifier is not None:
            identifiers.add(identifier)
        if identifier in excluded_ids:
            stats["excluded"] += 1
            continue
        try:
            candidate = _eligible(draft, references)
        except (ValueError, TypeError, KeyError, AttributeError):
            stats["invalid"] += 1
            continue
        if candidate is None:
            stats["excluded"] += 1
        else:
            candidates.append(candidate)
    stats["eligible"] = len(candidates)
    locations = []
    ordered = sorted(candidates, key=lambda value: (value["reference"]["published_date"], value["reference"]["id"]))
    cached_ids = set()
    for candidate in ordered:
        cached = cache.get(candidate["reference"]["id"])
        method = cached["method"] if cached else None
        eligible_paragraphs = candidate["municipal_paragraphs"] if method == MUNICIPAL_METHOD else candidate["department_paragraphs"]
        expected_department = candidate["municipal_place"]["department"] if method == MUNICIPAL_METHOD and candidate["municipal_place"] else candidate["department"]
        selection = (method, cached["evidence"]["paragraph_id"], expected_department) if cached else None
        if method == CORROBORATED_METHOD:
            model_id = cached["corroboration"]["model_paragraph_id"]
            belongs_to_model = model_id in eligible_paragraphs
            selection += (model_id,)
        else:
            belongs_to_model = cached and cached["evidence"]["paragraph_id"] in eligible_paragraphs
        if cached and belongs_to_model and cached == _entry(candidate, selection, cached["source_checked_at"]):
            locations.append(cached)
            cached_ids.add(candidate["reference"]["id"])
            stats["cached"] += 1
    # Persist only positive public projections, but do not let an old permanently
    # inaccessible/unsupported source consume the same first slots every hour.
    # Consecutive hourly blocks cover the unresolved candidates without storing
    # article text or private failure metadata in the public snapshot.
    # Use all eligible candidates for the index, including cached positives: a
    # new positive must not shift the modulus and skip a later unvisited block.
    # Manual reruns in the same hour use the same block. With a stable eligible
    # set, consecutive hourly blocks cover every candidate in ceil(N/limit) runs.
    if ordered:
        offset = (int(moment.timestamp() // 3600) * limit) % len(ordered)
        stats.update(rotation_offset=offset, rotation_candidates=len(ordered))
        ordered = ordered[offset:] + ordered[:offset]
    unchecked = [candidate for candidate in ordered if candidate["reference"]["id"] not in cached_ids]
    for candidate in unchecked:
        if stats["sources_checked"] >= limit:
            stats["deferred"] += 1
            continue
        stats["sources_checked"] += 1
        try:
            reference = candidate["reference"]
            article = fetch({**reference, "source_title": reference["title"]})
            paragraph = _source_paragraph(candidate, article)
        except Exception:
            # Network/parsing exceptions can carry publisher HTML or private paths.
            # This projection only emits an aggregate count, never that exception.
            stats["source_failures"] += 1
            continue
        if paragraph is None:
            stats["unsupported"] += 1
            continue
        checked_at = stamp if now is not None else datetime.now(timezone.utc).isoformat()
        locations.append(_entry(candidate, paragraph, checked_at))
    generated_at = stamp if now is not None else datetime.now(timezone.utc).isoformat()
    document = validate_collection({"schema_version": 3, "generated_at": generated_at, "locations": locations}, archive=archive)
    stats["locations"] = len(locations)
    stats["withdrawn"] = len({item["candidate_id"] for item in previous["locations"]} - {item["candidate_id"] for item in locations})
    # A stable snapshot avoids deploying on an unchanged extraction run.
    if previous.get("schema_version") in {2, 3} and previous.get("generated_at") and document["locations"] == previous["locations"]:
        document["generated_at"] = previous["generated_at"]
    return document, stats


def _read_json(path, maximum):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum:
        raise AutomaticLocationError("invalid_location_file")
    return json.loads(path.read_text(encoding="utf-8"))


def export_automatic_locations(drafts_dir, archive_path, output_path, *, limit=20,
                               fetch=fetch_article, now=None, excluded_ids=()):
    """Atomic local export; source bodies and private envelopes are never copied."""
    try:
        drafts_dir, archive_path, output_path = Path(drafts_dir), Path(archive_path), Path(output_path)
        if not drafts_dir.is_dir() or drafts_dir.is_symlink():
            raise AutomaticLocationError("invalid_location_drafts_directory")
        archive = _read_json(archive_path, MAX_ARCHIVE_BYTES)
        previous = _read_json(output_path, MAX_ARCHIVE_BYTES) if output_path.exists() else None
        files = sorted(drafts_dir.glob("*.json"))
        if len(files) > MAX_ENTRIES:
            raise AutomaticLocationError("invalid_location_drafts")
        drafts = []
        for path in files:
            if not HEX.fullmatch(path.stem):
                raise AutomaticLocationError("invalid_location_draft_filename")
            draft = _read_json(path, MAX_DRAFT_BYTES)
            if not isinstance(draft, dict) or draft.get("candidate_id") != path.stem:
                raise AutomaticLocationError("invalid_location_draft_identity")
            drafts.append(draft)
        document, stats = project_automatic_locations(drafts, archive, previous=previous, fetch=fetch,
                                                      limit=limit, now=now, excluded_ids=excluded_ids)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output_path.parent,
                                             prefix=".locations-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
            temporary.replace(output_path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return stats
    except (ValueError, TypeError, KeyError, OSError):
        raise AutomaticLocationError("automatic_location_export_failed") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--drafts", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--excluded-ids", type=Path, help="Private JSON array of approved/rejected candidate identities to withdraw")
    args = parser.parse_args()
    try:
        excluded = _read_json(args.excluded_ids, MAX_ARCHIVE_BYTES) if args.excluded_ids else []
        if not isinstance(excluded, list):
            raise AutomaticLocationError("invalid_location_exclusions")
        report = export_automatic_locations(args.drafts, args.archive, args.output, limit=args.limit, excluded_ids=excluded)
    except (ValueError, TypeError, KeyError, OSError):
        raise SystemExit("No se exportaron ubicaciones: entrada o proyección inválida; revise los archivos privados.") from None
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
