"""Promote bounded data-only bot PRs after CI, without bypassing branch protection.

The controller runs from the default branch, never imports or checks out the
candidate, and validates complete Git trees rather than GitHub's truncated PR
file listing. The public workflow token cannot read the private review queue.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .instance import load_instance

INSTANCE = load_instance()
REPOSITORY = INSTANCE.public_repository
REPOSITORY_ID = INSTANCE.public_repository_id
NEWS_PUBLISHER_ACTOR_ID = INSTANCE.publisher_actor_id
BOT_ID = 41898282
BRANCH = re.compile(r"chore/automated-(history|exception|news)-([1-9][0-9]*)-([1-9][0-9]*)")
SHA = re.compile(r"[a-f0-9]{40}")
ORIGIN = re.compile(r"<!-- mdh-publication-origin: ([1-9][0-9]*) -->")
MAX_RESPONSE = 48_000_000
MAX_JSON = 32_000_000
MAX_TOTAL_JSON = 64_000_000
CI_WAIT_SECONDS = 18 * 60
CI_POLL_SECONDS = 10
NATIVE_PR_WORKFLOWS = {
    ".github/workflows/ci.yml": "Verify public monitor",
    ".github/workflows/deploy_web.yml": "Deployment configuration check",
}
PROJECTIONS = frozenset({
    "data/automated/news_dossiers.json",
    "apps/web/public/archive/news.json",
    "apps/web/public/archive/processing.json",
    "apps/web/public/archive/dossiers.json",
    "apps/web/public/archive/locations.json",
})
ALLOWED = {
    "history": PROJECTIONS | frozenset(f"data/history/{name}.json" for name in (
        "co-latino", "gatoencerrado", "factum", "el-faro", "el-mundo",
        "la-prensa-grafica", "el-diario-de-hoy")),
    "exception": PROJECTIONS | {"data/selection/news_exceptions.json"},
    "news": PROJECTIONS | {"data/selection/news.json"},
}


class PublicationError(RuntimeError):
    """A bounded, non-sensitive failure safe to print in Actions logs."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHub:
    def __init__(self, token):
        if not token or "\n" in token or "\r" in token:
            raise PublicationError("missing_or_invalid_token")
        self.token = token
        self.opener = build_opener(_NoRedirect())

    def call(self, method, path, payload=None):
        if not path.startswith(f"/repos/{REPOSITORY}/") or "#" in path:
            raise PublicationError("invalid_api_path")
        request = Request("https://api.github.com" + path, method=method,
                          data=None if payload is None else json.dumps(payload).encode(), headers={
                              "Authorization": f"Bearer {self.token}",
                              "Accept": "application/vnd.github+json",
                              "Content-Type": "application/json",
                              "X-GitHub-Api-Version": "2022-11-28",
                              "User-Agent": "mdh-publication-controller",
                          })
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read(MAX_RESPONSE + 1)
        except HTTPError as exc:
            # Do not print API bodies, request headers, or candidate data.
            raise PublicationError(f"github_{method.lower()}_failed_status_{exc.code}") from None
        except (URLError, TimeoutError, OSError):
            raise PublicationError("github_request_failed") from None
        if len(raw) > MAX_RESPONSE:
            raise PublicationError("github_response_too_large")
        try:
            return json.loads(raw) if raw else None
        except (ValueError, UnicodeError):
            raise PublicationError("invalid_github_json") from None


def api(path):
    return f"/repos/{REPOSITORY}/{path}"


def require(condition, message):
    if not condition:
        raise PublicationError(message)


def valid_sha(value):
    return isinstance(value, str) and SHA.fullmatch(value) is not None


def bot(actor):
    return (isinstance(actor, dict) and actor.get("id") == BOT_ID
            and actor.get("login") == "github-actions[bot]" and actor.get("type") == "Bot")


def bot_run(run):
    return bot(run.get("actor")) and bot(run.get("triggering_actor"))


