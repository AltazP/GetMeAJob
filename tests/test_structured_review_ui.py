from dataclasses import replace
import json
import re
import unittest
from tests import test_dashboard as fixtures
from tests.test_structured_resume import SYNTHETIC
from getmeajob.resume.parser import parse

# Reuse authenticated synthetic setup without duplicating the inherited test methods.
class StructuredReviewTests(unittest.TestCase):
    setUp=fixtures.DashboardTests.setUp
    tearDown=fixtures.DashboardTests.tearDown
    csrf=fixtures.DashboardTests.csrf
    login=fixtures.DashboardTests.login
    def test_grouped_ui_section_confirmation_and_explicit_final_review(self):
        self.login(); store=self.app.state.store
        identity=store.upload('Structured','synthetic.pdf',b'original',SYNTHETIC,parse(SYNTHETIC)); url=f'/resumes/{identity}'
        page=self.client.get(url)
        for heading in ('Personal Information','Education','Experience','Skills','Projects','Other Information'):
            self.assertIn(heading,page.text)
        self.assertIn('Company',page.text);self.assertIn('Complete achievement bullets',page.text)
        self.assertIn('Review and Verify All',page.text)
        data={'csrf':self.csrf(url),'section':'Experience','revision':store.revision(identity),'operation':'verify-section'}
        response=self.client.post(url+'/review-section',data=data)
        self.assertIn('Confirmation required',response.text)
        self.assertFalse(store.facts(identity,True))
        data['confirm']='yes'
        result=self.client.post(url+'/review-section',data=data,follow_redirects=False)
        self.assertEqual(result.status_code,303,result.text)
        self.assertEqual(len(store.facts(identity,True)),2)
        final={'csrf':self.csrf(url+'/review-all'),'revision':store.revision(identity)}
        self.assertEqual(self.client.post(url+'/verify-all',data=final).status_code,400)
        final['confirm']='yes'
        self.assertEqual(self.client.post(url+'/verify-all',data=final,follow_redirects=False).status_code,303)
        self.assertTrue(all(f['status']=='verified' for f in store.facts(identity)))

    def test_reprocessing_requires_confirmation_and_preserves_original_default(self):
        self.login(); store=self.app.state.store
        identity=store.upload('Legacy','old.pdf',b'old',SYNTHETIC,[('education','Old verified fact')])
        fact=store.facts(identity)[0];store.fact(identity,'education',fact['value'],'verified',fact['id'])
        original=store.resume(identity); original_facts=store.facts(identity);url=f'/resumes/{identity}'
        self.assertIn('Legacy extraction',self.client.get(url).text)
        response=self.client.get(url+'/reprocess');self.assertIn('Software Developer',response.text)
        data={'csrf':self.csrf(url+'/reprocess'),'revision':store.revision(identity)}
        self.assertEqual(self.client.post(url+'/reprocess',data=data).status_code,400)
        data['confirm']='yes'
        response=self.client.post(url+'/reprocess',data=data,follow_redirects=False)
        self.assertEqual(response.status_code,303,response.text)
        new_id=int(response.headers['location'].rsplit('/',1)[1])
        self.assertEqual(original,store.resume(identity));self.assertEqual(original_facts,store.facts(identity))
        self.assertTrue(all(f['status']=='pending' for f in store.facts(new_id)))
        self.assertEqual(store.resume(new_id)['parent_resume_id'],identity)

    def test_edit_whole_employment_record_preserves_evidence_and_requires_reverification(self):
        self.login();store=self.app.state.store
        identity=store.upload('Structured','test.pdf',b'old',SYNTHETIC,parse(SYNTHETIC));url=f'/resumes/{identity}'
        fact=next(f for f in store.facts(identity) if f['category']=='employment')
        data={'csrf':self.csrf(url),'section':'Experience','revision':store.revision(identity),'operation':f"verify:{fact['id']}"}
        self.assertEqual(self.client.post(url+'/review-section',data=data,follow_redirects=False).status_code,303)
        data.update({'csrf':self.csrf(url),'revision':store.revision(identity),'operation':f"save:{fact['id']}",f"f_{fact['id']}_employer":'Corrected Fictional Labs'})
        self.assertEqual(self.client.post(url+'/review-section',data=data,follow_redirects=False).status_code,303)
        edited=next(f for f in store.facts(identity) if f['id']==fact['id'])
        self.assertEqual(edited['status'],'pending')
        self.assertEqual(edited['structure']['employer'],'Corrected Fictional Labs')
        self.assertEqual(edited['evidence'],fact['evidence'])
        self.assertEqual(len(edited['structure']['achievements']),2)

    def test_stale_bulk_review_and_csrf_cannot_verify(self):
        self.login();store=self.app.state.store
        identity=store.upload('Structured','test.pdf',b'old',SYNTHETIC,parse(SYNTHETIC));url=f'/resumes/{identity}'
        data={'csrf':self.csrf(url),'revision':store.revision(identity),'confirm':'yes'}
        store.fact(identity,'skills','New pending fact','pending')
        self.assertEqual(self.client.post(url+'/verify-all',data=data).status_code,400)
        data.update(revision=store.revision(identity),csrf='bad')
        self.assertEqual(self.client.post(url+'/verify-all',data=data).status_code,403)
        self.assertFalse(store.facts(identity,True))
    def test_final_bulk_verification_keeps_rejected_records_rejected(self):
        self.login();store=self.app.state.store
        identity=store.upload('Structured','test.pdf',b'old',SYNTHETIC,parse(SYNTHETIC));url=f'/resumes/{identity}'
        rejected=store.facts(identity)[0]
        store.review(identity,[{'id':rejected['id'],'status':'rejected'}],store.revision(identity))
        data={'csrf':self.csrf(url+'/review-all'),'revision':store.revision(identity),'confirm':'yes'}
        self.assertEqual(self.client.post(url+'/verify-all',data=data,follow_redirects=False).status_code,303)
        facts=store.facts(identity)
        self.assertEqual(next(f for f in facts if f['id']==rejected['id'])['status'],'rejected')
        self.assertTrue(all(f['status']=='verified' for f in facts if f['id']!=rejected['id']))
