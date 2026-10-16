"""A complete temporary fork renders local destinations without activation."""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('configure_instance', ROOT / 'scripts/configure_instance.py')
configure_instance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(configure_instance)
FORK = json.loads((Path(__file__).parent / 'fixtures/instance-config-cases.json').read_text())['base']


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / 'checkout'
    paths = ('instance.json', *configure_instance.STATIC_PATHS, 'scripts/configure_instance.py',
             'apps/pipeline/src/mdh_pipeline/instance.py', 'apps/pipeline/src/mdh_pipeline/__init__.py',
             'docs/PRIVATE-REPORTING.md')
    for relative in paths:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    (root / 'untouched.txt').write_text('Unrelated contributor work.\n')
    return root


@pytest.fixture
def source(tmp_path):
    path = tmp_path / 'fork.json'
    path.write_text(json.dumps(FORK))
    return path


def snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob('*') if path.is_file() and not path.is_symlink()}


def contracts(workflow):
    return {
        'actions': re.findall(r'uses: ([^\s]+)', workflow),
        'refs': re.findall(r'^\s+ref: (.+)$', workflow, flags=re.MULTILINE),
        'secrets': re.findall(r'secrets\.[A-Za-z0-9_]+', workflow),
        'activation': workflow.count("vars.MDH_AUTOMATION_ENABLED == 'true'"),
        'private': workflow.count('github.event.repository.private == true'),
        'main': workflow.count("github.ref == 'refs/heads/main'"),
    }


def test_full_fork_render_preserves_security_boundaries_and_history(checkout, source):
    old = snapshot(checkout)
    changed = configure_instance.configure(checkout, source=source)
    assert 'instance.json' in changed and 'SECURITY.md' in changed
    actual = json.loads((checkout / 'instance.json').read_text())
    assert actual == FORK
    for relative, (role, checkouts, guards) in configure_instance.WORKFLOWS.items():
        rendered = (checkout / relative).read_text()
        assert contracts(rendered) == contracts(old[relative].decode())
        repository = FORK['private_repository'] if role == 'private' else FORK['public_repository']
        assert rendered.count("github.repository == '" + repository + "'") == guards
        assert len(re.findall(r'^\s+repository: ' + re.escape(FORK['public_repository']) + '$',
                              rendered, flags=re.MULTILINE)) == checkouts
    security = (checkout / 'SECURITY.md').read_text()
    original = old['SECURITY.md'].decode()
    assert security.split(configure_instance.START)[0] == original.split(configure_instance.START)[0]
    assert security.split(configure_instance.END)[1] == original.split(configure_instance.END)[1]
    assert 'https://github.com/orgs/ExampleOrg/teams/editors' in security
    assert 'Configurar estos destinos no demuestra que los canales estén operativos.' in security
    assert '* @ExampleOrg/editors\n' in (checkout / '.github/CODEOWNERS').read_text()
    assert (checkout / 'docs/PRIVATE-REPORTING.md').read_bytes() == old['docs/PRIVATE-REPORTING.md']
    assert (checkout / 'untouched.txt').read_bytes() == old['untouched.txt']
    for relative in (*configure_instance.FORMS, configure_instance.CONTACTS):
        rendered = (checkout / relative).read_text()
        assert FORK['editorial_email'] in rendered
        assert 'https://github.com/' + FORK['public_repository'] + '/security/advisories/new' in rendered
    assert configure_instance.configure(checkout, source=source) == ()
    assert configure_instance.configure(checkout, check=True) == ()


def test_prefix_collisions_never_rewrite_new_or_private_repository(checkout, source):
    first = {**FORK, 'public_repository': 'SameOwner/project',
             'private_repository': 'SameOwner/project-private'}
    source.write_text(json.dumps(first))
    configure_instance.configure(checkout, source=source)
    second = {**first, 'public_repository': 'SameOwner/project-private-fork',
              'private_repository': 'SameOwner/project-private-fork-private',
              'reporting_responsible': '@SameOwner'}
    source.write_text(json.dumps(second))
    configure_instance.configure(checkout, source=source)
    for relative, (role, _, guards) in configure_instance.WORKFLOWS.items():
        content = (checkout / relative).read_text()
        expected = second['private_repository'] if role == 'private' else second['public_repository']
        assert content.count("github.repository == '" + expected + "'") == guards
        assert 'project-private-fork-private-fork' not in content
    assert 'https://github.com/SameOwner)' in (checkout / 'SECURITY.md').read_text()
    assert configure_instance.configure(checkout, check=True) == ()


def test_approved_private_code_refs_survive_rendering(checkout, source):
    for relative in configure_instance.WORKFLOWS:
        path = checkout / relative
        path.write_text(path.read_text().replace('MDH_PUBLIC_CODE_REF', 'abcdef1234' * 4))
    before = snapshot(checkout)
    configure_instance.configure(checkout, source=source)
    for relative in configure_instance.WORKFLOWS:
        assert contracts((checkout / relative).read_text()) == contracts(before[relative].decode())


def test_check_reports_destination_drift_without_writing(checkout, source):
    configure_instance.configure(checkout, source=source)
    relative = 'ops/review-queue/.github/workflows/publish-locations.yml'
    path = checkout / relative
    path.write_text(path.read_text().replace('repository: ' + FORK['public_repository'],
                                            'repository: unexpected/public', 1))
    before = snapshot(checkout)
    assert configure_instance.configure(checkout, check=True) == (relative,)
    assert snapshot(checkout) == before


