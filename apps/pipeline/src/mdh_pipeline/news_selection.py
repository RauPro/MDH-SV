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

from .news_scope_policy import SCOPE_PROMPT, EXCLUSION_PROMPT

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
MAX_DRAFT_BYTES = 512_000
MAX_JSON_BYTES = 30_000_000
DEFAULT_SCOPE_AUDITS = Path(__file__).resolve().parents[4] / "data/selection/scope_audits.json"


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
    from .review_queue import canonical_url
    if (not isinstance(document, dict) or type(document.get("schema_version")) is not int
            or document["schema_version"] not in {1, 2}
            or not isinstance(document.get("selections"), list) or len(document["selections"]) > MAX_SELECTIONS):
        _fail()
    expected = {"schema_version", "generated_at", "selections"}
    if document["schema_version"] == 2:
        expected.add("assessment_counts")
    if set(document) != expected:
        _fail()
    generated = _timestamp(document["generated_at"])
    references = _archive_index(archive) if archive is not None else None
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
    prepared = _timestamp(extraction["prepared_at"])
    if "source_read" in document:
        receipt = document["source_read"]
        if (not isinstance(receipt, dict) or set(receipt) != {
                "fetched_at", "extraction_method", "truncated", "content_sha256"}
                or receipt["extraction_method"] not in {"wordpress_rest_content", "html_article_content"}
                or type(receipt["truncated"]) is not bool
                or receipt["content_sha256"] != evidence["content_sha256"]
                or _timestamp(receipt["fetched_at"]) > prepared
                or _timestamp(receipt["fetched_at"]).date() < _day(document["published_date"])):
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


def validate_scope_audits(document, archive=None, *, now=None):
    """Validate minimal source-audit provenance; no source text or human attribution."""
    from .review_queue import canonical_url
    required = {"candidate_id", "source_url", "publisher", "published_date", "content_sha256",
                "decision", "method", "human_reviewed", "checked_at", "reason"}
    if (not isinstance(document, dict) or set(document) != {"schema_version", "audits"}
            or type(document["schema_version"]) is not int or document["schema_version"] != 1
            or not isinstance(document["audits"], list) or len(document["audits"]) > MAX_SELECTIONS):
        _fail()
    references = _archive_index(archive) if archive is not None else None
    instant = now or datetime.now(timezone.utc)
    instant = _timestamp(instant.isoformat() if isinstance(instant, datetime) else instant)
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
                    or not _day(entry["published_date"]) <= _timestamp(entry["checked_at"]).date()
                    or _timestamp(entry["checked_at"]) > instant):
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


def _category(scope, *, legacy=False):
    positive = scope["jurisdiction"] == "SV" and scope["relevance"] == "relevant"
    if positive:
        return "relevant"
    if legacy:
        return "uncertain"  # The narrower v1 exclusions require inclusive reassessment.
    if scope["jurisdiction"] == "other" or scope["relevance"] == "not_relevant":
        return "excluded"
    return "uncertain"


def _decision(scope, extraction, evidence, *, fetched_at=None, exclusion_verified=False):
    # A first-pass negative (under any policy) is an assessment awaiting a
    # source-bound second check, never an automatic exclusion by itself.
    category = _category(scope, legacy=not exclusion_verified)
    prepared = _timestamp(extraction["prepared_at"])
    selection = None
    if category == "relevant":
        selection = validate_selection({
            "method": "gemini_grounded_scope", "review_status": "automated_unreviewed",
            "human_reviewed": False, "jurisdiction": "SV", "relevance": "relevant",
            "classified_at": extraction["prepared_at"], "model": extraction["model"],
            "prompt_version": extraction["prompt_version"], "evidence": evidence,
        })
    return {"category": category, "selection": selection, "scope": (scope["jurisdiction"], scope["relevance"]),
            "hash": evidence["content_sha256"], "policy": extraction["prompt_version"],
            "timestamp": (_timestamp(fetched_at) if fetched_at else prepared, prepared)}


