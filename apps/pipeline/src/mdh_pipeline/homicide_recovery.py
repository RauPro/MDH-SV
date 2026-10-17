"""Recover a bounded, resumable year of homicide references for PRIVATE review.

No Gemini, article-body persistence, public export, editorial selection or GitHub
writes. Publisher dates, approved HTTPS hosts and bibliography validation remain
mandatory. Discovery keywords are deliberately independent of editorial approval.
"""
from __future__ import annotations

import argparse
import calendar
import html
import json
import os
import re
import tempfile
import time
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from .archive_sources import (
    ArchiveError, BudgetExhausted, MAX_ARTICLE_BYTES, MAX_INDEX_BYTES,
    _article_url, _blocked, parse_article_metadata, parse_index,
)
from .history import ROOT, TOPICS, page_url, reference_from_metadata, validate_reference
from .publishers import SOURCES
from .review_queue import canonical_url, normalize, require_public_dns

VERSION = 1
MAX_JOBS = 200_000
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_ATTEMPTS = 3
USER_AGENT = "MDH-SV/2.5 private-homicide-recovery (+https://github.com/RauPro/MDH-SV)"
ADDITIONAL_TERMS = ("matan", "mataron", "mato", "mata ", "matanza", "masacr", "acribill", "ultimad", "muert")
RECOVERY_TOPICS = {**TOPICS, "violencia_y_desapariciones": (*TOPICS["violencia_y_desapariciones"], *ADDITIONAL_TERMS)}
EXPLICIT = re.compile(r"\b(?:homicid\w*|asesin\w*|feminicid\w*|masacr\w*|matanzas?|acribill\w*|ultimad\w*|matan|mataron|mato|mata)\b")
DEATH = re.compile(r"\bmuert[oa]s?\b")
VIOLENT = re.compile(r"\b(?:bala\w*|balead\w*|tirote\w*|ataqu\w*|atacad\w*|arma\w*|dispar\w*|pandill\w*|violencia|apu[nñ]alad\w*)\b")
NONVIOLENT = re.compile(r"\b(?:lluvias?|tormenta\w*|terremoto\w*|huracan\w*|inundaci\w*|accidente\w*|covid\w*|cancer|enfermedad\w*)\b")
COUNT_KEYS = {"invalid_metadata", "outside_year", "not_relevant", "duplicate_matches", "matched_existing", "new_matches", "posts_scanned"}
NOTICE = "Recuperación bibliográfica parcial por metadatos y términos de homicidios. Cada referencia exige revisión editorial y de privacidad antes de incorporarse al archivo público. No acredita hechos, víctimas, jurisdicción ni cobertura completa."


class RecoveryError(ValueError):
    pass


def homicide_hint(title, url=""):
    """A discovery hint, not a finding that a homicide or violation occurred."""
    text = normalize(html.unescape(re.sub(r"<[^>]*>", " ", title)) + " " + unquote(urlsplit(url).path))
    text = re.sub(r"[-_/]+", " ", text)
    matches = sorted({match.group(0) for match in EXPLICIT.finditer(text)})
    if matches:
        # 'Mata' alone also appears in natural-disaster/illness headlines.
        strong = [term for term in matches if term not in ("mata", "mato", "matan", "mataron")]
        return strong or ([] if NONVIOLENT.search(text) else matches)
    if DEATH.search(text) and VIOLENT.search(text) and not NONVIOLENT.search(text):
        return sorted({match.group(0) for pattern in (DEATH, VIOLENT) for match in pattern.finditer(text)})
    return []


def reference_from_homicide_metadata(metadata, source, year, retrieved_at):
    title = metadata.get("title")
    if not isinstance(title, str) or not title.strip():
        raise RecoveryError("invalid_title")
    published = date.fromisoformat(metadata["published_date"])
    if published.year != year:
        return None, "outside_year"
    url = canonical_url(metadata["source_url"], set(source["hosts"]))
    if not homicide_hint(title, url):
        return None, "not_relevant"
    reference = reference_from_metadata(title, url, published.isoformat(), source,
        date(year, 1, 1), date(year, 12, 31), retrieved_at,
        source_post_id=metadata.get("source_post_id"), topic_terms=RECOVERY_TOPICS)
    if reference is None:
        raise RecoveryError("unclassified_homicide_hint")
    validate_reference(reference, source)
    return reference, "matched"


