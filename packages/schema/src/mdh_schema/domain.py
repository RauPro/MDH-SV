"""
Dominio Central de Datos Forenses y los 12 Indicadores del Paper IEEE (MDH-SV / WSDS 2.0).
Integra la Taxonomía Jerárquica de 4 Ramas, los campos de investigación publicados en IEEE (10933836)
y la cadena de custodia criptográfica SHA-256.
"""

from datetime import date, datetime, timezone
from enum import Enum
import hashlib
from typing import Any, Dict, List, Optional, Union
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from .taxonomy import (
    MacroCategoryEnum,
    SpecificViolationType,
    SpecificViolationTypeEnum,
    ViolationCategory,
    VulnerableGroup,
    VulnerableGroupEnum,
)
from .territory import DepartmentEnum, GeoCoordinates


class SeverityLevel(str, Enum):
    """Nivel de gravedad del impacto sobre la víctima o la comunidad."""
    CRITICA = "Crítica (Pérdida de vida, desaparición, tortura documentada)"
    ALTA = "Alta (Privación arbitraria continuada, incomunicación severa, agresión física)"
    MEDIA = "Media (Hostigamiento, censura, dilación procesal no justificada)"
    LEVE = "Baja / Monitoreo preventivo"


class CertaintyLevel(str, Enum):
    """Nivel de certeza y corroboración de la evidencia recopilada."""
    CONFIRMADO = "Confirmado (Multifuente, dictamen forense, expediente judicial o informe de ONG acreditada)"
    TESTIMONIAL = "Testimonial (Denuncia documentada de familiares o testigos directos)"
    EN_INVESTIGACION = "En Investigación (Reporte periodístico inicial pendiente de corroboración)"


class WeaponType(str, Enum):
    """Indicador 9 del Paper IEEE: Tipo de Arma / Instrumento empleado."""
    ARMA_DE_FUEGO = "Arma de fuego"
    ARMA_BLANCA = "Arma blanca (cuchillo, navaja)"
    CORVO_MACHETE = "Corvo / Machete"
    OBJETO_CONTUNDENTE = "Objeto contundente (golpiza, palo, piedra)"
    ASFIXIA_ESTRANGULAMIENTO = "Asfixia mecánica / Estrangulamiento"
    NO_ESPECIFICADA = "No especificada / En investigación"
    NO_APLICA = "No aplica (violación administrativa o procesal)"


class InstitutionalSource(str, Enum):
    """Indicador 4 del Paper IEEE: Fuentes oficiales o testimoniales citadas."""
    PNC = "Policía Nacional Civil (PNC)"
    FGR = "Fiscalía General de la República (FGR)"
    IML = "Instituto de Medicina Legal (IML)"
    DGCP = "Dirección General de Centros Penales (DGCP)"
    FAMILIARES = "Declaraciones de familiares"
    TESTIGOS = "Testigos presenciales / Vecinos"
    ORGANIZACION_DDHH = "Organización defensora de DDHH (Cristosal, IDHUCA, Socorro Jurídico)"
    COMUNICADO_PRENSA = "Reporte periodístico sin cita institucional"


class ActorType(str, Enum):
    """Clasificación del presunto perpetrador o agente agresor."""
    ESTATAL_PNC = "Policía Nacional Civil (PNC)"
    ESTATAL_FAES = "Fuerza Armada de El Salvador (FAES)"
    ESTATAL_PENITENCIARIO = "Dirección General de Centros Penales / Custodios"
    ESTATAL_PENITENCIARIO_DGCP = "Dirección General de Centros Penales (DGCP) / Custodios"
    ESTATAL_JUDICIAL_FISCAL = "Fiscalía General / Órgano Judicial"
    ESTATAL_OTRO = "Otro funcionario o institución pública"
    NO_ESTATAL_PANDILLAS = "Estructura criminal / Pandilla"
    NO_ESTATAL_PARTICULAR = "Persona particular / Crimen común"
    DESCONOCIDO = "No identificado / En proceso de individualización"


