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


class ReflowTests(unittest.TestCase):
    SHREDDED = "\n\n".join(("Dear Hiring Team, I am building Stratify, a full-stack algorithmic trading platform that supports backtesting. "
        "I enjoy solving hard problems. In my last role I shipped features weekly. I wrote tests for every change. Working with a team taught me to communicate early. "
        "Thank you for your time. Sincerely, Jane Doe").split())

    def test_one_word_per_block_text_becomes_real_paragraphs_without_changing_words(self):
        from getmeajob.resume.reflow import reflow
        fixed = reflow(self.SHREDDED)
        self.assertEqual(fixed.split(), self.SHREDDED.split())              # same words, same order
        blocks = fixed.split("\n\n")
        self.assertTrue(blocks[0].startswith("Dear Hiring Team,")); self.assertLessEqual(len(blocks), 6)
        self.assertGreater(len(blocks[1].split()), 10); self.assertTrue(fixed.endswith("Sincerely,\nJane Doe"))
        self.assertEqual(reflow(fixed), fixed)                                # idempotent

    def test_wrapped_lines_are_joined_but_short_lines_are_kept(self):
        from getmeajob.resume.reflow import reflow
        text = ("I have been building software for several years and enjoy turning messy\nrequirements into reliable tools that people use every day.\n\n"
                "Sincerely,\nJane Doe")
        fixed = reflow(text)
        self.assertIn("messy requirements into reliable tools", fixed); self.assertTrue(fixed.endswith("Sincerely,\nJane Doe"))

    def test_normal_letters_are_unchanged_and_empty_is_empty(self):
        from getmeajob.resume.reflow import reflow
        self.assertEqual(reflow(TEMPLATE), TEMPLATE); self.assertEqual(reflow("  \n\n "), "")

    def test_improver_repairs_a_shredded_upload_and_titles_lose_attached_locations(self):
        text, _, _, _, _ = adapt_letter(self.SHREDDED, job(role="Backend Developer", employer="Acme Corp", description="Python", industry=None), FACTS, None, "Jane Doe")
        self.assertGreater(len(text.split("\n\n")[1].split()), 8)             # no word-per-paragraph output
        self.assertLess(len(text.split("\n\n")), 12)
        facts = [{**FACTS[1], "structure": {"employer": "Fidelity Investments", "title": "Software Developer Toronto, ON", "achievements": ["Built APIs."]}}, FACTS[0]]
        letter_text = adapt_letter(TEMPLATE, job(role="Backend Developer", employer="Acme Corp", description="Python", industry=None), facts, None, "Jane Doe")[0]
        self.assertIn("as a Software Developer at Fidelity Investments", letter_text); self.assertNotIn("Toronto, ON at", letter_text)

    def test_saved_letters_are_repaired_on_upload(self):
        from getmeajob.resume.store import ProfileStore
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as folder:
            store = ProfileStore(pathlib.Path(folder)); identity = store.add_cover_letter("x", "x.pdf", self.SHREDDED)
            self.assertLess(len(store.cover_letter(identity)["text"].split("\n\n")), 8)


class WrittenForAnotherCompanyTests(unittest.TestCase):
    LETTER = ("1 Example Street Springfield, ON, Canada, A1A 1A1 June 7, 2026 Dear Hiring Team, I am a student at Example University, and I am applying for the "
              "Embedded Systems Intern role at Acme Drones. I am drawn to Acme Drones because the work sits at the intersection of software and impact. "
              "The idea that tested code can deliver goods safely is the problem I want to grow into.\n\n"
              "In my last role as a Developer at Sample Corp, I contributed to a platform.\n\n"
              "I would love to discuss how my experience with Python and testing could contribute to Acme Drones's Embedded Systems team. Thank you for your time.\n\n"
              "Sincerely,\nJane Doe")

    def run_it(self, letter=None, name="Jane Doe", employer="Mercury", role="Program Manager - GTM"):
        return adapt_letter(letter or self.LETTER, job(role=role, employer=employer, description="Python SQL", industry=None), FACTS, None, name)

    def test_one_greeting_header_on_its_own_lines_and_todays_date(self):
        from datetime import date
        text, _, _, changes, _ = self.run_it()
        blocks = text.split("\n\n")
        today = date.today(); today_text = f"{today.strftime('%B')} {today.day}, {today.year}"
        self.assertEqual(blocks[0], f"1 Example Street Springfield, ON, Canada, A1A 1A1\n{today_text}")
        self.assertEqual(blocks[1], "Dear Mercury Hiring Team,")
        self.assertEqual(text.count("Dear "), 1); self.assertNotIn("Hiring Team, I am", text); self.assertNotIn("June 7, 2026", text)
        self.assertTrue(text.endswith("Sincerely,\nJane Doe"))
        self.assertTrue(any("Updated the date" in c for c in changes)); self.assertTrue(any("own lines" in c for c in changes))

    def test_a_letter_written_for_another_company_is_retargeted_without_wrong_claims(self):
        text, _, _, changes, suggestions = self.run_it()
        self.assertIn("applying for the Program Manager - GTM role at Mercury.", text)
        self.assertNotIn("Acme Drones", text); self.assertNotIn("Embedded Systems Intern", text)
        self.assertNotIn("drawn to", text); self.assertNotIn("The idea that tested code", text)               # sentences about the old company and its follow-up are gone
        self.assertIn("could contribute to Mercury's team.", text)                                            # the closing is safely retargeted
        self.assertIn("I am a student at Example University", text); self.assertIn("In my last role as a Developer at Sample Corp", text)   # the writer's own facts stay
        joined = " ".join(suggestions)
        self.assertIn("Removed because they were about Acme Drones", joined); self.assertIn("drawn to Acme Drones", joined)   # nothing is silently lost
        self.assertTrue(any("Removed 2 sentence" in c for c in changes))

    def test_two_greetings_collapse_to_one_and_a_same_company_letter_is_left_alone(self):
        text, *_ = self.run_it("Dear Hiring Team,\n\nDear Hiring Manager,\n\nI am applying for the Backend Role role at Mercury. I like databases.\n\nSincerely,\nJane Doe", employer="Mercury")
        self.assertEqual(text.count("Dear "), 1); self.assertIn("I like databases.", text)
        self.assertIn("role at Mercury. I like databases.", text)                                              # same company: the sentence is untouched
        self.assertFalse(any(isinstance(c, str) and c.startswith("Removed") for c in self.run_it("Dear Team,\n\nI am applying for the Dev role at Mercury.\n\nSincerely,\nJane Doe")[3]))
