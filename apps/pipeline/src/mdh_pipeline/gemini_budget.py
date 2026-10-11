"""Durable, fail-closed project budget for Gemini's free-tier extraction worker.

Each HTTP attempt (including retries/timeouts) must call ``reserve`` first. There
is deliberately no refund: a client timeout does not prove that Google did not
accept the request. This ledger covers workers sharing this private repository;
the project must be dedicated to this worker or external usage must be seeded.

Input accounting uses the UTF-8 size of the *entire serialized JSON request* plus
1024 units for framing. It is an intentionally conservative admission bound, not
an exact Gemini tokenizer or a quota lookup. No countTokens request is made and
no payload, payload hash, article, credential or provider response is persisted.
All quota values must come from explicitly verified project limits.
"""
from __future__ import annotations

import base64
import binascii
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
import json
import re
from zoneinfo import ZoneInfo

MODEL = "gemini-3.5-flash-lite"
STATE_PATH = "runtime/gemini-budget.json"
PACIFIC = ZoneInfo("America/Los_Angeles")
MAX_REQUESTS = 20_000
MAX_STATE_BYTES = 4_000_000
MAX_CAS_ATTEMPTS = 3
TOKEN_FRAMING_ALLOWANCE = 1024
# A reservation can take up to 60 s to persist and reach the HTTP call. Keeping
# it for 120 s protects a full provider minute after that latest dispatch time.
DISPATCH_LIFETIME_SECONDS = 60
RESERVATION_RETENTION_SECONDS = 120
_STATE_KEYS = {"schema_version", "project_id", "model", "day", "used_requests",
               "updated_at", "cooldown_until", "reservations"}


class QuotaStateError(RuntimeError):
    """Safe failure: Gemini must not be called without an acknowledged reserve."""


class QuotaDeferred(RuntimeError):
    def __init__(self, reason: str, retry_at: datetime):
        self.reason = reason
        self.retry_at = retry_at
        super().__init__(reason)


def utcnow():
    return datetime.now(timezone.utc)


def _utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise QuotaStateError("invalid_budget_clock")
    return value.astimezone(timezone.utc)


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        raise QuotaStateError("invalid_budget_timestamp")
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
            raise ValueError()
        return parsed.astimezone(timezone.utc)
    except ValueError:
        raise QuotaStateError("invalid_budget_timestamp") from None


def _integer(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def next_pacific_midnight(now):
    local = _utc(now).astimezone(PACIFIC)
    return datetime.combine(local.date() + timedelta(days=1), time(), PACIFIC).astimezone(timezone.utc)


@dataclass(frozen=True)
class BudgetConfig:
    project_id: str
    model: str
    rpm: int
    rpd: int
    input_tpm: int
    daily_budget: int
    batch_ceiling: int
    verified_at: str

    def __post_init__(self):
        if (not isinstance(self.project_id, str)
                or not re.fullmatch(r"[a-z][a-z0-9-]{4,62}", self.project_id)
                or self.model != MODEL
                or not _integer(self.rpm, 1, MAX_REQUESTS)
                or not _integer(self.rpd, 2, MAX_REQUESTS)
                or not _integer(self.input_tpm, TOKEN_FRAMING_ALLOWANCE + 1, 100_000_000)
                or not _integer(self.daily_budget, 1, self.rpd - 1)
                or not _integer(self.batch_ceiling, 1, 50)):
            raise QuotaStateError("invalid_explicit_budget_configuration")
        _timestamp(self.verified_at)

    def validate_batch(self, limit):
        if not _integer(limit, 1, self.batch_ceiling):
            raise QuotaStateError("batch_exceeds_configured_ceiling")


@dataclass(frozen=True)
class Reservation:
    reserved_at: datetime
    day: str
    used_requests: int
    input_tokens: int
    dispatch_deadline: datetime


def estimate_input_tokens(payload):
    if not isinstance(payload, dict):
        raise QuotaStateError("invalid_budget_payload")
    try:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError):
        raise QuotaStateError("invalid_budget_payload") from None
    return len(raw) + TOKEN_FRAMING_ALLOWANCE


def initial_state(config, *, now, used_requests):
    """Explicit provisioning only; seed *all* project usage in the Pacific day.

    The first minute is blocked even when seed usage is zero, because older
    minute-window requests cannot be reconstructed from a daily counter.
    """
    now = _utc(now)
    if not _integer(used_requests, 0, MAX_REQUESTS):
        raise QuotaStateError("invalid_budget_seed")
    return {"schema_version": 1, "project_id": config.project_id, "model": config.model,
            "day": now.astimezone(PACIFIC).date().isoformat(), "used_requests": used_requests,
            "updated_at": now.isoformat(), "cooldown_until": (now + timedelta(seconds=60)).isoformat(),
            "reservations": []}


