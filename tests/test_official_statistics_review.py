import base64
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mdh_pipeline.official_statistics_review import (
    ROOT, enqueue_report, export_source_state, review_revision, revision_id,
    validate_candidate_schema, validate_report, validate_source_state,
)

NOW = datetime(2026, 10, 13, 5, tzinfo=timezone.utc)
BASELINE = b'{"baseline": 1}\n'
PROPOSAL = b'{"schema_version":1,"dataset_id":"sv-official-homicides","periods":[],"sources":[]}\n'
BASE_SHA = hashlib.sha256(BASELINE).hexdigest()
PROPOSAL_SHA = hashlib.sha256(PROPOSAL).hexdigest()
SOURCE_URL = "https://www.fiscalia.gob.sv/estadisticas-de-homicidios/"


def report(*, changed=True, error=False, proposal=True):
    sources = [{"source_id": "fgr-homicides", "url": SOURCE_URL, "format": "html",
                "status": "changed" if changed else "unchanged", "previous_sha256": "a" * 64,
                "content_sha256": "b" * 64, "error_code": None, "manual_review": changed}]
    errors = []
    if error:
        sources.append({"source_id": "iml-homicides", "url": "https://transparencia.oj.gob.sv/es/documentos/248",
                        "format": "html", "status": "error", "previous_sha256": None,
                        "content_sha256": None, "error_code": "source_http_403", "manual_review": True})
        errors.append({"source_id": "iml-homicides", "code": "source_http_403"})
    return {"schema_version": 1, "checked_at": NOW.isoformat(), "as_of": "2026-10-12",
            "baseline_sha256": BASE_SHA, "status": "error" if error else "changed" if changed else "unchanged",
            "review_required": changed or error, "proposal_file": "proposed-homicides.json" if changed and proposal else None,
            "proposal_sha256": PROPOSAL_SHA if changed and proposal else None, "sources": sources,
            "changes": [{"id": "c" * 64, "kind": "fgr_values", "source_id": "fgr-homicides",
                         "source_url": SOURCE_URL, "content_sha256": "b" * 64,
                         "details": {"year": 2026, "before": 49, "after": 52}}] if changed else [], "errors": errors}


def state():
    return {"schema_version": 1, "catalogs": {"iml-homicides": [
        "https://transparencia.oj.gob.sv/es/descargar/3/20791/HOMICIDIOS/11-01-2023"]}}


