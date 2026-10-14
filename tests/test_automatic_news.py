"""Automatic publication is a source-bound policy decision, never human review."""
import base64
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mdh_pipeline.automatic_news import (
    AutomaticNewsError, POLICY_VERSION, _hash, _privacy_projection, _receipt,
    evaluate_draft, evaluate_queue, export_collection, failed_report, fingerprint, project_collection,
    project_dossier, response_schema, validate_receipt,
)
from mdh_pipeline.article_source import SourceFetchError
from mdh_pipeline.gemini_budget import QuotaDeferred, QuotaStateError
from mdh_pipeline.gemini_draft import GeminiDraftError
from mdh_pipeline.history import SOURCES, reference_from_post
from mdh_pipeline.news_dossier import FIELD_NAMES, NewsDossierError, empty_news_dossier, validate_news_dossier, validate_published_news_dossier
from mdh_pipeline.news_publication import validate_collection
from mdh_pipeline.news_selection import FULL_SCOPE_PROMPT

NOW = datetime.now(timezone.utc) - timedelta(minutes=1)
MODEL = "gemini-3.5-flash-lite"
KEY = "fake-unit-test-key"


def inputs(number=1, extra=""):
    reference = reference_from_post({"id": number, "date": "2015-03-26T12:00:00",
        "link": f"https://www.diariocolatino.com/detencion-prueba-{number}/",
        "title": {"rendered": "Denuncia de detención en El Salvador"}}, SOURCES[0],
        date(2015, 1, 1), date(2015, 12, 31), NOW.isoformat())
    paragraphs = [{"id": "p1", "text": "En El Salvador, una organización denunció detenciones arbitrarias y falta de acceso a la defensa."},
                  {"id": "p2", "text": "Los hechos relatados ocurrieron en el departamento de San Salvador y afectaron a personas adultas."}]
    if extra:
        paragraphs.append({"id": "p3", "text": extra})
    text = "\n\n".join(p["text"] for p in paragraphs)
    digest = hashlib.sha256(text.encode()).hexdigest()
    reference["selection"] = {"method": "gemini_grounded_scope", "review_status": "automated_unreviewed",
        "human_reviewed": False, "jurisdiction": "SV", "relevance": "relevant", "classified_at": NOW.isoformat(),
        "model": MODEL, "prompt_version": FULL_SCOPE_PROMPT,
        "evidence": {"content_sha256": digest, "jurisdiction_paragraph_ids": ["p1"], "relevance_paragraph_ids": ["p1"]}}
    article = {"source_url": reference["source_url"], "title": reference["title"], "published_date": "2015-03-26",
        "text": text, "paragraphs": paragraphs, "content_sha256": digest, "fetched_at": NOW.isoformat(),
        "extraction_method": "wordpress_rest", "truncated": False}
    dossier = empty_news_dossier(reference, extraction_status="processed")
    dossier["classification"] = {"jurisdiction": "SV", "relevance": "relevant", "record_type": "incident"}
    dossier["evidence"] += [{"id": "article-" + p["id"], "url": reference["source_url"],
                             "locator": f"Párrafo {p['id']} del texto consultado; SHA-256 {digest}."} for p in paragraphs]
    dossier["fields"]["summary"] = {"value": "La organización denuncia detenciones y obstáculos al acceso a defensa.",
        "status": "reported", "evidence_refs": ["article-p1"], "note": None}
    dossier["fields"]["department"] = {"value": "San Salvador", "status": "reported", "evidence_refs": ["article-p2"], "note": None}
    dossier["fields"]["event_time"] = {"value": None, "status": "not_reported", "evidence_refs": [], "note": None}
    document = {"schema_version": 1, "candidate_id": reference["id"], "source_url": reference["source_url"],
        "status": "needs_scope_review", "publishable": False, "human_reviewed": False, "relevant": False,
        "record": None, "news_dossier": dossier, "classification": dossier["classification"],
        "extraction": {"provider": "gemini", "model": MODEL, "content_sha256": digest,
                       "protected_fields": [], "prepared_at": NOW.isoformat()}}
    return document, reference, article


def judgment():
    scope = {"verdict": "supported", "paragraph_ids": ["p1"], "reason_code": "supported_by_source"}
    fields = [{"field": name, "verdict": "supported", "paragraph_ids": ["p2" if name == "department" else "p1"],
               "reason_code": "supported_by_source", "privacy": "safe"} for name in FIELD_NAMES]
    return {"jurisdiction": deepcopy(scope), "relevance": deepcopy(scope), "fields": fields, "protected_fields": []}


def check(value, field):
    return next(item for item in value["fields"] if item["field"] == field)


