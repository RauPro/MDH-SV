"""Deterministic administrative mentions are conservative, literal and private-field free."""
from copy import deepcopy
import hashlib
import json

import pytest

from mdh_pipeline.municipalities import REFERENCE_VERSION, resolve_municipality
from mdh_pipeline.source_location_evidence import SOURCE_LOCATION_RULE_VERSION, extract_administrative_mention, extract_administrative_mentions, has_administrative_conflict


def article(*paragraphs):
    text = "\n\n".join(paragraphs)
    return {"text": text, "paragraphs": [{"id": f"p{n + 1}", "text": value} for n, value in enumerate(paragraphs)],
            "content_sha256": hashlib.sha256(text.encode()).hexdigest(), "title": "Título que no constituye evidencia"}


def test_explicit_department_is_a_minimal_literal_source_projection():
    value = article("Las organizaciones documentaron denuncias en el departamento de Cabañas.")
    original = deepcopy(value)
    assert extract_administrative_mention(value) == {
        "method": "source_explicit_department", "department": "Cabañas", "paragraph_id": "p1"}
    assert SOURCE_LOCATION_RULE_VERSION == 2 and value == original


@pytest.mark.parametrize("phrase", [
    "Hay denuncias en el municipio de Nahuizalco.",
    "Las organizaciones trabajan en el distrito de Nahuizalco.",
    "La documentación corresponde a la localidad de Nahuizalco.",
    "Las comunidades del municipio de Nahuizalco presentaron denuncias.",
    "El municipio de Nahuizalco enfrenta discriminación.",
    "La ciudad de Nahuizalco registra denuncias.",
])
def test_unique_typed_locality_uses_exact_official_reference_without_inventing_a_country(phrase):
    result = extract_administrative_mention(article(phrase))
    assert result["method"] == "source_municipality_reference" and result["department"] == "Sonsonate"
    place = resolve_municipality("Nahuizalco")
    assert result["resolution"] == {"type": "municipality_to_department", "municipality": "Nahuizalco",
        "municipality_kind": "district", "reference_version": REFERENCE_VERSION, "source_url": place["source_url"]}


def test_bare_locality_needs_explicit_salvadoran_context_in_the_article_body():
    assert extract_administrative_mention(article("Las comunidades denunciaron discriminación en Nahuizalco.")) is None
    result = extract_administrative_mention(article("La organización documentó el asunto en El Salvador.",
                                                 "Las comunidades denunciaron discriminación en Nahuizalco."))
    assert result["department"] == "Sonsonate" and result["paragraph_id"] == "p2"
    value = article("Las comunidades denunciaron discriminación en Nahuizalco.")
    value.update(title="Derechos humanos en El Salvador", publisher="Un medio salvadoreño")
    assert extract_administrative_mention(value) is None


def test_salvadoran_nationality_alone_does_not_establish_bare_locality_country():
    assert extract_administrative_mention(article("Un salvadoreño fue detenido en Santa Ana.")) is None
    result = extract_administrative_mention(article("El informe se refiere a El Salvador.",
                                                 "Una persona fue detenida en Santa Ana."))
    assert result["method"] == "source_municipality_reference"
    assert result["department"] == "Santa Ana" and result["paragraph_id"] == "p2"


def test_source_evidence_can_use_an_uncited_supporting_paragraph_without_claiming_model_grounding():
    result = extract_administrative_mention(article(
        "El informe sobre El Salvador estudia discriminación.",
        "La primera reseña menciona Nahuizalco sin indicar un contexto administrativo.",
        "Las comunidades exigen garantías en el municipio de Nahuizalco."))
    assert result["paragraph_id"] == "p3" and result["method"] == "source_municipality_reference"
    assert "gemini" not in json.dumps(result) and "corroboration" not in result


def test_prison_name_alone_is_not_a_municipality_but_a_separate_literal_locality_can_qualify():
    first = "El Salvador analiza las condiciones del complejo penitenciario de Izalco."
    assert extract_administrative_mention(article(first)) is None
    result = extract_administrative_mention(article(first, "El programa se desarrolla en el municipio de Izalco."))
    assert result["department"] == "Sonsonate" and result["paragraph_id"] == "p2"


