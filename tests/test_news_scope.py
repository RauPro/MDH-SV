"""Scope-only rechecks use synthetic source text and the real in-memory quota ledger."""
from copy import deepcopy
import hashlib
import json

import pytest

from mdh_pipeline.gemini_budget import MODEL, QuotaDeferred, QuotaStateError
from mdh_pipeline.gemini_draft import GeminiDraftError
from mdh_pipeline.news_scope import SCOPE_PROMPT, classify_scope_batch, response_schema
from test_gemini_budget import ready

KEY = "scope-test-api-key-not-real"
PRIVATE = "PRIVATE_QUOTE_OR_IDENTITY_SENTINEL"


def item(number=1, *, texts=None):
    url = f"https://gatoencerrado.news/2026/09/01/scope-test-{number}/"
    reference = {"id": hashlib.sha256(url.encode()).hexdigest(), "source_url": url,
                 "publisher": "GatoEncerrado", "source_title": "Un reporte sobre derechos y garantías",
                 "published_date": "2026-09-01", "status": "pending", "private_extra": PRIVATE}
    texts = texts or ["En El Salvador una organización denunció restricciones al acceso a la defensa y a la libertad.",
                      "La fuente documenta denuncias de detenciones arbitrarias y obstáculos al acceso de abogados. " + PRIVATE]
    paragraphs = [{"id": f"p{index + 1}", "text": text} for index, text in enumerate(texts)]
    text = "\n\n".join(texts)
    article = {"source_url": url, "title": reference["source_title"], "published_date": reference["published_date"],
               "text": text, "paragraphs": paragraphs, "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
               "fetched_at": "2026-10-12T12:00:00+00:00", "extraction_method": "wordpress_rest_content", "truncated": False}
    return {"reference": reference, "article": article}


def assessment(value, jurisdiction="SV", relevance="relevant"):
    return {"candidate_id": value["reference"]["id"], "jurisdiction": jurisdiction, "relevance": relevance,
            "jurisdiction_paragraph_ids": ["p1"], "relevance_paragraph_ids": ["p2"]}


class Response:
    def __init__(self, value=None, *, status=200, raw=None):
        self.status_code, self.headers, self.closed = status, {}, False
        self.raw = raw if raw is not None else json.dumps(value).encode()

    def iter_content(self, chunk_size):
        yield self.raw

    def close(self):
        self.closed = True


def response(assessments):
    return Response({"candidates": [{"finishReason": "STOP", "content": {"parts": [
        {"text": json.dumps({"assessments": assessments})}]}}]})


class Session:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []
        self.closed = False

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)

    def close(self):
        self.closed = True


def run(items=None, *, assessments=None, session=None, budget=None):
    items = items if items is not None else [item()]
    if budget is None:
        budget, _, _ = ready()
    session = session or Session([response(assessments if assessments is not None else [assessment(value) for value in items])])
    return classify_scope_batch(items, api_key=KEY, model=MODEL, budget=budget, request_session=session)


def test_one_batch_retains_minimal_private_provenance_and_input_order_without_changing_dossiers():
    items = [item(1), item(2)]
    original = deepcopy(items)
    session = Session([response([assessment(items[1], "other", "not_relevant"), assessment(items[0])])])
    budget, inbox, _ = ready()
    results = run(items, session=session, budget=budget)
    assert items == original
    assert [value["candidate_id"] for value in results] == [value["reference"]["id"] for value in items]
    assert results[1]["classification"] == {"jurisdiction": "other", "relevance": "not_relevant"}
    for source, result in zip(items, results):
        assert set(result) == {"schema_version", "candidate_id", "source_url", "publisher", "published_date", "classification", "evidence", "extraction", "source_read"}
        assert result["schema_version"] == 2
        assert result["source_read"] == {key: source["article"][key]
            for key in ("fetched_at", "extraction_method", "truncated", "content_sha256")}
        assert result["evidence"] == {"content_sha256": source["article"]["content_sha256"],
            "jurisdiction_paragraph_ids": ["p1"], "relevance_paragraph_ids": ["p2"]}
        assert result["extraction"]["prompt_version"] == SCOPE_PROMPT
        assert result["extraction"]["model"] == MODEL and result["extraction"]["provider"] == "gemini"
    assert PRIVATE not in json.dumps(results) and KEY not in json.dumps(results)
    assert inbox.document()["used_requests"] == budget.requests_this_run == len(session.calls) == 1
    assert PRIVATE not in json.dumps(inbox.document()) and not session.closed