def owner_push(run):
    return (run.get("event") == "push"
            and run.get("actor", {}).get("id") == NEWS_PUBLISHER_ACTOR_ID
            and run.get("triggering_actor", {}).get("id") == NEWS_PUBLISHER_ACTOR_ID)


def validate_run(run, branch, *, allow_pending=False):
    require(isinstance(run, dict)
            and run.get("repository", {}).get("full_name") == REPOSITORY
            and run.get("head_repository", {}).get("full_name") == REPOSITORY
            and run.get("path") == ".github/workflows/ci.yml"
            and run.get("name") == "Verify public monitor"
            and run.get("head_branch") == branch
            and valid_sha(run.get("head_sha"))
            and run.get("event") in {"push", "workflow_dispatch"}
            and ((allow_pending and run.get("status") in {
                "queued", "requested", "waiting", "pending", "in_progress", "completed"})
                 or (run.get("status") == "completed" and run.get("conclusion") == "success")),
            "untrusted_or_unsuccessful_ci_run")


def ref_sha(client, branch):
    result = client.call("GET", api("git/ref/heads/" + quote(branch, safe="/")))
    require(isinstance(result, dict) and result.get("object", {}).get("type") == "commit"
            and valid_sha(result.get("object", {}).get("sha")), "invalid_branch_reference")
    return result["object"]["sha"]


def compare(client, base, head):
    value = client.call("GET", api(f"compare/{base}...{head}"))
    require(isinstance(value, dict) and type(value.get("behind_by")) is int
            and value["behind_by"] >= 0 and valid_sha(value.get("merge_base_commit", {}).get("sha")),
            "invalid_commit_comparison")
    return value


def tree(client, commit):
    commit_value = client.call("GET", api(f"git/commits/{commit}"))
    require(isinstance(commit_value, dict) and commit_value.get("sha") == commit
            and valid_sha(commit_value.get("tree", {}).get("sha")), "invalid_git_commit")
    value = client.call("GET", api(f"git/trees/{commit_value['tree']['sha']}?recursive=1"))
    require(isinstance(value, dict) and value.get("truncated") is False
            and isinstance(value.get("tree"), list) and len(value["tree"]) <= 100_000,
            "incomplete_git_tree")
    result = {}
    for entry in value["tree"]:
        require(isinstance(entry, dict) and isinstance(entry.get("path"), str)
                and entry["path"] not in result and valid_sha(entry.get("sha")), "invalid_tree_entry")
        # Trees themselves change when any descendant changes. Compare leaves;
        # symlinks and submodules remain leaves and are explicitly rejected.
        if entry.get("type") != "tree":
            result[entry["path"]] = entry
    return result


def validate_changes(client, base, head, kind):
    before, after = tree(client, base), tree(client, head)
    changed = {path for path in before.keys() | after.keys()
               if (before.get(path, {}).get("sha"), before.get(path, {}).get("mode"))
               != (after.get(path, {}).get("sha"), after.get(path, {}).get("mode"))}
    require(changed <= ALLOWED[kind], "publication_changes_outside_allowlist")
    total = 0
    for path in sorted(changed):
        entry = after.get(path, {})
        require(entry.get("type") == "blob" and entry.get("mode") == "100644"
                and type(entry.get("size")) is int and 0 < entry["size"] <= MAX_JSON,
                "publication_requires_regular_json_files")
        total += entry["size"]
        require(total <= MAX_TOTAL_JSON, "publication_json_limit_exceeded")
        value = client.call("GET", api(f"git/blobs/{entry['sha']}"))
        require(isinstance(value, dict) and value.get("sha") == entry["sha"]
                and value.get("encoding") == "base64" and value.get("size") == entry["size"]
                and isinstance(value.get("content"), str)
                and len(value["content"]) <= MAX_RESPONSE, "invalid_json_blob")
        try:
            content = base64.b64decode(value["content"].replace("\n", ""), validate=True)
            require(len(content) == entry["size"], "invalid_json_blob_size")
            def unique(pairs):
                result = {}
                for key, item in pairs:
                    if key in result:
                        raise ValueError("duplicate_key")
                    result[key] = item
                return result
            def invalid_constant(_):
                raise ValueError("non_json_constant")
            parsed = json.loads(content, object_pairs_hook=unique, parse_constant=invalid_constant)
            require(isinstance(parsed, (dict, list)), "invalid_json_document")
        except (ValueError, UnicodeError):
            raise PublicationError("invalid_publication_json") from None
    return changed


