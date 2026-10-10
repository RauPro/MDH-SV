"""
Taxonomía Jerárquica Formal de Derechos Humanos para El Salvador (MDH-SV / WSDS 2.0).
Estructura de 4 Ramas basada en estándares del Sistema Interamericano (CADH),
HURIDOCS y la realidad nacional del Régimen de Excepción.
"""

from enum import Enum
from typing import Any, Dict, List
from pydantic import BaseModel, Field


class MacroCategoryEnum(str, Enum):
    """Rama Principal de la Taxonomía."""
    RAMA_1_VIDA = "DERECHO A LA VIDA E INTEGRIDAD PERSONAL"
    RAMA_2_LIBERTAD = "DERECHO A LA LIBERTAD Y AL DEBIDO PROCESO"
    RAMA_3_CIVILES = "LIBERTADES CIVILES Y DEMOCRÁTICAS"
    RAMA_4_POBLACIONES = "VÍCTIMAS Y POBLACIONES DE ATENCIÓN PRIORITARIA"


class SpecificViolationTypeEnum(str, Enum):
    """Subcategorías y Tipologías Específicas de Vulneración."""
    # RAMA 1: DERECHO A LA VIDA E INTEGRIDAD PERSONAL
    VIDA_PENAL = "Muertes bajo custodia estatal (Centros Penales / Bartolinas)"
    VIDA_HOMICIDIO_FEMINICIDIO = "Homicidios y Feminicidios"
    VIDA_DESAPARICION_FOSA = "Desapariciones forzadas y Fosas clandestinas"
    VIDA_USO_FUERZA = "Uso desproporcionado de la fuerza / Ejecuciones extrajudiciales"

    # RAMA 2: DERECHO A LA LIBERTAD Y AL DEBIDO PROCESO
    PROC_DETENCION_ARBITRARIA = "Detenciones arbitrarias (sin orden judicial / basadas en perfilamiento)"
    PROC_HABEAS_CORPUS = "Negativa o dilación de Hábeas Corpus / Exhibición Personal"
    PROC_JUICIO_COLECTIVO = "Juicios colectivos masivos sin individualización"
    PROC_INCOMUNICACION = "Incomunicación y falta de información a familiares"

    # RAMA 3: LIBERTADES CIVILES Y DEMOCRÁTICAS
    CIV_PRENSA_CENSURA = "Acoso, espionaje o censura a periodistas y medios"
    CIV_DEFENSORES = "Criminalización a defensores de derechos humanos y líderes comunitarios"
    CIV_ACCESO_INFO = "Restricciones al acceso a información pública (declaratorias de reserva)"

    OTRO = "Otra transgresión documentada"


class VulnerableGroupEnum(str, Enum):
    """RAMA 4: Víctimas y Poblaciones de Atención Prioritaria."""
    POB_NINEZ = "Niñez y adolescencia (menores de 18 años)"
    POB_MUJERES = "Mujeres (violencia de género, feminicidios)"
    POB_ENFERMOS_PENAL = "Personas con enfermedades crónicas o discapacidad en prisión"
    POB_LGBTIQ = "Población LGBTIQ+"
    POB_ADULTO_MAYOR = "Adultos mayores"
    POB_PERIODISTA = "Periodistas y trabajadores de medios"
    POB_DEFENSOR = "Defensores(as) de DDHH y líderes comunales"
    POB_GENERAL = "Población general / Sin grupo específico"


class ViolationCategory(str, Enum):
    """Categorías principales de violaciones a DDHH (compatibilidad pipeline/MCP)."""
    VIDA_INTEGRIDAD = "DERECHO A LA VIDA E INTEGRIDAD PERSONAL"
    LIBERTAD_DEBIDO_PROCESO = "DERECHO A LA LIBERTAD Y AL DEBIDO PROCESO"
    LIBERTADES_DEMOCRATICAS = "LIBERTADES CIVILES Y DEMOCRÁTICAS"
    POBLACIONES_VULNERABLES = "VÍCTIMAS Y POBLACIONES DE ATENCIÓN PRIORITARIA"
    NO_VINCULADO = "NO_VINCULADO"


