"""Offline tests for the operations drift check using recorded fake responses."""
import base64
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('check_operations', ROOT / 'scripts/check_operations.py')
ops = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ops)

PUB, PRIV = 'pub', 'priv'
SHA = 'a' * 40


class FakeClient(ops.GitHub):
    def __init__(self, responses):
        super().__init__('test-token')
        self.responses = responses
        self.requested = []

    def get(self, path):
        self.requested.append(path)
        if path not in self.responses:
            raise HTTPError(path, 404, 'not found', {}, None)
        return self.responses[path]


PUBLIC_WF = """on:
  schedule:
    - cron: '41 */6'
jobs:
  refresh:
    steps: []
"""

PRIVATE = {
    'extract.yml': "on:\n  workflow_dispatch:\nsteps: []\n",
    'extract-fallback.yml': "on:\n  schedule:\n    - cron: '23 1,13'\nsteps: []\n",
    'collect.yml': "on:\n  schedule:\n    - cron: '17 7,19'\nsteps: []\n",
    'history.yml': "on:\n  schedule:\n    - cron: '47 7,19'\nsteps: []\n",
    'official-statistics-monitor.yml': "on:\n  schedule:\n    - cron: '37 12'\nsteps: []\n",
    'official-statistics-review.yml': "on:\n  workflow_dispatch:\nsteps: []\n",
    'publish-locations.yml': "on:\n  workflow_run:\n    workflows: ['Preparar fichas con Gemini']\n    types: [completed]\nsteps: []\n",
    'reconcile-news-issues.yml': "on:\n  workflow_run:\n    workflows: ['Preparar fichas con Gemini']\n    types: [completed]\nsteps: []\n",
    'review.yml': "on:\n  workflow_dispatch:\nsteps: []\n",
    'review-scope.yml': "on:\n  workflow_dispatch:\nsteps: []\n",
}

TEMPLATES = {
    'ops/history/review-history.yml': 'history.yml',
    'ops/official-statistics-review/monitor.yml': 'official-statistics-monitor.yml',
    'ops/official-statistics-review/review.yml': 'official-statistics-review.yml',
    'ops/review-queue/.github/workflows/collect.yml': 'collect.yml',
    'ops/review-queue/.github/workflows/extract.yml': 'extract.yml',
    'ops/review-queue/.github/workflows/extract-fallback.yml': 'extract-fallback.yml',
    'ops/review-queue/.github/workflows/publish-locations.yml': 'publish-locations.yml',
    'ops/review-queue/.github/workflows/reconcile-news-issues.yml': 'reconcile-news-issues.yml',
    'ops/review-queue/.github/workflows/review.yml': 'review.yml',
    'ops/review-queue/.github/workflows/review-scope.yml': 'review-scope.yml',
}

CODE_REF_STEP = f"steps:\n  - uses: actions/checkout@{SHA}\n    with:\n      repository: {PUB}\n      ref: {ops.CODE_REF_PLACEHOLDER}\n"


def make_root(tmp_path):
    root = tmp_path / 'repo'
    for rel in ['.github/workflows', 'ops/history', 'ops/official-statistics-review',
                'ops/review-queue/.github/workflows']:
        (root / rel).mkdir(parents=True, exist_ok=True)
    (root / 'ops/operations-expectations.json').write_text(json.dumps({
        'version': 1,
        'repositories': {'public': {'name': PUB, 'visibility': 'public'},
                         'private': {'name': PRIV, 'visibility': 'private'}},
        'actions': {'enabled': True, 'sha_pinning_required': True,
                    'default_workflow_permissions': 'read',
                    'can_approve_pull_request_reviews': {'public': True, 'private': False}},
        'public_main_protection': {'file': '.github/main-protection.json', 'enforce_admins': True, 'restrictions': None},
        'private_main_protection': {'protected': False, 'status': 'accepted', 'reason': 'runtime writes'},
        'variables': {'MDH_AUTOMATION_ENABLED': {'repositories': ['public', 'private'], 'equals': 'true'},
                      'GEMINI_MODEL': {'repositories': ['private'], 'present': True},
                      'MDH_GEMINI_BUDGET_CONFIG': {'repositories': ['private'], 'budget_check': True}},
        'schedule_delay_tolerance_hours': 8}))
    (root / '.github/main-protection.json').write_text(json.dumps(
        {'enforce_admins': True, 'restrictions': None}))
    (root / 'ops/actions-minutes-budget.json').write_text(json.dumps({'max_requests_per_cycle': 16}))
    (root / 'vercel.json').write_text(json.dumps(
        {'crons': [{'path': '/api/internal/hourly-ingestion', 'schedule': '7 1,13'}]}))
    (root / '.github/workflows/refresh-history.yml').write_text(PUBLIC_WF)
    (root / '.github/workflows/ci.yml').write_text("on:\n  push:\nsteps: []\n")
    (root / 'ops/history/refresh-history.yml').write_text(PUBLIC_WF)
    for template, installed in TEMPLATES.items():
        target = root / template
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(CODE_REF_STEP + PRIVATE[installed])
    return root


