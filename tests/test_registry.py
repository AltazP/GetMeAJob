"""Synthetic registry, migration and CSV tests; no network access."""

from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from getmeajob.migrations import migrate
from getmeajob.registry import Registry
from getmeajob.sources import SourceError
from getmeajob.storage import JobStore
from getmeajob.urls import board_url, detect, public_url


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.store = JobStore(Path(":memory:"))
        self.registry = Registry(self.store)

    def tearDown(self):
        self.store.close()

    def add(self, **kwargs):
        return self.registry.add("Fictional Labs", ats_url="https://jobs.ashbyhq.com/fictional", **kwargs)

    def test_detected_board_remains_candidate_with_evidence(self):
        source_id = self.add(method="csv")
        row = self.registry.rows()[0]
        self.assertEqual(row["id"], source_id)
        self.assertEqual(row["status"], "CANDIDATE")
        self.assertEqual(row["board_identifier"], "fictional")
        self.assertIsNone(row["last_verified"])
        self.assertEqual(self.registry.eligible(), [])
        self.assertEqual(self.store.connection.execute("SELECT method FROM source_evidence").fetchone()[0], "csv")

    def test_same_board_and_multiple_methods_merge_and_keep_evidence(self):
        first = self.add(method="csv")
        second = self.registry.add("Fictional Alternate Label", ats_url="https://jobs.ashbyhq.com/fictional/123", method="page", evidence="https://fictional.example/careers")
        self.assertEqual(first, second)
        self.assertEqual(len(self.registry.rows()), 1)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM source_evidence").fetchone()[0], 2)

    def test_names_website_and_board_bridge_merge_employers(self):
        self.registry.add("Fictional Labs", website="https://fictional.example", careers_url="https://fictional.example/careers")
        self.registry.add("Other Label", ats_url="https://jobs.ashbyhq.com/fictional")
        self.registry.add("Fictional Labs", ats_url="https://jobs.ashbyhq.com/fictional")
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM employers").fetchone()[0], 1)
        self.assertEqual(len(self.registry.rows()), 2)
        self.assertEqual(self.store.connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_success_and_failure_health_states(self):
        source_id = self.add()
        self.registry.success(source_id, 5)
        self.assertEqual(self.registry.eligible()[0]["status"], "VERIFIED")
        self.registry.success(source_id, 5, active=True)
        self.assertEqual(self.registry.rows()[0]["status"], "ACTIVE")
        self.registry.failure(source_id, "Synthetic timeout")
        row = self.registry.rows()[0]
        self.assertEqual(row["failure_count"], 1)
        self.assertEqual(row["status"], "TEMPORARILY_UNAVAILABLE")
        self.assertIsNotNone(row["next_check"])
        self.assertIsNotNone(row["last_successful_fetch"])
        self.registry.success(source_id, 0)
        self.assertEqual(self.registry.rows()[0]["failure_count"], 0)

    def test_disable_and_reenable_source_requires_reverification(self):
        source_id = self.add()
        self.registry.success(source_id, 1)
        self.registry.set_enabled(False, source_id=source_id)
        self.add()  # rediscovery must not silently enable it
        self.assertEqual(self.registry.rows()[0]["status"], "DISABLED")
        self.assertEqual(self.registry.eligible(), [])
        self.registry.set_enabled(True, source_id=source_id)
        self.assertEqual(self.registry.rows()[0]["status"], "CANDIDATE")
        self.assertEqual(self.registry.eligible(), [])

    def test_employer_and_provider_controls(self):
        source_id = self.add()
        self.registry.success(source_id, 1)
        self.registry.set_enabled(False, provider="ashby")
        self.assertEqual(self.registry.eligible(), [])
        self.registry.set_enabled(True, provider="ashby")
        self.assertEqual(len(self.registry.eligible()), 1)
        employer_id = self.registry.rows()[0]["employer_id"]
        self.registry.set_enabled(False, employer_id=employer_id)
        self.assertEqual(self.registry.rows()[0]["status"], "DISABLED")
        self.registry.set_enabled(True, employer_id=employer_id)
        self.assertEqual(self.registry.eligible(), [])
        for kwargs in ({"source_id": 999}, {"employer_id": 999}, {"provider": "imaginary"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(SourceError):
                self.registry.set_enabled(False, **kwargs)

    def test_stale_sources_not_searched(self):
        source_id = self.add()
        self.registry.success(source_id, 1)
        with self.store.connection:
            self.store.connection.execute("UPDATE career_sources SET last_verified='2020-01-01T00:00:00+00:00'")
        self.assertEqual(self.registry.eligible(), [])

    def test_csv_import_is_idempotent_and_offline(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "employers.csv"
            path.write_text('name,website,careers_url,ats_url\n"Fictional, Inc.",https://fictional.example,,https://jobs.lever.co/fictional\n')
            self.assertEqual(self.registry.import_csv(path), 1)
            self.assertEqual(self.registry.import_csv(path), 1)
        self.assertEqual(len(self.registry.rows()), 1)
        self.assertEqual(self.registry.rows()[0]["canonical_name"], "Fictional, Inc.")
        self.assertEqual(self.registry.rows()[0]["status"], "CANDIDATE")

    def test_csv_invalid_later_row_leaves_registry_unchanged(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "employers.csv"
            for text in (
                'name,ats_url\nValid,https://jobs.lever.co/fictional\nUnsafe,http://localhost\n',
                'name,unknown\nFictional,value\n', 'name,ats_url\nMissing\n',
                'name,ats_url\nFictional,https://jobs.lever.co/fictional,extra\n',
                'name,name\nA,B\n', 'website\nhttps://fictional.example\n',
            ):
                with self.subTest(text=text), self.assertRaises(SourceError):
                    path.write_text(text)
                    self.registry.import_csv(path)
        self.assertEqual(self.registry.rows(), [])

    def test_unsupported_pages_are_recorded_not_invented_boards(self):
        self.registry.add("Fictional Workday", careers_url="https://fictional.myworkdayjobs.com/en-US/careers")
        row = self.registry.rows()[0]
        self.assertEqual(row["provider"], "workday")
        self.assertIsNone(row["board_identifier"])
        self.registry.failure(row["id"], "No compliant adapter", unsupported=True)
        self.assertEqual(self.registry.rows()[0]["status"], "UNSUPPORTED")


class UrlTests(unittest.TestCase):
    def test_exact_supported_patterns_and_regions(self):
        for url, provider, token, region in (
            ("https://boards.greenhouse.io/fictional/jobs/123", "greenhouse", "fictional", "global"),
            ("https://job-boards.greenhouse.io/fictional", "greenhouse", "fictional", "global"),
            ("https://boards.greenhouse.io/embed/job_board?for=fictional", "greenhouse", "fictional", "global"),
            ("https://boards-api.greenhouse.io/v1/boards/fictional/jobs", "greenhouse", "fictional", "global"),
            ("https://jobs.eu.lever.co/fictional/123", "lever", "fictional", "eu"),
            ("https://jobs.ashbyhq.com/fictional/123", "ashby", "fictional", "global"),
        ):
            with self.subTest(url=url):
                found = detect(url)
                self.assertEqual((found.provider, found.token, found.region), (provider, token, region))

    def test_similar_domains_and_unsafe_tokens_not_detected(self):
        for url in ("https://jobs.lever.co.evil.example/fictional", "https://fakejobs.ashbyhq.com/fictional",
                    "https://jobs.lever.co/fictional%2Fsecret", "https://jobs.ashbyhq.com/", "https://example.com/jobs"):
            with self.subTest(url=url):
                self.assertIsNone(detect(url))

    def test_unsafe_urls_are_rejected_without_network(self):
        for url in ("http://example.com", "https://localhost/", "https://127.0.0.1/", "https://[::1]/",
                    "https://user:pass@example.com/", "https://example.com:444/", "https://evil.local/",
                    "https://example.com/\nX", "https://example.com\\@127.0.0.1", "file:///etc/passwd"):
            with self.subTest(url=url), self.assertRaises(SourceError):
                public_url(url)
        with self.assertRaises(SourceError):
            board_url("ashby", "../invalid")


class MigrationTests(unittest.TestCase):
    def test_legacy_payloads_preserved_and_migration_idempotent(self):
        from tests.test_discovery import job
        with TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite3"
            with patch("getmeajob.storage.migrate"):
                with JobStore(path) as legacy:
                    identity = legacy.upsert(job(), "2026-01-01T00:00:00+00:00")
                    payload = legacy.connection.execute("SELECT payload FROM observations").fetchone()[0]
            for _ in range(2):
                with JobStore(path) as migrated:
                    self.assertEqual(migrated.count(), 1)
                    self.assertEqual(migrated.job_id(job()), identity)
                    self.assertEqual(migrated.connection.execute("SELECT payload FROM observations").fetchone()[0], payload)
                    self.assertEqual(migrated.connection.execute("PRAGMA user_version").fetchone()[0], 1)
                    self.assertEqual(migrated.connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_future_schema_refused(self):
        db = sqlite3.connect(":memory:")
        try:
            db.execute("PRAGMA user_version=999")
            with self.assertRaises(sqlite3.DatabaseError):
                migrate(db)
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 999)
        finally:
            db.close()

    def test_failed_migration_rolls_back_new_tables(self):
        db = sqlite3.connect(":memory:")
        try:
            db.execute("CREATE TABLE career_sources(id INTEGER)")
            with self.assertRaises(sqlite3.DatabaseError):
                migrate(db)
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='employers'").fetchone())
        finally:
            db.close()