class SpecificViolationType(str, Enum):
    """Tipologías específicas de vulneración (compatibilidad pipeline/MCP)."""
    MUERTE_EN_CUSTODIA_PENAL = "Muertes bajo custodia estatal (Centros Penales / Bartolinas)"
    HOMICIDIO_FEMINICIDIO = "Homicidios y Feminicidios"
    DESAPARICION_FOSA = "Desapariciones forzadas y Fosas clandestinas"
    USO_DESPROPORCIONADO_FUERZA = "Uso desproporcionado de la fuerza / Ejecuciones extrajudiciales"
    DETENCION_ARBITRARIA = "Detenciones arbitrarias (sin orden judicial / basadas en perfilamiento)"
    DILACION_HABEAS_CORPUS = "Negativa o dilación de Hábeas Corpus / Exhibición Personal"
    JUICIO_COLECTIVO = "Juicios colectivos masivos sin individualización"
    JUICIO_COLECTIVO_MASIVO = "Juicios colectivos masivos sin individualización"
    INCOMUNICACION = "Incomunicación y falta de información a familiares"
    ACOSO_ESPIONAJE_PRENSA = "Acoso, espionaje o censura a periodistas y medios"
    CRIMINALIZACION_DEFENSORES = "Criminalización a defensores de derechos humanos y líderes comunitarios"
    NEGACION_ACCESO_INFO = "Restricciones al acceso a información pública (declaratorias de reserva)"
    OTRO = "Otra transgresión documentada"


class VulnerableGroup(str, Enum):
    """Grupos de atención prioritaria (compatibilidad pipeline/MCP)."""
    NINEZ_ADOLESCENCIA = "Niñez y adolescencia (menores de 18 años)"
    MUJERES = "Mujeres (violencia de género, feminicidios)"
    PERSONAS_EN_PRISION = "Personas con enfermedades crónicas o discapacidad en prisión"
    PERSONA_ENFERMA_CRONICA = "Personas con enfermedades crónicas o discapacidad en prisión"
    LGBTIQ = "Población LGBTIQ+"
    ADULTO_MAYOR = "Adultos mayores"
    PERIODISTA = "Periodistas y trabajadores de medios"
    DEFENSOR_DDHH = "Defensores(as) de DDHH y líderes comunales"
    NINGUNO = "Población general / Sin grupo específico"


class TaxonomyNode(BaseModel):
    """Nodo descriptivo de la taxonomía con fundamentación jurídica."""
    code: str = Field(..., description="Código formal único de clasificación")
    name: str = Field(..., description="Nombre del tipo violatorio")
    macro_category: MacroCategoryEnum = Field(..., description="Rama principal a la que pertenece")
    legal_basis: str = Field(..., description="Fundamentación jurídica (CADH, Constitución SV)")
    detection_criteria: List[str] = Field(..., description="Criterios e indicadores léxico-semánticos")


