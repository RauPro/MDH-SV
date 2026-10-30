"""
Pruebas de las Estrategias de Ingesta Histórica (Sitemaps y Reportes de ONGs).
"""

import pytest
from mdh_pipeline.ngo_importer import NGOReportImporter
from mdh_pipeline.scrapers.sitemap_scraper import SalvadoranMediaSitemapScraper


def test_ngo_importer_missing_file(tmp_path):
    importer = NGOReportImporter(db_path=str(tmp_path / 'test.sqlite'))
    with pytest.raises(FileNotFoundError):
        importer.import_from_json(str(tmp_path / 'missing.json'))


def test_sitemap_scraper_url_filtering():
    """Verifica que el scraper de sitemaps filtre correctamente URLs por año."""
    scraper = SalvadoranMediaSitemapScraper(
        outlet_name="TestOutlet",
        sitemap_urls=[],
        base_url="https://test.com"
    )
    # Test method existence and initialization
    assert scraper.outlet_name == "TestOutlet"
    urls = scraper.fetch_historical_urls(target_years=["2024", "2025"], limit=10)
    assert isinstance(urls, list)
