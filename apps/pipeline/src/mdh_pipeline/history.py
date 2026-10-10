"""Resumable bibliography of public news, independent of reviewed case publication.

Only approved publishers' WordPress metadata is read. Article bodies, people extraction,
and claims of violations are deliberately absent from this catalog. Selection is a
documented title/URL keyword screen, not editorial or jurisdictional verification.
"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import html
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlencode, urljoin, urlsplit
from zoneinfo import ZoneInfo

import requests

from .review_queue import canonical_url, normalize, require_public_dns

ROOT = Path(__file__).resolve().parents[4]
START_DATE = date(2025, 3, 26)
SCREEN_VERSION = 2
SCOPE_VERSION = 1
SOURCES = (
    {"id": "co-latino", "name": "Diario Co Latino", "endpoint": "https://www.diariocolatino.com/wp-json/wp/v2/posts", "hosts": ["www.diariocolatino.com", "diariocolatino.com"]},
    {"id": "gatoencerrado", "name": "GatoEncerrado", "endpoint": "https://gatoencerrado.news/wp-json/wp/v2/posts", "hosts": ["gatoencerrado.news"]},
    {"id": "factum", "name": "Revista Factum", "endpoint": "https://www.revistafactum.com/wp-json/wp/v2/posts", "hosts": ["www.revistafactum.com", "revistafactum.com"]},
)
TOPICS = {
    "detencion_y_debido_proceso": ("regimen de excepcion", "regimen-de-excepcion", "deten", "detencion", "captur", "habeas", "inocente", "debido proceso", "debido-proceso", "defensa publica"),
    "condiciones_de_reclusion": ("carcel", "prision", "penitenciari", "custodia", "bartolina", "tortura", "cecot", "mariona", "apanteos"),
    "libertad_de_expresion": ("periodis", "censura", "libertad de expresion", "libertad-de-expresion", "libertad de prensa", "libertad-de-prensa"),
    "defensores_y_sociedad_civil": ("defensor", "cristosal", "socorro juridico", "socorro-juridico", "udapt", "oudh", "idelca", "movir", "sindical"),
    "violencia_y_desapariciones": ("desaparecid", "desaparicion", "feminicid", "homicid", "asesin", "violencia contra", "violencia-contra", "violencia de genero", "violencia-de-genero", "ejecucion extrajudicial"),
    "violencia_sexual_y_discriminacion": ("violacion", "abuso sexual", "abuso-sexual", "acoso sexual", "acoso-sexual", "discriminacion", "lgbti", "transfobia", "homofobia", "racismo"),
    "derechos_sociales_y_ambientales": ("desaloj", "derecho al agua", "derecho-al-agua", "derechos laborales", "derechos-laborales", "despidos", "mineria", "extractiv", "contaminacion"),
    "derechos_humanos": ("derechos humanos", "derechos-humanos", "ddhh", "derechos de la ninez", "derechos-de-la-ninez", "derechos de las mujeres", "derechos-de-las-mujeres"),
}
SALVADOR_SCOPE_PATTERNS = (
    r"\bel salvador\b", r"\bsalvadoren[oa]s?\b", r"\bbukele\b", r"\bcecot\b",
    r"\bsan salvador\b", r"\bsanta tecla\b", r"\bsonsonate\b", r"\bahuachapan\b",
    r"\bchalatenango\b", r"\busulutan\b", r"\bchalchuapa\b", r"\bcojutepeque\b",
    r"\bapanteos\b", r"\bizalco\b", r"\bzacatecoluca\b", r"\bpenal de mariona\b",
    r"\bruth lopez\b", r"\bfidel zavala\b", r"\balejandro muyshondt\b",
    r"\bingrid escobar\b", r"\bmovir\b", r"\bsocorro juridico humanitario\b",
)
INTERNATIONAL_SCOPE_PATTERNS = (
    r"\bestados unidos\b", r"\bee\s?uu\b", r"\bestadounidenses?\b",
    r"\bmaduro\b", r"\btrump\b", r"\bvenezolan[oa]s?\b", r"\bcuban[oa]s?\b",
    *[rf"\b{term}\b" for term in (
        "nicaragua", "venezuela", "cuba", "colombia", "guatemala", "honduras",
        "haiti", "mexico", "argentina", "chile", "ecuador", "peru", "brasil",
        "bolivia", "uruguay", "paraguay", "panama", "costa rica", "espana",
        "francia", "alemania", "italia", "reino unido", "rusia", "ucrania",
        "gaza", "israel", "palestina", "iran", "libano", "siria", "china",
        "taiwan", "japon", "india", "pakistan", "afganistan", "sudan", "congo",
        "washington", "caracas", "la habana", "bogota", "tegucigalpa",
        "buenos aires", "madrid", "paris", "londres", "moscu", "kiev", "teheran",
        "beirut", "new york", "nueva york",
    )],
)
POLICY = {
    "record_type": "news_reference",
    "screen_version": SCREEN_VERSION,
    "scope_version": SCOPE_VERSION,
    "scope_method": "Indicio conservador de relación territorial basado en términos explícitos del título y URL. El Salvador tiene prioridad cuando aparecen ambos contextos. scope_terms conserva los fragmentos detectados normalizados (sin tildes, en minúsculas y con separadores de URL convertidos en espacios). No confirma jurisdicción. Las referencias históricas anteriores a este filtro usan el título abreviado guardado y su URL; las nuevas capturas usan el título completo antes de abreviarlo.",
    "selection_method": "Coincidencia automática de términos en título y URL; sin lectura del cuerpo.",
    "review_status": "not_reviewed",
    "notice": "Archivo bibliográfico parcial de noticias candidatas. No acredita violaciones, víctimas, jurisdicción salvadoreña ni hechos verificados. No forma parte del recuento de casos. La revisión editorial y de privacidad es un proceso separado.",
    "coverage_limitations": [
        "Tres medios configurados; no representa toda la prensa ni todas las violaciones.",
        "El filtro de títulos y URL puede omitir noticias pertinentes y seleccionar noticias de otros países o sin una violación.",
        "La fecha es la publicación declarada por el medio, no la fecha del hecho.",
        "La enumeración de un mes solo acredita que se recorrió su API pública; no la integridad del archivo del medio.",
        "Las fichas requieren revisión humana antes de incorporarse a la colección de casos.",
    ],
    "rights": "Se redistribuyen referencias bibliográficas y títulos abreviados. El contenido periodístico permanece en su fuente y conserva sus propios derechos.",
}


def scope_hint(title, url):
    """A discoverability hint, never an assertion of the incident's jurisdiction."""
    text = normalize(title + " " + unquote(urlsplit(url).path))
    text = re.sub(r"[-_/]+", " ", text).replace(".", "")
    text = " ".join(text.split())
    domestic = sorted({match.group(0) for pattern in SALVADOR_SCOPE_PATTERNS for match in re.finditer(pattern, text)})
    international = sorted({match.group(0) for pattern in INTERNATIONAL_SCOPE_PATTERNS for match in re.finditer(pattern, text)})
    return {"scope_hint": "el_salvador" if domestic else "international_context" if international else "undetermined",
            "scope_terms": sorted(set(domestic + international))}


