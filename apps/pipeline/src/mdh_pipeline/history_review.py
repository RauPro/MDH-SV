"""Move a bounded batch of historical references into the private editorial inbox.

This command has no public-data write path. It reuses GitHubInbox's private-repo
check and deduplication marker, so RSS collection and historical collection share
one review queue. An enqueued reference is not an approved or published case.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .history import ROOT, SOURCES, validate_reference
from .review_queue import GitHubInbox


def enqueue_history(inbox, archive, *, limit=20):
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
    added, already_present = 0, 0
    for reference in sorted(archive["records"], key=lambda item: (item["published_date"], item["id"])):
        if reference["id"] in seen:
            already_present += 1
            continue
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
        }
        inbox.enqueue(candidate)
        seen.add(reference["id"])
        added += 1
    return {"references": len(archive["records"]), "already_in_review_queue": already_present,
            "added": added, "remaining": sum(r["id"] not in seen for r in archive["records"]),
            "publications": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=ROOT / "apps/web/public/archive/news.json")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    archive = json.loads(args.archive.read_text(encoding="utf-8"))
    inbox = GitHubInbox(os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"])
    report = enqueue_history(inbox, archive, limit=args.limit)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
            summary.write("## Revisión del archivo histórico\n\n```json\n" + json.dumps(report, indent=2) + "\n```\n")


if __name__ == "__main__":
    main()
