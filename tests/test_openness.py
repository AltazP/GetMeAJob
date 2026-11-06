"""Ported closed-posting checker and its integration. Synthetic responses only; no network."""
import io
import json
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.message import Message
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from getmeajob import jobfilters, jobsquery, notify, openness
from getmeajob.models import utc_now
from getmeajob.resume.intelligence import match, stored_weights
from getmeajob.sources import SourceError
from getmeajob.sources.http import BudgetExceeded, JsonClient, Probe
from getmeajob.storage import JobStore
from tests.test_discovery import job
from tests.test_job_filters import FilterBase, NOW, q


def P(status=200, body=b"", location=None):
    return Probe(status, location, body if isinstance(body, bytes) else body.encode())


class FakeClient:
    """Answers probe() from a url -> Probe map; unknown URLs are 404; robots.txt defaults to 'no file'."""
    def __init__(self, answers=None, robots=None):
        self.answers = answers or {}; self.robots = robots or {}; self.calls = []

    def probe(self, url, *, page_hosts, max_bytes=1024 * 1024, accept=""):
        self.calls.append(url)
        if url.endswith("/robots.txt"):
            return self.robots.get(url, P(404))
        outcome = self.answers.get(url, P(404))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def check(url, answers=None, robots=None):
    client = FakeClient(answers, robots)
    return openness.Checker(client).check(url), client