class Inbox:
    base = "/repos/RauPro/MDH-SV-review"
    repository = "RauPro/MDH-SV-review"

    def __init__(self):
        self.files = {}
        self.issues = []
        self.calls = []
        self.permission = "admin"
        self.fail_proposal_once = False
        self.uncertain_issue_once = False

    def get_file(self, path):
        self.calls.append(("GET_FILE", path, {}))
        relative = path.split("/contents/", 1)[1]
        if relative not in self.files:
            return None
        raw = self.files[relative]
        return {"type": "file", "path": relative, "encoding": "base64", "size": len(raw),
                "content": base64.b64encode(raw).decode(), "sha": hashlib.sha256(raw).hexdigest()[:40]}

    def call(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        payload = kwargs.get("json", {})
        if path.endswith("/permission"):
            return {"permission": self.permission}
        if method == "GET" and path.endswith("/labels"):
            return [{"name": "statistics/source-update"}, {"name": "statistics/pending"},
                    {"name": "statistics/approved"}, {"name": "statistics/rejected"}]
        if method == "GET" and path.endswith("/issues"):
            return self.issues
        if method == "GET" and "/issues/" in path:
            return self.issues[int(path.rsplit("/", 1)[1]) - 1]
        if method == "POST" and path.endswith("/issues"):
            issue = {**payload, "number": len(self.issues) + 1, "state": "open"}
            self.issues.append(issue)
            if self.uncertain_issue_once:
                self.uncertain_issue_once = False
                raise RuntimeError("uncertain delivery")
            return issue
        if method == "PATCH" and "/issues/" in path:
            self.issues[int(path.rsplit("/", 1)[1]) - 1].update(payload)
            return self.issues[int(path.rsplit("/", 1)[1]) - 1]
        if method == "PUT" and "/contents/" in path:
            relative = path.split("/contents/", 1)[1]
            if relative.endswith(".proposal.json") and self.fail_proposal_once:
                self.fail_proposal_once = False
                raise RuntimeError("interrupted write")
            raw = base64.b64decode(payload["content"])
            if relative in self.files:
                assert payload["sha"] == hashlib.sha256(self.files[relative]).hexdigest()[:40]
            self.files[relative] = raw
            return {"content": {"sha": hashlib.sha256(raw).hexdigest()[:40]}}
        raise AssertionError((method, path, kwargs))

    @property
    def mutations(self):
        return [entry for entry in self.calls if entry[0] in {"PUT", "POST", "PATCH"}]


def event(revision, *, decision="approve"):
    return {"sender": {"type": "User", "login": "RauPro"}, "inputs": {
        "issue_number": "1", "revision": revision, "decision": decision,
        "confirm_source": True, "review_notes": "Valores, cobertura y fuentes contrastados manualmente."}}


def prepare(inbox=None, **options):
    inbox = inbox or Inbox()
    document = report(**options)
    result = enqueue_report(inbox, document, proposal=PROPOSAL if document["proposal_file"] else None,
                            source_state=state())
    return inbox, document, result


def test_unchanged_sources_update_private_check_state_without_issues():
    inbox = Inbox()
    result = enqueue_report(inbox, report(changed=False), source_state=state())
    assert result["status"] == "unchanged" and result["issue"] is None
    assert result["publications"] == 0 and not inbox.issues
    assert set(inbox.files) == {f"{ROOT}/state/latest.json", f"{ROOT}/state/sources.json"}
    assert export_source_state(inbox) == state()
    count = len(inbox.mutations)
    enqueue_report(inbox, report(changed=False), source_state=state())
    assert len(inbox.mutations) == count


def test_changed_revision_is_immutable_and_deduplicated_despite_new_fetch_timestamp():
    inbox, document, result = prepare()
    revision = result["revision"]
    original = inbox.files[f"{ROOT}/pending/{revision}.json"]
    fresh = copy.deepcopy(document)
    fresh["checked_at"] = "2026-10-14T05:00:00+00:00"
    fresh["as_of"] = "2026-10-13"
    fresh_proposal = PROPOSAL.rstrip() + b"  \n"
    fresh["proposal_sha256"] = hashlib.sha256(fresh_proposal).hexdigest()
    fresh["sources"][0]["content_sha256"] = "e" * 64
    assert revision_id(fresh) == revision
    again = enqueue_report(inbox, fresh, proposal=fresh_proposal)
    assert again["created"] is False and again["issue"] == result["issue"]
    assert len(inbox.issues) == 1
    assert inbox.files[f"{ROOT}/pending/{revision}.json"] == original
    assert inbox.files[f"{ROOT}/pending/{revision}.proposal.json"] == PROPOSAL


def test_new_source_revision_has_distinct_review_issue():
    inbox, document, first = prepare()
    document["changes"][0]["id"] = "d" * 64
    second = enqueue_report(inbox, document, proposal=PROPOSAL)
    assert first["revision"] != second["revision"] and len(inbox.issues) == 2


def test_uncertain_issue_creation_reconciles_next_run_without_duplicate():
    inbox = Inbox()
    inbox.uncertain_issue_once = True
    with pytest.raises(RuntimeError):
        prepare(inbox)
    assert len(inbox.issues) == 1
    assert f"{ROOT}/state/sources.json" not in inbox.files
    _, _, result = prepare(inbox)
    assert result["created"] is False and len(inbox.issues) == 1


def test_interrupted_proposal_write_recovers_original_bytes():
    inbox = Inbox()
    inbox.fail_proposal_once = True
    with pytest.raises(RuntimeError):
        prepare(inbox)
    document = report()
    fresh = PROPOSAL.rstrip() + b"  \n"
    document["checked_at"] = "2026-10-14T05:00:00+00:00"
    document["proposal_sha256"] = hashlib.sha256(fresh).hexdigest()
    result = enqueue_report(inbox, document, proposal=fresh)
    assert inbox.files[f"{ROOT}/pending/{result['revision']}.proposal.json"] == PROPOSAL
    assert len(inbox.issues) == 1


@pytest.mark.parametrize("mutation", [
    lambda doc: doc.update(extra="raw response"),
    lambda doc: doc.update(status="unchanged"),
    lambda doc: doc.update(review_required=False),
    lambda doc: doc.update(proposal_file="../../public.json"),
    lambda doc: doc["sources"][0].update(url="https://127.0.0.1/data"),
    lambda doc: doc["sources"][0].update(url="https://www.fiscalia.gob.sv/data?token=secret"),
    lambda doc: doc["sources"][0].update(url="https://user:secret@www.fiscalia.gob.sv/data"),
    lambda doc: doc["sources"][0].update(extra="unexpected body"),
    lambda doc: doc["changes"][0].update(source_id="absent"),
    lambda doc: doc["changes"].append(doc["changes"][0]),
    lambda doc: doc.update(checked_at="2026-10-13T05:00:00"),
    lambda doc: doc.update(as_of="2026-02-30"),
    lambda doc: doc.update(schema_version=True),
])
def test_malformed_report_never_mutates_private_repo(mutation):
    inbox, document = Inbox(), report()
    mutation(document)
    with pytest.raises(ValueError):
        enqueue_report(inbox, document, proposal=PROPOSAL)
    assert not inbox.mutations


def test_candidate_hash_mismatch_rejected_before_any_write():
    inbox = Inbox()
    with pytest.raises(ValueError, match="identity"):
        enqueue_report(inbox, report(), proposal=PROPOSAL + b" ")
    assert not inbox.mutations


def test_duplicate_candidate_json_keys_are_rejected():
    inbox, document = Inbox(), report()
    raw = b'{"schema_version":1,"schema_version":1}'
    document["proposal_sha256"] = hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError, match="duplicate"):
        enqueue_report(inbox, document, proposal=raw)
    assert not inbox.mutations


