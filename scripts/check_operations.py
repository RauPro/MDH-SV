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
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]

# docs/GITHUB-ACTIONS.md template → installed workflow mapping.
TEMPLATE_MAP = {
    'ops/history/review-history.yml': '.github/workflows/history.yml',
    'ops/official-statistics-review/monitor.yml': '.github/workflows/official-statistics-monitor.yml',
    'ops/official-statistics-review/review.yml': '.github/workflows/official-statistics-review.yml',
}
REVIEW_QUEUE_TEMPLATES = 'ops/review-queue/.github/workflows'
CODE_REF_PLACEHOLDER = 'MDH_PUBLIC_CODE_REF'

# Workflows scheduled in GitHub or dispatched remotely on a fixed cadence.
SCHEDULED = {
    'public': {'.github/workflows/refresh-history.yml': '23 1,13'},
    'private': {
        'extract.yml': '7 1,13',          # Vercel cron dispatches it
        'extract-fallback.yml': '23 1,13',
        'collect.yml': '7 1,13',
        'review.yml': '7 1,13',
        'review-scope.yml': '7 1,13',
        'reconcile-news-issues.yml': '7 1,13',
    },
}

FINDINGS = []


def find(area, level, message):
    FINDINGS.append({'area': area, 'level': level, 'message': message})


def ok(area, message):
    find(area, 'ok', message)


def warning(area, message):
    find(area, 'warning', message)


def failure(area, message):
    find(area, 'failure', message)


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

    def get_optional(self, path):
        try:
            return self.get(path)
        except Exception:
            return None


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
        if workflow.get('can_approve_pull_request_reviews') != expected['can_approve_pull_request_reviews']:
            problems.append(f'can_approve_pull_request_reviews={workflow.get("can_approve_pull_request_reviews")}')
        if problems:
            failure('actions', f'{name}: ' + '; '.join(problems))
        else:
            ok('actions', f'{name}: default permissions read, no auto-approval')


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
    protection = client.get_optional(f'/repos/{public}/branches/main/protection')
    if protection is None:
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
    private_protection = client.get_optional(f'/repos/{private}/branches/main/protection')
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
        variables = {item['name']: item.get('value')
                 for item in repo_get(client, name, '/actions/variables').get('variables', [])}
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
                if config.get('rpm', 0) > 2:
                    problems.append(f"rpm={config.get('rpm')} > 2")
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
    listing = {item['name'] for item in repo_get(client, private_repo, '/contents/.github/workflows')}
    known_targets = set(files.values())
    for unknown in sorted(listing - {Path(t).name for t in known_targets}):
        failure('template-drift', f'unknown installed workflow: {unknown}')
    for template, installed in files.items():
        name = Path(installed).name
        if name not in listing:
            warning('template-drift', f'template never installed: {template}')
            continue
        template_text = (root / template).read_text()
        remote = repo_get(client, private_repo, f'/contents/{installed}')
        installed_text = base64.b64decode(remote['content']).decode()
        expected_text = substitute_refs(template_text, installed_text, public_repo)
        if expected_text == installed_text:
            ok('template-drift', f'{template} == {installed}')
        else:
            diff = '\n'.join(difflib.unified_diff(expected_text.splitlines(), installed_text.splitlines(),
                                                template, installed, lineterm=''))
            failure('template-drift', f'{template} differs from {installed}:\n{diff}')
        for ref in dict.fromkeys(installed_checkout_refs(installed_text, public_repo)):
            if re.fullmatch(r'[0-9a-f]{40}', ref):
                compare = repo_get(client, public_repo, f'/compare/{ref}...main')
                behind = compare.get('behind_by')
                if behind and behind > 0:
                    warning('template-drift', f'{installed} pins code {ref[:7]}, {behind} commits behind main')
                else:
                    ok('template-drift', f'{installed} pins code {ref[:7]}, up to date')


