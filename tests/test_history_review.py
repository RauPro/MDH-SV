import copy
from datetime import date

import pytest

from mdh_pipeline.history import SOURCES, reference_from_post
from mdh_pipeline.history_review import enqueue_history


def reference(n, title="Denuncian detenciones"):
    return reference_from_post({"id": n, "date": f"2025-04-{n:02}T12:00:00", "link": f"https://www.diariocolatino.com/detenciones-{n}/", "title": {"rendered": title}}, SOURCES[0], date(2025, 4, 1), date(2025, 4, 30), "2026-10-10T04:00:00+00:00")


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
    assert report == {"references": 3, "eligible_references": 3, "excluded_international": 0, "already_in_review_queue": 1, "added": 1, "remaining": 1, "publications": 0}
    assert inbox.created[0]["record_type"] == "news_candidate"
    assert inbox.created[0]["status"] == "pending"
    assert inbox.created[0]["jurisdiction_status"] == "not_reviewed"
    assert enqueue_history(inbox, archive, limit=10)["added"] == 1
    assert enqueue_history(inbox, archive, limit=10)["added"] == 0


def test_all_scopes_are_included_by_default_with_salvadoran_priority():
    unknown = reference(1)
    foreign = reference(2, "Detenciones en Venezuela")
    salvadoran = reference(3, "Detenciones en El Salvador")
    archive = {"schema_version": 1, "records": [unknown, foreign, salvadoran]}
    inbox = Inbox()
    report = enqueue_history(inbox, archive, limit=1)
    assert [r["id"] for r in inbox.created] == [salvadoran["id"]]
    assert report["eligible_references"] == 3
    assert report["excluded_international"] == 0
    assert report["remaining"] == 2
    report = enqueue_history(inbox, archive, limit=20)
    assert [r["id"] for r in inbox.created] == [salvadoran["id"], unknown["id"], foreign["id"]]
    assert report["remaining"] == 0
    assert report["already_in_review_queue"] == 1
    report = enqueue_history(inbox, archive, limit=20, include_international=True)
    assert inbox.created[-1]["id"] == foreign["id"]
    assert report["eligible_references"] == 3
    assert report["excluded_international"] == 0
    assert report["already_in_review_queue"] == 3
    assert report["remaining"] == 0


def test_preexisting_foreign_candidates_do_not_inflate_eligible_queue_counters():
    foreign = reference(1, "Detenciones en Venezuela")
    inbox = Inbox([foreign["id"]])
    report = enqueue_history(inbox, {"schema_version": 1, "records": [foreign]}, include_international=False)
    assert report["eligible_references"] == 0
    assert report["excluded_international"] == 1
    assert report["already_in_review_queue"] == 0
    assert report["remaining"] == 0
    assert report["added"] == 0


def test_fair_batch_reserves_slots_for_unknown_and_international_news():
    salvadoran = [reference(n, "Detenciones en El Salvador") for n in range(1, 21)]
    unknown = reference(21)
    international = reference(22, "Detenciones en Venezuela")
    inbox = Inbox()
    enqueue_history(inbox, {"schema_version": 1, "records": [*salvadoran, unknown, international]}, limit=6)
    assert [row["scope_hint"] for row in inbox.created] == [
        "el_salvador", "el_salvador", "undetermined", "el_salvador", "el_salvador", "international_context"]
    assert [row["published_date"] for row in inbox.created if row["scope_hint"] == "el_salvador"] == ["2025-04-01", "2025-04-02", "2025-04-03", "2025-04-04"]


def test_one_item_batches_rotate_instead_of_starving_other_scopes():
    records = [reference(n, "Detenciones en El Salvador") for n in range(1, 21)]
    records += [reference(21), reference(22, "Detenciones en Venezuela")]
    inbox = Inbox()
    for _ in range(6):
        enqueue_history(inbox, {"schema_version": 1, "records": records}, limit=1)
    assert {row["scope_hint"] for row in inbox.created} == {"el_salvador", "undetermined", "international_context"}


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
