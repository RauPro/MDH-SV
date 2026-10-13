import { newsDossierFor, type NewsDossier } from '../../../../shared/news-dossier.mjs';
import { newsLocationFor, type NewsLocation } from '../../../../shared/news-location.mjs';
import type { NewsReference } from '../newsArchive';
import { DEPARTMENTS } from './elSalvador';
import { UNKNOWN_DEPARTMENT } from './territory';

export type NewsTerritoryReason = 'located' | 'pending_review' | 'jurisdiction_unknown' | 'outside_el_salvador' | 'department_unknown';
export interface NewsTerritoryItem {
  reference: NewsReference;
  dossier: NewsDossier;
  department: string | null;
  departments: string[];
  locationKind: 'reviewed' | 'automated_unreviewed' | null;
  automaticLocation: NewsLocation | null;
  reason: NewsTerritoryReason;
}

const departmentNames = new Set<string>(DEPARTMENTS.map(department => department.name));

/** Human decisions override the separate, explicitly unreviewed location projection. */
export function newsTerritoryItem(reference: NewsReference, approved: Map<string, NewsDossier> = new Map(), automatic: Map<string, NewsLocation> = new Map()): NewsTerritoryItem {
  const dossier = newsDossierFor(reference, approved);
  const automaticLocation = !dossier.human_reviewed ? newsLocationFor(reference, automatic) : null;
  let locationKind: NewsTerritoryItem['locationKind'] = null;
  let reason: NewsTerritoryReason;
  let departments: string[] = [];
  if (!dossier.human_reviewed || dossier.extraction_status !== 'processed') {
    departments = automaticLocation?.method === 'source_administrative_mentions'
      ? automaticLocation.mentions.map(mention => mention.department)
      : automaticLocation?.department ? [automaticLocation.department] : [];
    locationKind = departments.length ? 'automated_unreviewed' : null;
    reason = departments.length ? 'located' : 'pending_review';
  }
  else if (dossier.classification.jurisdiction === 'other') reason = 'outside_el_salvador';
  else if (dossier.classification.jurisdiction !== 'SV') reason = 'jurisdiction_unknown';
  else {
    const field = dossier.fields.department;
    if (field.status === 'reported' && typeof field.value === 'string' && departmentNames.has(field.value)) {
      departments = [field.value];
      reason = 'located';
      locationKind = 'reviewed';
    } else reason = 'department_unknown';
  }
  return { reference, dossier, departments, department: departments.length === 1 ? departments[0] : null, reason, locationKind, automaticLocation };
}

export function buildNewsTerritoryItems(references: readonly NewsReference[], approved: Map<string, NewsDossier> = new Map(), automatic: Map<string, NewsLocation> = new Map()) {
  return references.map(reference => newsTerritoryItem(reference, approved, automatic));
}

export function summarizeNewsTerritory(items: readonly NewsTerritoryItem[]) {
  const counts: Record<string, number> = Object.fromEntries(DEPARTMENTS.map(department => [department.name, 0]));
  const reviewedCounts = { ...counts };
  const automaticCounts = { ...counts };
  let reviewed = 0;
  let automated = 0;
  const reasons: Record<NewsTerritoryReason, number> = {
    located: 0, pending_review: 0, jurisdiction_unknown: 0, outside_el_salvador: 0, department_unknown: 0,
  };
  const seen = new Set<string>();
  for (const item of items) {
    if (seen.has(item.reference.id)) continue;
    seen.add(item.reference.id);
    reasons[item.reason] += 1;
    if (item.departments.length) {
      if (item.locationKind === 'reviewed') reviewed += 1;
      if (item.locationKind === 'automated_unreviewed') automated += 1;
      for (const department of new Set(item.departments)) {
        counts[department] += 1;
        if (item.locationKind === 'reviewed') reviewedCounts[department] += 1;
        if (item.locationKind === 'automated_unreviewed') automaticCounts[department] += 1;
      }
    }
  }
  return { counts, reviewedCounts, automaticCounts, reviewed, automated, located: reasons.located, unlocated: seen.size - reasons.located, reasons };
}

export function matchesNewsTerritory(item: NewsTerritoryItem, selected: string) {
  if (!selected) return true;
  if (selected === UNKNOWN_DEPARTMENT) return item.departments.length === 0;
  return item.departments.includes(selected);
}
