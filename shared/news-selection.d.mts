export const NEWS_SELECTION_PROMPTS: readonly ['mdh-universal-news-grounded-8-relevance-scope', 'mdh-news-scope-1'];
export type NewsSelection = {
  method: 'gemini_grounded_scope'; review_status: 'automated_unreviewed'; human_reviewed: false; jurisdiction: 'SV'; relevance: 'relevant';
  classified_at: string; model: string; prompt_version: 'mdh-universal-news-grounded-8-relevance-scope' | 'mdh-news-scope-1';
  evidence: { content_sha256: string; jurisdiction_paragraph_ids: string[]; relevance_paragraph_ids: string[] };
} | {
  method: 'editorial_scope'; review_status: 'editorially_reviewed'; human_reviewed: true; jurisdiction: 'SV'; relevance: 'relevant'; reviewed_by: string; reviewed_at: string;
};
export interface NewsSelectionGate { version: 1; scope: 'human_rights_el_salvador'; candidates: number; selected: number }
export function validateNewsSelection(value: unknown): NewsSelection;
export function isSelectedNewsReference(reference: unknown, dossier?: { human_reviewed: boolean; classification: { jurisdiction: string; relevance: string } }): boolean;
export function assertSelectedNewsArchive<T>(payload: T): T;
