"""The checked-in lint gates must reject newly introduced correctness defects."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_python_lint_rejects_undefined_name(tmp_path):
    source = tmp_path / 'broken.py'
    source.write_text('print(quality_gate_missing_name)\n')
    result = subprocess.run([sys.executable, '-m', 'ruff', 'check', '--config',
                             str(ROOT / 'pyproject.toml'), str(source)],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert 'F821' in result.stdout


def test_root_javascript_lint_rejects_unused_binding(tmp_path):
    source = tmp_path / 'broken.mjs'
    source.write_text('const qualityGateUnusedBinding = 1;\n')
    result = subprocess.run([str(ROOT / 'node_modules/.bin/oxlint'), '--config',
                             str(ROOT / '.oxlintrc.json'), '--deny-warnings', str(source)],
                            cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert 'no-unused-vars' in result.stdout + result.stderr
