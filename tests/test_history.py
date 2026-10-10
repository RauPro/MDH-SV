"""Historical bibliography must preserve evidence and the editorial boundary."""
import json
from datetime import date
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from mdh_pipeline.history import (
    SOURCES, collect_source, export_archive, fetch_page, month_windows,
    migrate_scope_hints, page_url, reference_from_post, scope_hint, validate_reference,
)

SOURCE = SOURCES[0]
START = date(2025, 3, 26)
END = date(2025, 3, 31)
NOW = "2026-10-10T04:00:00+00:00"


def post(n=1, title="Denuncian detenciones arbitrarias", published="2025-03-26T00:00:00"):
    return {"id": n, "date": published, "link": f"https://www.diariocolatino.com/noticia-{n}/", "title": {"rendered": title}, "content": {"rendered": "Private-like article body must never be copied"}}


def test_inclusive_date_windows_and_wordpress_boundaries():
    assert list(month_windows(START, date(2025, 5, 9))) == [
        ("2025-03", START, END),
        ("2025-04", date(2025, 4, 1), date(2025, 4, 30)),
        ("2025-05", date(2025, 5, 1), date(2025, 5, 9)),
    ]
    params = parse_qs(urlsplit(page_url(SOURCE, START, END, 2)).query)
    assert params["after"] == ["2025-03-25T23:59:59"]
    assert params["before"] == ["2025-04-01T00:00:00"]
    assert params["page"] == ["2"]
    assert params["_fields"] == ["id,date,link,title"]
    with pytest.raises(ValueError):
        list(month_windows(END, START))


def test_reference_is_unreviewed_bibliography_not_a_case():
    reference = reference_from_post(post(), SOURCE, START, END, NOW)
    assert reference["published_date"] == START.isoformat()
    assert reference["review_status"] == "not_reviewed"
    assert reference["jurisdiction_status"] == "not_reviewed"
    assert reference["record_type"] == "news_reference"
    assert reference["topics"] == ["detencion_y_debido_proceso"]
    assert "content" not in reference and "victim" not in reference
    assert "Private-like" not in json.dumps(reference)
    validate_reference(reference, SOURCE)


def test_keyword_screen_and_quote_minimization():
    assert reference_from_post(post(title="Resultado del partido de fútbol"), SOURCE, START, END, NOW) is None
    item = post(title="<b>Detención &amp; tortura</b> " + "palabra " * 100)
    reference = reference_from_post(item, SOURCE, START, END, NOW)
    assert len(reference["title"].split()) <= 24
    assert len(reference["title"]) <= 180
    assert "<b>" not in reference["title"]
    assert reference["title_truncated"] is True


@pytest.mark.parametrize("title,url,expected,terms", [
    ("Maduro enfrenta acusaciones de tortura en EEUU", "https://www.diariocolatino.com/noticia/", "international_context", ["eeuu", "maduro"]),
    ("Detenciones en El Salvador y Estados Unidos", "https://www.diariocolatino.com/noticia/", "el_salvador", ["el salvador", "estados unidos"]),
    ("Periodistas salvadoreñas denuncian censura", "https://www.diariocolatino.com/noticia/", "el_salvador", ["salvadorenas"]),
    ("Preocupación por Ruth López", "https://www.diariocolatino.com/noticia/", "el_salvador", ["ruth lopez"]),
    ("PNC reporta detenciones durante el régimen de excepción", "https://www.diariocolatino.com/noticia/", "undetermined", []),
    ("José Rubén Zamora permanece preso en Guatemala", "https://www.diariocolatino.com/noticia/", "international_context", ["guatemala"]),
    ("Denuncian detenciones", "https://www.diariocolatino.com/detenciones-en-el-salvador/", "el_salvador", ["el salvador"]),
    ("Denuncian detenciones", "https://www.diariocolatino.com/detenciones-en-san-miguel/", "undetermined", []),
])
def test_scope_hints_are_conservative_traceable_and_prioritize_salvador(title, url, expected, terms):
    assert scope_hint(title, url) == {"scope_hint": expected, "scope_terms": terms}


def test_new_captures_determine_scope_before_title_truncation():
    item = post(title="Detenciones " + "palabra " * 30 + "en El Salvador")
    reference = reference_from_post(item, SOURCE, START, END, NOW)
    assert reference["title_truncated"] is True
    assert "Salvador" not in reference["title"]
    assert reference["scope_hint"] == "el_salvador"
    assert reference["scope_terms"] == ["el salvador"]
    assert reference["jurisdiction_status"] == "not_reviewed"


