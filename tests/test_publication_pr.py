from copy import deepcopy
import base64
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from mdh_pipeline import publication_pr as publication

BASE = "a" * 40
HEAD = "b" * 40
UPDATED = "c" * 40
ANCESTOR = "d" * 40
BOT = {"id": 41898282, "login": "github-actions[bot]", "type": "Bot"}
OWNER = {"id": 37725305, "login": "RauPro", "type": "User"}
HUMAN = {"id": 123, "login": "contributor", "type": "User"}
PATH = "apps/web/public/archive/processing.json"


def run(branch, sha=HEAD, event="workflow_dispatch", actor=BOT, identifier=91):
    return {
        "id": identifier, "path": ".github/workflows/ci.yml", "name": "Verify public monitor",
        "head_branch": branch, "head_sha": sha, "event": event,
        "status": "completed", "conclusion": "success", "actor": deepcopy(actor),
        "triggering_actor": deepcopy(actor), "repository": {"full_name": publication.REPOSITORY, "id": publication.REPOSITORY_ID},
        "head_repository": {"full_name": publication.REPOSITORY, "id": publication.REPOSITORY_ID},
    }


def pull(branch, sha=HEAD, body=""):
    return {"number": 7, "state": "open", "draft": False, "mergeable": True, "user": deepcopy(BOT), "body": body,
            "head": {"ref": branch, "sha": sha, "repo": {"full_name": publication.REPOSITORY}},
            "base": {"ref": "main", "sha": BASE, "repo": {"full_name": publication.REPOSITORY}}}


def native_run(branch, identifier=501, path=".github/workflows/ci.yml", *, sha=HEAD, base=BASE):
    value = run(branch, sha, "pull_request", BOT, identifier)
    value.update(path=path, name=publication.NATIVE_PR_WORKFLOWS.get(path, "Unexpected workflow"))
    repository = {"id": publication.REPOSITORY_ID, "url": f"https://api.github.com/repos/{publication.REPOSITORY}"}
    value["pull_requests"] = [{"number": 7,
                              "head": {"ref": branch, "sha": sha, "repo": deepcopy(repository)},
                              "base": {"ref": "main", "sha": base, "repo": deepcopy(repository)}}]
    return value


