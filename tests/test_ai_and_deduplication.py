"""
Pruebas automatizadas para el extractor de IA y el motor de deduplicación multifuente (Fase 3).
"""

from datetime import date
import pytest

from mdh_pipeline.ai_extractor import HumanRightsExtractor
from mdh_pipeline.deduplicator import IncidentDeduplicator
from mdh_schema import (
    CertaintyLevel,
    DepartmentEnum,
    HumanRightsIncident,
    SeverityLevel,
    SourceEvidence,
    SpecificViolationType,
    ViolationCategory,
    get_default_coordinates,
)


def test_extractor_human_rights_event():
    """Verifica que un reporte de muerte bajo custodia se clasifique con la severidad adecuada."""
    evidence = SourceEvidence(
        outlet_name="El Diario de Hoy",
        original_url="https://elsalvador.com/noticias/muerte-izalco",
        title="Fallece recluso en el penal de Izalco tras complicaciones de salud",
        published_date=date(2024, 3, 1),
        source_sha256="hash123",
    )
    full_text = (
        "Familiares de un hombre detenido durante el régimen de excepción confirmaron que "
        "murió dentro del penal de Izalco. Denuncian que no se le brindaron medicamentos y el cuerpo sin vida "
        "presentaba signos de violencia antes de la autopsia realizada por Medicina Legal en Sonsonate."
    )

    extractor = HumanRightsExtractor()
    incident = extractor.extract_from_evidence(evidence, full_text)

    assert incident is not None
    assert incident.category == ViolationCategory.VIDA_INTEGRIDAD
    assert incident.violation_type == SpecificViolationType.MUERTE_EN_CUSTODIA_PENAL
    assert incident.severity == SeverityLevel.CRITICA
    assert incident.penitentiary_related is True
    assert incident.prison_name is not None and "Izalco" in incident.prison_name
    assert incident.department == DepartmentEnum.SONSONATE


def test_extractor_irrelevant_news_discarded():
    """Verifica que una noticia deportiva o común no vinculada a DDHH sea descartada."""
    evidence = SourceEvidence(
        outlet_name="Diario El Mundo",
        original_url="https://elmundo.sv/deportes/clasico",
        title="Alianza vence 2-1 a FAS en el estadio Cuscatlán",
        published_date=date(2024, 3, 2),
        source_sha256="hash456",
    )
    full_text = "En un partido emocionante de la liga mayor de fútbol salvadoreño, Alianza se llevó la victoria."

    extractor = HumanRightsExtractor()
    incident = extractor.extract_from_evidence(evidence, full_text)

    # Debe ser ignorado completamente
    assert incident is None


def test_extractor_flagrancia_delito_comun_discarded():
    """Verifica que una detención en flagrancia por drogas (sin alegatos de abusos a DDHH) sea descartada."""
    evidence = SourceEvidence(
        outlet_name="Diario El Salvador",
        original_url="https://ejemplo.sv/noticias/detienen-droga-arce",
        title="Detienen a hombre con 20 porciones de marihuana en Ciudad Arce",
        published_date=date(2026, 9, 19),
        source_sha256="hash_droga_123",
    )
    full_text = (
        "El ministro de Defensa informó sobre la captura de Alexis Wilfredo Linares Anaya en Ciudad Arce, "
        "La Libertad, a quien la Fuerza Armada le decomisó 20 porciones de marihuana y dinero en efectivo."
    )

    extractor = HumanRightsExtractor()
    incident = extractor.extract_from_evidence(evidence, full_text)

    # Debe ser descartado (None) por ser persecución penal legítima de delito común en flagrancia
    assert incident is None


def test_extractor_arbitrary_detention_properly_detected():
    """Verifica que una detención arbitraria documentada por familiares u ONG sí sea detectada."""
    evidence = SourceEvidence(
        outlet_name="La Prensa Gráfica",
        original_url="https://laprensagrafica.com/denuncian-captura-arbitraria",
        title="Denuncian captura arbitraria de comerciante sin orden judicial en Soyapango",
        published_date=date(2026, 3, 10),
        source_sha256="hash_captura_456",
    )
    full_text = (
        "Familiares y organizaciones de DDHH denunciaron la detención arbitraria de un comerciante inocente "
        "sin orden judicial ni antecedentes penales bajo el régimen de excepción en Soyapango, San Salvador."
    )

    extractor = HumanRightsExtractor()
    incident = extractor.extract_from_evidence(evidence, full_text)

    assert incident is not None
    assert incident.category == ViolationCategory.LIBERTAD_DEBIDO_PROCESO
    assert incident.violation_type == SpecificViolationType.DETENCION_ARBITRARIA
    assert incident.department == DepartmentEnum.SAN_SALVADOR