def _dossier_decision(original, reference, instant):
    from .gemini_review import validate_envelope
    document = deepcopy(original)
    validate_envelope(document, reference)
    news, extraction = document["news_dossier"], document["extraction"]
    scope = news["classification"]
    fetched, prepared = _timestamp(extraction["fetched_at"]), _timestamp(extraction["prepared_at"])
    if (news["extraction_status"] != "processed" or not _bound(news, reference)
            or scope != document.get("classification") or extraction.get("provider") != "gemini"
            or extraction.get("prompt_version") not in {FULL_SCOPE_PROMPT, RIGHTS_FULL_SCOPE_PROMPT, LEGACY_FULL_SCOPE_PROMPT, PREVIOUS_FULL_SCOPE_PROMPT}
            or not _matches(_MODEL, extraction.get("model"))
            or not _matches(_HEX, extraction.get("content_sha256"))
            or not _day(news["published_date"]) <= fetched.date()
            or not fetched <= prepared <= instant):
        _fail()
    grounding = document.get("grounding", {}).get("classification", {})
    evidence = {"content_sha256": extraction["content_sha256"]}
    for field, positive in (("jurisdiction", "SV"), ("relevance", "relevant")):
        citations = grounding.get(field, [])
        evidence[field + "_paragraph_ids"] = (_scope_paragraphs(citations)
            if citations or scope[field] == positive else [])
    return _decision(scope, extraction, evidence, fetched_at=extraction["fetched_at"])


