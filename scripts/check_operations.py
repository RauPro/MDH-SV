"""Read-only operations drift check across the public and private repositories.

Compares the live GitHub state with ops/operations-expectations.json: visibility,
Actions policy, main protection, variables, workflow template drift, rulesets,
credential metadata (never secret values) and scheduled-run health.

Token: a fine-grained PAT in GH_TOKEN with read access to both repositories for
Metadata, Contents, Actions, Administration, Variables, Deployments,
Environments, Webhooks (if available) and the collaborator list. The token is
read from the environment and never printed. The client refuses any non-GET
method and any path containing /secrets.

Usage:
    GH_TOKEN=… .venv/bin/python scripts/check_operations.py [--output PATH]
"""
import argparse
import base64
import ssl
import difflib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import yaml

ROOT = Path(__file__).resolve().parents[1]

# docs/GITHUB-ACTIONS.md template → installed workflow mapping.
TEMPLATE_MAP = {
    'ops/history/review-history.yml': '.github/workflows/history.yml',
    'ops/official-statistics-review/monitor.yml': '.github/workflows/official-statistics-monitor.yml',
    'ops/official-statistics-review/review.yml': '.github/workflows/official-statistics-review.yml',
}
REVIEW_QUEUE_TEMPLATES = 'ops/review-queue/.github/workflows'
CODE_REF_PLACEHOLDER = 'MDH_PUBLIC_CODE_REF'

FINDINGS = []


def find(area, level, message):
    FINDINGS.append({'area': area, 'level': level, 'message': message})


def ok(area, message):
    find(area, 'ok', message)


def warning(area, message):
    find(area, 'warning', message)


def failure(area, message):
    find(area, 'failure', message)


READ_ERROR = object()


class GitHub:
    """Read-only REST client. Never logs or returns the token."""

    def __init__(self, token):
        self.headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json',
                        'X-GitHub-Api-Version': '2022-11-28'}

    def request(self, method, path):
        assert method == 'GET', 'read-only: only GET requests are allowed'
        assert '/secrets' not in path, 'read-only: secrets endpoints are forbidden'
        request = Request(f'https://api.github.com{path}', method='GET', headers=self.headers)
        context = ssl.create_default_context()
        try:
            import certifi
        except ImportError:
            pass
        else:
            context.load_verify_locations(cafile=certifi.where())
        return json.loads(urlopen(request, timeout=30, context=context).read())

    def get(self, path):
        return self.request('GET', path)

    def get_optional(self, path, area=None, level='failure'):
        try:
            return self.get(path)
        except HTTPError as error:
            if error.code == 404:
                return None
            if area:
                find(area, level, f'{path} no se pudo leer (HTTP {error.code}); revisar permisos del token')
            return READ_ERROR
        except Exception as error:
            if area:
                find(area, level, f'{path} no se pudo leer ({type(error).__name__})')
            return READ_ERROR


def repo_get(client, repo, suffix=''):
    return client.get(f'/repos/{repo}{suffix}')


def iso_now():
    return datetime.now(timezone.utc)


