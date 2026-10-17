"""Validated source identities from the approved code checkout.

Registration permits only the declared processing capabilities, never factual
approval. Hosts are exact: an unlisted subdomain is not implicitly trusted.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit

REGISTRY_PATH = Path(__file__).resolve().parents[4] / "shared/source-registry.json"
MAX_REGISTRY_BYTES = 262_144
REQUIRED = {"id", "name", "kind", "endpoint", "hosts", "feeds"}
OPTIONAL = {"archive_urls", "seed_urls", "access", "notice"}
CONSUMERS = frozenset({"private_queue", "legacy_scraper"})


class SourceRegistryError(ValueError):
    """Controlled diagnostic without arbitrary configuration contents."""


def _fail():
    raise SourceRegistryError("invalid_source_registry")


def _text(value, maximum):
    return (isinstance(value, str) and 0 < len(value) <= maximum
            and value == value.strip(" \t\n\r\v\f\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff") and not re.search(r"[\x00-\x1f\x7f]", value))


def _host(value):
    return (isinstance(value, str) and len(value) <= 253 and "." in value
            and not re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]*)", value.rsplit(".", 1)[-1])
            and not any(label.startswith("xn--") for label in value.split("."))
            and all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                    for label in value.split(".")))


def _hosts(value):
    if (not isinstance(value, list) or not 1 <= len(value) <= 100
            or not all(_host(host) for host in value) or len(set(value)) != len(value)):
        _fail()


def _url(value, hosts):
    if (not _text(value, 4096) or not value.isascii()
            or re.search(r"[\s\\%?#]", value)):
        _fail()
    try:
        parts = urlsplit(value)
        if (parts.scheme != "https" or not value.startswith("https://")
                or parts.hostname not in hosts or parts.netloc != parts.hostname
                or not parts.path.startswith("/")):
            _fail()
    except ValueError:
        _fail()


def validate_registry(value):
    """Validate the whole registry before exposing any host or capability."""
    if (not isinstance(value, dict)
            or set(value) != {"schema_version", "publishers", "primary_evidence_hosts",
                              "official_statistics_hosts", "official_statistics_discovery_extra_hosts"}
            or type(value["schema_version"]) not in (int, float) or value["schema_version"] != 1
            or not isinstance(value["publishers"], list) or not 1 <= len(value["publishers"]) <= 100):
        _fail()
    _hosts(value["primary_evidence_hosts"])
    for role in ("official_statistics_hosts", "official_statistics_discovery_extra_hosts"):
        _hosts(value[role])
    host_roles = [value[key] for key in ("primary_evidence_hosts", "official_statistics_hosts",
                                       "official_statistics_discovery_extra_hosts")]
    all_role_hosts = [host for hosts in host_roles for host in hosts]
    if len(set(all_role_hosts)) != len(all_role_hosts):
        _fail()
    used_ids, used_names, used_hosts = set(), set(), set(all_role_hosts)
    for source in value["publishers"]:
        if (not isinstance(source, dict) or not REQUIRED <= set(source)
                or set(source) - REQUIRED - OPTIONAL):
            _fail()
        if (not isinstance(source["id"], str)
                or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", source["id"])
                or len(source["id"]) > 80 or source["id"] in used_ids
                or not _text(source["name"], 200) or source["name"] in used_names
                or source["kind"] not in ("wordpress", "html_archive")):
            _fail()
        _hosts(source["hosts"])
        if used_hosts.intersection(source["hosts"]):
            _fail()
        used_ids.add(source["id"])
        used_names.add(source["name"])
        used_hosts.update(source["hosts"])
        _url(source["endpoint"], source["hosts"])
        if source["kind"] == "wordpress" and set(source).intersection(OPTIONAL):
            _fail()
        if source["kind"] == "html_archive":
            if ("archive_urls" not in source or not _text(source.get("notice"), 2000)
                    or source.get("access", "public") not in ("public", "restricted")):
                _fail()
            for key in ("archive_urls", "seed_urls"):
                urls = source.get(key, [])
                if (not isinstance(urls, list) or len(urls) > 100
                        or any(not isinstance(url, str) for url in urls)
                        or len(set(urls)) != len(urls)):
                    _fail()
                for url in urls:
                    _url(url, source["hosts"])
        feeds = source["feeds"]
        if not isinstance(feeds, list) or len(feeds) > 10:
            _fail()
        if source.get("access") == "restricted" and (feeds or source.get("archive_urls") or source.get("seed_urls")):
            _fail()
        used_feeds, used_consumers = set(), set()
        for feed in feeds:
            if not isinstance(feed, dict) or set(feed) != {"url", "consumers"}:
                _fail()
            _url(feed["url"], source["hosts"])
            consumers = feed["consumers"]
            if (feed["url"] in used_feeds or not isinstance(consumers, list)
                    or not consumers or any(not isinstance(consumer, str) for consumer in consumers)
                    or not set(consumers) <= CONSUMERS or len(set(consumers)) != len(consumers)
                    or used_consumers.intersection(consumers)):
                _fail()
            used_feeds.add(feed["url"])
            used_consumers.update(consumers)
    return {**deepcopy(value), "schema_version": 1}


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail()
        result[key] = value
    return result


def load_registry(path=REGISTRY_PATH):
    """Use a fixed code-relative file, never a publication checkout or env override."""
    try:
        path = Path(path)
        if not path.is_absolute() or path.is_symlink() or not path.is_file():
            _fail()
        with path.open("rb") as stream:
            raw = stream.read(MAX_REGISTRY_BYTES + 1)
        if not 0 < len(raw) <= MAX_REGISTRY_BYTES:
            _fail()
        return validate_registry(json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                                            parse_constant=lambda _: _fail()))
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
        raise SourceRegistryError("invalid_source_registry") from None


def news_sources(registry):
    return tuple({key: deepcopy(value) for key, value in source.items() if key != "feeds"}
                 for source in validate_registry(registry)["publishers"])


def registered_feeds(consumer, registry=None):
    if consumer not in CONSUMERS:
        _fail()
    validated = load_registry() if registry is None else validate_registry(registry)
    return [{"name": source["name"], "url": feed["url"], "hosts": list(source["hosts"])}
            for source in validated["publishers"] for feed in source["feeds"]
            if consumer in feed["consumers"]]


_registry = load_registry()
SOURCES = news_sources(_registry)
NEWS_HOSTS = frozenset(host for source in SOURCES for host in source["hosts"])
PRIMARY_SOURCE_HOSTS = NEWS_HOSTS | frozenset(_registry["primary_evidence_hosts"])
OFFICIAL_STATISTICS_HOSTS = frozenset(_registry["official_statistics_hosts"])
OFFICIAL_STATISTICS_DISCOVERY_HOSTS = (OFFICIAL_STATISTICS_HOSTS
                                      | frozenset(_registry["official_statistics_discovery_extra_hosts"]))
del _registry
