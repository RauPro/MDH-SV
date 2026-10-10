import hashlib
import json

import pytest

from mdh_pipeline.article_source import (
    MAX_BYTES, MAX_PARAGRAPHS, MAX_TEXT_CHARS, SourceFetchError, fetch_article,
)

URL = "https://www.diariocolatino.com/noticia-publica/"
PARAGRAPH = "Texto ficticio de una noticia utilizado exclusivamente como fixture. La fuente describe una denuncia pública y distingue lo reportado de una conclusión judicial. Las personas entrevistadas solicitaron una investigación independiente."
CONTENT = "<p>" + PARAGRAPH + "</p><p>" + PARAGRAPH.replace("fixture", "prueba") + "</p>"


def candidate(**extra):
    return {"source_url": URL, "source_title": "Noticia pendiente de revisión", "publisher": "Diario Co Latino", "published_date": None, **extra}


def wp(**extra):
    return {"id": 123, "link": URL, "date": "2025-04-03T08:32:00", "status": "publish", "title": {"rendered": "Denuncia &amp; revisión de <em>fuentes</em>"}, "content": {"rendered": CONTENT, "protected": False}, **extra}


class Response:
    def __init__(self, payload, status=200, content_type="application/json", headers=None):
        self.payload = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.status_code = status
        self.headers = {"Content-Type": content_type, **(headers or {})}
    def __enter__(self): return self
    def __exit__(self, *args): return None
    def iter_content(self, size):
        for i in range(0, len(self.payload), size): yield self.payload[i:i + size]


class Session:
    def __init__(self, responses):
        self.responses, self.calls, self.headers, self.auth = iter(responses), [], {}, None
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return next(self.responses)


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    monkeypatch.setattr("mdh_pipeline.article_source.require_public_dns", lambda host: None)


def test_wordpress_post_id_is_publisher_bound_and_body_stays_in_memory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session = Session([Response(wp())])
    result = fetch_article(candidate(discovery={"source_post_id": 123, "endpoint": "https://www.diariocolatino.com/wp-json/wp/v2/posts"}), session)
    assert session.calls[0][0].startswith("https://www.diariocolatino.com/wp-json/wp/v2/posts/123?")
    assert session.calls[0][1]["allow_redirects"] is False
    assert session.trust_env is False
    assert result["source_url"] == URL
    assert result["title"] == "Denuncia & revisión de fuentes"
    assert result["published_date"] == "2025-04-03"
    assert result["extraction_method"] == "wordpress_rest_content"
    assert result["truncated"] is False
    assert result["paragraphs"][0] == {"id": "p1", "text": PARAGRAPH}
    assert result["text"] == "\n\n".join(p["text"] for p in result["paragraphs"])
    assert result["content_sha256"] == hashlib.sha256(result["text"].encode()).hexdigest()
    assert not list(tmp_path.iterdir())


def test_slug_lookup_is_one_bounded_api_request_and_requires_exact_article_link():
    session = Session([Response([wp()])])
    result = fetch_article(candidate(), session)
    assert len(session.calls) == 1
    assert "slug=noticia-publica" in session.calls[0][0]
    assert "per_page=2" in session.calls[0][0]
    assert result["published_date"] == "2025-04-03"
    with pytest.raises(SourceFetchError, match="source_identity_mismatch"):
        fetch_article(candidate(), Session([Response([wp(link="https://www.diariocolatino.com/noticia-distinta/")])]))


def test_only_www_alias_and_trailing_slash_are_equivalent():
    session = Session([Response([wp(link="https://diariocolatino.com/noticia-publica")])])
    result = fetch_article(candidate(), session)
    assert result["source_url"] == URL


def test_removes_nonarticle_content_and_avoids_nested_paragraph_duplication():
    dirty = '<nav><p>NAVEGACION</p></nav><script>ALERT</script><aside><p>RELACIONADA</p></aside><div class="advertisement"><p>PUBLICIDAD</p></div><h2>Encabezado</h2><blockquote><p>' + PARAGRAPH + '</p></blockquote><ul><li>Segundo punto</li></ul><p>' + PARAGRAPH + '</p>'
    result = fetch_article(candidate(), Session([Response([wp(content={"rendered": dirty})])]))
    assert all(term not in result["text"] for term in ["NAVEGACION", "ALERT", "RELACIONADA", "PUBLICIDAD"])
    assert len(result["paragraphs"]) == 4
    assert result["paragraphs"][0]["text"] == "Encabezado"
    assert result["paragraphs"][2]["text"] == "Segundo punto"


def html_page(content=CONTENT, *, meta="", ld=None, canonical=URL):
    jsonld = '<script type="application/ld+json">' + json.dumps(ld) + '</script>' if ld else ''
    return ('<html><head><title>Artículo público</title><link rel="canonical" href="' + canonical + '">' + meta + jsonld + '</head><body><h1>Título del artículo</h1><nav><p>NO EXTRAER</p></nav><article><div class="entry-content">' + content + '</div></article></body></html>').encode()


