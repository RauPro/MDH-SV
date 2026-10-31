"""Contract consumers must not need collection, provider or repository adapters."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def isolated(code):
    return subprocess.run([sys.executable, '-c', code], cwd=ROOT, check=True,
                          env={**os.environ, 'PYTHONPATH': os.pathsep.join([
                              str(ROOT / 'apps/pipeline/src'), str(ROOT / 'packages/schema/src'),
                              str(ROOT / 'tests')])}, capture_output=True, text=True)


def test_source_and_file_utilities_do_not_import_adapters():
    isolated('''
import importlib.abc, sys
class NoAdapters(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {'requests', 'feedparser', 'mdh_pipeline.review_queue', 'mdh_pipeline.review_import'}:
            raise AssertionError('Unexpected adapter: ' + fullname)
sys.meta_path.insert(0, NoAdapters())
from mdh_pipeline.source_urls import canonical_url, normalize
from mdh_pipeline.file_outputs import write_json_outputs
assert canonical_url('https://example.org/story?utm_source=test', {'example.org'}) == 'https://example.org/story'
assert normalize('El SALVADÓR') == 'el salvador'
''')
