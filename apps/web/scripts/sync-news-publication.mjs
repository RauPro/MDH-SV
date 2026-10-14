import { lstatSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { newsDossierFor, parsePublishedNewsDossiers, validateDossierBindings } from '../../../shared/news-dossier.mjs';
import { newsLocationFor, parsePublishedNewsLocations, validateLocationBindings } from '../../../shared/news-location.mjs';
import { assertSelectedNewsArchive, isSelectedNewsReference } from '../../../shared/news-selection.mjs';
import { parseNewsExceptions } from '../../../shared/news-exceptions.mjs';

/** Reconcile safe public projections only; never read drafts or create evidence. */
export function reconcileNewsPublication({ archive, automated, reviewed, locations, exceptions }, now = new Date().toISOString()) {
  assertSelectedNewsArchive(archive);
  const human = parsePublishedNewsDossiers(reviewed);
  const automatic = parsePublishedNewsDossiers(automated);
  const geographic = parsePublishedNewsLocations(locations);
  const decisions = parseNewsExceptions(exceptions);
  if ([...human.values()].some(value => !value.human_reviewed)
    || [...automatic.values()].some(value => value.schema_version !== 2)) throw new Error('Mixed publication provenance');
  const byId = new Map(archive.records.map(reference => [reference.id, reference]));
  if (byId.size !== archive.records.length) throw new Error('Duplicate news reference');
  const eligible = archive.records.filter(reference => !decisions.get(reference.id)?.withdrawn
    && isSelectedNewsReference(reference, human.get(reference.id)));
  const autoKept = new Map();
  const merged = new Map();
  const locationKept = new Map();
  for (const reference of eligible) {
    // Any human decision supersedes the automatic source as well as its overlay:
    // otherwise earlier automatic values would remain exposed in the repository.
    const humanDossier = human.has(reference.id) ? newsDossierFor(reference, human) : undefined;
    const autoDossier = !human.has(reference.id) ? newsDossierFor(reference, automatic) : undefined;
    if (humanDossier?.human_reviewed) merged.set(reference.id, humanDossier);
    else if (autoDossier?.schema_version === 2) {
      autoKept.set(reference.id, autoDossier);
      merged.set(reference.id, autoDossier);
    }
    const dossier = merged.get(reference.id);
    if (!human.has(reference.id) && dossier?.fields.department.status !== 'withheld') {
      const location = newsLocationFor(reference, geographic);
      if (location) locationKept.set(reference.id, location);
    }
  }
  validateDossierBindings(merged, eligible);
  validateLocationBindings(locationKept, eligible);
  const sorted = index => [...index.values()].sort((a, b) => a.candidate_id.localeCompare(b.candidate_id));
  const afterLocations = { ...locations, locations: sorted(locationKept) };
  // Preserve the timestamp on a no-op, including an ordering-only normalization.
  if (locationKept.size !== geographic.size) afterLocations.generated_at = now;
  parsePublishedNewsLocations(afterLocations);
  return { automated: { schema_version: 1, dossiers: sorted(autoKept) },
    published: { schema_version: 1, dossiers: sorted(merged) }, locations: afterLocations };
}

function readSafe(path) {
  const info = lstatSync(path);
  if (!info.isFile() || info.isSymbolicLink() || info.size > 64 * 1024 * 1024) throw new Error('Unsafe public projection path');
  return JSON.parse(readFileSync(path, 'utf8'));
}

function writeAtomically(path, value) {
  const text = `${JSON.stringify(value, null, 2)}\n`;
  if (readFileSync(path, 'utf8') === text) return;
  const temporary = new URL(`.${path.pathname.split('/').at(-1)}.${randomUUID()}.tmp`, path);
  try {
    writeFileSync(temporary, text, { flag: 'wx', mode: 0o644 });
    renameSync(temporary, path);
  } finally { rmSync(temporary, { force: true }); }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const paths = {
    archive: new URL('../public/archive/news.json', import.meta.url),
    automated: new URL('../../../data/automated/news_dossiers.json', import.meta.url),
    reviewed: new URL('../../../data/reviewed/news_dossiers.json', import.meta.url),
    locations: new URL('../public/archive/locations.json', import.meta.url),
    exceptions: new URL('../../../data/selection/news_exceptions.json', import.meta.url),
    published: new URL('../public/archive/dossiers.json', import.meta.url),
  };
  const input = Object.fromEntries(Object.entries(paths).map(([name, path]) => [name, readSafe(path)]));
  parsePublishedNewsDossiers(input.published);
  const after = reconcileNewsPublication(input);
  // Validate everything before the first write. Each file replacement is atomic;
  // the workflow publishes all related replacements in a single Git commit.
  for (const name of ['automated', 'published', 'locations']) writeAtomically(paths[name], after[name]);
  console.log(`Retained ${after.automated.dossiers.length} automatic dossiers, ${after.published.dossiers.length} public dossiers and ${after.locations.locations.length} locations.`);
}
