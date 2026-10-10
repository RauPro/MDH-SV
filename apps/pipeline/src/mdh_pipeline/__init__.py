"""
Módulo de pipeline, ingesta y preservación de evidencias del MDH-SV.
"""

from .archiver import EvidenceArchiver
from .ingest import IngestionPipeline
from .scrapers.base import BaseNewsScraper, RawNewsArticle

__all__ = [
    "EvidenceArchiver",
    "IngestionPipeline",
    "BaseNewsScraper",
    "RawNewsArticle",
]