def _validate_state(value, config):
    if (not isinstance(value, dict) or set(value) != _STATE_KEYS
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["project_id"] != config.project_id or value["model"] != config.model
            or not _integer(value["used_requests"], 0, MAX_REQUESTS)
            or not isinstance(value["reservations"], list) or len(value["reservations"]) > MAX_REQUESTS):
        raise QuotaStateError("invalid_budget_state")
    updated = _timestamp(value["updated_at"])
    if value["day"] != updated.astimezone(PACIFIC).date().isoformat():
        raise QuotaStateError("invalid_budget_day")
    if value["cooldown_until"] is not None:
        cooldown = _timestamp(value["cooldown_until"])
        if cooldown > next_pacific_midnight(updated) + timedelta(seconds=60):
            raise QuotaStateError("invalid_budget_cooldown")
    previous = None
    same_day_count = 0
    for item in value["reservations"]:
        if (not isinstance(item, dict) or set(item) != {"at", "input_tokens", "model"}
                or item["model"] != config.model
                or not _integer(item["input_tokens"], TOKEN_FRAMING_ALLOWANCE, 100_000_000)):
            raise QuotaStateError("invalid_budget_reservation")
        instant = _timestamp(item["at"])
        if (instant > updated or instant < updated - timedelta(seconds=RESERVATION_RETENTION_SECONDS)
                or (previous and instant < previous)):
            raise QuotaStateError("invalid_budget_reservation_clock")
        previous = instant
        same_day_count += instant.astimezone(PACIFIC).date().isoformat() == value["day"]
    if same_day_count > value["used_requests"]:
        raise QuotaStateError("invalid_budget_request_count")
    return deepcopy(value)


def _advance(state, now):
    if now < _timestamp(state["updated_at"]):
        raise QuotaStateError("budget_clock_moved_backwards")
    day = now.astimezone(PACIFIC).date().isoformat()
    if day != state["day"]:
        # Requests dispatched before midnight can reach the provider after it.
        # Charge still-live previous-day reservations to both days, never refund.
        boundary = datetime.combine(now.astimezone(PACIFIC).date(), time(), PACIFIC).astimezone(timezone.utc)
        carried = sum(_timestamp(item["at"]) > boundary - timedelta(seconds=RESERVATION_RETENTION_SECONDS)
                      for item in state["reservations"])
        state["day"], state["used_requests"] = day, carried
    state["reservations"] = [item for item in state["reservations"]
                             if _timestamp(item["at"]) > now - timedelta(seconds=RESERVATION_RETENTION_SECONDS)]
    if state["cooldown_until"] and _timestamp(state["cooldown_until"]) <= now:
        state["cooldown_until"] = None
    state["updated_at"] = now.isoformat()
    return state


def _is_conflict(error):
    status = getattr(getattr(error, "response", None), "status_code", None)
    # GitHubInbox deliberately exposes only the HTTP status, never response data.
    return status in (409, 422) or (
        isinstance(error, RuntimeError)
        and re.fullmatch(r"GitHub request failed \((409|422)\); candidate content withheld", str(error)) is not None)


