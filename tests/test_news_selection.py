"""Relevance selection projects provenance, never private prose or approval."""
from copy import deepcopy
from datetime import date, datetime, timezone
import json

import pytest

from mdh_pipeline.history import SOURCES, reference_from_post
from mdh_pipeline.news_dossier import empty_news_dossier
from mdh_pipeline.news_selection import (
    FULL_SCOPE_PROMPT, SCOPE_REVIEW_PROMPT, project_news_selection, select_archive,
    validate_manifest, validate_scope_review, validate_selection,
)

NOW = datetime(2026, 10, 12, 16, 0, tzinfo=timezone.utc)
SOURCE = SOURCES[0]


def reference(number=1, published="2025-03-26"):
    return reference_from_post({"id": number, "date": published + "T12:00:00",
        "link": f"https://www.diariocolatino.com/detencion-ficticia-{number}/",
        "title": {"rendered": "Denuncia de detención en El Salvador"}},
        SOURCE, date(2025, 3, 1), date(2025, 4, 30), NOW.isoformat())


def archive(*references):
    references = list(references or [reference()])
    months = ("2025-03", "2025-04")
    return {"schema_version": 1, "generated_at": NOW.isoformat(), "paper": {},
        "policy": {"record_type": "news_reference", "notice": "Candidate references"},
        "records": references, "coverage": {
            "records": len(references), "start_date": "2025-03-01", "end_date": "2025-04-30",
            "status": "partial", "timezone": "America/El_Salvador", "sources": [{
                "id": SOURCE["id"], "publisher": SOURCE["name"], "endpoint": SOURCE["endpoint"],
                "status": "partial", "records": len(references),
                "first_published_date": min(r["published_date"] for r in references),
                "last_published_date": max(r["published_date"] for r in references),
                "months": [{"month": month, "status": "enumerated", "posts_scanned": 100,
                            "records": sum(r["published_date"].startswith(month) for r in references)} for month in months],
            }], "months": [{"month": month, "records": sum(r["published_date"].startswith(month) for r in references),
                            "sources": [{"source_id": SOURCE["id"], "status": "enumerated",
                                "records": sum(r["published_date"].startswith(month) for r in references)}]} for month in months],
        }}


def draft(item=None):
    item = item or reference()
    news = empty_news_dossier(item, extraction_status="processed")
    scope = {"jurisdiction": "SV", "relevance": "relevant", "record_type": "publication"}
    news["classification"] = deepcopy(scope)
    return {"schema_version": 1, "candidate_id": item["id"], "source_url": item["source_url"],
        "status": "needs_scope_review", "publishable": False, "human_reviewed": False,
        "relevant": False, "record": None, "news_dossier": news, "classification": scope,
        "grounding": {"classification": {
            "jurisdiction": [{"paragraph_id": "p2", "quote_sha256": "a" * 64}],
            "relevance": [{"paragraph_id": "p3", "quote_sha256": "b" * 64}],
        }}, "extraction": {"provider": "gemini", "model": "gemini-3.5-flash-lite",
            "prompt_version": FULL_SCOPE_PROMPT,
            "content_sha256": "c" * 64, "fetched_at": "2026-10-12T14:00:00+00:00",
            "prepared_at": "2026-10-12T14:01:00+00:00"}}


def reviewed(item=None, **scope):
    value = draft(item)["news_dossier"]
    value["classification"].update(scope)
    value.update(human_reviewed=True, reviewed_by="editor", reviewed_at="2026-10-12",
                 review_notes="Revisión humana de la fuente, su alcance y sus datos protegidos.")
    return {"schema_version": 1, "dossiers": [value]}


def project(document=None, bibliography=None):
    return project_news_selection([document or draft()], bibliography or archive(), now=NOW)


def scope_review(item=None, **classification):
    item = item or reference()
    return {"schema_version": 1, "candidate_id": item["id"],
        **{key: item[key] for key in ("source_url", "publisher", "published_date")},
        "classification": {"jurisdiction": "SV", "relevance": "relevant", **classification},
        "evidence": {"content_sha256": "c" * 64, "jurisdiction_paragraph_ids": ["p2"],
                     "relevance_paragraph_ids": ["p3"]},
        "extraction": {"provider": "gemini", "model": "gemini-3.5-flash-lite",
                       "prompt_version": SCOPE_REVIEW_PROMPT, "prepared_at": "2026-10-12T14:02:00Z"}}


