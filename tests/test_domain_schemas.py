"""
Pruebas automatizadas para la Taxonomía de 4 Ramas y los 12 Indicadores IEEE (Fase 1).
"""

from datetime import date
import pytest
from pydantic import ValidationError

from mdh_schema import (
    ActorType,
    CertaintyLevel,
    DepartmentEnum,
    HumanRightsIncident,
    InstitutionalSource,
    MacroCategoryEnum,
    Perpetrator,
    SALVADOR_MUNICIPALITIES,
    SeverityLevel,
    SourceEvidence,
    SpecificViolationTypeEnum,
    Victim,
    VulnerableGroupEnum,
    WeaponType,
    get_default_coordinates,
    get_taxonomy_tree,
    normalize_department,
    normalize_municipality,
)


def test_taxonomy_4_branches_structure():
    """Verifica que el árbol taxonómico contenga las 4 ramas formales."""
    tree = get_taxonomy_tree()

    # Verificar que existen las 4 ramas principales
    assert MacroCategoryEnum.RAMA_1_VIDA.value in tree
    assert MacroCategoryEnum.RAMA_2_LIBERTAD.value in tree
    assert MacroCategoryEnum.RAMA_3_CIVILES.value in tree
    assert MacroCategoryEnum.RAMA_4_POBLACIONES.value in tree

    # Verificar subcategorías de Vida (Rama 1)
    vida_codes = [node["code"] for node in tree[MacroCategoryEnum.RAMA_1_VIDA.value]]
    assert "VIDA_PENAL" in vida_codes
    assert "VIDA_HOMICIDIO_FEMINICIDIO" in vida_codes
    assert "VIDA_DESAPARICION_FOSA" in vida_codes
    assert "VIDA_USO_FUERZA" in vida_codes

    # Verificar subcategorías de Debido Proceso (Rama 2)
    proc_codes = [node["code"] for node in tree[MacroCategoryEnum.RAMA_2_LIBERTAD.value]]
    assert "PROC_DETENCION_ARBITRARIA" in proc_codes
    assert "PROC_HABEAS_CORPUS" in proc_codes
    assert "PROC_JUICIO_COLECTIVO" in proc_codes
    assert "PROC_INCOMUNICACION" in proc_codes

    # Verificar subcategorías de Libertades Civiles (Rama 3)
    civ_codes = [node["code"] for node in tree[MacroCategoryEnum.RAMA_3_CIVILES.value]]
    assert "CIV_PRENSA_CENSURA" in civ_codes
    assert "CIV_DEFENSORES" in civ_codes
    assert "CIV_ACCESO_INFO" in civ_codes

    # Verificar poblaciones de atención prioritaria (Rama 4)
    pob_codes = [node["code"] for node in tree[MacroCategoryEnum.RAMA_4_POBLACIONES.value]]
    assert "POB_NINEZ" in pob_codes
    assert "POB_MUJERES" in pob_codes
    assert "POB_ENFERMOS_PENAL" in pob_codes
    assert "POB_LGBTIQ" in pob_codes


def test_territory_normalization():
    """Verifica la normalización de departamentos tolerante a tildes y mayúsculas."""
    assert normalize_department("San Salvador") == DepartmentEnum.SAN_SALVADOR
    assert normalize_department("santa ana") == DepartmentEnum.SANTA_ANA
    assert normalize_department("ahuachapan") == DepartmentEnum.AHUACHAPAN
    assert normalize_department("Cuscatlan") == DepartmentEnum.CUSCATLAN
    assert normalize_department("morazan") == DepartmentEnum.MORAZAN
    assert normalize_department("la union") == DepartmentEnum.LA_UNION
    assert normalize_department("cabanas") == DepartmentEnum.CABANAS
    assert normalize_department("Inexistente") == DepartmentEnum.DESCONOCIDO
    assert normalize_department(None) == DepartmentEnum.DESCONOCIDO


def test_municipalities_count():
    """Verifica que los 14 departamentos contengan los 44 nuevos municipios oficiales."""
    total_muns = sum(
        len(muns) for dept, muns in SALVADOR_MUNICIPALITIES.items()
        if dept != DepartmentEnum.DESCONOCIDO
    )
    assert total_muns == 44, f"Se esperaban 44 municipios, se encontraron {total_muns}"


def test_municipality_normalization():
    """Verifica que los distritos históricos se normalicen a los 44 municipios oficiales."""
    assert normalize_municipality("Ilopango", DepartmentEnum.SAN_SALVADOR) == "San Salvador Este"
    assert normalize_municipality("Soyapango") == "San Salvador Este"
    assert normalize_municipality("Tecoluca") == "San Vicente Sur"
    assert normalize_municipality("Izalco") == "Sonsonate Este"
    assert normalize_municipality("Apopa") == "San Salvador Oeste"
    assert normalize_municipality("Santa Tecla") == "La Libertad Sur"
    assert normalize_municipality("San Francisco Gotera") == "Morazán Sur"
    assert normalize_municipality("Victoria / Santa Marta") == "Cabañas Este"


