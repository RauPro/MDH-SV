"""GitHub correction requests: bounded rechecks and authenticated withdrawal overrides.

Only identifiers cross into workers. Issue prose, links and attachments are never
sent to a model or executed. Public requests can queue one recheck per candidate;
further generations and withdrawals require an authenticated repository writer.
"""
from __future__ import annotations

import argparse
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re

import requests

from .instance import load_instance

INSTANCE = load_instance()
PUBLIC_REPO = INSTANCE.public_repository
PRIVATE_REPO = INSTANCE.private_repository
STATE_PATH = "runtime/news-requests.json"
ROOT = Path(__file__).resolve().parents[4]
EXCEPTIONS = ROOT / "data/selection/news_exceptions.json"
HEX = re.compile(r"[a-f0-9]{64}")
LOGIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}")
MAX_BYTES = 4_000_000
MAX_ENTRIES = 10_000


def _integer(value, minimum=0):
    return type(value) is int and minimum <= value <= 9_007_199_254_740_991


def _time(value):
    if not isinstance(value, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)", value):
        raise ValueError("invalid_request_timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError("future_request_timestamp")


def validate_exceptions(value):
    if (not isinstance(value, dict) or set(value) != {"schema_version", "decisions"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or not isinstance(value["decisions"], list) or len(value["decisions"]) > MAX_ENTRIES):
        raise ValueError("invalid_news_exceptions")
    seen = set()
    keys = {"candidate_id", "withdrawn", "request_generation", "issue_number", "decided_by", "decided_at"}
    for item in value["decisions"]:
        if (not isinstance(item, dict) or set(item) != keys
                or not isinstance(item["candidate_id"], str) or not HEX.fullmatch(item["candidate_id"])
                or item["candidate_id"] in seen or type(item["withdrawn"]) is not bool
                or not _integer(item["request_generation"]) or not _integer(item["issue_number"], 1)
                or not isinstance(item["decided_by"], str) or not LOGIN.fullmatch(item["decided_by"])):
            raise ValueError("invalid_news_exception")
        _time(item["decided_at"])
        seen.add(item["candidate_id"])
    return deepcopy(value)


def read_json(path):
    path = Path(path)
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 32_000_000:
        raise ValueError("invalid_request_file")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_request_key")
            result[key] = value
        return result
    def invalid(_):
        raise ValueError("invalid_request_constant")
    return json.loads(path.read_text(), object_pairs_hook=unique, parse_constant=invalid)


def write_json(path, value):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("invalid_request_output")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def withdrawn_ids(path=EXCEPTIONS):
    return {item["candidate_id"] for item in validate_exceptions(read_json(path))["decisions"] if item["withdrawn"]}


def candidate_from_request(issue):
    if (not isinstance(issue, dict) or "pull_request" in issue
            or not _integer(issue.get("number"), 1)
            or "news-correction" not in {label.get("name") for label in issue.get("labels", [])}
            or not isinstance(issue.get("body"), str) or len(issue["body"].encode()) > 40_000):
        return None
    # GitHub's form produces this exact heading. Multiple IDs/sections are not
    # interpreted as multiple commands, nor are URLs/markdown executed.
    if issue["body"].count("### Identificador de la noticia") != 1:
        return None
    values = re.findall(r"(?m)^### Identificador de la noticia\s*\n\s*([a-f0-9]{64})\s*(?=\n### |\Z)", issue["body"])
    return values[0] if len(values) == 1 else None


def public_rechecks(issues, known_ids, previous, exceptions):
    if (not isinstance(previous, dict) or set(previous) != {"schema_version", "generations"}
            or type(previous["schema_version"]) is not int or previous["schema_version"] != 1
            or not isinstance(previous["generations"], dict) or len(previous["generations"]) > MAX_ENTRIES
            or any(not HEX.fullmatch(key) or not _integer(value, 1) for key, value in previous["generations"].items())):
        raise ValueError("invalid_request_state")
    generations = dict(previous["generations"])
    for issue in sorted(issues, key=lambda item: item.get("number", 0)):
        identifier = candidate_from_request(issue)
        if identifier in known_ids and identifier not in generations:
            generations[identifier] = issue["number"]
    for decision in validate_exceptions(exceptions)["decisions"]:
        if decision["request_generation"]:
            key = decision["candidate_id"]
            generations[key] = max(generations.get(key, 0), decision["request_generation"])
    if len(generations) > MAX_ENTRIES:
        raise ValueError("request_capacity_exceeded")
    return {"schema_version": 1, "generations": dict(sorted(generations.items()))}


def sync_requests(inbox, archive, exceptions, *, output):
    if inbox.repository != PRIVATE_REPO:
        raise ValueError("wrong_request_repository")
    stored = inbox.get_file(inbox.base + "/contents/" + STATE_PATH)
    previous = {"schema_version": 2, "generations": {}, "next_page": 1}
    sha = None
    if stored is not None:
        if (stored.get("type") != "file" or stored.get("path") != STATE_PATH
                or stored.get("encoding") != "base64" or not _integer(stored.get("size"), 1)
                or stored["size"] > MAX_BYTES or len(stored.get("content", "")) > MAX_BYTES * 2):
            raise ValueError("invalid_request_state_file")
        raw = base64.b64decode(stored["content"].replace("\n", ""), validate=True)
        if len(raw) != stored["size"]:
            raise ValueError("invalid_request_state_size")
        previous, sha = json.loads(raw), stored["sha"]
    if (not isinstance(previous, dict) or set(previous) != {"schema_version", "generations", "next_page"}
            or type(previous["schema_version"]) is not int or previous["schema_version"] != 2
            or not _integer(previous["next_page"], 1)):
        raise ValueError("invalid_request_scan_state")
    rows, next_page = [], previous["next_page"]
    # Two pages per run with a persistent cursor, not an ever-growing all-issues
    # scan. Wrap at the end so deletions or later labels cannot permanently skip
    # a request. A large public issue backlog never blocks news publication.
    for page in range(next_page, next_page + 2):
        batch = inbox.call("GET", f"/repos/{PUBLIC_REPO}/issues", params={
            "state": "all", "labels": "news-correction", "sort": "created", "direction": "asc", "per_page": 100, "page": page})
        if not isinstance(batch, list) or len(batch) > 100:
            raise ValueError("invalid_request_listing")
        rows.extend(batch)
        next_page = page + 1
        if len(batch) < 100:
            next_page = 1
            break
    state = public_rechecks(rows, {row["id"] for row in archive["records"]},
        {"schema_version": 1, "generations": previous["generations"]}, exceptions)
    state.update(schema_version=2, next_page=next_page)
    if state != previous:
        payload = {"message": "Record bounded news correction requests", "branch": "main",
                   "content": base64.b64encode((json.dumps(state) + "\n").encode()).decode()}
        if sha:
            payload["sha"] = sha
        result = inbox.call("PUT", inbox.base + "/contents/" + STATE_PATH, json=payload)
        if not re.fullmatch(r"[a-f0-9]{40,64}", (result or {}).get("content", {}).get("sha", "")):
            raise ValueError("request_state_write_unconfirmed")
    write_json(output, state["generations"])
    return {"requests": len(rows), "recheck_candidates": len(state["generations"])}


def apply_exception(event, document, *, permission, actor, generation, known_ids):
    document = validate_exceptions(document)
    if (event.get("action") != "labeled" or event.get("repository", {}).get("full_name") != PUBLIC_REPO
            or event.get("sender", {}).get("type") != "User" or event["sender"].get("login") != actor
            or permission not in {"write", "maintain", "admin"} or not LOGIN.fullmatch(actor)
            or not _integer(generation, 1)):
        raise ValueError("unauthorized_news_exception")
    action = event.get("label", {}).get("name")
    if action not in {"news/withdraw", "news/restore", "news/recheck"}:
        raise ValueError("unsupported_news_exception")
    issue = event.get("issue", {})
    identifier = candidate_from_request(issue)
    if identifier not in known_ids:
        raise ValueError("unknown_news_exception_candidate")
    decisions = {item["candidate_id"]: item for item in document["decisions"]}
    previous = decisions.get(identifier)
    if previous and previous["request_generation"] >= generation:
        return document
    item = dict(previous or {"candidate_id": identifier, "withdrawn": False, "request_generation": 0})
    item["request_generation"] = generation
    if action == "news/withdraw":
        item["withdrawn"] = True
    else:
        item["request_generation"] = max(item["request_generation"], generation)
        if action == "news/restore":
            item["withdrawn"] = False
    item.update(issue_number=issue["number"], decided_by=actor, decided_at=datetime.now(timezone.utc).isoformat())
    decisions[identifier] = item
    return validate_exceptions({"schema_version": 1, "decisions": sorted(decisions.values(), key=lambda row: row["candidate_id"])})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    sync = commands.add_parser("sync")
    sync.add_argument("--archive", type=Path, required=True)
    sync.add_argument("--exceptions", type=Path, default=EXCEPTIONS)
    sync.add_argument("--output", type=Path, required=True)
    apply = commands.add_parser("apply-exception")
    apply.add_argument("--exceptions", type=Path, default=EXCEPTIONS)
    apply.add_argument("--candidates", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "sync":
            from .review_queue import GitHubInbox
            inbox = GitHubInbox(os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"])
            report = sync_requests(inbox, read_json(args.archive), read_json(args.exceptions), output=args.output)
        else:
            # The public workflow token is restricted to this repository. Never
            # follow a redirect or forward it to any URL supplied by an issue.
            actor = os.environ["GITHUB_ACTOR"]
            if os.environ["GITHUB_REPOSITORY"] != PUBLIC_REPO or not LOGIN.fullmatch(actor):
                raise ValueError("wrong_exception_repository")
            response = requests.get(f"https://api.github.com/repos/{PUBLIC_REPO}/collaborators/{actor}/permission",
                headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"], "Accept": "application/vnd.github+json"},
                timeout=(8, 30), allow_redirects=False)
            if response.status_code != 200:
                raise ValueError("exception_permission_check_failed")
            updated = apply_exception(read_json(os.environ["GITHUB_EVENT_PATH"]), read_json(args.exceptions),
                permission=response.json().get("permission"), actor=actor, generation=int(os.environ["GITHUB_RUN_ID"]),
                known_ids={row["id"] for row in read_json(args.candidates)["records"]})
            write_json(args.exceptions, updated)
            report = {"exception_recorded": True, "withdrawn": sum(item["withdrawn"] for item in updated["decisions"])}
    except (ValueError, KeyError, TypeError, OSError, requests.RequestException):
        raise SystemExit("News request processing failed; no issue content or credentials logged.") from None
    print(json.dumps(report))


if __name__ == "__main__":
    main()