@pytest.mark.parametrize("prompt", ["mdh-private-grounded-5", "mdh-universal-news-grounded-6",
    "mdh-universal-news-grounded-7-field-privacy", "mdh-universal-news-grounded-9-relevance-scope", "mdh-news-scope-2"])
def test_legacy_and_unknown_future_relevance_policies_cannot_select_without_reassessment(prompt):
    value = draft(); value["extraction"]["prompt_version"] = prompt
    assert project(value)["selections"] == []
    selection = project()["selections"][0]["selection"]; selection["prompt_version"] = prompt
    with pytest.raises(ValueError):
        validate_selection(selection)


def test_bound_scope_reassessment_can_replace_legacy_negative_without_publishing_private_data():
    value = draft(); value["extraction"]["prompt_version"] = "mdh-universal-news-grounded-7-field-privacy"
    for classification in (value["classification"], value["news_dossier"]["classification"]):
        classification.update(jurisdiction="unclear", relevance="not_relevant")
    value["grounding"] = {}  # New scope evidence replaces the old decision's missing proof.
    value["private_note"] = "PRIVATE_SENTINEL"
    manifest = project_news_selection([value], archive(), now=NOW, scope_reviews=[scope_review()])
    assert len(manifest["selections"]) == 1
    selection = manifest["selections"][0]["selection"]
    assert selection["prompt_version"] == SCOPE_REVIEW_PROMPT
    assert selection["classified_at"] == "2026-10-12T14:02:00Z"
    assert "PRIVATE_SENTINEL" not in json.dumps(manifest)


@pytest.mark.parametrize("classification", [{"jurisdiction": "other"}, {"jurisdiction": "unclear"},
                                         {"relevance": "not_relevant"}, {"relevance": "unclear"}])
def test_new_negative_or_uncertain_reassessment_overrides_an_older_positive(classification):
    assessment = scope_review(**classification)
    for field in classification:
        assessment["evidence"][field + "_paragraph_ids"] = []
    validated = validate_scope_review(assessment, reference=reference(), content_sha256="c" * 64, now=NOW)
    assert validated == assessment
    assert project_news_selection([draft()], archive(), now=NOW, scope_reviews=[assessment])["selections"] == []


def test_negative_scope_without_paragraphs_stays_private_and_keeps_the_source_hash_guard():
    assessment = scope_review(jurisdiction="other", relevance="not_relevant")
    assessment["evidence"].update(jurisdiction_paragraph_ids=[], relevance_paragraph_ids=[])
    assert validate_scope_review(assessment, reference=reference(), content_sha256="c" * 64, now=NOW) == assessment
    assert project_news_selection([draft()], archive(), now=NOW, scope_reviews=[assessment])["selections"] == []
    with pytest.raises(ValueError):
        validate_scope_review(assessment, reference=reference(), content_sha256="d" * 64, now=NOW)


@pytest.mark.parametrize("field", ["jurisdiction", "relevance"])
def test_positive_private_scope_and_public_selection_always_require_paragraph_evidence(field):
    assessment = scope_review()
    assessment["evidence"][field + "_paragraph_ids"] = []
    with pytest.raises(ValueError):
        validate_scope_review(assessment, reference=reference(), content_sha256="c" * 64, now=NOW)
    selection = project()["selections"][0]["selection"]
    selection["evidence"][field + "_paragraph_ids"] = []
    with pytest.raises(ValueError):
        validate_selection(selection)


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(source_url="https://www.diariocolatino.com/otra-noticia/"),
    lambda d: d.update(publisher="Otro medio"),
    lambda d: d.update(published_date="2025-03-27"),
    lambda d: d.update(schema_version=True),
    lambda d: d.update(private_note="PRIVATE_SENTINEL"),
    lambda d: d["evidence"].update(content_sha256="d" * 64),
    lambda d: d["evidence"].update(relevance_paragraph_ids=[]),
    lambda d: d["evidence"].update(relevance_paragraph_ids=["p3", "p3"]),
    lambda d: d["evidence"].update(quote="PRIVATE_SENTINEL"),
    lambda d: d["extraction"].update(prompt_version="mdh-news-scope-2"),
    lambda d: d["extraction"].update(prepared_at="2027-01-01T00:00:00Z"),
    lambda d: d["classification"].update(relevance="somewhat_relevant"),
])
def test_invalid_reassessment_cannot_enable_legacy_or_fall_back_to_positive_v8(mutate):
    assessment = scope_review(); mutate(assessment)
    with pytest.raises(ValueError):
        validate_scope_review(assessment, reference=reference(), content_sha256="c" * 64, now=NOW)
    assert project_news_selection([draft()], archive(), now=NOW, scope_reviews=[assessment])["selections"] == []


