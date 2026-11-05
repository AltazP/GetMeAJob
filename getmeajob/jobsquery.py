"""Filter, score, sort and paginate stored jobs for the dashboard.

Order of work keeps the page fast: cheap metadata filters run first; resume matching only
runs for jobs that survive them, and only when the user asked for a minimum score or
highest-match order. Derived scores are cached in the private profile database and are
invalidated by any change to the job payload, verified facts or scoring algorithm/weights.
The jobs database is opened read-only and is never written.
"""
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
from pathlib import Path

from getmeajob.canada import in_canada
from getmeajob.classify import classify_field, effective_level
from getmeajob.filters import evaluate
from getmeajob.jobfilters import JobFilters, province_matches, search_filters
from getmeajob.models import Job
from getmeajob.resume.intelligence import algorithm_hash, facts_hash, job_features, prepare, qualification_facts
from getmeajob.resume.vocabulary import VOCABULARY_HASH
import time

PAGE = 20
MAX_JOBS = 50000          # upper bound on stored jobs read per request
SCORE_SECONDS = 6.0       # time spent computing uncached scores per request; the rest finish on reload
LATEST = '''WITH ranked AS (
    SELECT job_id,payload,last_checked,
        (SELECT first_discovered FROM jobs WHERE jobs.id=observations.job_id) AS first_seen,
        ROW_NUMBER() OVER (PARTITION BY job_id ORDER BY last_checked DESC,rowid DESC) AS place
    FROM observations
) SELECT job_id,payload,last_checked,first_seen FROM ranked WHERE place=1 ORDER BY job_id LIMIT ?'''


@dataclass
class Row:
    id: int
    job: Job
    checked: str
    field: str = "Other"
    field_reason: str = ""
    level: str | None = None
    level_reason: str = "Unknown"
    score: int | None = None
    payload_hash: str = ""
    posted: datetime | None = None
    is_new: bool = False
    first_seen: str | None = None


@dataclass
class Result:
    rows: list[Row]
    total: int
    notices: list[str] = field(default_factory=list)
    scored: bool = False
    matches: dict = field(default_factory=dict)   # (job_id, resume_id) -> match dict for page rows
    best: dict = field(default_factory=dict)      # job_id -> resume dict recommended for that job
    stored: int = 0
    employers: int = 0
    sources: dict = field(default_factory=dict)   # source name -> stored job count
    newest_check: str | None = None
    dismissed_count: int = 0
    new_count: int = 0


def read_latest(path: Path):
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
    try:
        db.execute('PRAGMA query_only=ON')
        return db.execute(LATEST, (MAX_JOBS,)).fetchall()
    finally:
        db.close()


def sort_key(sort, row):
    unknown_posting = row.posted is None
    stamp = 0.0 if unknown_posting else -row.posted.timestamp()
    if sort == "company":
        return (row.job.employer.casefold(), row.job.role.casefold(), -row.id)
    if sort == "title":
        return (row.job.role.casefold(), row.job.employer.casefold(), -row.id)
    if sort == "match":
        return (-(row.score if row.score is not None else -1), _neg(row.checked), -row.id)
    # Most recent: known posting dates first (newest first); unknown dates after, by last checked.
    return (unknown_posting, stamp, _neg(row.checked), -row.id)


def _neg(text):
    """Sortable inverse of an ISO timestamp string (descending order)."""
    try:
        return -datetime.fromisoformat(text).timestamp()
    except (TypeError, ValueError):
        return 0.0


