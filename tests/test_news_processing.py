"""Public processing state is a source-bound allowlist, never a private draft."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json

import pytest

from mdh_pipeline.gemini_review import skeleton_envelope
from mdh_pipeline.history import SOURCES, reference_from_post
from mdh_pipeline.news_dossier import empty_news_dossier
from mdh_pipeline.news_processing import (
    NewsProcessingError, export_news_processing, project_news_processing, validate_collection,
)
from mdh_pipeline.news_selection import FULL_SCOPE_PROMPT

NOW = datetime(2026, 10, 14, 16, tzinfo=timezone.utc)
DIGEST = "c" * 64
PRIVATE = "PRIVATE_CANARY: personal source prose must never enter public metadata"


def reference(number=1, *, selected=True):
    result = reference_from_post({"id": number, "date": "2015-03-26T12:00:00",
        "link": f"https://www.diariocolatino.com/detencion-ficticia-{number}/",
        "title": {"rendered": "Denuncia de detención en El Salvador"}}, SOURCES[0],
        date(2015, 1, 1), date(2015, 12, 31), NOW.isoformat())
    if selected:
        result["selection"] = {"method": "gemini_grounded_scope", "review_status": "automated_unreviewed",
            "human_reviewed": False, "jurisdiction": "SV", "relevance": "relevant",
            "classified_at": "2026-10-13T12:00:00Z", "model": "gemini-3.5-flash-lite",
            "prompt_version": FULL_SCOPE_PROMPT,
            "evidence": {"content_sha256": DIGEST, "jurisdiction_paragraph_ids": ["p1"],
                         "relevance_paragraph_ids": ["p2"]}}
    return result


def archive(*references):
    return {"schema_version": 1, "records": list(references or [reference()])}


def draft(item=None):
    item = item or reference()
    news = empty_news_dossier(item, extraction_status="processed")
    news["fields"]["summary"] = {"value": PRIVATE, "status": "reported", "evidence_refs": ["primary"], "note": None}
    return {"schema_version": 1, "candidate_id": item["id"], "source_url": item["source_url"],
        "status": "needs_privacy_review", "publishable": False, "human_reviewed": False,
        "relevant": False, "record": None, "news_dossier": news,
        "classification": news["classification"], "privacy_flags": ["minor_identifiers"],
        "extraction": {"provider": "gemini", "content_sha256": DIGEST,
                       "prepared_at": "2026-10-13T14:01:00Z", "error_code": PRIVATE}}


def state(documents, bibliography=None, **kwargs):
    result = project_news_processing(documents, bibliography or archive(), now=NOW, **kwargs)
    return result["entries"][0]["status"]


def test_generated_dossier_requires_source_binding_and_never_exports_private_values():
    original = draft()
    before = deepcopy(original)
    result = project_news_processing([original], archive(), now=NOW)
    assert result["entries"][0]["status"] == "generated"
    assert result["entries"][0]["selection_content_sha256"] == DIGEST
    assert PRIVATE not in json.dumps(result)
    assert original == before
    assert set(result["entries"][0]) == {"candidate_id", "source_url", "publisher", "published_date",
                                        "selection_content_sha256", "status"}


def test_generation_is_not_inferred_from_selection_or_empty_template():
    assert state([], snapshot_complete=False) == "unknown"
    assert state([], snapshot_complete=True) == "pending"
    pending = skeleton_envelope(reference())
    pending["extraction"]["prepared_at"] = "2026-10-13T14:00:00Z"
    assert state([pending], snapshot_complete=True) == "pending"


@pytest.mark.parametrize("status", ["pending", "source_unavailable", "error"])
def test_valid_attempt_skeleton_without_source_text_retains_operational_status(status):
    document = skeleton_envelope(reference(), status, error_code=PRIVATE)
    document["extraction"]["prepared_at"] = "2026-10-13T14:00:00Z"
    assert state([document]) == status
    document["extraction"]["content_sha256"] = "d" * 64
    assert state([document]) == "unknown"


@pytest.mark.parametrize("digest", [None, "", "d" * 64, "not-a-content-hash", 5])
def test_unbound_or_stale_processed_dossier_is_unknown(digest):
    document = draft()
    if digest is None:
        document["extraction"].pop("content_sha256")
    else:
        document["extraction"]["content_sha256"] = digest
    assert state([document]) == "unknown"


@pytest.mark.parametrize("key,value", [("publisher", "A different publisher"), ("published_date", "2015-03-27"),
                                      ("source_url", "https://www.diariocolatino.com/different/")])
def test_generated_dossier_requires_matching_bibliographic_metadata(key, value):
    document = draft()
    document["news_dossier"][key] = value
    assert state([document]) == "unknown"


def test_public_truncated_title_and_provider_name_do_not_block_valid_source_binding():
    document = draft()
    document["news_dossier"]["source_title"] = "A longer original publisher title"
    document["extraction"]["provider"] = "legacy"
    assert state([document]) == "generated"


@pytest.mark.parametrize("path,value", [(("publishable",), True), (("human_reviewed",), True),
    (("status",), "error"), (("news_dossier", "fields", "summary", "evidence_refs"), ["missing"]),
    (("news_dossier", "extraction_status"), "pending"), (("extraction", "prepared_at"), "invalid"),
    (("extraction", "prepared_at"), "2099-01-01T00:00:00Z")])
def test_invalid_private_envelope_cannot_claim_generated(path, value):
    document = draft()
    target = document
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    assert state([document]) == "unknown"


def test_human_selection_without_text_hash_cannot_bind_generated_content():
    item = reference()
    item["selection"] = {"method": "editorial_scope", "review_status": "editorially_reviewed",
        "human_reviewed": True, "jurisdiction": "SV", "relevance": "relevant",
        "reviewed_by": "reviewer", "reviewed_at": "2026-10-13"}
    result = project_news_processing([draft(item)], archive(item), now=NOW)
    assert result["entries"][0]["selection_content_sha256"] is None
    assert result["entries"][0]["status"] == "unknown"


def test_only_selected_bibliography_is_projected_and_unrelated_private_drafts_do_not_leak():
    selected, excluded = reference(1), reference(2, selected=False)
    result = project_news_processing([draft(selected), draft(excluded)], archive(selected, excluded), now=NOW)
    assert [entry["candidate_id"] for entry in result["entries"]] == [selected["id"]]


def test_schema_rejects_extra_fields_duplicate_entries_and_current_source_mismatch():
    result = project_news_processing([draft()], archive(), now=NOW)
    extra = deepcopy(result)
    extra["entries"][0]["private_notes"] = PRIVATE
    duplicate = deepcopy(result)
    duplicate["entries"].append(deepcopy(duplicate["entries"][0]))
    mismatched = deepcopy(result)
    mismatched["entries"][0]["selection_content_sha256"] = "a" * 64
    for document in [extra, duplicate, mismatched]:
        with pytest.raises(NewsProcessingError):
            validate_collection(document, archive=archive())


def test_same_entries_preserve_timestamp_and_changed_selection_updates_it():
    first = project_news_processing([draft()], archive(), now=NOW)
    later = NOW + timedelta(hours=1)
    assert project_news_processing([draft()], archive(), previous=first, now=later) == first
    changed = reference()
    changed["selection"]["evidence"]["content_sha256"] = "d" * 64
    result = project_news_processing([draft()], archive(changed), previous=first, now=later)
    assert result["generated_at"] == later.isoformat()
    assert result["entries"][0]["status"] == "unknown"
    assert result["entries"][0]["selection_content_sha256"] == "d" * 64


def files(tmp_path):
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    source = tmp_path / "archive.json"
    source.write_text(json.dumps(archive()))
    output = tmp_path / "public" / "processing.json"
    return drafts, source, output


def test_atomic_export_skips_unchanged_file_and_no_network_is_needed(tmp_path):
    drafts, source, output = files(tmp_path)
    (drafts / (reference()["id"] + ".json")).write_text(json.dumps(draft()))
    first = export_news_processing(drafts, source, output, snapshot_complete=True, now=NOW)
    assert first["statuses"]["generated"] == 1
    assert first["changed"] is True
    timestamp = output.stat().st_mtime_ns
    before = output.read_bytes()
    second = export_news_processing(drafts, source, output, snapshot_complete=True, now=NOW + timedelta(hours=1))
    assert second["changed"] is False
    assert output.read_bytes() == before
    assert output.stat().st_mtime_ns == timestamp


@pytest.mark.parametrize("invalid_kind", ["json", "identity", "symlink", "oversize", "name", "duplicate_key", "nonfinite"])
def test_invalid_private_inventory_never_overwrites_public_state(tmp_path, invalid_kind):
    drafts, source, output = files(tmp_path)
    export_news_processing(drafts, source, output, snapshot_complete=True, now=NOW)
    before = output.read_bytes()
    destination = drafts / (reference()["id"] + ".json")
    if invalid_kind == "json":
        destination.write_text("{" + PRIVATE)
    elif invalid_kind == "identity":
        destination.write_text(json.dumps(draft(reference(2))))
    elif invalid_kind == "symlink":
        destination.symlink_to(source)
    elif invalid_kind == "oversize":
        destination.write_text(" " * 512_001)
    elif invalid_kind == "duplicate_key":
        content = json.dumps(draft())
        destination.write_text(content[:-1] + ', "status": "needs_privacy_review"}')
    elif invalid_kind == "nonfinite":
        content = json.dumps(draft())
        destination.write_text(content[:-1] + ', "unexpected_number": NaN}')
    else:
        (drafts / "wrong-name.json").write_text(json.dumps(draft()))
    with pytest.raises(NewsProcessingError) as error:
        export_news_processing(drafts, source, output, snapshot_complete=True, now=NOW + timedelta(hours=1))
    assert PRIVATE not in str(error.value)
    assert output.read_bytes() == before


def test_missing_directory_or_ambiguous_duplicate_private_identity_fails_closed(tmp_path):
    with pytest.raises(NewsProcessingError):
        export_news_processing(tmp_path / "absent", tmp_path / "archive", tmp_path / "public")
    with pytest.raises(NewsProcessingError):
        project_news_processing([draft(), draft()], archive(), snapshot_complete=True, now=NOW)
