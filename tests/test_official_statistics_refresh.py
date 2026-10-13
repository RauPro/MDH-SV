"""Deterministic source fixtures; no network, Gemini or publication in tests."""
import copy
from datetime import date
import json
from pathlib import Path
import subprocess

import pytest

from mdh_pipeline import official_statistics_refresh as monitor

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / 'data/official-statistics/homicides.json'
CUTOFF = date(2026, 10, 12)
CHECKED = '2026-10-13T04:00:00+00:00'


def baseline():
    return json.loads(BASELINE.read_bytes())


def fgr_html(document=None, *, changes=None):
    document = document or baseline()
    changes = changes or {}
    tables = []
    for period in reversed(document['periods']):
        if period['months'] is None or period['year'] < 2022:
            continue
        year = period['year']
        values = changes.get(year, [m['value'] for m in period['months']])
        known = [v for v in values if v is not None]
        total = sum(known) if known else None
        headers = ['', *(m + '.' for m in monitor.MONTHS), 'TOTAL']
        def row(cells):
            return '<tr>' + ''.join('<td>' + ('' if v is None else str(v)) + '</td>' for v in cells) + '</tr>'
        label = 'Total de Homicidios' if year == 2022 else 'Total Homicidios Intencionales'
        zeroes = [None if v is None else 0 for v in values]
        table = row(headers)
        table += row(['Delincuencia General', *values, total])
        table += row(['Intolerancia Social', *zeroes, 0 if known else None])
        table += row(['Intolerancia Familiar', *zeroes, 0 if known else None])
        table += row([label, *values, total])
        tables.append(f'<p><strong>ESTADÍSTICAS AÑO {year}</strong>.</p><figure><table>{table}</table></figure>')
    return ('<html><h2>Estadísticas de Homicidios</h2>' + ''.join(tables)
            + '<p>Fuente: Equipo Operativo de Homicidios</p></html>').encode()


def catalog_html(years=(2021,)):
    return ('<html>' + ''.join(f'<a href="/descargar/3/{year}/HOMICIDIOS AÑO {year}/01-01-2026">Descargar</a>'
                             for year in years) + '</html>').encode()


def setup_fetch(monkeypatch, *, changed=None, errors=None, catalog=None):
    document = baseline()
    raw = fgr_html(changes=changed)
    fake_raw = {}
    for source in document['sources']:
        if source['format'] == 'html':
            fake_raw[source['url']] = raw
        else:
            fake_raw[source['url']] = ('document:' + source['id']).encode()
            source['content_sha256'] = monitor.digest(fake_raw[source['url']])
    fake_raw[monitor.CATALOG_URL] = catalog or catalog_html()
    fake_raw[monitor.PNC_URL] = b'<html>Reportes PNC</html>'
    fake_raw[monitor.MEMORIAS_URL] = b'<html><a href="/informe-de-labores-2025-2026/">Informe 2025-2026</a></html>'
    def fetch(url, kind, session):
        if errors and url in errors:
            raise monitor.RefreshError(errors[url])
        return fake_raw[url]
    monkeypatch.setattr(monitor, 'fetch_source', fetch)
    return document, fake_raw


def run(document, **kwargs):
    kwargs.setdefault('previous_state', {'schema_version': 1, 'catalogs': {monitor.PNC_ID: [monitor.PNC_URL], monitor.MEMORIAS_ID: ['https://www.fiscalia.gob.sv/informe-de-labores-2025-2026/']}})
    return monitor.refresh(document, monitor.digest(monitor.json_bytes(document)),
                           session=object(), as_of=CUTOFF, checked_at=CHECKED, **kwargs)


def test_current_table_contract_keeps_zero_and_blank_different():
    values = [0, 0, 0, 0, 0, 0, 0, 0, None, None, None, None]
    rows = monitor.parse_fgr(fgr_html(changes={2026: values}), CUTOFF)
    assert rows[2026]['total'] == 0
    assert rows[2026]['months'][0] == 0 and rows[2026]['months'][8] is None
    assert rows[2022]['indicator'] == 'homicides'
    assert rows[2023]['indicator'] == 'intentional_homicides'


