"""Offline end-to-end pipeline flow: discovery, private extraction, publisher
chain and public surface, all inside two temporary trees with no network.

The scenario mirrors the scheduled private workflows
(``ops/review-queue/.github/workflows/extract.yml`` and ``publish-locations.yml``)
using the real CLIs and real storage/budget machinery; only the outermost HTTP
and GitHub clients are faked, plus ``require_public_dns`` which would otherwise
resolve real names. A socket guard refuses any non-loopback connection for the
whole test.
"""

import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import threading
import urllib.request

import pytest
import requests

import mdh_pipeline.history as history
import mdh_pipeline.review_queue as review_queue
import mdh_pipeline.gemini_review as gemini_review
import mdh_pipeline.news_selection as news_selection
import mdh_pipeline.news_processing as news_processing
import mdh_pipeline.news_requests as news_requests
import mdh_pipeline.automatic_news as automatic_news
import mdh_pipeline.automatic_locations as automatic_locations
import mdh_pipeline.publication_snapshot as publication_snapshot
from mdh_pipeline.history import SOURCES
from mdh_pipeline.review_queue import issue_body

ROOT = Path(__file__).resolve().parents[1]
SOURCE = next(source for source in SOURCES if source["id"] == "co-latino")
NODE = shutil.which("node")

PRIVATE_NAME = "María Reservada Confidencial"
FORBIDDEN_SENTENCE = "Testimonio interno que jamás debe aparecer en la ficha pública."
NOW = "2026-10-15T12:00:00+00:00"
RUN_EXTRACT = "100001"
RUN_PUBLISH = "200002"

MODEL = "gemini-3.5-flash-lite"
BUDGET_CONFIG = {
    "project_id": "mdh-offline",
    "model": MODEL,
    "rpm": 60,
    "rpd": 250,
    "input_tpm": 200_000,
    "daily_budget": 240,
    "batch_ceiling": 16,
    "verified_at": "2026-10-01T00:00:00Z",
    "limits_source": "operator_cap",
}

PARAGRAPHS = [
    {
        "id": "p1",
        "text": "En El Salvador, una organización de derechos humanos denunció "
        "detenciones arbitrarias cometidas por agentes policiales.",
    },
    {
        "id": "p2",
        "text": "Los hechos relatados ocurrieron en el departamento de San Salvador "
        "y afectaron a personas adultas.",
    },
    {"id": "p3", "text": f"La detenida {PRIVATE_NAME} relató que no pudo contactar a un abogado."},
    {"id": "p4", "text": FORBIDDEN_SENTENCE},
]
ARTICLE_TEXT = "\n\n".join(p["text"] for p in PARAGRAPHS)
POST_URL = "https://www.diariocolatino.com/2026/10/10/detenciones-denuncia/"
POST = {
    "id": 4242,
    "date": "2026-10-10T10:00:00",
    "link": POST_URL,
    "title": {"rendered": "Denuncian detenciones arbitrarias en San Salvador"},
    "status": "publish",
    "content": {"rendered": "".join(f"<p>{p['text']}</p>" for p in PARAGRAPHS)},
}


class FakeResponse:
    def __init__(self, body, *, status=200, headers=None):
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.status_code, self.headers = status, dict(headers or {})
        self.is_redirect = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}", response=self)

    def iter_content(self, chunk_size):
        for start in range(0, len(self.body), chunk_size):
            yield self.body[start : start + chunk_size]

    def close(self):
        pass


def scope_verdict(candidate_id):
    return {
        "assessments": [
            {
                "candidate_id": candidate_id,
                "jurisdiction": "SV",
                "relevance": "relevant",
                "jurisdiction_paragraph_ids": ["p1"],
                "relevance_paragraph_ids": ["p1"],
            }
        ]
    }


