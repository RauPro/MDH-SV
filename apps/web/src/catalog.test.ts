import { describe, expect, it } from 'vitest';
import data from './__fixtures__/catalog.json';
import { parseCatalog, filterRecords, safeSource, formatDate } from './catalog';

describe('public catalogue boundary', () => {
  it('reads the exported schema and keeps the input unchanged', () => {
    const copy = JSON.stringify(data);
    expect(parseCatalog(data).records).toHaveLength(9);
    expect(JSON.stringify(data)).toBe(copy);
  });
  it.each(['javascript:alert(1)', 'https://www.hrw.org.evil.test/report', 'https://user:pass@www.hrw.org/report', 'http://www.hrw.org/report', 'https://localhost/a'])('rejects unsafe links: %s', url => {
    expect(safeSource(url)).toBe(false);
    const modified = structuredClone(data);
    modified.records[0].source_url = url;
    expect(() => parseCatalog(modified)).toThrow();
  });
  it('rejects duplicate identities and malformed data', () => {
    expect(() => parseCatalog({})).toThrow();
    expect(() => parseCatalog({ ...data, records: [...data.records, data.records[0]] })).toThrow();
    expect(() => parseCatalog({ ...data, records: [{ ...data.records[0], published_date: '2024-02-31' }] })).toThrow();
  });
  it('supports accent-insensitive, trimmed, multiple-term search', () => {
    const records = parseCatalog(data).records;
    expect(filterRecords(records, '  AHUACHAPAN  detencion ', '', '')).toHaveLength(1);
    expect(filterRecords(records, 'inexistente', '', '')).toHaveLength(0);
    expect(filterRecords(records, '', 'Vida e integridad personal', '')).toHaveLength(2);
  });
  it('does not shift dates to the preceding day in El Salvador', () => {
    expect(formatDate('2024-07-16')).toMatch(/16.*2024/);
  });
});
