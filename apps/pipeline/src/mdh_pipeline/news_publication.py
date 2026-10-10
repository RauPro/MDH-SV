"""Export only human-approved news dossiers, independently of the case catalogue."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SOURCE = ROOT / "data/reviewed/news_dossiers.json"
PUBLIC = ROOT / "apps/web/public/archive/dossiers.json"
ARCHIVE = ROOT / "apps/web/public/archive/news.json"


def validate_collection(document, *, archive=None):
    from .news_dossier import validate_news_dossier
    if (not isinstance(document, dict) or set(document) != {"schema_version", "dossiers"}
            or type(document["schema_version"]) is not int or document["schema_version"] != 1
            or not isinstance(document["dossiers"], list)):
        raise ValueError("Invalid approved news dossier collection")
    references = None
    if archive is not None:
        if not isinstance(archive, dict) or not isinstance(archive.get("records"), list):
            raise ValueError("Invalid bibliography for news dossier publication")
        references = {record["id"]: record for record in archive["records"]}
        if len(references) != len(archive["records"]):
            raise ValueError("Duplicate bibliography identity")
    identifiers, urls, dossiers = set(), set(), []
    for value in document["dossiers"]:
        dossier = validate_news_dossier(value, require_reviewed=True)
        if dossier["extraction_status"] != "processed":
            raise ValueError("A source must be read before an enriched news dossier is published")
        if dossier["candidate_id"] in identifiers or dossier["source_url"] in urls:
            raise ValueError("Duplicate approved news dossier")
        if references is not None:
            reference = references.get(dossier["candidate_id"])
            if reference is None or any(reference.get(key) != dossier.get(key)
                                        for key in ("source_url", "publisher", "published_date")):
                raise ValueError("News dossier does not match the current bibliography")
        identifiers.add(dossier["candidate_id"])
        urls.add(dossier["source_url"])
        dossiers.append(dossier)
    return {"schema_version": 1, "dossiers": sorted(dossiers, key=lambda d: d["candidate_id"])}


def export_news_dossiers(source=SOURCE, destination=PUBLIC, archive=ARCHIVE):
    source, destination, archive = Path(source), Path(destination), Path(archive)
    document = validate_collection(json.loads(source.read_text()), archive=json.loads(archive.read_text()))
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)
    return len(document["dossiers"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--destination", type=Path, default=PUBLIC)
    parser.add_argument("--archive", type=Path, default=ARCHIVE)
    args = parser.parse_args()
    try:
        count = export_news_dossiers(args.source, args.destination, args.archive)
    except (ValueError, TypeError, KeyError, OSError):
        raise SystemExit("No se exportaron fichas: colección o aprobación inválida. Revise los archivos privados sin publicarlos.") from None
    print(json.dumps({"approved_news_dossiers": count, "case_catalog_changed": False}))


if __name__ == "__main__":
    main()