def extraction_document():
    def cite(paragraph, quote):
        return {"paragraph_id": paragraph, "quote": quote}

    def claim(value, evidence=None):
        return {
            "value": value,
            "status": "reported" if value is not None else "not_reported",
            "citations": [evidence] if evidence else [],
        }

    record = {
        key: claim(None)
        for key in (
            "title",
            "summary",
            "category",
            "alleged_violation",
            "facts",
            "affected_persons",
            "department",
            "municipality",
            "public_place",
            "event_date",
            "event_date_end",
            "event_time",
            "reported_actors",
            "institutional_sources",
            "vulnerable_groups",
            "themes",
            "hypothesis",
            "instrument",
            "penitentiary_related",
        )
    }
    record.update(
        title=claim("Denuncia de detenciones arbitrarias", cite("p1", "detenciones arbitrarias")),
        summary=claim(
            "Una organización denuncia detenciones arbitrarias por agentes policiales.",
            cite("p1", "denunció detenciones arbitrarias"),
        ),
        category=claim("Libertad y debido proceso", cite("p1", "derechos humanos")),
        alleged_violation=claim(
            "Presuntas detenciones arbitrarias.", cite("p1", "detenciones arbitrarias")
        ),
        facts=claim(
            "La organización reporta detenciones por agentes policiales.",
            cite("p1", "agentes policiales"),
        ),
        department=claim("San Salvador", cite("p2", "departamento de San Salvador")),
        reported_actors=claim(["Agentes policiales"], cite("p1", "agentes policiales")),
        themes=claim(["Detención", "Debido proceso"], cite("p1", "detenciones arbitrarias")),
        date_precision="unknown",
    )
    return {
        "jurisdiction": "SV",
        "relevance": "relevant",
        "record_type": "incident",
        "scope_evidence": {
            "jurisdiction": [cite("p1", "En El Salvador")],
            "relevance": [cite("p1", "derechos humanos")],
            "record_type": [cite("p1", "detenciones arbitrarias")],
        },
        "privacy_flags": [],
        "record": record,
    }


def extraction_response():
    document = extraction_document()
    source = {p["id"]: p["text"] for p in PARAGRAPHS}

    def refs(citations):
        return [
            "e" + item["paragraph_id"][1:]
            if item["quote"] in source.get(item["paragraph_id"], "")
            else "e999"
            for item in citations
        ]

    document["scope_evidence"] = {
        name: refs(items) for name, items in document["scope_evidence"].items()
    }
    original = document["record"]
    document["date_precision"] = original.pop("date_precision")
    document["record"] = [
        {
            "field": name,
            "values": field["value"] if isinstance(field["value"], list) else [field["value"]],
            "evidence_refs": refs(field["citations"]),
        }
        for name, field in original.items()
        if field["value"] is not None
    ]
    return {
        "candidates": [
            {
                "finishReason": "STOP",
                "content": {"parts": [{"text": json.dumps(document, ensure_ascii=False)}]},
            }
        ]
    }


def judgment_document(*, namespace=""):
    def ids(*names):
        return [namespace + name for name in names]

    scope = {
        "verdict": "supported",
        "paragraph_ids": ids("p1"),
        "reason_code": "supported_by_source",
    }
    from mdh_pipeline.news_dossier import FIELD_NAMES

    return {
        "jurisdiction": deepcopy(scope),
        "relevance": deepcopy(scope),
        "fields": [
            {
                "field": name,
                "verdict": "supported",
                "paragraph_ids": ids("p2" if name == "department" else "p1"),
                "reason_code": "supported_by_source",
                "privacy": "safe",
            }
            for name in FIELD_NAMES
        ],
        "protected_fields": [],
    }


def gemini_body(payload):
    schema = payload.get("generationConfig", {}).get("responseJsonSchema", {})
    properties = set(schema.get("properties", {}))
    contents = json.dumps(payload["contents"], ensure_ascii=False)
    if "assessments" in properties:
        item_properties = set(schema["properties"]["assessments"]["items"]["properties"])
        if "judgment" in item_properties:
            ids = scope_candidate_ids(contents)
            digest = hashlib.sha256(ARTICLE_TEXT.encode()).hexdigest()
            body = {
                "assessments": [
                    {
                        "candidate_id": identifier,
                        "source_content_sha256": digest,
                        "judgment": judgment_document(namespace=f"n{index}-"),
                    }
                    for index, identifier in enumerate(ids, 1)
                ]
            }
        else:
            ids = scope_candidate_ids(contents)
            body = {
                "assessments": [
                    dict(verdict, candidate_id=identifier)
                    for identifier in ids
                    for verdict in scope_verdict(identifier)["assessments"]
                ]
            }
    elif "record" in properties:
        return extraction_response()
    else:
        body = judgment_document()
    return {
        "candidates": [
            {
                "finishReason": "STOP",
                "content": {"parts": [{"text": json.dumps(body, ensure_ascii=False)}]},
            }
        ]
    }


def scope_candidate_ids(text):
    ids = re.findall(r'candidate_id["\\\s:]+([0-9a-f]{64})', text)
    return ids or ["0" * 64]


