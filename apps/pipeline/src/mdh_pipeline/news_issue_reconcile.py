"""Reconcile private news issues from saved evidence, without model requests."""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import time

import requests

from .gemini_review import candidate_from_issue, _load_phase_documents
from .contracts.dossier import empty_news_dossier as empty_news_dossier
from .news_issue_state import build_issue_state_indexes, candidate_matches_terminal
from .news_requests import read_json, withdrawn_ids
from .news_scope_rechecks import load_rechecks
from .review_queue import GitHubInbox, GitHubRequestError

from .instance import load_instance

INSTANCE = load_instance()
REPOSITORY = INSTANCE.private_repository
PRODUCTION = INSTANCE.public_url
STATE_PATH = "runtime/news-issue-reconciliation.json"
LABELS = {"news/published": "16815D", "news/discarded": "6B7280", "news/recheck": "D97706"}
MAX_TRANSITIONS = 150
PRIMARY_RESERVE = 400
MAX_PAGES = 200
BLOCK = re.compile(r"<!-- mdh-news-lifecycle:start -->.*?<!-- mdh-news-lifecycle:end -->\n?", re.S)
MARKER = re.compile(r"<!-- mdh-news-lifecycle:v1:(published|discarded|pending):([a-f0-9]{64}) -->")


def _stamp(value):
    if not isinstance(value, str):
        raise ValueError("invalid_reconciliation_timestamp")
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.utcoffset() is None:
        raise ValueError("invalid_reconciliation_timestamp")
    return instant


