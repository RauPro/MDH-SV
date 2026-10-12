import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { safeSource, validatePublishedNewsDossier } from '../../../shared/news-dossier.mjs';
import { assertSelectedNewsArchive, isSelectedNewsReference, validateNewsSelection } from '../../../shared/news-selection.mjs';

const exact = (value, keys) => value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const day = value => typeof value === 'string' && !value.startsWith('0000-') && /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
const instant = value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(value) && day(value.slice(0, 10)) && Number.isFinite(Date.parse(value));
const fail = () => { throw new Error('La selección publicada no coincide con su evidencia o aprobación de origen'); };
const bound = (entry, reference) => entry.candidate_id === reference.id && ['source_url', 'publisher', 'published_date'].every(key => entry[key] === reference[key]);

/** Build-time check; only approved dossiers can authorize a human selection. */
export function assertSelectionProvenance(archive, manifest, allApproved = new Map()) {
  assertSelectedNewsArchive(archive);
  if (!exact(manifest, ['schema_version', 'generated_at', 'selections', ...(manifest?.schema_version === 2 ? ['assessment_counts'] : [])]) || ![1, 2].includes(manifest.schema_version) || !instant(manifest.generated_at)
    || !Array.isArray(manifest.selections) || manifest.selections.length > 100000) fail();
  if (manifest.schema_version === 2) {
    const counts = manifest.assessment_counts;
    const keys = ['candidates', 'evaluated', 'relevant', 'excluded', 'uncertain', 'pending'];
    if (!exact(counts, keys) || keys.some(key => !Number.isSafeInteger(counts[key]) || counts[key] < 0)
      || counts.evaluated !== counts.relevant + counts.excluded + counts.uncertain
      || counts.candidates !== counts.evaluated + counts.pending) fail();
    const gate = archive.policy.selection_gate;
    const newlyDiscovered = gate.candidates - counts.candidates;
    if (gate.version !== 2 || gate.evaluated_at !== manifest.generated_at || newlyDiscovered < 0
      || gate.pending !== counts.pending + newlyDiscovered
      || ['evaluated', 'relevant', 'excluded', 'uncertain'].some(key => gate[key] !== counts[key])) fail();
  } else if (archive.policy.selection_gate.version === 2) fail();
  const selections = new Map();
  for (const entry of manifest.selections) {
    if (!exact(entry, ['candidate_id', 'source_url', 'publisher', 'published_date', 'selection'])
      || typeof entry.candidate_id !== 'string' || !/^[a-f0-9]{64}$/.test(entry.candidate_id)
      || !safeSource(entry.source_url) || entry.source_url.length > 4096 || new URL(entry.source_url).hash
      || createHash('sha256').update(entry.source_url).digest('hex') !== entry.candidate_id
      || typeof entry.publisher !== 'string' || entry.publisher.trim().length < 3 || entry.publisher.length > 200 || !day(entry.published_date)
      || selections.has(entry.candidate_id)) fail();
    const selection = validateNewsSelection(entry.selection);
    if (selection.method !== 'gemini_grounded_scope' || Date.parse(selection.classified_at) > Date.parse(manifest.generated_at)
      || selection.classified_at.slice(0, 10) < entry.published_date) fail();
    selections.set(entry.candidate_id, entry);
  }
  for (const reference of archive.records) {
    const original = allApproved.get(reference.id);
    const dossier = original ? validatePublishedNewsDossier(original) : undefined;
    if (!isSelectedNewsReference(reference, dossier) || (dossier && !bound(dossier, reference))) fail();
    if (dossier) {
      const expected = { method: 'editorial_scope', review_status: 'editorially_reviewed', human_reviewed: true,
        jurisdiction: 'SV', relevance: 'relevant', reviewed_by: dossier.reviewed_by, reviewed_at: dossier.reviewed_at };
      try { assert.deepEqual(reference.selection, expected); } catch { fail(); }
    } else {
      const entry = selections.get(reference.id);
      if (reference.selection.method !== 'gemini_grounded_scope' || !entry || !bound(entry, reference)) fail();
      try { assert.deepEqual(reference.selection, entry.selection); } catch { fail(); }
    }
  }
  return archive;
}
