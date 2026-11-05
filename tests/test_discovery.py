"""Offline tests with synthetic data only: no API calls or personal records."""

from dataclasses import replace
from datetime import datetime, timezone
from email.message import Message
from http.client import IncompleteRead
import io
import json
from pathlib import Path
import subprocess
import sys
import ssl
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from getmeajob.__main__ import main, terminal
from getmeajob.config import ConfigurationError, Settings, load_settings
from getmeajob.discovery import save_and_filter, search
from getmeajob.filters import SearchFilters, evaluate
from getmeajob.locations import Place, distance_km, resolve
from getmeajob.models import Job, Salary
from getmeajob.sources import Board, SourceError
from getmeajob.sources.greenhouse import GreenhouseSource
from getmeajob.sources.http import JsonClient, NoRedirect, tls_context
from getmeajob.sources.lever import LeverSource
from getmeajob.storage import JobStore, canonical_url


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
BOARD = Board("greenhouse", "fictional", "Fictional Employer", "Software")
LEVER_BOARD = Board("lever", "fictional", "Fictional Employer")


def job(**changes):
    return replace(Job(
        source="greenhouse", board="fictional", source_id="1", employer="Fictional Employer",
        role="Entry-level Python Developer", application_url="https://example.invalid/jobs/1",
        locations=("Waterloo, Ontario",), description="Python and SQLite developer.",
        salary=Salary(60000, 80000, "CAD", "year"), employment_type="full-time",
        work_arrangement="hybrid", posted_at="2026-11-04T12:00:00+00:00", industry="Software",
        experience_level="entry-level", minimum_experience_years=1,
    ), **changes)


def greenhouse_row(**changes):
    return {"id": 1, "title": "Entry-level Developer", "location": {"name": "Waterloo, Ontario"},
            "absolute_url": "https://example.invalid/jobs/1", "content": "&lt;p&gt;Python &amp; SQL&lt;/p&gt;",
            "updated_at": "2026-11-05T12:00:00Z", **changes}


def lever_row(**changes):
    return {"id": "1", "text": "Python Developer", "applyUrl": "https://jobs.lever.co/fictional/1/apply",
            "categories": {"location": "Toronto, Ontario", "allLocations": ["Toronto, Ontario", "Waterloo, Ontario"],
                           "commitment": "Full-time"}, "workplaceType": "hybrid",
            "descriptionPlain": "Python", "lists": [{"text": "Requirements", "content": "<li>SQL</li>"}],
            "salaryRange": {"min": 60000, "max": 80000, "currency": "CAD", "interval": "yearly"},
            "createdAt": 1700000000000, **changes}


class FakeClient:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


