"""
Motor de Exportación a Open Data Lake ($0 de costo en almacenamiento).
Genera datasets abiertos en formatos GeoJSON, Apache Parquet y CSV para la comunidad
internacional, periodistas, investigadores y organizaciones de DDHH.
"""

import csv
import json
import logging
import os
from typing import List, Any
import pyarrow as pa
import pyarrow.parquet as pq

from mdh_schema import HumanRightsIncident
from pathlib import Path

def _private_export_only(output_path):
    path = Path(output_path).resolve()
    if "public" in path.parts or "src" in path.parts:
        raise ValueError("Private candidates cannot be exported into public or frontend directories")


logger = logging.getLogger(__name__)

def _val(item: Any) -> str:
    if hasattr(item, "value"):
        return str(item.value)
    return str(item) if item is not None else ""


class OpenDataExporter:
    """Exportador masivo de datos a estándares abiertos."""

    @staticmethod
    def export_geojson(incidents: List[HumanRightsIncident], output_path: str):
        """Exporta los incidentes como un FeatureCollection de GeoJSON estándar (RFC 7946)."""
        _private_export_only(output_path)
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        feature_collection = {
            "type": "FeatureCollection",
            "name": "Monitor de Derechos Humanos de El Salvador (MDH-SV)",
            "features": [inc.to_geojson_feature() for inc in incidents]
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(feature_collection, f, ensure_ascii=False, indent=2)
        logger.info(f"Exportado GeoJSON con {len(incidents)} elementos a {output_path}")

    @staticmethod
    def export_csv(incidents: List[HumanRightsIncident], output_path: str):
        """Exporta una matriz tabular en CSV estándar para Excel y Google Sheets."""
        _private_export_only(output_path)
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        fieldnames = [
            "id",
            "fecha_incidente",
            "categoria",
            "tipo_violacion",
            "severidad",
            "nivel_certeza",
            "titulo",
            "resumen",
            "departamento",
            "municipio",
            "ubicacion_especifica",
            "latitud",
            "longitud",
            "total_victimas",
            "total_fuentes",
            "regimen_excepcion",
            "vinculado_penal",
            "nombre_penal",
            "enlaces_fuentes",
            "enlaces_wayback",
            "hashes_sha256"
        ]

        with open(output_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for inc in incidents:
                sources_urls = " | ".join(s.original_url for s in inc.sources if s.original_url)
                archive_urls = " | ".join(
                    s.archive_url
                    for s in inc.sources
                    if s.archive_url and s.archive_url not in ("https://web.archive.org", "https://web.archive.org/") and "*" not in s.archive_url
                )
                hashes = " | ".join(s.source_sha256 for s in inc.sources if s.source_sha256)

                writer.writerow({
                    "id": inc.id,
                    "fecha_incidente": inc.incident_date.isoformat() if inc.incident_date else "",
                    "categoria": _val(inc.category),
                    "tipo_violacion": _val(inc.violation_type),
                    "severidad": _val(inc.severity),
                    "nivel_certeza": _val(inc.certainty),
                    "titulo": inc.title,
                    "resumen": inc.summary,
                    "departamento": _val(inc.department),
                    "municipio": inc.municipality,
                    "ubicacion_especifica": inc.specific_location or "",
                    "latitud": inc.coordinates.latitude,
                    "longitud": inc.coordinates.longitude,
                    "total_victimas": inc.victims_count if inc.victims_count is not None else len(inc.victims),
                    "total_fuentes": len(inc.sources),
                    "regimen_excepcion": "SI" if inc.under_state_of_exception else "NO",
                    "vinculado_penal": "SI" if inc.penitentiary_related else "NO",
                    "nombre_penal": inc.prison_name or "",
                    "enlaces_fuentes": sources_urls,
                    "enlaces_wayback": archive_urls,
                    "hashes_sha256": hashes
                })
        logger.info(f"Exportado CSV con {len(incidents)} elementos a {output_path}")

    @staticmethod
    def export_parquet(incidents: List[HumanRightsIncident], output_path: str):
        """Exporta a Apache Parquet columnar de alto rendimiento."""
        _private_export_only(output_path)
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        data = {
            "id": [inc.id for inc in incidents],
            "incident_date": [inc.incident_date.isoformat() if inc.incident_date else None for inc in incidents],
            "category": [_val(inc.category) for inc in incidents],
            "violation_type": [_val(inc.violation_type) for inc in incidents],
            "severity": [_val(inc.severity) for inc in incidents],
            "certainty": [_val(inc.certainty) for inc in incidents],
            "title": [inc.title for inc in incidents],
            "summary": [inc.summary for inc in incidents],
            "department": [_val(inc.department) for inc in incidents],
            "municipality": [inc.municipality for inc in incidents],
            "latitude": [inc.coordinates.latitude for inc in incidents],
            "longitude": [inc.coordinates.longitude for inc in incidents],
            "victims_count": [inc.victims_count if inc.victims_count is not None else len(inc.victims) for inc in incidents],
            "sources_count": [len(inc.sources) for inc in incidents],
            "under_state_of_exception": [inc.under_state_of_exception for inc in incidents],
            "penitentiary_related": [inc.penitentiary_related for inc in incidents],
            "prison_name": [inc.prison_name for inc in incidents]
        }
        table = pa.Table.from_pydict(data)
        pq.write_table(table, output_path, compression="snappy")
        logger.info(f"Exportado Apache Parquet con {len(incidents)} elementos a {output_path}")
