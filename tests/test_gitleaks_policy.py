"""The reviewed editorial IDs cannot suppress credentials or unrelated content."""

import json
import os
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / ".security/gitleaks.toml"
CATALOG_PATHS = {
    "data/reviewed/publications.json",
    "apps/web/public/data/catalog.json",
    "apps/web/public/data/records.geojson",
    "tests/fixtures/catalog.json",
    "apps/web/src/__fixtures__/catalog.json",
}


def reviewed_ids():
    config = tomllib.loads(CONFIG_PATH.read_text())
    generic = next(rule for rule in config["rules"] if rule["id"] == "generic-api-key")
    return [expression.removeprefix('^source_record_key["]: ["]').removesuffix('["]$')
            for expression in generic["allowlists"][0]["regexes"]]


def test_policy_extends_defaults_and_limits_exceptions_to_exact_reviewed_fields_and_paths():
    config = tomllib.loads(CONFIG_PATH.read_text())
    assert config["extend"] == {"useDefault": True}
    assert "allowlist" not in config and "allowlists" not in config
    generic = next(rule for rule in config["rules"] if rule["id"] == "generic-api-key")
    # Omitting detection attributes preserves the entire built-in rule.
    assert set(generic) == {"id", "allowlists"}
    assert len(generic["allowlists"]) == 1
    exception = generic["allowlists"][0]
    assert set(exception) == {"description", "condition", "regexTarget", "paths", "regexes"}
    assert exception["condition"] == "AND" and exception["regexTarget"] == "match"
    assert set(exception["paths"]) == {"^" + re.escape(path) + "$" for path in CATALOG_PATHS}
    identifiers = reviewed_ids()
    assert len(set(identifiers)) == 11
    canonical = json.loads((ROOT / "data/reviewed/publications.json").read_text())
    assert set(identifiers) <= {record["source_record_key"] for record in canonical["records"]}
    assert all(re.fullmatch(r"[a-z]+(?:-[a-z]+)+-20[0-9]{2}", item) for item in identifiers)
    assert exception["regexes"] == [
        '^source_record_key["]: ["]' + item + '["]$' for item in identifiers
    ]
    assert all("allowlists" not in rule and "allowlist" not in rule
               for rule in config["rules"] if rule["id"] != "generic-api-key")


@pytest.fixture
def engine_binary():
    binary = os.environ.get("GITLEAKS_BIN")
    if not binary:
        pytest.skip("Set GITLEAKS_BIN to the pinned engine to run integration cases")
    result = subprocess.run([binary, "version"], capture_output=True, check=True)
    assert result.stdout.decode().strip() == "8.30.1"
    return binary


def scan_files(tmp_path, engine_binary, files):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("GIT_", "GITLEAKS_"))}
    env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})

    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                              check=True, env=env)

    git("init", "--quiet", "-b", "main")
    git("config", "user.name", "Policy fixture")
    git("config", "user.email", "policy@example.invalid")
    for name, content in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    git("add", "--all")
    git("commit", "--quiet", "-m", "policy fixture")
    report = tmp_path / "report.json"
    ignore = tmp_path / "empty.ignore"
    ignore.touch()
    result = subprocess.run([
        engine_binary, "git", str(repo), "--config", str(CONFIG_PATH),
        "--gitleaks-ignore-path", str(ignore), "--ignore-gitleaks-allow", "--redact=100",
        "--no-banner", "--no-color", "--log-level=error", "--exit-code=7",
        "--report-format=json", "--report-path", str(report),
        "--log-opts=--full-history --root -m --text --no-ext-diff --no-textconv HEAD --",
    ], capture_output=True, check=False, env=env)
    assert result.returncode in {0, 7}
    findings = json.loads(report.read_text())
    assert bool(findings) == (result.returncode == 7)
    return findings


def json_field(key, value):
    return json.dumps({key: value}) + "\n"


def test_exact_reviewed_ids_are_allowed_only_in_catalog_fields(tmp_path, engine_binary):
    content = "".join(json_field("source_record_key", item) for item in reviewed_ids())
    assert not scan_files(tmp_path, engine_binary, {path: content for path in CATALOG_PATHS})


@pytest.mark.parametrize("path,field", [
    ("data/reviewed/publications.json", "api_key"),
    ("unrelated.json", "source_record_key"),
    ("tests/other_fixture.json", "source_record_key"),
])
def test_known_id_in_another_field_or_path_still_uses_default_detector(
    tmp_path, engine_binary, path, field
):
    findings = scan_files(tmp_path, engine_binary, {path: json_field(field, reviewed_ids()[0])})
    assert any(finding["RuleID"] == "generic-api-key" for finding in findings)


def test_new_editorial_id_requires_its_own_review(tmp_path, engine_binary):
    identifier = reviewed_ids()[0].removesuffix("2023") + "2028"
    findings = scan_files(tmp_path, engine_binary, {
        "data/reviewed/publications.json": json_field("source_record_key", identifier),
    })
    assert any(finding["RuleID"] == "generic-api-key" for finding in findings)


def test_google_key_in_an_allowed_field_and_path_is_detected(tmp_path, engine_binary):
    synthetic = "AI" + "za" + "aB7_" * 8 + "xY9"
    findings = scan_files(tmp_path, engine_binary, {
        "data/reviewed/publications.json": json_field("source_record_key", synthetic),
    })
    assert any(finding["RuleID"] == "mdh-google-api-key" for finding in findings)


def test_allowed_field_does_not_suppress_other_matches_on_same_line(tmp_path, engine_binary):
    synthetic = "r5Qz" + "9B6y" + "W8d2" + "hF3n" + "K4x7" + "L1uT"
    content = json.dumps({"source_record_key": reviewed_ids()[0], "api_key": synthetic}) + "\n"
    findings = scan_files(tmp_path, engine_binary, {"data/reviewed/publications.json": content})
    assert any(finding["RuleID"] == "generic-api-key" for finding in findings)


def test_policy_literals_do_not_need_an_exception_for_the_policy_file(tmp_path, engine_binary):
    assert not scan_files(tmp_path, engine_binary, {".security/gitleaks.toml": CONFIG_PATH.read_text()})