@pytest.mark.parametrize('old,new,code', [
    (b'<td>ENE.</td>', b'<td>JAN.</td>', 'fgr_month_labels_changed'),
    (b'Total Homicidios Intencionales', b'Total Homicidios', 'fgr_total_label_changed'),
    (b'<td>49</td>', b'<td>50</td>', 'fgr_total_mismatch'),
    (b'<td>6</td>', b'<td>-1</td>', 'fgr_invalid_integer'),
    (b'<td>6</td>', b'<td>6*</td>', 'fgr_invalid_integer'),
    (b'<td>6</td>', b'<td>6.0</td>', 'fgr_invalid_integer'),
    (b'ESTAD\xc3\x8dSTICAS A\xc3\x91O 2026', b'ESTAD\xc3\x8dSTICAS A\xc3\x91O 2027', 'fgr_year_outside_cutoff'),
    (b'ESTAD\xc3\x8dSTICAS A\xc3\x91O 2026', b'ESTAD\xc3\x8dSTICAS A\xc3\x91O 2025', 'fgr_duplicate_year'),
    (b'Equipo Operativo de Homicidios', b'Otro organismo', 'fgr_attribution_missing'),
    (b'<td>ENE.</td>', b'<td colspan="2">ENE.</td>', 'fgr_merged_cells'),
])
def test_malformed_or_changed_source_is_not_silently_salvaged(old, new, code):
    with pytest.raises(monitor.RefreshError, match=code):
        monitor.parse_fgr(fgr_html().replace(old, new), CUTOFF)


def test_missing_year_heading_cannot_borrow_another_table_year():
    raw = fgr_html().replace(b'<p><strong>ESTAD\xc3\x8dSTICAS A\xc3\x91O 2025</strong>.</p>', b'')
    with pytest.raises(monitor.RefreshError, match='fgr_year_label_missing'):
        monitor.parse_fgr(raw, CUTOFF)


@pytest.mark.parametrize('values,code', [
    ([1] * 12, 'fgr_future_observation'),
    ([1, None, 1] + [None] * 9, 'fgr_noncontiguous_months'),
])
def test_future_or_interior_missing_month_is_not_inferred(values, code):
    with pytest.raises(monitor.RefreshError, match=code):
        monitor.parse_fgr(fgr_html(changes={2026: values}), CUTOFF)


def test_categories_are_cross_checked_but_not_double_counted():
    raw = fgr_html().replace(b'<td>Intolerancia Social</td><td>0</td>', b'<td>Intolerancia Social</td><td>1</td>')
    with pytest.raises(monitor.RefreshError, match='fgr_category_total_mismatch'):
        monitor.parse_fgr(raw, CUTOFF)


def test_unchanged_values_ignore_html_visitor_counter_and_timestamps(monkeypatch):
    document, raw = setup_fetch(monkeypatch)
    original = copy.deepcopy(document)
    report, candidate, state = run(document)
    raw[monitor.FGR_URL] += b'<p>99999 visitas</p>'
    again, candidate2, state2 = monitor.refresh(document, report['baseline_sha256'], session=object(),
        as_of=date(2026, 10, 13), checked_at='2026-10-14T04:00:00+00:00', previous_state=state)
    assert report['status'] == again['status'] == 'unchanged'
    assert not report['changes'] and not again['changes']
    assert candidate is candidate2 is None and document == original and state == state2


