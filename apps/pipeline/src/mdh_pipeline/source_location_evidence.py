"""Conservative administrative mentions from a verified article, without a model.

Callers must separately establish current Salvadoran human-rights relevance and
bind the source hash to that selection. This helper does not infer an incident's
location, inspect titles/bylines, geocode addresses or change any model evidence.
Only supported administrative names and paragraph identifiers leave this module.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import re

from mdh_schema import DepartmentEnum

from .municipalities import _entries, resolve_municipality, REFERENCE_VERSION
from .source_urls import normalize

SOURCE_LOCATION_RULE_VERSION = 2
_DEPARTMENTS = {normalize(value.value): value.value for value in DepartmentEnum
                if value is not DepartmentEnum.DESCONOCIDO}
_FOREIGN = re.compile(
    r"\b(?:guatemala|honduras|nicaragua|costa rica|panama|mexico|estados unidos|ee\.?\s?uu\.?|"
    r"california|texas|florida|nueva york|canada|cuba|haiti|republica dominicana|puerto rico|"
    r"colombia|venezuela|ecuador|peru|bolivia|brasil|argentina|chile|uruguay|paraguay|"
    r"espana|portugal|francia|italia|alemania|reino unido|inglaterra|rusia|ucrania|"
    r"israel|palestina|china|taiwan|japon|corea|australia|afganistan|iran|irak|siria|libano|filipinas)\b")
_SALVADORAN = re.compile(r"\bel salvador\b")
_ROLE_ONLY = re.compile(r"\b(?:sala de redaccion|redaccion|sede|oficinas?|corresponsal|"
                        r"carretera|autopista|ruta|tribunal|juzgado)\b")
_ORIGIN_RESIDENCE = re.compile(
    r"\b(?:nacio|nacido|nacida|originari[oa]|oriund[oa]|reside|residia|residentes?|"
    r"residencia|domicilio|domiciliad[oa]|vive|vivia|vecin[oa]|natal|natural de|nativ[oa])\b[^,;.!?]{0,80}$")
_DIRECTION = re.compile(r"^\s+(?:norte|sur|este|oeste|centro|oriental|occidental)\b")
_BARE_AMBIGUOUS_NAMES = {"la union", "la libertad", "victoria", "el triunfo", "armenia"}
_ADMINISTRATIVE_CONNECTOR = re.compile(
    r"\s*,?\s*(?:(?:ubicad[oa]|situad[oa]|localizad[oa])\s+)?"
    r"(?:(?:en|de|del)\s+(?:el\s+)?)?"
    r"(?:(?:norte|sur|este|oeste|centro)\s+del\s+)?")


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
        re.compile(rf"\b(?P<kind>departamentos?)\s+(?:de|como)\s+(?P<place>{departments})\b"),
        re.compile(rf"\b(?P<kind>municipios?|distritos?|localidades|localidad|ciudades|ciudad|municipalidad(?:es)?|alcaldias?(?:\s+municipal(?:es)?)?)\s+(?:de\s+)?(?P<place>{alternatives})\b"),
        re.compile(rf"\ben\s+(?P<place>{alternatives})\b"),
        re.compile(rf"\s*(?:,|\by\b|\be\b)\s*(?:de\s+)?(?P<place>{alternatives})\b"),
        re.compile(rf"\s*(?:,|\by\b|\be\b)\s*(?:de\s+)?(?P<place>{departments})\b"),
        re.compile(rf"^(?:(?:municipio|distrito|ciudad|localidad|departamento)\s+(?:de\s+)?)?"
                   rf"(?:{alternatives}|{departments})(?:\s*\([^)]{{0,40}}\))?\s*(?:[:—–-]|,\s*\d{{1,2}}\b)"),
        re.compile(rf"\b(?P<place>{alternatives})\b"),
    )


@lru_cache(maxsize=1)
def _labelled_list_patterns():
    places = sorted({normalize(place["name"]) for place in _entries()}, key=lambda name: (-len(name), name))
    place_names = "(?:" + "|".join(re.escape(name) for name in places) + ")"
    department_names = "(?:" + "|".join(re.escape(name) for name in sorted(_DEPARTMENTS, key=lambda name: (-len(name), name))) + ")"
    predicate = r"[^.;!?:]{0,160}\b(?:son|han\s+sido|fueron|estan)\s*:?\s*"
    return (
        re.compile(rf"\b(?:los\s+)?departamentos\b{predicate}(?P<place>{department_names})\b"),
        re.compile(rf"\b(?:las\s+)?municipalidades\b{predicate}(?P<place>{place_names})\b"),
        re.compile(rf"\s*,\s*(?:departamento\s+de\s+)?(?P<place>{department_names})\b"),
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
    # A comma in an address does not turn a personal residence/origin into a
    # public event location when the following municipality is in apposition.
    if re.search(r"\b(?:nacio|nacid[oa]|originari[oa]|oriund[oa]|reside|residia|residente|vive|vivia|domicilio)\b"
                 r"[^.;!?]{0,120},\s*$", clause[:start]):
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
    qualified = rf"(?:(?:el|la|los|las)\s+)?(?:(?:municipios?|distritos?|ciudad(?:es)?|localidad(?:es)?|departamentos?)\s+(?:(?:de|como)\s+)?)?{escaped}\b"
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
    if re.fullmatch(r"\s*en el salvador,\s*(?:(?:el|la|los|las)\s+)?", prefix):
        return True
    if re.search(r"\b(?:en|de|del|al|a|sobre|para|desde)\s+(?:(?:el|la|los|las)\s+)?(?:populoso\s+)?$", prefix):
        return True
    # Explicit administrative apposition, e.g. a canton followed by its
    # municipality. Unknown address words are not themselves geocoded.
    if re.search(r"\ben\s+(?:(?:el|la)\s+)?(?:canton|caserio|colonia|barrio|comunidad|sector)\b"
                 r"[^.;!?]{1,100},\s*$", prefix):
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


def _labelled_list_excluded(clause, match):
    heading = clause[match.start():match.start("place")]
    # Territorial lists differ from company divisions and lists of people's
    # origin/residence. A shared proper name does not erase those roles.
    return bool(re.search(r"\b(?:de|del|en)\s+(?:(?:la|el)\s+)?(?:empresa|compania|universidad|ministerio|instituto|hospital|organizacion|oficina|corporacion)\b", heading)
                or re.search(r"\b(?:origen|residencia|domicilio|nacimiento)\b", heading)
                or re.search(r"\b(?:no|nunca)\s+(?:son|han\s+sido|fueron|estan)\s*:?\s*$", clause[:match.start("place")]))


def _narrative_location_connector(connector):
    # A separate event predicate does not assert administrative containment of
    # an earlier city/person name. Its intervening prose remains unsupported;
    # do not silently delete it to obtain a clean geographical proof.
    return bool(re.search(r"\b(?:perpetrad[oa]s?|ocurrid[oa]s?|sucedid[oa]s?|registrad[oa]s?)\b", connector))


def _unresolved_narrative_department_link(clause):
    """Quarantine this clause without inventing a contradictory territorial pair."""
    departments, _, _, _, _, _, names = _patterns()
    for department in departments.finditer(clause):
        prefix = clause[:department.start()]
        for locality in reversed(list(names.finditer(prefix))):
            connector = prefix[locality.end():]
            if len(connector) > 100:
                break
            if re.match(r"^\s*,", connector) and _narrative_location_connector(connector):
                return True
    return False


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
        # Adjacency describes a relationship between territories, not an
        # assertion that the preceding municipality belongs to that department.
        if re.fullmatch(r"\s*,?\s*(?:que\s+)?(?:colinda|limita)\s+con\s+(?:el\s+)?", connector):
            return False
        if _narrative_location_connector(connector):
            return False
        if not _ADMINISTRATIVE_CONNECTOR.fullmatch(connector):
            return True
        department = _DEPARTMENTS[department_match["place"]]
        return resolve_municipality(locality["place"], department) is None
    return False


def _clause_has_administrative_conflict(clause):
    department_pattern, municipality_pattern, _, _, _, _, names = _patterns()
    _, _, department_suffix = _labelled_list_patterns()
    if any(_department_chain_conflict(clause, match, names) for match in department_pattern.finditer(clause)):
        return True
    for match in municipality_pattern.finditer(clause):
        if match["kind"] not in {"municipio", "distrito", "localidad", "ciudad", "municipalidad", "alcaldia", "alcaldia municipal"}:
            continue
        suffix = department_suffix.match(clause, match.end())
        if suffix and resolve_municipality(match["place"], _DEPARTMENTS[suffix["place"]]) is None:
            return True
    return False


def has_administrative_conflict(article):
    """Detect contradictory/damaged administrative chains, not mere plurality.

    An unresolved homonym without a department claim is uncertainty, not a
    contradictory pair. Callers must still validate the article independently.
    """
    paragraphs = _valid_paragraphs(article)
    if paragraphs is None:
        return False
    return any(_clause_has_administrative_conflict(clause)
               for paragraph in paragraphs for clause in re.split(r"[.!?;]+", _clean(paragraph["text"])))


def _paragraph_has_bound_administrative_pair(paragraph):
    """A local exact pair can distinguish a foreign comparison elsewhere.

    The caller still requires Salvadoran article context and no foreign country
    in this paragraph. Personal origins and institutional roles remain excluded.
    """
    departments, _, _, _, _, _, names = _patterns()
    for clause in re.split(r"[.!?;]+", paragraph):
        if (_unresolved_narrative_department_link(clause)
                or _clause_has_administrative_conflict(clause)):
            continue
        for department in departments.finditer(clause):
            prefix = clause[:department.start()]
            for locality in reversed(list(names.finditer(prefix))):
                connector = prefix[locality.end():]
                if len(connector) > 100:
                    break
                if not _ADMINISTRATIVE_CONNECTOR.fullmatch(connector):
                    continue
                if (resolve_municipality(locality["place"], _DEPARTMENTS[department["place"]]) is None
                        or _excluded_context(clause, locality.start(), locality.end(), locality["place"])
                        or _excluded_context(clause, department.start(), department.end(), department["place"])):
                    continue
                before_place = clause[:locality.start()]
                if (_typed_context(clause, department.start(), department.end())
                        or re.search(r"\ben\s+(?:(?:el|la)\s+)?(?:(?:municipio|distrito|ciudad|localidad)\s+de\s+)?$", before_place)):
                    return True
    return False


def extract_administrative_mentions(article, *, allow_municipality=True):
    """Return distinct supported departments without selecting a primary location.

    Typed unique Salvadoran administrative names can establish a mention without
    an explicit country. Bare 'en X' additionally requires literal El Salvador;
    a person's Salvadoran nationality does not locate that person in the country.
    Foreign context requires explicit local Salvadoran geographic evidence. These
    intentionally conservative rules cannot establish where a violation occurred.
    """
    if type(allow_municipality) is not bool:
        return []
    paragraphs = _valid_paragraphs(article)
    if paragraphs is None:
        return []
    country_context = _clean(article["text"])
    has_foreign_context = bool(_FOREIGN.search(country_context))
    explicit_sv = bool(_SALVADORAN.search(country_context))
    department_pattern, municipality_pattern, bare_pattern, place_tail, department_tail, dateline, names = _patterns()
    department_list, municipal_list, department_suffix = _labelled_list_patterns()
    candidates, ambiguous, observed_departments = [], False, set()
    for index, paragraph in enumerate(paragraphs, 1):
        normalized = _clean(paragraph["text"])
        # A comparison abroad must not suppress an independently located
        # Salvadoran paragraph. Conversely, nationality or an article-level
        # country name cannot transfer a foreign town into El Salvador.
        if has_foreign_context and (_FOREIGN.search(normalized)
                or not (re.search(r"\b(?:en|dentro de)\s+el salvador\b", normalized)
                        or (explicit_sv and _paragraph_has_bound_administrative_pair(normalized)))):
            continue
        # A place followed by a colon/dash at paragraph start is a dateline,
        # including a typed administrative label. Never use its trailing prose.
        if dateline.match(normalized):
            continue
        for clause_number, clause in enumerate(re.split(r"[.!?;]+", normalized)):
            if _unresolved_narrative_department_link(clause):
                continue
            if _clause_has_administrative_conflict(clause):
                ambiguous = True
                continue
            typed_departments = []
            for match in department_list.finditer(clause):
                if (_excluded_context(clause, match.start("place"), match.end(), match["place"])
                        or _labelled_list_excluded(clause, match)):
                    continue
                listed, cursor = [match], match.end()
                while more := department_tail.match(clause, cursor):
                    listed.append(more)
                    cursor = more.end()
                for mention in listed:
                    department = _DEPARTMENTS[mention["place"]]
                    observed_departments.add(department)
                    typed_departments.append(department)
                    candidates.append((0, index, clause_number, mention.start(), {
                        "method": "source_explicit_department", "department": department,
                        "paragraph_id": paragraph["id"]}))
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
            for match in municipal_list.finditer(clause):
                if (not _excluded_context(clause, match.start("place"), match.end(), match["place"])
                        and not _labelled_list_excluded(clause, match)):
                    matches.append((1, match))
                    cursor = match.end()
                    while more := place_tail.match(clause, cursor):
                        matches.append((1, more))
                        cursor = more.end()
            for match in municipality_pattern.finditer(clause):
                if (_typed_context(clause, match.start(), match.end())
                        and not _excluded_context(clause, match.start(), match.end(), match["place"])):
                    matches.append((1, match))
                    if match["kind"] in {"municipios", "distritos", "localidades", "ciudades", "municipalidades", "alcaldias", "alcaldias municipales"}:
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
                # A department elsewhere in the same clause does not establish
                # containment. Only an adjacent administrative suffix can
                # disambiguate a municipality; otherwise resolve it on its own.
                hint = None
                kind = match.groupdict().get("kind")
                if preference == 2 or kind in {"municipio", "distrito", "localidad", "ciudad", "municipalidad", "alcaldia", "alcaldia municipal"}:
                    suffix = department_suffix.match(clause, match.end())
                    if suffix:
                        declared = _DEPARTMENTS[suffix["place"]]
                        if hint is not None and hint != declared:
                            ambiguous = True
                            continue
                        hint = declared
                place = resolve_municipality(name, hint)
                if place is None:
                    ambiguous = True
                    continue
                observed_departments.add(place["department"])
                if allow_municipality:
                    candidates.append((preference, index, clause_number, match.start(), {
                        "method": "source_municipality_reference", "department": place["department"],
                        "paragraph_id": paragraph["id"], "resolution": _resolution(place)}))
    if ambiguous or not candidates:
        return []
    best = {}
    for candidate in sorted(candidates, key=lambda item: item[:4]):
        best.setdefault(candidate[4]["department"], candidate[4])
    return [best[department] for department in sorted(best)]


def extract_administrative_mention(article, *, allow_municipality=True):
    """Compatibility helper: never choose one of several supported departments."""
    mentions = extract_administrative_mentions(article, allow_municipality=allow_municipality)
    return mentions[0] if len(mentions) == 1 else None
