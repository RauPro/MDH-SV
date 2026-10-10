"""Private runner contract tests: in-memory GitHub and injected fetch/model calls only."""
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import traceback
from types import SimpleNamespace

import pytest

from mdh_pipeline.gemini_review import AI_LABELS, MAX_FILE_BYTES, process_queue, read_private_file
from mdh_pipeline.publication import load_catalog
from mdh_pipeline.review_queue import issue_body

NOW = datetime(2026, 10, 10, 6, 0, tzinfo=timezone.utc)
KEY = "test-gemini-key-never-persist"
FULL_BODY = "FULL_ARTICLE_SENTINEL: this in-memory body must never reach GitHub storage or logs."


def candidate(number=1, *, historical=False, published_date="2026-10-09", scope="el_salvador"):
    url = f"https://gatoencerrado.news/2026/10/09/noticia-{number}/"
    value = {"id": hashlib.sha256(url.encode()).hexdigest(), "source_url": url,
             "source_title": f"Reporte público de detención {number}", "publisher": "GatoEncerrado",
             "published_date": published_date, "discovered_at": NOW.isoformat(),
             "matched_terms": ["detencion"], "status": "pending", "record_type": "news_candidate"}
    if historical:
        value.update(discovery_method="historical_bibliography", scope_hint=scope, scope_terms=["el salvador"])
    return value


def draft(item, *, status="draft"):
    value = {"schema_version": 1, "candidate_id": item["id"], "source_url": item["source_url"],
             "status": status, "publishable": False, "human_reviewed": False,
             "relevant": status == "draft",
             "classification": {"jurisdiction": "SV", "relevance": "relevant", "record_type": "incident"},
             "record": None, "grounding": {},
             "extraction": {"provider": "gemini", "model": "gemini-2.5-flash", "content_sha256": "a" * 64}}
    if status == "draft":
        record = load_catalog().records[0].model_dump(mode="json")
        record.update(source_url=item["source_url"], source_title=item["source_title"],
                      publisher=item["publisher"], human_reviewed=False, review_status="source_checked",
                      reviewed_by=None, reviewed_at=None, review_notes=None, origin_candidate_id=item["id"])
        record["dossier"]["evidence"][0]["url"] = item["source_url"]
        value["record"] = record
        value["classification"]["record_type"] = record["record_type"]
    return value


def processing(item, *, attempts=1, next_retry_at=None, status="error"):
    return {"candidate_id": item["id"], "source_url": item["source_url"], "attempts": attempts,
            "status": status, "updated_at": NOW.isoformat(), "next_retry_at": next_retry_at,
            "error_code": "source_unavailable", "model": "gemini-2.5-flash"}


class PrivateInbox:
    repository = "RauPro/MDH-SV-review"
    base = "/repos/RauPro/MDH-SV-review"

    def __init__(self, candidates=()):
        self.issues = {}
        self.files = {}
        self.calls = []
        self.reads = []
        self.session = SimpleNamespace(headers={"Authorization": "Bearer github-token-never-forward"})
        self.issue_read_hook = None
        self.fail_processing_write = False
        self.tree_response = None
        for number, item in enumerate(candidates, 1):
            self.issues[number] = {"number": number, "state": "open", "body": issue_body(item),
                                   "created_at": (NOW + timedelta(seconds=number)).isoformat(),
                                   "labels": [{"name": "review/pending"}]}

    def path(self, folder, identifier):
        return self.base + f"/contents/{folder}/{identifier}.json"

    def store(self, folder, identifier, value):
        raw = json.dumps(value, ensure_ascii=False).encode()
        self.files[self.path(folder, identifier)] = {
            "type": "file", "path": f"{folder}/{identifier}.json", "encoding": "base64",
            "size": len(raw), "content": base64.b64encode(raw).decode(),
            "sha": hashlib.sha256(raw).hexdigest(),
        }

    def document(self, folder, identifier):
        return json.loads(base64.b64decode(self.files[self.path(folder, identifier)]["content"]))

    def get_file(self, path):
        assert path.startswith(self.base + "/contents/")
        self.reads.append(path)
        return deepcopy(self.files.get(path))

    def call(self, method, path, **kwargs):
        assert path.startswith(self.base + "/"), "No requests to the public repository are allowed"
        self.calls.append((method, path, deepcopy(kwargs)))
        if method == "GET" and path.endswith("/labels"):
            return [{"name": label} for label in AI_LABELS]
        if method == "GET" and path.endswith("/git/trees/main"):
            if self.tree_response is not None:
                return deepcopy(self.tree_response)
            return {"truncated": False, "tree": [{"path": file["path"], "type": "blob"}
                for file in self.files.values() if isinstance(file, dict)]}
        if method == "GET" and path.endswith("/issues"):
            page = kwargs["params"]["page"]
            return deepcopy(list(self.issues.values())[(page - 1) * 100:page * 100])
        if "/issues/" in path:
            number = int(path.rsplit("/", 1)[1])
            if method == "GET":
                if self.issue_read_hook:
                    self.issue_read_hook(number)
                return deepcopy(self.issues[number])
            if method == "PATCH":
                payload = deepcopy(kwargs["json"])
                if "labels" in payload:
                    payload["labels"] = [{"name": label} for label in payload["labels"]]
                self.issues[number].update(payload)
                return deepcopy(self.issues[number])
        if method == "PUT" and "/contents/" in path:
            folder, filename = path.split("/contents/", 1)[1].split("/")
            assert folder in ("drafts", "processing"), "AI cannot write approved or public records"
            if folder == "processing" and self.fail_processing_write:
                self.fail_processing_write = False
                raise RuntimeError("simulated GitHub interruption after draft persistence")
            previous = self.files.get(path)
            if previous:
                assert kwargs["json"].get("sha") == previous["sha"], "Update must be revision guarded"
            self.store(folder, filename[:-5], json.loads(base64.b64decode(kwargs["json"]["content"])))
            return {"content": {"sha": self.files[path]["sha"]}}
        raise AssertionError(f"Unexpected operation {method} {path}")