def test_monthly_update_generates_only_private_candidate_with_provenance(monkeypatch):
    values = [6, 5, 8, 5, 10, 3, 5, 7, 4, None, None, None]
    document, _ = setup_fetch(monkeypatch, changed={2026: values})
    original = copy.deepcopy(document)
    report, candidate, _ = run(document)
    assert report['status'] == 'changed' and report['review_required']
    assert report['proposal_sha256'] == monitor.digest(monitor.json_bytes(candidate))
    assert candidate['periods'][-1]['national']['value'] == 53
    assert '2026 sólo llega a agosto' not in candidate['comparability_note']
    assert candidate['periods'][-1]['end_date'] == '2026-09-30'
    assert candidate['periods'][-1]['months'][10]['status'] == 'outside_cutoff'
    assert all(d['value'] is None for d in candidate['periods'][-1]['departments'])
    assert candidate['periods'][:-1] == document['periods'][:-1]
    assert document == original
    source = next(s for s in candidate['sources'] if s['url'] == monitor.FGR_URL)
    assert source['retrieved_at'] == CHECKED
    assert report['changes'][0]['details']['before']['total'] == 49
    assert report['changes'][0]['details']['after']['total'] == 53
    # The real shared product schema validates the proposal, not just a test copy.
    js = "import {parseOfficialStatistics} from './shared/official-statistics.mjs';let s='';for await(const c of process.stdin)s+=c;parseOfficialStatistics(JSON.parse(s));"
    subprocess.run(['node', '--input-type=module', '-e', js], cwd=ROOT,
                   input=monitor.json_bytes(candidate), check=True, capture_output=True)


def test_repeat_proposal_id_is_stable_across_query_days(monkeypatch):
    document, _ = setup_fetch(monkeypatch, changed={2026: [7, 5, 8, 5, 10, 3, 5, 7, None, None, None, None]})
    first, _, state = run(document)
    second, _, _ = monitor.refresh(document, first['baseline_sha256'], session=object(),
        as_of=date(2026, 10, 13), checked_at='2026-10-14T04:00:00+00:00', previous_state=state)
    assert [c['id'] for c in first['changes']] == [c['id'] for c in second['changes']]


def test_revised_lower_count_is_explicit_change_not_discarded(monkeypatch):
    document, _ = setup_fetch(monkeypatch, changed={2026: [5, 5, 8, 5, 10, 3, 5, 7, None, None, None, None]})
    report, candidate, _ = run(document)
    assert candidate['periods'][-1]['national']['value'] == 48
    assert report['changes'][0]['details']['before']['total'] == 49


def test_removed_observation_stays_review_error_not_empty_overwrite(monkeypatch):
    document, _ = setup_fetch(monkeypatch, changed={2026: [6, 5, 8, 5, 10, 3, 5] + [None] * 5})
    report, candidate, _ = run(document)
    assert candidate is None and report['status'] == 'error'
    assert report['errors'][0]['code'] == 'fgr_existing_observation_removed'
    assert document['periods'][-1]['national']['value'] == 49


def test_missing_existing_annual_table_is_error():
    rows = monitor.parse_fgr(fgr_html(), CUTOFF)
    del rows[2024]
    with pytest.raises(monitor.RefreshError, match='fgr_existing_year_removed'):
        monitor.propose_fgr(baseline(), rows, CUTOFF, CHECKED, 'a' * 64)


def test_new_calendar_year_gets_missing_period_without_imputation():
    rows = monitor.parse_fgr(fgr_html(), date(2027, 2, 1))
    rows[2026]['months'][0] += 1
    rows[2026]['total'] += 1
    proposal, _ = monitor.propose_fgr(baseline(), rows, date(2027, 2, 1), '2027-02-02T02:00:00+00:00', 'a' * 64)
    assert len(proposal['periods']) == 18
    assert proposal['periods'][-1]['year'] == 2027
    assert proposal['periods'][-1]['completeness'] == 'unavailable'
    assert proposal['periods'][-1]['national']['value'] is None
    assert all(m['value'] is None for m in proposal['periods'][-1]['months'])
    assert proposal['periods'][-1]['months'][2]['status'] == 'outside_cutoff'
    assert proposal['periods'][-2]['months'][-1]['status'] == 'not_available'