@pytest.mark.parametrize('mutation', [
    {'public_repository': 'same/repo', 'private_repository': 'same/repo'},
    {'public_url': 'https://credentials@example.org'},
    {'mcp_allowed_hosts': ['wrong.example.org']},
    {'GEMINI_API_KEY': 'fixture'},
])
def test_invalid_input_prepares_no_mutation(checkout, source, mutation):
    before = snapshot(checkout)
    source.write_text(json.dumps({**FORK, **mutation}))
    with pytest.raises(configure_instance.InstanceConfigurationError):
        configure_instance.configure(checkout, source=source)
    assert snapshot(checkout) == before


@pytest.mark.parametrize('problem', ['missing-marker', 'extra-workflow', 'missing-activation', 'missing-private'])
def test_late_template_validation_fails_before_any_destination_changes(checkout, source, problem):
    if problem == 'missing-marker':
        path = checkout / 'SECURITY.md'
        path.write_text(path.read_text().replace(configure_instance.END, ''))
    elif problem == 'extra-workflow':
        (checkout / '.github/workflows/unknown.yml').write_text('name: Unreviewed workflow\n')
    else:
        path = checkout / 'ops/review-queue/.github/workflows/review.yml'
        old = "vars.MDH_AUTOMATION_ENABLED == 'true'" if problem == 'missing-activation' else 'github.event.repository.private == true'
        path.write_text(path.read_text().replace(old, 'true'))
    before = snapshot(checkout)
    with pytest.raises(configure_instance.ConfigurationError):
        configure_instance.configure(checkout, source=source)
    assert snapshot(checkout) == before


@pytest.mark.parametrize('relative', ['instance.json', 'SECURITY.md', '.github/CODEOWNERS'])
def test_symlink_surface_is_rejected_without_touching_its_target(checkout, source, relative, tmp_path):
    path = checkout / relative
    target = tmp_path / 'outside'
    target.write_bytes(path.read_bytes())
    original = target.read_bytes()
    path.unlink()
    path.symlink_to(target)
    before = snapshot(checkout)
    with pytest.raises((configure_instance.ConfigurationError, configure_instance.InstanceConfigurationError)):
        configure_instance.configure(checkout, source=source)
    assert snapshot(checkout) == before and target.read_bytes() == original


def test_symlink_parent_and_relative_source_are_rejected(checkout, source, tmp_path):
    github = checkout / '.github'
    outside = tmp_path / 'github-copy'
    github.rename(outside)
    github.symlink_to(outside, target_is_directory=True)
    before = snapshot(outside)
    with pytest.raises(configure_instance.ConfigurationError, match='symlink'):
        configure_instance.configure(checkout, source=source)
    assert snapshot(outside) == before
    with pytest.raises(configure_instance.InstanceConfigurationError):
        configure_instance.configure(checkout, source=Path('fork.json'))


def test_preparation_detects_concurrent_edits_and_does_not_overwrite_them(checkout, source, monkeypatch):
    original_stage = configure_instance._stage
    relative = '.github/CODEOWNERS'
    owner = checkout / relative
    changed = False

    def change_while_staging(*args, **kwargs):
        nonlocal changed
        result = original_stage(*args, **kwargs)
        if not changed:
            owner.write_text(owner.read_text() + '# Concurrent contributor note\n')
            changed = True
        return result

    before = snapshot(checkout)
    monkeypatch.setattr(configure_instance, '_stage', change_while_staging)
    with pytest.raises(configure_instance.ConfigurationError, match='changed_during_preparation'):
        configure_instance.configure(checkout, source=source)
    expected = deepcopy(before)
    expected[relative] += b'# Concurrent contributor note\n'
    assert snapshot(checkout) == expected
    assert not list(checkout.rglob('*.instance-*'))


def test_failed_replacement_restores_originals_and_file_modes(checkout, source, monkeypatch):
    security = checkout / 'SECURITY.md'
    security.chmod(0o640)
    before = snapshot(checkout)
    replace = Path.replace
    replacements = 0

    def fail_second_replacement(path, destination):
        nonlocal replacements
        replacements += 1
        if replacements == 2:
            raise OSError('simulated filesystem interruption')
        return replace(path, destination)

    monkeypatch.setattr(Path, 'replace', fail_second_replacement)
    with pytest.raises(OSError, match='simulated filesystem interruption'):
        configure_instance.configure(checkout, source=source)
    assert snapshot(checkout) == before
    assert security.stat().st_mode & 0o777 == 0o640
    assert not list(checkout.rglob('*.instance-*'))


def test_command_is_root_anchored_and_ignores_runtime_override(checkout, source, tmp_path):
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    (elsewhere / 'instance.json').write_text('not configuration')
    command = [sys.executable, str(checkout / 'scripts/configure_instance.py')]
    environment = {**os.environ, 'MDH_INSTANCE_FILE': str(elsewhere / 'instance.json'),
                   'PYTHONDONTWRITEBYTECODE': '1'}
    result = subprocess.run([*command, '--from', str(source)], cwd=elsewhere, env=environment,
                            capture_output=True, text=True, check=False, timeout=20)
    assert result.returncode == 0, result.stderr
    before = snapshot(checkout)
    result = subprocess.run([*command, '--check'], cwd=elsewhere, env=environment,
                            capture_output=True, text=True, check=False, timeout=20)
    assert result.returncode == 0, result.stderr
    assert snapshot(checkout) == before
    assert (elsewhere / 'instance.json').read_text() == 'not configuration'
    path = checkout / '.github/CODEOWNERS'
    path.write_text(path.read_text().replace('@ExampleOrg/editors', '@unrelated'))
    result = subprocess.run([*command, '--check'], cwd=elsewhere, env=environment,
                            capture_output=True, text=True, check=False, timeout=20)
    assert result.returncode == 1 and '.github/CODEOWNERS' in result.stderr