def _unique(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("duplicate_reconciliation_key")
        document[key] = value
    return document


class TransitionBudget:
    """Reserve a bounded batch before writing; uncertain runs keep their grant.

    This ledger concerns GitHub writes only. It never reads or changes Gemini's
    quota. Main-branch CAS and the shared workflow concurrency prevent double
    allocation. A rerun of the same Actions run cannot claim again.
    """
    def __init__(self, inbox):
        self.inbox = inbox
        self.path = inbox.base + "/contents/" + STATE_PATH

    def claim(self, run_id, requested, now):
        if (not isinstance(run_id, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", run_id)
                or type(requested) is not int or not 0 <= requested <= MAX_TRANSITIONS):
            raise ValueError("invalid_reconciliation_grant")
        if not requested:
            return 0
        file = self.inbox.get_file(self.path + "?ref=main")
        sha = None
        state = {"schema_version": 1, "repository": REPOSITORY, "claims": []}
        if file is not None:
            if (file.get("type") != "file" or file.get("encoding") != "base64"
                    or file.get("path") != STATE_PATH or type(file.get("size")) is not int
                    or not 0 < file["size"] <= 100_000
                    or not isinstance(file.get("content"), str) or len(file["content"]) > 200_000
                    or not re.fullmatch(r"[a-f0-9]{40,64}", file.get("sha", ""))):
                raise ValueError("invalid_reconciliation_ledger")
            raw = base64.b64decode(file["content"].replace("\n", ""), validate=True)
            if len(raw) != file["size"]:
                raise ValueError("invalid_reconciliation_ledger_size")
            state = json.loads(raw, object_pairs_hook=_unique)
            sha = file["sha"]
        if (not isinstance(state, dict) or set(state) != {"schema_version", "repository", "claims"}
                or type(state["schema_version"]) is not int or state["schema_version"] != 1
                or state["repository"] != REPOSITORY or not isinstance(state["claims"], list)
                or len(state["claims"]) > 200):
            raise ValueError("invalid_reconciliation_ledger")
        seen = set()
        for claim in state["claims"]:
            if (not isinstance(claim, dict) or set(claim) != {"run_id", "reserved_at", "limit"}
                    or not isinstance(claim["run_id"], str)
                    or not re.fullmatch(r"[1-9][0-9]{0,19}", claim["run_id"])
                    or claim["run_id"] in seen or type(claim["limit"]) is not int
                    or not 1 <= claim["limit"] <= MAX_TRANSITIONS
                    or _stamp(claim["reserved_at"]) > now):
                raise ValueError("invalid_reconciliation_claim")
            seen.add(claim["run_id"])
        if run_id in seen:
            return 0
        used = sum(c["limit"] for c in state["claims"] if _stamp(c["reserved_at"]) > now - timedelta(hours=1))
        grant = min(requested, max(0, MAX_TRANSITIONS - used))
        if not grant:
            return 0
        state["claims"] = [c for c in state["claims"] if _stamp(c["reserved_at"]) > now - timedelta(days=7)]
        if len(state["claims"]) >= 200:
            raise ValueError("reconciliation_ledger_full")
        state["claims"].append({"run_id": run_id, "reserved_at": now.isoformat(), "limit": grant})
        payload = {"message": "Reserve bounded news issue reconciliation writes", "branch": "main",
                   "content": base64.b64encode((json.dumps(state, indent=2) + "\n").encode()).decode()}
        if sha:
            payload["sha"] = sha
        result = self.inbox.call("PUT", self.path, json=payload)
        if not re.fullmatch(r"[a-f0-9]{40,64}", (result or {}).get("content", {}).get("sha", "")):
            raise ValueError("reconciliation_reservation_unconfirmed")
        return grant


def production_snapshot(session=None):
    """Only fixed public endpoints, with no GitHub credentials or redirects."""
    client = session or requests.Session()
    documents = []
    for name in ("news", "dossiers"):
        with client.get(f"{PRODUCTION}/archive/{name}.json", timeout=(8, 30),
                        stream=True, allow_redirects=False) as response:
            if response.status_code != 200:
                raise ValueError("production_snapshot_unavailable")
            raw = bytearray()
            for chunk in response.iter_content(65536):
                raw.extend(chunk)
                if len(raw) > 30_000_000:
                    raise ValueError("production_snapshot_oversized")
            documents.append(json.loads(raw, object_pairs_hook=_unique))
    return tuple(documents)


def issue_snapshot(inbox):
    """Enumerate before mutations; state=all keeps pagination stable on closure."""
    result, seen = [], set()
    for page in range(1, MAX_PAGES + 1):
        rows = inbox.call("GET", inbox.base + "/issues", params={
            "state": "all", "sort": "created", "direction": "asc", "per_page": 100, "page": page})
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValueError("invalid_issue_inventory")
        for row in rows:
            number = row.get("number") if isinstance(row, dict) else None
            if type(number) is not int or number < 1 or number in seen:
                raise ValueError("inconsistent_issue_inventory")
            seen.add(number)
            if "pull_request" not in row:
                result.append(row)
        if len(rows) < 100:
            return result
    raise ValueError("incomplete_issue_inventory")


def _labels(issue):
    return {label["name"] for label in issue.get("labels", [])}


def _managed(issue, candidate):
    markers = MARKER.findall(issue.get("body") or "")
    return (len(markers) == 1 and markers[0][1] == candidate["id"]
            and bool(_labels(issue) & set(LABELS)))


def desired_transition(issue, terminals, *, excluded_ids=(), reopen=None):
    if ("pull_request" in issue or issue.get("locked") or issue.get("state") not in {"open", "closed"}
            or not isinstance(issue.get("body"), str) or len(issue["body"].encode()) > 60_000):
        return None
    try:
        candidate = candidate_from_issue(issue)
        empty_news_dossier(candidate)  # Incomplete metadata is not a terminal decision.
        if any(key not in candidate for key in ("source_url", "publisher", "published_date")):
            return None
    except (ValueError, KeyError, TypeError):
        return None
    labels = _labels(issue)
    owned = _managed(issue, candidate)
    if issue["state"] == "closed" and not owned:
        return None  # Never reopen or rewrite somebody else's closure.
    if (issue["state"] == "closed" and issue.get("closed_by") is not None
            and issue["closed_by"].get("login") != "github-actions[bot]"):
        return None
    if "review/rejected" in labels:
        if issue["state"] == "closed":
            return None
        terminal = {"status": "discarded", "reason": "editorial_rejection", "candidate_id": candidate["id"],
                    **{k: candidate[k] for k in ("source_url", "publisher", "published_date")}}
    elif candidate["id"] in excluded_ids:
        return None  # Explicit withdrawals are managed through their editorial request.
    else:
        terminal = terminals.get(candidate["id"])
        if terminal is not None and not candidate_matches_terminal(candidate, terminal):
            return None
    if "review/approved" in labels and (terminal is None or terminal["status"] != "published"):
        return None
    pending = (reopen or {}).get(candidate["id"])
    if (issue["state"] == "closed" and owned and pending is not None
            and not labels.intersection({"review/approved", "review/rejected"})
            and candidate_matches_terminal(candidate, pending) and pending.get("recheck_requested_at")):
        try:
            if _stamp(issue.get("closed_at")) < _stamp(pending["recheck_requested_at"]):
                return {"candidate": candidate, **pending, "reason": "audit_recheck"}
        except ValueError:
            pass  # An absent/invalid closure timestamp is not proof of this race.
    if terminal is None:
        if issue["state"] == "closed" and owned and pending is not None and candidate_matches_terminal(candidate, pending):
            return {"candidate": candidate, **pending}
        return None
    if issue["state"] == "closed" and f"news/{terminal['status']}" in labels:
        return None
    if issue["state"] == "open" and not (labels & {"review/pending", "review/approved", "review/rejected"} or owned):
        return None
    return {"candidate": candidate, **terminal}


def transition_payload(issue, transition, *, private_revision, now):
    status, candidate = transition["status"], transition["candidate"]
    identifier = candidate["id"]
    labels = _labels(issue) - set(LABELS) - {"review/pending"}
    if status == "pending":
        labels.add("review/pending")
        labels.add("news/recheck")
        title = "Pendiente de reevaluación"
        detail = {"audit_recheck": "La auditoría solicitó una reevaluación prioritaria. La decisión anterior no permite volver a cerrar esta noticia.",
                  "source_content_mismatch": "El título y el cuerpo publicado por la fuente son incoherentes. Se conserva pendiente hasta obtener una fuente coherente; no se descarta ni se inventan datos.",
                  "exclusion_requires_verification": "La exclusión anterior tenía una sola evaluación. Se reabre para aplicar la política inclusiva y una segunda validación antes de cualquier descarte."}.get(
                      transition["reason"], "La evidencia vigente ya no confirma el cierre anterior. Se reabre para continuar el procesamiento automático.")
    elif status == "published":
        labels.add("news/published")
        title = "Ficha publicada"
        detail = f"La ficha se comprobó en la [colección desplegada]({PRODUCTION}/archive/dossiers.json)."
    else:
        labels.add("news/discarded")
        title = "Noticia descartada"
        reasons = {"outside_el_salvador": "La clasificación guardada sitúa el asunto fuera del alcance salvadoreño del monitor.",
                   "not_relevant": "La clasificación guardada no identifica un nexo sustantivo con derechos humanos.",
                   "editorial_rejection": "El issue ya tenía una decisión editorial explícita de rechazo."}
        detail = reasons[transition["reason"]]
        if transition["reason"] != "editorial_rejection":
            detail += f"\n\n[Clasificación guardada](https://github.com/{REPOSITORY}/blob/{private_revision}/scope-reviews/{identifier}.json)."
            detail += f"\n\n[Segunda validación concordante](https://github.com/{REPOSITORY}/blob/{private_revision}/scope-verifications/{identifier}.json)."
    digest = transition.get("content_sha256")
    proof = f"\n\nHuella del texto evaluado: `{digest}`." if digest else ""
    block = ("<!-- mdh-news-lifecycle:start -->\n"
             f"<!-- mdh-news-lifecycle:v1:{status}:{identifier} -->\n"
             f"## Estado del procesamiento: {title}\n\n{detail}\n\n"
             f"Identificador: `{identifier}`. Comprobado: {now.isoformat()}.{proof}\n\n"
             "Este cambio sincroniza GitHub con resultados existentes. No ejecuta Gemini ni acredita una revisión humana.\n"
             "<!-- mdh-news-lifecycle:end -->\n")
    body = block + BLOCK.sub("", issue["body"])
    return {"body": body, "labels": sorted(labels), "state": "open" if status == "pending" else "closed",
            "state_reason": "reopened" if status == "pending" else "completed" if status == "published" else "not_planned"}


def _available_transitions(inbox, limit):
    state = inbox.call("GET", "/rate_limit")["resources"]["core"]
    remaining = state["remaining"]
    if type(remaining) is not int or remaining < 0:
        raise ValueError("invalid_github_rate_snapshot")
    # One fresh read and one conditional state update per transition; leave
    # room for ingestion/publication, the CAS ledger, and label creation.
    return min(limit, max(0, (remaining - PRIMARY_RESERVE - 10) // 2))


def reconcile(inbox, terminals, *, private_revision, run_id=None, apply=False, limit=150,
              excluded_ids=(), reopen=None, now=None, sleep=time.sleep, clock=time.monotonic, issues=None):
    if (inbox.repository != REPOSITORY or type(limit) is not int or not 1 <= limit <= MAX_TRANSITIONS
            or not isinstance(private_revision, str) or not re.fullmatch(r"[a-f0-9]{40}", private_revision)):
        raise ValueError("invalid_reconciliation_configuration")
    now = now or datetime.now(timezone.utc)
    issues = issue_snapshot(inbox) if issues is None else issues
    planned = [(row, transition) for row in issues
               if (transition := desired_transition(row, terminals, excluded_ids=excluded_ids, reopen=reopen)) is not None]
    # Recover audited errors first; otherwise a large legacy backlog could keep
    # the concrete findings closed for hours despite an explicit recheck request.
    planned.sort(key=lambda pair: (0 if pair[1]["reason"] in {"audit_recheck", "source_content_mismatch"}
        else {"published": 1, "pending": 2, "discarded": 3}[pair[1]["status"]], pair[0]["number"]))
    report = {"issues_scanned": len(issues), "planned": dict(Counter(t["status"] for _, t in planned)),
              "changed": {"published": 0, "discarded": 0, "pending": 0}, "concurrent_changes": 0,
              "remaining_transitions": len(planned), "gemini_calls": 0, "dry_run": not apply}
    if not apply or not planned:
        return report
    allowed = _available_transitions(inbox, min(limit, len(planned)))
    grant = TransitionBudget(inbox).claim(run_id, allowed, now)
    report["transition_grant"] = grant
    if not grant:
        report["stopped_reason"] = "github_write_budget"
        return report
    existing = {label["name"] for label in inbox.call("GET", inbox.base + "/labels", params={"per_page": 100})}
    for name, color in LABELS.items():
        if name not in existing:
            inbox.call("POST", inbox.base + "/labels", json={"name": name, "color": color})
            sleep(2)
    started, last_write = clock(), None
    for original, transition in planned[:grant]:
        if clock() - started > 900:
            report["stopped_reason"] = "deadline"
            break
        if last_write is not None:
            sleep(max(0, 2 - (clock() - last_write)))
        path = inbox.base + f"/issues/{original['number']}"
        current = inbox.call("GET", path)
        if (transition["status"] == "pending"
                and (current.get("closed_by") or {}).get("login") != "github-actions[bot]"):
            report["concurrent_changes"] += 1
            continue
        if (current.get("body") != original.get("body") or _labels(current) != _labels(original)
                or current.get("state") != original.get("state") or current.get("updated_at") != original.get("updated_at")
                or desired_transition(current, terminals, excluded_ids=excluded_ids, reopen=reopen) != transition):
            report["concurrent_changes"] += 1
            continue
        payload = transition_payload(current, transition, private_revision=private_revision, now=now)
        last_write = clock()
        result = inbox.call("PATCH", path, json=payload)
        if (result.get("state") != payload["state"] or result.get("body") != payload["body"]
                or _labels(result) != set(payload["labels"])):
            raise ValueError("issue_transition_unconfirmed")
        report["changed"][transition["status"]] += 1
    report["remaining_transitions"] -= sum(report["changed"].values())
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inbox-dir", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--scope-audits", type=Path, required=True)
    parser.add_argument("--exceptions", type=Path, required=True)
    parser.add_argument("--private-revision", required=True)
    parser.add_argument("--limit", type=int, default=150)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        actual = subprocess.check_output(["git", "-C", str(args.inbox_dir), "rev-parse", "HEAD"], text=True).strip()
        if actual != args.private_revision:
            raise ValueError("private_snapshot_revision_mismatch")
        subprocess.run(["git", "-C", str(args.inbox_dir), "diff", "--quiet", "HEAD", "--", "drafts/", "scope-reviews/"], check=True)
        excluded = withdrawn_ids(args.exceptions)
        public_news, public_dossiers = production_snapshot()
        evidence = dict(archive=read_json(args.archive), published_archive=public_news,
            published_dossiers=public_dossiers, scope_reviews=_load_phase_documents(args.inbox_dir, "scope-reviews", maximum_bytes=30_000),
            scope_verifications=_load_phase_documents(args.inbox_dir, "scope-verifications", maximum_bytes=60_000),
            rechecks=load_rechecks(args.inbox_dir / "runtime/news-scope-rechecks.json"),
            drafts=_load_phase_documents(args.inbox_dir, "drafts"), scope_audits=read_json(args.scope_audits), excluded_ids=excluded)
        terminals, reopen = build_issue_state_indexes(**evidence)
        inbox = GitHubInbox(os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"])
        report = reconcile(inbox, terminals, private_revision=args.private_revision,
                           run_id=os.environ.get("GITHUB_RUN_ID"), apply=args.apply, limit=args.limit,
                           excluded_ids=excluded, reopen=reopen)
        print(json.dumps(report, sort_keys=True))
    except (ValueError, KeyError, TypeError, OSError, GitHubRequestError, requests.RequestException, subprocess.SubprocessError):
        raise SystemExit("News issue reconciliation stopped; verify the private evidence and GitHub budget. No Gemini calls were made.") from None


if __name__ == "__main__":
    main()
