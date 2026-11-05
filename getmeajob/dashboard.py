"""Authenticated loopback-only FastAPI interface. No submission or messaging routes."""
from dataclasses import asdict, replace
import hashlib
import io
import json
import sqlite3
from pathlib import Path
import secrets
import time
import zipfile

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from jinja2 import Environment, FileSystemLoader, select_autoescape

from getmeajob.resume.store import ProfileStore, CATEGORIES
from getmeajob.resume.extract import extract, MAX_FILE
from getmeajob.resume.parser import parse, GROUPS
from getmeajob.resume.intelligence import job_page, job_by_id, match, letter, weights, stored_weights, DEFAULT_WEIGHTS
from getmeajob import apply_assist, jobfilters, jobsquery, notify
from getmeajob.classify import FIELDS, LEVELS
from getmeajob.locations import PROVINCES
from datetime import datetime, timezone
from getmeajob.resume.documents import export
from getmeajob.models import utc_now


def password_hash(password,salt):
    return hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()


def create_app(private=Path('private'), job_database=Path('data/jobs.sqlite3'), port=8000):
    store=ProfileStore(private)
    async def cleanup(request:Request):
        try: yield
        finally: await request.close()
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None,dependencies=[Depends(cleanup)])
    app.state.store=store; app.state.bootstrap=secrets.token_urlsafe(32)
    sessions={}; attempts=[]
    templates=Environment(loader=FileSystemLoader(Path(__file__).parent/'templates'),autoescape=select_autoescape(['html']))
    origin=f'http://127.0.0.1:{port}'

    def page(request,title,status_code=200,**context):
        return HTMLResponse(templates.get_template('dashboard.html').render(title=title,csrf=request.state.session['csrf'],categories=CATEGORIES,**context),status_code=status_code)

    @app.middleware('http')
    async def boundary(request,call_next):
        if request.headers.get('host')!=f'127.0.0.1:{port}': return Response('Invalid host',status_code=400)
        if request.client and request.client.host not in ('127.0.0.1','::1','testclient'):
            return Response('Loopback only',status_code=403)
        if request.headers.get('origin') not in (None,origin) or request.headers.get('sec-fetch-site')=='cross-site':
            return Response('Cross-origin access denied',status_code=403)
        if request.method not in ('GET','POST'): return Response('Method not allowed',status_code=405)
        now=time.monotonic()
        for key in list(sessions):
            if sessions[key]['expires']<now: del sessions[key]
        cookie=request.cookies.get('gmj_session'); session=sessions.get(cookie)
        if not session:
            if request.method=='POST': return Response('Session expired; reload the page.',status_code=403)
            if len(sessions)>=1000: return Response('Too many sessions',status_code=429)
            cookie=secrets.token_urlsafe(32); session={'csrf':secrets.token_urlsafe(32),'auth':False,'expires':now+3600}
            sessions[cookie]=session
        request.state.session=session
        public=request.url.path in ('/login','/setup')
        if not public and not session['auth']:
            response=RedirectResponse('/login',status_code=303)
        else:
            if request.method=='POST':
                chunks=[]; size=0
                async for chunk in request.stream():
                    size+=len(chunk)
                    if size>MAX_FILE+128000: return Response('Upload too large',status_code=413)
                    chunks.append(chunk)
                request._body=b''.join(chunks)
                try: form=await request.form(max_files=1,max_fields=4000,max_part_size=MAX_FILE)
                except Exception: return Response('Invalid form',status_code=400)
                if not secrets.compare_digest(str(form.get('csrf','')),session['csrf']):
                    await form.close()
                    return Response('CSRF validation failed',status_code=403)
            try: response=await call_next(request)
            except ValueError: response=Response('Invalid data. Check fields and try again.',status_code=400)
            finally:
                if request.method=='POST': await form.close()
        if getattr(request.state,'rotate',False):
            sessions.pop(cookie,None)
            cookie=secrets.token_urlsafe(32); sessions[cookie]=session
        session['expires']=now+3600
        response.set_cookie('gmj_session',cookie,httponly=True,samesite='strict',max_age=3600,path='/')
        response.headers.update({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
            # no-referrer can turn ordinary form POST origins into null in browsers.
            'Referrer-Policy':'same-origin','Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"})
        return response

    @app.get('/setup')
    async def setup(request:Request):
        if store.get_setting('password'): return RedirectResponse('/login',303)
        if not secrets.compare_digest(request.query_params.get('token',''),app.state.bootstrap): return Response('Use the one-time setup link printed at startup.',status_code=403)
        request.state.session['setup']=True
        return page(request,'Set your local password',screen='setup')

    @app.post('/setup')
    async def setup_save(request:Request):
        if store.get_setting('password') or not request.state.session.get('setup'): return Response('Setup denied',status_code=403)
        form=await request.form(); password=str(form.get('password',''))
        if not 12<=len(password)<=200: return Response('Use a password of 12–200 characters.',status_code=400)
        salt=secrets.token_hex(16)
        store.set_setting('password',json.dumps({'salt':salt,'hash':password_hash(password,salt)}))
        request.state.session['setup']=False
        return RedirectResponse('/login',303)

    @app.get('/login')
    async def login(request:Request): return page(request,'Unlock GetMeAJob',screen='login',configured=bool(store.get_setting('password')))

    @app.post('/login')
    async def authenticate(request:Request):
        now=time.monotonic(); attempts[:]=[n for n in attempts if n>now-60]
        if len(attempts)>=5: return Response('Too many login attempts. Try again in one minute.',status_code=429)
        attempts.append(now)
        record=store.get_setting('password'); form=await request.form(); password=str(form.get('password',''))
        if not record or len(password)>200: return Response('Invalid credentials',status_code=401)
        record=json.loads(record)
        if not secrets.compare_digest(password_hash(password,record['salt']),record['hash']): return Response('Invalid credentials',status_code=401)
        # Rotate session and CSRF on authentication; the old session stays unauthenticated.
        request.state.rotate=True
        request.state.session['auth']=True; request.state.session['csrf']=secrets.token_urlsafe(32)
        attempts.clear()
        return RedirectResponse('/',303)

    @app.post('/logout')
    async def logout(request:Request):
        request.state.session['auth']=False; request.state.session['csrf']=secrets.token_urlsafe(32)
        return RedirectResponse('/login',303)

    @app.get('/')
    async def home(request:Request):
        return page(request,'Your resumes',screen='home',resumes=store.resumes(),packages=store.packages())

    @app.post('/upload')
    async def upload(request:Request):
        form=await request.form(); upload=form.get('resume')
        if not upload or not hasattr(upload,'read'): return Response('Choose a PDF or DOCX.',status_code=400)
        data=await upload.read(MAX_FILE+1)
        try: text=extract(upload.filename or '',data)
        except ValueError as exc: return page(request,'Upload needs attention',screen='message',message=str(exc))
        filename=Path((upload.filename or 'resume').replace('\\','/')).name[:120]
        identity=store.upload(str(form.get('label') or filename),filename,data,text,parse(text))
        return RedirectResponse(f'/resumes/{identity}',303)

    @app.get('/resumes/{identity}')
    async def resume_page(request:Request,identity:int):
        facts=store.facts(identity)
        return page(request,'Review your structured profile',screen='resume',resume=store.resume(identity),facts=facts,
            groups={name:[f for f in facts if f['category'] in categories] for name,categories in GROUPS.items()},
            counts={status:sum(f['status']==status for f in facts) for status in ('pending','verified','rejected')},revision=store.revision(identity))

    @app.post('/resumes/{identity}/facts')
    async def save_fact(request:Request,identity:int):
        form=await request.form()
        store.fact(identity,str(form.get('category')),str(form.get('value','')),str(form.get('status')),int(form['fact_id']) if form.get('fact_id') else None)
        return RedirectResponse(f'/resumes/{identity}',303)

    @app.post('/resumes/{identity}/review-section')
    async def review_section(request:Request,identity:int):
        form=await request.form(); section=str(form.get('section',''))
        if section not in GROUPS: raise ValueError('Invalid section.')
        facts=[f for f in store.facts(identity) if f['category'] in GROUPS[section]]
        operation=str(form.get('operation','save-section'))
        if operation not in ('save-section','verify-section') and not operation.startswith(('save:','verify:','reject:')):
            raise ValueError('Invalid review operation.')
        confirm=form.get('confirm')=='yes'
        if operation=='verify-section' and not confirm:
            return page(request,'Confirmation required',screen='message',message='Confirm that you reviewed this section, including flagged text, before verifying it.')
        if ':' in operation:
            action,chosen=operation.split(':',1); facts=[f for f in facts if f['id']==int(chosen)]
            if not facts: raise ValueError('Fact does not belong to this section.')
        else: action=operation
        updates=[]
        for fact in facts:
            prefix='f_'+str(fact['id'])+'_'
            status=str(form.get(prefix+'status',fact['status']))
            if action=='verify' or (action=='verify-section' and status!='rejected'): status='verified'
            if action=='reject': status='rejected'
            update={'id':fact['id'],'category':str(form.get(prefix+'category',fact['category'])),'status':status}
            if fact['structure'] is not None:
                record={key:str(form.get(prefix+key,fact['structure'].get(key,''))).strip() for key in ('employer','title','location','start_date','end_date','header_unassigned')}
                record['achievements']=[line.strip().lstrip('•').strip() for line in str(form.get(prefix+'achievements','\n'.join(fact['structure']['achievements']))).splitlines() if line.strip()]
                update['structure']=record
            else: update['value']=str(form.get(prefix+'value',fact['value']))
            # Saving an edit alone never inherits an old verified status.
            from getmeajob.resume.parser import render_position
            value=render_position(update['structure']) if 'structure' in update else update['value'].strip()
            if status=='verified' and action not in ('verify','verify-section') and (value!=fact['value'] or update['category']!=fact['category'] or fact['status']!='verified'):
                update['status']='pending'
            updates.append(update)
        store.review(identity,updates,str(form.get('revision','')),confirm=confirm or action=='verify')
        return RedirectResponse(f'/resumes/{identity}#section-'+str(list(GROUPS).index(section)),303)

    @app.get('/resumes/{identity}/review-all')
    async def review_all(request:Request,identity:int):
        return page(request,'Final review before verification',screen='review-all',resume=store.resume(identity),facts=store.facts(identity),revision=store.revision(identity))

    @app.post('/resumes/{identity}/verify-all')
    async def verify_all(request:Request,identity:int):
        form=await request.form()
        if form.get('confirm')!='yes': return Response('Explicit confirmation is required.',status_code=400)
        facts=store.facts(identity)
        store.review(identity,[{'id':f['id'],'status':'verified'} for f in facts if f['status']=='pending'],str(form.get('revision','')),confirm=True)
        return RedirectResponse(f'/resumes/{identity}',303)

    @app.get('/resumes/{identity}/reprocess')
    async def reprocess_preview(request:Request,identity:int):
        resume=store.resume(identity)
        return page(request,'Preview structured reprocessing',screen='reprocess',resume=resume,parsed=parse(resume['text']),revision=store.revision(identity))

    @app.post('/resumes/{identity}/reprocess')
    async def reprocess_confirm(request:Request,identity:int):
        form=await request.form()
        if form.get('confirm')!='yes' or str(form.get('revision',''))!=store.revision(identity):
            return Response('Confirm a fresh preview before reprocessing.',status_code=400)
        original=store.resume(identity)
        new=store.reprocess(identity,parse(original['text']),confirmed=True)
        return RedirectResponse(f'/resumes/{new}',303)

    @app.post('/resumes/{identity}/default')
    async def default_resume(request:Request,identity:int):
        store.default(identity); return RedirectResponse('/',303)

    @app.get('/settings')
    async def scoring(request:Request): return page(request,'Matching weights',screen='settings',weights=load_weights())

    @app.post('/settings')
    async def save_scoring(request:Request):
        form=await request.form(); configuration=weights({k:float(form[k]) for k in DEFAULT_WEIGHTS})
        store.set_setting('weights',json.dumps(configuration)); return RedirectResponse('/jobs',303)

    def load_weights():
        return stored_weights(store.get_setting('weights',json.dumps(DEFAULT_WEIGHTS)))

    def jobs_page(request,filters,offset=0,error=None,status_code=200,view_dismissed=False):
        try:
            offset=max(0,min(offset,100000))
            session=request.state.session
            if 'jobs_baseline' not in session:
                # "New" means first stored after your previous visit; fixed for this browser session.
                session['jobs_baseline']=store.get_setting('jobs_last_visit')
                store.set_setting('jobs_last_visit',utc_now())
            result=jobsquery.run(job_database,store,filters,load_weights(),lambda *a:match(*a),offset,
                baseline=session['jobs_baseline'],view_dismissed=view_dismissed)
            items=[]
            for row in result.rows:
                resume=result.best.get(row.id); m=result.matches.get((row.id,resume['id'])) if resume else None
                try: apply_assist.apply_url(row.job.to_dict()); can_fill=True
                except ValueError: can_fill=False
                items.append({'id':row.id,'job':row.job,'link':notify.safe_link(row.job.original_url,row.job.application_url),'can_fill':can_fill,'checked':row.checked,'resume':resume,'match':m,'score':row.score,
                    'field':row.field,'field_reason':row.field_reason,'level':row.level,'level_reason':row.level_reason,'is_new':row.is_new})
            age=None
            if result.newest_check:
                try: age=(datetime.now(timezone.utc)-datetime.fromisoformat(result.newest_check)).days
                except ValueError: pass
            return page(request,'Matching stored jobs',status_code=status_code,screen='jobs',results=items,f=filters,error=error,
                chips=jobfilters.chips(filters),notices=result.notices,total=result.total,offset=offset,stats=result,stale_days=age,
                sort_queries={k:jobfilters.query_string(replace(filters,sort=k),0) for k in jobfilters.SORTS},
                preset_json=jobfilters.dump_preferences(filters),
                view_dismissed=view_dismissed,resumes_exist=bool(store.resumes()),
                prev_query=jobfilters.query_string(filters,max(0,offset-20)),next_query=jobfilters.query_string(filters,offset+20),
                options={'fields':FIELDS,'employment':jobfilters.EMPLOYMENT,'arrangements':jobfilters.ARRANGEMENTS,'levels':LEVELS,
                    'provinces':sorted(PROVINCES),'currencies':jobfilters.CURRENCIES,'intervals':jobfilters.INTERVALS,
                    'posted':jobfilters.POSTED,'sorts':jobfilters.SORTS})
        except (sqlite3.DatabaseError, OSError, KeyError, TypeError, json.JSONDecodeError):
            return page(request,'Stored jobs unavailable',status_code=503,screen='message',
                message='Could not load stored jobs. The local database may be busy or contain an invalid posting. Please retry; no records were changed.')

    @app.get('/jobs')
    async def matching_jobs(request:Request,offset:int=0,hidden:int=0):
        error=None
        try:
            if 'applied' in request.query_params:
                filters=jobfilters.parse(request.query_params); jobfilters.validate_location(filters)
            else:
                filters=jobfilters.load_preferences(store.get_setting('job_filters'))
                try: jobfilters.validate_location(filters)
                except jobfilters.FilterError: filters=jobfilters.JobFilters()
        except jobfilters.FilterError as exc:
            error=str(exc); filters=jobfilters.JobFilters()
        return jobs_page(request,filters,offset,error,view_dismissed=bool(hidden))

    async def submitted_filters(request):
        form=await request.form()
        filters=jobfilters.parse(form); jobfilters.validate_location(filters)
        return filters

    @app.post('/jobs/filters/apply')
    async def apply_filters(request:Request):
        try: filters=await submitted_filters(request)
        except jobfilters.FilterError as exc: return jobs_page(request,jobfilters.JobFilters(),0,str(exc),400)
        return RedirectResponse('/jobs?'+jobfilters.query_string(filters),303)

    @app.post('/jobs/filters/save')
    async def save_filters(request:Request):
        try: filters=await submitted_filters(request)
        except jobfilters.FilterError as exc: return jobs_page(request,jobfilters.JobFilters(),0,str(exc),400)
        store.set_setting('job_filters',jobfilters.dump_preferences(filters))
        return RedirectResponse('/jobs?'+jobfilters.query_string(filters),303)

    @app.post('/jobs/filters/reset')
    async def reset_filters(request:Request):
        store.delete_setting('job_filters')
        return RedirectResponse('/jobs',303)

    def notifications_page(request,error=None,status_code=200):
        presets=[{**p,'chips':jobfilters.chips(jobfilters.load_preferences(p['filters']))} for p in store.presets()]
        return page(request,'Email alerts',status_code=status_code,screen='notifications',presets=presets,error=error,
            email_address=store.get_setting('notify_address'))

    @app.get('/notifications')
    async def notifications(request:Request): return notifications_page(request)

    @app.post('/notifications/create')
    async def create_preset(request:Request):
        form=await request.form()
        try:
            saved=json.loads(str(form.get('filters','')))
            if not isinstance(saved,dict) or saved.get('version')!=jobfilters.PREFERENCES_VERSION or not isinstance(saved.get('filters'),dict):
                raise jobfilters.FilterError('The filters could not be read. Apply your filters and try again.')
            filters=jobfilters.parse(saved['filters']); jobfilters.validate_location(filters)
            if not jobfilters.is_narrow(filters): raise jobfilters.FilterError(jobfilters.NARROW_MESSAGE)
            store.add_preset(str(form.get('name','')),jobfilters.dump_preferences(filters))
        except json.JSONDecodeError:
            return notifications_page(request,'The filters could not be read. Apply your filters and try again.',400)
        except (jobfilters.FilterError,ValueError) as exc:
            return notifications_page(request,str(exc) if str(exc) else 'Invalid preset.',400)
        return RedirectResponse('/notifications',303)

    @app.post('/notifications/toggle')
    async def toggle_preset(request:Request):
        form=await request.form(); store.set_preset_enabled(int(form['preset_id']),form.get('enabled')=='1')
        return RedirectResponse('/notifications',303)

    @app.post('/notifications/delete')
    async def delete_preset(request:Request):
        form=await request.form(); store.delete_preset(int(form['preset_id']))
        return RedirectResponse('/notifications',303)

    @app.post('/jobs/dismiss')
    async def dismiss(request:Request):
        form=await request.form(); store.dismiss(int(form['job_id']),form.get('undo')!='1')
        return RedirectResponse('/jobs?hidden=1' if form.get('undo')=='1' else '/jobs',303)

    def get_job(identity):
        return job_by_id(job_database,identity)

    @app.post('/packages')
    async def generate(request:Request):
        form=await request.form(); job_id=int(form['job_id']); resume_id=int(form['resume_id'])
        job,checked=get_job(job_id); resume=store.resume(resume_id); facts=store.facts(resume_id)
        configuration=load_weights()
        details=json.loads(store.get_setting('applicant','{}')); name=' '.join(x for x in (details.get('first_name'),details.get('last_name')) if x) or None
        text,ids,mode=letter(job,facts,str(form.get('model','')) or None,name)
        payload={'employer':job.employer,'position':job.role,'original_url':job.original_url or job.application_url,
            'job':job.to_dict(),'job_last_checked':checked,'resume':{k:v for k,v in resume.items() if k!='content'},
            'match':match(job,facts,configuration),'verified_facts':[f for f in facts if f['status']=='verified'],
            'letter_fact_ids':ids,'generation_mode':mode,'created':utc_now(),
            'missing_information':['Applicant name/signature and contact details need review.','Application questions, eligibility and current vacancy status require review.'],
            'submission_authorized':False}
        identity=store.package(job_id,resume_id,payload,text)
        return RedirectResponse(f'/packages/{identity}',303)

    def package_screen(request,identity,assist_error=None,status_code=200):
        package=store.get_package(identity)
        try: apply_assist.prepare(store,identity); ready=True
        except ValueError as exc: ready=False; assist_error=assist_error or (str(exc) if package['status']=='reviewed' else None)
        return page(request,'Review application package',status_code=status_code,screen='package',package=package,can_assist=ready,
            posting_link=notify.safe_link(package['payload'].get('original_url'),(package['payload'].get('job') or {}).get('application_url')),
            assist_error=assist_error,applications=store.applications(identity))

    @app.get('/applicant')
    async def applicant(request:Request):
        return page(request,'Applicant details',screen='applicant',applicant=json.loads(store.get_setting('applicant','{}')),error=None,saved=False)

    @app.post('/applicant')
    async def save_applicant(request:Request):
        form=await request.form()
        try: clean=apply_assist.validate_applicant({k:form.get(k,'') for k in apply_assist.APPLICANT_FIELDS})
        except ValueError as exc:
            return page(request,'Applicant details',status_code=400,screen='applicant',applicant={k:str(form.get(k,'')) for k in apply_assist.APPLICANT_FIELDS},error=str(exc),saved=False)
        store.set_setting('applicant',json.dumps(clean))
        return page(request,'Applicant details',screen='applicant',applicant=clean,error=None,saved=True)

    @app.post('/packages/{identity}/assist')
    async def assist(request:Request,identity:int):
        try: apply_assist.prepare(store,identity)
        except ValueError as exc: return package_screen(request,identity,str(exc),400)
        apply_assist.spawn(store.directory,identity,Path(__file__).resolve().parents[1])
        return page(request,'Opening the application',screen='message',message='Chrome is opening the application form. Review every field, answer the remaining questions and press Submit yourself. Nothing is submitted for you. Close that window when you are done.')

    @app.post('/packages/{identity}/applied')
    async def applied(request:Request,identity:int):
        package=store.get_package(identity); store.add_application(identity,package['job_id'],'applied_by_user')
        return RedirectResponse(f'/packages/{identity}',303)

    @app.get('/packages/{identity}')
    async def package_page(request:Request,identity:int):
        return package_screen(request,identity)

    @app.post('/packages/{identity}')
    async def edit_package(request:Request,identity:int):
        previous=store.get_package(identity); form=await request.form(); text=str(form.get('letter',''))
        if not 1<=len(text)<=30000: raise ValueError('Letter length invalid.')
        payload=previous['payload']; payload['user_edited']=True; payload['submission_authorized']=False
        new=store.package(previous['job_id'],previous['resume_id'],payload,text,str(form.get('status','draft')))
        return RedirectResponse(f'/packages/{new}',303)

    @app.get('/packages/{identity}/download/{kind}')
    async def download(request:Request,identity:int,kind:str):
        package=store.get_package(identity)
        if kind=='zip':
            stream=io.BytesIO()
            with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('package.json',json.dumps(package,indent=2,ensure_ascii=True))
                archive.writestr('cover-letter.txt',package['letter'])
                archive.writestr('cover-letter.docx',export(package['letter'],'docx')[0])
                resume=store.resume(package['resume_id'])
                archive.writestr('resume'+Path(resume['filename']).suffix.lower(),resume['content'])
            content,mime=stream.getvalue(),'application/zip'
        else: content,mime=export(package['letter'],kind)
        return Response(content,media_type=mime,headers={'Content-Disposition':f'attachment; filename="application-{identity}.{kind}"'})

    return app


def serve(settings):
    import uvicorn
    app=create_app(job_database=settings.database,port=settings.port)
    url=f'http://127.0.0.1:{settings.port}'
    if not app.state.store.get_setting('password'): url+='/setup?token='+app.state.bootstrap
    print('Open your private local interface:',url,flush=True)
    uvicorn.run(app,host='127.0.0.1',port=settings.port,access_log=False,proxy_headers=False)