class Victim(BaseModel):
    """Indicador 10 del Paper IEEE: Registro de persona afectada."""
    name: Optional[str] = Field(None, description="Nombre o iniciales para proteger identidad")
    age: Optional[int] = Field(None, ge=0, le=120, description="Edad estimada o confirmada")
    gender: Optional[str] = Field(None, description="Género (Femenino, Masculino, No binario, Desconocido)")
    occupation: Optional[str] = Field(None, description="Ocupación o profesión de la víctima")
    vulnerable_group: Optional[Union[VulnerableGroupEnum, VulnerableGroup, str]] = Field(
        default=VulnerableGroupEnum.POB_GENERAL,
        description="Rama 4 de la taxonomía: población de atención prioritaria"
    )
    is_anonymized: bool = Field(
        default=False,
        description="Si la identidad fue ofuscada por motivos de seguridad procesal o personal"
    )


class Perpetrator(BaseModel):
    """Indicador 11 del Paper IEEE: Agente individual o corporativo presuntamente responsable."""
    actor_type: Union[ActorType, str] = Field(..., description="Institución o tipo de actor")
    unit_or_division: Optional[str] = Field(None, description="Batallón, delegación policial o sección específica")
    identifier_or_alias: Optional[str] = Field(None, description="Nombre, número de placa o alias conocido")


