"""Pinned downloads and extraction fail closed without requiring network access."""

import hashlib
import importlib.util
import io
import json
import os
import stat
import tarfile
import tomllib
from http.client import HTTPException
from pathlib import Path
from urllib.error import URLError

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("install_gitleaks", ROOT / "scripts/install_gitleaks.py")
INSTALLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALLER)


def archive_bytes(members=None):
    members = members if members is not None else [("gitleaks", b"test binary", tarfile.REGTYPE)]
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode="w:gz") as archive:
        for name, content, kind in members:
            info = tarfile.TarInfo(name)
            info.type = kind
            if kind == tarfile.REGTYPE:
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
            else:
                info.linkname = "../../outside"
                archive.addfile(info)
    return result.getvalue()


@pytest.fixture
def setup_install(tmp_path, monkeypatch):
    def prepare(payload=None):
        payload = archive_bytes() if payload is None else payload
        manifest = INSTALLER.load_manifest()
        manifest["artifacts"]["linux_x64"]["sha256"] = hashlib.sha256(payload).hexdigest()
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        monkeypatch.setattr(INSTALLER, "current_platform", lambda: "linux_x64")
        calls = []

        def download(url, output):
            calls.append(url)
            output.write_bytes(payload)

        monkeypatch.setattr(INSTALLER, "download_archive", download)
        return tmp_path / "installation", manifest_path, calls

    return prepare


def test_installs_only_regular_binary_and_replaces_tampered_binary_from_verified_cache(setup_install):
    payload = archive_bytes([
        ("LICENSE", b"license", tarfile.REGTYPE),
        ("README.md", b"documentation", tarfile.REGTYPE),
        ("gitleaks", b"test binary", tarfile.REGTYPE),
    ])
    destination, manifest, downloads = setup_install(payload)
    result = INSTALLER.install(destination, manifest)
    assert result == destination.resolve()
    assert (destination / "gitleaks").read_bytes() == b"test binary"
    assert stat.S_IMODE((destination / "gitleaks").stat().st_mode) == 0o755
    assert stat.S_IMODE((destination / ".release.tar.gz").stat().st_mode) == 0o600
    assert not (destination / "README.md").exists()
    assert not (destination / "LICENSE").exists()
    (destination / "gitleaks").write_bytes(b"replaced binary")
    INSTALLER.install(destination, manifest)
    assert (destination / "gitleaks").read_bytes() == b"test binary"
    assert len(downloads) == 1
    assert not list(destination.glob(".install-*"))


def test_download_checksum_is_checked_before_any_tar_parsing(setup_install, monkeypatch):
    destination, manifest, _ = setup_install()
    monkeypatch.setattr(INSTALLER, "download_archive", lambda url, output: output.write_bytes(b"wrong"))

    def must_not_extract(*args):
        raise AssertionError("unverified archive was parsed")

    monkeypatch.setattr(INSTALLER, "extract_binary", must_not_extract)
    with pytest.raises(INSTALLER.InstallError, match="^archive-checksum-mismatch$"):
        INSTALLER.install(destination, manifest)
    assert not (destination / "gitleaks").exists()
    assert not (destination / ".release.tar.gz").exists()
    assert not list(destination.glob(".install-*"))


def test_cached_archive_is_authenticated_again_and_existing_binary_is_preserved(setup_install):
    destination, manifest, downloads = setup_install()
    INSTALLER.install(destination, manifest)
    (destination / ".release.tar.gz").write_bytes(b"tampered archive")
    with pytest.raises(INSTALLER.InstallError, match="^archive-checksum-mismatch$"):
        INSTALLER.install(destination, manifest)
    assert (destination / "gitleaks").read_bytes() == b"test binary"
    assert len(downloads) == 1