class FakeSession:
    """One dispatcher for every ``requests.Session()``: WordPress discovery and
    article fetches, Gemini generateContent and RSS feeds, all in memory."""

    lock = threading.Lock()
    calls = []

    def __init__(self):
        self.headers = {}
        self.auth = None
        self.cookies = {}
        self.trust_env = False

    def get(self, url, **kwargs):
        with FakeSession.lock:
            FakeSession.calls.append(("GET", url))
        if url.startswith(SOURCE["endpoint"]):
            if "slug=" in url or re.search(re.escape(SOURCE["endpoint"]) + r"/[0-9]+", url):
                return FakeResponse(
                    {
                        "id": POST["id"],
                        "date": POST["date"],
                        "link": POST["link"],
                        "title": POST["title"],
                        "status": "publish",
                        "content": POST["content"],
                    },
                    headers={"Content-Type": "application/json"},
                )
            posts, total, pages = ([POST], 1, 1) if "after=2026-09-30" in url else ([], 0, 0)
            return FakeResponse(
                posts,
                headers={
                    "Content-Type": "application/json",
                    "X-WP-Total": str(total),
                    "X-WP-TotalPages": str(pages),
                },
            )
        if any(url.startswith(source["endpoint"]) for source in SOURCES):
            return FakeResponse(
                [],
                headers={
                    "Content-Type": "application/json",
                    "X-WP-Total": "0",
                    "X-WP-TotalPages": "0",
                },
            )
        return FakeResponse(
            b"""<?xml version="1.0"?><rss version="2.0"><channel>
            <title>feed</title><item><title>Off-topic</title>
            <link>https://example.invalid/off-topic</link>
            <pubDate>Sat, 10 Oct 2026 10:00:00 GMT</pubDate></item></channel></rss>""",
            headers={"Content-Type": "application/rss+xml"},
        )

    def post(self, url, **kwargs):
        with FakeSession.lock:
            FakeSession.calls.append(("POST", url))
        assert url.startswith("https://generativelanguage.googleapis.com/"), url
        return FakeResponse(
            gemini_body(kwargs.get("json", {})), headers={"Content-Type": "application/json"}
        )

    def request(self, *args, **kwargs):
        raise AssertionError("raw requests.Session.request is not faked")

    def close(self):
        pass


class FakeInbox:
    """In-memory private repository: issues, labels, git tree and contents files."""

    repository = "RauPro/MDH-SV-review"
    base = "/repos/RauPro/MDH-SV-review"
    FOLDERS = {
        "drafts",
        "processing",
        "scope-reviews",
        "scope-verifications",
        "scope-attempts",
        "automatic-news",
        "runtime",
    }

    def __init__(self):
        self.issues = {}
        self.next_issue = 0
        self.files = {}
        self.calls = []
        self.writes = []
        self.reads = []

    @staticmethod
    def sha(value):
        return hashlib.sha1(json.dumps(value, sort_keys=True).encode()).hexdigest()

    def store(self, path, value):
        raw = json.dumps(value, ensure_ascii=False).encode()
        self.files[path] = {
            "path": path,
            "value": deepcopy(value),
            "sha": self.sha(value),
            "raw": raw,
        }

    def document(self, path):
        return deepcopy(self.files[path]["value"])

    def get_file(self, path):
        self.reads.append(path)
        name = path.split("/contents/")[-1]
        file = self.files.get(name)
        if file is None:
            return None
        return {
            "type": "file",
            "path": name,
            "sha": file["sha"],
            "size": len(file["raw"]),
            "encoding": "base64",
            "content": base64.b64encode(file["raw"]).decode(),
        }

    def ensure_labels(self):
        return None

    def existing_ids(self):
        ids = set()
        for issue in self.issues.values():
            match = re.search(r"<!-- mdh-candidate:([0-9a-f]{64}) -->", issue.get("body") or "")
            if match:
                ids.add(match[1])
        return ids

    def enqueue(self, candidate):
        title = re.sub(r"[@`<>\[\]]", "", candidate["source_title"])
        return self.call(
            "POST",
            self.base + "/issues",
            json={
                "title": "[Revisar] " + title,
                "body": issue_body(candidate),
                "labels": ["review/pending"],
            },
        )

    def call(self, method, path, **kwargs):
        self.calls.append((method, path, deepcopy(kwargs)))
        if method == "GET" and path == "/repos/RauPro/MDH-SV/issues":
            return []
        if method == "GET" and path.endswith("/labels"):
            return [{"name": "review/pending"}]
        if method == "POST" and path.endswith("/labels"):
            return {"name": kwargs["json"]["name"]}
        if method == "GET" and path.endswith("/git/trees/main"):
            return {
                "truncated": False,
                "tree": [
                    {"path": file["path"], "type": "blob", "sha": file["sha"]}
                    for file in self.files.values()
                ],
            }
        if method == "GET" and path.endswith("/issues"):
            params = kwargs.get("params", {})
            rows = list(self.issues.values())
            if params.get("state") in {"open", "closed"}:
                rows = [issue for issue in rows if issue["state"] == params["state"]]
            if params.get("labels"):
                rows = [
                    issue
                    for issue in rows
                    if params["labels"] in {label["name"] for label in issue["labels"]}
                ]
            page = params.get("page", 1)
            return deepcopy(rows[(page - 1) * 100 : page * 100])
        if "/issues/" in path:
            number = int(path.rsplit("/", 1)[1])
            if method == "GET":
                return deepcopy(self.issues[number])
            if method == "PATCH":
                payload = deepcopy(kwargs["json"])
                if "labels" in payload:
                    payload["labels"] = [{"name": label} for label in payload["labels"]]
                self.issues[number].update(payload)
                return deepcopy(self.issues[number])
        if method == "POST" and path.endswith("/issues"):
            self.next_issue += 1
            issue = {
                "number": self.next_issue,
                "state": "open",
                "title": kwargs["json"]["title"],
                "body": kwargs["json"]["body"],
                "user": {"login": "github-actions[bot]"},
                "labels": [{"name": label} for label in kwargs["json"].get("labels", [])],
                "created_at": NOW,
            }
            self.issues[self.next_issue] = issue
            return deepcopy(issue)
        if method == "PUT" and "/contents/" in path:
            name = path.split("/contents/", 1)[1]
            folder = name.split("/", 1)[0]
            assert folder in self.FOLDERS or folder == name, name
            previous = self.files.get(name)
            if previous is not None:
                assert kwargs["json"].get("sha") == previous["sha"], (
                    "Update must be revision guarded"
                )
            self.store(name, json.loads(base64.b64decode(kwargs["json"]["content"])))
            self.writes.append((name, self.document(name)))
            return {"content": {"sha": self.files[name]["sha"]}}
        if method == "GET" and "/contents/" in path:
            return self.get_file(path)
        raise AssertionError(f"Unexpected inbox operation {method} {path}")

    def sync_to_disk(self, root):
        for name, file in self.files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(file["raw"])


