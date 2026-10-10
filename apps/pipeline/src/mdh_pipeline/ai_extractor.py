"""
Motor de Extracción Estructurada con IA (Google Gemini).
Analiza noticias periodísticas y extrae expedientes forenses de Derechos Humanos validados con Pydantic.
Incluye un motor de respaldo heurístico/léxico para operación offline o sin API key.
"""

import json
import logging
import os
import re
from datetime import date
from typing import Optional, Tuple
from dotenv import load_dotenv

load_dotenv()

from mdh_schema import (
    ActorType,
    CertaintyLevel,
    DepartmentEnum,
    HumanRightsIncident,
    Perpetrator,
    SeverityLevel,
    SourceEvidence,
    SpecificViolationType,
    Victim,
    ViolationCategory,
    VulnerableGroup,
    get_default_coordinates,
    normalize_department,
    normalize_municipality,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_FORENSE = """
Eres un Analista Forense y Documentador Senior de Derechos Humanos para El Salvador (MDH-SV).
Tu tarea es analizar minuciosamente el artículo periodístico proporcionado y determinar con objetividad y rigor:

1. ¿Constituye o describe una presunta vulneración a Derechos Humanos en El Salvador?
   - Vulneraciones típicas en el contexto salvadoreño:
     * Muertes en custodia penitenciaria (Centros penales: Mariona, CECOT, Izalco, Bartolinas, etc.)
     * Detenciones arbitrarias bajo el Régimen de Excepción (sin orden judicial, perfilamiento discriminatorio, denuncias fundadas de inocencia)
     * Desapariciones forzadas, osamentas o fosas clandestinas
     * Homicidios y Feminicidios (cometidos por particulares, pandillas o agentes estatales)
     * Uso desproporcionado de la fuerza por agentes de seguridad del Estado (PNC / FAES)
     * Acoso, espionaje o censura a periodistas, medios independientes o defensores de DDHH
     * Violaciones graves al debido proceso legal (juicios masivos, negativa de hábeas corpus)
   - REGLAS ESTRICTAS DE EXCLUSIÓN (NO ES VIOLACIÓN A DERECHOS HUMANOS):
     * Persecución penal legítima y capturas en FLAGRANCIA por delitos comunes (ej. decomiso de drogas/marihuana/cocaína, incautación infraganti de armas ilícitas, detenciones por hurto o estafa común), SALVO que la nota documente torturas comprobadas, ejecuciones, siembra flagrante de pruebas (montaje) o muerte bajo custodia.
     * Si se trata de un delito común o captura en flagrancia sin transgresión a garantías básicas, marca category como "NO_VINCULADO".

2. Si clasifica como hecho relevante para DDHH:
   - Extrae el departamento y el municipio (de entre los 44 municipios de El Salvador).
   - Identifica a las víctimas (nombres, edades, género y si pertenecen a grupos vulnerables).
   - Identifica a los presuntos perpetradores (agentes estatales PNC/FAES/Centros Penales vs particulares/pandillas).
   - Evalúa el nivel de certeza (Confirmado, Testimonial, En Investigación).
   - Determina si ocurrió en el marco del Régimen de Excepción o en un penal.
"""


class HumanRightsExtractor:
    """Motor de extracción estructurada con IA generativa y fallback determinista."""

    def __init__(self, api_key: Optional[str] = None, model_name: Optional[str] = None):
        self.api_key = api_key if api_key is not None else os.environ.get("GEMINI_API_KEY")
        self.model_name = model_name or os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
        self.client = None

        if self.api_key:
            try:
                from google import genai
                self.client = genai.Client(api_key=self.api_key)
                logger.info(f"Cliente Gemini inicializado con modelo {self.model_name}")
            except Exception as e:
                logger.warning(f"No se pudo inicializar cliente Google GenAI: {e}. Usando fallback.")

    def extract_from_evidence(self, evidence: SourceEvidence, full_text: str) -> Optional[HumanRightsIncident]:
        """
        Analiza el texto de una noticia y genera un HumanRightsIncident estructurado.
        Si no constituye violación a DDHH, retorna None.
        """
        if self.client:
            try:
                incident = self._extract_with_gemini(evidence, full_text)
                if incident and incident.category == ViolationCategory.NO_VINCULADO:
                    return None
                if incident:
                    incident.certainty = CertaintyLevel.EN_INVESTIGACION
                    return incident
            except Exception as e:
                logger.warning(f"Fallo en inferencia de Gemini ({e}). Utilizando extractor heurístico de respaldo.")

        # Fallback determinista offline / sin API key
        return self._heuristic_fallback_extraction(evidence, full_text)

    def _extract_with_gemini(self, evidence: SourceEvidence, full_text: str) -> Optional[HumanRightsIncident]:
        """Inferencia estructurada usando la API de Google GenAI."""
        import time

        prompt = f"""
        Artículo:
        Titular: {evidence.title}
        Medio: {evidence.outlet_name}
        Fecha: {evidence.published_date}
        Texto completo:
        {full_text[:4000]}
        """

        last_error = None
        for attempt in range(2):
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=[SYSTEM_PROMPT_FORENSE, prompt],
                    config={
                        "response_mime_type": "application/json",
                        "response_schema": HumanRightsIncident,
                    },
                )

                data = json.loads(response.text)
                incident = HumanRightsIncident.model_validate(data)
                incident.sources = [evidence]
                incident.certainty = CertaintyLevel.EN_INVESTIGACION
                return incident
            except Exception as e:
                last_error = e
                err_msg = str(e).lower()
                if ("503" in err_msg or "429" in err_msg or "demand" in err_msg) and attempt < 1:
                    time.sleep(1.0)
                    continue
                raise last_error

    def _heuristic_fallback_extraction(self, evidence: SourceEvidence, full_text: str) -> Optional[HumanRightsIncident]:
        """
        Extractor heurístico de respaldo basado en análisis léxico-semántico y reglas del paper IEEE.
        Permite que el monitor funcione offline, en pruebas de CI o sin cuotas de API.
        """
        combined_text = f"{evidence.title} {full_text}".lower()

        # Validación estricta de jurisdicción o nexo con El Salvador
        is_sv_related = any(k in combined_text for k in [
            "el salvador", "salvadoreñ", "pnc", "faes", "cecot", "mariona", "izalco",
            "apanteos", "zacatraz", "fgr", "apes", "régimen de excepción", "regimen de excepcion"
        ]) or any(dept.value.lower() in combined_text for dept in DepartmentEnum if dept != DepartmentEnum.DESCONOCIDO)

        # Descarte de hallazgos arqueológicos o fósiles prehispánicos (falsos positivos por palabra "osamenta")
        is_archaeological = any(k in combined_text for k in [
            "prehispánic", "prehispanic", "arqueológic", "arqueolog", "3,000 años",
            "3.000 años", "2,500 años", "año 850 a. de c.", "cerámica prehispánica", "vasija"
        ])
        if is_archaeological:
            return None

        is_purely_foreign = (
            any(k in evidence.title.lower() for k in [
                "ice detiene", "ice arresta", "en ee. uu.", "en ee.uu.", "en estados unidos",
                "en nicaragua", "en honduras", "en guatemala", "en méxico", "en mexico",
                "en virginia", "en massachusetts", "en argentina", "en guanajuato", "villagrán",
                "long island", "great falls", "el sol del bajío"
            ]) and not any(k in evidence.title.lower() for k in ["cecot", "deportad"])
        )

        # Descarte de operativos deportivos, temas virales, económicos o contrabando sin relación con DDHH
        is_irrelevant_news = any(k in evidence.title.lower() for k in [
            "boletos previo al partido", "encuentro selecta", "estadio cuscatlán en operativo",
            "dinero no les alcanza para comer", "contrabando de cigarrillos", "ordenan liberar a tiktoker",
            "4 muertes en 7 meses: la indignación por los crímenes contra mujeres en la universidad de guanajuato"
        ])

        if not is_sv_related or is_purely_foreign or is_irrelevant_news:
            # Descartar noticias internacionales o sin nexo con El Salvador
            return None

        # Detección de flagrancia en delitos comunes (drogas, armas ilícitas infraganti, delitos patrimoniales)
        is_flagrancia_comun = any(k in combined_text for k in [
            "porciones de marihuana", "porciones de droga", "decomisaron marihuana",
            "decomisó marihuana", "decomiso de droga", "decomisaron droga",
            "decomisó droga", "incautaron droga", "incautación de droga", "marihuana",
            "cocaína", "crack", "metanfetamina", "narcomenudeo", "tráfico ilícito",
            "decomisó dinero", "dinero en efectivo", "posesión y tenencia",
            "hurto", "estafa", "accidente de tránsito", "ebriedad", "conducción peligrosa"
        ])

        has_alegato_abuso = any(k in combined_text for k in [
            "detención arbitraria", "captura arbitraria", "captura ilegal", "sin orden",
            "perfilamiento", "inocente", "plantaron", "sembraron", "montaje", "tortura",
            "golpiza", "golpes", "hábeas corpus", "habeas corpus", "desaparición",
            "incomunicación", "incomunicado", "falleció", "muerto", "denuncian familiares",
            "organización de ddhh", "cristosal", "socorro jurídico", "idhuca", "tutela legal", "apes"
        ])

        # Detección de condenas judiciales a estructuras criminales (no son nuevos homicidios ni violaciones estatales)
        is_gang_conviction = any(k in evidence.title.lower() for k in [
            "tribunal salvadoreño condena a", "condena a 53 integrantes", "24 integrantes de la ms-13 fueron condenados",
            "condenados a hasta", "penas de entre 20 y 40 años"
        ]) and not any(k in evidence.title.lower() for k in ["feminicidio", "ambientalista", "jesuita", "periodista"])
        if is_gang_conviction:
            return None

        # Detección de temas
        is_penal = any(k in combined_text for k in ["penal", "mariona", "cecot", "izalco", "apanteos", "bartolina", "custodio", "recluso", "preso", "cárcel", "prisión", "zacatraz", "gotera"])
        is_muerte_custodia = is_penal and any(k in combined_text for k in ["muerto", "murió", "falleció", "cadáver", "asfixia", "golpiza", "cuerpo sin vida", "autopsia", "asfixiado", "fallecimiento"])
        is_incomunicacion = is_penal and any(k in combined_text for k in ["incomunicación", "incomunicado", "sin visita", "aislamiento", "sin noticias"])
        is_homicidio = any(k in combined_text for k in ["homicidio", "asesinato", "feminicidio", "fosa clandestina", "osamenta", "desaparición", "degollado", "ultimado"])
        is_libertad_prensa = any(k in combined_text for k in ["periodista", "acoso a la prensa", "espionaje", "pegasus", "censura", "defensor", "líder comunal", "líder", "apes", "reportero"])

        # Una captura solo es arbitraria si hay mención explícita de arbitrariedad, o contexto de régimen/redada SIN flagrancia de delito común
        is_detencion_explicitamente_arbitraria = any(k in combined_text for k in [
            "detención arbitraria", "captura ilegal", "captura injusta", "sin orden",
            "inocente detenido", "hábeas corpus", "habeas corpus", "juicio colectivo",
            "captura por perfilamiento", "denuncian captura", "denuncian detención"
        ])
        is_redada_o_captura_regimen = any(k in combined_text for k in ["régimen de excepción", "regimen de excepcion", "redada"]) and any(k in combined_text for k in ["captura", "detenido", "aprehensión", "arrestado"])

        if is_flagrancia_comun and not has_alegato_abuso:
            is_detencion_arbitraria = False
        else:
            is_detencion_arbitraria = is_detencion_explicitamente_arbitraria or (is_redada_o_captura_regimen and not is_flagrancia_comun)

        if not (is_muerte_custodia or is_incomunicacion or is_detencion_arbitraria or is_homicidio or is_libertad_prensa):
            # No se detectaron indicadores de DDHH
            return None

        # Tipificación
        if is_muerte_custodia:
            category = ViolationCategory.VIDA_INTEGRIDAD
            violation_type = SpecificViolationType.MUERTE_EN_CUSTODIA_PENAL
            severity = SeverityLevel.CRITICA
        elif is_homicidio:
            category = ViolationCategory.VIDA_INTEGRIDAD
            violation_type = SpecificViolationType.HOMICIDIO_FEMINICIDIO
            severity = SeverityLevel.CRITICA
        elif is_incomunicacion:
            category = ViolationCategory.LIBERTAD_DEBIDO_PROCESO
            violation_type = SpecificViolationType.INCOMUNICACION
            severity = SeverityLevel.ALTA
        elif is_detencion_arbitraria:
            category = ViolationCategory.LIBERTAD_DEBIDO_PROCESO
            violation_type = SpecificViolationType.DETENCION_ARBITRARIA
            severity = SeverityLevel.ALTA
        else:
            category = ViolationCategory.LIBERTADES_DEMOCRATICAS
            violation_type = SpecificViolationType.ACOSO_ESPIONAJE_PRENSA
            severity = SeverityLevel.MEDIA

        # Extracción de Centro Penal si aplica
        prison_name = None
        detected_dept = None
        municipality = None

        if "cecot" in combined_text:
            prison_name = "CECOT"
            detected_dept = DepartmentEnum.SAN_VICENTE
            municipality = "Tecoluca"
        elif "izalco" in combined_text:
            prison_name = "Penal de Izalco"
            detected_dept = DepartmentEnum.SONSONATE
            municipality = "Izalco"
        elif "apanteos" in combined_text:
            prison_name = "Penal de Apanteos"
            detected_dept = DepartmentEnum.SANTA_ANA
            municipality = "Santa Ana"
        elif "mariona" in combined_text or "la esperanza" in combined_text:
            prison_name = "Penal La Esperanza (Mariona)"
            detected_dept = DepartmentEnum.SAN_SALVADOR
            municipality = "Ayutuxtepeque"
        elif "zacatraz" in combined_text or "zacatecoluca" in combined_text and is_penal:
            prison_name = "Penal de Máxima Seguridad de Zacatecoluca"
            detected_dept = DepartmentEnum.LA_PAZ
            municipality = "Zacatecoluca"
        elif "ilopango" in combined_text or "mujeres" in combined_text and is_penal:
            prison_name = "Centro Preventivo y de Cumplimiento de Ilopango (Cárcel de Mujeres)"
            detected_dept = DepartmentEnum.SAN_SALVADOR
            municipality = "Ilopango"
        elif "gotera" in combined_text and is_penal:
            prison_name = "Penal de San Francisco Gotera"
            detected_dept = DepartmentEnum.MORAZAN
            municipality = "San Francisco Gotera"

        # Mapeo de municipios a departamentos
        muni_dept_map = {
            "soyapango": (DepartmentEnum.SAN_SALVADOR, "Soyapango"),
            "ilopango": (DepartmentEnum.SAN_SALVADOR, "Ilopango"),
            "apopa": (DepartmentEnum.SAN_SALVADOR, "Apopa"),
            "mejicanos": (DepartmentEnum.SAN_SALVADOR, "Mejicanos"),
            "ciudad delgado": (DepartmentEnum.SAN_SALVADOR, "Ciudad Delgado"),
            "san marcos": (DepartmentEnum.SAN_SALVADOR, "San Marcos"),
            "san salvador": (DepartmentEnum.SAN_SALVADOR, "San Salvador"),
            "santa tecla": (DepartmentEnum.LA_LIBERTAD, "Santa Tecla"),
            "antiguo cuscatlán": (DepartmentEnum.LA_LIBERTAD, "Antiguo Cuscatlán"),
            "colón": (DepartmentEnum.LA_LIBERTAD, "Colón"),
            "lourdes": (DepartmentEnum.LA_LIBERTAD, "Lourdes Colón"),
            "quezaltepeque": (DepartmentEnum.LA_LIBERTAD, "Quezaltepeque"),
            "san juan opico": (DepartmentEnum.LA_LIBERTAD, "San Juan Opico"),
            "santa ana": (DepartmentEnum.SANTA_ANA, "Santa Ana"),
            "chalchuapa": (DepartmentEnum.SANTA_ANA, "Chalchuapa"),
            "metapán": (DepartmentEnum.SANTA_ANA, "Metapán"),
            "sonsonate": (DepartmentEnum.SONSONATE, "Sonsonate"),
            "acajutla": (DepartmentEnum.SONSONATE, "Acajutla"),
            "nahuizalco": (DepartmentEnum.SONSONATE, "Nahuizalco"),
            "ahuachapán": (DepartmentEnum.AHUACHAPAN, "Ahuachapán"),
            "atiquizaya": (DepartmentEnum.AHUACHAPAN, "Atiquizaya"),
            "chalatenango": (DepartmentEnum.CHALATENANGO, "Chalatenango"),
            "la palma": (DepartmentEnum.CHALATENANGO, "La Palma"),
            "cojutepeque": (DepartmentEnum.CUSCATLAN, "Cojutepeque"),
            "san vicente": (DepartmentEnum.SAN_VICENTE, "San Vicente"),
            "tecoluca": (DepartmentEnum.SAN_VICENTE, "Tecoluca"),
            "zacatecoluca": (DepartmentEnum.LA_PAZ, "Zacatecoluca"),
            "olocuilta": (DepartmentEnum.LA_PAZ, "Olocuilta"),
            "sensuntepeque": (DepartmentEnum.CABANAS, "Sensuntepeque"),
            "ilobasco": (DepartmentEnum.CABANAS, "Ilobasco"),
            "santa marta": (DepartmentEnum.CABANAS, "Victoria / Santa Marta"),
            "usulután": (DepartmentEnum.USULUTAN, "Usulután"),
            "jiquilisco": (DepartmentEnum.USULUTAN, "Jiquilisco"),
            "puerto el triunfo": (DepartmentEnum.USULUTAN, "Puerto El Triunfo"),
            "san miguel": (DepartmentEnum.SAN_MIGUEL, "San Miguel"),
            "ciudad barrios": (DepartmentEnum.SAN_MIGUEL, "Ciudad Barrios"),
            "san francisco gotera": (DepartmentEnum.MORAZAN, "San Francisco Gotera"),
            "osicala": (DepartmentEnum.MORAZAN, "Osicala"),
            "perquín": (DepartmentEnum.MORAZAN, "Perquín"),
            "la unión": (DepartmentEnum.LA_UNION, "La Unión"),
            "conchagua": (DepartmentEnum.LA_UNION, "Conchagua")
        }

        if not detected_dept:
            for muni_key, (dept_val, muni_name) in muni_dept_map.items():
                if f" {muni_key} " in f" {combined_text} ":
                    detected_dept = dept_val
                    municipality = muni_name
                    break

        if not detected_dept:
            for dept in DepartmentEnum:
                if dept != DepartmentEnum.DESCONOCIDO and dept.value.lower() in combined_text:
                    detected_dept = dept
                    break

        if not detected_dept or detected_dept == DepartmentEnum.DESCONOCIDO:
            detected_dept = DepartmentEnum.DESCONOCIDO
            municipality = "No determinado"
        else:
            municipality = normalize_municipality(municipality, detected_dept)

        # Extracción de fecha del texto si existe
        incident_date = None
        month_map = {
            "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
            "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12
        }
        date_pattern = re.search(r"\b(\d{1,2})\s+de\s+(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)\s+de\s+(2024|2025|2026)\b", combined_text)
        if date_pattern:
            try:
                day = int(date_pattern.group(1))
                mon = month_map[date_pattern.group(2)]
                yr = int(date_pattern.group(3))
                incident_date = date(yr, mon, day)
            except Exception:
                pass

        # Nivel de certeza
        is_confirmed = any(k in combined_text for k in [
            "medicina legal", "iml", "autopsia", "peritaje", "fiscalía confirmó",
            "carta de libertad", "juez ordenó", "informe forense", "cristosal",
            "socorro jurídico", "oudh", "idhuca", "certificado de defunción"
        ])
        certainty = CertaintyLevel.EN_INVESTIGACION

        # Extracción de presunto perpetrador
        perps = []
        if "pnc" in combined_text or "policía" in combined_text:
            perps.append(Perpetrator(actor_type=ActorType.ESTATAL_PNC))
        elif "faes" in combined_text or "ejército" in combined_text or "soldado" in combined_text:
            perps.append(Perpetrator(actor_type=ActorType.ESTATAL_FAES))
        elif is_penal:
            perps.append(Perpetrator(actor_type=ActorType.ESTATAL_PENITENCIARIO))
        else:
            perps.append(Perpetrator(actor_type=ActorType.DESCONOCIDO))

        coords = get_default_coordinates(detected_dept)

        return HumanRightsIncident(
            title=evidence.title,
            summary=full_text[:400].strip() + ("..." if len(full_text) > 400 else ""),
            category=category,
            violation_type=violation_type,
            severity=severity,
            certainty=certainty,
            incident_date=incident_date,
            department=detected_dept,
            municipality=municipality,
            coordinates=coords,
            sources=[evidence],
            perpetrators=[],
            victims=[],
            under_state_of_exception=any(t in combined_text for t in ("régimen de excepción", "regimen de excepcion")),
            penitentiary_related=is_penal,
            prison_name=prison_name
        )