@pytest.mark.parametrize("members,code", [
    ([("gitleaks", b"", tarfile.SYMTYPE)], "unsafe-archive-member"),
    ([("gitleaks", b"", tarfile.LNKTYPE)], "unsafe-archive-member"),
    ([("../gitleaks", b"binary", tarfile.REGTYPE)], "unsafe-archive-member"),
    ([("/tmp/gitleaks", b"binary", tarfile.REGTYPE)], "unsafe-archive-member"),
    ([("..\\gitleaks", b"binary", tarfile.REGTYPE)], "unsafe-archive-member"),
    ([("nested/gitleaks", b"binary", tarfile.REGTYPE)], "missing-binary-member"),
    ([("gitleaks", b"", tarfile.REGTYPE)], "invalid-binary-member"),
    ([("README.md", b"docs", tarfile.REGTYPE)], "missing-binary-member"),
    ([("gitleaks", b"one", tarfile.REGTYPE), ("gitleaks", b"two", tarfile.REGTYPE)],
     "invalid-binary-member"),
])
def test_rejects_unsafe_or_ambiguous_members_without_installing(setup_install, members, code):
    destination, manifest, _ = setup_install(archive_bytes(members))
    with pytest.raises(INSTALLER.InstallError, match=f"^{code}$"):
        INSTALLER.install(destination, manifest)
    assert not (destination / "gitleaks").exists()
    assert not (destination.parent / "gitleaks").exists()


def test_rejects_archive_cache_symlink(setup_install, tmp_path):
    destination, manifest, downloads = setup_install()
    destination.mkdir()
    target = tmp_path / "outside"
    target.write_bytes(b"unchanged")
    (destination / ".release.tar.gz").symlink_to(target)
    with pytest.raises(INSTALLER.InstallError, match="^unsafe-archive-cache$"):
        INSTALLER.install(destination, manifest)
    assert target.read_bytes() == b"unchanged"
    assert downloads == []


def test_rejects_fifo_archive_without_blocking(tmp_path):
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(INSTALLER.InstallError, match="^invalid-archive-size-or-type$"):
        INSTALLER.authenticate_archive(fifo, "0" * 64)


@pytest.mark.parametrize("limit,code", [
    ("MAX_ARCHIVE_BYTES", "invalid-archive-size-or-type"),
    ("MAX_BINARY_BYTES", "invalid-binary-member"),
    ("MAX_UNPACKED_BYTES", "archive-payload-limit"),
    ("MAX_MEMBERS", "archive-payload-limit"),
])
def test_archive_and_payload_limits(setup_install, monkeypatch, limit, code):
    destination, manifest, _ = setup_install()
    monkeypatch.setattr(INSTALLER, limit, 0)
    with pytest.raises(INSTALLER.InstallError, match=f"^{code}$"):
        INSTALLER.install(destination, manifest)
    assert not (destination / "gitleaks").exists()


def test_invalid_archive_uses_safe_error(setup_install):
    destination, manifest, _ = setup_install(b"not an archive")
    with pytest.raises(INSTALLER.InstallError, match="^invalid-release-archive$"):
        INSTALLER.install(destination, manifest)


class FakeResponse(io.BytesIO):
    def __init__(self, content, length=None, url="https://release-assets.githubusercontent.com/file"):
        super().__init__(content)
        self.headers = {} if length is None else {"Content-Length": length}
        self.url = url

    def geturl(self):
        return self.url


def mock_response(monkeypatch, response):
    class Opener:
        def open(self, request, timeout):
            assert timeout == 30
            return response

    monkeypatch.setattr(INSTALLER, "build_opener", lambda handler: Opener())


@pytest.mark.parametrize("length", [None, "10000000000", "invalid"])
def test_download_is_bounded_with_or_without_content_length(tmp_path, monkeypatch, length):
    monkeypatch.setattr(INSTALLER, "MAX_ARCHIVE_BYTES", 5)
    mock_response(monkeypatch, FakeResponse(b"123456", length))
    with pytest.raises(INSTALLER.InstallError, match="^archive-size-limit$"):
        INSTALLER.download_archive("https://github.com/release", tmp_path / "download")