class NormalizationTests(unittest.TestCase):
    def test_greenhouse_html_and_unknowns(self):
        item = GreenhouseSource.normalize(greenhouse_row(), BOARD)
        self.assertEqual(item.description, "Python & SQL")
        self.assertEqual(item.locations, ("Waterloo, Ontario",))
        self.assertEqual(item.experience_level, "entry-level")
        self.assertIsNone(item.posted_at)  # updated_at is not a posting date.
        self.assertIsNone(item.salary.minimum)
        self.assertIsNone(item.employment_type)
        self.assertEqual(item.source_updated_at, "2026-11-05T12:00:00+00:00")

    def test_greenhouse_explicit_metadata_and_publication(self):
        row = greenhouse_row(first_published="2026-11-01T12:00:00-05:00", metadata=[
            {"name": "Employment Type", "value": "Co-op"},
            {"name": "Work Arrangement", "value": "On-site"},
            {"name": "Minimum Years Experience", "value": 2},
        ])
        item = GreenhouseSource.normalize(row, BOARD)
        self.assertEqual(item.employment_type, "co-op")
        self.assertEqual(item.work_arrangement, "on-site")
        self.assertEqual(item.minimum_experience_years, 2)
        self.assertEqual(item.posted_at, "2026-11-01T17:00:00+00:00")
        self.assertEqual(item.industry, "Software")

    def test_experience_uses_explicit_evidence_not_senior_title(self):
        row = greenhouse_row(title="Senior Developer", content="At least 4 years of professional experience required.")
        item = GreenhouseSource.normalize(row, BOARD)
        self.assertEqual(item.minimum_experience_years, 4)
        self.assertIn("At least 4", item.experience_evidence)
        self.assertIsNone(item.experience_level)
        for content in ("2 years of experience preferred.", "At least 3 years of experience preferred.",
                        "Minimum 3 years of experience or equivalent training."):
            with self.subTest(content=content):
                self.assertIsNone(GreenhouseSource.normalize(greenhouse_row(content=content), BOARD).minimum_experience_years)

    def test_fractional_experience_requirement(self):
        item = GreenhouseSource.normalize(greenhouse_row(content="Minimum of 2.5 years of experience required."), BOARD)
        self.assertEqual(item.minimum_experience_years, 2.5)

    def test_internship_and_remote_explicit_labels(self):
        item = GreenhouseSource.normalize(greenhouse_row(title="Software Intern", location={"name": "Remote"},
                                                        content="No prior experience required."), BOARD)
        self.assertEqual(item.employment_type, "internship")
        self.assertEqual(item.work_arrangement, "remote")
        self.assertEqual(item.minimum_experience_years, 0)
        self.assertIsNone(GreenhouseSource.normalize(greenhouse_row(title="Internal Tools Engineer"), BOARD).employment_type)

    def test_lever_salary_locations_description_and_unknown_post_date(self):
        item = LeverSource.normalize(lever_row(), LEVER_BOARD)
        self.assertEqual(item.salary, Salary(60000, 80000, "CAD", "year"))
        self.assertEqual(len(item.locations), 2)
        self.assertEqual(item.description, "Python SQL")
        self.assertEqual(item.employment_type, "full-time")
        self.assertEqual(item.work_arrangement, "hybrid")
        self.assertIsNotNone(item.source_created_at)
        self.assertIsNone(item.posted_at)
        self.assertIsNone(item.industry)

    def test_optional_metadata_not_guessed(self):
        item = LeverSource.normalize(lever_row(categories={}, workplaceType="unspecified", salaryRange=None,
                                               createdAt=None, descriptionPlain=None, lists=[]), LEVER_BOARD)
        self.assertEqual(item.locations, ())
        self.assertEqual(item.salary, Salary())
        self.assertIsNone(item.employment_type)
        self.assertIsNone(item.work_arrangement)
        self.assertIsNone(item.source_created_at)

    def test_invalid_salary_not_trusted(self):
        for pay in ({"min": True}, {"min": -1}, {"min": float("nan")}, {"min": "50000"}, {"min": 500, "max": 100}):
            with self.subTest(pay=pay):
                self.assertIsNone(LeverSource.normalize(lever_row(salaryRange=pay), LEVER_BOARD).salary.minimum)

    def test_required_fields_and_malformed_schemas(self):
        for row in (None, {}, greenhouse_row(absolute_url="javascript:alert(1)"), greenhouse_row(id=None)):
            with self.subTest(row=row), self.assertRaises(SourceError):
                GreenhouseSource.normalize(row, BOARD)
        for row in (None, {}, lever_row(categories="invalid"), lever_row(lists="invalid"), lever_row(salaryRange="invalid")):
            with self.subTest(row=row), self.assertRaises(SourceError):
                LeverSource.normalize(row, LEVER_BOARD)

    def test_invalid_dates_stay_unknown(self):
        for value in ("yesterday", "2026-11-01", None, 123):
            with self.subTest(value=value):
                self.assertIsNone(GreenhouseSource.normalize(greenhouse_row(first_published=value), BOARD).posted_at)

    def test_job_serialization_roundtrip(self):
        self.assertEqual(Job.from_dict(json.loads(json.dumps(job().to_dict()))), job())


