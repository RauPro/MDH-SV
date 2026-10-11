"""Municipal map projection is sourced, disambiguated, private and retractable."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json

import pytest

from mdh_pipeline.automatic_locations import (
    AutomaticLocationError, MUNICIPAL_METHOD, project_automatic_locations, validate_collection,
)
from mdh_pipeline.history import SOURCES, reference_from_post
from mdh_pipeline.municipalities import resolve_municipality
from mdh_pipeline.news_dossier import empty_news_dossier

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
PRIVATE = "PRIVATE_PERSON_PHONE_ADDRESS_SENTINEL"


def sample(municipality="Jujutla", department=None, *, paragraphs=None,
           municipality_refs=("article-p1",), department_refs=("article-p1",)):
    reference = reference_from_post({
        "id": 501, "date": "2015-04-01T12:00:00",
        "link": "https://www.diariocolatino.com/noticia-municipio-501/",
        "title": {"rendered": "Denuncian detenciones en El Salvador"},
    }, SOURCES[0], date(2015, 4, 1), date(2015, 4, 30), NOW.isoformat())
    paragraphs = paragraphs or [f"La fuente informa detenciones en {municipality}."]
    text = "\n\n".join(paragraphs)
    digest = hashlib.sha256(text.encode()).hexdigest()
    dossier = empty_news_dossier(reference, extraction_status="processed")
    classification = {"jurisdiction": "SV", "relevance": "relevant", "record_type": "incident"}
    dossier["classification"] = classification
    dossier["evidence"].extend({
        "id": f"article-p{number}", "url": reference["source_url"],
        "locator": f"Párrafo p{number}; SHA-256 {digest}.",
    } for number in range(1, len(paragraphs) + 1))
    dossier["fields"]["municipality"] = {
        "status": "reported", "value": municipality,
        "evidence_refs": list(municipality_refs), "note": None,
    }
    if department is not None:
        dossier["fields"]["department"] = {
            "status": "reported", "value": department,
            "evidence_refs": list(department_refs), "note": None,
        }
    draft = {
        "schema_version": 1, "candidate_id": reference["id"],
        "source_url": reference["source_url"], "status": "draft",
        "publishable": False, "human_reviewed": False,
        "classification": deepcopy(classification), "news_dossier": dossier,
        "record": {"private_field": PRIVATE}, "grounding": {"quote": PRIVATE},
        "privacy_flags": [],
        "extraction": {"provider": "gemini", "prepared_at": (NOW - timedelta(hours=1)).isoformat(),
                       "content_sha256": digest, "privacy_policy_version": 2,
                       "protected_fields": []},
    }
    article = {
        "source_url": reference["source_url"], "published_date": reference["published_date"],
        "content_sha256": digest, "text": text,
        "paragraphs": [{"id": f"p{number}", "text": paragraph}
                       for number, paragraph in enumerate(paragraphs, 1)],
    }
    return reference, draft, article


def project(reference, draft, article, **options):
    return project_automatic_locations(
        [draft], {"schema_version": 1, "records": [reference]},
        fetch=lambda _: article, now=NOW, **options,
    )


def protect(draft, field):
    draft["extraction"]["protected_fields"].append(field)
    draft["news_dossier"]["fields"][field] = {
        "status": "withheld", "value": None, "evidence_refs": [],
        "note": "Información sensible protegida por privacidad.",
    }


def forbidden_fetch(_):
    raise AssertionError("Ineligible or unchanged drafts must not fetch a source")


@pytest.mark.parametrize(("municipality", "department", "kind", "paragraph"), [
    ("Jujutla", "Ahuachapán", "district", "Las detenciones ocurrieron en Jujutla."),
    ("San Salvador Centro", "San Salvador", "municipality",
     "El municipio de San Salvador Centro presentó denuncias de detenciones."),
    ("Antiguo Cuscatlán", "La Libertad", "district",
     "Se documentó el caso en el distrito de ANTIGUO CUSCATLAN."),
    ("Ciudad Arce", "La Libertad", "district", "En Ciudad Arce se reportaron detenciones."),
])
def test_historical_and_current_municipalities_map_without_a_reported_department(
        municipality, department, kind, paragraph):
    reference, draft, article = sample(municipality, paragraphs=[paragraph])
    document, stats = project(reference, draft, article)
    assert stats["locations"] == stats["sources_checked"] == 1
    entry = document["locations"][0]
    match = resolve_municipality(municipality)
    assert entry["department"] == department
    assert entry["method"] == MUNICIPAL_METHOD
    assert entry["resolution"] == {
        "type": "municipality_to_department", "municipality": match["name"],
        "municipality_kind": kind, "reference_version": match["reference_version"],
        "source_url": match["source_url"],
    }
    assert entry["published_date"] == "2015-04-01"  # No inferred event date or reform date.
    assert entry["human_reviewed"] is False
    assert entry["review_status"] == "automated_unreviewed"
    assert entry["evidence"] == {"paragraph_id": "p1", "content_sha256": article["content_sha256"]}
    assert PRIVATE not in json.dumps(document)
    assert stats["case_publications"] == stats["dossier_publications"] == 0


def test_homonymous_municipality_without_grounded_hint_is_not_guessed():
    reference, draft, article = sample("San Fernando")
    document, stats = project(reference, draft, article)
    assert document["locations"] == [] and stats["sources_checked"] == 0


def test_same_paragraph_department_disambiguates_historical_homonym():
    reference, draft, article = sample("San Fernando", "Chalatenango", paragraphs=[
        "Las familias denunciaron detenciones en San Fernando, Chalatenango.",
    ])
    document, _ = project(reference, draft, article)
    assert document["locations"][0]["method"] == MUNICIPAL_METHOD
    assert document["locations"][0]["department"] == "Chalatenango"


@pytest.mark.parametrize("department_refs", [("article-p1",), ("article-p2",)])
def test_department_in_another_paragraph_does_not_disambiguate_homonym(department_refs):
    reference, draft, article = sample("San Fernando", "Chalatenango", paragraphs=[
        "Las familias denunciaron detenciones en San Fernando.",
        "Otra organización de Chalatenango comentó el informe nacional.",
    ], department_refs=department_refs)
    assert project(reference, draft, article)[0]["locations"] == []


def test_ungrounded_model_hint_cannot_choose_between_homonyms():
    reference, draft, article = sample("San Fernando", "Chalatenango")
    assert project(reference, draft, article)[0]["locations"] == []


def test_department_conflict_with_unique_municipality_is_not_silently_corrected():
    reference, draft, article = sample("Jujutla", "La Paz", paragraphs=[
        "Las detenciones ocurrieron en Jujutla, según el informe del departamento de La Paz.",
    ])
    document, stats = project(reference, draft, article)
    assert document["locations"] == [] and stats["sources_checked"] == 0


@pytest.mark.parametrize("paragraph", [
    "El tribunal de Jujutla recibió una denuncia de otro país.",
    "El juzgado en Jujutla archivó una denuncia por hechos ocurridos fuera del país.",
    "En la carretera a Jujutla ocurrió el incidente.",
    "En la carretera que conduce a Jujutla ocurrió el incidente.",
    "Jujutla: una columna presenta una reflexión sobre hechos internacionales.",
    "La sede editorial en Jujutla publicó una crónica internacional.",
    "La sede de la Fiscalía en Jujutla recibió el informe de hechos ocurridos en Guatemala.",
    "Las detenciones ocurrieron en Jujutlita.",
])
def test_institution_seats_routes_datelines_and_partial_names_do_not_locate_a_case(paragraph):
    reference, draft, article = sample(paragraphs=[paragraph])
    assert project(reference, draft, article)[0]["locations"] == []


@pytest.mark.parametrize("paragraph", [
    "La alcaldía de Jujutla informó sobre denuncias locales de detenciones.",
    "El concejo municipal de Jujutla presentó un informe de derechos humanos.",
    "La alcaldía municipal de Jujutla informó sobre el caso.",
])
def test_local_municipality_as_the_subject_can_locate_its_own_report(paragraph):
    reference, draft, article = sample(paragraphs=[paragraph])
    assert project(reference, draft, article)[0]["locations"][0]["department"] == "Ahuachapán"


def test_municipality_only_in_title_or_uncited_paragraph_is_not_projected():
    reference, draft, article = sample(paragraphs=[
        "La organización publicó su informe nacional sobre derechos humanos.",
        "Las detenciones ocurrieron en Jujutla.",
    ])
    reference["title"] = "Denuncian detenciones en Jujutla"
    assert project(reference, draft, article)[0]["locations"] == []


def test_municipality_citation_is_required_even_when_source_contains_the_name():
    reference, draft, article = sample(municipality_refs=("primary",))
    assert project(reference, draft, article)[0]["locations"] == []


def test_a_changed_source_body_cannot_reuse_the_old_hash():
    reference, draft, article = sample()
    article["paragraphs"][0]["text"] = article["text"] = "Las detenciones ocurrieron en otro país."
    assert project(reference, draft, article)[0]["locations"] == []


@pytest.mark.parametrize("qualifier", ["California", "Costa Rica", "Colombia", "Estados Unidos", "Canadá", "Australia"])
def test_foreign_homonyms_are_not_mapped_despite_an_incorrect_model_jurisdiction(qualifier):
    reference, draft, article = sample("Santa Ana", paragraphs=[f"La noticia describe detenciones en Santa Ana, {qualifier}."])
    assert project(reference, draft, article)[0]["locations"] == []


@pytest.mark.parametrize("paragraph", [
    "Los hechos no ocurrieron en Santa Ana, sino en Guatemala.",
    "El hecho no fue en el municipio de Santa Ana.",
    "El caso nunca se registró en la ciudad de Santa Ana.",
])
def test_negated_event_location_is_not_treated_as_a_positive_mention(paragraph):
    reference, draft, article = sample("Santa Ana", paragraphs=[paragraph])
    assert project(reference, draft, article)[0]["locations"] == []


def test_negated_explicit_department_does_not_bypass_the_municipal_guard():
    reference, draft, article = sample("Santa Ana", "Santa Ana", paragraphs=[
        "Los hechos no ocurrieron en el departamento de Santa Ana, sino en otro lugar."])
    assert project(reference, draft, article)[0]["locations"] == []


def test_explicit_foreign_country_contradiction_stops_before_fetch():
    reference, draft, article = sample()
    draft["news_dossier"]["fields"]["country"] = {
        "value": "Guatemala", "status": "reported", "evidence_refs": ["article-p1"], "note": None,
    }
    result, stats = project(reference, draft, article)
    assert result["locations"] == [] and stats["sources_checked"] == 0


def test_field_level_privacy_preserves_safe_municipal_projection():
    reference, draft, article = sample()
    draft["status"] = "needs_privacy_review"
    draft["privacy_flags"] = ["minor_identifiers", "private_contact"]
    protect(draft, "affected_persons")
    document, stats = project(reference, draft, article)
    assert stats["locations"] == 1
    assert document["locations"][0]["department"] == "Ahuachapán"
    assert "privacy_flags" not in document["locations"][0]
    assert PRIVATE not in json.dumps(document)


@pytest.mark.parametrize("field", ["department", "municipality"])
def test_protected_geographic_fields_are_not_recovered_from_other_private_values(field):
    reference, draft, article = sample()
    draft["status"] = "needs_privacy_review"
    draft["privacy_flags"] = ["precise_address"]
    protect(draft, field)
    document, stats = project(reference, draft, article)
    assert document["locations"] == [] and stats["sources_checked"] == 0


@pytest.mark.parametrize("policy", [None, 1])
def test_legacy_privacy_flagged_drafts_require_reextraction_before_mapping(policy):
    reference, draft, article = sample()
    draft["privacy_flags"] = ["minor_identifiers"]
    if policy is None:
        draft["extraction"].pop("privacy_policy_version")
    else:
        draft["extraction"]["privacy_policy_version"] = policy
    document, stats = project(reference, draft, article)
    assert document["locations"] == [] and stats["sources_checked"] == 0


@pytest.mark.parametrize(("key", "value"), [
    ("type", "guessed_point"), ("municipality", "Jujutlita"),
    ("municipality", "Soyapango"), ("municipality_kind", "municipality"),
    ("reference_version", "sv-decree-762-2023-v999"),
    ("source_url", "https://untrusted.example/municipalities.json"),
])
def test_output_rejects_forged_resolution_provenance_or_mapping(key, value):
    reference, draft, article = sample()
    document, _ = project(reference, draft, article)
    document["locations"][0]["resolution"][key] = value
    with pytest.raises(AutomaticLocationError):
        validate_collection(document)


def test_output_rejects_reference_page_changed_to_an_unrelated_official_page():
    reference, draft, article = sample()
    document, _ = project(reference, draft, article)
    resolution = document["locations"][0]["resolution"]
    resolution["source_url"] = resolution["source_url"].split("#")[0] + "#page=13"
    with pytest.raises(AutomaticLocationError):
        validate_collection(document)


def test_output_rejects_other_department_even_with_an_existing_official_name():
    reference, draft, article = sample()
    document, _ = project(reference, draft, article)
    document["locations"][0]["department"] = "La Paz"
    with pytest.raises(AutomaticLocationError):
        validate_collection(document)


def test_unchanged_municipal_projection_reuses_exact_cache_without_fetching():
    reference, draft, article = sample()
    previous, _ = project(reference, draft, article)
    document, stats = project_automatic_locations(
        [draft], {"schema_version": 1, "records": [reference]}, previous=previous,
        fetch=forbidden_fetch, now=NOW + timedelta(hours=1),
    )
    assert document == previous and stats["cached"] == 1 and stats["sources_checked"] == 0


@pytest.mark.parametrize("cause", ["department_protected", "municipality_protected", "department_changed"])
def test_municipal_cache_withdrawn_after_private_geography_changes(cause):
    reference, draft, article = sample()
    previous, _ = project(reference, draft, article)
    if cause.endswith("_protected"):
        protect(draft, cause.removesuffix("_protected"))
    else:
        draft["news_dossier"]["fields"]["department"] = {
            "status": "reported", "value": "San Salvador", "note": None,
            "evidence_refs": ["article-p1"],
        }
    document, stats = project_automatic_locations(
        [draft], {"schema_version": 1, "records": [reference]}, previous=previous,
        fetch=forbidden_fetch, now=NOW + timedelta(hours=1),
    )
    assert document["locations"] == [] and stats["cached"] == 0 and stats["withdrawn"] == 1


def test_homonym_cache_rechecks_source_when_the_department_hint_changes():
    reference, draft, article = sample("San Fernando", "Chalatenango", paragraphs=[
        "Las detenciones ocurrieron en San Fernando, Chalatenango.",
    ])
    previous, _ = project(reference, draft, article)
    draft["news_dossier"]["fields"]["department"]["value"] = "Morazán"
    document, stats = project(reference, draft, article, previous=previous)
    assert document["locations"] == [] and stats["sources_checked"] == 1
    assert stats["cached"] == 0 and stats["withdrawn"] == 1