def test_download_preserves_bytes_and_checks_final_url(tmp_path, monkeypatch):
    output = tmp_path / "download"
    mock_response(monkeypatch, FakeResponse(b"payload", "7"))
    INSTALLER.download_archive("https://github.com/release", output)
    assert output.read_bytes() == b"payload"
    mock_response(monkeypatch, FakeResponse(b"payload", url="http://github.com/release"))
    with pytest.raises(INSTALLER.InstallError, match="^untrusted-download-url$"):
        INSTALLER.download_archive("https://github.com/release", output)


@pytest.mark.parametrize("url", [
    "http://github.com/release", "https://evil.example/release",
    "https://user:password@github.com/release", "https://github.com:8443/release",
])
def test_redirects_refuse_non_https_or_untrusted_destinations(url):
    with pytest.raises(INSTALLER.InstallError, match="^untrusted-download-url$"):
        INSTALLER.HTTPSReleaseRedirects().redirect_request(None, None, 302, "", {}, url)


@pytest.mark.parametrize("error_type", [URLError, HTTPException])
def test_network_failures_hide_upstream_details(tmp_path, monkeypatch, error_type):
    class FailedOpener:
        def open(self, *args, **kwargs):
            raise error_type("sensitive upstream response body")

    monkeypatch.setattr(INSTALLER, "build_opener", lambda handler: FailedOpener())
    with pytest.raises(INSTALLER.InstallError, match="^download-failed$"):
        INSTALLER.download_archive("https://github.com/release", tmp_path / "download")


def test_cli_prints_only_directory_on_success_and_safe_code_on_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(INSTALLER, "install", lambda destination: destination.resolve())
    assert INSTALLER.main(["--destination", str(tmp_path)]) == 0
    captured = capsys.readouterr()
    assert captured.out == str(tmp_path.resolve()) + "\n" and captured.err == ""

    def fail(destination):
        raise INSTALLER.InstallError("download-failed")

    monkeypatch.setattr(INSTALLER, "install", fail)
    assert INSTALLER.main([]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Gitleaks installation failed: download-failed\n"


@pytest.mark.parametrize("system,machine,target", [
    ("Darwin", "arm64", "darwin_arm64"), ("Darwin", "x86_64", "darwin_x64"),
    ("Linux", "aarch64", "linux_arm64"), ("Linux", "AMD64", "linux_x64"),
])
def test_supported_platform_mapping(monkeypatch, system, machine, target):
    monkeypatch.setattr(INSTALLER.platform, "system", lambda: system)
    monkeypatch.setattr(INSTALLER.platform, "machine", lambda: machine)
    assert INSTALLER.current_platform() == target


def test_unsupported_platform_fails_closed(monkeypatch):
    monkeypatch.setattr(INSTALLER.platform, "system", lambda: "Windows")
    with pytest.raises(INSTALLER.InstallError, match="^unsupported-platform$"):
        INSTALLER.current_platform()


def test_manifest_and_config_preserve_pinning_and_default_rules():
    assert INSTALLER.load_manifest()["version"] == "8.30.1"
    config = tomllib.loads((ROOT / ".security/gitleaks.toml").read_text())
    assert config["extend"] == {"useDefault": True}
    assert "allowlists" not in config and "allowlist" not in config
    assert config["rules"][0]["id"] == "mdh-google-api-key"
    assert all("allowlists" not in rule and "allowlist" not in rule for rule in config["rules"])


def test_manifest_rejects_unpinned_or_redirected_artifacts(tmp_path):
    manifest = INSTALLER.load_manifest()
    manifest["artifacts"]["linux_x64"]["url"] = "https://evil.example/release.tar.gz"
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    with pytest.raises(INSTALLER.InstallError, match="^invalid-release-manifest$"):
        INSTALLER.load_manifest(path)