def test_new_year_source_table_preserves_14_unavailable_departments():
    rows = monitor.parse_fgr(fgr_html(), date(2027, 2, 1))
    rows[2027] = {'year': 2027, 'indicator': 'intentional_homicides', 'unit': 'source_reported_homicides',
                  'label': 'Total Homicidios Intencionales', 'months': [3] + [None] * 11, 'total': 3}
    proposal, changes = monitor.propose_fgr(baseline(), rows, date(2027, 2, 1), '2027-02-02T02:00:00+00:00', 'a' * 64)
    assert next(change for change in changes if change['kind'] == 'fgr_values')['details']['before'] is None
    assert len(proposal['periods'][-1]['departments']) == 14
    assert all(d['value'] is None for d in proposal['periods'][-1]['departments'])


def test_pdf_revision_never_autoparses_or_rewrites_baseline(monkeypatch):
    document, raw = setup_fetch(monkeypatch)
    pdf = next(s for s in document['sources'] if s['format'] == 'pdf')
    raw[pdf['url']] = b'changed pdf bytes'
    report, candidate, _ = run(document)
    assert candidate is None
    assert report['changes'][0]['kind'] == 'source_revision'
    assert report['changes'][0]['details']['previous_sha256'] == pdf['content_sha256']


def test_network_error_keeps_baseline_and_catalog_state(monkeypatch):
    previous = {'schema_version': 1, 'catalogs': {monitor.CATALOG_ID: monitor.catalog_documents(catalog_html()), monitor.PNC_ID: [monitor.PNC_URL], monitor.MEMORIAS_ID: ['https://www.fiscalia.gob.sv/informe-de-labores-2025-2026/']}}
    document, _ = setup_fetch(monkeypatch, errors={monitor.FGR_URL: 'source_http_403', monitor.CATALOG_URL: 'source_http_503'})
    report, candidate, state = run(document, previous_state=previous)
    assert candidate is None and report['status'] == 'error' and state == previous
    assert {e['code'] for e in report['errors']} == {'source_http_403', 'source_http_503'}


def test_catalog_bootstrap_tracks_inventory_without_mass_queue(monkeypatch):
    document, _ = setup_fetch(monkeypatch, catalog=catalog_html(range(2010, 2027)))
    report, candidate, state = run(document)
    assert candidate is None and len(state['catalogs'][monitor.CATALOG_ID]) == 17
    assert len(report['changes']) == 1 and report['changes'][0]['kind'] == 'catalog_document'
    assert '2026' in report['changes'][0]['details']['document_url']
    again, _, _ = run(document, previous_state=state)
    assert again['status'] == 'unchanged'


def test_catalog_addition_and_removal_have_stable_individual_events(monkeypatch):
    previous = {'schema_version': 1, 'catalogs': {monitor.CATALOG_ID: monitor.catalog_documents(catalog_html((2020, 2021))), monitor.PNC_ID: [monitor.PNC_URL], monitor.MEMORIAS_ID: ['https://www.fiscalia.gob.sv/informe-de-labores-2025-2026/']}}
    document, _ = setup_fetch(monkeypatch, catalog=catalog_html((2021, 2022)))
    report, _, _ = run(document, previous_state=previous)
    assert {c['details']['action'] for c in report['changes']} == {'added', 'removed'}


def test_catalog_year_filter_does_not_mistake_portal_revision_date_for_data_year():
    raw = catalog_html((1999, 2010, 2021))
    urls = monitor.catalog_documents(raw)
    assert len(urls) == 2 and not any('1999' in u for u in urls)


def test_catalog_disallows_external_document_links():
    raw = b'<a href="https://evil.example/HOMICIDIOS-2026.pdf">Homicidios 2026</a>'
    with pytest.raises(monitor.RefreshError, match='source_url_not_allowed'):
        monitor.catalog_documents(raw)


class Response:
    def __init__(self, status=200, raw=b'<html>ok</html>', headers=None):
        self.status_code, self.raw, self.headers = status, raw, headers or {}
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def iter_content(self, size): yield self.raw


class Session:
    def __init__(self, *responses): self.responses, self.calls = list(responses), []
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


