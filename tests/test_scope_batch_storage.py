"""Runner-minute savings must not change what is stored, bounded or shared."""
import base64
import hashlib
import json
import threading
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from mdh_pipeline import gemini_review
from mdh_pipeline.gemini_budget import QuotaStateError
from mdh_pipeline.review_queue import GitHubRequestError
from test_scope_review_queue import Budget, Inbox, fetched, fixtures, run


def _name(kind, number):
    return hashlib.sha1(f"{kind}{number}".encode()).hexdigest()


class GitInbox:
    """In-memory Git Data API: trees are path maps, main only moves by fast-forward."""
    base = "/repos/example/private"

    def __init__(self):
        self.root_tree, self.root_commit = _name("tree", 0), _name("commit", 0)
        self.trees = {self.root_tree: {}}
        self.commits = {self.root_commit: {"tree": self.root_tree, "parents": []}}
        self.head, self.calls, self.conflicts = self.root_commit, [], 0

    def get_file(self, path):
        raise AssertionError("batch storage must not read individual files")

    def call(self, method, path, **kwargs):
        self.calls.append((method, path))
        name, body = path.removeprefix(self.base), kwargs.get("json")
        if (method, name) == ("GET", "/git/ref/heads/main"):
            return {"object": {"sha": self.head}}
        if method == "GET" and name.startswith("/git/commits/"):
            return {"tree": {"sha": self.commits[name.rsplit("/", 1)[1]]["tree"]}}
        if (method, name) == ("POST", "/git/trees"):
            assert all(set(entry) == {"path", "mode", "type", "content"} and entry["mode"] == "100644"
                       and entry["type"] == "blob" for entry in body["tree"])
            sha = _name("tree", len(self.trees))
            self.trees[sha] = {**self.trees[body["base_tree"]],
                               **{entry["path"]: entry["content"] for entry in body["tree"]}}
            return {"sha": sha}
        if (method, name) == ("POST", "/git/commits"):
            sha = _name("commit", len(self.commits))
            self.commits[sha] = {"tree": body["tree"], "parents": body["parents"], "message": body["message"]}
            return {"sha": sha}
        if (method, name) == ("PATCH", "/git/refs/heads/main"):
            assert body["force"] is False
            if self.conflicts or self.commits[body["sha"]]["parents"] != [self.head]:
                self.conflicts = max(0, self.conflicts - 1)
                raise GitHubRequestError(422)
            self.head = body["sha"]
            return {"object": {"sha": self.head}}
        raise AssertionError(f"unexpected {method} {name}")

    def files(self):
        return {path: json.loads(content) for path, content in self.trees[self.commits[self.head]["tree"]].items()}

    def writes(self):
        return [call for call in self.calls if call[0] != "GET"]


def stored(inbox):
    return {path.split("/contents/", 1)[1]: json.loads(base64.b64decode(file["content"]))
            for path, file in inbox.files.items()}


def test_a_batch_is_one_fast_forward_commit_with_the_same_documents_as_separate_writes():
    refs, _, data = fixtures(8)
    plain, batched = Inbox(), GitInbox()
    expected = run(plain, Budget(), [], data, max_requests=1, batch_size=8)
    result = run(batched, Budget(), [], data, max_requests=1, batch_size=8, batch_commits=True)
    assert len(plain.puts) == 8 and batched.writes() == [("POST", batched.base + "/git/trees"),
        ("POST", batched.base + "/git/commits"), ("PATCH", batched.base + "/git/refs/heads/main")]
    assert batched.files() == stored(plain) and len(batched.files()) == 8
    for report in (expected, result):
        assert set(report.pop("seconds")) == {"sources", "model", "storage"}
    assert result == expected