def test_scope_migration_preserves_source_coverage_and_never_claims_review(tmp_path):
    collect_source(SOURCE, START, END, tmp_path, fetch=lambda *args: ([post(title="Detenciones en El Salvador")], 1, 1), delay=0, now=NOW)
    path = tmp_path / "co-latino.json"
    before = json.loads(path.read_text())
    del before["records"][0]["scope_hint"]
    del before["records"][0]["scope_terms"]
    path.write_text(json.dumps(before))
    migrate_scope_hints(tmp_path, [SOURCE])
    after = json.loads(path.read_text())
    assert after["months"] == before["months"]
    assert after["records"][0]["discovery"] == before["records"][0]["discovery"]
    assert after["records"][0]["scope_hint"] == "el_salvador"
    assert after["records"][0]["review_status"] == "not_reviewed"
    assert after["records"][0]["jurisdiction_status"] == "not_reviewed"
    migrate_scope_hints(tmp_path, [SOURCE])
    assert json.loads(path.read_text()) == after


@pytest.mark.parametrize("published", [None, "", "invalid", "2025-03-25T23:59:59", "2025-04-01T00:00:00", "2027-01-01T00:00:00"])
def test_missing_dates_and_out_of_range_are_never_replaced_with_today(published):
    with pytest.raises(ValueError):
        reference_from_post(post(published=published), SOURCE, START, END, NOW)


@pytest.mark.parametrize("url", ["https://evil.example/article/", "http://www.diariocolatino.com/item/", "https://www.diariocolatino.com@localhost/item/", "https://www.diariocolatino.com:443/item/", "https://www.diariocolatino.com/item/?secret=1"])
def test_reference_rejects_unapproved_or_unsafe_links(url):
    item = post()
    item["link"] = url
    with pytest.raises(ValueError):
        reference_from_post(item, SOURCE, START, END, NOW)


def test_normalized_urls_have_stable_ids():
    first = post()
    second = post()
    first["link"] = first["link"].rstrip("/")
    second["link"] += "?utm_source=feed"
    assert reference_from_post(first, SOURCE, START, END, NOW)["id"] == reference_from_post(second, SOURCE, START, END, NOW)["id"]


def test_page_budget_resumes_and_deduplicates_without_losing_references(tmp_path):
    calls = []

    def fetch(source, start, end, page):
        calls.append(page)
        return [post(page)], 2, 2

    collect_source(SOURCE, START, END, tmp_path, max_pages=1, fetch=fetch, delay=0, now=NOW)
    state = json.loads((tmp_path / "co-latino.json").read_text())
    assert state["months"]["2025-03"]["next_page"] == 2
    assert state["months"]["2025-03"]["status"] == "partial"
    collect_source(SOURCE, START, END, tmp_path, max_pages=1, fetch=fetch, delay=0, now=NOW)
    assert calls == [1, 2]
    state = json.loads((tmp_path / "co-latino.json").read_text())
    assert len(state["records"]) == 2
    assert state["months"]["2025-03"]["status"] == "enumerated"
    collect_source(SOURCE, START, END, tmp_path, max_pages=2, fetch=fetch, delay=0, now=NOW)
    state = json.loads((tmp_path / "co-latino.json").read_text())
    assert len(state["records"]) == 2


def test_extended_range_preserves_old_checkpoints_and_exposes_unread_months(tmp_path):
    from mdh_pipeline.history import START_DATE
    assert START_DATE == date(2010, 1, 1)
    collect_source(SOURCE, START, END, tmp_path, fetch=lambda *args: ([post()], 1, 1), delay=0, now=NOW)
    path = tmp_path / "co-latino.json"
    previous = json.loads(path.read_text())
    collect_source(SOURCE, START_DATE, date(2026, 10, 10), tmp_path, max_pages=4,
                   fetch=lambda *args: ([], 0, 0), delay=0, now=NOW)
    state = json.loads(path.read_text())
    assert state["records"] == previous["records"]
    assert state["months"]["2025-03"] == previous["months"]["2025-03"]
    document = export_archive(tmp_path, tmp_path / "public.json", START_DATE, date(2026, 10, 10), sources=[SOURCE])
    assert document["coverage"]["start_date"] == "2010-01-01"
    assert len(document["coverage"]["months"]) == 202
    assert document["coverage"]["status"] == "partial"
    months = {m["month"]: m for m in document["coverage"]["sources"][0]["months"]}
    assert months["2010-01"]["status"] == "enumerated"
    assert months["2010-01"]["source_total_posts"] == 0
    assert months["2010-07"]["status"] == "not_started"
    assert months["2010-07"]["source_total_posts"] is None
    # The former March window started on the 26th; expansion cannot claim March 1–25 was read.
    assert months["2025-03"]["status"] == "partial"