def migrate_scope_hints(folder, sources=SOURCES):
    """Enrich old metadata only; preserve dates, collection counters and review state."""
    for source in sources:
        path = folder / (source["id"] + ".json")
        if not path.exists():
            continue
        state = read_json(path, {})
        for reference in state.get("records", []):
            if "scope_hint" not in reference or "scope_terms" not in reference:
                reference.update(scope_hint(reference["title"], reference["source_url"]))
        atomic_json(path, state)


def month_windows(start: date, end: date):
    if start > end:
        raise ValueError("Start date must not exceed end date")
    current = start.replace(day=1)
    while current <= end:
        last = current.replace(day=calendar.monthrange(current.year, current.month)[1])
        yield current.strftime("%Y-%m"), max(start, current), min(end, last)
        current = last + timedelta(days=1)


def atomic_json(path: Path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def read_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def reference_from_post(post, source, start, end, retrieved_at):
    """Return a minimal reference; never invent a date or infer a victim/violation."""
    if not isinstance(post, dict) or not isinstance(post.get("id"), int):
        raise ValueError("Malformed WordPress post")
    try:
        published = datetime.fromisoformat(post["date"]).date()
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Missing or invalid publisher date") from error
    if not start <= published <= end:
        raise ValueError("Publisher date outside requested interval")
    url = canonical_url(post.get("link", ""), set(source["hosts"]))
    # A slash variant and tracking query must not produce separate news references.
    url = url.rstrip("/") + "/"
    raw_title = post.get("title", {}).get("rendered", "")
    if not isinstance(raw_title, str):
        raise ValueError("Invalid publisher title")
    title = " ".join(html.unescape(re.sub(r"<[^>]*>", " ", raw_title)).split())
    if not title:
        raise ValueError("Missing publisher title")
    screened = normalize(title + " " + urlsplit(url).path)
    topics = [topic for topic, terms in TOPICS.items() if any(term in screened for term in terms)]
    if not topics:
        return None
    limited_title = " ".join(title.split()[:24])[:180]
    return {
        "id": hashlib.sha256(url.encode()).hexdigest(),
        "title": limited_title,
        "title_truncated": limited_title != title,
        "publisher": source["name"],
        "source_id": source["id"],
        "source_url": url,
        "published_date": published.isoformat(),
        "review_status": "not_reviewed",
        "jurisdiction_status": "not_reviewed",
        "record_type": "news_reference",
        "topics": topics,
        **scope_hint(title, url),
        "discovery": {"method": "wordpress_rest_title_screen", "endpoint": source["endpoint"], "source_post_id": post["id"], "retrieved_at": retrieved_at},
    }


def page_url(source, start, end, page):
    # WP after/before are exclusive; include the complete first and final day.
    after = datetime.combine(start, datetime.min.time()) - timedelta(seconds=1)
    before = datetime.combine(end + timedelta(days=1), datetime.min.time())
    return source["endpoint"] + "?" + urlencode({
        "after": after.isoformat(), "before": before.isoformat(), "orderby": "date",
        "order": "asc", "per_page": 100, "page": page,
        "_fields": "id,date,link,title",
    })


def fetch_page(source, start, end, page, session=None):
    """Bound network work, bytes and redirect destinations; no arbitrary URLs."""
    session = session or requests.Session()
    allowed = set(source["hosts"])
    original = page_url(source, start, end, page)
    for attempt in range(3):
        url = original
        try:
            for _ in range(4):
                parts = urlsplit(url)
                if parts.scheme != "https" or parts.hostname not in allowed or parts.username or parts.password or parts.port:
                    raise ValueError("Archive request outside approved HTTPS hosts")
                require_public_dns(parts.hostname)
                with session.get(url, timeout=(8, 30), stream=True, allow_redirects=False,
                        headers={"User-Agent": "MDH-SV/2.2 bibliography (+https://github.com/RauPro/MDH-SV)", "Accept": "application/json"}) as response:
                    if response.is_redirect:
                        url = urljoin(url, response.headers["Location"])
                        continue
                    response.raise_for_status()
                    content = bytearray()
                    for chunk in response.iter_content(65536):
                        content.extend(chunk)
                        if len(content) > 1_000_000:
                            raise ValueError("Archive page exceeds metadata size limit")
                    posts = json.loads(content)
                    if not isinstance(posts, list) or len(posts) > 100:
                        raise ValueError("Malformed WordPress page")
                    total_pages = int(response.headers["X-WP-TotalPages"])
                    total_posts = int(response.headers["X-WP-Total"])
                    if total_pages < 0 or total_posts < 0 or total_pages > 100_000:
                        raise ValueError("Invalid WordPress pagination")
                    return posts, total_pages, total_posts
            raise ValueError("Too many archive redirects")
        except (requests.RequestException, ValueError, KeyError):
            if attempt == 2:
                raise
            time.sleep(1 + attempt)
    raise RuntimeError("Archive request failed")


def collect_source(source, start, end, folder, *, max_pages=300, refresh=False,
                   fetch=fetch_page, delay=0.3, now=None):
    """Checkpoint each page; a failed month does not erase prior references."""
    now = now or datetime.now(timezone.utc).isoformat()
    state_path = folder / (source["id"] + ".json")
    state = read_json(state_path, {"schema_version": 1, "source_id": source["id"], "months": {}, "records": []})
    references = {item["id"]: item for item in state["records"]}
    used_pages = 0
    for month, first, last in month_windows(start, end):
        progress = state["months"].get(month, {})
        same_window = (progress.get("start_date") == first.isoformat() and progress.get("end_date") == last.isoformat()
                       and progress.get("screen_version") == SCREEN_VERSION)
        # Re-enumerate the two latest calendar months to catch additions/corrections.
        recent = (end.year * 12 + end.month) - (first.year * 12 + first.month) < 2
        if progress.get("status") == "enumerated" and same_window and not refresh and not recent:
            continue
        if not same_window or (progress.get("status") == "enumerated" and (refresh or recent)):
            progress = {}
        progress.update(start_date=first.isoformat(), end_date=last.isoformat(), status="partial", last_attempt_at=now, screen_version=SCREEN_VERSION)
        progress.setdefault("next_page", 1)
        progress.setdefault("pages_fetched", 0)
        progress.setdefault("posts_scanned", 0)
        progress.setdefault("invalid_posts", 0)
        state["months"][month] = progress
        while used_pages < max_pages:
            try:
                posts, total_pages, total_posts = fetch(source, first, last, progress["next_page"])
                progress.update(source_total_posts=total_posts, source_total_pages=total_pages)
                for post in posts:
                    try:
                        reference = reference_from_post(post, source, first, last, now)
                    except (ValueError, AttributeError, TypeError):
                        progress["invalid_posts"] += 1
                        continue
                    if reference:
                        previous = references.get(reference["id"])
                        if previous:
                            reference["discovery"]["retrieved_at"] = previous["discovery"]["retrieved_at"]
                        references[reference["id"]] = reference
                progress["posts_scanned"] += len(posts)
                progress["pages_fetched"] += 1
                used_pages += 1
                progress["error"] = None
                if progress["next_page"] >= total_pages:
                    progress.update(status="enumerated", completed_at=now)
                elif not posts:
                    raise ValueError("Unexpected empty page before archive end")
                progress["next_page"] += 1
            except (requests.RequestException, ValueError, KeyError, OSError) as error:
                progress.update(status="failed", error=type(error).__name__)
                # Log only a class name, never source response contents or credentials.
                print(json.dumps({"source": source["id"], "month": month, "error": type(error).__name__}), flush=True)
            state["records"] = sorted(references.values(), key=lambda item: (item["published_date"], item["id"]), reverse=True)
            atomic_json(state_path, state)
            if progress["status"] in ("enumerated", "failed"):
                break
            if delay:
                time.sleep(delay)
        if used_pages >= max_pages:
            break
        if delay:
            time.sleep(delay)
    state["records"] = sorted(references.values(), key=lambda item: (item["published_date"], item["id"]), reverse=True)
    atomic_json(state_path, state)
    return {"source": source["id"], "requests": used_pages, "references": len(references), "enumerated_months": sum(m.get("status") == "enumerated" for m in state["months"].values())}


def export_archive(folder, output, start, end, *, sources=SOURCES, generated_at=None):
    generated_at = generated_at or datetime.now(timezone.utc).isoformat()
    records, source_coverage, month_coverage = {}, [], {}
    for month, first, last in month_windows(start, end):
        month_coverage[month] = {"month": month, "start_date": first.isoformat(), "end_date": last.isoformat(), "records": 0, "sources": []}
    for source in sources:
        state = read_json(folder / (source["id"] + ".json"), {"records": [], "months": {}})
        selected = [r for r in state["records"] if start.isoformat() <= r["published_date"] <= end.isoformat()]
        for reference in selected:
            # Validate the public boundary even when processing a checkpoint modified on disk.
            validate_reference(reference, source)
            records[reference["id"]] = reference
            month_coverage[reference["published_date"][:7]]["records"] += 1
        months = []
        for month, first, last in month_windows(start, end):
            saved = state["months"].get(month, {})
            status = saved.get("status", "not_started")
            if (saved.get("start_date") != first.isoformat() or saved.get("end_date") != last.isoformat()
                    or saved.get("screen_version") != SCREEN_VERSION):
                status = "partial" if saved else "not_started"
            count = sum(r["published_date"].startswith(month) for r in selected)
            row = {"month": month, "status": status, "records": count,
                   "screen_version": saved.get("screen_version"),
                   "posts_scanned": saved.get("posts_scanned", 0), "source_total_posts": saved.get("source_total_posts"),
                   "pages_fetched": saved.get("pages_fetched", 0), "invalid_posts": saved.get("invalid_posts", 0),
                   "last_attempt_at": saved.get("last_attempt_at"), "error": saved.get("error")}
            months.append(row)
            month_coverage[month]["sources"].append({"source_id": source["id"], "status": status, "records": count})
        dates = sorted(r["published_date"] for r in selected)
        source_coverage.append({"id": source["id"], "publisher": source["name"], "endpoint": source["endpoint"],
            "status": "enumerated" if all(m["status"] == "enumerated" for m in months) else "partial",
            "records": len(selected), "first_published_date": dates[0] if dates else None,
            "last_published_date": dates[-1] if dates else None, "months": months})
    document = {"schema_version": 1, "generated_at": generated_at, "policy": POLICY,
        "paper": read_json(folder / "paper.json", {}),
        "coverage": {"start_date": start.isoformat(), "end_date": end.isoformat(), "timezone": "America/El_Salvador",
            "status": "partial", "records": len(records), "sources": source_coverage, "months": list(month_coverage.values())},
        "records": sorted(records.values(), key=lambda r: (r["published_date"], r["id"]), reverse=True)}
    atomic_json(output, document)
    return document


def validate_reference(reference, source):
    required = {"id", "title", "title_truncated", "publisher", "source_id", "source_url", "published_date", "review_status", "jurisdiction_status", "record_type", "topics", "scope_hint", "scope_terms", "discovery"}
    if set(reference) != required:
        raise ValueError("Unexpected fields in public bibliography")
    url = canonical_url(reference["source_url"], set(source["hosts"]))
    if reference["id"] != hashlib.sha256(url.encode()).hexdigest():
        raise ValueError("Bibliography identity mismatch")
    if reference["review_status"] != "not_reviewed" or reference["jurisdiction_status"] != "not_reviewed" or reference["record_type"] != "news_reference":
        raise ValueError("Bibliography cannot publish reviewed cases")
    if reference["publisher"] != source["name"] or reference["source_id"] != source["id"]:
        raise ValueError("Bibliography publisher mismatch")
    if not isinstance(reference["title"], str) or not reference["title"] or len(reference["title"]) > 180 or len(reference["title"].split()) > 24:
        raise ValueError("Bibliographic title exceeds quotation limit")
    date.fromisoformat(reference["published_date"])
    if not isinstance(reference["topics"], list) or not reference["topics"] or any(t not in TOPICS for t in reference["topics"]):
        raise ValueError("Invalid bibliography topic")
    if reference["scope_hint"] not in ("el_salvador", "international_context", "undetermined"):
        raise ValueError("Invalid geographic scope hint")
    terms = reference["scope_terms"]
    if not isinstance(terms, list) or any(not isinstance(t, str) or not t or len(t) > 80 for t in terms):
        raise ValueError("Invalid geographic scope terms")
    if (reference["scope_hint"] == "undetermined") != (len(terms) == 0):
        raise ValueError("Scope hint must retain the terms supporting it")
    discovery = reference["discovery"]
    if set(discovery) != {"method", "endpoint", "source_post_id", "retrieved_at"} or discovery["method"] != "wordpress_rest_title_screen" or discovery["endpoint"] != source["endpoint"]:
        raise ValueError("Invalid discovery metadata")
    if not isinstance(discovery["source_post_id"], int) or discovery["source_post_id"] < 1:
        raise ValueError("Invalid source post identifier")
    datetime.fromisoformat(discovery["retrieved_at"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=START_DATE)
    parser.add_argument("--end", type=date.fromisoformat, default=datetime.now(ZoneInfo("America/El_Salvador")).date())
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data/history")
    parser.add_argument("--output", type=Path, default=ROOT / "apps/web/public/archive/news.json")
    parser.add_argument("--max-pages", type=int, default=300, help="Maximum successful pages per source per run; checkpoints resume next run")
    parser.add_argument("--refresh", action="store_true", help="Re-enumerate completed historical months")
    parser.add_argument("--export-only", action="store_true", help="Validate and export existing metadata; no network")
    args = parser.parse_args()
    if args.start > args.end or not 1 <= args.max_pages <= 1000:
        parser.error("Invalid date range or page budget")
    migrate_scope_hints(args.data_dir)
    results = []
    if not args.export_only:
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(collect_source, source, args.start, args.end, args.data_dir,
                max_pages=args.max_pages, refresh=args.refresh) for source in SOURCES]
            results = [future.result() for future in futures]
    document = export_archive(args.data_dir, args.output, args.start, args.end)
    report = {"records": len(document["records"]), "start_date": args.start.isoformat(), "end_date": args.end.isoformat(),
        "coverage": "partial", "collection": results, "reviewed_cases_published": 0}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
            summary.write("## Archivo bibliográfico MDH-SV\n\n```json\n" + json.dumps(report, indent=2) + "\n```\n")
    if not args.export_only and not any(result["requests"] for result in results):
        raise SystemExit("No successful archive page was read; retained previous references")


if __name__ == "__main__":
    main()
