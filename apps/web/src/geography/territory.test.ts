import { describe, expect, it } from 'vitest';
import fixture from '../__fixtures__/catalog.json';
import { parseCatalog } from '../catalog';
import { DEPARTMENTS } from './elSalvador';
import { matchesTerritory, summarizeTerritory, UNKNOWN_DEPARTMENT } from './territory';

describe('departmental coverage', () => {
  it('has all fourteen unique departments and usable paths', () => {
    expect(DEPARTMENTS).toHaveLength(14);
    expect(new Set(DEPARTMENTS.map(d => d.name)).size).toBe(14);
    expect(new Set(DEPARTMENTS.map(d => d.id)).size).toBe(14);
    expect(DEPARTMENTS.every(d => d.d.startsWith('M') && d.labelX > 0 && d.labelX < 1000 && d.labelY > 0 && d.labelY < 547)).toBe(true);
  });
  it('counts individual reports without distributing collective or contextual records', () => {
    const records = parseCatalog(fixture).records;
    records.push({...records.find(r=>r.record_type==='incident')!, id:'unknown', department:null});
    records.push({...records.find(r=>r.record_type==='aggregate')!, id:'collective', department:'San Vicente'});
    const summary = summarizeTerritory(records);
    expect(summary.located).toBe(4);
    expect(summary.unlocated).toBe(1);
    expect(summary.counts['Sonsonate']).toBe(2);
    expect(summary.counts['San Vicente']).toBe(0);
    expect(summary.counts['La Unión']).toBe(0);
  });
  it('keeps unknown-location filtering consistent with counts', () => {
    const records = parseCatalog(fixture).records;
    const incident = {...records.find(r=>r.record_type==='incident')!, department:null};
    expect(matchesTerritory(incident, UNKNOWN_DEPARTMENT)).toBe(true);
    expect(matchesTerritory(records.find(r=>r.record_type==='publication')!, UNKNOWN_DEPARTMENT)).toBe(false);
    expect(matchesTerritory(incident, 'San Salvador')).toBe(false);
    expect(matchesTerritory(incident, '')).toBe(true);
  });
});
