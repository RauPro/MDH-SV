"""Exact Salvadoran administrative-name lookup backed by Legislative Decree 762.

This classifies an already sourced municipality/district name; it does not extract
places from prose, establish that an event happened there, geocode an address or
infer the event's date. Historical municipality names are retained as districts.
Homonyms fail closed unless a compatible, independently grounded department is
provided. Callers must verify the article citation before using the result.
"""
from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from importlib.resources import files
from typing import TypedDict

from mdh_schema import DepartmentEnum

REFERENCE_VERSION = "sv-decree-762-2023-v1"
REFERENCE_SOURCE_URL = (
    "https://cuscatlansur.gob.sv/wp-content/uploads/2024/08/"
    "LEY-ESPECIAL-PARA-LA-REESTRUCTURACION-MUNICIPAL.pdf"
)


class MunicipalityMatch(TypedDict):
    name: str
    department: str
    kind: str
    current_municipality: str
    reference_version: str
    source_url: str


def _normalize(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 200:
        return ""
    return " ".join("".join(
        character for character in unicodedata.normalize("NFD", value.casefold())
        if not unicodedata.combining(character)
    ).split())


def _place_key(value: object) -> str:
    return re.sub(r"^(?:municipio|distrito)(?: municipal)? de ", "", _normalize(value))


@lru_cache(maxsize=1)
def _entries() -> tuple[MunicipalityMatch, ...]:
    document = json.loads(files("mdh_pipeline").joinpath(
        "data", "el_salvador_municipalities.json"
    ).read_text(encoding="utf-8"))
    if (document["schema_version"] != 1
            or document["reference_version"] != REFERENCE_VERSION
            or document["country"] != "SV"
            or document["source"]["url"] != REFERENCE_SOURCE_URL):
        raise ValueError("invalid_municipality_reference")
    departments = {value.value for value in DepartmentEnum if value.name != "DESCONOCIDO"}
    entries = []
    for municipality in document["municipalities"]:
        if municipality["department"] not in departments:
            raise ValueError("invalid_municipality_reference")
        for kind, items in (("municipality", [municipality]),
                            ("district", municipality["districts"])):
            for item in items:
                if (not _normalize(item["name"]) or type(item["source_page"]) is not int
                        or not 6 <= item["source_page"] <= 13):
                    raise ValueError("invalid_municipality_reference")
                entries.append({
                    "name": item["name"], "department": municipality["department"],
                    "kind": kind, "current_municipality": municipality["name"],
                    "reference_version": REFERENCE_VERSION,
                    "source_url": f"{REFERENCE_SOURCE_URL}#page={item['source_page']}",
                })
    # Keep duplicates across departments to expose ambiguity, never overwrite them.
    identities = {(_normalize(item["name"]), item["department"], item["kind"])
                  for item in entries}
    if (len(entries) != 306 or len(identities) != 306
            or sum(item["kind"] == "municipality" for item in entries) != 44
            or {item["department"] for item in entries} != departments):
        raise ValueError("invalid_municipality_reference")
    return tuple(entries)


def resolve_municipality(name: str, department_hint: str | None = None) -> MunicipalityMatch | None:
    """Resolve exactly one official name; unknown/ambiguous/conflicting names return None.

    Case, accents, whitespace and ``municipio de``/``distrito de`` prefixes do not
    affect matching. There are no guessed aliases, fuzzy matches, suffix removal,
    address parsing or fallback coordinates. A supplied invalid hint is a conflict,
    even when the name would be unique without it. This is SV-specific: the caller
    must establish the country's scope from the source, not from the place name.
    """
    key = _place_key(name)
    if not key:
        return None
    hint = None
    if department_hint is not None:
        hint = re.sub(r"^departamento de ", "", _normalize(department_hint))
        if hint not in {_normalize(value.value) for value in DepartmentEnum
                        if value.name != "DESCONOCIDO"}:
            return None
    matches = [entry for entry in _entries() if _normalize(entry["name"]) == key
               and (hint is None or _normalize(entry["department"]) == hint)]
    return dict(matches[0]) if len(matches) == 1 else None
