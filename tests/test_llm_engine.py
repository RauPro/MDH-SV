"""Fallback lanes use synthetic clocks, in-memory ledgers and fake HTTP; never a provider."""
import base64
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from mdh_pipeline import gemini_draft, llm_engine
from mdh_pipeline.contracts.providers import SERVED_BY
from mdh_pipeline.gemini_budget import (BudgetConfig, GeminiBudget, MODEL, QuotaDeferred, QuotaStateError,
                                        STATE_PATH, next_pacific_midnight)
from mdh_pipeline.gemini_draft import GeminiDraftError, _request, served_by
from mdh_pipeline.llm_engine import CONFIG_VARIABLE, ENDPOINTS, Lane, LlmEngine, load_fallbacks, probe, with_fallbacks

NOW = datetime(2026, 11, 11, 14, 0, tzinfo=timezone.utc)
GEMINI_KEY, GROQ_KEY = "gemini-unit-test-key-not-real", "groq-unit-test-key-not-real"
GROQ_MODEL = "openai/gpt-oss-120b"
GROQ_PATH = "runtime/llm-budget-groq.json"
PAYLOAD = {"systemInstruction": {"parts": [{"text": "Instrucción sintética."}]},
           "contents": [{"role": "user", "parts": [{"text": "{\"untrusted\":\"dato ficticio\"}"}]}],
           "generationConfig": {"responseMimeType": "application/json", "candidateCount": 1, "temperature": 0,
                                "maxOutputTokens": 256, "responseJsonSchema": {"type": "object"}}}


def gemini_config(**changes):
    return replace(BudgetConfig("mdh-project-test", MODEL, 10, 101, 100_000, 100, 50,
                                "2026-11-11T13:00:00+00:00"), **changes)


def groq_document(**changes):
    return {"provider": "groq", "project_id": "mdh-groq-test", "model": GROQ_MODEL, "rpm": 10, "rpd": None,
            "input_tpm": 60_000, "daily_budget": 40, "batch_ceiling": 50, "limits_source": "operator_cap",
            "verified_at": "2026-11-11T13:00:00+00:00", "reset_timezone": "UTC", **changes}


class Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class Inbox:
    base = "/repos/example/private-review"

    def __init__(self):
        self.files, self.writes = {}, []

    def get_file(self, path):
        return deepcopy(self.files.get(path.split("/contents/", 1)[1]))

    def call(self, method, path, **kwargs):
        assert method == "PUT"
        name, payload = path.split("/contents/", 1)[1], kwargs["json"]
        assert payload["branch"] == "main"
        if payload.get("sha") != (self.files.get(name) or {}).get("sha"):
            raise RuntimeError("GitHub request failed (409); candidate content withheld")
        raw = base64.b64decode(payload["content"])
        self.files[name] = {"path": name, "type": "file", "encoding": "base64", "size": len(raw),
                            "content": payload["content"], "sha": hashlib.sha256(raw).hexdigest()}
        self.writes.append((name, payload["message"]))
        return {"content": {"sha": self.files[name]["sha"]}}

    def document(self, name):
        return json.loads(base64.b64decode(self.files[name]["content"]))


class Response:
    def __init__(self, document=None, status=200, headers=None, raw=None):
        self.status_code, self.headers = status, headers or {}
        self.body = raw if raw is not None else json.dumps(document or {}).encode()

    def iter_content(self, chunk_size):
        yield self.body

    def close(self):
        pass


class Session:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def post(self, url, **kwargs):
        if not self.calls and getattr(self, "before_first", None):
            self.before_first()
        self.calls.append((url, kwargs))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def gemini_answer(text='{"ok":true}', **extra):
    return Response({"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": text}]}}], **extra})


