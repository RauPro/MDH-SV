"""Scan complete, explicitly selected Git histories without printing secret material.

Run this file and its config from approved code. Candidate objects are read through
an isolated bare repository: Gitleaks also reads source/.gitleaksignore even when
--gitleaks-ignore-path is supplied, so scanning the candidate checkout is unsafe.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

VERSION = "8.30.1"
OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
RULE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
CONFIG = Path(__file__).resolve().parents[1] / ".security/gitleaks.toml"
LEAK_EXIT = 7
TIMEOUT = 600


class ScanError(RuntimeError):
    """Only constant, non-sensitive error codes may be used as messages."""


@dataclass(frozen=True)
class Finding:
    oid: str
    kind: str
    rule: str
    line: int


@dataclass(frozen=True)
class ScanResult:
    refs: int
    commits: int
    findings: tuple[Finding, ...]


def environment() -> dict[str, str]:
    # Do not allow injected config, alternate repositories, tracing, external
    # diff commands, or inherited engine configuration to affect this scan.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("GIT_", "GITLEAKS_"))}
    env.update({"GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1",
                "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_ATTR_NOSYSTEM": "1", "GIT_OPTIONAL_LOCKS": "0",
                "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
    return env


def run(args: list[str], *, env: dict[str, str], cwd: Path,
        data: bytes | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, cwd=cwd, env=env, input=data,
                              capture_output=True, timeout=TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError):
        raise ScanError("command_failed") from None


def git(repo: Path, env: dict[str, str], *args: str,
        data: bytes | None = None, allowed: tuple[int, ...] = (0,)) -> bytes:
    result = run(["git", "--no-replace-objects", "-C", str(repo), *args],
                 env=env, cwd=repo, data=data)
    if result.returncode not in allowed:
        raise ScanError("git_command_failed")
    return result.stdout


def text(raw: bytes) -> str:
    try:
        return raw.decode("utf-8").strip()
    except UnicodeError:
        raise ScanError("invalid_git_output") from None


def oid(raw: bytes) -> str:
    value = text(raw)
    if not OID.fullmatch(value):
        raise ScanError("invalid_git_object")
    return value


def resolve(repo: Path, env: dict[str, str], ref: str) -> str:
    # No revision ranges, exclusions, path expressions, or injected options.
    # rev-parse must produce precisely one object even for symbolic references.
    if (not ref or ref.startswith("-") or any(character in ref for character in
                                             "\x00\n\r\t^~:{} \\") or ".." in ref):
        raise ScanError("invalid_reference")
    return oid(git(repo, env, "rev-parse", "--verify", "--end-of-options", ref))


def check_repository(repo: Path, env: dict[str, str]) -> Path:
    if text(git(repo, env, "rev-parse", "--is-shallow-repository")) != "false":
        raise ScanError("shallow_history_not_allowed")
    grafts = Path(text(git(repo, env, "rev-parse", "--path-format=absolute",
                          "--git-path", "info/grafts")))
    if grafts.exists():
        raise ScanError("grafted_history_not_allowed")
    partial = git(repo, env, "config", "--local", "--get-regexp",
                  r"^(extensions\.partialclone|remote\..*\.promisor)$", allowed=(0, 1))
    if partial.strip():
        raise ScanError("partial_history_not_allowed")
    objects = Path(text(git(repo, env, "rev-parse", "--path-format=absolute",
                           "--git-path", "objects")))
    if not objects.is_dir() or "\n" in str(objects) or "\r" in str(objects):
        raise ScanError("invalid_object_store")
    return objects


def object_type(repo: Path, env: dict[str, str], value: str) -> str:
    return text(git(repo, env, "cat-file", "-t", value))


def ancestry(repo: Path, env: dict[str, str], ancestor: str, descendant: str) -> bool:
    result = run(["git", "--no-replace-objects", "-C", str(repo), "merge-base",
                  "--is-ancestor", ancestor, descendant], env=env, cwd=repo)
    if result.returncode not in (0, 1):
        raise ScanError("ancestry_check_failed")
    return result.returncode == 0


def stage_metadata(repo: Path, env: dict[str, str], values: dict[str, str],
                   destination: Path) -> dict[str, tuple[str, str]]:
    """Scan commit/tag headers and messages, not only file patches."""
    mapping = {}
    if not values:
        return mapping
    raw = git(repo, env, "cat-file", "--batch",
              data=("\n".join(values) + "\n").encode("ascii"))
    offset = 0
    for value, expected_kind in values.items():
        end = raw.find(b"\n", offset)
        if end < 0:
            raise ScanError("incomplete_git_metadata")
        header = raw[offset:end].split()
        if len(header) != 3 or header[0] != value.encode() or header[1] != expected_kind.encode():
            raise ScanError("invalid_git_metadata")
        try:
            size = int(header[2])
        except ValueError:
            raise ScanError("invalid_git_metadata") from None
        start, end = end + 1, end + 1 + size
        if size < 0 or end >= len(raw) or raw[end:end + 1] != b"\n":
            raise ScanError("incomplete_git_metadata")
        name = f"{value}.{expected_kind}"
        # The directory scanner skips binary files. Metadata can contain NULs
        # in deliberately malformed objects; preserve all other bytes for scan.
        (destination / name).write_bytes(raw[start:end].replace(b"\x00", b"\n"))
        mapping[name] = (value, expected_kind)
        offset = end + 1
    if offset != len(raw):
        raise ScanError("invalid_git_metadata")
    return mapping


def stage_binary_content(repo: Path, env: dict[str, str], tips: list[str],
                         destination: Path) -> dict[str, tuple[str, str]]:
    """Gitleaks' patch parser skips NUL-containing files even with --text."""
    objects = git(repo, env, "rev-list", "--objects", "--no-object-names", *tips, "--")
    listing = git(repo, env, "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)",
                  data=objects)
    mapping = {}
    for entry in listing.splitlines():
        parts = entry.split()
        if len(parts) != 3:
            raise ScanError("invalid_git_object_listing")
        value = oid(parts[0])
        if parts[1] != b"blob":
            continue
        content = git(repo, env, "cat-file", "blob", value)
        if parts[2] != str(len(content)).encode():
            raise ScanError("incomplete_git_blob")
        if b"\x00" in content:
            name = f"{value}.blob"
            # A plain header also prevents file-magic heuristics from silently
            # skipping ZIP/PDF/etc. Bytes are scanned, archives are not expanded.
            (destination / name).write_bytes(b"Raw Git blob bytes\n" +
                                             content.replace(b"\x00", b"\n"))
            mapping[name] = (value, "blob")
    return mapping


