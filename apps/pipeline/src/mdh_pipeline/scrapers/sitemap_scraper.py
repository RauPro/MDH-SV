"""
Scraper de Sitemaps XML para Medios Salvadoreños (2024 - Presente).
Permite recuperar artículos de investigación histórica que ya no aparecen en los feeds RSS.
"""

import logging
import re
from datetime import date
from typing import List, Set
import requests
from bs4 import BeautifulSoup

from .base import BaseNewsScraper, RawNewsArticle

logger = logging.getLogger("sitemap_scraper")


class SalvadoranMediaSitemapScraper(BaseNewsScraper):
    """
    Scraper para rastrear sitemaps XML de medios salvadoreños independientes.
    Filtra automáticamente por el rango de fechas 2024 a la fecha actual.
    """

    def __init__(self, outlet_name: str, sitemap_urls: List[str], base_url: str):
        super().__init__(outlet_name, base_url)
        self.sitemap_urls = sitemap_urls
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        }

    def fetch_recent_articles(self, limit: int = 10) -> List[RawNewsArticle]:
        """Implementación del método abstracto de BaseNewsScraper."""
        urls = self.fetch_historical_urls(limit=limit)
        return self.fetch_articles_from_urls(urls)

    def fetch_historical_urls(self, target_years: List[str] = None, limit: int = 50) -> List[str]:
        """Extrae URLs de artículos que correspondan a los años objetivo."""
        if target_years is None:
            target_years = ["2024", "2025", "2026"]

        collected_urls: List[str] = []
        seen: Set[str] = set()

        # Palabras clave para priorizar temas de DDHH y seguridad judicial
        ddhh_keywords = [
            "captur", "deten", "regimen", "excepcion", "penal", "mariona", "izalco",
            "cecot", "apanteos", "carcel", "prision", "bartolina", "homicidio",
            "asesin", "muerte", "fallec", "polici", "pnc", "faes", "tribunal",
            "juez", "fiscal", "inocente", "prensa", "periodista", "sindica",
            "ambiental", "abuso", "tortur", "osamenta", "fosa", "habeas"
        ]

        # Secciones irrelevantes a ignorar
        ignore_sections = [
            "/deportes/", "/entretenimiento/", "/vida/", "/turismo/",
            "/farandula/", "/famosos/", "/recetas/", "/viral/", "/tendencias/"
        ]

        for sitemap_url in self.sitemap_urls:
            logger.info(f"Consultando sitemap: {sitemap_url}")
            try:
                resp = requests.get(sitemap_url, headers=self.headers, timeout=12)
                if resp.status_code != 200:
                    logger.warning(f"Error {resp.status_code} al consultar {sitemap_url}")
                    continue

                urls = re.findall(r"<loc>(?:<!\[CDATA\[)?(https?://[^\s\]>]+)", resp.text)
                for u in urls:
                    u = u.strip().split("]]>")[0].rstrip("/")
                    if u in seen:
                        continue

                    # Ignorar secciones no judiciales
                    if any(sec in u.lower() for sec in ignore_sections):
                        continue

                    # Verificar año si está en la URL
                    has_target_year = any(y in u for y in target_years)
                    has_ddhh_keyword = any(kw in u.lower() for kw in ddhh_keywords)

                    # Si el medio no incluye año en la URL (ej. Factum/GatoEncerrado),
                    # aceptamos si contiene palabra clave de DDHH
                    if has_ddhh_keyword or has_target_year:
                        seen.add(u)
                        collected_urls.append(u)
                        if len(collected_urls) >= limit:
                            break
            except Exception as e:
                logger.error(f"Fallo al leer sitemap {sitemap_url}: {e}")

            if len(collected_urls) >= limit:
                break

        logger.info(f"[{self.outlet_name}] {len(collected_urls)} URLs históricas identificadas.")
        return collected_urls

    def fetch_articles_from_urls(self, urls: List[str]) -> List[RawNewsArticle]:
        """Descarga y extrae el texto limpio de cada URL identificada."""
        articles: List[RawNewsArticle] = []

        for u in urls:
            try:
                resp = requests.get(u, headers=self.headers, timeout=12)
                if resp.status_code != 200:
                    continue

                soup = BeautifulSoup(resp.text, "html.parser")

                # Extraer título real del artículo
                og_title = soup.find("meta", property="og:title") or soup.find("meta", attrs={"name": "twitter:title"})
                if og_title and og_title.get("content"):
                    title = og_title["content"].strip()
                else:
                    title_tag = soup.find("h1") or soup.find("title")
                    title = title_tag.get_text(strip=True) if title_tag else "Sin título"

                # Intentar parsear fecha desde la URL (ej. /2024/03/15/ o /2024/)
                pub_date = None
                date_match = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", u)
                if date_match:
                    pub_date = date(int(date_match.group(1)), int(date_match.group(2)), int(date_match.group(3)))
                else:
                    year_match = re.search(r"/(\d{4})/?$", u)
                    if year_match:
                        pub_date = date(int(year_match.group(1)), 1, 1)

                # Extracción limpia del cuerpo del HTML
                for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
                    tag.decompose()
                paragraphs = [p.get_text(strip=True) for p in soup.find_all("p")]
                body_text = " ".join(p for p in paragraphs if len(p) > 30)

                if len(body_text) < 100:
                    continue

                art = RawNewsArticle.from_raw(
                    outlet=self.outlet_name,
                    url=u,
                    title=title,
                    full_text=body_text,
                    summary=body_text[:300],
                    published_date=pub_date or date.today()
                )
                articles.append(art)
                logger.info(f"[{self.outlet_name}] Descargado: {title[:50]}...")
            except Exception as e:
                logger.warning(f"Error procesando URL {u}: {e}")

        return articles


def get_default_sitemap_scrapers() -> List[SalvadoranMediaSitemapScraper]:
    """Configuración de sitemaps de medios salvadoreños con archivo histórico (2024 - 2026)."""
    return [
        # El Diario de Hoy (elsalvador.com) - Sitemaps recientes cubriendo 2024 a 2026
        SalvadoranMediaSitemapScraper(
            outlet_name="El Diario de Hoy",
            sitemap_urls=[
                f"https://www.elsalvador.com/post-sitemap{i if i > 1 else ''}.xml"
                for i in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 15, 20, 25, 30, 35, 40]
            ],
            base_url="https://www.elsalvador.com"
        ),
        # GatoEncerrado - Investigaciones independientes
        SalvadoranMediaSitemapScraper(
            outlet_name="GatoEncerrado",
            sitemap_urls=[
                "https://gatoencerrado.news/post-sitemap.xml",
                "https://gatoencerrado.news/post-sitemap3.xml"
            ],
            base_url="https://gatoencerrado.news"
        ),
        # Revista Factum - Investigaciones sobre penales y DDHH
        SalvadoranMediaSitemapScraper(
            outlet_name="Revista Factum",
            sitemap_urls=[
                "https://www.revistafactum.com/post-sitemap.xml",
                "https://www.revistafactum.com/post-sitemap4.xml"
            ],
            base_url="https://www.revistafactum.com"
        ),
        # Voz Pública - Reportajes comunitarios
        SalvadoranMediaSitemapScraper(
            outlet_name="Voz Pública",
            sitemap_urls=[
                "https://vozpublica.net/wp-sitemap-posts-post-1.xml"
            ],
            base_url="https://vozpublica.net"
        )
    ]