def test_error_reports_are_deduplicated_without_numeric_proposals():
    inbox, document, result = prepare(changed=False, error=True, proposal=False)
    assert "source_http_403" in inbox.issues[0]["body"]
    assert result["errors"] == 1 and result["publications"] == 0
    assert enqueue_report(inbox, document)["created"] is False
    with pytest.raises(ValueError, match="numeric_proposal"):
        review_revision(inbox, event(result["revision"]), baseline=BASELINE)


@pytest.mark.parametrize("bad", [
    {"schema_version": 1, "catalogs": {}, "token": "secret"},
    {"schema_version": True, "catalogs": {}},
    {"schema_version": 1, "catalogs": {"unknown": []}},
    {"schema_version": 1, "catalogs": {"iml-homicides": [SOURCE_URL]}},
    {"schema_version": 1, "catalogs": {"iml-homicides": ["https://transparencia.oj.gob.sv/es/documentos/248"]}},
    {"schema_version": 1, "catalogs": {"iml-homicides": ["https://transparencia.oj.gob.sv/es/descargar/x?token=secret"]}},
])
def test_catalog_state_allows_only_expected_document_urls(bad):
    with pytest.raises(ValueError, match="source_state"):
        validate_source_state(bad)


def test_missing_catalog_state_bootstraps_empty_without_write():
    inbox = Inbox()
    assert export_source_state(inbox) == {"schema_version": 1, "catalogs": {}}
    assert not inbox.mutations


def test_approval_is_human_hash_bound_and_produces_only_a_private_artifact():
    inbox, _, result = prepare()
    validated = []
    artifact = review_revision(inbox, event(result["revision"]), now=NOW, baseline=BASELINE,
                               validate_candidate=validated.append)
    assert validated == [PROPOSAL]
    assert artifact["review"]["reviewed_by"] == "RauPro"
    assert artifact["review"]["proposal_sha256"] == PROPOSAL_SHA
    assert artifact["proposal_bytes"] == PROPOSAL and artifact["publications"] == 0
    assert inbox.issues[0]["state"] == "closed"
    assert all(path.startswith(inbox.base + "/") for _, path, _ in inbox.calls)
    assert all("/contents/official-statistics/" in path for method, path, _ in inbox.calls if method == "PUT")
    assert not any("dispatches" in path or "RauPro/MDH-SV/" in path for _, path, _ in inbox.calls)
    # A retry may recover its artifact, but cannot silently change the decision.
    repeated = review_revision(inbox, event(result["revision"]), baseline=BASELINE, validate_candidate=lambda raw: None)
    assert repeated["review"] == artifact["review"]
    with pytest.raises(ValueError, match="already_reviewed"):
        review_revision(inbox, event(result["revision"], decision="reject"))


def test_unrelated_fetch_errors_stay_explicit_after_narrow_proposal_approval():
    inbox, document, result = prepare(error=True)
    artifact = review_revision(inbox, event(result["revision"]), baseline=BASELINE, validate_candidate=lambda raw: None)
    assert artifact["review"]["unresolved_errors"] == document["errors"]
    assert "siguen sin resolverse" in inbox.issues[0]["body"]