class ProviderTests(unittest.TestCase):
    def test_workday_url_to_api_and_results(self):
        url = "https://acme.wd3.myworkdayjobs.com/en-US/Careers/job/Toronto-ON/Intern_R123"
        api = "https://acme.wd3.myworkdayjobs.com/wday/cxs/acme/Careers/job/Toronto-ON/Intern_R123"
        self.assertEqual(openness.workday_api_url(url), api)
        self.assertEqual(openness.workday_api_url("https://wd3.myworkdaysite.com/recruiting/acme/Site/job/Toronto/Intern_R1"),
                         "https://wd3.myworkdaysite.com/wday/cxs/acme/Site/job/Toronto/Intern_R1")
        self.assertIsNone(openness.workday_api_url("https://acme.wd3.myworkdayjobs.com/Careers"))
        self.assertEqual(check(url, {api: P(200, json.dumps({"jobPostingInfo": {"id": "1", "title": "Intern"}}))})[0].status, "OPEN")
        self.assertEqual(check(url, {api: P(404)})[0].status, "CLOSED")
        self.assertEqual(check(url, {api: P(403, json.dumps({"errorCode": "S22", "message": "Permission denied"}))})[0].status, "CLOSED")
        self.assertEqual(check(url, {api: P(500), url: P(200, "<html>app shell</html>")})[0].status, "UNKNOWN")      # a Workday HTML 200 proves nothing
        self.assertEqual(check(url, {api: P(200, "{}"), url: P(200, "<html>shell</html>")})[0].status, "UNKNOWN")

    def test_greenhouse_and_smartrecruiters_apis(self):
        gh = "https://job-boards.greenhouse.io/acme/jobs/123"; api = "https://boards-api.greenhouse.io/v1/boards/acme/jobs/123"
        self.assertEqual(openness.greenhouse_api_url(gh), api)
        self.assertEqual(check(gh, {api: P(200, json.dumps({"id": 123, "title": "Intern"}))})[0].status, "OPEN")
        self.assertEqual(check(gh, {api: P(404)})[0].status, "CLOSED")
        sr = "https://jobs.smartrecruiters.com/Acme/12345-intern"; sr_api = "https://api.smartrecruiters.com/v1/companies/Acme/postings/12345-intern"
        self.assertEqual(openness.smartrecruiters_api_url(sr), sr_api)
        expired = "<html><button disabled>Sorry, this job has expired</button></html>"
        self.assertEqual(check(sr, {sr_api: P(200, json.dumps({"uuid": "1", "name": "Intern"})), sr: P(200, expired)})[0].status, "CLOSED")   # API alone is not trusted
        self.assertEqual(check(sr, {sr_api: P(200, json.dumps({"uuid": "1", "name": "Intern"})), sr: P(200, "<html>ok</html>")})[0].status, "OPEN")

    def test_ashby_phenom_oracle(self):
        url = "https://jobs.ashbyhq.com/acme/9df6daf8-fd6e-49ed-8b66-7047b4bdf0e4/application"
        page = lambda posting: f"<script>window.__appData = {json.dumps({'maintenanceMode': False, 'posting': posting})};</script>"
        self.assertEqual(check(url, {url: P(200, page(None))})[0].status, "CLOSED")
        self.assertEqual(check(url, {url: P(200, page({"id": "9DF6DAF8-FD6E-49ED-8B66-7047B4BDF0E4", "title": "Intern"}))})[0].status, "OPEN")
        self.assertEqual(check(url, {url: P(200, "<html>no data</html>")})[0].status, "UNKNOWN")
        ph = "https://careers.acme.com/global/en/job/R-100/Intern"
        data = {"jobDetail": {"status": 200, "data": {"job": {"jobId": "R-100", "title": "Intern", "postingStatus": "Open"}}}}
        self.assertEqual(check(ph, {ph: P(200, f"<script>phApp.ddo = {json.dumps(data)};</script>")})[0].status, "OPEN")
        oracle = "https://acme.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/777"
        api = "https://acme.fa.us2.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?onlyData=true&finder=ById%3BId%3D%22777%22%2CsiteNumber%3DCX"
        self.assertEqual(check(oracle, {api: P(200, json.dumps({"items": [], "count": 0, "hasMore": False}))})[0].status, "CLOSED")
        self.assertEqual(check(oracle, {api: P(200, json.dumps({"items": [{"Id": "777", "Title": "Intern"}]}))})[0].status, "OPEN")

    def test_generic_pages(self):
        url = "https://careers.example.com/jobs/1"
        self.assertEqual(check(url, {url: P(404)})[0].status, "CLOSED"); self.assertEqual(check(url, {url: P(410)})[0].status, "CLOSED")
        self.assertEqual(check(url, {url: P(200, "<html><body>Sorry! This job is no longer available.</body></html>")})[0].status, "CLOSED")
        self.assertEqual(check(url, {url: P(200, "<html><script>var x='this job is closed'</script><p>Apply now</p></html>")})[0].status, "OPEN")   # script text ignored
        self.assertEqual(check(url, {url: P(200, "<html>Great role. We closed our last round.</html>")})[0].status, "OPEN")
        for status in (401, 403, 408, 429, 500, 503): self.assertEqual(check(url, {url: P(status)})[0].status, "UNKNOWN", status)

    def test_redirects_only_count_within_the_same_site(self):
        job_url = "https://acme.wd1.myworkdayjobs.com/Careers/job/Toronto/Intern_R1"
        search = "https://acme.wd1.myworkdayjobs.com/Careers/search"
        api = "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/Careers/job/Toronto/Intern_R1"
        result, _ = check(job_url, {api: P(500), job_url: P(302, location="/Careers/search"), search: P(200, "<html>Search</html>")})
        self.assertEqual(result.status, "CLOSED")
        gh = "https://boards.greenhouse.io/acme/jobs/99"
        result, client = check(gh, {"https://boards-api.greenhouse.io/v1/boards/acme/jobs/99": P(500), gh: P(302, location="https://evil.example/x")})
        self.assertEqual(result.status, "UNKNOWN"); self.assertNotIn("https://evil.example/x", client.calls)
        loop = "https://careers.example.com/a"
        self.assertEqual(check(loop, {loop: P(302, location="/a")})[0].status, "UNKNOWN")

    def test_robots_txt_is_honoured_and_failures_are_unknown(self):
        url = "https://careers.example.com/jobs/1"
        robots = {"https://careers.example.com/robots.txt": P(200, "User-agent: *\nDisallow: /jobs/")}
        result, client = check(url, {url: P(404)}, robots)
        self.assertEqual(result.status, "UNKNOWN"); self.assertNotIn(url, client.calls)                  # never fetched
        self.assertEqual(check(url, {url: P(404)}, {"https://careers.example.com/robots.txt": P(500)})[0].status, "UNKNOWN")
        allowed = {"https://careers.example.com/robots.txt": P(200, "User-agent: *\nDisallow: /private/")}
        self.assertEqual(check(url, {url: P(404)}, allowed)[0].status, "CLOSED")
        self.assertEqual(check(url, {url: SourceError("Host paused")})[0].status, "UNKNOWN")

    def test_a_spent_budget_propagates_instead_of_becoming_an_answer(self):
        with self.assertRaises(BudgetExceeded): check("https://careers.example.com/jobs/1", {"https://careers.example.com/jobs/1": BudgetExceeded("cap")})