def test_administrative_chain_requires_an_exact_compatible_pair():
    result = extract_administrative_mention(article("La comunidad está en Victoria, departamento de Cabañas."))
    assert result == {"method": "source_explicit_department", "department": "Cabañas", "paragraph_id": "p1"}
    assert extract_administrative_mention(article("La comunidad está en Nahuizalco, departamento de Cabañas.")) is None
    assert extract_administrative_mention(article("La comunidad está en Lugar Inventado, departamento de Cabañas.")) is None


@pytest.mark.parametrize("text", [
    "El humedal de Metapán, en el departamento de Ahuachapán, requiere protección.",
    "El humedal de Metapán, ubicado en el departamento de Ahuachapán, requiere protección.",
    "El humedal de Metapán, palabra intrusa en el departamento de Ahuachapán, requiere protección.",
    "El humedal de Metapán, texto desconocido en el departamento de Santa Ana, requiere protección.",
])
def test_adjacent_city_department_contradictions_or_unknown_insertions_suppress_explicit_department(text):
    assert extract_administrative_mention(article(text)) is None
    assert extract_administrative_mention(article(text), allow_municipality=False) is None


@pytest.mark.parametrize("connector", [", en el", ", ubicado en el", " en el"])
def test_correct_adjacent_city_department_pair_preserves_explicit_source_claim(connector):
    result = extract_administrative_mention(article(
        f"El humedal de Metapán{connector} departamento de Santa Ana requiere protección."))
    assert result == {"method": "source_explicit_department", "department": "Santa Ana", "paragraph_id": "p1"}


@pytest.mark.parametrize("direction", ["norte", "sur", "este", "oeste", "centro"])
def test_internal_department_direction_preserves_the_official_administrative_pair(direction):
    value = article(f"El hecho ocurrió en el municipio de Santa Rosa de Lima, en el {direction} del departamento de La Unión.")
    assert not has_administrative_conflict(value)
    result = extract_administrative_mention(value)
    assert result["department"] == "La Unión" and result["paragraph_id"] == "p1"
    mismatch = article(f"El hecho ocurrió en el municipio de Santa Rosa de Lima, en el {direction} del departamento de San Miguel.")
    assert has_administrative_conflict(mismatch)
    assert extract_administrative_mentions(mismatch) == []


def test_administrative_contradiction_is_not_relocated_or_overridden_by_another_valid_mention():
    value = article("El humedal de Metapán, en el departamento de Ahuachapán, requiere protección.",
                    "El informe también contiene denuncias en el departamento de Santa Ana.")
    assert extract_administrative_mention(value) is None


def test_all_source_locations_are_examined_and_multiple_departments_are_never_reduced_to_first():
    value = article("Las denuncias se registraron en el municipio de Nahuizalco.",
                    "Otras denuncias ocurrieron en el municipio de San Miguel.")
    assert extract_administrative_mention(value) is None
    mentions = extract_administrative_mentions(value)
    assert {item["department"] for item in mentions} == {"Sonsonate", "San Miguel"}
    assert len(mentions) == 2
    assert extract_administrative_mention(article(
        "Se registraron denuncias en los departamentos de Sonsonate y San Miguel.")) is None
    assert extract_administrative_mention(article(
        "Se registraron denuncias en los municipios de Nahuizalco y San Miguel.")) is None
    assert extract_administrative_mention(article(
        "Se registraron denuncias en el municipio de Nahuizalco y el municipio de San Miguel.")) is None
    assert extract_administrative_mention(article(
        "Se registraron denuncias en la localidad de Nahuizalco y la localidad de San Miguel.")) is None


def test_multiple_municipalities_in_same_department_are_allowed_with_stable_proof():
    result = extract_administrative_mention(article(
        "Se registraron denuncias en los municipios de Nahuizalco y Sonsonate."))
    assert result["department"] == "Sonsonate"
    assert result["resolution"]["municipality"] == "Nahuizalco"
    assert result["paragraph_id"] == "p1"
    assert len(extract_administrative_mentions(article(
        "Se registraron denuncias en los municipios de Nahuizalco y Sonsonate."))) == 1


