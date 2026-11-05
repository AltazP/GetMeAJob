import io
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile
from fastapi.testclient import TestClient
from getmeajob.dashboard import create_app
from getmeajob.resume.documents import export
from getmeajob.resume.extract import validate
from getmeajob.resume.intelligence import weights,local_order,match
from getmeajob.storage import JobStore
from tests.test_discovery import job

class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(); self.root=Path(self.temp.name)
        self.jobdb=self.root/'jobs.sqlite3'
        with JobStore(self.jobdb) as db: self.jobid=db.upsert(job())
        self.app=create_app(self.root/'private',self.jobdb)
        self.client=TestClient(self.app,base_url='http://127.0.0.1:8000')
    def tearDown(self): self.client.close(); self.temp.cleanup()
    def csrf(self,path='/'):
        response=self.client.get(path)
        return re.search(r'name="csrf" value="([^"]+)"',response.text)[1]
    def login(self):
        token=self.csrf('/setup?token='+self.app.state.bootstrap)
        self.assertEqual(self.client.post('/setup',data={'csrf':token,'password':'Synthetic-test-password!'},follow_redirects=False).status_code,303)
        old=self.client.cookies.get('gmj_session')
        token=self.csrf('/login')
        result=self.client.post('/login',data={'csrf':token,'password':'Synthetic-test-password!'},follow_redirects=False)
        self.assertEqual(result.status_code,303,result.text)
        self.assertNotEqual(old,self.client.cookies.get('gmj_session'))
    def test_authentication_csrf_host_origin(self):
        self.assertEqual(self.client.get('/',follow_redirects=False).status_code,303)
        self.assertEqual(self.client.get('/setup?token=invalid').status_code,403)
        self.login()
        self.assertEqual(self.client.post('/logout',data={'csrf':'bad'}).status_code,403)
        self.assertEqual(self.client.get('/',headers={'Host':'evil.example'}).status_code,400)
        self.assertEqual(self.client.get('/',headers={'Origin':'https://evil.example'}).status_code,403)
        response=self.client.get('/')
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertIn('HttpOnly',response.headers['set-cookie'])
        self.assertIn("frame-ancestors 'none'",response.headers['content-security-policy'])
    def test_full_browser_workflow_upload_verify_generate_edit_download(self):
        self.login()
        data,_=export('Skills\n\nPython\n\nCoursework\n\nSQL databases','docx')
        response=self.client.post('/upload',data={'csrf':self.csrf(),'label':'Synthetic version'},files={'resume':('../synthetic.docx',data)},follow_redirects=False)
        self.assertEqual(response.status_code,303,response.text)
        resume_url=response.headers['location']; identity=int(resume_url.rsplit('/',1)[1])
        self.assertEqual(self.app.state.store.resume(identity)['filename'],'synthetic.docx')
        for fact in self.app.state.store.facts(identity):
            response=self.client.post(resume_url+'/facts',data={'csrf':self.csrf(resume_url),'fact_id':fact['id'],'category':fact['category'],'value':fact['value'],'status':'verified'},follow_redirects=False)
            self.assertEqual(response.status_code,303,response.text)
        jobs=self.client.get('/jobs'); self.assertIn('Python Developer',jobs.text)
        response=self.client.post('/packages',data={'csrf':self.csrf('/jobs'),'job_id':self.jobid,'resume_id':identity},follow_redirects=False)
        self.assertEqual(response.status_code,303,response.text)
        package_url=response.headers['location']
        response=self.client.post(package_url,data={'csrf':self.csrf(package_url),'letter':'User-reviewed synthetic letter','status':'reviewed'},follow_redirects=False)
        self.assertEqual(response.status_code,303)
        new_url=response.headers['location']
        for kind in ['txt','docx','pdf','zip']:
            result=self.client.get(new_url+'/download/'+kind)
            self.assertEqual(result.status_code,200,result.text[:80] if kind=='txt' else str(result.status_code))
            self.assertIn('attachment',result.headers['content-disposition'])
        with zipfile.ZipFile(io.BytesIO(self.client.get(new_url+'/download/zip').content)) as archive:
            package=json.loads(archive.read('package.json'))
            self.assertFalse(package['payload']['submission_authorized'])
            self.assertEqual(package['version'],2)
            self.assertIn('resume.docx',archive.namelist())
    def test_login_rate_limit_and_logout(self):
        self.login(); csrf=self.csrf()
        self.assertEqual(self.client.post('/logout',data={'csrf':csrf},follow_redirects=False).status_code,303)
        self.assertEqual(self.client.get('/',follow_redirects=False).status_code,303)
        csrf=self.csrf('/login')
        for _ in range(5): self.assertEqual(self.client.post('/login',data={'csrf':csrf,'password':'Wrong password!'}).status_code,401)
        self.assertEqual(self.client.post('/login',data={'csrf':csrf,'password':'Wrong password!'}).status_code,429)
    def test_upload_without_csrf_and_oversize(self):
        self.login()
        self.assertEqual(self.client.post('/upload',files={'resume':('test.pdf',b'%PDF-bad')}).status_code,403)
        response=self.client.post('/upload',data={'csrf':self.csrf()},files={'resume':('test.pdf',b'x'*9000000)})
        self.assertEqual(response.status_code,413)
    def test_xss_and_invalid_fact(self):
        self.login(); store=self.app.state.store
        identity=store.upload('<script>alert(1)</script>','x.pdf',b'test','Synthetic',[])
        self.assertNotIn('<script>alert(1)</script>',self.client.get('/').text)
        self.assertIn('&lt;script&gt;',self.client.get('/').text)
        result=self.client.post(f'/resumes/{identity}/facts',data={'csrf':self.csrf(),'category':'bad','value':'bad','status':'verified'})
        self.assertEqual(result.status_code,400)
    def test_restart_invalidates_session_and_preserves_password(self):
        self.login(); cookie=self.client.cookies.get('gmj_session')
        other=TestClient(create_app(self.root/'private',self.jobdb),base_url='http://127.0.0.1:8000')
        other.cookies.set('gmj_session',cookie)
        self.assertEqual(other.get('/',follow_redirects=False).status_code,303)
        self.assertEqual(other.get('/setup?token=invalid',follow_redirects=False).status_code,303)
        other.close()
    def test_scoring_weights_validation_and_coursework(self):
        for value in ({'skills':-1,'employment':1,'coursework':1},{'skills':0,'employment':0,'coursework':0}):
            with self.assertRaises(ValueError): weights(value)
        facts=[{'status':'verified','category':'coursework','value':'Python SQLite development'}]
        self.assertTrue(match(job(),facts)['relevant_coursework'])
    def test_docx_relationships_and_zip_traversal_rejected(self):
        for name,content in [('word/_rels/document.xml.rels',b"<Relationship TargetMode='External'/>") ,('../escape',b'x')]:
            stream=io.BytesIO()
            with zipfile.ZipFile(stream,'w') as archive:
                archive.writestr('word/document.xml','test'); archive.writestr(name,content)
            with self.assertRaises(ValueError): validate('resume.docx',stream.getvalue())
    def test_optional_ai_rejects_invented_ids_and_falls_back(self):
        with patch('http.client.HTTPConnection') as connection:
            connection.return_value.getresponse.return_value.status=200
            connection.return_value.getresponse.return_value.read.return_value=json.dumps({'response':json.dumps({'ordered_ids':[999]})}).encode()
            self.assertIsNone(local_order(['Verified Python'],'synthetic:model'))
        self.assertIsNone(local_order(['Verified Python'],'bad model\n'))