class ClientProbeTests(unittest.TestCase):
    def client(self, *outcomes, **kw):
        from tests.test_discovery import FakeOpener
        self.opener = FakeOpener(*outcomes); self.sleeps = []
        return JsonClient(opener=self.opener, sleep=self.sleeps.append, clock=lambda: 100, **kw)

    @staticmethod
    def error(code, location=None, retry=None, body=b"x"):
        headers = Message()
        if location: headers["Location"] = location
        if retry: headers["Retry-After"] = retry
        return HTTPError("https://x.example/a", code, "synthetic", headers, io.BytesIO(body))

    def test_status_redirect_and_body_are_reported_not_raised_or_followed(self):
        client = self.client(self.error(404), self.error(302, location="/search"), b"<html>ok</html>")
        self.assertEqual(client.probe("https://x.example/a", page_hosts=("x.example",)).status, 404)
        moved = client.probe("https://x.example/b", page_hosts=("x.example",)); self.assertEqual((moved.status, moved.location), (302, "/search"))
        self.assertEqual(client.probe("https://x.example/c", page_hosts=("x.example",)).body, b"<html>ok</html>")
        self.assertEqual(len(self.opener.requests), 3)

    def test_access_denials_pause_the_host_and_hosts_must_be_permitted(self):
        client = self.client(self.error(429, retry="60"))
        self.assertEqual(client.probe("https://x.example/a", page_hosts=("x.example",)).status, 429)
        with self.assertRaises(SourceError): client.probe("https://x.example/b", page_hosts=("x.example",))       # paused
        with self.assertRaises(SourceError): self.client().probe("https://x.example/a", page_hosts=())            # host not permitted
        with self.assertRaises(SourceError): self.client().probe("http://x.example/a", page_hosts=("x.example",)) # https only
        with self.assertRaises(SourceError): self.client().probe("https://127.0.0.1/a", page_hosts=("127.0.0.1",))

    def test_size_budget_and_network_errors(self):
        with self.assertRaises(SourceError): self.client(b"x" * 100).probe("https://x.example/a", page_hosts=("x.example",), max_bytes=10)
        with self.assertRaises(SourceError): self.client(URLError("down")).probe("https://x.example/a", page_hosts=("x.example",))
        client = self.client(b"{}", max_requests=1)
        client.probe("https://x.example/a", page_hosts=("x.example",))
        with self.assertRaises(BudgetExceeded): client.probe("https://x.example/b", page_hosts=("x.example",))