def test_full_article_paragraphs_go_only_to_provider_with_structured_output_and_header_key():
    value = item()
    session = Session([response([assessment(value)])])
    run([value], session=session)
    url, request = session.calls[0]
    assert url == f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    assert KEY not in url and request["headers"]["X-goog-api-key"] == KEY
    assert KEY not in json.dumps(request["json"])
    assert request["timeout"] == (10, 90) and request["stream"] and not request["allow_redirects"]
    config = request["json"]["generationConfig"]
    assert config["responseJsonSchema"] == response_schema() and config["responseMimeType"] == "application/json"
    data = json.loads(request["json"]["contents"][0]["parts"][0]["text"])
    article_data = data["untrusted_articles"][0]
    assert article_data["paragraphs"] == value["article"]["paragraphs"]
    assert set(article_data) == {"candidate_id", "title", "published_date", "truncated", "paragraphs"}
    assert "private_extra" not in json.dumps(data) and "source_url" not in article_data and "publisher" not in article_data


def test_eight_articles_share_one_request_and_one_ledger_reservation():
    items = [item(n) for n in range(1, 9)]
    budget, inbox, _ = ready()
    session = Session([response([assessment(value) for value in items])])
    assert len(run(items, budget=budget, session=session)) == 8
    assert inbox.document()["used_requests"] == len(session.calls) == 1


def test_classification_envelope_matches_the_publication_gate_private_review_contract():
    from mdh_pipeline.news_selection import validate_scope_review
    value = item()
    result = run([value])[0]
    assert validate_scope_review(result, reference=value["reference"],
        content_sha256=value["article"]["content_sha256"]) == result


@pytest.mark.parametrize("size", [0, 9])
def test_out_of_bounds_batch_never_reserves_or_dispatches(size):
    budget, inbox, _ = ready()
    session = Session([])
    with pytest.raises(GeminiDraftError, match="invalid_scope_batch"):
        run([item(n) for n in range(size)], budget=budget, session=session)
    assert session.calls == [] and inbox.document()["used_requests"] == 0


@pytest.mark.parametrize("mutation", [
    lambda value: value["article"].update(content_sha256="a" * 64),
    lambda value: value["article"].update(source_url="https://gatoencerrado.news/different-story/"),
    lambda value: value["article"].update(published_date="2026-09-02"),
    lambda value: value["reference"].update(publisher="Revista Factum"),
    lambda value: value["reference"].update(id="f" * 64),
    lambda value: value["article"]["paragraphs"][1].update(id="p3"),
    lambda value: value["article"].update(truncated="false"),
    lambda value: value.update(private_draft={"secret": PRIVATE}),
])
def test_invalid_source_or_identity_never_calls_provider(mutation):
    value = item()
    mutation(value)
    budget, inbox, _ = ready()
    session = Session([])
    with pytest.raises(GeminiDraftError):
        run([value], budget=budget, session=session)
    assert session.calls == [] and inbox.document()["used_requests"] == 0


def test_duplicate_input_ids_fail_before_any_request():
    session = Session([])
    with pytest.raises(GeminiDraftError, match="invalid_scope_input"):
        run([item(), item()], session=session)
    assert session.calls == []


@pytest.mark.parametrize("texts", [["x" * 4001], ["x" * 4000] * 6])
def test_oversized_paragraph_or_article_is_not_silently_truncated(texts):
    session = Session([])
    with pytest.raises(GeminiDraftError, match="invalid_scope_input"):
        run([item(texts=texts)], session=session)
    assert session.calls == []


