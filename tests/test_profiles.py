import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from getmeajob.resume.store import ProfileStore
from getmeajob.resume.extract import extract,validate,candidates
from getmeajob.resume.intelligence import match,letter
from getmeajob.resume.documents import export
from tests.test_discovery import job

class ProfilesTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(); self.store=ProfileStore(Path(self.temp.name)/'private')
        self.identity=self.store.upload('Synthetic','synthetic.pdf',b'%PDF-fake','Skills\nPython',candidates('Skills\nPython'))
    def tearDown(self): self.temp.cleanup()
    def test_verification_edit_reject_audit(self):
        fact=self.store.facts(self.identity)[0]
        self.assertEqual(self.store.facts(self.identity,True),[])
        self.store.fact(self.identity,'skills','Python','verified',fact['id'])
        self.assertEqual(len(self.store.facts(self.identity,True)),1)
        self.store.fact(self.identity,'skills','Rust','pending',fact['id'])
        self.assertEqual(self.store.facts(self.identity,True),[])
        with self.store.connect() as db: self.assertEqual(db.execute('SELECT COUNT(*) FROM fact_history').fetchone()[0],2)
    def test_versions_default_and_persistence(self):
        other=self.store.upload('Second','two.pdf',b'x','text',[])
        self.store.default(other)
        self.assertEqual([r['id'] for r in self.store.resumes() if r['is_default']],[other])
        self.assertEqual(len(ProfileStore(self.store.directory).resumes()),2)
    def test_pdf_docx_exports_and_extraction(self):
        for kind,filename in [('pdf','resume.pdf'),('docx','resume.docx')]:
            with self.subTest(kind=kind):
                data,_=export('Education\n\nSynthetic University\n\nSkills\n\nPython',kind)
                self.assertIn('Python',extract(filename,data))
    def test_invalid_files(self):
        for name,data in [('x.exe',b'bad'),('x.pdf',b'bad'),('x.docx',b'notzip'),('x.pdf',b'x'*9000000)]:
            with self.subTest(name=name),self.assertRaises(ValueError): validate(name,data)
    def test_only_verified_facts_affect_matching_and_letters(self):
        pending=self.store.facts(self.identity)
        self.assertEqual(match(job(),pending)['matched_skills'],[])
        with self.assertRaises(ValueError): letter(job(),pending)
        fact=pending[0]; self.store.fact(self.identity,'skills','Python','verified',fact['id'])
        approved=self.store.facts(self.identity)
        self.assertIn('python',match(job(),approved)['matched_skills'])
        result,ids,_=letter(job(),approved)
        self.assertIn('Python',result); self.assertTrue(result.endswith('Sincerely,\n[Your name]')); self.assertEqual(ids,[fact['id']]); self.assertNotIn('expert',result)
        self.assertIn('sqlite',match(job(),approved)['not_evidenced_skills'])
    def test_package_versioning(self):
        a=self.store.package(1,self.identity,{'snapshot':'Synthetic'},'Letter')
        b=self.store.package(1,self.identity,{'snapshot':'Synthetic'},'Edited','reviewed')
        self.assertEqual(self.store.get_package(a)['version'],1)
        self.assertEqual(self.store.get_package(b)['version'],2)
        self.assertEqual(self.store.get_package(a)['letter'],'Letter')
