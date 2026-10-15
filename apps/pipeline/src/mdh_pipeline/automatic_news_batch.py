"""Source-isolated automatic assessments sharing one existing Gemini reservation.

The provider transports several assessments in one response; each assessment
still obeys the single-dossier policy. No source prose or aggregate token usage
is attributed to an individual receipt. The caller owns persistence and retries.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re

import requests

from .automatic_news import (
    AutomaticNewsError, PROTECTED_TITLE, SYSTEM_PROMPT, _bound_draft,
    _decode_judgment, _privacy_projection, _selection_hash, project_dossier,
    response_schema,
)
from .gemini_budget import TOKEN_FRAMING_ALLOWANCE, estimate_input_tokens
from .gemini_draft import GeminiDraftError, _input, _request, _single_json

MAX_BATCH_SIZE = 4
MAX_BATCH_INPUT_TOKENS = 90_000
RECOVERABLE_PARSE_CODES = frozenset({
    "invalid_automatic_batch_response", "automatic_batch_response_incomplete",
})
BATCH_INSTRUCTIONS = """
Evalúa cada elemento de assessments_input de forma independiente. Devuelve exactamente una
evaluación por candidate_id, copiando su source_content_sha256 sin modificarlo. No cruces hechos,
personas, ubicaciones, privacidad ni evidencias entre artículos. Cada artículo tiene un espacio
de párrafos exclusivo (n1-p1, n2-p1, etc.); paragraph_ids debe usar exclusivamente los identificadores
del artículo de esa evaluación, que también aparecen en evidence_refs. Conserva el prefijo nN-.
No añadas ni omitas artículos. Las evaluaciones de campos pueden ser parciales: un campo omitido
se conservará pendiente. Devuelve únicamente el objeto JSON assessments con las evaluaciones.
"""


class AutoBatchError(AutomaticNewsError):
    def __init__(self, code="invalid_automatic_batch_response"):
        self.recoverable = code in RECOVERABLE_PARSE_CODES
        super().__init__(code)


def batch_input_limit(budget):
    """Leave minute-ledger headroom without claiming a provider token count."""
    ceiling = getattr(getattr(budget, "config", None), "input_tpm", None)
    if type(ceiling) is not int or ceiling <= TOKEN_FRAMING_ALLOWANCE:
        raise AutoBatchError("invalid_automatic_provider_configuration")
    return min(MAX_BATCH_INPUT_TOKENS, ceiling - TOKEN_FRAMING_ALLOWANCE)


def batch_response_schema():
    # Use the same live-tested schema subset as the single-dossier evaluator.
    # Exact keys, cardinality, identities and references are enforced locally.
    return {"type": "object", "properties": {
        "assessments": {"type": "array", "items": {
            "type": "object", "properties": {
                "candidate_id": {"type": "string"},
                "source_content_sha256": {"type": "string"},
                "judgment": response_schema(),
            }, "required": ["candidate_id", "source_content_sha256", "judgment"],
        }},
    }, "required": ["assessments"]}


def prepare_evaluation_batch(items, *, max_input_tokens=MAX_BATCH_INPUT_TOKENS):
    """Validate and bind 1–4 full articles; never truncate to make a batch fit.

    Each item has ``document``, ``reference`` and ``article``. Returns private,
    in-memory prepared rows and the complete provider payload. A too-large batch
    raises before any reservation or HTTP call, allowing the caller to split it.
    """
    if (not isinstance(items, (list, tuple)) or not 1 <= len(items) <= MAX_BATCH_SIZE
            or type(max_input_tokens) is not int
            or not 1 <= max_input_tokens <= MAX_BATCH_INPUT_TOKENS):
        raise AutoBatchError("invalid_automatic_batch_input")
    prepared, wire, seen = [], [], set()
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict) or set(item) != {"document", "reference", "article"}:
            raise AutoBatchError("invalid_automatic_batch_input")
        document, reference, article = (deepcopy(item[key]) for key in ("document", "reference", "article"))
        original = _bound_draft(document, reference)
        _, paragraphs = _input(reference, article)
        digest, identifier = article["content_sha256"], reference["id"]
        if digest != original["extraction"]["content_sha256"] or digest != _selection_hash(reference):
            raise AutoBatchError("automatic_source_changed")
        if identifier in seen:
            raise AutoBatchError("invalid_automatic_batch_input")
        seen.add(identifier)
        # article_source constructs its body from these exact paragraphs. This
        # also rejects a caller supplying hidden, omitted or reordered text.
        if article["text"] != "\n\n".join(paragraphs.values()):
            raise AutoBatchError("invalid_automatic_batch_article")
        namespace = f"n{index}-"
        safe = _privacy_projection(original, paragraphs)
        fields = {name: {"value": deepcopy(field["value"]), "status": field["status"],
                         "evidence_refs": [namespace + key.removeprefix("article-")
                                           for key in field["evidence_refs"]]}
                  for name, field in safe["fields"].items()}
        wire.append({"candidate_id": identifier, "source_content_sha256": digest,
            "untrusted_article_data": {
                "paragraphs": [{"id": namespace + key, "text": text} for key, text in paragraphs.items()],
                "truncated": bool(article.get("truncated")), "published_date": reference["published_date"],
            }, "untrusted_dossier": {"classification": safe["classification"], "fields": fields,
                                     "source_title": safe["source_title"]}})
        prepared.append({"document": original, "reference": reference, "article": article,
                         "paragraphs": paragraphs, "namespace": namespace})
    payload = {"systemInstruction": {"parts": [{"text": SYSTEM_PROMPT + BATCH_INSTRUCTIONS}]},
        "contents": [{"role": "user", "parts": [{"text": json.dumps(
            {"assessments_input": wire}, ensure_ascii=False, separators=(",", ":"), allow_nan=False)}]}],
        "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": batch_response_schema(),
                             "candidateCount": 1, "temperature": 0,
                             "maxOutputTokens": 8192 if len(prepared) > 1 else 4096}}
    if estimate_input_tokens(payload) > max_input_tokens:
        raise AutoBatchError("automatic_batch_input_too_large")
    return prepared, payload


def _decode_response(raw, prepared):
    """Reject the complete response before projecting even its first article."""
    try:
        if raw.get("promptFeedback", {}).get("blockReason"):
            raise AutoBatchError("automatic_provider_blocked")
        candidates = raw.get("candidates", [])
        if (not isinstance(candidates, list) or len(candidates) != 1
                or candidates[0].get("finishReason") != "STOP"):
            raise AutoBatchError("automatic_batch_response_incomplete")
        parts = candidates[0]["content"]["parts"]
        if not isinstance(parts, list) or len(parts) > 8:
            raise AutoBatchError()
        visible = []
        for part in parts:
            if (not isinstance(part, dict) or not set(part) <= {"text", "thought", "thoughtSignature"}
                    or not isinstance(part.get("text"), str)
                    or ("thought" in part and type(part["thought"]) is not bool)
                    or ("thoughtSignature" in part and not isinstance(part["thoughtSignature"], str))):
                raise AutoBatchError()
            if part.get("thought") is not True:
                visible.append(part["text"])
        if len(visible) != 1:
            raise AutoBatchError()
        envelope = _single_json(visible[0])
        if (not isinstance(envelope, dict) or set(envelope) != {"assessments"}
                or not isinstance(envelope["assessments"], list)
                or len(envelope["assessments"]) != len(prepared)):
            raise AutoBatchError()
        expected = {item["reference"]["id"]: item for item in prepared}
        decoded = {}
        for assessment in envelope["assessments"]:
            if (not isinstance(assessment, dict)
                    or set(assessment) != {"candidate_id", "source_content_sha256", "judgment"}
                    or not isinstance(assessment["candidate_id"], str)
                    or assessment["candidate_id"] not in expected or assessment["candidate_id"] in decoded):
                raise AutoBatchError()
            identifier = assessment["candidate_id"]
            row = expected[identifier]
            if assessment["source_content_sha256"] != row["article"]["content_sha256"]:
                raise AutoBatchError()
            value = deepcopy(assessment["judgment"])
            # Never repair another article's prefix or accept bare pN aliases.
            checks = [value["jurisdiction"], value["relevance"], *value["fields"]]
            for check in checks:
                refs = check["paragraph_ids"]
                if not isinstance(refs, list):
                    raise AutoBatchError()
                restored = []
                for paragraph in refs:
                    if not isinstance(paragraph, str) or not paragraph.startswith(row["namespace"]):
                        raise AutoBatchError()
                    canonical = paragraph[len(row["namespace"]):]
                    if canonical not in row["paragraphs"]:
                        raise AutoBatchError()
                    restored.append(canonical)
                check["paragraph_ids"] = restored
            decoded[identifier] = _decode_judgment(value, row["paragraphs"])
        if set(decoded) != set(expected):
            raise AutoBatchError()
        return decoded
    except AutoBatchError:
        raise
    except (AutomaticNewsError, GeminiDraftError, KeyError, TypeError, AttributeError, ValueError):
        raise AutoBatchError() from None


def evaluate_batch(items, *, api_key, model, budget, session=None, now=None):
    """Evaluate a batch using the caller's shared ledger and existing model.

    All HTTP attempts, including provider retries, go through ``_request`` and
    its durable request/input reservation. Individual receipts deliberately
    carry ``usage=None`` rather than fabricated shares of provider token usage.
    """
    if (budget is None or model != budget.config.model or not isinstance(api_key, str)
            or not 10 <= len(api_key) <= 512 or re.search(r"\s", api_key)):
        raise AutoBatchError("invalid_automatic_provider_configuration")
    prepared, payload = prepare_evaluation_batch(items, max_input_tokens=batch_input_limit(budget))
    owned = session is None
    session = session or requests.Session()
    try:
        try:
            raw = _request(payload, api_key=api_key, model=model, session=session, budget=budget)
        except GeminiDraftError as error:
            if error.code == "invalid_json_response":
                raise AutoBatchError() from None
            raise
        judgments = _decode_response(raw, prepared)
    finally:
        if owned:
            session.close()
    results = {}
    for row in prepared:
        identifier = row["reference"]["id"]
        judgment = judgments[identifier]
        safe = _privacy_projection(row["document"], row["paragraphs"], protected=judgment["protected_fields"])
        protected = set(judgment["protected_fields"])
        protected.update(name for name, field in safe["fields"].items() if field["status"] == "withheld")
        if safe["source_title"] == PROTECTED_TITLE:
            protected.add("source_title")
        judgment["protected_fields"] = sorted(protected)
        public = project_dossier(safe, judgment, source_hash=row["article"]["content_sha256"], model=model,
                                 truncated=bool(row["article"].get("truncated")), now=now)
        results[identifier] = {"judgment": judgment, "dossier": public, "usage": None}
    return results
