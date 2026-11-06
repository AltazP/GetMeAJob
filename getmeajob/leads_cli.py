"""`leads fetch`: import the community internship lists (Simplify, Canadian Tech Internships, SpeedyApply)."""
import argparse
from pathlib import Path

from getmeajob.registry import Registry
from getmeajob.sources.leadlists import LABELS, LISTS, fetch_leads
from getmeajob.source_management import client_for
from getmeajob.storage import JobStore


def configure(commands):
    group = commands.add_parser("leads", help="Community internship lists as discovery leads (public GitHub files)")
    group.add_argument("--config", type=Path, default=argparse.SUPPRESS)
    actions = group.add_subparsers(dest="leads_action", required=True)
    fetch = actions.add_parser("fetch", help="Download the lists (one request each) and store their postings")
    fetch.add_argument("--config", type=Path, default=argparse.SUPPRESS)
    fetch.add_argument("--list", choices=("all", *LISTS), default="all")
    fetch.add_argument("--dry-run", action="store_true", help="Show what would be fetched; no network, no changes")
    check = actions.add_parser("check", help="Check whether your top list-sourced matches are still open (reads robots.txt first)")
    check.add_argument("--config", type=Path, default=argparse.SUPPRESS)
    check.add_argument("--limit", type=int, default=40, help="Most postings to check this run (default 40)")


def import_leads(store: JobStore, settings, names=tuple(LISTS), log=print) -> dict:
    """Fetch and store the chosen lists. A failing list is reported and skipped; the others still import."""
    registry = Registry(store)
    found, errors = fetch_leads(client_for(settings, registry), names)
    summary = {}
    for name, jobs in found.items():
        before = store.count()
        for job in jobs:
            store.upsert(job)
        summary[name] = {"listed": len(jobs), "new": store.count() - before}
        log(f"{LABELS[name]}: {len(jobs)} open posting(s) listed, {store.count() - before} new to your database.")
    for name, message in errors.items():
        log(f"{LABELS[name]}: skipped ({message})")
    return {"summary": summary, "errors": errors}


def check_top_matches(store, settings, limit: int, checker=None, log=print) -> dict:
    """Check the list-sourced postings your presets would email next; results are saved and shown on the jobs page."""
    from getmeajob import notify, openness
    from getmeajob.notify_cli import make_checker
    from getmeajob.sources.http import BudgetExceeded
    checker = checker or make_checker(settings, requests=max(20, limit * 3), store=store)
    counts = {"OPEN": 0, "CLOSED": 0, "UNKNOWN": 0}
    done = 0
    for digest in notify.build_digests(settings.database, store):
        for item in digest.items:
            job = item["row"].job
            if job.source in notify.API_SOURCES or done >= limit:
                continue
            try:
                result = openness.ensure_check(store, checker, item["row"].id, job.application_url)
            except BudgetExceeded:
                log("Stopped early: this run's request budget is used up."); return counts
            counts[result.status] += 1; done += 1
            log(f"{result.status:7} {job.employer} - {job.role[:50]} ({result.reason[:70]})")
    return counts


def run(args, settings) -> int:
    if getattr(args, "leads_action", "") == "check":
        from getmeajob.resume.store import ProfileStore
        from getmeajob.notify_cli import PRIVATE
        if not 1 <= args.limit <= 500:
            print("Use --limit between 1 and 500."); return 2
        counts = check_top_matches(ProfileStore(PRIVATE), settings, args.limit)
        print(f"Checked: {counts['OPEN']} open, {counts['CLOSED']} closed (hidden from now on), {counts['UNKNOWN']} could not be confirmed.")
        return 0
    names = tuple(LISTS) if args.list == "all" else (args.list,)
    if args.dry_run:
        for name in names:
            print(f"Would fetch {LABELS[name]}: {LISTS[name]}")
        print("Dry run: no requests were made and nothing changed.")
        return 0
    print("Reading public GitHub list files (no accounts, no scraping of employer sites). Each posting links to the employer's own page;")
    print("a listing is a lead and may already be closed, so verify before applying.")
    with JobStore(settings.database) as store:
        result = import_leads(store, settings, names)
    return 1 if result["errors"] and not result["summary"] else 0
