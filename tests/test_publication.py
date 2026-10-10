import csv
import hashlib
import io
import json
from pathlib import Path
import pytest
import pyarrow.parquet as pq
from pydantic import ValidationError
from mdh_pipeline.publication import CATALOG, Catalog, export_catalog, load_catalog, csv_safe
from mdh_pipeline.open_data import OpenDataExporter
from mdh_pipeline.archiver import EvidenceArchiver
from mdh_pipeline.deduplicator import IncidentDeduplicator


def test_provenance_and_privacy_boundary():
    catalog = load_catalog()
    assert len(catalog.records) >= 24
    assert {"mdh-ruth-lopez-2025", "mdh-fidel-zavala-2025", "mdh-beatriz-2013"} <= {r.id for r in catalog.records}
    data = catalog.model_dump(mode='json')
    for field, value in [('victims', [{'name': 'private'}]), ('human_reviewed', True), ('geometry', {'type':'Point','coordinates':[0,0]}), ('source_url','javascript:alert(1)')]:
        changed = json.loads(json.dumps(data))
        changed['records'][0][field] = value
        with pytest.raises(ValidationError): Catalog.model_validate(changed)
    changed = json.loads(json.dumps(data))
    changed['records'].append(changed['records'][0])
    with pytest.raises(ValidationError): Catalog.model_validate(changed)


def test_exports_consistent_and_checksums_real(tmp_path):
    manifest = export_catalog(destination=tmp_path)
    rows = json.loads((tmp_path/'catalog.json').read_text())['records']
    csv_rows = list(csv.DictReader(io.StringIO((tmp_path/'records.csv').read_text(encoding='utf-8-sig'))))
    parquet = pq.read_table(tmp_path/'records.parquet').to_pylist()
    geo = json.loads((tmp_path/'records.geojson').read_text())['features']
    assert {r['id'] for r in rows} == {r['id'] for r in csv_rows} == {r['id'] for r in parquet} == {f['properties']['id'] for f in geo}
    assert all(f['geometry'] is None for f in geo)
    assert all(r['human_reviewed'] is False for r in parquet)
    for name, item in manifest['files'].items():
        assert hashlib.sha256((tmp_path/name).read_bytes()).hexdigest() == item['sha256']
    second = tmp_path/'again'
    assert export_catalog(destination=second) == manifest


@pytest.mark.parametrize('value', ['=HYPERLINK("evil")', '+1', '-1', '@SUM(A1)', '  =1', '\t=1', '\r=1'])
def test_csv_formula_injection(value):
    assert csv_safe(value).startswith("'")


def test_legacy_export_cannot_publish(tmp_path):
    for export in [OpenDataExporter.export_csv, OpenDataExporter.export_geojson, OpenDataExporter.export_parquet]:
        with pytest.raises(ValueError): export([], str(tmp_path/'public/data/incidents.csv'))


def test_wayback_never_fabricates_snapshot(monkeypatch):
    from unittest.mock import MagicMock
    import requests
    archiver = EvidenceArchiver()
    monkeypatch.setattr(archiver, 'check_existing_snapshot', lambda _: None)
    response = MagicMock(url='https://web.archive.org/save/https://example.org/article')
    monkeypatch.setattr(archiver.session, 'get', lambda *a, **k: response)
    assert archiver.request_wayback_preservation('https://example.org/article')[0] is None
    def timeout(*args, **kwargs): raise requests.Timeout()
    monkeypatch.setattr(archiver.session, 'get', timeout)
    assert archiver.request_wayback_preservation('https://example.org/article')[0] is None


def test_private_ingestion_is_opt_in_and_does_not_publish(monkeypatch):
    from mdh_pipeline.cron_runner import run_periodic_scan
    monkeypatch.delenv('MDH_ENABLE_INGESTION', raising=False)
    before = CATALOG.read_bytes()
    with pytest.raises(RuntimeError): run_periodic_scan()
    assert CATALOG.read_bytes() == before


def test_case_updates_and_individuals_are_not_double_counted():
    records={r.id:r for r in load_catalog().records}
    for key in ['mdh-alejandro-henriquez-2025','mdh-jose-angel-perez-2025']:
        record=records[key]
        assert 'liberación' in record.summary
        assert record.related_sources[0]['published_date']=='2025-12-17'
    assert records['mdh-beatriz-2013'].date_precision=='year_range'
    assert records['mdh-venezolanos-cecot-2025'].record_type=='aggregate'
    assert records['mdh-musico-custodia-2022'].department is None
    assert all('victims_count' not in r.model_dump() for r in records.values())
