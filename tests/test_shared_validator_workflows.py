"""Shared validator dependencies are installed only from approved code locks."""
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow(path):
    return yaml.load((ROOT / path).read_text(), Loader=yaml.BaseLoader)


def install_step(steps, command):
    matching = [step for step in steps if step.get('run') == command]
    assert len(matching) == 1
    install = matching[0]
    assert set(install) == {'name', 'run'}
    setup = next(step for step in steps if step.get('uses', '').startswith('actions/setup-node@'))
    assert steps.index(setup) < steps.index(install)
    javascript = [step for step in steps if any(line.startswith('node ')
                  for line in step.get('run', '').splitlines())]
    assert javascript and all(steps.index(install) < steps.index(step) for step in javascript)
    return install


def test_ci_installs_shared_dependencies_once_after_history_scan_before_any_consumer():
    document = workflow('.github/workflows/ci.yml')
    assert document['permissions'] == {'contents': 'read'}
    steps = document['jobs']['verify']['steps']
    install = install_step(steps, 'npm ci --ignore-scripts')
    checkout = next(step for step in steps if step.get('uses', '').startswith('actions/checkout@'))
    scan = next(step for step in steps if step.get('run') ==
                'python scripts/publication_guard.py check --ref HEAD')
    scanner = next(step for step in steps if
                   'python scripts/install_gitleaks.py' in step.get('run', ''))
    assert checkout['with']['fetch-depth'] == '0'
    assert steps.index(checkout) < steps.index(scanner) < steps.index(scan) < steps.index(install)
    assert 'if' not in scan and 'continue-on-error' not in scan
    root_installs = [line for step in steps for line in step.get('run', '').splitlines()
                     if re.match(r'\s*npm\s+ci(?:\s|$)', line)]
    assert root_installs == ['npm ci --ignore-scripts']
    consumers = [step for step in steps if 'pytest' in step.get('run', '')
                 or 'npm run test:api' in step.get('run', '')]
    assert consumers and all(steps.index(install) < steps.index(step) for step in consumers)
    assert 'secrets.' not in str(document['jobs']['verify'])


@pytest.mark.parametrize('path,job', [
    ('.github/workflows/news-exception.yml', 'apply'),
    ('.github/workflows/refresh-history.yml', 'refresh'),
    ('ops/history/refresh-history.yml', 'refresh'),
])
def test_public_writers_install_the_root_lock_before_shared_validation(path, job):
    document = workflow(path)
    assert document['permissions'] == {'contents': 'read'}
    operation = document['jobs'][job]
    assert "vars.MDH_AUTOMATION_ENABLED == 'true'" in operation['if']
    steps = operation['steps']
    install = install_step(steps, 'npm ci --omit=dev --ignore-scripts')
    checkout = next(step for step in steps if step.get('uses', '').startswith('actions/checkout@'))
    assert steps.index(checkout) < steps.index(install)
    assert 'repository' not in checkout.get('with', {})
    assert 'npm install' not in str(steps)
    expected = {'contents': 'write', 'pull-requests': 'write', 'actions': 'write'}
    if job == 'apply':
        expected['issues'] = 'read'
    assert operation['permissions'] == expected


@pytest.mark.parametrize('job', ['publish', 'propose'])
def test_private_jobs_install_only_the_approved_monitor_lock_before_data_checkout(job):
    operation = workflow('ops/review-queue/.github/workflows/publish-locations.yml')['jobs'][job]
    steps = operation['steps']
    install = install_step(steps, 'npm --prefix monitor ci --omit=dev --ignore-scripts')
    code = next(step for step in steps if step.get('with', {}).get('path') == 'monitor')
    assert code['with']['ref'] == 'MDH_PUBLIC_CODE_REF'
    assert code['with']['persist-credentials'] == 'false'
    assert steps.index(code) < steps.index(install)
    data = next(step for step in steps if step.get('with', {}).get('path') == 'publication')
    assert steps.index(install) < steps.index(data)
    assert all('npm --prefix publication' not in step.get('run', '')
               and step.get('working-directory') != 'publication' for step in steps)
    assert not any('npm' in step.get('run', '') and step is not install for step in steps)
    if job == 'propose':
        assert operation['permissions'] == {}
        assert operation['needs'] == 'publish'
        assert data['with']['ssh-key'] == '${{ secrets.NEWS_LOCATION_PUBLISH_KEY }}'
        assert 'NEWS_LOCATION_PUBLISH_KEY' not in str(steps[:steps.index(data)])
    else:
        assert operation['permissions'] == {'contents': 'write', 'issues': 'read'}
        assert 'NEWS_LOCATION_PUBLISH_KEY' not in str(operation)


def test_history_template_is_still_identical_to_the_active_public_workflow():
    assert (ROOT / 'ops/history/refresh-history.yml').read_bytes() == (
        ROOT / '.github/workflows/refresh-history.yml').read_bytes()
