"""Observe official aggregate sources and prepare private, unapproved revisions.

This module never publishes, commits or rewrites the canonical baseline. Dynamic
HTML bytes alone do not create a revision: FGR is compared by validated values.
PDF/XLSX changes and new catalogue documents always require manual extraction.
"""
from __future__ import annotations

import argparse
import calendar
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from datetime import date, datetime, timezone
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from .review_queue import normalize, require_public_dns

FGR_URL = "https://www.fiscalia.gob.sv/estadisticas-de-homicidios/"
CATALOG_URL = "https://transparencia.oj.gob.sv/es/documentos/248"
CATALOG_ID = "iml-homicides"
PNC_URL = "https://transparencia.pnc.gob.sv/reportes"
PNC_ID = "pnc-reports"
MEMORIAS_ID = "fgr-memorias"
MEMORIAS_URL = "https://www.fiscalia.gob.sv/memoria-de-labores/"
ALLOWED_HOSTS = frozenset({"www.fiscalia.gob.sv", "fiscalia.gob.sv",
                           "transparencia.oj.gob.sv", "data.unodc.org", "transparencia.pnc.gob.sv"})
MAX_BYTES = {"html": 2_000_000, "pdf": 25_000_000, "xlsx": 40_000_000}
MONTHS = ("ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO",
          "SEP", "OCT", "NOV", "DIC")
FGR_LABELS = {"Total de Homicidios": "homicides",
              "Total Homicidios Intencionales": "intentional_homicides"}
CATEGORIES = {"Delincuencia General", "Intolerancia Social", "Intolerancia Familiar"}
USER_AGENT = "MDH-SV/2.4 official-statistics-review (+https://github.com/RauPro/MDH-SV)"