class LocationTests(unittest.TestCase):
    def test_waterloo_and_nearby(self):
        waterloo = resolve("Waterloo, ON, Canada")
        self.assertIsNotNone(waterloo)
        self.assertAlmostEqual(distance_km(43.4643, -80.5204, waterloo), 0)
        self.assertLess(distance_km(43.4643, -80.5204, resolve("Guelph, Ontario")), 100)
        self.assertAlmostEqual(distance_km(43.4643, -80.5204, resolve("Toronto, Ontario")), 94.0, delta=0.2)
        self.assertGreater(distance_km(43.4643, -80.5204, resolve("Ottawa, Ontario")), 100)

    def test_ambiguous_and_foreign_names_not_assumed_ontario(self):
        for name in ("Waterloo", "Waterloo, Iowa", "London", "London, UK", "Toronto area", "Remote, Canada"):
            with self.subTest(name=name):
                self.assertIsNone(resolve(name))

    def test_labels_and_custom_places(self):
        self.assertEqual(resolve("Waterloo, Ontario (Hybrid)").name, "Waterloo, Ontario")
        custom = Place("Verified City", 10, 20, ("Employer office label",))
        self.assertEqual(resolve("Employer office label", (custom,)), custom)
        self.assertIsNone(resolve("Employer office label", (custom, custom)))

    def test_radius_boundary_and_multilocation(self):
        self.assertTrue(evaluate(job(), SearchFilters(radius_km=0)).accepted)
        self.assertFalse(evaluate(job(locations=("Ottawa, Ontario",)), SearchFilters()).accepted)
        self.assertTrue(evaluate(job(locations=("Toronto, Ontario", "Guelph, Ontario")), SearchFilters()).accepted)

    def test_remote_outside_radius_requires_explicit_opt_in(self):
        item = job(locations=("Remote, Canada",), work_arrangement="remote")
        self.assertFalse(evaluate(item, SearchFilters()).accepted)
        self.assertTrue(evaluate(item, SearchFilters(include_remote_outside_radius=True)).accepted)
        self.assertFalse(evaluate(replace(item, work_arrangement="hybrid"), SearchFilters(include_remote_outside_radius=True)).accepted)

    def test_unknown_location_and_disable_radius(self):
        item = job(locations=())
        self.assertFalse(evaluate(item, SearchFilters()).accepted)
        result = evaluate(item, SearchFilters(include_unknown=True))
        self.assertTrue(result.accepted)
        self.assertIn("location distance", result.unknown_fields)
        self.assertTrue(evaluate(item, SearchFilters(radius_km=None)).accepted)


