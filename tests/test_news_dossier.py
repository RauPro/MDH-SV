import copy
import hashlib

import pytest

from mdh_pipeline.news_dossier import (
    FIELD_NAMES, NewsDossierError, _today, empty_news_dossier,
    from_case_record, from_legacy_record, validate_news_dossier,
)
from mdh_pipeline.publication import load_catalog


def candidate():
    url = "https://gatoencerrado.news/2026/09/01/noticia-de-prueba/"
    return {"id": hashlib.sha256(url.encode()).hexdigest(), "source_url": url,
            "publisher": "GatoEncerrado", "source_title": "Noticia de prueba para investigación",
            "published_date": "2026-09-01"}


def reported(value):
    return {"value": value, "status": "reported", "evidence_refs": ["primary"], "note": "Dato atribuido a la fuente consultada."}


@pytest.mark.parametrize("status", ["pending", "source_unavailable", "error"])
def test_every_valid_reference_has_all_fields_without_asserting_unread_content(status):
    document = empty_news_dossier(candidate(), extraction_status=status)
    assert document["kind"] == "news_dossier" and len(document["fields"]) == 18
    assert set(document["fields"]) == set(FIELD_NAMES)
    assert all(field["value"] is None and field["status"] == "pending" for field in document["fields"].values())
    assert document["classification"] == {"jurisdiction": "unclear", "relevance": "unclear", "record_type": None}
    assert document["human_reviewed"] is False
    assert document["candidate_id"] == candidate()["id"]
    assert validate_news_dossier(document) == document


def test_missing_publication_date_is_preserved_without_using_today():
    metadata = candidate()
    metadata["published_date"] = None
    document = empty_news_dossier(metadata)
    assert document["published_date"] is None and document["date_precision"] == "unknown"


def test_processed_foreign_news_does_not_require_a_violation_victim_or_salvadoran_department():
    document = empty_news_dossier(candidate(), "processed")
    document["classification"] = {"jurisdiction": "other", "relevance": "not_relevant", "record_type": None}
    document["fields"]["country"] = reported("Guatemala")
    document["fields"]["department"] = reported("Quetzaltenango")
    document["fields"]["alleged_violation"] = {"value": None, "status": "not_reported", "evidence_refs": [], "note": "Dato no mencionado en el texto consultado."}
    assert validate_news_dossier(document)["fields"]["department"]["value"] == "Quetzaltenango"


def test_protection_is_explicit_and_never_carries_hidden_values():
    document = empty_news_dossier(candidate(), "processed")
    document["fields"]["affected_persons"] = {"value": None, "status": "withheld", "evidence_refs": [], "note": "Información omitida por protección de identidad."}
    assert validate_news_dossier(document)["fields"]["affected_persons"]["status"] == "withheld"
    document["fields"]["affected_persons"]["value"] = ["Private identity"]
    with pytest.raises(NewsDossierError):
        validate_news_dossier(document)


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(candidate_id="a" * 64),
    lambda d: d.update(source_url="javascript:alert(1)"),
    lambda d: d["evidence"][0].update(url="https://unapproved.example/source"),
    lambda d: d["evidence"].append(copy.deepcopy(d["evidence"][0])),
    lambda d: d["fields"].pop("country"),
    lambda d: d["fields"]["facts"].update(value="Uncited value", status="reported"),
    lambda d: d["fields"]["facts"].update(value="Dangling citation", status="reported", evidence_refs=["missing"]),
    lambda d: d["fields"]["facts"].update(value="Hidden value", status="pending"),
    lambda d: d["fields"]["affected_persons"].update(value="Not a list", status="reported", evidence_refs=["primary"]),
])
def test_identity_field_types_and_source_binding_are_enforced(mutation):
    document = empty_news_dossier(candidate(), "processed")
    mutation(document)
    with pytest.raises(NewsDossierError):
        validate_news_dossier(document)


@pytest.mark.parametrize("field,value", [("schema_version", True), ("human_reviewed", "false"),
                                         ("human_reviewed", 1), ("published_date", 1_700_000_000),
                                         ("reviewed_at", 1_700_000_000), ("published_date", "2026-02-30")])