def receipt(document=None, reference=None, *, generation=0):
    if document is None:
        document, reference, _ = inputs()
    result = _receipt(document, reference, generation, now=NOW)
    verdict = judgment()
    public = project_dossier(document["news_dossier"], verdict, source_hash=document["extraction"]["content_sha256"], model=MODEL, now=NOW)
    result.update(status="evaluated", attempts=1, judgment=verdict, dossier=public,
                  usage={"prompt_tokens": 100, "candidate_tokens": 10, "total_tokens": 110})
    return result


def archive(*references):
    return {"schema_version": 1, "records": list(references)}


class Budget:
    def __init__(self, remaining=10):
        self.config = SimpleNamespace(model=MODEL, batch_ceiling=50)
        self.requests_this_run = 0
        self.remaining = remaining
        self.limit = None
        self.cooldown = None
    def snapshot(self):
        return {"remaining_requests": self.remaining, "cooldown_until": self.cooldown}
    def set_request_limit(self, value):
        assert self.limit is None
        self.limit = value
    def reserve(self, payload):
        if self.limit is not None and self.requests_this_run >= self.limit:
            raise QuotaDeferred("run_budget_exhausted", NOW + timedelta(hours=1))
        self.requests_this_run += 1
        return object()
    def assert_dispatch_allowed(self, reservation):
        pass


class Response:
    status_code = 200
    headers = {}
    def __init__(self, verdict):
        self.body = json.dumps({"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(verdict)}]}}],
                               "usageMetadata": {"promptTokenCount": 123, "candidatesTokenCount": 50}}).encode()
    def iter_content(self, chunk_size):
        yield self.body
    def close(self):
        pass


class Session:
    def __init__(self, verdict=None):
        self.verdict, self.calls = verdict or judgment(), []
    def post(self, endpoint, **kwargs):
        self.calls.append(kwargs)
        return Response(self.verdict)


class PartsSession(Session):
    def __init__(self, parts):
        super().__init__()
        self.parts = parts
    def post(self, endpoint, **kwargs):
        self.calls.append(kwargs)
        response = Response(self.verdict)
        response.body = json.dumps({"candidates": [{"finishReason": "STOP", "content": {"parts": self.parts}}]}).encode()
        return response


class Inbox:
    repository = "RauPro/MDH-SV-review"
    base = "/repos/RauPro/MDH-SV-review"
    def __init__(self, documents=None):
        self.files, self.writes = {}, []
        for path, value in (documents or {}).items():
            self.files[path] = (deepcopy(value), self.sha(value))
    @staticmethod
    def sha(value):
        return hashlib.sha1(json.dumps(value, sort_keys=True).encode()).hexdigest()
    def get_file(self, path):
        path = path.split("/contents/")[1]
        if path not in self.files:
            return None
        value, sha = self.files[path]
        raw = json.dumps(value).encode()
        return {"type": "file", "path": path, "sha": sha, "size": len(raw), "encoding": "base64",
                "content": base64.b64encode(raw).decode()}
    def call(self, method, path, *, json):
        assert method == "PUT"
        path = path.split("/contents/")[1]
        previous = self.files.get(path)
        assert json.get("sha") == (previous[1] if previous else None)
        value = __import__("json").loads(base64.b64decode(json["content"]))
        sha = self.sha(value)
        self.files[path] = (value, sha)
        self.writes.append((path, deepcopy(value)))
        return {"content": {"sha": sha}}


def setup_queue(tmp_path, document=None, reference=None):
    if document is None:
        document, reference, _ = inputs()
    drafts = tmp_path / "inbox" / "drafts"
    drafts.mkdir(parents=True)
    (drafts / (reference["id"] + ".json")).write_text(json.dumps(document))
    inbox = Inbox({"drafts/" + reference["id"] + ".json": document})
    return inbox, drafts.parent


def fake_evaluate(document, reference, article, *, budget, **kwargs):
    budget.reserve({})
    return {"judgment": judgment(), "dossier": project_dossier(document["news_dossier"], judgment(),
             source_hash=article["content_sha256"], model=MODEL, now=NOW), "usage": None}


def test_v2_is_automatic_never_human_and_raw_extraction_stays_private():
    document, reference, article = inputs()
    result = evaluate_draft(document, reference, article, api_key=KEY, model=MODEL,
                            budget=Budget(), session=Session(), now=NOW)
    public = result["dossier"]
    assert public["schema_version"] == 2
    assert public["human_reviewed"] is False
    assert public["reviewed_by"] is None
    assert public["automation"]["source_content_sha256"] == article["content_sha256"]
    assert public["fields"]["summary"]["value"] == document["news_dossier"]["fields"]["summary"]["value"]
    assert public["fields"]["facts"]["status"] == "pending"
    assert article["paragraphs"][0]["text"] not in json.dumps(public)
    validate_collection({"schema_version": 1, "dossiers": [public]}, archive=archive(reference), allow_automatic=True)
    with pytest.raises(NewsDossierError):
        validate_collection({"schema_version": 1, "dossiers": [public]}, archive=archive(reference))
    with pytest.raises(NewsDossierError):
        validate_news_dossier(public, require_reviewed=True)
    with pytest.raises(NewsDossierError):
        validate_published_news_dossier(document["news_dossier"])