class Extractors:
    def __init__(self, status="draft"):
        self.fetch_calls = []
        self.model_calls = []
        self.status = status
        self.error = None
        self.on_extract = None

    def fetch(self, metadata):
        self.fetch_calls.append(deepcopy(metadata))
        assert "session" not in metadata and "Authorization" not in metadata and "api_key" not in metadata
        return {"source_url": metadata["source_url"], "text": FULL_BODY,
                "paragraphs": [{"id": "p1", "text": FULL_BODY}], "content_sha256": "a" * 64}

    def extract(self, item, article, *, api_key, model):
        assert api_key == KEY and model == "gemini-2.5-flash"
        assert article["text"] == FULL_BODY
        self.model_calls.append(item["id"])
        if self.on_extract:
            self.on_extract()
        if self.error:
            raise self.error
        return draft(item, status=self.status)


def run(inbox, workers, **options):
    return process_queue(inbox, api_key=KEY, model="gemini-2.5-flash", fetch=workers.fetch,
                         extract=workers.extract, now=NOW, sleep=lambda _: None, **options)


def assert_private_minimal(inbox, report):
    serialized = json.dumps([inbox.files, inbox.issues, report])
    assert FULL_BODY not in serialized and KEY not in serialized and "github-token-never-forward" not in serialized
    for path in inbox.files:
        assert path.startswith(inbox.base + "/contents/drafts/") or path.startswith(inbox.base + "/contents/processing/")
    # Encoded files must also exclude the in-memory article and credentials.
    for file in inbox.files.values():
        content = base64.b64decode(file["content"]).decode()
        assert FULL_BODY not in content and KEY not in content
    assert report["publications"] == 0


def test_one_extraction_is_persisted_and_rerun_reuses_private_draft_without_ai():
    item = candidate(); inbox = PrivateInbox([item]); workers = Extractors()
    report = run(inbox, workers)
    assert report["attempted"] == report["drafts"] == 1
    assert workers.model_calls == [item["id"]]
    stored = inbox.document("drafts", item["id"])
    assert stored["publishable"] is False and stored["human_reviewed"] is False
    assert stored["record"]["origin_candidate_id"] == item["id"]
    assert inbox.document("processing", item["id"])["status"] == "completed"
    file_reads = len(inbox.reads)
    rerun = run(inbox, workers)
    assert rerun["attempted"] == 0 and rerun["skipped_existing"] == 1
    assert len(workers.model_calls) == len(workers.fetch_calls) == 1
    assert len(inbox.reads) == file_reads, "Completed indexed drafts must not be reread every scheduled run"
    assert inbox.issues[1]["body"].count("<!-- mdh-ai:start -->") == 1
    assert "review/pending" in {label["name"] for label in inbox.issues[1]["labels"]}
    assert_private_minimal(inbox, report)


def test_persisted_draft_survives_interruption_before_status_without_repeating_model_cost():
    item = candidate(); inbox = PrivateInbox([item]); workers = Extractors()
    inbox.fail_processing_write = True
    with pytest.raises(RuntimeError, match="interruption"):
        run(inbox, workers)
    assert inbox.document("drafts", item["id"])["status"] == "draft"
    report = run(inbox, workers)
    assert report["skipped_existing"] == 1 and report["attempted"] == 0
    assert workers.model_calls == [item["id"]]