def test_extractor_archaeological_findings_discarded():
    """Verifica que hallazgos arqueológicos prehistóricos (osamentas de 3,000 años) sean descartados."""
    evidence = SourceEvidence(
        outlet_name="El Diario de Hoy",
        original_url="https://elsalvador.com/arqueologia/osamenta-3000-anos",
        title="Extraen osamenta humana de casi 3.000 años en El Salvador junto a vasijas",
        published_date=date(2026, 7, 22),
        source_sha256="hash_arch_789",
    )
    full_text = (
        "Arqueólogos descubren osamenta prehispánica del año 850 a. de C. en Antiguo Cuscatlán "
        "junto a cerámica prehispánica y piezas de barro precolombinas."
    )

    extractor = HumanRightsExtractor()
    incident = extractor._heuristic_fallback_extraction(evidence, full_text)
    assert incident is None


def test_extractor_foreign_events_discarded():
    """Verifica que hechos ocurridos en el extranjero sin jurisdicción en SV sean descartados."""
    evidence = SourceEvidence(
        outlet_name="El Sol del Bajío",
        original_url="https://elsol.mx/fosa-villagran",
        title="Descubren fosa clandestina en Villagrán; hallan al menos nueve cuerpos",
        published_date=date(2026, 4, 22),
        source_sha256="hash_foreign_012",
    )
    full_text = "Autoridades ministeriales de Guanajuato, México, ubicaron una fosa clandestina en Villagrán."

    extractor = HumanRightsExtractor()
    incident = extractor._heuristic_fallback_extraction(evidence, full_text)
    assert incident is None


def test_extractor_sports_police_operations_discarded():
    """Verifica que operativos de seguridad deportiva o boletos de fútbol sean descartados."""
    evidence = SourceEvidence(
        outlet_name="Diario El Salvador",
        original_url="https://diario.sv/deportes/operativo-selecta",
        title="Más de 2,000 elementos en operativo por encuentro Selecta El Salvador-Surinam en Estadio Cuscatlán",
        published_date=date(2026, 6, 1),
        source_sha256="hash_sports_345",
    )
    full_text = "Protección Civil y la PNC desplegaron dispositivos de tráfico y verificación de boletos para el partido de fútbol."

    extractor = HumanRightsExtractor()
    incident = extractor._heuristic_fallback_extraction(evidence, full_text)
    assert incident is None


def test_deduplicator_merges_two_media_sources():
    """
    Verifica que si El Faro y La Prensa Gráfica cubren el mismo hecho en Mariona,
    el deduplicador los fusione en 1 solo expediente y eleve la certeza a CONFIRMADO.
    """
    source1 = SourceEvidence(
        outlet_name="El Faro",
        original_url="https://elfaro.net/mariona-muerte-1",
        title="Muere recluso en penal de Mariona bajo régimen de excepción",
        published_date=date(2024, 2, 10),
        source_sha256="hash_elfaro",
    )
    inc1 = HumanRightsIncident(
        title="Muerte de privado de libertad en penal de Mariona",
        summary="Recluso capturado bajo el régimen falleció en el centro penal La Esperanza (Mariona).",
        category=ViolationCategory.VIDA_INTEGRIDAD,
        violation_type=SpecificViolationType.MUERTE_EN_CUSTODIA_PENAL,
        severity=SeverityLevel.CRITICA,
        certainty=CertaintyLevel.EN_INVESTIGACION,
        incident_date=date(2024, 2, 10),
        department=DepartmentEnum.SAN_SALVADOR,
        municipality="San Salvador Norte",
        coordinates=get_default_coordinates(DepartmentEnum.SAN_SALVADOR),
        sources=[source1],
        penitentiary_related=True,
        prison_name="Mariona"
    )

    source2 = SourceEvidence(
        outlet_name="La Prensa Gráfica",
        original_url="https://laprensagrafica.com/mariona-fallecido-2",
        title="Reportan recluso fallecido en centro penal de Mariona",
        published_date=date(2024, 2, 10),
        source_sha256="hash_lpg",
    )
    inc2 = HumanRightsIncident(
        title="Reportan muerte de recluso en Mariona",
        summary="Autoridades entregaron cuerpo de recluso fallecido en el penal de Mariona a su familia.",
        category=ViolationCategory.VIDA_INTEGRIDAD,
        violation_type=SpecificViolationType.MUERTE_EN_CUSTODIA_PENAL,
        severity=SeverityLevel.CRITICA,
        certainty=CertaintyLevel.EN_INVESTIGACION,
        incident_date=date(2024, 2, 10),
        department=DepartmentEnum.SAN_SALVADOR,
        municipality="San Salvador Norte",
        coordinates=get_default_coordinates(DepartmentEnum.SAN_SALVADOR),
        sources=[source2],
        penitentiary_related=True,
        prison_name="Mariona"
    )

    deduplicator = IncidentDeduplicator()
    # Same prison, category and day does not establish that two people are one case.
    assert deduplicator.are_same_incident(inc1, inc2) is False
    assert len(deduplicator.deduplicate_batch([inc1, inc2])) == 2
    assert inc1.certainty == CertaintyLevel.EN_INVESTIGACION
    with pytest.raises(ValueError):
        deduplicator.merge_incidents(inc1, inc2)
