"""Publish minimal scope decisions, never private news worksheets or quotations."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

from .news_scope_policy import SCOPE_PROMPT, EXCLUSION_PROMPT

from .contracts.selection import (
    _HEX as _HEX,
    _PARAGRAPH as _PARAGRAPH,
    _MODEL as _MODEL,
    LEGACY_FULL_SCOPE_PROMPT as LEGACY_FULL_SCOPE_PROMPT,
    PREVIOUS_FULL_SCOPE_PROMPT as PREVIOUS_FULL_SCOPE_PROMPT,
    RIGHTS_FULL_SCOPE_PROMPT as RIGHTS_FULL_SCOPE_PROMPT,
    FULL_SCOPE_PROMPT as FULL_SCOPE_PROMPT,
    SCOPE_REVIEW_PROMPT as SCOPE_REVIEW_PROMPT,
    INCLUSIVE_SCOPE_PROMPT as INCLUSIVE_SCOPE_PROMPT,
    RIGHTS_SCOPE_PROMPT as RIGHTS_SCOPE_PROMPT,
    _SELECTION_PROMPTS as _SELECTION_PROMPTS,
    _STAMP as _STAMP,
    _COMMON as _COMMON,
    _AUTOMATIC as _AUTOMATIC,
    _HUMAN as _HUMAN,
    _ENTRY as _ENTRY,
    MAX_SELECTIONS as MAX_SELECTIONS,
    _fail as _fail,
    _matches as _matches,
    parse_timestamp as _timestamp,
    parse_day as _day,
    _paragraphs as _paragraphs,
    validate_selection as validate_selection,
    index_archive as _archive_index,
    _bound as _bound,
    _assessment_counts as _assessment_counts,
    validate_manifest as validate_manifest,
    _scope_paragraphs as _scope_paragraphs,
    _validate_scope_review_shape as _validate_scope_review_shape,
    validate_scope_review as validate_scope_review,
    validate_scope_audits as validate_scope_audits,
    _excluded_set as _excluded_set,
)

MAX_DRAFT_BYTES = 512_000
MAX_JSON_BYTES = 30_000_000
DEFAULT_SCOPE_AUDITS = Path(__file__).resolve().parents[4] / "data/selection/scope_audits.json"


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
    from .contracts.drafts import validate_envelope as validate_envelope
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
        decision_time = lambda item, same_text=same_text: item["timestamp"][1] if same_text else item["timestamp"]
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
        from .contracts.news_collection import validate_collection as validate_collection
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
        from .contracts.news_collection import validate_collection as validate_collection
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
