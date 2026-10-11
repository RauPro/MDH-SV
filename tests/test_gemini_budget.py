"""Quota accounting uses a synthetic clock and in-memory GitHub, never Gemini."""
import base64
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from mdh_pipeline.gemini_budget import (BudgetConfig, GeminiBudget, MODEL, QuotaDeferred,
    QuotaStateError, STATE_PATH, estimate_input_tokens, initial_state, next_pacific_midnight)

NOW = datetime(2026, 10, 11, 14, 0, tzinfo=timezone.utc)
PAYLOAD = {"contents": [{"parts": [{"text": "Artículo sintético sin datos reales."}]}]}


def configuration(**changes):
    return replace(BudgetConfig("mdh-project-test", MODEL, 10, 101, 100_000, 100, 50,
                               "2026-10-11T13:00:00+00:00"), **changes)


class Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class Inbox:
    base = "/repos/example/private-review"

    def __init__(self):
        self.file = None
        self.writes = []
        self.before_write = None
        self.receipt = True

    def store(self, document):
        raw = json.dumps(document).encode()
        self.file = {"path": STATE_PATH, "type": "file", "encoding": "base64",
                     "size": len(raw), "content": base64.b64encode(raw).decode(),
                     "sha": hashlib.sha256(raw).hexdigest()}

    def document(self):
        return json.loads(base64.b64decode(self.file["content"]))

    def get_file(self, path):
        assert path == self.base + "/contents/" + STATE_PATH
        return deepcopy(self.file)

    def call(self, method, path, **kwargs):
        assert method == "PUT"
        assert path == self.base + "/contents/" + STATE_PATH
        payload = kwargs["json"]
        assert payload["branch"] == "main"
        self.writes.append(deepcopy(payload))
        if self.before_write:
            self.before_write()
        if payload.get("sha") != (self.file or {}).get("sha"):
            raise RuntimeError("GitHub request failed (409); candidate content withheld")
        self.store(json.loads(base64.b64decode(payload["content"])))
        return {"content": {"sha": self.file["sha"]}} if self.receipt else {}


def ready(config=None, *, now=NOW, used_requests=0):
    clock, inbox, config = Clock(now), Inbox(), config or configuration()
    budget = GeminiBudget(inbox, config, clock=clock)
    budget.bootstrap(used_requests=used_requests)
    clock.advance(60)
    return budget, inbox, clock


@pytest.mark.parametrize("changes", [
    {"project_id": ""}, {"project_id": "project-with-key?secret"}, {"model": "gemini-2.5-flash"},
    {"rpm": 0}, {"rpm": True}, {"rpd": 20_001}, {"rpd": 1}, {"input_tpm": 1024},
    {"daily_budget": 101}, {"daily_budget": 102}, {"daily_budget": 0},
    {"batch_ceiling": 51}, {"batch_ceiling": False}, {"verified_at": "2026-10-11"},
])
def test_explicit_bounded_configuration(changes):
    with pytest.raises(QuotaStateError):
        configuration(**changes)


def test_batch_ceiling_is_explicit_and_enforced():
    config = configuration(batch_ceiling=30)
    config.validate_batch(30)
    for invalid in (31, 0, True):
        with pytest.raises(QuotaStateError):
            config.validate_batch(invalid)


def test_missing_state_never_silently_bootstraps():
    inbox = Inbox()
    budget = GeminiBudget(inbox, configuration(), clock=Clock())
    with pytest.raises(QuotaStateError, match="missing"):
        budget.reserve(PAYLOAD)
    assert inbox.writes == []


def test_bootstrap_requires_prior_usage_and_blocks_unknown_recent_minute():
    inbox, clock = Inbox(), Clock()
    budget = GeminiBudget(inbox, configuration(), clock=clock)
    budget.bootstrap(used_requests=37)
    assert budget.snapshot()["used_requests"] == 37
    with pytest.raises(QuotaDeferred) as deferred:
        budget.reserve(PAYLOAD)
    assert deferred.value.reason == "provider_cooldown"
    assert deferred.value.retry_at == NOW + timedelta(seconds=60)
    clock.advance(60)
    assert budget.reserve(PAYLOAD).used_requests == 38
    with pytest.raises(QuotaStateError, match="already_provisioned"):
        budget.bootstrap(used_requests=0)
    assert inbox.document()["used_requests"] == 38


