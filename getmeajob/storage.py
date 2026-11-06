"""SQLite job identities and per-source observations, with transactional deduplication."""

import hashlib
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from getmeajob.locations import normalized
from getmeajob.models import Job, utc_now
from getmeajob.migrations import VERSION, migrate


def canonical_url(url: str) -> str:
    parsed = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
             if not key.casefold().startswith("utm_") and key.casefold() not in {"gh_src", "lever-source", "lever-origin"}]
    path = parsed.path.rstrip("/")
    if parsed.hostname in {"jobs.lever.co", "jobs.eu.lever.co"} and path.endswith("/apply"):
        path = path[:-6]
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, urlencode(sorted(query)), ""))


ATS_URLS = (
    ("greenhouse", re.compile(r"^https://(?:job-)?boards\.greenhouse\.io/(?:embed/job_app\?for=([^&/]+)&token=(\d+)|([^/?]+)/jobs/(\d+))")),
    ("lever", re.compile(r"^https://jobs\.(?:eu\.)?lever\.co/([^/?]+)/([0-9a-fA-F-]{36})")),
    ("ashby", re.compile(r"^https://jobs\.ashbyhq\.com/([^/?]+)/([0-9a-fA-F-]{36})")),
)


def ats_key(job: Job) -> str | None:
    """Same Greenhouse/Lever/Ashby posting, wherever it was listed (API, community list, company page)."""
    if job.source in {"greenhouse", "lever", "ashby"}:
        return f"ats:{job.source}:{job.board.split(':')[-1].casefold()}:{job.source_id.casefold()}"
    for provider, pattern in ATS_URLS:
        found = pattern.match(job.application_url)
        if found:
            groups = [g for g in found.groups() if g]
            return f"ats:{provider}:{groups[0].casefold()}:{groups[1].casefold()}"
    return None


def identity_keys(job: Job) -> list[str]:
    keys = ["url:" + canonical_url(job.application_url)]
    if ats_key(job):
        keys.append(ats_key(job))
    if job.original_url:
        keys.append("url:" + canonical_url(job.original_url))
    # Exact content identity only; equal titles alone can be different requisitions.
    if job.description.strip() and job.locations:
        content = [normalized(job.employer), normalized(job.role),
                   sorted(normalized(item) for item in job.locations), " ".join(job.description.casefold().split())]
        keys.append("content:" + hashlib.sha256(json.dumps(content, ensure_ascii=True).encode()).hexdigest())
    return keys


class JobStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        if self.connection.execute("PRAGMA user_version").fetchone()[0] > VERSION:
            self.connection.close()
            raise sqlite3.DatabaseError("Database schema is newer than this application; refusing to downgrade.")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY,
                first_discovered TEXT NOT NULL,
                last_checked TEXT NOT NULL,
                last_updated TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS identities (
                key TEXT PRIMARY KEY,
                job_id INTEGER NOT NULL REFERENCES jobs(id)
            );
            CREATE TABLE IF NOT EXISTS observations (
                source TEXT NOT NULL, board TEXT NOT NULL, source_id TEXT NOT NULL,
                job_id INTEGER NOT NULL REFERENCES jobs(id),
                payload TEXT NOT NULL,
                first_discovered TEXT NOT NULL, last_checked TEXT NOT NULL, last_updated TEXT NOT NULL,
                PRIMARY KEY (source, board, source_id)
            );
            CREATE INDEX IF NOT EXISTS observations_job ON observations(job_id);
        """)

        migrate(self.connection)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def upsert(self, job: Job, now: str | None = None) -> int:
        now = now or utc_now()
        payload = json.dumps(job.to_dict(), sort_keys=True, ensure_ascii=True)
        keys = identity_keys(job)
        connection = self.connection
        with connection:
            previous = connection.execute(
                "SELECT * FROM observations WHERE source=? AND board=? AND source_id=?",
                (job.source, job.board, job.source_id),
            ).fetchone()
            if previous and now < previous["last_checked"]:
                return previous["job_id"]
            ids = {row[0] for key in keys for row in connection.execute("SELECT job_id FROM identities WHERE key=?", (key,))}
            if previous:
                ids.add(previous["job_id"])
            if ids:
                job_id = min(ids)
                for other in ids - {job_id}:
                    connection.execute("""UPDATE jobs SET
                        first_discovered=MIN(first_discovered, (SELECT first_discovered FROM jobs WHERE id=?)),
                        last_checked=MAX(last_checked, (SELECT last_checked FROM jobs WHERE id=?)),
                        last_updated=MAX(last_updated, (SELECT last_updated FROM jobs WHERE id=?)) WHERE id=?""",
                        (other, other, other, job_id))
                    connection.execute("UPDATE identities SET job_id=? WHERE job_id=?", (job_id, other))
                    connection.execute("UPDATE observations SET job_id=? WHERE job_id=?", (job_id, other))
                    connection.execute("DELETE FROM jobs WHERE id=?", (other,))
                connection.execute("UPDATE jobs SET last_checked=MAX(last_checked,?) WHERE id=?", (now, job_id))
                if previous is None or previous["payload"] != payload:
                    connection.execute("UPDATE jobs SET last_updated=? WHERE id=?", (now, job_id))
            else:
                job_id = connection.execute("INSERT INTO jobs(first_discovered,last_checked,last_updated) VALUES(?,?,?)",
                                            (now, now, now)).lastrowid
            for key in keys:
                connection.execute("INSERT OR IGNORE INTO identities(key,job_id) VALUES(?,?)", (key, job_id))
            connection.execute("""INSERT INTO observations VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(source,board,source_id) DO UPDATE SET job_id=excluded.job_id,
                payload=excluded.payload,last_checked=excluded.last_checked,
                last_updated=CASE WHEN observations.payload != excluded.payload THEN excluded.last_updated
                                  ELSE observations.last_updated END""",
                (job.source, job.board, job.source_id, job_id, payload, now, now, now))
        return job_id

    def job_id(self, job: Job) -> int:
        return self.connection.execute("SELECT job_id FROM observations WHERE source=? AND board=? AND source_id=?",
                                       (job.source, job.board, job.source_id)).fetchone()[0]

    def details(self, job_id: int) -> dict:
        record = dict(self.connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
        record["sources"] = [dict(row) for row in self.connection.execute(
            "SELECT source,board,source_id FROM observations WHERE job_id=? ORDER BY source,board,source_id", (job_id,))]
        return record

    def count(self) -> int:
        return self.connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