class SourceEvidence(BaseModel):
    """Indicador 12 del Paper IEEE & Forense: Evidencia periodística con preservación inmutable."""
    outlet_name: str = Field(..., description="Nombre del medio (La Prensa Gráfica, El Faro, etc.)")
    original_url: str = Field(..., description="URL original de la publicación")
    title: str = Field(..., description="Titular de la nota periodística")
    published_date: Optional[date] = Field(None, description="Fecha de publicación de la noticia")
    archive_url: Optional[str] = Field(
        None,
        description="Enlace permanente de respaldo en la Wayback Machine (Internet Archive)"
    )
    source_sha256: str = Field(
        ...,
        description="Hash criptográfico SHA-256 del contenido crudo original para verificación inmutable"
    )
    extracted_quote: Optional[str] = Field(
        None,
        description="Cita textual clave de la fuente"
    )

    @staticmethod
    def calculate_hash(content: str) -> str:
        """Calcula el hash SHA-256 de un contenido de texto plano o HTML."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()


class HumanRightsIncident(BaseModel):
    """
    Expediente central de un hecho violatorio en El Salvador.
    Integra de forma unificada los 12 Indicadores del Paper IEEE (10933836)
    y la Taxonomía Jerárquica de 4 Ramas.
    """
    id: str = Field(default_factory=lambda: str(uuid4()), description="Identificador único del expediente")
    incident_date: Optional[date] = Field(None, description="Fecha en que ocurrió el suceso")

    # --- INDICADOR 1: Clasificación formal (Ramas 1, 2 y 3) ---
    macro_category: Optional[MacroCategoryEnum] = Field(default=None, description="Rama principal de la taxonomía")
    category: Optional[Union[ViolationCategory, MacroCategoryEnum, str]] = Field(default=None, description="Categoría o rama de la violación")
    violation_type: Union[SpecificViolationTypeEnum, SpecificViolationType, str] = Field(..., description="Subtipo específico codificado")
    severity: SeverityLevel = Field(default=SeverityLevel.ALTA, description="Severidad del hecho")
    certainty: CertaintyLevel = Field(default=CertaintyLevel.EN_INVESTIGACION, description="Nivel probatorio")

    # --- INDICADOR 2: Título y Resumen fáctico ---
    title: str = Field(..., description="Título neutral del expediente forense")
    summary: str = Field(..., description="Síntesis fáctica de los hechos reportados")

    # --- INDICADOR 3: Lugar de los Hechos (Georreferenciación) ---
    department: DepartmentEnum = Field(..., description="Departamento de El Salvador (14 oficiales)")
    municipality: str = Field(..., description="Municipio (de entre los 44 nuevos municipios)")
    specific_location: Optional[str] = Field(None, description="Cantón, caserío, colonia o penal")
    coordinates: GeoCoordinates = Field(..., description="Coordenadas geográficas WGS84")

    # --- INDICADOR 4: Fuentes Institucionales Citadas ---
    institutional_sources: List[InstitutionalSource] = Field(
        default_factory=list,
        description="Instituciones y actores oficiales o testimoniales citados en la nota"
    )

    # --- INDICADOR 5: Temas Clave ---
    thematic_tags: List[str] = Field(
        default_factory=list,
        description="Etiquetas léxico-semánticas del hecho"
    )

    # --- INDICADOR 6: Hechos Violatorios ---
    violatory_facts: Optional[str] = Field(
        default="Hechos violatorios documentados en la investigación forense.",
        description="Desglose de conductas de transgresión a garantías o preceptos legales"
    )

    @model_validator(mode="before")
    @classmethod
    def sync_and_validate_categories(cls, data: Any) -> Any:
        if isinstance(data, dict):
            cat = data.get("category") or data.get("macro_category")
            if cat is not None:
                cat_val = (cat.value if hasattr(cat, "value") else str(cat)).strip().upper()
                matched_macro = None
                matched_category = None
                for m in MacroCategoryEnum:
                    if m.value.upper() == cat_val:
                        matched_macro = m
                        break
                for v in ViolationCategory:
                    if v.value.upper() == cat_val:
                        matched_category = v
                        break
                if not matched_macro:
                    if "VIDA" in cat_val:
                        matched_macro = MacroCategoryEnum.RAMA_1_VIDA
                    elif "LIBERTAD" in cat_val or "PROCESO" in cat_val:
                        matched_macro = MacroCategoryEnum.RAMA_2_LIBERTAD
                    elif "CIVIL" in cat_val or "PRENSA" in cat_val or "DEMOCRAT" in cat_val:
                        matched_macro = MacroCategoryEnum.RAMA_3_CIVILES
                    elif "POBLACION" in cat_val or "VICTIMA" in cat_val:
                        matched_macro = MacroCategoryEnum.RAMA_4_POBLACIONES

                if not matched_category:
                    if "VIDA" in cat_val:
                        matched_category = ViolationCategory.VIDA_INTEGRIDAD
                    elif "LIBERTAD" in cat_val or "PROCESO" in cat_val:
                        matched_category = ViolationCategory.LIBERTAD_DEBIDO_PROCESO
                    elif "CIVIL" in cat_val or "PRENSA" in cat_val or "DEMOCRAT" in cat_val:
                        matched_category = ViolationCategory.LIBERTADES_DEMOCRATICAS
                    elif "POBLACION" in cat_val or "VICTIMA" in cat_val:
                        matched_category = ViolationCategory.POBLACIONES_VULNERABLES
                    elif "NO_VINCULADO" in cat_val:
                        matched_category = ViolationCategory.NO_VINCULADO

                data["macro_category"] = matched_macro or MacroCategoryEnum.RAMA_1_VIDA
                data["category"] = matched_category or ViolationCategory.VIDA_INTEGRIDAD
            else:
                raise ValueError("Se requiere 'macro_category' o 'category'")
        return data

    @model_validator(mode="after")
    def populate_category_synonyms(self) -> "HumanRightsIncident":
        if self.category is None and self.macro_category is not None:
            for v in ViolationCategory:
                if v.value == self.macro_category.value:
                    self.category = v
                    break
            if self.category is None:
                self.category = self.macro_category
        elif self.macro_category is None and self.category is not None:
            for m in MacroCategoryEnum:
                if m.value == self.category.value:
                    self.macro_category = m
                    break
            if self.macro_category is None:
                self.macro_category = self.category
        return self

    # --- INDICADOR 7: Hipótesis de Hechos / Móvil ---
    fact_hypothesis: Optional[str] = Field(
        None,
        description="Línea de investigación, móvil del crimen o versiones contrapuestas"
    )

    # --- INDICADOR 8: Población Vulnerable (Rama 4 de la Taxonomía) ---
    vulnerable_populations: List[VulnerableGroupEnum] = Field(
        default_factory=list,
        description="Grupos de atención prioritaria vinculados al suceso"
    )

    # --- INDICADOR 9: Tipo de Arma ---
    weapon_type: WeaponType = Field(
        default=WeaponType.NO_APLICA,
        description="Instrumento o medio empleado en la agresión"
    )

    # --- INDICADOR 10: Víctimas ---
    victims: List[Victim] = Field(default_factory=list, description="Lista de víctimas vinculadas")
    victims_count: Optional[int] = Field(None, description="Número total de víctimas afectadas (censos macro, denuncias colectivas o informes OUDH)")

    # --- INDICADOR 11: Victimarios o Presuntos Agresores ---
    perpetrators: List[Perpetrator] = Field(default_factory=list, description="Lista de presuntos responsables")

    # --- INDICADOR 12: Cadena de Custodia & Evidencia Inmutable ---
    sources: List[SourceEvidence] = Field(
        default_factory=list,
        description="Fuentes documentales con URL, Wayback Machine y hash SHA-256"
    )

    # Contexto del Régimen de Excepción y Centros Penitenciarios
    under_state_of_exception: bool = Field(
        default=True,
        description="Si el hecho ocurrió en el marco del Régimen de Excepción vigente desde marzo 2022"
    )
    penitentiary_related: bool = Field(
        default=False,
        description="Si el suceso ocurrió en un centro penal o bartolina policial"
    )
    prison_name: Optional[str] = Field(
        None,
        description="Nombre del penal si aplica (ej. CECOT, Mariona, Izalco, Ilopango)"
    )

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def generate_dedup_key(self) -> str:
        """Clave espacio-temporal determinista para correlación."""
        date_str = self.incident_date.isoformat() if self.incident_date else "nodate"
        mun_clean = self.municipality.strip().lower()
        return f"{self.department.value}:{mun_clean}:{date_str}"

    def to_geojson_feature(self) -> Dict[str, Any]:
        """Exportación a Feature GeoJSON estándar RFC 7946."""
        cat_val = self.macro_category.value if self.macro_category else (self.category.value if hasattr(self.category, "value") else str(self.category or ""))
        violation_val = self.violation_type.value if hasattr(self.violation_type, "value") else str(self.violation_type)

        sources_data = []
        for s in self.sources:
            sd = s.model_dump(mode="json")
            arch = sd.get("archive_url")
            if arch and (arch in ("https://web.archive.org", "https://web.archive.org/") or "*" in arch):
                sd["archive_url"] = None
            sources_data.append(sd)

        return {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [self.coordinates.longitude, self.coordinates.latitude]
            },
            "properties": {
                "id": self.id,
                "title": self.title,
                "summary": self.summary,
                "macro_category": cat_val,
                "category": cat_val,
                "violation_type": violation_val,
                "severity": self.severity.value,
                "certainty": self.certainty.value,
                "incident_date": self.incident_date.isoformat() if self.incident_date else None,
                "department": self.department.value,
                "municipality": self.municipality,
                "specific_location": self.specific_location,
                "weapon_type": self.weapon_type.value,
                "thematic_tags": self.thematic_tags,
                "violatory_facts": self.violatory_facts,
                "fact_hypothesis": self.fact_hypothesis,
                "institutional_sources": [s.value for s in self.institutional_sources],
                "vulnerable_populations": [p.value for p in self.vulnerable_populations],
                "victims_count": self.victims_count if self.victims_count is not None else len(self.victims),
                "sources_count": len(self.sources),
                "under_state_of_exception": self.under_state_of_exception,
                "penitentiary_related": self.penitentiary_related,
                "prison_name": self.prison_name,
                "sources": sources_data,
                "victims": [v.model_dump(mode="json") for v in self.victims],
                "perpetrators": [p.model_dump(mode="json") for p in self.perpetrators],
            }
        }

    def generate_academic_citation(self) -> str:
        """Citación académica estandarizada para papers e informes forenses."""
        fecha = self.incident_date.strftime("%d/%m/%Y") if self.incident_date else "s.f."
        fuentes = ", ".join({s.outlet_name for s in self.sources}) or "Fuente sin identificar"
        cat_val = self.macro_category.value if self.macro_category else (self.category.value if hasattr(self.category, "value") else str(self.category or ""))
        violation_val = self.violation_type.value if hasattr(self.violation_type, "value") else str(self.violation_type)
        return (
            f"Monitor de Derechos Humanos El Salvador (MDH-SV / IEEE 10933836). "
            f"Expediente #{self.id[:8]}: \"{self.title}\" ({fecha}). "
            f"Ubicación: {self.municipality}, {self.department.value}. "
            f"Categoría: {cat_val} / {violation_val}. "
            f"Documentado en: {fuentes}. Registro inmutable con verificación SHA-256."
        )
