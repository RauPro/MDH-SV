"""Fork destinations remain explicit across private workers and PR promotion."""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from mdh_pipeline.review_queue import GitHubInbox, GitHubRequestError

ROOT = Path(__file__).resolve().parents[1]
FORK = json.loads((Path(__file__).parent / 'fixtures/instance-config-cases.json').read_text())['base']


@pytest.fixture
def fork_file(tmp_path):
    path = tmp_path / 'fork.json'
    path.write_text(json.dumps(FORK))
    return path


def run_fork(code, fork_file, tmp_path):
    paths = ['apps/pipeline/src', 'packages/schema/src', 'apps/mcp/src', 'tests']
    result = subprocess.run([sys.executable, '-c', textwrap.dedent(code)], cwd=tmp_path,
                            env={**os.environ, 'MDH_INSTANCE_FILE': str(fork_file),
                                 'PYTHONPATH': os.pathsep.join(str(ROOT / path) for path in paths)},
                            capture_output=True, text=True, timeout=40, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_fork_controller_promotes_only_its_own_exact_identity(fork_file, tmp_path):
    run_fork('''
        from copy import deepcopy
        from urllib.parse import parse_qs, urlsplit
        import pytest
        from mdh_pipeline import publication_pr as controller
        from mdh_pipeline.instance import load_instance
        import test_publication_pr as fixtures
        config = load_instance()
        assert controller.REPOSITORY == config.public_repository
        assert controller.REPOSITORY_ID == config.public_repository_id
        assert controller.NEWS_PUBLISHER_ACTOR_ID == config.publisher_actor_id
        fixtures.OWNER = {'id': config.publisher_actor_id, 'login': 'publisher', 'type': 'User'}
        class Client(fixtures.GitHubFake):
            def call(self, method, path, payload=None):
                assert path.startswith('/repos/' + config.public_repository + '/')
                if method == 'GET' and path.startswith(controller.api('pulls?')):
                    assert parse_qs(urlsplit(path).query)['head'] == [config.public_owner + ':' + self.branch]
                    self.calls.append((method, path, deepcopy(payload)))
                    return [] if self.pr is None else [deepcopy(self.pr)]
                return super().call(method, path, payload)
        for kind in ('history', 'exception', 'news'):
            client = Client(kind)
            assert controller.control(client, client.event()) == 'merged_with_required_checks'
            assert client.writes()[-1][2]['sha'] == fixtures.HEAD
        client = Client('news')
        client.runs[91]['actor']['id'] = 37725305
        with pytest.raises(controller.PublicationError):
            controller.control(client, client.event())
        assert not client.writes()
        client = Client('history')
        client.runs[501]['repository']['id'] = 1412705369
        with pytest.raises(controller.PublicationError):
            controller.control(client, client.event())
        assert not client.writes()
        with pytest.raises(controller.PublicationError, match='invalid_api_path'):
            controller.GitHub('fixture-token').call('GET', '/repos/RauPro/MDH-SV/pulls')
    ''', fork_file, tmp_path)


def test_fork_request_pages_and_saved_states_keep_the_configured_repositories(fork_file, tmp_path):
    run_fork('''
        import base64
        import json
        from pathlib import Path
        from mdh_pipeline import news_requests as requests
        from mdh_pipeline import news_issue_reconcile as reconcile
        from mdh_pipeline import homicide_recovery_queue as recovery
        from mdh_pipeline.instance import load_instance
        from test_news_requests import issue, ID, EMPTY
        config = load_instance()
        assert requests.PUBLIC_REPO == config.public_repository
        assert requests.PRIVATE_REPO == reconcile.REPOSITORY == recovery.REPOSITORY == config.private_repository
        assert reconcile.PRODUCTION == config.public_url
        class Inbox:
            repository = config.private_repository
            base = '/repos/' + repository
            state = {'schema_version': 2, 'generations': {}, 'next_page': 11}
            pages = []
            def get_file(self, path):
                assert path == self.base + '/contents/' + requests.STATE_PATH
                raw = json.dumps(self.state).encode()
                return {'type': 'file', 'path': requests.STATE_PATH, 'encoding': 'base64',
                        'size': len(raw), 'content': base64.b64encode(raw).decode(), 'sha': 'b' * 40}
            def call(self, method, path, **kwargs):
                if method == 'GET':
                    assert path == '/repos/' + config.public_repository + '/issues'
                    page = kwargs['params']['page']
                    self.pages.append(page)
                    return [issue(page * 100 + n) for n in range(100)] if page <= 12 else []
                assert method == 'PUT' and path == self.base + '/contents/' + requests.STATE_PATH
                self.state = json.loads(base64.b64decode(kwargs['json']['content']))
                return {'content': {'sha': 'c' * 40}}
        inbox = Inbox()
        requests.sync_requests(inbox, {'records': [{'id': ID}]}, EMPTY, output=Path('requests.json'))
        requests.sync_requests(inbox, {'records': [{'id': ID}]}, EMPTY, output=Path('requests.json'))
        assert inbox.pages == [11, 12, 13] and inbox.state['next_page'] == 1
        class Response:
            status_code = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def iter_content(self, size): yield b'{}'
        class Session:
            paths = []
            def get(self, url, **kwargs):
                assert kwargs['allow_redirects'] is False
                self.paths.append(url)
                return Response()
        session = Session()
        assert reconcile.production_snapshot(session) == ({}, {})
        assert session.paths == [config.public_url + '/archive/news.json', config.public_url + '/archive/dossiers.json']
    ''', fork_file, tmp_path)


@pytest.mark.parametrize('batched', [False, True])
def test_fork_workers_retain_source_and_privacy_checks(fork_file, tmp_path, batched):
    run_fork('''
        from pathlib import Path
        from mdh_pipeline.instance import load_instance
        from test_automatic_news_worker import setup, run, receipts
        from test_automatic_news import Budget, MODEL, KEY, NOW, archive, project_dossier, judgment
        from mdh_pipeline.automatic_news import evaluate_queue
        config = load_instance()
        rows, inbox, folder = setup(Path.cwd(), count=1)
        inbox.repository = config.private_repository
        inbox.base = '/repos/' + inbox.repository
        if BATCHED:
            report = run(rows, inbox, folder)
        else:
            row = rows[0]
            def evaluate(document, reference, article, *, budget, **kwargs):
                budget.reserve({})
                return {'judgment': judgment(), 'usage': None,
                        'dossier': project_dossier(document['news_dossier'], judgment(),
                            source_hash=article['content_sha256'], model=MODEL, now=NOW)}
            report = evaluate_queue(inbox, folder, archive(row['reference']),
                api_key=KEY, model=MODEL, budget=Budget(), limit=1,
                fetch=lambda ref: row['article'], evaluate=evaluate, now=NOW)
        assert report['publishable'] == 1 and report['http_requests'] == 1
        assert all(value['dossier']['human_reviewed'] is False for value in receipts(inbox).values())
    '''.replace('BATCHED', repr(batched)), fork_file, tmp_path)


def response(status, value):
    return SimpleNamespace(status_code=status, content=b'{}', json=lambda: value)


def private_session():
    session = MagicMock()
    session.request.return_value = response(200, {'private': True, 'full_name': FORK['private_repository']})
    return session


@pytest.mark.parametrize('repository', ['RauPro/MDH-SV-review', FORK['public_repository'], 'unrelated/private'])
def test_private_inbox_rejects_other_instances_before_network(repository, fork_file, monkeypatch):
    monkeypatch.setenv('MDH_INSTANCE_FILE', str(fork_file))
    session = private_session()
    with pytest.raises(ValueError, match='configured private queue'):
        GitHubInbox(repository, 'fixture-token', session)
    assert not session.request.called


@pytest.mark.parametrize('metadata', [
    {'private': False, 'full_name': FORK['private_repository']},
    {'private': True, 'full_name': 'unrelated/private'},
    {'private': True, 'full_name': None}, None,
])
def test_configured_private_name_still_requires_github_confirmation(metadata, fork_file, monkeypatch):
    monkeypatch.setenv('MDH_INSTANCE_FILE', str(fork_file))
    session = private_session()
    session.request.return_value = response(200, metadata)
    with pytest.raises(ValueError, match='MUST be a private'):
        GitHubInbox(FORK['private_repository'], 'fixture-token', session)


@pytest.mark.parametrize('status', [301, 302, 307, 308])
def test_private_inbox_never_follows_redirects(status, fork_file, monkeypatch):
    monkeypatch.setenv('MDH_INSTANCE_FILE', str(fork_file))
    session = private_session()
    inbox = GitHubInbox(FORK['private_repository'], 'fixture-token', session)
    session.request.return_value = response(status, {'message': 'PRIVATE_SENTINEL'})
    with pytest.raises(GitHubRequestError, match=f'^github_http_{status}$'):
        inbox.call('GET', inbox.base + '/issues')
    assert all(call.kwargs['allow_redirects'] is False for call in session.request.call_args_list)
    assert session.request.call_count == 2


@pytest.mark.parametrize(('method', 'path'), [
    ('POST', '/repos/ExampleOrg/monitor/issues'),
    ('PUT', '/repos/other/private/contents/draft.json'),
    ('GET', '/repos/ExampleOrg/review-private/../monitor/issues'),
    ('GET', '/repos/ExampleOrg/review-private/%2e%2e/monitor/issues'),
    ('GET', 'https://attacker.example.org/private'),
    ('GET', '//attacker.example.org/private'),
])
def test_private_credential_cannot_target_public_or_unconfigured_destinations(method, path, fork_file, monkeypatch):
    monkeypatch.setenv('MDH_INSTANCE_FILE', str(fork_file))
    session = private_session()
    inbox = GitHubInbox(FORK['private_repository'], 'fixture-token', session)
    with pytest.raises(GitHubRequestError, match='^github_path_not_authorized$'):
        inbox.call(method, path)
    assert session.request.call_count == 1


def test_private_inbox_can_read_its_public_requests_and_own_rate_budget(fork_file, monkeypatch):
    monkeypatch.setenv('MDH_INSTANCE_FILE', str(fork_file))
    session = private_session()
    inbox = GitHubInbox(FORK['private_repository'], 'fixture-token', session)
    for path in ('/repos/' + FORK['public_repository'] + '/issues', '/rate_limit'):
        inbox.call('GET', path)
        assert session.request.call_args.args[1] == 'https://api.github.com' + path


def test_fork_quota_handoff_is_private_and_consumed_once(fork_file, monkeypatch):
    from mdh_pipeline.gemini_budget import QuotaStateError
    from mdh_pipeline.pipeline_allocation import AllocationLedger
    from test_pipeline_allocation import NOW, Inbox, create

    monkeypatch.setenv('MDH_INSTANCE_FILE', str(fork_file))
    inbox = Inbox()
    inbox.repository = FORK['private_repository']
    inbox.base = '/repos/' + inbox.repository
    ledger = AllocationLedger(inbox, project_id='fixture-project', model='gemini-3.5-flash-lite',
                              clock=lambda: NOW)
    create(ledger)
    assert ledger.claim_validation(source_run_id='123', consumer_run_id='456', day='2026-10-14') == 2
    assert ledger.claim_validation(source_run_id='123', consumer_run_id='456', day='2026-10-14') == 0
    assert inbox.writes == 2
    inbox.repository = 'RauPro/MDH-SV-review'
    with pytest.raises(QuotaStateError, match='invalid_allocation_configuration'):
        AllocationLedger(inbox, project_id='fixture-project', model='gemini-3.5-flash-lite')
