"""Source-specific regression checks for the first official aggregate edition."""
import json
from pathlib import Path


DATA = Path(__file__).resolve().parents[1] / "data/official-statistics/homicides.json"


def dataset():
    return json.loads(DATA.read_text())


def test_department_counts_preserve_the_original_annual_universe():
    document = dataset()
    expected = {2010:4004,2011:4366,2013:2513,2014:3912,2015:6656,
                2016:5280,2017:3962,2018:3346,2019:2398,2020:1341,2021:1147}
    for period in document["periods"]:
        if period["year"] not in expected:
            continue
        assert period["national"]["value"] == expected[period["year"]]
        assert len(period["departments"]) == 14
        assert sum(item["value"] for item in period["departments"]) == period["national"]["value"]
        assert all(item["locator"] and item["status"] == "available" for item in period["departments"])
    assert sum(item["value"] is not None for period in document["periods"] for item in period["departments"]) == 154


def test_2012_uses_the_explicit_unodc_total_without_reconstructing_iml_categories():
    document = dataset()
    period = next(item for item in document["periods"] if item["year"] == 2012)
    assert period["completeness"] == "complete"
    assert period["national"]["value"] == 2594 and period["national"]["status"] == "available"
    assert "3321" in period["national"]["locator"]
    assert period["source_id"] == "unodc-cts-2026-07"
    source = next(item for item in document["sources"] if item["id"] == period["source_id"])
    assert source["format"] == "xlsx" and source["content_sha256"] == "126c1b5282f2872d5197bcd130c0d2d4adf3b820230651f402ec3c0918c423bb"
    assert period["indicator"] == "intentional_homicides" and period["unit"] == "persons"
    assert all(item["value"] is None and item["status"] == "not_available" for item in period["departments"])
    notes = " ".join(period["notes"])
    assert "2,246" in notes and "321" in notes and "281" in notes


def test_known_source_discrepancies_are_not_silently_reconciled():
    periods = {item["year"]:item for item in dataset()["periods"]}
    assert periods[2018]["months"] is None and "296" in " ".join(periods[2018]["notes"])
    assert periods[2021]["national"]["value"] == 1147
    assert "68" in " ".join(periods[2021]["notes"]) and "1,215" in " ".join(periods[2021]["notes"])
    assert periods[2022]["national"]["value"] == 496
    assert "495" in " ".join(periods[2022]["notes"])
    assert periods[2022]["indicator"] == "homicides"
    assert periods[2023]["indicator"] == "intentional_homicides"


def test_fgr_monthly_snapshot_keeps_missing_periods_and_territory_explicit():
    periods = {item["year"]:item for item in dataset()["periods"]}
    for year, total in {2022:496,2023:154,2024:114,2025:82,2026:49}.items():
        period = periods[year]
        assert period["national"]["value"] == total
        assert sum(m["value"] for m in period["months"] if m["value"] is not None) == total
        assert all(d["value"] is None and d["locator"] is None for d in period["departments"])
        assert period["unit"] == "source_reported_homicides"
    partial = periods[2026]
    assert partial["completeness"] == "partial" and partial["end_date"] == "2026-08-31"
    assert [m["status"] for m in partial["months"][8:]] == ["not_available","not_available","outside_cutoff","outside_cutoff"]
    assert all(m["value"] is None for m in partial["months"][8:])