def project_news_selection(drafts, archive, excluded_ids=(), now=None, *, scope_reviews=None,
                           reviewed_dossiers=None, scope_audits=None, scope_verifications=None,
                           scope_rechecks=None):
    """Classify bibliography directly from source-bound private scope snapshots.

    A dossier or queue issue is never a prerequisite. The source reader validates
    the digest and paragraph membership when storing the assessment; this export
    validates its trusted artifact's shape, bibliographic identity and receipt.
    It does not reread the web or claim that a digest proves semantic correctness.
    """
    references = _archive_index(archive)
    excluded = _excluded_set(excluded_ids)
    now = now or datetime.now(timezone.utc)
    generated = now.isoformat() if isinstance(now, datetime) else now
    instant = _timestamp(generated)
    # Import lazily: audit requests validate scope receipts through this module.
    from .news_scope_rechecks import blocked_source_hashes
    blocked_hashes = blocked_source_hashes(scope_rechecks if scope_rechecks is not None else
        {"schema_version": 1, "requests": []}, now=instant)
    audits = validate_scope_audits(scope_audits if scope_audits is not None else
                                  {"schema_version": 1, "audits": []}, archive, now=instant)["audits"]
    verifications = {}
    for receipt in scope_verifications if scope_verifications is not None else ():
        identifier = receipt.get("candidate_id") if isinstance(receipt, dict) else None
        if not _matches(_HEX, identifier) or identifier in verifications:
            _fail()
        verifications[identifier] = receipt
    decisions = {}
    seen = set()
    for original in drafts:
        if not isinstance(original, dict):
            continue
        identifier = original.get("candidate_id")
        if not isinstance(identifier, str) or identifier not in references:
            continue
        if identifier in seen:
            _fail()
        seen.add(identifier)
        try:
            decisions.setdefault(identifier, []).append(_dossier_decision(original, references[identifier], instant))
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
    seen = set()
    for original in scope_reviews if scope_reviews is not None else ():
        identifier = original.get("candidate_id") if isinstance(original, dict) else None
        if not _matches(_HEX, identifier) or identifier in seen:
            _fail()
        seen.add(identifier)
        if identifier not in references:
            continue
        try:
            assessment = validate_scope_review(original, reference=references[identifier],
                content_sha256=original["evidence"]["content_sha256"], now=instant)
            from .news_exclusions import confirmed_exclusion
            source_receipt = assessment.get("source_read", {})
            confirmed = confirmed_exclusion(verifications.get(identifier), assessment, references[identifier], now=instant)
            decisions.setdefault(identifier, []).append(_decision(
                assessment["classification"], assessment["extraction"], assessment["evidence"],
                fetched_at=source_receipt.get("fetched_at"), exclusion_verified=confirmed))
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
    latest = {}
    for identifier, options in decisions.items():
        if not options:
            continue
        # The dedicated inclusive classifier owns scope for this exact text.
        # Later dossier enrichment must not silently re-decide its eligibility.
        same_text = len({item["hash"] for item in options}) == 1
        dedicated = [item for item in options if item["policy"] in {
            INCLUSIVE_SCOPE_PROMPT, RIGHTS_SCOPE_PROMPT, SCOPE_PROMPT, EXCLUSION_PROMPT}]
        if same_text:
            current = [item for item in options if item["policy"] in {FULL_SCOPE_PROMPT, SCOPE_PROMPT, EXCLUSION_PROMPT}]
            current_scope = [item for item in current if item["policy"] in {SCOPE_PROMPT, EXCLUSION_PROMPT}]
            if current:
                options = current_scope or current
            elif dedicated:
                rights_nexus = [item for item in dedicated if item["policy"] == RIGHTS_SCOPE_PROMPT]
                options = rights_nexus or dedicated
        # A different body can supersede the old snapshot only after the newer
        # read is classified under the inclusive policy, never the old v8/v1 rules.
        decision_time = lambda item: item["timestamp"][1] if same_text else item["timestamp"]
        stamp = max(decision_time(item) for item in options)
        newest = [item for item in options if decision_time(item) == stamp]
        if (len({(item["scope"], item["hash"]) for item in newest}) > 1
                or not same_text and any(item["policy"] not in {FULL_SCOPE_PROMPT, RIGHTS_FULL_SCOPE_PROMPT, INCLUSIVE_SCOPE_PROMPT, RIGHTS_SCOPE_PROMPT, SCOPE_PROMPT, EXCLUSION_PROMPT}
                                         for item in newest)):
            latest[identifier] = {"category": "uncertain", "selection": None}
        else:
            latest[identifier] = newest[-1]
    for audit in audits:
        decision = latest.get(audit["candidate_id"])
        if (decision is not None and decision.get("hash") == audit["content_sha256"]
                and decision.get("policy") not in {FULL_SCOPE_PROMPT, SCOPE_PROMPT, EXCLUSION_PROMPT}):
            # Old restrictive audits are reassessment hints, not current-policy
            # exclusion evidence. Fresh inclusive decisions supersede them.
            latest[audit["candidate_id"]] = {**decision, "category": "uncertain", "selection": None}
    # Bind the audit to the effective source decision after precedence resolution.
    # A coherent newer body must not be held just because an old draft is cached.
    blocked_ids = {identifier for identifier, digest in blocked_hashes.items()
                   if latest.get(identifier, {}).get("hash") == digest}
    if reviewed_dossiers is not None:
        from .news_publication import validate_collection
        for dossier in validate_collection(reviewed_dossiers, archive=archive)["dossiers"]:
            identifier = dossier["candidate_id"]
            category = _category(dossier["classification"])
            automatic = latest.get(identifier, {}).get("selection") if category == "relevant" else None
            latest[identifier] = {"category": category, "selection": automatic}
    for identifier in blocked_ids:
        latest[identifier] = {"category": "uncertain", "selection": None}
    for identifier in excluded & references.keys():
        # A human withheld the reference, without necessarily finding it irrelevant.
        latest[identifier] = {"category": "uncertain", "selection": None}
    selections = []
    for identifier, decision in latest.items():
        if decision["selection"] is not None:
            selections.append({"candidate_id": identifier, **{key: references[identifier][key]
                for key in ("source_url", "publisher", "published_date")}, "selection": decision["selection"]})
    categories = Counter(item["category"] for item in latest.values())
    counts = {"candidates": len(references), "evaluated": len(latest),
              **{key: categories[key] for key in ("relevant", "excluded", "uncertain")},
              "pending": len(references) - len(latest)}
    return validate_manifest({"schema_version": 2, "generated_at": generated, "assessment_counts": counts,
                              "selections": sorted(selections, key=lambda item: item["candidate_id"])}, archive)


