"""Integrity guarantees for the public research dossier, not just its rendering."""
import copy
import csv
import json

import pyarrow.parquet as pq
import pytest
from pydantic import ValidationError

from mdh_pipeline.publication import Publication, export_catalog, load_catalog


def example(record_id="mdh-ruth-lopez-2025"):
    return next(r for r in load_catalog().records if r.id == record_id).model_dump(mode="json")


def test_every_public_record_has_a_source_attributed_dossier():
    for record in load_catalog().records:
        dossier = record.dossier
        assert dossier.schema_version == 1
        assert dossier.facts.status == "reported"
        assert dossier.alleged_violation.status == "reported"
        ids = {source.id for source in dossier.evidence}
        for _, field in dossier.evidence_fields():
            if field.status == "reported":
                assert field.value is not None and set(field.evidence_refs) <= ids
            else:
                assert field.value is None
        assert record.human_reviewed is False
        assert record.review_status == "source_checked"


@pytest.mark.parametrize("change", [
    {"value": None}, {"value": ""}, {"evidence_refs": []},
    {"evidence_refs": ["does-not-exist"]},
    {"evidence_refs": ["primary", "primary"]}, {"status": "confirmed"},
    {"status": "withheld", "note": "Privado"},
])
def test_invalid_or_uncited_claim_is_rejected(change):
    record = example()
    record["dossier"]["facts"].update(change)
    with pytest.raises(ValidationError):
        Publication.model_validate(record)


@pytest.mark.parametrize("path, value", [
    (("event_date", "value"), "2025-05-19"),
    (("event_date_end", "value"), "2025-05-20"),
    (("date_precision",), "unknown"),
    (("location", "department", "value"), "La Libertad"),
])
def test_dossier_does_not_contradict_compatibility_fields(path, value):
    record = example()
    target = record["dossier"]
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        Publication.model_validate(record)


def test_evidence_cannot_silently_change_source_or_reuse_an_identifier():
    record = example()
    record["dossier"]["evidence"][0]["url"] = "https://www.hrw.org/some-other-source"
    with pytest.raises(ValidationError):
        Publication.model_validate(record)
    record = example()
    record["dossier"]["evidence"].append(copy.deepcopy(record["dossier"]["evidence"][0]))
    with pytest.raises(ValidationError):
        Publication.model_validate(record)


def test_minors_are_described_without_identity_or_locality():
    ids = {"mdh-2022-001", "mdh-2022-002", "mdh-2022-003", "mdh-ninez-2022-005",
           "mdh-ninez-2023-001", "mdh-ninez-2022-006", "mdh-ninez-2022-007"}
    for record_id in ids:
        dossier = example(record_id)["dossier"]
        assert all("reservada" in person for person in dossier["affected_persons"]["value"])
        for field in ("municipality", "public_place"):
            assert dossier["location"][field]["value"] is None
            assert dossier["location"][field]["status"] == "withheld"
        assert dossier["event_time"]["value"] is None


def test_overview_report_cannot_be_given_a_precise_event_location():
    record = example("hrw-ninez-2024")
    record["dossier"]["location"]["municipality"] = {
        "value": "San Salvador", "status": "reported", "evidence_refs": ["primary"], "note": None,
    }
    with pytest.raises(ValidationError):
        Publication.model_validate(record)


def test_specific_instruments_and_updates_keep_their_provenance():
    pegasus = example("mdh-pegasus-2020-2021")["dossier"]
    assert pegasus["instrument"]["value"] == ["Programa espía Pegasus"]
    assert pegasus["reported_actors"]["value"] is None
    cecot = example("mdh-venezolanos-cecot-2025")["dossier"]
    assert cecot["instrument"]["evidence_refs"] == ["detention-context"]
    assert "Tecoluca" in cecot["location"]["public_place"]["value"]
    defender = example("mdh-alejandro-henriquez-2025")["dossier"]
    assert "supporting-1" in defender["facts"]["evidence_refs"]
    assert "HRW" in defender["hypothesis"]["value"]
    assert example("mdh-musico-custodia-2022")["dossier"]["hypothesis"]["value"] is None


def test_dossier_round_trips_all_public_export_formats(tmp_path):
    export_catalog(destination=tmp_path)
    records = json.loads((tmp_path / "catalog.json").read_text())["records"]
    expected = {r["id"]: r["dossier"] for r in records}
    with (tmp_path / "records.csv").open(encoding="utf-8-sig", newline="") as stream:
        csv_rows = list(csv.DictReader(stream))
    parquet_rows = pq.read_table(tmp_path / "records.parquet").to_pylist()
    for rows in (csv_rows, parquet_rows):
        assert {r["id"]: json.loads(r["dossier"]) for r in rows} == expected
        for row in rows:
            assert row["dossier"] == json.dumps(expected[row["id"]], ensure_ascii=False,
                                               sort_keys=True, separators=(",", ":"))
    geo = json.loads((tmp_path / "records.geojson").read_text())["features"]
    assert {f["properties"]["id"]: f["properties"]["dossier"] for f in geo} == expected
