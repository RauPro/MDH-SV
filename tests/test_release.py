"""Release versioning and reproducible-build contract."""
import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_every_versioned_surface_agrees():
    root = json.loads((ROOT / 'package.json').read_text())['version']
    web = json.loads((ROOT / 'apps/web/package.json').read_text())['version']
    python = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']
    code = re.search(r"CODE_VERSION = '([0-9.]+)'", (ROOT / 'server/version.mjs').read_text())
    assert code and len({root, web, python, code.group(1)}) == 1
