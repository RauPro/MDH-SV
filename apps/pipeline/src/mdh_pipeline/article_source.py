"""Fetch public article evidence in memory for a private, reviewable draft.

No article body is written to disk or GitHub. This module neither calls an AI
provider nor publishes records. Paragraph identifiers refer to the normalized
text returned here; a hash and explicit truncation flag describe that exact input.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from urllib.parse import unquote, urlencode, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup, Comment, Declaration, Doctype, ProcessingInstruction

from .publishers import SOURCES
from .source_urls import canonical_url, normalize
from .network_safety import require_public_dns

MAX_BYTES = 1_000_000
MAX_TEXT_CHARS = 24_000
MAX_PARAGRAPHS = 160
MAX_PARAGRAPH_CHARS = 4_000
MIN_TEXT_CHARS = 200
USER_AGENT = "MDH-SV/2.3 source-review (+https://github.com/RauPro/MDH-SV)"
ARTICLE_SELECTORS = (
    "[itemprop='articleBody']",
    # Older Elementor single-post templates isolate the article in this widget;
    # require its editorial parent so theme header/footer widgets cannot match.
    "[data-elementor-type='single-post'] .elementor-widget-theme-post-content[data-widget_type='theme-post-content.default']",
    # Elementor renders the editorial body without <article>. Header/footer
    # templates also use wp-post, so requiring post-type=post is essential.
    "[data-elementor-type='wp-post'][data-elementor-post-type='post']",
    ".entry-content", ".post-content", ".article-content", ".td-post-content", "article",
)
# These source-specific containers were inspected on public article pages. Do not
# fall back to unrelated <article> recommendation cards if a known layout changes.
PUBLISHER_ARTICLE_SELECTORS = {
    "el-faro": (".article #main-text",),
    "el-mundo": ("article .article-body",),
    "el-diario-de-hoy": ("article.detail .entry-content",),
}
UNWANTED = "script,style,nav,header,footer,aside,form,button,iframe,noscript,template,svg,figure,[hidden],[aria-hidden='true'],.advertisement,.advertising,.ads,.ad-container,.sharedaddy,.share-buttons,.social-share,.related-posts,.related-articles,.comments,#comments,.newsletter,.author-box,.post-meta,.entry-meta,.elementor-widget-posts,.elementor-widget-loop-grid,.elementor-widget-author-box,.elementor-widget-share-buttons,.elementor-widget-post-info,.elementor-widget-post-comments,.elementor-widget-nav-menu,[data-elementor-post-type='elementor-hf']"
PAYWALL_SELECTORS = "[class*='paywall'],[id*='paywall'],[class*='subscribe-wall'],[class*='subscription-wall'],[class*='piano-paywall']"
BLOCKED_TEXT = ("verify you are human", "verifica que eres humano", "verifique que es humano", "checking your browser", "enable javascript and cookies to continue", "suscribete para seguir leyendo", "suscribete para continuar leyendo", "subscribe to continue reading", "inicia sesion para continuar leyendo", "this content is password protected", "este contenido esta protegido por contrasena")


class SourceFetchError(ValueError):
    """A stable, non-sensitive error code suitable for private workflow reporting."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _clean(value):
    return " ".join(str(value or "").replace("\x00", "").split())


def _date(value):
    """Publisher dates are never replaced with a current date."""
    if not isinstance(value, str):
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value).isoformat()
        # Legacy EDH article metadata uses e.g. "2013-03-13 6:03:00".
        # Pad only the unambiguous single-digit hour of an otherwise ISO-shaped
        # timestamp; datetime still validates the actual date and all clock parts.
        value = re.sub(
            r"^(\d{4}-\d{2}-\d{2}[T ])(\d)(:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})?)$",
            r"\g<1>0\2\3", value)
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def _identity(url, source):
    """Only a source's www alias and trailing slash are interchangeable."""
    try:
        checked = canonical_url(url, set(source["hosts"]))
    except (ValueError, TypeError) as error:
        raise SourceFetchError("invalid_source_url") from error
    parts = urlsplit(checked)
    return (parts.hostname.removeprefix("www."), parts.path.rstrip("/"))


