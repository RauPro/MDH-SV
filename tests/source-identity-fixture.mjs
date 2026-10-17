// Independent Node crypto oracle for synthetic public URLs in Node/Vitest tests.
import { createHash } from 'node:crypto';

export const sourceId = url => createHash('sha256').update(url).digest('hex');

/** Recount a synthetic selected archive after changing its public references. */
export function recountArchive(archive) {
  const records = archive.records;
  archive.coverage.records = records.length;
  const gate = archive.policy.selection_gate;
  const candidates = Math.max(gate.candidates, records.length);
  if (gate.version === 2) gate.pending += candidates - gate.candidates;
  gate.candidates = candidates;
  gate.selected = records.length;
  for (const source of archive.coverage.sources) {
    const matches = records.filter(row => row.source_id === source.id);
    const dates = matches.map(row => row.published_date).sort();
    Object.assign(source, { records: matches.length, first_published_date: dates[0] ?? null,
      last_published_date: dates.at(-1) ?? null });
    for (const month of source.months) month.records = matches.filter(row => row.published_date.startsWith(month.month)).length;
  }
  for (const month of archive.coverage.months) {
    const matches = records.filter(row => row.published_date.startsWith(month.month));
    month.records = matches.length;
    for (const source of month.sources) source.records = matches.filter(row => row.source_id === source.source_id).length;
  }
  return archive;
}
