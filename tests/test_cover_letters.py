"""US region filter, uploaded-letter improver and the cover-letter pages. Synthetic data only."""
import io
import re
import unittest

from starlette.datastructures import QueryParams

from getmeajob import jobfilters, jobsquery
from getmeajob.regions import in_regions, location_in_us
from getmeajob.resume.documents import export
from getmeajob.resume.intelligence import adapt_letter, match, stored_weights
from tests.test_discovery import job
from tests.test_job_filters import FilterBase, NOW, q
from tests.test_letter_and_links import FACTS

TEMPLATE = """To whom it may concern,

I am writing to apply for the [Position] role at [Company]. I believe I would be a great fit because I am a hard-working team player.

During my internship I built small tools and learned a lot about databases.

Thank you for your time.

Best regards,
Old Name"""


class RegionTests(unittest.TestCase):
    def test_us_rules(self):
        for yes in ("San Francisco", "San Francisco, CA", "Remote - USA", "Remote US", "New York, NY", "United States", "US-CA-Menlo Park",
                    "Washington, D.C.", "Los Angeles, USA", "Remote, United States; Remote, Canada", "San Francisco, CA | Toronto, ON", "Austin, TX"):
            self.assertTrue(location_in_us(yes), yes)
        for no in ("London", "Dublin", "Singapore", "Tokyo, Japan", "Toronto, ON, CA", "Remote Canada", "Hybrid", "Remote", "Georgia", "Please contact us", "Vancouver, BC"):
            self.assertFalse(location_in_us(no), no)

    def test_regions_combine_and_unknown_is_none(self):
        self.assertTrue(in_regions(("US / Canada",), ("canada",))); self.assertTrue(in_regions(("US / Canada",), ("us",)))
        self.assertFalse(in_regions(("Paris, France",), ("canada", "us"))); self.assertIsNone(in_regions((), ("us",)))


class RegionFilterTests(FilterBase):
    def setUp(self):
        super().setUp()
        for role, where in (("Canada Job", ("Toronto, Canada",)), ("US Job", ("New York, NY",)), ("Both Job", ("US / Canada",)),
                            ("Europe Job", ("Paris, France",)), ("Unknown Job", ())):
            self.add(role=role, locations=where, department=None, industry=None)

    def roles(self, text):
        filters = jobfilters.parse(q(text))
        return {r.job.role for r in jobsquery.run(self.jobdb, self.app.state.store, filters, stored_weights(None), match, now=NOW).rows}

    def test_each_region_choice(self):
        self.assertEqual(self.roles(""), {"Canada Job", "Both Job", "Unknown Job"})                      # default stays Canada
        self.assertEqual(self.roles("region=canada&unknown=0"), {"Canada Job", "Both Job"})
        self.assertEqual(self.roles("region=us&unknown=0"), {"US Job", "Both Job"})
        self.assertEqual(self.roles("region=both&unknown=0"), {"Canada Job", "US Job", "Both Job"})
        self.assertEqual(self.roles("region=any"), {"Canada Job", "US Job", "Both Job", "Europe Job", "Unknown Job"})

    def test_legacy_canada_switch_still_works_and_bad_region_is_rejected(self):
        self.assertEqual(jobfilters.parse(q("canada=0")).region, "any"); self.assertEqual(jobfilters.parse(q("canada=1")).region, "canada")
        with self.assertRaises(jobfilters.FilterError): jobfilters.parse(q("region=mars"))

    def test_chips_query_and_saved_preferences_round_trip(self):
        f = jobfilters.parse(q("region=both&field=Finance"))
        self.assertIn("Canada and United States", jobfilters.chips(f)); self.assertEqual(jobfilters.load_preferences(jobfilters.dump_preferences(f)), f)
        self.assertNotIn("Canada only", jobfilters.chips(jobfilters.parse(q("region=us"))))
        self.assertIn("region=us", jobfilters.query_string(jobfilters.parse(q("region=us"))))

    def test_page_offers_the_region_choice(self):
        self.login(); page = self.client.get("/jobs?applied=1&region=us")
        self.assertIn("United States only", page.text); self.assertIn('<option value="both"', page.text)
        self.assertIn("US Job", page.text); self.assertNotIn("Europe Job", page.text)