def test_provider_schema_uses_tested_flat_subset_but_local_keys_are_strict():
    schema = response_schema()
    forbidden = {"additionalProperties", "minItems", "maxItems", "minLength", "maxLength", "pattern", "$ref", "$defs"}
    def walk(node):
        if isinstance(node, dict):
            assert not set(node).intersection(forbidden)
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)
    walk(schema)
    assert set(schema["required"]) == {"jurisdiction", "relevance", "fields", "protected_fields"}
    assert schema["properties"]["fields"]["items"]["properties"]["field"]["enum"] == list(FIELD_NAMES)
    document, reference, article = inputs()
    verdict = judgment()
    verdict["unexpected"] = "extra field must fail locally"
    with pytest.raises(AutomaticNewsError):
        evaluate_draft(document, reference, article, api_key=KEY, model=MODEL, budget=Budget(), session=Session(verdict))


def test_thought_signature_and_thoughts_are_accepted_then_discarded():
    document, reference, article = inputs()
    parts = [{"text": "PRIVATE_THOUGHT_CANARY", "thought": True},
             {"text": json.dumps(judgment()), "thoughtSignature": "OPAQUE_SIGNATURE_CANARY"}]
    result = evaluate_draft(document, reference, article, api_key=KEY, model=MODEL, budget=Budget(), session=PartsSession(parts))
    assert result["dossier"] is not None
    assert "CANARY" not in json.dumps(result)


@pytest.mark.parametrize("parts", [
    [],
    [{"text": "{}", "thought": "true"}],
    [{"text": "{}", "thoughtSignature": 123}],
    [{"text": "{}", "functionCall": {"name": "unsafe"}}],
    [{"text": "{}"}, {"text": "{}"}],
    [{"text": "{}", "thought": True}],
    [{"text": "{}", "thought": True}] * 9,
])
def test_invalid_transport_metadata_or_multiple_visible_parts_fail_closed(parts):
    document, reference, article = inputs()
    with pytest.raises(AutomaticNewsError):
        evaluate_draft(document, reference, article, api_key=KEY, model=MODEL, budget=Budget(), session=PartsSession(parts))


def test_supported_absence_reason_only_applies_to_explicit_absent_fields():
    document, reference, article = inputs()
    verdict = judgment()
    check(verdict, "event_time").update(reason_code="not_in_source", paragraph_ids=[])
    check(verdict, "summary").update(reason_code="not_in_source", paragraph_ids=[])
    check(verdict, "facts").update(reason_code="not_in_source", paragraph_ids=[])
    result = evaluate_draft(document, reference, article, api_key=KEY, model=MODEL, budget=Budget(), session=Session(verdict))["dossier"]
    assert result["fields"]["event_time"]["status"] == "not_reported"
    assert result["fields"]["summary"]["status"] == "pending"
    assert result["fields"]["facts"]["status"] == "pending"
    assert result["fields"]["department"]["value"] == "San Salvador"
    verdict["jurisdiction"]["reason_code"] = "not_in_source"
    with pytest.raises(AutomaticNewsError):
        evaluate_draft(document, reference, article, api_key=KEY, model=MODEL, budget=Budget(), session=Session(verdict))


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(human_reviewed=True),
    lambda d: d.update(reviewed_by="automation-bot"),
    lambda d: d["automation"].update(extra="private"),
    lambda d: d["automation"].update(source_content_sha256="bad"),
    lambda d: d["automation"].update(method="trusted_model"),
    lambda d: d["automation"].update(corroboration="confirmed"),
    lambda d: d["automation"].update(validated_at="2026-01-01"),
    lambda d: d["automation"].update(validated_at="2099-01-01T00:00:00Z"),
    lambda d: d["automation"].update(validated_at="2026-01-01T01:00:00+01:00"),
    lambda d: d["automation"].update(evaluator_model=" model "),
    lambda d: d["classification"].update(jurisdiction="other"),
])
def test_public_schema_rejects_forged_or_invalid_automatic_metadata(mutation):
    value = receipt()["dossier"]
    mutation(value)
    with pytest.raises(NewsDossierError):
        validate_published_news_dossier(value)


def test_refetch_hash_mismatch_never_calls_evaluator():
    document, reference, article = inputs()
    reference["selection"]["evidence"]["content_sha256"] = "f" * 64
    session = Session()
    with pytest.raises(AutomaticNewsError, match="source_changed"):
        evaluate_draft(document, reference, article, api_key=KEY, model=MODEL, budget=Budget(), session=session)
    assert session.calls == []


