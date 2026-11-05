"""Authenticated loopback-only FastAPI interface. No submission or messaging routes."""
from dataclasses import asdict
import hashlib
import io
import json
from pathlib import Path
import secrets
import time
import zipfile

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from jinja2 import Environment, FileSystemLoader, select_autoescape

from getmeajob.resume.store import ProfileStore, CATEGORIES
from getmeajob.resume.extract import extract, candidates, MAX_FILE
from getmeajob.resume.intelligence import jobs, match, letter, weights, DEFAULT_WEIGHTS
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

    def page(request,title,**context):
        return HTMLResponse(templates.get_template('dashboard.html').render(title=title,csrf=request.state.session['csrf'],categories=CATEGORIES,**context))

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
                try: form=await request.form(max_files=1,max_fields=30,max_part_size=MAX_FILE)
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
            'Referrer-Policy':'no-referrer','Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"})
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
        identity=store.upload(str(form.get('label') or filename),filename,data,text,candidates(text))
        return RedirectResponse(f'/resumes/{identity}',303)

    @app.get('/resumes/{identity}')
    async def resume_page(request:Request,identity:int):
        return page(request,'Review your profile facts',screen='resume',resume=store.resume(identity),facts=store.facts(identity))

    @app.post('/resumes/{identity}/facts')
    async def save_fact(request:Request,identity:int):
        form=await request.form()
        store.fact(identity,str(form.get('category')),str(form.get('value','')),str(form.get('status')),int(form['fact_id']) if form.get('fact_id') else None)
        return RedirectResponse(f'/resumes/{identity}',303)

    @app.post('/resumes/{identity}/default')
    async def default_resume(request:Request,identity:int):
        store.default(identity); return RedirectResponse('/',303)

    @app.get('/settings')
    async def scoring(request:Request): return page(request,'Matching weights',screen='settings',weights=json.loads(store.get_setting('weights',json.dumps(DEFAULT_WEIGHTS))))

    @app.post('/settings')
    async def save_scoring(request:Request):
        form=await request.form(); configuration=weights({k:float(form[k]) for k in DEFAULT_WEIGHTS})
        store.set_setting('weights',json.dumps(configuration)); return RedirectResponse('/jobs',303)

    def ranked_jobs(query='',offset=0):
        configuration=json.loads(store.get_setting('weights',json.dumps(DEFAULT_WEIGHTS)))
        resumes=store.resumes(); results=[]
        profile_facts={r['id']:store.facts(r['id']) for r in resumes}
        for identity,job,checked in jobs(job_database):
            if query and query.casefold() not in (job.role+' '+job.employer).casefold(): continue
            scores=[(r,match(job,profile_facts[r['id']],configuration)) for r in resumes]
            if not scores: continue
            r,m=max(scores,key=lambda item:(item[1]['score'],item[0]['is_default']))
            results.append({'id':identity,'job':job,'checked':checked,'resume':r,'match':m})
        return sorted(results,key=lambda item:-item['match']['score'])

    @app.get('/jobs')
    async def matching_jobs(request:Request,q:str='',offset:int=0):
        results=ranked_jobs(q)
        offset=max(0,offset)
        return page(request,'Matching stored jobs',screen='jobs',results=results[offset:offset+30],q=q,offset=offset,total=len(results))

    def get_job(identity):
        for key,job,checked in jobs(job_database):
            if key==identity: return job,checked
        raise ValueError('Job not found.')

    @app.post('/packages')
    async def generate(request:Request):
        form=await request.form(); job_id=int(form['job_id']); resume_id=int(form['resume_id'])
        job,checked=get_job(job_id); resume=store.resume(resume_id); facts=store.facts(resume_id)
        configuration=json.loads(store.get_setting('weights',json.dumps(DEFAULT_WEIGHTS)))
        text,ids,mode=letter(job,facts,str(form.get('model','')) or None)
        payload={'employer':job.employer,'position':job.role,'original_url':job.original_url or job.application_url,
            'job':job.to_dict(),'job_last_checked':checked,'resume':{k:v for k,v in resume.items() if k!='content'},
            'match':match(job,facts,configuration),'verified_facts':[f for f in facts if f['status']=='verified'],
            'letter_fact_ids':ids,'generation_mode':mode,'created':utc_now(),
            'missing_information':['Applicant name/signature and contact details need review.','Application questions, eligibility and current vacancy status require review.'],
            'submission_authorized':False}
        identity=store.package(job_id,resume_id,payload,text)
        return RedirectResponse(f'/packages/{identity}',303)

    @app.get('/packages/{identity}')
    async def package_page(request:Request,identity:int):
        return page(request,'Review application package',screen='package',package=store.get_package(identity))

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