def report_findings(path: Path, *, mode: str, commits: set[str],
                    metadata: dict[str, tuple[str, str]]) -> tuple[Finding, ...]:
    try:
        records = json.loads(path.read_bytes())
    except (OSError, ValueError, UnicodeError):
        raise ScanError("invalid_scanner_report") from None
    if not isinstance(records, list):
        raise ScanError("invalid_scanner_report")
    findings = []
    for record in records:
        if not isinstance(record, dict):
            raise ScanError("invalid_scanner_report")
        rule, line = record.get("RuleID"), record.get("StartLine")
        if not isinstance(rule, str) or not RULE.fullmatch(rule) or type(line) is not int or line < 1:
            raise ScanError("invalid_scanner_report")
        if mode == "git":
            value, kind = record.get("Commit"), "content"
            if not isinstance(value, str) or value not in commits:
                raise ScanError("invalid_scanner_report")
        else:
            name = record.get("File")
            if not isinstance(name, str) or Path(name).name not in metadata:
                raise ScanError("invalid_scanner_report")
            value, kind = metadata[Path(name).name]
        findings.append(Finding(value, kind, rule, line))
    return tuple(findings)


def engine(binary: str, source: Path, *, mode: str, config: Path, scratch: Path,
           env: dict[str, str], commits: set[str], metadata: dict[str, tuple[str, str]],
           tips: list[str]) -> tuple[Finding, ...]:
    report = scratch / f"{mode}-report.json"
    empty_ignore = scratch / "empty.ignore"
    empty_ignore.touch(mode=0o600, exist_ok=True)
    args = [binary, mode, str(source), "--config", str(config),
            "--gitleaks-ignore-path", str(empty_ignore), "--ignore-gitleaks-allow",
            "--redact=100", "--no-banner", "--no-color", "--log-level=error",
            "--exit-code", str(LEAK_EXIT), "--report-format=json", "--report-path", str(report)]
    if mode == "git":
        args.append("--log-opts=" + " ".join([
            "--full-history", "--root", "-m", "--text", "--no-ext-diff",
            "--no-textconv", *tips, "--"]))
    result = run(args, env=env, cwd=scratch)
    # A special finding exit code distinguishes leaks from scanner failure,
    # including failures after a partial report was successfully written.
    if result.returncode not in (0, LEAK_EXIT):
        raise ScanError("scanner_failed")
    findings = report_findings(report, mode=mode, commits=commits, metadata=metadata)
    if (result.returncode == LEAK_EXIT) != bool(findings):
        raise ScanError("inconsistent_scanner_report")
    return findings


