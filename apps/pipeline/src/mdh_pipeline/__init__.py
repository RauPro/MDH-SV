"""Pipeline package. Optional ingestion dependencies load only when requested."""
from importlib import import_module

_EXPORTS = {
    'EvidenceArchiver': '.archiver',
    'IngestionPipeline': '.ingest',
    'BaseNewsScraper': '.scrapers.base',
    'RawNewsArticle': '.scrapers.base',
}
__all__ = list(_EXPORTS)

def __getattr__(name):
    if name not in _EXPORTS: raise AttributeError(name)
    return getattr(import_module(_EXPORTS[name], __name__), name)
