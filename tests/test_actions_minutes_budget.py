"""Scheduled private automation must stay inside the free Actions minutes budget."""
import json
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / 'ops/actions-minutes-budget.json'
MODEL = json.loads(MODEL_PATH.read_text())
VERCEL = json.loads((ROOT / 'vercel.json').read_text())
DISPATCHER = (ROOT / 'api/internal/hourly-ingestion.mjs').read_text()

EXTRACT = 'ops/review-queue/.github/workflows/extract.yml'
FALLBACK_EXTRACT = 'ops/review-queue/.github/workflows/extract-fallback.yml'
# A completed extract.yml run always launches the publisher, which in turn
# launches the reconciler; none of them may carry a schedule of their own.
NO_SELF_SCHEDULE = (
    EXTRACT,
    'ops/review-queue/.github/workflows/publish-locations.yml',
    'ops/review-queue/.github/workflows/reconcile-news-issues.yml',
)
# Private template -> installed workflow name in the model's per_run table.
# extract.yml cycles and extract-fallback.yml runs are priced by the
# cycle and fallback terms instead of per_run.
SCHEDULED_PER_RUN = {
    'ops/review-queue/.github/workflows/collect.yml': 'collect.yml',
    'ops/history/review-history.yml': 'history.yml',
    'ops/official-statistics-review/monitor.yml': 'official-statistics-monitor.yml',
}
PRIVATE_TEMPLATES = sorted(
    [str(p.relative_to(ROOT)) for directory in (
        'ops/review-queue/.github/workflows', 'ops/official-statistics-review')
     for p in (ROOT / directory).glob('*.yml')]
    + ['ops/history/review-history.yml'])


def workflow(path):
    return yaml.load((ROOT / path).read_text(), Loader=yaml.BaseLoader)


def schedules(path):
    return [entry['cron'] for entry in workflow(path)['on'].get('schedule') or []]


def vercel_schedule():
    crons = {cron['path']: cron['schedule'] for cron in VERCEL['crons']}
    return crons['/api/internal/hourly-ingestion']


def expand_field(field, low, high):
    """Expand one cron field: '*', lists, ranges and '*/n' or 'a-b/n' steps."""
    values = set()
    for part in field.split(','):
        step = 1
        if '/' in part:
            part, stride = part.split('/', 1)
            step = int(stride)
            assert step > 0
        if part == '*':
            first, last = low, high
        elif '-' in part:
            first, last = (int(bound) for bound in part.split('-', 1))
        else:
            first = last = int(part)
        assert low <= first <= last <= high
        values.update(range(first, last + 1, step))
    return values


def cron_times(expression):
    fields = expression.split()
    assert len(fields) == 5
    # Only minute/hour are modelled; any other field restricts the calendar and
    # would make the per-day estimate wrong, so the model must be revisited.
    assert fields[2:] == ['*', '*', '*'], 'day-of-month, month and day-of-week must stay *'
    return expand_field(fields[0], 0, 59), expand_field(fields[1], 0, 23)


def runs_per_day(expression):
    minutes, hours = cron_times(expression)
    return len(minutes) * len(hours)


def monthly_worst_case(cycles_per_day, fallback_runs_per_day, other_runs_per_day):
    cycle = (MODEL['cycle']['extract_fixed'] + MODEL['cycle']['publish']
             + MODEL['cycle']['reconcile']
             + MODEL['gemini_minutes_per_request'] * MODEL['max_requests_per_cycle'])
    daily = (cycles_per_day * cycle
             + fallback_runs_per_day * MODEL['fallback_dedupe_minutes']
             + sum(MODEL['per_run'][name] * count
                   for name, count in other_runs_per_day.items()))
    return MODEL['days_per_month'] * daily + MODEL['manual_reserve_minutes_per_month']


def measured_runs():
    cycles = runs_per_day(vercel_schedule())
    fallback = sum(runs_per_day(cron) for cron in schedules(FALLBACK_EXTRACT))
    others = {model_name: sum(runs_per_day(cron) for cron in schedules(template))
              for template, model_name in SCHEDULED_PER_RUN.items()}
    return cycles, fallback, others


def budget_ceiling():
    return MODEL['included_minutes_per_month'] * MODEL['target_share']