def test_large_recent_months_leave_budget_for_backfill_and_resume(tmp_path):
    calls = []
    def fetch(source, first, last, page):
        calls.append((first.strftime("%Y-%m"), page))
        return [post(int(first.strftime("%Y%m")) * 1000 + page, published=first.isoformat() + "T12:00:00")], 1000, 100000
    report = collect_source(SOURCE, date(2010, 1, 1), date(2026, 10, 10), tmp_path,
                            max_pages=150, fetch=fetch, delay=0, now=NOW)
    assert calls[:10] == [("2026-10", page) for page in range(1, 11)]
    assert calls[10:20] == [("2026-09", page) for page in range(1, 11)]
    assert calls[20:] == [("2010-01", page) for page in range(1, 131)]
    assert report["requests"] == report["successful_pages"] == 150
    assert report["failed_page_attempts"] == 0
    calls.clear()
    collect_source(SOURCE, date(2010, 1, 1), date(2026, 10, 10), tmp_path,
                   max_pages=150, fetch=fetch, delay=0, now=NOW)
    assert calls[0] == ("2026-10", 11)
    assert calls[10] == ("2026-09", 11)
    assert calls[20] == ("2010-01", 131)


def test_small_budget_alternates_recent_months_without_starving_history(tmp_path):
    calls = []
    def fetch(source, first, last, page):
        calls.append((first.strftime("%Y-%m"), page))
        if first.year == 2026:
            return [], 0, 0
        return [post(page, published=first.isoformat() + "T12:00:00")], 3, 3
    collect_source(SOURCE, date(2010, 1, 1), date(2026, 10, 10), tmp_path,
                   max_pages=3, fetch=fetch, delay=0, now=NOW)
    assert calls == [("2026-10", 1), ("2010-01", 1), ("2010-01", 2)]
    calls.clear()
    collect_source(SOURCE, date(2010, 1, 1), date(2026, 10, 10), tmp_path,
                   max_pages=3, fetch=fetch, delay=0, now=NOW)
    assert calls == [("2026-09", 1), ("2010-01", 3), ("2010-02", 1)]


def test_failed_page_attempts_are_bounded_even_before_failure_circuit_opens(tmp_path):
    calls = []
    def fetch(*args):
        calls.append(args)
        raise requests.Timeout("withheld")
    report = collect_source(SOURCE, date(2010, 1, 1), date(2026, 10, 10), tmp_path,
                            max_pages=2, fetch=fetch, delay=0, now=NOW)
    assert len(calls) == report["requests"] == report["failed_page_attempts"] == 2
    assert report["successful_pages"] == 0


def test_three_consecutive_source_failures_stop_without_attempting_all_historical_months(tmp_path):
    calls = []
    def fetch(*args):
        calls.append(args)
        raise requests.Timeout("withheld")
    report = collect_source(SOURCE, date(2010, 1, 1), date(2026, 10, 10), tmp_path,
                            max_pages=150, fetch=fetch, delay=0, now=NOW)
    assert len(calls) == report["requests"] == report["failed_page_attempts"] == 3
    assert report["successful_pages"] == 0
    assert report["stopped_after_consecutive_failures"] is True
    document = export_archive(tmp_path, tmp_path / "public.json", date(2010, 1, 1), date(2026, 10, 10), sources=[SOURCE])
    months = document["coverage"]["sources"][0]["months"]
    assert sum(m["status"] == "failed" for m in months) == 3
    assert sum(m["status"] == "not_started" for m in months) == 199
    assert document["coverage"]["status"] == "partial"


def test_success_resets_consecutive_failure_counter(tmp_path):
    calls = []
    def fetch(*args):
        calls.append(args)
        if len(calls) == 2:
            return [], 0, 0
        raise requests.Timeout("withheld")
    report = collect_source(SOURCE, date(2010, 1, 1), date(2026, 10, 10), tmp_path,
                            max_pages=150, fetch=fetch, delay=0, now=NOW)
    assert len(calls) == report["requests"] == 5
    assert report["successful_pages"] == 1
    assert report["failed_page_attempts"] == 4
    assert report["stopped_after_consecutive_failures"] is True


