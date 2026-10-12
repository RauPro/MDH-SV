"""Recover transient private GitHub reads/writes without duplicate model work."""
import base64
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests

from mdh_pipeline.review_queue import GitHubInbox, GitHubRequestError


def response(status, document=None):
    return SimpleNamespace(status_code=status, content=b'{}', json=lambda: document)


def inbox(*responses, files=()):
    session = MagicMock()
    session.request.side_effect = [response(200, {'private': True, 'full_name': 'RauPro/MDH-SV-review'}), *responses]
    session.get.side_effect = list(files)
    pauses = []
    value = GitHubInbox('RauPro/MDH-SV-review', 'fake-secret', session, sleep=pauses.append)
    return value, session, pauses


def file(raw, sha='a' * 40):
    return {'type': 'file', 'encoding': 'base64', 'content': base64.b64encode(raw).decode(), 'sha': sha, 'size': len(raw)}


def writes(session):
    return [call for call in session.request.call_args_list if call.args[0] == 'PUT']


def test_transient_reads_retry_with_a_small_fixed_bound():
    value, session, pauses = inbox(response(500), response(502), response(200, {'result': 'ok'}))
    assert value.call('GET', value.base + '/issues') == {'result': 'ok'}
    assert pauses == [1, 2]
    assert session.request.call_count == 4  # Repository check plus three attempts.


def test_transient_file_read_keeps_404_semantics():
    value, session, pauses = inbox(files=[response(503), response(404)])
    assert value.get_file(value.base + '/contents/processing/example.json') is None
    assert session.get.call_count == 2 and pauses == [1]


def test_already_applied_contents_write_is_acknowledged_without_another_put():
    data = b'private fixture, never logged'
    value, session, pauses = inbox(response(500), files=[response(200, file(data, 'b' * 40))])
    result = value.call('PUT', value.base + '/contents/processing/example.json',
                        json={'content': base64.b64encode(data).decode(), 'sha': 'a' * 40, 'branch': 'main'})
    assert result == {'content': {'sha': 'b' * 40}}
    assert len(writes(session)) == 1 and pauses == []
    assert session.get.call_args.kwargs['params'] == {'ref': 'main'}


def test_unapplied_contents_write_retries_the_identical_payload_and_revision():
    payload = {'content': base64.b64encode(b'new').decode(), 'sha': 'a' * 40}
    value, session, pauses = inbox(response(504), response(200, {'content': {'sha': 'b' * 40}}),
                                  files=[response(200, file(b'old'))])
    assert value.call('PUT', value.base + '/contents/processing/example.json', json=payload)['content']['sha'] == 'b' * 40
    assert len(writes(session)) == 2 and pauses == [1]
    assert writes(session)[0].kwargs['json'] == writes(session)[1].kwargs['json'] == payload


def test_new_file_can_retry_after_confirmed_absence():
    value, session, _ = inbox(response(502), response(201, {'content': {'sha': 'b' * 40}}), files=[response(404)])
    payload = {'content': base64.b64encode(b'new').decode()}
    assert value.call('PUT', value.base + '/contents/processing/example.json', json=payload)['content']['sha'] == 'b' * 40
    assert all(call.kwargs['json'] == payload for call in writes(session))


@pytest.mark.parametrize('old_sha', [None, 'a' * 40])
def test_concurrent_contents_change_is_never_overwritten(old_sha):
    value, session, pauses = inbox(response(500), files=[response(200, file(b'concurrent', 'c' * 40))])
    payload = {'content': base64.b64encode(b'ours').decode()}
    if old_sha:
        payload['sha'] = old_sha
    with pytest.raises(GitHubRequestError) as caught:
        value.call('PUT', value.base + '/contents/processing/example.json', json=payload)
    assert caught.value.status_code == 409
    assert len(writes(session)) == 1 and pauses == []


@pytest.mark.parametrize(('method', 'status'), [('POST', 500), ('PATCH', 502), ('GET', 403), ('GET', 429), ('PUT', 409)])
def test_non_idempotent_writes_and_non_transient_failures_are_not_retried(method, status):
    value, session, pauses = inbox(response(status, {'message': 'PRIVATE_PROVIDER_SENTINEL'}))
    with pytest.raises(GitHubRequestError) as caught:
        value.call(method, value.base + '/contents/example.json')
    assert caught.value.status_code == status
    assert str(caught.value) == f'github_http_{status}'
    assert 'PRIVATE_PROVIDER_SENTINEL' not in str(caught.value)
    assert session.request.call_count == 2 and session.get.call_count == 0 and pauses == []


def test_retry_exhaustion_and_transport_errors_have_safe_diagnostics():
    value, session, pauses = inbox(response(500), response(500), response(500))
    with pytest.raises(GitHubRequestError, match='github_http_500'):
        value.call('GET', value.base + '/issues')
    assert session.request.call_count == 4 and pauses == [1, 2]
    value, _, _ = inbox(requests.Timeout('private URL or credential'))
    with pytest.raises(GitHubRequestError, match='^github_transport_error$'):
        value.call('GET', value.base + '/issues')
