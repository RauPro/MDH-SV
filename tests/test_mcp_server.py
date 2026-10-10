import json
import pytest
from pathlib import Path
from mdh_pipeline.publication import Catalog
from mdh_mcp import server as server_module

@pytest.fixture(autouse=True)
def fixture_catalog(monkeypatch):
    catalog = Catalog.model_validate_json((Path(__file__).parent / "fixtures/catalog.json").read_text())
    monkeypatch.setattr(server_module, "load_catalog", lambda: catalog)

from mdh_mcp.server import (mcp, consultar_incidentes, obtener_expediente_completo,
    obtener_metricas_globales, recurso_taxonomia, prompt_analizar_patron)


def test_tools_search_public_dataset():
    result = consultar_incidentes(departamento='Sonsonate')
    assert result['total'] == 2
    assert all(r['department'] == 'Sonsonate' for r in result['records'])
    assert all(r['human_reviewed'] is False for r in result['records'])
    assert consultar_incidentes(termino_busqueda='AHUACHAPAN')['total'] == 1


def test_pagination_and_unknown_dates():
    result = consultar_incidentes(limite=1)
    assert result['total'] == 4 and result['next_offset'] == 1
    assert consultar_incidentes(fecha_inicio='2022-01-01')['total'] == 3
    assert consultar_incidentes(tipo='aggregate', fecha_inicio='2021-06-01', fecha_fin='2021-07-01')['total'] == 1
    with pytest.raises(ValueError): consultar_incidentes(limite=-1)
    with pytest.raises(ValueError): consultar_incidentes(fecha_inicio='2025-01-01', fecha_fin='2020-01-01')


def test_dossier_cannot_read_private_candidate():
    assert 'error' in obtener_expediente_completo('test-mcp-uuid-1234')
    result = obtener_expediente_completo('mdh-2022-001')
    assert result['geojson']['geometry'] is None
    assert 'source_locator' in result['expediente']
    assert 'victims' not in result['expediente']


def test_metrics_separate_reports_from_people():
    metrics = obtener_metricas_globales()
    assert metrics['por_tipo'] == {'publication': 4, 'incident': 4, 'aggregate': 1}
    assert metrics['documentos_fuente'] == 5
    assert 'victimas' not in metrics
    assert 'categorias' in json.loads(recurso_taxonomia())
    assert 'No actúes como la CIDH' in prompt_analizar_patron('Sonsonate')
