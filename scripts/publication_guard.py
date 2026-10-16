"""Check exact public Git tips before a push, without exposing remote URLs or secrets."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
OID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
MAX_INPUT = 1024 * 1024
FORBIDDEN_PREFIXES = (
    "legacy/", ".local-quarantine/", ".source-location-cache/",
    "data/ngo_reports/", "apps/web/src/data/",
)


class GuardError(ValueError):
    """Only bounded, non-sensitive messages may leave the guard."""


def check_history_paths(repo: Path, refs: list[str]) -> None:
    """Even a subsequently deleted private bundle must not reach a public ref."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1",
                       GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)

    def read(*arguments):
        result = subprocess.run(["git", "--no-replace-objects", "-C", str(repo), *arguments],
                                capture_output=True, check=False, env=environment)
        if result.returncode:
            raise GuardError("publication_history_paths_unavailable")
        return result.stdout

    tips = []
    for ref in refs:
        value = read("rev-parse", "--verify", "--end-of-options", ref + "^{commit}").strip()
        if not re.fullmatch(rb"[0-9a-f]{40}|[0-9a-f]{64}", value):
            raise GuardError("publication_history_paths_unavailable")
        tips.append(value.decode("ascii"))
    data = read("log", "--no-color", "--format=", "--name-only", "-z", "--full-history",
                "--root", "-m", "--no-renames", "--no-ext-diff", "--no-textconv", *tips, "--")
    for raw in data.split(b"\0"):
        raw = raw.strip(b"\n")
        if not raw:
            continue
        try:
            name = raw.decode("utf-8")
        except UnicodeError:
            raise GuardError("publication_path_encoding_invalid") from None
        path = PurePosixPath(name)
        if (name.startswith(FORBIDDEN_PREFIXES) or path.is_absolute() or ".." in path.parts
                or "node_modules" in path.parts
                or path.suffix.lower() in {".bundle", ".pack", ".sqlite", ".db"}
                or path.name.lower().endswith((".sqlite-wal", ".sqlite-shm"))
                or (path.name.lower().startswith(".env") and path.name != ".env.example")):
            raise GuardError("forbidden_release_path_in_history")


def pushed_refs(data: bytes) -> tuple[list[str], list[str]]:
    if len(data) > MAX_INPUT:
        raise GuardError("push_reference_list_too_large")
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeError:
        raise GuardError("invalid_push_reference_encoding") from None
    tips, names = [], []
    for line in lines:
        fields = line.split()
        if len(fields) != 4:
            raise GuardError("invalid_push_reference_record")
        local_ref, local_oid, remote_ref, remote_oid = fields
        if not (OID.fullmatch(local_oid) and OID.fullmatch(remote_oid)):
            raise GuardError("invalid_push_object_id")
        # --mirror includes remote-tracking/private refs. They are never a release.
        if not remote_ref.startswith(("refs/heads/", "refs/tags/")):
            raise GuardError("push_namespace_not_publicable")
        if set(local_oid) != {"0"}:
            tips.append(local_oid)
            names.extend([local_ref, remote_ref])
    return list(dict.fromkeys(tips)), list(dict.fromkeys(names))


def policy_arguments(path: Path) -> list[str]:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise GuardError("duplicate_history_policy_key")
            result[key] = value
        return result

    try:
        policy = json.loads(path.read_text(), object_pairs_hook=unique)
    except (OSError, ValueError):
        raise GuardError("invalid_history_policy") from None
    if (not isinstance(policy, dict)
            or set(policy) != {"schema_version", "required_root", "rejected_ancestors"}
            or type(policy["schema_version"]) is not int or policy["schema_version"] != 1
            or not isinstance(policy["required_root"], str)
            or not OID.fullmatch(policy["required_root"])
            or not isinstance(policy["rejected_ancestors"], list)
            or not policy["rejected_ancestors"]
            or any(not isinstance(value, str) or not OID.fullmatch(value)
                   for value in policy["rejected_ancestors"])):
        raise GuardError("invalid_history_policy")
    arguments = ["--require-root", policy["required_root"]]
    for value in policy["rejected_ancestors"]:
        arguments.extend(["--reject-ancestor", value])
    return arguments


def check(repo: Path, refs: list[str], binary: str | None = None,
          ref_names: list[str] | None = None) -> int:
    check_history_paths(repo, refs)
    arguments = [sys.executable, "-I", "-S", str(ROOT / "scripts/scan_public_history.py"),
                 "--repo", str(repo), "--config", str(ROOT / ".security/gitleaks.toml")]
    arguments.extend(policy_arguments(ROOT / ".security/publication-history.json"))
    if binary:
        arguments.extend(["--gitleaks", binary])
    for ref in refs:
        arguments.extend(["--ref", ref])
    for name in ref_names or []:
        arguments.extend(["--ref-name", name])
    # The scanner normalizes its own failures and prints only redacted metadata.
    try:
        result = subprocess.run(arguments, stdin=subprocess.DEVNULL, check=False)
    except OSError:
        raise GuardError("history_scanner_unavailable") from None
    return result.returncode if result.returncode in {0, 1, 2} else 2


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--gitleaks", default=os.environ.get("GITLEAKS_BIN"))
    commands = parser.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("check")
    scan.add_argument("--ref", action="append")
    push = commands.add_parser("pre-push")
    push.add_argument("remote_name")
    push.add_argument("remote_url")
    args = parser.parse_args(argv)
    try:
        refs, names = (pushed_refs(sys.stdin.buffer.read(MAX_INPUT + 1))
                       if args.command == "pre-push" else (args.ref or ["HEAD"], []))
        if not refs:
            print("No new Git objects to publish.")
            return 0
        return check(args.repo, refs, args.gitleaks, names)
    except GuardError as error:
        print(str(error), file=sys.stderr)
        return 2
    except (OSError, UnicodeError):
        print("publication_guard_unavailable", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
