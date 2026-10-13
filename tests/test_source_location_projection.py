"""Independent administrative evidence remains source-bound and privacy-minimized."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json

import pytest

from mdh_pipeline.article_source import SourceFetchError
from mdh_pipeline.automatic_locations import (
    AutomaticLocationError, export_automatic_locations, project_automatic_locations,
    validate_collection, validate_negative_cache,
)
from test_automatic_locations import NOW, PRIVATE, forbidden_fetch, sample


def selected_sample(number=1, text="Las organizaciones documentaron denuncias en el departamento de Cabañas."):
    reference, draft, article = sample(number, department="Cabañas", paragraph=text)
    reference["selection"] = {"method": "gemini_grounded_scope", "review_status": "automated_unreviewed",
        "human_reviewed": False, "jurisdiction": "SV", "relevance": "relevant",
        "classified_at": (NOW - timedelta(hours=1)).isoformat(), "model": "gemini-3.5-flash-lite",
        "prompt_version": "mdh-news-scope-3-rights-nexus", "evidence": {
            "content_sha256": article["content_sha256"], "jurisdiction_paragraph_ids": ["p1"],
            "relevance_paragraph_ids": ["p1"]}}
    return reference, draft, article


def project_source(reference, article, *, drafts=(), **kwargs):
    options = {"now": NOW, "fetch": lambda _: article, **kwargs}
    return project_automatic_locations(list(drafts), {"schema_version": 1, "records": [reference]}, **options)


def protect(draft, field):
    draft["news_dossier"]["fields"][field].update(value=None, status="withheld", evidence_refs=[], note="Dato protegido por privacidad.")
    draft["extraction"].update(privacy_policy_version=2, protected_fields=[field])
    draft["status"] = "needs_privacy_review"


def test_selected_source_without_any_dossier_emits_minimal_department_evidence():
    reference, _, article = selected_sample()
    result, stats = project_source(reference, article)
    assert result["schema_version"] == 5 and stats["ready_without_dossier"] == 1
    location = result["locations"][0]
    assert location["method"] == "source_explicit_department" and location["rule_version"] == 2
    assert location["department"] == "Cabañas" and location["human_reviewed"] is False
    assert location["evidence"] == {"paragraph_id": "p1", "content_sha256": article["content_sha256"]}
    assert stats["source_methods"]["source_explicit_department"] == 1
    assert "gemini" not in json.dumps(result).lower() and PRIVATE not in json.dumps(result)
    assert validate_collection(result, archive={"schema_version": 1, "records": [reference]}) == result
    cached, stats = project_source(reference, article, previous=result, fetch=forbidden_fetch)
    assert cached == result and stats["cached"] == 1 and stats["sources_checked"] == 0


def test_source_municipality_uses_official_resolution_without_a_private_model_claim():
    reference, _, article = selected_sample(text="Las organizaciones trabajan en el municipio de Nahuizalco.")
    result, stats = project_source(reference, article)
    location = result["locations"][0]
    assert location["method"] == "source_municipality_reference"
    assert location["department"] == "Sonsonate" and location["resolution"]["municipality"] == "Nahuizalco"
    assert stats["source_methods"]["source_municipality_reference"] == 1


def multiple_sample():
    return selected_sample(text="Las denuncias se registraron en los departamentos de Cabañas y La Paz.")


def test_multiple_source_departments_keep_each_proof_without_inventing_a_primary_place():
    reference, _, article = multiple_sample()
    result, stats = project_source(reference, article)
    location = result["locations"][0]
    assert location["method"] == "source_administrative_mentions"
    assert location["rule_version"] == 2 and "department" not in location
    assert location["evidence"] == {"content_sha256": article["content_sha256"]}
    assert [item["department"] for item in location["mentions"]] == ["Cabañas", "La Paz"]
    assert all(item["paragraph_id"] == "p1" for item in location["mentions"])
    assert stats["locations"] == 1 and stats["source_methods"]["source_administrative_mentions"] == 1
    cached, stats = project_source(reference, article, previous=result, fetch=forbidden_fetch)
    assert cached == result and stats["cached"] == 1


@pytest.mark.parametrize("cause", ["private_field", "duplicate", "single", "wrong_resolution", "wrong_paragraph", "primary_place", "schema", "rule", "hash"])
def test_multiple_source_allowlist_rejects_ambiguity_and_private_data(cause):
    reference, _, article = multiple_sample()
    result, _ = project_source(reference, article)
    value = result["locations"][0]
    if cause == "private_field": value["mentions"][0]["quote"] = PRIVATE
    if cause == "duplicate": value["mentions"][1] = deepcopy(value["mentions"][0])
    if cause == "single": value["mentions"].pop()
    if cause == "wrong_resolution": value["mentions"][0]["resolution"] = {"municipality": "Lugar inventado"}
    if cause == "wrong_paragraph": value["mentions"][0]["paragraph_id"] = "p0"
    if cause == "primary_place": value["department"] = "Cabañas"
    if cause == "schema": result["schema_version"] = 4
    if cause == "rule": value["rule_version"] = 1
    if cause == "hash": value["evidence"]["content_sha256"] = "d" * 64
    with pytest.raises(AutomaticLocationError):
        validate_collection(result, archive={"schema_version": 1, "records": [reference]})


def test_privacy_recheck_removes_municipal_parts_but_retains_safe_explicit_department():
    reference, draft, article = selected_sample(text=(
        "Las denuncias se registraron en el municipio de Nahuizalco. "
        "El informe también documenta denuncias en el departamento de Cabañas."))
    previous, _ = project_source(reference, article)
    assert len(previous["locations"][0]["mentions"]) == 2
    protect(draft, "municipality")
    draft["news_dossier"]["fields"]["department"].update(value=None, status="pending", evidence_refs=[])
    result, stats = project_source(reference, article, drafts=[draft], previous=previous)
    assert stats["sources_checked"] == 1 and stats["cached"] == 0
    assert result["locations"][0]["department"] == "Cabañas"
    assert "resolution" not in result["locations"][0]


def test_legacy_source_rule_is_readable_but_rechecked_after_rule_change():
    reference, _, article = selected_sample()
    previous, _ = project_source(reference, article)
    previous["schema_version"] = 4
    previous["locations"][0]["rule_version"] = 1
    assert validate_collection(previous, archive={"schema_version": 1, "records": [reference]})
    result, stats = project_source(reference, article, previous=previous)
    assert stats["cached"] == 0 and stats["sources_checked"] == 1
    assert result["locations"][0]["rule_version"] == 2


def test_existing_supported_model_projection_is_preferred_with_no_extra_read():
    reference, draft, article = selected_sample()
    result, stats = project_source(reference, article, drafts=[draft])
    assert result["locations"][0]["method"] == "gemini_explicit_department"
    assert stats["sources_checked"] == 1 and not any(stats["source_methods"].values())


def test_one_supported_model_department_does_not_hide_other_supported_departments():
    reference, draft, article = multiple_sample()
    reads = []
    result, stats = project_source(reference, article, drafts=[draft], fetch=lambda r: reads.append(r["id"]) or article)
    assert result["locations"][0]["method"] == "source_administrative_mentions"
    assert {item["department"] for item in result["locations"][0]["mentions"]} == {"Cabañas", "La Paz"}
    assert len(reads) == stats["sources_checked"] == 1


def test_model_department_alone_cannot_restore_a_contradictory_administrative_claim():
    reference, draft, article = selected_sample(text="El informe sitúa Metapán, en el departamento de Cabañas.")
    # The model only filled the department; the article's municipality still
    # contradicts that assignment and must prevent publication.
    assert draft["news_dossier"]["fields"]["municipality"]["status"] != "reported"
    result, stats = project_source(reference, article, drafts=[draft])
    assert result["locations"] == [] and stats["unsupported"] == 1


def test_unsupported_model_citation_falls_back_to_literal_evidence_in_the_same_read():
    reference, draft, article = selected_sample(text="El tribunal de Cabañas presentó un informe.")
    article["paragraphs"].append({"id": "p2", "text": "Las denuncias se registraron en el departamento de Cabañas."})
    article["text"] = "\n\n".join(p["text"] for p in article["paragraphs"])
    digest = hashlib.sha256(article["text"].encode()).hexdigest()
    article["content_sha256"] = draft["extraction"]["content_sha256"] = reference["selection"]["evidence"]["content_sha256"] = digest
    draft["news_dossier"]["evidence"][1]["locator"] = f"Párrafo p1 consultado; SHA-256 {digest}."
    original = deepcopy(draft); reads = []
    result, stats = project_source(reference, article, drafts=[draft], fetch=lambda r: reads.append(r["id"]) or article)
    assert result["locations"][0]["method"] == "source_explicit_department"
    assert result["locations"][0]["evidence"]["paragraph_id"] == "p2"
    assert len(reads) == stats["sources_checked"] == 1 and draft == original


@pytest.mark.parametrize("department,text", [
    ("La Libertad", "Personas salvadoreñas denunciaron detenciones arbitrarias en el departamento de La Libertad, Perú."),
    ("San Salvador", "El departamento de San Salvador del Ministerio de Trabajo emitió un documento."),
    ("San Salvador", "La persona es originaria del departamento de San Salvador."),
    ("San Salvador", "La sede de la asociación está en el departamento de San Salvador."),
    ("San Salvador", "El departamento de San Salvador del Ministerio emitió un texto. El documento menciona San Salvador."),
    ("La Libertad", "En Perú hubo denuncias. El departamento de La Libertad registró casos."),
])
def test_model_department_cannot_revive_an_explicit_foreign_or_nonterritorial_source_context(department, text):
    reference, draft, article = sample(department=department, paragraph=text)
    reference["selection"] = selected_sample()[0]["selection"]
    reference["selection"]["evidence"]["content_sha256"] = article["content_sha256"]
    assert draft["news_dossier"]["fields"]["country"]["status"] == "pending"
    result, stats = project_source(reference, article, drafts=[draft])
    assert result["locations"] == [] and stats["sources_checked"] == 1 and stats["unsupported"] == 1


def test_unrelated_repeated_municipality_cannot_legitimize_an_institutional_anchor():
    reference, draft, article = selected_sample(text="El foro fue en Nahuizalco de la universidad. El informe menciona Nahuizalco.")
    draft["news_dossier"]["fields"]["department"].update(value=None, status="pending", evidence_refs=[])
    draft["news_dossier"]["fields"]["municipality"].update(value="Nahuizalco", status="reported", evidence_refs=["article-p1"])
    result, stats = project_source(reference, article, drafts=[draft])
    assert result["locations"] == [] and stats["unsupported"] == 1


def test_foreign_comparison_in_another_paragraph_does_not_veto_valid_model_department_anchor():
    reference, draft, article = selected_sample()
    article["paragraphs"].append({"id": "p2", "text": "En Perú existe otra iniciativa usada como comparación."})
    article["text"] = "\n\n".join(p["text"] for p in article["paragraphs"])
    digest = hashlib.sha256(article["text"].encode()).hexdigest()
    article["content_sha256"] = draft["extraction"]["content_sha256"] = reference["selection"]["evidence"]["content_sha256"] = digest
    draft["news_dossier"]["evidence"][1]["locator"] = f"Párrafo p1 consultado; SHA-256 {digest}."
    result, stats = project_source(reference, article, drafts=[draft])
    assert result["locations"][0]["department"] == "Cabañas"
    assert result["locations"][0]["method"] == "gemini_explicit_department"
    assert result["locations"][0]["evidence"]["paragraph_id"] == "p1" and stats["sources_checked"] == 1


def test_model_corroboration_cannot_borrow_an_explicit_department_from_a_foreign_paragraph():
    reference, draft, article = selected_sample(text="La comunidad denunció abusos en Cabañas.")
    article["paragraphs"].append({"id": "p2", "text": "El operativo se realizó en el departamento de Cabañas. El texto indica que sucedió en Honduras."})
    article["text"] = "\n\n".join(p["text"] for p in article["paragraphs"])
    digest = hashlib.sha256(article["text"].encode()).hexdigest()
    article["content_sha256"] = draft["extraction"]["content_sha256"] = reference["selection"]["evidence"]["content_sha256"] = digest
    draft["news_dossier"]["evidence"][1]["locator"] = f"Párrafo p1 consultado; SHA-256 {digest}."
    result, stats = project_source(reference, article, drafts=[draft])
    assert result["locations"] == [] and stats["unsupported"] == 1


@pytest.mark.parametrize("cause", ["excluded", "protected_department", "withheld_department", "foreign_country",
                                  "legacy_privacy", "malformed_privacy", "contradictory_envelope"])
def test_independent_route_never_bypasses_explicit_exclusion_or_private_geography_guard(cause):
    reference, draft, article = selected_sample()
    previous, _ = project_source(reference, article)
    excluded = []
    if cause == "excluded": excluded = [reference["id"]]
    if cause == "protected_department": draft["extraction"]["protected_fields"] = ["department"]
    if cause == "withheld_department": protect(draft, "department")
    if cause == "foreign_country":
        draft["news_dossier"]["fields"]["country"].update(value="Guatemala", status="reported", evidence_refs=["primary"])
    if cause == "legacy_privacy": draft["privacy_flags"] = ["minor_identifiers"]
    if cause == "malformed_privacy": draft["extraction"]["protected_fields"] = "department"
    if cause == "contradictory_envelope": draft["classification"]["jurisdiction"] = "other"
    current, stats = project_source(reference, article, drafts=[draft], previous=previous,
                                    excluded_ids=excluded, fetch=forbidden_fetch)
    assert current["locations"] == [] and stats["sources_checked"] == 0 and stats["withdrawn"] == 1


def test_protected_municipality_blocks_resolution_but_preserves_independent_safe_department():
    municipal, draft, article = selected_sample(text="Las organizaciones trabajan en el municipio de Nahuizalco.")
    previous, _ = project_source(municipal, article)
    protect(draft, "municipality")
    draft["news_dossier"]["fields"]["department"].update(value=None, status="pending", evidence_refs=[])
    result, stats = project_source(municipal, article, drafts=[draft], previous=previous)
    assert result["locations"] == [] and stats["withdrawn"] == 1

    reference, draft, article = selected_sample()
    protect(draft, "municipality")
    draft["news_dossier"]["fields"]["department"].update(value=None, status="pending", evidence_refs=[])
    result, _ = project_source(reference, article, drafts=[draft])
    assert result["locations"][0]["method"] == "source_explicit_department"
    assert "resolution" not in result["locations"][0]


@pytest.mark.parametrize("field", ["affected_persons", "public_place", "country"])
def test_other_protected_fields_do_not_erase_safe_explicit_department(field):
    reference, draft, article = selected_sample()
    protect(draft, field)
    draft["news_dossier"]["fields"]["department"].update(value=None, status="pending", evidence_refs=[])
    result, _ = project_source(reference, article, drafts=[draft])
    assert result["locations"][0]["department"] == "Cabañas"
    assert PRIVATE not in json.dumps(result)


@pytest.mark.parametrize("model", [False, True])
def test_any_stale_positive_hash_is_withdrawn_even_when_the_source_read_fails(model):
    reference, draft, article = selected_sample()
    previous, _ = project_source(reference, article, drafts=[draft] if model else [])
    reference["selection"]["evidence"]["content_sha256"] = "d" * 64
    with pytest.raises(AutomaticLocationError):
        validate_collection(previous, archive={"schema_version": 1, "records": [reference]})
    result, stats = project_source(reference, article, drafts=[draft] if model else [], previous=previous, fetch=forbidden_fetch)
    assert result["locations"] == [] and stats["withdrawn"] == 1 and stats["source_failures"] == 1


def test_selected_sources_and_model_candidates_share_one_fetch_budget():
    values = [selected_sample(n) for n in range(1, 4)]
    articles = {r["id"]: a for r, _, a in values}; reads = []
    result, stats = project_automatic_locations([values[0][1]], {"schema_version": 1, "records": [r for r, _, _ in values]},
        fetch=lambda r: reads.append(r["id"]) or articles[r["id"]], limit=1, now=NOW)
    assert len(reads) == stats["sources_checked"] == len(result["locations"]) == 1
    assert stats["deferred"] == 2


def test_private_unresolved_cache_defers_repeated_reads_for_24_hours_and_expires():
    reference, _, article = selected_sample(text="El informe habla de los derechos humanos sin identificar lugares.")
    cache = {"schema_version": 1, "entries": []}
    result, stats = project_source(reference, article, negative_cache=cache)
    assert result["locations"] == [] and stats["unsupported"] == 1
    entry = cache["entries"][0]
    assert set(entry) == {"id", "selectionhash", "checked_at", "next_retry_at", "status", "rule_version"}
    assert entry["status"] == "unresolved" and entry["next_retry_at"] == (NOW + timedelta(days=1)).isoformat()
    assert "source_url" not in json.dumps(cache) and article["text"] not in json.dumps(cache)
    _, stats = project_source(reference, article, negative_cache=cache, fetch=forbidden_fetch, now=NOW + timedelta(hours=23))
    assert stats["negative_cached"] == 1 and stats["sources_checked"] == 0
    _, stats = project_source(reference, article, negative_cache=cache, now=NOW + timedelta(days=1, seconds=1))
    assert stats["sources_checked"] == 1 and stats["negative_cached"] == 0


@pytest.mark.parametrize("cause", ["hash", "rule"])
def test_negative_cache_is_invalidated_by_new_selection_hash_or_rule(cause):
    reference, _, article = selected_sample(text="El informe no identifica una ubicación administrativa concreta.")
    cache = {"schema_version": 1, "entries": []}
    project_source(reference, article, negative_cache=cache)
    if cause == "rule": cache["entries"][0]["rule_version"] = 1
    else: cache["entries"][0]["selectionhash"] = "d" * 64
    _, stats = project_source(reference, article, negative_cache=cache)
    assert stats["sources_checked"] == 1 and stats["negative_cached"] == 0
    assert cache["entries"][0]["rule_version"] == 2


@pytest.mark.parametrize("code,days", [("source_access_restricted", 7), ("source_network_error", 1)])
def test_unavailable_sources_have_bounded_private_retry_without_error_text(code, days):
    reference, _, article = selected_sample()
    cache = {"schema_version": 1, "entries": []}
    def unavailable(_): raise SourceFetchError(code)
    _, stats = project_source(reference, article, negative_cache=cache, fetch=unavailable)
    assert stats["source_failures"] == 1
    assert cache["entries"][0]["status"] == "source_unavailable"
    assert cache["entries"][0]["next_retry_at"] == (NOW + timedelta(days=days)).isoformat()
    assert code not in json.dumps(cache)


def test_malformed_private_cache_is_rejected_by_pure_projection_without_fetching():
    reference, _, article = selected_sample()
    cache = {"schema_version": 1, "entries": [], "private_body": PRIVATE}
    with pytest.raises(AutomaticLocationError, match="invalid_negative_location_cache"):
        project_source(reference, article, negative_cache=cache, fetch=forbidden_fetch)


def test_export_discards_corrupt_optional_cache_and_never_puts_it_in_public_json(tmp_path):
    reference, _, article = selected_sample(text="La noticia no contiene una localización administrativa concreta.")
    archive = tmp_path / "archive.json"; archive.write_text(json.dumps({"schema_version": 1, "records": [reference]}))
    output = tmp_path / "locations.json"
    cache = tmp_path / "private-cache.json"; cache.write_text('{"private_body":"' + PRIVATE + '"}')
    stats = export_automatic_locations(None, archive, output, cache_file=cache, now=NOW, fetch=lambda _: article)
    assert stats["negative_cache_discarded"] == 1 and stats["sources_checked"] == 1
    assert validate_negative_cache(json.loads(cache.read_text()), now=NOW)["entries"][0]["status"] == "unresolved"
    assert set(json.loads(output.read_text())) == {"schema_version", "generated_at", "locations"}
    assert PRIVATE not in output.read_text() + cache.read_text()
    stats = export_automatic_locations(None, archive, output, cache_file=cache, now=NOW, fetch=forbidden_fetch)
    assert stats["negative_cached"] == 1 and stats["sources_checked"] == 0


def test_private_cache_cannot_be_written_under_public_artifacts(tmp_path):
    reference, _, _ = selected_sample()
    archive = tmp_path / "archive.json"; archive.write_text(json.dumps({"schema_version": 1, "records": [reference]}))
    with pytest.raises(AutomaticLocationError):
        export_automatic_locations(None, archive, tmp_path / "public/locations.json",
            cache_file=tmp_path / "public/cache.json", fetch=forbidden_fetch, now=NOW)
    assert not (tmp_path / "public/locations.json").exists()