@pytest.mark.parametrize("new_relevance,old_relevance,expected", [
    ("relevant", "not_relevant", 1), ("not_relevant", "relevant", 0),
])
def test_newer_full_v8_decision_supersedes_stale_same_text_reassessment(new_relevance, old_relevance, expected):
    value = draft(); value["extraction"]["prepared_at"] = "2026-10-12T14:03:00Z"
    for classification in (value["classification"], value["news_dossier"]["classification"]):
        classification["relevance"] = new_relevance
    manifest = project_news_selection([value], archive(), now=NOW, scope_reviews=[scope_review(relevance=old_relevance)])
    assert len(manifest["selections"]) == expected
    if expected:
        assert manifest["selections"][0]["selection"]["prompt_version"] == FULL_SCOPE_PROMPT


def test_same_time_conflicting_scope_decisions_require_review():
    value = draft(); value["extraction"]["prepared_at"] = "2026-10-12T14:02:00Z"
    assert project_news_selection([value], archive(), now=NOW,
        scope_reviews=[scope_review(relevance="not_relevant")])["selections"] == []


@pytest.mark.parametrize("country,expected", [("Guatemala", 0), ("El Salvador", 1), ("República de El Salvador", 1)])
def test_reported_country_contradiction_cannot_be_overridden_by_scope_classification(country, expected):
    value = draft()
    value["news_dossier"]["fields"]["country"].update(value=country, status="reported", evidence_refs=["primary"])
    assert len(project(value)["selections"]) == expected
    assert len(project_news_selection([value], archive(), now=NOW, scope_reviews=[scope_review()])["selections"]) == expected


def test_reassessment_cannot_promote_unknown_future_extraction_policy():
    value = draft(); value["extraction"]["prompt_version"] = "mdh-universal-news-grounded-9-new-policy"
    assert project_news_selection([value], archive(), now=NOW, scope_reviews=[scope_review()])["selections"] == []


def test_duplicate_reassessments_are_not_arbitrated_by_order():
    with pytest.raises(ValueError):
        project_news_selection([draft()], archive(), now=NOW, scope_reviews=[scope_review(), scope_review()])


def test_positive_projection_uses_universal_classification_and_exports_no_private_values():
    value = draft()
    value.update(privacy_flags=["minor_identifiers"], private_note="PRIVATE_SENTINEL")
    value["news_dossier"]["fields"]["facts"].update(status="withheld", note="PRIVATE_SENTINEL protegido.")
    before = deepcopy(value)
    manifest = project(value)
    assert len(manifest["selections"]) == 1  # envelope.relevant is deliberately false.
    selection = manifest["selections"][0]["selection"]
    assert selection["human_reviewed"] is False
    assert selection["review_status"] == "automated_unreviewed"
    assert selection["prompt_version"] == value["extraction"]["prompt_version"]
    assert selection["evidence"]["jurisdiction_paragraph_ids"] == ["p2"]
    serialized = json.dumps(manifest)
    assert all(key not in serialized for key in ("PRIVATE_SENTINEL", "minor_identifiers", "quote_sha256", "news_dossier"))
    assert value == before


@pytest.mark.parametrize("jurisdiction,relevance", [
    ("SV", "not_relevant"), ("SV", "unclear"), ("other", "relevant"), ("unclear", "relevant"),
])
def test_negative_or_uncertain_scope_never_enters_public_selection(jurisdiction, relevance):
    value = draft()
    for scope in (value["classification"], value["news_dossier"]["classification"]):
        scope.update(jurisdiction=jurisdiction, relevance=relevance)
    assert project(value)["selections"] == []


@pytest.mark.parametrize("mutate", [
    lambda d: d["grounding"].clear(),
    lambda d: d["grounding"]["classification"].update(relevance=[]),
    lambda d: d["grounding"]["classification"]["jurisdiction"][0].update(quote_sha256="bad"),
    lambda d: d["grounding"]["classification"]["jurisdiction"][0].update(quote="private prose"),
    lambda d: d["grounding"]["classification"]["jurisdiction"].append(d["grounding"]["classification"]["jurisdiction"][0].copy()),
    lambda d: d["classification"].update(jurisdiction="other"),
    lambda d: d["news_dossier"].update(publisher="Otro medio"),
    lambda d: d["news_dossier"].update(published_date="2025-03-27"),
    lambda d: d.update(source_url="https://www.diariocolatino.com/otra-noticia/"),
    lambda d: d.update(publishable=True),
    lambda d: d["extraction"].update(content_sha256="bad"),
    lambda d: d["extraction"].update(provider="another"),
    lambda d: d["extraction"].update(prepared_at="2027-01-01T00:00:00Z"),
    lambda d: d["extraction"].update(fetched_at="2026-10-12T15:00:00Z"),
    lambda d: d["extraction"].update(prompt_version=7),
    lambda d: d["extraction"].update(model="model?key=private"),
])
def test_invalid_or_unbound_drafts_are_excluded_without_echoing_them(mutate):
    value = draft(); mutate(value)
    assert project(value)["selections"] == []