@pytest.mark.parametrize("mutation", [
    lambda j: j["fields"].append(deepcopy(j["fields"][0])),
    lambda j: check(j, "summary").update(paragraph_ids=["p999"]),
    lambda j: check(j, "summary").update(verdict="looks_good"),
    lambda j: check(j, "summary").update(quote="source quotation"),
    lambda j: j["jurisdiction"].update(paragraph_ids=[]),
])
def test_invalid_model_verdict_never_becomes_public(mutation):
    document, reference, article = inputs()
    verdict = judgment()
    mutation(verdict)
    with pytest.raises(AutomaticNewsError):
        evaluate_draft(document, reference, article, api_key=KEY, model=MODEL, budget=Budget(), session=Session(verdict))


def test_sparse_provider_verdicts_preserve_missing_fields_as_unresolved():
    document, reference, article = inputs()
    verdict = judgment()
    verdict["fields"] = [check(verdict, name) for name in ("summary", "department", "event_time")]
    result = evaluate_draft(document, reference, article, api_key=KEY, model=MODEL, budget=Budget(), session=Session(verdict))
    assert len(result["judgment"]["fields"]) == len(FIELD_NAMES)
    assert result["dossier"]["fields"]["facts"]["status"] == "pending"
    assert result["dossier"]["fields"]["summary"]["status"] == "reported"
    assert check(result["judgment"], "facts")["verdict"] == "insufficient_evidence"
    assert check(result["judgment"], "facts")["paragraph_ids"] == []


def test_omitted_reported_or_absent_fields_cannot_be_published_as_approved():
    document, reference, article = inputs()
    verdict = judgment()
    verdict["fields"] = [check(verdict, "department")]
    result = evaluate_draft(document, reference, article, api_key=KEY, model=MODEL, budget=Budget(), session=Session(verdict))
    assert result["dossier"]["fields"]["summary"]["status"] == "pending"
    assert result["dossier"]["fields"]["event_time"]["status"] == "pending"
    assert result["dossier"]["fields"]["department"]["value"] == "San Salvador"


def test_empty_verdict_list_publishes_no_dossier_and_keeps_privacy_holds():
    document, reference, article = inputs()
    document["news_dossier"]["fields"]["summary"] = {"value": None, "status": "withheld", "evidence_refs": [], "note": "Identidad protegida por privacidad."}
    verdict = judgment()
    verdict["fields"] = []
    result = evaluate_draft(document, reference, article, api_key=KEY, model=MODEL, budget=Budget(), session=Session(verdict))
    assert result["dossier"] is None
    assert "summary" in result["judgment"]["protected_fields"]
    assert len(result["judgment"]["fields"]) == len(FIELD_NAMES)


@pytest.mark.parametrize("fields", [
    [{"field": "unknown_field"}],
    [{"field": "summary"}, {"field": "summary"}],
    [{"field": "summary"}],
    [None],
])
def test_sparse_verdicts_still_reject_unknown_duplicate_or_malformed_fields(fields):
    document, reference, article = inputs()
    verdict = judgment()
    verdict["fields"] = fields
    with pytest.raises(AutomaticNewsError):
        evaluate_draft(document, reference, article, api_key=KEY, model=MODEL, budget=Budget(), session=Session(verdict))


@pytest.mark.parametrize("privacy", ["safe", "withhold"])
def test_privacy_risk_verdict_withholds_only_affected_field(privacy):
    document, reference, article = inputs()
    verdict = judgment()
    check(verdict, "summary").update(verdict="supported", reason_code="privacy_risk", privacy=privacy)
    result = evaluate_draft(document, reference, article, api_key=KEY, model=MODEL, budget=Budget(), session=Session(verdict))
    assert result["dossier"]["fields"]["summary"]["status"] == "withheld"
    assert result["dossier"]["fields"]["department"]["value"] == "San Salvador"
    value = _receipt(document, reference, 0, now=NOW)
    value.update(status="evaluated", attempts=1, **result)
    assert len(project_collection({reference["id"]: document}, {reference["id"]: value}, archive(reference))[0]["dossiers"]) == 1
    verdict["jurisdiction"]["reason_code"] = "privacy_risk"
    with pytest.raises(AutomaticNewsError):
        evaluate_draft(document, reference, article, api_key=KEY, model=MODEL, budget=Budget(), session=Session(verdict))


