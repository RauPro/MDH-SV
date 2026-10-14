import copy
import json
from datetime import date
from urllib.parse import parse_qs, urlsplit

import pytest

from mdh_pipeline import homicide_recovery as recovery
from mdh_pipeline.history import TOPICS, reference_from_metadata, validate_reference

WP = {"id": "co-latino", "name": "Diario Co Latino", "kind": "wordpress", "endpoint": "https://www.diariocolatino.com/wp-json/wp/v2/posts", "hosts": ["www.diariocolatino.com", "diariocolatino.com"]}
HTML = {"id": "el-diario-de-hoy", "name": "El Diario de Hoy (archivo)", "kind": "html_archive", "endpoint": "https://historico.elsalvador.com/historico/index.xml", "hosts": ["historico.elsalvador.com"], "archive_urls": ["https://historico.elsalvador.com/historico/index.xml"]}
NOW = "2026-10-13T20:00:00+00:00"
URL = "https://historico.elsalvador.com/historico/15/matan-a-dos-personas.html"


class Response:
    def __init__(self, raw=b"", status=200, headers=None):
        self.raw = raw.encode() if isinstance(raw, str) else raw
        self.status_code = status
        self.headers = {"Content-Type": "text/html", **(headers or {})}
    def __enter__(self): return self
    def __exit__(self, *args): return None
    def iter_content(self, size):
        for index in range(0, len(self.raw), size): yield self.raw[index:index + size]


class Session:
    def __init__(self, responses): self.responses, self.calls = iter(responses), []
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return next(self.responses)


def wp_response(posts, pages=1, total=None):
    return Response(json.dumps(posts), headers={"Content-Type": "application/json", "X-WP-TotalPages": str(pages), "X-WP-Total": str(len(posts) if total is None else total)})


def post(number=1, title="Matan a dos personas en San Salvador", day="2015-01-15", host="www.diariocolatino.com", slug=None):
    return {"id": number, "date": day + "T12:30:00", "link": f"https://{host}/{slug or 'noticia-' + str(number)}/", "title": {"rendered": title}, "content": {"rendered": "BODY MUST NOT PERSIST"}}


def article(url=URL, day="2015-04-15", title="Matan a dos personas en San Salvador"):
    return f'<html><head><link rel="canonical" href="{url}"><meta property="og:title" content="{title}"><meta property="article:published_time" content="{day}T10:00:00"></head><body>BODY MUST NOT PERSIST</body></html>'


def sitemap(*urls):
    return '<urlset>' + ''.join(f'<url><loc>{url}</loc><lastmod>2015-02-02</lastmod></url>' for url in urls) + '</urlset>'


@pytest.fixture(autouse=True)
def no_dns(monkeypatch):
    monkeypatch.setattr(recovery, "require_public_dns", lambda host: None)


@pytest.fixture
def run(tmp_path):
    baseline, output = tmp_path / "baseline", tmp_path / "private"
    baseline.mkdir()
    for source in (WP, HTML):
        (baseline / f"{source['id']}.json").write_text(json.dumps({"source_id": source["id"], "records": []}))
    def invoke(responses, sources=(WP,), **kwargs):
        session = Session(responses)
        result = recovery.recover(year=2015, data_dir=baseline, output_dir=output,
            sources=sources, now=NOW, delay=0, session=session, **kwargs)
        candidates = json.loads((output / "candidates.json").read_text())
        state = json.loads((output / "state.json").read_text())
        return result, candidates, state, session
    invoke.baseline, invoke.output = baseline, output
    return invoke


@pytest.mark.parametrize("title", [
    "Matan a dos comerciantes", "Mataron a un policía", "Matanza en San Miguel", "Masacre de trabajadores",
    "Hombre acribillado", "Ultimado en su vivienda", "Asesinato en San Salvador", "Aumentan los homicidios",
    "Feminicidio: condenan al responsable", "Tres muertos por ataque armado", "Muerto por disparos en la colonia",
])
def test_expanded_discovery_accepts_violent_death_vocabulary(title):
    assert recovery.homicide_hint(title)


