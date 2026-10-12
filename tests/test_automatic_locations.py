"""The automatic map may reveal a department mention, never private dossier contents."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json

import pytest

from mdh_pipeline.automatic_locations import (
    AutomaticLocationError, CORROBORATED_METHOD, ENTRY_KEYS, export_automatic_locations,
    project_automatic_locations, validate_collection,
)
from mdh_pipeline.history import SOURCES, reference_from_post
from mdh_pipeline.news_dossier import empty_news_dossier

NOW = datetime(2026, 10, 10, 20, 0, tzinfo=timezone.utc)
PRIVATE = "PRIVATE_PERSON_ADDRESS_AND_QUOTATION_SENTINEL"


def sample(number=1, department="San Salvador", paragraph=None):
    reference = reference_from_post({
        "id": number, "date": f"2025-04-{((number - 1) % 28) + 1:02}T12:00:00",
        "link": f"https://www.diariocolatino.com/noticia-{number}/",
        "title": {"rendered": "Denuncian detenciones en El Salvador"},
    }, SOURCES[0], date(2025, 4, 1), date(2025, 4, 30), NOW.isoformat())
    text = paragraph or f"La publicación menciona el departamento de {department}. " + PRIVATE
    digest = hashlib.sha256(text.encode()).hexdigest()
    dossier = empty_news_dossier(reference, extraction_status="processed")
    dossier["classification"] = {"jurisdiction": "SV", "relevance": "relevant", "record_type": "incident"}
    dossier["evidence"].append({"id": "article-p1", "url": reference["source_url"],
                                "locator": f"Párrafo p1 consultado; SHA-256 {digest}."})
    dossier["fields"]["department"] = {"status": "reported", "value": department,
                                        "evidence_refs": ["article-p1"], "note": "Dato atribuido a la fuente."}
    dossier["fields"]["affected_persons"] = {"status": "reported", "value": [PRIVATE],
                                              "evidence_refs": ["article-p1"], "note": None}
    draft = {"schema_version": 1, "candidate_id": reference["id"], "source_url": reference["source_url"],
             "status": "draft", "publishable": False, "human_reviewed": False,
             "classification": deepcopy(dossier["classification"]), "news_dossier": dossier,
             "grounding": {"private_key": PRIVATE}, "record": {"secret": PRIVATE},
             "extraction": {"provider": "gemini", "content_sha256": digest,
                            "prepared_at": (NOW - timedelta(hours=1)).isoformat()}}
    article = {"source_url": reference["source_url"], "published_date": reference["published_date"],
               "text": text, "paragraphs": [{"id": "p1", "text": text}], "content_sha256": digest}
    return reference, draft, article


def project(draft, reference, article, **kwargs):
    return project_automatic_locations([draft], {"schema_version": 1, "records": [reference]},
                                       fetch=lambda candidate: article, now=NOW, **kwargs)


def forbidden_fetch(_):
    raise AssertionError("No source should be fetched")


def test_only_minimal_department_and_provenance_cross_the_public_boundary():
    reference, draft, article = sample()
    document, stats = project(draft, reference, article)
    assert stats["locations"] == 1 and stats["sources_checked"] == 1
    assert stats["dossier_publications"] == stats["case_publications"] == 0
    location = document["locations"][0]
    assert set(location) == ENTRY_KEYS
    assert location["department"] == "San Salvador"
    assert location["review_status"] == "automated_unreviewed" and location["human_reviewed"] is False
    assert location["evidence"] == {"paragraph_id": "p1", "content_sha256": article["content_sha256"]}
    assert PRIVATE not in json.dumps(document)
    assert "source_title" not in location and "record" not in location and "fields" not in location
    assert validate_collection(document, archive={"schema_version": 1, "records": [reference]}) == document


def test_previous_v1_locations_are_revalidated_once_before_becoming_current_cache():
    reference, draft, article = sample()
    old, _ = project(draft, reference, article)
    old["schema_version"] = 1
    checked = []
    current, stats = project_automatic_locations([draft], {"schema_version": 1, "records": [reference]},
        previous=old, fetch=lambda row: checked.append(row["id"]) or article, now=NOW)
    assert checked == [reference["id"]]
    assert current["schema_version"] == 3 and stats["cached"] == 0
    unchanged, stats = project_automatic_locations([draft], {"schema_version": 1, "records": [reference]},
        previous=current, fetch=forbidden_fetch, now=NOW)
    assert unchanged == current and stats["cached"] == 1


@pytest.mark.parametrize("department,paragraph", [
    ("Cabañas", "El informe cita el DEPARTAMENTO DE CABANAS en un análisis."),
    ("La Paz", "En el departamento   de\nLa\t Paz la fuente presenta cifras."),
    ("San Salvador", "El departamento de San Salvador registró cifras agregadas."),
])
def test_explicit_department_match_normalizes_accents_case_and_whitespace(department, paragraph):
    reference, draft, article = sample(department=department, paragraph=paragraph)
    assert project(draft, reference, article)[0]["locations"][0]["department"] == department


@pytest.mark.parametrize("paragraph", [
    "El ayuntamiento de San Salvador anunció el programa.",
    "El tribunal de San Salvador conoció el caso.",
    "Los municipios del Gran San Salvador se reunieron.",
    "El área metropolitana de San Salvador registra cambios.",
    "En la carretera a San Salvador ocurrió un accidente.",
    "San Salvador: la publicación presenta una reflexión editorial.",
    "El departamento de San Salvadorcito es un nombre distinto.",
    "El informe dice departamento y otro párrafo menciona San Salvador.",
])
def test_place_homonyms_and_nonexplicit_geography_are_never_projected(paragraph):
    reference, draft, article = sample(paragraph=paragraph)
    document, stats = project(draft, reference, article)
    assert document["locations"] == [] and stats["unsupported"] == 1


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(publishable=True),
    lambda d: d.update(publishable=0),
    lambda d: d.update(human_reviewed=0),
    lambda d: d.update(status="needs_privacy_review"),
    lambda d: d.update(privacy_flags=["minor_identifiers"]),
    lambda d: d.update(privacy_flags=None),
    lambda d: d["news_dossier"].update(extraction_status="error"),
    lambda d: d["news_dossier"]["classification"].update(jurisdiction="unclear"),
    lambda d: d["classification"].update(jurisdiction="other"),
    lambda d: d["news_dossier"]["fields"]["department"].update(value="Quetzaltenango"),
    lambda d: d["news_dossier"]["fields"]["department"].update(value=None, status="pending", evidence_refs=[]),
    lambda d: d["news_dossier"]["fields"]["department"].update(value=None, status="not_applicable", evidence_refs=[], note="No corresponde este campo."),
    lambda d: d["news_dossier"]["fields"]["department"].update(evidence_refs=["primary"]),
    lambda d: d["news_dossier"]["evidence"][1].update(locator="No se conserva el hash del párrafo."),
    lambda d: d["extraction"].update(provider="none"),
    lambda d: d["extraction"].update(content_sha256="bad"),
    lambda d: d["extraction"].update(prepared_at="2026-10-10"),
    lambda d: d.update(candidate_id="f" * 64),
    lambda d: d["news_dossier"].update(publisher="Otro medio"),
    lambda d: d["news_dossier"].update(published_date="2025-04-02"),
])
def test_noneligible_private_data_stops_before_fetch(mutation):
    reference, draft, _ = sample()
    mutation(draft)
    result, stats = project_automatic_locations([draft], {"schema_version": 1, "records": [reference]},
                                               fetch=forbidden_fetch, now=NOW)
    assert result["locations"] == [] and stats["sources_checked"] == 0


def test_scope_review_partial_dossier_is_allowed_only_with_explicit_sv_classification():
    reference, draft, article = sample()
    draft["status"] = "needs_scope_review"
    assert project(draft, reference, article)[1]["locations"] == 1


@pytest.mark.parametrize("mutation", [
    lambda a: a.update(content_sha256="f" * 64),
    lambda a: a.update(text="Changed source body"),
    lambda a: a.update(source_url="https://www.diariocolatino.com/different-article/"),
    lambda a: a.update(published_date="2025-04-02"),
    lambda a: a.update(paragraphs=[]),
    lambda a: a["paragraphs"][0].update(id="p2"),
    lambda a: a["paragraphs"][0].update(text="A different paragraph"),
])
def test_source_hash_identity_date_and_exact_paragraph_are_rechecked(mutation):
    reference, draft, article = sample()
    mutation(article)
    result, stats = project(draft, reference, article)
    assert result["locations"] == [] and stats["unsupported"] == 1


def test_department_must_be_explicit_in_the_cited_paragraph_not_elsewhere():
    reference, draft, article = sample(paragraph="El tribunal de San Salvador conoció el caso.")
    article["paragraphs"].append({"id": "p2", "text": "Las cifras corresponden al departamento de San Salvador."})
    article["text"] = "\n\n".join(p["text"] for p in article["paragraphs"])
    digest = hashlib.sha256(article["text"].encode()).hexdigest()
    article["content_sha256"] = draft["extraction"]["content_sha256"] = digest
    draft["news_dossier"]["evidence"][1]["locator"] = f"Párrafo p1; SHA-256 {digest}."
    assert project(draft, reference, article)[0]["locations"] == []


def test_unchanged_cache_preserves_timestamp_and_avoids_source_fetch():
    reference, draft, article = sample()
    previous, _ = project(draft, reference, article)
    current, stats = project_automatic_locations([draft], {"schema_version": 1, "records": [reference]},
                                                previous=previous, fetch=forbidden_fetch, now=NOW + timedelta(days=1))
    assert current == previous and stats["cached"] == 1 and stats["sources_checked"] == 0


@pytest.mark.parametrize("cause", ["absent", "excluded", "privacy", "scope", "approved", "changed_hash", "changed_department"])
def test_cache_is_withdrawn_when_private_eligibility_or_exact_evidence_changes(cause):
    reference, draft, article = sample()
    previous, _ = project(draft, reference, article)
    drafts, excluded = [draft], []
    if cause == "absent": drafts = []
    if cause == "excluded": excluded = [reference["id"]]
    if cause == "privacy": draft["privacy_flags"] = ["private_contact"]
    if cause == "scope": draft["classification"]["jurisdiction"] = "other"
    if cause == "approved": draft["human_reviewed"] = True
    if cause == "changed_hash": draft["extraction"]["content_sha256"] = "f" * 64
    if cause == "changed_department": draft["news_dossier"]["fields"]["department"]["value"] = "La Paz"
    current, stats = project_automatic_locations(drafts, {"schema_version": 1, "records": [reference]},
        previous=previous, excluded_ids=excluded, fetch=lambda _: article, now=NOW + timedelta(hours=1))
    assert current["locations"] == [] and stats["withdrawn"] == 1


def test_fetch_budget_is_bounded_and_deferred_locations_are_not_invented():
    samples = [sample(number) for number in range(1, 4)]
    fetches = []
    articles = {r["source_url"]: article for r, _, article in samples}
    def fetch(reference):
        fetches.append(reference["id"])
        return articles[reference["source_url"]]
    result, stats = project_automatic_locations([draft for _, draft, _ in samples],
        {"schema_version": 1, "records": [r for r, _, _ in samples]}, limit=1, fetch=fetch, now=NOW)
    assert len(fetches) == len(result["locations"]) == stats["sources_checked"] == 1
    assert stats["deferred"] == 2


def test_hourly_rotation_reaches_every_uncached_candidate_despite_persistent_old_failures():
    samples = [sample(number, paragraph="En el tribunal de San Salvador se discutió el caso.") for number in range(1, 6)]
    references = {r["source_url"]: article for r, _, article in samples}
    visited = set()
    def fetch(reference):
        visited.add(reference["id"])
        return references[reference["source_url"]]
    for hour in range(3):
        snapshot, stats = project_automatic_locations([draft for _, draft, _ in samples],
            {"schema_version": 1, "records": [r for r, _, _ in samples]},
            limit=2, fetch=fetch, now=NOW + timedelta(hours=hour))
        assert snapshot["locations"] == [] and stats["sources_checked"] == 2
    assert visited == {r["id"] for r, _, _ in samples}


def test_three_hourly_batches_cover_46_candidates_including_late_success_and_cached_positives():
    samples = [sample(number, paragraph="El tribunal de San Salvador presentó un informe.") for number in range(1, 47)]
    ordered = sorted(samples, key=lambda triple: (triple[0]["published_date"], triple[0]["id"]))
    # The final candidate is supported; another supported candidate is already
    # cached. A successful run must not change subsequent rotation boundaries.
    successful_ids = {ordered[0][0]["id"], ordered[-1][0]["id"]}
    for reference, draft, article in samples:
        if reference["id"] not in successful_ids:
            continue
        article["text"] = "La publicación menciona el departamento de San Salvador."
        article["paragraphs"][0]["text"] = article["text"]
        digest = hashlib.sha256(article["text"].encode()).hexdigest()
        article["content_sha256"] = draft["extraction"]["content_sha256"] = digest
        draft["news_dossier"]["evidence"][1]["locator"] = f"Párrafo p1; SHA-256 {digest}."
    initial_ref, initial_draft, initial_article = ordered[0]
    previous, _ = project(initial_draft, initial_ref, initial_article)
    articles = {r["source_url"]: article for r, _, article in samples}
    visited, visited_this_run = set(), []
    def fetch(reference):
        visited.add(reference["id"])
        visited_this_run.append(reference["id"])
        return articles[reference["source_url"]]
    for hour in range(3):
        visited_this_run.clear()
        previous, stats = project_automatic_locations([draft for _, draft, _ in samples],
            {"schema_version": 1, "records": [r for r, _, _ in samples]},
            previous=previous, limit=20, fetch=fetch, now=NOW + timedelta(hours=hour))
        assert stats["sources_checked"] == len(visited_this_run) == 20
        assert initial_ref["id"] not in visited_this_run
        assert stats["rotation_candidates"] == 46
    assert visited | {initial_ref["id"]} == {r["id"] for r, _, _ in samples}
    assert {entry["candidate_id"] for entry in previous["locations"]} == successful_ids


def test_source_failure_is_aggregate_only_and_never_exposes_response_body(capsys):
    reference, draft, _ = sample()
    def unavailable(_): raise RuntimeError(PRIVATE)
    result, stats = project_automatic_locations([draft], {"schema_version": 1, "records": [reference]},
                                                fetch=unavailable, now=NOW)
    assert result["locations"] == [] and stats["source_failures"] == 1
    assert PRIVATE not in json.dumps(stats) and capsys.readouterr() == ("", "")


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(private_value=PRIVATE),
    lambda d: d.update(schema_version=True),
    lambda d: d["locations"][0].update(note=PRIVATE),
    lambda d: d["locations"][0].update(human_reviewed=0),
    lambda d: d["locations"][0].update(review_status="approved"),
    lambda d: d["locations"][0]["evidence"].update(quote=PRIVATE),
    lambda d: d["locations"].append(deepcopy(d["locations"][0])),
    lambda d: d["locations"][0].update(extracted_at="2026-10-10"),
])
def test_exact_public_allowlist_rejects_private_extras_and_false_review(mutation):
    reference, draft, article = sample()
    document, _ = project(draft, reference, article)
    mutation(document)
    with pytest.raises(AutomaticLocationError, match="^invalid_automatic_locations$"):
        validate_collection(document)


def test_duplicate_private_drafts_and_invalid_archive_fail_before_fetch():
    reference, draft, _ = sample()
    with pytest.raises(AutomaticLocationError, match="duplicate_location_draft"):
        project_automatic_locations([draft, deepcopy(draft)], {"schema_version": 1, "records": [reference]},
                                    fetch=forbidden_fetch, now=NOW)
    reference["publisher"] = "Unbound publisher"
    with pytest.raises(AutomaticLocationError, match="invalid_location_bibliography"):
        project_automatic_locations([draft], {"schema_version": 1, "records": [reference]},
                                    fetch=forbidden_fetch, now=NOW)


@pytest.mark.parametrize("limit", [0, 101, True, 1.5])
def test_invalid_budget_rejected(limit):
    with pytest.raises(AutomaticLocationError, match="invalid_location_limit"):
        project_automatic_locations([], {"schema_version": 1, "records": []}, limit=limit, now=NOW)


def test_atomic_export_keeps_drafts_private_and_removes_approved_or_rejected_ids(tmp_path):
    reference, draft, article = sample()
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    (drafts / f'{reference["id"]}.json').write_text(json.dumps(draft))
    archive, output = tmp_path / "news.json", tmp_path / "public" / "locations.json"
    archive.write_text(json.dumps({"schema_version": 1, "records": [reference]}))
    stats = export_automatic_locations(drafts, archive, output, now=NOW, fetch=lambda _: article)
    assert stats["locations"] == 1 and PRIVATE not in output.read_text()
    stats = export_automatic_locations(drafts, archive, output, now=NOW, fetch=forbidden_fetch,
                                       excluded_ids=[reference["id"]])
    assert stats["withdrawn"] == 1 and json.loads(output.read_text())["locations"] == []
    assert not output.with_suffix(".json.tmp").exists()


def test_corrupt_private_file_cannot_replace_preexisting_public_snapshot(tmp_path):
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    (drafts / ("a" * 64 + ".json")).write_text('{"candidate_id": "PRIVATE_INVALID"}')
    archive, output = tmp_path / "news.json", tmp_path / "locations.json"
    archive.write_text(json.dumps({"schema_version": 1, "records": []}))
    prior = {"schema_version": 1, "generated_at": NOW.isoformat(), "locations": []}
    output.write_text(json.dumps(prior))
    with pytest.raises(AutomaticLocationError, match="^automatic_location_export_failed$"):
        export_automatic_locations(drafts, archive, output, now=NOW, fetch=forbidden_fetch)
    assert json.loads(output.read_text()) == prior


def corroborated_sample(model_text="El gobierno anunció un cerco militar a Cabañas.",
                        support="La comunidad está en el distrito de Victoria, departamento de Cabañas."):
    reference, draft, article = sample(department="Cabañas", paragraph=model_text)
    article["paragraphs"] += [{"id": "p2", "text": "La publicación recoge testimonios sobre el operativo."},
                              {"id": "p3", "text": support}]
    article["text"] = "\n\n".join(p["text"] for p in article["paragraphs"])
    digest = hashlib.sha256(article["text"].encode()).hexdigest()
    article["content_sha256"] = draft["extraction"]["content_sha256"] = digest
    draft["news_dossier"]["evidence"][1]["locator"] = f"Párrafo p1; SHA-256 {digest}."
    return reference, draft, article


def test_same_article_geographic_corroboration_keeps_model_citations_unchanged():
    reference, draft, article = corroborated_sample()
    original = deepcopy(draft)
    result, stats = project(draft, reference, article)
    assert stats["locations"] == 1 and result["schema_version"] == 3
    location = result["locations"][0]
    assert location["method"] == CORROBORATED_METHOD
    assert location["department"] == "Cabañas"
    assert location["evidence"] == {"paragraph_id": "p3", "content_sha256": article["content_sha256"]}
    assert location["corroboration"] == {"strategy": "same_article_explicit_department",
        "model_paragraph_id": "p1", "explicit_paragraph_id": "p3", "rule_version": 1}
    assert set(location) == ENTRY_KEYS | {"corroboration"}
    assert draft == original
    assert PRIVATE not in json.dumps(result) and "testimonios" not in json.dumps(result)


@pytest.mark.parametrize("model_text", [
    "El informe describe detenciones en Cabañas.",
    "Se anunció un asedio policial a Cabañas.",
    "Se anunció un operativo en Cabañas.",
    "El informe describe un despliegue militar sobre Cabañas.",
    "Se anunció una incursión policial en Cabañas.",
])
def test_corroboration_accepts_explicit_positive_geographic_context(model_text):
    reference, draft, article = corroborated_sample(model_text=model_text)
    assert project(draft, reference, article)[0]["locations"][0]["method"] == CORROBORATED_METHOD


@pytest.mark.parametrize("support", [
    "La comunidad se encuentra en Victoria, departamento de Cabañas.",
    "La comunidad se encuentra en Victoria, departamento de Cabañas. El artículo también menciona Honduras.",
    "La comunidad se encuentra en el distrito de Victoria, departamento de Cabañas.",
])
def test_corroboration_accepts_exact_official_municipality_department_locative_pair(support):
    reference, draft, article = corroborated_sample(support=support)
    result, _ = project(draft, reference, article)
    assert result["locations"][0]["method"] == CORROBORATED_METHOD
    assert result["locations"][0]["evidence"]["paragraph_id"] == "p3"


@pytest.mark.parametrize("support", [
    "La comunidad se encuentra en Lugar Inventado, departamento de Cabañas.",
    "La comunidad se encuentra en San Salvador, departamento de Cabañas.",
    "La comunidad se encuentra en el distrito de San Salvador, departamento de Cabañas.",
    "La comunidad se encuentra en Victoria, Guatemala, departamento de Cabañas.",
    "La comunidad se encuentra en Victoria, departamento de Cabañas, Honduras.",
    "El operativo no ocurrió en Victoria, departamento de Cabañas.",
    "El hecho no ocurrió en el distrito de Victoria, departamento de Cabañas; ocurrió en San Miguel.",
    "El operativo ocurrió fuera del lugar, no en Victoria, departamento de Cabañas.",
    "No hubo un operativo militar en Victoria, departamento de Cabañas.",
    "Victoria, departamento de Cabañas, aparece en una lista sin contexto geográfico.",
])
def test_corroboration_rejects_invalid_administrative_pairs_and_denied_locations(support):
    reference, draft, article = corroborated_sample(support=support)
    assert project(draft, reference, article)[0]["locations"] == []


@pytest.mark.parametrize("foreign_suffix", [", Perú", ", en Perú", ", en el Perú", " en Perú", " (en el Perú)"])
@pytest.mark.parametrize("foreign_paragraph", ["model", "support", "both"])
def test_corroboration_rejects_foreign_links_with_prepositions_in_either_paragraph(foreign_suffix, foreign_paragraph):
    model = "El operativo ocurrió en La Libertad" + (foreign_suffix if foreign_paragraph in {"model", "both"} else "") + "."
    support = "El operativo se desplegó en el departamento de La Libertad" + (foreign_suffix if foreign_paragraph in {"support", "both"} else "") + "."
    reference, draft, article = corroborated_sample(model_text=model, support=support)
    draft["news_dossier"]["fields"]["department"]["value"] = "La Libertad"
    assert project(draft, reference, article)[0]["locations"] == []


@pytest.mark.parametrize("model_text,support", [
    ("La publicación incluye la palabra Cabañas.", "El operativo ocurrió en el departamento de Cabañas."),
    ("Se mencionan Cabañas y otros lugares.", "El operativo ocurrió en el departamento de Cabañas."),
    ("El tribunal de Cabañas examinó el informe.", "El operativo ocurrió en el departamento de Cabañas."),
    ("El juzgado en Cabañas recibió el documento.", "El operativo ocurrió en el departamento de Cabañas."),
    ("La sede editorial está en Cabañas.", "El operativo ocurrió en el departamento de Cabañas."),
    ("La carretera a Cabañas quedó cerrada.", "El operativo ocurrió en el departamento de Cabañas."),
    ("Cabañas: se anunciaron medidas en Cabañas.", "El operativo ocurrió en el departamento de Cabañas."),
    ("El operativo no ocurrió en Cabañas.", "El operativo ocurrió en el departamento de Cabañas."),
    ("No hubo un cerco militar a Cabañas.", "El operativo ocurrió en el departamento de Cabañas."),
    ("La localidad quedó sin cerco militar a Cabañas.", "El operativo ocurrió en el departamento de Cabañas."),
    ("El operativo ocurrió en Cabañas, Guatemala.", "El operativo ocurrió en el departamento de Cabañas."),
    ("El gobierno anunció un cerco militar a Cabañas.", "La publicación menciona el departamento de Cabañas."),
    ("El gobierno anunció un cerco militar a Cabañas.", "La sede está en el departamento de Cabañas."),
    ("El gobierno anunció un cerco militar a Cabañas.", "El tribunal en el departamento de Cabañas emitió un comunicado."),
    ("El gobierno anunció un cerco militar a Cabañas.", "La carretera en el departamento de Cabañas fue ampliada."),
    ("El gobierno anunció un cerco militar a Cabañas.", "El operativo no ocurrió en el departamento de Cabañas."),
    ("El gobierno anunció un cerco militar a Cabañas.", "El operativo ocurrió en el departamento de Cabañas, Guatemala."),
    ("El gobierno anunció un cerco militar a Cabañas.", "El operativo ocurrió en el departamento de San Salvador."),
])
def test_corroboration_rejects_bare_mentions_negations_foreign_places_and_ambiguous_roles(model_text, support):
    reference, draft, article = corroborated_sample(model_text, support)
    result, stats = project(draft, reference, article)
    assert result["locations"] == [] and stats["unsupported"] == 1


@pytest.mark.parametrize("mutation", [
    lambda a: a.update(content_sha256="a" * 64),
    lambda a: a.update(source_url="https://www.diariocolatino.com/other-source/"),
    lambda a: a["paragraphs"][2].update(text="A different paragraph with a different hash."),
])
def test_corroboration_never_uses_different_or_changed_source(mutation):
    reference, draft, article = corroborated_sample()
    mutation(article)
    assert project(draft, reference, article)[0]["locations"] == []


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(schema_version=1),
    lambda d: d.update(schema_version=2),
    lambda d: d["locations"][0].pop("corroboration"),
    lambda d: d["locations"][0]["corroboration"].update(quote=PRIVATE),
    lambda d: d["locations"][0]["corroboration"].update(rule_version=True),
    lambda d: d["locations"][0]["corroboration"].update(rule_version=2),
    lambda d: d["locations"][0]["corroboration"].update(strategy="human_review"),
    lambda d: d["locations"][0]["corroboration"].update(model_paragraph_id="p3"),
    lambda d: d["locations"][0]["corroboration"].update(model_paragraph_id="p0"),
    lambda d: d["locations"][0]["corroboration"].update(explicit_paragraph_id="p2"),
    lambda d: d["locations"][0]["evidence"].update(paragraph_id="p1"),
])
def test_corroboration_requires_v3_exact_provenance_and_distinct_bound_paragraphs(mutation):
    reference, draft, article = corroborated_sample()
    result, _ = project(draft, reference, article)
    mutation(result)
    with pytest.raises(AutomaticLocationError, match="invalid_automatic_locations"):
        validate_collection(result)


@pytest.mark.parametrize("version", [1, 2, 3])
def test_existing_direct_locations_remain_valid_in_all_supported_collection_versions(version):
    reference, draft, article = sample()
    result, _ = project(draft, reference, article)
    result["schema_version"] = version
    assert validate_collection(result)["schema_version"] == version


@pytest.mark.parametrize("version", [2, 3])
def test_upgrade_preserves_all_19_existing_positive_caches_without_fetches(version):
    samples = [sample(number) for number in range(1, 20)]
    articles = {r["source_url"]: a for r, _, a in samples}
    archive = {"schema_version": 1, "records": [r for r, _, _ in samples]}
    drafts = [d for _, d, _ in samples]
    previous, _ = project_automatic_locations(drafts, archive, fetch=lambda r: articles[r["source_url"]], now=NOW)
    previous["schema_version"] = version
    current, stats = project_automatic_locations(drafts, archive, previous=previous, fetch=forbidden_fetch,
                                                now=NOW + timedelta(hours=1))
    assert current["schema_version"] == 3 and current["locations"] == previous["locations"]
    assert current["generated_at"] == previous["generated_at"]
    assert stats["cached"] == stats["locations"] == 19 and stats["sources_checked"] == 0


def test_corroborated_cache_keeps_timestamp_without_refetch_and_withdraws_changed_model_anchor():
    reference, draft, article = corroborated_sample()
    previous, _ = project(draft, reference, article)
    current, stats = project_automatic_locations([draft], {"schema_version": 1, "records": [reference]},
                                                previous=previous, fetch=forbidden_fetch, now=NOW + timedelta(hours=1))
    assert current == previous and stats["cached"] == 1
    draft["news_dossier"]["evidence"].append({"id": "article-p2", "url": reference["source_url"],
                                            "locator": f'Párrafo p2; SHA-256 {article["content_sha256"]}.'})
    draft["news_dossier"]["fields"]["department"]["evidence_refs"] = ["article-p2"]
    current, stats = project_automatic_locations([draft], {"schema_version": 1, "records": [reference]},
        previous=previous, fetch=lambda _: article, now=NOW + timedelta(hours=2))
    assert current["locations"] == [] and stats["cached"] == 0 and stats["withdrawn"] == 1