def identity_keys(reference, source):
    """Merge only approved www/slash aliases and stable source-local WP IDs."""
    url = canonical_url(reference["source_url"], set(source["hosts"]))
    parts = urlsplit(url)
    host = parts.hostname
    if host.startswith("www.") and host[4:] in source["hosts"]:
        host = host[4:]
    keys = {f"{source['id']}:url:{host}{parts.path.rstrip('/')}"}
    post = reference.get("discovery", {}).get("source_post_id")
    if source.get("kind", "wordpress") == "wordpress" and type(post) is int and post > 0:
        keys.add(f"{source['id']}:wp:{post}")
    return keys


def read_json(path):
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise RecoveryError("input_too_large")
    return json.loads(path.read_text(encoding="utf-8"))


def load_baseline(directory, sources=SOURCES):
    records, source_counts = [], Counter()
    for source in sources:
        path = directory / f"{source['id']}.json"
        document = read_json(path)  # A missing baseline must not silently disable deduplication.
        if document.get("source_id") != source["id"] or not isinstance(document.get("records"), list):
            raise RecoveryError("invalid_baseline")
        for reference in document["records"]:
            validate_reference(reference, source)
            records.append(reference)
            source_counts[(source["id"], reference["published_date"][:4])] += 1
    return records, source_counts


def private_directory(directory, baseline):
    resolved = directory.resolve()
    root = ROOT.resolve()
    if resolved == root or (resolved.is_relative_to(root) and not resolved.is_relative_to(root / ".local-quarantine")):
        raise RecoveryError("output_must_be_private")
    if resolved == baseline.resolve() or resolved.is_relative_to(baseline.resolve()):
        raise RecoveryError("output_overlaps_baseline")
    for name in ("state.json", "report.json", "candidates.json"):
        if (resolved / name).is_symlink():
            raise RecoveryError("unsafe_output_symlink")
    return resolved


def write_private(directory, name, document):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
        stream.write((json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode())
        temporary = Path(stream.name)
    os.chmod(temporary, 0o600)
    temporary.replace(directory / name)


def request_metadata(url, source, session, budget, *, limit, delay):
    """Every redirect counts; access denial stops the source without fallback."""
    for _ in range(4):
        try:
            parts = urlsplit(url)
            safe = (parts.scheme == "https" and parts.hostname in source["hosts"]
                    and not parts.username and not parts.password and not parts.port
                    and not any(ord(c) < 32 or c in '<>\\' for c in url))
        except ValueError:
            safe = False
        if not safe:
            raise ArchiveError("unsafe_url")
        if budget["requests"] >= budget["limit"]:
            raise BudgetExhausted()
        try:
            require_public_dns(parts.hostname)
        except (OSError, ValueError) as error:
            raise ArchiveError("unsafe_dns") from error
        if delay:
            time.sleep(delay)
        budget["requests"] += 1
        try:
            with session.get(url, timeout=(8, 20), stream=True, allow_redirects=False,
                    headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/html,application/xml,text/xml",
                             "Authorization": None, "Cookie": None}) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    if not response.headers.get("Location"):
                        raise ArchiveError("invalid_redirect")
                    url = urljoin(url, response.headers["Location"])
                    continue
                if response.status_code in (401, 402, 403, 429):
                    raise ArchiveError("access_restricted")
                if response.status_code != 200:
                    raise ArchiveError(f"http_{response.status_code}")
                content, deadline = bytearray(), time.monotonic() + 60
                for chunk in response.iter_content(65536):
                    content.extend(chunk)
                    budget["response_bytes"] += len(chunk)
                    if len(content) > limit:
                        raise ArchiveError("response_too_large")
                    if time.monotonic() > deadline:
                        raise ArchiveError("response_deadline")
                return bytes(content), dict(response.headers), url
        except requests.RequestException as error:
            raise ArchiveError("network_error") from error
    raise ArchiveError("too_many_redirects")


def _job_key(job):
    return (job["kind"], job.get("url"), job.get("month"), job.get("page"))


def _add_job(state, job):
    _add_jobs(state, [job])