def test_failed_early_months_do_not_strand_later_history_and_are_retried_afterward(tmp_path):
    calls = []
    def fetch(source, first, last, page):
        month = first.strftime("%Y-%m")
        calls.append(month)
        if month in ("2010-01", "2010-02", "2010-03"):
            raise requests.Timeout("persistent historical gap")
        return [], 0, 0
    first = collect_source(SOURCE, date(2010, 1, 1), date(2010, 6, 30), tmp_path,
                           max_pages=20, fetch=fetch, delay=0, now=NOW)
    assert first["stopped_after_consecutive_failures"] is True
    assert calls == ["2010-06", "2010-05", "2010-01", "2010-02", "2010-03"]
    calls.clear()
    second = collect_source(SOURCE, date(2010, 1, 1), date(2010, 6, 30), tmp_path,
                            max_pages=20, fetch=fetch, delay=0, now=NOW)
    assert calls == ["2010-06", "2010-05", "2010-04", "2010-01", "2010-02", "2010-03"]
    state = json.loads((tmp_path / "co-latino.json").read_text())
    assert state["months"]["2010-04"]["status"] == "enumerated"
    assert all(state["months"][month]["status"] == "failed" for month in ("2010-01", "2010-02", "2010-03"))
    assert second["successful_pages"] == 3


def test_failure_rotation_eventually_retries_fourth_previously_failed_month(tmp_path):
    from mdh_pipeline.history import SCREEN_VERSION
    months = {
        month: {"start_date": first.isoformat(), "end_date": last.isoformat(),
                "status": "failed", "screen_version": SCREEN_VERSION,
                "last_attempt_at": "2026-10-10T01:00:00+00:00", "next_page": 1,
                "pages_fetched": 0, "posts_scanned": 0, "invalid_posts": 0}
        for month, first, last in month_windows(date(2010, 1, 1), date(2010, 4, 30))
    }
    (tmp_path / "co-latino.json").write_text(json.dumps({"schema_version": 1, "source_id": SOURCE["id"], "records": [], "months": months}))
    calls = []
    def fetch(source, first, last, page):
        month = first.strftime("%Y-%m")
        calls.append(month)
        if month in ("2010-01", "2010-02", "2010-03"):
            raise requests.Timeout("still unavailable")
        return [], 0, 0
    collect_source(SOURCE, date(2010, 1, 1), date(2010, 6, 30), tmp_path,
                   max_pages=20, fetch=fetch, delay=0, now="2026-10-10T02:00:00+00:00")
    assert calls[-3:] == ["2010-01", "2010-02", "2010-03"]
    assert "2010-04" not in calls
    calls.clear()
    collect_source(SOURCE, date(2010, 1, 1), date(2010, 6, 30), tmp_path,
                   max_pages=20, fetch=fetch, delay=0, now="2026-10-10T03:00:00+00:00")
    assert calls[2] == "2010-04"
    state = json.loads((tmp_path / "co-latino.json").read_text())
    assert state["months"]["2010-04"]["status"] == "enumerated"


def test_source_failure_preserves_references_and_reports_partial_coverage(tmp_path):
    collect_source(SOURCE, START, END, tmp_path, fetch=lambda *args: ([post()], 1, 1), delay=0, now=NOW)

    def unavailable(*args):
        raise requests.Timeout("Do not log response bodies or secrets")

    collect_source(SOURCE, START, END, tmp_path, fetch=unavailable, delay=0, now=NOW)
    document = export_archive(tmp_path, tmp_path / "public.json", START, END, sources=[SOURCE], generated_at=NOW)
    assert len(document["records"]) == 1
    assert document["coverage"]["status"] == "partial"
    assert document["coverage"]["sources"][0]["status"] == "partial"
    assert document["coverage"]["sources"][0]["months"][0]["error"] == "Timeout"
    assert "secrets" not in json.dumps(document)