def test_field_failures_leave_other_fields_and_do_not_invent_absence():
    document, reference, article = inputs()
    verdict = judgment()
    check(verdict, "summary").update(verdict="contradicted", reason_code="wrong_attribution")
    result = evaluate_draft(document, reference, article, api_key=KEY, model=MODEL, budget=Budget(), session=Session(verdict))["dossier"]
    assert result["fields"]["summary"]["status"] == "pending"
    assert result["fields"]["department"]["value"] == "San Salvador"
    assert result["fields"]["event_time"]["status"] == "not_reported"


def test_partial_source_cannot_assert_absence():
    document, reference, article = inputs()
    article["truncated"] = True
    result = evaluate_draft(document, reference, article, api_key=KEY, model=MODEL, budget=Budget(), session=Session())["dossier"]
    assert result["fields"]["event_time"]["status"] == "pending"


def test_sensitive_names_in_narrative_are_withheld_but_coarse_place_survives():
    document, reference, article = inputs(extra="María López, de 12 años, sufrió una detención cerca de su escuela.")
    document["news_dossier"]["fields"]["summary"]["value"] = "La organización denunció la detención de María López."
    session = Session()
    result = evaluate_draft(document, reference, article, api_key=KEY, model=MODEL, budget=Budget(), session=session)["dossier"]
    assert result["fields"]["summary"]["status"] == "withheld"
    assert "María" not in json.dumps(result, ensure_ascii=False)
    assert result["fields"]["department"]["value"] == "San Salvador"
    prompt = json.loads(session.calls[0]["json"]["contents"][0]["parts"][0]["text"])
    assert prompt["untrusted_dossier"]["fields"]["summary"]["value"] is None


def test_preexisting_privacy_hold_and_evaluator_privacy_cannot_be_restored():
    document, reference, article = inputs()
    document["news_dossier"]["fields"]["summary"] = {"value": None, "status": "withheld", "evidence_refs": [], "note": "Identidad protegida por privacidad."}
    result = evaluate_draft(document, reference, article, api_key=KEY, model=MODEL, budget=Budget(), session=Session())["dossier"]
    assert result["fields"]["summary"]["status"] == "withheld"
    verdict = judgment()
    check(verdict, "department")["privacy"] = "withhold"
    assert evaluate_draft(document, reference, article, api_key=KEY, model=MODEL, budget=Budget(), session=Session(verdict))["dossier"] is None


def test_all_rejected_or_wrong_scope_has_no_public_projection():
    document, reference, article = inputs()
    for field in ("jurisdiction", "relevance"):
        verdict = judgment()
        verdict[field]["verdict"] = "contradicted"
        assert evaluate_draft(document, reference, article, api_key=KEY, model=MODEL, budget=Budget(), session=Session(verdict))["dossier"] is None


def test_public_receipt_binds_source_draft_and_request_generation():
    document, reference, _ = inputs()
    value = receipt(document, reference)
    drafts, receipts = {reference["id"]: document}, {reference["id"]: value}
    assert len(project_collection(drafts, receipts, archive(reference))[0]["dossiers"]) == 1
    assert project_collection(drafts, receipts, archive(reference), request_generations={reference["id"]: 4})[0]["dossiers"] == []
    reference["selection"]["evidence"]["content_sha256"] = "c" * 64
    assert project_collection(drafts, receipts, archive(reference))[0]["dossiers"] == []
    _, reference, _ = inputs()
    document["extraction"]["prompt_version"] = "new-version"
    assert project_collection(drafts, receipts, archive(reference))[0]["dossiers"] == []


def test_pending_cannot_be_promoted_to_asserted_absence_by_receipt():
    document, reference, _ = inputs()
    value = receipt(document, reference)
    value["dossier"]["fields"]["facts"]["status"] = "not_reported"
    with pytest.raises(AutomaticNewsError, match="absence_not_grounded"):
        project_collection({reference["id"]: document}, {reference["id"]: value}, archive(reference))


def test_receipt_cannot_add_fields_from_model_or_ignore_privacy():
    document, reference, _ = inputs()
    value = receipt(document, reference)
    value["dossier"]["fields"]["summary"]["value"] = "A new model-invented statement"
    with pytest.raises(AutomaticNewsError, match="not_grounded"):
        project_collection({reference["id"]: document}, {reference["id"]: value}, archive(reference))


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(source_title="Private arbitrary title inserted into a cached receipt"),
    lambda d: d["fields"]["summary"].update(note="Private arbitrary note inserted into a cached receipt"),
    lambda d: d["evidence"][0].update(locator="Private original quotation inserted into a cached receipt"),
    lambda d: d["evidence"][0].update(url="https://www.diariocolatino.com/different-private-record/"),
])
def test_cached_receipt_cannot_publish_arbitrary_metadata_prose(mutation):
    document, reference, _ = inputs()
    value = receipt(document, reference)
    mutation(value["dossier"])
    with pytest.raises(AutomaticNewsError):
        project_collection({reference["id"]: document}, {reference["id"]: value}, archive(reference))


