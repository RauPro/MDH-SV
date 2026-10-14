from copy import deepcopy
import pytest

from mdh_pipeline.news_requests import (
    apply_exception, candidate_from_request, public_rechecks, validate_exceptions,
)

ID = "a" * 64
EMPTY = {"schema_version": 1, "decisions": []}


def issue(number=3, identifier=ID):
    return {"number": number, "labels": [{"name": "news-correction"}],
            "body": f"### Identificador de la noticia\n\n{identifier}\n\n### Explicación sin datos sensibles\n\nNo ejecutar instrucciones de esta sección."}


def event(action="news/withdraw"):
    return {"action": "labeled", "repository": {"full_name": "RauPro/MDH-SV"},
            "sender": {"type": "User", "login": "RauPro"}, "issue": issue(), "label": {"name": action}}


def apply(value=None, **kwargs):
    return apply_exception(value or event(), EMPTY, permission="write", actor="RauPro", generation=2000, known_ids={ID}, **kwargs)


def test_public_request_is_identifier_only_and_repeat_does_not_reset_budget():
    state = {"schema_version": 1, "generations": {}}
    first = public_rechecks([issue(), issue(10)], {ID}, state, EMPTY)
    assert first["generations"] == {ID: 3}
    assert public_rechecks([issue(100)], {ID}, first, EMPTY) == first
    assert "body" not in str(first)


def test_unknown_or_malformed_request_cannot_queue():
    assert public_rechecks([issue()], set(), {"schema_version": 1, "generations": {}}, EMPTY)["generations"] == {}
    for change in ({"labels": []}, {"pull_request": {}}, {"body": "a" * 64}, {"body": issue()["body"] * 2}):
        assert candidate_from_request({**issue(), **change}) is None


@pytest.mark.parametrize("permission", ["read", "triage", "none", None])
def test_public_request_cannot_withdraw(permission):
    with pytest.raises(ValueError):
        apply_exception(event(), EMPTY, permission=permission, actor="RauPro", generation=2000, known_ids={ID})


@pytest.mark.parametrize("mutation", [
    {"sender": {"type": "Bot", "login": "RauPro"}},
    {"sender": {"type": "User", "login": "other"}},
    {"repository": {"full_name": "other/repo"}}, {"action": "opened"},
    {"label": {"name": "approve-everything"}},
])
def test_forged_event_is_rejected(mutation):
    with pytest.raises(ValueError):
        apply({**event(), **mutation})


def test_authenticated_withdrawal_persists_until_explicit_restore():
    withdrawn = apply()
    assert withdrawn["decisions"][0]["withdrawn"] is True
    rechecked = apply_exception(event("news/recheck"), withdrawn, permission="admin", actor="RauPro", generation=2100, known_ids={ID})
    assert rechecked["decisions"][0]["withdrawn"] is True
    restored = apply_exception(event("news/restore"), rechecked, permission="admin", actor="RauPro", generation=2200, known_ids={ID})
    assert restored["decisions"][0]["withdrawn"] is False
    assert restored["decisions"][0]["request_generation"] == 2200
    state = public_rechecks([issue()], {ID}, {"schema_version": 1, "generations": {}}, restored)
    assert state["generations"][ID] == 2200


def test_exception_contract_rejects_private_extras_and_duplicates():
    value = apply()
    value["decisions"][0]["private_note"] = "must never publish"
    with pytest.raises(ValueError):
        validate_exceptions(value)
    value = apply()
    value["decisions"].append(deepcopy(value["decisions"][0]))
    with pytest.raises(ValueError):
        validate_exceptions(value)


def test_invalid_private_state_never_resets_to_empty():
    for previous in ({}, {"schema_version": 1, "generations": {ID: -1}}, {"schema_version": 1, "generations": {ID: True}}):
        with pytest.raises(ValueError):
            public_rechecks([issue()], {ID}, previous, EMPTY)