def run(job_db: Path, store, filters: JobFilters, weights: dict, scorer, offset: int = 0, now=None,
        baseline: str | None = None, view_dismissed: bool = False) -> Result:
    now = now or datetime.now(timezone.utc)
    if not job_db.exists():
        return Result([], 0)
    criteria = search_filters(filters)
    query = filters.q.casefold()
    rows: list[Row] = []
    hidden = store.dismissed()
    employers, sources, newest = set(), {}, None
    stored = 0
    for identity, payload, checked, first_seen in read_latest(job_db):
        job = Job.from_dict(json.loads(payload))
        stored += 1
        employers.add(job.employer.casefold()); sources[job.source] = sources.get(job.source, 0) + 1
        newest = max(newest, checked) if newest else checked
        if (identity in hidden) != view_dismissed:
            continue
        if query and query not in f"{job.role} {job.employer}".casefold():
            continue
        if filters.canada_only:
            # Only explicit Canadian evidence counts; a job with no location at all is Unknown.
            canadian = in_canada(job.locations)
            if canadian is False or (canadian is None and not filters.include_unknown):
                continue
        group, why = ("Other", "")
        if filters.fields:   # classification is only needed up front when filtering by field
            group, why = classify_field(job)
            if group not in filters.fields:
                continue
        level, level_why = effective_level(job)
        # The shared evaluate() sees the level that is explicit in the source or the title.
        probe = replace(job, experience_level=level) if level else job
        verdict = evaluate(probe, criteria, now=now)
        if not verdict.accepted:
            continue
        # A location we have text for but cannot place on the offline city list is not "missing":
        # it only passes a distance filter when the user opted in to unplaced locations.
        if filters.city and "location distance" in verdict.unknown_fields and job.locations and not filters.include_unplaced:
            continue
        if filters.province and not filters.city:
            if job.locations and not province_matches(job.locations, filters.province):
                continue
            if not job.locations and not filters.include_unknown:
                continue
        posted = None
        if job.posted_at:
            try: posted = datetime.fromisoformat(job.posted_at)
            except ValueError: posted = None
        rows.append(Row(identity, job, checked, group, why, level.title() if level else None, level_why,
                        payload_hash=hashlib.sha256(payload.encode()).hexdigest()[:24], posted=posted,
                        first_seen=first_seen, is_new=bool(baseline and first_seen and first_seen > baseline)))

    result = Result([], 0, stored=stored, employers=len(employers), sources=dict(sorted(sources.items())), newest_check=newest,
                    dismissed_count=len(hidden), new_count=sum(r.is_new for r in rows))
    resumes = [r for r in store.resumes()]
    facts = {r['id']: store.facts(r['id']) for r in resumes}
    eligible = [r for r in resumes if qualification_facts(facts[r['id']])]
    need_scores = filters.minimum_score > 0 or filters.sort == "match"
    if need_scores and not eligible:
        result.notices.append("No resume has verified qualification facts yet, so match scores are unavailable; "
                              "the minimum-match filter and match sorting were not applied.")
        need_scores = False
    algo = algorithm_hash(weights)
    prepared = {r['id']: prepare(facts[r['id']]) for r in resumes}
    fact_digest = {r['id']: facts_hash(facts[r['id']]) for r in resumes}
    usable = {r['id'] for r in eligible}
    stored_features, new_features = None, []

    def features_for(row):
        nonlocal stored_features
        if stored_features is None:
            stored_features = store.feature_rows()
        entry = stored_features.get(row.id)
        if entry and entry[:2] == (row.payload_hash, VOCABULARY_HASH):
            return json.loads(entry[2])
        found = job_features(row.job)
        new_features.append((row.id, row.payload_hash, VOCABULARY_HASH, json.dumps(found)))
        return found

    if need_scores:
        result.scored = True
        cached = store.cache_rows([r['id'] for r in eligible])
        fresh, deadline = [], time.monotonic() + SCORE_SECONDS
        for row in rows:
            best = None
            for r in eligible:
                entry = cached.get((row.id, r['id']))
                if entry and entry[:3] == (row.payload_hash, fact_digest[r['id']], algo):
                    value = entry[3]
                elif time.monotonic() < deadline:
                    m = scorer(row.job, facts[r['id']], weights, prepared[r['id']], features_for(row))
                    value = m['score']
                    fresh.append((row.id, r['id'], row.payload_hash, fact_digest[r['id']], algo, value, json.dumps(m)))
                else:
                    result.notices.append("Scores are still being calculated for part of the list (first-time scoring of a large job list takes a few reloads). Reload to continue; "
                                          "unscored jobs are omitted by minimum-match filters and sorted last.")
                    value = None
                if value is not None and (best is None or (value, r['is_default']) > best[:2]):
                    best = (value, r['is_default'])
            row.score = best[0] if best else None
        store.cache_save(fresh)
        store.feature_save(new_features); new_features.clear()
        result.notices = list(dict.fromkeys(result.notices))
        if filters.minimum_score:
            rows = [row for row in rows if row.score is not None and row.score >= filters.minimum_score]
    rows.sort(key=lambda row: sort_key(filters.sort, row))
    result.total = len(rows)
    page = rows[offset:offset + PAGE]
    result.rows = page
    for row in page:
        if not row.field_reason:
            row.field, row.field_reason = classify_field(row.job)

    # Full explanations for just the visible page, from cache when still valid.
    if page and resumes:
        cached = store.cache_rows([r['id'] for r in resumes], with_result=True)
        fresh = []
        for row in page:
            options = []
            for r in resumes:
                entry = cached.get((row.id, r['id']))
                if entry and entry[:3] == (row.payload_hash, fact_digest[r['id']], algo):
                    m = json.loads(entry[4])
                else:
                    m = scorer(row.job, facts[r['id']], weights, prepared[r['id']], job_features(row.job))
                    fresh.append((row.id, r['id'], row.payload_hash, fact_digest[r['id']], algo, m['score'], json.dumps(m)))
                result.matches[(row.id, r['id'])] = m
                options.append((m['score'], r['is_default'], r))
            top = max(options, key=lambda item: (item[2]['id'] in usable, *item[:2]))
            row.score = top[0] if top[2]['id'] in usable else None
            result.best[row.id] = top[2]
        store.cache_save(fresh)
    return result