def test_local_privacy_decision_is_recorded_and_reconstructed_without_source_prose():
    document, reference, article = inputs(extra="María López, de 12 años, sufrió una detención cerca de su escuela.")
    document["news_dossier"]["fields"]["summary"]["value"] = "La organización denunció la detención de María López."
    document["news_dossier"]["source_title"] = "Detención de María López"
    evaluated = evaluate_draft(document, reference, article, api_key=KEY, model=MODEL, budget=Budget(), session=Session(), now=NOW)
    value = _receipt(document, reference, 0, now=NOW)
    value.update(status="evaluated", attempts=1, **evaluated)
    assert {"summary", "source_title"} <= set(value["judgment"]["protected_fields"])
    automatic, _ = project_collection({reference["id"]: document}, {reference["id"]: value}, archive(reference))
    assert len(automatic["dossiers"]) == 1
    assert "María" not in json.dumps(automatic, ensure_ascii=False)


def test_protected_date_interval_remains_withheld_in_public_projection():
    document, reference, article = inputs()
    for name in ("event_date", "event_date_end"):
        document["news_dossier"]["fields"][name] = {"value": None, "status": "withheld", "evidence_refs": [], "note": "Fecha protegida por privacidad."}
    evaluated = evaluate_draft(document, reference, article, api_key=KEY, model=MODEL, budget=Budget(), session=Session(), now=NOW)
    assert evaluated["dossier"]["fields"]["event_date_end"]["status"] == "withheld"
    value = _receipt(document, reference, 0, now=NOW)
    value.update(status="evaluated", attempts=1, **evaluated)
    assert len(project_collection({reference["id"]: document}, {reference["id"]: value}, archive(reference))[0]["dossiers"]) == 1


def test_human_override_and_removal_request_take_precedence():
    document, reference, _ = inputs()
    value = receipt(document, reference)
    human = deepcopy(document["news_dossier"])
    human.update(human_reviewed=True, reviewed_by="maintainer", reviewed_at=date.today().isoformat(),
                 review_notes="Comprobados el texto, la atribución y la privacidad.")
    human["fields"]["summary"]["value"] = "Corrección editorial respaldada por la fuente."
    automatic, merged = project_collection({reference["id"]: document}, {reference["id"]: value}, archive(reference),
                                          human={"schema_version": 1, "dossiers": [human]})
    assert automatic["dossiers"] == []
    assert merged["dossiers"] == [human]
    assert project_collection({reference["id"]: document}, {reference["id"]: value}, archive(reference),
        human={"schema_version": 1, "dossiers": [human]}, excluded_ids=[reference["id"]])[1]["dossiers"] == []


def test_worker_persists_attempt_before_request_and_is_idempotent(tmp_path):
    document, reference, article = inputs()
    inbox, folder = setup_queue(tmp_path)
    budget = Budget()
    def checked_evaluate(*args, **kwargs):
        assert inbox.writes[-1][1]["status"] == "evaluating"
        return fake_evaluate(*args, **kwargs)
    result = evaluate_queue(inbox, folder, archive(reference), api_key=KEY, model=MODEL, budget=budget,
                            fetch=lambda _: article, evaluate=checked_evaluate, now=NOW)
    assert result["publishable"] == result["http_requests"] == 1
    assert len(inbox.writes) == 2
    result = evaluate_queue(inbox, folder, archive(reference), api_key=KEY, model=MODEL, budget=Budget(),
                            fetch=lambda _: pytest.fail("cached"), now=NOW)
    assert result["cached"] == 1
    assert len(inbox.writes) == 2


def test_request_generation_invalidates_cache_once_and_persists_until_processed(tmp_path):
    document, reference, article = inputs()
    inbox, folder = setup_queue(tmp_path)
    evaluate_queue(inbox, folder, archive(reference), api_key=KEY, model=MODEL, budget=Budget(), fetch=lambda _: article, evaluate=fake_evaluate)
    result = evaluate_queue(inbox, folder, archive(reference), api_key=KEY, model=MODEL, budget=Budget(),
        fetch=lambda _: article, evaluate=fake_evaluate, request_generations={reference["id"]: 55})
    assert result["publishable"] == 1
    assert inbox.writes[-1][1]["request_generation"] == 55


@pytest.mark.parametrize("reason", ["quota", "cooldown"])
def test_exhausted_budget_does_not_fetch_write_or_call_model(tmp_path, reason):
    _, reference, _ = inputs()
    inbox, folder = setup_queue(tmp_path)
    budget = Budget(0 if reason == "quota" else 10)
    if reason == "cooldown":
        budget.cooldown = NOW.isoformat()
    result = evaluate_queue(inbox, folder, archive(reference), api_key=KEY, model=MODEL, budget=budget,
                            fetch=lambda _: pytest.fail("no fetch"))
    assert result["stopped_reason"] == "budget_deferred"
    assert inbox.writes == []