class ImproverTests(unittest.TestCase):
    def run_adapt(self, template=TEMPLATE, facts=FACTS, name="Jane Doe", **posting):
        defaults = dict(role="Backend Developer", employer="Acme Corp", description="Python, SQL and Git. Rust is a plus.", industry=None)
        return adapt_letter(template, job(**{**defaults, **posting}), facts, None, name)

    def test_keeps_the_users_words_and_applies_safe_tweaks(self):
        text, ids, mode, changes, suggestions = self.run_adapt()
        self.assertTrue(text.startswith("Dear Acme Corp Hiring Team,")); self.assertTrue(text.endswith("Best regards,\nJane Doe"))
        self.assertIn("for the Backend Developer role at Acme Corp", text)           # placeholders filled
        self.assertNotIn("I believe I would", text); self.assertIn("I would be a great fit", text)   # hedge removed
        self.assertIn("During my internship I built small tools", text)              # the user's own sentences remain
        self.assertNotIn("Old Name", text); self.assertNotIn("[Company]", text)
        joined = " ".join(changes)
        for expected in ("placeholders", "hedging", "greeting", "Signed off", "Inserted a paragraph"): self.assertIn(expected, joined)

    def test_tailored_paragraph_uses_only_verified_facts_and_never_unevidenced_skills(self):
        text, ids, *_ = self.run_adapt()
        self.assertIn("focus on Python, SQL and Git", text); self.assertIn("Software Developer Intern at Fictional Labs", text)
        for forbidden in ("Rust", "Pending Corp", "invented claim", "jane@example.com"): self.assertNotIn(forbidden, text)
        self.assertTrue(set(ids) <= {1, 2, 3, 4})

    def test_suggestions_flag_generic_phrases_without_rewriting_them(self):
        text, _, _, _, suggestions = self.run_adapt()
        joined = " ".join(suggestions)
        self.assertIn("team player", joined); self.assertIn("hard-working", joined); self.assertIn("great fit", joined)
        self.assertIn("different company", joined); self.assertIn("hard-working team player", text)
        self.assertNotIn("never names", joined)   # [Company] was filled, so the employer is named in the body         # flagged, not silently edited

    def test_leftover_placeholders_and_missing_name_are_reported(self):
        text, _, _, _, suggestions = self.run_adapt("Dear [Hiring Manager],\n\nI like [Something specific].\n\nSincerely,\n[Name]", name=None)
        self.assertTrue(text.endswith("Sincerely,\n[Your name]")); joined = " ".join(suggestions)
        self.assertIn("[Something specific]", joined); self.assertIn("Applicant details", joined)

    def test_letter_without_greeting_or_signoff_gets_both_and_empty_is_refused(self):
        text, *_ = self.run_adapt("I enjoy building software and want to grow.")
        self.assertTrue(text.startswith("Dear Acme Corp Hiring Team,")); self.assertTrue(text.endswith("Sincerely,\nJane Doe"))
        with self.assertRaises(ValueError): self.run_adapt("   ")
        with self.assertRaises(ValueError): self.run_adapt(facts=[f for f in FACTS if f["status"] != "verified"])

    def test_long_letter_gets_a_trimming_suggestion(self):
        _, _, _, _, suggestions = self.run_adapt("Dear Team,\n\n" + ("I enjoy solving problems carefully. " * 100) + "\n\nSincerely,\nX")
        self.assertIn("trimming", " ".join(suggestions))


