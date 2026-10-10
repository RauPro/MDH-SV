"""
Clase base y modelos para los colectores OSINT de medios de comunicación salvadoreños.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import List, Optional
import hashlib
from bs4 import BeautifulSoup


@dataclass
class RawNewsArticle:
    """Representa una noticia extraída antes del análisis y estructuración con IA."""
    outlet: str
    url: str
    title: str
    summary: str
    full_text: str
    published_date: Optional[date]
    content_sha256: str
    archive_url: Optional[str] = None

    @classmethod
    def from_raw(
        cls,
        outlet: str,
        url: str,
        title: str,
        full_text: str,
        summary: str = "",
        published_date: Optional[date] = None,
        archive_url: Optional[str] = None
    ) -> "RawNewsArticle":
        content_hash = hashlib.sha256(full_text.encode("utf-8")).hexdigest()
        return cls(
            outlet=outlet,
            url=url,
            title=title.strip(),
            summary=summary.strip(),
            full_text=full_text.strip(),
            published_date=published_date,
            content_sha256=content_hash,
            archive_url=archive_url
        )


class BaseNewsScraper(ABC):
    """Interfaz abstracta para scrapers de medios salvadoreños."""

    def __init__(self, outlet_name: str, base_url: str):
        self.outlet_name = outlet_name
        self.base_url = base_url

    @abstractmethod
    def fetch_recent_articles(self, limit: int = 10) -> List[RawNewsArticle]:
        """Extrae los artículos recientes del medio."""
        pass

    @staticmethod
    def clean_html(html_text: str) -> str:
        """Limpia etiquetas de script, estilo y anuncios del HTML."""
        soup = BeautifulSoup(html_text, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
            tag.decompose()
        return soup.get_text(separator=" ", strip=True)