class FilteringTests(unittest.TestCase):
    def test_title_or_and_keywords_all_case_insensitive(self):
        self.assertTrue(evaluate(job(), SearchFilters(titles=("Designer", "PYTHON"), keywords=("python", "SQLITE"))).accepted)
        self.assertFalse(evaluate(job(), SearchFilters(titles=("Designer",))).accepted)
        self.assertFalse(evaluate(job(), SearchFilters(keywords=("python", "Rust"))).accepted)

    def test_exclusions_override_inclusions(self):
        self.assertFalse(evaluate(job(), SearchFilters(keywords=("Python",), excluded_keywords=("PYTHON",))).accepted)
        self.assertFalse(evaluate(job(), SearchFilters(companies=("Fictional Employer",), excluded_companies=("fictional employer",))).accepted)
        self.assertFalse(evaluate(job(), SearchFilters(companies=("Other",))).accepted)

    def test_structured_filter_groups(self):
        cases = {"industries": ("Software", "Finance"), "employment_types": ("full-time", "contract"),
                 "work_arrangements": ("hybrid", "remote"), "experience_levels": ("entry-level", "senior")}
        for name, (matching, other) in cases.items():
            with self.subTest(name=name):
                self.assertTrue(evaluate(job(), SearchFilters(**{name: (matching,)})).accepted)
                self.assertFalse(evaluate(job(), SearchFilters(**{name: (other,)})).accepted)

    def test_every_employment_type_can_match(self):
        for value in ("internship", "co-op", "full-time", "part-time", "contract"):
            with self.subTest(value=value):
                self.assertTrue(evaluate(job(employment_type=value), SearchFilters(employment_types=(value,))).accepted)

    def test_experience_threshold(self):
        self.assertTrue(evaluate(job(), SearchFilters(max_experience_years=1)).accepted)
        self.assertFalse(evaluate(job(minimum_experience_years=3), SearchFilters(max_experience_years=2)).accepted)
        self.assertFalse(evaluate(job(minimum_experience_years=None), SearchFilters(max_experience_years=2)).accepted)

    def test_salary_uses_lower_bound_and_matching_units(self):
        self.assertTrue(evaluate(job(), SearchFilters(minimum_salary=60000)).accepted)
        self.assertFalse(evaluate(job(), SearchFilters(minimum_salary=70000)).accepted)
        for salary in (Salary(90000, 100000, "USD", "year"), Salary(100, 200, "CAD", "hour")):
            with self.subTest(salary=salary):
                self.assertFalse(evaluate(job(salary=salary), SearchFilters(minimum_salary=50000)).accepted)
        self.assertTrue(evaluate(job(salary=Salary(25, 30, "CAD", "hour")), SearchFilters(minimum_salary=20, salary_interval="hour")).accepted)

    def test_posting_date_window(self):
        filters = SearchFilters(posted_within_days=7)
        self.assertTrue(evaluate(job(), filters, now=NOW).accepted)
        for date in ("2026-10-01T12:00:00+00:00", "2026-11-06T12:00:00+00:00", None):
            with self.subTest(date=date):
                self.assertFalse(evaluate(job(posted_at=date), filters, now=NOW).accepted)

    def test_unknown_metadata_excluded_or_explicitly_flagged(self):
        item = job(salary=Salary(), experience_level=None, minimum_experience_years=None,
                   industry=None, employment_type=None, work_arrangement=None, posted_at=None)
        filters = SearchFilters(minimum_salary=50000, experience_levels=("entry-level",),
                                max_experience_years=2, industries=("Software",),
                                employment_types=("full-time",), work_arrangements=("remote",), posted_within_days=7)
        self.assertFalse(evaluate(item, filters).accepted)
        result = evaluate(item, replace(filters, include_unknown=True))
        self.assertTrue(result.accepted)
        self.assertEqual(len(result.unknown_fields), 7)
        self.assertFalse(evaluate(job(work_arrangement="on-site"), replace(filters, include_unknown=True)).accepted)


class AdapterTests(unittest.TestCase):
    def test_greenhouse_endpoint_and_schema(self):
        client = FakeClient({"jobs": [greenhouse_row()]})
        self.assertEqual(len(GreenhouseSource(client).fetch(BOARD)), 1)
        self.assertEqual(client.urls, ["https://boards-api.greenhouse.io/v1/boards/fictional/jobs?content=true"])
        with self.assertRaises(SourceError):
            GreenhouseSource(FakeClient({"error": "bad"})).fetch(BOARD)

    def test_lever_pagination_and_eu_region(self):
        client = FakeClient([lever_row(id="1"), lever_row(id="2")], [lever_row(id="3")])
        adapter = LeverSource(client)
        adapter.PAGE_SIZE = 2
        self.assertEqual(len(adapter.fetch(replace(LEVER_BOARD, region="eu"))), 3)
        self.assertIn("api.eu.lever.co", client.urls[0])
        self.assertIn("skip=2&limit=2", client.urls[1])

    def test_lever_repeated_page_and_pagination_cap(self):
        adapter = LeverSource(FakeClient([lever_row()], [lever_row()]))
        adapter.PAGE_SIZE = 1
        with self.assertRaisesRegex(SourceError, "repeated"):
            adapter.fetch(LEVER_BOARD)
        adapter = LeverSource(FakeClient([lever_row()]))
        adapter.PAGE_SIZE, adapter.MAX_PAGES = 1, 1
        with self.assertRaisesRegex(SourceError, "safety limit"):
            adapter.fetch(LEVER_BOARD)

    def test_empty_board_is_successful_and_wrong_schema_fails(self):
        self.assertEqual(LeverSource(FakeClient([])).fetch(LEVER_BOARD), [])
        with self.assertRaises(SourceError):
            LeverSource(FakeClient({"error": "bad"})).fetch(LEVER_BOARD)


class FakeOpener:
    def __init__(self, *outcomes):
        self.outcomes = iter(outcomes)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return io.BytesIO(outcome)


def http_error(code, retry=None):
    headers = Message()
    if retry:
        headers["Retry-After"] = retry
    return HTTPError("https://api.lever.co/test", code, "synthetic", headers, None)