def _add_jobs(state, new_jobs):
    jobs = state["jobs"]
    known = {_job_key(saved) for saved in jobs}
    for job in new_jobs:
        key = _job_key(job)
        if key not in known:
            if len(jobs) >= MAX_JOBS:
                raise RecoveryError("too_many_discovered_jobs")
            jobs.append({**job, "status": "pending", "attempts": 0, "error": None})
            known.add(key)


def _validate_state(state, year, sources):
    if (not isinstance(state, dict) or set(state) != {"schema_version", "year", "created_at", "updated_at", "sources", "records", "requests_total"}
            or state.get("schema_version") != VERSION
            or state.get("year") != year or not isinstance(state.get("records"), list)
            or not isinstance(state.get("sources"), dict)
            or type(state.get("requests_total")) is not int or state["requests_total"] < 0):
        raise RecoveryError("invalid_state")
    source_map = {source["id"]: source for source in sources}
    if set(state["sources"]) - set(source_map):
        raise RecoveryError("unknown_state_source")
    for reference in state["records"]:
        source = source_map.get(reference.get("source_id"))
        if source is None or not reference.get("published_date", "").startswith(f"{year}-"):
            raise RecoveryError("invalid_state_reference")
        validate_reference(reference, source)
    for source_id, saved in state["sources"].items():
        source = source_map[source_id]
        if (not isinstance(saved, dict) or set(saved) != {"jobs", "counts", "seen", "blocked"}
                or not isinstance(saved.get("jobs"), list)
                or len(saved["jobs"]) > MAX_JOBS or type(saved.get("blocked")) is not bool
                or not isinstance(saved.get("counts"), dict) or not isinstance(saved.get("seen"), list)
                or len(saved["seen"]) > MAX_JOBS * 2
                or any(not isinstance(key, str) for key in saved["seen"])
                or set(saved["counts"]) - COUNT_KEYS
                or any(type(value) is not int or value < 0 for value in saved["counts"].values())):
            raise RecoveryError("invalid_source_state")
        for job in saved["jobs"]:
            permitted = {"kind", "url", "month", "page", "status", "attempts", "error", "last_attempt_at",
                         "source_total_pages", "source_total_posts", "article_urls_observed", "article_urls_selected"}
            if (set(job) - permitted or job.get("status") not in ("pending", "done", "failed", "restricted")
                    or type(job.get("attempts")) is not int or not 0 <= job["attempts"] <= MAX_ATTEMPTS):
                raise RecoveryError("invalid_state_job")
            if job.get("kind") == "wordpress":
                if (source.get("kind", "wordpress") != "wordpress"
                        or job.get("month") not in [f"{year}-{month:02}" for month in range(1, 13)]
                        or type(job.get("page")) is not int or not 1 <= job["page"] <= 100_000):
                    raise RecoveryError("invalid_state_wordpress_job")
            elif job.get("kind") in ("index", "article"):
                canonical_url(job["url"], set(source["hosts"]))
            else:
                raise RecoveryError("invalid_state_job_kind")


def _index_articles(raw, content_type, url, source):
    articles, indexes = parse_index(raw, content_type, url, source)
    selected = {article for article in articles if homicide_hint("", article)}
    if "xml" not in content_type.lower() and not raw.lstrip().startswith((b"<?xml", b"<urlset", b"<sitemapindex")):
        soup = BeautifulSoup(raw, "html.parser")
        _blocked(soup)
        for anchor in soup.find_all("a", href=True):
            article = _article_url(anchor["href"], url, source)
            if article and homicide_hint(anchor.get_text(" ", strip=True), article):
                selected.add(article)
    return sorted(selected), indexes, len(articles)