@pytest.mark.parametrize("title", [
    "Diez muertos por lluvias", "Tormenta mata a diez personas", "Muertos por accidente vial",
    "Plazo muerto para inscripciones", "El comercio está muerto", "Jornada sobre salud", "Matar el tiempo libre",
])
def test_discovery_does_not_treat_weather_or_deadlines_as_homicides(title):
    assert not recovery.homicide_hint(title)


def test_optional_topic_injection_does_not_change_ordinary_history_or_title():
    original = copy.deepcopy(TOPICS)
    args = ("Matan a dos personas", "https://www.diariocolatino.com/noticia/", "2015-01-15", WP,
            date(2015, 1, 1), date(2015, 12, 31), NOW)
    assert reference_from_metadata(*args, source_post_id=1) is None
    metadata = {"title": args[0], "source_url": args[1], "published_date": args[2], "source_post_id": 1}
    reference, outcome = recovery.reference_from_homicide_metadata(metadata, WP, 2015, NOW)
    assert outcome == "matched" and reference["title"] == args[0]
    assert reference["topics"] == ["violencia_y_desapariciones"]
    assert reference["review_status"] == reference["jurisdiction_status"] == "not_reviewed"
    assert TOPICS == original
    assert reference_from_metadata(*args, source_post_id=1) is None
    validate_reference(reference, WP)


def test_wp_reads_exact_calendar_months_without_bodies_and_stays_private(run):
    report, candidates, state, session = run([wp_response([post()])], max_requests=1)
    assert report["new_candidates"] == 1 and report["publications"] == report["gemini_requests"] == 0
    assert candidates["records"][0]["published_date"] == "2015-01-15"
    params = parse_qs(urlsplit(session.calls[0][0]).query)
    assert params["after"] == ["2014-12-31T23:59:59"]
    assert params["before"] == ["2015-02-01T00:00:00"]
    assert params["_fields"] == ["id,date,link,title"]
    assert "BODY MUST NOT PERSIST" not in ''.join(path.read_text() for path in run.output.iterdir())
    assert json.loads((run.baseline / "co-latino.json").read_text())["records"] == []
    assert report["sources"][0]["jobs_pending"] == 11


def test_resume_reads_next_month_not_previous_and_retains_private_candidate(run):
    run([wp_response([post()])], max_requests=1)
    report, candidates, _, session = run([wp_response([post(2, day="2015-02-15")])], max_requests=1)
    assert len(candidates["records"]) == 2
    assert parse_qs(urlsplit(session.calls[0][0]).query)["before"] == ["2015-03-01T00:00:00"]
    assert report["requests_total"] == 2


def test_all_12_months_complete_and_resume_makes_zero_requests(run):
    run([wp_response([], pages=0) for _ in range(12)], max_requests=12)
    report, _, _, session = run([], max_requests=12)
    assert report["requests"] == len(session.calls) == 0
    assert report["sources"][0]["jobs_done"] == 12


def test_baseline_alias_and_wp_id_dedup_without_merging_distinct_articles(run):
    known, _ = recovery.reference_from_homicide_metadata({"title": "Un homicidio", "source_url": "https://www.diariocolatino.com/old-title/", "published_date": "2015-01-15", "source_post_id": 1}, WP, 2015, NOW)
    (run.baseline / "co-latino.json").write_text(json.dumps({"source_id": WP["id"], "records": [known]}))
    records = [post(1, slug="changed-title"), post(2, host="diariocolatino.com", slug="old-title"), post(3), post(3), post(4)]
    report, candidates, _, _ = run([wp_response(records)], max_requests=1)
    assert {reference["discovery"]["source_post_id"] for reference in candidates["records"]} == {3, 4}
    counters = report["sources"][0]["counts"]
    assert counters["matched_existing"] == 2 and counters["duplicate_matches"] == 1
    # Both different URLs describing the same case are retained, never fuzzy-deduplicated.
    assert len({reference["title"] for reference in candidates["records"]}) == 1


def test_missing_baseline_is_not_treated_as_empty(run):
    (run.baseline / "co-latino.json").unlink()
    with pytest.raises(FileNotFoundError): run([], max_requests=1)