class HttpTests(unittest.TestCase):
    URL = "https://api.lever.co/v0/postings/fictional?mode=json"

    def client(self, *outcomes):
        self.opener = FakeOpener(*outcomes)
        self.sleeps = []
        return JsonClient(opener=self.opener, sleep=self.sleeps.append, clock=lambda: 100)

    def test_get_headers_pacing_and_timeout(self):
        client = self.client(b"[]", b"[]")
        self.assertEqual(client.get(self.URL), [])
        self.assertEqual(client.get(self.URL), [])
        self.assertEqual(self.sleeps, [1.0])
        request, timeout = self.opener.requests[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.headers["Accept"], "application/json")
        self.assertEqual(timeout, 20)

    def test_rate_limit_and_denials_stop_host_without_retry(self):
        for code in (401, 403, 429):
            with self.subTest(code=code):
                client = self.client(http_error(code, "120"))
                with self.assertRaises(SourceError):
                    client.get(self.URL)
                with self.assertRaisesRegex(SourceError, "paused"):
                    client.get("https://api.lever.co/v0/postings/other")
                self.assertEqual(len(self.opener.requests), 1)
                self.assertEqual(self.sleeps, [])

    def test_service_retry_after_is_honored_by_stopping(self):
        client = self.client(http_error(503, "300"))
        with self.assertRaisesRegex(SourceError, "pause"):
            client.get(self.URL)
        self.assertEqual(len(self.opener.requests), 1)

    def test_transient_server_error_recovers(self):
        client = self.client(http_error(500), b"[]")
        self.assertEqual(client.get(self.URL), [])
        self.assertIn(2, self.sleeps)
        self.assertEqual(len(self.opener.requests), 2)

    def test_network_retries_are_bounded(self):
        for failure in (URLError("synthetic DNS error"), TimeoutError()):
            with self.subTest(failure=failure):
                client = self.client(failure, failure, failure)
                with self.assertRaises(SourceError):
                    client.get(self.URL)
                self.assertEqual(len(self.opener.requests), 3)

    def test_invalid_json_oversize_and_404(self):
        client = self.client(b"<html>blocked</html>")
        with self.assertRaisesRegex(SourceError, "JSON"):
            client.get(self.URL)
        client = self.client(b"01234567890")
        client.MAX_BYTES = 10
        with self.assertRaisesRegex(SourceError, "size limit"):
            client.get(self.URL)
        client = self.client(http_error(404))
        with self.assertRaisesRegex(SourceError, "404"):
            client.get(self.URL)
        self.assertEqual(len(self.opener.requests), 1)

    def test_tls_verification_stays_enabled(self):
        context = tls_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        client = self.client(URLError(ssl.SSLCertVerificationError("synthetic certificate error")))
        with self.assertRaisesRegex(SourceError, "certificate verification failed"):
            client.get(self.URL)
        self.assertEqual(len(self.opener.requests), 1)

    def test_macos_certificate_fallback_uses_system_bundle(self):
        with patch("getmeajob.sources.http.ssl.create_default_context") as create, \
             patch("getmeajob.sources.http.ssl.get_default_verify_paths") as defaults, \
             patch("getmeajob.sources.http.Path.is_file", return_value=True):
            defaults.return_value.cafile = None
            defaults.return_value.capath = None
            tls_context()
            create.return_value.load_verify_locations.assert_called_once_with(cafile="/etc/ssl/cert.pem")
            defaults.return_value.cafile = "/existing/bundle.pem"
            create.return_value.reset_mock()
            tls_context()
            create.return_value.load_verify_locations.assert_not_called()

    def test_incomplete_response_is_reported_as_source_failure(self):
        client = self.client(IncompleteRead(b"partial"), IncompleteRead(b"partial"), IncompleteRead(b"partial"))
        with self.assertRaises(SourceError):
            client.get(self.URL)
        self.assertEqual(len(self.opener.requests), 3)

    def test_json_decoder_exhaustion_is_reported_as_source_failure(self):
        client = self.client(b"[]")
        with patch("getmeajob.sources.http.json.loads", side_effect=RecursionError), \
             self.assertRaisesRegex(SourceError, "JSON"):
            client.get(self.URL)

    def test_no_unapproved_hosts_or_redirects(self):
        client = self.client()
        for url in ("http://api.lever.co/test", "https://example.invalid/test", "https://api.lever.co@evil.invalid/test"):
            with self.subTest(url=url), self.assertRaises(SourceError):
                client.get(url)
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.invalid"))
        client = self.client(http_error(302))
        with self.assertRaises(SourceError):
            client.get(self.URL)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.path = Path(self.directory.name) / "nested" / "jobs.sqlite3"
        self.store = JobStore(self.path)

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def test_repeated_search_preserves_first_and_update_timestamps(self):
        first, second, third = ("2026-11-01T12:00:00+00:00", "2026-11-02T12:00:00+00:00", "2026-11-03T12:00:00+00:00")
        identity = self.store.upsert(job(), first)
        self.assertEqual(self.store.upsert(job(), second), identity)
        self.assertEqual(self.store.count(), 1)
        record = self.store.details(identity)
        self.assertEqual(record["first_discovered"], first)
        self.assertEqual(record["last_checked"], second)
        self.assertEqual(record["last_updated"], first)
        self.store.upsert(job(description="Updated Python requirements."), third)
        record = self.store.details(identity)
        self.assertEqual(record["first_discovered"], first)
        self.assertEqual(record["last_updated"], third)
        observed = self.store.connection.execute("SELECT * FROM observations").fetchone()
        self.assertEqual(observed["first_discovered"], first)
        self.assertEqual(observed["last_checked"], third)
        self.assertEqual(Job.from_dict(json.loads(observed["payload"])).description, "Updated Python requirements.")

    def test_cross_source_url_tracking_dedup_retains_provenance(self):
        identity = self.store.upsert(job())
        second = job(source="lever", source_id="other", application_url="https://example.invalid/jobs/1?utm_source=other#section")
        self.assertEqual(self.store.upsert(second), identity)
        self.assertEqual(self.store.count(), 1)
        self.assertEqual(len(self.store.details(identity)["sources"]), 2)

    def test_cross_source_exact_content_with_different_urls(self):
        identity = self.store.upsert(job())
        self.assertEqual(self.store.upsert(job(source="lever", application_url="https://jobs.lever.co/fictional/other/apply")), identity)

    def test_similar_title_different_description_or_location_stays_distinct(self):
        self.store.upsert(job())
        self.store.upsert(job(source_id="2", application_url="https://example.invalid/jobs/2", description="Different team and requirements."))
        self.store.upsert(job(source_id="3", application_url="https://example.invalid/jobs/3", locations=("Guelph, Ontario",)))
        self.assertEqual(self.store.count(), 3)

    def test_unknown_description_not_used_as_content_identity(self):
        self.store.upsert(job(description=""))
        self.store.upsert(job(source_id="2", application_url="https://example.invalid/jobs/2", description=""))
        self.assertEqual(self.store.count(), 2)

    def test_changed_url_keeps_source_identity(self):
        identity = self.store.upsert(job())
        self.assertEqual(self.store.upsert(job(application_url="https://example.invalid/new-location/1")), identity)
        self.assertEqual(self.store.count(), 1)

    def test_persistence_after_close_and_reopen(self):
        identity = self.store.upsert(job())
        with JobStore(self.path) as reopened:
            self.assertEqual(reopened.count(), 1)
            self.assertEqual(reopened.job_id(job()), identity)
            payload = reopened.connection.execute("SELECT payload FROM observations").fetchone()[0]
            self.assertEqual(Job.from_dict(json.loads(payload)), job())

    def test_identity_bridge_merges_rows_and_preserves_all_sources(self):
        first = self.store.upsert(job())
        second = self.store.upsert(job(source="lever", source_id="2", application_url="https://example.invalid/jobs/2", description="Different requirements"))
        self.assertNotEqual(first, second)
        bridge = job(source="lever", source_id="2", application_url=job().application_url)
        self.assertEqual(self.store.upsert(bridge), first)
        self.assertEqual(self.store.count(), 1)
        self.assertEqual(len(self.store.details(first)["sources"]), 2)
        self.assertEqual(self.store.connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_sql_looking_description_is_only_data(self):
        item = job(description="'); DROP TABLE jobs; --")
        self.store.upsert(item)
        self.assertEqual(self.store.count(), 1)
        payload = self.store.connection.execute("SELECT payload FROM observations").fetchone()[0]
        self.assertEqual(Job.from_dict(json.loads(payload)), item)

    def test_canonical_urls_keep_real_ids(self):
        self.assertNotEqual(canonical_url("https://example.invalid/careers?gh_jid=1"),
                            canonical_url("https://example.invalid/careers?gh_jid=2"))
        self.assertEqual(canonical_url("https://jobs.lever.co/fictional/1/apply?utm_source=test"),
                         "https://jobs.lever.co/fictional/1")


class SearchConfigurationTests(unittest.TestCase):
    def load(self, text):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "settings.toml"
            path.write_text(text)
            return load_settings(path)

    def test_live_example_has_both_adapters_and_waterloo_radius(self):
        settings = load_settings(ROOT / "config/search.example.toml")
        self.assertEqual({board.source for board in settings.boards}, {"greenhouse", "lever"})
        self.assertEqual(settings.search.city, "Waterloo, Ontario")
        self.assertEqual(settings.search.radius_km, 100)
        self.assertFalse(settings.search.include_unknown)

    def test_all_filter_settings_parse(self):
        settings = self.load('''[search]
titles = ["Developer"]
keywords = ["Python"]
excluded_keywords = ["Sales"]
companies = ["Example"]
excluded_companies = ["Other"]
industries = ["Software"]
work_arrangements = ["hybrid"]
employment_types = ["co-op"]
experience_levels = ["entry-level"]
max_experience_years = 2
minimum_salary = 25
salary_currency = "CAD"
salary_interval = "hour"
posted_within_days = 7
include_unknown = true
include_remote_outside_radius = true
use_radius = false
''')
        self.assertEqual(settings.search.keywords, ("Python",))
        self.assertEqual(settings.search.minimum_salary, 25)
        self.assertIsNone(settings.search.radius_km)

    def test_changing_city_resolves_coordinates_or_requires_both(self):
        settings = self.load('[search]\ncity="Ottawa, Ontario"')
        self.assertAlmostEqual(settings.search.latitude, 45.4215)
        with self.assertRaises(ConfigurationError):
            self.load('[search]\ncity="Unknown City"')
        settings = self.load('[search]\ncity="Custom City"\nlatitude=10\nlongitude=20')
        self.assertEqual(settings.search.longitude, 20)

    def test_custom_location_aliases(self):
        settings = self.load('''[[locations]]
name = "Verified Custom City"
latitude = 10
longitude = 20
aliases = ["Exact employer label"]
[search]
city = "Verified Custom City"
''')
        self.assertEqual(settings.search.latitude, 10)
        self.assertEqual(resolve("Exact employer label", settings.places).longitude, 20)

    def test_invalid_config_values_are_rejected(self):
        for text in (
            '[search]\nkeywords="Python"', '[search]\nkeywords=[1]', '[search]\nminimum_salary=-1',
            '[search]\nmax_experience_years=nan', '[search]\nposted_within_days=true',
            '[search]\ninclude_unknown="true"', '[search]\nuse_radius=1', '[search]\nlatitude=43',
            '[search]\nlatitude=91\nlongitude=0', '[search]\nwork_arrangements=["anywhere"]',
            '[search]\nemployment_types=["permanent-ish"]', '[search]\nexperience_levels=["junior-ish"]',
            '[search]\nsalary_currency="dollars"', '[search]\nsalary_interval="day"',
            '[network]\nrequest_interval_seconds=0', '[storage]\ndatabase=1',
            'boards="bad"', 'locations="bad"', '[[locations]]\nname="City"',
            '[[locations]]\nname="City"\nlatitude=0\nlongitude=0\naliases="bad"',
        ):
            with self.subTest(text=text), self.assertRaises(ConfigurationError):
                self.load(text)

    def test_invalid_and_duplicate_boards_rejected(self):
        for suffix in ('source="other"\ntoken="valid"', 'source="lever"\ntoken="../secret"',
                       'source="lever"\ntoken="https://example.invalid"', 'source="lever"',
                       'source="greenhouse"\ntoken="valid"\nregion="eu"'):
            with self.subTest(suffix=suffix), self.assertRaises(ConfigurationError):
                self.load('[[boards]]\nemployer="Fictional"\n' + suffix)
        board = '[[boards]]\nemployer="Fictional"\nsource="lever"\ntoken="valid"\n'
        with self.assertRaisesRegex(ConfigurationError, "Duplicate"):
            self.load(board + board)


class SearchIntegrationTests(unittest.TestCase):
    def test_partial_failure_does_not_discard_successful_boards(self):
        settings = Settings(boards=(BOARD, LEVER_BOARD))
        adapters = {"greenhouse": GreenhouseSource(FakeClient({"jobs": [greenhouse_row()]})),
                    "lever": LeverSource(FakeClient(SourceError("HTTP 403 synthetic")))}
        with JobStore(Path(":memory:")) as store:
            report = search(settings, store, adapters=adapters)
            self.assertEqual(report.fetched, 1)
            self.assertEqual(report.successful_boards, 1)
            self.assertEqual(len(report.results), 1)
            self.assertEqual(report.stored_jobs, 1)
            self.assertIn("403", report.errors[0])

    def test_only_current_results_displayed_and_unmatched_stored(self):
        with JobStore(Path(":memory:")) as store:
            store.upsert(job(source_id="old", application_url="https://example.invalid/old", description="Old job."))
            results = save_and_filter([job(locations=("Ottawa, Ontario",))], Settings(), store)
            self.assertEqual(results, ())
            self.assertEqual(store.count(), 2)

    def test_dedup_before_display_and_matching_observation_used(self):
        with JobStore(Path(":memory:")) as store:
            settings = Settings(search=SearchFilters(work_arrangements=("hybrid",)))
            results = save_and_filter([job(work_arrangement=None), job(source="lever")], settings, store)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].job.source, "lever")
            self.assertEqual(len(results[0].record["sources"]), 2)

    def test_demo_is_offline_and_separate_from_live_database(self):
        with TemporaryDirectory() as directory:
            live_database = Path(directory) / "live.sqlite3"
            config = Path(directory) / "settings.toml"
            config.write_text('[storage]\ndatabase=' + json.dumps(str(live_database)))
            result = subprocess.run([sys.executable, "-m", "getmeajob", "demo", "--config", str(config), "--json"], cwd=ROOT,
                                    capture_output=True, text=True, check=False)
            self.assertFalse(live_database.exists())
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(data["fetched"], 5)
            self.assertEqual(data["matching_jobs"], 2)
            self.assertEqual(data["stored_jobs"], 4)
            self.assertTrue(all(item["source"].startswith("demo-") for item in data["results"]))
        with patch("getmeajob.__main__.search", side_effect=AssertionError("Demo must not search online")), patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(main(["demo"]), 0)

    def test_search_requires_explicit_boards_and_bad_limit_fails(self):
        with patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(main(["search"]), 2)
            self.assertEqual(main(["demo", "--limit", "0"]), 2)

    def test_cli_partial_failure_returns_one_and_json_remains_valid(self):
        from getmeajob.discovery import SearchReport
        with TemporaryDirectory() as directory:
            settings = Settings(boards=(BOARD,), database=Path(directory) / "test.sqlite3")
            with patch("getmeajob.__main__.load_settings", return_value=settings), \
                 patch("getmeajob.__main__.search", return_value=SearchReport((), 0, 0, ("synthetic failure",), 0)), \
                 patch("sys.stdout", new=io.StringIO()) as output:
                self.assertEqual(main(["search", "--json"]), 1)
                self.assertEqual(json.loads(output.getvalue())["errors"], ["synthetic failure"])

    def test_terminal_control_sequences_are_removed(self):
        self.assertNotIn("\x1b", terminal("Role\x1b[2J"))
        self.assertNotIn("\u202e", terminal("Role\u202eevil"))
