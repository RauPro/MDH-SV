"""Prepare a checkout without credentials; run with Python 3.13 and Node 22.

Installs the locked Python and npm dependencies and the authenticated Gitleaks
release. Existing .env files and virtual environments are never replaced.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SetupError(Exception):
    """An actionable preparation error without command output or credentials."""


def run(command: list[str], *, root: Path, env: dict | None = None) -> None:
    print("+ " + " ".join(map(str, command)), flush=True)
    subprocess.run(command, cwd=root, env=env, check=True)


def capture(command: list[str], *, root: Path) -> str:
    try:
        return subprocess.check_output(command, cwd=root, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        raise SetupError(f"Cannot run {Path(command[0]).name}; check its installation.") from None


def python_version(python: Path, *, root: Path) -> tuple[int, int]:
    version = capture([str(python), "--version"], root=root)
    match = re.fullmatch(r"Python (\d+)\.(\d+)\.\d+(?:\S*)", version)
    if not match:
        raise SetupError("Cannot determine the Python interpreter version.")
    return int(match[1]), int(match[2])


def validate_runtime(root: Path, python: Path) -> tuple[str, str]:
    """Fail before creating or installing anything on an unsupported runtime."""
    expected = tuple(map(int, (root / ".python-version").read_text().strip().split(".")))
    if python_version(python, root=root) != expected:
        raise SetupError("Use Python " + ".".join(map(str, expected)) + " for this checkout.")
    node, npm = shutil.which("node"), shutil.which("npm")
    if not node or not npm:
        raise SetupError("Install Node 22 (>=22.12) with npm and put both on PATH.")
    version = capture([node, "--version"], root=root)
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", version)
    major = int((root / ".nvmrc").read_text().strip())
    if not match or int(match[1]) != major or tuple(map(int, match.groups())) < (22, 12, 0):
        raise SetupError(f"Use Node {major} (>=22.12); run nvm use if nvm is installed.")
    if not re.fullmatch(r"\d+\.\d+\.\d+", capture([npm, "--version"], root=root)):
        raise SetupError("Cannot determine the npm version.")
    if capture(["git", "rev-parse", "--show-toplevel"], root=root) != str(root.resolve()):
        raise SetupError("Run preparation from a Git checkout of this project.")
    if capture(["git", "rev-parse", "--is-shallow-repository"], root=root) != "false":
        raise SetupError("A complete Git history is required; fetch --unshallow before preparation.")
    return node, npm


def environment_python(root: Path) -> Path:
    environment = root / ".venv"
    if environment.is_symlink():
        raise SetupError("Refusing an external/symlinked .venv; choose a separate clean checkout.")
    return environment / "bin/python"


def prepare_environment(root: Path, python: Path) -> Path:
    destination = root / ".venv"
    executable = environment_python(root)
    if destination.exists():
        if not (destination / "pyvenv.cfg").is_file() or not executable.is_file():
            raise SetupError("Existing .venv is not usable; preserve it and use a clean checkout.")
        if python_version(executable, root=root) != python_version(python, root=root):
            raise SetupError("Existing .venv uses another Python version; use a separate checkout.")
    else:
        venv.EnvBuilder(with_pip=True).create(destination)
    return executable


def bootstrap(root: Path = ROOT) -> None:
    python = Path(sys.executable)
    _, npm = validate_runtime(root, python)
    # Import only stdlib-based repository tooling; no dependencies or secrets are needed.
    import install_gitleaks

    try:
        install_gitleaks.current_platform()
    except install_gitleaks.InstallError:
        raise SetupError("Use macOS or Linux on arm64/x64 for the pinned secret scanner.") from None
    executable = prepare_environment(root, python)
    environment = os.environ.copy()
    environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    run([str(executable), "-m", "pip", "install", "-r", "requirements.lock"],
        root=root, env=environment)
    run([str(executable), "-m", "pip", "install", "-e", ".", "--no-deps", "--no-build-isolation"],
        root=root, env=environment)
    run([str(executable), "-m", "pip", "check"], root=root, env=environment)
    run([npm, "ci", "--ignore-scripts", "--no-audit", "--no-fund"], root=root, env=environment)
    run([npm, "--prefix", "apps/web", "ci", "--no-audit", "--no-fund"],
        root=root, env=environment)
    # Use the environment's locked CA bundle when system Python lacks certificates
    # (notably the python.org macOS installation). TLS verification stays enabled.
    scanner_environment = environment.copy()
    if not scanner_environment.get("SSL_CERT_FILE"):
        scanner_environment["SSL_CERT_FILE"] = capture(
            [str(executable), "-c", "import certifi; print(certifi.where())"], root=root)
    run([str(executable), "scripts/install_gitleaks.py"], root=root, env=scanner_environment)
    print("Prepared. Run: .venv/bin/python scripts/check.py", flush=True)


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    try:
        bootstrap()
    except (SetupError, OSError, subprocess.CalledProcessError) as error:
        message = str(error) if isinstance(error, SetupError) else "Preparation failed; see the step above."
        print(message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
