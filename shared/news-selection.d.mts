export const NEWS_SELECTION_PROMPTS: readonly ['mdh-universal-news-grounded-8-relevance-scope', 'mdh-universal-news-grounded-9-inclusive-context', 'mdh-news-scope-1', 'mdh-news-scope-2-inclusive-context'];
export type NewsSelection = {
  method: 'gemini_grounded_scope'; review_status: 'automated_unreviewed'; human_reviewed: false; jurisdiction: 'SV'; relevance: 'relevant';
  classified_at: string; model: string; prompt_version: typeof NEWS_SELECTION_PROMPTS[number];
  evidence: { content_sha256: string; jurisdiction_paragraph_ids: string[]; relevance_paragraph_ids: string[] };
} | {
  method: 'editorial_scope'; review_status: 'editorially_reviewed'; human_reviewed: true; jurisdiction: 'SV'; relevance: 'relevant'; reviewed_by: string; reviewed_at: string;
};
export type NewsSelectionGate = { scope: 'human_rights_el_salvador'; candidates: number; selected: number } & (
  | { version: 1 }
  | { version: 2; evaluated: number; relevant: number; excluded: number; uncertain: number; pending: number; evaluated_at: string }
);
export function validateNewsSelection(value: unknown): NewsSelection;
export function isSelectedNewsReference(reference: unknown, dossier?: { human_reviewed: boolean; classification: { jurisdiction: string; relevance: string } }): boolean;
export function assertSelectedNewsArchive<T>(payload: T): T;