def _fetch(url, source, session, *, accept):
    """One request plus at most three same-publisher redirects; bounded decoded bytes."""
    for _ in range(4):
        parts = urlsplit(url)
        try:
            valid = (parts.scheme == "https" and parts.hostname in source["hosts"]
                     and not parts.username and not parts.password and not parts.port)
        except ValueError as error:
            raise SourceFetchError("invalid_source_url") from error
        if not valid:
            raise SourceFetchError("redirect_outside_source")
        try:
            require_public_dns(parts.hostname)
        except (ValueError, OSError) as error:
            raise SourceFetchError("unsafe_source_dns") from error
        try:
            with session.get(url, timeout=(8, 25), stream=True, allow_redirects=False,
                    headers={"User-Agent": USER_AGENT, "Accept": accept,
                             "Authorization": None, "Cookie": None}) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    if not response.headers.get("Location"):
                        raise SourceFetchError("invalid_redirect")
                    url = urljoin(url, response.headers["Location"])
                    continue
                if response.status_code in (401, 402, 403, 429, 503):
                    raise SourceFetchError("source_access_restricted")
                if response.status_code >= 400:
                    raise SourceFetchError("source_not_found" if response.status_code == 404 else "source_http_error")
                if response.status_code != 200:
                    raise SourceFetchError("source_http_error")
                try:
                    if int(response.headers.get("Content-Length", "0")) > MAX_BYTES:
                        raise SourceFetchError("source_too_large")
                except ValueError as error:
                    if isinstance(error, SourceFetchError):
                        raise
                    raise SourceFetchError("invalid_content_length") from error
                raw = bytearray()
                for chunk in response.iter_content(65536):
                    raw.extend(chunk)
                    if len(raw) > MAX_BYTES:
                        raise SourceFetchError("source_too_large")
                return bytes(raw), response.headers.get("Content-Type", ""), url
        except requests.RequestException as error:
            raise SourceFetchError("source_network_error") from error
    raise SourceFetchError("too_many_redirects")


def _blocked(soup, *, page=False):
    if soup.select_one(PAYWALL_SELECTORS):
        raise SourceFetchError("source_access_restricted")
    # Only access-control widgets or a challenge page count; an article may discuss captchas.
    if soup.select_one("form[action*='captcha'],#challenge-form,.cf-challenge,#cf-challenge-running"):
        raise SourceFetchError("source_access_restricted")
    relevant = soup.get_text(" ", strip=True) if not page else " ".join(
        node.get_text(" ", strip=True) for node in soup.select("title,h1,main,article")
    )
    if any(term in normalize(relevant) for term in BLOCKED_TEXT):
        raise SourceFetchError("source_access_restricted")


def _paragraphs(fragment):
    soup = BeautifulSoup(fragment, "html.parser")
    _blocked(soup)
    for node in soup.select(UNWANTED):
        node.decompose()
    paragraphs = []
    blocks = ("p", "h2", "h3", "h4", "li", "blockquote")
    def read_block(node):
        parts = []
        for child in node.children:
            if isinstance(child, (Comment, Declaration, Doctype, ProcessingInstruction)):
                continue
            if isinstance(child, str):
                parts.append(child)
            elif child.name in blocks or child.find(blocks):
                text = _clean(" ".join(parts))
                if text:
                    paragraphs.append(text)
                parts = []
                read_block(child)
            else:
                parts.append(child.get_text(" ", strip=True))
        text = _clean(" ".join(parts))
        if text:
            paragraphs.append(text)
    for node in soup.find_all(blocks):
        # HTML parsers can preserve malformed nested <p> tags. Read each block
        # tree once, retaining leading/trailing prose in its actual order.
        if not node.find_parent(blocks):
            read_block(node)
    if not paragraphs:
        # Accept prose in an explicit WP/article container, never an entire unscoped page.
        paragraphs = [_clean(line) for line in soup.get_text("\n", strip=True).splitlines() if _clean(line)]
    return paragraphs


