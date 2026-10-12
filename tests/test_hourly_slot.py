import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from mdh_pipeline.hourly_slot import HourlySlots, HourlySlotError, STATE_PATH

NOW = datetime(2026, 10, 11, 22, 17, tzinfo=timezone.utc)


class Inbox:
    base = "/repos/RauPro/MDH-SV-review"

    def __init__(self):
        self.file = None
        self.writes = 0
        self.before_write = None

    def get_file(self, path):
        assert path == self.base + "/contents/" + STATE_PATH
        return deepcopy(self.file)

    def put(self, data):
        raw = json.dumps(data).encode()
        self.file = {"type": "file", "path": STATE_PATH, "encoding": "base64", "size": len(raw),
                     "sha": hashlib.sha256(raw).hexdigest(), "content": base64.b64encode(raw).decode()}

    def call(self, method, path, **kwargs):
        assert method == "PUT" and path == self.base + "/contents/" + STATE_PATH
        if self.before_write:
            callback, self.before_write = self.before_write, None
            callback()
        if kwargs["json"].get("sha") != (self.file["sha"] if self.file else None):
            raise RuntimeError("sha_conflict")
        self.put(json.loads(base64.b64decode(kwargs["json"]["content"])))
        self.writes += 1
        return {"content": {"sha": self.file["sha"]}}

    def document(self):
        return json.loads(base64.b64decode(self.file["content"]))


def test_every_trigger_claims_the_current_hour_at_most_once():
    inbox = Inbox(); slots = HourlySlots(inbox, clock=lambda: NOW)
    claim = slots.claim(run_id="123", trigger_source="vercel_cron", requested_hour="2026-10-11T22:00:00Z")
    assert claim["hour"] == "2026-10-11T22:00:00Z" and claim["lag_seconds"] == 17 * 60
    assert slots.claim(run_id="456", trigger_source="github_schedule") is None
    slots.finish(claim, status="completed", counts={"attempted": 20, "news_dossiers": 18, "errors": 2})
    assert slots.claim(run_id="789", trigger_source="manual") is None
    assert inbox.writes == 2
    assert inbox.document()["slots"][0]["status"] == "completed"


def test_crash_locks_only_the_claimed_hour():
    inbox = Inbox()
    first = HourlySlots(inbox, clock=lambda: NOW)
    first.claim(run_id="123", trigger_source="manual")
    assert first.claim(run_id="456", trigger_source="manual") is None
    next_hour = HourlySlots(inbox, clock=lambda: NOW + timedelta(hours=1))
    assert next_hour.claim(run_id="456", trigger_source="github_schedule") is not None


def test_delayed_triggers_share_actual_hour_instead_of_backfilling_multiple_slots():
    inbox = Inbox(); slots = HourlySlots(inbox, clock=lambda: NOW)
    claim = slots.claim(run_id="123", trigger_source="vercel_cron", requested_hour="2026-10-11T20:00:00Z")
    assert claim["hour"] == "2026-10-11T22:00:00Z"
    assert claim["requested_hour"] == "2026-10-11T20:00:00Z"
    assert slots.claim(run_id="456", trigger_source="vercel_cron", requested_hour="2026-10-11T21:00:00Z") is None


@pytest.mark.parametrize("requested", ["2026-10-11T19:00:00Z", "2026-10-11T23:00:00Z", "2026-10-11T22:01:00Z",
                                        "2026-02-30T22:00:00Z", "2026-10-11T22:00:00+00:00", "not-a-date"])
def test_invalid_stale_future_hour_never_mutates_state(requested):
    inbox = Inbox(); slots = HourlySlots(inbox, clock=lambda: NOW)
    with pytest.raises(HourlySlotError):
        slots.claim(run_id="123", trigger_source="manual", requested_hour=requested)
    assert inbox.file is None


def test_competing_sha_claim_is_successful_noop_for_loser():
    inbox = Inbox(); first = HourlySlots(inbox, clock=lambda: NOW)
    inbox.before_write = lambda: first.claim(run_id="111", trigger_source="vercel_cron")
    loser = HourlySlots(inbox, clock=lambda: NOW)
    assert loser.claim(run_id="222", trigger_source="github_schedule") is None
    assert inbox.document()["slots"][0]["run_id"] == "111" and inbox.writes == 1


def test_unknown_put_failure_never_grants_a_claim():
    inbox = Inbox()
    def fail():
        raise RuntimeError("unavailable")
    inbox.before_write = fail
    with pytest.raises(RuntimeError):
        HourlySlots(inbox, clock=lambda: NOW).claim(run_id="123", trigger_source="manual")
    assert inbox.file is None


def test_repair_cannot_complete_another_runs_claim():
    inbox = Inbox(); slots = HourlySlots(inbox, clock=lambda: NOW)
    claim = slots.claim(run_id="123", trigger_source="manual")
    with pytest.raises(HourlySlotError, match="claim_changed"):
        slots.finish({**claim, "run_id": "456"}, status="completed")


def test_corrupt_metadata_is_fail_closed():
    inbox = Inbox(); inbox.put({"schema_version": 1, "slots": [], "article_body": "must-not-store"})
    with pytest.raises(HourlySlotError, match="invalid_hourly_state"):
        HourlySlots(inbox, clock=lambda: NOW).claim(run_id="123", trigger_source="manual")
    assert inbox.writes == 0


def test_retention_is_bounded_and_completion_rejects_nonaggregate_data():
    inbox = Inbox()
    for hour in range(75):
        slots = HourlySlots(inbox, clock=lambda hour=hour: NOW + timedelta(hours=hour))
        claim = slots.claim(run_id=str(hour + 1), trigger_source="github_schedule")
        slots.finish(claim, status="completed", counts={"news_dossiers": 1})
    assert len(inbox.document()["slots"]) == 72
    future = HourlySlots(inbox, clock=lambda: NOW + timedelta(hours=76))
    claim = future.claim(run_id="999", trigger_source="manual")
    with pytest.raises(HourlySlotError, match="invalid_hourly_counts"):
        future.finish(claim, status="completed", counts={"source_url": "secret"})


def test_deferral_records_aggregate_success_without_opening_another_claim():
    inbox = Inbox(); slots = HourlySlots(inbox, clock=lambda: NOW)
    claim = slots.claim(run_id="123", trigger_source="manual")
    slots.finish(claim, status="deferred", counts={"attempted": 0, "errors": 0})
    assert inbox.document()["slots"][0]["status"] == "deferred"
    assert slots.claim(run_id="456", trigger_source="manual") is None


def test_clock_moving_backwards_cannot_evict_claims_or_reopen_old_hours():
    inbox = Inbox()
    HourlySlots(inbox, clock=lambda: NOW).claim(run_id="123", trigger_source="manual")
    with pytest.raises(HourlySlotError, match="clock_moved_backwards"):
        HourlySlots(inbox, clock=lambda: NOW - timedelta(hours=1)).claim(run_id="456", trigger_source="manual")
    assert inbox.writes == 1