def test_html_fallback_extracts_jsonld_date_but_never_its_articlebody():
    page = html_page(ld={"@graph": [{"@type": "NewsArticle", "datePublished": "2025-05-14T12:00:00-06:00", "articleBody": "SECRET EMBEDDED BODY"}]})
    session = Session([Response([]), Response(page, content_type="text/html; charset=UTF-8")])
    result = fetch_article(candidate(), session)
    assert len(session.calls) == 2
    assert result["extraction_method"] == "html_article_content"
    assert result["published_date"] == "2025-05-14"
    assert "SECRET" not in result["text"] and "NO EXTRAER" not in result["text"]


def test_elementor_editorial_post_precedes_related_article_cards_and_excludes_theme_widgets():
    """Regression for GatoEncerrado's labor-hours article: <article> is only related news."""
    url = "https://gatoencerrado.news/2026/09/30/noticia-editorial/"
    page = (f'<html><head><link rel="canonical" href="{url}">'
            '<meta property="article:published_time" content="2026-10-01T02:32:11+00:00">'
            '</head><body><h1>Artículo editorial de prueba</h1>'
            '<div data-elementor-type="wp-post" data-elementor-post-type="elementor-hf"><p>ENCABEZADO AJENO</p></div>'
            '<article class="elementor-post"><h3>TARJETA RELACIONADA AJENA</h3></article>'
            '<div data-elementor-type="wp-post" data-elementor-post-type="post" data-elementor-id="83375">'
            '<div class="elementor-widget-author-box"><p>BIOGRAFIA AJENA</p></div>'
            '<div class="elementor-widget-post-info"><p>METADATOS AJENOS</p></div>'
            '<div class="elementor-widget-text-editor"><h2>Hechos de la fuente</h2>' + CONTENT + '</div>'
            '<div class="elementor-widget-html"><p>Contexto del gráfico editorial.</p><script>NO EJECUTAR</script></div>'
            '<div class="elementor-widget-posts"><article><h3>OTRA NOTICIA AJENA</h3><p>RESUMEN AJENO</p></article></div>'
            '<div class="elementor-widget-post-comments"><p>COMENTARIO AJENO</p></div>'
            '<div class="elementor-widget-share-buttons"><p>COMPARTIR AJENO</p></div>'
            '</div><div data-elementor-type="wp-post" data-elementor-post-type="elementor-hf"><p>PIE AJENO</p></div>'
            '</body></html>').encode()
    # The public API can redirect to the homepage while the public article remains available.
    session = Session([
        Response(b"", status=302, headers={"Location": "https://gatoencerrado.news"}),
        Response(b"<html><title>Medio de prueba</title></html>", content_type="text/html"),
        Response(page, content_type="text/html"),
    ])
    result = fetch_article(candidate(source_url=url, publisher="GatoEncerrado"), session)
    assert result["source_url"] == url
    assert result["published_date"] == "2026-10-01"
    assert result["extraction_method"] == "html_article_content"
    assert result["paragraphs"][0]["text"] == "Hechos de la fuente"
    assert len(result["paragraphs"]) == 4
    assert "Contexto del gráfico editorial." in result["text"]
    assert "AJENO" not in result["text"] and "AJENA" not in result["text"]
    assert "NO EJECUTAR" not in result["text"]
    assert result["truncated"] is False


def test_elementor_header_alone_is_not_an_editorial_article():
    page = ('<html><body><div data-elementor-type="wp-post" data-elementor-post-type="elementor-hf">'
            + CONTENT + '</div></body></html>').encode()
    with pytest.raises(SourceFetchError, match="source_article_container_missing"):
        fetch_article(candidate(), Session([Response([]), Response(page, content_type="text/html")]))


@pytest.mark.parametrize("date_value,expected", [(None, None), ("not-a-date", None), ("2025-02-30", None), ("2025-04-04", "2025-04-04")])
def test_unknown_dates_never_become_today(date_value, expected):
    result = fetch_article(candidate(published_date=date_value), Session([Response([wp(date="invalid")])]))
    assert result["published_date"] == expected


def test_html_declared_publication_date_takes_precedence_over_candidate():
    page = html_page(meta='<meta property="article:published_time" content="2025-07-08T09:00:00-06:00">')
    result = fetch_article(candidate(published_date="2025-01-01"), Session([Response([], status=404), Response(page, content_type="text/html")]))
    assert result["published_date"] == "2025-07-08"


@pytest.mark.parametrize("restricted", [
    {"protected": True, "rendered": CONTENT},
    {"rendered": '<div class="paywall">Suscríbete para seguir leyendo</div>' + CONTENT},
    {"rendered": '<form action="/captcha">Verify you are human</form>' + CONTENT},
])
def test_restricted_wordpress_content_has_no_html_bypass(restricted):
    session = Session([Response([wp(content=restricted)])])
    with pytest.raises(SourceFetchError, match="source_access_restricted"):
        fetch_article(candidate(), session)
    assert len(session.calls) == 1