def _result(candidate, title, published, paragraphs, method):
    title = _clean(BeautifulSoup(title or "", "html.parser").get_text(" ", strip=True))
    if not title:
        title = _clean(candidate.get("source_title") or candidate.get("title"))
    if not title:
        raise SourceFetchError("source_title_missing")
    # Publication can remain unknown; editorial review must not invent a date.
    published = _date(published) or _date(candidate.get("published_date"))
    selected, chars, truncated = [], 0, False
    for text in paragraphs:
        if len(selected) >= MAX_PARAGRAPHS:
            truncated = True
            break
        # Count the exact paragraph separators included in the returned text/hash.
        remaining = MAX_TEXT_CHARS - chars - (2 if selected else 0)
        if remaining <= 0:
            truncated = True
            break
        allowed = min(remaining, MAX_PARAGRAPH_CHARS)
        if len(text) > allowed:
            text = text[:allowed].rstrip()
            truncated = True
        if not text:
            continue
        selected.append({"id": f"p{len(selected) + 1}", "text": text})
        chars += len(text) + (2 if len(selected) > 1 else 0)
    body = "\n\n".join(item["text"] for item in selected)
    if len(body) < MIN_TEXT_CHARS:
        raise SourceFetchError("source_text_too_short")
    return {"source_url": canonical_url(candidate["source_url"]), "title": title[:500],
            "published_date": published, "text": body, "paragraphs": selected,
            "content_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "extraction_method": method, "truncated": truncated}


def _from_wordpress(candidate, source, session):
    discovery = candidate.get("discovery") or {}
    if not isinstance(discovery, dict):
        raise SourceFetchError("invalid_source_discovery")
    if discovery.get("endpoint") is not None and _identity(discovery["endpoint"], source) != _identity(source["endpoint"], source):
        raise SourceFetchError("invalid_source_discovery")
    post_id = discovery.get("source_post_id")
    fields = "id,date,link,title,content,status"
    if post_id is not None:
        if type(post_id) is not int or not 1 <= post_id <= 2_147_483_647:
            raise SourceFetchError("invalid_source_post_id")
        endpoint = source["endpoint"] + "/" + str(post_id) + "?" + urlencode({"_fields": fields})
    else:
        slug = unquote(urlsplit(candidate["source_url"]).path.rstrip("/").split("/")[-1])
        if not slug or len(slug) > 350 or not re.fullmatch(r"[\w-]+", slug):
            return None
        endpoint = source["endpoint"] + "?" + urlencode({"slug": slug, "per_page": 2, "_fields": fields})
    try:
        raw, content_type, _ = _fetch(endpoint, source, session, accept="application/json")
    except SourceFetchError as error:
        if error.code == "source_not_found":
            return None
        raise
    if "json" not in content_type.lower():
        # A challenge response is not a reason to try bypassing it through HTML.
        _blocked(BeautifulSoup(raw, "html.parser"), page=True)
        return None
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as error:
        raise SourceFetchError("invalid_wordpress_response") from error
    posts = [payload] if post_id is not None else payload
    if not isinstance(posts, list) or len(posts) > 2:
        raise SourceFetchError("invalid_wordpress_response")
    if not posts:
        return None
    expected = _identity(candidate["source_url"], source)
    matching = []
    for post in posts:
        if not isinstance(post, dict):
            raise SourceFetchError("invalid_wordpress_response")
        if post_id is not None and post.get("id") != post_id:
            raise SourceFetchError("source_identity_mismatch")
        if _identity(post.get("link", ""), source) == expected:
            matching.append(post)
    if len(matching) != 1:
        raise SourceFetchError("source_identity_mismatch")
    post = matching[0]
    if post.get("status", "publish") != "publish":
        raise SourceFetchError("source_access_restricted")
    content = post.get("content")
    if not isinstance(content, dict):
        return None
    if content.get("protected"):
        raise SourceFetchError("source_access_restricted")
    rendered = content.get("rendered")
    if not isinstance(rendered, str) or not rendered.strip():
        return None
    title = post.get("title", {}).get("rendered", "")
    try:
        return _result(candidate, title, post.get("date"), _paragraphs(rendered), "wordpress_rest_content")
    except SourceFetchError as error:
        if error.code == "source_text_too_short":
            return None
        raise


def _jsonld_articles(soup):
    def walk(node):
        if isinstance(node, list):
            for child in node:
                yield from walk(child)
        elif isinstance(node, dict):
            types = node.get("@type", [])
            types = [types] if isinstance(types, str) else types
            if any(kind in ("NewsArticle", "Article", "ReportageNewsArticle") for kind in types):
                yield node
            yield from walk(node.get("@graph", []))
    for node in soup.select("script[type='application/ld+json']"):
        try:
            yield from walk(json.loads(node.string or node.get_text()))
        except (ValueError, TypeError, RecursionError):
            continue


def _from_html(candidate, source, session):
    raw, content_type, final_url = _fetch(candidate["source_url"], source, session, accept="text/html")
    if "html" not in content_type.lower():
        raise SourceFetchError("unsupported_source_content_type")
    if _identity(final_url, source) != _identity(candidate["source_url"], source):
        raise SourceFetchError("source_identity_mismatch")
    soup = BeautifulSoup(raw, "html.parser")
    _blocked(soup, page=True)
    canonical = soup.find("link", rel="canonical")
    if canonical and canonical.get("href") and _identity(urljoin(final_url, canonical["href"]), source) != _identity(candidate["source_url"], source):
        raise SourceFetchError("source_identity_mismatch")
    structured = list(_jsonld_articles(soup))
    # No embedded/paywalled body is used as an alternate extraction path.
    if any(str(item.get("isAccessibleForFree", "true")).lower() == "false" for item in structured):
        raise SourceFetchError("source_access_restricted")
    title_tag = soup.find("meta", property="og:title")
    heading = soup.find("h1")
    title = title_tag.get("content") if title_tag else heading.get_text(" ", strip=True) if heading else None
    published_tag = soup.find("meta", property="article:published_time")
    published = published_tag.get("content") if published_tag else None
    if not _date(published):
        expected = _identity(candidate["source_url"], source)
        def same_article(item):
            declared = item.get("url") or item.get("mainEntityOfPage")
            if isinstance(declared, dict):
                declared = declared.get("@id")
            if not declared:
                if source.get("kind", "wordpress") == "wordpress":
                    return True  # Preserve the established WP fallback contract.
                headline = _clean(item.get("headline"))
                return bool(headline and title and normalize(headline) == normalize(_clean(title)))
            try:
                return _identity(urljoin(final_url, declared), source) == expected
            except (SourceFetchError, TypeError):
                return False
        published = next((item.get("datePublished") for item in structured
                          if same_article(item) and _date(item.get("datePublished"))), None)
    selectors = PUBLISHER_ARTICLE_SELECTORS.get(source["id"], ARTICLE_SELECTORS)
    container = next((soup.select_one(selector) for selector in selectors if soup.select_one(selector)), None)
    if not container:
        raise SourceFetchError("source_article_container_missing")
    if not _date(published):
        # A clock in the site header is not the article publication date.
        article_scope = container.find_parent("article") or container
        time_tag = article_scope.select_one("time[datetime][itemprop='datePublished'], time[datetime].entry-date.published")
        published = time_tag.get("datetime") if time_tag else None
    if source.get("kind", "wordpress") != "wordpress" and not _date(published):
        raise SourceFetchError("source_publication_date_missing")
    return _result(candidate, title, published, _paragraphs(str(container)), "html_article_content")


def fetch_article(candidate, session=None):
    """Return bounded public source text + paragraph anchors; never persist text.

    A stale/mismatching API identifier is a hard error, not permission to switch
    articles. HTML fallback is only for a missing/empty publicly accessible API.
    """
    if not isinstance(candidate, dict):
        raise SourceFetchError("invalid_candidate")
    try:
        url = canonical_url(candidate.get("source_url", ""))
    except (ValueError, TypeError) as error:
        raise SourceFetchError("invalid_source_url") from error
    source = next((source for source in SOURCES if urlsplit(url).hostname in source["hosts"]), None)
    if source is None:
        raise SourceFetchError("unsupported_publisher")
    if source.get("access") == "restricted":
        raise SourceFetchError("source_access_restricted")
    if candidate.get("publisher") and candidate["publisher"] != source["name"]:
        raise SourceFetchError("source_publisher_mismatch")
    candidate = {**candidate, "source_url": url}
    owns_session = session is None
    if owns_session:
        session = requests.Session()
    # Never reuse a GitHub/AI authenticated session for fetching public news.
    if getattr(session, "auth", None) or getattr(session, "cookies", None) or any(str(key).lower() in ("authorization", "cookie", "proxy-authorization", "x-api-key") for key in getattr(session, "headers", {})):
        raise SourceFetchError("credentialed_source_session")
    session.trust_env = False  # Disable implicit .netrc authentication and proxy credentials.
    try:
        result = _from_wordpress(candidate, source, session) if source.get("kind", "wordpress") == "wordpress" else None
        return result if result is not None else _from_html(candidate, source, session)
    except SourceFetchError:
        raise
    except (ValueError, TypeError, AttributeError, RecursionError) as error:
        raise SourceFetchError("invalid_source_document") from error
    finally:
        if owns_session:
            session.close()