@pytest.mark.parametrize("status", ["pending", "error", "source_unavailable"])
def test_unprocessed_or_failed_document_cannot_select_a_reference(status):
    value = draft(); value["status"] = status; value["news_dossier"]["extraction_status"] = status
    assert project(value)["selections"] == []


def test_human_rejected_candidate_ids_are_excluded_and_duplicates_are_not_arbitrated():
    assert project_news_selection([draft()], archive(), [reference()["id"]], NOW)["selections"] == []
    with pytest.raises(ValueError):
        project_news_selection([draft(), draft()], archive(), now=NOW)


@pytest.mark.parametrize("mutate", [
    lambda s: s.update(human_reviewed=0),
    lambda s: s.update(review_status="editorially_reviewed"),
    lambda s: s.update(jurisdiction="other"),
    lambda s: s.update(privacy_flags=[]),
    lambda s: s.update(prompt_version=True),
    lambda s: s.update(classified_at="2026-10-12T14:01:00"),
    lambda s: s.update(classified_at="2026-02-30T14:01:00Z"),
    lambda s: s["evidence"].update(relevance_paragraph_ids=[]),
    lambda s: s["evidence"].update(relevance_paragraph_ids=["p1", "p1"]),
    lambda s: s["evidence"].update(relevance_paragraph_ids=["p1000"]),
    lambda s: s["evidence"].update(quote="private prose"),
])
def test_public_selection_is_an_exact_typed_union(mutate):
    selection = project()["selections"][0]["selection"]; mutate(selection)
    with pytest.raises(ValueError):
        validate_selection(selection)


@pytest.mark.parametrize("mutate", [
    lambda m: m.update(schema_version=True),
    lambda m: m.update(private_note="private prose"),
    lambda m: m["selections"].append(deepcopy(m["selections"][0])),
    lambda m: m["selections"][0].update(published_date="2025-04-01"),
    lambda m: m["selections"][0].update(publisher="Otro medio"),
    lambda m: m["selections"][0].update(candidate_id="a" * 64),
])
def test_malformed_or_stale_manifest_fails_as_a_unit(mutate):
    manifest = project(); mutate(manifest)
    with pytest.raises(ValueError):
        validate_manifest(manifest, archive())


def test_selection_recounts_every_counter_and_preserves_collection_telemetry():
    first, discarded = reference(1), reference(2, "2025-04-27")
    bibliography = archive(first, discarded); before = deepcopy(bibliography)
    selected = select_archive(bibliography, project(draft(first), bibliography))
    assert bibliography == before
    assert len(selected["records"]) == selected["coverage"]["records"] == 1
    source = selected["coverage"]["sources"][0]
    assert source["records"] == 1 and source["first_published_date"] == source["last_published_date"] == "2025-03-26"
    assert [m["records"] for m in source["months"]] == [1, 0]
    assert [m["posts_scanned"] for m in source["months"]] == [100, 100]
    assert [m["records"] for m in selected["coverage"]["months"]] == [1, 0]
    assert [m["sources"][0]["records"] for m in selected["coverage"]["months"]] == [1, 0]
    assert selected["policy"]["selection_gate"] == {
        "version": 1, "scope": "human_rights_el_salvador", "candidates": 2, "selected": 1}
    assert selected["records"][0]["review_status"] == "not_reviewed"


def test_human_positive_scope_overrides_ai_and_can_select_a_previously_unselected_reference():
    bibliography = archive()
    empty = project_news_selection([], bibliography, now=NOW)
    for manifest in (empty, project()):
        selected = select_archive(bibliography, manifest, reviewed())
        selection = selected["records"][0]["selection"]
        assert selection == {"method": "editorial_scope", "review_status": "editorially_reviewed",
            "human_reviewed": True, "jurisdiction": "SV", "relevance": "relevant",
            "reviewed_by": "editor", "reviewed_at": "2026-10-12"}