def chat_answer(text='{"ok":true}', finish="stop", model=GROQ_MODEL):
    return Response({"model": model, "choices": [{"finish_reason": finish, "message": {"role": "assistant", "content": text}}],
                     "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14}})


def ready(*, groq=True, provision=True, gemini=None, lane=None, key=GROQ_KEY):
    """A provisioned Gemini ledger plus, optionally, one Groq lane past its first blocked minute."""
    clock, inbox = Clock(), Inbox()
    primary = GeminiBudget(inbox, gemini or gemini_config(), clock=clock)
    primary.bootstrap(used_requests=0)
    lanes = []
    if groq:
        environ = {CONFIG_VARIABLE: json.dumps([groq_document(**(lane or {}))]), "GROQ_API_KEY": key}
        lanes = load_fallbacks(inbox, environ)
        for item in lanes:
            item.budget.clock = clock
            if provision:
                item.budget.bootstrap(used_requests=0)
    clock.advance(60)
    return with_fallbacks(inbox, primary, lanes=lanes), inbox, clock


def test_fallback_configuration_is_explicit_bounded_and_zero_cost_only():
    config = BudgetConfig(**groq_document())
    assert config.state_path == GROQ_PATH and gemini_config().state_path == STATE_PATH
    for changes in ({"provider": "xai", "model": "grok-4.7"}, {"provider": "openrouter", "model": "openai/gpt-4o"},
                    {"model": "openai/gpt oss"}, {"reset_timezone": "Europe/Madrid"}, {"reset_timezone": None},
                    {"provider": "gemini"}, {"daily_budget": 0}, {"rpm": 0}, {"verified_at": "2026-11-11"}):
        with pytest.raises(QuotaStateError):
            BudgetConfig(**groq_document(**changes))
    BudgetConfig(**groq_document(provider="openrouter", model="openrouter/free"))
    with pytest.raises(QuotaStateError):
        gemini_config(reset_timezone="UTC")


@pytest.mark.parametrize("raw", [
    "{", "{}", "[]", "[1]", json.dumps([groq_document(), groq_document()]),
    json.dumps([groq_document(provider="gemini", model=MODEL, reset_timezone="America/Los_Angeles")]),
    json.dumps([groq_document(unknown=1)]), json.dumps([groq_document(response_mode="free_text")]),
    json.dumps([groq_document(endpoint="https://attacker.invalid/v1")]),
    '[{"provider":"groq","provider":"nvidia"}]',
])
def test_malformed_or_redirecting_fallback_configuration_fails_closed(raw):
    with pytest.raises(QuotaStateError, match="invalid_fallback_configuration|invalid_explicit_budget_configuration"):
        load_fallbacks(Inbox(), {CONFIG_VARIABLE: raw, "GROQ_API_KEY": GROQ_KEY})


def test_without_configuration_the_primary_budget_is_returned_unchanged():
    primary = GeminiBudget(Inbox(), gemini_config(), clock=Clock())
    assert load_fallbacks(Inbox(), {}) == [] and load_fallbacks(Inbox(), {CONFIG_VARIABLE: "  "}) == []
    assert with_fallbacks(primary.inbox, primary, environ={}) is primary


def test_endpoints_are_fixed_https_hosts_and_keys_come_only_from_their_own_variable():
    assert set(ENDPOINTS) == set(llm_engine.KEY_VARIABLES) == {"groq", "nvidia", "mistral", "openrouter"}
    assert all(url.startswith("https://") and url.endswith("/chat/completions") for url in ENDPOINTS.values())
    for key in ("", "short", "has space in the key value"):
        lanes = load_fallbacks(Inbox(), {CONFIG_VARIABLE: json.dumps([groq_document()]), "GROQ_API_KEY": key,
                                         "GEMINI_API_KEY": GEMINI_KEY})
        assert lanes[0].closed == "missing_api_key" and not lanes[0].usable(NOW)


def test_primary_is_preferred_and_one_run_limit_covers_every_lane():
    engine, inbox, _ = ready()
    assert isinstance(engine, LlmEngine) and isinstance(engine, GeminiBudget)
    engine.set_request_limit(2)
    first = engine.reserve(PAYLOAD)
    assert first.lane.provider == "gemini" and engine.requests_this_run == 1
    engine.primary.record_429()
    second = engine.reserve(PAYLOAD)
    assert second.lane.provider == "groq" and second.lane.model == GROQ_MODEL
    engine.assert_dispatch_allowed(second)
    with pytest.raises(QuotaDeferred) as deferred:
        engine.reserve(PAYLOAD)
    assert deferred.value.reason == "run_budget_exhausted" and engine.requests_this_run == 2
    assert inbox.document(STATE_PATH)["used_requests"] == 1 and inbox.document(GROQ_PATH)["used_requests"] == 1
    assert {name for name, _ in inbox.writes} == {STATE_PATH, GROQ_PATH}
    assert all("Gemini" not in message for name, message in inbox.writes if name == GROQ_PATH)


def test_a_busy_gemini_minute_overflows_to_the_fallback_instead_of_waiting():
    engine, _, _ = ready(gemini=gemini_config(rpm=1))
    assert [engine.reserve(PAYLOAD).lane.provider for _ in range(3)] == ["gemini", "groq", "groq"]


def test_unprovisioned_fallback_ledger_is_never_created_or_used():
    engine, inbox, _ = ready(provision=False)
    assert engine.snapshot()["remaining_requests"] == 100
    engine.primary.record_429()
    with pytest.raises(QuotaDeferred) as deferred:
        engine.reserve(PAYLOAD)
    assert deferred.value.reason == "provider_cooldown"
    assert GROQ_PATH not in inbox.files and engine.fallbacks[0].closed == "ledger_unavailable"


def test_missing_or_corrupt_primary_ledger_still_fails_closed():
    engine, inbox, _ = ready()
    del inbox.files[STATE_PATH]
    for operation in (engine.snapshot, lambda: engine.reserve(PAYLOAD)):
        with pytest.raises(QuotaStateError, match="missing"):
            operation()
    assert inbox.document(GROQ_PATH)["used_requests"] == 0


def test_snapshot_adds_fallback_capacity_and_reports_cooldown_only_without_any():
    engine, _, clock = ready()
    assert engine.snapshot()["remaining_requests"] == 140
    engine.primary.record_429()
    state = engine.snapshot()
    assert state["remaining_requests"] == 40 and state["cooldown_until"] is None
    engine.fallbacks[0].budget.record_429()
    state = engine.snapshot()
    assert state["remaining_requests"] == 0 and state["cooldown_until"] == next_pacific_midnight(clock.now).isoformat()


def test_fallback_day_resets_at_its_own_boundary():
    engine, inbox, clock = ready(lane={"daily_budget": 1})
    engine.primary.record_429()
    assert engine.reserve(PAYLOAD).lane.provider == "groq"
    with pytest.raises(QuotaDeferred) as deferred:
        engine.reserve(PAYLOAD)
    assert deferred.value.reason == "provider_cooldown"
    clock.now = datetime(2026, 11, 12, 0, 5, tzinfo=timezone.utc)  # Still the same Pacific day.
    engine.fallbacks[0].deferred_until = None
    assert engine.reserve(PAYLOAD).lane.provider == "groq"
    assert inbox.document(GROQ_PATH)["day"] == "2026-11-12" and inbox.document(STATE_PATH)["day"] == "2026-11-11"


def test_rejections_pause_only_their_own_ledger_for_a_bounded_time():
    engine, inbox, clock = ready()
    lane = engine.fallbacks[0]
    assert lane.budget.record_429(retry_after=5) == clock.now + timedelta(seconds=60)
    clock.advance(61)
    assert lane.budget.record_429(retry_after=1800) == clock.now + timedelta(seconds=1800)
    clock.advance(1801)
    assert lane.budget.record_429(retry_after=999_999) == datetime(2026, 11, 12, tzinfo=timezone.utc)
    assert engine.primary.snapshot()["cooldown_until"] is None
    assert engine.primary.record_429(retry_after=5) == next_pacific_midnight(clock.now)
    clock.now = datetime(2026, 11, 11, 23, 50, tzinfo=timezone.utc)
    assert lane.budget.record_429(retry_after=3600) == datetime(2026, 11, 12, tzinfo=timezone.utc)
    assert lane.budget.snapshot()["cooldown_until"] == "2026-11-12T00:00:00+00:00"


def test_gemini_rejection_is_persisted_and_the_same_request_continues_on_groq():
    engine, inbox, clock = ready()
    engine.set_request_limit(4)
    session = Session([Response(status=429), chat_answer('{"answer":1}')])
    result = _request(PAYLOAD, api_key=GEMINI_KEY, model=MODEL, session=session, budget=engine)
    assert served_by(result, MODEL) == ("groq", GROQ_MODEL)
    assert result["candidates"] == [{"finishReason": "STOP", "content": {"parts": [{"text": '{"answer":1}'}]}}]
    assert result["usageMetadata"] == {"promptTokenCount": 11, "candidatesTokenCount": 3, "totalTokenCount": 14}
    assert inbox.document(STATE_PATH)["cooldown_until"] == next_pacific_midnight(clock.now).isoformat()
    (google_url, google), (groq_url, groq) = session.calls
    assert google_url.startswith("https://generativelanguage.googleapis.com/") and groq_url == ENDPOINTS["groq"]
    assert google["headers"] == {"X-goog-api-key": GEMINI_KEY, "Content-Type": "application/json"}
    assert groq["headers"] == {"Authorization": "Bearer " + GROQ_KEY, "Content-Type": "application/json"}
    assert GEMINI_KEY not in json.dumps(groq, default=str) and GROQ_KEY not in json.dumps(google, default=str)
    assert google["allow_redirects"] is groq["allow_redirects"] is False
    body = groq["json"]
    assert body["model"] == GROQ_MODEL and body["stream"] is False and body["max_tokens"] == 256 and body["temperature"] == 0
    assert body["messages"] == [{"role": "system", "content": "Instrucción sintética."},
                                {"role": "user", "content": "{\"untrusted\":\"dato ficticio\"}"}]
    assert body["response_format"] == {"type": "json_schema", "json_schema": {"name": "mdh_response", "schema": {"type": "object"}}}
    assert engine.requests_this_run == 2
    assert [lane.report(clock.now)["state"] for lane in engine.lanes] == ["paused", "open"]


def test_fallback_rejection_without_another_lane_defers_instead_of_looping():
    engine, inbox, clock = ready()
    engine.primary.record_429()
    session = Session([Response(status=429, headers={"Retry-After": "120"})])
    with pytest.raises(QuotaDeferred) as deferred:
        _request(PAYLOAD, api_key=GEMINI_KEY, model=MODEL, session=session, budget=engine)
    assert deferred.value.reason == "provider_cooldown" and len(session.calls) == 1
    assert inbox.document(GROQ_PATH)["cooldown_until"] == (clock.now + timedelta(seconds=120)).isoformat()
    with pytest.raises(QuotaDeferred):
        engine.reserve(PAYLOAD)


@pytest.mark.parametrize("status,closed", [(401, "provider_http_401"), (403, "provider_http_403"), (400, None), (503, None)])
def test_a_failing_fallback_steps_aside_for_gemini_and_bad_credentials_close_it(status, closed, monkeypatch):
    monkeypatch.setattr(gemini_draft.time, "sleep", lambda _: pytest.fail("switching lanes must not sleep"))
    engine, _, _ = ready(gemini=gemini_config(rpm=1))
    engine.reserve(PAYLOAD)  # Fills the Gemini minute, so the next request starts on Groq.
    session = Session([Response(status=status), gemini_answer()])
    session.before_first = lambda: setattr(engine.primary, "config", gemini_config(rpm=2))
    result = _request(PAYLOAD, api_key=GEMINI_KEY, model=MODEL, session=session, budget=engine)
    assert served_by(result, MODEL) == ("gemini", MODEL) and SERVED_BY not in result
    assert [url == ENDPOINTS["groq"] for url, _ in session.calls] == [True, False]
    assert engine.fallbacks[0].closed == closed


def test_gemini_server_error_moves_to_the_fallback_without_spending_retry_delays(monkeypatch):
    monkeypatch.setattr(gemini_draft.time, "sleep", lambda _: pytest.fail("switching lanes must not sleep"))
    engine, _, _ = ready()
    session = Session([Response(status=503), chat_answer()])
    assert served_by(_request(PAYLOAD, api_key=GEMINI_KEY, model=MODEL, session=session, budget=engine), MODEL)[0] == "groq"
    assert engine.lanes[0].closed is None


def test_configuration_error_of_the_only_open_lane_is_reported_not_retried():
    engine, _, _ = ready()
    engine.primary.record_429()
    session = Session([Response(status=404)])
    with pytest.raises(GeminiDraftError) as error:
        _request(PAYLOAD, api_key=GEMINI_KEY, model=MODEL, session=session, budget=engine)
    assert (error.value.code, error.value.status_code) == ("provider_http_error", 404) and len(session.calls) == 1


def test_translation_modes_and_untrusted_completions():
    budget = GeminiBudget(Inbox(), BudgetConfig(**groq_document(provider="nvidia", model="meta/llama-3.3-70b-instruct")))
    nvidia = Lane(budget, api_key=GROQ_KEY, response_mode="guided_json").encode(PAYLOAD)
    assert nvidia["nvext"] == {"guided_json": {"type": "object"}} and "response_format" not in nvidia
    assert Lane(budget, api_key=GROQ_KEY, response_mode="json_object").encode(PAYLOAD)["response_format"] == {"type": "json_object"}
    lane = Lane(GeminiBudget(Inbox(), BudgetConfig(**groq_document())), api_key=GROQ_KEY, response_mode="json_schema")
    for document in (None, [], {}, {"choices": "x"}, {"choices": [None]}, {"choices": [{"message": {"content": 5}}]}):
        decoded = lane.decode(document)
        assert decoded[SERVED_BY] == {"provider": "groq", "model": GROQ_MODEL}
        assert all(not row["content"]["parts"] or row["finishReason"] != "STOP" for row in decoded["candidates"])
    truncated = lane.decode({"choices": [{"finish_reason": "length", "message": {"content": "{"}}],
                             "usage": {"prompt_tokens": -1, "total_tokens": "9"}, "model": "not a model"})
    assert truncated["candidates"][0]["finishReason"] == "MAX_TOKENS" and truncated["usageMetadata"] == {}
    assert truncated[SERVED_BY]["model"] == GROQ_MODEL
    routed = Lane(GeminiBudget(Inbox(), BudgetConfig(**groq_document(provider="openrouter", model="openrouter/free"))),
                  api_key=GROQ_KEY).decode({"choices": [], "model": "meta-llama/llama-4-scout:free"})
    assert routed[SERVED_BY] == {"provider": "openrouter", "model": "meta-llama/llama-4-scout:free"}


def test_a_gemini_body_cannot_claim_another_provider():
    engine, _, _ = ready()
    spoof = gemini_answer(**{SERVED_BY: {"provider": "groq", "model": GROQ_MODEL}})
    result = _request(PAYLOAD, api_key=GEMINI_KEY, model=MODEL, session=Session([spoof]), budget=engine)
    assert served_by(result, MODEL) == ("gemini", MODEL)
    for forged in ({SERVED_BY: {"provider": "xai", "model": "grok-4.7"}}, {SERVED_BY: "groq"}, None, []):
        assert served_by(forged, MODEL) == ("gemini", MODEL)


def test_scope_classified_on_a_fallback_lane_is_published_under_its_own_name():
    from mdh_pipeline.news_scope import classify_scope_batch
    from mdh_pipeline.news_selection import _decision, validate_scope_review
    from test_news_scope import assessment, item
    engine, _, _ = ready()
    engine.primary.record_429()
    entry = item()
    answer = chat_answer(json.dumps({"assessments": [assessment(entry)]}))
    review = classify_scope_batch([entry], api_key=GEMINI_KEY, model=MODEL, budget=engine, request_session=Session([answer]))[0]
    assert review["extraction"]["provider"] == "groq" and review["extraction"]["model"] == GROQ_MODEL
    validate_scope_review(review, reference=entry["reference"], content_sha256=entry["article"]["content_sha256"])
    selection = _decision(review["classification"], review["extraction"], review["evidence"],
                          fetched_at=review["source_read"]["fetched_at"])["selection"]
    assert (selection["method"], selection["provider"], selection["model"]) == ("llm_grounded_scope", "groq", GROQ_MODEL)
    wrapped = chat_answer("```json\n" + json.dumps({"assessments": [assessment(entry)]}) + "\n```")
    with pytest.raises(GeminiDraftError, match="invalid_json_response"):
        classify_scope_batch([entry], api_key=GEMINI_KEY, model=MODEL, budget=engine, request_session=Session([wrapped]))


def test_probe_spends_one_reserved_request_and_prints_no_model_text():
    engine, inbox, _ = ready()
    session = Session([chat_answer('{"status":"ok"}'), chat_answer('{"status":"ok"}')])
    report = probe(engine, "groq", api_key=GEMINI_KEY, session=session)
    assert report == {"provider": "groq", "http_requests": 1, "finish_reason": "STOP",
                      "served_by": {"provider": "groq", "model": GROQ_MODEL}, "valid_json": True}
    assert len(session.calls) == 1 and inbox.document(STATE_PATH)["used_requests"] == 0
    engine, _, _ = ready()
    failed = probe(engine, "groq", api_key=GEMINI_KEY, session=Session([Response(status=400), chat_answer()]))
    assert failed == {"provider": "groq", "http_requests": 1, "valid_json": False,
                      "error_code": "provider_http_error", "status_code": 400}


def test_a_smaller_fallback_declines_what_does_not_fit_without_changing_batch_sizing():
    from mdh_pipeline.automatic_news_batch import batch_input_limit
    engine, inbox, _ = ready()
    assert batch_input_limit(engine) == 90_000
    engine.primary.record_429()
    assert batch_input_limit(engine) == 90_000
    with pytest.raises(QuotaDeferred) as deferred:
        engine.reserve({"contents": "x" * 70_000})
    assert deferred.value.reason == "provider_cooldown" and engine.requests_this_run == 0
    assert inbox.document(GROQ_PATH)["used_requests"] == 0 and engine.fallbacks[0].usable(engine.clock())
    assert engine.reserve(PAYLOAD).lane.provider == "groq"


def test_extraction_on_a_fallback_lane_records_the_lane_in_the_private_draft():
    import test_gemini_draft as fixtures
    from mdh_pipeline.contracts.drafts import validate_envelope
    engine, _, _ = ready()
    engine.primary.record_429()
    candidate, article = fixtures.inputs()
    wire = json.loads(fixtures.response().body)["candidates"][0]["content"]["parts"][0]["text"]
    draft = gemini_draft.extract_draft(candidate, article, api_key=GEMINI_KEY, model=MODEL,
                                       request_session=Session([chat_answer(wire)]), budget=engine)
    assert (draft["extraction"]["provider"], draft["extraction"]["model"]) == ("groq", GROQ_MODEL)
    assert draft["extraction"]["usage"] == {"prompt_tokens": 11, "candidate_tokens": 3, "total_tokens": 14}
    validate_envelope(draft, candidate)
    assert draft["status"] == "draft" and "Gemini" not in draft["record"]["source_check_method"]
    assert "(groq)" in draft["record"]["source_check_method"]
    direct = gemini_draft.extract_draft(candidate, article, api_key=GEMINI_KEY, model=MODEL,
                                        request_session=Session([fixtures.response()]))
    assert (direct["extraction"]["provider"], direct["extraction"]["model"]) == ("gemini", MODEL)
    assert "con Gemini;" in direct["record"]["source_check_method"]


def test_evaluation_on_a_fallback_lane_publishes_a_named_review():
    import test_automatic_news as fixtures
    from mdh_pipeline.automatic_news import evaluate_draft
    engine, _, _ = ready()
    engine.primary.record_429()
    document, reference, article = fixtures.inputs()
    result = evaluate_draft(document, reference, article, api_key=GEMINI_KEY, model=MODEL, budget=engine,
                            session=Session([chat_answer(json.dumps(fixtures.judgment()))]), now=fixtures.NOW)
    automation = result["dossier"]["automation"]
    assert (automation["method"], automation["evaluator_provider"], automation["evaluator_model"]) == (
        "llm_evidence_review", "groq", GROQ_MODEL)
    assert result["dossier"]["human_reviewed"] is False and result["usage"]["total_tokens"] == 14


def test_private_templates_expose_fallback_keys_only_where_the_gemini_key_already_is():
    from pathlib import Path
    import yaml
    folder = Path(__file__).resolve().parents[1] / "ops/review-queue/.github/workflows"
    secrets = {name: "${{ secrets." + name + " }}" for name in llm_engine.KEY_VARIABLES.values()}
    keyed = 0
    for path in sorted(folder.glob("*.yml")):
        document = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        for job in document["jobs"].values():
            for step in job["steps"]:
                env = step.get("env", {})
                if "GEMINI_API_KEY" in env:
                    keyed += 1
                    assert {name: env.get(name) for name in secrets} == secrets
                    assert env[CONFIG_VARIABLE] == "${{ vars." + CONFIG_VARIABLE + " }}"
                else:
                    assert not set(env) & (set(secrets) | {CONFIG_VARIABLE})
                    assert not any(name in step.get("run", "") for name in secrets)
    assert keyed == 4
    extract = yaml.load((folder / "extract.yml").read_text(), Loader=yaml.BaseLoader)
    env = next(step["env"] for step in extract["jobs"]["extract"]["steps"] if "GEMINI_API_KEY" in step.get("env", {}))
    for name in ("MDH_CYCLE_HOURS", "MDH_SCOPE_REQUESTS_PER_CYCLE", "MDH_SCOPE_BATCH_COMMITS", "MDH_SOURCE_FETCH_WORKERS"):
        assert env[name] == "${{ vars." + name + " }}"