@pytest.fixture
def public_dns(monkeypatch):
    monkeypatch.setattr(monitor, 'require_public_dns', lambda host: None)


@pytest.mark.parametrize('url', ['http://www.fiscalia.gob.sv/a', 'https://127.0.0.1/a',
    'https://evil.example/a', 'https://www.fiscalia.gob.sv:443/a',
    'https://user:password@www.fiscalia.gob.sv/a', 'https://www.fiscalia.gob.sv/a?target=x'])
def test_fetch_rejects_unapproved_source_url_before_network(url, public_dns):
    session = Session()
    with pytest.raises(monitor.RefreshError, match='source_url_not_allowed'):
        monitor.fetch_source(url, 'html', session)
    assert not session.calls


def test_cross_host_redirect_cannot_reach_other_allowlisted_institution(public_dns):
    session = Session(Response(302, headers={'Location': 'https://data.unodc.org/private'}))
    with pytest.raises(monitor.RefreshError, match='source_url_not_allowed'):
        monitor.fetch_source(monitor.FGR_URL, 'html', session)
    assert len(session.calls) == 1


def test_private_dns_is_rejected(monkeypatch):
    def fail(host): raise ValueError('private')
    monkeypatch.setattr(monitor, 'require_public_dns', fail)
    session = Session()
    with pytest.raises(monitor.RefreshError, match='source_dns_not_public'):
        monitor.fetch_source(monitor.FGR_URL, 'html', session)
    assert not session.calls


@pytest.mark.parametrize('response,kind,code', [
    (Response(403), 'html', 'source_http_403'),
    (Response(headers={'Content-Length': '99999999'}), 'html', 'source_too_large'),
    (Response(raw=b'a' * 2_000_001), 'html', 'source_too_large'),
    (Response(headers={'Content-Length': 'abc'}), 'html', 'source_invalid_content_length'),
    (Response(raw=b'<html>challenge</html>'), 'pdf', 'source_not_pdf'),
    (Response(raw=b'<html>challenge</html>'), 'xlsx', 'source_not_xlsx'),
    (Response(raw=b''), 'html', 'source_empty'),
])
def test_fetch_is_bounded_and_rejects_error_pages(response, kind, code, public_dns):
    with pytest.raises(monitor.RefreshError, match=code):
        monitor.fetch_source(monitor.FGR_URL, kind, Session(response))


def test_safe_redirect_uses_no_automatic_redirect_or_credentials(public_dns):
    session = Session(Response(302, headers={'Location': 'https://fiscalia.gob.sv/a'}), Response())
    assert monitor.fetch_source(monitor.FGR_URL, 'html', session) == b'<html>ok</html>'
    assert all(c[1]['allow_redirects'] is False and c[1]['headers']['Authorization'] is None
               and c[1]['timeout'] == (8, 30) for c in session.calls)


def test_private_outputs_and_stale_candidate_cleanup(tmp_path, monkeypatch):
    document, _ = setup_fetch(monkeypatch)
    report, candidate, state = run(document)
    (tmp_path / 'proposed-homicides.json').write_text('stale')
    monitor.write_outputs(tmp_path, report, candidate, state)
    assert not (tmp_path / 'proposed-homicides.json').exists()
    assert json.loads((tmp_path / 'report.json').read_bytes()) == report
    assert (tmp_path / 'report.json').stat().st_mode & 0o777 == 0o600
    with pytest.raises(monitor.RefreshError, match='output_must_be_private'):
        monitor.write_outputs(ROOT / 'apps/web/public/statistics', report, candidate, state)


def test_cli_error_status_written_without_sensitive_exception(tmp_path, monkeypatch, capsys):
    document, _ = setup_fetch(monkeypatch, errors={monitor.FGR_URL: 'source_http_403'})
    input_path = tmp_path / 'baseline.json'
    input_path.write_bytes(monitor.json_bytes(document))
    assert monitor.main(['--baseline', str(input_path), '--output-dir', str(tmp_path / 'review')]) == 2
    assert (tmp_path / 'review/report.json').exists()
    assert json.loads(capsys.readouterr().out)['status'] == 'error'