def test_invalid_wp_metadata_or_wrong_month_cannot_enter_review(run):
    bad = [post(day="2016-01-15"), post(2, day="2015-02-15"), post(3, title="Diez muertos por lluvias"), post(4)]
    bad[-1]["link"] = "https://evil.example/x"
    report, candidates, _, _ = run([wp_response(bad)], max_requests=1)
    assert not candidates["records"]
    assert report["sources"][0]["counts"] == {"invalid_metadata": 3, "not_relevant": 1, "posts_scanned": 4}


def test_sitemap_uses_broader_terms_but_requires_publisher_date(run):
    foreign = URL.replace("/15/", "/16/")
    responses = [Response(sitemap(URL, URL, foreign), headers={"Content-Type": "text/xml"}), Response(article()), Response(article(foreign, day="2016-04-15"))]
    report, candidates, _, _ = run(responses, sources=(HTML,), max_requests=3)
    assert len(candidates["records"]) == 1
    assert report["sources"][0]["counts"]["outside_year"] == 1
    assert report["requests"] == 3


def test_missing_article_date_never_uses_sitemap_date(run):
    report, candidates, state, _ = run([Response(sitemap(URL)), Response('<h1>Matan a dos personas</h1>')], sources=(HTML,), max_requests=2)
    assert not candidates["records"]
    assert report["sources"][0]["errors"][0]["code"] == "missing_publication_date"


def test_seed_needs_article_metadata_not_seed_title_or_url_date(run):
    source = {**HTML, "archive_urls": []}
    report, candidates, _, session = run([Response(article())], sources=(source,),
        seeds=[{"source_id": HTML["id"], "source_url": URL}], max_requests=1)
    assert candidates["records"][0]["title"] == "Matan a dos personas en San Salvador"
    assert session.calls[0][0] == URL
    assert report["sources"][0]["jobs_done"] == 1


def test_403_stops_source_and_does_not_retry_or_fallback_on_resume(run):
    report, _, _, session = run([Response(status=403)], max_requests=10)
    assert len(session.calls) == 1 and report["sources"][0]["blocked"]
    report, _, _, session = run([], max_requests=10)
    assert report["requests"] == len(session.calls) == 0


def test_configured_restricted_source_performs_no_requests(run):
    report, _, _, session = run([], sources=({**HTML, "access": "restricted"},), max_requests=10)
    assert not session.calls and report["sources"][0]["blocked"]


def test_redirect_budget_exhaustion_preserves_pending_job(run):
    report, _, state, _ = run([Response(status=302, headers={"Location": WP["endpoint"] + "?moved=1"})], max_requests=1)
    assert report["requests"] == 1
    assert state["sources"][WP["id"]]["jobs"][0]["status"] == "pending"
    report, candidates, _, _ = run([wp_response([post()])], max_requests=1)
    assert len(candidates["records"]) == 1 and report["requests_total"] == 2


def test_cross_host_redirect_never_requested(run):
    report, _, _, session = run([Response(status=302, headers={"Location": "https://evil.example/x"})], max_requests=1)
    assert len(session.calls) == 1
    assert report["sources"][0]["errors"][0]["code"] == "unsafe_url"


def test_errors_retry_only_three_times_across_runs(run):
    source = {**HTML, "archive_urls": []}
    seeds = [{"source_id": HTML["id"], "source_url": URL}]
    for attempt in range(1, 4):
        report, _, _, session = run([Response(status=500)], sources=(source,), seeds=seeds, max_requests=10)
        assert len(session.calls) == 1 and report["sources"][0]["errors"][0]["attempts"] == attempt
    report, _, _, session = run([], sources=(source,), seeds=seeds, max_requests=10)
    assert not session.calls


def test_output_rejects_repository_public_data_even_via_symlink(tmp_path, monkeypatch):
    root = tmp_path / "repository"
    public = root / "apps/web/public"
    public.mkdir(parents=True)
    monkeypatch.setattr(recovery, "ROOT", root)
    alias = tmp_path / "alias"
    alias.symlink_to(public, target_is_directory=True)
    for destination in (public, alias, root / "data", root):
        with pytest.raises(recovery.RecoveryError, match="output_must_be_private"):
            recovery.private_directory(destination, root / "data/history")
    assert recovery.private_directory(root / ".local-quarantine/recovery", root / "data/history")


