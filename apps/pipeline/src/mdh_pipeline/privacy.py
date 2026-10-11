"""Field-level minimization for unreviewed extraction, not a privacy approval.

The source stays in memory. Triage flags concern the article; they must not erase
unrelated fields. Locally detected identifiers and model-declared protected
fields are withheld in full, including their citation references.
"""
from __future__ import annotations

import re

from .review_queue import normalize

PRIVACY_POLICY_VERSION = 2
_MINOR = r"\b(?:niñ[oa]s?|adolescentes?|menor(?:es)? de edad|(?:[1-9]|1[0-7]) años)\b"
_CLINICAL = r"\b(?:diagn[oó]stic\w*|c[aá]ncer|VIH|diab[eé]t\w*|historial m[eé]dico|historia cl[ií]nica|embaraz\w*)\b"
_ADDRESS = r"\b(?:domicilio|direcci[oó]n particular|reside en|residente en|vive en|habita en|casa de habitaci[oó]n|vivienda (?:situada|ubicada)|casa\s*(?:n[uú]mero|#)|pasaje\s+\w+)\b|[-+]?\d{1,2}\.\d{2,}\s*,\s*[-+]?\d{1,3}\.\d{2,}"
_CONTACT = r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|\b(?:tel[eé]fono|celular|DUI|pasaporte|documento de identidad)\s*[:#]?\s*[+\d][\d -]{6,}|(?<!\d)(?:\+503[ -]?)?[267]\d{3}[- ]?\d{4}(?!\d)|(?<!\d)\d{8}-\d(?!\d)"
_PATTERNS = {"minor_identifiers": _MINOR, "clinical_details": _CLINICAL,
             "precise_address": _ADDRESS, "private_contact": _CONTACT}
_NARRATIVE = {"title", "summary", "facts", "alleged_violation", "hypothesis"}
_CAPITAL = r"[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+"
_NAME = rf"\b{_CAPITAL}(?:\s+(?:(?:de|del|la|los)\s+)?{_CAPITAL}){{1,4}}\b"
_SINGLE_NAME = rf"\b(?i:llamad[oa]|identificad[oa] como|de nombre|niñ[oa]|adolescente|paciente)\s+({_CAPITAL})\b"
_NAME_BEFORE_MINOR_AGE = rf"\b({_CAPITAL})\s*(?:,\s*)?(?:de\s+|\(\s*)(?:[1-9]|1[0-7])\s+años\b"
_PUBLIC_ENTITY = r"^(?:(?:el|la|en)\s+){0,2}(?:ministerio|hospital|universidad|organizacion|instituto|fundacion|fiscalia|procuraduria|policia|defensoria|corte|tribunal|centro|consejo|secretaria|gobierno|asamblea|direccion)\b"


def privacy_flags(text):
    """Article triage is deliberately broader than field-level redaction."""
    return sorted(key for key, pattern in _PATTERNS.items() if re.search(pattern, text, re.I))


def _known_department(value):
    from mdh_schema import DepartmentEnum
    return normalize(value).strip() in {
        normalize(item.value) for item in DepartmentEnum if item is not DepartmentEnum.DESCONOCIDO}


def _known_coarse_place(name, value, department_hint=None):
    if name == "department":
        return _known_department(value)
    if name == "municipality":
        from .municipalities import resolve_municipality
        from mdh_schema import DepartmentEnum
        # Privacy only asks whether this is a coarse name. A homonym remains a
        # safe coarse name even though the map must leave it unresolved. Do not
        # persist or choose any of the possible departments in this guard.
        return (resolve_municipality(value) is not None
                or resolve_municipality(value, department_hint=department_hint) is not None
                or any(resolve_municipality(value, item.value) is not None
                       for item in DepartmentEnum if item is not DepartmentEnum.DESCONOCIDO))
    return False


def _private_names(paragraphs):
    """Conservative exact-name guard in sensitive paragraphs; no identity inference.

    This is a second guard after the model's explicit minimization instructions.
    Ambiguous public names in a sensitive paragraph can also require review.
    Coarse canonical geography is checked separately and is never a name match.
    """
    names = set()
    for paragraph in paragraphs.values():
        if not (re.search(_MINOR, paragraph, re.I) or re.search(_CLINICAL, paragraph, re.I)):
            continue
        names.update(normalize(match.group()) for match in re.finditer(_NAME, paragraph))
        names.update(normalize(match.group(1)) for match in re.finditer(_SINGLE_NAME, paragraph))
        names.update(normalize(match.group(1)) for match in re.finditer(_NAME_BEFORE_MINOR_AGE, paragraph))
        # Initials can identify a minor just as a full name can.
        names.update(normalize(match.group().strip()) for match in re.finditer(r"\b(?:[A-ZÁÉÍÓÚÑ]\.\s*){2,5}", paragraph))
    names = {name for name in names if not _known_department(name) and name != "el salvador"
             and not re.search(_PUBLIC_ENTITY, name)}
    # A surname alone is still identifying; retain no variants of a detected
    # sensitive name merely because the generated field shortened it.
    return names | {part for name in names for part in name.split()
                    if len(part) >= 4 and part not in {"desde", "segun", "sobre"}}


def minimize_fields(record, *, paragraphs, source_title, flags, protected_fields):
    """Mutate only protected claims and return their names, never source prose.

    Call after exact citation and place/date grounding. A retained department or
    municipality under privacy triage must be a known coarse administrative
    name, not an address or a person's name placed in a geographic field.
    """
    protected = set(protected_fields)
    if record is None:
        return sorted(protected)
    names = _private_names({**paragraphs, "source_title": source_title})
    for name in type(record).model_fields:
        if name == "date_precision":
            continue
        claim = getattr(record, name)
        if claim.value is None:
            continue
        values = claim.value if isinstance(claim.value, list) else [claim.value]
        text = " ".join(str(value) for value in values)
        normalized = normalize(text)
        direct_flags = set(privacy_flags(text))
        citations = " ".join(citation.quote for citation in claim.citations)
        if direct_flags & {"precise_address", "private_contact"}:
            protected.add(name)
        if "clinical_details" in direct_flags and (name in _NARRATIVE or name == "affected_persons"):
            protected.add(name)
        if flags and name in {"department", "municipality"}:
            hint = record.department.value if record.department.status == "reported" else None
            if not isinstance(claim.value, str) or not _known_coarse_place(name, claim.value, hint):
                protected.add(name)
            # A coarse place can remain usable even if a different passage
            # identifies a child or contains an address. The projector still
            # checks whether the citation actually locates the news event.
            continue
        if name == "public_place" and (re.search(_ADDRESS, citations, re.I)
                                        or re.search(_MINOR, citations, re.I)):
            protected.add(name)
        if "minor_identifiers" in flags and name in {"event_date", "event_date_end", "event_time"} and re.search(
                r"\b(?:naci[oó]|nacimiento|cumpleañ\w*)\b", citations, re.I):
            protected.add(name)
        if any(re.search(r"(?<!\w)" + re.escape(private_name) + r"(?!\w)", normalized)
               for private_name in names):
            protected.add(name)
        # A reference to children or students is not itself an identifier.
        # Anonymous descriptions remain useful; named identities are removed
        # above even when the model omitted its privacy declaration.
    for name in protected:
        claim = getattr(record, name)
        claim.value, claim.status, claim.citations = None, "withheld", []
    if record.event_date.value is None:
        if record.event_date_end.value is not None:
            record.event_date_end.value, record.event_date_end.status, record.event_date_end.citations = None, "withheld", []
            protected.add("event_date_end")
        record.date_precision = "unknown"
    return sorted(protected)