@pytest.mark.parametrize("decision", ["review/approved", "review/rejected"])
def test_already_decided_candidates_are_not_read_or_overwritten(decision):
    inbox = PrivateInbox([candidate()]); workers = Extractors()
    inbox.issues[1]["labels"].append({"name": decision})
    report = run(inbox, workers)
    assert report["pending"] == report["attempted"] == 0
    assert not workers.fetch_calls and not inbox.reads and not inbox.files
    assert not any(method == "PATCH" for method, _, _ in inbox.calls)


@pytest.mark.parametrize("decision", ["review/approved", "review/rejected"])
def test_human_decision_while_model_runs_is_never_reversed(decision):
    inbox = PrivateInbox([candidate()]); workers = Extractors()
    workers.on_extract = lambda: inbox.issues[1].update(labels=[{"name": decision}], state="closed")
    report = run(inbox, workers)
    assert inbox.issues[1]["labels"] == [{"name": decision}]
    assert not any(method == "PATCH" for method, _, _ in inbox.calls)
    assert_private_minimal(inbox, report)


def test_article_fetch_and_model_receive_no_github_authenticated_client_or_arbitrary_issue_fields():
    item = candidate(); item["secret_instruction"] = "Do not forward arbitrary issue data"
    inbox = PrivateInbox([item]); workers = Extractors()
    archive = {"records": [{"id": item["id"], "source_url": item["source_url"], "discovery": {"source_post_id": 42}}]}
    report = run(inbox, workers, archive=archive)
    assert "secret_instruction" not in workers.fetch_calls[0]
    assert workers.fetch_calls[0]["discovery"] == {"source_post_id": 42}
    assert_private_minimal(inbox, report)


@pytest.mark.parametrize("status,counter", [("needs_scope_review", "scope_review"), ("needs_privacy_review", "privacy_review")])
def test_scope_and_privacy_findings_are_private_review_work_without_case_records(status, counter):
    item = candidate(); inbox = PrivateInbox([item]); workers = Extractors(status=status)
    report = run(inbox, workers)
    assert report[counter] == 1 and report["drafts"] == 0
    assert inbox.document("drafts", item["id"])["record"] is None
    assert "review/pending" in {label["name"] for label in inbox.issues[1]["labels"]}
    assert_private_minimal(inbox, report)


def test_attempt_budget_and_retry_timestamp_defer_work_until_allowed():
    items = [candidate(1), candidate(2), candidate(3)]
    inbox = PrivateInbox(items); workers = Extractors()
    inbox.store("processing", items[0]["id"], processing(items[0], attempts=3))
    inbox.store("processing", items[1]["id"], processing(items[1], next_retry_at=(NOW + timedelta(hours=1)).isoformat()))
    report = run(inbox, workers, limit=1)
    assert workers.model_calls == [items[2]["id"]]
    assert report["deferred"] == 2 and report["attempted"] == 1


def test_explicit_retry_failed_reopens_a_bounded_attempt_without_removing_audit_count():
    item = candidate(); inbox = PrivateInbox([item]); workers = Extractors()
    inbox.store("processing", item["id"], processing(item, attempts=3, next_retry_at=(NOW + timedelta(hours=6)).isoformat()))
    report = run(inbox, workers, retry_failed=True, limit=1)
    assert report["attempted"] == 1
    assert inbox.document("processing", item["id"])["attempts"] == 4


def test_ordinary_failure_records_safe_code_and_backoff_without_body_or_error_message():
    item = candidate(); inbox = PrivateInbox([item]); workers = Extractors()
    workers.error = RuntimeError(KEY + FULL_BODY)
    report = run(inbox, workers)
    state = inbox.document("processing", item["id"])
    assert state["attempts"] == 1 and state["error_code"] == "extraction_failed"
    assert datetime.fromisoformat(state["next_retry_at"]) == NOW + timedelta(hours=1)
    assert report["errors"] == 1 and report["stopped_reason"] is None
    assert inbox.path("drafts", item["id"]) not in inbox.files
    assert_private_minimal(inbox, report)


