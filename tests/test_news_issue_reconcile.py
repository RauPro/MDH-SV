"""Private issue lifecycle changes reuse evidence and reserve bounded GitHub writes."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import json

import pytest

from mdh_pipeline import news_issue_reconcile as lifecycle
from mdh_pipeline.review_queue import GitHubRequestError, issue_body

NOW = datetime(2026, 10, 15, 3, 0, tzinfo=timezone.utc)
REVISION = "a" * 40


def candidate(number=1):
    url = f"https://www.diariocolatino.com/noticia-{number}/"
    return {"id": hashlib.sha256(url.encode()).hexdigest(), "source_url": url,
        "source_title": "Noticia de prueba", "publisher": "Diario Co Latino",
        "published_date": "2025-01-02", "discovered_at": "2025-01-03T00:00:00Z",
        "matched_terms": ["derechos humanos"], "status": "pending", "record_type": "news_candidate"}


def issue(number=1, *, labels=("review/pending",), state="open"):
    return {"number": number, "body": issue_body(candidate(number)), "state": state,
        "labels": [{"name": name} for name in labels], "updated_at": "2026-10-14T22:00:00Z",
        "locked": False}


def terminal(number=1, status="published"):
    value = candidate(number)
    return {"candidate_id": value["id"], "status": status,
        "reason": "published_automatic" if status == "published" else "not_relevant",
        **{key: value[key] for key in ("source_url", "publisher", "published_date")},
        "content_sha256": "b" * 64, "evidence_at": NOW.isoformat()}


def pending(number=1):
    return {**terminal(number), "status": "pending", "reason": "awaiting_publication"}


def evidence(*rows):
    return {row["candidate_id"]: row for row in rows}


def encode_file(state, *, raw=None):
    raw = raw if raw is not None else json.dumps(state).encode()
    return {"type": "file", "encoding": "base64", "path": lifecycle.STATE_PATH,
        "content": base64.b64encode(raw).decode(), "size": len(raw), "sha": "c" * 40}


def ledger(*claims):
    return {"schema_version": 1, "repository": lifecycle.REPOSITORY, "claims": list(claims)}


def claim(run="1", limit=100, stamp=NOW):
    return {"run_id": run, "limit": limit, "reserved_at": stamp.isoformat()}


class Inbox:
    repository = lifecycle.REPOSITORY
    base = "/repos/" + repository

    def __init__(self, rows=(), *, state=None, remaining=1000, labels=None):
        self.rows = {row["number"]: deepcopy(row) for row in rows}
        self.file = encode_file(state) if state is not None else None
        self.remaining = remaining
        self.labels = set(lifecycle.LABELS if labels is None else labels)
        self.calls = []
        self.pages = None
        self.fresh = {}
        self.patch_error = None
        self.patch_unconfirmed = False
        self.put_error = None
        self.put_unconfirmed = False

    def get_file(self, path):
        self.calls.append(("GET_FILE", path, {}))
        return deepcopy(self.file)

    def call(self, method, path, **kwargs):
        self.calls.append((method, path, deepcopy(kwargs)))
        if method == "GET" and path == "/rate_limit":
            return {"resources": {"core": {"remaining": self.remaining}}}
        if method == "GET" and path == self.base + "/issues":
            page = kwargs["params"]["page"]
            if self.pages is not None:
                return deepcopy(self.pages.get(page, []))
            ordered = sorted(self.rows.values(), key=lambda row: row["number"])
            return deepcopy(ordered[(page - 1) * 100:page * 100])
        if method == "GET" and path == self.base + "/labels":
            return [{"name": name} for name in sorted(self.labels)]
        if method == "GET" and path.startswith(self.base + "/issues/"):
            number = int(path.rsplit("/", 1)[1])
            return deepcopy(self.fresh.get(number, self.rows[number]))
        if method == "POST" and path == self.base + "/labels":
            self.labels.add(kwargs["json"]["name"])
            return {"name": kwargs["json"]["name"]}
        if method == "PUT" and path == self.base + "/contents/" + lifecycle.STATE_PATH:
            if self.put_error:
                raise self.put_error
            state = json.loads(base64.b64decode(kwargs["json"]["content"]))
            self.file = encode_file(state)
            return {} if self.put_unconfirmed else {"content": {"sha": "d" * 40}}
        if method == "PATCH" and path.startswith(self.base + "/issues/"):
            if self.patch_error:
                raise self.patch_error
            number = int(path.rsplit("/", 1)[1])
            value = kwargs["json"]
            result = {**self.rows[number], **value, "labels": [{"name": name} for name in value["labels"]]}
            self.rows[number] = deepcopy(result)
            if self.patch_unconfirmed:
                result["state"] = "open"
            return result
        raise AssertionError(f"Unexpected request: {method} {path}")

    def mutations(self, method=None):
        return [item for item in self.calls if item[0] in {"POST", "PUT", "PATCH", "DELETE"}
                and (method is None or item[0] == method)]


def run(inbox, terminals, **kwargs):
    return lifecycle.reconcile(inbox, terminals, private_revision=REVISION, run_id="123",
        now=NOW, sleep=lambda _: None, clock=lambda: 0, **kwargs)


def machine_closed(number=1, status="published", *, extra=()):
    original = issue(number, labels=("review/pending", *extra))
    decision = lifecycle.desired_transition(original, evidence(terminal(number, status)))
    payload = lifecycle.transition_payload(original, decision, private_revision=REVISION, now=NOW)
    return {**original, **payload, "labels": [{"name": label} for label in payload["labels"]],
            "closed_by": {"login": "github-actions[bot]", "type": "Bot"}}


def test_issue_snapshot_is_complete_stable_and_filters_pull_requests():
    rows = [issue(n) for n in range(1, 206)]
    rows[3]["pull_request"] = {"url": "ignored"}
    inbox = Inbox(rows)
    actual = lifecycle.issue_snapshot(inbox)
    assert [row["number"] for row in actual] == [n for n in range(1, 206) if n != 4]
    calls = [call for call in inbox.calls if call[1] == inbox.base + "/issues"]
    assert len(calls) == 3
    assert all(call[2]["params"] == {"state": "all", "sort": "created", "direction": "asc",
        "per_page": 100, "page": index} for index, call in enumerate(calls, 1))
    assert not inbox.mutations()


@pytest.mark.parametrize("missing", ["publisher", "published_date", "source_title"])
def test_incomplete_rejected_metadata_does_not_block_other_news(missing):
    malformed = issue(1, labels=("review/rejected",))
    metadata = candidate(1)
    del metadata[missing]
    malformed["body"] = (f"<!-- mdh-candidate:{metadata['id']} -->\n"
                          "```json\n" + json.dumps(metadata) + "\n```")
    inbox = Inbox([malformed, issue(2)])
    result = run(inbox, evidence(terminal(2)), apply=True)
    assert result["changed"] == {"published": 1, "discarded": 0, "pending": 0}
    assert inbox.rows[1]["state"] == "open"


def test_write_budget_always_reads_and_writes_main():
    inbox = Inbox()
    assert lifecycle.TransitionBudget(inbox).claim("123", 1, NOW) == 1
    assert inbox.calls[0][1].endswith("?ref=main")
    assert inbox.mutations("PUT")[0][2]["json"]["branch"] == "main"


def test_later_manual_closure_is_not_reopened_even_with_an_old_automation_marker():
    row = machine_closed()
    row["closed_by"] = {"login": "maintainer", "type": "User"}
    inbox = Inbox([row])
    result = run(inbox, {}, reopen=evidence(pending()), apply=True)
    assert result["changed"]["pending"] == 0
    assert not inbox.mutations()


def test_reopening_requires_fresh_confirmation_of_the_bot_closure():
    row = machine_closed()
    del row["closed_by"]  # The list endpoint can omit this field.
    inbox = Inbox([row])
    result = run(inbox, {}, reopen=evidence(pending()), apply=True)
    assert result["changed"]["pending"] == 0 and result["concurrent_changes"] == 1
    assert not inbox.mutations("PATCH")


def test_all_issue_pages_are_loaded_before_first_mutation():
    rows = [issue(n) for n in range(1, 103)]
    inbox = Inbox(rows)
    result = run(inbox, evidence(terminal(1), terminal(102)), apply=True)
    listing_positions = [index for index, call in enumerate(inbox.calls) if call[1] == inbox.base + "/issues"]
    first_write = next(index for index, call in enumerate(inbox.calls) if call[0] in {"PUT", "POST", "PATCH"})
    assert len(listing_positions) == 2 and max(listing_positions) < first_write
    assert result["changed"]["published"] == 2


@pytest.mark.parametrize("bad", [[{"number": True}], [{"number": 0}], [{"number": 1}, {"number": 1}], {}])
def test_invalid_issue_inventory_does_not_start_mutations(bad):
    inbox = Inbox()
    inbox.pages = {1: bad}
    with pytest.raises(ValueError):
        run(inbox, {}, apply=True)
    assert not inbox.mutations()


def test_incomplete_or_duplicate_pagination_fails_closed(monkeypatch):
    inbox = Inbox([issue(n) for n in range(1, 101)])
    monkeypatch.setattr(lifecycle, "MAX_PAGES", 1)
    with pytest.raises(ValueError, match="incomplete_issue_inventory"):
        run(inbox, {}, apply=True)
    assert not inbox.mutations()
    monkeypatch.setattr(lifecycle, "MAX_PAGES", 2)
    inbox.pages = {1: [issue(n) for n in range(1, 101)], 2: [issue(1)]}
    with pytest.raises(ValueError, match="inconsistent_issue_inventory"):
        run(inbox, {}, apply=True)
    assert not inbox.mutations()


@pytest.mark.parametrize("change", [
    {"body": "An official statistics review request"}, {"pull_request": {}}, {"locked": True},
    {"body": None}, {"body": "x" * 60001}, {"state": "unknown"},
    {"labels": [{"name": "unrelated"}]},
])
def test_non_news_or_held_issues_are_untouched(change):
    row = {**issue(), **change}
    assert lifecycle.desired_transition(row, evidence(terminal())) is None


@pytest.mark.parametrize("key,value", [
    ("candidate_id", "f" * 64), ("source_url", "https://www.diariocolatino.com/different/"),
    ("publisher", "Another publisher"), ("published_date", "2025-01-03"),
])
def test_terminal_bibliographic_identity_mismatch_stays_open(key, value):
    item = terminal()
    item[key] = value
    assert lifecycle.desired_transition(issue(), {candidate()["id"]: item}) is None


def test_publication_preserves_unrelated_and_human_labels_without_human_attribution():
    row = issue(labels=("review/pending", "review/approved", "ai/draft", "custom/keep"))
    inbox = Inbox([row])
    result = run(inbox, evidence(terminal()), apply=True)
    updated = inbox.rows[1]
    names = lifecycle._labels(updated)
    assert names == {"news/published", "review/approved", "ai/draft", "custom/keep"}
    assert updated["state"] == "closed" and updated["state_reason"] == "completed"
    assert "No ejecuta Gemini ni acredita una revisión humana" in updated["body"]
    assert candidate()["id"] in updated["body"]
    assert result["changed"] == {"published": 1, "discarded": 0, "pending": 0}
    assert result["gemini_calls"] == 0


def test_discard_closure_has_pinned_private_evidence_without_approval_label():
    inbox = Inbox([issue()])
    run(inbox, evidence(terminal(status="discarded")), apply=True)
    updated = inbox.rows[1]
    assert lifecycle._labels(updated) == {"news/discarded"}
    assert updated["state_reason"] == "not_planned"
    assert f"/blob/{REVISION}/scope-reviews/{candidate()['id']}.json" in updated["body"]
    assert "review/rejected" not in lifecycle._labels(updated)


def test_dry_run_and_pending_work_never_write_or_reserve_quota():
    inbox = Inbox([issue()])
    result = run(inbox, evidence(terminal()), apply=False)
    assert result["dry_run"] and result["planned"] == {"published": 1}
    assert not inbox.mutations() and not any(call[1] == "/rate_limit" for call in inbox.calls)
    result = run(inbox, {}, apply=True)
    assert result["planned"] == {} and not inbox.mutations()


def test_closed_manual_issues_and_pending_human_approvals_are_preserved():
    assert lifecycle.desired_transition(issue(state="closed"), {}) is None
    assert lifecycle.desired_transition(issue(state="closed"), evidence(terminal())) is None
    approved = issue(labels=("review/approved",))
    assert lifecycle.desired_transition(approved, evidence(terminal(status="discarded"))) is None
    assert lifecycle.desired_transition(approved, {}) is None
    rejected = machine_closed(extra=("review/rejected",))
    assert lifecycle.desired_transition(rejected, {}) is None


def test_explicit_rejection_wins_over_automatic_publication():
    row = issue(labels=("review/pending", "review/rejected", "custom/keep"))
    decision = lifecycle.desired_transition(row, evidence(terminal()))
    assert decision["status"] == "discarded" and decision["reason"] == "editorial_rejection"
    payload = lifecycle.transition_payload(row, decision, private_revision=REVISION, now=NOW)
    assert "review/rejected" in payload["labels"] and "custom/keep" in payload["labels"]


def test_withdrawn_candidate_does_not_reopen_or_close_as_automatically_published():
    identifier = candidate()["id"]
    assert lifecycle.desired_transition(issue(), evidence(terminal()), excluded_ids={identifier}) is None
    assert lifecycle.desired_transition(machine_closed(), {}, excluded_ids={identifier}) is None


def test_existing_machine_closure_is_idempotent_and_changed_evidence_reopens_it():
    row = machine_closed(extra=("custom/keep",))
    assert lifecycle.desired_transition(row, evidence(terminal())) is None
    inbox = Inbox([row])
    assert lifecycle.desired_transition(row, {}) is None
    result = run(inbox, {}, apply=True, reopen=evidence(pending()))
    reopened = inbox.rows[1]
    assert reopened["state"] == "open" and reopened["state_reason"] == "reopened"
    assert lifecycle._labels(reopened) == {"custom/keep", "review/pending"}
    assert result["changed"]["pending"] == 1
    assert reopened["body"].count("<!-- mdh-news-lifecycle:start -->") == 1
    assert lifecycle.desired_transition(reopened, {}) is None
    assert lifecycle.desired_transition(reopened, evidence(terminal()))["status"] == "published"


def test_marker_without_managed_label_or_with_wrong_identity_cannot_reopen():
    row = machine_closed()
    row["labels"] = [{"name": "custom/keep"}]
    assert lifecycle.desired_transition(row, {}, reopen=evidence(pending())) is None
    row = machine_closed()
    row["body"] = row["body"].replace("v1:published:" + candidate()["id"], "v1:published:" + "f" * 64)
    assert lifecycle.desired_transition(row, {}, reopen=evidence(pending())) is None


@pytest.mark.parametrize("change", [
    {"body": "changed by a human"}, {"labels": [{"name": "review/rejected"}]},
    {"state": "closed"}, {"updated_at": "2026-10-15T02:59:00Z"}, {"locked": True},
])
def test_fresh_get_detects_concurrent_changes_and_never_patches_them(change):
    inbox = Inbox([issue()])
    inbox.fresh[1] = {**issue(), **change}
    result = run(inbox, evidence(terminal()), apply=True)
    assert result["concurrent_changes"] == 1 and result["remaining_transitions"] == 1
    assert not inbox.mutations("PATCH")


def test_published_then_reopen_then_discard_priority_and_bounded_writes():
    inbox = Inbox([issue(1), machine_closed(2), issue(3)])
    result = run(inbox, evidence(terminal(1, "discarded"), terminal(3)), apply=True, limit=2, reopen=evidence(pending(2)))
    assert [call[1].rsplit("/", 1)[1] for call in inbox.mutations("PATCH")] == ["3", "2"]
    assert result["changed"] == {"published": 1, "pending": 1, "discarded": 0}
    assert result["remaining_transitions"] == 1


def test_initial_budget_reservation_and_existing_file_cas_are_confirmed():
    inbox = Inbox()
    budget = lifecycle.TransitionBudget(inbox)
    assert budget.claim("1", 100, NOW) == 100
    assert budget.claim("2", 100, NOW) == 50
    payloads = [call[2]["json"] for call in inbox.mutations("PUT")]
    assert "sha" not in payloads[0] and payloads[1]["sha"] == "c" * 40
    final = json.loads(base64.b64decode(inbox.file["content"]))
    assert [row["limit"] for row in final["claims"]] == [100, 50]
    assert lifecycle.STATE_PATH in inbox.mutations("PUT")[0][1]
    assert "gemini-budget" not in str(inbox.calls)


def test_duplicate_run_and_full_rolling_hour_cannot_claim_again():
    inbox = Inbox(state=ledger(claim("1", 150)))
    budget = lifecycle.TransitionBudget(inbox)
    assert budget.claim("1", 100, NOW) == 0
    assert budget.claim("2", 100, NOW + timedelta(minutes=59)) == 0
    assert not inbox.mutations()
    assert budget.claim("2", 150, NOW + timedelta(hours=1)) == 150


def test_old_claims_expire_but_recent_run_identity_remains_idempotent():
    inbox = Inbox(state=ledger(claim("1", 10, NOW - timedelta(days=1)), claim("2", 50, NOW - timedelta(days=8))))
    budget = lifecycle.TransitionBudget(inbox)
    assert budget.claim("1", 150, NOW) == 0
    assert budget.claim("3", 150, NOW) == 150
    final = json.loads(base64.b64decode(inbox.file["content"]))
    assert [item["run_id"] for item in final["claims"]] == ["1", "3"]


@pytest.mark.parametrize("state", [
    {"schema_version": True, "repository": lifecycle.REPOSITORY, "claims": []},
    {"schema_version": 1, "repository": "another/private", "claims": []},
    ledger(claim("1"), claim("1")), ledger(claim(limit=151)), ledger(claim(limit=True)),
    ledger(claim(stamp=NOW + timedelta(seconds=1))), ledger({"run_id": "0", "limit": 1, "reserved_at": NOW.isoformat()}),
    ledger({"run_id": "1", "limit": 1, "reserved_at": "2026-10-15T03:00:00"}),
])
def test_invalid_or_duplicate_ledger_state_never_reserves(state):
    inbox = Inbox(state=state)
    with pytest.raises((ValueError, TypeError)):
        lifecycle.TransitionBudget(inbox).claim("9", 150, NOW)
    assert not inbox.mutations()


@pytest.mark.parametrize("change", [
    {"path": "runtime/gemini-budget.json"}, {"sha": "wrong"}, {"size": 1},
    {"encoding": "raw"}, {"content": "%%%"}, {"size": True},
])
def test_invalid_ledger_file_never_reserves(change):
    inbox = Inbox(state=ledger())
    inbox.file.update(change)
    with pytest.raises((ValueError, TypeError)):
        lifecycle.TransitionBudget(inbox).claim("9", 150, NOW)
    assert not inbox.mutations()


def test_duplicate_json_keys_fail_before_quota_reservation():
    inbox = Inbox()
    inbox.file = encode_file(None, raw=b'{"schema_version":1,"schema_version":1,"repository":"RauPro/MDH-SV-review","claims":[]}')
    with pytest.raises(ValueError, match="duplicate_reconciliation_key"):
        lifecycle.TransitionBudget(inbox).claim("9", 150, NOW)
    assert not inbox.mutations()


@pytest.mark.parametrize("remaining,expected", [(1000, 150), (412, 1), (410, 0), (400, 0), (0, 0)])
def test_primary_budget_leaves_ingestion_reserve(remaining, expected):
    inbox = Inbox(remaining=remaining)
    assert lifecycle._available_transitions(inbox, 150) == expected
    assert lifecycle.PRIMARY_RESERVE == 400


def test_primary_exhaustion_does_not_write_ledger_labels_or_issues():
    inbox = Inbox([issue()], remaining=400, labels=())
    result = run(inbox, evidence(terminal()), apply=True)
    assert result["transition_grant"] == 0 and result["stopped_reason"] == "github_write_budget"
    assert not inbox.mutations()


@pytest.mark.parametrize("kind", ["reservation_error", "reservation_unconfirmed", "patch_error", "patch_unconfirmed"])
def test_uncertain_reservation_or_patch_stops_without_additional_issue_writes(kind):
    inbox = Inbox([issue(1), issue(2)])
    if kind == "reservation_error":
        inbox.put_error = GitHubRequestError(500)
    elif kind == "reservation_unconfirmed":
        inbox.put_unconfirmed = True
    elif kind == "patch_error":
        inbox.patch_error = GitHubRequestError(500)
    else:
        inbox.patch_unconfirmed = True
    with pytest.raises((ValueError, GitHubRequestError)):
        run(inbox, evidence(terminal(1), terminal(2)), apply=True)
    assert len(inbox.mutations("PATCH")) == (1 if kind.startswith("patch") else 0)
    assert len(inbox.mutations("PUT")) == 1


def test_reconciliation_does_not_invoke_model_even_when_applied(monkeypatch):
    from mdh_pipeline import gemini_draft
    def forbidden(*args, **kwargs):
        raise AssertionError("Reconciliation must not request Gemini")
    monkeypatch.setattr(gemini_draft, "_request", forbidden)
    inbox = Inbox([issue()])
    result = run(inbox, evidence(terminal()), apply=True)
    assert result["gemini_calls"] == 0
    assert all(path == "/rate_limit" or path.startswith(inbox.base + "/") for _, path, _ in inbox.calls)


def test_rejected_label_alone_is_enough_for_existing_editorial_rejection():
    row = issue(labels=("review/rejected",))
    value = lifecycle.desired_transition(row, {})
    assert value["status"] == "discarded" and value["reason"] == "editorial_rejection"


def test_invalid_candidate_metadata_and_wrong_reopen_identity_stay_untouched():
    row = issue()
    row["body"] = row["body"].replace('"status": "pending"', '"status": "approved"')
    assert lifecycle.desired_transition(row, evidence(terminal())) is None
    row = machine_closed()
    wrong = {**pending(), "published_date": "2025-01-03"}
    assert lifecycle.desired_transition(row, {}, reopen=evidence(wrong)) is None
    manual = issue(state="closed")
    assert lifecycle.desired_transition(manual, {}, reopen=evidence(pending())) is None


def test_oversized_listing_page_is_not_treated_as_a_complete_inventory():
    inbox = Inbox()
    inbox.pages = {1: [issue(n) for n in range(1, 102)]}
    with pytest.raises(ValueError):
        lifecycle.issue_snapshot(inbox)
    assert not inbox.mutations()


@pytest.mark.parametrize("run_id,requested", [(None, 1), ("0", 1), ("1", 151), ("1", True), ("1", -1)])
def test_invalid_budget_request_is_rejected_before_read_or_write(run_id, requested):
    inbox = Inbox()
    with pytest.raises(ValueError):
        lifecycle.TransitionBudget(inbox).claim(run_id, requested, NOW)
    assert inbox.calls == []


def test_label_creation_is_reserved_and_never_contains_source_or_model_data():
    inbox = Inbox([issue()], labels=())
    result = run(inbox, evidence(terminal()), apply=True)
    mutations = inbox.mutations()
    assert [call[0] for call in mutations] == ["PUT", "POST", "POST", "PATCH"]
    assert all(set(call[2]["json"]) == {"name", "color"} for call in mutations if call[0] == "POST")
    assert result["changed"]["published"] == 1


class PublicResponse:
    def __init__(self, value, status=200, raw=None):
        self.raw = raw if raw is not None else json.dumps(value).encode()
        self.status_code = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_content(self, _):
        yield self.raw


class PublicClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return next(self.responses)


def test_public_snapshot_uses_only_fixed_endpoints_without_auth_or_redirects():
    client = PublicClient([PublicResponse({"records": []}), PublicResponse({"dossiers": []})])
    assert lifecycle.production_snapshot(client) == ({"records": []}, {"dossiers": []})
    assert [url for url, _ in client.calls] == [
        lifecycle.PRODUCTION + "/archive/news.json", lifecycle.PRODUCTION + "/archive/dossiers.json"]
    assert all(kwargs == {"timeout": (8, 30), "stream": True, "allow_redirects": False}
        for _, kwargs in client.calls)


@pytest.mark.parametrize("response", [
    PublicResponse({}, status=302), PublicResponse({}, status=503),
    PublicResponse(None, raw=b'{"records":[],"records":[]}'),
])
def test_bad_public_snapshot_cannot_supply_a_partial_collection(response):
    client = PublicClient([response])
    with pytest.raises(ValueError):
        lifecycle.production_snapshot(client)
    assert len(client.calls) == 1