def test_source_failures_join_the_batch_and_other_files_survive():
    refs, _, data = fixtures(3)
    inbox = GitInbox()
    inbox.trees[inbox.root_tree]["drafts/keep.json"] = "{}"
    def unreadable(reference):
        if reference["id"] == refs[0]["id"]:
            raise RuntimeError("PRIVATE_SENTINEL")
        return fetched(reference)
    result = run(inbox, Budget(), [], data, fetch=unreadable, batch_commits=True)
    files = inbox.files()
    assert result["source_unavailable"] == 1 and result["classified"] == 2
    assert sum(path.startswith("scope-attempts/") for path in files) == 1
    assert sum(path.startswith("scope-reviews/") for path in files) == 2 and "drafts/keep.json" in files
    assert "PRIVATE_SENTINEL" not in json.dumps(files)


def test_batch_writes_keep_the_two_second_pacing_between_mutations():
    _, _, data = fixtures(8)
    inbox, waits, clock = GitInbox(), [], [0.0]
    def sleep(seconds):
        waits.append(seconds)
        clock[0] += seconds
    run(inbox, Budget(), [], data, max_requests=1, batch_size=8, batch_commits=True, sleep=sleep, clock=lambda: clock[0])
    assert len(inbox.writes()) == 3 and waits == [2.0, 2.0]


def test_scope_share_only_grows_when_the_operator_raises_it():
    from mdh_pipeline.pipeline_allocation import plan_pipeline_requests
    workload = dict(extraction_pending=1000, validation_ready=1000, scope_pending=1000)
    assert plan_pipeline_requests(16, **workload)["scope_requests"] == 2
    raised = plan_pipeline_requests(16, **workload, scope_ceiling=4)
    assert raised["scope_requests"] == 4 and sum(raised.values()) == 16
    assert plan_pipeline_requests(10, **workload, scope_ceiling=8)["scope_requests"] == 2
    assert plan_pipeline_requests(40, **workload, scope_ceiling=8)["scope_requests"] == 8
    assert plan_pipeline_requests(16, extraction_pending=5, validation_ready=5, scope_pending=20, scope_ceiling=6)["scope_requests"] == 2
    for invalid in (0, 9, True, "2"):
        with pytest.raises(ValueError, match="invalid_pipeline_allocation"):
            plan_pipeline_requests(16, **workload, scope_ceiling=invalid)
    assert gemini_review.scope_request_ceiling({}) == 2
    assert gemini_review.scope_request_ceiling({"MDH_SCOPE_REQUESTS_PER_CYCLE": "4"}) == 4
    for invalid in ("0", "9", "2.5", "-1", "four"):
        with pytest.raises(ValueError, match="invalid_scope_request_ceiling"):
            gemini_review.scope_request_ceiling({"MDH_SCOPE_REQUESTS_PER_CYCLE": invalid})


def test_a_moved_branch_is_rebuilt_and_never_forced_and_repeated_conflicts_store_nothing():
    _, _, data = fixtures(2)
    inbox = GitInbox()
    inbox.conflicts = 1
    assert run(inbox, Budget(), [], data, batch_commits=True)["classified"] == 2
    assert len(inbox.files()) == 2 and sum(call[0] == "PATCH" for call in inbox.calls) == 2
    blocked = GitInbox()
    blocked.conflicts = 3
    with pytest.raises(ValueError, match="scope_batch_commit_conflict"):
        run(blocked, Budget(), [], data, batch_commits=True)
    assert blocked.files() == {} and blocked.head == blocked.root_commit


@pytest.mark.parametrize("options", [{"batch_commits": "true"}, {"fetch_workers": 0}, {"fetch_workers": 5}, {"fetch_workers": True}])
def test_storage_options_are_bounded(options):
    _, _, data = fixtures(1)
    with pytest.raises(ValueError, match="invalid_scope_storage_options"):
        run(Inbox(), Budget(), [], data, **options)