class PagesTests(FilterBase):
    def setUp(self):
        super().setUp(); self.login(); self.store = self.app.state.store
        self.resume = self.verified_resume(("skills", "Python SQL Git"), ("employment", "Software developer intern"), label="R")
        self.add(role="Python Developer", employer="Acme", description="Python and SQL", department=None, industry=None)

    def upload(self, **files):
        return self.client.post("/cover-letters", data={"csrf": self.csrf("/cover-letters"), "label": "Mine", "text": files.pop("text", "")}, files=files or None)

    def test_upload_txt_docx_and_paste_then_delete(self):
        self.assertEqual(self.upload(file=("l.txt", TEMPLATE.encode(), "text/plain")).status_code, 200)
        self.assertEqual(self.upload(file=("l.docx", export(TEMPLATE, "docx")[0], "application/octet-stream")).status_code, 200)
        self.assertEqual(self.upload(text=TEMPLATE).status_code, 200)
        letters = self.store.cover_letters(); self.assertEqual(len(letters), 3); self.assertIn("Best regards", letters[0]["text"])
        page = self.client.get("/cover-letters"); self.assertIn("My cover letters", page.text)
        token = self.csrf("/cover-letters")
        self.assertEqual(self.client.post(f"/cover-letters/{letters[0]['id']}/delete", data={"csrf": token}, follow_redirects=False).status_code, 303)
        self.assertEqual(len(self.store.cover_letters()), 2); self.assertEqual(len(self.store.resumes()), 1)

    def test_bad_uploads_csrf_and_limits(self):
        for bad in (dict(file=("l.exe", b"MZ", "application/octet-stream")), dict(file=("l.txt", b"\xff\xfe\x00bad", "text/plain")), dict(text="too short")):
            with self.subTest(bad=list(bad)): self.assertEqual(self.upload(**bad).status_code, 400)
        self.assertEqual(self.client.post("/cover-letters", data={"text": TEMPLATE}).status_code, 403)
        for _ in range(10): self.store.add_cover_letter("x", "x.txt", TEMPLATE)
        self.assertEqual(self.upload(text=TEMPLATE).status_code, 400)

    def test_job_card_offers_both_choices_and_package_uses_the_chosen_one(self):
        self.store.set_setting("applicant", '{"first_name": "Jane", "last_name": "Doe"}')
        letter_id = self.store.add_cover_letter("General", "g.txt", TEMPLATE)
        page = self.client.get("/jobs?applied=1&role=Python"); self.assertIn("Auto-generate a new letter", page.text); self.assertIn("Improve and tailor my letter: General", page.text)
        job_id = re.search(r'name="job_id" value="(\d+)"', page.text)[1]
        token = self.csrf("/jobs")
        made = self.client.post("/packages", data={"csrf": token, "job_id": job_id, "resume_id": str(self.resume), "letter_source": f"upload:{letter_id}"}, follow_redirects=False)
        self.assertEqual(made.status_code, 303)
        package = self.client.get(made.headers["location"])
        self.assertIn("What changed in your letter", package.text); self.assertIn("Suggestions (not applied)", package.text); self.assertIn("Best regards,\nJane Doe", package.text)
        auto = self.client.post("/packages", data={"csrf": token, "job_id": job_id, "resume_id": str(self.resume), "letter_source": "auto"}, follow_redirects=False)
        self.assertNotIn("What changed in your letter", self.client.get(auto.headers["location"]).text)
        bad = self.client.post("/packages", data={"csrf": token, "job_id": job_id, "resume_id": str(self.resume), "letter_source": "evil"})
        self.assertEqual(bad.status_code, 400)

    def test_uploaded_text_is_escaped(self):
        self.store.add_cover_letter("<b>x</b>", "x.txt", "<script>alert(1)</script> " + "word " * 20)
        page = self.client.get("/cover-letters"); self.assertNotIn("<script>alert", page.text); self.assertIn("&lt;script&gt;", page.text)


if __name__ == "__main__":
    unittest.main()