def git(*args, cwd):
    subprocess.run(
        ["git", "-c", "user.email=offline@example.invalid", "-c", "user.name=offline", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def run_main(module, argv):
    previous = sys.argv
    try:
        sys.argv = ["prog", *argv]
        module.main()
    finally:
        sys.argv = previous


@pytest.fixture
def no_sockets(monkeypatch):
    real_connect = socket.socket.connect
    allowed = {"127.0.0.1", "::1", "localhost"}

    def connect(sock, address):
        host = address[0] if isinstance(address, tuple) else address
        if isinstance(host, bytes):
            host = host.decode()
        if host not in allowed and not str(host).startswith("/"):
            raise AssertionError(f"Blocked offline network connection to {address!r}")
        return real_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", connect)


def test_full_offline_pipeline_flow(tmp_path, monkeypatch, no_sockets):
    monkeypatch.setattr(requests, "Session", FakeSession)

    for module in (history, review_queue):
        monkeypatch.setattr(module, "require_public_dns", lambda host: None)
    import mdh_pipeline.article_source as article_source
    import mdh_pipeline.archive_sources as archive_sources

    monkeypatch.setattr(article_source, "require_public_dns", lambda host: None)
    monkeypatch.setattr(archive_sources, "require_public_dns", lambda host: None)
    monkeypatch.setattr(__import__("time"), "sleep", lambda *_: None)
    monkeypatch.setenv("GEMINI_API_KEY", "offline-flow-test-key")
    monkeypatch.setenv("GEMINI_MODEL", MODEL)
    monkeypatch.setenv("GITHUB_REPOSITORY", FakeInbox.repository)
    monkeypatch.setenv("GH_TOKEN", "offline-flow-test-token")
    monkeypatch.setenv("MDH_GEMINI_BUDGET_CONFIG", json.dumps(BUDGET_CONFIG))
    monkeypatch.setenv("GITHUB_RUN_ID", RUN_EXTRACT)
    monkeypatch.delenv("MDH_ALLOCATION_SOURCE_RUN_ID", raising=False)
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    monkeypatch.delenv("MDH_SCHEDULED_HOUR", raising=False)
    monkeypatch.delenv("MDH_TRIGGER_SOURCE", raising=False)

    inbox = FakeInbox()
    # The paced proxy calls the real GitHubInbox class helpers (ensure_labels,
    # enqueue) against the faked instance, so the patched class must keep those
    # real methods while its constructor returns the fixture inbox.
    InboxFactory = type(
        "InboxFactory",
        (),
        {
            "__new__": staticmethod(lambda *args, **kwargs: inbox),
            "ensure_labels": review_queue.GitHubInbox.ensure_labels,
            "enqueue": review_queue.GitHubInbox.enqueue,
        },
    )
    for module in (review_queue, gemini_review, news_requests, automatic_news):
        if hasattr(module, "GitHubInbox"):
            monkeypatch.setattr(module, "GitHubInbox", InboxFactory)
    from mdh_pipeline.gemini_budget import BudgetConfig, initial_state

    config = BudgetConfig(**BUDGET_CONFIG)
    state = initial_state(config, now=datetime.now(timezone.utc), used_requests=0)
    # The explicit provisioning cooldown only guards the unrecoverable first
    # minute; the fixture starts after it.
    state["cooldown_until"] = None
    inbox.store("runtime/gemini-budget.json", state)
    publication = tmp_path / "publication"
    inbox_dir = tmp_path / "inbox"
    work = tmp_path / "work"
    for folder in (publication, inbox_dir, work):
        folder.mkdir()
    shutil.copytree(ROOT / "data", publication / "data")
    shutil.copytree(
        ROOT / "apps" / "web" / "public",
        publication / "apps" / "web" / "public",
        dirs_exist_ok=True,
    )
    # The private scope phase bounds source reads per run and orders pending
    # candidates oldest-first, so the real 14k-record backlog would starve the
    # fixture post for many cycles. Start every source history empty (schema,
    # source id and scope version kept); the collection run then discovers the
    # fixture post as the only pending candidate, like a fresh queue.
    source_ids = {source["id"] for source in SOURCES}
    for state in (publication / "data/history").glob("*.json"):
        if state.stem not in source_ids:
            continue
        document = json.loads(state.read_text())
        state.write_text(
            json.dumps(
                {key: value for key, value in document.items() if key != "records"}
                | {"months": {}, "records": []},
                ensure_ascii=False,
            )
        )
    git("init", "-q", cwd=publication)
    git("add", "data", "apps/web/public", cwd=publication)
    git("commit", "-q", "-m", "base", cwd=publication)
    git("init", "-q", cwd=inbox_dir)
    git("commit", "-q", "--allow-empty", "-m", "base", cwd=inbox_dir)

    # -- Discovery: the fictitious post enters data/history/co-latino.json.
    run_main(
        history,
        [
            "--start",
            "2026-10-01",
            "--end",
            "2026-10-31",
            "--data-dir",
            str(publication / "data/history"),
            "--output",
            str(work / "collect-export.json"),
            "--candidates-only",
        ],
    )
    collected = json.loads((publication / "data/history/co-latino.json").read_text())
    candidate = next(record for record in collected["records"] if record["source_url"] == POST_URL)
    assert candidate["discovery"]["source_post_id"] == POST["id"]

    run_main(
        history,
        [
            "--export-only",
            "--candidates-only",
            "--data-dir",
            str(publication / "data/history"),
            "--output",
            str(work / "candidates.json"),
        ],
    )
    candidates = json.loads((work / "candidates.json").read_text())
    assert candidate["id"] in {r["id"] for r in candidates["records"]}

    # -- Extraction cycle, mirroring extract.yml.
    run_main(review_queue, ["collect"])
    run_main(
        gemini_review,
        [
            "--archive",
            str(work / "candidates.json"),
            "--limit",
            "16",
            "--scope-first",
            "--inbox-dir",
            str(inbox_dir),
            "--exceptions",
            str(publication / "data/selection/news_exceptions.json"),
            "--scope-audits",
            str(publication / "data/selection/scope_audits.json"),
        ],
    )
    inbox.sync_to_disk(inbox_dir)
    assert (inbox_dir / "drafts").is_dir(), "extraction produced no drafts"
    drafts = list((inbox_dir / "drafts").iterdir())
    assert candidate["id"] in {path.stem for path in drafts}
    git("add", "-A", cwd=inbox_dir)
    git("commit", "-q", "-m", "queue", cwd=inbox_dir)

    # -- Publisher chain, mirroring publish-locations.yml step order.
    run_main(publication_snapshot, ["check-inputs", "--repository", str(publication)])
    reviewed_path = publication / "data/reviewed/news_dossiers.json"
    reviewed = json.loads(reviewed_path.read_text())
    reviewed_ids = {d["candidate_id"] for d in reviewed["dossiers"]}
    from mdh_pipeline.gemini_review import editorial_excluded_ids
    from mdh_pipeline.news_requests import withdrawn_ids

    excluded = editorial_excluded_ids(inbox, imported_ids=reviewed_ids) | withdrawn_ids(
        str(publication / "data/selection/news_exceptions.json")
    )
    (work / "excluded-news.json").write_text(json.dumps(sorted(excluded)))
    (work / "excluded-locations.json").write_text(json.dumps(sorted(excluded | reviewed_ids)))
    run_main(
        history,
        [
            "--export-only",
            "--candidates-only",
            "--data-dir",
            str(publication / "data/history"),
            "--output",
            str(work / "candidates.json"),
        ],
    )
    run_main(
        news_selection,
        [
            "--drafts",
            str(inbox_dir / "drafts"),
            "--scope-reviews",
            str(inbox_dir / "scope-reviews"),
            "--scope-verifications",
            str(inbox_dir / "scope-verifications"),
            "--scope-rechecks",
            str(inbox_dir / "runtime/news-scope-rechecks.json"),
            "--archive",
            str(work / "candidates.json"),
            "--output",
            str(publication / "data/selection/news.json"),
            "--reviewed-dossiers",
            str(reviewed_path),
            "--scope-audits",
            str(publication / "data/selection/scope_audits.json"),
            "--excluded-ids",
            str(work / "excluded-news.json"),
        ],
    )
    run_main(
        history,
        [
            "--export-only",
            "--data-dir",
            str(publication / "data/history"),
            "--selection",
            str(publication / "data/selection/news.json"),
            "--reviewed-news",
            str(reviewed_path),
            "--output",
            str(publication / "apps/web/public/archive/news.json"),
            "--excluded-ids",
            str(work / "excluded-news.json"),
            "--exceptions",
            str(publication / "data/selection/news_exceptions.json"),
        ],
    )
    archive = json.loads((publication / "apps/web/public/archive/news.json").read_text())
    assert candidate["id"] in {r["id"] for r in archive["records"]}
    entry = next(r for r in archive["records"] if r["id"] == candidate["id"])
    assert entry["selection"]["human_reviewed"] is False
    git("ls-files", "-z", "--", "drafts/", cwd=inbox_dir)
    subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "drafts/"], cwd=inbox_dir, check=True)
    run_main(
        news_processing,
        [
            "--drafts",
            str(inbox_dir / "drafts"),
            "--archive",
            str(publication / "apps/web/public/archive/news.json"),
            "--output",
            str(publication / "apps/web/public/archive/processing.json"),
            "--snapshot-complete",
        ],
    )
    run_main(
        news_requests,
        [
            "sync",
            "--archive",
            str(publication / "apps/web/public/archive/news.json"),
            "--exceptions",
            str(publication / "data/selection/news_exceptions.json"),
            "--output",
            str(work / "request-generations.json"),
        ],
    )
    monkeypatch.setenv("MDH_ALLOCATION_SOURCE_RUN_ID", RUN_EXTRACT)
    monkeypatch.setenv("GITHUB_RUN_ID", RUN_PUBLISH)
    run_main(
        automatic_news,
        [
            "evaluate",
            "--inbox-dir",
            str(inbox_dir),
            "--archive",
            str(publication / "apps/web/public/archive/news.json"),
            "--excluded-ids",
            str(work / "excluded-locations.json"),
            "--request-generations",
            str(work / "request-generations.json"),
            "--limit",
            "16",
            "--batch-size",
            "4",
        ],
    )
    inbox.sync_to_disk(inbox_dir)
    run_main(
        automatic_news,
        [
            "export",
            "--inbox-dir",
            str(inbox_dir),
            "--archive",
            str(publication / "apps/web/public/archive/news.json"),
            "--reviewed",
            str(reviewed_path),
            "--excluded-ids",
            str(work / "excluded-news.json"),
            "--request-generations",
            str(work / "request-generations.json"),
            "--automatic-output",
            str(publication / "data/automated/news_dossiers.json"),
            "--output",
            str(publication / "apps/web/public/archive/dossiers.json"),
        ],
    )
    run_main(
        automatic_locations,
        [
            "--drafts",
            str(inbox_dir / "drafts"),
            "--archive",
            str(publication / "apps/web/public/archive/news.json"),
            "--output",
            str(publication / "apps/web/public/archive/locations.json"),
            "--excluded-ids",
            str(work / "excluded-locations.json"),
            "--limit",
            "16",
            "--cache-file",
            str(work / "source-checks.json"),
        ],
    )
    env = {**os.environ, "PATH": os.environ["PATH"]}
    for script in ("sync-news-publication.mjs", "sync-news-processing.mjs", "check-data.mjs"):
        subprocess.run(
            [NODE, str(ROOT / "apps/web/scripts" / script), "--root", str(publication)],
            check=True,
            env=env,
            capture_output=True,
        )

    # -- Selection and dossier assertions.
    news = json.loads((publication / "apps/web/public/archive/news.json").read_text())
    item = next(r for r in news["records"] if r["id"] == candidate["id"])
    assert (
        item["selection"]["jurisdiction"] == "SV" and item["selection"]["relevance"] == "relevant"
    )
    dossiers = json.loads((publication / "apps/web/public/archive/dossiers.json").read_text())
    dossier = next(d for d in dossiers["dossiers"] if d["candidate_id"] == candidate["id"])
    assert dossier["schema_version"] == 2 and dossier["human_reviewed"] is False
    assert dossier["automation"]["corroboration"] == "single_source"
    assert dossier["fields"]["department"]["status"] in {"reported", "validated"}

    # -- Privacy assertions over every public output byte.
    forbidden = [PRIVATE_NAME, FORBIDDEN_SENTENCE]
    for path in publication.rglob("*"):
        if path.is_file() and ".git" not in path.parts:
            raw = path.read_bytes()
            for marker in forbidden:
                assert marker.encode() not in raw, f"{marker!r} leaked into {path}"
            assert b"generativelanguage" not in raw
    assert PRIVATE_NAME not in json.dumps(dossier, ensure_ascii=False)

    # -- Public surface: stage the API onto the temp root and query it.
    for folder in ("server", "api", "shared"):
        shutil.copytree(ROOT / folder, publication / folder)
    shutil.copy(ROOT / "instance.json", publication / "instance.json")
    shutil.copytree(
        ROOT / "apps" / "pipeline" / "src" / "mdh_pipeline" / "data",
        publication / "apps" / "pipeline" / "src" / "mdh_pipeline" / "data",
        dirs_exist_ok=True,
    )
    (publication / "node_modules").symlink_to(ROOT / "node_modules", target_is_directory=True)
    launcher = work / "serve.mjs"
    launcher.write_text(
        "import { createPublicServer } from './server-link.mjs';\n"
        "const server = createPublicServer();\n"
        "server.listen(0, '127.0.0.1', () => console.log(server.address().port));\n"
        "setInterval(() => {}, 60000);\n"
    )
    (work / "server-link.mjs").write_text(
        f"export {{ createPublicServer }} from '{(publication / 'server/dev.mjs').as_posix()}';\n"
    )
    server = subprocess.Popen(
        [NODE, str(launcher)],
        cwd=work,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        port = int(server.stdout.readline().strip())
        base = f"http://127.0.0.1:{port}"
        api = urllib.request.urlopen(base + "/api/v1/news", timeout=10)
        api_news = json.loads(api.read())
        assert api.status == 200
        api_item = next(r for r in api_news["data"] if r["id"] == candidate["id"])
        assert api_item["processing_status"] == "automatically_published"
        assert PRIVATE_NAME not in json.dumps(api_news, ensure_ascii=False)
        assert FORBIDDEN_SENTENCE not in json.dumps(api_news, ensure_ascii=False)
        mcp_request = urllib.request.Request(
            base + "/mcp",
            method="POST",
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "search_news", "arguments": {"q": "detenciones"}},
                }
            ).encode(),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
        mcp = json.loads(urllib.request.urlopen(mcp_request, timeout=10).read())
        mcp_text = json.dumps(mcp, ensure_ascii=False)
        assert "detenciones" in mcp_text and PRIVATE_NAME not in mcp_text
        openapi = json.loads(
            urllib.request.urlopen(base + "/api/v1/openapi.json", timeout=10).read()
        )
        validator = work / "validate-openapi.mjs"
        validator.write_text(
            "import { readFileSync } from 'node:fs';\n"
            f"import {{ responseContracts }} from '{(ROOT / 'tests/openapi-validator.mjs').as_posix()}';\n"
            "const spec = JSON.parse(readFileSync(process.argv[2], 'utf8'));\n"
            "const value = JSON.parse(readFileSync(process.argv[3], 'utf8'));\n"
            "responseContracts(spec).validate('/api/v1/news', 200, value);\n"
        )
        (work / "openapi.json").write_text(json.dumps(openapi))
        (work / "api-news.json").write_text(json.dumps(api_news))
        subprocess.run(
            [NODE, str(validator), str(work / "openapi.json"), str(work / "api-news.json")],
            check=True,
            cwd=ROOT,
            env=env,
            capture_output=True,
        )
    finally:
        server.kill()
        server.wait()

    # -- Idempotence: re-running the publisher chain changes nothing.
    gemini_posts = sum(1 for method, _ in FakeSession.calls if method == "POST")
    snapshot = {
        path: path.read_bytes()
        for path in publication.rglob("*")
        if path.is_file() and ".git" not in path.parts
    }
    run_main(
        history,
        [
            "--export-only",
            "--candidates-only",
            "--data-dir",
            str(publication / "data/history"),
            "--output",
            str(work / "candidates.json"),
        ],
    )
    run_main(
        news_selection,
        [
            "--drafts",
            str(inbox_dir / "drafts"),
            "--scope-reviews",
            str(inbox_dir / "scope-reviews"),
            "--scope-verifications",
            str(inbox_dir / "scope-verifications"),
            "--scope-rechecks",
            str(inbox_dir / "runtime/news-scope-rechecks.json"),
            "--archive",
            str(work / "candidates.json"),
            "--output",
            str(publication / "data/selection/news.json"),
            "--reviewed-dossiers",
            str(reviewed_path),
            "--scope-audits",
            str(publication / "data/selection/scope_audits.json"),
            "--excluded-ids",
            str(work / "excluded-news.json"),
        ],
    )
    run_main(
        history,
        [
            "--export-only",
            "--data-dir",
            str(publication / "data/history"),
            "--selection",
            str(publication / "data/selection/news.json"),
            "--reviewed-news",
            str(reviewed_path),
            "--output",
            str(publication / "apps/web/public/archive/news.json"),
            "--excluded-ids",
            str(work / "excluded-news.json"),
            "--exceptions",
            str(publication / "data/selection/news_exceptions.json"),
        ],
    )
    run_main(
        news_processing,
        [
            "--drafts",
            str(inbox_dir / "drafts"),
            "--archive",
            str(publication / "apps/web/public/archive/news.json"),
            "--output",
            str(publication / "apps/web/public/archive/processing.json"),
            "--snapshot-complete",
        ],
    )
    run_main(
        news_requests,
        [
            "sync",
            "--archive",
            str(publication / "apps/web/public/archive/news.json"),
            "--exceptions",
            str(publication / "data/selection/news_exceptions.json"),
            "--output",
            str(work / "request-generations.json"),
        ],
    )
    run_main(
        automatic_news,
        [
            "evaluate",
            "--inbox-dir",
            str(inbox_dir),
            "--archive",
            str(publication / "apps/web/public/archive/news.json"),
            "--excluded-ids",
            str(work / "excluded-locations.json"),
            "--request-generations",
            str(work / "request-generations.json"),
            "--limit",
            "16",
            "--batch-size",
            "4",
        ],
    )
    run_main(
        automatic_news,
        [
            "export",
            "--inbox-dir",
            str(inbox_dir),
            "--archive",
            str(publication / "apps/web/public/archive/news.json"),
            "--reviewed",
            str(reviewed_path),
            "--excluded-ids",
            str(work / "excluded-news.json"),
            "--request-generations",
            str(work / "request-generations.json"),
            "--automatic-output",
            str(publication / "data/automated/news_dossiers.json"),
            "--output",
            str(publication / "apps/web/public/archive/dossiers.json"),
        ],
    )
    run_main(
        automatic_locations,
        [
            "--drafts",
            str(inbox_dir / "drafts"),
            "--archive",
            str(publication / "apps/web/public/archive/news.json"),
            "--output",
            str(publication / "apps/web/public/archive/locations.json"),
            "--excluded-ids",
            str(work / "excluded-locations.json"),
            "--limit",
            "16",
            "--cache-file",
            str(work / "source-checks.json"),
        ],
    )
    for script in ("sync-news-publication.mjs", "sync-news-processing.mjs", "check-data.mjs"):
        subprocess.run(
            [NODE, str(ROOT / "apps/web/scripts" / script), "--root", str(publication)],
            check=True,
            env=env,
            capture_output=True,
        )
    assert sum(1 for method, _ in FakeSession.calls if method == "POST") == gemini_posts
    assert {
        path: path.read_bytes()
        for path in publication.rglob("*")
        if path.is_file() and ".git" not in path.parts
    } == snapshot