def test_pnc_access_failure_is_a_review_alert_not_missing_to_zero():
    inbox, document = Inbox(), report(changed=False, error=True, proposal=False)
    document["sources"][1].update(source_id="pnc-reports", url="https://transparencia.pnc.gob.sv/reportes")
    document["errors"][0]["source_id"] = "pnc-reports"
    result = enqueue_report(inbox, document)
    assert result["errors"] == 1 and result["changes"] == 0
    assert "No convierte datos en cero" in inbox.issues[0]["body"]


def test_issue_reference_preserves_dedup_after_manual_label_changes():
    inbox, _, result = prepare()
    inbox.issues[0]["labels"] = []
    assert enqueue_report(inbox, report(), proposal=PROPOSAL)["issue"] == result["issue"]
    assert len(inbox.issues) == 1


def test_tampered_review_receipt_is_not_a_human_approval():
    inbox, _, result = prepare()
    path = f"{ROOT}/state/{result['revision']}.json"
    inbox.files[path] = json.dumps({"decision": "approve", "revision": result["revision"], "issue": 1}).encode()
    previous = len(inbox.mutations)
    with pytest.raises(ValueError, match="review_receipt"):
        review_revision(inbox, event(result["revision"]), baseline=BASELINE, validate_candidate=lambda raw: None)
    assert len(inbox.mutations) == previous


@pytest.mark.parametrize("permission", ["read", "triage", "none"])
def test_reviewer_must_have_write_permission(permission):
    inbox, _, result = prepare()
    inbox.permission = permission
    previous = len(inbox.mutations)
    with pytest.raises(ValueError, match="write_permission"):
        review_revision(inbox, event(result["revision"]), baseline=BASELINE)
    assert len(inbox.mutations) == previous


def test_bot_or_missing_manual_confirmation_cannot_approve():
    inbox, _, result = prepare()
    data = event(result["revision"])
    data["sender"]["type"] = "Bot"
    previous = len(inbox.mutations)
    with pytest.raises(ValueError, match="human"):
        review_revision(inbox, data)
    data["sender"]["type"] = "User"
    data["inputs"]["confirm_source"] = False
    with pytest.raises(ValueError, match="explicit_source"):
        review_revision(inbox, data)
    assert len(inbox.mutations) == previous


@pytest.mark.parametrize("baseline", [None, b"changed public data"])
def test_stale_public_baseline_cannot_be_approved(baseline):
    inbox, _, result = prepare()
    previous = len(inbox.mutations)
    with pytest.raises(ValueError, match="public_baseline_changed"):
        review_revision(inbox, event(result["revision"]), baseline=baseline, validate_candidate=lambda raw: None)
    assert len(inbox.mutations) == previous


def test_schema_validation_is_required_and_happens_before_decision_write():
    inbox, _, result = prepare()
    previous = len(inbox.mutations)
    with pytest.raises(ValueError, match="validator_required"):
        review_revision(inbox, event(result["revision"]), baseline=BASELINE)
    def invalid(_):
        raise ValueError("invalid candidate")
    with pytest.raises(ValueError, match="invalid candidate"):
        review_revision(inbox, event(result["revision"]), baseline=BASELINE, validate_candidate=invalid)
    assert len(inbox.mutations) == previous


def test_issue_label_or_closure_does_not_constitute_approval():
    inbox, _, result = prepare()
    inbox.issues[0].update(state="closed", labels=["statistics/approved"])
    assert f"{ROOT}/state/{result['revision']}.json" not in inbox.files
    assert enqueue_report(inbox, report(), proposal=PROPOSAL)["created"] is False
    assert f"{ROOT}/state/{result['revision']}.json" not in inbox.files


def test_rejection_never_materializes_a_proposal():
    inbox, _, result = prepare()
    artifact = review_revision(inbox, event(result["revision"], decision="reject"))
    assert artifact["proposal"] is None and artifact["proposal_bytes"] is None
    assert artifact["review"]["decision"] == "reject" and artifact["publications"] == 0


def test_public_schema_validator_accepts_real_dataset_and_rejects_missing_periods():
    root = Path(__file__).resolve().parents[1]
    schema = root / "shared/official-statistics.mjs"
    raw = (root / "data/official-statistics/homicides.json").read_bytes()
    validate_candidate_schema(raw, schema)
    with pytest.raises(ValueError, match="candidate_schema"):
        validate_candidate_schema(PROPOSAL, schema)