def test_reservation_persisted_before_return_and_never_refunded():
    budget, inbox, _ = ready()
    first = budget.reserve(PAYLOAD)
    assert first.used_requests == inbox.document()["used_requests"] == 1
    # A caller's HTTP timeout and retry still consume two *distinct* reservations.
    second = budget.reserve(PAYLOAD)
    assert second.used_requests == 2
    assert len(inbox.document()["reservations"]) == 2
    serialized = json.dumps(inbox.document())
    assert "Artículo" not in serialized and "contents" not in serialized
    assert set(inbox.document()) == {"schema_version", "project_id", "model", "day", "used_requests",
                                      "updated_at", "cooldown_until", "reservations"}


def test_rate_limit_reserves_window_and_does_not_sleep_or_write_on_deferral():
    budget, inbox, clock = ready(configuration(rpm=2))
    budget.reserve(PAYLOAD)
    clock.advance(5)
    budget.reserve(PAYLOAD)
    count = len(inbox.writes)
    with pytest.raises(QuotaDeferred) as deferred:
        budget.reserve(PAYLOAD)
    assert deferred.value.reason == "minute_budget_exhausted"
    assert deferred.value.retry_at == NOW + timedelta(seconds=180)
    assert len(inbox.writes) == count
    clock.advance(115)
    assert budget.reserve(PAYLOAD).used_requests == 3
    assert len(inbox.document()["reservations"]) == 2


def test_token_window_waits_until_sufficient_capacity_not_only_first_expiry():
    tokens = estimate_input_tokens(PAYLOAD)
    budget, _, clock = ready(configuration(input_tpm=tokens * 3))
    budget.reserve(PAYLOAD)
    clock.advance(10)
    budget.reserve(PAYLOAD)
    clock.advance(10)
    budget.reserve(PAYLOAD)
    larger = {"text": "x" * (tokens + 10)}
    assert tokens < estimate_input_tokens(larger) < tokens * 3
    with pytest.raises(QuotaDeferred) as deferred:
        budget.reserve(larger)
    assert deferred.value.retry_at == NOW + timedelta(seconds=190)
    clock.now = deferred.value.retry_at
    budget.reserve(larger)


def test_oversized_payload_is_rejected_without_reservation_or_provider_call():
    budget, inbox, _ = ready(configuration(input_tpm=estimate_input_tokens(PAYLOAD)))
    writes = len(inbox.writes)
    with pytest.raises(QuotaDeferred) as deferred:
        budget.reserve(PAYLOAD)
    assert deferred.value.reason == "payload_exceeds_tpm"
    assert len(inbox.writes) == writes
    assert inbox.document()["used_requests"] == 0


def test_input_estimate_counts_utf8_and_framing_without_extra_network():
    serialized = json.dumps(PAYLOAD, ensure_ascii=False, separators=(",", ":")).encode()
    assert estimate_input_tokens(PAYLOAD) == len(serialized) + 1024
    for invalid in ([], {"float": float("nan")}, {"object": object()}):
        with pytest.raises(QuotaStateError):
            estimate_input_tokens(invalid)


def test_daily_limit_preserves_seed_and_rejects_without_write():
    budget, inbox, _ = ready(used_requests=99)
    budget.reserve(PAYLOAD)
    count = len(inbox.writes)
    with pytest.raises(QuotaDeferred) as deferred:
        budget.reserve(PAYLOAD)
    assert deferred.value.reason == "daily_budget_exhausted"
    assert deferred.value.retry_at == datetime(2026, 10, 12, 7, tzinfo=timezone.utc)
    assert len(inbox.writes) == count


def test_configuration_changes_do_not_reset_consumed_project_budget():
    budget, inbox, clock = ready(used_requests=75)
    budget.reserve(PAYLOAD)
    tighter = GeminiBudget(inbox, configuration(rpd=80, daily_budget=70, rpm=1), clock=clock)
    assert tighter.snapshot()["used_requests"] == 76
    with pytest.raises(QuotaDeferred, match="daily_budget_exhausted"):
        tighter.reserve(PAYLOAD)
    different_project = GeminiBudget(inbox, configuration(project_id="other-project"), clock=clock)
    with pytest.raises(QuotaStateError):
        different_project.reserve(PAYLOAD)


def test_midnight_resets_daily_count_but_never_last_minute_request_or_token_usage():
    midnight = datetime(2026, 10, 12, 7, tzinfo=timezone.utc)
    budget, inbox, clock = ready(configuration(rpm=1), now=midnight - timedelta(seconds=70))
    assert budget.reserve(PAYLOAD).day == "2026-10-11"
    clock.advance(15)
    assert budget.snapshot()["used_requests"] == 1
    with pytest.raises(QuotaDeferred, match="minute_budget_exhausted") as deferred:
        budget.reserve(PAYLOAD)
    assert deferred.value.retry_at == midnight + timedelta(seconds=110)
    clock.now = deferred.value.retry_at
    result = budget.reserve(PAYLOAD)
    assert result.day == "2026-10-12" and result.used_requests == 2
    assert len(inbox.document()["reservations"]) == 1


