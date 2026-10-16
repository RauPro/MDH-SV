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
        "triggering_actor": deepcopy(actor), "repository": {"full_name": publication.REPOSITORY},
        "head_repository": {"full_name": publication.REPOSITORY},
    }


def pull(branch, sha=HEAD, body=""):
    return {"number": 7, "state": "open", "draft": False, "mergeable": True, "user": deepcopy(BOT), "body": body,
            "head": {"ref": branch, "sha": sha, "repo": {"full_name": publication.REPOSITORY}},
            "base": {"ref": "main", "repo": {"full_name": publication.REPOSITORY}}}


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

    def add_file(self, commit, path, content, *, mode="100644", kind="blob"):
        import hashlib
        sha = hashlib.sha1(content).hexdigest()
        self.files[sha] = content
        self.trees[commit][path] = {"path": path, "mode": mode, "type": kind,
                                     "sha": sha, "size": len(content)}

    def call(self, method, path, payload=None):
        self.calls.append((method, path, deepcopy(payload)))
        path = path.removeprefix(f"/repos/{publication.REPOSITORY}/")
        if method == "GET" and path.startswith("actions/runs/"):
            return deepcopy(self.runs[int(path.split("/")[-1])])
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
            assert payload == {"ref": self.branch}
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
    assert [path.rsplit("/", 1)[-1] for _, path, _ in client.writes()] == ["pulls", "dispatches"]


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
    assert [path.rsplit("/", 1)[-1] for _, path, _ in client.writes()] == ["update-branch", "dispatches"]
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