class GitHubFake:
    def __init__(self, kind="history", *, behind=False):
        self.branch = f"chore/automated-{kind}-123-1"
        self.runs = {91: run(self.branch, event="push" if kind == "news" else "workflow_dispatch",
                             actor=OWNER if kind == "news" else BOT)}
        self.pr = None if kind == "news" else pull(self.branch)
        self.head = HEAD
        self.base = BASE
        self.behind = behind
        self.files = {}
        self.trees = {BASE: {}, HEAD: {}, ANCESTOR: {}, UPDATED: {}}
        self.add_file(BASE, PATH, b'{"revision":1}')
        self.add_file(ANCESTOR, PATH, b'{"revision":1}')
        self.add_file(HEAD, PATH, b'{"revision":2}')
        self.add_file(UPDATED, PATH, b'{"revision":2}')
        self.calls = []
        self.truncated = False
        self.merge_result = {"merged": True}
        self.update_delay = 0
        self.update_reads = None
        self.mutate_before_merge = None
        self.first_ref_reads = 0
        self.mergeability_delay = 0
        self.mergeability_reads = 0
        self.dispatch_response = {"workflow_run_id": 92}
        self.dispatched_run_changes = {}
        self.run_sequences = {}
        self.runs[501] = native_run(self.branch)
        self.native_ids = [501]
        self.native_listing_count = None
        self.approval_error = None

    def add_file(self, commit, path, content, *, mode="100644", kind="blob"):
        import hashlib
        sha = hashlib.sha1(content).hexdigest()
        self.files[sha] = content
        self.trees[commit][path] = {"path": path, "mode": mode, "type": kind,
                                     "sha": sha, "size": len(content)}

    def call(self, method, path, payload=None):
        self.calls.append((method, path, deepcopy(payload)))
        path = path.removeprefix(f"/repos/{publication.REPOSITORY}/")
        if method == "GET" and path.startswith("actions/runs?"):
            query = parse_qs(urlsplit("/" + path).query)
            assert query == {"event": ["pull_request"], "head_sha": [HEAD], "per_page": ["100"]}
            entries = [deepcopy(self.runs[identifier]) for identifier in self.native_ids]
            count = len(entries) if self.native_listing_count is None else self.native_listing_count
            return {"total_count": count, "workflow_runs": entries}
        if method == "POST" and path.endswith("/approve"):
            if self.approval_error:
                raise publication.PublicationError(self.approval_error)
            identifier = int(path.split("/")[-2])
            assert identifier in self.native_ids
            self.runs[identifier].update(status="queued", conclusion=None)
            return None
        if method == "GET" and path.startswith("actions/runs/"):
            identifier = int(path.split("/")[-1])
            sequence = self.run_sequences.get(identifier)
            if sequence:
                result = sequence.pop(0) if len(sequence) > 1 else sequence[0]
                if isinstance(result, Exception):
                    raise result
                self.runs[identifier] = deepcopy(result)
            return deepcopy(self.runs[identifier])
        if method == "GET" and path.startswith("git/ref/heads/"):
            branch = path.removeprefix("git/ref/heads/")
            if branch == "main":
                return {"object": {"type": "commit", "sha": self.base}}
            assert branch == self.branch
            self.first_ref_reads += 1
            if self.mutate_before_merge and self.first_ref_reads >= 2:
                self.head = self.mutate_before_merge
            if self.update_reads is not None:
                if self.update_reads >= self.update_delay:
                    self.head = UPDATED
                    self.pr["head"]["sha"] = UPDATED
                self.update_reads += 1
            return {"object": {"type": "commit", "sha": self.head}}
        if method == "GET" and path.startswith("compare/"):
            base, head = path.removeprefix("compare/").split("...")
            behind = self.behind and base == BASE and head == HEAD
            return {"behind_by": int(behind), "merge_base_commit": {"sha": ANCESTOR if behind else base}}
        if method == "GET" and path.startswith("git/commits/"):
            sha = path.split("/")[-1]
            return {"sha": sha, "tree": {"sha": sha}}
        if method == "GET" and path.startswith("git/trees/"):
            sha = path.split("/")[-1].split("?")[0]
            return {"truncated": self.truncated, "tree": list(self.trees[sha].values())}
        if method == "GET" and path.startswith("git/blobs/"):
            sha = path.split("/")[-1]
            return {"sha": sha, "encoding": "base64", "size": len(self.files[sha]),
                    "content": base64.b64encode(self.files[sha]).decode()}
        if method == "GET" and path.startswith("pulls?"):
            assert parse_qs(urlsplit("/" + path).query)["head"] == [f"RauPro:{self.branch}"]
            return [] if self.pr is None else [deepcopy(self.pr)]
        if method == "GET" and path == "pulls/7":
            result = deepcopy(self.pr)
            if self.mergeability_reads < self.mergeability_delay:
                result["mergeable"] = None
            self.mergeability_reads += 1
            return result
        if method == "POST" and path == "pulls":
            assert self.pr is None
            self.pr = pull(self.branch, self.head, payload["body"])
            return deepcopy(self.pr)
        if method == "PUT" and path == "pulls/7/merge":
            assert payload["sha"] == HEAD
            assert payload["merge_method"] == "squash"
            return self.merge_result
        if method == "PUT" and path == "pulls/7/update-branch":
            assert payload == {"expected_head_sha": HEAD}
            self.update_reads = 0
            return {"message": "Updating pull request branch."}
        if method == "POST" and path == "actions/workflows/ci.yml/dispatches":
            assert payload == {"ref": self.branch, "return_run_details": True}
            self.runs[92] = run(self.branch, self.head, identifier=92)
            self.runs[92].update(deepcopy(self.dispatched_run_changes))
            return self.dispatch_response
        if method == "POST" and path == "actions/workflows/publication-pr.yml/dispatches":
            assert payload == {"ref": "main", "inputs": {"ci_run_id": "92"}}
            return None
        raise AssertionError((method, path, payload))

    def event(self):
        return {"repository": {"full_name": publication.REPOSITORY},
                "workflow_run": deepcopy(self.runs[91])}

    def writes(self):
        return [(method, path, payload) for method, path, payload in self.calls if method != "GET"]


@pytest.mark.parametrize("kind", ["history", "exception", "news"])
def test_merge_exact_successful_json_snapshot(kind):
    client = GitHubFake(kind)
    assert publication.control(client, client.event()) == "merged_with_required_checks"
    assert client.writes()[-1][2]["sha"] == HEAD
    assert all("admin" not in json.dumps(payload) for _, _, payload in client.writes())
    if kind == "news":
        assert "<!-- mdh-publication-origin: 91 -->" in client.pr["body"]


@pytest.mark.parametrize("kind", ["history", "exception"])
def test_proposal_creates_bot_pr_and_explicitly_dispatches_ci(kind):
    client = GitHubFake(kind)
    client.pr = None
    assert publication.propose(client, client.branch, kind) == "proposed_and_ci_dispatched"
    assert [path.rsplit("/", 1)[-1] for _, path, _ in client.writes()] == ["pulls", "dispatches", "dispatches"]


