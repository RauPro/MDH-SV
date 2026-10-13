"""Scheduled statistics detection must never become an automatic public writer."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow(name):
    return yaml.load((ROOT / 'ops/official-statistics-review' / name).read_text(), Loader=yaml.BaseLoader)


def test_daily_statistics_worker_has_only_private_queue_access():
    value = workflow('monitor.yml')
    assert value['on']['schedule'] == [{'cron': '37 12 * * *'}]
    assert set(value['on']) == {'schedule', 'workflow_dispatch'}
    assert value['permissions'] == {'contents': 'write', 'issues': 'write'}
    job = value['jobs']['monitor']
    assert "github.repository == 'RauPro/MDH-SV-review'" in job['if']
    assert 'github.event.repository.private == true' in job['if']
    assert "github.ref == 'refs/heads/main'" in job['if']
    collector = next(s for s in job['steps'] if s.get('id') == 'collect')
    assert collector['continue-on-error'] == 'true'
    assert 'GH_TOKEN' not in collector['env']
    assert '--previous-state' in collector['run']
    assert any(s.get('if') == "steps.collect.outcome == 'failure'" and s.get('run') == 'exit 1' for s in job['steps'])


def test_templates_pin_code_and_have_no_public_push_or_model_secret():
    for name in ['monitor.yml', 'review.yml']:
        value = workflow(name)
        job = next(iter(value['jobs'].values()))
        checkouts = [s['with'] for s in job['steps'] if s.get('uses') == 'actions/checkout@v4']
        assert next(s for s in checkouts if s['path'] == 'monitor')['ref'] == 'MDH_PUBLIC_CODE_REF'
        assert next(s for s in checkouts if s['path'] == 'baseline')['sparse-checkout'] == 'data/official-statistics'
        assert all(s['persist-credentials'] == 'false' for s in checkouts)
        text = '\n'.join(s.get('run', '') for s in job['steps'])
        assert 'git push' not in text and 'git commit' not in text
        assert not any('secrets.' in str(s) for s in job['steps'])
        assert 'GEMINI' not in str(value)
        assert value['concurrency']['cancel-in-progress'] == 'false'
        assert value['concurrency']['group'] == 'official-statistics-review'


def test_approval_is_manual_and_receives_an_event_file_not_interpolated_commands():
    value = workflow('review.yml')
    assert set(value['on']) == {'workflow_dispatch'}
    inputs = value['on']['workflow_dispatch']['inputs']
    assert inputs['confirm_source']['default'] == 'false'
    assert inputs['decision']['default'] == 'reject'
    job = value['jobs']['review']
    assert "github.event.sender.type == 'User'" in job['if']
    assert 'github.triggering_actor == github.event.sender.login' in job['if']
    command = next(s['run'] for s in job['steps'] if 'official_statistics_review review' in s.get('run', ''))
    assert '--event "$GITHUB_EVENT_PATH"' in command
    assert '--schema-validator monitor/shared/official-statistics.mjs' in command
    assert '--baseline baseline/data/official-statistics/homicides.json' in command
    assert '${{' not in command
    publisher = yaml.load((ROOT / 'ops/review-queue/.github/workflows/publish-locations.yml').read_text(), Loader=yaml.BaseLoader)
    assert 'Consultar estadísticas oficiales' not in str(publisher['on'])
    assert 'Revisar estadísticas oficiales' not in str(publisher['on'])