def select_archive(archive, manifest, reviewed_dossiers=None, *, excluded_ids=()):
    """Filter bibliographic references and recount them; preserve crawl telemetry."""
    references = _archive_index(archive)
    excluded = _excluded_set(excluded_ids)
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
    for identifier in excluded:
        selected.pop(identifier, None)
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
        gate = {"version": 1, "scope": "human_rights_el_salvador",
                "candidates": len(references), "selected": len(result["records"])}
        if manifest["schema_version"] == 2:
            gate.update(manifest["assessment_counts"], version=2, evaluated_at=manifest["generated_at"])
            # The collector only appends references. New discoveries have never
            # been assessed; preserve the snapshot and count the growth as pending.
            growth = len(references) - manifest["assessment_counts"]["candidates"]
            gate.update(candidates=len(references), pending=gate["pending"] + growth)
        # Imported human decisions may be newer than this classification snapshot.
        # Keep selected live; never invent per-category adjustments without a ledger.
        result["policy"].update(
            selection_gate=gate,
            selection_method="Selección de pertinencia en derechos humanos y alcance en El Salvador a partir del texto, con evidencia localizada; las decisiones editoriales prevalecen.",
            notice="Archivo bibliográfico parcial seleccionado por pertinencia en derechos humanos y alcance en El Salvador. Las fichas se publican automáticamente por campo tras controles de evidencia y privacidad, atribuidas a su fuente. La selección y publicación automáticas no equivalen a revisión humana ni acreditan violaciones o corroboración independiente. Las solicitudes de corrección se tramitan en GitHub; el catálogo de casos conserva criterios separados.",
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
    parser.add_argument("--drafts", type=Path, help="Optional full-dossier decisions; standalone scope reviews suffice")
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--excluded-ids", type=Path, help="JSON list of candidate IDs rejected by human review")
    parser.add_argument("--scope-reviews", type=Path, help="Independent private source snapshots; a dossier is not required")
    parser.add_argument("--scope-verifications", type=Path, help="Private source-bound second checks before automatic exclusions")
    parser.add_argument("--scope-rechecks", type=Path, help="Private audit requests identifying inconsistent source bodies")
    parser.add_argument("--reviewed-dossiers", type=Path, help="Approved human scope decisions included in snapshot counts")
    parser.add_argument("--scope-audits", type=Path, help="Source-bound automated exclusions; defaults to repository audit collection")
    args = parser.parse_args()
    try:
        paths = []
        if args.drafts is not None:
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
        verification_paths = []
        if args.scope_verifications is not None:
            if args.scope_verifications.is_symlink() or (args.scope_verifications.exists() and not args.scope_verifications.is_dir()):
                _fail()
            verification_paths = sorted(args.scope_verifications.glob("*.json"))
            if len(verification_paths) > MAX_SELECTIONS:
                _fail()
        audit_path = args.scope_audits if args.scope_audits is not None else DEFAULT_SCOPE_AUDITS
        audits = (_read_json(audit_path, MAX_JSON_BYTES)
                  if args.scope_audits is not None or audit_path.exists() else {"schema_version": 1, "audits": []})
        from .news_scope_rechecks import load_rechecks
        rechecks = load_rechecks(args.scope_rechecks) if args.scope_rechecks is not None else None
        document = project_news_selection(_read_drafts(paths), archive, excluded,
            scope_reviews=_read_drafts(scope_paths), scope_verifications=_read_drafts(verification_paths), reviewed_dossiers=(
                _read_json(args.reviewed_dossiers, MAX_JSON_BYTES) if args.reviewed_dossiers else None),
            scope_audits=audits, scope_rechecks=rechecks)
        if args.output.exists():
            try:
                previous = validate_manifest(_read_json(args.output, MAX_JSON_BYTES))
            except ValueError:
                previous = None  # An obsolete output is not an input permission.
            if previous and {key: value for key, value in previous.items() if key != "generated_at"} == {
                    key: value for key, value in document.items() if key != "generated_at"}:
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
