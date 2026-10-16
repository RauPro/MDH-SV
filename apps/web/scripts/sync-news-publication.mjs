import { pathToFileURL } from 'node:url';
import { publicDataRoot, readPublicJson, writePublicJson } from './public-data-root.mjs';
import { NEWS_DOSSIER_PROTECTED_TITLE, newsDossierFor, parsePublishedNewsDossiers, validateDossierBindings } from '../../../shared/news-dossier.mjs';
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
  const safeArchive = structuredClone(archive);
  for (const reference of safeArchive.records) {
    const dossier = merged.get(reference.id);
    if (dossier?.schema_version === 2 && dossier.source_title === NEWS_DOSSIER_PROTECTED_TITLE) {
      reference.title = NEWS_DOSSIER_PROTECTED_TITLE;
      reference.title_truncated = false;
    }
  }
  assertSelectedNewsArchive(safeArchive);
  return { automated: { schema_version: 1, dossiers: sorted(autoKept) },
    published: { schema_version: 1, dossiers: sorted(merged) }, locations: afterLocations, archive: safeArchive };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const data = publicDataRoot();
  const paths = {
    archive: data.path('apps/web/public/archive/news.json'),
    automated: data.path('data/automated/news_dossiers.json'),
    reviewed: data.path('data/reviewed/news_dossiers.json'),
    locations: data.path('apps/web/public/archive/locations.json'),
    exceptions: data.path('data/selection/news_exceptions.json'),
    published: data.path('apps/web/public/archive/dossiers.json'),
  };
  const input = Object.fromEntries(Object.entries(paths).map(([name, path]) => [name, readPublicJson(path)]));
  parsePublishedNewsDossiers(input.published);
  const after = reconcileNewsPublication(input);
  // Validate everything before the first write. Each file replacement is atomic;
  // the workflow publishes all related replacements in a single Git commit.
  for (const name of ['automated', 'published', 'locations', 'archive']) writePublicJson(paths[name], after[name]);
  console.log(`Retained ${after.automated.dossiers.length} automatic dossiers, ${after.published.dossiers.length} public dossiers and ${after.locations.locations.length} locations.`);
}