def test_incident_with_12_ieee_indicators():
    """Verifica la creación íntegra de un expediente forense con los 12 indicadores del paper."""
    coords = get_default_coordinates(DepartmentEnum.SONSONATE)

    evidence = SourceEvidence(
        outlet_name="El Faro",
        original_url="https://elfaro.net/es/202402/el_salvador/muerte-joven-izalco",
        title="Fallece joven recluido en Centro Penal de Izalco",
        published_date=date(2024, 2, 22),
        archive_url="https://web.archive.org/web/20240222/https://elfaro.net/muerte-izalco",
        source_sha256=SourceEvidence.calculate_hash("Contenido original de prueba para SHA-256"),
        extracted_quote="Medicina Legal confirmó asfixia mecánica y golpes contusos."
    )

    victim = Victim(
        name="C. E. T.",
        age=22,
        gender="Masculino",
        occupation="Estudiante universitario",
        vulnerable_group=VulnerableGroupEnum.POB_ENFERMOS_PENAL,
        is_anonymized=True
    )

    perpetrator = Perpetrator(
        actor_type=ActorType.ESTATAL_PENITENCIARIO,
        unit_or_division="Custodios Penal de Izalco Fase II"
    )

    incident = HumanRightsIncident(
        # 1. Clasificación formal
        macro_category=MacroCategoryEnum.RAMA_1_VIDA,
        violation_type=SpecificViolationTypeEnum.VIDA_PENAL,
        severity=SeverityLevel.CRITICA,
        certainty=CertaintyLevel.CONFIRMADO,

        # 2. Título y Resumen
        title="Muerte bajo custodia de joven en Penal de Izalco",
        summary="Joven de 22 años capturado en el régimen falleció con signos de violencia dentro de Izalco.",

        # 3. Lugar de los Hechos
        department=DepartmentEnum.SONSONATE,
        municipality="Sonsonate Este",
        specific_location="Centro Penal de Izalco",
        coordinates=coords,

        # 4. Fuentes Institucionales Citadas
        institutional_sources=[
            InstitutionalSource.IML,
            InstitutionalSource.FAMILIARES,
            InstitutionalSource.ORGANIZACION_DDHH
        ],

        # 5. Temas Clave
        thematic_tags=["muerte bajo custodia", "asfixia mecánica", "régimen de excepción", "izalco"],

        # 6. Hechos Violatorios
        violatory_facts="Omisión de auxilio médico, presunta golpiza y muerte violenta bajo custodia estatal.",

        # 7. Hipótesis de Hechos / Móvil
        fact_hypothesis="Versión oficial alega causa súbita; dictamen forense y familiares sostienen golpiza por custodios.",

        # 8. Población Vulnerable (Rama 4)
        vulnerable_populations=[VulnerableGroupEnum.POB_ENFERMOS_PENAL],

        # 9. Tipo de Arma
        weapon_type=WeaponType.ASFIXIA_ESTRANGULAMIENTO,

        # 10. Víctimas
        victims=[victim],

        # 11. Victimario / Agresores
        perpetrators=[perpetrator],

        # 12. Cadena de Custodia
        sources=[evidence],

        # Contexto
        under_state_of_exception=True,
        penitentiary_related=True,
        prison_name="Izalco"
    )

    # Validaciones
    assert incident.macro_category == MacroCategoryEnum.RAMA_1_VIDA
    assert incident.violation_type == SpecificViolationTypeEnum.VIDA_PENAL
    assert incident.weapon_type == WeaponType.ASFIXIA_ESTRANGULAMIENTO
    assert len(incident.institutional_sources) == 3
    assert len(incident.thematic_tags) == 4
    assert incident.fact_hypothesis is not None

    # Validar GeoJSON Feature
    geojson = incident.to_geojson_feature()
    assert geojson["type"] == "Feature"
    assert geojson["geometry"]["coordinates"] == [coords.longitude, coords.latitude]
    assert geojson["properties"]["weapon_type"] == WeaponType.ASFIXIA_ESTRANGULAMIENTO.value
    assert geojson["properties"]["macro_category"] == MacroCategoryEnum.RAMA_1_VIDA.value
    assert len(geojson["properties"]["sources"]) == 1
    assert geojson["properties"]["sources"][0]["original_url"] == "https://elfaro.net/es/202402/el_salvador/muerte-joven-izalco"
    assert geojson["properties"]["sources"][0]["archive_url"] == "https://web.archive.org/web/20240222/https://elfaro.net/muerte-izalco"
    assert len(geojson["properties"]["victims"]) == 1
    assert len(geojson["properties"]["perpetrators"]) == 1

    # Validar Citación
    citation = incident.generate_academic_citation()
    assert "IEEE 10933836" in citation
    assert "Sonsonate Este" in citation


def test_incident_validation_required_fields():
    """Verifica que Pydantic capture errores si falta algún campo obligatorio de los 12 indicadores."""
    with pytest.raises(ValidationError):
        # Falta violatory_facts, macro_category, etc.
        HumanRightsIncident(
            title="Incidente incompleto",
            summary="Faltan indicadores obligatorios"
        )
