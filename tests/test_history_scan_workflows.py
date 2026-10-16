"""All public proposal paths scan complete committed history before transmission."""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow(path):
    return yaml.load((ROOT / path).read_text(), Loader=yaml.BaseLoader)


def command_step(steps, command):
    matches = [step for step in steps if command in step.get("run", "")]
    assert len(matches) == 1
    return matches[0]


def assert_required(step):
    assert "if" not in step
    assert step.get("continue-on-error", "false") == "false"


def test_required_verify_scans_full_history_before_dependency_installation():
    document = workflow(".github/workflows/ci.yml")
    assert document["permissions"] == {"contents": "read"}
    assert {"push", "pull_request", "workflow_dispatch"} == set(document["on"])
    assert "pull_request_target" not in document["on"]
    job = document["jobs"]["verify"]
    assert "permissions" not in job and "if" not in job
    steps = job["steps"]
    checkout = next(step for step in steps if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["fetch-depth"] == "0"
    install = command_step(steps, "python scripts/install_gitleaks.py")
    scan = command_step(steps, "python scripts/publication_guard.py check --ref HEAD")
    dependencies = command_step(steps, "python -m pip install")
    assert_required(install)
    assert_required(scan)
    assert steps.index(checkout) < steps.index(install) < steps.index(scan) < steps.index(dependencies)
    assert '>> "$GITHUB_PATH"' in install["run"]
    assert "secrets." not in str(job)


@pytest.mark.parametrize("path,job_name", [
    (".github/workflows/refresh-history.yml", "refresh"),
    ("ops/history/refresh-history.yml", "refresh"),
    (".github/workflows/news-exception.yml", "apply"),
])
def test_public_bots_scan_the_new_commit_before_any_push(path, job_name):
    document = workflow(path)
    assert document["permissions"] == {"contents": "read"}
    job = document["jobs"][job_name]
    expected_permissions = {"contents": "write", "pull-requests": "write", "actions": "write"}
    if job_name == "apply":
        expected_permissions["issues"] = "read"
    assert job["permissions"] == expected_permissions
    steps = job["steps"]
    checkout = next(step for step in steps if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["fetch-depth"] == "0"
    install = command_step(steps, "python scripts/install_gitleaks.py")
    dependencies = command_step(steps, "python -m pip install")
    proposal = command_step(steps, "git push")
    assert_required(install)
    assert_required(proposal)
    assert steps.index(checkout) < steps.index(install) < steps.index(dependencies) < steps.index(proposal)
    assert "env" not in install and "GH_TOKEN" not in job.get("env", {})
    script = proposal["run"]
    guard = "python scripts/publication_guard.py check --ref HEAD"
    assert "set -euo pipefail" in script
    assert script.index("git commit") < script.index(guard) < script.index("git push")
    assert script.count(guard) == 1
    assert "||" not in script and "--force" not in script
    assert 'HEAD:refs/heads/$PUBLICATION_BRANCH' in script
    assert "HEAD:main" not in script


def test_private_publisher_uses_approved_scanner_without_private_data():
    document = workflow("ops/review-queue/.github/workflows/publish-locations.yml")
    prepare = document["jobs"]["publish"]
    job = document["jobs"]["propose"]
    assert job["permissions"] == {}
    assert job["needs"] == "publish"
    assert "NEWS_LOCATION_PUBLISH_KEY" not in str(prepare)
    assert "publication_guard.py" not in str(prepare)
    assert "GEMINI" not in str(job) and "inbox" not in str(job)
    steps = job["steps"]
    code = next(step for step in steps if step.get("with", {}).get("path") == "monitor")
    assert code["with"]["ref"] == "MDH_PUBLIC_CODE_REF"
    assert code["with"]["persist-credentials"] == "false"
    install = command_step(steps, "python monitor/scripts/install_gitleaks.py")
    checkout = next(step for step in steps if "ssh-key" in step.get("with", {}))
    assert checkout["with"]["path"] == "publication"
    assert checkout["with"]["ref"] == "${{ needs.publish.outputs.base_sha }}"
    assert checkout["with"]["fetch-depth"] == "0"
    # actions/checkout enables a blobless partial fetch for sparse-checkout.
    # Apply sparsity after a full fetch to keep complete offline history.
    assert "sparse-checkout" not in checkout["with"] and "filter" not in checkout["with"]
    sparse = command_step(steps, "git -C publication sparse-checkout set --no-cone")
    assert steps.index(sparse) == steps.index(checkout) + 1
    assert "'/data/' '/apps/web/public/'" in sparse["run"]
    assert steps.index(code) < steps.index(install) < steps.index(checkout)
    assert_required(install)
    assert "env" not in install
    proposal = command_step(steps, "git -C publication push")
    assert_required(proposal)
    script = proposal["run"]
    guard = "python monitor/scripts/publication_guard.py --repo publication check --ref HEAD"
    assert "set -euo pipefail" in script
    assert script.index("git -C publication commit") < script.index(guard) < script.index("git -C publication push")
    assert "||" not in script and "--force" not in script
    assert all(step.get("working-directory") != "publication" for step in steps)
    assert "python publication/" not in str(steps)


def test_history_template_remains_identical_to_active_public_bot():
    assert (ROOT / "ops/history/refresh-history.yml").read_bytes() == (
        ROOT / ".github/workflows/refresh-history.yml"
    ).read_bytes()