@pytest.mark.parametrize("status,reason", [(429, "quota_or_rate_limit"), (401, "provider_authentication"), (403, "provider_authentication")])
def test_quota_or_authentication_failure_stops_the_entire_remaining_batch(status, reason):
    items = [candidate(1), candidate(2)]
    inbox = PrivateInbox(items); workers = Extractors()
    workers.error = RuntimeError(KEY + FULL_BODY); workers.error.status_code = status
    report = run(inbox, workers)
    assert report["attempted"] == report["errors"] == 1
    assert report["stopped_reason"] == reason
    assert workers.model_calls == [items[0]["id"]]
    state = inbox.document("processing", items[0]["id"])
    assert state["attempts"] == 0
    assert datetime.fromisoformat(state["next_retry_at"]) == NOW + timedelta(hours=6)
    assert_private_minimal(inbox, report)


def test_completed_first_five_rss_do_not_starve_the_next_rss_behind_historical_backlog():
    rss = [candidate(number) for number in range(1, 7)]
    old = [candidate(20, historical=True, published_date="2025-04-01"), candidate(21, historical=True, published_date="2025-04-02")]
    inbox = PrivateInbox([*rss, *old]); workers = Extractors()
    for item in rss[:5]:
        inbox.store("drafts", item["id"], draft(item))
    report = run(inbox, workers, limit=2)
    assert workers.model_calls == [rss[5]["id"], old[0]["id"]]
    assert report["attempted"] == 2


@pytest.mark.parametrize("change", [
    lambda state: state.update(candidate_id="0" * 64),
    lambda state: state.update(source_url="https://gatoencerrado.news/another/"),
    lambda state: state.update(status="approved"),
    lambda state: state.update(attempts=True),
    lambda state: state.update(attempts=-1),
    lambda state: state.update(next_retry_at="not-a-date"),
    lambda state: state.update(next_retry_at="2026-10-11T00:00:00"),
])
def test_mismatched_or_malformed_processing_state_is_rejected_before_ai(change):
    item = candidate(); inbox = PrivateInbox([item]); workers = Extractors()
    state = processing(item); change(state); inbox.store("processing", item["id"], state)
    with pytest.raises(ValueError):
        run(inbox, workers)
    assert not workers.fetch_calls and not workers.model_calls
    assert not any(method in ("PUT", "PATCH") for method, _, _ in inbox.calls)


def test_issue_identity_changed_after_listing_is_deferred_before_article_fetch():
    first, replacement = candidate(1), candidate(2)
    inbox = PrivateInbox([first]); workers = Extractors()
    inbox.issue_read_hook = lambda _: inbox.issues[1].update(body=issue_body(replacement))
    report = run(inbox, workers)
    assert report["deferred"] == 1 and report["attempted"] == 0
    assert not workers.fetch_calls and not workers.model_calls and not inbox.files


@pytest.mark.parametrize("change", [
    lambda file: {**file, "path": "drafts/another.json"},
    lambda file: {**file, "type": "dir"},
    lambda file: {**file, "encoding": "none", "download_url": "https://not-allowed.example/data"},
    lambda file: {**file, "size": MAX_FILE_BYTES + 1},
    lambda file: {**file, "size": 1},
    lambda file: {**file, "content": "invalid-base64$$"},
    lambda file: [],
])
def test_private_file_responses_cannot_redirect_or_spoof_contents_metadata(change):
    item = candidate(); inbox = PrivateInbox([item])
    inbox.store("drafts", item["id"], draft(item))
    path = inbox.path("drafts", item["id"]); inbox.files[path] = change(inbox.files[path])
    with pytest.raises(ValueError):
        read_private_file(inbox, "drafts", item["id"])
    assert not any(method in ("PUT", "PATCH") for method, _, _ in inbox.calls)


def test_invalid_candidate_json_is_reported_without_crashing_the_entire_batch():
    inbox = PrivateInbox([candidate()]); workers = Extractors()
    inbox.issues[1]["body"] = "<!-- mdh-candidate:" + "a" * 64 + " -->\n```json\n[]\n```"
    report = run(inbox, workers)
    assert report["invalid_metadata"] == 1
    assert not workers.fetch_calls and not inbox.files


def test_missing_configuration_stops_before_reading_or_mutating_private_queue():
    inbox = PrivateInbox([candidate()]); workers = Extractors()
    with pytest.raises(ValueError):
        process_queue(inbox, api_key="", model="gemini-2.5-flash", fetch=workers.fetch, extract=workers.extract)
    assert not inbox.calls and not inbox.reads


def test_truncated_private_draft_index_fails_before_any_paid_attempt():
    inbox = PrivateInbox([candidate()]); workers = Extractors()
    inbox.tree_response = {"tree": [], "truncated": True}
    with pytest.raises(ValueError, match="incomplete_private_draft_index"):
        run(inbox, workers)
    assert not workers.fetch_calls and not inbox.files


