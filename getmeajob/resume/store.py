"""Separate, private, versioned SQLite store. Discovery databases remain unchanged."""
import json
import os
from pathlib import Path
import sqlite3
from getmeajob.models import utc_now

CATEGORIES = ('personal', 'other', 'education', 'university', 'program', 'coursework', 'employment', 'skills', 'tools', 'projects', 'certifications', 'achievements')

class ProfileStore:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory, 0o700)
        self.directory = directory
        self.path = directory / 'profiles.sqlite3'
        with self.connect() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version > 2:
                raise ValueError('Private database is newer than this application.')
            db.executescript('''
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS resumes(id INTEGER PRIMARY KEY,label TEXT NOT NULL,filename TEXT NOT NULL,
                content BLOB NOT NULL,text TEXT NOT NULL,created TEXT NOT NULL,is_default INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS facts(id INTEGER PRIMARY KEY,resume_id INTEGER NOT NULL REFERENCES resumes(id),
                category TEXT NOT NULL,value TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',evidence TEXT NOT NULL,
                updated TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS fact_history(id INTEGER PRIMARY KEY,fact_id INTEGER NOT NULL,
                value TEXT NOT NULL,status TEXT NOT NULL,updated TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS packages(id INTEGER PRIMARY KEY,job_id INTEGER NOT NULL,resume_id INTEGER NOT NULL,
                version INTEGER NOT NULL,payload TEXT NOT NULL,letter TEXT NOT NULL,status TEXT NOT NULL,
                created TEXT NOT NULL,UNIQUE(job_id,resume_id,version));

            ''')
            if version < 2:
                try:
                    db.executescript("""BEGIN IMMEDIATE;
                        ALTER TABLE resumes ADD COLUMN parser_version INTEGER NOT NULL DEFAULT 1;
                        ALTER TABLE resumes ADD COLUMN parent_resume_id INTEGER REFERENCES resumes(id);
                        ALTER TABLE facts ADD COLUMN structure TEXT;
                        ALTER TABLE facts ADD COLUMN warnings TEXT NOT NULL DEFAULT '[]';
                        PRAGMA user_version=2; COMMIT;""")
                except Exception:
                    db.rollback(); raise
        os.chmod(self.path, 0o600)

    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        return db

    def get_setting(self, key, default=None):
        with self.connect() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
        return row[0] if row else default

    def set_setting(self,key,value):
        with self.connect() as db:
            db.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,value))

    def resumes(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT id,label,filename,created,is_default FROM resumes ORDER BY id DESC')]

    def resume(self, identity):
        with self.connect() as db:
            row=db.execute('SELECT * FROM resumes WHERE id=?',(identity,)).fetchone()
        if not row: raise ValueError('Resume not found.')
        return dict(row)

    def upload(self,label,filename,content,text,candidates,parent_resume_id=None):
        with self.connect() as db:
            default = int(db.execute('SELECT COUNT(*) FROM resumes').fetchone()[0] == 0)
            identity=db.execute('INSERT INTO resumes(label,filename,content,text,created,is_default) VALUES(?,?,?,?,?,?)',
                (label[:120],filename,content,text,utc_now(),default)).lastrowid
            rich=all(isinstance(item,dict) for item in candidates)
            db.execute('UPDATE resumes SET parser_version=?,parent_resume_id=? WHERE id=?',(2 if rich else 1,parent_resume_id,identity))
            for item in candidates:
                if isinstance(item,dict):
                    category,value=item['category'],item['value']; evidence=item['evidence']
                    structure=item.get('structure'); warnings=item.get('warnings',[])
                else:
                    category,value=item; evidence=value; structure=None; warnings=[]
                db.execute('INSERT INTO facts(resume_id,category,value,evidence,updated,structure,warnings) VALUES(?,?,?,?,?,?,?)',
                    (identity,category,value,evidence,utc_now(),json.dumps(structure) if structure else None,json.dumps(warnings)))
        return identity

    def facts(self,identity,verified=False):
        with self.connect() as db:
            rows=db.execute('SELECT * FROM facts WHERE resume_id=? ORDER BY id',(identity,)).fetchall()
        return [{**dict(row),'structure':json.loads(row['structure']) if row['structure'] else None,'warnings':json.loads(row['warnings'])} for row in rows if not verified or row['status']=='verified']

    def fact(self,identity,category,value,status,fact_id=None):
        self.resume(identity)
        if category not in CATEGORIES or status not in ('pending','verified','rejected') or not value.strip() or len(value)>20000:
            raise ValueError('Choose a category, status and a fact of 1–20000 characters.')
        with self.connect() as db:
            if fact_id:
                old=db.execute('SELECT * FROM facts WHERE id=? AND resume_id=?',(fact_id,identity)).fetchone()
                if not old: raise ValueError('Fact not found.')
                db.execute('INSERT INTO fact_history(fact_id,value,status,updated) VALUES(?,?,?,?)',
                    (fact_id,old['value'],old['status'],old['updated']))
                db.execute('UPDATE facts SET category=?,value=?,status=?,updated=?,structure=NULL WHERE id=?',
                    (category,value.strip(),status,utc_now(),fact_id))
            else:
                db.execute('INSERT INTO facts(resume_id,category,value,status,evidence,updated) VALUES(?,?,?,?,?,?)',
                    (identity,category,value.strip(),status,'User supplied: '+value.strip(),utc_now()))

    def default(self,identity):
        self.resume(identity)
        with self.connect() as db:
            db.execute('UPDATE resumes SET is_default=0')
            db.execute('UPDATE resumes SET is_default=1 WHERE id=?',(identity,))

    def package(self,job_id,resume_id,payload,letter,status='draft'):
        if status not in ('draft','reviewed'): raise ValueError('Invalid review state.')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            version=db.execute('SELECT COALESCE(MAX(version),0)+1 FROM packages WHERE job_id=? AND resume_id=?',(job_id,resume_id)).fetchone()[0]
            return db.execute('INSERT INTO packages(job_id,resume_id,version,payload,letter,status,created) VALUES(?,?,?,?,?,?,?)',
                (job_id,resume_id,version,json.dumps(payload,ensure_ascii=True),letter,status,utc_now())).lastrowid

    def packages(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT id,job_id,resume_id,version,status,created FROM packages ORDER BY id DESC')]

    def get_package(self,identity):
        with self.connect() as db: row=db.execute('SELECT * FROM packages WHERE id=?',(identity,)).fetchone()
        if not row: raise ValueError('Package not found.')
        result=dict(row); result['payload']=json.loads(result['payload']); return result


    def revision(self,identity):
        import hashlib
        return hashlib.sha256(json.dumps(self.facts(identity),sort_keys=True).encode()).hexdigest()

    def review(self,identity,updates,revision,confirm=False):
        """Atomic scoped review; stale forms never verify newly changed data."""
        from getmeajob.resume.parser import render_position
        self.resume(identity)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if self.revision(identity)!=revision: raise ValueError('Profile changed. Reload and review again.')
            current={row['id']:row for row in self.facts(identity)}
            for item in updates:
                old=current.get(item['id'])
                if not old: raise ValueError('Fact does not belong to this resume.')
                category=item.get('category',old['category']); status=item.get('status',old['status'])
                structure=item.get('structure',old['structure'])
                value=render_position(structure) if structure is not None else item.get('value',old['value']).strip()
                if category not in CATEGORIES or status not in ('pending','verified','rejected') or not value or len(value)>20000:
                    raise ValueError('Invalid review data.')
                if structure is not None:
                    if category!='employment' or set(structure)!={'title','employer','location','start_date','end_date','header_unassigned','achievements'}:
                        raise ValueError('Invalid employment record.')
                    if any(not isinstance(v,str) for k,v in structure.items() if k!='achievements') or not isinstance(structure['achievements'],list) or any(not isinstance(v,str) for v in structure['achievements']):
                        raise ValueError('Invalid employment fields.')
                changed=value!=old['value'] or category!=old['category'] or structure!=old['structure']
                if status=='verified' and (old['status']!='verified' or changed) and not confirm:
                    raise ValueError('Explicit review confirmation is required.')
                if changed or status!=old['status']:
                    db.execute('INSERT INTO fact_history(fact_id,value,status,updated) VALUES(?,?,?,?)',(old['id'],old['value'],old['status'],old['updated']))
                    db.execute('UPDATE facts SET category=?,value=?,status=?,structure=?,updated=? WHERE id=?',
                        (category,value,status,json.dumps(structure) if structure else None,utc_now(),old['id']))

    def reprocess(self,identity,parsed,confirmed=False):
        if not confirmed: raise ValueError('Confirm creation of a new pending resume version.')
        original=self.resume(identity)
        return self.upload(original['label']+' (reprocessed)',original['filename'],original['content'],original['text'],parsed,parent_resume_id=identity)
