"""Batch transport must retain every single-dossier evidence/privacy boundary."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json

import pytest

import mdh_pipeline.automatic_news_batch as batch
from mdh_pipeline.automatic_news import _receipt, project_collection
from mdh_pipeline.gemini_budget import QuotaDeferred, estimate_input_tokens
from mdh_pipeline.news_dossier import FIELD_NAMES
from test_automatic_news import (
    Budget as SingleBudget, KEY, MODEL, NOW, Response, archive, check, inputs, judgment,
)


class Budget(SingleBudget):
    def __init__(self, input_tpm=250_000):
        super().__init__()
        self.config.input_tpm = input_tpm
        self.reservations = []

    def reserve(self, payload):
        value = super().reserve(payload)
        self.reservations.append(deepcopy(payload))
        return value

    def record_429(self):
        self.cooldown = NOW + timedelta(hours=1)
        return self.cooldown


def items(count=4):
    values = []
    for number in range(1, count + 1):
        document, reference, article = inputs(number, extra=f"La organización publicó el comunicado número {number}.")
        values.append({"document": document, "reference": reference, "article": article})
    return values


def response_for(rows):
    assessments = []
    for index, row in enumerate(rows, 1):
        value = judgment()
        for field in [value["jurisdiction"], value["relevance"], *value["fields"]]:
            field["paragraph_ids"] = [f"n{index}-" + ref for ref in field["paragraph_ids"]]
        assessments.append({"candidate_id": row["reference"]["id"],
                            "source_content_sha256": row["article"]["content_sha256"], "judgment": value})
    return {"assessments": assessments}


class Session:
    def __init__(self, value, *, statuses=None, parts=None, finish_reason="STOP"):
        self.value = value
        self.statuses = list(statuses or [200])
        self.parts = parts
        self.finish_reason = finish_reason
        self.calls = []

    def post(self, endpoint, **kwargs):
        self.calls.append(kwargs)
        response = Response(self.value)
        response.status_code = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        response.body = json.dumps({"candidates": [{"finishReason": self.finish_reason, "content": {
            "parts": self.parts if self.parts is not None else [{"text": json.dumps(self.value)}],
        }}], "usageMetadata": {"promptTokenCount": 400, "candidatesTokenCount": 300}}).encode()
        return response


def evaluate(rows, *, verdict=None, budget=None, session=None):
    return batch.evaluate_batch(rows, api_key=KEY, model=MODEL, budget=budget or Budget(),
                                session=session or Session(verdict or response_for(rows)), now=NOW)


def test_four_independent_dossiers_use_one_existing_reservation_and_no_fake_usage():
    rows = items()
    original = deepcopy(rows)
    budget = Budget()
    session = Session(response_for(rows))
    results = evaluate(rows, budget=budget, session=session)
    assert len(results) == 4
    assert rows == original
    assert len(session.calls) == budget.requests_this_run == len(budget.reservations) == 1
    payload = session.calls[0]["json"]
    assert budget.reservations == [payload]
    assert payload["generationConfig"]["candidateCount"] == 1
    assert payload["generationConfig"]["maxOutputTokens"] == 8192
    wire = json.loads(payload["contents"][0]["parts"][0]["text"])["assessments_input"]
    for index, (row, sent) in enumerate(zip(rows, wire), 1):
        result = results[row["reference"]["id"]]
        assert result["usage"] is None
        assert result["dossier"]["automation"]["source_content_sha256"] == row["article"]["content_sha256"]
        assert result["dossier"]["human_reviewed"] is False
        assert result["judgment"]["jurisdiction"]["paragraph_ids"] == ["p1"]
        assert result["dossier"]["fields"]["summary"]["evidence_refs"] == ["article-p1"]
        assert sent["untrusted_article_data"]["paragraphs"][0]["id"] == f"n{index}-p1"
        assert sent["untrusted_dossier"]["fields"]["summary"]["evidence_refs"] == [f"n{index}-p1"]
        assert all(set(field) == {"value", "status", "evidence_refs"}
                   for field in sent["untrusted_dossier"]["fields"].values())
        assert "\n\n".join(p["text"] for p in sent["untrusted_article_data"]["paragraphs"]) == row["article"]["text"]
        assert "paragraphs" not in json.dumps(result)


def test_single_dossier_uses_smaller_output_allowance():
    prepared, payload = batch.prepare_evaluation_batch(items(1))
    assert len(prepared) == 1
    assert payload["generationConfig"]["maxOutputTokens"] == 4096


def test_input_bound_counts_complete_serialized_payload_not_just_article():
    rows = items(1)
    _, payload = batch.prepare_evaluation_batch(rows)
    exact = estimate_input_tokens(payload)
    assert exact > len(rows[0]["article"]["text"].encode())
    batch.prepare_evaluation_batch(rows, max_input_tokens=exact)
    with pytest.raises(batch.AutoBatchError, match="automatic_batch_input_too_large"):
        batch.prepare_evaluation_batch(rows, max_input_tokens=exact - 1)


def test_large_articles_require_smaller_batch_and_are_never_truncated():
    rows = items()
    for row in rows:
        article = row["article"]
        article["paragraphs"][2]["text"] += " Texto preservado." * 1500
        article["text"] = "\n\n".join(p["text"] for p in article["paragraphs"])
        digest = hashlib.sha256(article["text"].encode()).hexdigest()
        article["content_sha256"] = digest
        row["document"]["extraction"]["content_sha256"] = digest
        row["reference"]["selection"]["evidence"]["content_sha256"] = digest
    before = deepcopy(rows)
    session, budget = Session(response_for(rows)), Budget()
    with pytest.raises(batch.AutoBatchError, match="automatic_batch_input_too_large"):
        evaluate(rows, session=session, budget=budget)
    assert rows == before and session.calls == [] and budget.requests_this_run == 0
    prepared, payload = batch.prepare_evaluation_batch(rows[:2])
    assert len(prepared) == 2 and estimate_input_tokens(payload) <= 90_000


def test_effective_bound_leaves_minute_ledger_headroom_before_calling_provider():
    rows = items(1)
    assert batch.batch_input_limit(Budget(20_000)) == 18_976
    assert batch.batch_input_limit(Budget()) == 90_000
    session, budget = Session(response_for(rows)), Budget(2_048)
    with pytest.raises(batch.AutoBatchError, match="automatic_batch_input_too_large"):
        evaluate(rows, session=session, budget=budget)
    assert session.calls == [] and budget.requests_this_run == 0


@pytest.mark.parametrize("count", [0, 5])
def test_batch_count_is_bounded_before_call(count):
    with pytest.raises(batch.AutoBatchError, match="invalid_automatic_batch_input"):
        batch.prepare_evaluation_batch(items(count))


@pytest.mark.parametrize("ceiling", [True, 0, -1, 90_001, 90_000.0])
def test_invalid_preparation_limits_are_not_accepted(ceiling):
    with pytest.raises(batch.AutoBatchError, match="invalid_automatic_batch_input"):
        batch.prepare_evaluation_batch(items(1), max_input_tokens=ceiling)


@pytest.mark.parametrize("mutation", [
    lambda rows: rows.append(deepcopy(rows[0])),
    lambda rows: rows[0].update(extra="unexpected"),
    lambda rows: rows[0]["reference"]["selection"]["evidence"].update(content_sha256="f" * 64),
    lambda rows: rows[0]["article"]["paragraphs"].pop(),
])
def test_invalid_or_incomplete_article_input_spends_no_request(mutation):
    rows = items(1)
    mutation(rows)
    session, budget = Session({}), Budget()
    with pytest.raises(batch.AutomaticNewsError):
        evaluate(rows, session=session, budget=budget)
    assert session.calls == [] and budget.requests_this_run == 0


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(extra="unexpected"),
    lambda r: r["assessments"].pop(),
    lambda r: r["assessments"].append(deepcopy(r["assessments"][0])),
    lambda r: r["assessments"][1].update(candidate_id=r["assessments"][0]["candidate_id"]),
    lambda r: r["assessments"][1].update(candidate_id="f" * 64),
    lambda r: r["assessments"][1].update(source_content_sha256=r["assessments"][0]["source_content_sha256"]),
    lambda r: r["assessments"][1].update(extra="unexpected"),
    lambda r: r["assessments"][1].pop("source_content_sha256"),
    lambda r: r["assessments"][1]["judgment"].update(extra="unexpected"),
    lambda r: r["assessments"][1]["judgment"]["jurisdiction"].update(paragraph_ids=["n1-p1"]),
    lambda r: check(r["assessments"][1]["judgment"], "summary").update(paragraph_ids=["n1-p1"]),
    lambda r: check(r["assessments"][1]["judgment"], "summary").update(paragraph_ids=["p1"]),
    lambda r: check(r["assessments"][1]["judgment"], "summary").update(paragraph_ids=["n2-p999"]),
    lambda r: check(r["assessments"][1]["judgment"], "summary").update(paragraph_ids=["n2-p1", "n2-p1"]),
    lambda r: check(r["assessments"][1]["judgment"], "summary").update(quote="private quote"),
])
def test_bad_row_rejects_whole_batch_before_any_projection(monkeypatch, mutation):
    rows = items(2)
    response = response_for(rows)
    mutation(response)
    projected = []
    monkeypatch.setattr(batch, "project_dossier", lambda *args, **kwargs: projected.append(True))
    with pytest.raises(batch.AutoBatchError) as caught:
        evaluate(rows, verdict=response)
    assert caught.value.code == "invalid_automatic_batch_response"
    assert caught.value.recoverable is True
    assert projected == []


def test_return_order_does_not_rebind_candidate_or_article_evidence():
    rows = items(2)
    response = response_for(rows)
    response["assessments"].reverse()
    result = evaluate(rows, verdict=response)
    assert list(result) == [row["reference"]["id"] for row in rows]
    for row in rows:
        assert result[row["reference"]["id"]]["dossier"]["automation"]["source_content_sha256"] == row["article"]["content_sha256"]


def test_sparse_fields_remain_pending_and_cannot_gain_other_rows_verdicts():
    rows = items(2)
    response = response_for(rows)
    response["assessments"][0]["judgment"]["fields"] = [check(response["assessments"][0]["judgment"], "department")]
    result = evaluate(rows, verdict=response)
    first, second = (result[row["reference"]["id"]] for row in rows)
    assert first["dossier"]["fields"]["summary"]["status"] == "pending"
    assert first["dossier"]["fields"]["event_time"]["status"] == "pending"
    assert first["dossier"]["fields"]["department"]["status"] == "reported"
    assert len(first["judgment"]["fields"]) == len(FIELD_NAMES)
    assert check(first["judgment"], "summary")["verdict"] == "insufficient_evidence"
    assert second["dossier"]["fields"]["summary"]["status"] == "reported"


def test_local_privacy_is_isolated_per_article_and_canonical_export_still_matches():
    rows = items(2)
    document, reference, article = inputs(1, extra="María López, de 12 años, sufrió una detención cerca de su escuela.")
    document["news_dossier"]["fields"]["summary"]["value"] = "La organización denunció la detención de María López."
    rows[0] = {"document": document, "reference": reference, "article": article}
    response = response_for(rows)
    response["assessments"][1]["judgment"]["protected_fields"] = ["department"]
    results = evaluate(rows, verdict=response)
    first, second = (results[row["reference"]["id"]] for row in rows)
    assert first["dossier"]["fields"]["summary"]["status"] == "withheld"
    assert "summary" in first["judgment"]["protected_fields"]
    assert first["dossier"]["fields"]["department"]["value"] == "San Salvador"
    assert second["dossier"]["fields"]["summary"]["status"] == "reported"
    assert second["dossier"]["fields"]["department"]["status"] == "withheld"
    assert "María" not in json.dumps(results, ensure_ascii=False)
    receipts = {}
    for row in rows:
        identifier = row["reference"]["id"]
        receipt = _receipt(row["document"], row["reference"], 0, now=NOW)
        receipt.update(status="evaluated", attempts=1, **results[identifier])
        receipts[identifier] = receipt
    collection, _ = project_collection({row["reference"]["id"]: row["document"] for row in rows}, receipts,
                                       archive(*(row["reference"] for row in rows)))
    assert len(collection["dossiers"]) == 2


def test_truncated_article_never_supports_absence_even_with_supported_judgment():
    rows = items(2)
    rows[0]["article"]["truncated"] = True
    response = response_for(rows)
    for assessment in response["assessments"]:
        check(assessment["judgment"], "event_time").update(reason_code="not_in_source", paragraph_ids=[])
    result = evaluate(rows, verdict=response)
    assert result[rows[0]["reference"]["id"]]["dossier"]["fields"]["event_time"]["status"] == "pending"
    assert result[rows[1]["reference"]["id"]]["dossier"]["fields"]["event_time"]["status"] == "not_reported"


def test_provider_retry_reserves_each_http_attempt_for_the_entire_batch(monkeypatch):
    rows = items()
    session, budget = Session(response_for(rows), statuses=[503, 200]), Budget()
    monkeypatch.setattr("mdh_pipeline.gemini_draft.time.sleep", lambda delay: None)
    results = evaluate(rows, session=session, budget=budget)
    assert len(results) == 4
    assert len(session.calls) == len(budget.reservations) == budget.requests_this_run == 2
    assert budget.reservations[0] == budget.reservations[1]


def test_provider_429_stops_batch_without_retry_and_keeps_shared_cooldown():
    rows = items()
    session, budget = Session(response_for(rows), statuses=[429, 200]), Budget()
    with pytest.raises(QuotaDeferred, match="provider_cooldown"):
        evaluate(rows, session=session, budget=budget)
    assert len(session.calls) == budget.requests_this_run == 1
    assert budget.cooldown is not None


def test_thought_signature_and_thought_parts_are_discarded_for_batches():
    rows = items(2)
    value = response_for(rows)
    session = Session(value, parts=[{"text": "PRIVATE_THOUGHT_CANARY", "thought": True},
        {"text": json.dumps(value), "thoughtSignature": "OPAQUE_SIGNATURE_CANARY"}])
    result = evaluate(rows, session=session)
    assert len(result) == 2 and "CANARY" not in json.dumps(result)


@pytest.mark.parametrize("parts", [
    [], [{"text": "{}", "thought": "true"}], [{"text": "{}", "thoughtSignature": 123}],
    [{"text": "{}", "functionCall": {"name": "unsafe"}}], [{"text": "{}"}, {"text": "{}"}],
    [{"text": "{}", "thought": True}], [{"text": "{}", "thought": True}] * 9,
    [{"text": '{"assessments": [], "assessments": []}'}],
])
def test_transport_or_json_anomalies_fail_whole_batch(parts):
    rows = items(2)
    with pytest.raises(batch.AutoBatchError, match="invalid_automatic_batch_response"):
        evaluate(rows, session=Session(response_for(rows), parts=parts))


def test_output_truncation_is_recoverable_but_never_a_partial_result():
    rows = items(2)
    with pytest.raises(batch.AutoBatchError) as caught:
        evaluate(rows, session=Session(response_for(rows), finish_reason="MAX_TOKENS"))
    assert caught.value.code == "automatic_batch_response_incomplete"
    assert caught.value.recoverable


def test_wire_schema_uses_existing_live_provider_subset():
    forbidden = {"additionalProperties", "minItems", "maxItems", "minLength", "maxLength", "pattern", "$ref", "$defs"}
    def walk(node):
        if isinstance(node, dict):
            assert not set(node).intersection(forbidden)
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)
    walk(batch.batch_response_schema())
