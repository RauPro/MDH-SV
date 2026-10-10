"""
Paquete central de esquemas y dominio de datos del Monitor de Derechos Humanos de El Salvador.
"""

from .domain import (
    ActorType,
    CertaintyLevel,
    HumanRightsIncident,
    InstitutionalSource,
    Perpetrator,
    SeverityLevel,
    SourceEvidence,
    Victim,
    WeaponType,
)
from .taxonomy import (
    TAXONOMY_REGISTRY,
    MacroCategoryEnum,
    SpecificViolationTypeEnum,
    SpecificViolationType,
    TaxonomyNode,
    ViolationCategory,
    VulnerableGroup,
    VulnerableGroupEnum,
    get_taxonomy_tree,
)
from .territory import (
    DEPARTMENT_CENTROIDS,
    DISTRICT_TO_MUNICIPALITY,
    SALVADOR_MUNICIPALITIES,
    DepartmentEnum,
    GeoCoordinates,
    get_default_coordinates,
    normalize_department,
    normalize_municipality,
)

__all__ = [
    # Territorio
    "DepartmentEnum",
    "GeoCoordinates",
    "DEPARTMENT_CENTROIDS",
    "SALVADOR_MUNICIPALITIES",
    "DISTRICT_TO_MUNICIPALITY",
    "normalize_department",
    "normalize_municipality",
    "get_default_coordinates",
    # Taxonomía 4 Ramas
    "MacroCategoryEnum",
    "SpecificViolationTypeEnum",
    "VulnerableGroupEnum",
    "ViolationCategory",
    "SpecificViolationType",
    "VulnerableGroup",
    "TaxonomyNode",
    "TAXONOMY_REGISTRY",
    "get_taxonomy_tree",
    # Dominio e Indicadores IEEE
    "SeverityLevel",
    "CertaintyLevel",
    "WeaponType",
    "InstitutionalSource",
    "ActorType",
    "Victim",
    "Perpetrator",
    "SourceEvidence",
    "HumanRightsIncident",
]
