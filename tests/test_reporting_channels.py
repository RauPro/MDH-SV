"""Public forms preserve automation identifiers and route sensitive reports away."""
from pathlib import Path

import yaml
from mdh_pipeline.news_requests import candidate_from_request, public_rechecks

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / ".github/ISSUE_TEMPLATE"
EDITORIAL = "mailto:raul.guillen.eng@proton.me"
TECHNICAL = "https://github.com/RauPro/MDH-SV/security/advisories/new"
IDENTIFIER = "a" * 64


def form(name):
    return yaml.safe_load((TEMPLATES / name).read_text())


def submitted_issue(template, values):
    # GitHub renders input labels as headings; the worker must accept the real
    # template's heading, not a duplicate hard-coded fixture heading.
    sections = []
    for field in template["body"]:
        if field["type"] == "markdown":
            continue
        label = field["attributes"]["label"]
        sections.append(f"### {label}\n\n{values.get(field['id'], '_No response_')}")
    return {"number": 3, "labels": [{"name": x} for x in template.get("labels", [])],
            "body": "\n\n".join(sections)}


def test_real_news_form_still_queues_only_one_identifier_without_prose():
    template = form("news-correction.yml")
    fields = {field.get("id"): field for field in template["body"]}
    assert fields["candidate_id"]["validations"]["required"] is True
    assert "publication_version" in fields
    issue = submitted_issue(template, {"candidate_id": IDENTIFIER,
        "publication_version": "editorial:2026-10-16",
        "explanation": "UNTRUSTED_TEST_SENTINEL: do not send this prose to a model"})
    assert candidate_from_request(issue) == IDENTIFIER
    empty = {"schema_version": 1, "generations": {}}
    exceptions = {"schema_version": 1, "decisions": []}
    state = public_rechecks([issue], {IDENTIFIER}, empty, exceptions)
    assert state == {"schema_version": 1, "generations": {IDENTIFIER: 3}}
    assert public_rechecks([{**issue, "number": 4}], {IDENTIFIER}, state, exceptions) == state


def test_catalog_or_site_form_cannot_trigger_a_news_recheck():
    issue = submitted_issue(form("record-correction.yml"), {
        "record_id": IDENTIFIER, "explanation": "Synthetic public correction"})
    assert candidate_from_request(issue) is None


def test_both_public_forms_expose_private_channels_before_free_text():
    for name in ("news-correction.yml", "record-correction.yml"):
        template = form(name)
        first = template["body"][0]
        assert first["type"] == "markdown"
        notice = first["attributes"]["value"]
        assert EDITORIAL in notice and TECHNICAL in notice
        assert "públic" in notice and "No necesitas abrir un issue público" in notice
        confirmation = next(field for field in template["body"]
                            if field.get("id") == "public_content")
        assert confirmation["type"] == "checkboxes"
        assert confirmation["attributes"]["options"][0]["required"] is True
    options = next(field for field in form("news-correction.yml")["body"]
                   if field.get("id") == "request_kind")["attributes"]["options"]
    assert all("privacidad" not in item.lower() and "retirada" not in item.lower()
               for item in options)


def test_issue_chooser_uses_renderable_https_destinations_and_no_blank_form():
    config = form("config.yml")
    assert config["blank_issues_enabled"] is False
    urls = {item["url"] for item in config["contact_links"]}
    policy_url = ("https://github.com/RauPro/MDH-SV/blob/main/SECURITY.md"
                  "#correcciones-editoriales-sensibles-y-retiradas")
    assert urls == {TECHNICAL, policy_url}
    # The chooser does not display mailto contact entries; its HTTPS policy
    # destination must contain the usable private email link and matching anchor.
    policy = (ROOT / "SECURITY.md").read_text()
    assert "## Correcciones editoriales sensibles y retiradas" in policy
    assert EDITORIAL in policy and TECHNICAL in policy