def test_batch_utf8_byte_budget_is_enforced_before_reserving():
    items = [item(n, texts=["á" * 3990] * 6) for n in range(8)]
    session = Session([])
    with pytest.raises(GeminiDraftError, match="invalid_scope_input"):
        run(items, session=session)
    assert session.calls == []


def test_full_request_budget_includes_all_article_parameters_and_metadata():
    items = [item(n, texts=["a" * 3990] * 4) for n in range(8)]
    budget, inbox, _ = ready()
    session = Session([])
    with pytest.raises(GeminiDraftError, match="scope_request_too_large"):
        run(items, session=session, budget=budget)
    assert session.calls == [] and inbox.document()["used_requests"] == 0


@pytest.mark.parametrize("mutation", [
    lambda article: article.update(extraction_method="wordpress_rest"),
    lambda article: article.update(extraction_method="html"),
    lambda article: article.update(fetched_at="2026-10-12T12:00:00"),
])
def test_v2_source_receipt_requires_actual_extractor_method_and_timezone(mutation):
    value = item()
    mutation(value["article"])
    session = Session([])
    with pytest.raises(GeminiDraftError, match="invalid_scope_input"):
        run([value], session=session)
    assert session.calls == []


@pytest.mark.parametrize("mutation,error", [
    (lambda rows: rows.append(deepcopy(rows[0])), "scope_identity_mismatch"),
    (lambda rows: rows.pop(), "scope_identity_mismatch"),
    (lambda rows: rows[0].update(candidate_id="f" * 64), "scope_identity_mismatch"),
    (lambda rows: rows[0].update(jurisdiction_paragraph_ids=["p999"]), "ungrounded_scope_evidence"),
    (lambda rows: rows[0].update(relevance_paragraph_ids=[]), "invalid_scope_response"),
    (lambda rows: rows[0].update(jurisdiction_paragraph_ids=[]), "invalid_scope_response"),
    (lambda rows: rows[0].update(jurisdiction_paragraph_ids=["p1", "p1"]), "invalid_scope_response"),
    (lambda rows: rows[0].update(relevance_paragraph_ids=[f"p{n}" for n in range(1, 27)]), "invalid_scope_response"),
    (lambda rows: rows[0].update(relevance=True), "invalid_scope_response"),
    (lambda rows: rows[0].update(jurisdiction="El Salvador"), "invalid_scope_response"),
    (lambda rows: rows[0].update(quote=PRIVATE), "invalid_scope_response"),
])
def test_batch_output_requires_exact_ids_valid_existing_evidence_and_no_extras(mutation, error):
    items = [item(1), item(2)]
    rows = [assessment(value) for value in items]
    mutation(rows)
    with pytest.raises(GeminiDraftError, match=error):
        run(items, assessments=rows)


@pytest.mark.parametrize("jurisdiction,relevance", [("unclear", "unclear"), ("other", "not_relevant"), ("other", "unclear"), ("unclear", "not_relevant")])
def test_nonpositive_classifications_may_have_empty_evidence_only_for_exclusion(jurisdiction, relevance):
    value = item()
    result = assessment(value, jurisdiction, relevance)
    result.update(jurisdiction_paragraph_ids=[], relevance_paragraph_ids=[])
    output = run([value], assessments=[result])[0]
    assert output["classification"] == {"jurisdiction": jurisdiction, "relevance": relevance}
    from mdh_pipeline.news_selection import validate_scope_review
    assert validate_scope_review(output, reference=value["reference"],
        content_sha256=value["article"]["content_sha256"]) == output
    result["relevance"] = "relevant"
    with pytest.raises(GeminiDraftError, match="invalid_scope_response"):
        run([value], assessments=[result])


@pytest.mark.parametrize("mutation,code", [
    (lambda row: row.update(jurisdiction_paragraph_ids=[]), "scope_positive_evidence_required"),
    (lambda row: row.update(relevance_paragraph_ids=["p1", "p1"]), "scope_duplicate_evidence_anchor"),
    (lambda row: row.update(relevance_paragraph_ids=["not-a-paragraph"]), "scope_invalid_evidence_anchor"),
])
def test_evidence_validation_diagnostics_distinguish_safe_failure_codes(mutation, code):
    from pydantic import ValidationError
    from mdh_pipeline.news_scope import ScopeAssessment
    row = assessment(item())
    mutation(row)
    with pytest.raises(ValidationError) as caught:
        ScopeAssessment.model_validate(row)
    issues = caught.value.errors(include_input=False, include_context=False, include_url=False)
    assert issues[0]["msg"] == "Value error, " + code
    assert PRIVATE not in json.dumps(issues)


