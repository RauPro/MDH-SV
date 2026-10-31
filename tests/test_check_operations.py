"""Offline tests for the operations drift check using recorded fake responses."""
import base64
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

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

    def get(self, path):
        if path not in self.responses:
            raise KeyError(path)
        return self.responses[path]


def make_root(tmp_path):
    root = tmp_path / 'repo'
    for rel, source in [('ops/operations-expectations.json', 'ops/operations-expectations.json'),
                        ('.github/main-protection.json', '.github/main-protection.json'),
                        ('ops/actions-minutes-budget.json', 'ops/actions-minutes-budget.json')]:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((ROOT / source).read_text())
    (root / 'ops/operations-expectations.json').write_text(json.dumps({
        'version': 1,
        'repositories': {'public': {'name': PUB, 'visibility': 'public'},
                         'private': {'name': PRIV, 'visibility': 'private'}},
        'actions': {'enabled': True, 'sha_pinning_required': True,
                    'default_workflow_permissions': 'read', 'can_approve_pull_request_reviews': False},
        'public_main_protection': {'file': '.github/main-protection.json', 'enforce_admins': True, 'restrictions': None},
        'private_main_protection': {'protected': False, 'status': 'accepted', 'reason': 'runtime writes'},
        'variables': {'MDH_AUTOMATION_ENABLED': {'repositories': ['public', 'private'], 'equals': 'true'},
                      'GEMINI_MODEL': {'repositories': ['private'], 'present': True},
                      'MDH_GEMINI_BUDGET_CONFIG': {'repositories': ['private'], 'budget_check': True}},
        'schedule_delay_tolerance_hours': 8}))
    (root / '.github/main-protection.json').write_text(json.dumps(
        {'enforce_admins': True, 'restrictions': None}))
    (root / 'ops/actions-minutes-budget.json').write_text(json.dumps({'max_requests_per_cycle': 16}))
    template = (f"steps:\n  - uses: actions/checkout@{SHA}\n    with:\n      repository: {PUB}\n      ref: {ops.CODE_REF_PLACEHOLDER}\n"
                f"  - uses: actions/checkout@{SHA}\n    with:\n      repository: {PRIV}\n")
    for rel in ['ops/review-queue/.github/workflows/extract.yml', 'ops/history/review-history.yml',
                'ops/history/refresh-history.yml', '.github/workflows/refresh-history.yml']:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(template)
    return root, template


def responses(template):
    fixture = json.loads((ROOT / 'tests/fixtures/operations/baseline.json').read_text())
    now = datetime.now(timezone.utc).isoformat()
    for key, value in list(fixture.items()):
        if not isinstance(value, dict):
            continue
        for run in (value.get('workflow_runs') or []):
            if run.get('created_at') == 'NOW':
                run['created_at'] = now
    protection = {'enforce_admins': {'enabled': True}, 'restrictions': None}
    fixture[f'/repos/{PUB}/branches/main/protection'] = protection
    fixture[f'/repos/{PRIV}/contents/.github/workflows'] = [{'name': name} for name in ('extract.yml', 'history.yml')]
    installed = template.replace(ops.CODE_REF_PLACEHOLDER, SHA)
    for path in ('/contents/.github/workflows/extract.yml', '/contents/.github/workflows/history.yml'):
        fixture[f'/repos/{PRIV}{path}'] = {'content': base64.b64encode(installed.encode()).decode()}
    fixture[f'/repos/{PUB}/contents/.github/workflows/refresh-history.yml'] = {'content': base64.b64encode(template.encode()).decode()}
    fixture[f'/repos/{PUB}/compare/{SHA}...main'] = {'behind_by': 0}
    return fixture


def levels(report):
    return [f for f in report['findings'] if f['level'] == 'failure']


def test_clean_pass(tmp_path):
    root, template = make_root(tmp_path)
    report = ops.run_checks(FakeClient(responses(template)), root)
    assert report['summary']['failures'] == 0 and report['summary']['warnings'] >= 1


def test_template_drift_reports_a_diff(tmp_path):
    root, template = make_root(tmp_path)
    fixture = responses(template)
    drifted = base64.b64encode(b'steps: changed\n').decode()
    fixture[f'/repos/{PRIV}/contents/.github/workflows/extract.yml'] = {'content': drifted}
    report = ops.run_checks(FakeClient(fixture), root)
    assert any('differs' in f['message'] and 'changed' in f['message'] for f in levels(report))


