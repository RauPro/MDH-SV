"""Versioned editorial evaluation corpus: deterministic sample, labels and offline reports.

The corpus keeps public bibliographic identity, the SHA-256 of the exact text that
was labelled and short paraphrased rationales. It never stores article bodies,
quotations or personal names. Reports compare labels with decisions already present
in the public snapshots (or an explicit decisions file in the same format). They
call no model, fetch no article and never read the private queue.

Only labels confirmed by a human reviewer feed the acceptance gates. Labels
proposed by an assistant are reported separately as provisional evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from mdh_schema.territory import DepartmentEnum

from .history import SCOPE_VERSION, scope_hint
from .publishers import SOURCES

ROOT = Path(__file__).resolve().parents[4]
CORPUS_PATH = ROOT / "data/editorial-corpus/corpus.json"
SAMPLE_SALT = "mdh-editorial-corpus-v1"
POLICY_REFERENCE = "inclusive-violence-1"
LABELING_GUIDE = "docs/EDITORIAL-CORPUS.md"
DEPARTMENTS = tuple(item.value for item in DepartmentEnum if item is not DepartmentEnum.DESCONOCIDO)
JURISDICTIONS = ("SV", "other", "unclear")
RELEVANCE = ("relevant", "not_relevant", "unclear")
HEX64 = re.compile(r"[a-f0-9]{64}")
PARAGRAPH = re.compile(r"p[1-9][0-9]{0,2}")
REVIEWER = re.compile(r"@[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
MAX_CORPUS_BYTES = 2_000_000
STRATA = (
    {"id": "selected-scope-3", "kind": "selected", "size": 8,
     "prompt_versions": ("mdh-news-scope-3-rights-nexus",)},
    {"id": "selected-scope-4", "kind": "selected", "size": 6,
     "prompt_versions": ("mdh-news-scope-4-inclusive-violence",)},
    {"id": "selected-universal-11", "kind": "selected", "size": 6,
     "prompt_versions": ("mdh-universal-news-grounded-11-inclusive-violence",)},
    {"id": "selected-other-prompts", "kind": "selected", "size": 3,
     "prompt_versions": ("mdh-news-exclusion-1-inclusive-violence",
                         "mdh-universal-news-grounded-10-rights-nexus", "mdh-news-scope-1")},
    {"id": "automatic-location", "kind": "location", "size": 4},
    {"id": "automatic-dossier-department", "kind": "dossier_department", "size": 4},
    {"id": "unselected-el-salvador-hint", "kind": "unselected", "size": 6, "scope_hint": "el_salvador"},
    {"id": "unselected-international-hint", "kind": "unselected", "size": 5,
     "scope_hint": "international_context"},
    {"id": "unselected-undetermined-hint", "kind": "unselected", "size": 6, "scope_hint": "undetermined"},
)
STRATUM_IDS = frozenset(stratum["id"] for stratum in STRATA)
LABEL_KEYS = frozenset({"jurisdiction", "relevance", "event_departments", "mentioned_departments",
                        "evidence_paragraph_ids", "names_private_persons", "rationale"})
ITEM_KEYS = frozenset({"candidate_id", "source_url", "publisher", "published_date", "stratum",
                       "text_binding", "proposed_label", "label", "review"})
# Acceptance gates use confirmed labels only. A cell with fewer bound items is
# reported as insufficient evidence, never as a pass.
GATES = (
    {"id": "selection_jurisdiction_precision", "section": "selection", "metric": "jurisdiction_precision",
     "minimum": 0.95, "minimum_items": 10},
    {"id": "selection_relevance_precision", "section": "selection", "metric": "relevance_precision",
     "minimum": 0.90, "minimum_items": 10},
    {"id": "dossier_department_precision", "section": "dossier_department", "metric": "precision",
     "minimum": 0.90, "minimum_items": 10},
    {"id": "dossier_false_absence", "section": "dossier_department", "metric": "false_absence",
     "maximum": 0, "minimum_items": 1},
    {"id": "location_faithfulness", "section": "locations", "metric": "faithfulness",
     "minimum": 0.95, "minimum_items": 10},
)


class CorpusError(ValueError):
    """A bounded validation failure without article content."""


def _require(condition, message):
    if not condition:
        raise CorpusError(message)


def _read_json(path: Path, maximum=40_000_000):
    path = Path(path)
    _require(path.is_file() and not path.is_symlink(), f"missing_or_unsafe_file:{path.name}")
    raw = path.read_bytes()
    _require(len(raw) <= maximum, f"oversized_file:{path.name}")

    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, f"duplicate_key:{key}")
            result[key] = value
        return result
    return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)


def _timestamp(value, message):
    _require(isinstance(value, str) and len(value) <= 40, message)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise CorpusError(message) from None
    _require(parsed.tzinfo is not None, message)


def _departments(value, message):
    _require(isinstance(value, list) and all(item in DEPARTMENTS for item in value)
             and value == sorted(set(value), key=DEPARTMENTS.index), message)
    return set(value)


def validate_label(label, where):
    _require(isinstance(label, dict) and set(label) == LABEL_KEYS, f"invalid_label_keys:{where}")
    _require(label["jurisdiction"] in JURISDICTIONS, f"invalid_jurisdiction:{where}")
    _require(label["relevance"] in RELEVANCE, f"invalid_relevance:{where}")
    event = _departments(label["event_departments"], f"invalid_event_departments:{where}")
    mentioned = _departments(label["mentioned_departments"], f"invalid_mentioned_departments:{where}")
    _require(event <= mentioned, f"event_department_not_mentioned:{where}")
    paragraphs = label["evidence_paragraph_ids"]
    _require(isinstance(paragraphs, list) and len(paragraphs) <= 20
             and all(isinstance(item, str) and PARAGRAPH.fullmatch(item) for item in paragraphs)
             and len(set(paragraphs)) == len(paragraphs), f"invalid_evidence_paragraphs:{where}")
    # A positive or negative decision must point to the text that supports it.
    decided = label["jurisdiction"] != "unclear" or label["relevance"] != "unclear"
    _require(not decided or paragraphs, f"missing_evidence_paragraphs:{where}")
    _require(type(label["names_private_persons"]) is bool, f"invalid_privacy_flag:{where}")
    rationale = label["rationale"]
    _require(isinstance(rationale, str) and 10 <= len(rationale) <= 400 and rationale == rationale.strip()
             and '"' not in rationale and "«" not in rationale, f"invalid_rationale:{where}")


def validate_corpus(document):
    """Validate shape and review provenance; bindings to snapshots are checked separately."""
    _require(isinstance(document, dict) and set(document) == {
        "schema_version", "corpus_version", "policy_reference", "labeling_guide", "sampling", "items"},
        "invalid_corpus_keys")
    _require(document["schema_version"] == 1 and type(document["schema_version"]) is int, "invalid_schema_version")
    _require(type(document["corpus_version"]) is int and document["corpus_version"] >= 1, "invalid_corpus_version")
    _require(document["policy_reference"] == POLICY_REFERENCE, "unexpected_policy_reference")
    _require(document["labeling_guide"] == LABELING_GUIDE, "unexpected_labeling_guide")
    sampling = document["sampling"]
    _require(isinstance(sampling, dict) and set(sampling) == {"salt", "snapshot_commit", "strata", "replaced"},
             "invalid_sampling")
    _require(sampling["salt"] == SAMPLE_SALT, "unexpected_sampling_salt")
    _require(isinstance(sampling["snapshot_commit"], str) and re.fullmatch(r"[a-f0-9]{40}", sampling["snapshot_commit"]),
             "invalid_snapshot_commit")
    _require(sampling["strata"] == [{"id": stratum["id"], "size": stratum["size"]} for stratum in STRATA],
             "sampling_strata_changed")
    replaced = sampling["replaced"]
    _require(isinstance(replaced, list) and all(
        isinstance(row, dict) and set(row) == {"candidate_id", "reason"}
        and isinstance(row["candidate_id"], str) and HEX64.fullmatch(row["candidate_id"])
        and isinstance(row["reason"], str) and re.fullmatch(r"[a-z_]{3,60}", row["reason"]) for row in replaced),
        "invalid_replacements")
    items = document["items"]
    _require(isinstance(items, list) and items, "missing_items")
    seen = set()
    for index, item in enumerate(items):
        where = f"items[{index}]"
        _require(isinstance(item, dict) and set(item) == ITEM_KEYS, f"invalid_item_keys:{where}")
        identifier = item["candidate_id"]
        _require(isinstance(identifier, str) and HEX64.fullmatch(identifier), f"invalid_candidate_id:{where}")
        _require(identifier not in seen, f"duplicate_candidate:{where}")
        seen.add(identifier)
        _require(isinstance(item["source_url"], str) and item["source_url"].startswith("https://")
                 and hashlib.sha256(item["source_url"].encode()).hexdigest() == identifier,
                 f"candidate_url_mismatch:{where}")
        _require(isinstance(item["publisher"], str) and item["publisher"], f"invalid_publisher:{where}")
        _require(isinstance(item["published_date"], str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", item["published_date"]),
                 f"invalid_published_date:{where}")
        _require(item["stratum"] in STRATUM_IDS, f"unknown_stratum:{where}")
        binding = item["text_binding"]
        _require(isinstance(binding, dict) and set(binding) == {"content_sha256", "extraction_method", "truncated", "fetched_at"}
                 and isinstance(binding["content_sha256"], str) and HEX64.fullmatch(binding["content_sha256"])
                 and isinstance(binding["extraction_method"], str) and re.fullmatch(r"[a-z_]{3,40}", binding["extraction_method"])
                 and type(binding["truncated"]) is bool, f"invalid_text_binding:{where}")
        _timestamp(binding["fetched_at"], f"invalid_fetch_time:{where}")
        validate_label(item["proposed_label"], where + ".proposed_label")
        validate_label(item["label"], where + ".label")
        review = item["review"]
        _require(isinstance(review, dict) and set(review) == {
            "status", "proposed_by", "proposed_at", "reviewer", "reviewed_at", "notes"}, f"invalid_review:{where}")
        _require(review["proposed_by"] == "ai_assistant", f"invalid_proposer:{where}")
        _timestamp(review["proposed_at"], f"invalid_proposal_time:{where}")
        _require(review["notes"] is None or (isinstance(review["notes"], str) and 0 < len(review["notes"]) <= 600),
                 f"invalid_review_notes:{where}")
        if review["status"] == "proposed":
            # Unreviewed proposals cannot be edited into apparent ground truth.
            _require(review["reviewer"] is None and review["reviewed_at"] is None
                     and item["label"] == item["proposed_label"], f"unreviewed_label_changed:{where}")
        else:
            _require(review["status"] == "confirmed", f"invalid_review_status:{where}")
            _require(isinstance(review["reviewer"], str) and REVIEWER.fullmatch(review["reviewer"]),
                     f"missing_human_reviewer:{where}")
            _timestamp(review["reviewed_at"], f"invalid_review_time:{where}")
    _require(not seen & {row["candidate_id"] for row in replaced}, "replaced_candidate_still_sampled")
    return document


def _history(root: Path):
    references = {}
    for source in SOURCES:
        if source.get("access") == "restricted":
            continue
        path = root / "data/history" / f"{source['id']}.json"
        if path.exists():
            for reference in _read_json(path)["records"]:
                references[reference["id"]] = reference
    return references


def load_snapshots(root: Path = ROOT, decisions: Path | None = None):
    selection = _read_json(decisions or root / "data/selection/news.json")
    _require(isinstance(selection, dict) and isinstance(selection.get("selections"), list), "invalid_decisions")
    return {
        "history": _history(root),
        "selection": {row["candidate_id"]: row for row in selection["selections"]},
        "dossiers": {row["candidate_id"]: row for row in _read_json(root / "data/automated/news_dossiers.json")["dossiers"]},
        "locations": {row["candidate_id"]: row for row in _read_json(root / "apps/web/public/archive/locations.json")["locations"]},
    }


def binding_problems(corpus, snapshots):
    """Items whose public bibliographic identity no longer matches the labelled reference."""
    problems = []
    for index, item in enumerate(corpus["items"]):
        reference = snapshots["history"].get(item["candidate_id"])
        if reference is None:
            problems.append((index, "candidate_not_in_public_history"))
        elif ((reference["source_url"], reference["publisher"], reference["published_date"])
              != (item["source_url"], item["publisher"], item["published_date"])):
            problems.append((index, "candidate_identity_changed"))
    return problems


def check_bindings(corpus, snapshots):
    """Maintainer check: every labelled item must still be the same public reference."""
    for index, code in binding_problems(corpus, snapshots):
        raise CorpusError(f"{code}:items[{index}]")


def _rank(identifier):
    return hashlib.sha256(f"{SAMPLE_SALT}:{identifier}".encode()).hexdigest()


def _eligible(stratum, identifier, reference, snapshots):
    selected = snapshots["selection"].get(identifier)
    kind = stratum["kind"]
    if kind == "selected":
        return selected is not None and selected["selection"].get("prompt_version") in stratum["prompt_versions"]
    if kind == "location":
        return selected is not None and identifier in snapshots["locations"]
    if kind == "dossier_department":
        dossier = snapshots["dossiers"].get(identifier)
        return dossier is not None and dossier["fields"]["department"]["status"] == "reported"
    return selected is None and reference.get("scope_hint") == stratum["scope_hint"]


def sample(snapshots, *, excluded=()):
    """Deterministic stratified sample; strata are exclusive and taken in order."""
    picked, rows = set(excluded), []
    for stratum in STRATA:
        pool = [identifier for identifier, reference in snapshots["history"].items()
                if identifier not in picked and _eligible(stratum, identifier, reference, snapshots)]
        for identifier in sorted(pool, key=_rank)[:stratum["size"]]:
            picked.add(identifier)
            rows.append({"candidate_id": identifier, "stratum": stratum["id"]})
    return rows


def _ratio(numerator, denominator):
    return round(numerator / denominator, 4) if denominator else None


COUNTS = {
    "selection": ("items", "correct", "wrong_jurisdiction", "wrong_relevance", "label_unclear", "text_changed"),
    "dossier_department": ("items", "correct", "incorrect", "false_absence", "not_published", "text_changed"),
    "locations": ("items", "bound", "faithful", "unfaithful", "event_match", "event_mismatch", "text_changed"),
    "unselected": ("items", "labelled_in_scope", "labelled_out_or_unclear"),
}


def evaluate(corpus, snapshots):
    groups = {"confirmed": defaultdict(lambda: defaultdict(Counter)), "provisional": defaultdict(lambda: defaultdict(Counter))}
    privacy = {"confirmed": [], "provisional": []}
    # Snapshot drift excludes an item instead of failing automated data updates.
    unbound = {corpus["items"][index]["candidate_id"]: code for index, code in binding_problems(corpus, snapshots)}
    for item in corpus["items"]:
        if item["candidate_id"] in unbound:
            continue
        group = "confirmed" if item["review"]["status"] == "confirmed" else "provisional"
        sections, label, identifier = groups[group], item["label"], item["candidate_id"]
        bound_hash = item["text_binding"]["content_sha256"]
        in_scope = label["jurisdiction"] == "SV" and label["relevance"] == "relevant"
        reference = snapshots["history"][identifier]
        hint = scope_hint(reference["title"], reference["source_url"])["scope_hint"]
        sections["scope_hint"][f"title-screen-v{SCOPE_VERSION}"][f"{hint}->{label['jurisdiction']}"] += 1
        selected = snapshots["selection"].get(identifier)
        if selected is None:
            cell = sections["unselected"]["all"]
            cell["items"] += 1
            cell["labelled_in_scope" if in_scope else "labelled_out_or_unclear"] += 1
        else:
            decision = selected["selection"]
            key = f"{decision.get('model')}|{decision.get('prompt_version')}"
            cell = sections["selection"][key]
            cell["items"] += 1
            if (decision.get("evidence") or {}).get("content_sha256") != bound_hash:
                cell["text_changed"] += 1
            elif label["jurisdiction"] == "other":
                cell["wrong_jurisdiction"] += 1
            elif label["relevance"] == "not_relevant":
                cell["wrong_relevance"] += 1
            elif in_scope:
                cell["correct"] += 1
            else:
                cell["label_unclear"] += 1
        dossier = snapshots["dossiers"].get(identifier)
        if dossier is not None and dossier.get("schema_version") == 2:
            automation = dossier["automation"]
            cell = sections["dossier_department"][f"{automation['evaluator_model']}|{automation['policy_version']}"]
            cell["items"] += 1
            field = dossier["fields"]["department"]
            if automation["source_content_sha256"] != bound_hash:
                cell["text_changed"] += 1
            elif field["status"] == "reported":
                cell["correct" if field["value"] in label["event_departments"] else "incorrect"] += 1
            elif field["status"] == "not_reported" and label["event_departments"]:
                cell["false_absence"] += 1
            else:
                cell["not_published"] += 1
            persons = dossier["fields"]["affected_persons"]
            if label["names_private_persons"] and persons["status"] == "reported":
                privacy[group].append(identifier)
        location = snapshots["locations"].get(identifier)
        if location is not None:
            cell = sections["locations"][f"{location['method']}|rule-{location.get('rule_version')}"]
            cell["items"] += 1
            projected = ({location["department"]} if "department" in location
                         else {mention["department"] for mention in location.get("mentions", [])})
            if location["evidence"]["content_sha256"] != bound_hash:
                cell["text_changed"] += 1
            else:
                cell["bound"] += 1
                cell["faithful" if projected <= set(label["mentioned_departments"]) else "unfaithful"] += 1
                cell["event_match" if projected & set(label["event_departments"]) else "event_mismatch"] += 1
    report = {"corpus_version": corpus["corpus_version"], "policy_reference": corpus["policy_reference"],
              "items": len(corpus["items"]),
              "confirmed_items": sum(item["review"]["status"] == "confirmed" for item in corpus["items"]),
              "excluded_identity_changes": dict(sorted(unbound.items())),
              "groups": {}, "privacy_inspection": privacy}
    for name, sections in groups.items():
        rendered = {}
        for section, cells in sorted(sections.items()):
            rendered[section] = {}
            for key, counts in sorted(cells.items()):
                values = {**dict.fromkeys(COUNTS.get(section, ()), 0), **dict(sorted(counts.items()))}
                if section == "selection":
                    judged = values["correct"] + values["wrong_jurisdiction"] + values["wrong_relevance"]
                    values["bound_items"] = values["items"] - values["text_changed"]
                    values["jurisdiction_precision"] = _ratio(judged - values["wrong_jurisdiction"], judged)
                    values["relevance_precision"] = _ratio(values["correct"], values["correct"] + values["wrong_relevance"])
                elif section == "dossier_department":
                    values["bound_items"] = values["items"] - values["text_changed"]
                    values["precision"] = _ratio(values["correct"], values["correct"] + values["incorrect"])
                elif section == "locations":
                    values["bound_items"] = values["bound"]
                    values["faithfulness"] = _ratio(values["faithful"], values["bound"])
                    values["event_agreement"] = _ratio(values["event_match"], values["bound"])
                rendered[section][key] = values
        report["groups"][name] = rendered
    report["gates"] = gates(report["groups"]["confirmed"])
    return report


def gates(confirmed):
    results = []
    for gate in GATES:
        for key, values in sorted(confirmed.get(gate["section"], {}).items()):
            observed, items = values.get(gate["metric"]), values.get("bound_items", 0)
            if items < gate["minimum_items"] or observed is None:
                status = "insufficient_evidence"
            elif "minimum" in gate:
                status = "pass" if observed >= gate["minimum"] else "fail"
            else:
                status = "pass" if observed <= gate["maximum"] else "fail"
            results.append({"gate": gate["id"], "cell": key, "observed": observed, "bound_items": items,
                            "status": status})
        if not confirmed.get(gate["section"]):
            results.append({"gate": gate["id"], "cell": None, "observed": None, "bound_items": 0,
                            "status": "insufficient_evidence"})
    return results


def render_markdown(report):
    lines = [f"# Informe del corpus editorial v{report['corpus_version']}", "",
             f"Política de referencia: `{report['policy_reference']}`. Elementos: {report['items']}; "
             f"confirmados por revisión humana: {report['confirmed_items']}; excluidos por cambio de identidad "
             f"bibliográfica: {len(report['excluded_identity_changes'])}.", "",
             "Sólo las etiquetas confirmadas alimentan los criterios de aceptación. Las propuestas "
             "del asistente se muestran como evidencia provisional y no acreditan exactitud.", ""]
    for name, title in (("confirmed", "Etiquetas confirmadas"), ("provisional", "Etiquetas propuestas, pendientes de revisión")):
        lines += [f"## {title}", ""]
        sections = report["groups"][name]
        if not sections:
            lines += ["Sin elementos.", ""]
        for section, cells in sections.items():
            columns = sorted({column for values in cells.values() for column in values})
            lines += [f"### {section}", "", "| celda | " + " | ".join(columns) + " |",
                      "| --- |" + " --- |" * len(columns)]
            lines += [f"| `{key}` | " + " | ".join("" if values.get(column) is None else str(values.get(column))
                                                    for column in columns) + " |" for key, values in cells.items()]
            lines.append("")
    lines += ["## Criterios de aceptación (sólo confirmadas)", "", "| criterio | celda | observado | elementos | estado |",
              "| --- | --- | --- | --- | --- |"]
    lines += [f"| {row['gate']} | {row['cell'] or '—'} | {'' if row['observed'] is None else row['observed']} | "
              f"{row['bound_items']} | {row['status']} |" for row in report["gates"]]
    pending = report["privacy_inspection"]
    lines += ["", "## Inspección manual de privacidad", "",
              f"Confirmadas: {len(pending['confirmed'])}; provisionales: {len(pending['provisional'])}. "
              "Son fichas con personas afectadas publicadas cuando la etiqueta indica nombres de personas privadas en la fuente; "
              "requieren lectura humana, no implican por sí solas una filtración.", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "report", "sample"):
        command = commands.add_parser(name)
        command.add_argument("--root", type=Path, default=ROOT)
        if name != "sample":
            command.add_argument("--corpus", type=Path, default=None)
        if name == "report":
            command.add_argument("--decisions", type=Path, help="Selection-format decisions to evaluate instead of the public snapshot")
            command.add_argument("--format", choices=("json", "markdown"), default="markdown")
            command.add_argument("--require-gates", action="store_true", help="Exit 1 when a confirmed-label gate fails")
            command.add_argument("--cell", action="append", default=[],
                                 help="Restrict --require-gates to these cells, e.g. MODEL|PROMPT_VERSION")
    args = parser.parse_args(argv)
    try:
        snapshots = load_snapshots(args.root, getattr(args, "decisions", None))
        if args.command == "sample":
            print(json.dumps(sample(snapshots), indent=2))
            return 0
        corpus = validate_corpus(_read_json(args.corpus or args.root / "data/editorial-corpus/corpus.json", MAX_CORPUS_BYTES))
        if args.command == "check":
            check_bindings(corpus, snapshots)
            print(f"Corpus v{corpus['corpus_version']}: {len(corpus['items'])} elementos válidos y vinculados.")
            return 0
        report = evaluate(corpus, snapshots)
        print(render_markdown(report) if args.format == "markdown" else json.dumps(report, ensure_ascii=False, indent=2))
        failed = [row for row in report["gates"] if row["status"] == "fail" and (not args.cell or row["cell"] in args.cell)]
        return 1 if args.require_gates and failed else 0
    except (CorpusError, OSError, json.JSONDecodeError, KeyError) as error:
        print(f"editorial_corpus: {error if isinstance(error, CorpusError) else type(error).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
