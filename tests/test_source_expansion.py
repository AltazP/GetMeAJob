"""Offline workflows, source discovery and network security regression tests."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.message import Message
from http.client import HTTPException
import io
import json
from pathlib import Path
import socket
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request

from getmeajob.__main__ import main
from getmeajob.config import DiscoverySettings, ConfigurationError, Settings, load_settings
from getmeajob.employer_discovery import Links, PublicPages, discover, discover_hn
from getmeajob.migrations import migrate
from getmeajob.network import PinnedConnection, SafeOpener, addresses
from getmeajob.registry import Registry
from getmeajob.source_management import search_enabled, snapshot_store, verify
from getmeajob.sources import Board, SourceError
from getmeajob.sources.ashby import AshbySource
from getmeajob.sources.http import BudgetExceeded, JsonClient, retry_seconds
from getmeajob.storage import JobStore
from tests.test_discovery import FakeClient, FakeOpener, http_error, job
from tests.test_ashby import row as ashby_row


class CountingClient(FakeClient):
    requests = 0
    interval = 1

    def get(self, url):
        self.requests += 1
        return super().get(url)

    def read(self, url, **kwargs):
        return self.get(url)


def thread(**changes):
    return {"id": 10, "by": "whoishiring", "type": "story", "title": "Ask HN: Who is hiring? (Synthetic month)", "kids": [11, 12], **changes}


def comment(identity=11, **changes):
    return {"id": identity, "type": "comment", "parent": 10,
            "text": 'Fictional Labs | Waterloo | <a href="https://jobs.ashbyhq.com/fictional/123">Careers</a>', **changes}


class ExpansionWorkflows(unittest.TestCase):
    def setUp(self):
        self.store = JobStore(Path(":memory:"))
        self.registry = Registry(self.store)
        self.settings = Settings()
        self.source = self.registry.add("Fictional Labs", ats_url="https://jobs.ashbyhq.com/fictional")

    def tearDown(self):
        self.store.close()

    def adapters(self, client):
        return {"ashby": AshbySource(client)}

    def test_official_verification_then_cached_search_and_real_coverage(self):
        client = CountingClient({"jobs": [ashby_row()]})
        self.assertEqual(self.registry.eligible(), [])
        report = verify(self.registry, self.settings, client, adapters=self.adapters(client))
        self.assertEqual(report["verified"], 1)
        self.assertEqual(self.store.count(), 0)  # Verification cache is separate from collected jobs.
        self.assertEqual(self.registry.rows()[0]["status"], "VERIFIED")
        network = CountingClient()  # Any network request would fail this fixture.
        report = search_enabled(self.registry, self.settings, network, adapters=self.adapters(network))
        self.assertEqual(len(report.results), 1)
        self.assertEqual(report.requests, 0)
        self.assertEqual(report.cached_sources, 1)
        coverage = self.registry.coverage()
        self.assertEqual(coverage["total_known_employers"], 1)
        self.assertEqual(coverage["verified_employers"], 1)
        self.assertEqual(coverage["active_employer_boards"], 1)
        self.assertEqual(coverage["jobs_collected_per_provider"], {"ashby": 1})
        self.assertEqual(coverage["new_jobs_last_run"], 1)
        search_enabled(self.registry, self.settings, network, adapters=self.adapters(network))
        self.assertEqual(self.registry.coverage()["new_jobs_last_run"], 0)

    def test_schema_failure_does_not_verify_and_persists_backoff(self):
        client = CountingClient({"not_jobs": []})
        report = verify(self.registry, self.settings, client, adapters=self.adapters(client))
        self.assertEqual(report["verified"], 0)
        self.assertEqual(len(report["errors"]), 1)
        row = self.registry.rows()[0]
        self.assertEqual(row["status"], "TEMPORARILY_UNAVAILABLE")
        self.assertIsNone(row["last_verified"])
        no_network = CountingClient()
        self.assertEqual(verify(self.registry, self.settings, no_network, force=True, adapters=self.adapters(no_network))["skipped"], 1)

    def test_offline_verification_and_disabled_sources_never_request(self):
        client = CountingClient()
        report = verify(self.registry, self.settings, client, offline=True)
        self.assertEqual(report["planned_sources"], [self.source])
        self.assertEqual(self.registry.rows()[0]["status"], "CANDIDATE")
        self.registry.set_enabled(False, source_id=self.source)
        self.assertEqual(verify(self.registry, self.settings, client)["planned_sources"], [])
        self.assertEqual(client.requests, 0)

    def test_stale_verification_rechecked_but_fresh_not_downloaded(self):
        self.registry.success(self.source, 0)
        client = CountingClient()
        self.assertEqual(verify(self.registry, self.settings, client)["skipped"], 1)
        with self.registry.db:
            self.registry.db.execute("UPDATE career_sources SET last_verified='2020-01-01T00:00:00+00:00'")
        self.assertEqual(self.registry.coverage()["stale_sources"], 1)
        client = CountingClient({"jobs": []})
        self.assertEqual(verify(self.registry, self.settings, client, adapters=self.adapters(client))["verified"], 1)

    def test_force_refresh_failures_are_isolated_from_other_sources(self):
        other = self.registry.add("Other Fictional", ats_url="https://jobs.ashbyhq.com/other")
        for source in (self.source, other):
            self.registry.success(source, 0)
        client = CountingClient(SourceError("Synthetic timeout"), {"jobs": [ashby_row(id="other", jobUrl="https://jobs.ashbyhq.com/other/1", applyUrl="https://jobs.ashbyhq.com/other/1/apply")]})
        report = search_enabled(self.registry, self.settings, client, force=True, adapters=self.adapters(client))
        self.assertEqual(report.successful_boards, 1)
        self.assertEqual(len(report.errors), 1)
        self.assertEqual(len(report.results), 1)
        self.assertEqual(self.registry.rows()[0]["status"], "TEMPORARILY_UNAVAILABLE")
        self.assertEqual(self.registry.rows()[1]["status"], "ACTIVE")

    def test_budget_deferral_does_not_mark_source_broken(self):
        client = CountingClient(BudgetExceeded("Synthetic budget"))
        report = verify(self.registry, self.settings, client, adapters=self.adapters(client))
        self.assertIn("deferred", report["errors"][0])
        self.assertEqual(self.registry.rows()[0]["status"], "CANDIDATE")
        self.assertEqual(self.registry.rows()[0]["failure_count"], 0)

    def test_unsupported_pages_never_use_job_adapters(self):
        source = self.registry.add("Fictional Workday", careers_url="https://fictional.myworkdayjobs.com/jobs")
        self.registry.set_enabled(False, source_id=self.source)
        report = verify(self.registry, self.settings, CountingClient(), adapters={})
        self.assertEqual(report["verified"], 0)
        self.assertEqual(self.registry.rows()[1]["status"], "UNSUPPORTED")
        self.assertEqual(self.registry.coverage()["unsupported_sources"], 1)

    def test_offline_cache_search_keeps_original_check_time(self):
        self.registry.success(self.source, 1)
        observed = "2026-01-01T00:00:00+00:00"
        item = job(source="ashby", board="fictional")
        self.registry.cache(self.source, [item], observed)
        report = search_enabled(self.registry, self.settings, CountingClient(), offline=True)
        self.assertEqual(report.mode, "offline")
        self.assertEqual(report.results[0].record["last_checked"], observed)
        self.assertEqual(report.requests, 0)

    def test_old_cache_cannot_overwrite_a_newer_job_observation(self):
        item = job()
        self.store.upsert(item, "2026-01-02T00:00:00+00:00")
        self.store.upsert(replace(item, description="Old"), "2026-01-01T00:00:00+00:00")
        stored = json.loads(self.registry.db.execute("SELECT payload FROM observations").fetchone()[0])
        self.assertEqual(stored["description"], item.description)


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.store = JobStore(Path(":memory:"))
        self.registry = Registry(self.store)

    def tearDown(self):
        self.store.close()

    def test_automatic_hn_thread_discovery_and_incremental_comments(self):
        client = CountingClient({"submitted": [9, 10]}, {"title": "Other story"}, thread(), thread(), comment(), comment(12))
        report = discover_hn(self.registry, client, DiscoverySettings())
        self.assertEqual(report["new_sources"], 1)
        self.assertEqual(report["comments_checked"], 2)
        self.assertEqual(self.registry.rows()[0]["status"], "CANDIDATE")
        self.assertEqual(self.registry.rows()[0]["discovery_source"], "hn-hiring-api")
        second = discover_hn(self.registry, CountingClient(thread()), DiscoverySettings(hn_story_id=10))
        self.assertEqual(second["comments_checked"], 0)

    def test_hn_api_nofollow_links_are_candidates_without_following_them(self):
        fixture = comment(text='Fictional Labs | Waterloo<p><a href="https://jobs.ashbyhq.com/fictional" rel="nofollow">Jobs</a>')
        client = CountingClient(thread(kids=[11]), fixture)
        report = discover_hn(self.registry, client, DiscoverySettings(hn_story_id=10))
        self.assertEqual(report["new_sources"], 1)
        self.assertTrue(all("hacker-news.firebaseio.com" in url for url in client.urls))
        again = discover_hn(self.registry, CountingClient(thread(kids=[11]), fixture), DiscoverySettings(hn_story_id=10), recheck=True)
        self.assertEqual(again["new_sources"], 0)
        self.assertEqual(again["comments_checked"], 1)

    def test_hn_leading_paragraph_and_html_pipe_headers(self):
        client = CountingClient(thread(kids=[11]), comment(text='<p>Fictional Labs &#124; Waterloo<p><a href="https://jobs.ashbyhq.com/fictional">Jobs</a>'))
        report = discover_hn(self.registry, client, DiscoverySettings(hn_story_id=10))
        self.assertEqual(report["new_sources"], 1)
        self.assertEqual(report["ats_links_detected"], 1)
        self.assertEqual(self.registry.rows()[0]["canonical_name"], "Fictional Labs")

    def test_hn_missing_employer_header_does_not_invent_name_from_token(self):
        client = CountingClient(thread(kids=[11]), comment(text='<a href="https://jobs.ashbyhq.com/fictional">Apply</a>'))
        discover_hn(self.registry, client, DiscoverySettings(hn_story_id=10))
        self.assertEqual(self.registry.rows(), [])

    def test_hn_thread_validation_partial_failure_and_comment_bound(self):
        for story in (None, thread(by="impostor"), thread(deleted=True), thread(title="Other story")):
            with self.subTest(story=story), self.assertRaises(SourceError):
                discover_hn(self.registry, CountingClient(story), DiscoverySettings(hn_story_id=10))
        client = CountingClient(thread(), SourceError("Synthetic API failure"), comment(12))
        report = discover_hn(self.registry, client, DiscoverySettings(hn_story_id=10))
        self.assertEqual(len(report["errors"]), 1)
        self.assertEqual(report["new_sources"], 1)
        limited = CountingClient(thread(kids=[21, 22]), comment(21))
        self.assertEqual(discover_hn(self.registry, limited, DiscoverySettings(hn_story_id=10, max_items=1))["comments_checked"], 1)

    def test_html_links_extraction_and_nofollow(self):
        links = Links("https://fictional.example/careers").extract('''
            <base href="http://localhost"><a href="javascript:bad">ignore</a>
            <a href="/jobs">Jobs</a><iframe src="https://boards.greenhouse.io/embed/job_board?for=fictional"></iframe>
            <a href="https://jobs.lever.co/ignored" rel="nofollow">ignore</a>
            <script>executeNothing()</script>
        ''')
        self.assertEqual(links, ["https://fictional.example/jobs", "https://boards.greenhouse.io/embed/job_board?for=fictional"])
        self.assertEqual(Links("https://fictional.example").extract('<meta name="robots" content="nofollow"><a href="/jobs">x</a>'), [])

    def test_career_page_robots_and_candidate_extraction(self):
        self.registry.add("Fictional Labs", careers_url="https://fictional.example/careers")
        client = CountingClient(b"User-agent: *\nAllow: /\nCrawl-delay: 3\n", b'<a href="https://jobs.ashbyhq.com/fictional">Jobs</a>')
        settings = Settings(discovery=DiscoverySettings(hn_enabled=False, page_hosts=("fictional.example",)))
        report = discover(self.registry, settings, client)
        self.assertEqual(report["new_sources"], 1)
        self.assertEqual(client.interval, 3)
        self.assertEqual(self.registry.rows()[1]["status"], "CANDIDATE")
        self.assertEqual(self.registry.rows()[0]["status"], "UNSUPPORTED")

    def test_robots_denial_and_unapproved_page_never_fetch_html(self):
        client = CountingClient(b"User-agent: *\nDisallow: /\n")
        pages = PublicPages(client, ("fictional.example",))
        with self.assertRaisesRegex(SourceError, "robots"):
            pages.read("https://fictional.example/careers")
        self.assertEqual(client.requests, 1)
        with self.assertRaisesRegex(SourceError, "not explicitly permitted"):
            pages.read("https://other.example/careers")
        self.assertEqual(client.requests, 1)

    def test_robots_fetch_errors_fail_closed_except_explicit_404(self):
        pages = PublicPages(CountingClient(SourceError("Timeout")), ("fictional.example",))
        with self.assertRaises(SourceError):
            pages.read("https://fictional.example/careers")
        missing = SourceError("HTTP 404")
        missing.__cause__ = HTTPError("https://fictional.example/robots.txt", 404, "missing", {}, None)
        client = CountingClient(missing, b"Public page")
        self.assertEqual(PublicPages(client, ("fictional.example",)).read("https://fictional.example/careers"), "Public page")

    def test_offline_discovery_seeds_only_no_network(self):
        settings = Settings(boards=(Board("ashby", "fictional", "Fictional Labs"),))
        client = CountingClient()
        report = discover(self.registry, settings, client, offline=True)
        self.assertEqual(report["new_sources"], 1)
        self.assertEqual(client.requests, 0)
        self.assertEqual(self.registry.rows()[0]["status"], "CANDIDATE")


class NetworkBoundaryTests(unittest.TestCase):
    def test_dns_rejects_private_mixed_and_translation_addresses(self):
        for address in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "fc00::1", "64:ff9b::7f00:1", "2002:7f00:1::"):
            family = socket.AF_INET6 if ":" in address else socket.AF_INET
            rows = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
                    (family, socket.SOCK_STREAM, 6, "", (address, 443))]
            with self.subTest(address=address), patch("getmeajob.network.socket.getaddrinfo", return_value=rows), self.assertRaises(SourceError):
                addresses("fictional.example", 20)

    def test_connection_uses_validated_address_and_original_tls_hostname(self):
        context, raw = Mock(), Mock()
        resolved = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))
        connection = PinnedConnection("fictional.example", resolved, context, 20)
        with patch("getmeajob.network.socket.socket", return_value=raw):
            connection.connect()
        raw.connect.assert_called_once_with(("8.8.8.8", 443))
        context.wrap_socket.assert_called_once_with(raw, server_hostname="fictional.example")
        raw.settimeout.assert_called_once_with(20)

    def test_redirect_never_followed_by_pinned_transport(self):
        response = Mock(status=302, headers={"Location": "http://127.0.0.1"})
        with patch("getmeajob.network.addresses", return_value=[("synthetic",)]), \
             patch("getmeajob.network.PinnedConnection") as connection:
            connection.return_value.getresponse.return_value = response
            with self.assertRaises(HTTPError):
                SafeOpener(Mock()).open(Request("https://fictional.example/careers"), 20)
            self.assertEqual(connection.call_count, 1)
            connection.return_value.close.assert_called()

    def test_global_host_source_budgets_and_offline(self):
        for kwargs in ({"max_requests": 1}, {"per_host_limit": 1}, {"per_source_limit": 1}):
            opener = FakeOpener(b"[]")
            client = JsonClient(opener=opener, **kwargs)
            self.assertEqual(client.get("https://api.lever.co/first"), [])
            with self.assertRaises(BudgetExceeded):
                client.get("https://api.lever.co/second")
            self.assertEqual(len(opener.requests), 1)
        client = JsonClient(opener=FakeOpener(), offline=True)
        with self.assertRaises(BudgetExceeded):
            client.get("https://api.lever.co/first")
        self.assertEqual(client.requests, 0)

    def test_host_pause_persists_across_client_instances(self):
        with JobStore(Path(":memory:")) as store:
            registry = Registry(store)
            client = JsonClient(opener=FakeOpener(http_error(429, "120")), state=registry)
            with self.assertRaises(SourceError):
                client.get("https://api.lever.co/first")
            new = JsonClient(opener=FakeOpener(), state=registry)
            with self.assertRaisesRegex(SourceError, "paused"):
                new.get("https://api.lever.co/second")
            self.assertEqual(new.requests, 0)
            self.assertGreater(retry_seconds("120"), 119)
            self.assertEqual(retry_seconds("invalid"), 3600)


class ExpansionCliTests(unittest.TestCase):
    def invoke(self, arguments):
        with patch("sys.stdout", new=io.StringIO()) as output, patch("sys.stderr", new=io.StringIO()):
            result = main(arguments)
            return result, output.getvalue()

    def test_dry_run_offline_and_import_do_not_create_or_modify_disk_database(self):
        with TemporaryDirectory() as directory:
            db = Path(directory) / "not-created.sqlite3"
            config = Path(directory) / "settings.toml"
            config.write_text('[storage]\ndatabase=' + json.dumps(str(db)))
            csv = Path(directory) / "employers.csv"
            csv.write_text("name,ats_url\nFictional,https://jobs.ashbyhq.com/fictional\n")
            for arguments in (
                ["employers", "discover", "--offline"], ["employers", "verify", "--dry-run"],
                ["employers", "import", "--file", str(csv), "--dry-run"],
                ["employers", "coverage", "--offline"], ["search", "--all-enabled", "--dry-run"],
                ["search", "--all-enabled", "--dry-run", "--offline"],
            ):
                with self.subTest(arguments=arguments):
                    status, output = self.invoke(["--config", str(config), *arguments])
                    self.assertEqual(status, 0, output)
                    self.assertFalse(db.exists())
            with JobStore(db) as store:
                store.upsert(job())
            before = db.read_bytes()
            self.invoke(["employers", "discover", "--config", str(config), "--offline"])
            self.assertEqual(db.read_bytes(), before)

    def test_real_csv_command_and_offline_list(self):
        with TemporaryDirectory() as directory:
            config = Path(directory) / "settings.toml"
            config.write_text('[storage]\ndatabase=' + json.dumps(str(Path(directory) / "test.sqlite3")))
            csv = Path(directory) / "employers.csv"
            csv.write_text("name,ats_url\nFictional,https://jobs.ashbyhq.com/fictional\n")
            self.assertEqual(self.invoke(["employers", "import", "--config", str(config), "--file", str(csv)])[0], 0)
            status, output = self.invoke(["employers", "list", "--config", str(config), "--offline"])
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(output)["sources"][0]["status"], "CANDIDATE")

    def test_invalid_discovery_config_rejected(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "settings.toml"
            for text in ('max_requests=-1', 'max_items=true', 'page_hosts=["localhost"]',
                         'page_hosts=["example.com/secret"]', 'disabled_providers=["unknown"]',
                         'hn_enabled="yes"', 'refresh_minutes=0', 'timeout_seconds=1000'):
                with self.subTest(text=text), self.assertRaises(ConfigurationError):
                    path.write_text('[discovery]\n' + text)
                    load_settings(path)

    def test_source_cache_migration_from_registry_v1(self):
        with JobStore(Path(":memory:")) as store:
            store.connection.execute("DROP TABLE source_cache")
            store.connection.execute("PRAGMA user_version=1")
            migrate(store.connection)
            self.assertIsNotNone(store.connection.execute("SELECT name FROM sqlite_master WHERE name='source_cache'").fetchone())

    def test_offline_snapshot_migrates_legacy_data_only_in_memory(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite3"
            with patch("getmeajob.storage.migrate"):
                with JobStore(path) as legacy:
                    legacy.upsert(job())
            before = path.read_bytes()
            with snapshot_store(path) as preview:
                self.assertEqual(preview.count(), 1)
                self.assertEqual(Registry(preview).rows(), [])
            self.assertEqual(path.read_bytes(), before)
