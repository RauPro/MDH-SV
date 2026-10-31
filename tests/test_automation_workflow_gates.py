"""Forks can run verification without activating production writers or private jobs."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
# Source contracts follow the checked-in installation, never the upstream-only
# runtime unit fixture selected through MDH_INSTANCE_FILE in conftest.py.
INSTANCE = json.loads((ROOT / 'instance.json').read_text())
PUBLIC = [
    ('.github/workflows/publication-pr.yml', 'promote'),
    ('.github/workflows/news-exception.yml', 'apply'),
    ('.github/workflows/refresh-history.yml', 'refresh'),
    ('ops/history/refresh-history.yml', 'refresh'),
]
PRIVATE = [
    ('ops/review-queue/.github/workflows/collect.yml', 'collect'),
    ('ops/review-queue/.github/workflows/extract.yml', 'extract'),
    ('ops/review-queue/.github/workflows/extract-fallback.yml', 'extract'),
    ('ops/review-queue/.github/workflows/review.yml', 'review'),
    ('ops/review-queue/.github/workflows/review-scope.yml', 'scope'),
    ('ops/review-queue/.github/workflows/reconcile-news-issues.yml', 'reconcile'),
    ('ops/review-queue/.github/workflows/publish-locations.yml', 'publish'),
    ('ops/review-queue/.github/workflows/publish-locations.yml', 'propose'),
    ('ops/history/review-history.yml', 'enqueue'),
    ('ops/official-statistics-review/monitor.yml', 'monitor'),
    ('ops/official-statistics-review/review.yml', 'review'),
]
CASES = [pytest.param(path, job, private, id=f'{path}:{job}')
         for private, entries in ((False, PUBLIC), (True, PRIVATE))
         for path, job in entries]


def workflow(path):
    return yaml.load((ROOT / path).read_text(), Loader=yaml.BaseLoader)


def allowed(path, job, *, private, automation='true', repository=None,
            repository_private=None, ref='refs/heads/main', event_name=None):
    configured_repository = INSTANCE['private_repository' if private else 'public_repository']
    github = SimpleNamespace(
        repository=configured_repository if repository is None else repository,
        ref=ref,
        event_name=event_name or ('issues' if job == 'apply' else 'workflow_dispatch'),
        triggering_actor='maintainer',
        event=SimpleNamespace(
            repository=SimpleNamespace(private=private if repository_private is None
                                       else repository_private),
            sender=SimpleNamespace(type='User', login='maintainer'),
            label=SimpleNamespace(name='news/withdraw'),
        ),
    )
    condition = workflow(path)['jobs'][job]['if']
    condition = condition.replace('&&', ' and ').replace('||', ' or ')
    condition = condition.replace(' == true', ' == True')
    # Evaluate only the repository-owned expression; event values remain data.
    return eval(condition, {'__builtins__': {}}, {
        'github': github, 'vars': SimpleNamespace(MDH_AUTOMATION_ENABLED=automation.casefold()),
        'contains': lambda collection, value: value in collection, 'fromJSON': json.loads,
    })


@pytest.mark.parametrize('path,job,private', CASES)
def test_operational_jobs_require_explicit_opt_in(path, job, private):
    condition = workflow(path)['jobs'][job]['if']
    assert "vars.MDH_AUTOMATION_ENABLED == 'true'" in condition
    assert allowed(path, job, private=private)
    # GitHub's string equality is case-insensitive, including repository vars.
    assert allowed(path, job, private=private, automation='TRUE')
    for unset_or_false in ('', 'false', 'False', 'yes', '1'):
        assert not allowed(path, job, private=private, automation=unset_or_false)


@pytest.mark.parametrize('path,job,private', CASES)
def test_copied_workflows_cannot_target_a_different_repository_when_enabled(path, job, private):
    assert not allowed(path, job, private=private, repository='another/unconfigured-fork')


@pytest.mark.parametrize('path,job', PRIVATE)
def test_private_automation_still_requires_a_private_main_branch(path, job):
    assert not allowed(path, job, private=True, repository_private=False)
    assert not allowed(path, job, private=True, ref='refs/heads/untrusted')


@pytest.mark.parametrize('path,job', [
    ('ops/review-queue/.github/workflows/collect.yml', 'collect'),
    ('ops/history/review-history.yml', 'enqueue'),
])
def test_previously_unguarded_collectors_only_accept_intended_entrypoints(path, job):
    assert allowed(path, job, private=True, event_name='schedule')
    assert allowed(path, job, private=True, event_name='workflow_dispatch')
    assert not allowed(path, job, private=True, event_name='push')


def test_gate_inventory_includes_every_private_template_and_the_public_mirror():
    templates = set((ROOT / 'ops/review-queue/.github/workflows').glob('*.yml'))
    templates |= set((ROOT / 'ops/official-statistics-review').glob('*.yml'))
    templates.add(ROOT / 'ops/history/review-history.yml')
    assert {ROOT / path for path, _ in PRIVATE} == templates
    assert (ROOT / 'ops/history/refresh-history.yml').read_bytes() == (
        ROOT / '.github/workflows/refresh-history.yml').read_bytes()


def test_fork_verification_remains_read_only_and_does_not_require_automation_opt_in():
    document = workflow('.github/workflows/ci.yml')
    assert document['permissions'] == {'contents': 'read'}
    assert set(document['on']) == {'push', 'pull_request', 'workflow_dispatch'}
    verify = document['jobs']['verify']
    assert 'if' not in verify and 'permissions' not in verify
    assert 'MDH_AUTOMATION_ENABLED' not in str(verify)
    assert 'secrets.' not in str(verify)
