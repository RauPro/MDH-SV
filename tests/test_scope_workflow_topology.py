"""Production must have one hourly classification owner and preserve human exclusions."""
from pathlib import Path
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
