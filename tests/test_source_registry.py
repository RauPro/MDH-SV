"""One registration drives both runtimes without granting new capabilities."""
from pathlib import Path

import pytest
from contract_fixtures import load_cases
from mdh_pipeline.publishers import (
    NEWS_HOSTS,
    OFFICIAL_STATISTICS_DISCOVERY_HOSTS,
    OFFICIAL_STATISTICS_HOSTS,
    PRIMARY_SOURCE_HOSTS,
    REGISTRY_PATH,
    SOURCES,
    SourceRegistryError,
    load_registry,
    news_sources,
    registered_feeds,
    validate_registry,
)

CASES = load_cases(Path(__file__).parent / 'fixtures/source-registry-contract.json')


@pytest.mark.parametrize('name,valid,document', CASES, ids=[case[0] for case in CASES])
def test_shared_source_registry_contract(name, valid, document):
    if valid:
        assert validate_registry(document)['schema_version'] == 1
    else:
        with pytest.raises(SourceRegistryError, match='^invalid_source_registry$'):
            validate_registry(document)


def test_existing_processing_capabilities_and_exact_hosts_are_preserved():
    assert len(SOURCES) == 7 and len(NEWS_HOSTS) == 14 and len(PRIMARY_SOURCE_HOSTS) == 21
    assert [row['name'] for row in registered_feeds('private_queue')] == [
        'Diario Co Latino', 'GatoEncerrado', 'Revista Factum']
    assert [row['name'] for row in registered_feeds('legacy_scraper')] == [
        'Diario Co Latino', 'GatoEncerrado', 'El Faro']
    assert OFFICIAL_STATISTICS_DISCOVERY_HOSTS - OFFICIAL_STATISTICS_HOSTS == {
        'transparencia.pnc.gob.sv'}
    assert not NEWS_HOSTS.intersection(OFFICIAL_STATISTICS_DISCOVERY_HOSTS)
    assert not PRIMARY_SOURCE_HOSTS.intersection(OFFICIAL_STATISTICS_DISCOVERY_HOSTS)
    restricted = next(source for source in SOURCES if source['id'] == 'la-prensa-grafica')
    assert restricted['access'] == 'restricted' and restricted['archive_urls'] == []
    assert 'evil.elsalvador.com' not in NEWS_HOSTS
    assert 'child.www.hrw.org' not in PRIMARY_SOURCE_HOSTS
    with pytest.raises(SourceRegistryError):
        registered_feeds('public_writer')


def test_source_file_is_code_relative_and_validation_precedes_projection(tmp_path, monkeypatch):
    expected = load_registry()
    (tmp_path / 'source-registry.json').write_text('{}')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('MDH_SOURCE_REGISTRY', str(tmp_path / 'source-registry.json'))
    assert load_registry() == expected
    returned = load_registry()
    returned['publishers'][0]['hosts'].append('untrusted.example.org')
    assert load_registry() == expected and 'untrusted.example.org' not in NEWS_HOSTS
    link = tmp_path / 'linked.json'
    link.symlink_to(REGISTRY_PATH)
    with pytest.raises(SourceRegistryError):
        load_registry(link)


@pytest.mark.parametrize('raw', [b'\xff', b'{}', b' ' * 262_145,
    b'{"schema_version":1,"schema_version":1}', b'{"value":NaN}'])
def test_loader_rejects_malformed_duplicate_or_oversized_input_without_echoing_it(tmp_path, raw):
    path = tmp_path / 'registry.json'
    path.write_bytes(raw)
    with pytest.raises(SourceRegistryError, match='^invalid_source_registry$'):
        load_registry(path)


def test_single_synthetic_registration_reaches_collection_and_history_without_code_changes():
    from datetime import date

    from mdh_pipeline.history import reference_from_post, validate_reference
    from mdh_pipeline.review_queue import candidate_from_entry
    registry = load_registry()
    source = {'id': 'synthetic-outlet', 'name': 'Synthetic Outlet', 'kind': 'wordpress',
              'endpoint': 'https://synthetic.example.org/wp-json/wp/v2/posts',
              'hosts': ['synthetic.example.org'],
              'feeds': [{'url': 'https://synthetic.example.org/feed/', 'consumers': ['private_queue']}]}
    registry['publishers'].append(source)
    projected = next(row for row in news_sources(registry) if row['id'] == source['id'])
    feed = next(row for row in registered_feeds('private_queue', registry) if row['name'] == source['name'])
    url = 'https://synthetic.example.org/detenciones/'
    candidate = candidate_from_entry({'link': url, 'title': 'Denuncian detenciones arbitrarias'}, feed)
    reference = reference_from_post({'id': 1, 'date': '2025-03-26T00:00:00', 'link': url,
        'title': {'rendered': 'Denuncian detenciones arbitrarias'}}, projected,
        date(2025, 3, 1), date(2025, 3, 31), '2026-10-10T00:00:00Z')
    validate_reference(reference, projected)
    assert candidate['publisher'] == reference['publisher'] == source['name']
    assert candidate['source_url'] == reference['source_url'] == url
    assert reference['review_status'] == reference['jurisdiction_status'] == 'not_reviewed'
    assert all(feed['name'] != source['name'] for feed in registered_feeds('legacy_scraper', registry))
    assert all(row['id'] != source['id'] for row in SOURCES)
    assert 'synthetic.example.org' not in NEWS_HOSTS