def validate_recovery_report(report, candidates, *, sources=SOURCES):
    """Validate the entire private review boundary before any queue mutation."""
    expected = {"schema_version", "year", "generated_at", "requests", "requests_total", "response_bytes",
                "new_candidates", "publications", "gemini_requests", "review_required", "coverage", "sources", "notice"}
    if (not isinstance(report, dict) or set(report) != expected or type(report["schema_version"]) is not int or report["schema_version"] != 1
            or type(report["year"]) is not int or not 2010 <= report["year"] < 2100
            or report["publications"] != 0 or type(report["publications"]) is not int
            or report["gemini_requests"] != 0 or type(report["gemini_requests"]) is not int
            or report["review_required"] is not True or report["coverage"] != "partial" or report["notice"] != NOTICE):
        raise RecoveryError("invalid_recovery_report")
    if (not isinstance(candidates, dict) or set(candidates) != {"schema_version", "generated_at", "records"}
            or type(candidates["schema_version"]) is not int or candidates["schema_version"] != 1 or candidates["generated_at"] != report["generated_at"]
            or not isinstance(candidates["records"], list)):
        raise RecoveryError("invalid_candidate_envelope")
    generated = datetime.fromisoformat(report["generated_at"])
    if generated.tzinfo is None:
        raise RecoveryError("invalid_report_timestamp")
    for key in ("requests", "requests_total", "response_bytes", "new_candidates"):
        if type(report[key]) is not int or not 0 <= report[key] <= 100_000_000_000:
            raise RecoveryError("invalid_report_count")
    if (report["requests"] > 1000 or report["requests_total"] < report["requests"]
            or report["response_bytes"] > report["requests"] * MAX_INDEX_BYTES
            or report["new_candidates"] != len(candidates["records"])):
        raise RecoveryError("inconsistent_report_count")
    source_map = {source["id"]: source for source in sources}
    counts, identities = Counter(), set()
    for reference in candidates["records"]:
        source = source_map.get(reference.get("source_id"))
        if source is None or not reference.get("published_date", "").startswith(f"{report['year']}-"):
            raise RecoveryError("invalid_candidate_year_or_source")
        try:
            validate_reference(reference, source)
        except ValueError:
            raise RecoveryError("invalid_candidate_reference") from None
        retrieved = datetime.fromisoformat(reference["discovery"]["retrieved_at"])
        if retrieved.tzinfo is None or retrieved > generated:
            raise RecoveryError("invalid_candidate_timestamp")
        keys = identity_keys(reference, source)
        if identities & keys:
            raise RecoveryError("duplicate_candidate")
        identities.update(keys)
        counts[source["id"]] += 1
    if not isinstance(report["sources"], list) or len(report["sources"]) > len(sources):
        raise RecoveryError("invalid_report_sources")
    source_ids = set()
    baseline_key = "baseline_2015" if report["year"] == 2015 else "baseline_year"
    for row in report["sources"]:
        if set(row) != {"source_id", "publisher", baseline_key, "new_candidates", "counts", "blocked", "jobs_done", "jobs_pending", "errors"}:
            raise RecoveryError("invalid_report_source_fields")
        source = source_map.get(row["source_id"])
        if source is None or row["source_id"] in source_ids or row["publisher"] != source["name"]:
            raise RecoveryError("invalid_report_source")
        source_ids.add(source["id"])
        if type(row["blocked"]) is not bool or row["new_candidates"] != counts[source["id"]]:
            raise RecoveryError("inconsistent_source_count")
        for key in (baseline_key, "new_candidates", "jobs_done", "jobs_pending"):
            if type(row[key]) is not int or not 0 <= row[key] <= MAX_JOBS:
                raise RecoveryError("invalid_source_count")
        if (not isinstance(row["counts"], dict) or set(row["counts"]) - COUNT_KEYS
                or any(type(value) is not int or not 0 <= value <= MAX_JOBS * 300 for value in row["counts"].values())):
            raise RecoveryError("invalid_source_counters")
        if not isinstance(row["errors"], list) or len(row["errors"]) > MAX_JOBS:
            raise RecoveryError("invalid_source_errors")
        for error in row["errors"]:
            if (set(error) != {"kind", "url", "month", "page", "code", "attempts"}
                    or error["kind"] not in ("wordpress", "article", "index")
                    or not isinstance(error["code"], str) or not re.fullmatch(r"[a-z][a-z0-9_]{1,50}", error["code"])
                    or type(error["attempts"]) is not int or not 1 <= error["attempts"] <= MAX_ATTEMPTS):
                raise RecoveryError("invalid_source_error")
            if error["kind"] == "wordpress":
                if (error["url"] is not None or error["month"] not in [f"{report['year']}-{month:02}" for month in range(1, 13)]
                        or type(error["page"]) is not int or not 1 <= error["page"] <= 100_000):
                    raise RecoveryError("invalid_wordpress_error")
            else:
                canonical_url(error["url"], set(source["hosts"]))
                if error["month"] is not None or error["page"] is not None:
                    raise RecoveryError("invalid_html_error")
    if set(counts) - source_ids:
        raise RecoveryError("candidate_missing_from_report")
    return report


