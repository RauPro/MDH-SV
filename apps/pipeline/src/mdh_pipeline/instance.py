"""Validated, non-secret deployment identity from the approved code checkout.

Never infer destinations from HTTP headers, the current directory, a publication
data checkout, or a Git remote. MDH_INSTANCE_FILE is an explicit operator override
and must be an absolute path to an equally trusted configuration file.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_PATH = Path(__file__).resolve().parents[4] / "instance.json"
MAX_BYTES = 32_768
MAX_INTEGER = 9_007_199_254_740_991
FIELDS = frozenset({
    "schema_version", "public_url", "public_repository", "private_repository",
    "public_repository_id", "publisher_actor_id", "mcp_allowed_hosts",
    "editorial_email", "reporting_responsible",
})
LOGIN = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?"
REPOSITORY = re.compile(rf"{LOGIN}/[A-Za-z0-9](?:[A-Za-z0-9_.-]{{0,99}})")
LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


class InstanceConfigurationError(ValueError):
    """Bounded error; untrusted configuration values must never enter logs."""


@dataclass(frozen=True)
class Instance:
    schema_version: int
    public_url: str
    public_repository: str
    private_repository: str
    public_repository_id: int
    publisher_actor_id: int
    mcp_allowed_hosts: tuple[str, ...]
    editorial_email: str
    reporting_responsible: str

    @property
    def public_owner(self):
        return self.public_repository.split("/", 1)[0]


def _fail():
    raise InstanceConfigurationError("invalid_instance_configuration")


def _positive_integer(value):
    # JSON has a number type, not distinct int/float types. Match JavaScript's
    # Number.isSafeInteger for integral forms such as 1.0, excluding booleans.
    return type(value) in {int, float} and 1 <= value <= MAX_INTEGER and int(value) == value


def _hostname(value):
    if value in {"localhost", "127.0.0.1", "[::1]"}:
        return
    if (not value or len(value) > 253 or "." not in value
            or any(not LABEL.fullmatch(label) for label in value.split("."))):
        _fail()
    # WHATWG URLs normalize alternative numeric IP spellings; reject those too.
    if value.rsplit(".", 1)[-1].isdigit():
        _fail()
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return
    _fail()


def _authority(value):
    if not isinstance(value, str) or not value.isascii() or len(value) > 260:
        _fail()
    if value.startswith("[::1]"):
        hostname, suffix = "[::1]", value[5:]
    else:
        hostname, separator, port = value.partition(":")
        suffix = separator + port
    _hostname(hostname)
    if suffix:
        if not re.fullmatch(r":[1-9][0-9]{0,4}", suffix) or int(suffix[1:]) > 65535:
            _fail()
        return hostname, int(suffix[1:])
    return hostname, None


def _public_url(value):
    if (not isinstance(value, str) or not value.isascii() or len(value) > 300
            or re.search(r"[\s\\%?#@]", value)):
        _fail()
    try:
        parts = urlsplit(value)
    except ValueError:
        _fail()
    if (parts.scheme not in {"https", "http"} or parts.path not in {"", "/"}
            or not value.startswith(parts.scheme + "://")):
        _fail()
    hostname, port = _authority(parts.netloc)
    if parts.scheme == "http" and hostname not in {"localhost", "127.0.0.1", "[::1]"}:
        _fail()
    if port == {"http": 80, "https": 443}[parts.scheme]:
        port = None
    authority = hostname + (f":{port}" if port is not None else "")
    return f"{parts.scheme}://{authority}", authority


def validate_instance(value):
    """Validate exact fields and return an immutable, canonical instance."""
    if (not isinstance(value, dict) or set(value) != FIELDS
            or not _positive_integer(value["schema_version"]) or value["schema_version"] != 1):
        _fail()
    public_url, public_host = _public_url(value["public_url"])
    for field in ("public_repository", "private_repository"):
        if not isinstance(value[field], str) or not REPOSITORY.fullmatch(value[field]):
            _fail()
    if value["public_repository"].lower() == value["private_repository"].lower():
        _fail()
    for field in ("public_repository_id", "publisher_actor_id"):
        if not _positive_integer(value[field]):
            _fail()
    hosts = value["mcp_allowed_hosts"]
    if not isinstance(hosts, list) or not 1 <= len(hosts) <= 32:
        _fail()
    for host in hosts:
        _authority(host)
    if len(set(hosts)) != len(hosts) or public_host not in hosts:
        _fail()
    email = value["editorial_email"]
    if (not isinstance(email, str) or not email.isascii() or len(email) > 254
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*@[a-z0-9.-]+", email)):
        _fail()
    email_domain = email.split("@", 1)[1]
    if email_domain in {"localhost", "127.0.0.1"}:
        _fail()
    _hostname(email_domain)
    if not isinstance(value["reporting_responsible"], str) or not re.fullmatch(
            rf"@{LOGIN}(?:/[A-Za-z0-9](?:[A-Za-z0-9_-]{{0,98}}[A-Za-z0-9])?)?",
            value["reporting_responsible"]):
        _fail()
    return Instance(**{**value, "schema_version": 1, "public_url": public_url,
                       "public_repository_id": int(value["public_repository_id"]),
                       "publisher_actor_id": int(value["publisher_actor_id"]),
                       "mcp_allowed_hosts": tuple(hosts)})


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail()
        result[key] = value
    return result


def load_instance(path=None, *, environ=None):
    """Load the approved root file or an explicit absolute operator override."""
    environment = os.environ if environ is None else environ
    configured = path if path is not None else environment.get("MDH_INSTANCE_FILE", DEFAULT_PATH)
    try:
        file = Path(configured)
        if (not file.is_absolute() or file.is_symlink() or not file.is_file()
                or not 0 < file.stat().st_size <= MAX_BYTES):
            _fail()
        with file.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            _fail()
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                              parse_constant=lambda _: _fail())
        return validate_instance(document)
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
        raise InstanceConfigurationError("invalid_instance_configuration") from None