def check_history_copies(client, public_repo, root=ROOT):
    local_template = (root / 'ops/history/refresh-history.yml').read_text()
    local_installed = (root / '.github/workflows/refresh-history.yml').read_text()
    remote = repo_get(client, public_repo, '/contents/.github/workflows/refresh-history.yml')
    remote_text = base64.b64decode(remote['content']).decode()
    if local_template == local_installed == remote_text:
        ok('history-copies', 'refresh-history.yml identical locally and on public main')
    else:
        failure('history-copies', 'refresh-history.yml copies differ (local template, local installed, public main)')


def check_rulesets_and_credentials(client, repos):
    for role, repo in repos.items():
        name = repo['full_name']
        rulesets = client.get_optional(f'/repos/{name}/rulesets') or []
        ok('rulesets', f'{name}: {len(rulesets)} ruleset(s): ' + ', '.join(r.get('name', '?') for r in rulesets) or 'none')
        keys = client.get_optional(f'/repos/{name}/keys') or []
        for key in keys:
            ok('credentials', f'{name}: deploy key {key.get("title")} read_only={key.get("read_only")} created={key.get("created_at")}')
        collaborators = client.get_optional(f'/repos/{name}/collaborators') or []
        for collaborator in collaborators:
            ok('credentials', f'{name}: collaborator {collaborator.get("login")} permission={collaborator.get("role_name", collaborator.get("permissions"))}')
        hooks = client.get_optional(f'/repos/{name}/hooks') or []
        for hook in hooks:
            url = (hook.get('config') or {}).get('url', '')
            host = url.split('/')[2] if url.count('/') >= 2 else 'unknown'
            ok('credentials', f'{name}: webhook {host} events={hook.get("events")} active={hook.get("active")}')
        environments = (client.get_optional(f'/repos/{name}/environments') or {}).get('environments', [])
        for environment in environments:
            ok('credentials', f'{name}: environment {environment.get("name")}')


def cron_gap_hours(cron):
    minutes, hours, *_ = cron.split()
    hours = sorted(int(hour) for hour in hours.split(',') if hour.strip().isdigit())
    if not hours:
        return 24
    gaps = [hours[index + 1] - hours[index] for index in range(len(hours) - 1)] + [24 - hours[-1] + hours[0]]
    return max(gaps)


def check_run_health(client, repos, expectations):
    tolerance = expectations.get('schedule_delay_tolerance_hours', 8)
    for role, repo in repos.items():
        name = repo['full_name']
        for workflow, cron in SCHEDULED[role].items():
            runs = (client.get_optional(f'/repos/{name}/actions/workflows/{workflow}/runs?per_page=10')
                    or {}).get('workflow_runs', [])
            successes = [run for run in runs if run.get('conclusion') == 'success']
            window = timedelta(hours=cron_gap_hours(cron) + tolerance)
            if successes and iso_now() - parse_time(successes[0]['created_at']) <= window:
                ok('run-health', f'{name}:{workflow} last success {successes[0]["created_at"]} within {window}')
                continue
            started = [run for run in runs if run.get('status') == 'in_progress' or run.get('conclusion') is not None]
            if runs and all(run.get('conclusion') in ('startup_failure', 'failure') for run in runs[:3]) and not successes:
                warning('run-health', f'{name}:{workflow}: sin ejecuciones correctas recientes — probable cuota agotada')
            elif started and not successes:
                warning('run-health', f'{name}:{workflow}: sin ejecuciones correctas recientes')
            else:
                failure('run-health', f'{name}:{workflow}: last success outside {window} or none')


def run_checks(client, root=ROOT):
    FINDINGS.clear()
    expectations = json.loads((root / 'ops/operations-expectations.json').read_text())
    repos = dict(check_visibility(client, expectations))
    check_actions_policy(client, repos, expectations)
    check_protection(client, repos, expectations, root)
    check_variables(client, repos, expectations, root)
    check_templates(client, repos['private']['full_name'], repos['public']['full_name'], root)
    check_history_copies(client, repos['public']['full_name'], root)
    check_rulesets_and_credentials(client, repos)
    check_run_health(client, repos, expectations)
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
