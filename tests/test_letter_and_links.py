"""Cover letter quality/truthfulness and apply-link behaviour. Synthetic data only."""
from dataclasses import replace
import json
import unittest

from getmeajob.apply_assist import apply_url
from getmeajob.resume.intelligence import letter
from tests.test_discovery import job
from tests.test_job_filters import FilterBase

FACTS = [
    {'id': 1, 'status': 'verified', 'category': 'skills', 'value': 'Python, SQL, Git', 'structure': None},
    {'id': 2, 'status': 'verified', 'category': 'employment', 'value': 'x', 'structure': {
        'employer': 'Fictional Labs', 'title': 'Software Developer Intern', 'achievements': [
            'Built Python services that processed synthetic records using SQLite.', 'Wrote unit tests that caught regressions before release.']}},
    {'id': 3, 'status': 'verified', 'category': 'projects', 'value': 'Budget tracker app in Python and SQL', 'structure': None},
    {'id': 4, 'status': 'verified', 'category': 'education', 'value': 'BSc Computer Science, Synthetic University', 'structure': None},
    {'id': 5, 'status': 'pending', 'category': 'employment', 'value': 'Pending Corp invented claim', 'structure': None},
    {'id': 6, 'status': 'rejected', 'category': 'skills', 'value': 'Rust expert', 'structure': None},
    {'id': 7, 'status': 'verified', 'category': 'personal', 'value': 'jane@example.com 555-0100', 'structure': None},
]


class LetterTests(unittest.TestCase):
    def test_letter_is_tailored_natural_and_signed_with_the_applicants_name(self):
        posting = job(role='Backend Developer', employer='Acme Corp', description='You will use Python, SQL and Git. Rust is a plus.')
        text, ids, _ = letter(posting, FACTS, None, 'Jane Doe')
        self.assertTrue(text.startswith('Dear Acme Corp Hiring Team,'))
        self.assertTrue(text.endswith('Sincerely,\nJane Doe'))
        self.assertIn('Python, SQL and Git', text)                       # the skills the posting names that she has
        self.assertIn('In my role as a Software Developer Intern at Fictional Labs, I built Python services', text)
        self.assertIn('Project work I would highlight: Budget tracker app', text)
        self.assertIn('BSc Computer Science', text)
        self.assertGreaterEqual(text.count('\n\n'), 5)                    # real paragraphs, not a bullet dump
        self.assertNotIn('\n- ', text); self.assertNotIn('Applicant', text)

    def test_only_verified_non_personal_facts_and_no_unevidenced_skills(self):
        text, ids, _ = letter(job(description='Needs Rust and Python.', industry=None), FACTS, None, 'Jane Doe')
        for forbidden in ('Pending Corp', 'invented claim', 'Rust', 'jane@example.com', '555-0100'):
            self.assertNotIn(forbidden, text)
        self.assertTrue(set(ids) <= {1, 2, 3, 4})

    def test_two_different_jobs_get_different_letters(self):
        a, _, _ = letter(job(role='Python Developer', employer='Alpha', description='Python and SQL'), FACTS, None, 'Jane Doe')
        b, _, _ = letter(job(role='Data Analyst', employer='Beta Corp', description='SQL reporting and Git'), FACTS, None, 'Jane Doe')
        self.assertNotEqual(a, b); self.assertIn('Alpha', a); self.assertNotIn('Alpha', b)

    def test_missing_name_uses_a_visible_placeholder_and_missing_facts_still_refuse(self):
        self.assertTrue(letter(job(), FACTS)[0].endswith('[Your name]'))
        with self.assertRaises(ValueError): letter(job(), [f for f in FACTS if f['status'] != 'verified'])

    def test_resume_with_only_skills_still_makes_a_letter(self):
        text, _, _ = letter(job(description='Python'), [FACTS[0]], None, 'Jane Doe')
        self.assertIn('My background includes', text); self.assertTrue(text.endswith('Jane Doe'))


class ApplyUrlTests(unittest.TestCase):
    def test_company_hosted_greenhouse_jobs_use_the_official_embed_form(self):
        data = dict(source='greenhouse', board='airbnb', source_id='6757302', application_url='https://careers.airbnb.com/positions/6757302?gh_jid=6757302')
        self.assertEqual(apply_url(data), 'https://job-boards.greenhouse.io/embed/job_app?for=airbnb&token=6757302')
        hosted = dict(data, application_url='https://job-boards.greenhouse.io/airbnb/jobs/6757302')
        self.assertEqual(apply_url(hosted), hosted['application_url'])

    def test_unsupported_or_malformed_jobs_are_refused(self):
        for data in (dict(source='greenhouse', board='a b', source_id='1', application_url='https://x.com/1'),
                     dict(source='greenhouse', board='ok', source_id='abc', application_url='https://x.com/1'),
                     dict(source='lever', board='ok', source_id='1', application_url='https://x.com/1'),
                     dict(source='workday', board='ok', source_id='1', application_url='https://x.com/1'), {}):
            with self.subTest(data=data), self.assertRaises(ValueError): apply_url(data)


class CardLinkTests(FilterBase):
    def test_every_card_has_a_safe_link_and_an_autofill_label(self):
        with __import__('sqlite3').connect(self.jobdb) as db:
            db.execute('DELETE FROM observations'); db.execute('DELETE FROM jobs')
        self.add(role='Hosted Role', employer='A', department=None, industry=None, source='greenhouse', board='acme', source_id='123',
                 application_url='https://careers.example.invalid/jobs/123')
        self.add(role='Custom Role', employer='B', department=None, industry=None, source='lever', board='global:b', source_id='9',
                 application_url='https://careers.example.invalid/jobs/9')
        self.add(role='Bad Link Role', employer='C', department=None, industry=None, source='greenhouse', board='c', source_id='x',
                 application_url='javascript:alert(1)')
        self.login(); page = self.client.get('/jobs')
        self.assertIn('href="https://careers.example.invalid/jobs/123"', page.text); self.assertIn('rel="noopener noreferrer"', page.text)
        self.assertNotIn('javascript:', page.text)
        self.assertEqual(page.text.count('Auto-fill available'), 1)       # only the greenhouse job with a numeric id
        self.assertEqual(page.text.count('Apply on the site yourself'), 2)
        self.assertIn('No link stored for this job.', page.text)


if __name__ == '__main__':
    unittest.main()
