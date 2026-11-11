"""Approved zero-cost model providers and their public provenance vocabulary.

Gemini keeps its original public shape. Every other provider must be named in
the published decision, with a model identifier bounded by that provider's
pattern. Paid-only providers are deliberately absent, and OpenRouter admits only
its free router or explicit ``:free`` variants. A valid identifier is a data
binding; it does not prove that a model exists or that its output is correct.
"""
from __future__ import annotations

import re

GEMINI = "gemini"
_SEGMENT = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,44}"
_MODEL_ID = rf"{_SEGMENT}(?:/{_SEGMENT})?"
MODEL_PATTERNS = {
    GEMINI: r"gemini-[A-Za-z0-9_.-]{1,90}",
    "groq": _MODEL_ID,
    "nvidia": _MODEL_ID,
    "mistral": _MODEL_ID,
    "openrouter": rf"(?:openrouter/free|{_SEGMENT}/{_SEGMENT}:free)",
}
LLM_PROVIDERS = frozenset(MODEL_PATTERNS) - {GEMINI}

GEMINI_SCOPE_METHOD = "gemini_grounded_scope"
LLM_SCOPE_METHOD = "llm_grounded_scope"
AUTOMATED_SCOPE_METHODS = frozenset({GEMINI_SCOPE_METHOD, LLM_SCOPE_METHOD})
GEMINI_REVIEW_METHOD = "gemini_evidence_review"
LLM_REVIEW_METHOD = "llm_evidence_review"
SERVED_BY = "_served_by"


def valid_model(provider, model):
    """True only for an approved provider and a model identifier in its own pattern."""
    pattern = MODEL_PATTERNS.get(provider) if isinstance(provider, str) else None
    return pattern is not None and isinstance(model, str) and re.fullmatch(pattern, model) is not None


def scope_method(provider):
    return GEMINI_SCOPE_METHOD if provider == GEMINI else LLM_SCOPE_METHOD


def review_method(provider):
    return GEMINI_REVIEW_METHOD if provider == GEMINI else LLM_REVIEW_METHOD