@pytest.mark.parametrize(("instant", "expected"), [
    ("2026-03-08T08:00:00+00:00", "2026-03-09T07:00:00+00:00"),  # 23h spring day
    ("2026-11-01T07:00:00+00:00", "2026-11-02T08:00:00+00:00"),  # 25h fall day
])
def test_pacific_day_handles_dst(instant, expected):
    assert next_pacific_midnight(datetime.fromisoformat(instant)) == datetime.fromisoformat(expected)


def test_backwards_clock_fails_closed():
    budget, inbox, clock = ready()
    budget.reserve(PAYLOAD)
    clock.advance(-1)
    before = len(inbox.writes)
    with pytest.raises(QuotaStateError, match="backwards"):
        budget.reserve(PAYLOAD)
    assert len(inbox.writes) == before


def test_provider_429_persists_shared_cooldown_and_retains_consumption():
    budget, inbox, clock = ready()
    budget.reserve(PAYLOAD)
    deadline = budget.record_429()
    other_worker = GeminiBudget(inbox, configuration(), clock=clock)
    assert other_worker.snapshot()["used_requests"] == 1
    with pytest.raises(QuotaDeferred, match="provider_cooldown") as deferred:
        other_worker.reserve(PAYLOAD)
    assert deferred.value.retry_at == deadline == next_pacific_midnight(clock.now)
    clock.now = deadline
    assert other_worker.reserve(PAYLOAD).used_requests == 1


def test_cas_conflict_reloads_and_preserves_competing_reservation():
    budget, inbox, clock = ready()
    competitor = GeminiBudget(inbox, configuration(), clock=clock)

    def competing_write():
        inbox.before_write = None
        competitor.reserve(PAYLOAD)

    inbox.before_write = competing_write
    assert budget.reserve(PAYLOAD).used_requests == 2
    assert inbox.document()["used_requests"] == 2


def test_competitor_consuming_last_daily_slot_blocks_loser():
    budget, inbox, clock = ready(used_requests=99)
    competitor = GeminiBudget(inbox, configuration(), clock=clock)

    def competing_write():
        inbox.before_write = None
        competitor.reserve(PAYLOAD)

    inbox.before_write = competing_write
    with pytest.raises(QuotaDeferred, match="daily_budget_exhausted"):
        budget.reserve(PAYLOAD)
    assert inbox.document()["used_requests"] == 100


def test_cas_retries_are_bounded():
    budget, inbox, _ = ready()

    def conflict():
        raise RuntimeError("GitHub request failed (409); candidate content withheld")

    inbox.before_write = conflict
    before = len(inbox.writes)
    with pytest.raises(QuotaStateError, match="concurrent_update_exhausted"):
        budget.reserve(PAYLOAD)
    assert len(inbox.writes) - before == 3


def test_unacknowledged_write_fails_closed_even_if_reservation_reached_server():
    budget, inbox, _ = ready()
    inbox.receipt = False
    with pytest.raises(QuotaStateError, match="receipt"):
        budget.reserve(PAYLOAD)
    assert inbox.document()["used_requests"] == 1


def test_storage_error_does_not_retry_or_forward_sensitive_text():
    budget, inbox, _ = ready()

    def failure():
        raise RuntimeError("secret-provider-response-must-not-escape")

    inbox.before_write = failure
    before = len(inbox.writes)
    with pytest.raises(QuotaStateError, match="reservation_write_failed") as error:
        budget.reserve(PAYLOAD)
    assert "secret-provider" not in str(error.value)
    assert len(inbox.writes) - before == 1


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(schema_version=True),
    lambda d: d.update(used_requests=-1),
    lambda d: d.update(used_requests=True),
    lambda d: d.update(used_requests=20_001),
    lambda d: d.update(day="2010-01-01"),
    lambda d: d.update(cooldown_until="2030-01-01T00:00:00+00:00"),
    lambda d: d.update(updated_at="2026-10-11"),
    lambda d: d.update(secret="not-allowed"),
    lambda d: d.update(reservations=[{"at": NOW.isoformat(), "model": MODEL, "input_tokens": -1}]),
])
def test_corrupt_state_fails_closed(mutate):
    budget, inbox, _ = ready()
    document = inbox.document()
    mutate(document)
    inbox.store(document)
    before = len(inbox.writes)
    with pytest.raises(QuotaStateError):
        budget.reserve(PAYLOAD)
    assert len(inbox.writes) == before