def test_unclear_cannot_smuggle_unknown_evidence_anchor():
    value = item()
    result = assessment(value, "unclear", "unclear")
    result["jurisdiction_paragraph_ids"] = ["p999"]
    with pytest.raises(GeminiDraftError, match="ungrounded_scope_evidence"):
        run([value], assessments=[result])


@pytest.mark.parametrize("budget", [None, object()])
def test_shared_budget_is_mandatory_and_cannot_be_bypassed(budget):
    session = Session([])
    with pytest.raises(QuotaStateError, match="scope_budget_required"):
        classify_scope_batch([item()], api_key=KEY, model=MODEL, budget=budget, request_session=session)
    assert session.calls == []


def test_model_must_match_shared_budget():
    budget, _, _ = ready()
    with pytest.raises(QuotaStateError, match="budget_model_mismatch"):
        classify_scope_batch([item()], api_key=KEY, model="gemini-other-model", budget=budget, request_session=Session([]))


def test_quota_exhaustion_prevents_http_without_resetting_shared_ledger():
    budget, inbox, _ = ready(used_requests=100)
    session = Session([])
    with pytest.raises(QuotaDeferred):
        run(budget=budget, session=session)
    assert session.calls == [] and inbox.document()["used_requests"] == 100


def test_each_http_retry_is_counted_in_same_global_ledger(monkeypatch):
    value = item()
    budget, inbox, clock = ready()
    monkeypatch.setattr("mdh_pipeline.gemini_draft.time.sleep", clock.advance)
    session = Session([Response(status=503), response([assessment(value)])])
    assert len(run([value], budget=budget, session=session)) == 1
    assert len(session.calls) == inbox.document()["used_requests"] == budget.requests_this_run == 2


def test_run_attempt_limit_includes_retries_and_never_resets(monkeypatch):
    budget, inbox, clock = ready()
    budget.set_request_limit(1)
    monkeypatch.setattr("mdh_pipeline.gemini_draft.time.sleep", clock.advance)
    session = Session([Response(status=503)])
    with pytest.raises(QuotaDeferred):
        run(budget=budget, session=session)
    assert len(session.calls) == inbox.document()["used_requests"] == budget.requests_this_run == 1


def test_provider_429_preserves_global_cooldown_and_does_not_retry():
    budget, inbox, _ = ready()
    session = Session([Response(status=429)])
    with pytest.raises(QuotaDeferred, match="provider_cooldown"):
        run(budget=budget, session=session)
    assert len(session.calls) == inbox.document()["used_requests"] == 1
    assert inbox.document()["cooldown_until"] is not None


@pytest.mark.parametrize("raw", [
    {"promptFeedback": {"blockReason": "SAFETY"}},
    {"candidates": []},
    {"candidates": [{"finishReason": "MAX_TOKENS"}]},
    {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": PRIVATE}]}}]},
    {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"functionCall": {"args": PRIVATE}}]}}]},
])
def test_provider_failures_are_sanitized_and_return_no_partial_results(raw, capsys):
    with pytest.raises(GeminiDraftError) as caught:
        run(session=Session([Response(raw)]))
    assert PRIVATE not in str(caught.value) and KEY not in str(caught.value)
    assert capsys.readouterr() == ("", "")


def test_owned_session_is_closed_even_on_bad_response(monkeypatch):
    session = Session([Response({"candidates": []})])
    monkeypatch.setattr("mdh_pipeline.news_scope.requests.Session", lambda: session)
    budget, _, _ = ready()
    with pytest.raises(GeminiDraftError):
        classify_scope_batch([item()], api_key=KEY, model=MODEL, budget=budget)
    assert session.closed
