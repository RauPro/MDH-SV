"""
Módulo de scrapers para medios y fuentes de datos de El Salvador.
"""

from .base import BaseNewsScraper, RawNewsArticle
from .feed_scrapers import SalvadoranMediaRSSScraper, get_all_scrapers
from .sitemap_scraper import SalvadoranMediaSitemapScraper, get_default_sitemap_scrapers

__all__ = [
    "BaseNewsScraper",
    "RawNewsArticle",
    "SalvadoranMediaRSSScraper",
    "get_all_scrapers",
    "SalvadoranMediaSitemapScraper",
    "get_default_sitemap_scrapers",
]