def find_pr(client, branch):
    query = urlencode({"state": "open", "base": "main", "head": f"{INSTANCE.public_owner}:{branch}", "per_page": 100})
    values = client.call("GET", api("pulls?" + query))
    require(isinstance(values, list) and len(values) < 100 and len(values) <= 1,
            "ambiguous_publication_pull_request")
    return values[0] if values else None


def validate_pr(pr, branch, head):
    require(isinstance(pr, dict) and type(pr.get("number")) is int and pr["number"] > 0
            and pr.get("state") == "open" and pr.get("draft") is False and bot(pr.get("user"))
            and pr.get("base", {}).get("ref") == "main"
            and pr.get("base", {}).get("repo", {}).get("full_name") == REPOSITORY
            and pr.get("head", {}).get("ref") == branch and pr["head"].get("sha") == head
            and pr["head"].get("repo", {}).get("full_name") == REPOSITORY,
            "untrusted_publication_pull_request")


def wait_for_mergeability(client, number, branch, head):
    # GitHub computes mergeability asynchronously, especially just after the
    # news controller creates a PR. Do not attempt its protected merge early.
    for attempt in range(15):
        pr = client.call("GET", api(f"pulls/{number}"))
        validate_pr(pr, branch, head)
        if pr.get("mergeable") is True:
            return pr
        if pr.get("mergeable") is False:
            raise PublicationError("publication_merge_conflict")
        if attempt < 14:
            time.sleep(2)
    raise PublicationError("publication_mergeability_pending_rerun_controller")


def create_pr(client, branch, kind, origin=None):
    body = ("Actualización automática limitada a los JSON públicos permitidos. "
            "Se integra únicamente tras Verify public monitor y las reglas de main.\n")
    if origin is not None:
        body += f"\n<!-- mdh-publication-origin: {origin} -->\n"
    return client.call("POST", api("pulls"), {
        "head": branch, "base": "main", "title": f"chore: update public {kind} snapshots",
        "body": body,
    })


def validate_dispatched_run(run, run_id, *, branch=None, head=None):
    candidate_branch = run.get("head_branch", "") if isinstance(run, dict) else ""
    require(isinstance(candidate_branch, str) and BRANCH.fullmatch(candidate_branch)
            and type(run.get("id")) is int and run["id"] == run_id,
            "invalid_dispatched_ci_identity")
    validate_run(run, candidate_branch, allow_pending=True)
    require(run["event"] == "workflow_dispatch" and bot_run(run), "untrusted_dispatched_ci_actor")
    require((branch is None or candidate_branch == branch)
            and (head is None or run["head_sha"] == head), "changed_dispatched_ci_identity")


def read_dispatched_run(client, run_id):
    # The dispatch response identifies the exact run; allow only brief API
    # visibility delay, never fall back to a different run from a listing.
    for attempt in range(15):
        try:
            return client.call("GET", api(f"actions/runs/{run_id}"))
        except PublicationError as exc:
            if str(exc) != "github_get_failed_status_404" or attempt == 14:
                raise
            time.sleep(2)
    raise PublicationError("dispatched_ci_not_visible")


