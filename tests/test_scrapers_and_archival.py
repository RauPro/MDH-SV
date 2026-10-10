"""
Pruebas automatizadas para los scrapers OSINT y módulo de preservación inmutable (Fase 2).
"""

from datetime import date
from unittest.mock import MagicMock, patch
import pytest

from mdh_pipeline.archiver import EvidenceArchiver
from mdh_pipeline.ingest import IngestionPipeline
from mdh_pipeline.scrapers.base import BaseNewsScraper, RawNewsArticle
from mdh_pipeline.scrapers.feed_scrapers import SalvadoranMediaRSSScraper


def test_raw_article_hashing():
    """Verifica que el artículo calcule su hash SHA-256 automáticamente al instanciarse."""
    text = "Reporte sobre condiciones en el penal de Izalco durante el régimen de excepción."
    article = RawNewsArticle.from_raw(
        outlet="GatoEncerrado",
        url="https://gatoencerrado.news/2024/02/izalco-reporte",
        title="Condiciones carcelarias bajo escrutinio",
        full_text=text,
        summary="Resumen de la situación en Izalco",
        published_date=date(2024, 2, 1)
    )

    assert article.content_sha256 == EvidenceArchiver.compute_sha256(text)
    assert len(article.content_sha256) == 64
    assert article.outlet == "GatoEncerrado"


def test_html_cleaning():
    """Verifica la eliminación de scripts, estilos y menús en el HTML."""
    dirty_html = """
    <html>
        <head><script>alert('malware');</script><style>.ad{display:block}</style></head>
        <body>
            <nav><a href="/">Inicio</a></nav>
            <article>
                <h1>Muertes bajo custodia</h1>
                <p>Este es el párrafo principal de la noticia periodística.</p>
            </article>
            <footer>Derechos Reservados 2024</footer>
        </body>
    </html>
    """
    clean_text = BaseNewsScraper.clean_html(dirty_html)
    assert "alert" not in clean_text
    assert ".ad" not in clean_text
    assert "Inicio" not in clean_text
    assert "Muertes bajo custodia" in clean_text
    assert "Este es el párrafo principal de la noticia periodística." in clean_text


@patch("requests.Session.get")
def test_wayback_existing_snapshot(mock_get):
    """Simula la respuesta de la API de Wayback Machine cuando ya existe un snapshot."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "archived_snapshots": {
            "closest": {
                "available": True,
                "url": "https://web.archive.org/web/20240101120000/https://ejemplo.com/nota"
            }
        }
    }
    mock_get.return_value = mock_response

    archiver = EvidenceArchiver()
    snapshot_url = archiver.check_existing_snapshot("https://ejemplo.com/nota")

    assert snapshot_url == "https://web.archive.org/web/20240101120000/https://ejemplo.com/nota"


def test_ingestion_pipeline_article_processing():
    """Verifica que el pipeline convierta un RawNewsArticle en SourceEvidence con hash."""
    article = RawNewsArticle.from_raw(
        outlet="El Faro",
        url="https://elfaro.net/es/noticia_test",
        title="Investigación especial",
        full_text="Contenido fáctico analizado por periodistas independientes.",
        summary="Resumen de la investigación",
        published_date=date(2024, 3, 10)
    )

    pipeline = IngestionPipeline(enable_wayback=False)
    evidence = pipeline.process_article_to_evidence(article)

    assert evidence.outlet_name == "El Faro"
    assert evidence.original_url == "https://elfaro.net/es/noticia_test"
    assert evidence.source_sha256 == article.content_sha256
    assert len(evidence.source_sha256) == 64