class RefreshError(ValueError):
    """Stable error codes, never raw upstream HTML or request credentials."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def stable_id(value: object) -> str:
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode())


def clean(text: str) -> str:
    return " ".join(text.split())


def source_url(value: str, hosts=ALLOWED_HOSTS) -> str:
    try:
        if not isinstance(value, str) or len(value) > 4096 or any(
                ord(c) < 32 or c in "<>\\" for c in value):
            raise ValueError()
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or parsed.hostname not in hosts or parsed.username
                or parsed.password or parsed.port or parsed.query or len(parsed.path) < 2):
            raise ValueError()
        path = quote(unquote(parsed.path), safe="/")
        if parsed.hostname == "transparencia.oj.gob.sv" and path.startswith("/descargar/"):
            path = "/es" + path
        return urlunsplit(("https", parsed.hostname, path, "", ""))
    except (ValueError, TypeError) as exc:
        raise RefreshError("source_url_not_allowed") from exc


def fetch_source(url: str, kind: str, session) -> bytes:
    """Bound decoded bytes and validate every redirect before sending a request."""
    if kind not in MAX_BYTES:
        raise RefreshError("source_format_not_supported")
    original_host = urlsplit(source_url(url)).hostname
    allowed = ({"www.fiscalia.gob.sv", "fiscalia.gob.sv"}
               if original_host in {"www.fiscalia.gob.sv", "fiscalia.gob.sv"}
               else {original_host})
    maximum = MAX_BYTES[kind]
    for _ in range(4):
        url = source_url(url, allowed)
        try:
            require_public_dns(urlsplit(url).hostname)
        except (ValueError, OSError) as exc:
            raise RefreshError("source_dns_not_public") from exc
        try:
            with session.get(url, stream=True, allow_redirects=False, timeout=(8, 30),
                             headers={"User-Agent": USER_AGENT, "Authorization": None,
                                      "Cookie": None}) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location")
                    if not location:
                        raise RefreshError("source_redirect_missing")
                    url = urljoin(url, location)
                    continue
                if response.status_code != 200:
                    raise RefreshError(f"source_http_{response.status_code}")
                try:
                    length = int(response.headers.get("Content-Length", "0"))
                except ValueError as exc:
                    raise RefreshError("source_invalid_content_length") from exc
                if length < 0 or length > maximum:
                    raise RefreshError("source_too_large")
                raw = bytearray()
                for chunk in response.iter_content(65536):
                    raw.extend(chunk)
                    if len(raw) > maximum:
                        raise RefreshError("source_too_large")
                content = bytes(raw)
                if not content:
                    raise RefreshError("source_empty")
                if kind == "pdf" and not content.startswith(b"%PDF-"):
                    raise RefreshError("source_not_pdf")
                if kind == "xlsx" and not content.startswith(b"PK\x03\x04"):
                    raise RefreshError("source_not_xlsx")
                if kind == "html" and b"<" not in content[:1000]:
                    raise RefreshError("source_not_html")
                return content
        except requests.RequestException as exc:
            raise RefreshError("source_network_error") from exc
    raise RefreshError("source_redirect_limit")


def _integer(text: str, *, blank: bool = False):
    text = clean(text)
    if not text and blank:
        return None
    # No decimal coercion, thousands inference, footnotes, minus signs or dashes.
    if not re.fullmatch(r"(?:0|[1-9][0-9]{0,8})", text):
        raise RefreshError("fgr_invalid_integer")
    return int(text)


def _table_year(table) -> int:
    # FGR uses a <p><strong>ESTADÍSTICAS AÑO YYYY</strong>.</p> before each
    # figure. Never skip across another table to borrow its year.
    for node in table.previous_elements:
        if getattr(node, "name", None) == "table":
            break
        if getattr(node, "name", None) in {"p", "h2", "h3", "h4", "caption"}:
            label = normalize(clean(node.get_text(" ", strip=True)))
            match = re.fullmatch(r"estadisticas ano (20\d{2})\s*\.?", label)
            if match:
                return int(match.group(1))
    raise RefreshError("fgr_year_label_missing")


def parse_fgr(raw: bytes, as_of: date) -> dict[int, dict]:
    soup = BeautifulSoup(raw, "html.parser")
    if "equipo operativo de homicidios" not in normalize(soup.get_text(" ", strip=True)):
        raise RefreshError("fgr_attribution_missing")
    result = {}
    tables = soup.find_all("table")
    if not tables or len(tables) > 60:
        raise RefreshError("fgr_tables_missing")
    for table in tables:
        rows = []
        for tr in table.find_all("tr"):
            cells = tr.find_all(["th", "td"], recursive=False)
            if any(c.get("colspan", "1") != "1" or c.get("rowspan", "1") != "1"
                   for c in cells):
                raise RefreshError("fgr_merged_cells")
            rows.append([clean(cell.get_text(" ", strip=True)) for cell in cells])
        if not rows or len(rows[0]) != 14:
            raise RefreshError("fgr_columns_changed")
        header = [re.sub(r"\s|\.", "", cell).upper() for cell in rows[0]]
        header = ["SEP" if cell == "SEPT" else cell for cell in header]
        if header != ["", *MONTHS, "TOTAL"]:
            raise RefreshError("fgr_month_labels_changed")
        year = _table_year(table)
        if year < 2022 or year > as_of.year:
            raise RefreshError("fgr_year_outside_cutoff")
        if year in result:
            raise RefreshError("fgr_duplicate_year")
        if len(rows) != 5 or any(len(row) != 14 for row in rows):
            raise RefreshError("fgr_rows_changed")
        total_rows = [row for row in rows[1:] if row[0] in FGR_LABELS]
        if len(total_rows) != 1:
            raise RefreshError("fgr_total_label_changed")
        row = total_rows[0]
        expected_label = "Total de Homicidios" if year == 2022 else "Total Homicidios Intencionales"
        if row[0] != expected_label:
            raise RefreshError("fgr_indicator_changed")
        categories = [r for r in rows[1:] if r is not row]
        if {r[0] for r in categories} != CATEGORIES or len(categories) != 3:
            raise RefreshError("fgr_categories_changed")
        values = [_integer(cell, blank=True) for cell in row[1:13]]
        total = _integer(row[-1], blank=True)
        available = [value for value in values if value is not None]
        if total is None:
            if available:
                raise RefreshError("fgr_total_missing")
        elif not available or sum(available) != total:
            raise RefreshError("fgr_total_mismatch")
        for month, value in enumerate(values, 1):
            if year == as_of.year and month > as_of.month and value is not None:
                raise RefreshError("fgr_future_observation")
        if available:
            last = max(i for i, value in enumerate(values) if value is not None)
            if any(value is None for value in values[:last + 1]):
                raise RefreshError("fgr_noncontiguous_months")
        parsed_categories = [[_integer(c, blank=True) for c in r[1:]] for r in categories]
        for category in parsed_categories:
            known = [n for n in category[:12] if n is not None]
            if (category[-1] is None and known) or (category[-1] is not None
                    and (not known or sum(known) != category[-1])):
                raise RefreshError("fgr_category_total_mismatch")
        for i, value in enumerate([*values, total]):
            parts = [category[i] for category in parsed_categories]
            if value is None:
                if any(part is not None for part in parts):
                    raise RefreshError("fgr_category_missing_total")
            elif any(part is None for part in parts) or sum(parts) != value:
                raise RefreshError("fgr_category_sum_mismatch")
        result[year] = {"year": year, "indicator": FGR_LABELS[row[0]],
                        "unit": "source_reported_homicides", "label": row[0],
                        "months": values, "total": total}
    return result


def catalog_documents(raw: bytes) -> list[str]:
    soup = BeautifulSoup(raw, "html.parser")
    urls = set()
    for link in soup.find_all("a", href=True):
        href = link["href"]
        document_path = unquote(urlsplit(href).path).rsplit("/", 1)[0]
        label = normalize(clean(link.get_text(" ", strip=True)) + " " + str(link.get("download", "")) + " " + document_path)
        if "homicid" not in label or not re.search(r"\b20(?:1\d|[2-9]\d)\b", label):
            continue
        url = source_url(urljoin(CATALOG_URL, href), {"transparencia.oj.gob.sv"})
        if not urlsplit(url).path.startswith("/es/descargar/"):
            continue
        urls.add(url)
    if not urls:
        raise RefreshError("catalog_documents_missing")
    if len(urls) > 1000:
        raise RefreshError("catalog_too_many_documents")
    return sorted(urls)


def _observation(value, locator):
    return {"value": value, "status": "available" if value is not None else "not_available",
            "locator": locator if value is not None else None}


def _period_from_fgr(row: dict, previous: dict, as_of: date) -> dict:
    period = copy.deepcopy(previous)
    year = row["year"]
    months = []
    for month, value in enumerate(row["months"], 1):
        observation = _observation(value, f"HTML, tabla del año {year}, fila «{row['label']}», columna del mes {month}.")
        if value is None and year == as_of.year and month > as_of.month:
            observation["status"] = "outside_cutoff"
        months.append({"month": month, **observation})
    known = [m["month"] for m in months if m["value"] is not None]
    end_month = max(known) if known else (as_of.month if year == as_of.year else 12)
    end = min(date(year, end_month, calendar.monthrange(year, end_month)[1]), as_of)
    period.update(year=year, start_date=f"{year}-01-01", end_date=end.isoformat(),
                  completeness=("unavailable" if not known else "complete" if len(known) == 12
                                and end.month == 12 and end.day == 31 else "partial"),
                  indicator=row["indicator"], unit=row["unit"], months=months,
                  national=_observation(row["total"], f"HTML, tabla del año {year}, fila «{row['label']}», columna TOTAL."))
    period["notes"] = [note for note in period["notes"] if not note.startswith("Cobertura numérica:")]
    period["notes"].append(f"Cobertura numérica: {len(known)} meses publicados para {year}, hasta {end.isoformat()} "
                           f"en la consulta del {as_of.isoformat()}. Las celdas vacías no se convierten en cero; "
                           "la fecha de consulta no certifica una fecha de cierre de la fuente.")
    return period


def _semantic(period: dict) -> dict:
    return {"year": period["year"], "indicator": period["indicator"], "unit": period["unit"],
            "total": period["national"]["value"],
            "months": [row["value"] for row in sorted(period["months"], key=lambda row: row["month"])]}


def propose_fgr(baseline: dict, rows: dict[int, dict], as_of: date, checked_at: str,
                raw_sha: str) -> tuple[dict | None, list[dict]]:
    sources = [s for s in baseline["sources"] if source_url(s["url"]) == source_url(FGR_URL)]
    if len(sources) != 1:
        raise RefreshError("baseline_fgr_source_missing")
    source = sources[0]
    periods = [p for p in baseline["periods"] if p["source_id"] == source["id"]]
    if not periods or any(p["months"] is None or p["unit"] != "source_reported_homicides"
                          or any(d["value"] is not None for d in p["departments"]) for p in periods):
        raise RefreshError("baseline_fgr_contract_changed")
    if not {p["year"] for p in periods}.issubset(rows):
        raise RefreshError("fgr_existing_year_removed")
    updated = copy.deepcopy(baseline)
    by_year = {p["year"]: p for p in updated["periods"]}
    changes = []
    for year, row in sorted(rows.items()):
        previous = by_year.get(year)
        if previous and previous["source_id"] != source["id"]:
            raise RefreshError("fgr_year_source_conflict")
        before = _semantic(previous) if previous else None
        after = {key: row[key] for key in ("year", "indicator", "unit", "total", "months")}
        if before:
            if before["indicator"] != after["indicator"] or before["unit"] != after["unit"]:
                raise RefreshError("fgr_indicator_changed")
            if any(old is not None and new is None for old, new in zip(before["months"], after["months"])):
                raise RefreshError("fgr_existing_observation_removed")
        if before == after:
            continue
        if previous is None:
            previous = copy.deepcopy(max(periods, key=lambda period: period["year"]))
            previous["notes"] = [n for n in previous["notes"] if not n.startswith("Cobertura numérica:")]
        by_year[year] = _period_from_fgr(row, previous, as_of)
        details = {"year": year, "before": before, "after": after}
        changes.append({"id": stable_id(["fgr_values", source["id"], details]),
                        "kind": "fgr_values", "source_id": source["id"], "source_url": source["url"],
                        "content_sha256": raw_sha, "details": details})
    if not changes:
        return None, []
    # New calendar years absent from a source stay explicitly unavailable.
    for year in range(2010, as_of.year + 1):
        if year not in by_year:
            empty = {"year": year, "indicator": "intentional_homicides", "unit": "source_reported_homicides",
                     "label": "Total Homicidios Intencionales", "months": [None] * 12, "total": None}
            template = copy.deepcopy(max(periods, key=lambda period: period["year"]))
            template["notes"] = ["No se ha publicado una tabla anual de este período en la fuente consultada."]
            by_year[year] = _period_from_fgr(empty, template, as_of)
    updated["periods"] = [by_year[y] for y in sorted(by_year)]
    # Keep missing/future markers consistent with the new cutoff, preserving
    # existing locators, counts and reviewed explanatory notes on unchanged years.
    for period in updated["periods"]:
        if period["months"] is not None:
            for month in period["months"]:
                if month["value"] is None:
                    month["status"] = ("outside_cutoff" if period["year"] == as_of.year
                                       and month["month"] > as_of.month else "not_available")
    updated["comparability_note"] = updated["comparability_note"].replace(
        "2026 sólo llega a agosto.", "Consulte el período y los meses disponibles de cada año.")
    updated["as_of"] = as_of.isoformat()
    updated["generated_at"] = checked_at
    changed_source = next(s for s in updated["sources"] if s["id"] == source["id"])
    changed_source.update(content_sha256=raw_sha, retrieved_at=checked_at,
                          title=f"Estadísticas de homicidios: tablas anuales {min(rows)}–{max(rows)}",
                          edition_note=f"Tabla FGR consultada el {as_of.isoformat()}; "
                          "se conservan celdas vacías y diferencias entre períodos. La fuente no publica "
                          "en esta tabla un desglose departamental ni una fecha explícita de actualización.")
    return updated, changes


def validate_state(value: dict) -> dict:
    if (not isinstance(value, dict) or set(value) != {"schema_version", "catalogs"}
            or value["schema_version"] != 1 or not isinstance(value["catalogs"], dict)
            or set(value["catalogs"]) - {CATALOG_ID, PNC_ID, MEMORIAS_ID}):
        raise RefreshError("invalid_previous_state")
    for catalog_id, entries in value["catalogs"].items():
        if (not isinstance(entries, list) or len(entries) > 1000
                or any(not isinstance(u, str) for u in entries) or len(entries) != len(set(entries))):
            raise RefreshError("invalid_previous_state")
        hosts = ({"transparencia.oj.gob.sv"} if catalog_id == CATALOG_ID
                 else {"transparencia.pnc.gob.sv"} if catalog_id == PNC_ID
                 else {"www.fiscalia.gob.sv", "fiscalia.gob.sv"})
        for entry in entries:
            if source_url(entry, hosts) != entry:
                raise RefreshError("invalid_previous_state")
            if catalog_id == CATALOG_ID and not urlsplit(entry).path.startswith("/es/descargar/"):
                raise RefreshError("invalid_previous_state")
    return copy.deepcopy(value)


def refresh(baseline: dict, baseline_sha256: str, *, session=None, as_of: date | None = None,
            checked_at: str | None = None, previous_state: dict | None = None):
    checked_at = checked_at or datetime.now(timezone.utc).isoformat()
    instant = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    as_of = as_of or instant.astimezone(ZoneInfo("America/El_Salvador")).date()
    if instant.tzinfo is None or as_of > instant.astimezone(ZoneInfo("America/El_Salvador")).date():
        raise RefreshError("invalid_check_date")
    if (not isinstance(baseline, dict) or baseline.get("schema_version") != 1
            or baseline.get("dataset_id") != "sv-official-homicides"
            or not isinstance(baseline.get("sources"), list) or not 1 <= len(baseline["sources"]) <= 256
            or not isinstance(baseline.get("periods"), list) or baseline.get("as_of", "9999") > as_of.isoformat()):
        raise RefreshError("invalid_baseline")
    state = validate_state(previous_state) if previous_state is not None else {"schema_version": 1, "catalogs": {}}
    report = {"schema_version": 1, "checked_at": checked_at, "as_of": as_of.isoformat(),
              "baseline_sha256": baseline_sha256, "status": "unchanged", "review_required": False,
              "proposal_file": None, "proposal_sha256": None, "sources": [], "changes": [], "errors": []}
    candidate = None
    own_session = session is None
    session = session or requests.Session()
    if own_session:
        session.trust_env = False
    try:
        for source in baseline["sources"]:
            item = {"source_id": source["id"], "url": source["url"], "format": source["format"],
                    "status": "unchanged", "previous_sha256": source["content_sha256"],
                    "content_sha256": None, "error_code": None, "manual_review": False}
            try:
                raw = fetch_source(source["url"], source["format"], session)
                sha = digest(raw)
                item["content_sha256"] = sha
                if source_url(source["url"]) == source_url(FGR_URL):
                    candidate, changes = propose_fgr(baseline, parse_fgr(raw, as_of), as_of, checked_at, sha)
                    if changes:
                        item["status"] = "changed"
                        report["changes"].extend(changes)
                elif source["format"] in {"pdf", "xlsx"}:
                    if sha != source["content_sha256"]:
                        item.update(status="changed", manual_review=True)
                        details = {"previous_sha256": source["content_sha256"], "action": "manual_document_review"}
                        report["changes"].append({"id": stable_id(["source_revision", source["id"], source["content_sha256"], sha]),
                                                  "kind": "source_revision", "source_id": source["id"],
                                                  "source_url": source["url"], "content_sha256": sha, "details": details})
                else:
                    raise RefreshError("source_html_parser_missing")
            except RefreshError as exc:
                item.update(status="error", error_code=exc.code, manual_review=True)
                report["errors"].append({"source_id": source["id"], "code": exc.code})
            report["sources"].append(item)
        catalog = {"source_id": CATALOG_ID, "url": CATALOG_URL, "format": "html", "status": "unchanged",
                   "previous_sha256": None, "content_sha256": None, "error_code": None, "manual_review": False}
        try:
            raw = fetch_source(CATALOG_URL, "html", session)
            inventory = catalog_documents(raw)
            catalog["content_sha256"] = digest(raw)
            before = state["catalogs"].get(CATALOG_ID)
            if before is None:
                known = {source_url(s["url"]) for s in baseline["sources"]}
                added = [u for u in inventory if u not in known and re.search(rf"\b{as_of.year}\b", unquote(urlsplit(u).path).rsplit("/", 1)[0])][:20]
                removed = []
            else:
                added = sorted(set(inventory) - set(before))
                removed = sorted(set(before) - set(inventory))
            for action, entries in (("added", added), ("removed", removed)):
                for url in entries:
                    details = {"action": action, "document_url": url, "requires_manual_extraction": True}
                    report["changes"].append({"id": stable_id(["catalog_document", action, url]),
                                              "kind": "catalog_document", "source_id": CATALOG_ID,
                                              "source_url": CATALOG_URL, "content_sha256": catalog["content_sha256"], "details": details})
            if added or removed:
                catalog.update(status="changed", manual_review=True)
            state["catalogs"][CATALOG_ID] = inventory
        except RefreshError as exc:
            catalog.update(status="error", error_code=exc.code, manual_review=True)
            report["errors"].append({"source_id": CATALOG_ID, "code": exc.code})
        report["sources"].append(catalog)
        pnc = {"source_id": PNC_ID, "url": PNC_URL, "format": "html", "status": "unchanged",
               "previous_sha256": None, "content_sha256": None, "error_code": None, "manual_review": False}
        try:
            raw = fetch_source(PNC_URL, "html", session)
            soup = BeautifulSoup(raw, "html.parser")
            # Access recovery itself deserves review. Never follow a discovered
            # download or work around an access-denied response.
            links = {PNC_URL}
            for anchor in soup.find_all("a", href=True):
                if "homicid" in normalize(anchor.get_text(" ", strip=True) + " " + unquote(anchor["href"])):
                    links.add(source_url(urljoin(PNC_URL, anchor["href"]), {"transparencia.pnc.gob.sv"}))
            if len(links) > 1000:
                raise RefreshError("catalog_too_many_documents")
            inventory = sorted(links)
            before = state["catalogs"].get(PNC_ID)
            pnc["content_sha256"] = digest(raw)
            added = sorted(set(inventory) - set(before or []))[:20] if before is None else sorted(set(inventory) - set(before))
            removed = sorted(set(before or []) - set(inventory))
            for action, entries in (("added", added), ("removed", removed)):
                for url in entries:
                    report["changes"].append({"id": stable_id(["catalog_document", action, url]),
                                              "kind": "catalog_document", "source_id": PNC_ID,
                                              "source_url": PNC_URL, "content_sha256": pnc["content_sha256"],
                                              "details": {"action": action, "document_url": url,
                                                          "requires_manual_extraction": True}})
            if added or removed:
                pnc.update(status="changed", manual_review=True)
            state["catalogs"][PNC_ID] = inventory
        except RefreshError as exc:
            pnc.update(status="error", error_code=exc.code, manual_review=True)
            report["errors"].append({"source_id": PNC_ID, "code": exc.code})
        report["sources"].append(pnc)
        memoria = {"source_id": MEMORIAS_ID, "url": MEMORIAS_URL, "format": "html", "status": "unchanged",
                   "previous_sha256": None, "content_sha256": None, "error_code": None, "manual_review": False}
        try:
            raw = fetch_source(MEMORIAS_URL, "html", session)
            soup = BeautifulSoup(raw, "html.parser")
            links = set()
            for anchor in soup.find_all("a", href=True):
                href = anchor["href"]
                if re.fullmatch(r"/(?:informe|memoria)-de-labores-20\d{2}-20\d{2}/?", urlsplit(href).path):
                    links.add(source_url(urljoin(MEMORIAS_URL, href), {"www.fiscalia.gob.sv", "fiscalia.gob.sv"}))
            if not links:
                raise RefreshError("catalog_documents_missing")
            if len(links) > 1000:
                raise RefreshError("catalog_too_many_documents")
            inventory = sorted(links)
            before = state["catalogs"].get(MEMORIAS_ID)
            memoria["content_sha256"] = digest(raw)
            if before is not None:
                added = sorted(set(inventory) - set(before))
            else:
                added = [url for url in inventory if str(as_of.year) in urlsplit(url).path][:20]
            removed = sorted(set(before or []) - set(inventory))
            for action, entries in (("added", added), ("removed", removed)):
                for url in entries:
                    report["changes"].append({"id": stable_id(["catalog_document", action, url]),
                                              "kind": "catalog_document", "source_id": MEMORIAS_ID,
                                              "source_url": MEMORIAS_URL, "content_sha256": memoria["content_sha256"],
                                              "details": {"action": action, "document_url": url,
                                                          "requires_manual_extraction": True}})
            if added or removed:
                memoria.update(status="changed", manual_review=True)
            state["catalogs"][MEMORIAS_ID] = inventory
        except RefreshError as exc:
            memoria.update(status="error", error_code=exc.code, manual_review=True)
            report["errors"].append({"source_id": MEMORIAS_ID, "code": exc.code})
        report["sources"].append(memoria)
    finally:
        if own_session:
            session.close()
    if candidate is not None:
        report["proposal_file"] = "proposed-homicides.json"
        report["proposal_sha256"] = digest(json_bytes(candidate))
    report["status"] = "error" if report["errors"] else "changed" if report["changes"] else "unchanged"
    report["review_required"] = bool(report["changes"] or report["errors"])
    return report, candidate, state


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def review_markdown(report: dict) -> str:
    lines = ["# Revisión de estadísticas oficiales", "", "**Propuesta sin aprobar. No se ha publicado ningún cambio.**", "",
             f"Consulta: {report['checked_at']} · Corte local: {report['as_of']}",
             f"Resultado: `{report['status']}` · Base: `{report['baseline_sha256']}`", "",
             "Los documentos modificados y vínculos nuevos requieren revisión de su universo, unidad y tablas.",
             "Las celdas vacías se conservan como ausencias; nunca se distribuye un total nacional entre departamentos.", ""]
    for change in report["changes"]:
        lines.extend([f"## {change['kind']} · {change['source_id']}", f"Fuente: {change['source_url']}",
                      f"Identificador: `{change['id']}`", "", "```json",
                      json.dumps(change["details"], ensure_ascii=False, indent=2), "```", ""])
    for error in report["errors"]:
        lines.append(f"- Atención: `{error['source_id']}` — `{error['code']}`. Se conserva la publicación anterior.")
    if not report["review_required"]:
        lines.append("No se detectaron cambios en los valores ni documentos oficiales observados.")
    return "\n".join(lines) + "\n"


def _read_json(path: Path, maximum=2_000_000):
    if path.stat().st_size > maximum:
        raise RefreshError("input_too_large")
    return json.loads(path.read_bytes())


def write_outputs(directory: Path, report: dict, candidate: dict | None, state: dict):
    directory = directory.resolve()
    repo = Path(__file__).resolve().parents[4]
    if directory == repo or (repo in directory.parents and ".local-quarantine" not in directory.relative_to(repo).parts):
        raise RefreshError("output_must_be_private")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    payloads = {"report.json": json_bytes(report), "source-state.json": json_bytes(state),
                "review.md": review_markdown(report).encode()}
    if candidate is not None:
        payloads["proposed-homicides.json"] = json_bytes(candidate)
    stale = directory / "proposed-homicides.json"
    if candidate is None and stale.exists():
        stale.unlink()
    for filename, raw in payloads.items():
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
            stream.write(raw)
            path = Path(stream.name)
        os.chmod(path, 0o600)
        path.replace(directory / filename)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--previous-state", type=Path)
    args = parser.parse_args(argv)
    try:
        baseline = _read_json(args.baseline)
        previous = _read_json(args.previous_state) if args.previous_state and args.previous_state.exists() else None
        report, candidate, state = refresh(baseline, digest(args.baseline.read_bytes()), previous_state=previous)
        write_outputs(args.output_dir, report, candidate, state)
    except (RefreshError, OSError, ValueError, KeyError, TypeError) as exc:
        # Never print a possibly sensitive response, path or exception message.
        print(json.dumps({"status": "error", "code": exc.code if isinstance(exc, RefreshError) else "invalid_monitor_input"}))
        return 2
    print(json.dumps({"status": report["status"], "changes": len(report["changes"]),
                      "errors": len(report["errors"]), "proposal": candidate is not None}))
    return 2 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