def dispatch(client, branch):
    require(isinstance(branch, str) and BRANCH.fullmatch(branch), "invalid_dispatch_branch")
    head = ref_sha(client, branch)
    # Since February 2026 GitHub can return the exact run ID. Token-authored CI
    # does not reliably emit workflow_run, so explicitly arm the trusted
    # default-branch controller as well; candidate CI keeps read-only access.
    dispatched = client.call("POST", api("actions/workflows/ci.yml/dispatches"), {
        "ref": branch, "return_run_details": True,
    })
    run_id = dispatched.get("workflow_run_id") if isinstance(dispatched, dict) else None
    require(type(run_id) is int and run_id > 0, "ci_dispatch_missing_run_id")
    run = read_dispatched_run(client, run_id)
    validate_dispatched_run(run, run_id, branch=branch, head=head)
    require(ref_sha(client, branch) == head, "publication_refs_changed_retry_required")
    client.call("POST", api("actions/workflows/publication-pr.yml/dispatches"), {
        "ref": "main", "inputs": {"ci_run_id": str(run_id)},
    })


def control_dispatch(client, event, ref):
    require(ref == "refs/heads/main"
            and event.get("repository", {}).get("full_name") == REPOSITORY,
            "controller_dispatch_requires_main")
    run_id_text = event.get("inputs", {}).get("ci_run_id")
    require(isinstance(run_id_text, str) and re.fullmatch(r"[1-9][0-9]{0,19}", run_id_text),
            "invalid_dispatched_ci_run_id")
    run_id = int(run_id_text)
    run = read_dispatched_run(client, run_id)
    validate_dispatched_run(run, run_id)
    branch, head = run["head_branch"], run["head_sha"]
    for attempt in range(CI_WAIT_SECONDS // CI_POLL_SECONDS + 1):
        validate_dispatched_run(run, run_id, branch=branch, head=head)
        if ref_sha(client, branch) != head:
            return "ignored_stale_ci_head"
        if run["status"] == "completed":
            require(run.get("conclusion") == "success", "dispatched_ci_unsuccessful")
            # Reuse every PR/origin/tree/check/protection guard and reload the
            # successful run again in control(); inputs never supply its data.
            return control(client, {
                "repository": {"full_name": REPOSITORY}, "workflow_run": run,
            })
        if attempt < CI_WAIT_SECONDS // CI_POLL_SECONDS:
            time.sleep(CI_POLL_SECONDS)
            run = client.call("GET", api(f"actions/runs/{run_id}"))
    raise PublicationError("dispatched_ci_wait_timeout")


def propose(client, branch, kind):
    match = BRANCH.fullmatch(branch)
    require(match is not None and match[1] == kind and kind in {"history", "exception"},
            "invalid_proposal_branch")
    head = ref_sha(client, branch)
    pr = find_pr(client, branch) or create_pr(client, branch, kind)
    validate_pr(pr, branch, head)
    dispatch(client, branch)
    return "proposed_and_ci_dispatched"


def validate_native_pr_run(run, branch, head, base, number):
    require(isinstance(run, dict) and type(run.get("id")) is int and run["id"] > 0
            and run.get("event") == "pull_request"
            and run.get("path") in NATIVE_PR_WORKFLOWS
            and run.get("name") == NATIVE_PR_WORKFLOWS[run["path"]]
            and run.get("head_branch") == branch and run.get("head_sha") == head
            and run.get("repository", {}).get("full_name") == REPOSITORY
            and run["repository"].get("id") == REPOSITORY_ID
            and run.get("head_repository", {}).get("full_name") == REPOSITORY
            and run["head_repository"].get("id") == REPOSITORY_ID
            and bot_run(run), "untrusted_native_pr_workflow")
    prs = run.get("pull_requests")
    require(isinstance(prs, list) and len(prs) == 1 and isinstance(prs[0], dict),
            "invalid_native_pr_association")
    linked = prs[0]
    require(linked.get("number") == number
            and linked.get("head", {}).get("ref") == branch
            and linked["head"].get("sha") == head
            and linked.get("base", {}).get("ref") == "main"
            and linked["base"].get("sha") == base,
            "changed_native_pr_association")
    for side in ("head", "base"):
        repository = linked[side].get("repo", {})
        require(repository.get("id") == REPOSITORY_ID
                and repository.get("url") == f"https://api.github.com/repos/{REPOSITORY}",
                "untrusted_native_pr_repository")
    require(run.get("status") in {
        "queued", "requested", "waiting", "pending", "in_progress", "completed"},
        "invalid_native_pr_status")


def native_pr_runs(client, branch, head, base, number):
    query = urlencode({"event": "pull_request", "head_sha": head, "per_page": 100})
    listing = client.call("GET", api("actions/runs?" + query))
    require(isinstance(listing, dict) and type(listing.get("total_count")) is int
            and 0 <= listing["total_count"] <= 100
            and isinstance(listing.get("workflow_runs"), list)
            and len(listing["workflow_runs"]) == listing["total_count"],
            "incomplete_native_pr_run_listing")
    latest, seen = {}, set()
    for run in listing["workflow_runs"]:
        require(isinstance(run, dict) and type(run.get("id")) is int
                and run["id"] > 0 and run["id"] not in seen, "invalid_native_pr_run_listing")
        seen.add(run["id"])
        if run.get("path") not in NATIVE_PR_WORKFLOWS:
            continue  # Never approve any other workflow, even with a similar name.
        validate_native_pr_run(run, branch, head, base, number)
        previous = latest.get(run["path"])
        if previous is None or previous["id"] < run["id"]:
            latest[run["path"]] = run
    # Re-read the exact selected runs before any approvals. The list endpoint is
    # not permission to approve a run with changed identity or PR association.
    result = {}
    for path, selected in latest.items():
        fresh = client.call("GET", api(f"actions/runs/{selected['id']}"))
        validate_native_pr_run(fresh, branch, head, base, number)
        require(fresh["id"] == selected["id"] and fresh["path"] == path,
                "changed_native_pr_run_identity")
        result[path] = fresh
    return result


def wait_for_native_pr_ci(client, pr, branch, head, base):
    # workflow_dispatch checks cannot satisfy required PR checks on GitHub.
    # Only after validating the complete data-only diff do we allow the two
    # read-only native PR workflows to run, using GitHub's approval endpoint.
    # This approves execution, never a PR review or a synthetic check result.
    approved = set()
    for attempt in range(CI_WAIT_SECONDS // CI_POLL_SECONDS + 1):
        require(ref_sha(client, branch) == head and ref_sha(client, "main") == base,
                "publication_refs_changed_retry_required")
        current = client.call("GET", api(f"pulls/{pr['number']}"))
        validate_pr(current, branch, head)
        require(current["base"].get("sha") == base, "publication_pr_base_changed")
        native = native_pr_runs(client, branch, head, base, pr["number"])
        if ".github/workflows/ci.yml" in native:
            require(all(candidate["status"] != "completed" or candidate.get("conclusion") in {
                "success", "action_required"} for candidate in native.values()), "native_pr_ci_unsuccessful")
            require(ref_sha(client, branch) == head and ref_sha(client, "main") == base,
                    "publication_refs_changed_retry_required")
            all_success = True
            for candidate in native.values():
                if candidate["status"] == "completed" and candidate.get("conclusion") == "action_required":
                    all_success = False
                    if candidate["id"] not in approved:
                        client.call("POST", api(f"actions/runs/{candidate['id']}/approve"))
                        approved.add(candidate["id"])
                elif candidate["status"] == "completed":
                    require(candidate.get("conclusion") == "success", "native_pr_ci_unsuccessful")
                else:
                    all_success = False
            if all_success:
                return
        if attempt < CI_WAIT_SECONDS // CI_POLL_SECONDS:
            time.sleep(CI_POLL_SECONDS)
    raise PublicationError("native_pr_ci_wait_timeout")


def control(client, event):
    if event.get("repository", {}).get("full_name") != REPOSITORY:
        return "ignored_repository"
    candidate = event.get("workflow_run", {})
    branch = candidate.get("head_branch", "")
    match = BRANCH.fullmatch(branch) if isinstance(branch, str) else None
    if (match is None or candidate.get("event") not in {"push", "workflow_dispatch"}
            or candidate.get("conclusion") != "success"
            or candidate.get("path") != ".github/workflows/ci.yml"):
        return "ignored_ci_run"
    run_id = candidate.get("id")
    require(type(run_id) is int and run_id > 0, "invalid_ci_run_id")
    run = client.call("GET", api(f"actions/runs/{run_id}"))
    validate_run(run, branch)
    require(run.get("id") == run_id and run.get("head_sha") == candidate.get("head_sha"),
            "changed_ci_run_identity")
    kind, head = match[1], run["head_sha"]
    if ref_sha(client, branch) != head:
        return "ignored_stale_ci_head"
    pr = find_pr(client, branch)
    if kind == "news":
        require(owner_push(run) or (bot_run(run) and run["event"] == "workflow_dispatch"),
                "untrusted_news_ci_actor")
        if pr is None:
            require(owner_push(run), "news_origin_requires_owner_push")
            origin_id = run_id
        else:
            validate_pr(pr, branch, head)
            origins = ORIGIN.findall(pr.get("body") or "")
            require(len(origins) == 1, "missing_news_origin")
            origin_id = int(origins[0])
        origin = client.call("GET", api(f"actions/runs/{origin_id}"))
        validate_run(origin, branch)
        require(origin.get("id") == origin_id and owner_push(origin), "untrusted_news_origin")
        ancestry = compare(client, origin["head_sha"], head)
        require(ancestry["behind_by"] == 0
                and ancestry["merge_base_commit"]["sha"] == origin["head_sha"], "changed_news_origin")
    else:
        require(bot_run(run) and run["event"] == "workflow_dispatch", "untrusted_publication_ci_actor")
        require(pr is not None, "missing_bot_publication_pr")
        validate_pr(pr, branch, head)
        origin_id = None
    base = ref_sha(client, "main")
    comparison = compare(client, base, head)
    changed = validate_changes(client, comparison["merge_base_commit"]["sha"], head, kind)
    if not changed:
        return "ignored_empty_publication"
    # Refresh both refs immediately before mutating. All merging is also guarded
    # by the exact candidate SHA and strict required checks on protected main.
    require(ref_sha(client, branch) == head and ref_sha(client, "main") == base,
            "publication_refs_changed_retry_required")
    if pr is None:
        pr = create_pr(client, branch, kind, origin=origin_id)
        validate_pr(pr, branch, head)
    pr = wait_for_mergeability(client, pr["number"], branch, head)
    require(ref_sha(client, branch) == head and ref_sha(client, "main") == base,
            "publication_refs_changed_retry_required")
    if comparison["behind_by"]:
        updated = client.call("PUT", api(f"pulls/{pr['number']}/update-branch"), {"expected_head_sha": head})
        require(isinstance(updated, dict), "branch_update_unconfirmed")
        # update-branch is asynchronous. A token-authored merge does not start
        # push CI; dispatch only once the updated commit is actually visible.
        for attempt in range(15):
            updated_head = ref_sha(client, branch)
            if updated_head != head:
                ancestry = compare(client, head, updated_head)
                require(ancestry["behind_by"] == 0
                        and ancestry["merge_base_commit"]["sha"] == head,
                        "branch_update_replaced_candidate")
                latest = compare(client, base, updated_head)
                require(latest["behind_by"] == 0
                        and latest["merge_base_commit"]["sha"] == base,
                        "branch_update_missing_base")
                validate_changes(client, base, updated_head, kind)
                dispatch(client, branch)
                return "branch_updated_ci_dispatched"
            if attempt < 14:
                time.sleep(2)
        raise PublicationError("branch_update_pending_rerun_controller")
    if run["event"] == "workflow_dispatch":
        wait_for_native_pr_ci(client, pr, branch, head, base)
        require(ref_sha(client, branch) == head and ref_sha(client, "main") == base,
                "publication_refs_changed_retry_required")
        validate_pr(client.call("GET", api(f"pulls/{pr['number']}")), branch, head)
    merged = client.call("PUT", api(f"pulls/{pr['number']}/merge"), {
        "sha": head, "merge_method": "squash", "commit_title": f"chore: update public {kind} snapshots",
    })
    require(isinstance(merged, dict) and merged.get("merged") is True, "protected_merge_not_accepted")
    if kind == "history":
        close_superseded_history_prs(client, pr["number"], branch)
    return "merged_with_required_checks"


SUPERSEDED_COMMENT = ("Cerrada automáticamente como sustituida por #{number}: `main` ya integró una "
                      "instantánea posterior y las instantáneas de bibliografía se regeneran desde "
                      "`main`. La rama se conserva; puede reabrirse si se detecta un dato ausente.")
SUPERSEDED_MAX_PAGES = 5
SUPERSEDED_MAX_CLOSINGS = 50


def close_superseded_history_prs(client, merged_number, merged_branch):
    """Close strictly older open history snapshot PRs after a confirmed merge.

    Only history snapshots are superseded this way: they are regenerated from
    ``main`` on every run. News snapshots can hold publisher output that later
    runs do not regenerate, and exception PRs carry distinct editorial
    withdrawals, so neither kind is ever closed here. Branches are never
    deleted. Failures are best-effort: a listing or closing error cannot turn
    the confirmed merge into a failure.
    """
    merged_match = BRANCH.fullmatch(merged_branch)
    merged_position = (int(merged_match[2]), int(merged_match[3]))
    closed, failed = [], []
    try:
        for page in range(1, SUPERSEDED_MAX_PAGES + 1):
            pulls = client.call(
                "GET", api(f"pulls?state=open&base=main&per_page=100&page={page}"))
            require(isinstance(pulls, list), "invalid_pulls_listing")
            for candidate in pulls:
                if len(closed) + len(failed) >= SUPERSEDED_MAX_CLOSINGS:
                    break
                if not isinstance(candidate, dict):
                    continue
                ref = candidate.get("head", {}).get("ref")
                match = BRANCH.fullmatch(ref) if isinstance(ref, str) else None
                if (match is None or match[1] != "history"
                        or (int(match[2]), int(match[3])) >= merged_position
                        or candidate.get("number") == merged_number
                        or candidate.get("head", {}).get("repo", {}).get("full_name") != REPOSITORY
                        or not bot(candidate.get("user"))):
                    continue
                try:
                    client.call("POST", api(f"issues/{candidate['number']}/comments"),
                                {"body": SUPERSEDED_COMMENT.format(number=merged_number)})
                    client.call("PATCH", api(f"pulls/{candidate['number']}"), {"state": "closed"})
                except (PublicationError, KeyError, ValueError, OSError):
                    failed.append(candidate["number"])
                else:
                    closed.append(candidate["number"])
            if len(pulls) < 100 or len(closed) + len(failed) >= SUPERSEDED_MAX_CLOSINGS:
                break
    except (PublicationError, KeyError, ValueError, OSError):
        failed.append("list")
    if failed:
        print("::warning::superseded_close_failed")
    print(json.dumps({"superseded_closed": closed, "superseded_failed": failed}))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")
    proposal = subparsers.add_parser("propose")
    proposal.add_argument("--kind", required=True, choices=("history", "exception"))
    proposal.add_argument("--branch", required=True)
    args = parser.parse_args(argv)
    try:
        require(os.environ.get("GITHUB_REPOSITORY") == REPOSITORY, "wrong_repository")
        client = GitHub(os.environ.get("GH_TOKEN", ""))
        if args.command == "propose":
            result = propose(client, args.branch, args.kind)
        else:
            event_path = Path(os.environ["GITHUB_EVENT_PATH"])
            require(event_path.stat().st_size <= 2_000_000, "event_too_large")
            event = json.loads(event_path.read_text())
            if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
                result = control_dispatch(client, event, os.environ.get("GITHUB_REF"))
            else:
                require(os.environ.get("GITHUB_EVENT_NAME") == "workflow_run", "invalid_controller_event")
                result = control(client, event)
        print(result)
        return 0
    except (PublicationError, KeyError, ValueError, OSError) as exc:
        print(str(exc) if isinstance(exc, PublicationError) else "invalid_controller_input", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
