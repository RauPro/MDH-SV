"""Pure drafts contracts, extracted without changing validation or normalization."""
from __future__ import annotations

def validate_envelope(document, candidate, *, allow_legacy=False):
    if (not isinstance(document, dict) or type(document.get("schema_version")) is not int or document.get("schema_version") != 1 or document.get("candidate_id") != candidate["id"]
            or document.get("source_url") != candidate["source_url"] or document.get("publishable") is not False
            or document.get("human_reviewed") is not False
            or document.get("status") not in ("draft", "needs_scope_review", "needs_privacy_review", "pending", "source_unavailable", "error")):
        raise ValueError("invalid_draft_envelope")
    if not allow_legacy or "news_dossier" in document:
        from .dossier import validate_news_dossier
        try:
            news = validate_news_dossier(document.get("news_dossier"))
        except (ValueError, TypeError):
            raise ValueError("invalid_news_dossier") from None
        if (news.get("candidate_id") != candidate["id"] or news.get("source_url") != candidate["source_url"]
                or news.get("human_reviewed") is not False):
            raise ValueError("invalid_news_dossier_identity")
        expected = document["status"] if document["status"] in ("pending", "source_unavailable", "error") else "processed"
        if news.get("extraction_status") != expected:
            raise ValueError("invalid_news_dossier_status")
        document["news_dossier"] = news
    record = document.get("record")
    if document["status"] == "draft" and record is not None:
        from .catalog import Publication
        if (not isinstance(record, dict) or record.get("human_reviewed") is not False
                or record.get("origin_candidate_id") != candidate["id"]
                or record.get("source_url") != candidate["source_url"] or document.get("relevant") is not True
                or document.get("classification", {}).get("jurisdiction") != "SV"
                or document.get("classification", {}).get("relevance") != "relevant"):
            raise ValueError("invalid_unreviewed_record")
        # Validate the field structure without removing the publication guard from the stored draft.
        try:
            Publication.model_validate({**record, "origin_candidate_id": None})
        except ValueError:
            raise ValueError("invalid_unreviewed_record_fields") from None
    elif record is not None:
        raise ValueError("unsafe_scope_or_privacy_record")

def complete_document(document):
    return document.get("news_dossier", {}).get("extraction_status") == "processed"