def test_invalid_posts_counted_without_aborting_the_month(tmp_path):
    collect_source(SOURCE, START, END, tmp_path, fetch=lambda *args: ([post(), post(2, published="invalid")], 1, 2), delay=0, now=NOW)
    document = export_archive(tmp_path, tmp_path / "public.json", START, END, sources=[SOURCE], generated_at=NOW)
    assert len(document["records"]) == 1
    coverage = document["coverage"]["sources"][0]["months"][0]
    assert coverage["invalid_posts"] == 1
    assert coverage["posts_scanned"] == 2


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(victim="New private field"),
    lambda r: r.update(review_status="approved"),
    lambda r: r.update(jurisdiction_status="verified"),
    lambda r: r.update(record_type="individual_case"),
    lambda r: r.update(id="0" * 64),
    lambda r: r.update(publisher="Someone else"),
    lambda r: r.update(scope_hint="verified"),
    lambda r: r.update(scope_hint="el_salvador", scope_terms=[]),
    lambda r: r["discovery"].update(body="Full text"),
])
def test_export_fails_closed_on_unexpected_or_editorially_approved_fields(tmp_path, mutation):
    reference = reference_from_post(post(), SOURCE, START, END, NOW)
    mutation(reference)
    (tmp_path / "co-latino.json").write_text(json.dumps({"records": [reference], "months": {}}))
    with pytest.raises(ValueError):
        export_archive(tmp_path, tmp_path / "public.json", START, END, sources=[SOURCE])
    assert not (tmp_path / "public.json").exists()


def test_empty_month_is_distinct_from_unattempted_source(tmp_path):
    collect_source(SOURCE, START, END, tmp_path, fetch=lambda *args: ([], 0, 0), delay=0, now=NOW)
    document = export_archive(tmp_path, tmp_path / "public.json", START, END, sources=SOURCES, generated_at=NOW)
    assert document["coverage"]["sources"][0]["status"] == "enumerated"
    assert document["coverage"]["sources"][1]["months"][0]["status"] == "not_started"
    assert document["coverage"]["status"] == "partial"


def test_checkpoint_window_mismatch_never_claims_full_enumeration(tmp_path):
    collect_source(SOURCE, START, END, tmp_path, fetch=lambda *args: ([], 0, 0), delay=0, now=NOW)
    document = export_archive(tmp_path, tmp_path / "public.json", START, date(2025, 4, 1), sources=[SOURCE])
    assert document["coverage"]["sources"][0]["months"][1]["status"] == "not_started"


def test_old_screening_version_requires_reenumeration(tmp_path):
    collect_source(SOURCE, START, END, tmp_path, fetch=lambda *args: ([post()], 1, 1), delay=0, now=NOW)
    path = tmp_path / "co-latino.json"
    checkpoint = json.loads(path.read_text())
    checkpoint["months"]["2025-03"]["screen_version"] = 0
    path.write_text(json.dumps(checkpoint))
    document = export_archive(tmp_path, tmp_path / "public.json", START, END, sources=[SOURCE])
    assert document["coverage"]["sources"][0]["months"][0]["status"] == "partial"


def test_committed_archive_contains_only_valid_public_references():
    from mdh_pipeline.history import ROOT, START_DATE
    path = ROOT / "apps/web/public/archive/news.json"
    if not path.exists():
        pytest.skip("Bibliography not yet collected")
    document = json.loads(path.read_text())
    sources = {s["id"]: s for s in SOURCES}
    assert document["schema_version"] == 1
    assert document["coverage"]["status"] == "partial"
    assert document["coverage"]["start_date"] == START_DATE.isoformat()
    ids = set()
    for reference in document["records"]:
        validate_reference(reference, sources[reference["source_id"]])
        assert reference["id"] not in ids
        ids.add(reference["id"])
        assert START_DATE.isoformat() <= reference["published_date"] <= document["coverage"]["end_date"]
    assert len(document["records"]) == document["coverage"]["records"]
    assert sum(month["records"] for month in document["coverage"]["months"]) == len(ids)


def test_fetch_refuses_cross_host_redirect_before_second_request(monkeypatch):
    monkeypatch.setattr("mdh_pipeline.history.require_public_dns", lambda host: None)
    monkeypatch.setattr("mdh_pipeline.history.time.sleep", lambda seconds: None)
    calls = []

    class Response:
        is_redirect = True
        headers = {"Location": "https://127.0.0.1/private"}
        def __enter__(self): return self
        def __exit__(self, *args): return None

    class Session:
        def get(self, url, **kwargs):
            calls.append(url)
            return Response()

    with pytest.raises(ValueError, match="approved HTTPS"):
        fetch_page(SOURCE, START, END, 1, Session())
    assert len(calls) == 3
    assert all(url.startswith(SOURCE["endpoint"]) for url in calls)