def test_proposal_will_not_reuse_human_pr():
    client = GitHubFake()
    client.pr["user"] = HUMAN
    with pytest.raises(publication.PublicationError, match="untrusted_publication_pull_request"):
        publication.propose(client, client.branch, "history")
    assert not client.writes()


@pytest.mark.parametrize("field,value", [
    ("head_branch", "feat/a"), ("head_branch", "chore/automated-history-1-1/extra"),
    ("head_branch", "chore/automated-history-0-1"),
    ("event", "pull_request"), ("conclusion", "failure"), ("conclusion", "cancelled"),
    ("path", ".github/workflows/evil.yml"),
])
def test_unrelated_or_unsuccessful_events_are_ignored(field, value):
    client = GitHubFake()
    event = client.event()
    event["workflow_run"][field] = value
    assert publication.control(client, event) == "ignored_ci_run"
    assert not client.calls


def test_wrong_repository_ignored():
    client = GitHubFake()
    event = client.event()
    event["repository"]["full_name"] = "someone/fork"
    assert publication.control(client, event) == "ignored_repository"
    assert not client.calls


@pytest.mark.parametrize("field,value", [
    ("path", ".github/workflows/evil.yml"), ("name", "Fake CI"),
    ("event", "pull_request_target"), ("status", "in_progress"), ("conclusion", "failure"),
    ("repository", {"full_name": "someone/fork"}),
    ("head_repository", {"full_name": "someone/fork"}),
])
def test_reloads_and_authenticates_current_ci_record(field, value):
    client = GitHubFake()
    event = client.event()
    client.runs[91][field] = value
    with pytest.raises(publication.PublicationError, match="untrusted_or_unsuccessful_ci_run"):
        publication.control(client, event)
    assert not client.writes()


def test_stale_ci_never_merges_new_head():
    client = GitHubFake()
    client.head = UPDATED
    assert publication.control(client, client.event()) == "ignored_stale_ci_head"
    assert not client.writes()


def test_race_while_validating_is_detected_before_mutation():
    client = GitHubFake()
    client.mutate_before_merge = UPDATED
    with pytest.raises(publication.PublicationError, match="refs_changed"):
        publication.control(client, client.event())
    assert not client.writes()


@pytest.mark.parametrize("kind", ["history", "exception", "news"])
@pytest.mark.parametrize("field", ["actor", "triggering_actor"])
def test_human_run_cannot_hijack_bot_branch(kind, field):
    client = GitHubFake(kind)
    client.runs[91][field] = HUMAN
    with pytest.raises(publication.PublicationError, match="ci_actor"):
        publication.control(client, client.event())
    assert not client.writes()


@pytest.mark.parametrize("mutation", ["author", "head_repo", "base_repo", "base", "head", "sha", "draft"])
def test_existing_pr_must_be_trusted_and_match_current_refs(mutation):
    client = GitHubFake()
    if mutation == "author":
        client.pr["user"] = HUMAN
    elif mutation in {"head_repo", "base_repo"}:
        client.pr[mutation.split("_")[0]]["repo"]["full_name"] = "someone/fork"
    elif mutation in {"base", "head"}:
        client.pr[mutation]["ref"] = "wrong-branch"
    elif mutation == "sha":
        client.pr["head"]["sha"] = UPDATED
    else:
        client.pr["draft"] = True
    with pytest.raises(publication.PublicationError, match="untrusted_publication_pull_request"):
        publication.control(client, client.event())
    assert not client.writes()


@pytest.mark.parametrize("path", [".github/workflows/ci.yml", "server/public-data.mjs", "data/history/paper.json",
                                  "data/selection/news_exceptions.json", "apps/web/public/archive/extra.json"])
def test_complete_tree_comparison_rejects_non_allowlisted_changes(path):
    client = GitHubFake()
    client.add_file(HEAD, path, b'{"extra":true}')
    with pytest.raises(publication.PublicationError, match="outside_allowlist"):
        publication.control(client, client.event())
    assert not client.writes()


def test_comparison_does_not_depend_on_truncated_compare_files_listing():
    client = GitHubFake()
    for number in range(301):
        client.add_file(HEAD, f"hidden/{number}.json", b'{}')
    with pytest.raises(publication.PublicationError, match="outside_allowlist"):
        publication.control(client, client.event())
    assert not client.writes()


