import json
from datetime import date

import pytest

from mdh_pipeline.archive_sources import (
    ArchiveError, MAX_ARTICLE_BYTES, collect_html_archive, parse_article_metadata, parse_index,
)

SOURCE = {"id": "archive-test", "name": "Archivo de prueba", "kind": "html_archive",
          "endpoint": "https://historico.elsalvador.com/historico/index.xml",
          "hosts": ["historico.elsalvador.com"],
          "archive_urls": ["https://historico.elsalvador.com/historico/index.xml"]}
START, END = date(2010, 1, 1), date(2026, 10, 10)
NOW = "2026-10-10T20:00:00+00:00"
URL = "https://historico.elsalvador.com/historico/123/derechos-humanos.html"
URL2 = "https://historico.elsalvador.com/historico/456/denuncia-detenciones.html"


class Response:
    def __init__(self, raw, status=200, content_type="text/html", headers=None):
        self.raw = raw.encode() if isinstance(raw, str) else raw
        self.status_code = status
        self.headers = {"Content-Type": content_type, **(headers or {})}
    def __enter__(self): return self
    def __exit__(self, *args): return None
    def iter_content(self, size):
        for i in range(0, len(self.raw), size): yield self.raw[i:i + size]


class Session:
    def __init__(self, responses): self.responses, self.calls = iter(responses), []
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return next(self.responses)


def sitemap(*urls):
    return '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + ''.join(f'<url><loc>{url}</loc><lastmod>1969-12-31</lastmod></url>' for url in urls) + '</urlset>'


def article(url=URL, date_value="2013-03-13 6:03:00"):
    return f'<html><head><link rel="canonical" href="{url}"><meta property="og:title" content="Denuncian violaciones de derechos humanos"><meta property="article:published_time" content="{date_value}"></head><body><p>SYNTHETIC BODY NEVER PERSISTED</p></body></html>'


@pytest.fixture(autouse=True)
def no_dns(monkeypatch):
    monkeypatch.setattr("mdh_pipeline.archive_sources.require_public_dns", lambda host: None)


def state(folder): return json.loads((folder / "archive-test.json").read_text())


def test_sitemap_lastmod_is_never_publication_date_and_html_suffix_is_preserved(tmp_path):
    session = Session([Response(sitemap(URL), content_type="text/xml"), Response(article())])
    report = collect_html_archive(SOURCE, START, END, tmp_path, max_pages=2, session=session, now=NOW)
    checkpoint = state(tmp_path)
    reference = checkpoint["records"][0]
    assert reference["published_date"] == "2013-03-13"
    assert reference["source_url"] == URL
    assert reference["discovery"]["source_post_id"] is None
    assert reference["discovery"]["method"] == "html_article_metadata"
    assert report["requests"] == 2
    assert report["enumerated_months"] == 0
    assert checkpoint["months"]["2013-03"]["status"] == "partial"
    assert "SYNTHETIC BODY" not in (tmp_path / "archive-test.json").read_text()


def test_budget_resume_does_not_repeat_sitemap_and_deduplicates_urls(tmp_path):
    first = Session([Response(sitemap(URL, URL, URL2), content_type="application/xml")])
    collect_html_archive(SOURCE, START, END, tmp_path, max_pages=1, session=first, now=NOW)
    assert state(tmp_path)["collection"]["article_cursor"] == 0
    second = Session([Response(article()), Response(article(URL2))])
    report = collect_html_archive(SOURCE, START, END, tmp_path, max_pages=2, session=second, now=NOW)
    assert [call[0] for call in second.calls] == [URL, URL2]
    assert report["references"] == 2
    assert len(state(tmp_path)["records"]) == 2


def test_multiple_indexes_resume_even_when_first_has_no_relevant_urls(tmp_path):
    source = {**SOURCE, "archive_urls": [SOURCE["endpoint"], SOURCE["endpoint"].replace("index.xml", "index2.xml")]}
    collect_html_archive(source, START, END, tmp_path, max_pages=1,
                         session=Session([Response(sitemap(), content_type="text/xml")]), now=NOW)
    second = Session([Response(sitemap(URL), content_type="text/xml"), Response(article())])
    collect_html_archive(source, START, END, tmp_path, max_pages=2, session=second, now=NOW)
    assert second.calls[0][0] == source["archive_urls"][1]
    assert len(state(tmp_path)["records"]) == 1


def test_redirect_hops_count_toward_total_request_budget(tmp_path):
    first = Session([Response(b"", status=302, headers={"Location": SOURCE["endpoint"] + "?next=1"})])
    report = collect_html_archive(SOURCE, START, END, tmp_path, max_pages=1, session=first, now=NOW)
    assert report["requests"] == 1
    assert state(tmp_path)["collection"]["index_cursor"] == 0
    assert not state(tmp_path)["collection"]["failures"]


