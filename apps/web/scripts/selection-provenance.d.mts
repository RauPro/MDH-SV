import type { NewsDossier } from '../../../shared/news-dossier.mjs';
export function assertSelectionProvenance<T>(archive: T, manifest: unknown, allApproved?: Map<string, NewsDossier>): T;
