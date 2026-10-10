import copy
import hashlib
import json
import stat
from datetime import date

import pytest

from mdh_pipeline.draft import history_candidate, prepare_draft, write_private_draft
from mdh_pipeline.publication import Publication

TODAY = date(2026, 10, 9)


def candidate():
    url = "https://gatoencerrado.news/2026/09/01/noticia-de-prueba/"
    return {"id": hashlib.sha256(url.encode()).hexdigest(), "source_url": url,
            "source_title": "Titular de prueba sobre una detención", "publisher": "GatoEncerrado",
            "published_date": "2026-09-01", "status": "pending",
            "matched_terms": ["detencion"], "private_note": "never copy this"}


@pytest.mark.parametrize("kind", ["incident", "aggregate", "publication"])
def test_template_is_complete_but_publication_rejects_it_without_human_review(kind):
    draft = prepare_draft(candidate(), record_type=kind, today=TODAY)
    with pytest.raises(ValueError, match="Collected candidates require explicit human review"):
        Publication.model_validate(draft["record"])
    # Check the remaining field shapes without removing the guard from the real draft.
    record = Publication.model_validate({**draft["record"], "origin_candidate_id": None})
    assert draft["status"] == "draft" and draft["publishable"] is False
    assert record.human_reviewed is False
    assert draft["record"]["origin_candidate_id"] == candidate()["id"]
    assert record.reviewed_at is None and record.reviewed_by is None
    assert "pendiente de lectura" in record.source_check_method
    assert record.dossier.evidence[0].url == candidate()["source_url"]
    assert all(field.value is None for _, field in record.dossier.evidence_fields())
    assert record.dossier.alleged_violation.status == "not_reported"
    assert record.dossier.location.department.status == ("not_applicable" if kind == "publication" else "not_reported")
    assert "private_note" not in json.dumps(draft) and "never copy this" not in json.dumps(draft)


def test_missing_publication_date_is_not_replaced_with_discovery_date():
    metadata = candidate()
    metadata.update(published_date=None, discovered_at="2026-10-09T12:00:00Z")
    with pytest.raises(ValueError, match="Publication date is unknown"):
        prepare_draft(metadata, record_type="incident", today=TODAY)
    draft = prepare_draft(metadata, record_type="incident", published_date=date(2026, 8, 3), today=TODAY)
    assert draft["record"]["published_date"] == "2026-08-03"
    assert draft["publication_date_origin"] == "operator"
    assert draft["record"]["incident_date"] is None


@pytest.mark.parametrize("changes", [
    {"id": "a" * 64}, {"published_date": "2027-01-01"}, {"published_date": "n/a"},
    {"source_url": "https://127.0.0.1/article"}, {"source_url": "javascript:alert(1)"},
    {"source_title": ""}, {"source_title": "x" * 301}, {"status": "rejected"},
    {"published_date": {"private": "must not pass through an operator override"}},
])
def test_invalid_or_already_rejected_metadata_fails(changes):
    metadata = candidate()
    metadata.update(changes)
    with pytest.raises(ValueError):
        prepare_draft(metadata, record_type="incident", today=TODAY)


def test_record_type_must_be_an_explicit_supported_choice():
    with pytest.raises(ValueError):
        prepare_draft(candidate(), record_type="automatically-confirmed", today=TODAY)


def test_date_override_cannot_copy_arbitrary_private_metadata():
    metadata = candidate()
    metadata["published_date"] = {"sensitive": "not metadata"}
    with pytest.raises(ValueError, match="Invalid source publication date"):
        prepare_draft(metadata, record_type="incident", published_date=TODAY, today=TODAY)


def test_history_reference_is_selected_exactly_and_accepts_title_key(tmp_path):
    metadata = candidate()
    metadata["title"] = metadata.pop("source_title")
    archive = tmp_path / "news.json"
    archive.write_text(json.dumps({"records": [metadata]}))
    selected = history_candidate(metadata["id"], archive)
    assert prepare_draft(selected, record_type="incident", today=TODAY)["record"]["source_title"] == metadata["title"]
    with pytest.raises(ValueError, match="missing or ambiguous"):
        history_candidate("b" * 64, archive)
    archive.write_text(json.dumps({"records": [metadata, copy.deepcopy(metadata)]}))
    with pytest.raises(ValueError, match="missing or ambiguous"):
        history_candidate(metadata["id"], archive)


def test_private_output_is_owner_only_and_cannot_overwrite_or_target_public(tmp_path):
    draft = prepare_draft(candidate(), record_type="incident", today=TODAY)
    private_root = tmp_path / ".local-quarantine/drafts"
    result = write_private_draft(draft, private_root=private_root)
    assert result.is_relative_to(private_root)
    assert stat.S_IMODE(result.stat().st_mode) == 0o600
    assert json.loads(result.read_text())["publishable"] is False
    with pytest.raises(FileExistsError):
        write_private_draft(draft, private_root=private_root)
    with pytest.raises(ValueError, match="inside .local-quarantine/drafts"):
        write_private_draft(draft, tmp_path / "apps/web/public/template.json", private_root=private_root)
    symlink = private_root / "public-link"
    symlink.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="inside .local-quarantine/drafts"):
        write_private_draft(draft, symlink / "public.json", private_root=private_root)


def test_draft_preparation_never_calls_network(monkeypatch):
    import requests
    def unexpected(*args, **kwargs):
        raise AssertionError("Draft templates must not fetch, call AI or publish")
    monkeypatch.setattr(requests.Session, "request", unexpected)
    draft = prepare_draft(candidate(), record_type="incident", today=TODAY)
    assert draft["record"]["dossier"]["facts"]["value"] is None


def test_manually_completed_draft_can_enter_the_existing_human_review_workflow(tmp_path, monkeypatch):
    from mdh_pipeline.review_queue import issue_body, review
    metadata = candidate()
    draft = prepare_draft(metadata, record_type="incident", today=TODAY)
    record = draft["record"]
    # Synthetic editorial edits for a unit test; no real case or approval is created.
    record.update(title="Ficha ficticia utilizada sólo en pruebas",
                  category="Libertad y debido proceso", tags=["Prueba de revisión"],
                  summary="Resumen ficticio redactado por el operador para comprobar el flujo de revisión.",
                  source_locator="Párrafo ficticio del artículo utilizado en esta prueba local.",
                  review_notes="Fuente, fechas y privacidad comprobadas por el editor ficticio de la prueba.")
    record["dossier"]["facts"] = {"value": record["summary"], "status": "reported",
                                   "evidence_refs": ["primary"], "note": "Contenido ficticio de prueba."}
    record["dossier"]["evidence"][0]["locator"] = record["source_locator"]
    class LocalInbox:
        base = "/repos/test/private-inbox"
        repository = "test/private-inbox"
        def call(self, method, path, **kwargs):
            if path.endswith("/permission"):
                return {"permission": "admin"}
            if method == "GET":
                return {"body": issue_body(metadata)}
            return {}
        def get_file(self, path):
            return None
    event = {"sender": {"login": "test-editor", "type": "User"},
             "inputs": {"issue_number": "1", "decision": "approve", "privacy_reviewed": "true",
                        "record_json": json.dumps(record)}}
    monkeypatch.chdir(tmp_path)
    result = review(LocalInbox(), event)
    proposal = json.loads((tmp_path / "proposal.json").read_text())
    reviewed = Publication.model_validate(proposal["record"])
    assert result["publications"] == 0
    assert reviewed.origin_candidate_id == metadata["id"]
    assert reviewed.human_reviewed is True and reviewed.reviewed_by == "test-editor"
