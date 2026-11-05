"""Search orchestration: isolate board failures, persist observations, filter current results."""

from dataclasses import dataclass

from getmeajob.config import Settings
from getmeajob.filters import Match, evaluate
from getmeajob.models import Job
from getmeajob.sources import SourceError
from getmeajob.sources.ashby import AshbySource
from getmeajob.sources.greenhouse import GreenhouseSource
from getmeajob.sources.http import JsonClient
from getmeajob.sources.lever import LeverSource
from getmeajob.storage import JobStore


@dataclass(frozen=True)
class Result:
    job: Job
    match: Match
    record: dict


@dataclass(frozen=True)
class SearchReport:
    results: tuple[Result, ...]
    fetched: int
    successful_boards: int
    errors: tuple[str, ...]
    stored_jobs: int
    requests: int | None = None
    cached_sources: int = 0
    mode: str = "live"


def save_and_filter(jobs: list[Job], settings: Settings, store: JobStore, checked_at: dict | None = None) -> tuple[Result, ...]:
    for job in jobs:
        store.upsert(job, now=(checked_at or {}).get((job.source, job.board, job.source_id)))
    matches = {}
    for job in jobs:
        match = evaluate(job, settings.search, settings.places)
        if match.accepted:
            job_id = store.job_id(job)  # Resolve after all inserts, including identity merges.
            existing = matches.get(job_id)
            if existing is None or len(match.unknown_fields) < len(existing.match.unknown_fields):
                matches[job_id] = Result(job, match, store.details(job_id))
    return tuple(sorted(matches.values(), key=lambda item: (item.job.employer.casefold(), item.job.role.casefold(), item.record["id"])))


def search(settings: Settings, store: JobStore, adapters=None, progress=None) -> SearchReport:
    if adapters is None:
        client = JsonClient(interval=settings.request_interval_seconds)
        adapters = {"greenhouse": GreenhouseSource(client), "lever": LeverSource(client), "ashby": AshbySource(client)}
    jobs, errors = [], []
    successful = 0
    for board in settings.boards:
        if progress:
            progress(f"Checking {board.employer} ({board.source}/{board.token})...")
        client = getattr(adapters[board.source], "client", None)
        if client is not None and hasattr(client, "source"):
            client.source = f"{board.source}:{board.region}:{board.token}"
        try:
            found = adapters[board.source].fetch(board)
        except SourceError as exc:
            errors.append(f"{board.employer} ({board.source}/{board.token}): {exc}")
            continue
        successful += 1
        jobs.extend(found)
    results = save_and_filter(jobs, settings, store)
    return SearchReport(results, len(jobs), successful, tuple(errors), store.count())
