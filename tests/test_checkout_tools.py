"""Checkout tooling rejects incompatible runtimes and never edits source data."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


installer = load("install_gitleaks")
bootstrap = load("bootstrap")
check = load("check")


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "checkout with spaces"
    root.mkdir()
    (root / ".python-version").write_text("3.13\n")
    (root / ".nvmrc").write_text("22\n")
    return root


def fake_runtime(monkeypatch, root, *, python="Python 3.13.14", node="v22.22.0", shallow="false"):
    replies = {
        "--version": python,
        "--show-toplevel": str(root),
        "--is-shallow-repository": shallow,
    }

    def capture(command, *, root):
        if command[0] == "/tools/node":
            return node
        if command[0] == "/tools/npm":
            return "10.9.4"
        return replies[command[-1]]

    monkeypatch.setattr(bootstrap, "capture", capture)
    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: "/tools/" + name)


@pytest.mark.parametrize("node", ["v20.19.0", "v22.11.0", "v24.1.0", "v26.4.0", "invalid"])
def test_unsupported_node_stops_before_mutating_environment(checkout, monkeypatch, node):
    fake_runtime(monkeypatch, checkout, node=node)
    with pytest.raises(bootstrap.SetupError, match="Use Node 22"):
        bootstrap.bootstrap(checkout)
    assert not (checkout / ".venv").exists()


@pytest.mark.parametrize("python", ["Python 3.12.10", "Python 3.14.0", "Python 2.7.18"])
def test_unsupported_python_stops_before_installation(checkout, monkeypatch, python):
    fake_runtime(monkeypatch, checkout, python=python)
    with pytest.raises(bootstrap.SetupError, match="Use Python 3.13"):
        bootstrap.bootstrap(checkout)
    assert not (checkout / ".venv").exists()


def test_shallow_history_is_rejected_before_installation(checkout, monkeypatch):
    fake_runtime(monkeypatch, checkout, shallow="true")
    with pytest.raises(bootstrap.SetupError, match="complete Git history"):
        bootstrap.bootstrap(checkout)
    assert not (checkout / ".venv").exists()


def test_missing_npm_has_actionable_error(checkout, monkeypatch):
    fake_runtime(monkeypatch, checkout)
    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: None)
    with pytest.raises(bootstrap.SetupError, match="Install Node"):
        bootstrap.validate_runtime(checkout, Path("python"))


def test_existing_unknown_venv_is_not_replaced(checkout):
    existing = checkout / ".venv"
    existing.mkdir()
    sentinel = existing / "important.txt"
    sentinel.write_text("preserve me")
    with pytest.raises(bootstrap.SetupError, match="Existing .venv is not usable"):
        bootstrap.prepare_environment(checkout, Path(sys.executable))
    assert sentinel.read_text() == "preserve me"


def test_external_venv_symlink_is_rejected(checkout, tmp_path):
    destination = tmp_path / "other-environment"
    destination.mkdir()
    (checkout / ".venv").symlink_to(destination, target_is_directory=True)
    with pytest.raises(bootstrap.SetupError, match="symlinked .venv"):
        bootstrap.prepare_environment(checkout, Path(sys.executable))
    assert list(destination.iterdir()) == []


def test_valid_existing_environment_is_reused(checkout, monkeypatch):
    environment = checkout / ".venv"
    (environment / "bin").mkdir(parents=True)
    (environment / "bin/python").touch()
    (environment / "pyvenv.cfg").write_text("home = /python\n")
    sentinel = environment / "local-marker"
    sentinel.write_text("preserve me")
    monkeypatch.setattr(bootstrap, "python_version", lambda *args, **kwargs: (3, 13))
    monkeypatch.setattr(bootstrap.venv.EnvBuilder, "create",
                        lambda *args: pytest.fail("existing virtualenv must not be recreated"))
    assert bootstrap.prepare_environment(checkout, Path("python")) == environment / "bin/python"
    assert sentinel.read_text() == "preserve me"


def test_wrong_existing_environment_is_not_upgraded(checkout, monkeypatch):
    environment = checkout / ".venv"
    (environment / "bin").mkdir(parents=True)
    (environment / "bin/python").touch()
    (environment / "pyvenv.cfg").write_text("home = /old-python\n")
    monkeypatch.setattr(bootstrap, "python_version",
                        lambda path, **kwargs: (3, 12) if path.is_relative_to(environment) else (3, 13))
    with pytest.raises(bootstrap.SetupError, match="another Python version"):
        bootstrap.prepare_environment(checkout, Path("/new/python"))
    assert (environment / "pyvenv.cfg").read_text() == "home = /old-python\n"


def test_bootstrap_uses_locks_and_preserves_env_from_external_directory(checkout, monkeypatch, tmp_path):
    fake_runtime(monkeypatch, checkout)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(bootstrap.venv.EnvBuilder, "create", lambda self, path: path.mkdir())
    monkeypatch.setattr(installer, "current_platform", lambda: "linux_x64")
    monkeypatch.setenv("SSL_CERT_FILE", "/trusted/ca.pem")
    (checkout / ".env").write_text("LOCAL_SETTING=leave-it-alone\n")
    calls = []
    monkeypatch.setattr(bootstrap, "run", lambda command, **kwargs: calls.append((command, kwargs)))
    bootstrap.bootstrap(checkout)
    python = str(checkout / ".venv/bin/python")
    assert calls[0][0] == [python, "-m", "pip", "install", "-r", "requirements.lock.txt"]
    assert calls[1][0] == [python, "-m", "pip", "install", "-e", ".", "--no-deps", "--no-build-isolation"]
    assert calls[3][0] == ["/tools/npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"]
    assert calls[4][0] == ["/tools/npm", "--prefix", "apps/web", "ci", "--no-audit", "--no-fund"]
    assert calls[5][0] == [python, "scripts/install_gitleaks.py"]
    assert all(arguments["root"] == checkout for _, arguments in calls)
    assert (checkout / ".env").read_text() == "LOCAL_SETTING=leave-it-alone\n"


def test_failed_dependency_install_stops_before_other_installers(checkout, monkeypatch):
    fake_runtime(monkeypatch, checkout)
    monkeypatch.setattr(bootstrap.venv.EnvBuilder, "create", lambda self, path: path.mkdir())
    calls = []

    def failing(command, **kwargs):
        calls.append(command)
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(bootstrap, "run", failing)
    with pytest.raises(subprocess.CalledProcessError):
        bootstrap.bootstrap(checkout)
    assert len(calls) == 1


def test_missing_scanner_does_not_download(checkout, monkeypatch):
    monkeypatch.setattr(installer, "load_manifest", lambda _: {"version": "8.30.1"})
    monkeypatch.setattr(installer, "current_platform", lambda: "linux_x64")
    monkeypatch.setattr(installer, "download_archive", lambda *args: pytest.fail("unexpected network"))
    with pytest.raises(bootstrap.SetupError, match="run scripts/bootstrap.py"):
        check.scanner_binary(checkout)


def test_modified_scanner_archive_is_rejected(checkout, monkeypatch):
    manifest = installer.load_manifest()
    platform = installer.current_platform()
    directory = checkout / ".local-quarantine/tools/gitleaks" / manifest["version"] / platform
    directory.mkdir(parents=True)
    (directory / ".release.tar.gz").write_bytes(b"tampered archive")
    security = checkout / ".security"
    security.mkdir()
    (security / "gitleaks-version.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(installer, "download_archive", lambda *args: pytest.fail("unexpected network"))
    with pytest.raises(installer.InstallError, match="archive-checksum-mismatch"):
        check.scanner_binary(checkout)


def test_data_drift_fails_without_changing_checkout(checkout, monkeypatch):
    public = checkout / "apps/web/public/data"
    public.mkdir(parents=True)
    (public / "catalog.json").write_bytes(b"original bytes")

    def generate(command, **kwargs):
        target = Path(command[-1])
        target.mkdir(parents=True)
        (target / "catalog.json").write_bytes(b"different bytes")

    monkeypatch.setattr(bootstrap, "run", generate)
    with pytest.raises(bootstrap.SetupError, match="Catalogue exports differ"):
        check.verify_exports(checkout, Path("python"), "node", {})
    assert (public / "catalog.json").read_bytes() == b"original bytes"


def test_news_reconciliation_uses_only_temporary_copy(checkout, monkeypatch):
    public = checkout / "apps/web/public/data"
    public.mkdir(parents=True)
    (public / "catalog.json").write_bytes(b"catalogue")
    for name in check.NEWS_INPUTS:
        path = checkout / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"original news")

    def generate(command, **kwargs):
        if command[1] == "-c":
            target = Path(command[-1])
            target.mkdir(parents=True)
            (target / "catalog.json").write_bytes(b"catalogue")
        else:
            staging = Path(command[-1])
            assert staging != checkout
            (staging / check.NEWS_INPUTS[0]).write_bytes(b"reconciled news")

    monkeypatch.setattr(bootstrap, "run", generate)
    with pytest.raises(bootstrap.SetupError, match="News exports differ"):
        check.verify_exports(checkout, Path("python"), "node", {})
    assert all((checkout / name).read_bytes() == b"original news" for name in check.NEWS_INPUTS)


@pytest.mark.parametrize("audit", [False, True])
def test_full_check_requires_real_scanner_and_audit_is_explicit(checkout, monkeypatch, audit):
    fake_runtime(monkeypatch, checkout)
    (checkout / ".venv/bin").mkdir(parents=True)
    (checkout / ".venv/bin/python").touch()
    (checkout / ".venv/pyvenv.cfg").touch()
    for name in ("node_modules", "apps/web/node_modules"):
        (checkout / name).mkdir(parents=True)
    binary = checkout / "scanner/gitleaks"
    monkeypatch.setattr(check, "scanner_binary", lambda root: binary)
    monkeypatch.setattr(check, "verify_exports", lambda *args: None)
    calls = []
    monkeypatch.setattr(bootstrap, "run", lambda command, **kwargs: calls.append((command, kwargs)))
    check.check(checkout, audit=audit)
    assert any("scripts/publication_guard.py" in command for command, _ in calls)
    assert any("pytest" in command for command, _ in calls)
    assert any("test:api" in command for command, _ in calls)
    assert any(command[1:] == ["-m", "ruff", "check", "--config", "pyproject.toml", "."] for command, _ in calls)
    assert any(command[-2:] == ["run", "lint"] for command, _ in calls)
    audits = [command for command, _ in calls if "pip_audit" in command]
    assert len(audits) == (2 if audit else 0)
    if audit:
        assert {command[-1] for command in audits} == {"requirements.lock.txt", "ops/review-queue/requirements.txt"}
        assert all("--strict" in command and "--disable-pip" in command and "--no-deps" in command for command in audits)
    assert sum("audit" in command for command, _ in calls) == (2 if audit else 0)
    assert all(arguments["env"]["GITLEAKS_BIN"] == str(binary) for _, arguments in calls)
    assert all(arguments["root"] == checkout for _, arguments in calls)