def test_plural_administrative_lists_keep_all_distinct_departments_without_a_primary():
    value = article("Las organizaciones trabajan en los departamentos de La Paz, Cuscatlán y Cabañas.")
    mentions = extract_administrative_mentions(value)
    assert {item["department"] for item in mentions} == {"La Paz", "Cuscatlán", "Cabañas"}
    assert all(item["paragraph_id"] == "p1" and item["method"] == "source_explicit_department" for item in mentions)
    assert extract_administrative_mention(value) is None


def test_plural_still_rejects_conflicting_chains_and_homonyms_instead_of_partial_salvage():
    value = article("El humedal de Metapán, en el departamento de Ahuachapán, requiere protección.",
                    "El informe también documenta denuncias en el departamento de Cabañas.")
    assert extract_administrative_mentions(value) == []
    assert extract_administrative_mentions(article("Hay denuncias en el municipio de El Rosario.",
                                                   "Otras se registraron en el departamento de Sonsonate.")) == []


def test_conflict_helper_distinguishes_contradiction_from_homonyms_and_valid_plurality():
    assert has_administrative_conflict(article("El humedal de Metapán, en el departamento de Ahuachapán, requiere protección."))
    assert has_administrative_conflict(article("El humedal de Metapán, palabra intrusa en el departamento de Santa Ana, requiere protección."))
    assert not has_administrative_conflict(article("Se documentan denuncias en los departamentos de La Paz y Cabañas."))
    assert not has_administrative_conflict(article("Se documentan denuncias en el municipio de El Rosario."))


def test_explicit_department_examples_and_predicate_lists_keep_all_named_departments():
    examples = article("Las denuncias se concentran en departamentos como San Salvador, La Libertad, Sonsonate y Santa Ana.")
    assert {m["department"] for m in extract_administrative_mentions(examples)} == {"San Salvador", "La Libertad", "Sonsonate", "Santa Ana"}
    predicate = article("Los departamentos con mayor índice de denuncias han sido Usulután y San Salvador.")
    assert {m["department"] for m in extract_administrative_mentions(predicate)} == {"Usulután", "San Salvador"}


def test_explicit_municipalities_predicate_list_maps_jurisdictions_and_deduplicates_department():
    value = article("Entre las municipalidades donde se realiza el programa están: Santa Tecla, San Marcos y San Martín.")
    results = extract_administrative_mentions(value)
    assert {m["department"] for m in results} == {"La Libertad", "San Salvador"}
    assert {m["resolution"]["municipality"] for m in results} == {"Santa Tecla", "San Marcos"}


def test_administrative_apposition_after_locative_canton_validates_explicit_department_suffix():
    value = article("El hecho ocurrió en el cantón El Ejemplo, municipio Concepción Batres, Usulután.")
    result = extract_administrative_mention(value)
    assert result["department"] == "Usulután" and result["resolution"]["municipality"] == "Concepción Batres"
    mismatch = article("El hecho ocurrió en el cantón El Ejemplo, municipio Concepción Batres, San Miguel.")
    assert extract_administrative_mentions(mismatch) == [] and has_administrative_conflict(mismatch)
    private = article("La persona residía en el cantón El Ejemplo, municipio Concepción Batres, Usulután.")
    assert extract_administrative_mentions(private) == []


def test_limited_attested_adjective_preserves_literal_municipal_reference():
    result = extract_administrative_mention(article("El hecho ocurrió en el populoso municipio de Apopa."))
    assert result["department"] == "San Salvador" and result["resolution"]["municipality"] == "Apopa"
    assert extract_administrative_mentions(article("La persona nació en el populoso municipio de Apopa.")) == []


def test_municipal_government_jurisdiction_is_recognized_but_an_office_address_is_not():
    result = extract_administrative_mention(article("Trabajadores de la alcaldía municipal de San Martín denuncian violaciones laborales."))
    assert result["department"] == "San Salvador" and result["resolution"]["municipality"] == "San Martín"
    assert extract_administrative_mentions(article("La oficina de la alcaldía municipal de San Martín recibió una carta.")) == []
    assert extract_administrative_mentions(article("Trabajadores de la alcaldía de San Martín denuncian violaciones laborales."), allow_municipality=False) == []


def test_neighboring_department_is_not_a_false_administrative_containment_contradiction():
    value = article("El informe documenta denuncias en el municipio de Nombre de Jesús, que colinda con el departamento de Cabañas.")
    assert not has_administrative_conflict(value)
    result = extract_administrative_mention(value)
    assert result["department"] == "Chalatenango"


