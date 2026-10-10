import { expect, it } from 'vitest';
import fixture from './__fixtures__/newsArchive.json';
import catalog from './__fixtures__/catalog.json';
import currentArchive from '../public/archive/news.json';
import { filterNews, invalidArchiveRange, parseNewsArchive, relatedPublications, type NewsArchiveData } from './newsArchive';
import { parseCatalog } from './catalog';

const filters = { query: '', publisher: '', topic: '', from: '', to: '', scope: '' };
function htmlArchiveFixture() {
  const value = structuredClone(fixture) as unknown as NewsArchiveData;
  const reference = value.records[0];
  const previousId = reference.source_id;
  const source = value.coverage.sources.find(item => item.id === previousId)!;
  source.id = reference.source_id = 'el-diario-de-hoy';
  source.publisher = reference.publisher = 'El Diario de Hoy (archivo)';
  source.endpoint = reference.discovery.endpoint = 'https://historico.elsalvador.com/historico/SiteMapHistoricoEscom.xml';
  reference.source_url = 'https://historico.elsalvador.com/historico/123/detenciones.html';
  reference.discovery.method = 'html_article_metadata';
  reference.discovery.source_post_id = null;
  for (const month of value.coverage.months) for (const item of month.sources) if (item.source_id === previousId) item.source_id = source.id;
  return value;
}
it('accepts historical HTML metadata without pretending it has a WordPress id', () => {
  const value = htmlArchiveFixture();
  expect(parseNewsArchive(value).records).toHaveLength(3);
  value.records[0].source_url = 'https://www.elsalvador.com/h-noticias/h-nacional/detenciones/123/2024/';
  expect(parseNewsArchive(value).records).toHaveLength(3);
  value.records[0].source_url = 'https://evil.elsalvador.com/detenciones';
  expect(() => parseNewsArchive(value)).toThrow();
});
it('rejects invented discovery methods and untrusted source identities', () => {
  const value = htmlArchiveFixture();
  value.records[0].discovery.method = 'wordpress_rest_title_screen';
  expect(() => parseNewsArchive(value)).toThrow();
  const wp = structuredClone(fixture) as unknown as NewsArchiveData;
  wp.records[0].discovery.source_post_id = null;
  expect(() => parseNewsArchive(wp)).toThrow();
});
it('accepts the actual generated archive snapshot', () => {
  expect(parseNewsArchive(currentArchive).records).toHaveLength(currentArchive.records.length);
});
it('validates and sorts the bibliography without converting references to cases', () => {
  const archive = parseNewsArchive(fixture);
  expect(archive.records).toHaveLength(3);
  expect(archive.records[0].published_date).toBe('2026-10-08');
  expect(archive.records.every(record => record.review_status === 'not_reviewed' && record.jurisdiction_status === 'not_reviewed')).toBe(true);
  expect(archive.coverage.status).toBe('partial');
});
it('rejects unsafe sources, inconsistent dates, false review labels and misleading counts', () => {
  for (const update of [
    (value: typeof fixture) => { value.records[0].source_url = 'javascript:alert(1)'; },
    (value: typeof fixture) => { value.records[0].source_url = 'https://unapproved.example/news'; },
    (value: typeof fixture) => { value.records[0].discovery.endpoint = 'https://private.example/api'; },
    (value: typeof fixture) => { value.records[0].published_date = '2025-02-30'; },
    (value: typeof fixture) => { value.records[0].published_date = '2020-01-01'; },
    (value: typeof fixture) => { value.records[0].review_status = 'reviewed'; },
    (value: typeof fixture) => { value.records[0].jurisdiction_status = 'confirmed'; },
    (value: typeof fixture) => { value.records[0].scope_hint = 'confirmed_jurisdiction'; },
    (value: typeof fixture) => { value.records[0].scope_terms = ['']; },
    (value: typeof fixture) => { value.records[0].id = value.records[1].id; },
    (value: typeof fixture) => { value.coverage.records = 200; },
    (value: typeof fixture) => { value.coverage.sources[0].records = 90; },
    (value: typeof fixture) => { value.coverage.sources[0].months[0].status = 'complete'; },
  ]) {
    const changed = structuredClone(fixture);
    update(changed);
    expect(() => parseNewsArchive(changed)).toThrow();
  }
});
it('filters automatic territorial hints without asserting a confirmed jurisdiction', () => {
  const { records } = parseNewsArchive(structuredClone(fixture));
  records[1].scope_hint = 'international_context';
  records[2].scope_hint = 'undetermined';
  expect(filterNews(records, { ...filters, scope: 'el_salvador' })).toHaveLength(1);
  expect(filterNews(records, { ...filters, scope: 'international_context' })[0].id).toBe(records[1].id);
  expect(filterNews(records, { ...filters, scope: 'undetermined' })[0].jurisdiction_status).toBe('not_reviewed');
  expect(filterNews(records, filters)).toHaveLength(3);
});
it('combines accent-insensitive terms, publisher, topic and inclusive publication date filters', () => {
  const { records } = parseNewsArchive(fixture);
  expect(filterNews(records, { ...filters, query: 'persecucion sindical', publisher: 'GatoEncerrado', topic: 'defensores_y_sociedad_civil', from: '2026-08-18', to: '2026-08-18' })).toHaveLength(1);
  expect(filterNews(records, { ...filters, query: 'PERSECUCIÓN', to: '2026-01-01' })).toHaveLength(0);
  expect(filterNews(records, { ...filters, query: 'detencion' })[0].publisher).toBe('Revista Factum');
  expect(invalidArchiveRange('2026-10-01', '2026-09-01')).toBe(true);
  expect(filterNews(records, { ...filters, from: '2026-10-01', to: '2026-09-01' })).toHaveLength(0);
  expect(invalidArchiveRange('2025-02-30', '')).toBe(true);
});
it('links public dossiers by source URL and related sources, preserving meaningful URL differences', () => {
  const reference = parseNewsArchive(fixture).records[0];
  const base = parseCatalog(catalog).records[0];
  const records = [
    { ...base, id: 'direct', source_url: `${reference.source_url}?utm_source=monitor` },
    { ...base, id: 'related', related_sources: [{ url: reference.source_url.replace('www.', '').replace(/\/$/, ''), title: 'Fuente', published_date: reference.published_date, locator: 'Documento' }] },
    { ...base, id: 'different', source_url: `${reference.source_url}?edition=2` },
  ];
  expect(relatedPublications(reference, records).map(record => record.id)).toEqual(['direct', 'related']);
});