TAXONOMY_REGISTRY: Dict[SpecificViolationTypeEnum, TaxonomyNode] = {
    SpecificViolationTypeEnum.VIDA_PENAL: TaxonomyNode(
        code="VIDA_PENAL",
        name="Muertes bajo custodia estatal (Centros Penales / Bartolinas)",
        macro_category=MacroCategoryEnum.RAMA_1_VIDA,
        legal_basis="Art. 4 y 5 CADH (Derecho a la Vida e Integridad); Art. 2 y 11 Constitución de El Salvador.",
        detection_criteria=[
            "Fallecimiento dentro de centros penales (Mariona, Izalco, CECOT, Apanteos)",
            "Entrega de cadáver a familiares desde hospital o morgue IML",
            "Menciones de golpizas, asfixia, falta de medicamentos o signos de violencia"
        ]
    ),
    SpecificViolationTypeEnum.VIDA_HOMICIDIO_FEMINICIDIO: TaxonomyNode(
        code="VIDA_HOMICIDIO_FEMINICIDIO",
        name="Homicidios y Feminicidios",
        macro_category=MacroCategoryEnum.RAMA_1_VIDA,
        legal_basis="Art. 4 CADH; Ley Especial Integral para una Vida Libre de Violencia para las Mujeres (LEIV).",
        detection_criteria=[
            "Homicidio agravado, feminicidio, asesinato",
            "Uso de arma de fuego, arma blanca, corvo o elemento contundente",
            "Móvil de género, intolerancia, ajuste o violencia armada"
        ]
    ),
    SpecificViolationTypeEnum.VIDA_DESAPARICION_FOSA: TaxonomyNode(
        code="VIDA_DESAPARICION_FOSA",
        name="Desapariciones forzadas y Fosas clandestinas",
        macro_category=MacroCategoryEnum.RAMA_1_VIDA,
        legal_basis="Convención Interamericana sobre Desaparición Forzada de Personas; Art. 2 Constitución SV.",
        detection_criteria=[
            "Persona no localizada tras ser vista por última vez con autoridades o desconocidos",
            "Hallazgo de osamentas, cementerios clandestinos o fosas comunes",
            "Denuncias de familiares ante Fiscalía sin respuesta"
        ]
    ),
    SpecificViolationTypeEnum.VIDA_USO_FUERZA: TaxonomyNode(
        code="VIDA_USO_FUERZA",
        name="Uso desproporcionado de la fuerza / Ejecuciones extrajudiciales",
        macro_category=MacroCategoryEnum.RAMA_1_VIDA,
        legal_basis="Principios Básicos sobre el Empleo de la Fuerza y de Armas de Fuego (ONU).",
        detection_criteria=[
            "Muerte de civiles en presuntos enfrentamientos armados con PNC o FAES",
            "Disparos a personas desarmadas en retenes o allanamientos",
            "Versiones contradictorias entre el reporte policial y testigos presenciales"
        ]
    ),
    SpecificViolationTypeEnum.PROC_DETENCION_ARBITRARIA: TaxonomyNode(
        code="PROC_DETENCION_ARBITRARIA",
        name="Detenciones arbitrarias (sin orden judicial / basadas en perfilamiento)",
        macro_category=MacroCategoryEnum.RAMA_2_LIBERTAD,
        legal_basis="Art. 7 CADH (Libertad Personal); Art. 11 y 13 Constitución de El Salvador.",
        detection_criteria=[
            "Capturas bajo Régimen de Excepción sin orden de detención de FGR ni flagrancia",
            "Perfilamiento discriminatorio por tatuajes artísticos, vestimenta o zona residencial",
            "Denuncia de inocencia y ausencia de antecedentes penales o ficha policial"
        ]
    ),
    SpecificViolationTypeEnum.PROC_HABEAS_CORPUS: TaxonomyNode(
        code="PROC_HABEAS_CORPUS",
        name="Negativa o dilación de Hábeas Corpus / Exhibición Personal",
        macro_category=MacroCategoryEnum.RAMA_2_LIBERTAD,
        legal_basis="Art. 25 CADH (Protección Judicial); Art. 11 Constitución de El Salvador.",
        detection_criteria=[
            "Recurso de hábeas corpus presentado ante Sala de lo Constitucional sin resolución",
            "Falta de nombramiento de juez ejecutor en el plazo de ley",
            "Desacato institucional para presentar físicamente al detenido"
        ]
    ),
    SpecificViolationTypeEnum.PROC_JUICIO_COLECTIVO: TaxonomyNode(
        code="PROC_JUICIO_COLECTIVO",
        name="Juicios colectivos masivos sin individualización",
        macro_category=MacroCategoryEnum.RAMA_2_LIBERTAD,
        legal_basis="Art. 8 CADH (Garantías Judiciales); Principio de presunción de inocencia.",
        detection_criteria=[
            "Audiencias judiciales colectivas de cientos o miles de imputados simultáneos",
            "Falta de individualización probatoria para cada procesado",
            "Imposibilidad real de comunicación y defensa técnica privada"
        ]
    ),
    SpecificViolationTypeEnum.PROC_INCOMUNICACION: TaxonomyNode(
        code="PROC_INCOMUNICACION",
        name="Incomunicación y falta de información a familiares",
        macro_category=MacroCategoryEnum.RAMA_2_LIBERTAD,
        legal_basis="Conjunto de Principios para la protección de todas las personas sometidas a cualquier forma de detención (ONU).",
        detection_criteria=[
            "Familias que desconocen el penal en que se encuentra su pariente detenido",
            "Negativa de información por parte de Centros Penales en ventanillas oficiales",
            "Prohibición prolongada de visitas y correspondencia básica"
        ]
    ),
    SpecificViolationTypeEnum.CIV_PRENSA_CENSURA: TaxonomyNode(
        code="CIV_PRENSA_CENSURA",
        name="Acoso, espionaje o censura a periodistas y medios",
        macro_category=MacroCategoryEnum.RAMA_3_CIVILES,
        legal_basis="Art. 13 CADH (Libertad de Pensamiento y Expresión); Art. 6 Constitución SV.",
        detection_criteria=[
            "Retención temporal de periodistas o equipos de comunicación en coberturas",
            "Revisión e incautación indebida de teléfonos y equipo fotográfico",
            "Alertas emitidas por la Asociación de Periodistas de El Salvador (APES) o CIDH"
        ]
    ),
    SpecificViolationTypeEnum.CIV_DEFENSORES: TaxonomyNode(
        code="CIV_DEFENSORES",
        name="Criminalización a defensores de derechos humanos y líderes comunitarios",
        macro_category=MacroCategoryEnum.RAMA_3_CIVILES,
        legal_basis="Declaración de la ONU sobre los Defensores de los Derechos Humanos.",
        detection_criteria=[
            "Apertura de causas penales sin fundamento fáctico contra directivos comunales",
            "Hostigamiento a miembros de organizaciones de DDHH (Cristosal, Tutela Legal, etc.)",
            "Acoso a líderes ambientalistas, sindicales o defensores del agua"
        ]
    ),
    SpecificViolationTypeEnum.CIV_ACCESO_INFO: TaxonomyNode(
        code="CIV_ACCESO_INFO",
        name="Restricciones al acceso a información pública (declaratorias de reserva)",
        macro_category=MacroCategoryEnum.RAMA_3_CIVILES,
        legal_basis="Ley de Acceso a la Información Pública (LAIP); Art. 13 CADH.",
        detection_criteria=[
            "Declaratorias de reserva total de estadísticas de homicidios o muertes en penales",
            "Negativa de entrega de información por FGR, PNC o Medicina Legal",
            "Bloqueo o desmantelamiento de unidades de acceso a la información pública"
        ]
    ),
    SpecificViolationTypeEnum.OTRO: TaxonomyNode(
        code="OTRO",
        name="Otra transgresión documentada",
        macro_category=MacroCategoryEnum.RAMA_3_CIVILES,
        legal_basis="Garantías fundamentales generales.",
        detection_criteria=["Hechos de transgresión a derechos fundamentales no tipificados en las categorías anteriores"]
    ),
}


def get_taxonomy_tree() -> Dict[str, Any]:
    """Retorna la estructura jerárquica completa con todas sus ramas y nodos."""
    tree: Dict[str, Any] = {
        macro.value: [] for macro in MacroCategoryEnum
    }
    for violation_type, node in TAXONOMY_REGISTRY.items():
        tree[node.macro_category.value].append({
            "code": node.code,
            "name": node.name,
            "legal_basis": node.legal_basis,
            "detection_criteria": node.detection_criteria
        })

    # Poblaciones vulnerables como 4ta rama
    tree[MacroCategoryEnum.RAMA_4_POBLACIONES.value] = [
        {"code": group.name, "name": group.value}
        for group in VulnerableGroupEnum
    ]
    return tree
