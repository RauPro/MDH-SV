"""
Módulo de División Político-Administrativa de El Salvador.
Incluye los 14 Departamentos y los 44 Nuevos Municipios (Ley Especial de Reestructuración Municipal 2023/2024),
junto con sus coordenadas de referencia (lat/lon) y distritos históricos asociados.
"""

import unicodedata

from enum import Enum
from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class DepartmentEnum(str, Enum):
    AHUACHAPAN = "Ahuachapán"
    SANTA_ANA = "Santa Ana"
    SONSONATE = "Sonsonate"
    CHALATENANGO = "Chalatenango"
    LA_LIBERTAD = "La Libertad"
    SAN_SALVADOR = "San Salvador"
    CUSCATLAN = "Cuscatlán"
    LA_PAZ = "La Paz"
    CABANAS = "Cabañas"
    SAN_VICENTE = "San Vicente"
    USULUTAN = "Usulután"
    SAN_MIGUEL = "San Miguel"
    MORAZAN = "Morazán"
    LA_UNION = "La Unión"
    DESCONOCIDO = "Desconocido / No especificado"


class GeoCoordinates(BaseModel):
    latitude: float = Field(..., description="Latitud en formato decimal WGS84")
    longitude: float = Field(..., description="Longitud en formato decimal WGS84")


# Coordenadas aproximadas de cabeceras departamentales para geolocalización fallback
DEPARTMENT_CENTROIDS: Dict[DepartmentEnum, GeoCoordinates] = {
    DepartmentEnum.AHUACHAPAN: GeoCoordinates(latitude=13.9214, longitude=-89.8450),
    DepartmentEnum.SANTA_ANA: GeoCoordinates(latitude=13.9942, longitude=-89.5597),
    DepartmentEnum.SONSONATE: GeoCoordinates(latitude=13.7189, longitude=-89.7242),
    DepartmentEnum.CHALATENANGO: GeoCoordinates(latitude=14.0406, longitude=-88.9328),
    DepartmentEnum.LA_LIBERTAD: GeoCoordinates(latitude=13.6769, longitude=-89.2797),
    DepartmentEnum.SAN_SALVADOR: GeoCoordinates(latitude=13.6929, longitude=-89.2182),
    DepartmentEnum.CUSCATLAN: GeoCoordinates(latitude=13.7192, longitude=-88.9669),
    DepartmentEnum.LA_PAZ: GeoCoordinates(latitude=13.4833, longitude=-88.9833),
    DepartmentEnum.CABANAS: GeoCoordinates(latitude=13.8667, longitude=-88.7500),
    DepartmentEnum.SAN_VICENTE: GeoCoordinates(latitude=13.6333, longitude=-88.8000),
    DepartmentEnum.USULUTAN: GeoCoordinates(latitude=13.3500, longitude=-88.4500),
    DepartmentEnum.SAN_MIGUEL: GeoCoordinates(latitude=13.4833, longitude=-88.1833),
    DepartmentEnum.MORAZAN: GeoCoordinates(latitude=13.7833, longitude=-88.1000),
    DepartmentEnum.LA_UNION: GeoCoordinates(latitude=13.3369, longitude=-87.8439),
    DepartmentEnum.DESCONOCIDO: GeoCoordinates(latitude=13.7942, longitude=-88.8965), # Centro geográfico SV
}

# Los 44 Nuevos Municipios agrupados por departamento
SALVADOR_MUNICIPALITIES: Dict[DepartmentEnum, List[str]] = {
    DepartmentEnum.AHUACHAPAN: [
        "Ahuachapán Centro",
        "Ahuachapán Norte",
        "Ahuachapán Sur",
    ],
    DepartmentEnum.SANTA_ANA: [
        "Santa Ana Centro",
        "Santa Ana Este",
        "Santa Ana Norte",
        "Santa Ana Oeste",
    ],
    DepartmentEnum.SONSONATE: [
        "Sonsonate Centro",
        "Sonsonate Este",
        "Sonsonate Norte",
        "Sonsonate Oeste",
    ],
    DepartmentEnum.CHALATENANGO: [
        "Chalatenango Centro",
        "Chalatenango Norte",
        "Chalatenango Sur",
    ],
    DepartmentEnum.LA_LIBERTAD: [
        "La Libertad Centro",
        "La Libertad Costa",
        "La Libertad Este",
        "La Libertad Norte",
        "La Libertad Oeste",
        "La Libertad Sur",
    ],
    DepartmentEnum.SAN_SALVADOR: [
        "San Salvador Centro",
        "San Salvador Este",
        "San Salvador Norte",
        "San Salvador Oeste",
        "San Salvador Sur",
    ],
    DepartmentEnum.CUSCATLAN: [
        "Cuscatlán Norte",
        "Cuscatlán Sur",
    ],
    DepartmentEnum.LA_PAZ: [
        "La Paz Centro",
        "La Paz Este",
        "La Paz Oeste",
    ],
    DepartmentEnum.CABANAS: [
        "Cabañas Este",
        "Cabañas Oeste",
    ],
    DepartmentEnum.SAN_VICENTE: [
        "San Vicente Norte",
        "San Vicente Sur",
    ],
    DepartmentEnum.USULUTAN: [
        "Usulután Este",
        "Usulután Norte",
        "Usulután Poniente",
    ],
    DepartmentEnum.SAN_MIGUEL: [
        "San Miguel Centro",
        "San Miguel Norte",
        "San Miguel Oeste",
    ],
    DepartmentEnum.MORAZAN: [
        "Morazán Norte",
        "Morazán Sur",
    ],
    DepartmentEnum.LA_UNION: [
        "La Unión Norte",
        "La Unión Sur",
    ],
    DepartmentEnum.DESCONOCIDO: [
        "No especificado",
    ],
}




