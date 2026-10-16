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
    assert value['permissions'] == {'contents': 'read'}
    assert value['jobs']['publish']['permissions'] == {'contents': 'write', 'issues': 'read'}
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
    pack = next(s for s in steps if 'publication_snapshot pack' in s.get('run', ''))
    assert steps.index(step) < steps.index(pack)
    evaluate = next(s for s in steps if 'mdh_pipeline.automatic_news evaluate' in s.get('run', ''))
    assert steps.index(step) < steps.index(evaluate) < steps.index(pack)
    assert evaluate['env']['MDH_GEMINI_BUDGET_CONFIG'] == '${{ vars.MDH_GEMINI_BUDGET_CONFIG }}'
    assert '--request-generations request-generations.json' in evaluate['run']
    assert '--excluded-ids excluded-locations.json' in evaluate['run']
    assert evaluate['id'] == 'automatic_validation' and evaluate['continue-on-error'] == 'true'
    assert evaluate['env']['VALIDATION_BATCH_SIZE'] == '4'
    assert 'github.event.workflow_run.id' in evaluate['env']['MDH_ALLOCATION_SOURCE_RUN_ID']
    assert '--batch-size "$VALIDATION_BATCH_SIZE"' in evaluate['run']
    assert value['jobs']['publish']['outputs']['evaluation_failed'] == "${{ steps.automatic_validation.outcome == 'failure' }}"
    failure_report = value['jobs']['report']
    assert failure_report['needs'] == ['publish', 'propose']
    assert failure_report['if'] == "always() && needs.publish.outputs.evaluation_failed == 'true'"
    assert failure_report['steps'] == [{'run': 'exit 1'}]
    assert 'inbox/drafts' not in pack['run']
    assert pack['run'].index('check-data.mjs') < pack['run'].index('publication_snapshot pack')


def test_public_bibliography_refresh_only_reconciles_receipts_without_private_access():
    for path in ['.github/workflows/refresh-history.yml', 'ops/history/refresh-history.yml']:
        value = workflow(path)
        text = '\n'.join(s.get('run', '') for s in value['jobs']['refresh']['steps'])
        assert 'sync-news-processing.mjs' in text
        assert 'apps/web/public/archive/processing.json' in text
        assert '--snapshot-complete' not in text and '--drafts' not in text
        assert 'inbox/drafts' not in text