def test_distinct_city_and_department_in_same_clause_are_not_forced_into_a_false_pair():
    value = article("Hay denuncias en el municipio de Nahuizalco y en el departamento de San Miguel.")
    assert {m["department"] for m in extract_administrative_mentions(value)} == {"Sonsonate", "San Miguel"}


def test_separate_event_predicate_does_not_assert_that_a_previous_city_belongs_to_its_department():
    narrative = "Se denunció el ataque contra una persona de la ciudad de San Miguel, texto intruso, perpetrado en el departamento de La Unión."
    isolated = article(narrative)
    assert not has_administrative_conflict(isolated)
    # The unresolved intervening prose is not repaired or published as proof.
    assert extract_administrative_mentions(isolated) == []
    value = article(narrative, "El hecho ocurrió en el municipio de Santa Rosa de Lima, en el norte del departamento de La Unión.")
    result = extract_administrative_mention(value)
    assert not has_administrative_conflict(value)
    assert result["department"] == "La Unión" and result["paragraph_id"] == "p2"


def test_a_later_narrative_clause_cannot_erase_an_explicit_contradictory_pair():
    value = article("El hecho ocurrió en el municipio de Metapán, en el departamento de Ahuachapán.",
                    "Se denunció un ataque contra una persona de San Miguel, perpetrado en el departamento de La Unión.")
    assert has_administrative_conflict(value)
    assert extract_administrative_mentions(value) == []


def test_exact_local_administrative_pair_can_distinguish_a_foreign_comparison_elsewhere():
    context = "El informe documenta la libertad de expresión en El Salvador y compara las políticas de California."
    local = "El hecho ocurrió en el municipio de Santa Rosa de Lima, en el norte del departamento de La Unión."
    result = extract_administrative_mention(article(context, local))
    assert result["department"] == "La Unión" and result["paragraph_id"] == "p2"
    # A country name in the same paragraph, a mismatched pair or nationality
    # alone cannot be repaired through the administrative reference.
    assert extract_administrative_mentions(article(context, local + " El hecho sucedió en Honduras.")) == []
    assert extract_administrative_mentions(article(context, local.replace("La Unión", "San Miguel"))) == []
    assert extract_administrative_mentions(article("Una persona salvadoreña compara las políticas de California.", local)) == []


@pytest.mark.parametrize("local", [
    "La persona nació en el municipio de Santa Rosa de Lima, en el norte del departamento de La Unión.",
    "La oficina está en el municipio de Santa Rosa de Lima, en el norte del departamento de La Unión.",
    "El informe nombra Santa Rosa de Lima y el departamento de La Unión.",
])
def test_local_pair_does_not_override_personal_institutional_or_unbound_roles(local):
    context = "El informe menciona El Salvador y compara California."
    assert extract_administrative_mentions(article(context, local)) == []


def test_foreign_comparison_does_not_suppress_an_independently_located_salvadoran_paragraph():
    value = article("En El Salvador, el municipio de Nahuizalco registra denuncias.",
                    "Otra parte del informe compara las políticas de Honduras.")
    assert extract_administrative_mention(value)["department"] == "Sonsonate"
    # A country name or nationality elsewhere does not locate a homonymous city.
    assert extract_administrative_mentions(article("El migrante salió de El Salvador.",
        "Vivió en California.", "Se registró la denuncia en el municipio de Santa Ana.")) == []
    assert extract_administrative_mentions(article("El Salvador analiza el asunto.",
        "Otra denuncia ocurrió en Honduras.", "Hay denuncias en el municipio de San Marcos.")) == []


@pytest.mark.parametrize("text", [
    "El hecho no ocurrió en los municipios de Nahuizalco y San Miguel.",
    "El hecho no ocurrió en los departamentos de Sonsonate y San Miguel.",
    "Los departamentos considerados no son Sonsonate y San Miguel.",
    "Las municipalidades consideradas no están: Santa Tecla y San Marcos.",
])
def test_plural_negated_location_lists_are_not_salvaged_as_positive_members(text):
    assert extract_administrative_mentions(article(text)) == []