def test_refresh_history_copies_differing(tmp_path):
    root, template = make_root(tmp_path)
    fixture = responses(template)
    fixture[f'/repos/{PUB}/contents/.github/workflows/refresh-history.yml'] = {'content': base64.b64encode(b'other').decode()}
    report = ops.run_checks(FakeClient(fixture), root)
    assert any(f['area'] == 'history-copies' for f in levels(report))


def test_protection_mismatch_bypass_and_enforce_admins(tmp_path):
    root, template = make_root(tmp_path)
    fixture = responses(template)
    fixture[f'/repos/{PUB}/branches/main/protection'] = {'enforce_admins': {'enabled': False}, 'restrictions': {'users': ['x']}}
    report = ops.run_checks(FakeClient(fixture), root)
    assert any(f['area'] == 'protection' for f in levels(report))


def test_sha_pinning_off_is_a_failure(tmp_path):
    root, template = make_root(tmp_path)
    fixture = responses(template)
    fixture[f'/repos/{PUB}/actions/permissions'] = {'enabled': True, 'sha_pinning_required': False}
    report = ops.run_checks(FakeClient(fixture), root)
    assert any('sha_pinning' in f['message'] for f in levels(report))


def test_unknown_installed_workflow(tmp_path):
    root, template = make_root(tmp_path)
    fixture = responses(template)
    fixture[f'/repos/{PRIV}/contents/.github/workflows'].append({'name': 'sneaky.yml'})
    report = ops.run_checks(FakeClient(fixture), root)
    assert any('sneaky.yml' in f['message'] for f in levels(report))


def test_missing_variable_and_bad_budget(tmp_path):
    root, template = make_root(tmp_path)
    fixture = responses(template)
    fixture[f'/repos/{PRIV}/actions/variables']['variables'] = [
        {'name': 'MDH_AUTOMATION_ENABLED', 'value': 'true'},
        {'name': 'GEMINI_MODEL', 'value': 'gemini-9'},
        {'name': 'MDH_GEMINI_BUDGET_CONFIG', 'value': '{"rpm": 5, "batch_ceiling": 4, "model": "other"}'}]
    report = ops.run_checks(FakeClient(fixture), root)
    assert any('budget' in f['message'].lower() for f in levels(report))
    fixture[f'/repos/{PRIV}/actions/variables']['variables'] = []
    report = ops.run_checks(FakeClient(fixture), root)
    assert any('GEMINI_MODEL' in f['message'] or 'missing' in f['message'] for f in levels(report))


def test_wrong_visibility(tmp_path):
    root, template = make_root(tmp_path)
    fixture = responses(template)
    fixture[f'/repos/{PRIV}']['visibility'] = 'public'
    report = ops.run_checks(FakeClient(fixture), root)
    assert any(f['area'] == 'visibility' for f in levels(report))


def test_stale_run_health(tmp_path):
    root, template = make_root(tmp_path)
    fixture = responses(template)
    stale = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat()
    fixture[f'/repos/{PUB}/actions/workflows/.github/workflows/refresh-history.yml/runs?per_page=10'] = {
        'workflow_runs': [{'conclusion': 'success', 'created_at': stale}]}
    report = ops.run_checks(FakeClient(fixture), root)
    assert any('run-health' == f['area'] for f in levels(report))


def test_quota_exhaustion_is_a_warning(tmp_path):
    root, template = make_root(tmp_path)
    fixture = responses(template)
    fixture[f'/repos/{PRIV}/actions/workflows/extract.yml/runs?per_page=10'] = {
        'workflow_runs': [{'conclusion': 'failure', 'created_at': 'x'}] * 3}
    report = ops.run_checks(FakeClient(fixture), root)
    assert any('sin ejecuciones correctas' in f['message'] for f in report['findings'] if f['level'] == 'warning')


def test_client_refusals():
    client = FakeClient({})
    with pytest.raises(AssertionError):
        client.request('POST', '/repos/x/issues')
    with pytest.raises(AssertionError):
        client.request('GET', '/repos/x/actions/secrets/GH_TOKEN')