def test_output_file_symlink_is_rejected(run, tmp_path):
    run.output.mkdir()
    (run.output / "state.json").symlink_to(tmp_path / "private-target.json")
    with pytest.raises(recovery.RecoveryError, match="unsafe_output_symlink"):
        run([], max_requests=1)


def test_state_with_body_or_outside_hosts_is_rejected_before_network(run):
    run([wp_response([], pages=0)], max_requests=1)
    path = run.output / "state.json"
    saved = json.loads(path.read_text())
    saved["body"] = "Should never be copied"
    path.write_text(json.dumps(saved))
    with pytest.raises(recovery.RecoveryError, match="invalid_state"):
        run([], max_requests=1)


def test_report_validation_rejects_extras_duplicate_references_and_claims_of_publication(run):
    report, candidates, _, _ = run([wp_response([post()])], max_requests=1)
    recovery.validate_recovery_report(report, candidates, sources=(WP,))
    mutated = copy.deepcopy(report)
    mutated["publications"] = 1
    with pytest.raises(recovery.RecoveryError): recovery.validate_recovery_report(mutated, candidates, sources=(WP,))
    mutated = copy.deepcopy(candidates)
    mutated["body"] = "No"
    with pytest.raises(recovery.RecoveryError): recovery.validate_recovery_report(report, mutated, sources=(WP,))
    candidates["records"].append(copy.deepcopy(candidates["records"][0]))
    report["new_candidates"] += 1
    with pytest.raises(recovery.RecoveryError, match="duplicate_candidate"):
        recovery.validate_recovery_report(report, candidates, sources=(WP,))


def test_seed_extra_fields_or_unapproved_hosts_fail_before_any_request(run):
    for seed in ({"source_id": HTML["id"], "source_url": URL, "published_date": "2015-01-01"},
                 {"source_id": HTML["id"], "source_url": "https://evil.example/x"}):
        with pytest.raises(ValueError): run([], sources=(HTML,), seeds=[seed], max_requests=1)


def test_credentialed_session_rejected_before_http(run):
    session = Session([])
    session.headers = {"Authorization": "sensitive"}
    with pytest.raises(recovery.RecoveryError, match="credentialed_session"):
        recovery.recover(year=2015, data_dir=run.baseline, output_dir=run.output,
            sources=(WP,), session=session, now=NOW, delay=0)
    assert not session.calls


def test_known_2015_id_range_only_prioritizes_reads_not_dates(run):
    baseline = []
    for number in (150000, 160000):
        reference, _ = recovery.reference_from_homicide_metadata({"title": "Un homicidio",
            "source_url": f"https://historico.elsalvador.com/historico/{number}/homicidio.html",
            "published_date": "2015-05-05"}, HTML, 2015, NOW)
        baseline.append(reference)
    (run.baseline / (HTML["id"] + ".json")).write_text(json.dumps({"source_id": HTML["id"], "records": baseline}))
    older_id = URL.replace("/15/", "/100000/")
    in_range = URL.replace("/15/", "/155000/")
    responses = [Response(sitemap(older_id, in_range)), Response(article(in_range, day="2014-01-15"))]
    report, candidates, state, session = run(responses, sources=(HTML,), max_requests=2)
    assert session.calls[1][0] == in_range
    assert not candidates["records"]
    assert report["sources"][0]["counts"]["outside_year"] == 1
    assert any(job.get("url") == older_id and job["status"] == "pending" for job in state["sources"][HTML["id"]]["jobs"])


@pytest.mark.parametrize("field", ["report_version", "candidate_version", "naive_time", "future_time"])
def test_review_boundary_rejects_boolean_versions_and_invalid_retrieval_times(run, field):
    report, candidates, _, _ = run([wp_response([post()])], max_requests=1)
    if field == "report_version": report["schema_version"] = True
    elif field == "candidate_version": candidates["schema_version"] = True
    elif field == "naive_time": candidates["records"][0]["discovery"]["retrieved_at"] = "2026-10-13T19:00:00"
    else: candidates["records"][0]["discovery"]["retrieved_at"] = "2026-10-14T19:00:00+00:00"
    with pytest.raises(recovery.RecoveryError):
        recovery.validate_recovery_report(report, candidates, sources=(WP,))