def test_pnc_denial_is_reported_and_not_retried_elsewhere(monkeypatch):
    document, _ = setup_fetch(monkeypatch, errors={monitor.PNC_URL: 'source_http_403'})
    report, candidate, state = run(document)
    assert candidate is None and report['status'] == 'error'
    assert report['errors'] == [{'source_id': monitor.PNC_ID, 'code': 'source_http_403'}]
    assert next(s for s in report['sources'] if s['source_id'] == monitor.PNC_ID)['manual_review']
    assert state['catalogs'][monitor.PNC_ID] == [monitor.PNC_URL]


def test_pnc_access_recovery_is_only_a_manual_discovery(monkeypatch):
    document, _ = setup_fetch(monkeypatch)
    report, candidate, state = run(document, previous_state={'schema_version': 1, 'catalogs': {
        monitor.MEMORIAS_ID: ['https://www.fiscalia.gob.sv/informe-de-labores-2025-2026/']}})
    assert candidate is None
    assert report['changes'][0]['source_id'] == monitor.PNC_ID
    assert report['changes'][0]['details']['requires_manual_extraction'] is True
    assert report['changes'][0]['details']['document_url'] == monitor.PNC_URL
    assert state['catalogs'][monitor.PNC_ID] == [monitor.PNC_URL]


def test_fiscal_year_report_is_discovered_without_creating_calendar_year_counts(monkeypatch):
    document, raw = setup_fetch(monkeypatch)
    report, candidate, state = run(document, previous_state={'schema_version': 1, 'catalogs': {
        monitor.PNC_ID: [monitor.PNC_URL]}})
    assert candidate is None
    change = report['changes'][0]
    assert change['kind'] == 'catalog_document' and change['source_id'] == monitor.MEMORIAS_ID
    assert change['details']['document_url'].endswith('/informe-de-labores-2025-2026/')
    assert change['content_sha256'] == monitor.digest(raw[monitor.MEMORIAS_URL])
    raw[monitor.MEMORIAS_URL] += b'<p>New visitor counter 93894</p>'
    again, _, _ = run(document, previous_state=state)
    assert again['status'] == 'unchanged'


def test_changed_fgr_numbers_remain_reviewable_if_other_source_is_inaccessible(monkeypatch):
    document, _ = setup_fetch(monkeypatch, changed={2026: [7, 5, 8, 5, 10, 3, 5, 7, None, None, None, None]},
                              errors={monitor.PNC_URL: 'source_http_403'})
    report, candidate, _ = run(document)
    assert report['status'] == 'error' and report['review_required']
    assert candidate['periods'][-1]['national']['value'] == 50
    assert report['proposal_file'] == 'proposed-homicides.json'
    assert document['periods'][-1]['national']['value'] == 49


def test_semantic_comparison_uses_month_numbers_not_incidental_json_order():
    document = baseline()
    rows = monitor.parse_fgr(fgr_html(document), CUTOFF)
    for period in document['periods']:
        if period['months'] is not None:
            period['months'].reverse()
    candidate, changes = monitor.propose_fgr(document, rows, CUTOFF, CHECKED, 'a' * 64)
    assert candidate is None and changes == []