@pytest.mark.parametrize("mode,kind", [("120000", "blob"), ("100755", "blob"), ("160000", "commit")])
def test_symlink_executable_and_submodule_rejected(mode, kind):
    client = GitHubFake()
    client.add_file(HEAD, PATH, b'{}', mode=mode, kind=kind)
    with pytest.raises(publication.PublicationError, match="regular_json"):
        publication.control(client, client.event())
    assert not client.writes()


def test_deleted_snapshot_rejected():
    client = GitHubFake()
    del client.trees[HEAD][PATH]
    with pytest.raises(publication.PublicationError, match="regular_json"):
        publication.control(client, client.event())
    assert not client.writes()


@pytest.mark.parametrize("content", [b'not json', b'{"x":NaN}', b'{"x":1,"x":2}', b'"scalar"'])
def test_non_strict_json_rejected(content):
    client = GitHubFake()
    client.add_file(HEAD, PATH, content)
    with pytest.raises(publication.PublicationError, match="invalid_.*json"):
        publication.control(client, client.event())
    assert not client.writes()


def test_truncated_git_tree_rejected():
    client = GitHubFake()
    client.truncated = True
    with pytest.raises(publication.PublicationError, match="incomplete_git_tree"):
        publication.control(client, client.event())
    assert not client.writes()


def test_empty_snapshot_not_published():
    client = GitHubFake()
    client.trees[HEAD] = deepcopy(client.trees[BASE])
    assert publication.control(client, client.event()) == "ignored_empty_publication"
    assert not client.writes()


def test_behind_branch_waits_for_update_and_dispatches_new_ci(monkeypatch):
    waits = []
    monkeypatch.setattr(publication.time, "sleep", waits.append)
    client = GitHubFake(behind=True)
    client.update_delay = 2
    assert publication.control(client, client.event()) == "branch_updated_ci_dispatched"
    assert waits == [2, 2]
    assert [path.rsplit("/", 1)[-1] for _, path, _ in client.writes()] == ["update-branch", "dispatches", "dispatches"]
    assert client.head == UPDATED


def test_pending_update_is_visible_failure_without_dispatch(monkeypatch):
    monkeypatch.setattr(publication.time, "sleep", lambda _: None)
    client = GitHubFake(behind=True)
    client.update_delay = 100
    with pytest.raises(publication.PublicationError, match="branch_update_pending"):
        publication.control(client, client.event())
    assert len(client.writes()) == 1 and client.writes()[0][1].endswith("update-branch")


def test_updated_tree_is_revalidated_before_dispatch(monkeypatch):
    monkeypatch.setattr(publication.time, "sleep", lambda _: None)
    client = GitHubFake(behind=True)
    client.add_file(UPDATED, "server/evil.mjs", b'{}')
    with pytest.raises(publication.PublicationError, match="outside_allowlist"):
        publication.control(client, client.event())
    assert len(client.writes()) == 1


def test_protection_rejection_is_not_bypassed():
    client = GitHubFake()
    client.merge_result = {"merged": False, "message": "blocked"}
    with pytest.raises(publication.PublicationError, match="protected_merge_not_accepted"):
        publication.control(client, client.event())
    assert len(client.writes()) == 1


def test_news_updated_by_bot_revalidates_original_owner_push():
    client = GitHubFake("news")
    client.pr = pull(client.branch, body="<!-- mdh-publication-origin: 90 -->")
    client.runs[90] = run(client.branch, BASE, "push", OWNER, 90)
    client.runs[91] = run(client.branch)
    assert publication.control(client, client.event()) == "merged_with_required_checks"
    assert ("GET", publication.api("actions/runs/90"), None) in client.calls


@pytest.mark.parametrize("origin", [None, "bad_actor", "other_branch", "wrong_workflow"])
def test_news_origin_marker_is_not_trusted_without_api_evidence(origin):
    client = GitHubFake("news")
    client.pr = pull(client.branch, body="" if origin is None else "<!-- mdh-publication-origin: 90 -->")
    client.runs[90] = run(client.branch, BASE, "push", OWNER, 90)
    client.runs[91] = run(client.branch)
    if origin == "bad_actor":
        client.runs[90]["actor"] = HUMAN
    if origin == "other_branch":
        client.runs[90]["head_branch"] = "chore/automated-news-99-1"
    if origin == "wrong_workflow":
        client.runs[90]["path"] = ".github/workflows/fake.yml"
    with pytest.raises(publication.PublicationError):
        publication.control(client, client.event())
    assert not client.writes()


def test_news_dispatch_cannot_create_origin():
    client = GitHubFake("news")
    client.runs[91] = run(client.branch)
    with pytest.raises(publication.PublicationError, match="origin_requires_owner_push"):
        publication.control(client, client.event())
    assert not client.writes()