def test_cross_host_redirect_is_recorded_without_fetching_it(tmp_path):
    session = Session([Response(b"", status=302, headers={"Location": "https://evil.example/private"})])
    collect_html_archive(SOURCE, START, END, tmp_path, max_pages=2, session=session, now=NOW)
    assert len(session.calls) == 1
    assert state(tmp_path)["collection"]["failures"][SOURCE["endpoint"]]["code"] == "unsafe_url"


def test_configured_restricted_source_makes_no_requests(tmp_path):
    session = Session([])
    report = collect_html_archive({**SOURCE, "access": "restricted"}, START, END, tmp_path, session=session, now=NOW)
    assert not session.calls
    assert report["requests"] == 0
    assert state(tmp_path)["collection"]["phase"] == "restricted"
    assert state(tmp_path)["collection"]["last_attempt_at"] is None


def test_three_restricted_articles_stop_and_keep_errors_for_later_retry(tmp_path):
    urls = [URL, URL2, URL2.replace("456", "789"), URL2.replace("456", "999")]
    session = Session([Response(sitemap(*urls), content_type="text/xml")] + [Response(b"restricted", status=403)] * 3)
    report = collect_html_archive(SOURCE, START, END, tmp_path, max_pages=20, session=session, now=NOW)
    assert report["requests"] == 4
    assert report["stopped_after_consecutive_failures"] is True
    assert len(state(tmp_path)["collection"]["failures"]) == 3
    assert not state(tmp_path)["records"]
    second = Session([Response(article(urls[3]))])
    collect_html_archive(SOURCE, START, END, tmp_path, max_pages=1, session=second, now=NOW)
    assert second.calls[0][0] == urls[3]


def test_html_listings_and_explicit_seeds_are_supported(tmp_path):
    source = {**SOURCE, "archive_urls": ["https://historico.elsalvador.com/noticias"], "seed_urls": [URL2]}
    listing = f'<html><a href="{URL}">Derechos humanos</a><a href="https://evil.example/otro">No</a><a href="/deportes/futbol.html">No pertinente</a></html>'
    session = Session([Response(listing), Response(article(URL2)), Response(article())])
    collect_html_archive(source, START, END, tmp_path, max_pages=3, session=session, now=NOW)
    assert len(state(tmp_path)["records"]) == 2
    stats = state(tmp_path)["collection"]["index_stats"][source["archive_urls"][0]]
    assert stats["article_urls_observed"] == 2
    assert stats["article_urls_selected"] == 1


def test_no_date_from_url_lastmod_modified_or_migration_metadata():
    raw = b'<meta property="og:title" content="Noticia"><meta property="article:modified_time" content="2010-01-02"><meta name="DC.date.issued" content="2026-01-01">'
    with pytest.raises(ArchiveError, match="missing_publication_date"):
        parse_article_metadata(raw, URL.replace("123", "20100102"), SOURCE)
    raw = b'<meta property="og:title" content="Noticia"><meta name="DC.date" content="2016-04-25T18:02:15-06:00">'
    assert parse_article_metadata(raw, URL, SOURCE)["published_date"] == "2016-04-25"


def test_jsonld_date_is_article_scoped_and_paywall_is_not_bypassed():
    raw = ('<script type="application/ld+json">' + json.dumps({"@type": "NewsArticle", "url": URL, "headline": "Noticia", "datePublished": "2013-01-02"}) + '</script>').encode()
    assert parse_article_metadata(raw, URL, SOURCE)["published_date"] == "2013-01-02"
    with pytest.raises(ArchiveError, match="access_restricted"):
        parse_article_metadata(raw.replace(b'"headline"', b'"isAccessibleForFree":false,"headline"'), URL, SOURCE)


def test_jsonld_related_article_or_missing_identity_does_not_supply_publication_date():
    for identity in ({}, {"url": URL2}, {"mainEntityOfPage": {"@id": URL2}}):
        raw = ('<meta property="og:title" content="Título actual"><script type="application/ld+json">' + json.dumps({"@type": "NewsArticle", "headline": "Noticia relacionada", "datePublished": "2013-01-02", **identity}) + '</script>').encode()
        with pytest.raises(ArchiveError, match="missing_publication_date"):
            parse_article_metadata(raw, URL, SOURCE)


def test_soft_404_never_becomes_a_dated_reference_or_a_valid_index():
    raw = b'<title>Not Found 404 | Diario El Mundo</title><meta property="og:title" content="Diario El Mundo"><meta name="DC.date" content="2023-10-17">'
    with pytest.raises(ArchiveError, match="source_not_found"):
        parse_article_metadata(raw, URL, SOURCE)
    with pytest.raises(ArchiveError, match="source_not_found"):
        parse_index(raw, "text/html", URL, SOURCE)


def test_failed_index_retries_next_run_before_large_article_queue_finishes(tmp_path):
    source = {**SOURCE, "archive_urls": [SOURCE["endpoint"], SOURCE["endpoint"].replace("index.xml", "second.xml")]}
    first = Session([Response(b"restricted", status=403), Response(sitemap(URL, URL2), content_type="text/xml"), Response(article())])
    collect_html_archive(source, START, END, tmp_path, max_pages=3, session=first, now=NOW)
    assert state(tmp_path)["collection"]["article_cursor"] == 1
    second = Session([Response(sitemap(URL), content_type="text/xml"), Response(article(URL2))])
    collect_html_archive(source, START, END, tmp_path, max_pages=2, session=second, now=NOW)
    assert second.calls[0][0] == SOURCE["endpoint"]
    assert not state(tmp_path)["collection"]["failures"]
    assert len(state(tmp_path)["records"]) == 2


