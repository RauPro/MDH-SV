"""Bounded public HTML/sitemap discovery; metadata checkpoints, never article bodies."""
from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timezone
from urllib.parse import unquote, urljoin, urlsplit
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup

from .source_urls import canonical_url, normalize
from .network_safety import require_public_dns

MAX_INDEX_BYTES = 16 * 1024 * 1024
MAX_ARTICLE_BYTES = 1_000_000
MAX_DISCOVERED_URLS = 200_000
USER_AGENT = "MDH-SV/2.4 public-archive (+https://github.com/RauPro/MDH-SV)"


class ArchiveError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class BudgetExhausted(Exception):
    pass


def _request(url, source, session, budget, limit):
    """Count every HTTP request, including redirect hops; never retry a 403."""
    for _ in range(4):
        try:
            parts = urlsplit(url)
            safe = parts.scheme == "https" and parts.hostname in source["hosts"] and not parts.username and not parts.password and not parts.port
        except ValueError:
            safe = False
        if not safe:
            raise ArchiveError("unsafe_url")
        if budget["requests"] >= budget["limit"]:
            raise BudgetExhausted()
        try:
            require_public_dns(parts.hostname)
        except (OSError, ValueError) as error:
            raise ArchiveError("unsafe_dns") from error
        budget["requests"] += 1
        try:
            with session.get(url, timeout=(8, 20), stream=True, allow_redirects=False,
                    headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xml,text/xml,application/json", "Authorization": None, "Cookie": None}) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    if not response.headers.get("Location"):
                        raise ArchiveError("invalid_redirect")
                    url = urljoin(url, response.headers["Location"])
                    continue
                if response.status_code in (401, 402, 403, 429, 503):
                    raise ArchiveError("access_restricted")
                if response.status_code != 200:
                    raise ArchiveError(f"http_{response.status_code}")
                content = bytearray()
                content_type = response.headers.get("Content-Type", "")
                effective_limit = limit if "xml" in content_type.lower() else min(limit, MAX_ARTICLE_BYTES)
                deadline = time.monotonic() + 60
                for chunk in response.iter_content(65536):
                    content.extend(chunk)
                    budget["response_bytes"] = budget.get("response_bytes", 0) + len(chunk)
                    if len(content) > effective_limit:
                        raise ArchiveError("response_too_large")
                    if time.monotonic() > deadline:
                        raise ArchiveError("response_deadline")
                return bytes(content), content_type, url
        except requests.RequestException as error:
            raise ArchiveError("network_error") from error
    raise ArchiveError("too_many_redirects")


def _article_url(value, base, source):
    try:
        joined = urljoin(base, value)
        parts = urlsplit(joined)
        # Old public indexes contain HTTP links. Upgrade only explicitly approved
        # hosts before any request; credentials/ports still fail canonical_url.
        if parts.scheme == "http" and parts.hostname in source["hosts"]:
            joined = parts._replace(scheme="https").geturl()
        url = canonical_url(joined, set(source["hosts"]))
    except (ValueError, TypeError):
        return None
    path = urlsplit(url).path
    if re.search(r"/(?:api|wp-json|admin|search|buscar|author|autor|tag|category|suscrib|login)(?:/|\b)", path, re.I):
        return None
    if re.search(r"\.(?:jpg|jpeg|png|webp|gif|pdf|mp4|css|js|xml)$", path, re.I):
        return None
    return url if len([part for part in path.split("/") if part]) >= 2 else None


def _blocked(soup):
    title = normalize(soup.title.get_text(" ", strip=True) if soup.title else "")
    if re.search(r"\b404\b|not found|pagina no encontrada|pagina no existe", title):
        raise ArchiveError("source_not_found")
    if any(term in title for term in ("just a moment", "access denied", "error 403", "verify you are human")):
        raise ArchiveError("access_restricted")
    if soup.select_one("#challenge-form,.cf-challenge,[class*='paywall'],[id*='paywall']"):
        raise ArchiveError("access_restricted")


def parse_index(raw, content_type, url, source, diagnostics=None, selection_hints=None):
    """Return URLs. Anchor relevance is ephemeral; no anchor title/date is published."""
    articles, indexes, observed = [], [], 0
    if "xml" in content_type.lower() or raw.lstrip().startswith((b"<?xml", b"<urlset", b"<sitemapindex")):
        if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
            raise ArchiveError("unsafe_xml")
        try:
            root = ElementTree.fromstring(raw)
        except ElementTree.ParseError as error:
            raise ArchiveError("invalid_sitemap") from error
        kind = root.tag.rsplit("}", 1)[-1]
        if kind not in ("urlset", "sitemapindex"):
            raise ArchiveError("invalid_sitemap")
        for child in root:
            location = next((node.text for node in child if node.tag.rsplit("}", 1)[-1] == "loc"), None)
            if not location:
                continue
            observed += 1
            if kind == "sitemapindex":
                try:
                    indexes.append(canonical_url(location.strip(), set(source["hosts"])))
                except ValueError:
                    continue
            else:
                article = _article_url(location.strip(), url, source)
                if article:
                    articles.append(article)
    else:
        from .contracts.reference import TOPICS as TOPICS
        terms = tuple(term for group in TOPICS.values() for term in group)
        soup = BeautifulSoup(raw, "html.parser")
        _blocked(soup)
        for anchor in soup.find_all("a", href=True):
            observed += 1
            article = _article_url(anchor["href"], url, source)
            if article:
                articles.append(article)
                if selection_hints is not None:
                    hint = normalize(anchor.get_text(" ", strip=True))
                    selection_hints[article] = selection_hints.get(article, False) or any(term in hint for term in terms)
    if len(articles) > MAX_DISCOVERED_URLS:
        raise ArchiveError("too_many_urls")
    if diagnostics is not None:
        diagnostics.update(url_entries_observed=observed,
            url_entries_rejected=observed - len(articles) - len(indexes),
            duplicate_article_urls=len(articles) - len(set(articles)),
            article_urls_matching_anchor=sum(bool(value) for value in (selection_hints or {}).values()))
    return list(dict.fromkeys(articles)), list(dict.fromkeys(indexes))


def _published_date(value):
    if not isinstance(value, str):
        return None
    # The source declares the entire calendar date, even for legacy hours such as "6:03:00".
    match = re.match(r"^(\d{4}-\d{2}-\d{2})(?:[T\s]|$)", value.strip())
    if not match:
        return None
    try:
        return date.fromisoformat(match[1]).isoformat()
    except ValueError:
        return None


def _metadata_html(raw, url):
    """Decode the verified EDH legacy archive without guessing Central European encodings.

    Legacy /historico/ pages serve Latin-1/Windows-1252 bytes with neither an
    HTTP charset nor an HTML charset. Generic detection can turn policías into
    policÝas. Honor HTML declarations; otherwise prefer valid UTF-8, then the
    archive's observed Windows-1252 encoding. Other publishers remain unchanged.
    """
    if not isinstance(raw, bytes):
        return raw
    parts = urlsplit(url)
    if parts.hostname != "historico.elsalvador.com" or not parts.path.startswith("/historico/"):
        return raw
    declared = re.search(br"<meta\b[^>]*\bcharset\s*=\s*['\"]?\s*[a-z0-9._:-]+", raw[:16384], re.I)
    if declared:
        return raw
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            return raw.decode("windows-1252")
        except UnicodeDecodeError as error:
            raise ArchiveError("invalid_metadata_encoding") from error


def parse_article_metadata(raw, url, source):
    """Read publisher metadata; no body text, lastmod, dateModified or URL dates."""
    soup = BeautifulSoup(_metadata_html(raw, url), "html.parser")
    _blocked(soup)
    canonical = soup.find("link", rel="canonical")
    if canonical and canonical.get("href"):
        canonical_value = _article_url(canonical["href"], url, source)
        if not canonical_value:
            raise ArchiveError("invalid_canonical")
        a, b = urlsplit(canonical_value), urlsplit(url)
        if (a.hostname.removeprefix("www."), a.path.rstrip("/")) != (b.hostname.removeprefix("www."), b.path.rstrip("/")):
            raise ArchiveError("canonical_mismatch")
    tag = soup.find("meta", property="og:title")
    heading = soup.find("h1")
    title = tag.get("content") if tag else heading.get_text(" ", strip=True) if heading else None
    published = None
    for selector in ("meta[property='article:published_time']", "meta[itemprop='datePublished']", "meta[name='DC.date']", "meta[name='citation_publication_date']", "time[itemprop='datePublished'][datetime]"):
        node = soup.select_one(selector)
        if node:
            published = _published_date(node.get("content") or node.get("datetime"))
            if published:
                break
    for node in soup.select("script[type='application/ld+json']"):
        try:
            payload = json.loads(node.string or node.get_text())
        except (ValueError, TypeError):
            continue
        candidates = payload if isinstance(payload, list) else [payload]
        for item in list(candidates):
            if isinstance(item, dict) and isinstance(item.get("@graph"), list):
                candidates.extend(item["@graph"])
        for item in candidates:
            if not isinstance(item, dict):
                continue
            types = item.get("@type", [])
            types = [types] if isinstance(types, str) else types
            if not isinstance(types, list) or not any(t in ("Article", "NewsArticle", "ReportageNewsArticle") for t in types):
                continue
            identity = item.get("url") or item.get("mainEntityOfPage") or item.get("@id")
            if isinstance(identity, dict):
                identity = identity.get("@id") or identity.get("url")
            declared = _article_url(identity, url, source) if isinstance(identity, str) else None
            if not declared or declared.rstrip("/") != url.rstrip("/"):
                continue
            if str(item.get("isAccessibleForFree", "true")).lower() == "false":
                raise ArchiveError("access_restricted")
            published = published or _published_date(item.get("datePublished"))
            title = title or item.get("headline")
    if not isinstance(title, str) or not title.strip():
        raise ArchiveError("missing_title")
    if not published:
        raise ArchiveError("missing_publication_date")
    return {"title": title, "source_url": url, "published_date": published, "source_post_id": None}


def collect_html_archive(source, start, end, folder, *, max_pages=150, refresh=False, now=None, session=None):
    from .history import atomic_json, read_json, reference_from_metadata, SCREEN_VERSION
    from .contracts.reference import TOPICS as TOPICS
    if start > end or not 1 <= max_pages <= 1000:
        raise ValueError("Invalid archive interval/budget")
    now = now or datetime.now(timezone.utc).isoformat()
    path = folder / (source["id"] + ".json")
    state = read_json(path, {"schema_version": 1, "source_id": source["id"], "months": {}, "records": []})
    records = {record["id"]: record for record in state["records"]}
    collection = state.setdefault("collection", {})
    collection.update(method="html_archive", access=source.get("access", "public"), notice=source.get("notice", "Cobertura parcial de índices públicos; no acredita una hemeroteca completa."))
    if source.get("access") == "restricted":
        collection.update(phase="restricted")
        collection.setdefault("last_attempt_at", None)
        atomic_json(path, state)
        return {"source": source["id"], "requests": 0, "successful_pages": 0, "failed_page_attempts": 0, "references": len(records), "enumerated_months": 0}
    collection["notice"] += " La consulta de artículos se limita a términos temáticos en URLs o enlaces de los listados y a enlaces históricos configurados; puede omitir noticias pertinentes. Los títulos y fechas publicados se obtienen de cada artículo."
    if refresh:
        for key in ("indexes", "index_cursor", "articles", "article_cursor", "visited", "failures"):
            collection.pop(key, None)
    collection.setdefault("indexes", list(source.get("archive_urls", [])))
    collection.setdefault("index_cursor", 0)
    collection.setdefault("articles", list(source.get("seed_urls", [])))
    collection.setdefault("article_cursor", 0)
    collection.setdefault("visited", {})
    collection.setdefault("failures", {})
    collection.setdefault("index_stats", {})
    if collection.get("index_screen_version") != SCREEN_VERSION:
        collection["index_cursor"] = 0
        collection["index_screen_version"] = SCREEN_VERSION
    # Refresh completed public listings to discover new links. Historical queues
    # retain their cursor while incomplete, without downloading huge XML on each run.
    if (collection.get("phase") in ("complete", "partial")
            and collection["index_cursor"] >= len(collection["indexes"])
            and collection["article_cursor"] >= len(collection["articles"])):
        collection["index_cursor"] = 0
    for seed in reversed(source.get("seed_urls", [])):
        if seed not in collection["articles"]:
            collection["articles"].insert(collection["article_cursor"], seed)
    for index in source.get("archive_urls", []):
        if index not in collection["indexes"]:
            collection["indexes"].append(index)
    known_articles = set(collection["articles"])
    budget = {"requests": 0, "limit": max_pages, "response_bytes": 0}
    success, failures, consecutive = 0, 0, 0
    owned = session is None
    session = session or requests.Session()
    if getattr(session, "auth", None) or getattr(session, "cookies", None) or any(k.lower() in ("authorization", "cookie", "x-api-key") for k in getattr(session, "headers", {})):
        raise ArchiveError("credentialed_session")
    session.trust_env = False

    def save():
        state["records"] = sorted(records.values(), key=lambda r: (r["published_date"], r["id"]), reverse=True)
        atomic_json(path, state)

    def failure(url, error):
        nonlocal failures, consecutive
        failures += 1
        consecutive += 1
        collection["failures"][url] = {"code": error.code, "last_attempt_at": now}

    def needs_read(value):
        return (not value or value["status"] == "failed" or value.get("screen_version") != SCREEN_VERSION
                or (value["status"] == "outside_range" and start.isoformat() <= value.get("published_date", "") <= end.isoformat()))

    try:
        collection["phase"] = "scanning"
        pending_indexes = collection["indexes"][collection["index_cursor"]:]
        extra_indexes = sorted((url for url in collection["indexes"] if url not in pending_indexes
            and (url in collection["failures"] or not urlsplit(url).path.lower().endswith(".xml"))),
            key=lambda url: collection["failures"].get(url, collection["index_stats"].get(url, {})).get("last_attempt_at", ""))
        index_jobs = [(url, True) for url in pending_indexes] + [(url, False) for url in extra_indexes]
        for url, pending_index in index_jobs:
            if budget["requests"] >= max_pages or consecutive >= 3:
                break
            if (budget["requests"] >= max_pages - 1
                    and (collection["article_cursor"] < len(collection["articles"])
                         or any(needs_read(value) for value in collection["visited"].values()))):
                break  # Even tiny budgets must advance article metadata, not only refresh listings.
            collection["last_attempt_at"] = now
            try:
                raw, content_type, final = _request(url, source, session, budget, MAX_INDEX_BYTES)
                diagnostics, selection_hints = {}, {}
                articles, indexes = parse_index(raw, content_type, final, source, diagnostics, selection_hints)
                terms = tuple(term for group in TOPICS.values() for term in group)
                relevant = [article for article in articles if selection_hints.get(article)
                            or any(term in normalize(unquote(article)).replace("-", " ") for term in terms)]
                collection["index_stats"][url] = {**diagnostics, "article_urls_observed": len(articles), "article_urls_selected": len(relevant), "last_attempt_at": now}
                fresh = [article for article in relevant if article not in known_articles]
                if len(known_articles) + len(fresh) > MAX_DISCOVERED_URLS:
                    raise ArchiveError("too_many_urls")
                if "xml" not in content_type.lower() and fresh:
                    cut = collection["article_cursor"]
                    old_pending = collection["articles"][cut:]
                    seeds = set(source.get("seed_urls", []))
                    collection["articles"][cut:] = ([u for u in old_pending if u in seeds]
                                                   + fresh + [u for u in old_pending if u not in seeds])
                else:
                    collection["articles"].extend(fresh)
                known_articles.update(fresh)
                new_indexes = [index for index in indexes if index not in collection["indexes"]]
                if len(collection["indexes"]) + len(new_indexes) > 1000:
                    raise ArchiveError("too_many_indexes")
                collection["indexes"].extend(new_indexes)
                index_jobs.extend((index, True) for index in new_indexes)
                collection["failures"].pop(url, None)
                success += 1
                consecutive = 0
            except BudgetExhausted:
                save()
                break
            except ArchiveError as error:
                failure(url, error)
            if pending_index:
                collection["index_cursor"] += 1
            save()
        # Failed article retries are deferred until untouched URLs are exhausted.
        retries = sorted((url for url, value in collection["visited"].items() if needs_read(value)), key=lambda url: collection["visited"][url].get("last_attempt_at", ""))
        while budget["requests"] < max_pages and consecutive < 3:
            pending = collection["article_cursor"] < len(collection["articles"])
            if pending:
                url = collection["articles"][collection["article_cursor"]]
            elif retries:
                url = retries.pop(0)
            else:
                break
            visited = collection["visited"].get(url)
            if not needs_read(visited):
                if pending:
                    collection["article_cursor"] += 1
                continue
            collection["last_attempt_at"] = now
            try:
                raw, _, final = _request(url, source, session, budget, MAX_ARTICLE_BYTES)
                metadata = parse_article_metadata(raw, final, source)
                published = date.fromisoformat(metadata["published_date"])
                reference = None
                if start <= published <= end:
                    reference = reference_from_metadata(metadata["title"], metadata["source_url"], metadata["published_date"], source, start, end, now)
                    month = published.strftime("%Y-%m")
                    progress = state["months"].setdefault(month, {"status": "partial", "posts_scanned": 0, "pages_fetched": 0, "invalid_posts": 0, "source_total_posts": None, "screen_version": SCREEN_VERSION})
                    progress.update(status="partial", last_attempt_at=now, error=None)
                    if not visited or visited.get("status") in ("failed", "outside_range"):
                        progress["posts_scanned"] += 1
                        progress["pages_fetched"] += 1
                if reference:
                    if reference["id"] in records:
                        reference["discovery"]["retrieved_at"] = records[reference["id"]]["discovery"]["retrieved_at"]
                    records[reference["id"]] = reference
                collection["visited"][url] = {"status": "selected" if reference else "not_relevant" if start <= published <= end else "outside_range", "published_date": published.isoformat(), "last_attempt_at": now, "screen_version": SCREEN_VERSION}
                collection["failures"].pop(url, None)
                success += 1
                consecutive = 0
            except BudgetExhausted:
                save()
                break
            except (ArchiveError, ValueError) as error:
                error = error if isinstance(error, ArchiveError) else ArchiveError("invalid_metadata")
                failure(url, error)
                collection["visited"][url] = {"status": "failed", "last_attempt_at": now, "screen_version": SCREEN_VERSION}
            if pending:
                collection["article_cursor"] += 1
            save()
        finished = collection["index_cursor"] >= len(collection["indexes"]) and collection["article_cursor"] >= len(collection["articles"])
        collection["phase"] = "complete" if finished and not collection["failures"] else "partial"
        save()
    finally:
        if owned:
            session.close()
    return {"source": source["id"], "requests": budget["requests"], "response_bytes": budget["response_bytes"], "successful_pages": success, "failed_page_attempts": failures, "stopped_after_consecutive_failures": consecutive >= 3, "references": len(records), "enumerated_months": 0}