def test_file_binding_and_base64_checked():
    budget, inbox, _ = ready()
    inbox.file["path"] = "drafts/unrelated.json"
    with pytest.raises(QuotaStateError, match="invalid_budget_file"):
        budget.reserve(PAYLOAD)
    inbox.file["path"] = STATE_PATH
    inbox.file["content"] = "invalid base64!"
    with pytest.raises(QuotaStateError, match="content"):
        budget.reserve(PAYLOAD)


def test_only_recent_window_is_stored_with_aggregate_day_count():
    budget, inbox, clock = ready()
    for _ in range(30):
        budget.reserve(PAYLOAD)
        clock.advance(121)
    assert inbox.document()["used_requests"] == 30
    assert len(inbox.document()["reservations"]) == 1


def test_seed_above_budget_is_accounted_and_deferred_until_new_day():
    budget, _, clock = ready(used_requests=101)
    with pytest.raises(QuotaDeferred, match="daily_budget_exhausted"):
        budget.reserve(PAYLOAD)
    clock.now = next_pacific_midnight(clock.now)
    assert budget.reserve(PAYLOAD).used_requests == 1


def test_initial_state_rejects_missing_or_invalid_usage():
    for invalid in (-1, True, "0", 20_001):
        with pytest.raises(QuotaStateError):
            initial_state(configuration(), now=NOW, used_requests=invalid)


def test_dispatch_must_happen_within_reserved_minute_without_network_on_expiry():
    budget, inbox, clock = ready()
    reservation = budget.reserve(PAYLOAD)
    assert reservation.dispatch_deadline == clock.now + timedelta(seconds=60)
    clock.advance(60)
    budget.assert_dispatch_allowed(reservation)
    clock.advance(0.001)
    before = len(inbox.writes)
    with pytest.raises(QuotaDeferred, match="reservation_expired"):
        budget.assert_dispatch_allowed(reservation)
    assert len(inbox.writes) == before
    assert inbox.document()["used_requests"] == 1


def test_dispatch_after_pacific_midnight_requires_new_day_reservation():
    midnight = datetime(2026, 10, 12, 7, tzinfo=timezone.utc)
    budget, inbox, clock = ready(now=midnight - timedelta(seconds=70))
    reservation = budget.reserve(PAYLOAD)
    assert reservation.dispatch_deadline == midnight
    clock.advance(10)
    with pytest.raises(QuotaDeferred, match="reservation_expired"):
        budget.assert_dispatch_allowed(reservation)
    # Possible in-flight old-day request is charged conservatively to both days.
    assert budget.snapshot()["used_requests"] == 1
    clock.advance(130)
    assert budget.reserve(PAYLOAD).used_requests == 2
    assert inbox.document()["day"] == "2026-10-12"


def test_slow_github_ack_does_not_return_permit_for_late_dispatch():
    budget, inbox, clock = ready()

    def slow_ack():
        clock.advance(61)

    inbox.before_write = slow_ack
    with pytest.raises(QuotaDeferred, match="reservation_expired"):
        budget.reserve(PAYLOAD)
    assert inbox.document()["used_requests"] == 1


def test_dispatch_with_backwards_clock_fails_closed():
    budget, _, clock = ready()
    reservation = budget.reserve(PAYLOAD)
    clock.advance(-1)
    with pytest.raises(QuotaStateError, match="clock_moved_backwards"):
        budget.assert_dispatch_allowed(reservation)


def test_per_run_allocation_caps_http_retries_as_separate_requests():
    budget, inbox, clock = ready()
    budget.set_request_limit(2)
    budget.reserve(PAYLOAD)
    budget.reserve(PAYLOAD)
    before = len(inbox.writes)
    with pytest.raises(QuotaDeferred, match="run_budget_exhausted") as error:
        budget.reserve(PAYLOAD)
    assert error.value.retry_at == clock.now.replace(minute=0, second=0) + timedelta(hours=1)
    assert budget.requests_this_run == 2 and inbox.document()["used_requests"] == 2
    assert len(inbox.writes) == before
    assert set(budget.snapshot()) == {"day", "used_requests", "remaining_requests", "cooldown_until"}


def test_per_run_allocation_cannot_reset_or_expand_budget():
    budget, _, _ = ready()
    budget.set_request_limit(2)
    budget.reserve(PAYLOAD)
    budget.set_request_limit(2)
    assert budget.requests_this_run == 1
    with pytest.raises(QuotaStateError, match="already_allocated"):
        budget.set_request_limit(3)
    with pytest.raises(QuotaStateError, match="ceiling"):
        budget.set_request_limit(51)