def test_api_client_rejects_external_paths_and_does_not_follow_redirects():
    client = publication.GitHub("unit-test-token")
    for path in ["https://attacker.invalid", "//attacker.invalid", "/repos/other/repo/pulls"]:
        with pytest.raises(publication.PublicationError, match="invalid_api_path"):
            client.call("GET", path)
    assert publication._NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.invalid") is None


def test_new_news_pr_waits_for_github_mergeability(monkeypatch):
    waits = []
    monkeypatch.setattr(publication.time, "sleep", waits.append)
    client = GitHubFake("news")
    client.mergeability_delay = 2
    assert publication.control(client, client.event()) == "merged_with_required_checks"
    assert waits == [2, 2]
    assert client.mergeability_reads == 3


def test_pending_mergeability_fails_without_attempting_merge(monkeypatch):
    monkeypatch.setattr(publication.time, "sleep", lambda _: None)
    client = GitHubFake("news")
    client.mergeability_delay = 100
    with pytest.raises(publication.PublicationError, match="mergeability_pending"):
        publication.control(client, client.event())
    assert len(client.writes()) == 1 and client.writes()[0][0] == "POST"


def test_conflicting_json_is_never_resolved_by_overwriting():
    client = GitHubFake(behind=True)
    client.pr["mergeable"] = False
    with pytest.raises(publication.PublicationError, match="publication_merge_conflict"):
        publication.control(client, client.event())
    assert not client.writes()


@pytest.mark.parametrize("response", [None, {}, {"workflow_run_id": True},
                                      {"workflow_run_id": "92"}, {"workflow_run_id": 0}])
def test_dispatch_requires_exact_numeric_run_id_from_github(response):
    client = GitHubFake()
    client.dispatch_response = response
    with pytest.raises(publication.PublicationError, match="ci_dispatch_missing_run_id"):
        publication.dispatch(client, client.branch)
    assert len(client.writes()) == 1


@pytest.mark.parametrize("field,value", [
    ("id", 93), ("head_sha", UPDATED), ("head_branch", "chore/automated-history-999-1"),
    ("path", ".github/workflows/fake.yml"), ("actor", HUMAN), ("triggering_actor", HUMAN),
    ("event", "push"), ("repository", {"full_name": "someone/fork"}),
])
def test_dispatch_reads_back_exact_ci_identity_before_arming_controller(field, value):
    client = GitHubFake()
    client.dispatched_run_changes[field] = value
    with pytest.raises(publication.PublicationError):
        publication.dispatch(client, client.branch)
    assert len(client.writes()) == 1


def test_dispatch_arms_controller_on_main_with_exact_returned_id():
    client = GitHubFake()
    publication.dispatch(client, client.branch)
    assert client.writes()[-1] == (
        "POST", publication.api("actions/workflows/publication-pr.yml/dispatches"),
        {"ref": "main", "inputs": {"ci_run_id": "92"}},
    )
    assert ("GET", publication.api("actions/runs/92"), None) in client.calls


def test_dispatched_run_readback_tolerates_only_bounded_visibility_delay(monkeypatch):
    waits = []
    monkeypatch.setattr(publication.time, "sleep", waits.append)
    client = GitHubFake()
    client.run_sequences[92] = [publication.PublicationError("github_get_failed_status_404"),
                                run(client.branch, identifier=92)]
    publication.dispatch(client, client.branch)
    assert waits == [2]
    assert len(client.writes()) == 2


def test_dispatched_run_readback_does_not_hide_permission_failures(monkeypatch):
    monkeypatch.setattr(publication.time, "sleep", lambda _: pytest.fail("unexpected retry"))
    client = GitHubFake()
    client.run_sequences[92] = [publication.PublicationError("github_get_failed_status_403")]
    with pytest.raises(publication.PublicationError, match="403"):
        publication.dispatch(client, client.branch)
    assert len(client.writes()) == 1


def dispatch_event(identifier="91"):
    return {"repository": {"full_name": publication.REPOSITORY}, "inputs": {"ci_run_id": identifier}}


def test_explicit_controller_waits_for_ci_then_reuses_all_merge_guards(monkeypatch):
    waits = []
    monkeypatch.setattr(publication.time, "sleep", waits.append)
    client = GitHubFake()
    complete = deepcopy(client.runs[91])
    pending = dict(complete, status="in_progress", conclusion=None)
    client.run_sequences[91] = [pending, pending, complete]
    assert publication.control_dispatch(client, dispatch_event(), "refs/heads/main") == "merged_with_required_checks"
    assert waits == [10, 10]
    assert client.writes()[-1][2]["sha"] == HEAD


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out", "action_required", "skipped"])
def test_explicit_controller_never_promotes_unsuccessful_ci(conclusion):
    client = GitHubFake()
    client.runs[91]["conclusion"] = conclusion
    with pytest.raises(publication.PublicationError, match="dispatched_ci_unsuccessful"):
        publication.control_dispatch(client, dispatch_event(), "refs/heads/main")
    assert not client.writes()