@pytest.mark.parametrize("text", [
    "Los departamentos de la empresa son Santa Ana y San Miguel.",
    "Los departamentos de origen de las víctimas son Santa Ana y San Miguel.",
    "Las municipalidades de residencia de las personas son Santa Tecla y San Marcos.",
    "El asunto ocurrió en el municipio de San Miguel, Filipinas.",
])
def test_administrative_wording_does_not_override_organizational_personal_or_foreign_context(text):
    assert extract_administrative_mentions(article(text)) == []


def test_explicit_department_is_preferred_over_earlier_same_department_municipality():
    result = extract_administrative_mention(article(
        "Las comunidades trabajan en el distrito de Nahuizalco.",
        "El informe describe garantías en el departamento de Sonsonate."))
    assert result == {"method": "source_explicit_department", "department": "Sonsonate", "paragraph_id": "p2"}


def test_ambiguous_municipality_requires_same_clause_explicit_department():
    assert extract_administrative_mention(article("Las denuncias se registraron en el municipio de El Rosario.")) is None
    result = extract_administrative_mention(article(
        "Las denuncias se registraron en el municipio de El Rosario, departamento de La Paz."))
    assert result["department"] == "La Paz" and result["method"] == "source_explicit_department"
    assert extract_administrative_mention(article(
        "Se documentaron denuncias en el municipio de El Rosario.",
        "Otras denuncias ocurrieron en el departamento de Sonsonate.")) is None


def test_longest_official_name_wins_and_unknown_direction_suffixes_are_not_guessed():
    result = extract_administrative_mention(article("El programa se desarrolla en el municipio de San Salvador Norte."))
    assert result["resolution"]["municipality"] == "San Salvador Norte"
    assert result["resolution"]["municipality_kind"] == "municipality"
    assert extract_administrative_mention(article("El programa se desarrolla en el municipio de Nahuizalco Sur.")) is None


@pytest.mark.parametrize("text", [
    "El informe se refiere a denuncias en el municipio de San Miguel, Perú.",
    "Hay denuncias en el municipio de Nahuizalco. El asunto ocurrió en Guatemala.",
    "El Salvador compara el asunto con Honduras. Se realizaron actividades en Nahuizalco.",
    "El asunto se desarrolla en el departamento de La Libertad, en el Perú.",
    "La organización salvadoreña documentó detenciones en San Miguel, California.",
])
def test_foreign_context_fails_closed_even_with_salvadoran_or_known_administrative_names(text):
    assert extract_administrative_mention(article(text)) is None


@pytest.mark.parametrize("text", [
    "Nahuizalco: se realiza un encuentro en el municipio de Nahuizalco.",
    "San Salvador — el informe contiene denuncias en el departamento de San Salvador.",
    "San Salvador, 12 de octubre. El informe menciona el municipio de San Salvador.",
    "La sede de la organización está en el municipio de Nahuizalco.",
    "Las oficinas están en el departamento de Sonsonate.",
    "La sala de redacción funciona en el municipio de Nahuizalco.",
    "El tribunal del municipio de Nahuizalco conoció una causa.",
    "El juzgado está en el departamento de Sonsonate.",
    "La carretera hacia el municipio de Nahuizalco aparece en el informe.",
    "La ruta de transporte pasa por el municipio de Nahuizalco.",
    "La universidad de Nahuizalco presentó su informe.",
    "El departamento de San Salvador del Ministerio de Trabajo emitió un documento.",
    "La víctima nació en el municipio de Nahuizalco.",
    "La persona es originaria del municipio de Nahuizalco.",
    "La persona reside en el municipio de Nahuizalco.",
    "La persona vive en el municipio de Nahuizalco.",
    "El domicilio está en el municipio de Nahuizalco.",
    "El Salvador informa sobre una persona vecina del municipio de Nahuizalco.",
    "Las organizaciones de El Salvador se dirigieron a Nahuizalco por la carretera.",
])
def test_datelines_institutions_routes_and_personal_origin_or_residence_do_not_establish_a_location(text):
    assert extract_administrative_mention(article(text)) is None


