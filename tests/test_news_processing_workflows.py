"""A processing label cannot publish a private draft or infer progress from sparse files."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow(path):
    return yaml.load((ROOT / path).read_text(), Loader=yaml.BaseLoader)


def test_processing_projection_requires_a_verified_full_private_inventory():
    value = workflow('ops/review-queue/.github/workflows/publish-locations.yml')
    # The evaluator persists receipts and quota reservations in the private repo;
    # the processing projection itself remains credential-free.
    assert value['permissions'] == {'contents': 'write', 'issues': 'read'}
    steps = value['jobs']['publish']['steps']
    checkout = next(s['with'] for s in steps if s.get('with', {}).get('path') == 'inbox')
    assert 'sparse-checkout' not in checkout
    assert checkout['ref'] == 'main' and checkout['persist-credentials'] == 'false'
    step = next(s for s in steps if 'mdh_pipeline.news_processing' in s.get('run', ''))
    assert 'GH_TOKEN' not in step.get('env', {}) and 'GEMINI_API_KEY' not in step.get('env', {})
    assert "'ls-files', '-z'" in step['run'] and 'expected == actual' in step['run']
    assert "'diff', '--quiet', 'HEAD'" in step['run']
    assert step['run'].index('expected == actual') < step['run'].index('--snapshot-complete')
    assert '--output publication/apps/web/public/archive/processing.json' in step['run']
    publish = next(s for s in steps if 'git push' in s.get('run', ''))
    assert steps.index(step) < steps.index(publish)
    evaluate = next(s for s in steps if 'mdh_pipeline.automatic_news evaluate' in s.get('run', ''))
    assert steps.index(step) < steps.index(evaluate) < steps.index(publish)
    assert evaluate['env']['MDH_GEMINI_BUDGET_CONFIG'] == '${{ vars.MDH_GEMINI_BUDGET_CONFIG }}'
    assert '--request-generations request-generations.json' in evaluate['run']
    assert '--excluded-ids excluded-locations.json' in evaluate['run']
    assert evaluate['id'] == 'automatic_validation' and evaluate['continue-on-error'] == 'true'
    assert evaluate['env']['VALIDATION_BATCH_SIZE'] == '4'
    assert 'github.event.workflow_run.id' in evaluate['env']['MDH_ALLOCATION_SOURCE_RUN_ID']
    assert '--batch-size "$VALIDATION_BATCH_SIZE"' in evaluate['run']
    failure_report = next(s for s in steps if "steps.automatic_validation.outcome == 'failure'" in s.get('if', ''))
    assert steps.index(failure_report) > steps.index(publish)
    assert 'apps/web/public/archive/processing.json' in publish['run']
    assert 'inbox/drafts' not in publish['run']
    assert publish['run'].index('check-data.mjs') < publish['run'].index('git push')


def test_public_bibliography_refresh_only_reconciles_receipts_without_private_access():
    for path in ['.github/workflows/refresh-history.yml', 'ops/history/refresh-history.yml']:
        value = workflow(path)
        text = '\n'.join(s.get('run', '') for s in value['jobs']['refresh']['steps'])
        assert 'sync-news-processing.mjs' in text
        assert 'apps/web/public/archive/processing.json' in text
        assert '--snapshot-complete' not in text and '--drafts' not in text
        assert 'inbox/drafts' not in text