def responses():
    now = datetime.now(timezone.utc).isoformat()
    fixture = {
        f'/repos/{PUB}': {'full_name': PUB, 'visibility': 'public'},
        f'/repos/{PRIV}': {'full_name': PRIV, 'visibility': 'private'},
        f'/repos/{PUB}/actions/permissions': {'enabled': True, 'sha_pinning_required': True},
        f'/repos/{PRIV}/actions/permissions': {'enabled': True, 'sha_pinning_required': True},
        f'/repos/{PUB}/actions/permissions/workflow': {'default_workflow_permissions': 'read', 'can_approve_pull_request_reviews': True},
        f'/repos/{PRIV}/actions/permissions/workflow': {'default_workflow_permissions': 'read', 'can_approve_pull_request_reviews': False},
        f'/repos/{PUB}/branches/main/protection': {'enforce_admins': {'enabled': True}, 'restrictions': None},
        f'/repos/{PUB}/actions/variables': {'variables': [{'name': 'MDH_AUTOMATION_ENABLED', 'value': 'true'}]},
        f'/repos/{PRIV}/actions/variables': {'variables': [
            {'name': 'MDH_AUTOMATION_ENABLED', 'value': 'true'},
            {'name': 'GEMINI_MODEL', 'value': 'gemini-9'},
            {'name': 'MDH_GEMINI_BUDGET_CONFIG', 'value': '{"rpm": 2, "batch_ceiling": 16, "model": "gemini-9"}'}]},
        f'/repos/{PUB}/rulesets': [{'name': 'main'}],
        f'/repos/{PRIV}/rulesets': [],
        f'/repos/{PUB}/keys': [{'title': 'pub-key', 'read_only': True, 'created_at': 'x'}],
        f'/repos/{PRIV}/keys': [],
        f'/repos/{PUB}/collaborators': [{'login': 'RauPro', 'role_name': 'admin'}],
        f'/repos/{PRIV}/collaborators': [{'login': 'RauPro', 'role_name': 'admin'}],
        f'/repos/{PUB}/hooks': [{'events': ['push'], 'active': True, 'config': {'url': 'https://h.vercel.app/x'}}],
        f'/repos/{PRIV}/hooks': [],
        f'/repos/{PUB}/environments': {'environments': [{'name': 'Production'}]},
        f'/repos/{PRIV}/environments': {'environments': []},
        f'/repos/{PRIV}/contents/.github/workflows': [{'name': name} for name in PRIVATE],
        f'/repos/{PUB}/contents/.github/workflows/refresh-history.yml': {'content': base64.b64encode(PUBLIC_WF.encode()).decode()},
        f'/repos/{PUB}/compare/{SHA}...main': {'status': 'identical', 'ahead_by': 0},
    }
    for name in PRIVATE:
        fixture[f'/repos/{PRIV}/actions/workflows/{name}/runs?per_page=10'] = {
            'workflow_runs': [{'conclusion': 'success', 'created_at': now}]}
    fixture[f'/repos/{PUB}/actions/workflows/refresh-history.yml/runs?per_page=10'] = {
        'workflow_runs': [{'conclusion': 'success', 'created_at': now}]}
    for installed, name in TEMPLATES.items():
        fixture[f'/repos/{PRIV}/contents/.github/workflows/{name}'] = {
            'content': base64.b64encode((CODE_REF_STEP.replace(ops.CODE_REF_PLACEHOLDER, SHA) + PRIVATE[name]).encode()).decode()}
    return fixture


def levels(report, level='failure'):
    return [f for f in report['findings'] if f['level'] == level]


def test_cadences():
    assert ops.cron_gap_hours('41 */6') == 6
    assert ops.cron_gap_hours('17 7,19') == 12
    assert ops.cron_gap_hours('37 12') == 24
    assert ops.cron_gap_hours('7 1,13') == 12
    assert ops.cron_gap_hours('* *') == 1


def test_clean_pass_and_unscheduled_reported(tmp_path):
    root = make_root(tmp_path)
    report = ops.run_checks(FakeClient(responses()), root)
    assert report['summary']['failures'] == 0
    skipped = [f for f in report['findings'] if 'no programado' in f['message']]
    assert skipped and all(f['level'] == 'ok' for f in skipped)


def test_can_approve_is_checked_per_repo(tmp_path):
    root = make_root(tmp_path)
    fixture = responses()
    fixture[f'/repos/{PRIV}/actions/permissions/workflow'] = {
        'default_workflow_permissions': 'read', 'can_approve_pull_request_reviews': True}
    report = ops.run_checks(FakeClient(fixture), root)
    assert any('can_approve' in f['message'] and PRIV in f['message'] for f in levels(report))


def test_extract_uses_the_vercel_cadence(tmp_path):
    root = make_root(tmp_path)
    fixture = responses()
    stale = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    fixture[f'/repos/{PRIV}/actions/workflows/extract.yml/runs?per_page=10'] = {
        'workflow_runs': [{'conclusion': 'success', 'created_at': stale}]}
    report = ops.run_checks(FakeClient(fixture), root)
    assert any('extract.yml' in f['message'] and 'outside' in f['message'] for f in levels(report))


