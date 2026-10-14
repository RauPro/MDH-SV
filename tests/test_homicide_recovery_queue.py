import base64
import copy
import hashlib
import json
from datetime import date

import pytest

from mdh_pipeline.history import reference_from_post
from mdh_pipeline.homicide_recovery import NOTICE
from mdh_pipeline.homicide_recovery_queue import (
    LABELS, REPOSITORY, ROOT, batch_id, enqueue_recovery, main,
)
from mdh_pipeline.publishers import SOURCES
from mdh_pipeline.review_queue import GitHubRequestError

NOW = "2026-10-14T01:00:00+00:00"


def documents(n=2):
    refs = [reference_from_post({"id": i, "date": f"2015-05-{i:02}T12:00:00",
        "link": f"https://www.diariocolatino.com/homicidios-{i}/",
        "title": {"rendered": f"Investigan homicidio en El Salvador {i}"}},
        SOURCES[0], date(2015, 1, 1), date(2015, 12, 31), NOW) for i in range(1, n + 1)]
    candidates = {"schema_version": 1, "generated_at": NOW, "records": refs}
    report = {"schema_version": 1, "year": 2015, "generated_at": NOW,
        "requests": 1, "requests_total": 1, "response_bytes": 100,
        "new_candidates": n, "publications": 0, "gemini_requests": 0,
        "review_required": True, "coverage": "partial", "notice": NOTICE,
        "sources": [{"source_id": "co-latino", "publisher": "Diario Co Latino",
            "baseline_2015": 590, "new_candidates": n, "counts": {},
            "blocked": False, "jobs_done": 1, "jobs_pending": 11, "errors": []}]}
    return candidates, report