@pytest.mark.parametrize("text", [
    "El hecho no ocurrió en el municipio de Nahuizalco.",
    "El operativo nunca se produjo en el departamento de Sonsonate.",
    "El Salvador aclara que el hecho fue en otro lugar, no en Nahuizalco.",
    "El Salvador aclara que en Nahuizalco no ocurrió el operativo.",
    "El Salvador aclara que en Nahuizalco no hubo una detención.",
    "El hecho no ocurrió en Victoria, departamento de Cabañas.",
    "No hubo una agresión en el municipio de Nahuizalco.",
])
def test_negated_locations_are_not_projected(text):
    assert extract_administrative_mention(article(text)) is None


def test_denial_of_a_service_is_not_automatically_a_denial_of_the_location():
    result = extract_administrative_mention(article(
        "En el municipio de Nahuizalco no hay acceso suficiente a agua potable."))
    assert result["department"] == "Sonsonate"


@pytest.mark.parametrize("text", [
    "Las organizaciones de El Salvador creen en la libertad.",
    "La organización salvadoreña insiste en la unión sindical.",
    "La población de El Salvador confía en Victoria.",
    "El debate en El Salvador se centra en la libertad de expresión.",
])
def test_common_abstract_words_are_not_silently_interpreted_as_municipalities(text):
    assert extract_administrative_mention(article(text)) is None


@pytest.mark.parametrize("text", [
    "En El Salvador depositamos nuestra esperanza en la unión.",
    "En El Salvador depositamos nuestra esperanza en la libertad.",
    "La organización salvadoreña depositó sus expectativas en Victoria.",
    "En El Salvador depositamos nuestra esperanza en el triunfo.",
    "La comunidad salvadoreña documentó restricciones en Armenia.",
])
def test_ambiguous_abstract_or_country_names_require_typed_administrative_context(text):
    assert extract_administrative_mention(article(text)) is None


@pytest.mark.parametrize("name,department", [("La Unión", "La Unión"), ("Armenia", "Sonsonate")])
def test_typed_municipalities_with_ambiguous_bare_names_remain_available(name, department):
    result = extract_administrative_mention(article(f"La comunidad documentó denuncias en el municipio de {name}."))
    assert result["method"] == "source_municipality_reference" and result["department"] == department
    assert result["resolution"]["municipality"] == name


def test_municipality_privacy_can_disable_resolution_without_removing_safe_explicit_department():
    municipal = "El programa se desarrolla en el municipio de Nahuizalco."
    assert extract_administrative_mention(article(municipal), allow_municipality=False) is None
    result = extract_administrative_mention(article(municipal,
        "La documentación se refiere al departamento de Sonsonate."), allow_municipality=False)
    assert result == {"method": "source_explicit_department", "department": "Sonsonate", "paragraph_id": "p2"}
    assert "resolution" not in result and "Nahuizalco" not in json.dumps(result)
    assert extract_administrative_mention(article(municipal), allow_municipality="false") is None


@pytest.mark.parametrize("mutation", [
    lambda value: value.update(content_sha256="a" * 64),
    lambda value: value.update(text=value["text"] + " Distinto"),
    lambda value: value["paragraphs"][0].update(id="p2"),
    lambda value: value["paragraphs"][0].update(text="Texto diferente"),
    lambda value: value["paragraphs"][0].update(private_quote="SENSITIVE"),
    lambda value: value.update(paragraphs=[]),
    lambda value: value.update(text=None),
])
def test_invalid_article_evidence_fails_closed(mutation):
    value = article("Hay denuncias en el municipio de Nahuizalco.")
    mutation(value)
    assert extract_administrative_mention(value) is None


def test_article_and_paragraph_limits_are_enforced_without_silent_truncation():
    assert extract_administrative_mention(article("x" * 4001)) is None
    assert extract_administrative_mention(article(*(["x" * 4000] * 6))) is None
    assert extract_administrative_mention(article(*(["x"] * 161))) is None


def test_return_has_no_source_text_people_coordinates_or_model_attribution():
    value = article("PRIVATE_PERSON_SENTINEL denunció un abuso en el municipio de Nahuizalco.")
    result = extract_administrative_mention(value)
    assert set(result) == {"method", "department", "paragraph_id", "resolution"}
    assert "PRIVATE_PERSON_SENTINEL" not in json.dumps(result)
    assert "text" not in result and "coordinates" not in result and "gemini" not in json.dumps(result)