def test_explicit_controller_ci_wait_is_bounded(monkeypatch):
    waits = []
    monkeypatch.setattr(publication.time, "sleep", waits.append)
    monkeypatch.setattr(publication, "CI_WAIT_SECONDS", 20)
    client = GitHubFake()
    client.runs[91].update(status="queued", conclusion=None)
    with pytest.raises(publication.PublicationError, match="dispatched_ci_wait_timeout"):
        publication.control_dispatch(client, dispatch_event(), "refs/heads/main")
    assert waits == [10, 10]
    assert not client.writes()


@pytest.mark.parametrize("identifier", [None, "", "0", "01", "-1", "1;echo injected", "9" * 21, 91, True])
def test_explicit_controller_rejects_invalid_input_before_api_calls(identifier):
    client = GitHubFake()
    with pytest.raises(publication.PublicationError, match="invalid_dispatched_ci_run_id"):
        publication.control_dispatch(client, dispatch_event(identifier), "refs/heads/main")
    assert not client.calls


@pytest.mark.parametrize("ref", ["main", "refs/heads/feat/hijack", "refs/tags/main", None])
def test_explicit_controller_only_runs_on_default_branch(ref):
    client = GitHubFake()
    with pytest.raises(publication.PublicationError, match="requires_main"):
        publication.control_dispatch(client, dispatch_event(), ref)
    assert not client.calls


@pytest.mark.parametrize("field,value", [("actor", HUMAN), ("triggering_actor", HUMAN),
                                         ("id", 123), ("path", ".github/workflows/evil.yml")])
def test_explicit_controller_authenticates_before_waiting(monkeypatch, field, value):
    monkeypatch.setattr(publication.time, "sleep", lambda _: pytest.fail("unexpected wait"))
    client = GitHubFake()
    client.runs[91].update(status="queued", conclusion=None)
    client.runs[91][field] = value
    with pytest.raises(publication.PublicationError):
        publication.control_dispatch(client, dispatch_event(), "refs/heads/main")
    assert not client.writes()


@pytest.mark.parametrize("field,value", [("head_sha", UPDATED), ("head_branch", "chore/automated-history-1-2"),
                                         ("triggering_actor", HUMAN), ("id", 99)])
def test_explicit_controller_revalidates_identity_on_each_poll(monkeypatch, field, value):
    monkeypatch.setattr(publication.time, "sleep", lambda _: None)
    client = GitHubFake()
    pending = dict(client.runs[91], status="in_progress", conclusion=None)
    changed = dict(client.runs[91])
    changed[field] = value
    client.run_sequences[91] = [pending, changed]
    with pytest.raises(publication.PublicationError):
        publication.control_dispatch(client, dispatch_event(), "refs/heads/main")
    assert not client.writes()


def test_explicit_controller_discards_stale_candidate_without_waiting(monkeypatch):
    monkeypatch.setattr(publication.time, "sleep", lambda _: pytest.fail("unexpected wait"))
    client = GitHubFake()
    client.head = UPDATED
    client.runs[91].update(status="queued", conclusion=None)
    assert publication.control_dispatch(client, dispatch_event(), "refs/heads/main") == "ignored_stale_ci_head"
    assert not client.writes()


def test_explicit_controller_still_enforces_full_tree_allowlist():
    client = GitHubFake()
    client.add_file(HEAD, ".github/workflows/ci.yml", b'{}')
    with pytest.raises(publication.PublicationError, match="outside_allowlist"):
        publication.control_dispatch(client, dispatch_event(), "refs/heads/main")
    assert not client.writes()


def test_cli_dispatch_uses_event_file_not_shell_interpolation(monkeypatch, tmp_path):
    client = GitHubFake()
    event = tmp_path / "event.json"
    event.write_text(json.dumps(dispatch_event()))
    monkeypatch.setenv("GITHUB_REPOSITORY", publication.REPOSITORY)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setattr(publication, "GitHub", lambda _: client)
    assert publication.main([]) == 0
    assert client.writes()[-1][1].endswith("/merge")