class Inbox:
    repository = REPOSITORY
    base = "/repos/" + REPOSITORY

    def __init__(self):
        self.files, self.issues, self.calls = {}, [], []
        self.private = True
        self.uncertain_post = False
        self.uncertain_put = False
        self.fail_state = False

    def get_file(self, path):
        self.calls.append(("GET_FILE", path, {}))
        relative = path.split("/contents/", 1)[1]
        if relative not in self.files:
            return None
        raw = self.files[relative]
        return {"type": "file", "path": relative, "encoding": "base64", "size": len(raw),
                "sha": hashlib.sha256(raw).hexdigest()[:40], "content": base64.b64encode(raw).decode()}

    def call(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        payload = kwargs.get("json", {})
        if method == "GET" and path == self.base:
            return {"full_name": self.repository, "private": self.private}
        if method == "GET" and path == self.base + "/contents/" + ROOT:
            entries = [{"name": relative.rsplit("/", 1)[1], "path": relative, "type": "file"}
                       for relative in self.files if relative.startswith(ROOT + "/")
                       and "/" not in relative[len(ROOT) + 1:]]
            if any(relative.startswith(ROOT + "/state/") for relative in self.files):
                entries.append({"name": "state", "path": ROOT + "/state", "type": "dir"})
            if not entries:
                raise GitHubRequestError(404)
            return entries
        if method == "GET" and path.endswith("/labels"):
            return [{"name": name} for name in LABELS]
        if method == "GET" and path.endswith("/issues"):
            assert kwargs["params"]["state"] == "all"
            assert "labels" not in kwargs["params"]
            return self.issues
        if method == "GET" and "/issues/" in path:
            return self.issues[int(path.rsplit("/", 1)[1]) - 1]
        if method == "POST" and path.endswith("/issues"):
            issue = {**payload, "number": len(self.issues) + 1, "state": "open"}
            self.issues.append(issue)
            if self.uncertain_post:
                self.uncertain_post = False
                raise RuntimeError("uncertain POST")
            return issue
        if method == "PUT" and "/contents/" in path:
            relative = path.split("/contents/", 1)[1]
            assert relative not in self.files  # immutable; never replaces a review artifact
            if "/state/" in relative and self.fail_state:
                self.fail_state = False
                raise RuntimeError("interrupted state write")
            self.files[relative] = base64.b64decode(payload["content"])
            if self.uncertain_put:
                self.uncertain_put = False
                raise RuntimeError("uncertain PUT")
            return {"content": {"sha": hashlib.sha256(self.files[relative]).hexdigest()[:40]}}
        raise AssertionError((method, path, kwargs))

    @property
    def mutations(self):
        return [item for item in self.calls if item[0] in {"POST", "PUT", "PATCH"}]


def test_private_batch_has_one_issue_without_individual_extractor_markers():
    inbox = Inbox()
    candidates, report = documents()
    before = copy.deepcopy((candidates, report))
    result = enqueue_recovery(inbox, candidates, report)
    revision = result["batch_id"]
    assert result["new_candidates"] == 2 and result["by_source"] == {"co-latino": 2}
    assert result["publications"] == result["gemini_requests"] == 0
    assert result["issue_url"] == f"https://github.com/{REPOSITORY}/issues/1"
    assert set(inbox.files) == {f"{ROOT}/{revision}.json", f"{ROOT}/state/{revision}.issue.json"}
    stored = json.loads(inbox.files[f"{ROOT}/{revision}.json"])
    assert stored["candidates"] == candidates and stored["report"] == report
    assert stored["status"] == "pending" and stored["review_required"] is True
    assert len(inbox.issues) == 1
    issue = inbox.issues[0]
    assert issue["labels"] == list(LABELS)
    assert "review/pending" not in issue["labels"] and "mdh-candidate:" not in issue["body"]
    assert "Diario Co Latino | 2" in issue["body"] and "PR posterior" in issue["body"]
    assert "no necesariamente a la fecha del hecho" in issue["body"]
    assert (candidates, report) == before


def test_reordering_and_fresh_timestamps_reuse_immutable_batch_and_issue():
    inbox = Inbox()
    candidates, report = documents()
    first = enqueue_recovery(inbox, candidates, report)
    original = dict(inbox.files)
    mutations = len(inbox.mutations)
    candidates["records"].reverse()
    candidates["generated_at"] = report["generated_at"] = "2026-10-15T01:00:00+00:00"
    for ref in candidates["records"]:
        ref["discovery"]["retrieved_at"] = candidates["generated_at"]
    report["requests_total"] = 2
    second = enqueue_recovery(inbox, candidates, report)
    assert second["batch_id"] == first["batch_id"] and second["created"] is False
    assert inbox.files == original and len(inbox.mutations) == mutations


@pytest.mark.parametrize("failure", ["uncertain_post", "uncertain_put", "fail_state"])
def test_interrupted_mutations_resume_without_duplicate_or_overwrite(failure):
    inbox = Inbox()
    setattr(inbox, failure, True)
    candidates, report = documents()
    with pytest.raises(RuntimeError):
        enqueue_recovery(inbox, candidates, report)
    result = enqueue_recovery(inbox, candidates, report)
    assert result["issue"] == 1 and len(inbox.issues) == 1
    assert len(inbox.files) == 2


def test_closed_issue_without_labels_is_not_reopened_or_recreated():
    inbox = Inbox()
    candidates, report = documents()
    first = enqueue_recovery(inbox, candidates, report)
    inbox.issues[0].update(state="closed", labels=[])
    del inbox.files[f"{ROOT}/state/{first['batch_id']}.issue.json"]
    result = enqueue_recovery(inbox, candidates, report)
    assert result["created"] is False and inbox.issues[0]["state"] == "closed"
    assert len(inbox.issues) == 1


def test_changed_reference_gets_new_batch_identity():
    candidates, report = documents()
    original = batch_id(candidates)
    candidates["records"][0]["title"] = "Nueva atribución del mismo homicidio"
    assert batch_id(candidates) != original


def test_incremental_batch_cannot_requeue_records_from_an_earlier_batch():
    inbox = Inbox()
    enqueue_recovery(inbox, *documents(2))
    before = len(inbox.mutations)
    with pytest.raises(ValueError, match="^recovery_batch_overlaps_existing$"):
        enqueue_recovery(inbox, *documents(3))
    assert len(inbox.issues) == 1 and len(inbox.mutations) == before


@pytest.mark.parametrize("variant", ["www_alias", "slash_alias", "wordpress_id"])
def test_cross_batch_duplicate_aliases_are_rejected(variant):
    inbox = Inbox()
    enqueue_recovery(inbox, *documents(1))
    candidates, report = documents(1)
    ref = candidates["records"][0]
    if variant == "www_alias":
        ref["source_url"] = ref["source_url"].replace("www.", "")
        ref["discovery"]["source_post_id"] = 200
    elif variant == "slash_alias":
        ref["source_url"] = ref["source_url"].rstrip("/")
        ref["discovery"]["source_post_id"] = 200
    else:
        ref["source_url"] = "https://www.diariocolatino.com/homicidio-nuevo-slug/"
    ref["id"] = hashlib.sha256(ref["source_url"].encode()).hexdigest()
    before = len(inbox.mutations)
    with pytest.raises(ValueError, match="^recovery_batch_overlaps_existing$"):
        enqueue_recovery(inbox, candidates, report)
    assert len(inbox.mutations) == before


@pytest.mark.parametrize("listing", [
    {"message": "not a directory"},
    [{"name": "unknown.json", "path": ROOT + "/unknown.json", "type": "file"}],
    [{"name": "a" * 64 + ".json", "path": "elsewhere/a.json", "type": "file"}],
    [{"name": "a" * 64 + ".json", "path": ROOT + "/" + "a" * 64 + ".json", "type": "file"}],
    [{"name": "state", "path": ROOT + "/state", "type": "dir"}] * 2,
    [{}] * 1000,
])
def test_inconsistent_batch_listing_fails_before_mutation(listing):
    class BadListing(Inbox):
        def call(self, method, path, **kwargs):
            if method == "GET" and path == self.base + "/contents/" + ROOT:
                return listing
            return super().call(method, path, **kwargs)
    inbox = BadListing()
    with pytest.raises(ValueError):
        enqueue_recovery(inbox, *documents())
    assert not inbox.mutations


def test_disjoint_batch_is_allowed_without_touching_prior_review():
    inbox = Inbox()
    first = enqueue_recovery(inbox, *documents(1))
    candidates, report = documents(2)
    candidates["records"] = candidates["records"][1:]
    report["new_candidates"] = report["sources"][0]["new_candidates"] = 1
    prior = dict(inbox.files)
    second = enqueue_recovery(inbox, candidates, report)
    assert second["issue"] == 2 and second["batch_id"] != first["batch_id"]
    assert all(inbox.files[path] == raw for path, raw in prior.items())


@pytest.mark.parametrize("mutation", [
    lambda c, r: c["records"][0].update(raw_body="private body"),
    lambda c, r: c.update(extra="not allowed"),
    lambda c, r: c["records"][0].update(published_date="2016-01-01"),
    lambda c, r: c["records"][0].update(review_status="approved"),
    lambda c, r: c["records"][0].update(source_url="https://evil.example/homicide"),
    lambda c, r: c["records"].append(c["records"][0]),
    lambda c, r: r.update(review_required=False),
    lambda c, r: r.update(publications=1),
    lambda c, r: r.update(gemini_requests=1),
    lambda c, r: r.update(raw_response="source body"),
    lambda c, r: r.update(new_candidates=99),
    lambda c, r: r["sources"][0].update(new_candidates=99),
    lambda c, r: r["sources"][0].update(publisher="Other publisher"),
    lambda c, r: r.update(schema_version=True),
    lambda c, r: c.update(schema_version=True),
    lambda c, r: c["records"][0]["discovery"].update(retrieved_at="2026-10-14T01:00:00"),
    lambda c, r: c["records"][0]["discovery"].update(retrieved_at="2026-10-15T01:00:00+00:00"),
])
def test_invalid_metadata_and_reports_fail_before_any_write(mutation):
    inbox = Inbox()
    candidates, report = documents()
    mutation(candidates, report)
    with pytest.raises(ValueError):
        enqueue_recovery(inbox, candidates, report)
    assert not inbox.mutations


def test_empty_batch_never_creates_empty_review_issue():
    inbox = Inbox()
    with pytest.raises(ValueError, match="empty"):
        enqueue_recovery(inbox, *documents(0))
    assert not inbox.mutations


@pytest.mark.parametrize("option", ["public", "other_repository"])
def test_only_exact_authorized_private_repository_is_allowed(option):
    inbox = Inbox()
    if option == "public":
        inbox.private = False
    else:
        inbox.repository = "RauPro/Other"
        inbox.base = "/repos/" + inbox.repository
    with pytest.raises(ValueError, match="repository"):
        enqueue_recovery(inbox, *documents())
    assert not inbox.mutations


def test_saved_envelope_hash_tampering_fails_closed():
    inbox = Inbox()
    candidates, report = documents()
    result = enqueue_recovery(inbox, candidates, report)
    path = f"{ROOT}/{result['batch_id']}.json"
    record = json.loads(inbox.files[path])
    record["candidates"]["records"][0]["title"] = "Una atribución alterada"
    inbox.files[path] = json.dumps(record).encode()
    count = len(inbox.mutations)
    with pytest.raises(ValueError, match="hash"):
        enqueue_recovery(inbox, candidates, report)
    assert len(inbox.mutations) == count


def test_duplicate_existing_batch_issues_require_manual_reconciliation():
    inbox = Inbox()
    candidates, report = documents()
    result = enqueue_recovery(inbox, candidates, report)
    inbox.issues.append({**inbox.issues[0], "number": 2})
    del inbox.files[f"{ROOT}/state/{result['batch_id']}.issue.json"]
    count = len(inbox.mutations)
    with pytest.raises(ValueError, match="duplicate"):
        enqueue_recovery(inbox, candidates, report)
    assert len(inbox.mutations) == count


def test_incomplete_issue_enumeration_does_not_create_a_possible_duplicate():
    class FullInbox(Inbox):
        def call(self, method, path, **kwargs):
            if method == "GET" and path.endswith("/issues"):
                return [{"number": i, "body": "unrelated issue"} for i in range(1, 101)]
            return super().call(method, path, **kwargs)
    inbox = FullInbox()
    with pytest.raises(ValueError, match="pagination"):
        enqueue_recovery(inbox, *documents())
    assert not inbox.issues
    assert all(not (method == "POST" and path.endswith("/issues")) for method, path, _ in inbox.calls)


def test_oversized_manifest_rejected_before_remote_mutation(monkeypatch):
    monkeypatch.setattr("mdh_pipeline.homicide_recovery_queue.MAX_BYTES", 100)
    inbox = Inbox()
    with pytest.raises(ValueError, match="large"):
        enqueue_recovery(inbox, *documents())
    assert not inbox.mutations


def test_cli_rejects_duplicate_keys_before_loading_token(tmp_path, monkeypatch, capsys):
    candidates = tmp_path / "candidates.json"
    candidates.write_text('{"schema_version":1,"schema_version":1}')
    report = tmp_path / "report.json"
    report.write_text("{}")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    assert main(["--candidates", str(candidates), "--report", str(report)]) == 1
    assert json.loads(capsys.readouterr().out) == {"error": "ValueError", "publications": 0}
