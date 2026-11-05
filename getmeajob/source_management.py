"""Official endpoint verification, cached refreshes, source health and real coverage."""

from dataclasses import replace
from datetime import datetime, timezone
import sqlite3
from pathlib import Path

from getmeajob.discovery import SearchReport, save_and_filter
from getmeajob.models import utc_now
from getmeajob.registry import Registry, elapsed
from getmeajob.sources import SourceError
from getmeajob.sources.ashby import AshbySource
from getmeajob.sources.greenhouse import GreenhouseSource
from getmeajob.sources.http import BudgetExceeded, JsonClient
from getmeajob.sources.lever import LeverSource
from getmeajob.storage import JobStore
from getmeajob.urls import PROVIDERS


def snapshot_store(path: Path) -> JobStore:
    """Offline/dry-run uses an in-memory clone, never creates or migrates a disk DB."""
    store = JobStore(Path(":memory:"))
    if path.is_file():
        original = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            original.backup(store.connection)
        finally:
            original.close()
        from getmeajob.migrations import migrate
        migrate(store.connection)
    return store


def client_for(settings, registry: Registry, offline: bool = False) -> JsonClient:
    options = settings.discovery
    return JsonClient(interval=settings.request_interval_seconds, timeout=options.timeout_seconds,
                      max_requests=options.max_requests, per_host_limit=options.per_host_requests,
                      per_source_limit=options.per_source_requests, state=registry, offline=offline, memoize=True)


def adapters_for(client, include_compensation=True):
    return {"greenhouse": GreenhouseSource(client), "lever": LeverSource(client),
            "ashby": AshbySource(client, include_compensation)}


def verify(registry: Registry, settings, client, force: bool = False, offline: bool = False, adapters=None) -> dict:
    adapters = adapters if adapters is not None else adapters_for(client, settings.discovery.include_compensation)
    verified, skipped, errors, planned = 0, 0, [], []
    for row in registry.rows():
        if not registry.enabled(row) or row["provider"] in settings.discovery.disabled_providers:
            continue
        if row["provider"] not in PROVIDERS:
            if not offline and row["status"] != "UNSUPPORTED":
                registry.failure(row["id"], "No supported public job-board adapter.", unsupported=True)
            continue
        if row["next_check"] and datetime.fromisoformat(row["next_check"]) > datetime.now(timezone.utc):
            skipped += 1
            continue
        if not force and row["status"] in {"VERIFIED", "ACTIVE"} and not elapsed(row["last_verified"], settings.discovery.verification_hours * 3600):
            skipped += 1
            continue
        if len(planned) >= settings.discovery.max_items:
            break
        planned.append(row["id"])
        if offline:
            continue
        client.source = row["source_key"]
        try:
            jobs = adapters[row["provider"]].fetch(registry.board(row))
            registry.cache(row["id"], jobs)
            registry.success(row["id"], len(jobs))
            verified += 1
        except BudgetExceeded as exc:
            errors.append(f"Source {row['id']} deferred: {exc}")
        except SourceError as exc:
            registry.failure(row["id"], str(exc))
            errors.append(f"Source {row['id']}: {exc}")
    return {"verified": verified, "skipped": skipped, "planned_sources": planned, "errors": errors,
            "requests": client.requests, "offline": offline}


def search_enabled(registry: Registry, settings, client, offline=False, force=False, adapters=None) -> SearchReport:
    adapters = adapters if adapters is not None else adapters_for(client, settings.discovery.include_compensation)
    start, before = utc_now(), registry.store.count()
    jobs, errors, checked_at = [], [], {}
    successful, cache_hits = 0, 0
    for row in registry.eligible(settings.discovery.verification_hours):
        if row["provider"] in settings.discovery.disabled_providers:
            continue
        client.source = row["source_key"]
        cached = registry.cached(row["id"], None if offline else settings.discovery.refresh_minutes)
        if offline and cached is None:
            errors.append(f"Source {row['id']}: no offline snapshot available.")
            continue
        try:
            if cached is not None and (offline or not force):
                found, observed_at = cached
                cache_hits += 1
            else:
                found = adapters[row["provider"]].fetch(registry.board(row))
                observed_at = utc_now()
                registry.cache(row["id"], found, observed_at)
                registry.success(row["id"], len(found), active=True)
            jobs.extend(found)
            successful += 1
            if not offline and cached is not None and not force:
                # Cache reuse activates a previously verified board, but is not a new network check.
                with registry.db:
                    registry.db.execute("UPDATE career_sources SET status='ACTIVE' WHERE id=?", (row["id"],))
            checked_at.update({(item.source, item.board, item.source_id): observed_at for item in found})
        except BudgetExceeded as exc:
            errors.append(f"Source {row['id']} deferred: {exc}")
        except SourceError as exc:
            registry.failure(row["id"], str(exc))
            errors.append(f"Source {row['id']}: {exc}")
    results = save_and_filter(jobs, settings, registry.store, checked_at=checked_at)
    if not offline:
        with registry.db:
            registry.db.execute("INSERT INTO refresh_runs(started_at,finished_at,fetched,new_jobs,failed_sources,successful_sources) VALUES(?,?,?,?,?,?)",
                                (start, utc_now(), len(jobs), max(0, registry.store.count() - before), len(errors), successful))
    return SearchReport(results, len(jobs), successful, tuple(errors), registry.store.count(), requests=client.requests, cached_sources=cache_hits, mode="offline" if offline else "live")