def recover(*, year, data_dir, output_dir, max_requests=100, source_ids=None,
            seeds=(), sources=SOURCES, session=None, now=None, delay=0.5):
    """Checkpoint bounded metadata reads; candidates remain private on every exit."""
    today = datetime.now(timezone.utc).date()
    if type(year) is not int or not 2010 <= year < today.year or type(max_requests) is not int or not 1 <= max_requests <= 1000:
        raise RecoveryError("invalid_year_or_budget")
    if not 0 <= delay <= 60:
        raise RecoveryError("invalid_delay")
    now = now or datetime.now(timezone.utc).isoformat()
    if datetime.fromisoformat(now).tzinfo is None:
        raise RecoveryError("timestamp_requires_timezone")
    data_dir, output_dir = Path(data_dir), Path(output_dir)
    output_dir = private_directory(output_dir, data_dir)
    source_map = {source["id"]: source for source in sources}
    wanted = set(source_ids or source_map)
    if not wanted or wanted - set(source_map):
        raise RecoveryError("unknown_source")
    baseline, baseline_counts = load_baseline(data_dir, sources)
    baseline_keys = set().union(*(identity_keys(reference, source_map[reference["source_id"]]) for reference in baseline)) if baseline else set()
    state_path = output_dir / "state.json"
    state = read_json(state_path) if state_path.exists() else {
        "schema_version": VERSION, "year": year, "created_at": now, "updated_at": now,
        "sources": {}, "records": [], "requests_total": 0,
    }
    _validate_state(state, year, sources)
    # Remove candidates incorporated into the baseline by a separate human-reviewed step.
    state["records"] = [reference for reference in state["records"]
        if not identity_keys(reference, source_map[reference["source_id"]]) & baseline_keys]
    candidate_keys = set().union(*(identity_keys(reference, source_map[reference["source_id"]]) for reference in state["records"])) if state["records"] else set()
    for source in sources:
        if source["id"] not in wanted:
            continue
        saved = state["sources"].setdefault(source["id"], {"jobs": [], "counts": {}, "seen": [], "blocked": False})
        if source.get("access") == "restricted":
            saved["blocked"] = True
        elif source.get("kind", "wordpress") == "wordpress":
            for month in range(1, 13):
                _add_job(saved, {"kind": "wordpress", "month": f"{year}-{month:02}", "page": 1})
        else:
            for url in source.get("archive_urls", []):
                canonical_url(url, set(source["hosts"]))
                _add_job(saved, {"kind": "index", "url": url})
    if not isinstance(seeds, (list, tuple)) or len(seeds) > 10_000:
        raise RecoveryError("invalid_seeds")
    for seed in seeds:
        if not isinstance(seed, dict) or set(seed) != {"source_id", "source_url"} or seed["source_id"] not in source_map:
            raise RecoveryError("invalid_seed")
        source = source_map[seed["source_id"]]
        url = canonical_url(seed["source_url"], set(source["hosts"]))
        if source.get("kind", "wordpress") == "wordpress":
            raise RecoveryError("wordpress_seeds_require_rest_metadata")
        if source["id"] in wanted:
            _add_job(state["sources"][source["id"]], {"kind": "article", "url": url})
    seed_urls = {seed["source_url"] for seed in seeds}
    # These numerical bounds ONLY prioritize reads. Every resulting article
    # must still declare its own 2015 date; no URL is excluded or dated from ID.
    verified_archive_ids = []
    for reference in baseline + state["records"]:
        parts = urlsplit(reference["source_url"])
        match = re.match(r"^/historico/(\d+)/", parts.path)
        if (reference["published_date"].startswith(f"{year}-")
                and parts.hostname == "historico.elsalvador.com" and match):
            verified_archive_ids.append(int(match[1]))
    archive_bounds = (min(verified_archive_ids), max(verified_archive_ids)) if verified_archive_ids else None

    def priority(job):
        if job["kind"] == "wordpress" or job.get("url") in seed_urls:
            rank = 0
        elif job["kind"] == "index":
            rank = 2
        else:
            parts = urlsplit(job["url"])
            match = re.match(r"^/historico/(\d+)/", parts.path)
            rank = 1 if (archive_bounds and parts.hostname == "historico.elsalvador.com"
                         and match and archive_bounds[0] <= int(match[1]) <= archive_bounds[1]) else 3
        return (job["status"] == "failed", rank)
    budget = {"requests": 0, "limit": max_requests, "response_bytes": 0}
    owned, session = session is None, session or requests.Session()
    if (getattr(session, "auth", None) or getattr(session, "cookies", None)
            or any(key.lower() in ("authorization", "cookie", "x-api-key") for key in getattr(session, "headers", {}))):
        raise RecoveryError("credentialed_session")
    session.trust_env = False
    attempted = set()
    consecutive = Counter()

    def accept(metadata, source, saved):
        counts = saved["counts"]
        try:
            reference, outcome = reference_from_homicide_metadata(metadata, source, year, now)
        except (ValueError, TypeError, KeyError, AttributeError):
            counts["invalid_metadata"] = counts.get("invalid_metadata", 0) + 1
            return
        if reference is None:
            counts[outcome] = counts.get(outcome, 0) + 1
            return
        keys = identity_keys(reference, source)
        seen = set(saved["seen"])
        if keys & seen:
            outcome = "duplicate_matches"
        elif keys & baseline_keys:
            outcome = "matched_existing"
        elif keys & candidate_keys:
            outcome = "duplicate_matches"
        else:
            state["records"].append(reference)
            candidate_keys.update(keys)
            outcome = "new_matches"
        saved["seen"] = sorted(seen | keys)
        counts[outcome] = counts.get(outcome, 0) + 1

    def save():
        state["updated_at"] = now
        write_private(output_dir, "state.json", state)

    try:
        progress = True
        while progress and budget["requests"] < max_requests:
            progress = False
            for source in sources:
                if source["id"] not in wanted:
                    continue
                saved = state["sources"][source["id"]]
                if saved["blocked"] or consecutive[source["id"]] >= 3:
                    continue
                eligible = [job for job in saved["jobs"] if job["status"] in ("pending", "failed")
                    and job["attempts"] < MAX_ATTEMPTS and (source["id"], _job_key(job)) not in attempted]
                eligible.sort(key=priority)
                if not eligible or budget["requests"] >= max_requests:
                    continue
                job = eligible[0]
                attempted.add((source["id"], _job_key(job)))
                progress = True
                if job["kind"] == "wordpress":
                    start = date.fromisoformat(job["month"] + "-01")
                    end = start.replace(day=calendar.monthrange(year, start.month)[1])
                    url = page_url(source, start, end, job["page"])
                else:
                    url = job["url"]
                try:
                    raw, headers, final = request_metadata(url, source, session, budget,
                        limit=MAX_INDEX_BYTES if job["kind"] == "index" else MAX_ARTICLE_BYTES, delay=delay)
                    if job["kind"] == "wordpress":
                        posts = json.loads(raw)
                        if not isinstance(posts, list) or len(posts) > 100:
                            raise ArchiveError("invalid_wordpress_page")
                        totals = {key.lower(): value for key, value in headers.items()}
                        pages, total = int(totals["x-wp-totalpages"]), int(totals["x-wp-total"])
                        if (not 0 <= pages <= 100_000 or total < 0 or (pages == 0) != (total == 0)
                                or (job["page"] < pages and len(posts) != 100)):
                            raise ArchiveError("invalid_wordpress_pagination")
                        for post in posts:
                            try:
                                if not isinstance(post, dict) or type(post.get("id")) is not int or post["id"] < 1:
                                    raise ValueError("invalid_post")
                                published = datetime.fromisoformat(post["date"]).date()
                                if not start <= published <= end:
                                    raise ValueError("post_outside_window")
                                metadata = {"title": post["title"]["rendered"], "source_url": post["link"],
                                    "published_date": published.isoformat(), "source_post_id": post["id"]}
                                accept(metadata, source, saved)
                            except (ValueError, TypeError, KeyError):
                                saved["counts"]["invalid_metadata"] = saved["counts"].get("invalid_metadata", 0) + 1
                        saved["counts"]["posts_scanned"] = saved["counts"].get("posts_scanned", 0) + len(posts)
                        job.update(source_total_pages=pages, source_total_posts=total)
                        if job["page"] < pages:
                            _add_job(saved, {"kind": "wordpress", "month": job["month"], "page": job["page"] + 1})
                    elif job["kind"] == "index":
                        articles, indexes, observed = _index_articles(raw, headers.get("Content-Type", ""), final, source)
                        job.update(article_urls_observed=observed, article_urls_selected=len(articles))
                        _add_jobs(saved, [{"kind": "article", "url": article} for article in articles]
                                        + [{"kind": "index", "url": index} for index in indexes])
                    else:
                        accept(parse_article_metadata(raw, final, source), source, saved)
                    job.update(status="done", error=None, last_attempt_at=now)
                    job["attempts"] += 1
                    consecutive[source["id"]] = 0
                except BudgetExhausted:
                    progress = False
                    break
                except (ArchiveError, ValueError, KeyError, TypeError) as error:
                    code = error.code if isinstance(error, ArchiveError) else "invalid_metadata_response"
                    job.update(status="restricted" if code == "access_restricted" else "failed", error=code, last_attempt_at=now)
                    job["attempts"] += 1
                    consecutive[source["id"]] += 1
                    if code == "access_restricted":
                        saved["blocked"] = True
                save()
    finally:
        if owned:
            session.close()
        state["requests_total"] += budget["requests"]
        state["records"].sort(key=lambda reference: (reference["published_date"], reference["id"]))
        save()
    _validate_state(state, year, sources)
    for reference in state["records"]:
        if identity_keys(reference, source_map[reference["source_id"]]) & baseline_keys:
            raise RecoveryError("baseline_duplicate_in_output")
    candidates = {"schema_version": 1, "generated_at": now, "records": state["records"]}
    report = {"schema_version": 1, "year": year, "generated_at": now,
        "requests": budget["requests"], "requests_total": state["requests_total"], "response_bytes": budget["response_bytes"],
        "new_candidates": len(state["records"]), "publications": 0, "gemini_requests": 0,
        "review_required": True, "coverage": "partial", "sources": [],
        "notice": NOTICE}
    for source in sources:
        saved = state["sources"].get(source["id"])
        if saved is None:
            continue
        jobs = saved["jobs"]
        report["sources"].append({"source_id": source["id"], "publisher": source["name"],
            "baseline_2015" if year == 2015 else "baseline_year": baseline_counts[(source["id"], str(year))],
            "new_candidates": sum(reference["source_id"] == source["id"] for reference in state["records"]),
            "counts": saved["counts"], "blocked": saved["blocked"],
            "jobs_done": sum(job["status"] == "done" for job in jobs),
            "jobs_pending": sum(job["status"] == "pending" for job in jobs),
            "errors": [{"kind": job["kind"], "url": job.get("url"), "month": job.get("month"),
                        "page": job.get("page"), "code": job["error"], "attempts": job["attempts"]}
                       for job in jobs if job["status"] in ("failed", "restricted")]})
    validate_recovery_report(report, candidates, sources=sources)
    write_private(output_dir, "candidates.json", candidates)
    write_private(output_dir, "report.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2015)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data/history")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-requests", type=int, default=100)
    parser.add_argument("--source", action="append", dest="source_ids", choices=[source["id"] for source in SOURCES])
    parser.add_argument("--seed-file", type=Path, help="JSON list of approved source_id/source_url objects; publisher metadata is fetched again")
    args = parser.parse_args(argv)
    try:
        report = recover(year=args.year, data_dir=args.data_dir, output_dir=args.output_dir,
            max_requests=args.max_requests, source_ids=args.source_ids,
            seeds=read_json(args.seed_file) if args.seed_file else ())
    except (RecoveryError, OSError, ValueError, TypeError, KeyError) as error:
        # Never echo an HTTP body, credential or arbitrary exception message.
        print(json.dumps({"error": type(error).__name__, "publications": 0}))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
