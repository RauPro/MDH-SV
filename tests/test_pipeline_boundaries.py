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


def test_all_contracts_validate_shared_corpus_without_loading_adapters_or_network():
    isolated('''
import importlib.abc, sys, socket
from pathlib import Path
adapters = {'review_queue', 'review_import', 'publication', 'news_dossier', 'history',
            'news_selection', 'news_processing', 'news_publication', 'automatic_locations',
            'automatic_news', 'gemini_review', 'gemini_draft', 'article_source', 'news_scope',
            'scope_review_queue', 'network_safety', 'file_outputs'}
class NoAdapters(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'requests', 'feedparser', 'bs4', 'google', 'pyarrow', 'dotenv'}:
            raise AssertionError('Unexpected transport/dependency: ' + fullname)
        if fullname.startswith('mdh_pipeline.') and fullname.split('.')[1] in adapters:
            raise AssertionError('Unexpected adapter: ' + fullname)
sys.meta_path.insert(0, NoAdapters())
def no_network(*args, **kwargs): raise AssertionError('Contracts must not resolve or connect')
socket.getaddrinfo = socket.create_connection = no_network
from mdh_pipeline.contracts import catalog, dossier, drafts, locations, news_collection, processing, reference, selection
from mdh_pipeline.publishers import SOURCES
from contract_fixtures import load_cases
validators = {'catalogue': catalog.Catalog.model_validate,
              'news-reference': lambda d: reference.validate_reference(d, next((s for s in SOURCES if s['id'] == d.get('source_id')), SOURCES[0])),
              'news-dossier': dossier.validate_published_news_dossier,
              'news-selection': selection.validate_selection,
              'news-processing': processing.validate_collection,
              'news-location': locations.validate_collection}
count = 0
for filename, validator in validators.items():
    for name, valid, document in load_cases(Path('tests/fixtures/contracts') / (filename + '.json')):
        try: validator(document)
        except ValueError:
            assert not valid, (filename, name)
        else: assert valid, (filename, name)
        count += 1
assert count >= 290
assert news_collection.validate_collection({'schema_version': 1, 'dossiers': []}) == {'schema_version': 1, 'dossiers': []}
try: drafts.validate_envelope({}, {'id': 'a'*64, 'source_url': 'https://example.org/article'})
except ValueError as error: assert str(error) == 'invalid_draft_envelope'
else: raise AssertionError('Invalid private envelope accepted')
assert not any('mdh_pipeline.' + name in sys.modules for name in adapters)
''')


def test_legacy_contract_exports_are_the_canonical_objects():
    from mdh_pipeline import (automatic_locations, gemini_review, history, news_dossier,
                              news_processing, news_publication, news_selection, publication)
    from mdh_pipeline.contracts import (catalog, dossier, drafts, locations,
                                        news_collection, processing, reference, selection)
    for old, new in [(publication.Catalog, catalog.Catalog),
                     (publication.Publication, catalog.Publication),
                     (news_dossier.NewsDossier, dossier.NewsDossier),
                     (news_dossier.NewsDossierError, dossier.NewsDossierError),
                     (history.validate_reference, reference.validate_reference),
                     (news_selection.validate_selection, selection.validate_selection),
                     (news_processing.validate_collection, processing.validate_collection),
                     (news_processing.NewsProcessingError, processing.NewsProcessingError),
                     (automatic_locations.validate_collection, locations.validate_collection),
                     (automatic_locations.AutomaticLocationError, locations.AutomaticLocationError),
                     (gemini_review.validate_envelope, drafts.validate_envelope),
                     (news_publication.validate_collection, news_collection.validate_collection)]:
        assert old is new


def test_contract_import_direction_including_lazy_branches():
    import ast
    from importlib.util import resolve_name
    support = {'mdh_pipeline.publishers', 'mdh_pipeline.source_urls',
               'mdh_pipeline.municipalities', 'mdh_pipeline.news_scope_policy'}
    for path in (ROOT / 'apps/pipeline/src/mdh_pipeline/contracts').glob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                module = resolve_name('.' * node.level + (node.module or ''),
                                      'mdh_pipeline.contracts') if node.level else node.module
                modules = [module]
            elif isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            else:
                continue
            for module in modules:
                if module.startswith('mdh_pipeline.'):
                    assert module.startswith('mdh_pipeline.contracts.') or module in support, (path, module)
