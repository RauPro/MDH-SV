"""Move a bounded batch of historical references into the private editorial inbox.

This command has no public-data write path. It reuses GitHubInbox's private-repo
check and deduplication marker, so RSS collection and historical collection share
one review queue. An enqueued reference is not an approved or published case.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import deque
from pathlib import Path

from .history import ROOT, SOURCES
from .contracts.reference import validate_reference as validate_reference
from .review_queue import GitHubInbox


def fair_scope_order(items, *, scope=lambda item: item.get("scope_hint"), key=lambda item: (item.get("published_date") or "", item["id"]), offset=0):
    """Weighted rotation: four SV slots, one undetermined and one international.

    The offset advances between batches, including one-item batches, while each
    territory retains oldest-first order. Empty groups donate their slots.
    """
    groups = {name: deque() for name in ("el_salvador", "undetermined", "international_context")}
    for item in sorted(items, key=key):
        groups[scope(item) if scope(item) in groups else "undetermined"].append(item)
    slots = ("el_salvador", "el_salvador", "undetermined", "el_salvador", "el_salvador", "international_context")
    position = offset % len(slots)
    while any(groups.values()):
        group = groups[slots[position]]
        position = (position + 1) % len(slots)
        if group:
            yield group.popleft()


def enqueue_history(inbox, archive, *, limit=20, include_international=True):
    if not 1 <= limit <= 100:
        raise ValueError("Historical queue limit must be between 1 and 100")
    if archive.get("schema_version") != 1 or not isinstance(archive.get("records"), list):
        raise ValueError("Invalid bibliography schema")
    sources = {source["id"]: source for source in SOURCES}
    # Validate all incoming records before any external mutation.
    for reference in archive["records"]:
        source = sources.get(reference.get("source_id"))
        if source is None:
            raise ValueError("Unknown bibliography source")
        validate_reference(reference, source)
    inbox.ensure_labels()
    seen = inbox.existing_ids()
    added = 0
    eligible = [reference for reference in archive["records"]
                if include_international or reference["scope_hint"] != "international_context"]
    already_present = sum(reference["id"] in seen for reference in eligible)
    unseen = [reference for reference in eligible if reference["id"] not in seen]
    for reference in fair_scope_order(unseen, offset=already_present):
        if added >= limit:
            continue
        candidate = {
            "id": reference["id"], "source_url": reference["source_url"],
            "source_title": reference["title"], "publisher": reference["publisher"],
            "published_date": reference["published_date"],
            "discovered_at": reference["discovery"]["retrieved_at"],
            "matched_terms": reference["topics"], "status": "pending",
            "record_type": "news_candidate", "discovery_method": "historical_bibliography",
            "jurisdiction_status": "not_reviewed",
            "scope_hint": reference["scope_hint"], "scope_terms": reference["scope_terms"],
        }
        inbox.enqueue(candidate)
        seen.add(reference["id"])
        added += 1
    return {"references": len(archive["records"]), "eligible_references": len(eligible),
            "excluded_international": len(archive["records"]) - len(eligible),
            "already_in_review_queue": already_present,
            "added": added, "remaining": sum(r["id"] not in seen for r in eligible),
            "publications": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=ROOT / "apps/web/public/archive/news.json")
    parser.add_argument("--limit", type=int, default=20)
    inclusion = parser.add_mutually_exclusive_group()
    inclusion.add_argument("--include-international", dest="include_international", action="store_true", default=True, help="Include every geographic scope (the default)")
    inclusion.add_argument("--exclude-international", dest="include_international", action="store_false", help="Operator opt-out for international-context references")
    args = parser.parse_args()
    archive = json.loads(args.archive.read_text(encoding="utf-8"))
    inbox = GitHubInbox(os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"])
    report = enqueue_history(inbox, archive, limit=args.limit, include_international=args.include_international)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
            summary.write("## Revisión del archivo histórico\n\n```json\n" + json.dumps(report, indent=2) + "\n```\n")


if __name__ == "__main__":
    main()
