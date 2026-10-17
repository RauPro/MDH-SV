"""Project coarse administrative source evidence without publishing private dossiers.

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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from mdh_schema import DepartmentEnum

from .article_source import fetch_article
from .history import SOURCES, validate_reference
from .news_dossier import FIELD_NAMES, validate_news_dossier
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
SOURCE_SINGLE_METHODS = {"source_explicit_department", "source_municipality_reference"}
MULTI_SOURCE_METHOD = "source_administrative_mentions"
SOURCE_METHODS = SOURCE_SINGLE_METHODS | {MULTI_SOURCE_METHOD}
SOURCE_LOCATION_RULE_VERSION = 2
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
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)", value):
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
                or type(document["schema_version"]) not in (int, float) or document["schema_version"] not in {1, 2, 3, 4, 5}
                or not isinstance(document["locations"], list) or len(document["locations"]) > MAX_ENTRIES):
            raise ValueError()
        _timestamp(document["generated_at"])
        references = _references(archive) if archive is not None else None
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
                    or not _calendar_date(entry["published_date"])
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
                    _validate_source_mention(mention)
                    if mention["department"] in departments:
                        raise ValueError()
                    departments.add(mention["department"])
            if municipal:
                resolution = entry["resolution"]
                if not isinstance(resolution, dict) or set(resolution) != RESOLUTION_KEYS:
                    raise ValueError()
                place = resolve_municipality(resolution["municipality"], entry["department"])
                if place is None or resolution != _resolution(place):
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
            _timestamp(entry["extracted_at"])
            _timestamp(entry["source_checked_at"])
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
                if "selection" in reference and _selection_hash(reference) != evidence["content_sha256"]:
                    raise ValueError()
            identifiers.add(entry["candidate_id"])
            urls.add(entry["source_url"])
        return {"schema_version": document["schema_version"], "generated_at": document["generated_at"],
                "locations": sorted(document["locations"], key=lambda value: value["candidate_id"])}
    except (ValueError, TypeError, KeyError, AttributeError):
        raise AutomaticLocationError() from None


def _validate_source_mention(mention):
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
        if place is None or resolution != _resolution(place):
            raise ValueError()


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


def _checked_paragraphs(candidate, article):
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
    return paragraphs


def _without_quoted_counterfactuals(text):
    """A complete quoted counterfactual supplies neither a place nor its context."""
    quotes = r'“[^”\n]+”|«[^»\n]+»|"[^"\n]+"|‘[^’\n]+’'
    def mask(match):
        content = match[0][1:-1].strip().lstrip("¿¡").strip()
        # Do not hide an asserted sentence that happens to share a quotation
        # with a hypothesis. Periods inside the country abbreviation are not
        # sentence boundaries.
        sentences = re.sub(r"\bee\.?\s?uu\.?", "eeuu", content)
        conditional = re.match(
            r"si\b[^.!?;]{0,120}\b(?:tuviera[n]?|hubiera[n]?|fuera[n]?|"
            r"estuviera[n]?|existiera[n]?|ocurriera[n]?|sucediera[n]?)\b", content)
        if conditional and not re.search(r"[.!?;].*\w", sentences):
            return " " * len(match[0])
        return match[0]
    return re.sub(quotes, mask, text)


def _official_pair_at_anchor(clause, name, end):
    """Only the exact administrative occurrence can establish a local pair."""
    pair = re.search(
        r"\ben\s+(?:el\s+|la\s+)?(?:(?:municipio|distrito|ciudad|localidad)\s+de\s+)?"
        r"(?P<place>[a-z]+(?:\s+[a-z]+){0,5})\s*,\s*(?:en\s+)?(?:el\s+)?"
        r"departamento\s+de\s+" + re.escape(normalize(name)) + r"$", clause[:end])
    return bool(pair and resolve_municipality(pair["place"], name) is not None
                and _department_context(clause, name, explicit=True))


def _temporary_refugee_return(clause):
    """A bounded past stay with an explicit return is not a relocation claim."""
    from .source_location_evidence import _FOREIGN
    return bool(re.fullmatch(
        r"\s*(?:vivio|vivieron|estuvo|estuvieron)\s+(?:un tiempo|temporalmente)\s+en\s+"
        r"[^.!?;]{1,180}\brefugiados\s+en\s+" + _FOREIGN.pattern +
        r"\s*,?\s+y\s+(?:regreso|regresaron|retorno|retornaron)\s+a\s+(?:su|la)\s+"
        r"comunidad(?:\s+[a-z]+){0,3}(?:\s+en\s+\d{4})?\s*", clause))


def _model_anchor_allowed(text, name, *, explicit_department=False, municipality=None):
    """An old model proposal cannot override an explicit source-context veto.

    Check only the cited paragraph: a foreign comparison elsewhere in the article
    does not relocate an otherwise supported Salvadoran administrative mention.
    """
    from .source_location_evidence import (
        _FOREIGN, _clean, _excluded_context, _unresolved_narrative_department_link,
    )
    normalized, place = _without_quoted_counterfactuals(_clean(text)), normalize(name)
    # A locative country statement can govern the next clause in this paragraph.
    # A mere comparison ("the article also mentions Honduras") cannot do so.
    foreign_locations = list(re.finditer(
        r"\b(?:en|dentro de)\s+(?:(?:el|la|los|las)\s+)?(?:pais de\s+)?" + _FOREIGN.pattern, normalized))
    clauses = list(re.finditer(r"[^.!?;]+", normalized))
    for section in clauses:
        clause = section[0]
        if _FOREIGN.search(clause) or _unresolved_narrative_department_link(clause):
            continue
        matches = list(re.finditer(r"\b" + re.escape(place) + r"\b", clause))
        for match in matches:
            if explicit_department and not re.search(r"\bdepartamento\s+de\s+$", clause[:match.start()]):
                continue
            if _excluded_context(clause, match.start(), match.end(), place):
                continue
            if foreign_locations:
                # A named official pair followed by a separate temporary
                # refugee stay and return remains anchored to El Salvador.
                # Earlier foreign context or any other asserted foreign event
                # still governs/vetoes the paragraph, including "sucedió en".
                if not explicit_department or not _official_pair_at_anchor(clause, name, match.end()):
                    continue
                if any(not any(other.start() > section.end()
                               and other.start() <= foreign.start() < foreign.end() <= other.end()
                               and _temporary_refugee_return(other[0]) for other in clauses)
                       for foreign in foreign_locations):
                    continue
            if explicit_department:
                return True
            # Evaluate this occurrence's context only. An unrelated repetition
            # of a name must not legitimize a forbidden institutional mention.
            focused = clause
            for other in reversed(matches):
                if other is not match:
                    focused = focused[:other.start()] + ("~" * (other.end() - other.start())) + focused[other.end():]
            if (_municipal_context(focused, municipality) if municipality is not None
                    else _department_context(focused, name)):
                return True
    return False


def _source_paragraph(candidate, article):
    paragraphs = _checked_paragraphs(candidate, article)
    if paragraphs is None:
        return None
    if candidate["department"]:
        explicit = re.compile(r"\bdepartamento\s+de\s+" + re.escape(normalize(candidate["department"])) + r"\b")
        for identifier in candidate["department_paragraphs"]:
            text = paragraphs.get(identifier, "")
            normalized = " ".join(normalize(text).split())
            if (explicit.search(normalized) and _model_anchor_allowed(text, candidate["department"], explicit_department=True)
                    and not _negated_location(normalized, re.escape(normalize(candidate["department"])))):
                return "gemini_explicit_department", identifier, candidate["department"]
    if candidate["municipal_place"]:
        for identifier in candidate["municipal_paragraphs"]:
            if (identifier in paragraphs and _model_anchor_allowed(paragraphs[identifier], candidate["municipal_place"]["name"], municipality=candidate)
                    and _municipal_context(paragraphs[identifier], candidate)):
                return MUNICIPAL_METHOD, identifier, candidate["municipal_place"]["department"]
    if candidate["department"]:
        for model_id in candidate["department_paragraphs"]:
            text = paragraphs.get(model_id, "")
            if not _model_anchor_allowed(text, candidate["department"]) or not _department_context(text, candidate["department"]):
                continue
            for explicit_id, text in paragraphs.items():
                if (explicit_id != model_id and _model_anchor_allowed(text, candidate["department"], explicit_department=True)
                        and _department_context(text, candidate["department"], explicit=True)):
                    return CORROBORATED_METHOD, explicit_id, candidate["department"], model_id
    return None


def _selection_hash(reference):
    from .news_selection import validate_selection
    if "selection" not in reference:
        return None
    selection = validate_selection(reference["selection"])
    if selection["method"] != "gemini_grounded_scope":
        return None  # Human geography is managed by the editorial projection.
    return selection["evidence"]["content_sha256"]


def _independent_draft_allowed(draft, reference):
    """A full dossier is optional; an explicit privacy/foreign-country block is not."""
    if draft is None:
        return True
    if (type(draft.get("schema_version")) is not int or draft["schema_version"] != 1
            or draft.get("candidate_id") != reference["id"] or draft.get("source_url") != reference["source_url"]
            or draft.get("status") not in {"draft", "needs_scope_review", "needs_privacy_review", "pending", "source_unavailable", "error"}
            or draft.get("human_reviewed") is not False or draft.get("publishable") is not False):
        return False
    extraction = draft.get("extraction", {})
    protected = extraction.get("protected_fields", [])
    flags = draft.get("privacy_flags", [])
    if (not isinstance(protected, list) or any(not isinstance(value, str) or value not in {*FIELD_NAMES, "title", "penitentiary_related"} for value in protected)
            or not isinstance(flags, list) or any(value not in {
                "minor_identifiers", "clinical_details", "precise_address", "private_contact"} for value in flags)):
        return False
    if (flags or draft.get("status") == "needs_privacy_review") and (
            type(extraction.get("privacy_policy_version")) is not int or extraction["privacy_policy_version"] != 2):
        return False
    if "department" in protected:
        return False
    if draft.get("news_dossier") is None:
        return True
    news = validate_news_dossier(draft["news_dossier"])
    if (news["human_reviewed"] or news["candidate_id"] != reference["id"]
            or draft.get("classification") != news["classification"]
            or any(news[key] != reference[key] for key in ("source_url", "publisher", "published_date"))):
        return False
    if news["fields"]["department"]["status"] == "withheld":
        return False
    country = news["fields"]["country"]
    return country["status"] != "reported" or normalize(country["value"]).strip() in {"el salvador", "republica de el salvador"}


def _source_entry(candidate, mention, checked_at):
    reference = candidate["reference"]
    result = {"candidate_id": reference["id"],
        **{key: reference[key] for key in ("source_url", "publisher", "published_date")},
        "department": mention["department"], "review_status": "automated_unreviewed", "human_reviewed": False,
        "method": mention["method"], "rule_version": SOURCE_LOCATION_RULE_VERSION,
        "extracted_at": checked_at, "source_checked_at": checked_at,
        "evidence": {"paragraph_id": mention["paragraph_id"], "content_sha256": candidate["digest"]}}
    if "resolution" in mention:
        result["resolution"] = mention["resolution"]
    return result


def _source_mentions_entry(candidate, mentions, checked_at):
    if len(mentions) == 1:
        return _source_entry(candidate, mentions[0], checked_at)
    reference = candidate["reference"]
    return {"candidate_id": reference["id"],
        **{key: reference[key] for key in ("source_url", "publisher", "published_date")},
        "review_status": "automated_unreviewed", "human_reviewed": False,
        "method": MULTI_SOURCE_METHOD, "rule_version": SOURCE_LOCATION_RULE_VERSION,
        "extracted_at": checked_at, "source_checked_at": checked_at,
        "evidence": {"content_sha256": candidate["digest"]},
        "mentions": sorted(mentions, key=lambda item: item["department"])}


def validate_negative_cache(document, *, now=None):
    """Private cache stores only identity, source hash, a bounded retry and status."""
    try:
        if (not isinstance(document, dict) or set(document) != {"schema_version", "entries"}
                or type(document["schema_version"]) not in (int, float) or document["schema_version"] != 1
                or not isinstance(document["entries"], list) or len(document["entries"]) > MAX_ENTRIES):
            raise ValueError()
        instant = now or datetime.now(timezone.utc)
        seen = set()
        for entry in document["entries"]:
            if (not isinstance(entry, dict) or set(entry) != {
                    "id", "selectionhash", "checked_at", "next_retry_at", "status", "rule_version"}
                    or not isinstance(entry["id"], str) or not HEX.fullmatch(entry["id"])
                    or not isinstance(entry["selectionhash"], str) or not HEX.fullmatch(entry["selectionhash"])
                    or entry["status"] not in {"unresolved", "source_unavailable"}
                    or type(entry["rule_version"]) not in (int, float) or not 1 <= entry["rule_version"] <= 1000
                    or entry["id"] in seen):
                raise ValueError()
            checked = datetime.fromisoformat(_timestamp(entry["checked_at"]).replace("Z", "+00:00"))
            retry = datetime.fromisoformat(_timestamp(entry["next_retry_at"]).replace("Z", "+00:00"))
            durations = {timedelta(days=1)} if entry["status"] == "unresolved" else {timedelta(days=1), timedelta(days=7)}
            if checked > instant or retry - checked not in durations:
                raise ValueError()
            seen.add(entry["id"])
        return {"schema_version": 1, "entries": [dict(entry) for entry in document["entries"]]}
    except (ValueError, TypeError, KeyError, AttributeError):
        raise AutomaticLocationError("invalid_negative_location_cache") from None


def project_automatic_locations(drafts, archive, *, previous=None, fetch=fetch_article,
                                limit=20, now=None, excluded_ids=(), negative_cache=None):
    """Project coarse source evidence with one shared read budget, never dossiers.

    Current automatic selection authorizes independent source inspection. Legacy
    references without selection retain the existing dossier-based route. A caller
    may provide a mutable, private negative-cache document; it never enters output.
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
    private_cache = validate_negative_cache(negative_cache, now=moment) if negative_cache is not None else {"schema_version": 1, "entries": []}
    previous = validate_collection(previous) if previous is not None else {"locations": []}
    positives = {item["candidate_id"]: item for item in previous["locations"]} if previous.get("schema_version") in {2, 3, 4, 5} else {}
    stats = {"drafts": len(drafts), "selected_references": len(references), "eligible": 0, "excluded": 0, "invalid": 0, "cached": 0,
             "sources_checked": 0, "source_failures": 0, "unsupported": 0, "deferred": 0,
             "rotation_offset": 0, "rotation_candidates": 0, "ready_without_dossier": 0,
             "negative_cached": 0, "negative_cache_discarded": 0,
             "source_methods": {method: 0 for method in sorted(SOURCE_METHODS)},
             "locations": 0, "withdrawn": 0, "case_publications": 0, "dossier_publications": 0}
    by_id = {}
    for draft in drafts:
        identifier = draft.get("candidate_id") if isinstance(draft, dict) else None
        if not isinstance(identifier, str) or not HEX.fullmatch(identifier):
            stats["invalid"] += 1
            continue
        if identifier in by_id:
            raise AutomaticLocationError("duplicate_location_draft")
        by_id[identifier] = draft
        if identifier not in references:
            stats["excluded"] += 1
    candidates = []
    for identifier, reference in references.items():
        draft = by_id.get(identifier)
        if identifier in excluded_ids:
            stats["excluded"] += 1
            continue
        try:
            if "selection" in reference:
                digest = _selection_hash(reference)
                if digest is None or not _independent_draft_allowed(draft, reference):
                    stats["excluded"] += 1
                    continue
                try:
                    model = _eligible(draft, references) if draft is not None else None
                except (ValueError, TypeError, KeyError, AttributeError):
                    model = None
                if model and model["digest"] != digest:
                    model = None
                candidate = model or {"reference": reference, "department": None, "digest": digest,
                    "extracted_at": reference["selection"]["classified_at"], "department_paragraphs": [],
                    "municipal_place": None, "municipal_paragraphs": [], "municipality": None,
                    "ambiguous_municipality": False}
                candidate.update(independent=True, has_model=model is not None)
                candidate["municipality_protected"] = bool(draft and (
                    "municipality" in draft.get("extraction", {}).get("protected_fields", [])
                    or draft.get("news_dossier", {}).get("fields", {}).get("municipality", {}).get("status") == "withheld"))
                if draft is None or draft.get("news_dossier", {}).get("extraction_status") != "processed":
                    stats["ready_without_dossier"] += 1
            else:
                candidate = _eligible(draft, references) if draft is not None else None
                if candidate:
                    candidate.update(independent=False, has_model=True)
            if candidate is None:
                if draft is not None:
                    stats["excluded"] += 1
                continue
            candidates.append(candidate)
        except (ValueError, TypeError, KeyError, AttributeError):
            stats["invalid"] += 1
    stats["eligible"] = len(candidates)
    current = {item["reference"]["id"]: item for item in candidates}
    negatives = {item["id"]: item for item in private_cache["entries"]
                 if item["id"] in current and item["selectionhash"] == current[item["id"]]["digest"]
                 and item["rule_version"] == SOURCE_LOCATION_RULE_VERSION}
    locations, cached_ids = [], set()
    ordered = sorted(candidates, key=lambda value: (value["reference"]["published_date"], value["reference"]["id"]))
    for candidate in ordered:
        identifier = candidate["reference"]["id"]
        cached = positives.get(identifier)
        if not cached or cached["evidence"]["content_sha256"] != candidate["digest"]:
            continue
        if any(cached[key] != candidate["reference"][key] for key in ("source_url", "publisher", "published_date")):
            continue
        method = cached["method"]
        reusable = False
        if method in SOURCE_METHODS:
            reusable = candidate["independent"] and cached["rule_version"] == SOURCE_LOCATION_RULE_VERSION
            has_municipality = method == "source_municipality_reference" or (method == MULTI_SOURCE_METHOD
                and any(item["method"] == "source_municipality_reference" for item in cached["mentions"]))
            if has_municipality and candidate.get("municipality_protected"):
                reusable = False
        elif candidate["has_model"]:
            paragraphs = candidate["municipal_paragraphs"] if method == MUNICIPAL_METHOD else candidate["department_paragraphs"]
            department = candidate["municipal_place"]["department"] if method == MUNICIPAL_METHOD and candidate["municipal_place"] else candidate["department"]
            selection = (method, cached["evidence"]["paragraph_id"], department)
            if method == CORROBORATED_METHOD:
                model_id = cached["corroboration"]["model_paragraph_id"]
                belongs = model_id in paragraphs
                selection += (model_id,)
            else:
                belongs = cached["evidence"]["paragraph_id"] in paragraphs
            reusable = belongs and cached == _entry(candidate, selection, cached["source_checked_at"])
        if reusable:
            locations.append(cached)
            cached_ids.add(identifier)
            negatives.pop(identifier, None)
            stats["cached"] += 1
    # Rotate all eligible references, so new positives cannot starve the tail.
    if ordered:
        offset = (int(moment.timestamp() // 3600) * limit) % len(ordered)
        stats.update(rotation_offset=offset, rotation_candidates=len(ordered))
        ordered = ordered[offset:] + ordered[:offset]
    for candidate in ordered:
        identifier = candidate["reference"]["id"]
        if identifier in cached_ids:
            continue
        negative = negatives.get(identifier)
        if negative and datetime.fromisoformat(negative["next_retry_at"].replace("Z", "+00:00")) > moment:
            stats["negative_cached"] += 1
            continue
        if stats["sources_checked"] >= limit:
            stats["deferred"] += 1
            continue
        stats["sources_checked"] += 1
        failure = None
        location = None
        checked = moment if now is not None else datetime.now(timezone.utc)
        try:
            reference = candidate["reference"]
            article = fetch({**reference, "source_title": reference["title"]})
            checked = moment if now is not None else datetime.now(timezone.utc)
            paragraphs = _checked_paragraphs(candidate, article)
            if paragraphs is not None:
                mentions = []
                conflict = False
                if candidate["independent"]:
                    from .source_location_evidence import extract_administrative_mentions, has_administrative_conflict
                    conflict = has_administrative_conflict(article)
                    mentions = extract_administrative_mentions(article, allow_municipality=not candidate.get("municipality_protected", False))
                    if (not all(mention.get("paragraph_id") in paragraphs for mention in mentions)
                            or (candidate.get("municipality_protected") and any(
                                mention.get("method") == "source_municipality_reference" for mention in mentions))):
                        mentions = []
                match = _source_paragraph(candidate, article) if candidate["has_model"] and not conflict else None
                # One model proposal must not hide other independently supported
                # departments. A damaged administrative chain is never repaired
                # by falling back to an otherwise literal model citation.
                if not conflict and len(mentions) > 1:
                    location = _source_mentions_entry(candidate, mentions, checked.isoformat())
                elif match:
                    location = _entry(candidate, match, checked.isoformat())
                elif not conflict and mentions:
                    location = _source_mentions_entry(candidate, mentions, checked.isoformat())
        except Exception as error:
            # Exceptions can carry private source text. Only this enum affects retry.
            failure = "restricted" if getattr(error, "code", None) == "source_access_restricted" else "unavailable"
            stats["source_failures"] += 1
        if location is not None:
            locations.append(location)
            negatives.pop(identifier, None)
            if location["method"] in SOURCE_METHODS:
                stats["source_methods"][location["method"]] += 1
        else:
            if failure is None:
                stats["unsupported"] += 1
            negatives[identifier] = {"id": identifier, "selectionhash": candidate["digest"],
                "checked_at": checked.isoformat(), "next_retry_at": (checked + timedelta(days=7 if failure == "restricted" else 1)).isoformat(),
                "status": "source_unavailable" if failure else "unresolved", "rule_version": SOURCE_LOCATION_RULE_VERSION}
    generated_at = stamp if now is not None else datetime.now(timezone.utc).isoformat()
    document = validate_collection({"schema_version": 5, "generated_at": generated_at, "locations": locations}, archive=archive)
    stats["locations"] = len(locations)
    stats["withdrawn"] = len({item["candidate_id"] for item in previous["locations"]} - {item["candidate_id"] for item in locations})
    if previous.get("schema_version") in {2, 3, 4, 5} and previous.get("generated_at") and document["locations"] == previous["locations"]:
        document["generated_at"] = previous["generated_at"]
    if negative_cache is not None:
        clean = validate_negative_cache({"schema_version": 1, "entries": sorted(negatives.values(), key=lambda item: item["id"])}, now=now)
        negative_cache.clear()
        negative_cache.update(clean)
    return document, stats


def _read_json(path, maximum):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum:
        raise AutomaticLocationError("invalid_location_file")
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_json(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".locations-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def export_automatic_locations(drafts_dir, archive_path, output_path, *, limit=20,
                               fetch=fetch_article, now=None, excluded_ids=(), cache_file=None):
    """Atomic local export; source bodies and private envelopes are never copied."""
    try:
        drafts_dir = Path(drafts_dir) if drafts_dir is not None else None
        archive_path, output_path = Path(archive_path), Path(output_path)
        if drafts_dir is not None and (not drafts_dir.is_dir() or drafts_dir.is_symlink()):
            raise AutomaticLocationError("invalid_location_drafts_directory")
        archive = _read_json(archive_path, MAX_ARCHIVE_BYTES)
        previous = _read_json(output_path, MAX_ARCHIVE_BYTES) if output_path.exists() else None
        files = sorted(drafts_dir.glob("*.json")) if drafts_dir is not None else []
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
        private_cache, discarded = None, 0
        cache_path = Path(cache_file) if cache_file is not None else None
        if cache_path is not None:
            if cache_path.resolve() == output_path.resolve() or "public" in cache_path.resolve().parts:
                raise AutomaticLocationError("location_cache_must_be_private")
            private_cache = {"schema_version": 1, "entries": []}
            if cache_path.exists() or cache_path.is_symlink():
                try:
                    private_cache = validate_negative_cache(_read_json(cache_path, MAX_ARCHIVE_BYTES), now=now)
                except (ValueError, TypeError, KeyError, OSError):
                    discarded = 1  # A corrupt performance cache is not publication authority.
        document, stats = project_automatic_locations(drafts, archive, previous=previous, fetch=fetch,
            limit=limit, now=now, excluded_ids=excluded_ids, negative_cache=private_cache)
        stats["negative_cache_discarded"] = discarded
        _atomic_json(output_path, document)
        if cache_path is not None:
            try:
                _atomic_json(cache_path, private_cache)
            except OSError:
                stats["negative_cache_discarded"] = 1
        return stats
    except (ValueError, TypeError, KeyError, OSError):
        raise AutomaticLocationError("automatic_location_export_failed") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--drafts", type=Path, required=True,
                        help="Private directory for privacy controls; individual sources do not require a dossier")
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--excluded-ids", type=Path, help="Private JSON array of approved/rejected candidate identities to withdraw")
    parser.add_argument("--cache-file", type=Path, help="Optional PRIVATE cache of unresolved or unavailable source checks")
    args = parser.parse_args()
    try:
        excluded = _read_json(args.excluded_ids, MAX_ARCHIVE_BYTES) if args.excluded_ids else []
        if not isinstance(excluded, list):
            raise AutomaticLocationError("invalid_location_exclusions")
        report = export_automatic_locations(args.drafts, args.archive, args.output, limit=args.limit,
                                            excluded_ids=excluded, cache_file=args.cache_file)
    except (ValueError, TypeError, KeyError, OSError):
        raise SystemExit("No se exportaron ubicaciones: entrada o proyección inválida; revise los archivos privados.") from None
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
