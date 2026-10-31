"""Pure reference contracts, extracted without changing validation or normalization."""
from __future__ import annotations
import hashlib
import re
from datetime import date, datetime
from urllib.parse import urlsplit
from ..source_urls import canonical_url

TOPICS = {
    "detencion_y_debido_proceso": ("regimen de excepcion", "regimen-de-excepcion", "deten", "detencion", "captur", "habeas", "inocente", "debido proceso", "debido-proceso", "defensa publica"),
    "condiciones_de_reclusion": ("carcel", "prision", "penitenciari", "custodia", "bartolina", "tortura", "cecot", "mariona", "apanteos"),
    "libertad_de_expresion": ("periodis", "censura", "libertad de expresion", "libertad-de-expresion", "libertad de prensa", "libertad-de-prensa"),
    "defensores_y_sociedad_civil": ("defensor", "cristosal", "socorro juridico", "socorro-juridico", "udapt", "oudh", "idelca", "movir", "sindical"),
    "violencia_y_desapariciones": ("desaparecid", "desaparicion", "feminicid", "homicid", "asesin", "violencia contra", "violencia-contra", "violencia de genero", "violencia-de-genero", "ejecucion extrajudicial", "ejecuciones extrajudiciales", "ejecuciones-extrajudiciales"),
    "violencia_sexual_y_discriminacion": ("violacion", "abuso sexual", "abuso-sexual", "acoso sexual", "acoso-sexual", "discriminacion", "lgbti", "transfobia", "homofobia", "racismo"),
    "derechos_sociales_y_ambientales": ("desaloj", "derecho al agua", "derecho-al-agua", "derechos laborales", "derechos-laborales", "despidos", "mineria", "extractiv", "contaminacion"),
    "derechos_humanos": ("derechos humanos", "derechos-humanos", "ddhh", "derechos de la ninez", "derechos-de-la-ninez", "derechos de las mujeres", "derechos-de-las-mujeres"),
}

def validate_reference(reference, source):
    required = {"id", "title", "title_truncated", "publisher", "source_id", "source_url", "published_date", "review_status", "jurisdiction_status", "record_type", "topics", "scope_hint", "scope_terms", "discovery"}
    if "selection" in reference:
        from .selection import validate_selection
        validate_selection(reference["selection"])
        required.add("selection")
    if set(reference) != required:
        raise ValueError("Unexpected fields in public bibliography")
    url = canonical_url(reference["source_url"], set(source["hosts"]))
    if (reference["source_url"] != url or len(urlsplit(url).path) < 5
            or reference["id"] != hashlib.sha256(url.encode()).hexdigest()):
        raise ValueError("Bibliography identity mismatch")
    if reference["review_status"] != "not_reviewed" or reference["jurisdiction_status"] != "not_reviewed" or reference["record_type"] != "news_reference":
        raise ValueError("Bibliography cannot publish reviewed cases")
    if reference["publisher"] != source["name"] or reference["source_id"] != source["id"]:
        raise ValueError("Bibliography publisher mismatch")
    if (not isinstance(reference["title"], str) or not reference["title"].strip()
            or len(reference["title"]) > 180 or len(reference["title"].split()) > 24
            or type(reference["title_truncated"]) is not bool):
        raise ValueError("Bibliographic title exceeds quotation limit")
    if (not isinstance(reference["published_date"], str)
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", reference["published_date"])):
        raise ValueError("Bibliography requires an explicit calendar date")
    date.fromisoformat(reference["published_date"])
    if not isinstance(reference["topics"], list) or not reference["topics"] or any(t not in TOPICS for t in reference["topics"]):
        raise ValueError("Invalid bibliography topic")
    if reference["scope_hint"] not in ("el_salvador", "international_context", "undetermined"):
        raise ValueError("Invalid geographic scope hint")
    terms = reference["scope_terms"]
    if not isinstance(terms, list) or any(not isinstance(t, str) or not t.strip() or len(t) > 80 for t in terms):
        raise ValueError("Invalid geographic scope terms")
    if (reference["scope_hint"] == "undetermined") != (len(terms) == 0):
        raise ValueError("Scope hint must retain the terms supporting it")
    discovery = reference["discovery"]
    wordpress = source.get("kind", "wordpress") == "wordpress"
    expected_method = "wordpress_rest_title_screen" if wordpress else "html_article_metadata"
    if set(discovery) != {"method", "endpoint", "source_post_id", "retrieved_at"} or discovery["method"] != expected_method or discovery["endpoint"] != source["endpoint"]:
        raise ValueError("Invalid discovery metadata")
    post_id = discovery["source_post_id"]
    if (wordpress or post_id is not None) and (
            type(post_id) not in {int, float} or not 1 <= post_id <= 9_007_199_254_740_991
            or int(post_id) != post_id):
        raise ValueError("Invalid source post identifier")
    if (not isinstance(discovery["retrieved_at"], str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)",
            discovery["retrieved_at"])):
        raise ValueError("Discovery requires an explicit time zone")
    datetime.fromisoformat(discovery["retrieved_at"])
