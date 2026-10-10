import copy
from datetime import date

import pytest

from mdh_pipeline.history import SOURCES, reference_from_post
from mdh_pipeline.history_review import enqueue_history


def reference(n):
    return reference_from_post({"id": n, "date": f"2025-04-{n:02}T12:00:00", "link": f"https://www.diariocolatino.com/detenciones-{n}/", "title": {"rendered": "Denuncian detenciones"}}, SOURCES[0], date(2025, 4, 1), date(2025, 4, 30), "2026-10-10T04:00:00+00:00")


class Inbox:
    def __init__(self, seen=()):
        self.seen, self.created = set(seen), []
        self.labels_called = False
    def ensure_labels(self): self.labels_called = True
    def existing_ids(self): return self.seen.copy()
    def enqueue(self, item):
        self.created.append(item)
        self.seen.add(item["id"])


def test_history_enqueues_oldest_bounded_batch_and_deduplicates_rss_candidates():
    first, second, third = reference(1), reference(2), reference(3)
    inbox = Inbox([first["id"]])
    archive = {"schema_version": 1, "records": [third, first, second]}
    report = enqueue_history(inbox, archive, limit=1)
    assert [r["id"] for r in inbox.created] == [second["id"]]
    assert report == {"references": 3, "already_in_review_queue": 1, "added": 1, "remaining": 1, "publications": 0}
    assert inbox.created[0]["record_type"] == "news_candidate"
    assert inbox.created[0]["status"] == "pending"
    assert inbox.created[0]["jurisdiction_status"] == "not_reviewed"
    assert enqueue_history(inbox, archive, limit=10)["added"] == 1
    assert enqueue_history(inbox, archive, limit=10)["added"] == 0


def test_entire_batch_is_validated_before_creating_any_issue():
    good, invalid = reference(1), reference(2)
    invalid["victim"] = "Must not enter this channel"
    inbox = Inbox()
    with pytest.raises(ValueError):
        enqueue_history(inbox, {"schema_version": 1, "records": [good, invalid]})
    assert not inbox.created and not inbox.labels_called


def test_history_never_changes_bibliography_review_status_or_writes_public_records():
    archive = {"schema_version": 1, "records": [reference(1)]}
    before = copy.deepcopy(archive)
    assert enqueue_history(Inbox(), archive)["publications"] == 0
    assert archive == before


@pytest.mark.parametrize("limit", [0, -1, 101])
def test_historical_queue_has_strict_batch_limit(limit):
    with pytest.raises(ValueError):
        enqueue_history(Inbox(), {"schema_version": 1, "records": []}, limit=limit)