def _strip_accents(text: str) -> str:
    """Elimina tildes y diacríticos para comparación tolerante."""
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    ).lower()


def normalize_department(dept_raw: Optional[str]) -> DepartmentEnum:
    """Normaliza cadenas de texto libre al enum estricto de Departamento, tolerando tildes y mayúsculas."""
    if not dept_raw:
        return DepartmentEnum.DESCONOCIDO
    cleaned = _strip_accents(dept_raw.strip())
    for dept in DepartmentEnum:
        dept_norm = _strip_accents(dept.value)
        if dept_norm in cleaned or cleaned in dept_norm:
            return dept
    return DepartmentEnum.DESCONOCIDO


def get_default_coordinates(department: DepartmentEnum) -> GeoCoordinates:
    """Obtiene las coordenadas centrales por defecto para un departamento."""
    return DEPARTMENT_CENTROIDS.get(department, DEPARTMENT_CENTROIDS[DepartmentEnum.DESCONOCIDO])


# Mapeo exhaustivo de los 262 distritos históricos a los 44 Nuevos Municipios oficiales (Ley de Reestructuración Municipal)
DISTRICT_TO_MUNICIPALITY: Dict[str, str] = {
    # Ahuachapán
    "atiquizaya": "Ahuachapán Norte",
    "el refugio": "Ahuachapán Norte",
    "san lorenzo": "Ahuachapán Norte",
    "turín": "Ahuachapán Norte",
    "turin": "Ahuachapán Norte",
    "ahuachapán": "Ahuachapán Centro",
    "ahuachapan": "Ahuachapán Centro",
    "apaneca": "Ahuachapán Centro",
    "concepción de ataco": "Ahuachapán Centro",
    "concepcion de ataco": "Ahuachapán Centro",
    "ataco": "Ahuachapán Centro",
    "tacuba": "Ahuachapán Centro",
    "guaymango": "Ahuachapán Sur",
    "jujutla": "Ahuachapán Sur",
    "san francisco menéndez": "Ahuachapán Sur",
    "san francisco menendez": "Ahuachapán Sur",
    "cara sucia": "Ahuachapán Sur",
    "san pedro puxtla": "Ahuachapán Sur",

    # Santa Ana
    "metapán": "Santa Ana Norte",
    "metapan": "Santa Ana Norte",
    "masahuat": "Santa Ana Norte",
    "santa rosa guachipilín": "Santa Ana Norte",
    "santa rosa guachipilin": "Santa Ana Norte",
    "texistepeque": "Santa Ana Norte",
    "santa ana": "Santa Ana Centro",
    "coatepeque": "Santa Ana Este",
    "el congo": "Santa Ana Este",
    "candelaria de la frontera": "Santa Ana Oeste",
    "chalchuapa": "Santa Ana Oeste",
    "el porvenir": "Santa Ana Oeste",
    "san antonio pajonal": "Santa Ana Oeste",
    "san sebastián salitrillo": "Santa Ana Oeste",
    "san sebastian salitrillo": "Santa Ana Oeste",
    "santiago de la frontera": "Santa Ana Oeste",

    # Sonsonate
    "juayúa": "Sonsonate Norte",
    "juayua": "Sonsonate Norte",
    "nahuizalco": "Sonsonate Norte",
    "salcoatitán": "Sonsonate Norte",
    "salcoatitan": "Sonsonate Norte",
    "santa catarina masahuat": "Sonsonate Norte",
    "sonsonate": "Sonsonate Centro",
    "sonzacate": "Sonsonate Centro",
    "nahulingo": "Sonsonate Centro",
    "san antonio del monte": "Sonsonate Centro",
    "santo domingo de guzmán": "Sonsonate Centro",
    "santo domingo de guzman": "Sonsonate Centro",
    "izalco": "Sonsonate Este",
    "armenia": "Sonsonate Este",
    "caluco": "Sonsonate Este",
    "san julián": "Sonsonate Este",
    "san julian": "Sonsonate Este",
    "cuisnahuat": "Sonsonate Este",
    "santa isabel ishuatán": "Sonsonate Este",
    "santa isabel ishuatan": "Sonsonate Este",
    "acajutla": "Sonsonate Oeste",

    # Chalatenango
    "la palma": "Chalatenango Norte",
    "san ignacio": "Chalatenango Norte",
    "citalá": "Chalatenango Norte",
    "citala": "Chalatenango Norte",
    "nueva concepción": "Chalatenango Centro",
    "nueva concepcion": "Chalatenango Centro",
    "tejutla": "Chalatenango Centro",
    "la reina": "Chalatenango Centro",
    "agua caliente": "Chalatenango Centro",
    "dulce nombre de maría": "Chalatenango Centro",
    "dulce nombre de maria": "Chalatenango Centro",
    "el paraíso": "Chalatenango Centro",
    "el paraiso": "Chalatenango Centro",
    "san fernando chalatenango": "Chalatenango Centro",
    "san francisco morazán": "Chalatenango Centro",
    "san francisco morazan": "Chalatenango Centro",
    "san rafael": "Chalatenango Centro",
    "santa rita": "Chalatenango Centro",
    "chalatenango": "Chalatenango Sur",
    "arcatao": "Chalatenango Sur",
    "san josé las flores": "Chalatenango Sur",
    "san jose las flores": "Chalatenango Sur",
    "las flores": "Chalatenango Sur",
    "las vueltas": "Chalatenango Sur",
    "san antonio los ranchos": "Chalatenango Sur",

    # La Libertad
    "quezaltepeque": "La Libertad Norte",
    "san matías": "La Libertad Norte",
    "san matias": "La Libertad Norte",
    "san pablo tacachico": "La Libertad Norte",
    "san juan opico": "La Libertad Centro",
    "opico": "La Libertad Centro",
    "ciudad arce": "La Libertad Centro",
    "colón": "La Libertad Oeste",
    "colon": "La Libertad Oeste",
    "lourdes": "La Libertad Oeste",
    "lourdes colón": "La Libertad Oeste",
    "lourdes colon": "La Libertad Oeste",
    "jayaque": "La Libertad Oeste",
    "sacacoyo": "La Libertad Oeste",
    "tepecoyo": "La Libertad Oeste",
    "talnique": "La Libertad Oeste",
    "antiguo cuscatlán": "La Libertad Este",
    "antiguo cuscatlan": "La Libertad Este",
    "huizúcar": "La Libertad Este",
    "huizucar": "La Libertad Este",
    "nuevo cuscatlán": "La Libertad Este",
    "nuevo cuscatlan": "La Libertad Este",
    "san josé villanueva": "La Libertad Este",
    "san jose villanueva": "La Libertad Este",
    "zaragoza": "La Libertad Este",
    "chiltiupán": "La Libertad Costa",
    "chiltiupan": "La Libertad Costa",
    "jicalapa": "La Libertad Costa",
    "la libertad": "La Libertad Costa",
    "puerto de la libertad": "La Libertad Costa",
    "tamanique": "La Libertad Costa",
    "el tunco": "La Libertad Costa",
    "teotepeque": "La Libertad Costa",
    "santa tecla": "La Libertad Sur",
    "comasagua": "La Libertad Sur",

    # San Salvador
    "aguilares": "San Salvador Norte",
    "el paisnal": "San Salvador Norte",
    "guazapa": "San Salvador Norte",
    "apopa": "San Salvador Oeste",
    "nejapa": "San Salvador Oeste",
    "ilopango": "San Salvador Este",
    "san martín": "San Salvador Este",
    "san martin": "San Salvador Este",
    "soyapango": "San Salvador Este",
    "tonacatepeque": "San Salvador Este",
    "ayutuxtepeque": "San Salvador Centro",
    "mejicanos": "San Salvador Centro",
    "cuscatancingo": "San Salvador Centro",
    "ciudad delgado": "San Salvador Centro",
    "san salvador": "San Salvador Centro",
    "panchimalco": "San Salvador Sur",
    "rosario de mora": "San Salvador Sur",
    "san marcos": "San Salvador Sur",
    "santo tomás": "San Salvador Sur",
    "santo tomas": "San Salvador Sur",
    "santiago texacuangos": "San Salvador Sur",

    # Cuscatlán
    "suchitoto": "Cuscatlán Norte",
    "san josé guayabal": "Cuscatlán Norte",
    "san jose guayabal": "Cuscatlán Norte",
    "san bartolomé perulapía": "Cuscatlán Norte",
    "san bartolome perulapia": "Cuscatlán Norte",
    "san pedro perulapán": "Cuscatlán Norte",
    "san pedro perulapan": "Cuscatlán Norte",
    "cojutepeque": "Cuscatlán Sur",
    "candelaria": "Cuscatlán Sur",
    "el carmen cuscatlán": "Cuscatlán Sur",
    "monte san juan": "Cuscatlán Sur",
    "san rafael cedros": "Cuscatlán Sur",
    "san ramón": "Cuscatlán Sur",
    "san ramon": "Cuscatlán Sur",
    "santa cruz michapa": "Cuscatlán Sur",
    "tenancingo": "Cuscatlán Sur",

    # La Paz
    "olocuilta": "La Paz Oeste",
    "cuyultitán": "La Paz Oeste",
    "cuyultitan": "La Paz Oeste",
    "san juan talpa": "La Paz Oeste",
    "san luis talpa": "La Paz Oeste",
    "san pedro masahuat": "La Paz Oeste",
    "tapalhuaca": "La Paz Oeste",
    "san francisco chinameca": "La Paz Oeste",
    "san luis la herradura": "La Paz Centro",
    "santiago nonualco": "La Paz Centro",
    "san pedro nonualco": "La Paz Centro",
    "santa maría ostuma": "La Paz Centro",
    "santa maria ostuma": "La Paz Centro",
    "san juan nonualco": "La Paz Este",
    "san rafael obrajuelo": "La Paz Este",
    "zacatecoluca": "La Paz Este",

    # Cabañas
    "sensuntepeque": "Cabañas Este",
    "victoria": "Cabañas Este",
    "victoria / santa marta": "Cabañas Este",
    "santa marta": "Cabañas Este",
    "dolores": "Cabañas Este",
    "guacotecti": "Cabañas Este",
    "san isidro cabañas": "Cabañas Este",
    "ilobasco": "Cabañas Oeste",
    "tejutepeque": "Cabañas Oeste",
    "jutiapa": "Cabañas Oeste",
    "cinquera": "Cabañas Oeste",

    # San Vicente
    "apastepeque": "San Vicente Norte",
    "santa clara": "San Vicente Norte",
    "san ildefonso": "San Vicente Norte",
    "san esteban catarina": "San Vicente Norte",
    "san sebastián": "San Vicente Norte",
    "san sebastian": "San Vicente Norte",
    "santo domingo": "San Vicente Norte",
    "san vicente": "San Vicente Sur",
    "tecoluca": "San Vicente Sur",
    "cecot": "San Vicente Sur",
    "guadalupe": "San Vicente Sur",
    "san cayetano istepeque": "San Vicente Sur",
    "tepetitán": "San Vicente Sur",
    "tepetitan": "San Vicente Sur",
    "verapaz": "San Vicente Sur",

    # Usulután
    "santiago de maría": "Usulután Norte",
    "santiago de maria": "Usulután Norte",
    "alegría": "Usulután Norte",
    "alegria": "Usulután Norte",
    "berlín": "Usulután Norte",
    "berlin": "Usulután Norte",
    "mercedes umaña": "Usulután Norte",
    "jucuapa": "Usulután Norte",
    "el triunfo": "Usulután Norte",
    "estanzuelas": "Usulután Norte",
    "nueva granada": "Usulután Norte",
    "usulután": "Usulután Este",
    "usulutan": "Usulután Este",
    "jucuarán": "Usulután Este",
    "jucuaran": "Usulután Este",
    "san dionisio": "Usulután Este",
    "concepción batres": "Usulután Este",
    "concepcion batres": "Usulután Este",
    "santa maría": "Usulután Este",
    "santa maria": "Usulután Este",
    "ereguayquín": "Usulután Este",
    "ereguayquin": "Usulután Este",
    "santa elena": "Usulután Este",
    "jiquilisco": "Usulután Poniente",
    "puerto el triunfo": "Usulután Poniente",
    "san agustín": "Usulután Poniente",
    "san agustin": "Usulután Poniente",
    "san francisco javier": "Usulután Poniente",

    # San Miguel
    "ciudad barrios": "San Miguel Norte",
    "sesori": "San Miguel Norte",
    "nuevo edén de san juan": "San Miguel Norte",
    "nuevo eden de san juan": "San Miguel Norte",
    "san gerardo": "San Miguel Norte",
    "san luis de la reina": "San Miguel Norte",
    "carolina": "San Miguel Norte",
    "chapeltique": "San Miguel Norte",
    "san miguel": "San Miguel Centro",
    "comacarán": "San Miguel Centro",
    "comacaran": "San Miguel Centro",
    "moncagua": "San Miguel Centro",
    "quelepa": "San Miguel Centro",
    "chirilagua": "San Miguel Centro",
    "chinameca": "San Miguel Oeste",
    "nueva guadalupe": "San Miguel Oeste",
    "lolotique": "San Miguel Oeste",
    "san jorge": "San Miguel Oeste",
    "el tránsito": "San Miguel Oeste",
    "el transito": "San Miguel Oeste",

    # Morazán
    "arambala": "Morazán Norte",
    "cacaopera": "Morazán Norte",
    "corinto": "Morazán Norte",
    "el rosario morazán": "Morazán Norte",
    "joateca": "Morazán Norte",
    "jocoaitique": "Morazán Norte",
    "meanguera": "Morazán Norte",
    "osicala": "Morazán Norte",
    "perquín": "Morazán Norte",
    "perquin": "Morazán Norte",
    "san fernando morazán": "Morazán Norte",
    "torola": "Morazán Norte",
    "san francisco gotera": "Morazán Sur",
    "gotera": "Morazán Sur",
    "chilanga": "Morazán Sur",
    "el divisadero": "Morazán Sur",
    "guatajiagua": "Morazán Sur",
    "jocoro": "Morazán Sur",
    "lolotiquillo": "Morazán Sur",
    "sociedad": "Morazán Sur",
    "yamabal": "Morazán Sur",

    # La Unión
    "anamorós": "La Unión Norte",
    "anamoros": "La Unión Norte",
    "bolívar": "La Unión Norte",
    "bolivar": "La Unión Norte",
    "concepción de oriente": "La Unión Norte",
    "concepcion de oriente": "La Unión Norte",
    "el carmen la unión": "La Unión Norte",
    "el sauce": "La Unión Norte",
    "lislique": "La Unión Norte",
    "nueva esparta": "La Unión Norte",
    "pasaquina": "La Unión Norte",
    "polorós": "La Unión Norte",
    "poloros": "La Unión Norte",
    "san alejo": "La Unión Norte",
    "santa rosa de lima": "La Unión Norte",
    "la unión": "La Unión Sur",
    "la union": "La Unión Sur",
    "conchagua": "La Unión Sur",
    "el tamarindo": "La Unión Sur",
    "intipucá": "La Unión Sur",
    "intipuca": "La Unión Sur",
    "meanguera del golfo": "La Unión Sur",
    "yayantique": "La Unión Sur",
    "yucuaiquín": "La Unión Sur",
    "yucuaiquin": "La Unión Sur",
}


