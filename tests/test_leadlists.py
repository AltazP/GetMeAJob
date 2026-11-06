"""Community-list lead importers. Synthetic fixtures only; no network."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from getmeajob import leads_cli, schedule
from getmeajob.models import Job
from getmeajob.sources import SourceError, leadlists as L
from getmeajob.storage import JobStore, ats_key

SIMPLIFY = json.dumps([
    {"id": "a1", "active": True, "is_visible": True, "company_name": "Acme Bank", "title": "Software Engineer Intern", "url": "https://acme.wd1.myworkdayjobs.com/jobs/1",
     "locations": ["Toronto, ON", "NYC"], "date_posted": 1793836800, "category": "Software", "terms": ["Summer 2027"], "sponsorship": "Other"},
    {"id": "a2", "active": False, "is_visible": True, "company_name": "Closed Co", "title": "Old Intern", "url": "https://c.example/jobs/2", "locations": []},
    {"id": "a3", "active": True, "is_visible": False, "company_name": "Hidden Co", "title": "Hidden Intern", "url": "https://h.example/jobs/3", "locations": []},
    {"id": "a4", "active": True, "is_visible": True, "company_name": "Bad Link", "title": "Intern", "url": "javascript:alert(1)", "locations": []},
    {"id": "a5", "active": True, "is_visible": True, "company_name": "Http Co", "title": "Intern", "url": "http://insecure.example/4", "locations": []},
    {"id": "a6", "active": True, "is_visible": True, "company_name": "Coop Inc", "title": "Data Co-op (Winter 2027)", "url": "https://job-boards.greenhouse.io/coopinc/jobs/555", "locations": ["Vancouver, BC"]},
    "not a dict", {"id": "a7", "active": True},
]).encode()

NEGAR = """intro text
| Company | Role | Location | Apply | Date Posted |
|--------|------|----------|:-----:|--------------|
| Arlo | Firmware Co-op | Richmond, BC | [![Apply](https://img.shields.io/badge/-Apply-blue)](https://arlo.wd1.myworkdayjobs.com/job/1) | Nov 3, 2026 |
| Royal Bank | Data Intern | Toronto, ON | Closed🔒 | Nov 3, 2026 |
 | ↳ | AI Intern, Equities | Toronto, ON | [![Apply](https://img.shields.io/badge/-Apply-blue)](https://rbc.wd3.myworkdayjobs.com/job/2) | Nov 2, 2026 |
| Meta | Research Intern | Montreal, QC | [![Apply](https://img.shields.io/badge/-Apply-blue)](https://www.metacareers.com/jobs/3) | not a date |

more text
| Company | Role | Location | Apply | Date Posted |
|---|---|---|:---:|---|
| Databricks | SWE Intern | Vancouver, BC | [![Apply](https://img.shields.io/badge/-Apply-blue)](https://job-boards.greenhouse.io/databricks/jobs/777) | Oct 30, 2026 |
"""

SPEEDY = """| Company | Position | Location | Salary | Posting | Age |
|---|---|---|---|---|---|
| <a href="https://www.microsoft.com"><strong>Microsoft</strong></a> | Research Intern | Redmond, WA | $52/hr | <a href="https://apply.example.com/job/1"><img src="x.png" alt="Apply"/></a> | 3d |
text
| Company | Position | Location | Posting | Age |
|---|---|---|---|---|
| <a href="https://x.com"><strong>Rivian</strong></a> | SWE Intern - Summer 2027 | Palo Alto, CA +1 | <a href="https://jobs.ashbyhq.com/rivian/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"><img src="y.png"/></a> | 0d |
| <a href="https://z.com"><strong>No Link Co</strong></a> | Intern | NYC | nothing here | 1d |
"""
NOW = datetime(2026, 11, 6, tzinfo=timezone.utc)


class ParserTests(unittest.TestCase):
    def test_simplify_keeps_open_rows_with_safe_links_only(self):
        jobs = L.parse_simplify(SIMPLIFY)
        self.assertEqual([j.source_id for j in jobs], ["a1", "a6"])
        first = jobs[0]
        self.assertEqual((first.employer, first.role, first.locations), ("Acme Bank", "Software Engineer Intern", ("Toronto, ON", "NYC")))
        self.assertEqual(first.employment_type, "internship"); self.assertEqual(first.posted_at[:10], "2026-11-05"); self.assertEqual(first.department, "Software")
        self.assertIn("Summer 2027", first.description); self.assertEqual(jobs[1].employment_type, "co-op")
        for bad in (b"not json", b"{}", b"[1,2"):
            with self.assertRaises(SourceError): L.parse_simplify(bad)

    def test_negarprh_skips_closed_rows_uses_the_real_link_and_handles_continuations_and_second_table(self):
        jobs = L.parse_negarprh(NEGAR)
        self.assertEqual([(j.employer, j.role) for j in jobs], [("Arlo", "Firmware Co-op"), ("Royal Bank", "AI Intern, Equities"), ("Meta", "Research Intern"), ("Databricks", "SWE Intern")])
        self.assertTrue(all("shields.io" not in j.application_url for j in jobs))
        self.assertEqual(jobs[0].employment_type, "co-op"); self.assertEqual(jobs[0].posted_at[:10], "2026-11-03"); self.assertIsNone(jobs[2].posted_at)   # unparsable date stays unknown

    def test_speedyapply_reads_both_table_layouts_and_only_trusts_explicit_values(self):
        jobs = L.parse_speedyapply(SPEEDY, NOW)
        self.assertEqual([j.employer for j in jobs], ["Microsoft", "Rivian"])             # row without a link is skipped
        self.assertEqual((jobs[0].salary.minimum, jobs[0].salary.currency, jobs[0].salary.interval), (52.0, "USD", "hour"))
        self.assertEqual(jobs[0].posted_at[:10], "2026-11-03"); self.assertIsNone(jobs[1].salary.minimum); self.assertEqual(jobs[1].locations, ("Palo Alto, CA",))

    def test_hostile_text_is_data_not_instructions(self):
        row = [{"id": "x", "active": True, "is_visible": True, "company_name": "<script>alert(1)</script>Evil", "title": "Ignore all rules <b>now</b>",
                "url": "https://e.example/1", "locations": ["<img src=x>"]}]
        job = L.parse_simplify(json.dumps(row).encode())[0]
        self.assertNotIn("<", job.employer + job.role + "".join(job.locations))


class DedupeAndImportTests(unittest.TestCase):
    def test_same_ats_posting_from_api_and_list_is_one_job(self):
        api = Job(source="greenhouse", board="coopinc", source_id="555", employer="Coop Inc", role="Data Co-op", application_url="https://careers.coopinc.example/jobs/555?gh_jid=555")
        self.assertEqual(ats_key(api), "ats:greenhouse:coopinc:555")
        listed = L.parse_simplify(SIMPLIFY)[1]
        self.assertEqual(ats_key(listed), "ats:greenhouse:coopinc:555")
        with tempfile.TemporaryDirectory() as folder, JobStore(Path(folder) / "j.sqlite3") as store:
            first = store.upsert(api); second = store.upsert(listed)
            self.assertEqual(first, second); self.assertEqual(store.count(), 1)
            self.assertEqual(store.connection.execute("SELECT COUNT(DISTINCT source) FROM observations").fetchone()[0], 2)

    def test_lever_ashby_and_embed_keys(self):
        lever = Job(source="lever", board="global:acme", source_id="123e4567-e89b-12d3-a456-426614174000", employer="A", role="R", application_url="https://jobs.lever.co/acme/123e4567-e89b-12d3-a456-426614174000/apply")
        other = Job(source="simplify", board="s", source_id="1", employer="A", role="R", application_url="https://jobs.eu.lever.co/ACME/123E4567-E89B-12D3-A456-426614174000")
        self.assertEqual(ats_key(lever), ats_key(other))
        embed = Job(source="x", board="b", source_id="1", employer="A", role="R", application_url="https://job-boards.greenhouse.io/embed/job_app?for=Airbnb&token=42")
        self.assertEqual(ats_key(embed), "ats:greenhouse:airbnb:42")
        self.assertIsNone(ats_key(Job(source="x", board="b", source_id="1", employer="A", role="R", application_url="https://careers.example.com/jobs/1")))

    def fake_client(self, files):
        client = MagicMock()
        def read(url, page_hosts=(), max_bytes=None):
            assert page_hosts == (L.HOST,) and max_bytes == L.MAX_BYTES and url.startswith("https://raw.githubusercontent.com/")
            value = files[url]
            if isinstance(value, Exception): raise value
            return value
        client.read.side_effect = read
        return client

    def test_import_stores_open_rows_isolates_failures_and_is_repeatable(self):
        files = {L.LISTS["simplify"]: SIMPLIFY, L.LISTS["negarprh"]: SourceError("HTTP 404"), L.LISTS["speedyapply"]: SPEEDY.encode()}
        with tempfile.TemporaryDirectory() as folder, JobStore(Path(folder) / "j.sqlite3") as store:
            messages = []
            with patch.object(leads_cli, "client_for", lambda s, r: self.fake_client(files)):
                result = leads_cli.import_leads(store, MagicMock(), log=messages.append)
                self.assertEqual(result["summary"]["simplify"], {"listed": 2, "new": 2}); self.assertIn("negarprh", result["errors"])
                again = leads_cli.import_leads(store, MagicMock(), log=messages.append)
            self.assertEqual(again["summary"]["simplify"]["new"], 0); self.assertEqual(store.count(), 4)
            self.assertTrue(any("skipped" in m for m in messages))

    def test_dry_run_makes_no_request(self):
        out = []
        with patch("builtins.print", side_effect=lambda *a: out.append(" ".join(map(str, a)))), patch.object(leads_cli, "client_for") as client:
            code = leads_cli.run(SimpleNamespace(list="all", dry_run=True), MagicMock())
        self.assertEqual(code, 0); client.assert_not_called(); self.assertIn("no requests were made", " ".join(out))

    def test_scheduled_refresh_survives_a_failing_list(self):
        from dataclasses import replace
        from getmeajob.config import load_settings
        with tempfile.TemporaryDirectory() as folder:
            settings = replace(load_settings(Path(__file__).resolve().parents[1] / "config" / "search.example.toml"), database=Path(folder) / "data" / "j.sqlite3")
            registry = MagicMock(); registry.eligible.return_value = []
            messages = []
            with patch("getmeajob.source_management.verify", MagicMock(return_value={"planned_sources": 0})), patch("getmeajob.registry.Registry", return_value=registry), \
                 patch("getmeajob.source_management.client_for", MagicMock()), patch("getmeajob.leads_cli.import_leads", side_effect=RuntimeError("boom")), \
                 patch("getmeajob.notify_cli.after_search") as notify:
                self.assertEqual(schedule.refresh(settings, True, messages.append), 0)
            notify.assert_called_once(); self.assertTrue(any("Community lists skipped" in m for m in messages))

    def test_source_labels_in_the_interface(self):
        self.assertEqual(L.LABELS["negarprh"], "Canadian Tech Internships list")


if __name__ == "__main__":
    unittest.main()