def test_machine_metadata_requires_exact_booleans_versions_and_iso_dates(field, value):
    document = empty_news_dossier(candidate())
    document[field] = value
    with pytest.raises(NewsDossierError):
        validate_news_dossier(document)


def test_unread_or_failed_source_cannot_claim_a_datum_was_absent():
    document = empty_news_dossier(candidate(), "source_unavailable")
    document["fields"]["facts"]["status"] = "not_reported"
    with pytest.raises(NewsDossierError):
        validate_news_dossier(document)


def test_publication_gate_requires_substantive_human_review_but_accepts_partial_news():
    document = empty_news_dossier(candidate(), "processed")
    document["classification"]["jurisdiction"] = "other"
    with pytest.raises(NewsDossierError, match="news_dossier_review_required"):
        validate_news_dossier(document, require_reviewed=True)
    document.update(human_reviewed=True, reviewed_by="Editor", reviewed_at=_today().isoformat(),
                    review_notes="Fuente y privacidad revisadas; los campos pendientes se conservan explícitos.")
    reviewed = validate_news_dossier(document, require_reviewed=True)
    assert reviewed["human_reviewed"] is True and reviewed["fields"]["alleged_violation"]["value"] is None
    document["review_notes"] = "OK"
    with pytest.raises(NewsDossierError):
        validate_news_dossier(document, require_reviewed=True)


def test_legacy_absence_migrates_as_pending_without_inventing_a_country_or_reviewer():
    record = next(r for r in load_catalog().records if r.id == "mdh-alejandro-henriquez-2025").model_dump(mode="json")
    document = from_case_record(record)
    assert from_legacy_record(record) == document
    assert document["extraction_status"] == "processed" and document["human_reviewed"] is False
    assert document["fields"]["country"]["status"] == "pending"
    assert document["fields"]["event_time"]["status"] == "pending"
    assert "no se ha evaluado" in document["fields"]["event_time"]["note"]
    assert document["fields"]["facts"]["evidence_refs"] == record["dossier"]["facts"]["evidence_refs"]
    assert document["evidence"] == record["dossier"]["evidence"]


def test_migration_preserves_protection_and_inapplicability_without_legacy_claim_references():
    record = next(r for r in load_catalog().records if r.id == "mdh-2022-001").model_dump(mode="json")
    record["dossier"]["location"]["municipality"]["evidence_refs"] = ["primary"]
    record["dossier"]["event_date_end"]["evidence_refs"] = ["primary"]
    document = from_case_record(record)
    assert document["fields"]["municipality"]["status"] == "withheld"
    assert document["fields"]["municipality"]["evidence_refs"] == []
    assert document["fields"]["event_date_end"]["status"] == "not_applicable"
    assert document["fields"]["event_date_end"]["evidence_refs"] == []


def test_review_cannot_predate_the_article():
    document = empty_news_dossier(candidate(), "processed")
    document.update(human_reviewed=True, reviewed_by="Editor", reviewed_at="2026-08-31",
                    review_notes="Notas de revisión ficticias que no acreditan una fecha imposible.")
    with pytest.raises(NewsDossierError):
        validate_news_dossier(document, require_reviewed=True)


def test_news_event_dates_are_valid_and_ordered_without_case_catalogue_geography_rules():
    document = empty_news_dossier(candidate(), "processed")
    document["fields"]["event_date"] = reported("2026-09-10")
    document["date_precision"] = "day"
    assert validate_news_dossier(document)["fields"]["event_date"]["value"] == "2026-09-10"
    document["fields"]["event_date_end"] = reported("2026-09-09")
    with pytest.raises(NewsDossierError):
        validate_news_dossier(document)


@pytest.mark.parametrize("mutation", [
    lambda d: d["fields"].update(facts=reported("a" * 2001)),
    lambda d: d["fields"].update(affected_persons=reported(["x"] * 41)),
    lambda d: d["fields"]["facts"].update(note="x" * 1001),
    lambda d: d["evidence"][0].update(locator="x" * 1001),
])
def test_private_document_components_have_explicit_size_bounds(mutation):
    document = empty_news_dossier(candidate(), "processed")
    mutation(document)
    with pytest.raises(NewsDossierError) as error:
        validate_news_dossier(document)
    assert str(error.value) == "invalid_news_dossier"
