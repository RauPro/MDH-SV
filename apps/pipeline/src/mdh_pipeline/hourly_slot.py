"""Private, revision-guarded hourly execution claims shared by every trigger."""
from __future__ import annotations

import base64
import binascii
import json
import re
from datetime import datetime, timedelta, timezone

STATE_PATH = "runtime/gemini-hourly.json"
MAX_SLOTS = 72
MAX_BYTES = 80_000
SOURCES = {"manual", "vercel_cron", "github_schedule"}
STATUSES = {"claimed", "completed", "deferred", "failed"}
COUNTERS = {"attempted", "news_dossiers", "errors", "migrated", "http_requests"}


class HourlySlotError(RuntimeError):
    """Safe diagnostic codes only; no third-party response contents."""


def utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise HourlySlotError("invalid_hourly_time")
    return value.astimezone(timezone.utc)


def timestamp(value):
    return utc(value).isoformat().replace("+00:00", "Z")


def parse_hour(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:00:00Z", value):
        raise HourlySlotError("invalid_scheduled_hour")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HourlySlotError("invalid_scheduled_hour") from None


def _parse_time(value):
    if not isinstance(value, str) or len(value) > 40:
        raise HourlySlotError("invalid_hourly_state_time")
    try:
        return utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except (ValueError, TypeError):
        raise HourlySlotError("invalid_hourly_state_time") from None


def _validate_state(document):
    if (not isinstance(document, dict) or set(document) != {"schema_version", "slots"}
            or type(document["schema_version"]) is not int or document["schema_version"] != 1
            or not isinstance(document["slots"], list) or len(document["slots"]) > MAX_SLOTS):
        raise HourlySlotError("invalid_hourly_state")
    seen = set()
    for row in document["slots"]:
        required = {"hour", "run_id", "trigger_source", "requested_hour", "claimed_at", "lag_seconds", "status"}
        if (not isinstance(row, dict) or not required <= row.keys()
                or set(row) - required - {"finished_at", "counts"}
                or not isinstance(row["run_id"], str) or not re.fullmatch(r"[0-9]{1,20}", row["run_id"])
                or row["trigger_source"] not in SOURCES or row["status"] not in STATUSES
                or type(row["lag_seconds"]) is not int or not 0 <= row["lag_seconds"] < 10_800):
            raise HourlySlotError("invalid_hourly_slot")
        hour, requested = parse_hour(row["hour"]), parse_hour(row["requested_hour"])
        claimed = _parse_time(row["claimed_at"])
        if (row["hour"] in seen or requested > hour or hour - requested > timedelta(hours=2)
                or claimed.replace(minute=0, second=0, microsecond=0) != hour
                or row["lag_seconds"] != int((claimed - requested).total_seconds())):
            raise HourlySlotError("invalid_hourly_slot_time")
        seen.add(row["hour"])
        if row["status"] == "claimed":
            if "finished_at" in row or "counts" in row:
                raise HourlySlotError("invalid_hourly_claim")
        else:
            if _parse_time(row.get("finished_at")) < claimed:
                raise HourlySlotError("invalid_hourly_completion_time")
            counts = row.get("counts")
            if (not isinstance(counts, dict) or set(counts) - COUNTERS
                    or any(type(value) is not int or not 0 <= value <= 100_000 for value in counts.values())):
                raise HourlySlotError("invalid_hourly_counts")
    return document


class HourlySlots:
    def __init__(self, inbox, *, clock=None):
        self.inbox = inbox
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.path = inbox.base + "/contents/" + STATE_PATH

    def _read(self):
        file = self.inbox.get_file(self.path)
        if file is None:
            return {"schema_version": 1, "slots": []}, None
        if (not isinstance(file, dict) or file.get("type") != "file" or file.get("path") != STATE_PATH
                or file.get("encoding") != "base64" or type(file.get("size")) is not int
                or not 0 < file["size"] <= MAX_BYTES or not isinstance(file.get("content"), str)
                or len(file["content"]) > MAX_BYTES * 2 or not isinstance(file.get("sha"), str)
                or not re.fullmatch(r"[a-f0-9]{40,64}", file["sha"])):
            raise HourlySlotError("invalid_hourly_file")
        try:
            raw = base64.b64decode(file["content"].replace("\n", ""), validate=True)
            if len(raw) != file["size"]:
                raise HourlySlotError("invalid_hourly_file_size")
            return _validate_state(json.loads(raw)), file["sha"]
        except (binascii.Error, UnicodeError, json.JSONDecodeError):
            raise HourlySlotError("invalid_hourly_file_content") from None

    def _write(self, document, sha):
        _validate_state(document)
        raw = (json.dumps(document, sort_keys=True, indent=2) + "\n").encode()
        if len(raw) > MAX_BYTES:
            raise HourlySlotError("oversized_hourly_state")
        payload = {"message": "Record bounded hourly Gemini execution state", "content": base64.b64encode(raw).decode()}
        if sha:
            payload["sha"] = sha
        result = self.inbox.call("PUT", self.path, json=payload)
        receipt = (result or {}).get("content", {}).get("sha")
        if not isinstance(receipt, str) or not re.fullmatch(r"[a-f0-9]{40,64}", receipt):
            raise HourlySlotError("invalid_hourly_write_receipt")

    def claim(self, *, run_id, trigger_source, requested_hour=None):
        now = utc(self.clock())
        hour = now.replace(minute=0, second=0, microsecond=0)
        requested = parse_hour(requested_hour) if requested_hour else hour
        if requested > hour or hour - requested > timedelta(hours=2):
            raise HourlySlotError("stale_or_future_scheduled_hour")
        if not isinstance(run_id, str) or not re.fullmatch(r"[0-9]{1,20}", run_id) or trigger_source not in SOURCES:
            raise HourlySlotError("invalid_hourly_trigger")
        key = timestamp(hour)
        document, sha = self._read()
        if any(parse_hour(item["hour"]) > hour for item in document["slots"]):
            raise HourlySlotError("hourly_clock_moved_backwards")
        if any(row["hour"] == key for row in document["slots"]):
            return None
        row = {"hour": key, "run_id": run_id, "trigger_source": trigger_source,
               "requested_hour": timestamp(requested), "claimed_at": timestamp(now),
               "lag_seconds": int((now - requested).total_seconds()), "status": "claimed"}
        document["slots"] = sorted([*document["slots"], row], key=lambda item: item["hour"])[-MAX_SLOTS:]
        try:
            self._write(document, sha)
        except RuntimeError:
            # A racing trigger can win the GitHub SHA comparison. Never execute
            # a second batch, even when the first trigger has already finished.
            latest, _ = self._read()
            if any(item["hour"] == key for item in latest["slots"]):
                return None
            raise
        return row

    def finish(self, claim, *, status, counts=None):
        if status not in STATUSES - {"claimed"}:
            raise HourlySlotError("invalid_hourly_completion_status")
        document, sha = self._read()
        matches = [row for row in document["slots"] if row["hour"] == claim["hour"]]
        if len(matches) != 1 or matches[0] != claim:
            raise HourlySlotError("hourly_claim_changed")
        row = matches[0]
        row.update(status=status, finished_at=timestamp(self.clock()), counts=counts or {})
        self._write(document, sha)
