import type { Publication } from '../catalog';
import { DEPARTMENTS } from './elSalvador';

export const UNKNOWN_DEPARTMENT = '__unknown__';
const names = new Set<string>(DEPARTMENTS.map(d => d.name));

export function matchesTerritory(record: Publication, selected: string) {
  if (!selected) return true;
  if (selected === UNKNOWN_DEPARTMENT) return record.record_type === 'incident' && !names.has(record.department ?? '');
  return record.department === selected;
}

export function summarizeTerritory(records: Publication[]) {
  const counts: Record<string, number> = Object.fromEntries(DEPARTMENTS.map(d => [d.name, 0]));
  let unlocated = 0;
  let located = 0;
  for (const record of records) {
    if (record.record_type !== 'incident') continue;
    if (record.department && names.has(record.department)) {
      counts[record.department] += 1;
      located += 1;
    } else {
      unlocated += 1;
    }
  }
  return { counts, located, unlocated };
}
