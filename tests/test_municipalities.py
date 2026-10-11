"""Official municipality coverage and conservative ambiguity handling."""
import json
from collections import Counter
from importlib.resources import files

import pytest

from mdh_pipeline.municipalities import (
    REFERENCE_SOURCE_URL, REFERENCE_VERSION, resolve_municipality,
)
from mdh_schema import DepartmentEnum


def test_official_reference_has_all_262_historical_and_44_current_names():
    document = json.loads(files("mdh_pipeline").joinpath(
        "data", "el_salvador_municipalities.json"
    ).read_text())
    assert document["reference_version"] == REFERENCE_VERSION
    assert document["source"]["url"] == REFERENCE_SOURCE_URL
    rows = document["municipalities"]
    assert len(rows) == 44
    assert len({row["name"] for row in rows}) == 44
    assert sum(len(row["districts"]) for row in rows) == 262
    assert {row["department"] for row in rows} == {
        dep.value for dep in DepartmentEnum if dep.name != "DESCONOCIDO"
    }
    assert Counter({department: sum(len(row["districts"]) for row in rows
                                   if row["department"] == department)
                    for department in {row["department"] for row in rows}}) == {
        "Ahuachapán": 12, "Santa Ana": 13, "Sonsonate": 16, "Chalatenango": 33,
        "La Libertad": 22, "San Salvador": 19, "Cuscatlán": 16, "La Paz": 22,
        "Cabañas": 9, "San Vicente": 13, "Usulután": 23, "San Miguel": 20,
        "Morazán": 26, "La Unión": 18,
    }
    for row in rows:
        result = resolve_municipality(row["name"])
        assert result["department"] == row["department"]
        assert result["kind"] == "municipality"
        for district in row["districts"]:
            result = resolve_municipality(district["name"], row["department"])
            assert result["kind"] == "district"
            assert result["current_municipality"] == row["name"]
            assert result["source_url"] == f"{REFERENCE_SOURCE_URL}#page={district['source_page']}"


@pytest.mark.parametrize(("name", "department", "parent"), [
    ("Soyapango", "San Salvador", "San Salvador Este"),
    ("DISTRITO DE   ANTIGUO CUSCATLAN", "La Libertad", "La Libertad Este"),
    ("Municipio de Zacatecoluca", "La Paz", "La Paz Este"),
    ("Distrito municipal de Juayúa", "Sonsonate", "Sonsonate Norte"),
    ("San Luis La Herradura", "La Paz", "La Paz Centro"),
    ("Ciudad Arce", "La Libertad", "La Libertad Centro"),
    ("Santa Ana Oeste", "Santa Ana", "Santa Ana Oeste"),
])
def test_normalizes_only_official_name_spelling(name, department, parent):
    result = resolve_municipality(name)
    assert result["department"] == department
    assert result["current_municipality"] == parent
    assert result["reference_version"] == REFERENCE_VERSION


@pytest.mark.parametrize(("name", "departments"), [
    ("San Fernando", ["Chalatenango", "Morazán"]),
    ("San Isidro", ["Cabañas", "Morazán"]),
    ("San Lorenzo", ["Ahuachapán", "San Vicente"]),
    ("El Carmen", ["Cuscatlán", "La Unión"]),
    ("El Rosario", ["Cuscatlán", "La Paz", "Morazán"]),
])
def test_homonyms_require_matching_independent_department(name, departments):
    assert resolve_municipality(name) is None
    for department in departments:
        assert resolve_municipality(name, department)["department"] == department
    assert resolve_municipality(name, "San Salvador") is None


@pytest.mark.parametrize("name", [
    "", None, 1, "x" * 201, "Soyapngo", "Soyapango Centro", "San Juan",
    "La casa de Soyapango", "colonia Soyapango", "Soyapango, San Salvador",
    "municipio de municipio de Soyapango", "Opico", "Lourdes", "Cuscatlán",
])
def test_unknown_addresses_partial_names_and_uncited_aliases_are_not_guessed(name):
    assert resolve_municipality(name) is None


@pytest.mark.parametrize("hint", ["La Paz", "", "No especificado", "Guatemala", 123])
def test_invalid_or_conflicting_hint_never_falls_back_to_unique_name(hint):
    assert resolve_municipality("Soyapango", hint) is None


def test_department_hint_normalization_and_result_mutation_are_safe():
    result = resolve_municipality("San Isidro", "Departamento de CABANAS")
    assert result["department"] == "Cabañas"
    result["department"] = "La Paz"
    assert resolve_municipality("San Isidro", "Cabañas")["department"] == "Cabañas"