def test_existing_scope_or_privacy_badge_and_private_file_never_trigger_new_ai_work():
    item = candidate(); inbox = PrivateInbox([item]); workers = Extractors(status="needs_scope_review")
    run(inbox, workers)
    first_reads = len(inbox.reads)
    second = run(inbox, workers)
    assert second["skipped_existing"] == 1 and second["attempted"] == 0
    assert len(workers.model_calls) == 1 and len(inbox.reads) == first_reads


def test_duplicate_candidate_issues_share_one_extraction_and_do_not_overwrite_draft():
    item = candidate(); inbox = PrivateInbox([item, item]); workers = Extractors()
    report = run(inbox, workers)
    assert report["attempted"] == 1 and report["skipped_existing"] == 1
    assert workers.model_calls == [item["id"]]
    draft_puts = [path for method, path, _ in inbox.calls if method == "PUT" and "/drafts/" in path]
    assert len(draft_puts) == 1


def test_existing_invalid_draft_field_never_appears_in_validation_traceback():
    item = candidate(); inbox = PrivateInbox([item]); workers = Extractors()
    private_marker = "PRIVATE_DOSSIER_VALUE_MUST_NOT_APPEAR_IN_STDERR"
    envelope = draft(item)
    envelope["record"]["title"] = {"private_value": private_marker}
    inbox.store("drafts", item["id"], envelope)
    with pytest.raises(ValueError, match="^invalid_unreviewed_record_fields$") as failure:
        run(inbox, workers)
    formatted = "".join(traceback.format_exception(failure.value))
    assert private_marker not in formatted
    assert "ValidationError" not in formatted
    assert failure.value.__suppress_context__ is True
    assert not workers.fetch_calls and not workers.model_calls
    assert not any(method in ("PUT", "PATCH") for method, _, _ in inbox.calls)


def test_boolean_envelope_schema_version_is_not_accepted_as_integer_one():
    item = candidate(); inbox = PrivateInbox([item]); workers = Extractors()
    envelope = draft(item); envelope["schema_version"] = True
    inbox.store("drafts", item["id"], envelope)
    with pytest.raises(ValueError, match="invalid_draft_envelope"):
        run(inbox, workers)
    assert not workers.fetch_calls and not workers.model_calls
    assert not any(method in ("PUT", "PATCH") for method, _, _ in inbox.calls)


@pytest.mark.parametrize("http_status", [400, 404])
def test_provider_model_configuration_error_stops_batch_and_defers_without_spending_attempts(http_status):
    items = [candidate(1), candidate(2)]
    inbox = PrivateInbox(items); workers = Extractors()
    inbox.store("processing", items[0]["id"], processing(items[0], attempts=1))
    workers.error = RuntimeError(KEY + FULL_BODY)
    workers.error.status_code = http_status
    workers.error.code = "provider_http_error"
    report = run(inbox, workers)
    assert report["stopped_reason"] == "provider_configuration"
    assert report["attempted"] == report["errors"] == 1
    assert workers.model_calls == [items[0]["id"]]
    state = inbox.document("processing", items[0]["id"])
    assert state["attempts"] == 1 and state["error_code"] == "provider_configuration"
    assert datetime.fromisoformat(state["next_retry_at"]) == NOW + timedelta(hours=6)
    assert_private_minimal(inbox, report)


def test_provider_outage_stops_work_without_exposing_exception_details():
    from mdh_pipeline.gemini_review import error_category
    error = RuntimeError("Sensitive provider payload must not be logged")
    error.code = "provider_http_error"
    error.status_code = 503
    assert error_category(error) == ("provider_unavailable", True)
    error.code = "provider_unavailable"
    error.status_code = None
    assert error_category(error) == ("provider_unavailable", True)


def test_cli_global_failures_do_not_print_private_exception_context(monkeypatch, capsys):
    import mdh_pipeline.gemini_review as module
    monkeypatch.setenv("GEMINI_API_KEY", "test-private-key")
    monkeypatch.setenv("GITHUB_REPOSITORY", "RauPro/MDH-SV-review")
    monkeypatch.setenv("GH_TOKEN", "test-private-token")
    monkeypatch.setattr("sys.argv", ["gemini_review"])
    def failed(*args, **kwargs):
        raise RuntimeError("Sensitive source text and test-private-key")
    monkeypatch.setattr(module, "GitHubInbox", failed)
    with pytest.raises(SystemExit):
        module.main()
    output = capsys.readouterr()
    assert "Sensitive" not in output.out + output.err
    assert "test-private" not in output.out + output.err
    assert json.loads(output.out)["publications"] == 0