def test_source_changed_reextracts_with_same_budget_then_waits_for_selection(tmp_path):
    document, reference, _ = inputs()
    _, _, fresh = inputs(extra="La fuente añadió una actualización explicativa posterior sobre el informe.")
    inbox, folder = setup_queue(tmp_path)
    def extract(candidate, article, *, budget, **kwargs):
        budget.reserve({})
        updated = deepcopy(document)
        updated["extraction"]["content_sha256"] = article["content_sha256"]
        return updated
    result = evaluate_queue(inbox, folder, archive(reference), api_key=KEY, model=MODEL, budget=Budget(),
        fetch=lambda _: fresh, evaluate=lambda *a, **k: pytest.fail("do not judge stale selection"), extract=extract)
    assert result["source_refreshed"] == 1
    assert result["publishable"] == 0
    assert result["http_requests"] == 1
    assert inbox.writes[-1][1]["status"] == "source_refreshed"
    assert json.loads((folder / "drafts" / (reference["id"] + ".json")).read_text())["extraction"]["content_sha256"] == fresh["content_sha256"]


def test_selection_catches_up_without_leaving_source_changed_permanently_cached(tmp_path):
    document, reference, article = inputs()
    inbox, folder = setup_queue(tmp_path)
    value = _receipt(document, reference, 0, now=NOW)
    value.update(status="source_changed", attempts=3)
    path = "automatic-news/" + reference["id"] + ".json"
    inbox.files[path] = (value, inbox.sha(value))
    (folder / "automatic-news").mkdir()
    (folder / path).write_text(json.dumps(value))
    result = evaluate_queue(inbox, folder, archive(reference), api_key=KEY, model=MODEL, budget=Budget(),
                            fetch=lambda _: article, evaluate=fake_evaluate)
    assert result["publishable"] == 1
    assert inbox.writes[-1][1]["attempts"] == 1


@pytest.mark.parametrize("reserved", [False, True])
def test_budget_deferral_counts_attempt_only_after_real_reservation(tmp_path, reserved):
    _, reference, article = inputs()
    inbox, folder = setup_queue(tmp_path)
    def evaluate(*args, budget, **kwargs):
        if reserved:
            budget.reserve({})
        raise QuotaDeferred("daily_budget_exhausted", NOW + timedelta(days=1))
    result = evaluate_queue(inbox, folder, archive(reference), api_key=KEY, model=MODEL, budget=Budget(),
                            fetch=lambda _: article, evaluate=evaluate)
    assert result["stopped_reason"] == "budget_deferred"
    assert inbox.writes[-1][1]["attempts"] == int(reserved)


def test_repeated_source_failures_are_bounded_and_private(tmp_path):
    _, reference, _ = inputs()
    inbox, folder = setup_queue(tmp_path)
    def fetch(_):
        raise SourceFetchError("source_access_restricted")
    for _ in range(3):
        result = evaluate_queue(inbox, folder, archive(reference), api_key=KEY, model=MODEL, budget=Budget(), fetch=fetch)
        assert result["source_unavailable"] == 1
    result = evaluate_queue(inbox, folder, archive(reference), api_key=KEY, model=MODEL, budget=Budget(), fetch=lambda _: pytest.fail("bounded"))
    assert result["cached"] == 1
    assert result["http_requests"] == 0


def test_uncertain_budget_state_stops_worker(tmp_path):
    _, reference, article = inputs()
    inbox, folder = setup_queue(tmp_path)
    def evaluate(*args, **kwargs):
        raise QuotaStateError("budget_read_failed")
    with pytest.raises(QuotaStateError):
        evaluate_queue(inbox, folder, archive(reference), api_key=KEY, model=MODEL, budget=Budget(), fetch=lambda _: article, evaluate=evaluate)
    assert inbox.writes[-1][1]["status"] == "evaluating"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 500])
def test_provider_http_failure_records_only_bounded_status_code(tmp_path, status):
    _, reference, article = inputs()
    inbox, folder = setup_queue(tmp_path)
    def evaluate(*args, **kwargs):
        error = GeminiDraftError("provider_http_error", status_code=status)
        error.args = ("PRIVATE_PROVIDER_RESPONSE_CANARY",)
        raise error
    result = evaluate_queue(inbox, folder, archive(reference), api_key=KEY, model=MODEL, budget=Budget(),
                            fetch=lambda _: article, evaluate=evaluate)
    assert result["errors"] == 1
    assert inbox.writes[-1][1]["error_code"] == f"provider_http_{status}"
    assert "CANARY" not in json.dumps(inbox.writes)


