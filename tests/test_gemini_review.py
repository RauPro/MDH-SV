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
    from mdh_pipeline.news_dossier import empty_news_dossier, from_legacy_record
    value["news_dossier"] = (from_legacy_record(value) if value["record"] else
                              empty_news_dossier(item, extraction_status="processed"))
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
        self.fail_draft_write = False
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
            return {"truncated": False, "tree": [{"path": file["path"], "type": "blob", "sha": file["sha"]}
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
            if folder == "drafts" and self.fail_draft_write:
                self.fail_draft_write = False
                raise RuntimeError("simulated GitHub interruption before draft persistence")
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
    assert not inbox.files, "Do not persist new drafts after observing a human decision"
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
    failed_draft = inbox.document("drafts", item["id"])
    assert failed_draft["news_dossier"]["extraction_status"] == "error"
    assert all(field["status"] == "pending" for field in failed_draft["news_dossier"]["fields"].values())
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
    lambda state: state.update(privacy_reprocessing_version=2),
    lambda state: state.update(privacy_reprocessing_version=2, privacy_reprocessing_attempts=True),
    lambda state: state.update(privacy_reprocessing_version=2, privacy_reprocessing_attempts=-1),
    lambda state: state.update(privacy_reprocessing_version=1, privacy_reprocessing_attempts=0),
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


def test_validation_diagnostics_preserve_only_known_schema_paths_and_types():
    from mdh_pipeline.gemini_review import safe_validation_issues
    error = RuntimeError(KEY + FULL_BODY)
    error.validation_issues = [
        {"field":"record.3.values", "type":"too_long", "input":FULL_BODY, "message":KEY},
        {"field":KEY, "type":KEY},
    ]
    details = safe_validation_issues(error)
    assert details[0] == {"field":"record.3.values", "type":"too_long"}
    assert details[1] == {"field":"unknown", "type":"validation_error"}
    assert KEY not in json.dumps(details) and FULL_BODY not in json.dumps(details)


def test_legacy_case_migrates_without_fetching_source_or_calling_gemini():
    item = candidate(); inbox = PrivateInbox([item]); workers = Extractors()
    legacy = draft(item); legacy.pop("news_dossier")
    original_record = deepcopy(legacy["record"])
    inbox.store("drafts", item["id"], legacy)
    before_sha = inbox.files[inbox.path("drafts", item["id"])]["sha"]
    report = run(inbox, workers)
    migrated = inbox.document("drafts", item["id"])
    assert report["migrated"] == report["skipped_existing"] == 1
    assert report["attempted"] == 0
    assert not workers.fetch_calls and not workers.model_calls
    assert migrated["record"] == original_record
    assert migrated["news_dossier"]["extraction_status"] == "processed"
    assert migrated["news_dossier"]["human_reviewed"] is False
    assert any(field["status"] == "pending" for field in migrated["news_dossier"]["fields"].values())
    draft_put = next(kwargs["json"] for method, path, kwargs in inbox.calls if method == "PUT" and "/drafts/" in path)
    assert draft_put["sha"] == before_sha
    reads = len(inbox.reads)
    assert run(inbox, workers)["skipped_existing"] == 1
    assert len(inbox.reads) == reads


def test_offline_legacy_migration_is_bounded_per_run_and_resumes_without_ai():
    items = [candidate(number) for number in (1, 2, 3)]
    inbox = PrivateInbox(items); workers = Extractors()
    for item in items:
        legacy = draft(item); legacy.pop("news_dossier")
        inbox.store("drafts", item["id"], legacy)
    for index in range(3):
        report = run(inbox, workers, limit=1)
        assert report["migrated"] == 1
        assert report["offline_candidates"] == 1, "Migrating and repairing one candidate share a slot"
        assert report["attempted"] == 0
        assert report["deferred"] == 2 - index
        migrated = [item for item in items if "news_dossier" in inbox.document("drafts", item["id"])]
        assert len(migrated) == index + 1
        for item in items[index + 1:]:
            issue = inbox.issues[items.index(item) + 1]
            assert "<!-- mdh-news-dossier:" not in issue["body"]
    assert not workers.fetch_calls and not workers.model_calls
    assert run(inbox, workers, limit=1)["migrated"] == 0


def test_legacy_error_skeletons_share_offline_budget_even_during_retry_cooldown():
    items = [candidate(number) for number in (1, 2, 3)]
    inbox = PrivateInbox(items); workers = Extractors()
    for item in items:
        inbox.store("processing", item["id"], processing(item, attempts=3,
                    next_retry_at=(NOW + timedelta(hours=6)).isoformat()))
    for count in (1, 2, 3):
        before_puts = len([path for method, path, _ in inbox.calls if method == "PUT" and "/drafts/" in path])
        report = run(inbox, workers, limit=1)
        after_puts = len([path for method, path, _ in inbox.calls if method == "PUT" and "/drafts/" in path])
        assert after_puts - before_puts == 1
        assert report["offline_candidates"] == report["partial_dossiers"] == 1
        assert report["attempted"] == report["skipped_existing"] == 0
        assert report["deferred"] == 3
        assert sum(inbox.path("drafts", item["id"]) in inbox.files for item in items) == count
        assert all("<!-- mdh-news-dossier:" not in issue["body"] for issue in inbox.issues.values())
    assert not workers.fetch_calls and not workers.model_calls


def test_unmarked_completed_dossier_repairs_share_offline_budget_and_resume():
    items = [candidate(number) for number in (1, 2, 3)]
    inbox = PrivateInbox(items); workers = Extractors()
    for item in items:
        inbox.store("drafts", item["id"], draft(item))
    for count in (1, 2, 3):
        before = len([path for method, path, _ in inbox.calls if method == "PUT" and "/processing/" in path])
        report = run(inbox, workers, limit=1)
        after = len([path for method, path, _ in inbox.calls if method == "PUT" and "/processing/" in path])
        assert after - before == 1
        assert report["offline_candidates"] == 1
        assert report["attempted"] == 0
        assert report["deferred"] == 3 - count
        assert sum("<!-- mdh-news-dossier:v2:" in issue["body"] for issue in inbox.issues.values()) == count
    assert not workers.model_calls and not workers.fetch_calls
    assert not [path for method, path, _ in inbox.calls if method == "PUT" and "/drafts/" in path]


def test_all_runner_writes_are_paced_including_migration_and_marker_repairs():
    from mdh_pipeline.gemini_review import _PacedInbox
    clock = [0.0]
    writes, sleeps = [], []
    class Client:
        def call(self, method, path, **kwargs):
            if method != "GET": writes.append(clock[0])
            return {}
    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds
    paced = _PacedInbox(Client(), sleep, lambda: clock[0])
    for _ in range(85):
        paced.call("GET", "/read")
        paced.call("PUT", "/private/drafts/fixture")
    assert len(writes) == paced.write_calls == 85
    assert all(b - a >= 2 for a, b in zip(writes, writes[1:]))
    assert max(sum(start <= value < start + 60 for value in writes) for start in writes) <= 30
    assert len(sleeps) == 84, "Reads are not unnecessarily delayed"


@pytest.mark.parametrize("legacy_status", ["needs_scope_review", "needs_privacy_review"])
def test_legacy_triage_gets_pending_worksheet_then_new_extraction_not_a_false_complete_marker(legacy_status):
    item = candidate(historical=True, scope="international_context")
    inbox = PrivateInbox([item]); workers = Extractors(status=legacy_status)
    legacy = draft(item, status=legacy_status); legacy.pop("news_dossier")
    inbox.store("drafts", item["id"], legacy)
    inbox.store("processing", item["id"], processing(item, attempts=3, status="completed"))
    report = run(inbox, workers)
    assert report["migrated"] == 1 and report["attempted"] == 1
    assert workers.model_calls == [item["id"]]
    puts = [json.loads(base64.b64decode(kwargs["json"]["content"])) for method, path, kwargs in inbox.calls if method == "PUT" and "/drafts/" in path]
    assert puts[0]["status"] == "pending"
    assert all(field["status"] == "pending" for field in puts[0]["news_dossier"]["fields"].values())
    assert puts[-1]["news_dossier"]["extraction_status"] == "processed"
    assert puts[-1]["record"] is None
    assert inbox.document("processing", item["id"])["attempts"] == 1
    assert inbox.document("processing", item["id"])["dossier_schema_version"] == 1


def test_unavailable_source_has_pending_worksheet_and_is_not_skipped_as_completed():
    item = candidate(); inbox = PrivateInbox([item]); workers = Extractors()
    calls = []
    def failed_fetch(metadata):
        calls.append(metadata)
        error = RuntimeError(FULL_BODY)
        error.code = "source_access_restricted"
        raise error
    report = process_queue(inbox, api_key=KEY, model="gemini-2.5-flash", fetch=failed_fetch,
                           extract=workers.extract, now=NOW, sleep=lambda _: None)
    assert report["errors"] == report["partial_dossiers"] == 1
    document = inbox.document("drafts", item["id"])
    assert document["status"] == document["news_dossier"]["extraction_status"] == "source_unavailable"
    assert all(field["status"] == "pending" for field in document["news_dossier"]["fields"].values())
    assert "<!-- mdh-news-dossier:" not in inbox.issues[1]["body"]
    immediate = run(inbox, workers)
    assert immediate["deferred"] == 1 and immediate["skipped_existing"] == 0
    later = process_queue(inbox, api_key=KEY, model="gemini-2.5-flash", fetch=workers.fetch,
                          extract=workers.extract, now=NOW + timedelta(hours=2), sleep=lambda _: None)
    assert later["attempted"] == 1 and later["skipped_existing"] == 0
    assert inbox.document("drafts", item["id"])["news_dossier"]["extraction_status"] == "processed"
    assert_private_minimal(inbox, later)


def test_fastpath_marker_is_bound_to_exact_blob_and_cannot_hide_a_modified_invalid_worksheet():
    item = candidate(); inbox = PrivateInbox([item]); workers = Extractors()
    run(inbox, workers)
    document = inbox.document("drafts", item["id"])
    document["news_dossier"]["candidate_id"] = "0" * 64
    inbox.store("drafts", item["id"], document)
    with pytest.raises(ValueError, match="invalid_news_dossier"):
        run(inbox, workers)
    assert len(workers.model_calls) == 1


def test_historical_runner_fair_share_reaches_international_and_unknown_news():
    items = [candidate(n, historical=True) for n in range(1, 21)]
    unknown = candidate(30, historical=True, scope="undetermined")
    international = candidate(31, historical=True, scope="international_context")
    inbox = PrivateInbox([*items, unknown, international]); workers = Extractors(status="needs_scope_review")
    report = run(inbox, workers, limit=6)
    assert unknown["id"] in workers.model_calls and international["id"] in workers.model_calls
    assert report["attempted"] == report["news_dossiers"] == 6


def test_error_skeleton_does_not_bypass_attempt_limit_and_is_preserved():
    item = candidate(); inbox = PrivateInbox([item]); workers = Extractors()
    inbox.store("processing", item["id"], processing(item, attempts=3))
    report = run(inbox, workers)
    assert report["attempted"] == 0 and report["deferred"] == 1
    assert not workers.model_calls
    assert inbox.document("drafts", item["id"])["news_dossier"]["extraction_status"] == "source_unavailable"
    assert run(inbox, workers)["skipped_existing"] == 0


def blanket_privacy_draft(item, *, new_policy=False):
    value = draft(item, status="needs_privacy_review")
    value["extraction"]["prompt_version"] = ("mdh-universal-news-grounded-7-field-privacy" if new_policy
                                             else "mdh-universal-news-grounded-6")
    if new_policy:
        value["extraction"]["privacy_policy_version"] = 2
    for field in value["news_dossier"]["fields"].values():
        field.update(value=None, status="withheld", evidence_refs=[],
                     note="Dato omitido por protección de datos sensibles.")
    return value


def store_marked_draft(inbox, number, item, value, *, attempts=3, marker_version=1):
    inbox.store("drafts", item["id"], value)
    state = processing(item, status="completed", attempts=attempts)
    state["dossier_schema_version"] = 1
    inbox.store("processing", item["id"], state)
    sha = inbox.files[inbox.path("drafts", item["id"])]["sha"]
    label = "ai/privacy-review" if value["status"] == "needs_privacy_review" else "ai/ready"
    inbox.issues[number]["labels"].append({"name": label})
    inbox.issues[number]["body"] += ("\n<!-- mdh-ai:start -->\n"
        f"<!-- mdh-news-dossier:v{marker_version}:{sha} -->\n<!-- mdh-ai:end -->\n")


class FieldPrivacyExtractors(Extractors):
    def extract(self, item, article, *, api_key, model):
        value = super().extract(item, article, api_key=api_key, model=model)
        value.update(status="needs_privacy_review", record=None)
        value["extraction"].update(privacy_policy_version=2,
                                  prompt_version="mdh-universal-news-grounded-7-field-privacy")
        return value


def test_blanket_privacy_reprocesses_once_and_preserves_cumulative_attempts():
    item = candidate(); inbox = PrivateInbox([item]); workers = FieldPrivacyExtractors()
    store_marked_draft(inbox, 1, item, blanket_privacy_draft(item), attempts=3)
    report = run(inbox, workers, limit=1)
    assert report["attempted"] == report["privacy_reprocessed"] == report["news_dossiers"] == 1
    assert report["offline_candidates"] == 0
    value = inbox.document("drafts", item["id"])
    assert any(field["status"] == "reported" for field in value["news_dossier"]["fields"].values())
    assert value["human_reviewed"] is False and value["publishable"] is False
    state = inbox.document("processing", item["id"])
    assert state["attempts"] == 4 and state["privacy_reprocessing_attempts"] == 1
    assert state["privacy_reprocessing_version"] == 2
    reads = len(inbox.reads)
    rerun = run(inbox, workers)
    assert rerun["skipped_existing"] == 1 and rerun["attempted"] == 0
    assert len(inbox.reads) == reads and len(workers.model_calls) == 1
    assert_private_minimal(inbox, report)


def test_new_policy_fully_protected_dossier_is_not_reprocessed():
    item = candidate(); inbox = PrivateInbox([item]); workers = FieldPrivacyExtractors()
    store_marked_draft(inbox, 1, item, blanket_privacy_draft(item, new_policy=True))
    report = run(inbox, workers)
    assert report["attempted"] == 0 and report["skipped_existing"] == 1
    assert not workers.model_calls
    assert "mdh-news-dossier:v2:" in inbox.issues[1]["body"]
    reads = len(inbox.reads)
    run(inbox, workers)
    assert len(inbox.reads) == reads


def test_safe_old_v1_marker_is_preserved_without_reads_or_model_work():
    item = candidate(); inbox = PrivateInbox([item]); workers = FieldPrivacyExtractors()
    store_marked_draft(inbox, 1, item, draft(item))
    originals = deepcopy(inbox.files)
    report = run(inbox, workers)
    assert report["skipped_existing"] == 1 and report["attempted"] == 0
    assert not inbox.reads and not workers.model_calls
    assert inbox.files == originals


def test_privacy_reprocessing_errors_keep_backoff_and_exhaust_new_budget_only_once():
    item = candidate(); inbox = PrivateInbox([item]); workers = FieldPrivacyExtractors()
    store_marked_draft(inbox, 1, item, blanket_privacy_draft(item), attempts=3)
    workers.error = RuntimeError(FULL_BODY + KEY)
    for count in range(1, 4):
        report = process_queue(inbox, api_key=KEY, model="gemini-2.5-flash", fetch=workers.fetch,
            extract=workers.extract, now=NOW + timedelta(hours=(count - 1) * 2), sleep=lambda _: None)
        assert report["attempted"] == report["errors"] == 1
        state = inbox.document("processing", item["id"])
        assert state["attempts"] == 3 + count
        assert state["privacy_reprocessing_attempts"] == count
        assert state["privacy_reprocessing_version"] == 2
        assert inbox.document("drafts", item["id"])["extraction"]["privacy_reprocessing_version"] == 2
        assert "mdh-news-dossier:" not in inbox.issues[1]["body"]
        # Repeated cron invocations during the cooldown must not reset its budget.
        immediate = run(inbox, workers)
        assert immediate["attempted"] == 0 and immediate["deferred"] == 1
    final = process_queue(inbox, api_key=KEY, model="gemini-2.5-flash", fetch=workers.fetch,
        extract=workers.extract, now=NOW + timedelta(days=1), sleep=lambda _: None)
    assert final["attempted"] == 0 and final["deferred"] == 1
    assert len(workers.model_calls) == 3
    assert_private_minimal(inbox, final)


def test_privacy_reprocessing_quota_failure_keeps_six_hour_cooldown():
    item = candidate(); inbox = PrivateInbox([item]); workers = FieldPrivacyExtractors()
    store_marked_draft(inbox, 1, item, blanket_privacy_draft(item), attempts=3)
    workers.error = RuntimeError(KEY); workers.error.status_code = 429
    report = run(inbox, workers)
    state = inbox.document("processing", item["id"])
    assert report["stopped_reason"] == "quota_or_rate_limit"
    assert state["attempts"] == 3 and state["privacy_reprocessing_attempts"] == 0
    assert datetime.fromisoformat(state["next_retry_at"]) == NOW + timedelta(hours=6)
    assert run(inbox, workers)["attempted"] == 0
    assert len(workers.model_calls) == 1


@pytest.mark.parametrize("decision", ["review/approved", "review/rejected"])
@pytest.mark.parametrize("during_extraction", [False, True])
def test_privacy_reprocessing_cannot_overwrite_human_decisions(decision, during_extraction):
    item = candidate(); inbox = PrivateInbox([item]); workers = FieldPrivacyExtractors()
    store_marked_draft(inbox, 1, item, blanket_privacy_draft(item))
    originals = deepcopy(inbox.files)
    def decide():
        inbox.issues[1]["labels"].append({"name": decision})
    if during_extraction:
        workers.on_extract = decide
    else:
        decide()
    report = run(inbox, workers)
    assert inbox.files == originals
    assert report["privacy_reprocessed"] == 0
    assert not [method for method, _, _ in inbox.calls if method in ("PUT", "PATCH")]


def test_privacy_reprocessing_and_fresh_news_share_the_normal_batch_budget():
    items = [candidate(number) for number in range(1, 6)]
    inbox = PrivateInbox(items); workers = FieldPrivacyExtractors()
    for number, item in enumerate(items[:4], 1):
        store_marked_draft(inbox, number, item, blanket_privacy_draft(item))
    report = run(inbox, workers, limit=2)
    assert report["attempted"] == 2 and report["privacy_reprocessed"] == 1
    assert report["deferred"] == 3 and report["offline_candidates"] == 0
    assert workers.model_calls == [items[0]["id"], items[4]["id"]]
    assert inbox.document("drafts", items[2]["id"])["extraction"]["prompt_version"] == "mdh-universal-news-grounded-6"


class Budget:
    def __init__(self, remaining=480, ceiling=50, cooldown=None):
        self.remaining = remaining
        self.config = SimpleNamespace(batch_ceiling=ceiling)
        self.cooldown = cooldown
        self.requests_this_run = 0
        self.request_limit = None

    def set_request_limit(self, limit):
        self.request_limit = limit

    def snapshot(self):
        return {"day": "2026-10-09", "used_requests": 480 - self.remaining,
                "remaining_requests": self.remaining, "cooldown_until": self.cooldown}


class BudgetWorkers(Extractors):
    def __init__(self, budget):
        super().__init__()
        self.budget = budget

    def extract(self, item, article, *, api_key, model, budget):
        assert budget is self.budget
        budget.requests_this_run += 1
        value = super().extract(item, article, api_key=api_key, model=model)
        budget.remaining -= 1
        return value


def test_budget_passes_to_each_extractor_and_caps_batch_by_verified_ceiling():
    budget = Budget(remaining=8, ceiling=3); workers = BudgetWorkers(budget)
    inbox = PrivateInbox([candidate(n) for n in range(1, 9)])
    report = run(inbox, workers, limit=50, budget=budget)
    assert report["effective_limit"] == report["news_dossiers"] == report["attempted"] == 3
    assert report["budget"]["remaining_requests"] == 5
    assert report["http_requests"] == budget.request_limit == 3
    assert len(workers.model_calls) == 3


def test_daily_budget_is_distributed_across_remaining_pacific_hours():
    budget = Budget(remaining=480); workers = BudgetWorkers(budget)
    inbox = PrivateInbox([candidate(n) for n in range(1, 51)])
    # 07:00 Pacific daylight time leaves 17 hourly slots until the daily reset.
    now = datetime(2026, 10, 10, 14, 0, tzinfo=timezone.utc)
    report = process_queue(inbox, api_key=KEY, model="gemini-2.5-flash", fetch=workers.fetch,
                           extract=workers.extract, now=now, sleep=lambda _: None, limit=50, budget=budget)
    assert report["remaining_hourly_slots"] == 17
    assert report["effective_limit"] == report["news_dossiers"] == 29
    assert budget.request_limit == report["http_requests"] == 29


def test_explicit_phase_grant_does_not_recalculate_a_second_hourly_share():
    budget = Budget(remaining=10)
    workers = BudgetWorkers(budget)
    inbox = PrivateInbox([candidate(number) for number in range(1, 8)])
    now = datetime(2026, 10, 10, 15, tzinfo=timezone.utc)
    report = process_queue(inbox, api_key=KEY, model='gemini-2.5-flash',
        fetch=workers.fetch, extract=workers.extract, now=now, sleep=lambda _: None,
        limit=5, phase_http_limit=5, budget=budget)
    assert report['effective_limit'] == report['http_requests'] == 5
    assert budget.request_limit == 5


def test_zero_phase_grant_and_elapsed_wall_time_never_call_the_model():
    budget = Budget(remaining=10)
    workers = BudgetWorkers(budget)
    inbox = PrivateInbox([candidate()])
    report = run(inbox, workers, budget=budget, phase_http_limit=0)
    assert report['http_requests'] == 0 and report['quota_reason'] == 'run_budget_exhausted'
    assert not workers.model_calls
    times = iter([0, 721])
    report = run(inbox, workers, budget=budget, run_clock=lambda: next(times))
    assert report['stopped_reason'] == 'run_time_limit' and report['http_requests'] == 0
    assert not workers.model_calls


def test_positive_scope_priority_precedes_the_older_unclassified_queue():
    older, selected = candidate(1, historical=True), candidate(2, historical=True)
    inbox = PrivateInbox([older, selected])
    workers = Extractors()
    report = run(inbox, workers, limit=1, priority_ids=[selected['id']])
    assert report['news_dossiers'] == 1
    assert workers.model_calls == [selected['id']]


def test_positive_backlog_reserves_one_fresh_rss_attempt_without_visiting_it_twice():
    old_unselected = candidate(1, historical=True)
    positives = [candidate(number, historical=True) for number in range(2, 5)]
    fresh = candidate(5)
    inbox = PrivateInbox([old_unselected, *positives, fresh])
    workers = Extractors()
    report = run(inbox, workers, limit=3, priority_ids=[item['id'] for item in positives])
    assert report['attempted'] == report['news_dossiers'] == 3
    assert workers.model_calls[0] == fresh['id']
    assert set(workers.model_calls[1:]) <= {item['id'] for item in positives}
    assert len(set(workers.model_calls)) == 3
    assert old_unselected['id'] not in workers.model_calls


def test_rss_reservation_skips_completed_items_and_deduplicates_positive_rss():
    completed, fresh = candidate(1), candidate(2)
    positives = [candidate(number, historical=True) for number in range(3, 6)]
    inbox = PrivateInbox([completed, fresh, *positives])
    store_marked_draft(inbox, 1, completed, draft(completed))
    workers = Extractors()
    report = run(inbox, workers, limit=3, priority_ids=[fresh['id'], *[item['id'] for item in positives]])
    assert report['attempted'] == report['news_dossiers'] == 3
    assert workers.model_calls[0] == fresh['id']
    assert completed['id'] not in workers.model_calls
    assert workers.model_calls.count(fresh['id']) == 1
    assert set(workers.model_calls[1:]) <= {item['id'] for item in positives}


def test_zero_daily_allowance_does_not_fetch_news_or_change_private_records():
    budget = Budget(remaining=0); workers = BudgetWorkers(budget); inbox = PrivateInbox([candidate()])
    report = run(inbox, workers, budget=budget)
    assert report["attempted"] == report["errors"] == report["effective_limit"] == 0
    assert report["stopped_reason"] == "budget_deferred"
    assert not workers.fetch_calls and not inbox.calls and not inbox.files
    assert budget.request_limit is None and report["http_requests"] == 0


def test_active_provider_cooldown_does_not_scan_or_modify_any_news_state():
    budget = Budget(cooldown=(NOW + timedelta(hours=1)).isoformat())
    workers = BudgetWorkers(budget); inbox = PrivateInbox([candidate()])
    report = run(inbox, workers, budget=budget)
    assert report["stopped_reason"] == "budget_deferred" and report["quota_reason"] == "provider_cooldown"
    assert report["quota_retry_at"] == budget.cooldown
    assert report["attempted"] == report["errors"] == report["http_requests"] == 0
    assert not workers.fetch_calls and not inbox.calls and not inbox.files
    assert budget.request_limit is None


@pytest.mark.parametrize("existing", [False, True])
def test_quota_deferral_preserves_candidate_draft_and_attempt_state(existing):
    from mdh_pipeline.gemini_budget import QuotaDeferred
    item = candidate(); inbox = PrivateInbox([item]); budget = Budget(); workers = BudgetWorkers(budget)
    if existing:
        inbox.store("processing", item["id"], processing(item, attempts=1))
        from mdh_pipeline.gemini_review import skeleton_envelope
        inbox.store("drafts", item["id"], skeleton_envelope(item, "error", error_code="previous_error"))
    original = deepcopy(inbox.files)
    workers.error = QuotaDeferred("minute_budget_exhausted", NOW + timedelta(seconds=60))
    report = run(inbox, workers, budget=budget)
    assert report["stopped_reason"] == "budget_deferred" and report["quota_reason"] == "minute_budget_exhausted"
    assert report["attempted"] == report["errors"] == report["partial_dossiers"] == 0
    assert inbox.files == original
    assert not [method for method, _, _ in inbox.calls if method in ("PUT", "PATCH")]


def test_invalid_budget_state_is_never_converted_to_candidate_error():
    from mdh_pipeline.gemini_budget import QuotaStateError
    item = candidate(); inbox = PrivateInbox([item]); budget = Budget(); workers = BudgetWorkers(budget)
    workers.error = QuotaStateError("budget_cas_failed")
    with pytest.raises(QuotaStateError, match="budget_cas_failed"):
        run(inbox, workers, budget=budget)
    assert not inbox.files


def test_cli_requires_explicit_quota_configuration_before_any_network(monkeypatch, capsys):
    from mdh_pipeline import gemini_review
    monkeypatch.setattr("sys.argv", ["gemini_review"])
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    monkeypatch.delenv("MDH_GEMINI_BUDGET_CONFIG", raising=False)
    monkeypatch.setattr(gemini_review, "GitHubInbox", lambda *args: pytest.fail("must not reach network"))
    with pytest.raises(SystemExit) as error:
        gemini_review.main()
    assert error.value.code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["error_code"] == "gemini_budget_configuration_required" and KEY not in json.dumps(output)


@pytest.mark.parametrize("outcome", ["quota", "duplicate", "all_failed"])
def test_cli_hourly_status_matches_exit_and_claim_precedes_extraction(monkeypatch, capsys, outcome):
    from mdh_pipeline import gemini_review, gemini_budget, hourly_slot
    already_claimed = outcome == "duplicate"
    monkeypatch.setattr("sys.argv", ["gemini_review", "--limit", "50"])
    for name, value in {"GEMINI_API_KEY": KEY, "MDH_GEMINI_BUDGET_CONFIG": "{}", "GEMINI_MODEL": "gemini-2.5-flash",
                        "GITHUB_REPOSITORY": "RauPro/MDH-SV-review", "GH_TOKEN": "test", "GITHUB_RUN_ID": "123",
                        "GITHUB_EVENT_NAME": "workflow_dispatch", "MDH_TRIGGER_SOURCE": "vercel_cron",
                        "MDH_SCHEDULED_HOUR": "2026-10-10T06:00:00Z"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    config = SimpleNamespace(model="gemini-2.5-flash", batch_ceiling=30)
    budget = Budget(remaining=0)
    monkeypatch.setattr(gemini_budget, "BudgetConfig", lambda **kwargs: config)
    monkeypatch.setattr(gemini_budget, "GeminiBudget", lambda inbox, passed: budget)
    monkeypatch.setattr(gemini_review, "GitHubInbox", lambda *args: PrivateInbox())
    events = []
    class Slots:
        def __init__(self, inbox):
            pass
        def claim(self, **kwargs):
            events.append("claim")
            assert kwargs == {"run_id": "123", "trigger_source": "vercel_cron", "requested_hour": "2026-10-10T06:00:00Z"}
            return None if already_claimed else {"hour": "2026-10-10T06:00:00Z"}
        def finish(self, claim, **kwargs):
            events.append("finish")
            assert kwargs["status"] == ("failed" if outcome == "all_failed" else "deferred")
            assert kwargs["counts"]["errors"] == (2 if outcome == "all_failed" else 0)
    monkeypatch.setattr(hourly_slot, "HourlySlots", Slots)
    if already_claimed:
        monkeypatch.setattr(gemini_review, "process_queue", lambda *args, **kwargs: pytest.fail("duplicate hour must not process"))
    if outcome == "all_failed":
        monkeypatch.setattr(gemini_review, "process_queue", lambda *args, **kwargs: {
            "attempted": 2, "errors": 2, "news_dossiers": 0, "migrated": 0, "http_requests": 0,
            "stopped_reason": None, "publications": 0})
        with pytest.raises(SystemExit) as error:
            gemini_review.main()
        assert error.value.code == 1
    else:
        gemini_review.main()
    output = json.loads(capsys.readouterr().out)
    assert output["attempted"] == (2 if outcome == "all_failed" else 0)
    assert events == (["claim"] if already_claimed else ["claim", "finish"])
    assert output.get("status") == ("hour_already_claimed" if already_claimed else None)


def test_privacy_reprocessing_does_not_accept_an_old_policy_model_result():
    item = candidate(); inbox = PrivateInbox([item]); workers = Extractors()
    store_marked_draft(inbox, 1, item, blanket_privacy_draft(item))
    report = run(inbox, workers)
    assert report["errors"] == 1 and report["privacy_reprocessed"] == 0
    assert inbox.document("processing", item["id"])["privacy_reprocessing_attempts"] == 1
    assert "mdh-news-dossier:" not in inbox.issues[1]["body"]


def test_privacy_failure_draft_write_interruption_does_not_reset_persisted_retry_budget():
    item = candidate(); inbox = PrivateInbox([item]); workers = FieldPrivacyExtractors()
    store_marked_draft(inbox, 1, item, blanket_privacy_draft(item), attempts=3)
    inbox.fail_draft_write = True
    workers.error = RuntimeError(KEY)
    with pytest.raises(RuntimeError, match="before draft persistence"):
        run(inbox, workers)
    # The prior draft and old marker can still exist, but the failure state now
    # guarantees that neither the new-policy allowance nor its cooldown resets.
    state = inbox.document("processing", item["id"])
    assert state["privacy_reprocessing_attempts"] == 1 and state["attempts"] == 4
    assert inbox.document("drafts", item["id"])["extraction"]["prompt_version"] == "mdh-universal-news-grounded-6"
    report = run(inbox, workers)
    assert report["attempted"] == 0 and report["deferred"] == 1
    assert len(workers.model_calls) == 1


@pytest.mark.parametrize("limit,privacy_quota,rss_quota", [(10, 2, 2), (20, 5, 5)])
def test_privacy_reservation_keeps_rss_and_historical_shares_without_duplicate_ai(limit, privacy_quota, rss_quota):
    protected = [candidate(number, historical=True, published_date="2026-01-01") for number in range(1, 9)]
    older = [candidate(number, historical=True, published_date="2014-01-01") for number in range(20, 40)]
    fresh = [candidate(number) for number in range(50, 60)]
    inbox = PrivateInbox([*protected, *older, *fresh]); workers = FieldPrivacyExtractors()
    for number, item in enumerate(protected, 1):
        store_marked_draft(inbox, number, item, blanket_privacy_draft(item))
    report = run(inbox, workers, limit=limit)
    assert report["attempted"] == limit and report["privacy_reprocessed"] == privacy_quota
    assert len(workers.model_calls) == len(set(workers.model_calls)) == limit
    assert set(workers.model_calls[:privacy_quota]).issubset({item["id"] for item in protected})
    assert workers.model_calls[privacy_quota:privacy_quota + rss_quota] == [item["id"] for item in fresh[:rss_quota]]
    assert len(set(workers.model_calls).intersection(item["id"] for item in older)) == limit - privacy_quota - rss_quota


def test_privacy_budget_exhaustion_does_not_consume_fresh_news_reservation():
    protected, fresh = candidate(1), candidate(2)
    inbox = PrivateInbox([protected, fresh]); workers = FieldPrivacyExtractors()
    store_marked_draft(inbox, 1, protected, blanket_privacy_draft(protected))
    state = processing(protected, status="error", attempts=6)
    state.update(dossier_schema_version=1, privacy_reprocessing_version=2, privacy_reprocessing_attempts=3)
    inbox.store("processing", protected["id"], state)
    report = run(inbox, workers, limit=1)
    assert report["attempted"] == 1 and report["deferred"] == 1
    assert report["privacy_reprocessed"] == 0
    assert workers.model_calls == [fresh["id"]]


def test_privacy_retry_retains_migration_share_after_error_badge_replaces_privacy_badge():
    protected = candidate(1, historical=True, published_date="2026-01-01")
    older = candidate(2, historical=True, published_date="2014-01-01")
    inbox = PrivateInbox([protected, older]); workers = FieldPrivacyExtractors()
    store_marked_draft(inbox, 1, protected, blanket_privacy_draft(protected))
    workers.error = RuntimeError(KEY)
    report = run(inbox, workers, limit=1)
    assert report["attempted"] == report["errors"] == 1
    assert "ai/error" in {label["name"] for label in inbox.issues[1]["labels"]}
    assert "mdh-field-privacy-retry:v2" in inbox.issues[1]["body"]
    workers.error = None
    later = process_queue(inbox, api_key=KEY, model="gemini-2.5-flash", fetch=workers.fetch,
        extract=workers.extract, limit=1, now=NOW + timedelta(hours=2), sleep=lambda _: None)
    assert later["privacy_reprocessed"] == later["attempted"] == 1
    assert workers.model_calls == [protected["id"], protected["id"]]
    assert "mdh-field-privacy-retry:v2" not in inbox.issues[1]["body"]
