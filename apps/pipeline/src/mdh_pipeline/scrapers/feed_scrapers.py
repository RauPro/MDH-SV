"""
Scrapers basados en RSS y Sitemaps para medios de comunicación salvadoreños ($0 de costo en APIs).
"""

import logging
from datetime import date, datetime
from typing import List, Optional
from urllib.parse import urlsplit

import feedparser
import requests
from bs4 import BeautifulSoup

from ..publishers import registered_feeds
from .base import BaseNewsScraper, RawNewsArticle

logger = logging.getLogger(__name__)


class SalvadoranMediaRSSScraper(BaseNewsScraper):
    """
    Scraper automatizado para fuentes RSS públicas de medios salvadoreños.
    No requiere APIs de pago como Google CSE ni Twitter API.
    """

    def __init__(self, outlet_name: str, feed_url: str, base_url: str):
        super().__init__(outlet_name, base_url)
        self.feed_url = feed_url
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        }

    def fetch_recent_articles(self, limit: int = 10) -> List[RawNewsArticle]:
        """Obtiene las últimas noticias desde el canal RSS y parsea el cuerpo de cada nota."""
        articles: List[RawNewsArticle] = []
        try:
            try:
                resp = requests.get(self.feed_url, headers=self.headers, timeout=10)
                feed = feedparser.parse(resp.content) if resp.status_code == 200 else feedparser.parse(self.feed_url)
            except Exception:
                feed = feedparser.parse(self.feed_url)
            entries = feed.entries[:limit]
            logger.info(f"[{self.outlet_name}] {len(entries)} entradas encontradas en feed RSS")

            for entry in entries:
                url = getattr(entry, "link", None)
                if not url:
                    continue

                title = getattr(entry, "title", "Sin título")
                summary = getattr(entry, "summary", "")

                # Extraer fecha
                pub_date = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    pub_date = date(
                        entry.published_parsed.tm_year,
                        entry.published_parsed.tm_mon,
                        entry.published_parsed.tm_mday
                    )

                # Intentar descargar el cuerpo completo de la nota
                full_text = self._extract_article_body(url) or summary or title

                articles.append(
                    RawNewsArticle.from_raw(
                        outlet=self.outlet_name,
                        url=url,
                        title=title,
                        summary=summary,
                        full_text=full_text,
                        published_date=pub_date
                    )
                )
        except Exception as e:
            logger.error(f"Error parseando feed RSS de {self.outlet_name}: {e}")

        return articles

    def _extract_article_body(self, url: str) -> Optional[str]:
        """Descarga la página y extrae el texto del artículo usando selectores comunes."""
        try:
            resp = requests.get(url, headers=self.headers, timeout=10)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "lxml")
                # Eliminar ruido
                for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                    tag.decompose()

                # Buscar contenedores habituales de artículos periodísticos
                article_tag = soup.find("article") or soup.find("div", class_=["article-body", "story-body", "post-content", "entry-content"])
                if article_tag:
                    paragraphs = article_tag.find_all("p")
                    return "\n\n".join(p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 25)

                paragraphs = soup.find_all("p")
                return "\n\n".join(p.get_text(strip=True) for p in paragraphs[:15] if len(p.get_text(strip=True)) > 25)
        except Exception as e:
            logger.debug(f"No se pudo extraer cuerpo completo de {url}: {e}")
        return None


# Directorio de fuentes preconfiguradas de El Salvador (Sin costo)
SALVADORAN_FEEDS = [
    {"outlet": feed["name"], "feed": feed["url"],
     "base": "https://" + urlsplit(feed["url"]).hostname}
    for feed in registered_feeds("legacy_scraper")
]


def get_all_scrapers() -> List[SalvadoranMediaRSSScraper]:
    """Instancia la lista de scrapers configurados para monitoreo continuo."""
    return [
        SalvadoranMediaRSSScraper(
            outlet_name=item["outlet"],
            feed_url=item["feed"],
            base_url=item["base"]
        )
        for item in SALVADORAN_FEEDS
    ]
