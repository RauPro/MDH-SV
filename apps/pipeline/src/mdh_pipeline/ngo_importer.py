"""
Importador de Expedientes Forenses de ONGs (Cristosal, Socorro Jurídico, IDHUCA).
Valida matrices estructuradas de casos ocurridos entre 2024 y 2026, computa hashes
criptográficos SHA-256 e inyecta los expedientes en el repositorio inmutable.
"""

import json
import logging
from datetime import date, datetime
from typing import List, Optional
import os

from mdh_schema import (
    HumanRightsIncident,
    GeoCoordinates,
    Victim,
    Perpetrator,
    SourceEvidence,
    DepartmentEnum,
    ViolationCategory,
    MacroCategoryEnum,
    SpecificViolationType,
    SpecificViolationTypeEnum,
    SeverityLevel,
    CertaintyLevel,
    WeaponType,
    ActorType,
    VulnerableGroupEnum,
)
from mdh_pipeline.storage import IncidentRepository

logger = logging.getLogger("ngo_importer")


class NGOReportImporter:
    """Importador y validador de reportes de derechos humanos de la sociedad civil."""

    def __init__(self, db_path: str = "./data/mdh_database.sqlite"):
        self.repo = IncidentRepository(db_path=db_path)

    def import_from_json(self, file_path: str) -> List[HumanRightsIncident]:
        """Carga y procesa expedientes desde un archivo JSON estructurado de ONGs."""
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Archivo de reportes no encontrado: {file_path}")

        with open(file_path, "r", encoding="utf-8") as f:
            raw_cases = json.load(f)

        logger.info(f"Procesando {len(raw_cases)} expedientes de ONGs desde {file_path}...")
        ingested: List[HumanRightsIncident] = []

        for item in raw_cases:
            try:
                incident = self._build_incident(item)
                self.repo.save_incident(incident)
                ingested.append(incident)
                logger.info(f"✔ Expediente ONG registrado: {incident.id} - {incident.title[:50]}...")
            except Exception as e:
                logger.error(f"Error procesando caso {item.get('id')}: {e}")

        logger.info(f"Importación completada. {len(ingested)}/{len(raw_cases)} casos registrados.")
        return ingested

    def _build_incident(self, data: dict) -> HumanRightsIncident:
        """Transforma y valida un diccionario en un HumanRightsIncident riguroso."""
        # Fechas
        inc_date = None
        if data.get("incident_date"):
            inc_date = date.fromisoformat(data["incident_date"])

        # Coordenadas
        coords = GeoCoordinates(
            latitude=float(data["latitude"]),
            longitude=float(data["longitude"])
        )

        # Víctimas
        victims: List[Victim] = []
        for v in data.get("victims", []):
            vuln = None
            if v.get("vulnerable_group"):
                for vg in VulnerableGroupEnum:
                    if vg.value.lower() == v["vulnerable_group"].lower():
                        vuln = vg
                        break

            victims.append(Victim(
                name=v.get("name", "Anónimo"),
                is_anonymized=v.get("is_anonymized", v.get("is_anonymous", False)),
                age=v.get("age"),
                gender=v.get("gender", "No determinado"),
                occupation=v.get("occupation"),
                vulnerable_group=vuln
            ))

        # Agresores
        perpetrators: List[Perpetrator] = []
        for p in data.get("perpetrators", []):
            act_val = p.get("institutional_name", "") + " " + p.get("actor_type", "")
            act_lower = act_val.lower()
            if "pnc" in act_lower or "policía" in act_lower or "policia" in act_lower:
                actor = ActorType.ESTATAL_PNC
            elif "faes" in act_lower or "militar" in act_lower or "marina" in act_lower or "armada" in act_lower:
                actor = ActorType.ESTATAL_FAES
            elif "penal" in act_lower or "dgcp" in act_lower or "custodio" in act_lower:
                actor = ActorType.ESTATAL_PENITENCIARIO
            elif "judicial" in act_lower or "fiscal" in act_lower or "tribunal" in act_lower:
                actor = ActorType.ESTATAL_JUDICIAL_FISCAL
            else:
                actor = ActorType.ESTATAL_OTRO

            perpetrators.append(Perpetrator(
                actor_type=actor,
                institutional_name=p.get("institutional_name"),
                unit_or_rank=p.get("unit_or_rank")
            ))

        # Evidencias y Fuentes
        sources: List[SourceEvidence] = []
        for s in data.get("sources", []):
            content_to_hash = s.get("extracted_quote") or s.get("title") or data.get("summary", "")
            sha256_hash = SourceEvidence.calculate_hash(content_to_hash)

            sources.append(SourceEvidence(
                outlet_name=s.get("outlet_name", "Organización de DDHH"),
                original_url=s.get("original_url", "https://cristosal.org"),
                title=s.get("title", data["title"]),
                published_date=inc_date or date.today(),
                archive_url=s.get("archive_url"),
                source_sha256=sha256_hash,
                extracted_quote=s.get("extracted_quote")
            ))

        # Enums de clasificación
        dept = None
        for d in DepartmentEnum:
            if d.value.lower() == data["department"].lower():
                dept = d
                break
        if not dept:
            dept = DepartmentEnum.SAN_SALVADOR

        sev = SeverityLevel.ALTA
        for s in SeverityLevel:
            if s.value.lower() == data.get("severity", "").lower():
                sev = s
                break

        cert = CertaintyLevel.CONFIRMADO
        for c in CertaintyLevel:
            if c.value.lower() == data.get("certainty", "").lower():
                cert = c
                break

        weapon = WeaponType.NO_APLICA
        for w in WeaponType:
            if w.value.lower() == data.get("weapon_type", "").lower():
                weapon = w
                break

        macro = MacroCategoryEnum.RAMA_1_VIDA
        for m in MacroCategoryEnum:
            if m.value.lower() == data.get("macro_category", "").lower():
                macro = m
                break

        return HumanRightsIncident(
            id=data.get("id"),
            incident_date=inc_date,
            macro_category=macro,
            category=data.get("category", ViolationCategory.VIDA_INTEGRIDAD),
            violation_type=data.get("violation_type", SpecificViolationType.MUERTE_EN_CUSTODIA_PENAL),
            severity=sev,
            certainty=cert,
            title=data["title"],
            summary=data["summary"],
            department=dept,
            municipality=data.get("municipality", "San Salvador Centro"),
            specific_location=data.get("specific_location"),
            coordinates=coords,
            weapon_type=weapon,
            violatory_facts=data.get("violatory_facts", ""),
            fact_hypothesis=data.get("fact_hypothesis"),
            thematic_tags=data.get("thematic_tags", []),
            victims=victims,
            victims_count=data.get("victims_count"),
            perpetrators=perpetrators,
            sources=sources,
            under_state_of_exception=data.get("under_state_of_exception", True),
            penitentiary_related=data.get("penitentiary_related", True),
            prison_name=data.get("prison_name")
        )
