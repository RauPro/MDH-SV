import { validatePublishedNewsDossier, type NewsDossier } from '../../../shared/news-dossier.mjs';
import { REPOSITORY_URL } from './reporting';

/** A private generated draft must never unlock fields merely by claiming generation. */
export function automaticPublicDossier(value: NewsDossier): NewsDossier | null {
  if (value.schema_version !== 2 || value.human_reviewed) return null;
  try { return validatePublishedNewsDossier(value); } catch { return null; }
}

/** Public corrections carry only the stable identifier and publication version. */
export function newsCorrectionUrl(dossier: NewsDossier): string {
  const url = new URL(`${REPOSITORY_URL}/issues/new`);
  const automatic = automaticPublicDossier(dossier);
  const version = automatic?.automation
    ? `${automatic.automation.policy_version}:${automatic.automation.source_content_sha256}:${automatic.automation.validated_at}`
    : dossier.human_reviewed ? `editorial:${dossier.reviewed_at}` : 'unpublished';
  url.searchParams.set('template', 'news-correction.yml');
  url.searchParams.set('title', `Corrección de noticia ${dossier.candidate_id.slice(0, 12)}`);
  url.searchParams.set('candidate_id', dossier.candidate_id);
  url.searchParams.set('publication_version', version);
  return url.href;
}
