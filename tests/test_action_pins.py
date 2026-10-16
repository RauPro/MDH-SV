"""Every workflow and installed template uses immutable third-party Actions.

Local wrappers are followed, so moving an unpinned use into a composite Action
or reusable workflow cannot evade this check. This does not audit dependencies
inside third-party Actions or arbitrary shell commands.
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
REMOTE_ACTION = re.compile(
    r"[A-Za-z0-9_-][A-Za-z0-9_.-]*/[A-Za-z0-9_-][A-Za-z0-9_.-]*"
    r"(?:/[A-Za-z0-9_.-]+)*@[0-9a-fA-F]{40}\Z"
)
CONTAINER_ACTION = re.compile(r"docker://[^\s@]+@sha256:[0-9a-fA-F]{64}\Z")


def _load(path):
    document = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    assert isinstance(document, dict), f"Not a YAML mapping: {path}"
    return document


def _local_target(reference, repository):
    assert repository is not None, (
        "Local uses in flat ops templates need an explicit installed repository layout"
    )
    assert "${{" not in reference and "\\" not in reference, reference
    relative = Path(reference[2:])
    assert not relative.is_absolute() and ".." not in relative.parts, reference
    target = (repository / relative).resolve()
    assert target.is_relative_to(repository.resolve()), f"Local uses escapes checkout: {reference}"
    return target


def _check_uses(reference, repository, active, *, reusable=False):
    assert isinstance(reference, str), "uses must be a literal string"
    if reference.startswith("./"):
        target = _local_target(reference, repository)
        if reusable:
            assert reference.startswith("./.github/workflows/"), reference
            assert target.parent == (repository / ".github/workflows").resolve(), reference
            assert target.suffix in {".yml", ".yaml"} and target.is_file(), reference
            _check_manifest(target, repository, active, workflow=True)
        else:
            manifests = [target / name for name in ("action.yml", "action.yaml")]
            manifests = [path for path in manifests if path.is_file()]
            assert len(manifests) == 1, f"Local Action must have one manifest: {reference}"
            _check_manifest(manifests[0], repository, active, workflow=False)
        return
    if reference.startswith("docker://"):
        assert not reusable, "Reusable workflows cannot be Docker Actions"
        assert CONTAINER_ACTION.fullmatch(reference), f"Container Action needs SHA-256: {reference}"
        return
    assert REMOTE_ACTION.fullmatch(reference), f"Action needs a full 40-character SHA: {reference}"
    assert all(part not in {".", ".."} for part in reference.split("@")[0].split("/")), reference
    if reusable:
        target = reference.split("@")[0].split("/")
        assert len(target) == 5 and target[2:4] == [".github", "workflows"], reference
        assert Path(target[-1]).suffix in {".yml", ".yaml"}, reference


def _check_steps(steps, repository, active):
    assert isinstance(steps, list), "steps must be a list"
    for step in steps:
        assert isinstance(step, dict), "step must be a mapping"
        if "uses" in step:
            _check_uses(step["uses"], repository, active)


def _check_manifest(path, repository, active=None, *, workflow=True):
    path = path.resolve()
    if repository is not None:
        assert path.is_relative_to(repository.resolve()), f"Manifest escapes checkout: {path}"
    active = set() if active is None else active
    assert path not in active, f"Recursive local Action/workflow: {path}"
    active = active | {path}
    document = _load(path)
    if workflow:
        jobs = document.get("jobs")
        assert isinstance(jobs, dict) and jobs, f"Workflow needs jobs: {path}"
        for job in jobs.values():
            assert isinstance(job, dict), f"Job must be a mapping: {path}"
            if "uses" in job:
                _check_uses(job["uses"], repository, active, reusable=True)
            else:
                _check_steps(job.get("steps"), repository, active)
    else:
        runs = document.get("runs")
        assert isinstance(runs, dict), f"Action needs runs: {path}"
        using = runs.get("using", "")
        if using == "composite":
            _check_steps(runs.get("steps"), repository, active)
        elif using == "docker":
            container = runs.get("image", "")
            if container.startswith("docker://"):
                _check_uses(container, repository, active)
            else:
                assert container in {"Dockerfile", "./Dockerfile"}, container
                assert (path.parent / "Dockerfile").is_file(), path
        else:
            assert re.fullmatch(r"node\d+", using), f"Unsupported local Action runtime: {using}"


def _workflow_paths(root):
    # rglob includes private-repository templates nested under ops/**/.github.
    paths = set()
    for directory in (root / ".github/workflows", root / "ops"):
        for extension in ("*.yml", "*.yaml"):
            for path in directory.rglob(extension):
                if path.name in {"action.yml", "action.yaml"} and path.parent.name != "workflows":
                    document = _load(path)
                    if "runs" in document and "jobs" not in document:
                        continue  # Recursively checked when used by a workflow.
                paths.add(path)
    return sorted(paths)


def _repository_for(path):
    for parent in path.parents:
        if parent.name == ".github":
            return parent.parent
    # Flat deployment templates have no unambiguous local checkout layout.
    return None


@pytest.mark.parametrize("path", _workflow_paths(ROOT), ids=lambda path: str(path.relative_to(ROOT)))
def test_all_workflows_and_templates_pin_actions(path):
    _check_manifest(path, _repository_for(path))


@pytest.mark.parametrize("reference", [
    "actions/checkout@" + "a" * 40,
    "actions/cache/restore@" + "B" * 40,
    "docker://ghcr.io/example/action@sha256:" + "a" * 64,
])
def test_immutable_remote_action_references(reference):
    _check_uses(reference, None, set())


@pytest.mark.parametrize("reference", [
    "actions/checkout@v4",
    "actions/checkout@v4.2.2",
    "actions/checkout@main",
    "actions/checkout@abcdef0",
    "actions/checkout@" + "g" * 40,
    "actions/checkout@" + "a" * 39,
    "actions/checkout@" + "a" * 41,
    "actions/checkout@${{ inputs.ref }}",
    "docker://alpine:3.20",
    "docker://alpine@sha256:" + "a" * 63,
    "docker://alpine@sha256:${{ inputs.digest }}",
    "./missing-action",
    "../outside-action",
    "https://github.com/actions/checkout@" + "a" * 40,
])
def test_mutable_or_unverifiable_action_references_fail_closed(reference, tmp_path):
    with pytest.raises(AssertionError):
        _check_uses(reference, tmp_path, set())


def _write(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(document))
    return path


def _workflow(reference, *, reusable=False):
    job = {"uses": reference} if reusable else {"steps": [{"uses": reference}]}
    return {"jobs": {"verify": job}}


@pytest.mark.parametrize("pinned", [False, True])
def test_reusable_remote_workflows_are_checked(tmp_path, pinned):
    reference = "owner/repo/.github/workflows/reusable.yml@" + ("a" * 40 if pinned else "main")
    path = _write(tmp_path / ".github/workflows/ci.yml", _workflow(reference, reusable=True))
    if pinned:
        _check_manifest(path, tmp_path)
    else:
        with pytest.raises(AssertionError, match="full 40-character SHA"):
            _check_manifest(path, tmp_path)


@pytest.mark.parametrize("pinned", [False, True])
def test_local_reusable_workflow_and_nested_composite_cannot_hide_mutable_actions(tmp_path, pinned):
    entry = _write(tmp_path / ".github/workflows/ci.yml", _workflow(
        "./.github/workflows/reusable.yml", reusable=True,
    ))
    _write(tmp_path / ".github/workflows/reusable.yml", _workflow("./.github/actions/outer"))
    _write(tmp_path / ".github/actions/outer/action.yml", {
        "runs": {"using": "composite", "steps": [{"uses": "./.github/actions/inner"}]},
    })
    _write(tmp_path / ".github/actions/inner/action.yaml", {
        "runs": {"using": "composite", "steps": [
            {"uses": "actions/checkout@" + ("a" * 40 if pinned else "v4")},
        ]},
    })
    if pinned:
        _check_manifest(entry, tmp_path)
    else:
        with pytest.raises(AssertionError, match="full 40-character SHA"):
            _check_manifest(entry, tmp_path)


def test_local_reference_cannot_escape_checkout_through_symlink(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    _write(tmp_path / "external/action.yml", {"runs": {"using": "node20", "main": "index.js"}})
    (repository / "wrapper").symlink_to(tmp_path / "external", target_is_directory=True)
    with pytest.raises(AssertionError, match="escapes checkout"):
        _check_uses("./wrapper", repository, set())


def test_recursive_local_wrappers_fail_closed(tmp_path):
    path = _write(tmp_path / ".github/workflows/ci.yml", _workflow("./wrapper"))
    _write(tmp_path / "wrapper/action.yml", {
        "runs": {"using": "composite", "steps": [{"uses": "./wrapper"}]},
    })
    with pytest.raises(AssertionError, match="Recursive local Action"):
        _check_manifest(path, tmp_path)


def test_discovers_hidden_private_templates_and_both_yaml_extensions(tmp_path):
    paths = [
        tmp_path / ".github/workflows/ci.yml",
        tmp_path / ".github/workflows/action.yml",
        tmp_path / "ops/review-queue/.github/workflows/private.yaml",
        tmp_path / "ops/history/review.yml",
    ]
    for path in paths:
        _write(path, _workflow("actions/checkout@" + "a" * 40))
    # Referenced Action manifests are scanned recursively, not as workflows.
    _write(tmp_path / "ops/review-queue/.github/actions/helper/action.yml", {
        "runs": {"using": "node20", "main": "index.js"},
    })
    assert _workflow_paths(tmp_path) == sorted(paths)
    assert _repository_for(paths[0]) == tmp_path
    assert _repository_for(paths[2]) == tmp_path / "ops/review-queue"
    assert _repository_for(paths[3]) is None


def test_local_reference_in_flat_template_requires_explicit_repository_layout():
    with pytest.raises(AssertionError, match="installed repository layout"):
        _check_uses("./wrapper", None, set())


def test_local_manifest_symlink_cannot_escape_checkout(tmp_path):
    repository = tmp_path / "repository"
    (repository / "wrapper").mkdir(parents=True)
    external = _write(tmp_path / "external.yml", {
        "runs": {"using": "node20", "main": "index.js"},
    })
    (repository / "wrapper/action.yml").symlink_to(external)
    with pytest.raises(AssertionError, match="Manifest escapes checkout"):
        _check_uses("./wrapper", repository, set())


@pytest.mark.parametrize("pinned", [False, True])
def test_local_container_action_cannot_hide_a_mutable_image(tmp_path, pinned):
    image = "docker://alpine" + ("@sha256:" + "a" * 64 if pinned else ":3.20")
    _write(tmp_path / "wrapper/action.yml", {"runs": {"using": "docker", "image": image}})
    if pinned:
        _check_uses("./wrapper", tmp_path, set())
    else:
        with pytest.raises(AssertionError, match="needs SHA-256"):
            _check_uses("./wrapper", tmp_path, set())
