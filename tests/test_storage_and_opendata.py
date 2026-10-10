"""
Pruebas automatizadas para la persistencia y exportación a Open Data (Fase 4).
"""

from datetime import date
import os
import tempfile
import duckdb
import pytest

from mdh_pipeline.open_data import OpenDataExporter
from mdh_pipeline.storage import IncidentRepository
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


@pytest.fixture
def sample_incidents():
    """Genera una lista de incidentes de prueba para validar persistencia y analítica."""
    inc1 = HumanRightsIncident(
        title="Detención arbitraria de líder comunitario en Sonsonate",
        summary="Persona detenida sin orden judicial en Sonsonate Centro.",
        category=ViolationCategory.LIBERTAD_DEBIDO_PROCESO,
        violation_type=SpecificViolationType.DETENCION_ARBITRARIA,
        severity=SeverityLevel.ALTA,
        certainty=CertaintyLevel.CONFIRMADO,
        incident_date=date(2024, 2, 1),
        department=DepartmentEnum.SONSONATE,
        municipality="Sonsonate Centro",
        coordinates=get_default_coordinates(DepartmentEnum.SONSONATE),
        sources=[
            SourceEvidence(
                outlet_name="El Faro",
                original_url="https://elfaro.net/nota1",
                title="Detención arbitraria",
                source_sha256="hash1",
            )
        ]
    )

    inc2 = HumanRightsIncident(
        title="Fallecimiento en Centro Penal Mariona",
        summary="Fallece privado de libertad en hospital tras salir de Mariona.",
        category=ViolationCategory.VIDA_INTEGRIDAD,
        violation_type=SpecificViolationType.MUERTE_EN_CUSTODIA_PENAL,
        severity=SeverityLevel.CRITICA,
        certainty=CertaintyLevel.EN_INVESTIGACION,
        incident_date=date(2024, 2, 5),
        department=DepartmentEnum.SAN_SALVADOR,
        municipality="San Salvador Norte",
        coordinates=get_default_coordinates(DepartmentEnum.SAN_SALVADOR),
        penitentiary_related=True,
        prison_name="Mariona",
        sources=[
            SourceEvidence(
                outlet_name="La Prensa Gráfica",
                original_url="https://laprensagrafica.com/nota2",
                title="Fallece recluso",
                source_sha256="hash2",
            )
        ]
    )
    return [inc1, inc2]


def test_repository_save_and_filter(sample_incidents):
    """Verifica el guardado y filtrado por departamento, penal y categoría."""
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
        db_path = tmp.name

    try:
        repo = IncidentRepository(db_path=db_path)
        for inc in sample_incidents:
            repo.save_incident(inc)

        assert repo.count_total() == 2

        # Filtro por departamento
        sonsonate_results = repo.query_incidents(department="Sonsonate")
        assert len(sonsonate_results) == 1
        assert sonsonate_results[0].department == DepartmentEnum.SONSONATE

        # Filtro por penales
        penal_results = repo.query_incidents(penitentiary_only=True)
        assert len(penal_results) == 1
        assert penal_results[0].prison_name == "Mariona"

        # Búsqueda de texto
        search_results = repo.query_incidents(search_query="Mariona")
        assert len(search_results) == 1
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_open_data_exports(sample_incidents):
    """Verifica la generación de GeoJSON, Parquet y CSV, y su consulta con DuckDB."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        geojson_path = os.path.join(tmp_dir, "incidents.geojson")
        parquet_path = os.path.join(tmp_dir, "incidents.parquet")
        csv_path = os.path.join(tmp_dir, "incidents.csv")

        OpenDataExporter.export_geojson(sample_incidents, geojson_path)
        OpenDataExporter.export_parquet(sample_incidents, parquet_path)
        OpenDataExporter.export_csv(sample_incidents, csv_path)

        assert os.path.exists(geojson_path)
        assert os.path.exists(parquet_path)
        assert os.path.exists(csv_path)

        # Consulta analítica directa con DuckDB sobre el Parquet
        con = duckdb.connect()
        res = con.execute(f"SELECT count(*) as total, department FROM '{parquet_path}' GROUP BY department ORDER BY total DESC").fetchall()

        assert len(res) == 2
        departments = {r[1] for r in res}
        assert "Sonsonate" in departments
        assert "San Salvador" in departments
