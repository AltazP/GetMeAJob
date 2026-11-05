import hashlib
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from getmeajob.resume.parser import parse,clean,render_position
from getmeajob.resume.store import ProfileStore
from getmeajob.resume.intelligence import match,letter
from tests.test_discovery import job

SYNTHETIC='''Jordan Example
jordan@example.invalid | +1 555 010 1234
https://github.com/synthetic
Education
Synthetic University | BSc Computer Science
Relevant Coursework
SQL databases, distributed systems
Experience
Software Developer | Fictional Labs | Waterloo, ON | May 2023 – Aug 2024
• Built Python services that
  processed synthetic records using SQLite.
• Reduced duplicate records through validation.

Data Analyst | Example Tools | Guelph, Ontario | Sep 2024 – Present
• Designed SQL reports for
  synthetic operational datasets.
Technical Skills
Python, SQL, C++, C#, SQLite
Projects
• Built a test application with
  reproducible fixtures.
Certifications
Synthetic Certificate
'''

class ParserTests(unittest.TestCase):
    def test_contacts_are_not_education(self):
        facts=parse(SYNTHETIC)
        self.assertEqual([f['value'] for f in facts if f['category']=='personal'][:2],['Jordan Example','jordan@example.invalid | +1 555 010 1234'])
        self.assertFalse(any('jordan@' in f['value'] for f in facts if f['category']=='education'))
    def test_positions_and_multiline_bullets(self):
        records=[f for f in parse(SYNTHETIC) if f['category']=='employment']
        self.assertEqual(len(records),2)
        first,second=[f['structure'] for f in records]
        self.assertEqual(first['employer'],'Fictional Labs')
        self.assertEqual(first['title'],'Software Developer')
        self.assertEqual(first['location'],'Waterloo, ON')
        self.assertEqual(first['start_date'],'May 2023');self.assertEqual(first['end_date'],'Aug 2024')
        self.assertEqual(first['achievements'],['Built Python services that processed synthetic records using SQLite.','Reduced duplicate records through validation.'])
        self.assertEqual(second['employer'],'Example Tools')
        self.assertNotIn('Python',second['achievements'][0])
        self.assertIn('• Built Python services that\n  processed',records[0]['evidence'])
    def test_unicode_cleanup_preserves_meaning_and_flags_uncertainty(self):
        value,notes=clean('Oﬃce C++ C# café ﬁles \ufffd (cid:42)')
        self.assertEqual(value,'Office C++ C# café files \ufffd (cid:42)')
        self.assertTrue(any('U+FFFD' in note for note in notes))
        self.assertTrue(any('character ID' in note for note in notes))
        value,notes=clean('\ue123 jordan@example.invalid')
        self.assertEqual(value,'jordan@example.invalid');self.assertTrue(notes)
        value,notes=clean('Meaningful\ue123symbol')
        self.assertIn('\ue123',value);self.assertTrue(notes)
    def test_capitalized_wrapped_bullet_and_unmarked_new_achievement(self):
        record=parse('Experience\nDeveloper | Fictional Labs\n• Built reports using Python and\nSQL for synthetic data\nDesigned independent reports.')[0]
        self.assertEqual(record['structure']['achievements'],['Built reports using Python and SQL for synthetic data','Designed independent reports.'])
        self.assertTrue(record['warnings'])
    def test_education_dates_are_not_phone_numbers(self):
        facts=parse('Education\nSynthetic University\n2020 - 2024')
        self.assertTrue(all(f['category']=='education' for f in facts))
    def test_unknown_mojibake_is_flagged_not_guessed(self):
        value,notes=clean('Text â€¢ with broken encoding')
        self.assertEqual(value,'Text â€¢ with broken encoding')
        self.assertTrue(any('encoding artifact' in note for note in notes))
    def test_unknown_headers_not_invented(self):
        fact=parse('Experience\nAmbiguous company text\n• Built a synthetic tool.')[0]
        self.assertEqual(fact['structure']['title'],'')
        self.assertTrue(any('ambiguous' in n for n in fact['warnings']))
    def test_unrelated_marked_bullets_remain_separate(self):
        record=parse('Experience\nDeveloper | Fictional Company\n• First achievement\n• Second achievement')[0]
        self.assertEqual(record['structure']['achievements'],['First achievement','Second achievement'])
    def test_project_wrap_and_evidence(self):
        record=[f for f in parse(SYNTHETIC) if f['category']=='projects'][0]
        self.assertIn('reproducible fixtures.',record['value'])
        self.assertIn('\n  reproducible',record['evidence'])

class StructuredStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.store=ProfileStore(Path(self.temp.name)/'private')
        self.identity=self.store.upload('Original','test.pdf',b'original bytes',SYNTHETIC,parse(SYNTHETIC))
    def tearDown(self): self.temp.cleanup()
    def test_review_requires_explicit_confirmation_and_scopes_ids(self):
        record=self.store.facts(self.identity)[0]
        with self.assertRaises(ValueError): self.store.review(self.identity,[{'id':record['id'],'status':'verified'}],self.store.revision(self.identity))
        self.assertEqual(self.store.facts(self.identity)[0]['status'],'pending')
        self.store.review(self.identity,[{'id':record['id'],'status':'verified'}],self.store.revision(self.identity),confirm=True)
        with self.assertRaises(ValueError): self.store.review(self.identity,[{'id':999,'status':'verified'}],self.store.revision(self.identity),confirm=True)
    def test_stale_review_and_edited_verified_record(self):
        revision=self.store.revision(self.identity);first=self.store.facts(self.identity)[0]
        self.store.review(self.identity,[{'id':first['id'],'status':'verified'}],revision,confirm=True)
        with self.assertRaises(ValueError): self.store.review(self.identity,[],revision,confirm=True)
        with self.assertRaises(ValueError): self.store.review(self.identity,[{'id':first['id'],'value':'Changed','status':'verified'}],self.store.revision(self.identity))
    def test_reprocess_preserves_original_and_does_not_inherit_verification(self):
        records=self.store.facts(self.identity)
        self.store.fact(self.identity,records[0]['category'],records[0]['value'],'verified',records[0]['id'])
        before=self.store.resume(self.identity); facts=self.store.facts(self.identity)
        with self.assertRaises(ValueError): self.store.reprocess(self.identity,parse(SYNTHETIC))
        second=self.store.reprocess(self.identity,parse(SYNTHETIC),confirmed=True)
        self.assertEqual(before,self.store.resume(self.identity)); self.assertEqual(facts,self.store.facts(self.identity))
        self.assertEqual(self.store.resume(second)['parent_resume_id'],self.identity)
        self.assertFalse(self.store.resume(second)['is_default'])
        self.assertTrue(all(f['status']=='pending' for f in self.store.facts(second)))
    def test_matching_and_letter_use_complete_verified_records(self):
        facts=self.store.facts(self.identity)
        employment=next(f for f in facts if f['category']=='employment')
        self.store.review(self.identity,[{'id':employment['id'],'status':'verified'}],self.store.revision(self.identity),confirm=True)
        facts=self.store.facts(self.identity)
        result=match(job(),facts)
        self.assertIn('Fictional Labs',result['relevant_experience'][0])
        text,ids,_=letter(job(),facts)
        self.assertIn('Built Python services that processed synthetic records using SQLite.',text)
        self.assertIn('Fictional Labs',text);self.assertNotIn('Example Tools',text)
        self.assertEqual(ids,[employment['id']])
    def test_verified_contact_details_are_not_qualification_evidence(self):
        facts=self.store.facts(self.identity)
        personal=next(f for f in facts if '@' in f['value'])
        self.store.review(self.identity,[{'id':personal['id'],'status':'verified'}],self.store.revision(self.identity),confirm=True)
        approved=self.store.facts(self.identity)
        self.assertFalse(match(job(),approved)['relevant_experience'])
        with self.assertRaises(ValueError): letter(job(),approved)
    def test_schema_one_migration_preserves_all_existing_values(self):
        with self.store.connect() as db:
            # A separate realistic v1 file, not the migrated database.
            legacy=Path(self.temp.name)/'legacy';legacy.mkdir()
            old=sqlite3.connect(legacy/'profiles.sqlite3')
            old.executescript('''CREATE TABLE resumes(id INTEGER PRIMARY KEY,label TEXT,filename TEXT,content BLOB,text TEXT,created TEXT,is_default INTEGER);
              CREATE TABLE facts(id INTEGER PRIMARY KEY,resume_id INTEGER,category TEXT,value TEXT,status TEXT,evidence TEXT,updated TEXT);
              INSERT INTO resumes VALUES(1,'Old','old.pdf',X'0102','Original','then',1);
              INSERT INTO facts VALUES(1,1,'education','Old verified fact','verified','Original evidence','then');
              PRAGMA user_version=1;'''); old.close()
            migrated=ProfileStore(legacy)
            self.assertEqual(migrated.resume(1)['content'],b'\x01\x02')
            fact=migrated.facts(1)[0]
            self.assertEqual((fact['value'],fact['status'],fact['evidence']),('Old verified fact','verified','Original evidence'))
            self.assertIsNone(fact['structure'])
