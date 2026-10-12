"""Conservative administrative mentions from a verified article, without a model.

Callers must separately establish current Salvadoran human-rights relevance and
bind the source hash to that selection. This helper does not infer an incident's
location, inspect titles/bylines, geocode addresses or change any model evidence.
Only an unambiguous department and a paragraph identifier leave this module.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import re

from mdh_schema import DepartmentEnum

from .municipalities import _entries, resolve_municipality, REFERENCE_VERSION
from .review_queue import normalize

SOURCE_LOCATION_RULE_VERSION = 1
_DEPARTMENTS = {normalize(value.value): value.value for value in DepartmentEnum
                if value is not DepartmentEnum.DESCONOCIDO}
_FOREIGN = re.compile(
    r"\b(?:guatemala|honduras|nicaragua|costa rica|panama|mexico|estados unidos|ee\.?\s?uu\.?|"
    r"california|texas|florida|nueva york|canada|cuba|haiti|republica dominicana|puerto rico|"
    r"colombia|venezuela|ecuador|peru|bolivia|brasil|argentina|chile|uruguay|paraguay|"
    r"espana|portugal|francia|italia|alemania|reino unido|inglaterra|rusia|ucrania|"
    r"israel|palestina|china|taiwan|japon|corea|australia|afganistan|iran|irak|siria|libano)\b")
_SALVADORAN = re.compile(r"\bel salvador\b")
_ROLE_ONLY = re.compile(r"\b(?:sala de redaccion|redaccion|sede|oficinas?|corresponsal|"
                        r"carretera|autopista|ruta|tribunal|juzgado)\b")
_ORIGIN_RESIDENCE = re.compile(
    r"\b(?:nacio|nacido|nacida|originari[oa]|oriund[oa]|reside|residia|residentes?|"
    r"residencia|domicilio|domiciliad[oa]|vive|vivia|vecin[oa]|natal|natural de|nativ[oa])\b[^,;.!?]{0,80}$")
_DIRECTION = re.compile(r"^\s+(?:norte|sur|este|oeste|centro|oriental|occidental)\b")
_BARE_AMBIGUOUS_NAMES = {"la union", "la libertad", "victoria", "el triunfo", "armenia"}


def _clean(text):
    return " ".join(normalize(text).split())


@lru_cache(maxsize=1)
def _patterns():
    # Longest names first prevents matching "San Salvador" inside the official
    # "San Salvador Norte". resolve_municipality still detects all homonyms.
    places = sorted({normalize(place["name"]) for place in _entries()}, key=lambda name: (-len(name), name))
    alternatives = "(?:" + "|".join(re.escape(name) for name in places) + ")"
    departments = "(?:" + "|".join(re.escape(name) for name in sorted(_DEPARTMENTS, key=lambda name: (-len(name), name))) + ")"
    return (
        re.compile(rf"\b(?P<kind>departamentos?)\s+de\s+(?P<place>{departments})\b"),
        re.compile(rf"\b(?P<kind>municipios?|distritos?|localidades|localidad|ciudades|ciudad)\s+(?:de\s+)?(?P<place>{alternatives})\b"),
        re.compile(rf"\ben\s+(?P<place>{alternatives})\b"),
        re.compile(rf"\s*(?:,|\by\b|\be\b)\s*(?:de\s+)?(?P<place>{alternatives})\b"),
        re.compile(rf"\s*(?:,|\by\b|\be\b)\s*(?:de\s+)?(?P<place>{departments})\b"),
        re.compile(rf"^(?:(?:municipio|distrito|ciudad|localidad|departamento)\s+(?:de\s+)?)?"
                   rf"(?:{alternatives}|{departments})(?:\s*\([^)]{{0,40}}\))?\s*(?:[:—–-]|,\s*\d{{1,2}}\b)"),
        re.compile(rf"\b(?P<place>{alternatives})\b"),
    )


def _valid_paragraphs(article):
    if not isinstance(article, dict):
        return None
    text, paragraphs = article.get("text"), article.get("paragraphs")
    if (not isinstance(text, str) or not 0 < len(text) <= 24_000
            or not isinstance(paragraphs, list) or not 1 <= len(paragraphs) <= 160):
        return None
    try:
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != article.get("content_sha256"):
            return None
    except UnicodeError:
        return None
    for index, paragraph in enumerate(paragraphs, 1):
        if (not isinstance(paragraph, dict) or set(paragraph) != {"id", "text"}
                or paragraph.get("id") != f"p{index}" or not isinstance(paragraph.get("text"), str)
                or not paragraph["text"].strip() or len(paragraph["text"]) > 4000):
            return None
    if "\n\n".join(paragraph["text"] for paragraph in paragraphs) != text:
        return None
    return paragraphs


def _excluded_context(clause, start, end, name):
    """Refuse other roles and explicit denials instead of guessing their target."""
    if _ROLE_ONLY.search(clause) or _ORIGIN_RESIDENCE.search(clause[:start]):
        return True
    if re.search(r"\b(?:cree|creen|creer|creyendo|confia\w*|piensa\w*|pensar|confianza|apuesta\w*|"
                 r"consiste\w*|insiste\w*|oramos|rezamos)\s+(?:en\s+(?:el\s+|la\s+)?)?$", clause[:start]):
        return True
    if name in {"la libertad", "la union", "victoria", "el triunfo"} and re.match(
            r"\s+(?:de expresion|de prensa|de pensamiento|de asociacion|de trabajadores|sindical|gremial|electoral|democratica)\b", clause[end:]):
        return True
    if _DIRECTION.search(clause[end:]):
        return True
    escaped = re.escape(name)
    qualified = rf"(?:(?:el|la)\s+)?(?:(?:municipio|distrito|ciudad|localidad|departamento)\s+(?:de\s+)?)?{escaped}\b"
    verbs = r"(?:ocurri\w*|sucedi\w*|aconteci\w*|produj\w*|registr\w*|ubic\w*|tuvo lugar|tuvieron lugar|fue|fueron|era|eran|estaba|estaban)"
    if (re.search(rf"\b(?:no|nunca|tampoco)\s+(?:(?:se|ha|han|habia|habian)\s+)*{verbs}[^,;.!?]{{0,60}}\ben\s+{qualified}", clause)
            or re.search(rf"\b(?:no|nunca|tampoco)\s+en\s+{qualified}", clause)
            or re.search(rf"\b{escaped}\b\s*,?\s*(?:no|nunca)\s+(?:se\s+)?(?:ocurri\w*|sucedi\w*|produj\w*|registr\w*|tuvo lugar)\b", clause)
            or re.search(rf"\b{escaped}\b\s*,?\s*(?:no|nunca)\s+hubo\s+(?:un[ao]?\s+|el\s+|la\s+)?(?:ataque|detencion|operativo|incidente|agresion)\b", clause)):
        return True
    if re.search(rf"\b(?:no|nunca)\s+hubo\s+(?:un[ao]?\s+|el\s+|la\s+)?(?:ataque|detencion|operativo|incidente|agresion)\b[^,;.!?]{{0,50}}\ben\s+{qualified}", clause):
        return True
    # A named organizational division is not a territorial department.
    if re.match(r"\s+(?:de|del)\s+(?:la\s+|el\s+)?(?:universidad|ministerio|empresa|instituto|partido|hospital)\b", clause[end:]):
        return True
    return False


def _typed_context(clause, start, end):
    prefix = clause[:start]
    if re.search(r"\b(?:en|de|del|al|a|sobre|para|desde)\s+(?:(?:el|la|los|las)\s+)?$", prefix):
        return True
    if (re.search(r"(?:\by\b|\be\b|,)\s*(?:(?:el|la|los|las)\s+)?$", prefix)
            and re.search(r"\b(?:en|del|al|sobre|para|desde)\s+(?:(?:el|la|los|las)\s+)?"
                          r"(?:municipios?|distritos?|localidad(?:es)?|ciudad(?:es)?|departamentos?)\b", prefix)):
        return True
    # An administrative region may itself be the subject of a geographic report.
    if prefix.strip(" ,:\"'“”()-") in {"", "el", "la", "los", "las"}:
        return bool(re.match(r"\s+(?:es|son|registra\w*|reporta\w*|cuenta\w*|tiene\w*|"
                             r"enfrenta\w*|sufre\w*|afronta\w*|presenta\w*|denuncia\w*|recibe\w*)\b", clause[end:]))
    return False


def _resolution(place):
    return {"type": "municipality_to_department", "municipality": place["name"],
            "municipality_kind": place["kind"], "reference_version": REFERENCE_VERSION,
            "source_url": place["source_url"]}


def _department_chain_conflict(clause, department_match, names):
    """Reject contradictory or damaged adjacent locality-to-department claims.

    This check also sees names that cannot independently support a projection,
    such as a bare locality without an explicit Salvadoran country context.
    Unknown intervening words are not silently deleted to repair a source.
    """
    prefix = clause[:department_match.start()]
    for locality in reversed(list(names.finditer(prefix))):
        connector = prefix[locality.end():]
        if len(connector) > 100:
            break
        if not re.match(r"^\s*(?:$|,|\(|\b(?:en|del|de|ubicad[oa]|situad[oa]|localizad[oa])\b)", connector):
            continue
        if not re.fullmatch(r"\s*,?\s*(?:(?:ubicad[oa]|situad[oa]|localizad[oa])\s+)?"
                            r"(?:(?:en|de|del)\s+(?:el\s+)?)?", connector):
            return True
        department = _DEPARTMENTS[department_match["place"]]
        return resolve_municipality(locality["place"], department) is None
    return False


def extract_administrative_mention(article, *, allow_municipality=True):
    """Return one conservative administrative mention, or None for ambiguity.

    Typed unique Salvadoran administrative names can establish a mention without
    an explicit country. Bare 'en X' additionally requires literal El Salvador;
    a person's Salvadoran nationality does not locate that person in the country.
    Any conflicting foreign context suppresses this source-only fallback. These
    intentionally conservative rules cannot establish where a violation occurred.
    """
    if type(allow_municipality) is not bool:
        return None
    paragraphs = _valid_paragraphs(article)
    if paragraphs is None:
        return None
    country_context = _clean(article["text"])
    if _FOREIGN.search(country_context):
        return None
    explicit_sv = bool(_SALVADORAN.search(country_context))
    department_pattern, municipality_pattern, bare_pattern, place_tail, department_tail, dateline, names = _patterns()
    candidates, ambiguous, observed_departments = [], False, set()
    for index, paragraph in enumerate(paragraphs, 1):
        normalized = _clean(paragraph["text"])
        # A place followed by a colon/dash at paragraph start is a dateline,
        # including a typed administrative label. Never use its trailing prose.
        if dateline.match(normalized):
            continue
        for clause_number, clause in enumerate(re.split(r"[.!?;]+", normalized)):
            typed_departments = []
            for match in department_pattern.finditer(clause):
                # Always validate an adjacent administrative chain before the
                # ordinary context path can accept its explicit department.
                if _department_chain_conflict(clause, match, names):
                    ambiguous = True
                    continue
                context = _typed_context(clause, match.start(), match.end())
                if not context:
                    pair = re.search(r"\ben\s+(?:(?:el|la)\s+)?(?:(?:municipio|distrito|ciudad|localidad)\s+de\s+)?"
                                     r"(?P<place>[a-z]+(?:\s+[a-z]+){0,5})\s*,\s*$", clause[:match.start()])
                    if pair:
                        context = resolve_municipality(pair["place"], _DEPARTMENTS[match["place"]]) is not None
                        if not context:
                            ambiguous = True
                        elif _excluded_context(clause, pair.start(), pair.end(), pair["place"]):
                            context = False
                if (not context or _excluded_context(clause, match.start(), match.end(), match["place"])):
                    continue
                mentions = [match]
                if match["kind"] == "departamentos":
                    cursor = match.end()
                    while more := department_tail.match(clause, cursor):
                        mentions.append(more)
                        cursor = more.end()
                for mention in mentions:
                    department = _DEPARTMENTS[mention["place"]]
                    observed_departments.add(department)
                    typed_departments.append(department)
                    candidates.append((0, index, clause_number, mention.start(), {
                        "method": "source_explicit_department", "department": department,
                        "paragraph_id": paragraph["id"]}))
            matches = []
            for match in municipality_pattern.finditer(clause):
                if _typed_context(clause, match.start(), match.end()):
                    matches.append((1, match))
                    if match["kind"] in {"municipios", "distritos", "localidades", "ciudades"}:
                        cursor = match.end()
                        while more := place_tail.match(clause, cursor):
                            matches.append((1, more))
                            cursor = more.end()
            if explicit_sv:
                matches.extend((2, match) for match in bare_pattern.finditer(clause))
            for preference, match in matches:
                name = match["place"]
                # These official names also denote abstract concepts or a
                # foreign country. Country-level Salvadoran context alone does
                # not disambiguate a bare mention; typed matches remain valid.
                if preference == 2 and name in _BARE_AMBIGUOUS_NAMES:
                    continue
                if _excluded_context(clause, match.start(), match.end(), name):
                    continue
                # An explicit same-clause department can resolve an administrative
                # homonym. Conflicting pairs never become an arbitrary first hit.
                hints = set(typed_departments)
                if len(hints) > 1:
                    ambiguous = True
                    continue
                hint = next(iter(hints), None)
                place = resolve_municipality(name, hint)
                if place is None:
                    ambiguous = True
                    continue
                observed_departments.add(place["department"])
                if allow_municipality:
                    candidates.append((preference, index, clause_number, match.start(), {
                        "method": "source_municipality_reference", "department": place["department"],
                        "paragraph_id": paragraph["id"], "resolution": _resolution(place)}))
    if ambiguous or len(observed_departments) != 1 or not candidates:
        return None
    return min(candidates, key=lambda item: item[:4])[4]
