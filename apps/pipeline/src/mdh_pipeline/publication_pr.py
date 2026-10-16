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

REPOSITORY = "RauPro/MDH-SV"
NEWS_PUBLISHER_ACTOR_ID = 37725305
BOT_ID = 41898282
BRANCH = re.compile(r"chore/automated-(history|exception|news)-([1-9][0-9]*)-([1-9][0-9]*)")
SHA = re.compile(r"[a-f0-9]{40}")
ORIGIN = re.compile(r"<!-- mdh-publication-origin: ([1-9][0-9]*) -->")
MAX_RESPONSE = 48_000_000
MAX_JSON = 32_000_000
MAX_TOTAL_JSON = 64_000_000
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


def validate_run(run, branch):
    require(isinstance(run, dict)
            and run.get("repository", {}).get("full_name") == REPOSITORY
            and run.get("head_repository", {}).get("full_name") == REPOSITORY
            and run.get("path") == ".github/workflows/ci.yml"
            and run.get("name") == "Verify public monitor"
            and run.get("head_branch") == branch
            and valid_sha(run.get("head_sha"))
            and run.get("event") in {"push", "workflow_dispatch"}
            and run.get("status") == "completed" and run.get("conclusion") == "success",
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
    query = urlencode({"state": "open", "base": "main", "head": f"RauPro:{branch}", "per_page": 100})
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


def dispatch(client, branch):
    client.call("POST", api("actions/workflows/ci.yml/dispatches"), {"ref": branch})


def propose(client, branch, kind):
    match = BRANCH.fullmatch(branch)
    require(match is not None and match[1] == kind and kind in {"history", "exception"},
            "invalid_proposal_branch")
    head = ref_sha(client, branch)
    pr = find_pr(client, branch) or create_pr(client, branch, kind)
    validate_pr(pr, branch, head)
    dispatch(client, branch)
    return "proposed_and_ci_dispatched"


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
    merged = client.call("PUT", api(f"pulls/{pr['number']}/merge"), {
        "sha": head, "merge_method": "squash", "commit_title": f"chore: update public {kind} snapshots",
    })
    require(isinstance(merged, dict) and merged.get("merged") is True, "protected_merge_not_accepted")
    return "merged_with_required_checks"


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
            result = control(client, json.loads(event_path.read_text()))
        print(result)
        return 0
    except (PublicationError, KeyError, ValueError, OSError) as exc:
        print(str(exc) if isinstance(exc, PublicationError) else "invalid_controller_input", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