def test_run_health_uses_basename_paths_and_warns_on_no_runs(tmp_path):
    root = make_root(tmp_path)
    fixture = responses()
    fixture[f'/repos/{PRIV}/actions/workflows/history.yml/runs?per_page=10'] = {'workflow_runs': []}
    client = FakeClient(fixture)
    report = ops.run_checks(client, root)
    run_names = [p.split('/actions/workflows/')[-1].split('/runs')[0] for p in client.requested
                 if '/actions/workflows/' in p]
    assert run_names and all('/' not in name for name in run_names)
    assert any('sin ejecuciones registradas' in f['message'] for f in levels(report, 'warning'))


def test_template_drift_reports_a_diff(tmp_path):
    root = make_root(tmp_path)
    fixture = responses()
    fixture[f'/repos/{PRIV}/contents/.github/workflows/extract.yml'] = {
        'content': base64.b64encode(b'changed\n').decode()}
    report = ops.run_checks(FakeClient(fixture), root)
    assert any('differs' in f['message'] for f in levels(report))


def test_protection_403_is_a_failure_not_accepted(tmp_path):
    root = make_root(tmp_path)
    fixture = responses()
    class Forbidden(FakeClient):
        def get(self, path):
            if path == '/repos/priv/branches/main/protection':
                raise HTTPError(path, 403, 'forbidden', {}, None)
            return super().get(path)
    report = ops.run_checks(Forbidden(fixture), root)
    assert any('protection' == f['area'] and '403' in f['message'] for f in levels(report))


def test_keys_403_is_a_warning(tmp_path):
    root = make_root(tmp_path)
    fixture = responses()
    class Forbidden(FakeClient):
        def get(self, path):
            if path.endswith('/keys'):
                raise HTTPError(path, 403, 'forbidden', {}, None)
            return super().get(path)
    report = ops.run_checks(Forbidden(fixture), root)
    assert any('credentials' == f['area'] and '403' in f['message'] for f in levels(report, 'warning'))


def test_client_refusals():
    client = FakeClient({})
    with pytest.raises(AssertionError):
        client.request('POST', '/repos/x/issues')
    with pytest.raises(AssertionError):
        client.request('GET', '/repos/x/actions/secrets/GH_TOKEN')


def test_404_on_installed_workflow_and_public_history_are_failures(tmp_path):
    root = make_root(tmp_path)
    fixture = responses()
    del fixture[f'/repos/{PRIV}/contents/.github/workflows/history.yml']
    del fixture[f'/repos/{PUB}/contents/.github/workflows/refresh-history.yml']
    report = ops.run_checks(FakeClient(fixture), root)
    messages = [f['message'] for f in levels(report)]
    assert any('missing' in message and 'history.yml' in message for message in messages)
    assert any('does not exist' in message and 'refresh-history' in message for message in messages)
    assert report['summary']['failures'] >= 2


def failed_runs(with_steps):
    runs = [{'id': index, 'conclusion': 'failure',
             'created_at': datetime.now(timezone.utc).isoformat()} for index in range(3)]
    jobs = {index: {'jobs': [{'steps': [{'number': 1}] if with_steps else [],
                            'runner_name': 'gh-1' if with_steps else None}]}
            for index in range(3)}
    return runs, jobs


def test_failed_runs_with_executed_steps_are_a_failure(tmp_path):
    root = make_root(tmp_path)
    fixture = responses()
    runs, jobs = failed_runs(with_steps=True)
    fixture[f'/repos/{PRIV}/actions/workflows/history.yml/runs?per_page=10'] = {'workflow_runs': runs}
    for index, body in jobs.items():
        fixture[f'/repos/{PRIV}/actions/runs/{index}/jobs'] = body
    report = ops.run_checks(FakeClient(fixture), root)
    assert any('run-health' == f['area'] and 'outside' in f['message'] for f in levels(report))


def test_runs_that_never_started_are_a_quota_warning(tmp_path):
    root = make_root(tmp_path)
    fixture = responses()
    runs, jobs = failed_runs(with_steps=False)
    fixture[f'/repos/{PRIV}/actions/workflows/history.yml/runs?per_page=10'] = {'workflow_runs': runs}
    for index, body in jobs.items():
        fixture[f'/repos/{PRIV}/actions/runs/{index}/jobs'] = body
    report = ops.run_checks(FakeClient(fixture), root)
    assert any('cuota' in f['message'] for f in levels(report, 'warning'))
    assert not any(f['area'] == 'run-health' for f in levels(report))


def test_compare_status_mapping(tmp_path):
    root = make_root(tmp_path)
    for status, level in [('identical', 'ok'), ('ahead', 'warning'), ('diverged', 'failure')]:
        fixture = responses()
        fixture[f'/repos/{PUB}/compare/{SHA}...main'] = {'status': status, 'ahead_by': 7}
        report = ops.run_checks(FakeClient(fixture), root)
        findings = [f for f in report['findings']
                    if f['area'] == 'template-drift' and SHA[:7] in f['message']]
        assert findings and all(f['level'] == level for f in findings), (status, findings)
        if status == 'ahead':
            assert '7 commits' in findings[0]['message']