@pytest.mark.parametrize("status,reason", [(400, "provider_configuration"), (404, "provider_configuration"),
                                         (401, "provider_authentication"), (403, "provider_authentication")])
def test_provider_configuration_or_auth_error_stops_entire_batch(tmp_path, status, reason):
    first, first_ref, first_article = inputs(1)
    second, second_ref, second_article = inputs(2)
    inbox, folder = setup_queue(tmp_path, first, first_ref)
    second_path = "drafts/" + second_ref["id"] + ".json"
    (folder / second_path).write_text(json.dumps(second))
    inbox.files[second_path] = (second, inbox.sha(second))
    articles = {first_ref["id"]: first_article, second_ref["id"]: second_article}
    def evaluate(*args, budget, **kwargs):
        budget.reserve({})
        raise GeminiDraftError("provider_http_error", status_code=status)
    result = evaluate_queue(inbox, folder, archive(first_ref, second_ref), api_key=KEY, model=MODEL,
                            budget=Budget(), fetch=lambda ref: articles[ref["id"]], evaluate=evaluate, limit=3)
    assert result["attempted"] == result["http_requests"] == result["errors"] == 1
    assert result["stopped_reason"] == reason
    assert result["last_error_code"] == f"provider_http_{status}"
    assert len(inbox.writes) == 2


@pytest.mark.parametrize("changes,expected", [
    ({"stopped_reason": "provider_configuration", "attempted": 1, "errors": 1}, True),
    ({"stopped_reason": "provider_authentication", "attempted": 1, "errors": 1}, True),
    ({"attempted": 3, "errors": 3}, True),
    ({"attempted": 3, "errors": 2, "evaluated": 1}, False),
    ({"attempted": 1, "evaluated": 1, "publishable": 0}, False),
    ({"attempted": 1, "source_refreshed": 1}, False),
    ({"attempted": 1, "source_unavailable": 1}, False),
    ({"stopped_reason": "budget_deferred", "attempted": 1, "errors": 1}, False),
    ({}, False),
])
def test_worker_report_distinguishes_failure_from_quota_or_abstention(changes, expected):
    report = {"attempted": 0, "evaluated": 0, "source_refreshed": 0, "errors": 0, "stopped_reason": None, **changes}
    assert failed_report(report) is expected


def test_cli_prints_safe_failure_report_then_exits_nonzero(tmp_path, monkeypatch, capsys):
    import mdh_pipeline.automatic_news as module
    _, reference, _ = inputs()
    inbox, folder = setup_queue(tmp_path)
    archive_path = tmp_path / "archive.json"
    archive_path.write_text(json.dumps(archive(reference)))
    report = {"attempted": 1, "errors": 1, "evaluated": 0, "source_refreshed": 0,
              "stopped_reason": "provider_configuration", "last_error_code": "provider_http_400"}
    monkeypatch.setenv("MDH_GEMINI_BUDGET_CONFIG", "{}")
    monkeypatch.setenv("GITHUB_REPOSITORY", inbox.repository)
    monkeypatch.setenv("GH_TOKEN", "unit-test-token")
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    monkeypatch.setattr(module, "BudgetConfig", lambda **kw: SimpleNamespace(model=MODEL))
    monkeypatch.setattr(module, "GitHubInbox", lambda *a: inbox)
    monkeypatch.setattr(module, "GeminiBudget", lambda *a: Budget())
    monkeypatch.setattr(module, "evaluate_queue", lambda *a, **kw: report)
    monkeypatch.setattr("sys.argv", ["automatic_news", "evaluate", "--inbox-dir", str(folder), "--archive", str(archive_path)])
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 1
    assert json.loads(capsys.readouterr().out) == report


def test_export_only_safe_fields_to_both_sources_and_rejects_symlinks(tmp_path):
    document, reference, _ = inputs()
    inbox, folder = setup_queue(tmp_path)
    (folder / "automatic-news").mkdir()
    (folder / "automatic-news" / (reference["id"] + ".json")).write_text(json.dumps(receipt(document, reference)))
    archive_path, reviewed_path = tmp_path / "news.json", tmp_path / "reviewed.json"
    archive_path.write_text(json.dumps(archive(reference)))
    reviewed_path.write_text(json.dumps({"schema_version": 1, "dossiers": []}))
    public, automatic = tmp_path / "public.json", tmp_path / "auto.json"
    result = export_collection(folder, archive_path, reviewed_path, public, automatic)
    assert result == {"automatic_dossiers": 1, "published_dossiers": 1}
    assert json.loads(public.read_text()) == json.loads(automatic.read_text())
    assert "judgment" not in public.read_text()
    public.unlink()
    public.symlink_to(automatic)
    with pytest.raises(AutomaticNewsError):
        export_collection(folder, archive_path, reviewed_path, public, automatic)
