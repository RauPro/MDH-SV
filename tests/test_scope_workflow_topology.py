"""Production must have one hourly classification owner and preserve human exclusions."""
from pathlib import Path
import re
from types import SimpleNamespace
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1] / 'ops/review-queue/.github/workflows'


def workflow(name):
    return yaml.load((ROOT / name).read_text(), Loader=yaml.BaseLoader)


def test_hourly_job_classifies_first_from_a_current_private_cache():
    value = workflow('extract.yml')
    assert value['on']['schedule'] == [{'cron': '23 * * * *'}]
    steps = value['jobs']['extract']['steps']
    private_checkout = next(s for s in steps if s.get('with', {}).get('path') == 'inbox')
    assert private_checkout['with']['ref'] == 'main'
    assert private_checkout['with']['persist-credentials'] == 'false'
    execution = next(s for s in steps if 'mdh_pipeline.gemini_review' in s.get('run', ''))
    assert '--scope-first --inbox-dir inbox' in execution['run']


def test_separate_scope_worker_is_manual_only_and_uses_the_same_private_queue():
    value = workflow('review-scope.yml')
    assert set(value['on']) == {'workflow_dispatch'}
    assert value['concurrency']['group'] == workflow('extract.yml')['concurrency']['group']
    execution = next(s for s in value['jobs']['scope']['steps'] if 'mdh_pipeline.scope_review_queue' in s.get('run', ''))
    assert '--excluded-ids scope-exclusions.json' in execution['run']
    assert '--scope-reviews inbox/scope-reviews --attempts inbox/scope-attempts' in execution['run']


def test_publisher_separates_news_withdrawals_from_automatic_location_overrides():
    value = workflow('publish-locations.yml')
    text = '\n'.join(s.get('run', '') for s in value['jobs']['publish']['steps'])
    assert 'editorial_excluded_ids(inbox, imported_ids=reviewed_ids)' in text
    assert 'excluded | reviewed_ids' in text
    assert '--reviewed-dossiers publication/data/reviewed/news_dossiers.json --scope-audits publication/data/selection/scope_audits.json --excluded-ids excluded-news.json' in text
    assert '--output publication/apps/web/public/archive/news.json --excluded-ids excluded-news.json' in text


def publisher_allowed(*, conclusion='success', name='Preparar fichas con Gemini',
                      path='.github/workflows/extract.yml', head_branch='main',
                      head_repository='RauPro/MDH-SV-review', private=True,
                      ref='refs/heads/main', event_name='workflow_run'):
    run = SimpleNamespace(conclusion=conclusion, name=name, path=path,
                          head_branch=head_branch, head_repository=SimpleNamespace(full_name=head_repository))
    github = SimpleNamespace(event=SimpleNamespace(repository=SimpleNamespace(private=private), workflow_run=run),
                             ref=ref, event_name=event_name, repository='RauPro/MDH-SV-review')
    # Evaluate this repository-owned boolean gate, never event data as code.
    condition = workflow('publish-locations.yml')['jobs']['publish']['if']
    condition = condition.replace('&&', ' and ').replace('||', ' or ')
    condition = re.sub(r'\btrue\b', 'True', condition)
    return eval(condition, {'__builtins__': {}}, {'github': github})


def test_validated_partial_extractor_results_publish_without_hiding_the_run_failure():
    assert publisher_allowed(conclusion='failure')
    assert publisher_allowed(event_name='workflow_dispatch')
    assert publisher_allowed(name='Revisar noticia', path='.github/workflows/review.yml')
    assert publisher_allowed(name='Reevaluar pertinencia de noticias', path='.github/workflows/review-scope.yml')
    steps = workflow('publish-locations.yml')['jobs']['publish']['steps']
    checkouts = [step['with'] for step in steps if step.get('uses') == 'actions/checkout@v4']
    assert next(step for step in checkouts if step['path'] == 'monitor')['ref'] == 'MDH_PUBLIC_CODE_REF'
    assert next(step for step in checkouts if step['path'] == 'inbox')['ref'] == 'main'
    assert not any('download-artifact' in step.get('uses', '') for step in steps)
    validation = next(step['run'] for step in steps if 'git push' in step.get('run', ''))
    assert validation.index('check-data.mjs') < validation.index('git push')
    assert validation.index('audit_release.py') < validation.index('git push')


@pytest.mark.parametrize('changes', [
    {'conclusion': 'cancelled'}, {'conclusion': 'timed_out'}, {'conclusion': 'skipped'},
    {'conclusion': 'failure', 'name': 'Revisar noticia', 'path': '.github/workflows/review.yml'},
    {'conclusion': 'failure', 'name': 'Reevaluar pertinencia de noticias', 'path': '.github/workflows/review-scope.yml'},
    {'conclusion': 'failure', 'path': '.github/workflows/other.yml'},
    {'head_branch': 'untrusted'}, {'head_repository': 'another/fork'},
    {'private': False}, {'ref': 'refs/heads/untrusted'}, {'event_name': 'push'},
])
def test_partial_publication_does_not_relax_other_event_or_repository_boundaries(changes):
    assert not publisher_allowed(**changes)