def test_scheduled_private_automation_stays_under_the_included_minutes_target():
    cycles, fallback, others = measured_runs()
    assert set(others) == set(MODEL['per_run']), 'per_run must cover every scheduled template'
    worst = monthly_worst_case(cycles, fallback, others)
    assert worst <= budget_ceiling()


def test_every_scheduled_private_template_is_covered_by_the_model():
    scheduled = {path for path in PRIVATE_TEMPLATES if workflow(path)['on'].get('schedule')}
    assert scheduled == set(SCHEDULED_PER_RUN) | {FALLBACK_EXTRACT}


def test_extract_fallback_hours_coincide_with_vercel_cycles():
    _, cycle_hours = cron_times(vercel_schedule())
    assert schedules(FALLBACK_EXTRACT), 'the scheduled extract fallback must exist'
    for expression in schedules(FALLBACK_EXTRACT):
        _, fallback_hours = cron_times(expression)
        assert fallback_hours <= cycle_hours


def test_chain_workflows_have_no_schedule():
    for path in NO_SELF_SCHEDULE:
        assert 'schedule' not in workflow(path)['on']


def test_cycle_request_cap_matches_dispatch_and_fallback_values():
    dispatch_batch = re.search(r"batch_size: '(\d+)'", DISPATCHER)
    assert dispatch_batch and int(dispatch_batch.group(1)) == MODEL['max_requests_per_cycle']
    for path in (EXTRACT, FALLBACK_EXTRACT):
        fallback = re.search(r"MDH_BATCH_SIZE: [^\n]*\|\| '(\d+)' \}\}", (ROOT / path).read_text())
        assert fallback and int(fallback.group(1)) == MODEL['max_requests_per_cycle']
    default = workflow(EXTRACT)['on']['workflow_dispatch']['inputs']['batch_size']['default']
    assert int(default) <= MODEL['max_requests_per_cycle']


def test_an_hourly_cycle_would_exceed_the_budget():
    _, fallback, others = measured_runs()
    hourly = monthly_worst_case(runs_per_day('7 * * * *'), fallback, others)
    assert hourly > budget_ceiling()


def extract_steps(path):
    return workflow(path)['jobs']['extract']['steps']


def extract_fallback_cron():
    return workflow(FALLBACK_EXTRACT)['on']['schedule'][0]['cron']


def test_late_schedule_guard_is_first_and_gates_every_step():
    for path in (EXTRACT, FALLBACK_EXTRACT):
        steps = extract_steps(path)
        window = steps[0]
        assert window['id'] == 'window'
        assert 'uses' not in window
        assert window['env'] == {
            'MDH_EVENT_NAME': '${{ github.event_name }}',
            'MDH_SCHEDULE': '${{ github.event.schedule }}',
        }
        for step in steps[1:]:
            assert step['if'] == "steps.window.outputs.on_time == 'true'"


def run_window_step(event, schedule, hour, tmp_path):
    shim = tmp_path / 'bin'
    shim.mkdir()
    # GitHub's default shell is bash -e; the shim stands in for `date -u +%H`.
    (shim / 'date').write_text(f'#!/bin/sh\nprintf \'%s\\n\' "{hour}"\n')
    (shim / 'date').chmod(stat.S_IRWXU)
    output = tmp_path / 'github_output'
    env = {
        **os.environ,
        'PATH': f"{shim}:{os.environ['PATH']}",
        'MDH_EVENT_NAME': event,
        'MDH_SCHEDULE': schedule,
        'GITHUB_OUTPUT': str(output),
    }
    subprocess.run(['bash', '-e', '-c', extract_steps(FALLBACK_EXTRACT)[0]['run']],
                   env=env, check=True, capture_output=True, text=True)
    return output.read_text()


@pytest.mark.parametrize('event,schedule,hour,expected', [
    ('schedule', extract_fallback_cron(), '01', 'true'),
    ('schedule', extract_fallback_cron(), '13', 'true'),
    ('schedule', extract_fallback_cron(), '00', 'false'),
    ('schedule', extract_fallback_cron(), '11', 'false'),
    ('schedule', extract_fallback_cron(), '14', 'false'),
    ('schedule', extract_fallback_cron(), '19', 'false'),
    ('schedule', '', '13', 'false'),
    ('workflow_dispatch', '', '19', 'true'),
])
def test_late_schedule_guard_behaviour(event, schedule, hour, expected, tmp_path):
    assert run_window_step(event, schedule, hour, tmp_path) == f'on_time={expected}\n'