def test_parallel_source_reads_keep_results_order_and_the_shared_read_bound():
    refs, _, data = fixtures(12)
    threads, active, peak, lock = set(), [0], [0], threading.Lock()
    def slow(reference):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
            threads.add(threading.get_ident())
        try:
            if reference["id"] == refs[5]["id"]:
                raise RuntimeError("unreadable")
            threading.Event().wait(0.01)
            return fetched(reference)
        finally:
            with lock:
                active[0] -= 1
    sequential = run(Inbox(), Budget(), [], data, max_requests=2, batch_size=6)
    inbox = Inbox()
    parallel = run(inbox, Budget(), [], data, max_requests=2, batch_size=6, fetch=slow, fetch_workers=4)
    assert 1 < peak[0] <= 4 and len(threads) > 1
    assert parallel["sources_checked"] == 12 and parallel["source_unavailable"] == 1 and parallel["classified"] == 11
    assert sequential["classified"] == 12 and parallel["http_requests"] == sequential["http_requests"] == 2
    limited = run(Inbox(), Budget(), [], data, max_requests=1, batch_size=2, fetch_workers=4)
    assert limited["sources_checked"] <= 4 and limited["classified"] == 2


def test_daily_budget_is_shared_among_scheduled_cycles_only_when_declared():
    config, snapshot = SimpleNamespace(batch_ceiling=50), {"remaining_requests": 240}
    morning, evening = (datetime(2026, 11, 10, 13, 7, tzinfo=timezone.utc), datetime(2026, 11, 11, 1, 7, tzinfo=timezone.utc))
    assert gemini_review.hourly_request_allowance(snapshot, config, 50, morning) == (13, 19)
    assert gemini_review.hourly_request_allowance(snapshot, config, 50, morning, {1, 13}) == (50, 2)
    assert gemini_review.hourly_request_allowance(snapshot, config, 16, morning, {1, 13}) == (16, 2)
    assert gemini_review.hourly_request_allowance({"remaining_requests": 60}, config, 50, morning, {1, 13}) == (30, 2)
    assert gemini_review.hourly_request_allowance({"remaining_requests": 30}, config, 50, evening, {1, 13}) == (30, 1)
    assert gemini_review.scheduled_cycle_hours({}) is None
    assert gemini_review.scheduled_cycle_hours({"MDH_CYCLE_HOURS": "1,13"}) == {1, 13}
    for invalid in ("24", "1,1", "1;13", "-1", "1, 13", "a"):
        with pytest.raises(QuotaStateError, match="invalid_cycle_hours"):
            gemini_review.scheduled_cycle_hours({"MDH_CYCLE_HOURS": invalid})


def test_storage_switches_are_explicit_and_default_to_the_previous_behaviour():
    assert gemini_review.scope_storage_options({}) == {}
    assert gemini_review.scope_storage_options({"MDH_SCOPE_BATCH_COMMITS": "false", "MDH_SOURCE_FETCH_WORKERS": "1"}) == {}
    assert gemini_review.scope_storage_options({"MDH_SCOPE_BATCH_COMMITS": "true", "MDH_SOURCE_FETCH_WORKERS": "4"}) == {
        "batch_commits": True, "fetch_workers": 4}
    for invalid in ({"MDH_SCOPE_BATCH_COMMITS": "yes"}, {"MDH_SOURCE_FETCH_WORKERS": "5"}, {"MDH_SOURCE_FETCH_WORKERS": "0x2"}):
        with pytest.raises(ValueError, match="invalid_scope_storage_options"):
            gemini_review.scope_storage_options(invalid)


def test_fixed_pause_only_applies_when_no_ledger_spaces_the_requests():
    from test_gemini_review import Budget as QueueBudget, BudgetWorkers, Extractors, KEY, NOW, PrivateInbox, candidate
    def execute(budget, workers):
        pauses = []
        gemini_review.process_queue(PrivateInbox([candidate(1), candidate(2), candidate(3)]), api_key=KEY,
            model="gemini-2.5-flash", fetch=workers.fetch, extract=workers.extract, now=NOW,
            sleep=pauses.append, **({"budget": budget} if budget else {}))
        return [value for value in pauses if value == 4]
    assert execute(None, Extractors()) == [4, 4]
    budget = QueueBudget()
    assert execute(budget, BudgetWorkers(budget)) == []
