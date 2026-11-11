const segment = '[A-Za-z0-9][A-Za-z0-9_.-]{0,44}';
const modelId = `${segment}(?:/${segment})?`;
export const GEMINI_MODEL_PATTERN = 'gemini-[A-Za-z0-9_.-]{1,90}';
export const LLM_MODEL_PATTERNS = Object.freeze({
  groq: modelId, nvidia: modelId, mistral: modelId,
  openrouter: `(?:openrouter/free|${segment}/${segment}:free)`,
});
export const LLM_PROVIDERS = Object.freeze(Object.keys(LLM_MODEL_PATTERNS));
export const AUTOMATED_SCOPE_METHODS = Object.freeze(['gemini_grounded_scope', 'llm_grounded_scope']);
const full = pattern => new RegExp(`^(?:${pattern})$`);
const geminiModel = full(GEMINI_MODEL_PATTERN);
const llmModels = Object.fromEntries(Object.entries(LLM_MODEL_PATTERNS).map(([provider, pattern]) => [provider, full(pattern)]));
export const validGeminiModel = model => typeof model === 'string' && geminiModel.test(model);
export const validLlmModel = (provider, model) => typeof provider === 'string' && Object.hasOwn(llmModels, provider)
  && typeof model === 'string' && llmModels[provider].test(model);
export const isAutomatedScope = selection => AUTOMATED_SCOPE_METHODS.includes(selection?.method);
