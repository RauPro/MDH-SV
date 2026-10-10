import { newsDossierFor, type NewsDossier } from '../../../../shared/news-dossier.mjs';
import type { NewsReference } from '../newsArchive';
import { DEPARTMENTS } from './elSalvador';
import { UNKNOWN_DEPARTMENT } from './territory';

export type NewsTerritoryReason = 'located' | 'pending_review' | 'jurisdiction_unknown' | 'outside_el_salvador' | 'department_unknown';
export interface NewsTerritoryItem {
  reference: NewsReference;
  dossier: NewsDossier;
  department: string | null;
  reason: NewsTerritoryReason;
}

const departmentNames = new Set<string>(DEPARTMENTS.map(department => department.name));

/** A news location must come from the reviewed public dossier, never discovery metadata. */
export function newsTerritoryItem(reference: NewsReference, approved: Map<string, NewsDossier> = new Map()): NewsTerritoryItem {
  const dossier = newsDossierFor(reference, approved);
  let reason: NewsTerritoryReason;
  let department: string | null = null;
  if (!dossier.human_reviewed || dossier.extraction_status !== 'processed') reason = 'pending_review';
  else if (dossier.classification.jurisdiction === 'other') reason = 'outside_el_salvador';
  else if (dossier.classification.jurisdiction !== 'SV') reason = 'jurisdiction_unknown';
  else {
    const field = dossier.fields.department;
    if (field.status === 'reported' && typeof field.value === 'string' && departmentNames.has(field.value)) {
      department = field.value;
      reason = 'located';
    } else reason = 'department_unknown';
  }
  return { reference, dossier, department, reason };
}

export function buildNewsTerritoryItems(references: readonly NewsReference[], approved: Map<string, NewsDossier> = new Map()) {
  return references.map(reference => newsTerritoryItem(reference, approved));
}

export function summarizeNewsTerritory(items: readonly NewsTerritoryItem[]) {
  const counts: Record<string, number> = Object.fromEntries(DEPARTMENTS.map(department => [department.name, 0]));
  const reasons: Record<NewsTerritoryReason, number> = {
    located: 0, pending_review: 0, jurisdiction_unknown: 0, outside_el_salvador: 0, department_unknown: 0,
  };
  for (const item of items) {
    reasons[item.reason] += 1;
    if (item.department) counts[item.department] += 1;
  }
  return { counts, located: reasons.located, unlocated: items.length - reasons.located, reasons };
}

export function matchesNewsTerritory(item: NewsTerritoryItem, selected: string) {
  if (!selected) return true;
  if (selected === UNKNOWN_DEPARTMENT) return item.department === null;
  return item.department === selected;
}
