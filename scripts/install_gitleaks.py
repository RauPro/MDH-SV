"""Install a SHA256-pinned Gitleaks release locally, without executing downloaded code.

The cached archive is authenticated on every run and the binary is always replaced
from that archive. A modified binary or cache marker therefore cannot be trusted.
Only the installation directory is written to stdout (suitable for GITHUB_PATH).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import stat
import sys
import tarfile
import tempfile
from http.client import HTTPException
from pathlib import Path, PurePosixPath
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / ".security/gitleaks-version.json"
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_BINARY_BYTES = 128 * 1024 * 1024
MAX_UNPACKED_BYTES = 256 * 1024 * 1024
MAX_MEMBERS = 128
CHUNK_BYTES = 1024 * 1024
DOWNLOAD_HOSTS = {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}


class InstallError(Exception):
    """A safe, fixed error code; never include upstream errors or response bodies."""


def _check_download_url(url: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in DOWNLOAD_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
    ):
        raise InstallError("untrusted-download-url")


class HTTPSReleaseRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_download_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def current_platform() -> str:
    system = platform.system().lower()
    architecture = {"arm64": "arm64", "aarch64": "arm64", "amd64": "x64", "x86_64": "x64"}.get(
        platform.machine().lower()
    )
    if system not in {"darwin", "linux"} or architecture is None:
        raise InstallError("unsupported-platform")
    return f"{system}_{architecture}"


def load_manifest(path: Path = MANIFEST) -> dict:
    try:
        manifest = json.loads(path.read_text())
        version = manifest["version"]
        if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
            raise ValueError
        release = f"https://github.com/gitleaks/gitleaks/releases/tag/v{version}"
        if manifest["source_release"] != release:
            raise ValueError
        if set(manifest["artifacts"]) != {"darwin_arm64", "darwin_x64", "linux_arm64", "linux_x64"}:
            raise ValueError
        for target, artifact in manifest["artifacts"].items():
            expected_url = (
                f"https://github.com/gitleaks/gitleaks/releases/download/v{version}/"
                f"gitleaks_{version}_{target}.tar.gz"
            )
            if artifact["url"] != expected_url or not re.fullmatch(r"[a-f0-9]{64}", artifact["sha256"]):
                raise ValueError
        return manifest
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise InstallError("invalid-release-manifest") from None


def authenticate_archive(path: Path, expected_sha256: str) -> None:
    """Validate the complete, bounded archive before parsing any tar headers."""
    if not stat.S_ISREG(path.lstat().st_mode):
        raise InstallError("invalid-archive-size-or-type")
    with path.open("rb") as source:
        metadata = os.fstat(source.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_ARCHIVE_BYTES:
            raise InstallError("invalid-archive-size-or-type")
        digest = hashlib.sha256()
        total = 0
        while chunk := source.read(CHUNK_BYTES):
            total += len(chunk)
            if total > MAX_ARCHIVE_BYTES:
                raise InstallError("archive-size-limit")
            digest.update(chunk)
    if digest.hexdigest() != expected_sha256:
        raise InstallError("archive-checksum-mismatch")


def download_archive(url: str, output: Path) -> None:
    """Use Python's verified TLS defaults and restrict every redirect to release hosts."""
    _check_download_url(url)
    opener = build_opener(HTTPSReleaseRedirects())
    request = Request(url, headers={"User-Agent": "mdh-sv-pinned-gitleaks-installer"})
    try:
        with opener.open(request, timeout=30) as response, output.open("wb") as destination:
            _check_download_url(response.geturl())
            length = response.headers.get("Content-Length")
            if length is not None and (not length.isdecimal() or int(length) > MAX_ARCHIVE_BYTES):
                raise InstallError("archive-size-limit")
            total = 0
            while chunk := response.read(CHUNK_BYTES):
                total += len(chunk)
                if total > MAX_ARCHIVE_BYTES:
                    raise InstallError("archive-size-limit")
                destination.write(chunk)
    except (URLError, HTTPException, OSError, ValueError):
        raise InstallError("download-failed") from None


def extract_binary(archive_path: Path, output: Path) -> None:
    """Read only the exact regular `gitleaks` member; never extract archive paths."""
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            binary = None
            unpacked = 0
            for count, member in enumerate(archive, start=1):
                unpacked += member.size
                if count > MAX_MEMBERS or member.size < 0 or unpacked > MAX_UNPACKED_BYTES:
                    raise InstallError("archive-payload-limit")
                parts = PurePosixPath(member.name)
                if parts.is_absolute() or ".." in parts.parts or "\\" in member.name:
                    raise InstallError("unsafe-archive-member")
                if not member.isfile():
                    raise InstallError("unsafe-archive-member")
                if member.name == "gitleaks":
                    if binary is not None or not 0 < member.size <= MAX_BINARY_BYTES:
                        raise InstallError("invalid-binary-member")
                    binary = member
            if binary is None:
                raise InstallError("missing-binary-member")
            source = archive.extractfile(binary)
            if source is None:
                raise InstallError("invalid-binary-member")
            total = 0
            with source, output.open("wb") as destination:
                while chunk := source.read(CHUNK_BYTES):
                    total += len(chunk)
                    if total > MAX_BINARY_BYTES:
                        raise InstallError("binary-size-limit")
                    destination.write(chunk)
            if total != binary.size:
                raise InstallError("invalid-binary-size")
    except (tarfile.TarError, EOFError, OSError):
        raise InstallError("invalid-release-archive") from None


def install(destination: Path | None = None, manifest_path: Path = MANIFEST) -> Path:
    manifest = load_manifest(manifest_path)
    target = current_platform()
    artifact = manifest["artifacts"][target]
    try:
        destination = (
            destination or ROOT / ".local-quarantine/tools/gitleaks" / manifest["version"] / target
        ).resolve()
        destination.mkdir(parents=True, exist_ok=True)
        archive_path = destination / ".release.tar.gz"
        if archive_path.is_symlink():
            raise InstallError("unsafe-archive-cache")
        with tempfile.TemporaryDirectory(prefix=".install-", dir=destination) as temporary:
            temporary = Path(temporary)
            if archive_path.exists():
                authenticate_archive(archive_path, artifact["sha256"])
            else:
                downloaded = temporary / "release.tar.gz"
                download_archive(artifact["url"], downloaded)
                authenticate_archive(downloaded, artifact["sha256"])
                os.chmod(downloaded, 0o600)
                os.replace(downloaded, archive_path)
            binary = temporary / "gitleaks"
            extract_binary(archive_path, binary)
            os.chmod(binary, 0o755)
            os.replace(binary, destination / "gitleaks")
        return destination
    except OSError:
        raise InstallError("installation-io-failed") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, help="Local directory for the pinned binary")
    args = parser.parse_args(argv)
    try:
        destination = install(args.destination)
    except InstallError as error:
        print(f"Gitleaks installation failed: {error}", file=sys.stderr)
        return 1
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
