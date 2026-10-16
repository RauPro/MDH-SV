"""Generated updates cannot broaden the review exemption or bypass main checks."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_GENERATED = {
    "data/history/co-latino.json", "data/history/gatoencerrado.json",
    "data/history/factum.json", "data/history/el-faro.json",
    "data/history/el-mundo.json", "data/history/la-prensa-grafica.json",
    "data/history/el-diario-de-hoy.json", "data/selection/news.json",
    "data/selection/news_exceptions.json", "data/automated/news_dossiers.json",
    "apps/web/public/archive/news.json", "apps/web/public/archive/dossiers.json",
    "apps/web/public/archive/locations.json", "apps/web/public/archive/processing.json",
}


def test_only_exact_generated_paths_are_exempt_from_code_owner_review():
    lines = [line.split() for line in (ROOT / ".github/CODEOWNERS").read_text().splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    assert lines[0] == ["*", "@RauPro"]
    assert all(len(line) == 1 and line[0].startswith("/") for line in lines[1:])
    assert {line[0][1:] for line in lines[1:]} == EXPECTED_GENERATED
    assert len(lines) == len(EXPECTED_GENERATED) + 1
    assert all((ROOT / path).is_file() and not (ROOT / path).is_symlink()
               for path in EXPECTED_GENERATED)


def test_main_requires_checked_pull_requests_without_admin_or_bot_bypass():
    policy = json.loads((ROOT / ".github/main-protection.json").read_text())
    assert policy["required_status_checks"] == {
        "strict": True, "checks": [{"context": "verify", "app_id": 15368}]}
    review = policy["required_pull_request_reviews"]
    assert review["require_code_owner_reviews"] and review["dismiss_stale_reviews"]
    # Global approvals would stall generated data; code owners still gate code.
    assert review["required_approving_review_count"] == 0
    assert "bypass_pull_request_allowances" not in review
    assert policy["enforce_admins"] and policy["required_linear_history"]
    assert policy["required_conversation_resolution"]
    assert not policy["allow_force_pushes"] and not policy["allow_deletions"]
