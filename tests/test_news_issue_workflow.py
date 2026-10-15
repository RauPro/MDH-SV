"""Issue reconciliation uses existing decisions and cannot publish or call a model."""
from pathlib import Path
import re
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1] / "ops/review-queue/.github/workflows"


def workflow():
    return yaml.load((ROOT / "reconcile-news-issues.yml").read_text(), Loader=yaml.BaseLoader)


def allowed(*, conclusion="success", name="Publicar ubicaciones automáticas",
            path=".github/workflows/publish-locations.yml", head_branch="main",
            head_repository="RauPro/MDH-SV-review", repository="RauPro/MDH-SV-review",
            private=True, ref="refs/heads/main", event_name="workflow_run"):
    run = SimpleNamespace(conclusion=conclusion, name=name, path=path,
        head_branch=head_branch, head_repository=SimpleNamespace(full_name=head_repository))
    github = SimpleNamespace(event=SimpleNamespace(repository=SimpleNamespace(private=private), workflow_run=run),
        ref=ref, event_name=event_name, repository=repository)
    # Only the repository-owned boolean condition is evaluated, never event prose.
    condition = workflow()["jobs"]["reconcile"]["if"].replace("&&", " and ").replace("||", " or ")
    condition = re.sub(r"\btrue\b", "True", condition)
    return eval(condition, {"__builtins__": {}}, {"github": github})


def test_reconciliation_follows_publication_and_serializes_with_private_pipeline():
    value = workflow()
    assert set(value["on"]) == {"workflow_run", "workflow_dispatch"}
    assert value["on"]["workflow_run"] == {
        "workflows": ["Publicar ubicaciones automáticas"], "types": ["completed"], "branches": ["main"]}
    assert value["concurrency"] == {"group": "editorial-inbox", "cancel-in-progress": "false", "queue": "max"}
    assert value["jobs"]["reconcile"]["timeout-minutes"] == "20"
    assert allowed() and allowed(conclusion="failure") and allowed(event_name="workflow_dispatch")


@pytest.mark.parametrize("changes", [
    {"conclusion": "cancelled"}, {"conclusion": "timed_out"}, {"conclusion": "skipped"},
    {"name": "Preparar fichas con Gemini"}, {"path": ".github/workflows/other.yml"},
    {"head_branch": "untrusted"}, {"head_repository": "another/fork"},
    {"repository": "another/private-repo"}, {"private": False},
    {"ref": "refs/heads/untrusted"}, {"event_name": "push"},
])
def test_reconciliation_rejects_untrusted_or_unrelated_runs(changes):
    assert not allowed(**changes)


def test_reconciliation_pins_code_without_model_or_public_write_credentials():
    value = workflow()
    # Contents writes are only for the durable private reconciliation allowance.
    assert value["permissions"] == {"contents": "write", "issues": "write"}
    steps = value["jobs"]["reconcile"]["steps"]
    checkouts = {step["with"]["path"]: step["with"] for step in steps if step.get("uses") == "actions/checkout@v4"}
    assert checkouts["monitor"]["repository"] == checkouts["publication"]["repository"] == "RauPro/MDH-SV"
    assert checkouts["monitor"]["ref"] == "MDH_PUBLIC_CODE_REF"
    assert checkouts["inbox"]["ref"] == checkouts["publication"]["ref"] == "main"
    assert "sparse-checkout" not in checkouts["inbox"]
    assert set(checkouts["publication"]["sparse-checkout"].split()) == {"data/history", "data/selection"}
    assert all(item["persist-credentials"] == "false" for item in checkouts.values())
    text = str(value)
    assert "secrets." not in text and "GEMINI" not in text
    assert "git push" not in text and "git commit" not in text
    assert not any("artifact" in step.get("uses", "") for step in steps)
    assert "workflow_run.head_sha" not in text


def test_reconciliation_cli_uses_full_bibliography_and_supports_mutation_free_preview():
    value = workflow()
    inputs = value["on"]["workflow_dispatch"]["inputs"]
    assert inputs["apply"]["type"] == "boolean" and inputs["apply"]["default"] == "true"
    assert inputs["limit"]["default"] == "150" and inputs["limit"]["options"] == ["50", "150"]
    steps = value["jobs"]["reconcile"]["steps"]
    export = next(step for step in steps if "mdh_pipeline.history " in step.get("run", ""))
    assert "--export-only --candidates-only --data-dir publication/data/history --output candidates.json" in export["run"]
    assert "GH_TOKEN" not in export.get("env", {})
    run = next(step for step in steps if "mdh_pipeline.news_issue_reconcile" in step.get("run", ""))
    assert steps.index(export) < steps.index(run)
    assert run["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert run["env"]["RECONCILE_APPLY"] == "${{ github.event_name != 'workflow_dispatch' || inputs.apply }}"
    assert '${{' not in run["run"]
    assert 'git -C inbox rev-parse HEAD' in run["run"]
    for argument in ("--inbox-dir inbox", "--archive candidates.json",
        "--scope-audits publication/data/selection/scope_audits.json",
        "--exceptions publication/data/selection/news_exceptions.json",
        '--private-revision "$private_revision"', '--limit "$RECONCILE_LIMIT"'):
        assert argument in run["run"]
    assert 'if [ "$RECONCILE_APPLY" = \'true\' ]; then apply_args+=(--apply); fi' in run["run"]
    assert '"${apply_args[@]}"' in run["run"]