def test_small_html_listings_refresh_and_new_links_precede_old_backlog(tmp_path):
    listing_url = "https://historico.elsalvador.com/nacionales"
    source = {**SOURCE, "archive_urls": [listing_url]}
    listing = f'<a href="{URL}">Uno</a><a href="{URL2}">Dos</a>'
    first = Session([Response(listing), Response(article())])
    collect_html_archive(source, START, END, tmp_path, max_pages=2, session=first, now=NOW)
    fresh = URL2.replace("456", "999")
    second = Session([Response(listing + f'<a href="{fresh}">Nueva noticia</a>'), Response(article(fresh))])
    collect_html_archive(source, START, END, tmp_path, max_pages=2, session=second, now=NOW)
    assert [call[0] for call in second.calls] == [listing_url, fresh]
    assert state(tmp_path)["collection"]["articles"][-1] == URL2


def test_tiny_budget_advances_articles_instead_of_refreshing_two_listings_forever(tmp_path):
    source = {**SOURCE, "archive_urls": ["https://historico.elsalvador.com/nacionales", "https://historico.elsalvador.com/politica"]}
    listing = f'<a href="{URL}">Uno</a><a href="{URL2}">Dos</a>'
    first = Session([Response(listing), Response(article())])
    collect_html_archive(source, START, END, tmp_path, max_pages=2, session=first, now=NOW)
    assert state(tmp_path)["collection"]["article_cursor"] == 1
    second = Session([Response(listing), Response(article(URL2))])
    collect_html_archive(source, START, END, tmp_path, max_pages=2, session=second, now=NOW)
    assert state(tmp_path)["collection"]["article_cursor"] == 2
    assert len(state(tmp_path)["records"]) == 2
    assert [call[0] for call in second.calls] == [source["archive_urls"][1], URL2]


def test_previous_outside_range_is_reconsidered_after_interval_expands(tmp_path):
    first = Session([Response(sitemap(URL), content_type="text/xml"), Response(article())])
    collect_html_archive(SOURCE, date(2025, 1, 1), END, tmp_path, max_pages=2, session=first, now=NOW)
    assert state(tmp_path)["collection"]["visited"][URL]["status"] == "outside_range"
    second = Session([Response(sitemap(URL), content_type="text/xml"), Response(article())])
    collect_html_archive(SOURCE, START, END, tmp_path, max_pages=2, session=second, now=NOW)
    assert len(state(tmp_path)["records"]) == 1


def test_new_screening_version_rechecks_old_metadata(tmp_path, monkeypatch):
    from mdh_pipeline import history
    first = Session([Response(sitemap(URL), content_type="text/xml"), Response(article())])
    collect_html_archive(SOURCE, START, END, tmp_path, max_pages=2, session=first, now=NOW)
    monkeypatch.setattr(history, "SCREEN_VERSION", history.SCREEN_VERSION + 1)
    second = Session([Response(sitemap(URL), content_type="text/xml"), Response(article())])
    collect_html_archive(SOURCE, START, END, tmp_path, max_pages=2, session=second, now=NOW)
    assert [call[0] for call in second.calls] == [SOURCE["endpoint"], URL]
    assert len(state(tmp_path)["records"]) == 1


def test_xml_has_larger_bound_than_article_html(tmp_path):
    # Real EDH sitemaps are 10–11 MB; a generic article-size limit must not reject XML.
    padding = "<!--" + "x" * (MAX_ARTICLE_BYTES + 1) + "-->"
    session = Session([Response(sitemap(URL).replace("</urlset>", padding + "</urlset>"), content_type="text/xml")])
    report = collect_html_archive(SOURCE, START, END, tmp_path, max_pages=1, session=session, now=NOW)
    assert report["successful_pages"] == 1
    assert state(tmp_path)["collection"]["articles"] == [URL]


def test_xml_entities_are_rejected():
    with pytest.raises(ArchiveError, match="unsafe_xml"):
        parse_index(b'<!DOCTYPE urlset [<!ENTITY x SYSTEM "file:///secret">]><urlset/>', "text/xml", SOURCE["endpoint"], SOURCE)


def test_missing_article_date_is_a_visible_failure_not_a_fabricated_record(tmp_path):
    session = Session([Response(sitemap(URL), content_type="text/xml"), Response('<h1>Derechos humanos</h1>')])
    report = collect_html_archive(SOURCE, START, END, tmp_path, max_pages=2, session=session, now=NOW)
    assert report["references"] == 0
    assert state(tmp_path)["collection"]["failures"][URL]["code"] == "missing_publication_date"
    assert not state(tmp_path)["months"]
