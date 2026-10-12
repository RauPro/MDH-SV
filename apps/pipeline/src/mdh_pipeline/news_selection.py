"""Publish minimal scope decisions, never private news worksheets or quotations."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re

_HEX = r"[a-f0-9]{64}"
_PARAGRAPH = r"p[1-9][0-9]{0,2}"
_MODEL = r"gemini-[A-Za-z0-9_.-]{1,90}"
FULL_SCOPE_PROMPT = "mdh-universal-news-grounded-8-relevance-scope"
SCOPE_REVIEW_PROMPT = "mdh-news-scope-1"
_SELECTION_PROMPTS = {FULL_SCOPE_PROMPT, SCOPE_REVIEW_PROMPT}
_DOSSIER_PROMPTS = {FULL_SCOPE_PROMPT, "mdh-private-grounded-5", "mdh-universal-news-grounded-6",
                    "mdh-universal-news-grounded-7-field-privacy"}
_STAMP = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})"
_COMMON = {"method", "review_status", "human_reviewed", "jurisdiction", "relevance"}
_AUTOMATIC = _COMMON | {"classified_at", "model", "prompt_version", "evidence"}
_HUMAN = _COMMON | {"reviewed_by", "reviewed_at"}
_ENTRY = {"candidate_id", "source_url", "publisher", "published_date", "selection"}
MAX_SELECTIONS = 50_000
MAX_DRAFT_BYTES = 512_000
MAX_JSON_BYTES = 30_000_000


def _fail():
    raise ValueError("Invalid news selection or bibliography binding")


def _matches(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def _timestamp(value):
    if not _matches(_STAMP, value):
        _fail()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail()


def _day(value):
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
        _timestamp(value["classified_at"])
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
        _day(value["reviewed_at"])
    else:
        _fail()
    return deepcopy(value)


def _archive_index(archive):
    # Local import allows history.export_archive to call select_archive safely.
    from .history import validate_reference
    from .publishers import SOURCES
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


def validate_manifest(document, archive=None):
    """A stale or malformed manifest fails as a unit, without echoing its content."""
    from .review_queue import canonical_url
    if (not isinstance(document, dict) or set(document) != {"schema_version", "generated_at", "selections"}
            or type(document["schema_version"]) is not int or document["schema_version"] != 1
            or not isinstance(document["selections"], list) or len(document["selections"]) > MAX_SELECTIONS):
        _fail()
    generated = _timestamp(document["generated_at"])
    references = _archive_index(archive) if archive is not None else None
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
        published = _day(entry["published_date"])
        selection = validate_selection(entry["selection"])
        # A manifest carries automated decisions only. Human decisions must
        # come from validate_collection(require_reviewed) inside select_archive.
        if selection["method"] != "gemini_grounded_scope":
            _fail()
        classified = _timestamp(selection["classified_at"])
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
    if (not isinstance(document, dict) or set(document) != {
            "schema_version", "candidate_id", "source_url", "publisher", "published_date",
            "classification", "evidence", "extraction"}
            or type(document["schema_version"]) is not int or document["schema_version"] != 1):
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
            or extraction["prompt_version"] != SCOPE_REVIEW_PROMPT):
        _fail()
    _timestamp(extraction["prepared_at"])
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
    from .review_queue import canonical_url
    try:
        _validate_scope_review_shape(document)
        instant = now or datetime.now(timezone.utc)
        instant = _timestamp(instant.isoformat() if isinstance(instant, datetime) else instant)
        prepared = _timestamp(document["extraction"]["prepared_at"])
        if (not _bound(document, reference) or not _matches(_HEX, content_sha256)
                or document["evidence"]["content_sha256"] != content_sha256
                or not _matches(_HEX, document["candidate_id"])
                or canonical_url(document["source_url"]) != document["source_url"]
                or hashlib.sha256(document["source_url"].encode()).hexdigest() != document["candidate_id"]
                or prepared > instant or prepared.date() < _day(document["published_date"])):
            _fail()
    except (ValueError, TypeError, KeyError, AttributeError):
        _fail()
    return deepcopy(document)


def project_news_selection(drafts, archive, excluded_ids=(), now=None, *, scope_reviews=None):
    """Project cited positive classifications; invalid/private-only drafts stay out.

    Eligibility is not approval of the dossier, corroboration of an allegation or
    semantic verification of the model. Classification hashes prove provenance.
    """
    from .gemini_review import validate_envelope
    from .review_queue import normalize
    references = _archive_index(archive)
    if isinstance(excluded_ids, (str, bytes)):
        _fail()
    excluded = set(excluded_ids)
    if any(not _matches(_HEX, identifier) for identifier in excluded):
        _fail()
    now = now or datetime.now(timezone.utc)
    generated = now.isoformat() if isinstance(now, datetime) else now
    instant = _timestamp(generated)
    assessments = {}
    for assessment in scope_reviews if scope_reviews is not None else ():
        identifier = assessment.get("candidate_id") if isinstance(assessment, dict) else None
        if not _matches(_HEX, identifier) or identifier in assessments:
            _fail()
        assessments[identifier] = assessment
    selections, seen = [], set()
    for original in drafts:
        if not isinstance(original, dict):
            continue
        identifier = original.get("candidate_id")
        if not isinstance(identifier, str) or identifier in excluded or identifier not in references:
            continue
        if identifier in seen:
            _fail()  # Do not choose an arbitrary duplicate decision.
        seen.add(identifier)
        document = deepcopy(original)
        reference = references[identifier]
        try:
            validate_envelope(document, reference)
            news = document["news_dossier"]
            scope = news["classification"]
            if (news["extraction_status"] != "processed" or not _bound(news, reference)
                    or scope != document.get("classification")):
                continue
            country = news["fields"]["country"]
            if country["status"] == "reported" and normalize(country["value"]).strip() not in {
                    "el salvador", "republica de el salvador"}:
                continue
            extraction = document["extraction"]
            classified = _timestamp(extraction["prepared_at"])
            fetched = _timestamp(extraction["fetched_at"])
            if (extraction.get("provider") != "gemini"
                    or extraction.get("prompt_version") not in _DOSSIER_PROMPTS
                    or not _matches(_HEX, extraction.get("content_sha256"))
                    or not fetched <= classified <= instant):
                continue
            direct = extraction["prompt_version"] == FULL_SCOPE_PROMPT
            assessment = assessments.get(identifier)
            if assessment is not None:
                assessment = validate_scope_review(assessment, reference=reference,
                    content_sha256=extraction["content_sha256"], now=instant)
                reevaluated = _timestamp(assessment["extraction"]["prepared_at"])
                if not fetched <= reevaluated:
                    continue
                if direct and reevaluated == classified and any(
                        assessment["classification"][key] != scope[key] for key in ("jurisdiction", "relevance")):
                    continue  # Conflicting simultaneous decisions need review.
                if not direct or reevaluated >= classified:
                    scope = assessment["classification"]
                    extraction = assessment["extraction"]
                    evidence = assessment["evidence"]
                    direct = False
                else:
                    assessment = None  # A newer v8 decision supersedes a stale reassessment.
            if assessment is None and not direct:
                continue  # Legacy relevance definitions cannot authorize publication.
            if scope["jurisdiction"] != "SV" or scope["relevance"] != "relevant":
                continue
            if direct:
                grounding = document["grounding"]["classification"]
                evidence = {"content_sha256": extraction["content_sha256"],
                            "jurisdiction_paragraph_ids": _scope_paragraphs(grounding["jurisdiction"]),
                            "relevance_paragraph_ids": _scope_paragraphs(grounding["relevance"])}
            selection = validate_selection({
                "method": "gemini_grounded_scope", "review_status": "automated_unreviewed",
                "human_reviewed": False, "jurisdiction": "SV", "relevance": "relevant",
                "classified_at": extraction["prepared_at"], "model": extraction["model"],
                "prompt_version": extraction["prompt_version"], "evidence": evidence,
            })
            entry = {"candidate_id": identifier, **{key: reference[key] for key in
                     ("source_url", "publisher", "published_date")}, "selection": selection}
            # This also validates publication/classification temporal consistency.
            validate_manifest({"schema_version": 1, "generated_at": generated, "selections": [entry]})
            selections.append(entry)
        except (ValueError, TypeError, KeyError, AttributeError):
            continue  # No source fields or error values may leak to output/logs.
    return validate_manifest({"schema_version": 1, "generated_at": generated,
                              "selections": sorted(selections, key=lambda item: item["candidate_id"])}, archive)


def select_archive(archive, manifest, reviewed_dossiers=None):
    """Filter bibliographic references and recount them; preserve crawl telemetry."""
    references = _archive_index(archive)
    selected = {entry["candidate_id"]: entry["selection"]
                for entry in validate_manifest(manifest, archive)["selections"]}
    if reviewed_dossiers is not None:
        from .news_publication import validate_collection
        reviewed = validate_collection(reviewed_dossiers, archive=archive)
        for dossier in reviewed["dossiers"]:
            scope = dossier["classification"]
            identifier = dossier["candidate_id"]
            if scope["jurisdiction"] != "SV" or scope["relevance"] != "relevant":
                selected.pop(identifier, None)
                continue
            selected[identifier] = validate_selection({
                "method": "editorial_scope", "review_status": "editorially_reviewed", "human_reviewed": True,
                "jurisdiction": "SV", "relevance": "relevant", "reviewed_by": dossier["reviewed_by"],
                "reviewed_at": dossier["reviewed_at"],
            })
    result = deepcopy(archive)
    result["records"] = [{**references[item["id"]], "selection": selected[item["id"]]}
                         for item in archive["records"] if item["id"] in selected]
    counts = Counter((item["source_id"], item["published_date"][:7]) for item in result["records"])
    try:
        coverage = result["coverage"]
        source_ids = {source["id"] for source in coverage["sources"]}
        months = {row["month"] for row in coverage["months"]}
        if (len(source_ids) != len(coverage["sources"]) or len(months) != len(coverage["months"])
                or any(source not in source_ids or month not in months for source, month in counts)):
            _fail()
        coverage["records"] = len(result["records"])
        for source in coverage["sources"]:
            if (len(source["months"]) != len(months)
                    or {row["month"] for row in source["months"]} != months):
                _fail()
            dates = sorted(item["published_date"] for item in result["records"] if item["source_id"] == source["id"])
            source.update(records=len(dates), first_published_date=dates[0] if dates else None,
                          last_published_date=dates[-1] if dates else None)
            for row in source["months"]:
                row["records"] = counts[source["id"], row["month"]]
        for row in coverage["months"]:
            if (len(row["sources"]) != len(source_ids)
                    or {source["source_id"] for source in row["sources"]} != source_ids):
                _fail()
            row["records"] = sum(counts[source, row["month"]] for source in source_ids)
            for source in row["sources"]:
                source["records"] = counts[source["source_id"], row["month"]]
        result["policy"].update(
            selection_gate={"version": 1, "scope": "human_rights_el_salvador",
                            "candidates": len(references), "selected": len(result["records"])},
            selection_method="Selección de pertinencia en derechos humanos y alcance en El Salvador a partir del texto, con evidencia localizada; las decisiones editoriales prevalecen.",
            notice="Archivo bibliográfico parcial seleccionado por pertinencia en derechos humanos y alcance en El Salvador. La selección automatizada no equivale a revisión humana ni acredita violaciones o hechos verificados. Las fichas y el recuento de casos mantienen su revisión editorial y de privacidad separada.",
        )
        result["policy"]["coverage_limitations"] = [
            "La clasificación temática automática puede equivocarse; las decisiones humanas prevalecen.",
            "Las candidatas sin evaluación vigente quedan fuera de esta consulta; el archivo seleccionado no mide la totalidad de noticias pertinentes.",
            *[line for line in result["policy"].get("coverage_limitations", [])
              if not line.startswith("El filtro de títulos y URL")],
        ]
    except (KeyError, TypeError, AttributeError):
        _fail()
    return result


def _read_json(path, limit):
    # Bound actual bytes read as well as stat size; files can change after stat.
    if not path.is_file() or path.stat().st_size > limit:
        _fail()
    with path.open("rb") as source:
        raw = source.read(limit + 1)
    if len(raw) > limit:
        _fail()
    return json.loads(raw)


def _read_drafts(paths):
    seen = set()
    for path in paths:
        if path.is_symlink() or not _matches(_HEX, path.stem):
            _fail()
        document = _read_json(path, MAX_DRAFT_BYTES)
        if (not isinstance(document, dict) or document.get("candidate_id") != path.stem
                or path.stem in seen):
            _fail()
        seen.add(path.stem)
        yield document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--drafts", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--excluded-ids", type=Path, help="JSON list of candidate IDs rejected by human review")
    parser.add_argument("--scope-reviews", type=Path, help="Private reassessments bound to each draft's exact source hash")
    args = parser.parse_args()
    try:
        if args.drafts.is_symlink() or not args.drafts.is_dir():
            _fail()
        paths = sorted(args.drafts.glob("*.json"))
        if len(paths) > MAX_SELECTIONS:
            _fail()
        archive = _read_json(args.archive, MAX_JSON_BYTES)
        excluded = _read_json(args.excluded_ids, MAX_JSON_BYTES) if args.excluded_ids else []
        if not isinstance(excluded, list):
            _fail()
        scope_paths = []
        if args.scope_reviews is not None:
            if args.scope_reviews.is_symlink() or (args.scope_reviews.exists() and not args.scope_reviews.is_dir()):
                _fail()
            scope_paths = sorted(args.scope_reviews.glob("*.json"))
            if len(scope_paths) > MAX_SELECTIONS:
                _fail()
        document = project_news_selection(_read_drafts(paths), archive, excluded,
                                          scope_reviews=_read_drafts(scope_paths))
        if args.output.exists():
            try:
                previous = validate_manifest(_read_json(args.output, MAX_JSON_BYTES))
            except ValueError:
                previous = None  # An obsolete output is not an input permission.
            if previous and previous["selections"] == document["selections"]:
                document["generated_at"] = previous["generated_at"]
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(args.output)
    except (ValueError, TypeError, KeyError, OSError, UnicodeDecodeError):
        raise SystemExit("No se exportó la selección: entradas, evidencia o correspondencia bibliográfica inválidas.") from None
    print(json.dumps({"selected_references": len(document["selections"]), "published_dossiers": 0}))


if __name__ == "__main__":
    main()
