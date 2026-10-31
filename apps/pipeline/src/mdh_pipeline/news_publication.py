"""Export only human-approved news dossiers, independently of the case catalogue."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .contracts.news_collection import (
    validate_collection as validate_collection,
)

ROOT = Path(__file__).resolve().parents[4]
SOURCE = ROOT / "data/reviewed/news_dossiers.json"
PUBLIC = ROOT / "apps/web/public/archive/dossiers.json"
ARCHIVE = ROOT / "apps/web/public/archive/news.json"


def export_news_dossiers(source=SOURCE, destination=PUBLIC, archive=ARCHIVE):
    source, destination, archive = Path(source), Path(destination), Path(archive)
    approved = validate_collection(json.loads(source.read_text()))
    # An editorial decision can validly document that an article is outside
    # scope; retain that decision in the source, not in the public monitor.
    eligible = {"schema_version": 1, "dossiers": [d for d in approved["dossiers"]
        if d["classification"]["jurisdiction"] == "SV" and d["classification"]["relevance"] == "relevant"]}
    document = validate_collection(eligible, archive=json.loads(archive.read_text()))
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