def scan(repo: Path, refs: list[str], *, binary: str, config: Path = CONFIG,
         require_root: str | None = None,
         reject_ancestors: list[str] | None = None,
         ref_names: list[str] | None = None) -> ScanResult:
    repo, config = repo.resolve(), config.resolve()
    env = environment()
    if not config.is_file() or not refs:
        raise ScanError("missing_scan_configuration")
    objects = check_repository(repo, env)
    object_format = text(git(repo, env, "rev-parse", "--show-object-format"))
    if object_format not in {"sha1", "sha256"}:
        raise ScanError("unsupported_git_object_format")
    executable = shutil.which(binary) if not Path(binary).is_absolute() else binary
    if not executable:
        raise ScanError("missing_scanner")
    executable = str(Path(executable).resolve())
    version = run([executable, "version"], env=env, cwd=repo)
    if version.returncode != 0 or text(version.stdout) != VERSION:
        raise ScanError("unsupported_scanner_version")
    tips, tags, resolved = [], {}, []
    for ref in refs:
        value = resolve(repo, env, ref)
        resolved.append(value)
        seen = set()
        while object_type(repo, env, value) == "tag":
            if value in seen:
                raise ScanError("cyclic_tag")
            seen.add(value)
            tags[value] = "tag"
            content = git(repo, env, "cat-file", "tag", value)
            first = content.split(b"\n", 1)[0]
            if not first.startswith(b"object "):
                raise ScanError("invalid_tag")
            value = oid(first[7:])
        if object_type(repo, env, value) != "commit":
            raise ScanError("reference_must_resolve_to_commit")
        tips.append(value)
    tips = list(dict.fromkeys(tips))
    if require_root is not None:
        required = resolve(repo, env, require_root)
        if object_type(repo, env, required) != "commit" or any(
                not ancestry(repo, env, required, value) for value in tips):
            raise ScanError("history_outside_public_root")
    for rejected in reject_ancestors or []:
        if not OID.fullmatch(rejected):
            raise ScanError("invalid_rejected_ancestor")
        # The quarantined commit normally does not exist in a clean clone.
        present = run(["git", "--no-replace-objects", "-C", str(repo),
                       "cat-file", "-e", rejected], env=env, cwd=repo)
        if present.returncode == 0 and any(ancestry(repo, env, rejected, value) for value in tips):
            raise ScanError("rejected_ancestor_is_publishable")
        if present.returncode not in (0, 1, 128):
            raise ScanError("ancestry_check_failed")
    commits = {oid(line) for line in git(repo, env, "rev-list", *tips, "--").splitlines()}
    if not commits or not set(tips) <= commits:
        raise ScanError("empty_or_incomplete_history")
    with tempfile.TemporaryDirectory(prefix="mdh-secret-scan-") as temporary:
        scratch = Path(temporary)
        isolated, metadata_dir = scratch / "objects.git", scratch / "metadata"
        metadata_dir.mkdir(mode=0o700)
        templates = scratch / "templates"
        templates.mkdir()
        init = run(["git", "--no-replace-objects", "init", "--bare", "--quiet",
                    f"--object-format={object_format}", f"--template={templates}",
                    str(isolated)], env=env, cwd=scratch)
        if init.returncode:
            raise ScanError("isolated_repository_failed")
        (isolated / "objects/info/alternates").write_text(str(objects) + "\n")
        mapping = stage_metadata(isolated, env, dict.fromkeys(sorted(commits), "commit") | tags,
                                 metadata_dir)
        mapping.update(stage_binary_content(isolated, env, tips, metadata_dir))
        for index, (ref, value) in enumerate(zip(refs, resolved, strict=True)):
            name = f"{index}.ref"
            (metadata_dir / name).write_text(ref)
            mapping[name] = (value, "reference")
        # A pre-push caller selects immutable OIDs, and separately supplies the
        # source/destination names. Names never participate in object resolution.
        for index, name in enumerate(ref_names or []):
            path = f"name-{index}.ref"
            (metadata_dir / path).write_text(name.replace("\x00", "\n"))
            mapping[path] = (resolved[0], "reference")
        content_findings = engine(executable, isolated, mode="git", config=config,
                                  scratch=scratch, env=env, commits=commits,
                                  metadata=mapping, tips=tips)
        metadata_findings = engine(executable, metadata_dir, mode="dir", config=config,
                                   scratch=scratch, env=env, commits=commits,
                                   metadata=mapping, tips=[])
        findings = tuple(sorted(set(content_findings + metadata_findings),
                                key=lambda finding: (finding.oid, finding.kind,
                                                     finding.rule, finding.line)))
    return ScanResult(len(refs), len(commits), findings)


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        raise ScanError("invalid_arguments")


def main(argv: list[str] | None = None) -> int:
    try:
        parser = SafeParser(description=__doc__)
        parser.add_argument("--repo", type=Path, default=Path.cwd())
        parser.add_argument("--ref", action="append", dest="refs")
        parser.add_argument("--ref-name", action="append", default=[],
                            help="Additional push reference name to scan as metadata only")
        parser.add_argument("--gitleaks", default=os.environ.get("GITLEAKS_BIN", "gitleaks"))
        parser.add_argument("--config", type=Path, default=CONFIG)
        parser.add_argument("--require-root")
        parser.add_argument("--reject-ancestor", action="append", default=[])
        args = parser.parse_args(argv)
        result = scan(args.repo, args.refs or ["HEAD"], binary=args.gitleaks,
                      config=args.config, require_root=args.require_root,
                      reject_ancestors=args.reject_ancestor, ref_names=args.ref_name)
        for finding in result.findings:
            print(f"Secret match: object={finding.oid} type={finding.kind} "
                  f"rule={finding.rule} line={finding.line}; value withheld.")
        if result.findings:
            print(f"Publication blocked: {len(result.findings)} secret matches.")
            return 1
        print(f"Public history checked: {result.refs} references, {result.commits} commits; "
              "no matched credentials.")
        return 0
    except ScanError as exc:
        print(f"Public history scan failed: {exc}.", file=sys.stderr)
        return 2
    except (OSError, ValueError, UnicodeError):
        print("Public history scan failed: local_scan_error.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