def parse_time(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def check_visibility(client, expectations):
    for role, expected in expectations['repositories'].items():
        repo = repo_get(client, expected['name'])
        if repo['visibility'] != expected['visibility']:
            failure('visibility', f'{role} {repo["name_with_owner" if "name_with_owner" in repo else "full_name"]} visibility is {repo["visibility"]}, expected {expected["visibility"]}')
        else:
            ok('visibility', f'{role} repository {repo.get("full_name")} is {repo["visibility"]}')
        yield role, repo


def check_actions_policy(client, repos, expectations):
    expected = expectations['actions']
    for role, repo in repos.items():
        name = repo['full_name']
        permissions = repo_get(client, name, '/actions/permissions')
        if permissions.get('enabled') != expected['enabled']:
            failure('actions', f'{name}: actions enabled={permissions.get("enabled")}')
        elif permissions.get('sha_pinning_required') != expected['sha_pinning_required']:
            failure('actions', f'{name}: sha_pinning_required={permissions.get("sha_pinning_required")}')
        else:
            ok('actions', f'{name}: enabled with SHA pinning required')
        workflow = repo_get(client, name, '/actions/permissions/workflow')
        problems = []
        if workflow.get('default_workflow_permissions') != expected['default_workflow_permissions']:
            problems.append(f'default_workflow_permissions={workflow.get("default_workflow_permissions")}')
        can_approve = expected['can_approve_pull_request_reviews']
        want = can_approve[role] if isinstance(can_approve, dict) else can_approve
        if workflow.get('can_approve_pull_request_reviews') != want:
            problems.append(f'can_approve_pull_request_reviews={workflow.get("can_approve_pull_request_reviews")}')
        if problems:
            failure('actions', f'{name}: ' + '; '.join(problems))
        else:
            ok('actions', f'{name}: default permissions read, can_approve={can_approve[role] if isinstance(can_approve, dict) else can_approve}')


def normalize(value, template):
    """Project `value` onto the shape of `template` for comparison."""
    if isinstance(template, dict):
        if not isinstance(value, dict):
            return value
        return {key: normalize(value.get(key), expected) for key, expected in template.items()}
    if isinstance(template, list):
        if not isinstance(value, list):
            return value
        return [normalize(item, template[0]) for item in value]
    if isinstance(template, bool) and isinstance(value, dict) and set(value) <= {'url', 'enabled'}:
        return value['enabled']
    return value


def check_protection(client, repos, expectations, root=ROOT):
    public = repos['public']['full_name']
    expected_file = json.loads((root / '.github/main-protection.json').read_text())
    policy = expectations['public_main_protection']
    protection = client.get_optional(f'/repos/{public}/branches/main/protection', 'protection', 'failure')
    if protection is READ_ERROR:
        pass
    elif protection is None:
        failure('protection', f'{public}: main is not protected')
    else:
        diff = normalize(protection, expected_file)
        if diff != expected_file:
            failure('protection', f'{public}: main protection drifted: {json.dumps(diff, sort_keys=True)}')
        elif protection.get('enforce_admins', {}).get('enabled') != policy['enforce_admins']:
            failure('protection', f'{public}: enforce_admins is off')
        elif protection.get('restrictions') not in (None, policy['restrictions']):
            failure('protection', f'{public}: unexpected bypass allowances')
        else:
            ok('protection', f'{public}: main protection matches .github/main-protection.json')
    private = repos['private']['full_name']
    expected_private = expectations['private_main_protection']
    private_protection = client.get_optional(f'/repos/{private}/branches/main/protection', 'protection', 'failure')
    if private_protection is READ_ERROR:
        return
    is_protected = private_protection is not None
    if is_protected != expected_private['protected']:
        failure('protection', f'{private}: main protected={is_protected}, expected {expected_private["protected"]}')
    elif expected_private['status'] == 'accepted':
        warning('protection', f'{private}: main unprotected (accepted): {expected_private["reason"]}')
    else:
        ok('protection', f'{private}: main protection as expected')


def check_variables(client, repos, expectations, root=ROOT):
    budget = json.loads((root / 'ops/actions-minutes-budget.json').read_text())
    model_var = {}
    for role, repo in repos.items():
        name = repo['full_name']
        raw = client.get_optional(f'/repos/{name}/actions/variables', 'variables', 'failure')
        if raw is READ_ERROR:
            continue
        variables = {item['name']: item.get('value') for item in (raw or {}).get('variables', [])}
        model_var[role] = variables.get('GEMINI_MODEL')
        for variable, rule in expectations['variables'].items():
            if role not in rule['repositories']:
                continue
            value = variables.get(variable)
            if value is None:
                failure('variables', f'{name}: variable {variable} is missing')
            elif 'equals' in rule and value != rule['equals']:
                failure('variables', f'{name}: variable {variable} != {rule["equals"]}')
            elif rule.get('budget_check'):
                try:
                    config = json.loads(value)
                except json.JSONDecodeError:
                    failure('variables', f'{name}: {variable} is not valid JSON')
                    continue
                problems = []
                google = rule.get('google_free_tier', {})
                if config.get('rpm', 0) > rule.get('max_rpm', 2):
                    problems.append(f"rpm={config.get('rpm')} > max_rpm={rule.get('max_rpm', 2)}")
                if google.get('rpm') and config.get('rpm', 0) > google['rpm']:
                    problems.append(f"rpm={config.get('rpm')} above the Google free-tier {google['rpm']}/min")
                if google.get('rpd') and config.get('daily_budget', 0) >= google['rpd']:
                    problems.append(f"daily_budget={config.get('daily_budget')} >= Google free-tier {google['rpd']}/day")
                if config.get('rpd') is not None:
                    if config.get('daily_budget', 0) >= config['rpd']:
                        problems.append(f"daily_budget={config.get('daily_budget')} >= rpd={config['rpd']}")
                    if google.get('rpd') and config['rpd'] > google['rpd']:
                        problems.append(f"rpd={config['rpd']} above the Google free-tier {google['rpd']}/day")
                if config.get('batch_ceiling', 0) < budget['max_requests_per_cycle']:
                    problems.append(f"batch_ceiling={config.get('batch_ceiling')} < max_requests_per_cycle={budget['max_requests_per_cycle']}")
                if config.get('model') != model_var.get(role):
                    problems.append('model differs from GEMINI_MODEL')
                if problems:
                    failure('variables', f'{name}: {variable} breaks the budget model: ' + '; '.join(problems))
                else:
                    ok('variables', f'{name}: {variable} inside the budget model')
            else:
                ok('variables', f'{name}: variable {variable} as expected')


def template_files(root=ROOT):
    files = {key: value for key, value in TEMPLATE_MAP.items() if (root / key).exists()}
    for path in sorted((root / REVIEW_QUEUE_TEMPLATES).glob('*.yml')):
        files[str(path.relative_to(root))] = f'.github/workflows/{path.name}'
    return files


def installed_checkout_refs(installed_text, public_repo):
    """ref: values of checkouts of the public repository, in order."""
    refs = []
    repository = None
    for line in installed_text.splitlines():
        stripped = line.strip().removeprefix('-').strip()
        if stripped.startswith('uses: actions/checkout@'):
            repository = '__checkout__'
        elif stripped.startswith('uses:'):
            repository = None
        if repository is not None and stripped.startswith('repository:'):
            repository = stripped.split(':', 1)[1].strip().strip('"\'')
        if repository == public_repo and stripped.startswith('ref:'):
            refs.append(stripped.split(':', 1)[1].strip().strip('"\''))
    return refs


def substitute_refs(template_text, installed_text, public_repo):
    # Every placeholder takes the pinned code SHA, in checkout order; env-var
    # placeholders after the checkouts reuse the last SHA.
    refs = [ref for ref in installed_checkout_refs(installed_text, public_repo)
            if re.fullmatch(r'[0-9a-f]{40}', ref)]
    parts = template_text.split(CODE_REF_PLACEHOLDER)
    result = parts[0]
    for index, part in enumerate(parts[1:]):
        result += (refs[min(index, len(refs) - 1)] if refs else CODE_REF_PLACEHOLDER) + part
    return result


def check_templates(client, private_repo, public_repo, root=ROOT):
    files = template_files(root)
    installed_texts = {}
    listing_data = client.get_optional(f'/repos/{private_repo}/contents/.github/workflows', 'template-drift', 'failure')
    if listing_data is READ_ERROR:
        return installed_texts
    if listing_data is None:
        failure('template-drift', f'/repos/{private_repo}/contents/.github/workflows does not exist')
        return installed_texts
    listing = {item['name'] for item in listing_data}
    for unknown in sorted(listing - {Path(t).name for t in files.values()}):
        failure('template-drift', f'unknown installed workflow: {unknown}')
    for template, installed in files.items():
        name = Path(installed).name
        if name not in listing:
            warning('template-drift', f'template never installed: {template}')
            continue
        template_text = (root / template).read_text()
        remote = client.get_optional(f'/repos/{private_repo}/contents/{installed}', 'template-drift', 'failure')
        if remote is READ_ERROR:
            continue
        if remote is None:
            failure('template-drift', f'{installed} is listed but missing at /repos/{private_repo}/contents/{installed}')
            continue
        installed_text = base64.b64decode(remote['content']).decode()
        installed_texts[name] = installed_text
        expected_text = substitute_refs(template_text, installed_text, public_repo)
        if expected_text == installed_text:
            ok('template-drift', f'{template} == {installed}')
        else:
            diff = '\n'.join(difflib.unified_diff(expected_text.splitlines(), installed_text.splitlines(),
                                                template, installed, lineterm=''))
            failure('template-drift', f'{template} differs from {installed}:\n{diff}')
        for ref in dict.fromkeys(installed_checkout_refs(installed_text, public_repo)):
            if re.fullmatch(r'[0-9a-f]{40}', ref):
                compare = client.get_optional(f'/repos/{public_repo}/compare/{ref}...main', 'template-drift', 'failure')
                if compare is READ_ERROR:
                    continue
                if compare is None:
                    failure('template-drift', f'{installed} pins code {ref[:7]}, which does not exist in {public_repo}')
                    continue
                status, ahead = compare.get('status'), compare.get('ahead_by')
                if status == 'identical':
                    ok('template-drift', f'{installed} pins code {ref[:7]}, up to date')
                elif status == 'ahead':
                    warning('template-drift', f'{installed} pins code {ref[:7]}, {ahead} commits behind main')
                else:
                    failure('template-drift', f'{installed} pins code {ref[:7]}, which is not contained in main ({status})')
    return installed_texts


def check_history_copies(client, public_repo, root=ROOT):
    local_template = (root / 'ops/history/refresh-history.yml').read_text()
    local_installed = (root / '.github/workflows/refresh-history.yml').read_text()
    remote = client.get_optional(f'/repos/{public_repo}/contents/.github/workflows/refresh-history.yml', 'history-copies', 'failure')
    if remote is READ_ERROR:
        return
    if remote is None:
        failure('history-copies', f'/repos/{public_repo}/contents/.github/workflows/refresh-history.yml does not exist')
        return
    remote_text = base64.b64decode(remote['content']).decode()
    if local_template == local_installed == remote_text:
        ok('history-copies', 'refresh-history.yml identical locally and on public main')
    else:
        failure('history-copies', 'refresh-history.yml copies differ (local template, local installed, public main)')


def check_rulesets_and_credentials(client, repos):
    for role, repo in repos.items():
        name = repo['full_name']
        rulesets = client.get_optional(f'/repos/{name}/rulesets', 'rulesets', 'warning')
        if rulesets is not READ_ERROR:
            rulesets = rulesets or []
            names = ', '.join(r.get('name', '?') for r in rulesets) or 'ninguno'
            ok('rulesets', f'{name}: {len(rulesets)} ruleset(s): {names}')
        keys = client.get_optional(f'/repos/{name}/keys', 'credentials', 'warning')
        if keys is not READ_ERROR:
            for key in keys or []:
                ok('credentials', f'{name}: deploy key {key.get("title")} read_only={key.get("read_only")} created={key.get("created_at")}')
        collaborators = client.get_optional(f'/repos/{name}/collaborators', 'credentials', 'warning')
        if collaborators is not READ_ERROR:
            for collaborator in collaborators or []:
                ok('credentials', f'{name}: collaborator {collaborator.get("login")} permission={collaborator.get("role_name", collaborator.get("permissions"))}')
        hooks = client.get_optional(f'/repos/{name}/hooks', 'credentials', 'warning')
        if hooks is not READ_ERROR:
            for hook in hooks or []:
                url = (hook.get('config') or {}).get('url', '')
                host = url.split('/')[2] if url.count('/') >= 2 else 'unknown'
                ok('credentials', f'{name}: webhook {host} events={hook.get("events")} active={hook.get("active")}')
        environments = client.get_optional(f'/repos/{name}/environments', 'credentials', 'warning')
        if environments is not READ_ERROR:
            for environment in (environments or {}).get('environments', []):
                ok('credentials', f'{name}: environment {environment.get("name")}')


def cron_gap_hours(cron):
    parts = cron.split()
    if len(parts) < 2:
        return 24
    hours = parts[1]
    if hours == '*':
        return 1
    if hours.startswith('*/'):
        return int(hours[2:])
    if ',' in hours:
        values = sorted(int(value) for value in hours.split(',') if value.strip().isdigit())
        if not values:
            return 24
        gaps = [values[index + 1] - values[index] for index in range(len(values) - 1)] + [24 - values[-1] + values[0]]
        return max(gaps)
    return 24


def workflow_schedule(text):
    """Cron list plus whether the workflow is chained through workflow_run."""
    document = yaml.load(text, Loader=yaml.BaseLoader)
    if not isinstance(document, dict) or not isinstance(document.get('on'), dict):
        return [], False
    on = document['on']
    crons = [entry.get('cron') for entry in on.get('schedule') or [] if isinstance(entry, dict)]
    return [cron for cron in crons if cron], 'workflow_run' in on


def vercel_cron(root):
    """Cadence of the Vercel cron that dispatches the private extract cycle."""
    config = json.loads((root / 'vercel.json').read_text())
    for cron in config.get('crons', []):
        if cron.get('path') == '/api/internal/hourly-ingestion':
            return cron.get('schedule')
    return '7 1,13'


def check_run_health(client, repos, expectations, installed, root=ROOT):
    tolerance = expectations.get('schedule_delay_tolerance_hours', 8)
    schedules = {'public': {}, 'private': {}}
    for path in sorted((root / '.github/workflows').glob('*.yml')):
        crons, _ = workflow_schedule(path.read_text())
        if crons:
            schedules['public'][path.name] = max(crons, key=cron_gap_hours)
    extract_cron = vercel_cron(root)
    for name, text in installed.items():
        crons, chained = workflow_schedule(text)
        if crons:
            schedules['private'][name] = max(crons, key=cron_gap_hours)
        elif chained or name == 'extract.yml':
            # extract runs on the Vercel dispatch cadence; chained jobs share it
            schedules['private'][name] = extract_cron
    known = {'public': {path.name for path in (root / '.github/workflows').glob('*.yml')},
             'private': set(installed)}
    for role, repo in repos.items():
        name = repo['full_name']
        for workflow in sorted(known[role] - set(schedules[role])):
            ok('run-health', f'{name}:{workflow}: no programado')
        for workflow, cron in sorted(schedules[role].items()):
            data = client.get_optional(f'/repos/{name}/actions/workflows/{workflow}/runs?per_page=10',
                                       'run-health', 'failure')
            if data is READ_ERROR:
                continue
            runs = (data or {}).get('workflow_runs', [])
            successes = [run for run in runs if run.get('conclusion') == 'success']
            window = timedelta(hours=cron_gap_hours(cron) + tolerance)
            if successes and iso_now() - parse_time(successes[0]['created_at']) <= window:
                ok('run-health', f'{name}:{workflow} last success {successes[0]["created_at"]} within {window}')
            elif not runs:
                warning('run-health', f'{name}:{workflow}: sin ejecuciones registradas')
            else:
                # A quota-blocked run fails without starting: it has jobs, and every
                # job has no steps and no runner. Cancelled runs or missing job data
                # prove nothing about the quota, so they stay failures.
                not_started = True
                for run in runs[:3]:
                    if run.get('id') is None or run.get('conclusion') not in ('failure', 'startup_failure'):
                        not_started = False
                        break
                    jobs = client.get_optional(f'/repos/{name}/actions/runs/{run["id"]}/jobs',
                                               'run-health', 'failure')
                    if jobs is READ_ERROR:
                        not_started = None
                        break
                    job_list = (jobs or {}).get('jobs', [])
                    if not job_list or any(job.get('steps') or job.get('runner_name') for job in job_list):
                        not_started = False
                        break
                if not_started is True:
                    warning('run-health', f'{name}:{workflow}: sin ejecuciones correctas recientes — probable cuota agotada')
                elif not_started is False:
                    failure('run-health', f'{name}:{workflow}: last success outside {window} or none')


def run_checks(client, root=ROOT):
    FINDINGS.clear()
    expectations = json.loads((root / 'ops/operations-expectations.json').read_text())
    repos = dict(check_visibility(client, expectations))
    check_actions_policy(client, repos, expectations)
    check_protection(client, repos, expectations, root)
    check_variables(client, repos, expectations, root)
    installed = check_templates(client, repos['private']['full_name'], repos['public']['full_name'], root)
    check_history_copies(client, repos['public']['full_name'], root)
    check_rulesets_and_credentials(client, repos)
    check_run_health(client, repos, expectations, installed, root)
    return {'generated_at': iso_now().isoformat(), 'findings': FINDINGS,
            'summary': {'ok': sum(1 for f in FINDINGS if f['level'] == 'ok'),
                        'warnings': sum(1 for f in FINDINGS if f['level'] == 'warning'),
                        'failures': sum(1 for f in FINDINGS if f['level'] == 'failure')}}


def human_summary(report):
    lines = []
    by_level = {'failure': 'FALLO', 'warning': 'AVISO', 'ok': 'OK'}
    for finding in report['findings']:
        lines.append(f"[{by_level[finding['level']]}] {finding['area']}: {finding['message']}")
    summary = report['summary']
    lines.append(f"Resumen: {summary['failures']} fallos, {summary['warnings']} avisos, {summary['ok']} correctas.")
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    import os
    token = os.environ.get('GH_TOKEN')
    if not token:
        print('check_operations: GH_TOKEN is not set', file=sys.stderr)
        return 2
    report = run_checks(GitHub(token))
    text = json.dumps(report, indent=2, sort_keys=True) + '\n'
    if args.output:
        args.output.write_text(text)
    else:
        print(text)
    print(human_summary(report), file=sys.stderr)
    return 1 if report['summary']['failures'] else 0


if __name__ == '__main__':
    sys.exit(main())
