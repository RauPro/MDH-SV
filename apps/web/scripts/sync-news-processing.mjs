import { pathToFileURL } from 'node:url';
import { publicDataRoot, readPublicJson, writePublicJson } from './public-data-root.mjs';
import { parseNewsProcessing, processingMatchesReference } from '../../../shared/news-processing.mjs';

/** A public-only refresh may remove stale receipts, never invent private progress. */
export function reconcileNewsProcessing(payload, references, now = new Date().toISOString()) {
  const index = parseNewsProcessing(payload);
  const byId = new Map(references.map(reference => [reference.id, reference]));
  if (byId.size !== references.length) throw new Error('Duplicate news reference');
  const entries = [...index.values()].filter(entry => processingMatchesReference(entry, byId.get(entry.candidate_id)));
  if (entries.length === index.size) return structuredClone(payload);
  return { schema_version: 1, generated_at: now, entries };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const data = publicDataRoot();
  const location = data.path('apps/web/public/archive/processing.json');
  const before = readPublicJson(location);
  const archive = readPublicJson(data.path('apps/web/public/archive/news.json'));
  const after = reconcileNewsProcessing(before, archive.records);
  if (JSON.stringify(before) !== JSON.stringify(after)) writePublicJson(location, after);
  console.log(`Retained ${after.entries.length} matching public processing receipts; missing statuses remain unknown.`);
}
