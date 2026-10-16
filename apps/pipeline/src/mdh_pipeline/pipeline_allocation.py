"""Allocate one HTTP-attempt grant across scope, extraction and validation.

This planner never reserves provider quota itself. Every phase must still use
the shared GeminiBudget ledger, including retries and unsuccessful requests.
"""
from __future__ import annotations

import base64
import binascii
from copy import deepcopy
from datetime import date, datetime, timezone
import json
import math
import re

from .gemini_budget import QuotaStateError
from .instance import load_instance

STATE_PATH = "runtime/news-allocation.json"
MAX_BYTES = 120_000
MAX_RUNS = 72
PLAN_KEYS = {"scope_requests", "extraction_requests", "validation_requests", "unused_requests"}


def _integer(value, maximum=50):
    return type(value) is int and 0 <= value <= maximum


def _run_id(value):
    return isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,19}", value) is not None


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise QuotaStateError("invalid_allocation_day")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise QuotaStateError("invalid_allocation_day") from None
    return value


def _strict_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise QuotaStateError("duplicate_allocation_key")
        value[key] = item
    return value


class AllocationLedger:
    """Private CAS handoff: a publisher can consume a run's reservation once.

    HTTP quota remains exclusively owned by GeminiBudget. An unfinished
    extraction releases only the validation reservation made before it began;
    a completed extraction can release its acknowledged unused capacity too.
    """
    def __init__(self, inbox, *, project_id, model, clock=None):
        if (not isinstance(getattr(inbox, "repository", None), str)
                or inbox.repository.lower() != load_instance().private_repository.lower()
                or not isinstance(project_id, str) or not project_id or len(project_id) > 200
                or not isinstance(model, str) or not re.fullmatch(r"gemini-[A-Za-z0-9.-]+", model)):
            raise QuotaStateError("invalid_allocation_configuration")
        self.inbox, self.project_id, self.model = inbox, project_id, model
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.path = inbox.base + "/contents/" + STATE_PATH

    def _validate(self, document):
        if (not isinstance(document, dict) or set(document) != {"schema_version", "project_id", "model", "runs"}
                or type(document["schema_version"]) is not int or document["schema_version"] != 1
                or document["project_id"] != self.project_id or document["model"] != self.model
                or not isinstance(document["runs"], list) or len(document["runs"]) > MAX_RUNS):
            raise QuotaStateError("invalid_allocation_state")
        seen = set()
        for row in document["runs"]:
            if (not isinstance(row, dict) or set(row) != {"run_id", "kind", "day", "created_at", "grant", "plan",
                    "scope_used", "extraction_used", "consumer_run_id", "claimed_requests"}
                    or not _run_id(row["run_id"]) or row["kind"] not in {"extraction", "manual_validation"}
                    or not _integer(row["grant"]) or not isinstance(row["plan"], dict)
                    or set(row["plan"]) != PLAN_KEYS or any(not _integer(v) for v in row["plan"].values())
                    or sum(row["plan"].values()) != row["grant"]
                    or (row["kind"], row["run_id"]) in seen):
                raise QuotaStateError("invalid_allocation_run")
            _day(row["day"])
            try:
                stamp = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
                if stamp.utcoffset() is None:
                    raise ValueError()
            except (ValueError, TypeError, AttributeError):
                raise QuotaStateError("invalid_allocation_time") from None
            seen.add((row["kind"], row["run_id"]))
            completed = row["scope_used"] is not None or row["extraction_used"] is not None
            if completed and (not _integer(row["scope_used"], row["plan"]["scope_requests"])
                              or not _integer(row["extraction_used"], row["grant"] - row["scope_used"]
                                              - row["plan"]["validation_requests"])):
                raise QuotaStateError("invalid_allocation_usage")
            available = (row["grant"] - row["scope_used"] - row["extraction_used"] if completed
                         else row["plan"]["validation_requests"])
            if row["consumer_run_id"] is None:
                if row["claimed_requests"] != 0:
                    raise QuotaStateError("invalid_allocation_claim")
            elif not _run_id(row["consumer_run_id"]) or not _integer(row["claimed_requests"], available):
                raise QuotaStateError("invalid_allocation_claim")
        return document

    def _read(self):
        file = self.inbox.get_file(self.path)
        if file is None:
            return {"schema_version": 1, "project_id": self.project_id, "model": self.model, "runs": []}, None
        if (not isinstance(file, dict) or file.get("type") != "file" or file.get("path") != STATE_PATH
                or file.get("encoding") != "base64" or not _integer(file.get("size"), MAX_BYTES)
                or not file["size"] or not isinstance(file.get("content"), str)
                or len(file["content"]) > MAX_BYTES * 2 or not isinstance(file.get("sha"), str)
                or not re.fullmatch(r"[a-f0-9]{40,64}", file["sha"])):
            raise QuotaStateError("invalid_allocation_file")
        try:
            raw = base64.b64decode(file["content"].replace("\n", ""), validate=True)
            if len(raw) != file["size"]:
                raise QuotaStateError("invalid_allocation_file_size")
            document = json.loads(raw, object_pairs_hook=_strict_pairs,
                                  parse_constant=lambda _: (_ for _ in ()).throw(QuotaStateError("invalid_allocation_number")))
            return self._validate(document), file["sha"]
        except (binascii.Error, UnicodeError, json.JSONDecodeError):
            raise QuotaStateError("invalid_allocation_file_content") from None

    def _write(self, document, sha):
        self._validate(document)
        raw = (json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
        if len(raw) > MAX_BYTES:
            raise QuotaStateError("oversized_allocation_state")
        payload = {"message": "Record shared hourly news processing allocation", "content": base64.b64encode(raw).decode()}
        if sha:
            payload["sha"] = sha
        result = self.inbox.call("PUT", self.path, json=payload)
        receipt = (result or {}).get("content", {}).get("sha")
        if not isinstance(receipt, str) or not re.fullmatch(r"[a-f0-9]{40,64}", receipt):
            raise QuotaStateError("allocation_write_unconfirmed")

    def create(self, *, run_id, day, hourly_grant, plan, kind="extraction"):
        _day(day)
        if not _run_id(run_id):
            raise QuotaStateError("invalid_allocation_run_id")
        document, sha = self._read()
        if any(row["run_id"] == run_id and row["kind"] == kind for row in document["runs"]):
            raise QuotaStateError("allocation_run_already_exists")
        now = self.clock()
        if not isinstance(now, datetime) or now.utcoffset() is None:
            raise QuotaStateError("invalid_allocation_clock")
        row = {"run_id": run_id, "kind": kind, "day": day, "created_at": now.astimezone(timezone.utc).isoformat(),
               "grant": hourly_grant, "plan": deepcopy(plan), "scope_used": None, "extraction_used": None,
               "consumer_run_id": None, "claimed_requests": 0}
        document["runs"] = [*document["runs"], row][-MAX_RUNS:]
        self._write(document, sha)

    def finish(self, *, run_id, scope_used, extraction_used):
        document, sha = self._read()
        matches = [row for row in document["runs"] if row["run_id"] == run_id and row["kind"] == "extraction"]
        if (len(matches) != 1 or matches[0]["scope_used"] is not None
                or matches[0]["consumer_run_id"] is not None):
            raise QuotaStateError("allocation_completion_changed")
        matches[0].update(scope_used=scope_used, extraction_used=extraction_used)
        self._write(document, sha)

    def claim_validation(self, *, source_run_id, consumer_run_id, day, kind="extraction"):
        _day(day)
        if not _run_id(source_run_id) or not _run_id(consumer_run_id):
            raise QuotaStateError("invalid_allocation_run_id")
        document, sha = self._read()
        matches = [row for row in document["runs"] if row["run_id"] == source_run_id and row["kind"] == kind]
        if not matches or matches[0]["day"] != day or matches[0]["consumer_run_id"] is not None:
            return 0
        row = matches[0]
        grant = (row["grant"] - row["scope_used"] - row["extraction_used"] if row["scope_used"] is not None
                 else row["plan"]["validation_requests"])
        row.update(consumer_run_id=consumer_run_id, claimed_requests=grant)
        # A failed/uncertain write is never permission to call the provider.
        self._write(document, sha)
        return grant

    def claim_manual_validation(self, *, consumer_run_id, day, grant):
        _day(day)
        if not _run_id(consumer_run_id) or not _integer(grant) or grant == 0:
            raise QuotaStateError("invalid_manual_allocation")
        document, _ = self._read()
        if any(row["kind"] == "manual_validation" and row["run_id"] == consumer_run_id for row in document["runs"]):
            return 0
        self.create(run_id=consumer_run_id, day=day, hourly_grant=grant, kind="manual_validation",
                    plan={"scope_requests": 0, "extraction_requests": 0, "validation_requests": grant, "unused_requests": 0})
        return self.claim_validation(source_run_id=consumer_run_id, consumer_run_id=consumer_run_id,
                                     day=day, kind="manual_validation")


def plan_pipeline_requests(hourly_grant, *, extraction_pending, validation_ready,
                           scope_pending, batch_size=4):
    """Return bounded grants based on eligible work, never open issue counts.

    Two bulk scope calls keep discoveries moving. With all queues populated a
    ten-call hour gives 2 scope, 6 extraction and 2 validation calls (up to eight
    dossiers). When scope is complete, the steady state becomes 8 + 2.
    """
    for value in (hourly_grant, extraction_pending, validation_ready, scope_pending):
        if type(value) is not int or not 0 <= value <= 100_000:
            raise ValueError("invalid_pipeline_workload")
    if hourly_grant > 50 or type(batch_size) is not int or not 1 <= batch_size <= 4:
        raise ValueError("invalid_pipeline_allocation")
    scope = min(2, math.ceil(hourly_grant / 5), math.ceil(scope_pending / 16)) if scope_pending else 0
    # One remaining call is more useful completing an existing dossier than
    # creating more classified work that cannot yet be enriched or published.
    if hourly_grant == 1 and (validation_ready or extraction_pending):
        scope = 0
    remaining = hourly_grant - scope
    extraction = min(extraction_pending, remaining)
    # Reserve enough calls to assess the extraction share, bounded by a fair
    # one-fifth share when an older validation backlog also exists.
    validation = min(remaining, math.ceil(hourly_grant / (batch_size + 1)),
                     math.ceil((validation_ready + extraction) / batch_size))
    extraction = min(extraction_pending, remaining - validation)
    # Idle extraction capacity goes to the ready validation backlog. Capacity
    # reserved solely for hypothetical new drafts is never spent needlessly.
    validation = min(remaining - extraction,
                     math.ceil((validation_ready + extraction) / batch_size))
    # A lone extraction can move forward even if its validation must wait for
    # the next grant. Otherwise tiny budgets could starve extraction forever.
    if remaining and not extraction and not validation_ready and extraction_pending:
        extraction = min(extraction_pending, remaining)
        validation = min(remaining - extraction, math.ceil(extraction / batch_size))
    return {"scope_requests": scope, "extraction_requests": extraction,
            "validation_requests": validation,
            "unused_requests": hourly_grant - scope - extraction - validation}
