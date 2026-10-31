"""Prepare a private, empty editorial worksheet from collected bibliographic metadata.

No fetch, model, classification or publication is performed. The candidate identity
keeps the record inadmissible to the publication schema until explicit human review.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .publication import Publication
from .source_urls import canonical_url

ROOT = Path(__file__).resolve().parents[4]
HISTORY = ROOT / "apps/web/public/archive/news.json"
PRIVATE = ROOT / ".local-quarantine/drafts"
RECORD_TYPES = ("incident", "aggregate", "publication")
CHECKLIST = [
    "Leer la noticia original y comprobar fecha, fuente y jurisdicción salvadoreña.",
    "Elegir tipo de registro, clasificación y etiquetas; eliminar los marcadores de borrador.",
    "Redactar título neutral, resumen y hechos atribuidos con localizadores específicos.",
    "Completar personas, lugar, fechas, actores y demás campos sólo cuando la fuente los respalde.",
    "Conservar null para datos desconocidos y usar withheld con motivo para los datos protegidos.",
    "Revisar duplicados y actualizaciones de fichas existentes antes de crear otro caso.",
    "Revisar privacidad y mantener human_reviewed=false hasta la aprobación humana del flujo.",
]


def _today():
    return datetime.now(ZoneInfo("America/El_Salvador")).date()


def _unknown(note="Pendiente de lectura editorial de la fuente; no se infiere a partir del titular."):
    return {"value": None, "status": "not_reported", "evidence_refs": [], "note": note}


def _not_applicable():
    return {"value": None, "status": "not_applicable", "evidence_refs": [],
            "note": "Informe general seleccionado por el operador; no se asigna un único hecho."}


def _text(value, label, maximum):
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f"Missing or oversized {label}")
    clean = " ".join(html.unescape(re.sub(r"<[^>]*>", " ", value)).split())
    if len(clean) < 3:
        raise ValueError(f"Missing or invalid {label}")
    return clean


def prepare_draft(candidate: dict, *, record_type: str, published_date: date | None = None,
                  today: date | None = None) -> dict:
    """Return a complete worksheet that cannot validate as a public record yet.

    ``record_type`` is an operator decision. A missing publication date must be supplied
    explicitly; neither discovery time nor today's date is used as a substitute.
    The function returns only allowlisted metadata and neutral drafting placeholders.
    """
    if not isinstance(candidate, dict) or record_type not in RECORD_TYPES:
        raise ValueError("Expected candidate metadata and an explicit record type")
    today = today or _today()
    url = canonical_url(candidate.get("source_url", ""))
    candidate_id = hashlib.sha256(url.encode()).hexdigest()
    if candidate.get("id") != candidate_id:
        raise ValueError("Candidate identifier does not match its canonical source URL")
    if candidate.get("status") in ("rejected", "approved_for_proposal", "published"):
        raise ValueError("This candidate is not pending; inspect its existing review decision")
    title = _text(candidate.get("source_title") or candidate.get("title"), "source title", 300)
    publisher = _text(candidate.get("publisher"), "publisher", 200)
    original_date = candidate.get("published_date")
    if original_date is not None:
        if not isinstance(original_date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", original_date):
            raise ValueError("Invalid source publication date")
        try:
            date.fromisoformat(original_date)
        except ValueError as error:
            raise ValueError("Invalid source publication date") from error
    if published_date is None:
        if not isinstance(original_date, str):
            raise ValueError("Publication date is unknown; supply --published-date after checking the source")
        try:
            published_date = date.fromisoformat(original_date)
        except ValueError as error:
            raise ValueError("Invalid source publication date") from error
    if not isinstance(published_date, date) or isinstance(published_date, datetime):
        raise ValueError("Publication date must be a date")
    if published_date > today:
        raise ValueError("Cannot prepare a draft from a future publication")
    is_overview = record_type == "publication"
    event_field = _not_applicable if is_overview else _unknown
    locator = "Título y metadatos bibliográficos; localizador del contenido pendiente de revisión."
    fields = ("alleged_violation", "facts", "affected_persons", "reported_actors",
              "institutional_sources", "vulnerable_groups", "themes", "hypothesis", "instrument")
    dossier = {field: _unknown() for field in fields}
    dossier.update(
        schema_version=1,
        location={field: event_field() for field in ("department", "municipality", "public_place")},
        event_date=event_field(), event_date_end=event_field(), event_time=event_field(),
        date_precision="unknown", evidence=[{"id": "primary", "url": url, "locator": locator}],
    )
    record = {
        "id": "news-" + candidate_id, "record_type": record_type,
        "title": "Borrador editorial: " + title[:280], "publisher": publisher,
        "source_title": title, "published_date": published_date.isoformat(),
        "date_note": "Fecha de publicación bibliográfica; fecha del hecho pendiente de revisión.",
        "category": "Pendiente de clasificación", "tags": ["Pendiente de revisión"],
        "summary": "Borrador editorial preparado a partir de metadatos bibliográficos. "
                   "Los hechos y la jurisdicción de la noticia están pendientes de lectura y revisión.",
        "source_url": url, "source_locator": locator,
        "source_checked_at": today.isoformat(),
        "source_check_method": "Sólo comprobación de formato y coherencia de metadatos del borrador; "
                               "texto periodístico pendiente de lectura y revisión editorial.",
        "coverage": "Jurisdicción y alcance pendientes de revisión",
        "department": None, "geometry": None, "language": "es",
        "incident_date": None, "incident_date_end": None, "date_precision": "unknown",
        "review_status": "source_checked", "human_reviewed": False,
        "reviewed_by": None, "reviewed_at": None, "review_notes": None,
        "origin_candidate_id": candidate_id, "related_sources": [], "reported_actor": None,
        "source_record_key": "news:" + candidate_id,
        "privacy_note": "Borrador sin extracción de identidades ni domicilios; "
                        "el titular original requiere revisión de privacidad antes de publicar.",
        "penitentiary_related": False, "dossier": dossier,
    }
    # Check all remaining field shapes with a throwaway copy. The actual output keeps
    # its candidate origin and fails Publication validation while human_reviewed=false.
    # The existing review workflow validates it only after recording human approval.
    validated = Publication.model_validate({**record, "origin_candidate_id": None}).model_dump(mode="json")
    validated["origin_candidate_id"] = candidate_id
    return {
        "schema_version": 1, "status": "draft", "publishable": False,
        "candidate_id": candidate_id, "prepared_at": today.isoformat(),
        "notice": "Plantilla privada para completar. Su identidad de candidata impide validarla "
                  "como publicación mientras human_reviewed sea false. source_checked es un marcador "
                  "de metadatos y no acredita lectura del artículo. No elimines origin_candidate_id: "
                  "el flujo registra y valida la aprobación humana por separado.",
        "metadata_publication_date": original_date,
        "publication_date_origin": "metadata" if original_date == published_date.isoformat() else "operator",
        "operator_selected_record_type": record_type,
        "completion_required": [*CHECKLIST,
                                "Comprobar penitentiary_related: false es un marcador de plantilla, "
                                "no una conclusión sobre la noticia."],
        "record": validated,
    }


def _read_json(path: Path, maximum: int):
    with path.open("rb") as stream:
        payload = stream.read(maximum + 1)
    if len(payload) > maximum:
        raise ValueError("Input exceeds the metadata size limit")
    return json.loads(payload)


def history_candidate(news_id: str, path: Path = HISTORY) -> dict:
    if not re.fullmatch(r"[a-f0-9]{64}", news_id):
        raise ValueError("Expected the full 64-character history identifier")
    archive = _read_json(Path(path), 32 * 1024 * 1024)
    matches = [record for record in archive["records"] if record.get("id") == news_id]
    if len(matches) != 1:
        raise ValueError("History identifier is missing or ambiguous")
    return matches[0]


def write_private_draft(document: dict, destination: Path | None = None,
                        *, private_root: Path = PRIVATE) -> Path:
    """Write with owner-only permissions, never clobber or target a public directory."""
    private_root = Path(private_root).resolve()
    destination = Path(destination) if destination else private_root / (document["candidate_id"] + ".json")
    destination = destination.resolve()
    if any(destination.is_relative_to(folder.resolve())
           for folder in (ROOT / "apps/web/public", ROOT / "data/reviewed")):
        raise ValueError("Draft output cannot target public or reviewed data")
    if not destination.is_relative_to(private_root) or destination == private_root or destination.suffix != ".json":
        raise ValueError("Draft output must be a JSON file inside .local-quarantine/drafts")
    if document.get("publishable") is not False or document.get("status") != "draft":
        raise ValueError("Only an unpublished editorial worksheet can be written here")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    content = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--candidate", type=Path, help="JSON metadata copied from a private review candidate")
    source.add_argument("--history-id", help="Full identifier from the public bibliographic archive")
    parser.add_argument("--history-path", type=Path, default=HISTORY)
    parser.add_argument("--record-type", required=True, choices=RECORD_TYPES)
    parser.add_argument("--published-date", type=date.fromisoformat)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        candidate = _read_json(args.candidate, 256 * 1024) if args.candidate else history_candidate(args.history_id, args.history_path)
        document = prepare_draft(candidate, record_type=args.record_type, published_date=args.published_date)
        path = write_private_draft(document, args.output)
    except (ValueError, KeyError, OSError) as error:
        parser.error(str(error))
    # Source text and draft data are deliberately absent from console/Action logs.
    print(json.dumps({"status": "draft", "publishable": False, "output": str(path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