class GeminiBudget:
    """GitHub Contents SHA-CAS ledger. Missing/invalid state never starts at zero."""
    def __init__(self, inbox, config: BudgetConfig, *, clock=utcnow):
        self.inbox, self.config, self.clock = inbox, config, clock
        self.path = inbox.base + "/contents/" + STATE_PATH
        self.request_limit = None
        self.requests_this_run = 0

    def set_request_limit(self, limit):
        """Cap actual request reservations in this run, including HTTP retries.

        The runner supplies its hourly allocation after claiming its UTC slot.
        Setting the same limit again is harmless; changing it cannot reset or
        expand an already allocated run budget.
        """
        self.config.validate_batch(limit)
        if self.request_limit is not None and self.request_limit != limit:
            raise QuotaStateError("run_budget_already_allocated")
        self.request_limit = limit

    def _read(self):
        try:
            file = self.inbox.get_file(self.path)
        except Exception:
            raise QuotaStateError("budget_read_failed") from None
        if file is None:
            raise QuotaStateError("budget_state_missing_provision_explicitly")
        if (not isinstance(file, dict) or file.get("type") != "file" or file.get("path") != STATE_PATH
                or file.get("encoding") != "base64" or not _integer(file.get("size"), 1, MAX_STATE_BYTES)
                or not isinstance(file.get("content"), str) or len(file["content"]) > MAX_STATE_BYTES * 2
                or not isinstance(file.get("sha"), str) or not re.fullmatch(r"[a-f0-9]{40,64}", file["sha"])):
            raise QuotaStateError("invalid_budget_file")
        try:
            raw = base64.b64decode(file["content"].replace("\n", ""), validate=True)
            if len(raw) != file["size"]:
                raise QuotaStateError("invalid_budget_file_size")
            document = json.loads(raw)
        except (binascii.Error, UnicodeError, ValueError):
            raise QuotaStateError("invalid_budget_file_content") from None
        return _validate_state(document, self.config), file["sha"]

    def _write(self, state, sha, *, message="Reserve Gemini project quota"):
        raw = (json.dumps(state, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode()
        if len(raw) > MAX_STATE_BYTES:
            raise QuotaStateError("budget_state_too_large")
        payload = {"message": message, "content": base64.b64encode(raw).decode(), "branch": "main"}
        if sha is not None:
            payload["sha"] = sha
        result = self.inbox.call("PUT", self.path, json=payload)
        receipt = result.get("content", {}).get("sha") if isinstance(result, dict) else None
        if not isinstance(receipt, str) or not re.fullmatch(r"[a-f0-9]{40,64}", receipt):
            raise QuotaStateError("budget_write_receipt_invalid")

    def bootstrap(self, *, used_requests):
        """One-time explicit creation; cannot overwrite an existing ledger."""
        try:
            if self.inbox.get_file(self.path) is not None:
                raise QuotaStateError("budget_already_provisioned")
            state = initial_state(self.config, now=self.clock(), used_requests=used_requests)
            self._write(state, None, message="Provision Gemini quota with explicit prior usage")
            return deepcopy(state)
        except QuotaStateError:
            raise
        except Exception:
            raise QuotaStateError("budget_provision_failed") from None

    def snapshot(self):
        state, _ = self._read()
        state = _advance(state, _utc(self.clock()))
        return {"day": state["day"], "used_requests": state["used_requests"],
                "remaining_requests": max(0, self.config.daily_budget - state["used_requests"]),
                "cooldown_until": state["cooldown_until"]}

    def assert_dispatch_allowed(self, reservation):
        """Call immediately before HTTP POST, including each retry.

        No network write or replacement reservation occurs on expiration. Stop
        the batch rather than looping endlessly on slow quota-storage responses.
        """
        if not isinstance(reservation, Reservation):
            raise QuotaStateError("invalid_budget_dispatch_reservation")
        now = _utc(self.clock())
        if now < reservation.reserved_at:
            raise QuotaStateError("budget_clock_moved_backwards")
        if (now > reservation.dispatch_deadline
                or now.astimezone(PACIFIC).date().isoformat() != reservation.day):
            raise QuotaDeferred("reservation_expired", now + timedelta(seconds=1))

    def reserve(self, payload):
        tokens = estimate_input_tokens(payload)
        if self.request_limit is not None and self.requests_this_run >= self.request_limit:
            now = _utc(self.clock())
            raise QuotaDeferred("run_budget_exhausted", now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
        for _ in range(MAX_CAS_ATTEMPTS):
            state, sha = self._read()
            now = _utc(self.clock())
            state = _advance(state, now)
            if tokens >= self.config.input_tpm:
                raise QuotaDeferred("payload_exceeds_tpm", next_pacific_midnight(now))
            if state["cooldown_until"]:
                raise QuotaDeferred("provider_cooldown", _timestamp(state["cooldown_until"]))
            if state["used_requests"] >= self.config.daily_budget:
                raise QuotaDeferred("daily_budget_exhausted", next_pacific_midnight(now))
            recent = state["reservations"]
            running_tokens = sum(item["input_tokens"] for item in recent)
            if len(recent) >= self.config.rpm or running_tokens + tokens > self.config.input_tpm:
                remaining = len(recent)
                for item in recent:
                    remaining -= 1
                    running_tokens -= item["input_tokens"]
                    if remaining < self.config.rpm and running_tokens + tokens <= self.config.input_tpm:
                        raise QuotaDeferred("minute_budget_exhausted", _timestamp(item["at"]) + timedelta(seconds=RESERVATION_RETENTION_SECONDS))
                raise QuotaStateError("invalid_minute_budget_state")
            state["used_requests"] += 1
            state["reservations"].append({"at": now.isoformat(), "input_tokens": tokens, "model": self.config.model})
            try:
                self._write(state, sha)
            except Exception as error:
                if _is_conflict(error):
                    continue
                if isinstance(error, QuotaStateError):
                    raise
                raise QuotaStateError("budget_reservation_write_failed") from None
            self.requests_this_run += 1
            reservation = Reservation(now, state["day"], state["used_requests"], tokens,
                min(now + timedelta(seconds=DISPATCH_LIFETIME_SECONDS), next_pacific_midnight(now)))
            self.assert_dispatch_allowed(reservation)
            return reservation
        raise QuotaStateError("budget_concurrent_update_exhausted")

    def record_429(self):
        """Stop all workers until the next Pacific day; no automatic 429 retries."""
        for _ in range(MAX_CAS_ATTEMPTS):
            state, sha = self._read()
            now = _utc(self.clock())
            state = _advance(state, now)
            deadline = next_pacific_midnight(now)
            if state["cooldown_until"]:
                deadline = max(deadline, _timestamp(state["cooldown_until"]))
            state["cooldown_until"] = deadline.isoformat()
            try:
                self._write(state, sha, message="Pause Gemini after provider quota rejection")
            except Exception as error:
                if _is_conflict(error):
                    continue
                if isinstance(error, QuotaStateError):
                    raise
                raise QuotaStateError("budget_cooldown_write_failed") from None
            return deadline
        raise QuotaStateError("budget_concurrent_update_exhausted")