def test_html_paywall_rejects_even_when_full_body_is_embedded():
    page = html_page(ld={"@type": "NewsArticle", "isAccessibleForFree": False, "articleBody": PARAGRAPH * 10})
    with pytest.raises(SourceFetchError, match="source_access_restricted"):
        fetch_article(candidate(), Session([Response([]), Response(page, content_type="text/html")]))


@pytest.mark.parametrize("status", [401, 402, 403, 429, 503])
def test_source_http_access_restrictions_do_not_trigger_alternate_fetch(status):
    session = Session([Response({}, status=status)])
    with pytest.raises(SourceFetchError, match="source_access_restricted"):
        fetch_article(candidate(), session)
    assert len(session.calls) == 1


def test_fails_on_short_content_or_page_without_article_container():
    with pytest.raises(SourceFetchError, match="source_text_too_short"):
        fetch_article(candidate(), Session([Response([]), Response(html_page(content="<p>Texto corto</p>"), content_type="text/html")]))
    with pytest.raises(SourceFetchError, match="source_article_container_missing"):
        fetch_article(candidate(), Session([Response([]), Response(b"<html><body>" + PARAGRAPH.encode() * 3 + b"</body></html>", content_type="text/html")]))


def test_truncation_limits_paragraph_count_text_and_hash_without_claiming_complete_text():
    huge = "<p>" + "A" * 10000 + "</p>" + "<p>" + PARAGRAPH + "</p>"
    result = fetch_article(candidate(), Session([Response([wp(content={"rendered": huge})])]))
    assert result["truncated"] is True
    assert len(result["paragraphs"][0]["text"]) == 4000
    many = ("<p>" + PARAGRAPH + "</p>") * 200
    result = fetch_article(candidate(), Session([Response([wp(content={"rendered": many})])]))
    assert result["truncated"] is True
    assert len(result["text"]) <= MAX_TEXT_CHARS
    assert len(result["paragraphs"]) <= MAX_PARAGRAPHS
    assert result["content_sha256"] == hashlib.sha256(result["text"].encode()).hexdigest()


def test_rejects_oversized_decoded_response_and_content_length():
    for response in [Response(b"x" * (MAX_BYTES + 1)), Response(b"{}", headers={"Content-Length": str(MAX_BYTES + 1)})]:
        with pytest.raises(SourceFetchError, match="source_too_large"):
            fetch_article(candidate(), Session([response]))


def test_redirects_cannot_leave_publisher_or_switch_article():
    session = Session([Response(b"", status=302, headers={"Location": "https://127.0.0.1/private"})])
    with pytest.raises(SourceFetchError, match="redirect_outside_source"):
        fetch_article(candidate(), session)
    assert len(session.calls) == 1
    session = Session([Response([]), Response(b"", status=302, headers={"Location": "https://www.diariocolatino.com/otra/"}), Response(html_page(), content_type="text/html")])
    with pytest.raises(SourceFetchError, match="source_identity_mismatch"):
        fetch_article(candidate(), session)


def test_redirect_count_is_bounded():
    session = Session([Response(b"", status=302, headers={"Location": "/loop/"}) for _ in range(4)])
    with pytest.raises(SourceFetchError, match="too_many_redirects"):
        fetch_article(candidate(), session)
    assert len(session.calls) == 4


def test_dns_must_resolve_publicly_before_network_request(monkeypatch):
    def nonpublic(host): raise ValueError("private DNS")
    monkeypatch.setattr("mdh_pipeline.article_source.require_public_dns", nonpublic)
    session = Session([])
    with pytest.raises(SourceFetchError, match="unsafe_source_dns"):
        fetch_article(candidate(), session)
    assert not session.calls


@pytest.mark.parametrize("url", ["http://www.diariocolatino.com/item/", "https://evil.example/item/", "https://www.diariocolatino.com:443/item/", "https://user:secret@www.diariocolatino.com/item/"])
def test_invalid_source_urls_rejected_before_network(url):
    session = Session([])
    with pytest.raises(SourceFetchError, match="invalid_source_url"):
        fetch_article(candidate(source_url=url), session)
    assert not session.calls


def test_mismatching_id_or_publisher_never_fetches_another_article():
    with pytest.raises(SourceFetchError, match="source_identity_mismatch"):
        fetch_article(candidate(discovery={"source_post_id": 55}), Session([Response(wp())]))
    with pytest.raises(SourceFetchError, match="source_publisher_mismatch"):
        fetch_article(candidate(publisher="Otro medio"), Session([]))
    with pytest.raises(SourceFetchError, match="invalid_source_post_id"):
        fetch_article(candidate(discovery={"source_post_id": "123/../../secret"}), Session([]))
    with pytest.raises(SourceFetchError, match="invalid_source_discovery"):
        fetch_article(candidate(discovery={"source_post_id": 123, "endpoint": "https://www.diariocolatino.com/otro-endpoint"}), Session([]))


def test_never_reuses_authenticated_sessions():
    session = Session([])
    session.headers = {"Authorization": "synthetic-fixture-token"}
    with pytest.raises(SourceFetchError, match="credentialed_source_session"):
        fetch_article(candidate(), session)
    assert not session.calls