class IntegrationTests(FilterBase):
    def setUp(self):
        super().setUp(); self.store = self.app.state.store

    def listed(self, role, url="https://careers.example.com/jobs/1", source="simplify", **kw):
        return self.add(role=role, source=source, board="b", application_url=url, department=None, industry=None, **kw)

    def roles(self, text="canada=0"):
        result = jobsquery.run(self.jobdb, self.store, jobfilters.parse(q(text)), stored_weights(None), match, now=NOW)
        return {r.job.role for r in result.rows}, result

    def test_checks_are_stored_cached_and_closed_jobs_are_hidden_until_requested(self):
        a = self.listed("Alpha Intern", "https://careers.example.com/jobs/a"); b = self.listed("Beta Intern", "https://careers.example.com/jobs/b")
        client = FakeClient({"https://careers.example.com/jobs/a": P(200, "<html>fine</html>"), "https://careers.example.com/jobs/b": P(404)})
        checker = openness.Checker(client)
        self.assertEqual(openness.ensure_check(self.store, checker, a, "https://careers.example.com/jobs/a").status, "OPEN")
        self.assertEqual(openness.ensure_check(self.store, checker, b, "https://careers.example.com/jobs/b").status, "CLOSED")
        calls = len(client.calls)
        openness.ensure_check(self.store, checker, a, "https://careers.example.com/jobs/a"); self.assertEqual(len(client.calls), calls)   # fresh answer reused
        shown, result = self.roles()
        self.assertEqual(shown, {"Alpha Intern"}); self.assertEqual(result.hidden_closed, 1)
        self.assertEqual(self.roles("canada=0&closed=1")[0], {"Alpha Intern", "Beta Intern"})
        self.assertIn("Including closed", " ".join(jobfilters.chips(jobfilters.parse(q("closed=1")))))
        row = [r for r in self.roles("canada=0&closed=1")[1].rows if r.job.role == "Alpha Intern"][0]; self.assertEqual(row.open_check["status"], "OPEN")

    def test_old_closed_answers_expire_and_budget_exhaustion_stores_nothing(self):
        j = self.listed("Old Intern")
        self.store.save_open_check(j, "https://careers.example.com/jobs/1", "CLOSED", "HTTP 404")
        with self.store.connect() as db: db.execute("UPDATE open_checks SET checked=?", ((datetime.now(timezone.utc) - timedelta(days=45)).isoformat(),))
        self.assertEqual(self.roles()[0], {"Old Intern"})                                    # a 45-day-old verdict no longer hides it
        with self.store.connect() as db: db.execute("DELETE FROM open_checks")
        checker = openness.Checker(FakeClient({"https://careers.example.com/jobs/1": BudgetExceeded("cap")}))
        with self.assertRaises(BudgetExceeded): openness.ensure_check(self.store, checker, j, "https://careers.example.com/jobs/1")
        self.assertEqual(self.store.open_checks(), {})
        with self.assertRaises(ValueError): self.store.open_checks(list(range(501)))
        with self.assertRaises(ValueError): self.store.save_open_check(1, "u", "MAYBE", "r")

    def test_jobs_an_employers_board_stopped_listing_are_hidden_as_no_longer_listed(self):
        with JobStore(self.jobdb) as db:
            old = replace(job(), source="greenhouse", board="acme", source_id="1", role="Gone Role", application_url="https://boards.greenhouse.io/acme/jobs/1", industry=None)
            kept = replace(job(), source="greenhouse", board="acme", source_id="2", role="Kept Role", application_url="https://boards.greenhouse.io/acme/jobs/2", industry=None)
            other = replace(job(), source="greenhouse", board="beta", source_id="3", role="Other Board Role", application_url="https://boards.greenhouse.io/beta/jobs/3", industry=None)
            db.upsert(old, now="2026-11-01T00:00:00+00:00"); db.upsert(other, now="2026-11-01T00:00:00+00:00")
            db.upsert(kept, now="2026-11-05T00:00:00+00:00")          # a later refresh of the acme board saw Kept but not Gone
        shown, result = self.roles()
        self.assertEqual(shown, {"Kept Role", "Other Board Role"})    # beta's board was not refreshed later, so nothing is implied about it
        self.assertEqual(result.hidden_closed, 1); self.assertIn("Gone Role", self.roles("canada=0&closed=1")[0])
        self.assertTrue([r for r in self.roles("canada=0&closed=1")[1].rows if r.job.role == "Gone Role"][0].delisted)

    def test_a_closed_verdict_is_void_after_the_link_changes_or_the_board_lists_the_job_again(self):
        j = self.listed("Moved Intern", "https://careers.example.com/jobs/new-link")
        self.store.save_open_check(j, "https://careers.example.com/jobs/old-link", "CLOSED", "HTTP 404")        # verdict was for the old link
        self.assertEqual(self.roles()[0], {"Moved Intern"})
        self.store.save_open_check(j, "https://careers.example.com/jobs/new-link", "CLOSED", "HTTP 404")        # same link: hidden
        self.assertEqual(self.roles()[0], set())
        with JobStore(self.jobdb) as db:
            api = replace(job(), source="greenhouse", board="acme", source_id="9", role="Relisted Role", application_url="https://boards.greenhouse.io/acme/jobs/9", industry=None)
            jid = db.upsert(api, now="2026-11-01T00:00:00+00:00")
        self.store.save_open_check(jid, "https://boards.greenhouse.io/acme/jobs/9", "CLOSED", "HTTP 404")
        with self.store.connect() as db: db.execute("UPDATE open_checks SET checked='2026-11-01T01:00:00+00:00' WHERE job_id=?", (jid,))
        with JobStore(self.jobdb) as db: db.upsert(api, now="2026-11-03T00:00:00+00:00")                        # the board listed it again later
        with patch.object(jobsquery, "recent", return_value=True):
            self.assertIn("Relisted Role", self.roles()[0])

    def test_pause_state_is_shared_across_check_clients_via_the_private_database(self):
        from tests.test_discovery import FakeOpener
        first = JsonClient(opener=FakeOpener(ClientProbeTests.error(429, retry="3600")), sleep=lambda s: None, state=self.store)
        self.assertEqual(first.probe("https://x.example/a", page_hosts=("x.example",)).status, 429)
        opener = FakeOpener(b"{}")
        second = JsonClient(opener=opener, sleep=lambda s: None, state=self.store)                            # a new client, e.g. the next scheduled run
        with self.assertRaises(SourceError): second.probe("https://x.example/b", page_hosts=("x.example",))
        self.assertEqual(opener.requests, [])                                                                # it never contacted the paused host
        self.assertTrue(self.store.paused("x.example")); self.assertFalse(self.store.paused("y.example"))

    def test_email_digests_drop_closed_list_postings_and_confirm_open_ones(self):
        self.verified_resume(("skills", "Python SQL"), ("employment", "Python developer"))
        open_id = self.listed("Python Open Intern", "https://careers.example.com/jobs/open", description="Python SQL")
        closed_id = self.listed("Python Closed Intern", "https://careers.example.com/jobs/closed", description="Python SQL")
        api_id = self.add(role="Python API Role", description="Python SQL", department=None, industry=None, source="greenhouse")
        self.store.add_preset("Python", jobfilters.dump_preferences(jobfilters.parse(q("role=python&canada=0"))))
        digests = notify.build_digests(self.jobdb, self.store)
        client = FakeClient({"https://careers.example.com/jobs/open": P(200, "<html>ok</html>"), "https://careers.example.com/jobs/closed": P(404)})
        notify.screen_closed(self.store, digests, openness.Checker(client))
        names = {i["row"].job.role for i in digests[0].items}
        self.assertEqual(names, {"Python Open Intern", "Python API Role"}); self.assertEqual(digests[0].dropped_closed, 1)
        self.assertFalse(any("api" in url for url in client.calls))                      # employer-board jobs are not re-checked
        text = notify.render(digests[0])[1]
        self.assertIn("Confirmed open when checked", text); self.assertNotIn("Closed Intern", text)

    def test_digest_screening_keeps_items_when_the_budget_runs_out_and_builds_the_checker_lazily(self):
        self.verified_resume(("skills", "Python SQL"))
        self.listed("Python Budget Intern", description="Python SQL")
        self.store.add_preset("P", jobfilters.dump_preferences(jobfilters.parse(q("role=python&canada=0"))))
        digests = notify.build_digests(self.jobdb, self.store)
        notify.screen_closed(self.store, digests, openness.Checker(FakeClient({"https://careers.example.com/jobs/1": BudgetExceeded("cap")})))
        self.assertEqual(len(digests[0].items), 1)
        built = []
        notify.screen_closed(self.store, [], lambda: built.append(1))                    # nothing to check: the checker is never built
        self.assertEqual(built, [])

    def test_leads_check_command_checks_the_top_matches_only(self):
        from getmeajob import leads_cli
        self.verified_resume(("skills", "Python SQL"))
        self.listed("Python CLI Intern", "https://careers.example.com/jobs/cli", description="Python SQL")
        self.add(role="Python Board Role", description="Python SQL", department=None, industry=None, source="greenhouse")
        self.store.add_preset("P", jobfilters.dump_preferences(jobfilters.parse(q("role=python&canada=0"))))
        client = FakeClient({"https://careers.example.com/jobs/cli": P(404)}); messages = []
        counts = leads_cli.check_top_matches(self.store, MagicMock(database=self.jobdb), 10, checker=openness.Checker(client), log=messages.append)
        self.assertEqual(counts, {"OPEN": 0, "CLOSED": 1, "UNKNOWN": 0}); self.assertTrue(any("CLOSED" in m for m in messages))

    def test_dashboard_shows_status_and_the_check_button_works(self):
        self.login()
        self.listed("Visible Intern", "https://careers.example.com/jobs/visible")
        page = self.client.get("/jobs?applied=1&canada=0")
        self.assertIn("Not checked if open", page.text); self.assertIn("Check if still open", page.text)
        job_id = int(__import__("re").search(r"/jobs/(\d+)/check", page.text)[1])
        token = self.csrf("/jobs")
        self.assertEqual(self.client.post(f"/jobs/{job_id}/check").status_code, 403)                  # needs CSRF
        with patch.object(openness.Checker, "check", return_value=openness.Check("OPEN", "No closure signal")):
            done = self.client.post(f"/jobs/{job_id}/check", data={"csrf": token}, headers={"Referer": "http://127.0.0.1:8000/jobs?applied=1&canada=0"}, follow_redirects=False)
        self.assertEqual(done.status_code, 303); self.assertTrue(done.headers["location"].startswith("http://127.0.0.1:8000/jobs"))
        self.assertIn("Open when checked", self.client.get("/jobs?applied=1&canada=0").text)
        evil = self.client.post(f"/jobs/{job_id}/check", data={"csrf": token}, headers={"Referer": "https://evil.example/jobs"}, follow_redirects=False)
        self.assertEqual(evil.headers["location"], "/jobs")                                           # never redirects off-site
        with patch.object(openness.Checker, "check", return_value=openness.Check("CLOSED", "HTTP 404")):
            self.client.post(f"/jobs/{job_id}/check", data={"csrf": token})
        gone = self.client.get("/jobs?applied=1&canada=0"); self.assertNotIn("Visible Intern", gone.text); self.assertIn("hidden because they look closed", gone.text)
        self.assertIn("Looks closed", self.client.get("/jobs?applied=1&canada=0&closed=1").text)


if __name__ == "__main__":
    unittest.main()