@pytest.mark.parametrize("scope", [{"jurisdiction": "other"}, {"jurisdiction": "unclear"},
                                  {"relevance": "not_relevant"}, {"relevance": "unclear"}])
def test_human_negative_or_uncertain_decisions_suppress_positive_ai(scope):
    selected = select_archive(archive(), project(), reviewed(**scope))
    assert selected["records"] == [] and selected["coverage"]["records"] == 0
    source = selected["coverage"]["sources"][0]
    assert source["first_published_date"] is source["last_published_date"] is None


def test_unapproved_dossier_cannot_impersonate_a_human_scope_decision():
    collection = {"schema_version": 1, "dossiers": [draft()["news_dossier"]]}
    with pytest.raises(ValueError):
        select_archive(archive(), project(), collection)


def test_forged_human_manifest_cannot_promote_a_reference_without_editorial_collection():
    manifest = project()
    manifest["selections"][0]["selection"] = {
        "method": "editorial_scope", "review_status": "editorially_reviewed", "human_reviewed": True,
        "jurisdiction": "SV", "relevance": "relevant", "reviewed_by": "forged-editor", "reviewed_at": "2026-10-12"}
    # The public union supports real human decisions; the automated manifest must not.
    validate_selection(manifest["selections"][0]["selection"])
    with pytest.raises(ValueError):
        validate_manifest(manifest, archive())
    with pytest.raises(ValueError):
        select_archive(archive(), manifest, {"schema_version": 1, "dossiers": []})


def test_source_title_may_be_minimized_without_breaking_bibliographic_binding():
    value = draft(); value["news_dossier"]["source_title"] = "Título protegido por privacidad"
    assert len(project(value)["selections"]) == 1


def test_unknown_and_duplicate_archive_records_fail_closed():
    bibliography = archive(reference(), reference())
    with pytest.raises(ValueError):
        project(draft(), bibliography)
    bibliography = archive(); bibliography["records"][0]["source_id"] = "unapproved"
    with pytest.raises(ValueError):
        project(draft(), bibliography)


def test_missing_coverage_rows_cannot_create_inconsistent_public_counters():
    bibliography = archive(); bibliography["coverage"]["sources"][0]["months"].pop()
    with pytest.raises(ValueError):
        select_archive(bibliography, project())


def test_manifest_cli_honors_human_exclusions_without_serializing_private_drafts(tmp_path, monkeypatch, capsys):
    from mdh_pipeline.news_selection import main
    drafts = tmp_path / "drafts"; drafts.mkdir()
    first, second = reference(1), reference(2)
    for item in (first, second):
        value = draft(item); value["private_note"] = "PRIVATE_SENTINEL"
        (drafts / (item["id"] + ".json")).write_text(json.dumps(value))
    bibliography = tmp_path / "archive.json"; bibliography.write_text(json.dumps(archive(first, second)))
    excluded = tmp_path / "excluded.json"; excluded.write_text(json.dumps([second["id"]]))
    output = tmp_path / "selection.json"
    monkeypatch.setattr("sys.argv", ["news_selection", "--drafts", str(drafts), "--archive", str(bibliography),
                                     "--output", str(output), "--excluded-ids", str(excluded)])
    main()
    manifest = validate_manifest(json.loads(output.read_text()), archive(first, second))
    assert [entry["candidate_id"] for entry in manifest["selections"]] == [first["id"]]
    assert "PRIVATE_SENTINEL" not in output.read_text() + capsys.readouterr().out
    assert not output.with_suffix(".json.tmp").exists()


def test_manifest_cli_failure_keeps_previous_output_and_sanitizes_error(tmp_path, monkeypatch):
    from mdh_pipeline.news_selection import main
    drafts = tmp_path / "drafts"; drafts.mkdir()
    (drafts / (reference()["id"] + ".json")).write_text("PRIVATE_SENTINEL invalid JSON")
    bibliography = tmp_path / "archive.json"; bibliography.write_text(json.dumps(archive()))
    output = tmp_path / "selection.json"; output.write_text("previous valid output")
    monkeypatch.setattr("sys.argv", ["news_selection", "--drafts", str(drafts), "--archive", str(bibliography),
                                     "--output", str(output)])
    with pytest.raises(SystemExit) as error:
        main()
    assert "PRIVATE_SENTINEL" not in str(error.value)
    assert output.read_text() == "previous valid output"