def test_year_rollover_alone_proposes_missing_year_and_reclassifies_old_future_months():
    document = baseline()
    rows = monitor.parse_fgr(fgr_html(document), date(2027, 1, 1))
    proposal, changes = monitor.propose_fgr(document, rows, date(2027, 1, 1), '2027-01-02T02:00:00+00:00', 'a' * 64)
    assert [c['kind'] for c in changes] == ['coverage_period']
    assert changes[0]['details']['before']['last_year'] == 2026
    assert changes[0]['details']['after']['added_years'] == [2027]
    assert len(proposal['periods']) == 18
    current = proposal['periods'][-1]
    assert current['year'] == 2027 and current['completeness'] == 'unavailable'
    assert current['national']['value'] is None and len(current['departments']) == 14
    assert all(d['value'] is None for d in current['departments'])
    assert current['months'][0]['status'] == 'not_available'
    assert all(m['status'] == 'outside_cutoff' for m in current['months'][1:])
    previous = proposal['periods'][-2]
    assert previous['national']['value'] == 49
    assert all(m['status'] == 'not_available' for m in previous['months'][8:])
    note = next(n for n in previous['notes'] if n.startswith('Cobertura numérica:'))
    assert 'fuera del corte' not in note
    assert 'Meses sin datos publicados: septiembre, octubre, noviembre, diciembre.' in note
    assert previous['notes'][:-1] == document['periods'][-1]['notes'][:-1]
    js = """const D=Date;globalThis.Date=class extends D{constructor(...a){super(...(a.length?a:['2027-01-03T00:00:00Z']))}static now(){return D.parse('2027-01-03T00:00:00Z')}};
const {parseOfficialStatistics}=await import('./shared/official-statistics.mjs');let s='';for await(const c of process.stdin)s+=c;parseOfficialStatistics(JSON.parse(s));"""
    subprocess.run(['node', '--input-type=module', '-e', js], cwd=ROOT,
                   input=monitor.json_bytes(proposal), check=True, capture_output=True)
    # Approval of an explicit missing year does not make the next fetch report
    # its still-absent table as a deletion, nor create a new proposal every day.
    again, repeated = monitor.propose_fgr(proposal, rows, date(2027, 1, 2), '2027-01-03T02:00:00+00:00', 'b' * 64)
    assert again is None and repeated == []


def test_historical_revision_refreshes_stale_coverage_note_on_unchanged_recent_year():
    document = baseline()
    rows = monitor.parse_fgr(fgr_html(document), date(2027, 1, 1))
    rows[2024]['months'][0] += 1
    rows[2024]['total'] += 1
    proposal, changes = monitor.propose_fgr(document, rows, date(2027, 1, 1), '2027-01-02T02:00:00+00:00', 'a' * 64)
    assert {c['kind'] for c in changes} == {'fgr_values', 'coverage_period'}
    recent = next(p for p in proposal['periods'] if p['year'] == 2026)
    assert recent['national']['value'] == 49
    assert 'fuera del corte' not in ' '.join(recent['notes'])
    assert recent['notes'][:-1] == document['periods'][-1]['notes'][:-1]


def test_month_rollover_only_proposes_when_a_missing_month_changes_cutoff_status():
    document = baseline()
    rows = monitor.parse_fgr(fgr_html(document), date(2026, 11, 1))
    proposal, changes = monitor.propose_fgr(document, rows, date(2026, 11, 1), '2026-11-02T02:00:00+00:00', 'a' * 64)
    assert [c['kind'] for c in changes] == ['coverage_period']
    assert changes[0]['details']['after']['added_years'] == []
    assert changes[0]['details']['after']['monthly_statuses'] == [{'year': 2026, 'month': 11, 'status': 'not_available'}]
    assert proposal['periods'][-1]['months'][10]['status'] == 'not_available'
    assert proposal['periods'][-1]['months'][11]['status'] == 'outside_cutoff'
    assert 'Meses fuera del corte: diciembre.' in proposal['periods'][-1]['notes'][-1]
    again, repeated = monitor.propose_fgr(proposal, rows, date(2026, 11, 2), '2026-11-03T02:00:00+00:00', 'b' * 64)
    assert again is None and repeated == []


def test_unapproved_rollover_event_id_does_not_change_with_the_query_day():
    document = baseline()
    rows = monitor.parse_fgr(fgr_html(document), date(2027, 1, 1))
    _, changes = monitor.propose_fgr(document, rows, date(2027, 1, 1), '2027-01-02T02:00:00+00:00', 'a' * 64)
    _, later = monitor.propose_fgr(document, rows, date(2027, 1, 7), '2027-01-08T02:00:00+00:00', 'b' * 64)
    assert [c['id'] for c in changes] == [c['id'] for c in later]
