"""Evidence-only terminal states for news issues; never fetch or call a model.

An absent public record is not a rejection. A classification is not publication.
This projection deliberately leaves conflicting or incomplete snapshots pending.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from .news_publication import validate_collection
from .news_scope import SCOPE_PROMPT
from .news_selection import (
    MAX_SELECTIONS, _archive_index, _bound, _category, _decision,
    _dossier_decision, _excluded_set, _timestamp, project_news_selection,
    validate_scope_audits, validate_scope_review,
)

_IDENTITY = ("source_url", "publisher", "published_date")
_ERRORS = (ValueError, TypeError, KeyError, AttributeError)
_LEGACY_EXTRACTION_PROMPTS = {
    "mdh-private-grounded-5", "mdh-universal-news-grounded-6",
    "mdh-universal-news-grounded-7-field-privacy",
}


def candidate_matches_terminal(candidate, evidence):
    """Require the exact bibliographic identity; title is intentionally excluded."""
    return (isinstance(candidate, dict) and isinstance(evidence, dict)
            and candidate.get("id") == evidence.get("candidate_id")
            and isinstance(candidate.get("id"), str)
            and len(candidate["id"]) == 64
            and all(isinstance(evidence.get(key), str) for key in _IDENTITY)
            and all(candidate.get(key) == evidence.get(key) for key in _IDENTITY))


def _group(values):
    grouped = defaultdict(list)
    for count, value in enumerate(values, 1):
        if count > MAX_SELECTIONS:
            raise ValueError("Too many news issue evidence records")
        if isinstance(value, dict) and isinstance(value.get("candidate_id"), str):
            grouped[value["candidate_id"]].append(value)
    return grouped


def _evidence(reference, status, reason, digest, stamp):
    return {"candidate_id": reference["id"], "status": status, "reason": reason,
            **{key: reference[key] for key in _IDENTITY},
            "content_sha256": digest, "evidence_at": stamp}


def _legacy_published_draft(original, reference, dossier, assessment, instant):
    """A current publication can validate a legacy extraction's exact text.

    Older extraction prompts are not current scope evidence and cannot close a
    rejection or reopen an issue. The current dedicated classification and the
    independently validated production dossier must agree on this exact source.
    """
    from .automatic_news import _bound_draft
    if dossier is None or dossier["schema_version"] != 2 or assessment is None:
        return False
    document = _bound_draft(original, reference)
    extraction, news = document["extraction"], document["news_dossier"]
    digest = dossier["automation"]["source_content_sha256"]
    return (extraction.get("prompt_version") in _LEGACY_EXTRACTION_PROMPTS
            and extraction.get("provider") == "gemini"
            and extraction["content_sha256"] == digest
            and document.get("classification") == news["classification"]
            and _category(news["classification"]) == "relevant"
            and _timestamp(extraction["fetched_at"]) <= _timestamp(extraction["prepared_at"])
            <= _timestamp(dossier["automation"]["validated_at"]) <= instant
            and assessment["extraction"]["prompt_version"] == SCOPE_PROMPT
            and assessment.get("source_read", {}).get("truncated") is False
            and assessment["evidence"]["content_sha256"] == digest
            and _category(assessment["classification"]) == "relevant")


def build_terminal_index(**kwargs):
    """Return terminal evidence; missing IDs do not imply a safe reopen."""
    return build_issue_state_indexes(**kwargs)[0]


def build_reopen_index(**kwargs):
    """Return explicit valid pending evidence, independently of terminal absence."""
    return build_issue_state_indexes(**kwargs)[1]


def build_issue_state_indexes(*, archive, published_archive, published_dossiers,
                              scope_reviews=(), drafts=(), scope_audits=None,
                              excluded_ids=(), now=None):
    """Return ``(terminal, reopen)`` source-bound evidence indexes.

    ``published_*`` must be obtained from the deployed production snapshot, not
    a prospective Git export. Broken public collections fail as a unit. Broken
    or duplicate private evidence affects only its candidate. Withdrawals remain
    outside this projection; they need their explicit editorial handling.
    """
    instant = now or datetime.now(timezone.utc)
    instant = _timestamp(instant.isoformat() if isinstance(instant, datetime) else instant)
    references = _archive_index(archive)
    public_references = _archive_index(published_archive)
    published = validate_collection(published_dossiers, archive=published_archive,
                                    allow_automatic=True)["dossiers"]
    excluded = _excluded_set(excluded_ids)
    audits = validate_scope_audits(scope_audits if scope_audits is not None else
        {"schema_version": 1, "audits": []}, archive, now=instant)["audits"]
    audit_groups, scope_groups, draft_groups = map(_group, (audits, scope_reviews, drafts))
    public = {item["candidate_id"]: item for item in published}
    public_selected = {item["id"]: item["selection"] for item in published_archive["records"]
                       if "selection" in item}
    bibliography_selected = {item["id"]: item["selection"] for item in archive["records"]
                             if "selection" in item}
    result, reopen = {}, {}
    for identifier in (public.keys() | scope_groups.keys()) - excluded:
        reference = references.get(identifier)
        if reference is None:
            continue
        dossier = public.get(identifier)
        if dossier is not None:
            deployed_reference = public_references.get(identifier)
            if (not _bound(dossier, reference) or deployed_reference is None
                    or any(reference[key] != deployed_reference[key] for key in _IDENTITY)
                    or _category(dossier["classification"]) != "relevant"):
                continue
            # An actual editorial publication overrides automated assessments,
            # including an older negative or a failed model retry. Explicit
            # withdrawal/rejection belongs in excluded_ids or the issue guard.
            if dossier["schema_version"] == 1:
                if dossier["reviewed_at"] <= instant.date().isoformat():
                    result[identifier] = _evidence(reference, "published", "published_reviewed",
                                                    None, dossier["reviewed_at"])
                continue
        scope_rows, draft_rows = scope_groups.get(identifier, []), draft_groups.get(identifier, [])
        if len(scope_rows) > 1 or len(draft_rows) > 1:
            continue
        assessment, scope_decision, draft_decision = None, None, None
        try:
            if scope_rows:
                assessment = validate_scope_review(scope_rows[0], reference=reference,
                    content_sha256=scope_rows[0]["evidence"]["content_sha256"], now=instant)
                scope_decision = _decision(assessment["classification"], assessment["extraction"],
                    assessment["evidence"], fetched_at=assessment.get("source_read", {}).get("fetched_at"),
                    legacy=assessment["schema_version"] == 1)
            selection_drafts = draft_rows
            if draft_rows:
                try:
                    draft_decision = _dossier_decision(draft_rows[0], reference, instant)
                except _ERRORS:
                    if not _legacy_published_draft(draft_rows[0], reference, dossier, assessment, instant):
                        raise
                    selection_drafts = []
            # Reuse selection precedence, including current text, source audits,
            # and disagreements between snapshots. Never classify from a title.
            manifest = project_news_selection(selection_drafts, {"schema_version": 1, "records": [reference]},
                now=instant, scope_reviews=scope_rows,
                scope_audits={"schema_version": 1, "audits": audit_groups.get(identifier, [])})
        except _ERRORS:
            continue

        if dossier is not None:
            digest = dossier["automation"]["source_content_sha256"]
            stamp = dossier["automation"]["validated_at"]
            if _timestamp(stamp) > instant:
                continue
            # A stale deployment may still contain a source now excluded or
            # replaced in the private queue. Wait for the snapshots to converge.
            current = True
            if scope_rows or draft_rows:
                selections = manifest["selections"]
                current = bool(selections and selections[0]["selection"]["evidence"]["content_sha256"] == digest)
            if any(audit["content_sha256"] == digest for audit in audit_groups.get(identifier, [])):
                current = False
            if current:
                result[identifier] = _evidence(reference, "published", "published_automatic", digest, stamp)
                continue

        # Only the current dedicated scope policy, after a complete source read,
        # may close a negative automatically. Older/narrower policies must retry.
        if (assessment is None or assessment["extraction"]["prompt_version"] != SCOPE_PROMPT
                or assessment.get("source_read", {}).get("truncated") is not False):
            continue
        if draft_decision is not None:
            same_text = draft_decision["hash"] == scope_decision["hash"]
            # Dedicated scope owns selection, but closure is more conservative:
            # retain a same-text disagreement or a newer different source read.
            if ((same_text and draft_decision["category"] != scope_decision["category"])
                    or not same_text and draft_decision["timestamp"] >= scope_decision["timestamp"]):
                continue
        category = scope_decision["category"]
        if manifest["assessment_counts"][category] != 1:
            continue
        if category == "excluded":
            if dossier is not None or identifier in public_selected or identifier in bibliography_selected:
                continue
            reason = ("outside_el_salvador" if assessment["classification"]["jurisdiction"] == "other"
                      else "not_relevant")
            result[identifier] = _evidence(reference, "discarded", reason,
                scope_decision["hash"], assessment["extraction"]["prepared_at"])
        else:
            if dossier is not None:
                # A still-deployed automatic publication is not reopened merely
                # because an old or contradictory local assessment was read.
                if (scope_decision["hash"] == dossier["automation"]["source_content_sha256"]
                        or scope_decision["timestamp"][0] <= _timestamp(dossier["automation"]["validated_at"])):
                    continue
            reason = "awaiting_publication" if category == "relevant" else "scope_unclear"
            reopen[identifier] = _evidence(reference, "pending", reason,
                scope_decision["hash"], assessment["extraction"]["prepared_at"])
    return result, reopen
