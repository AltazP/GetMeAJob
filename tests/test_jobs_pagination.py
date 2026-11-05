"""Synthetic, offline checks for bounded job-page work and current scores."""
from dataclasses import replace
import json
import sqlite3
import unittest
from unittest.mock import patch

from tests import test_dashboard as fixtures
from tests.test_discovery import job
from getmeajob.resume.intelligence import job_page, job_by_id


class JobPageTests(unittest.TestCase):
    setUp=fixtures.DashboardTests.setUp
    tearDown=fixtures.DashboardTests.tearDown
    csrf=fixtures.DashboardTests.csrf
    login=fixtures.DashboardTests.login

    def seed(self,count):
        now='2026-11-05T00:00:00+00:00'
        with sqlite3.connect(self.jobdb) as db:
            for number in range(count):
                identity=db.execute('INSERT INTO jobs(first_discovered,last_checked,last_updated) VALUES(?,?,?)',(now,now,now)).lastrowid
                item=replace(job(),source_id=f'bulk-{number}',role=f'Synthetic Developer {number}',application_url=f'https://example.invalid/jobs/{number}')
                db.execute('INSERT INTO observations(source,board,source_id,job_id,payload,first_discovered,last_checked,last_updated) VALUES(?,?,?,?,?,?,?,?)',
                    (item.source,item.board,item.source_id,identity,json.dumps(item.to_dict()),now,now,now))

    def test_large_database_scores_only_twenty_jobs_per_page(self):
        self.seed(240); self.login();store=self.app.state.store
        store.upload('Version A','a.pdf',b'a','Synthetic',[('skills','Python')])
        store.upload('Version B','b.pdf',b'b','Synthetic',[('skills','SQL')])
        from getmeajob.dashboard import match
        with patch('getmeajob.dashboard.match',wraps=match) as measured:
            first=self.client.get('/jobs')
        self.assertEqual(first.status_code,200)
        self.assertIn('241 stored job(s) found',first.text)
        self.assertEqual(first.text.count('Prepare draft package'),20)
        self.assertEqual(measured.call_count,40)
        self.assertIn('/jobs?offset=20',first.text)
        second=self.client.get('/jobs?offset=20')
        self.assertEqual(second.status_code,200)
        self.assertEqual(second.text.count('Prepare draft package'),20)
        self.assertIn('Previous',second.text)
        self.assertNotEqual(first.text,second.text)
        filtered=self.client.get('/jobs?q=Synthetic%20Developer%202')
        self.assertEqual(filtered.status_code,200)
        self.assertIn('Synthetic Developer 2',filtered.text)
        self.assertNotIn('Synthetic Developer 10',filtered.text)

    def test_latest_duplicate_observation_and_current_job_description(self):
        self.seed(1)
        rows,total=job_page(self.jobdb)
        self.assertEqual(total,2)
        identity=next(key for key,item,_ in rows if item.role=='Synthetic Developer 0')
        changed=replace(job(),source='lever',board='synthetic',source_id='second',role='Updated Synthetic Developer',description='New description')
        with sqlite3.connect(self.jobdb) as db:
            db.execute('INSERT INTO observations(source,board,source_id,job_id,payload,first_discovered,last_checked,last_updated) VALUES(?,?,?,?,?,?,?,?)',
                ('lever','synthetic','second',identity,json.dumps(changed.to_dict()),'2026-11-05T00:00:00+00:00','2026-11-06T00:00:00+00:00','2026-11-06T00:00:00+00:00'))
        rows,total=job_page(self.jobdb,query='Updated Synthetic')
        self.assertEqual(total,1)
        self.assertEqual(rows[0][0],identity)
        self.assertEqual(rows[0][1].description,'New description')
        self.assertEqual(job_by_id(self.jobdb,identity)[0].role,'Updated Synthetic Developer')

    def test_verified_fact_changes_are_reflected_on_next_page_request(self):
        self.login();store=self.app.state.store
        identity=store.upload('Version','v.pdf',b'v','Synthetic',[('skills','Python')])
        initial=self.client.get('/jobs').text
        fact=store.facts(identity)[0]
        store.fact(identity,'skills',fact['value'],'verified',fact['id'])
        updated=self.client.get('/jobs').text
        self.assertIn('Python Developer',updated)
        self.assertNotEqual(initial,updated)

    def test_invalid_database_has_useful_error_page(self):
        self.login()
        self.jobdb.write_bytes(b'not a SQLite database')
        response=self.client.get('/jobs')
        self.assertEqual(response.status_code,503)
        self.assertIn('Could not load stored jobs',response.text)
