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
            if version > 6:
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
            if version < 3:
                # Derived, rebuildable match results; never the only copy of any user record.
                try:
                    db.executescript("""BEGIN IMMEDIATE;
                        CREATE TABLE IF NOT EXISTS match_cache(job_id INTEGER NOT NULL,resume_id INTEGER NOT NULL,
                            job_hash TEXT NOT NULL,facts_hash TEXT NOT NULL,algo_hash TEXT NOT NULL,
                            score INTEGER NOT NULL,result TEXT NOT NULL,PRIMARY KEY(job_id,resume_id));
                        CREATE INDEX IF NOT EXISTS match_cache_resume ON match_cache(resume_id);
                        CREATE TABLE IF NOT EXISTS job_features(job_id INTEGER PRIMARY KEY,job_hash TEXT NOT NULL,vocab_hash TEXT NOT NULL,features TEXT NOT NULL);
                        CREATE TABLE IF NOT EXISTS dismissed_jobs(job_id INTEGER PRIMARY KEY,dismissed TEXT NOT NULL);
                        PRAGMA user_version=3; COMMIT;""")
                except Exception:
                    db.rollback(); raise
            if version < 4:
                # Email-alert presets and a log of which postings each preset already emailed.
                try:
                    db.executescript("""BEGIN IMMEDIATE;
                        CREATE TABLE IF NOT EXISTS notify_presets(id INTEGER PRIMARY KEY,name TEXT NOT NULL UNIQUE,filters TEXT NOT NULL,
                            enabled INTEGER NOT NULL DEFAULT 1,created TEXT NOT NULL,last_run TEXT);
                        CREATE TABLE IF NOT EXISTS notify_sent(preset_id INTEGER NOT NULL REFERENCES notify_presets(id) ON DELETE CASCADE,
                            job_id INTEGER NOT NULL,sent TEXT NOT NULL,PRIMARY KEY(preset_id,job_id));
                        PRAGMA user_version=4; COMMIT;""")
                except Exception:
                    db.rollback(); raise
            if version < 5:
                # Record of assisted form openings; the person submits, we only note what was filled.
                try:
                    db.executescript("""BEGIN IMMEDIATE;
                        CREATE TABLE IF NOT EXISTS applications(id INTEGER PRIMARY KEY,package_id INTEGER NOT NULL REFERENCES packages(id),
                            job_id INTEGER NOT NULL,status TEXT NOT NULL,filled TEXT NOT NULL,needs TEXT NOT NULL,updated TEXT NOT NULL);
                        CREATE INDEX IF NOT EXISTS applications_package ON applications(package_id);
                        PRAGMA user_version=5; COMMIT;""")
                except Exception:
                    db.rollback(); raise
            if version < 6:
                # The user's own cover letters (text only), used as starting points that get tailored per job.
                try:
                    db.executescript("""BEGIN IMMEDIATE;
                        CREATE TABLE IF NOT EXISTS cover_letters(id INTEGER PRIMARY KEY,label TEXT NOT NULL,filename TEXT NOT NULL,
                            text TEXT NOT NULL,created TEXT NOT NULL);
                        PRAGMA user_version=6; COMMIT;""")
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

    def add_application(self,package_id,job_id,status,filled=(),needs=()):
        if status not in ('form_opened','applied_by_user'): raise ValueError('Invalid application status.')
        with self.connect() as db:
            return db.execute('INSERT INTO applications(package_id,job_id,status,filled,needs,updated) VALUES(?,?,?,?,?,?)',
                (package_id,job_id,status,json.dumps(list(filled)),json.dumps(list(needs)),utc_now())).lastrowid

    def applications(self,package_id):
        with self.connect() as db:
            rows=db.execute('SELECT * FROM applications WHERE package_id=? ORDER BY id DESC',(package_id,)).fetchall()
        return [{**dict(r),'filled':json.loads(r['filled']),'needs':json.loads(r['needs'])} for r in rows]

    MAX_COVER_LETTERS=10

    def cover_letters(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT id,label,filename,text,created FROM cover_letters ORDER BY id DESC')]

    def cover_letter(self,identity):
        with self.connect() as db: row=db.execute('SELECT id,label,filename,text,created FROM cover_letters WHERE id=?',(identity,)).fetchone()
        if not row: raise ValueError('Cover letter not found.')
        return dict(row)

    def add_cover_letter(self,label,filename,text):
        label=' '.join(label.split())[:80] or 'My cover letter'; text=text.strip()
        if not 40<=len(text)<=20000: raise ValueError('A cover letter must be between 40 and 20,000 characters.')
        with self.connect() as db:
            if db.execute('SELECT COUNT(*) FROM cover_letters').fetchone()[0]>=self.MAX_COVER_LETTERS: raise ValueError(f'At most {self.MAX_COVER_LETTERS} saved cover letters; delete one first.')
            return db.execute('INSERT INTO cover_letters(label,filename,text,created) VALUES(?,?,?,?)',(label,filename[:120],text,utc_now())).lastrowid

    def delete_cover_letter(self,identity):
        with self.connect() as db: db.execute('DELETE FROM cover_letters WHERE id=?',(identity,))

    MAX_PRESETS=10

    def presets(self):
        with self.connect() as db:
            return [dict(r)|{'sent':db.execute('SELECT COUNT(*) FROM notify_sent WHERE preset_id=?',(r['id'],)).fetchone()[0]}
                    for r in db.execute('SELECT id,name,filters,enabled,created,last_run FROM notify_presets ORDER BY id')]

    def add_preset(self,name,filters_json):
        name=' '.join(name.split())
        if not 1<=len(name)<=60: raise ValueError('Use a preset name of 1-60 characters.')
        with self.connect() as db:
            if db.execute('SELECT COUNT(*) FROM notify_presets').fetchone()[0]>=self.MAX_PRESETS: raise ValueError(f'At most {self.MAX_PRESETS} presets.')
            try: return db.execute('INSERT INTO notify_presets(name,filters,created) VALUES(?,?,?)',(name,filters_json,utc_now())).lastrowid
            except sqlite3.IntegrityError: raise ValueError('A preset with that name already exists.') from None

    def delete_preset(self,identity):
        with self.connect() as db: db.execute('DELETE FROM notify_presets WHERE id=?',(identity,))

    def set_preset_enabled(self,identity,enabled):
        with self.connect() as db: db.execute('UPDATE notify_presets SET enabled=? WHERE id=?',(int(enabled),identity))

    def sent_ids(self,preset_id):
        with self.connect() as db: return {r[0] for r in db.execute('SELECT job_id FROM notify_sent WHERE preset_id=?',(preset_id,))}

    def mark_sent(self,preset_id,job_ids):
        with self.connect() as db:
            db.executemany('INSERT OR IGNORE INTO notify_sent VALUES(?,?,?)',[(preset_id,j,utc_now()) for j in job_ids])
            db.execute('UPDATE notify_presets SET last_run=? WHERE id=?',(utc_now(),preset_id))

    def dismissed(self):
        with self.connect() as db:
            return {r[0] for r in db.execute('SELECT job_id FROM dismissed_jobs')}

    def dismiss(self,job_id,hide=True):
        """Hide or restore a job in this app only; the job database is never touched."""
        with self.connect() as db:
            if hide: db.execute('INSERT OR REPLACE INTO dismissed_jobs VALUES(?,?)',(job_id,utc_now()))
            else: db.execute('DELETE FROM dismissed_jobs WHERE job_id=?',(job_id,))

    def restore_all_dismissed(self):
        with self.connect() as db: db.execute('DELETE FROM dismissed_jobs')

    def delete_setting(self,key):
        with self.connect() as db: db.execute('DELETE FROM settings WHERE key=?',(key,))

    def cache_rows(self,resume_ids,with_result=False):
        """{(job_id,resume_id): (job_hash,facts_hash,algo_hash,score[,result])} for the given resumes."""
        if not resume_ids: return {}
        marks=','.join('?'*len(resume_ids))
        columns='job_id,resume_id,job_hash,facts_hash,algo_hash,score'+(',result' if with_result else '')
        with self.connect() as db:
            rows=db.execute(f'SELECT {columns} FROM match_cache WHERE resume_id IN ({marks})',tuple(resume_ids)).fetchall()
        return {(r[0],r[1]):tuple(r[2:]) for r in rows}

    def feature_rows(self):
        with self.connect() as db:
            return {r[0]:(r[1],r[2],r[3]) for r in db.execute('SELECT job_id,job_hash,vocab_hash,features FROM job_features')}

    def feature_save(self,entries):
        if not entries: return
        with self.connect() as db:
            db.executemany('INSERT OR REPLACE INTO job_features VALUES(?,?,?,?)',entries)

    def cache_save(self,entries):
        """entries: (job_id,resume_id,job_hash,facts_hash,algo_hash,score,result_json). One transaction."""
        if not entries: return
        with self.connect() as db:
            db.executemany('INSERT OR REPLACE INTO match_cache VALUES(?,?,?,?,?,?,?)',entries)

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
