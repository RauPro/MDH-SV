"""Private, bounded audit requests that keep disputed news out of final exclusion.

These receipts contain identifiers and hashes only. They neither classify news nor
call a model. An audited corrupt source body stays blocked even after a different
body has resolved its recheck, so reverting the publisher page cannot revive it.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from .news_scope_policy import CURRENT_SCOPE_PROMPTS
from .news_selection import validate_scope_review

MAX_REQUESTS = 50_000
MAX_BYTES = 20_000_000
MAX_ISSUE_NUMBER = 2_147_483_647
_HEX = re.compile(r"[a-f0-9]{64}")
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})")
REASON_PRIORITY = {
    "source_content_mismatch": 0,
    "confirmed_false_exclusion": 1,
    "suspected_false_exclusion": 2,
    "classification_error_closure_defensible": 3,
    "policy_mismatch": 4,
    "uncertain_review": 5,
}
_REQUEST_KEYS = {
    "candidate_id", "source_content_sha256", "issue_number", "reason_code", "requested_at",
}


def _fail():
    raise ValueError("invalid_news_scope_rechecks")


def _time(value):
    if not isinstance(value, str) or not _STAMP.fullmatch(value):
        _fail()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, OverflowError):
        _fail()


def _now(value):
    value = value if value is not None else datetime.now(timezone.utc)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            _fail()
        value = value.isoformat()
    return _time(value)


def validate_rechecks(document, *, now=None):
    """Return a defensive copy; one audit request per candidate and issue."""
    if (not isinstance(document, dict) or set(document) != {"schema_version", "requests"}
            or type(document["schema_version"]) is not int or document["schema_version"] != 1
            or not isinstance(document["requests"], list)
            or len(document["requests"]) > MAX_REQUESTS):
        _fail()
    instant, identifiers, issues = _now(now), set(), set()
    for item in document["requests"]:
        if (not isinstance(item, dict) or set(item) != _REQUEST_KEYS
                or not isinstance(item["candidate_id"], str)
                or not _HEX.fullmatch(item["candidate_id"])
                or item["candidate_id"] in identifiers
                or not isinstance(item["source_content_sha256"], str)
                or not _HEX.fullmatch(item["source_content_sha256"])
                or type(item["issue_number"]) is not int
                or not 1 <= item["issue_number"] <= MAX_ISSUE_NUMBER
                or item["issue_number"] in issues
                or not isinstance(item["reason_code"], str)
                or item["reason_code"] not in REASON_PRIORITY
                or _time(item["requested_at"]) > instant):
            _fail()
        identifiers.add(item["candidate_id"])
        issues.add(item["issue_number"])
    return deepcopy(document)


def load_rechecks(path, *, now=None):
    """Missing audit receipts mean no requests; malformed or symlink inputs fail."""
    path = Path(path)
    if path.is_symlink() or path.parent.is_symlink():
        _fail()
    if not path.exists():
        return {"schema_version": 1, "requests": []}
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_BYTES:
        _fail()

    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                _fail()
            value[key] = item
        return value

    def invalid_constant(_):
        _fail()

    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            _fail()
        document = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_constant)
    except (ValueError, UnicodeDecodeError):
        _fail()
    return validate_rechecks(document, now=now)


def _resolves(request, review, instant):
    """A stale, malformed or old-policy assessment cannot clear an audit request."""
    try:
        if (review["schema_version"] != 2
                or review["candidate_id"] != request["candidate_id"]
                or review["extraction"]["prompt_version"] not in CURRENT_SCOPE_PROMPTS):
            return False
        reference = {"id": request["candidate_id"], **{
            key: review[key] for key in ("source_url", "publisher", "published_date")}}
        checked = validate_scope_review(review, reference=reference,
            content_sha256=review["evidence"]["content_sha256"], now=instant)
        requested = _time(request["requested_at"])
        if (checked["source_read"]["truncated"]
                or _time(checked["extraction"]["prepared_at"]) <= requested
                or _time(checked["source_read"]["fetched_at"]) <= requested):
            return False
        return (request["reason_code"] != "source_content_mismatch"
                or checked["evidence"]["content_sha256"] != request["source_content_sha256"])
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def pending_recheck_ids(document, scope_reviews, *, now=None):
    """Return unresolved requests; invalid or duplicate assessments fail closed.

    A fresh uncertain classification completes the audit request, but remains an
    uncertain news item under the normal selection and issue lifecycle rules.
    """
    instant = _now(now)
    requests = validate_rechecks(document, now=instant)["requests"]
    if not isinstance(scope_reviews, (list, tuple)) or len(scope_reviews) > 100_000:
        _fail()
    reviews, ambiguous = {}, set()
    for review in scope_reviews:
        if not isinstance(review, dict):
            continue
        identifier = review.get("candidate_id")
        if not isinstance(identifier, str):
            continue
        if identifier in reviews:
            ambiguous.add(identifier)
        reviews[identifier] = review
    return {item["candidate_id"] for item in requests
            if item["candidate_id"] in ambiguous
            or not _resolves(item, reviews.get(item["candidate_id"], {}), instant)}


def priority_recheck_ids(document, scope_reviews, *, now=None):
    """Stable priority order for unresolved requests, without spending model quota."""
    pending = pending_recheck_ids(document, scope_reviews, now=now)
    return [item["candidate_id"] for item in sorted(document["requests"], key=lambda item: (
        REASON_PRIORITY[item["reason_code"]], _time(item["requested_at"]), item["issue_number"]))
        if item["candidate_id"] in pending]


def blocked_source_hashes(document, *, now=None):
    """Known inconsistent bodies must never be passed back to a classifier."""
    return {item["candidate_id"]: item["source_content_sha256"]
            for item in validate_rechecks(document, now=now)["requests"]
            if item["reason_code"] == "source_content_mismatch"}