def test_controller_workflow_has_bounded_default_branch_dispatch_and_queue():
    from pathlib import Path
    import yaml
    document = yaml.load((Path(__file__).resolve().parents[1] / ".github/workflows/publication-pr.yml").read_text(), Loader=yaml.BaseLoader)
    assert document["on"]["workflow_dispatch"]["inputs"]["ci_run_id"]["type"] == "string"
    assert document["concurrency"] == {"group": "public-snapshot-promotion", "cancel-in-progress": "false", "queue": "max"}
    job = document["jobs"]["promote"]
    assert "github.ref == 'refs/heads/main'" in job["if"]
    assert int(job["timeout-minutes"]) * 60 > publication.CI_WAIT_SECONDS
    assert job["steps"][0]["with"]["ref"] == "${{ github.workflow_sha }}"
    assert "inputs.ci_run_id" not in str(job["steps"])


def test_dispatch_source_waits_for_native_pr_checks_before_merge():
    client = GitHubFake()
    assert publication.control(client, client.event()) == "merged_with_required_checks"
    assert any(path.startswith(publication.api("actions/runs?")) for _, path, _ in client.calls)
    native_read = client.calls.index(("GET", publication.api("actions/runs/501"), None))
    merge = next(index for index, (method, path, _) in enumerate(client.calls) if method == "PUT" and path.endswith("/merge"))
    assert native_read < merge


def test_owner_push_ci_already_satisfies_protection_without_native_approval():
    client = GitHubFake("news")
    assert publication.control(client, client.event()) == "merged_with_required_checks"
    assert not any("actions/runs?" in path or path.endswith("/approve") for _, path, _ in client.calls)


def test_native_ci_and_optional_configuration_are_approved_once_then_awaited(monkeypatch):
    waits = []
    monkeypatch.setattr(publication.time, "sleep", waits.append)
    client = GitHubFake()
    for identifier, path in [(501, ".github/workflows/ci.yml"), (502, ".github/workflows/deploy_web.yml")]:
        ready = native_run(client.branch, identifier, path)
        approval = dict(ready, conclusion="action_required")
        client.runs[identifier] = approval
        client.run_sequences[identifier] = [approval, approval, ready]
    client.native_ids = [501, 502]
    assert publication.control(client, client.event()) == "merged_with_required_checks"
    assert waits == [10, 10]
    assert [path.rsplit("/", 2)[-2:] for _, path, _ in client.writes()] == [
        ["501", "approve"], ["502", "approve"], ["7", "merge"],
    ]


@pytest.mark.parametrize("field,value", [
    ("event", "workflow_dispatch"), ("name", "Misleading verification"),
    ("head_sha", UPDATED), ("head_branch", "feat/untrusted"),
    ("actor", HUMAN), ("triggering_actor", HUMAN),
    ("repository", {"id": 1, "full_name": publication.REPOSITORY}),
    ("head_repository", {"id": publication.REPOSITORY_ID, "full_name": "someone/fork"}),
    ("pull_requests", []), ("status", "unexpected"),
])
def test_native_run_identity_is_checked_before_approval(field, value):
    client = GitHubFake()
    client.runs[501]["conclusion"] = "action_required"
    client.runs[501][field] = value
    with pytest.raises(publication.PublicationError):
        publication.control(client, client.event())
    assert not client.writes()


@pytest.mark.parametrize("side,field,value", [
    ("head", "sha", UPDATED), ("head", "ref", "feat/untrusted"),
    ("base", "sha", ANCESTOR), ("base", "ref", "other-main"),
    ("head", "repo", {"id": 1, "url": f"https://api.github.com/repos/{publication.REPOSITORY}"}),
    ("base", "repo", {"id": publication.REPOSITORY_ID, "url": "https://api.github.com/repos/someone/fork"}),
])
def test_native_run_pr_association_matches_both_repo_ids_and_exact_refs(side, field, value):
    client = GitHubFake()
    client.runs[501]["conclusion"] = "action_required"
    client.runs[501]["pull_requests"][0][side][field] = value
    with pytest.raises(publication.PublicationError):
        publication.control(client, client.event())
    assert not client.writes()


def test_native_pr_number_cannot_be_substituted():
    client = GitHubFake()
    client.runs[501]["pull_requests"][0]["number"] = 8
    with pytest.raises(publication.PublicationError, match="association"):
        publication.control(client, client.event())
    assert not client.writes()


@pytest.mark.parametrize("count", [101, 2, -1, True])
def test_native_run_listing_must_be_complete(count):
    client = GitHubFake()
    client.native_listing_count = count
    with pytest.raises(publication.PublicationError, match="incomplete_native"):
        publication.control(client, client.event())
    assert not client.writes()


