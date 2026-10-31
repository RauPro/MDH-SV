"""Dependabot groups minor/patch updates per directory; majors and Actions stay individual."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = yaml.safe_load((ROOT / '.github/dependabot.yml').read_text())


def test_every_non_actions_entry_groups_exactly_minor_and_patch():
    for entry in CONFIG['updates']:
        if entry['package-ecosystem'] == 'github-actions':
            continue
        name = f"{entry['package-ecosystem']}-{entry['directory']}"
        groups = entry.get('groups')
        assert groups and len(groups) == 1, f'{name} needs exactly one group'
        group = next(iter(groups.values()))
        assert sorted(group['update-types']) == ['minor', 'patch'], name


def test_github_actions_updates_stay_individual():
    actions = [entry for entry in CONFIG['updates'] if entry['package-ecosystem'] == 'github-actions']
    assert actions and all('groups' not in entry for entry in actions)
