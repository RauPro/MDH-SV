"""
Orquestador de Ingesta OSINT para el Monitor de Derechos Humanos de El Salvador.
Coordina la extracción de medios, cómputo criptográfico y preservación inmutable en Wayback Machine.
"""

import logging
from typing import List, Optional
from mdh_schema import SourceEvidence
from .archiver import EvidenceArchiver
from .scrapers.base import RawNewsArticle
from .scrapers.feed_scrapers import get_all_scrapers

logger = logging.getLogger(__name__)


class IngestionPipeline:
    """Orquestador de ingesta continua de fuentes noticiosas."""

    def __init__(self, enable_wayback: bool = False):
        self.archiver = EvidenceArchiver()
        self.enable_wayback = enable_wayback

    def run_ingestion(self, limit_per_source: int = 5) -> List[SourceEvidence]:
        """
        Ejecuta la recolección de noticias en todas las fuentes configuradas,
        calcula hashes SHA-256 y archiva opcionalmente en Internet Archive.
        """
        all_evidences: List[SourceEvidence] = []
        scrapers = get_all_scrapers()

        for scraper in scrapers:
            try:
                articles = scraper.fetch_recent_articles(limit=limit_per_source)
                for art in articles:
                    evidence = self.process_article_to_evidence(art)
                    if evidence:
                        all_evidences.append(evidence)
            except Exception as e:
                logger.error(f"Error procesando fuente {scraper.outlet_name}: {e}")

        return all_evidences

    def process_article_to_evidence(self, article: RawNewsArticle) -> SourceEvidence:
        """Convierte un RawNewsArticle en SourceEvidence con hash y archivo verificado."""
        archive_url = None
        if self.enable_wayback:
            archive_url, _ = self.archiver.request_wayback_preservation(article.url)

        return SourceEvidence(
            outlet_name=article.outlet,
            original_url=article.url,
            title=article.title,
            published_date=article.published_date,
            archive_url=archive_url,
            source_sha256=article.content_sha256,
            extracted_quote=article.summary or article.full_text[:300]
        )
