"""The editorial corpus measures automatic decisions without becoming a source of truth."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from mdh_pipeline import editorial_corpus as corpus_module
from mdh_pipeline.editorial_corpus import (
    CorpusError, STRATA, check_bindings, evaluate, load_snapshots, main, render_markdown, sample, validate_corpus,
)

ROOT = Path(__file__).resolve().parents[1]
MODEL = "gemini-3.5-flash-lite"
PROMPT = "mdh-news-scope-4-inclusive-violence"


def url(number):
    return f"https://www.diariocolatino.com/prueba-editorial-{number}/"


def ident(number):
    return hashlib.sha256(url(number).encode()).hexdigest()


def text_hash(number):
    return hashlib.sha256(f"texto ficticio {number}".encode()).hexdigest()


def label(jurisdiction="SV", relevance="relevant", event=("San Salvador",), mentioned=("San Salvador",), names=False):
    return {"jurisdiction": jurisdiction, "relevance": relevance, "event_departments": list(event),
            "mentioned_departments": list(mentioned), "evidence_paragraph_ids": ["p1"],
            "names_private_persons": names, "rationale": "Texto ficticio que describe un hecho pertinente."}


def item(number, *, value=None, status="proposed", stratum="selected-scope-4"):
    value = value or label()
    return {"candidate_id": ident(number), "source_url": url(number), "publisher": "Diario Co Latino",
            "published_date": "2015-03-26", "stratum": stratum,
            "text_binding": {"content_sha256": text_hash(number), "extraction_method": "wordpress_rest",
                             "truncated": False, "fetched_at": "2026-10-31T00:00:00+00:00"},
            "proposed_label": deepcopy(value), "label": deepcopy(value),
            "review": {"status": status, "proposed_by": "ai_assistant", "proposed_at": "2026-10-31T00:00:00+00:00",
                       "reviewer": "@revisora" if status == "confirmed" else None,
                       "reviewed_at": "2026-11-02T00:00:00+00:00" if status == "confirmed" else None, "notes": None}}


def corpus(*items):
    return {"schema_version": 1, "corpus_version": 1, "policy_reference": "inclusive-violence-1",
            "labeling_guide": "docs/EDITORIAL-CORPUS.md",
            "sampling": {"salt": "mdh-editorial-corpus-v1", "snapshot_commit": "a" * 40,
                         "strata": [{"id": stratum["id"], "size": stratum["size"]} for stratum in STRATA],
                         "replaced": []},
            "items": list(items)}


def reference(number, title="Denuncia de detención en El Salvador"):
    return {"id": ident(number), "title": title, "source_url": url(number), "publisher": "Diario Co Latino",
            "published_date": "2015-03-26", "scope_hint": "el_salvador"}


def selection(number, *, digest=None):
    return {"candidate_id": ident(number), "selection": {"model": MODEL, "prompt_version": PROMPT,
            "jurisdiction": "SV", "relevance": "relevant",
            "evidence": {"content_sha256": digest or text_hash(number)}}}


def dossier(number, status="reported", value="San Salvador", digest=None, persons="pending"):
    return {"candidate_id": ident(number), "schema_version": 2,
            "automation": {"evaluator_model": MODEL, "policy_version": "auto-news-v1",
                           "source_content_sha256": digest or text_hash(number)},
            "fields": {"department": {"status": status, "value": value if status == "reported" else None},
                       "affected_persons": {"status": persons}}}


def snapshots(numbers, *, selected=(), dossiers=(), locations=()):
    return {"history": {ident(n): reference(n) for n in numbers},
            "selection": {ident(n): selection(n) for n in selected},
            "dossiers": {row["candidate_id"]: row for row in dossiers},
            "locations": {row["candidate_id"]: row for row in locations}}


def test_versioned_corpus_is_valid_and_never_stores_bodies():
    # Identity drift is reported by `editorial_corpus check`; automated snapshot
    # updates must not fail because a labelled reference changed upstream.
    document = validate_corpus(json.loads((ROOT / "data/editorial-corpus/corpus.json").read_text()))
    assert {row["stratum"] for row in document["items"]} == {stratum["id"] for stratum in STRATA}
    assert len(document["items"]) == sum(stratum["size"] for stratum in STRATA)
    # The corpus never redistributes bodies: no item stores paragraphs or long text.
    serialized = json.dumps(document, ensure_ascii=False)
    assert '"paragraphs"' not in serialized and '"text"' not in serialized


def test_real_corpus_report_is_offline_and_separates_proposals():
    document = validate_corpus(json.loads((ROOT / "data/editorial-corpus/corpus.json").read_text()))
    report = evaluate(document, load_snapshots(ROOT))
    confirmed = sum(row["review"]["status"] == "confirmed" for row in document["items"])
    assert report["confirmed_items"] == confirmed
    proposals_only = deepcopy(document)
    for row in proposals_only["items"]:
        row["label"], row["review"] = deepcopy(row["proposed_label"]), {
            **row["review"], "status": "proposed", "reviewer": None, "reviewed_at": None}
    gated = evaluate(validate_corpus(proposals_only), load_snapshots(ROOT))
    assert {row["status"] for row in gated["gates"]} == {"insufficient_evidence"}
    assert gated["groups"]["confirmed"] == {}
    assert "Etiquetas propuestas" in render_markdown(gated)


def test_selection_metrics_count_errors_unclear_and_changed_text():
    rows = [item(1), item(2, value=label("other", event=(), mentioned=())),
            item(3, value=label(relevance="not_relevant")),
            item(4, value=label("unclear", "unclear", event=(), mentioned=())), item(5)]
    data = snapshots(range(1, 6), selected=range(1, 6))
    data["selection"][ident(5)] = selection(5, digest="b" * 64)
    cell = evaluate(corpus(*rows), data)["groups"]["provisional"]["selection"][f"{MODEL}|{PROMPT}"]
    assert (cell["correct"], cell["wrong_jurisdiction"], cell["wrong_relevance"], cell["label_unclear"],
            cell["text_changed"], cell["bound_items"]) == (1, 1, 1, 1, 1, 4)
    assert cell["jurisdiction_precision"] == round(2 / 3, 4)
    assert cell["relevance_precision"] == 0.5


def test_dossier_and_location_metrics_bind_to_the_labelled_text():
    rows = [item(1), item(2), item(3), item(4, value=label(names=True)), item(5)]
    locations = [
        {"candidate_id": ident(1), "method": "source_explicit_department", "rule_version": 2,
         "department": "San Salvador", "evidence": {"content_sha256": text_hash(1)}},
        {"candidate_id": ident(2), "method": "source_administrative_mentions", "rule_version": 2,
         "mentions": [{"department": "San Salvador"}, {"department": "La Paz"}], "evidence": {"content_sha256": text_hash(2)}},
        {"candidate_id": ident(3), "method": "source_explicit_department", "rule_version": 2,
         "department": "San Salvador", "evidence": {"content_sha256": "c" * 64}},
    ]
    dossiers = [dossier(1), dossier(2, value="La Paz"), dossier(3, status="not_reported"),
                dossier(4, persons="reported"), dossier(5, digest="d" * 64)]
    report = evaluate(corpus(*rows), snapshots(range(1, 6), dossiers=dossiers, locations=locations))
    group = report["groups"]["provisional"]
    department = group["dossier_department"][f"{MODEL}|auto-news-v1"]
    assert (department["correct"], department["incorrect"], department["false_absence"],
            department["text_changed"], department["precision"]) == (2, 1, 1, 1, round(2 / 3, 4))
    explicit = group["locations"]["source_explicit_department|rule-2"]
    mentions = group["locations"]["source_administrative_mentions|rule-2"]
    assert (explicit["faithful"], explicit["text_changed"], explicit["faithfulness"]) == (1, 1, 1.0)
    assert (mentions["unfaithful"], mentions["event_match"], mentions["faithfulness"]) == (1, 1, 0.0)
    assert report["privacy_inspection"] == {"confirmed": [], "provisional": [ident(4)]}


def test_only_confirmed_labels_reach_gates_and_small_cells_never_pass():
    numbers = range(1, 13)
    proposed = evaluate(corpus(*[item(n) for n in numbers]), snapshots(numbers, selected=numbers))
    assert {row["status"] for row in proposed["gates"]} == {"insufficient_evidence"}
    confirmed = evaluate(corpus(*[item(n, status="confirmed") for n in numbers]), snapshots(numbers, selected=numbers))
    statuses = {row["gate"]: row["status"] for row in confirmed["gates"] if row["cell"]}
    assert statuses == {"selection_jurisdiction_precision": "pass", "selection_relevance_precision": "pass"}
    wrong = [item(n, status="confirmed", value=label("other", event=(), mentioned=())) if n < 3 else item(n, status="confirmed")
             for n in numbers]
    failed = evaluate(corpus(*wrong), snapshots(numbers, selected=numbers))
    assert {row["gate"]: row["status"] for row in failed["gates"] if row["cell"]}[
        "selection_jurisdiction_precision"] == "fail"
    few = evaluate(corpus(*[item(n, status="confirmed") for n in range(1, 4)]), snapshots(range(1, 4), selected=range(1, 4)))
    assert {row["status"] for row in few["gates"]} == {"insufficient_evidence"}


def test_scope_hint_is_recomputed_from_current_code():
    data = snapshots([1])
    data["history"][ident(1)]["title"] = "Protestas en Venezuela"
    report = evaluate(corpus(item(1)), data)
    (cell,) = report["groups"]["provisional"]["scope_hint"].values()
    assert cell == {"international_context->SV": 1}


@pytest.mark.parametrize("mutate, code", [
    (lambda c: c["items"].append(deepcopy(c["items"][0])), "duplicate_candidate"),
    (lambda c: c["items"][0].update(source_url=url(99)), "candidate_url_mismatch"),
    (lambda c: c["items"][0]["label"].update(event_departments=["Atlántida"]), "invalid_event_departments"),
    (lambda c: c["items"][0]["label"].update(event_departments=["La Paz"]), "event_department_not_mentioned"),
    (lambda c: c["items"][0]["label"].update(evidence_paragraph_ids=[]), "missing_evidence_paragraphs"),
    (lambda c: c["items"][0]["label"].update(rationale='Cita "literal" del texto.'), "invalid_rationale"),
    (lambda c: c["items"][0]["label"].update(relevance="not_relevant"), "unreviewed_label_changed"),
    (lambda c: c["items"][0]["review"].update(status="confirmed"), "missing_human_reviewer"),
    (lambda c: c["items"][0]["review"].update(reviewer="@revisora"), "unreviewed_label_changed"),
    (lambda c: c["items"][0]["review"].update(proposed_by="human"), "invalid_proposer"),
    (lambda c: c["items"][0].update(extra=True), "invalid_item_keys"),
    (lambda c: c["sampling"].update(snapshot_commit="main"), "invalid_snapshot_commit"),
    (lambda c: c["sampling"]["strata"].pop(), "sampling_strata_changed"),
    (lambda c: c["sampling"]["replaced"].append({"candidate_id": ident(1), "reason": "source_text_too_short"}),
     "replaced_candidate_still_sampled"),
])
def test_validation_rejects_unsafe_or_unreviewed_changes(mutate, code):
    document = corpus(item(1))
    mutate(document)
    with pytest.raises(CorpusError, match=code):
        validate_corpus(document)


def test_confirmed_label_may_differ_from_the_proposal_and_keeps_it_for_agreement():
    row = item(1, status="confirmed")
    row["label"].update(relevance="unclear")
    assert validate_corpus(corpus(row))["items"][0]["proposed_label"]["relevance"] == "relevant"


def test_bindings_reject_changed_public_identity_and_reports_exclude_it():
    data = snapshots([1], selected=[1])
    data["history"][ident(1)]["published_date"] = "2015-03-27"
    with pytest.raises(CorpusError, match="candidate_identity_changed"):
        check_bindings(corpus(item(1)), data)
    with pytest.raises(CorpusError, match="candidate_not_in_public_history"):
        check_bindings(corpus(item(2)), data)
    report = evaluate(corpus(item(1), item(2)), data)
    assert report["excluded_identity_changes"] == dict(sorted({
        ident(1): "candidate_identity_changed", ident(2): "candidate_not_in_public_history"}.items()))
    assert report["groups"]["provisional"] == {}


def test_sampling_is_deterministic_exclusive_and_ordered_by_salted_rank():
    numbers = range(1, 80)
    data = snapshots(numbers, selected=numbers)
    for n in numbers:
        data["selection"][ident(n)]["selection"]["prompt_version"] = "mdh-news-scope-3-rights-nexus" if n % 2 else PROMPT
    rows = sample(data)
    assert rows == sample(deepcopy(data))
    assert len({row["candidate_id"] for row in rows}) == len(rows)
    scope3 = [row["candidate_id"] for row in rows if row["stratum"] == "selected-scope-3"]
    assert scope3 == sorted(scope3, key=corpus_module._rank) and len(scope3) == 8
    assert sample(data, excluded=scope3[:1])[0]["candidate_id"] != scope3[0]


def test_history_loader_excludes_restricted_publishers(tmp_path):
    (tmp_path / "data/history").mkdir(parents=True)
    restricted = {"records": [{"id": "e" * 64}]}
    (tmp_path / "data/history/la-prensa-grafica.json").write_text(json.dumps(restricted))
    (tmp_path / "data/history/co-latino.json").write_text(json.dumps({"records": [reference(1)]}))
    assert set(corpus_module._history(tmp_path)) == {ident(1)}


def test_cli_requires_gates_only_for_failed_confirmed_cells(tmp_path, capsys):
    numbers = range(1, 13)
    for folder in ("data/history", "data/selection", "data/automated", "apps/web/public/archive"):
        (tmp_path / folder).mkdir(parents=True)
    (tmp_path / "data/history/co-latino.json").write_text(json.dumps({"records": [reference(n) for n in numbers]}))
    (tmp_path / "data/selection/news.json").write_text(json.dumps({"selections": [selection(n) for n in numbers]}))
    (tmp_path / "data/automated/news_dossiers.json").write_text(json.dumps({"dossiers": []}))
    (tmp_path / "apps/web/public/archive/locations.json").write_text(json.dumps({"locations": []}))
    wrong = corpus(*[item(n, status="confirmed", value=label("other", event=(), mentioned=())) if n < 4
                     else item(n, status="confirmed") for n in numbers])
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(wrong))
    assert main(["check", "--root", str(tmp_path), "--corpus", str(path)]) == 0
    assert main(["report", "--root", str(tmp_path), "--corpus", str(path)]) == 0
    assert main(["report", "--root", str(tmp_path), "--corpus", str(path), "--require-gates"]) == 1
    assert main(["report", "--root", str(tmp_path), "--corpus", str(path), "--require-gates",
                 "--cell", "other|cell"]) == 0
    decisions = tmp_path / "decisions.json"
    decisions.write_text(json.dumps({"selections": [selection(n, digest="f" * 64) for n in numbers]}))
    assert main(["report", "--root", str(tmp_path), "--corpus", str(path), "--decisions", str(decisions),
                 "--require-gates", "--format", "json"]) == 0
    assert '"text_changed": 12' in capsys.readouterr().out
