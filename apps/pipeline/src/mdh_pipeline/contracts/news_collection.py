"""Pure news_collection contracts, extracted without changing validation or normalization."""
from __future__ import annotations

def validate_collection(document, *, archive=None, allow_automatic=False):
    from .dossier import validate_news_dossier, validate_published_news_dossier
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
        dossier = (validate_published_news_dossier(value) if allow_automatic
                   else validate_news_dossier(value, require_reviewed=True))
        if dossier["extraction_status"] != "processed":
            raise ValueError("A source must be read before an enriched news dossier is published")
        if dossier["candidate_id"] in identifiers or dossier["source_url"] in urls:
            raise ValueError("Duplicate approved news dossier")
        if references is not None:
            reference = references.get(dossier["candidate_id"])
            if reference is None or any(reference.get(key) != dossier.get(key)
                                        for key in ("source_url", "publisher", "published_date")):
                raise ValueError("News dossier does not match the current bibliography")
            if (dossier["schema_version"] == 2 and dossier["automation"]["source_content_sha256"]
                    != reference.get("selection", {}).get("evidence", {}).get("content_sha256")):
                raise ValueError("Automatic dossier does not match the selected source text")
        identifiers.add(dossier["candidate_id"])
        urls.add(dossier["source_url"])
        dossiers.append(dossier)
    return {"schema_version": 1, "dossiers": sorted(dossiers, key=lambda d: d["candidate_id"])}