def test_native_run_listing_cannot_repeat_ids():
    client = GitHubFake()
    client.native_ids = [501, 501]
    with pytest.raises(publication.PublicationError, match="invalid_native_pr_run_listing"):
        publication.control(client, client.event())
    assert not client.writes()


def test_native_readback_identity_checked_before_any_approval():
    client = GitHubFake()
    client.runs[501]["conclusion"] = "action_required"
    fresh = dict(client.runs[501], id=999)
    client.run_sequences[501] = [fresh]
    with pytest.raises(publication.PublicationError, match="changed_native_pr_run_identity"):
        publication.control(client, client.event())
    assert not client.writes()


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out", "skipped"])
def test_failed_native_ci_never_merges_or_approves_another_run(conclusion):
    client = GitHubFake()
    client.runs[501]["conclusion"] = conclusion
    client.runs[502] = native_run(client.branch, 502, ".github/workflows/deploy_web.yml")
    client.runs[502]["conclusion"] = "action_required"
    client.native_ids = [502, 501]  # Validate all conclusions before approvals.
    with pytest.raises(publication.PublicationError, match="native_pr_ci_unsuccessful"):
        publication.control(client, client.event())
    assert not client.writes()


def test_missing_native_ci_times_out_without_approving_only_configuration(monkeypatch):
    monkeypatch.setattr(publication, "CI_WAIT_SECONDS", 20)
    monkeypatch.setattr(publication.time, "sleep", lambda _: None)
    client = GitHubFake()
    client.runs[502] = native_run(client.branch, 502, ".github/workflows/deploy_web.yml")
    client.runs[502]["conclusion"] = "action_required"
    client.native_ids = [502]
    with pytest.raises(publication.PublicationError, match="native_pr_ci_wait_timeout"):
        publication.control(client, client.event())
    assert not client.writes()


def test_unknown_workflows_are_never_approved(monkeypatch):
    monkeypatch.setattr(publication, "CI_WAIT_SECONDS", 0)
    client = GitHubFake()
    client.runs[501]["path"] = ".github/workflows/evil.yml"
    client.runs[501]["conclusion"] = "action_required"
    with pytest.raises(publication.PublicationError, match="native_pr_ci_wait_timeout"):
        publication.control(client, client.event())
    assert not client.writes()


def test_native_approval_permission_failure_is_visible_without_bypass():
    client = GitHubFake()
    client.runs[501]["conclusion"] = "action_required"
    client.approval_error = "github_post_failed_status_403"
    with pytest.raises(publication.PublicationError, match="403"):
        publication.control(client, client.event())
    assert len(client.writes()) == 1 and client.writes()[0][1].endswith("/501/approve")


def test_native_approval_timeout_does_not_repeat_approval_or_merge(monkeypatch):
    monkeypatch.setattr(publication, "CI_WAIT_SECONDS", 20)
    monkeypatch.setattr(publication.time, "sleep", lambda _: None)
    client = GitHubFake()
    client.runs[501]["conclusion"] = "action_required"
    with pytest.raises(publication.PublicationError, match="native_pr_ci_wait_timeout"):
        publication.control(client, client.event())
    assert len(client.writes()) == 1 and client.writes()[0][1].endswith("/501/approve")


@pytest.mark.parametrize("changed_ref", ["head", "base"])
def test_refs_are_rechecked_while_native_ci_runs(monkeypatch, changed_ref):
    client = GitHubFake()
    client.runs[501]["conclusion"] = "action_required"
    monkeypatch.setattr(publication.time, "sleep", lambda _: setattr(client, changed_ref, UPDATED))
    with pytest.raises(publication.PublicationError, match="publication_refs_changed"):
        publication.control(client, client.event())
    assert len(client.writes()) == 1 and client.writes()[0][1].endswith("/501/approve")


def test_native_wait_rechecks_pr_base_sha_before_approving():
    client = GitHubFake()
    client.pr["base"]["sha"] = ANCESTOR
    client.runs[501]["conclusion"] = "action_required"
    with pytest.raises(publication.PublicationError, match="publication_pr_base_changed"):
        publication.control(client, client.event())
    assert not client.writes()


def test_latest_native_run_for_each_workflow_must_pass(monkeypatch):
    monkeypatch.setattr(publication.time, "sleep", lambda _: None)
    client = GitHubFake()
    client.runs[502] = native_run(client.branch, 502)
    client.runs[502]["conclusion"] = "failure"
    client.native_ids = [501, 502]
    with pytest.raises(publication.PublicationError, match="native_pr_ci_unsuccessful"):
        publication.control(client, client.event())
    assert not client.writes()