@pytest.fixture
def cli_files(tmp_path, monkeypatch):
    drafts = tmp_path / "drafts"; drafts.mkdir()
    document = drafts / (reference()["id"] + ".json"); document.write_text(json.dumps(draft()))
    bibliography = tmp_path / "archive.json"; bibliography.write_text(json.dumps(archive()))
    excluded = tmp_path / "excluded.json"; excluded.write_text("[]")
    output = tmp_path / "selection.json"; output.write_text("previous")
    monkeypatch.setattr("sys.argv", ["news_selection", "--drafts", str(drafts), "--archive", str(bibliography),
                                     "--output", str(output), "--excluded-ids", str(excluded)])
    return {"drafts": drafts, "document": document, "archive": bibliography, "excluded": excluded, "output": output}


@pytest.mark.parametrize("target", ["drafts", "document"])
def test_cli_rejects_symlink_draft_directory_or_file(cli_files, target):
    from mdh_pipeline.news_selection import main
    original = cli_files[target]
    moved = original.with_name(original.name + "-real")
    original.rename(moved)
    original.symlink_to(moved, target_is_directory=target == "drafts")
    with pytest.raises(SystemExit):
        main()
    assert cli_files["output"].read_text() == "previous"


@pytest.mark.parametrize("kind", ["invalid_filename", "mismatched_id", "duplicate_id"])
def test_cli_requires_hex_filenames_matching_unique_candidate_ids(cli_files, kind):
    from mdh_pipeline.news_selection import main
    path = cli_files["document"]
    if kind == "invalid_filename":
        path.rename(path.with_name("not-a-candidate.json"))
    elif kind == "mismatched_id":
        value = draft(); value["candidate_id"] = "a" * 64; path.write_text(json.dumps(value))
    else:
        path.with_name("a" * 64 + ".json").write_bytes(path.read_bytes())
    with pytest.raises(SystemExit):
        main()
    assert cli_files["output"].read_text() == "previous"


@pytest.mark.parametrize("target", ["archive", "excluded"])
def test_cli_bounds_archive_and_exclusion_json_before_parsing(cli_files, target, monkeypatch):
    from mdh_pipeline.news_selection import main
    monkeypatch.setattr("mdh_pipeline.news_selection.MAX_JSON_BYTES", 5000)
    cli_files[target].write_bytes(b" " * 5001)
    with pytest.raises(SystemExit):
        main()
    assert cli_files["output"].read_text() == "previous"


def test_cli_accepts_legitimate_wrapper_over_180k_but_limits_drafts_to_512k(cli_files):
    from mdh_pipeline.news_selection import MAX_DRAFT_BYTES, main
    value = draft(); value["private_note"] = "x" * 200_000
    cli_files["document"].write_text(json.dumps(value))
    main()
    assert len(json.loads(cli_files["output"].read_text())["selections"]) == 1
    previous = cli_files["output"].read_bytes()
    cli_files["document"].write_bytes(b" " * (MAX_DRAFT_BYTES + 1))
    with pytest.raises(SystemExit):
        main()
    assert cli_files["output"].read_bytes() == previous


def test_cli_reassesses_legacy_from_private_files_and_writes_only_positive_projection(cli_files, monkeypatch):
    import sys
    from mdh_pipeline.news_selection import main
    value = draft(); value["extraction"]["prompt_version"] = "mdh-private-grounded-5"
    cli_files["document"].write_text(json.dumps(value))
    reviews = cli_files["drafts"].parent / "scope-reviews"; reviews.mkdir()
    review_file = reviews / cli_files["document"].name
    review_file.write_text(json.dumps(scope_review()))
    monkeypatch.setattr("sys.argv", sys.argv + ["--scope-reviews", str(reviews)])
    main()
    document = json.loads(cli_files["output"].read_text())
    assert len(document["selections"]) == 1
    assert document["selections"][0]["selection"]["prompt_version"] == SCOPE_REVIEW_PROMPT
    review_file.write_text(json.dumps(scope_review(relevance="not_relevant")))
    main()
    assert json.loads(cli_files["output"].read_text())["selections"] == []


def test_cli_scope_review_directory_symlink_is_rejected(cli_files, monkeypatch):
    import sys
    from mdh_pipeline.news_selection import main
    reviews = cli_files["drafts"].parent / "scope-reviews"
    reviews.symlink_to(cli_files["drafts"], target_is_directory=True)
    monkeypatch.setattr("sys.argv", sys.argv + ["--scope-reviews", str(reviews)])
    with pytest.raises(SystemExit):
        main()
    assert cli_files["output"].read_text() == "previous"
