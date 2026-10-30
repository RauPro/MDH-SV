"""
Scraper de Google News RSS para Medios Salvadoreños (2024 - Presente).
Permite buscar por palabras clave forenses (homicidios, régimen de excepción, muertes en penales, prensa, capturas)
en La Prensa Gráfica, El Diario de Hoy, El Faro, Revista Factum, Diario Co Latino, APES, etc.
Utiliza googlenewsdecoder para obtener la URL original y curl_cffi para evadir protecciones anti-bot (Cloudflare/Akamai).
"""

import logging
import re
import urllib.parse
from datetime import date
from typing import List, Optional, Set
import feedparser
import requests
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests
from googlenewsdecoder import gnewsdecoder

from .base import BaseNewsScraper, RawNewsArticle

logger = logging.getLogger("google_news_scraper")

SALVADORAN_DDHH_QUERIES = [
    # Régimen de excepción y capturas
    'El Salvador "régimen de excepción" captura',
    'El Salvador "detención arbitraria"',
    'El Salvador captura inocente OR sindicalista',
    # Centros penales y muertes bajo custodia
    'El Salvador "muerte" "penal" OR "cárcel"',
    'El Salvador Mariona OR Izalco OR CECOT fallecido OR muerto',
    # Homicidios y feminicidios
    'El Salvador homicidio OR asesinato OR feminicidio',
    # Libertad de prensa y defensores
    'El Salvador periodista agresión OR amenaza APES',
    'El Salvador ambientalistas Santa Marta captura OR juicio',
    # Medios específicos
    'site:laprensagrafica.com "régimen de excepción" OR homicidio OR penal',
    'site:elsalvador.com "régimen de excepción" OR captura OR penal',
    'site:revistafactum.com penal OR régimen OR policía',
    'site:elfaro.net penal OR régimen OR captura',
    'site:diariocolatino.com "derechos humanos" OR captura OR penal',
    # Cobertura territorial departamental y garantías
    'El Salvador homicidio San Salvador OR Santa Ana OR San Miguel',
    'El Salvador captura Sonsonate OR Usulután OR La Libertad',
    'El Salvador operativo La Paz OR Ahuachapán OR Cuscatlán',
    'El Salvador captura Morazán OR Chalatenango OR Cabañas OR La Unión',
    'El Salvador "carta de libertad" OR "hábeas corpus" penal',
    'El Salvador "fosa clandestina" OR "osamenta"',
]


class GoogleNewsSalvadorScraper(BaseNewsScraper):
    """Scraper multicanal basado en Google News RSS especializado en El Salvador."""

    def __init__(self, queries: Optional[List[str]] = None):
        super().__init__(outlet_name="Prensa Salvadoreña (Google News)", base_url="https://news.google.com")
        self.queries = queries or SALVADORAN_DDHH_QUERIES
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        }

    def fetch_recent_articles(self, limit: int = 15) -> List[RawNewsArticle]:
        return self.fetch_historical_articles(limit_per_query=max(2, limit // len(self.queries)))

    def fetch_historical_articles(self, limit_per_query: int = 10, target_years: Optional[List[str]] = None) -> List[RawNewsArticle]:
        """
        Rastrea noticias históricas desde 2024 hasta la fecha a través de las consultas especializadas.
        """
        if target_years is None:
            target_years = ["2024", "2025", "2026"]

        articles: List[RawNewsArticle] = []
        seen_urls: Set[str] = set()
        seen_titles: Set[str] = set()

        for q in self.queries:
            logger.info(f"Consultando Google News RSS: {q}")
            rss_url = f"https://news.google.com/rss/search?q={urllib.parse.quote(q)}&hl=es-419&gl=US&ceid=US:es-419"
            try:
                resp = requests.get(rss_url, headers=self.headers, timeout=12)
                feed = feedparser.parse(resp.content)
            except Exception as e:
                logger.warning(f"Error consultando RSS para '{q}': {e}")
                continue

            query_count = 0
            for entry in feed.entries:
                if query_count >= limit_per_query:
                    break

                raw_title = getattr(entry, "title", "")
                norm_title = re.sub(r"\s*-\s*[^-]+$", "", raw_title).strip()
                if not norm_title or norm_title.lower() in seen_titles:
                    continue

                # Extraer medio del título o del source
                outlet_name = "Prensa Independiente"
                if hasattr(entry, "source") and isinstance(entry.source, dict) and entry.source.get("title"):
                    outlet_name = entry.source["title"]
                elif " - " in raw_title:
                    outlet_name = raw_title.split(" - ")[-1].strip()

                # Parsear fecha
                pub_date = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    yr = str(entry.published_parsed.tm_year)
                    if yr not in target_years:
                        continue
                    pub_date = date(entry.published_parsed.tm_year, entry.published_parsed.tm_mon, entry.published_parsed.tm_mday)

                # Decodificar link de Google News
                google_link = getattr(entry, "link", None)
                if not google_link:
                    continue

                real_url = google_link
                try:
                    res = gnewsdecoder(google_link)
                    if res and res.get("status") and res.get("decoded_url"):
                        real_url = res["decoded_url"]
                except Exception:
                    pass

                if real_url in seen_urls:
                    continue

                seen_urls.add(real_url)
                seen_titles.add(norm_title.lower())

                # Extraer contenido del artículo
                full_text = self._extract_article_content(real_url, outlet_name)
                if not full_text or len(full_text) < 120:
                    # Usar el summary o el título si no se pudo descargar el HTML completo
                    full_text = getattr(entry, "summary", "") or norm_title

                art = RawNewsArticle.from_raw(
                    outlet=outlet_name,
                    url=real_url,
                    title=norm_title,
                    full_text=full_text,
                    summary=full_text[:300],
                    published_date=pub_date or date.today()
                )
                articles.append(art)
                query_count += 1
                logger.info(f"[{outlet_name}] Obtenido: {norm_title[:55]}...")

        logger.info(f"Total de artículos periodísticos recopilados: {len(articles)}")
        return articles

    def _extract_article_content(self, url: str, outlet_name: str) -> Optional[str]:
        """Descarga el contenido del artículo, usando curl_cffi para medios con Cloudflare."""
        try:
            if "laprensagrafica.com" in url:
                resp = cffi_requests.get(url, impersonate="chrome124", timeout=12)
            else:
                resp = requests.get(url, headers=self.headers, timeout=10)

            if resp.status_code != 200:
                return None

            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
                tag.decompose()

            # Buscar párrafos de contenido
            paragraphs = [p.get_text(strip=True) for p in soup.find_all("p")]
            clean_paragraphs = [p for p in paragraphs if len(p) > 35]

            if clean_paragraphs:
                return "\n\n".join(clean_paragraphs)
        except Exception as e:
            logger.debug(f"Error extrayendo texto de {url}: {e}")

        return None