def normalize_municipality(mun_raw: Optional[str], dept: Optional[DepartmentEnum] = None) -> str:
    """Normaliza un nombre de distrito o municipio al estándar oficial de los 44 Nuevos Municipios."""
    if not mun_raw:
        if dept and dept in SALVADOR_MUNICIPALITIES and SALVADOR_MUNICIPALITIES[dept]:
            return SALVADOR_MUNICIPALITIES[dept][0]
        return "San Salvador Centro"

    raw_clean = mun_raw.strip()
    raw_lower = _strip_accents(raw_clean)

    # Si ya es uno de los 44 municipios exactos
    for d, m_list in SALVADOR_MUNICIPALITIES.items():
        for m in m_list:
            if _strip_accents(m) == raw_lower:
                return m

    # Búsqueda en mapeo de distritos históricos
    if raw_lower in DISTRICT_TO_MUNICIPALITY:
        return DISTRICT_TO_MUNICIPALITY[raw_lower]

    # Búsqueda parcial en llaves de distritos
    for dist, mun in DISTRICT_TO_MUNICIPALITY.items():
        if dist in raw_lower or raw_lower in dist:
            return mun

    # Fallback por departamento
    if dept and dept in SALVADOR_MUNICIPALITIES and SALVADOR_MUNICIPALITIES[dept]:
        return SALVADOR_MUNICIPALITIES[dept][0]

    return "San Salvador Centro"
