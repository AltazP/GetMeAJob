"""Local command-line entry point for readiness, live discovery and an offline demo."""

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import sys
import unicodedata

from getmeajob import __version__
from getmeajob.config import ConfigurationError, load_settings
from getmeajob.demo import synthetic_jobs
from getmeajob.discovery import SearchReport, save_and_filter, search
from getmeajob.storage import JobStore
from getmeajob.sources import SourceError
from getmeajob.registry import Registry
from getmeajob import employer_cli
from getmeajob.source_management import adapters_for, client_for, search_enabled, snapshot_store


def terminal(value: object) -> str:
    """Prevent untrusted job text from injecting terminal control sequences."""
    return "".join(char if not unicodedata.category(char).startswith("C") else " " for char in str(value))


def display(report: SearchReport, limit: int, as_json: bool) -> None:
    if as_json:
        print(json.dumps({
            "fetched": report.fetched, "matching_jobs": len(report.results), "stored_jobs": report.stored_jobs,
            "successful_boards": report.successful_boards, "errors": report.errors,
            "mode": report.mode, "requests": report.requests, "cached_sources": report.cached_sources,
            "results": [{**item.job.to_dict(), **item.record, "distance_km": item.match.distance_km,
                         "unknown_filter_fields": item.match.unknown_fields} for item in report.results[:limit]],
        }, indent=2, ensure_ascii=True))
        return
    print(f"Observed {report.fetched} postings; {len(report.results)} unique matching jobs; {report.stored_jobs} unique jobs stored.")
    for item in report.results[:limit]:
        job, match, record = item.job, item.match, item.record
        print(f"\n{terminal(job.employer)} — {terminal(job.role)}")
        print(f"  Location: {terminal('; '.join(job.locations)) or 'Unknown'}")
        distance = f"{match.distance_km:.1f} km (city-centre estimate)" if match.distance_km is not None else "Unknown"
        print(f"  Distance: {distance}")
        print(f"  Type: {job.employment_type or 'Unknown'} | Arrangement: {job.work_arrangement or 'Unknown'}")
        salary = job.salary
        print(f"  Salary: {salary.minimum if salary.minimum is not None else 'Unknown'}–{salary.maximum if salary.maximum is not None else 'Unknown'} {terminal(salary.currency or 'Unknown currency')} / {salary.interval or 'Unknown period'}")
        print(f"  Posted: {job.posted_at or 'Unknown'} | Industry: {terminal(job.industry or 'Unknown')}")
        print(f"  Experience: {job.experience_level or 'Unknown'}; minimum years: {job.minimum_experience_years if job.minimum_experience_years is not None else 'Unknown'}")
        if job.experience_evidence:
            print(f"  Experience evidence (review): {terminal(job.experience_evidence)}")
        print(f"  Source: {terminal(', '.join(row['source'] + '/' + row['board'] for row in record['sources']))}")
        if job.original_url:
            print(f"  Original job URL: {terminal(job.original_url)}")
        if job.department:
            print(f"  Department: {terminal(job.department)}")
        print(f"  Original application URL: {terminal(job.application_url)}")
        print(f"  First discovered: {record['first_discovered']} | Last checked: {record['last_checked']}")
        print(f"  Last updated locally: {record['last_updated']}")
        if match.unknown_fields:
            print(f"  Unverified filter fields: {', '.join(match.unknown_fields)}")
    if len(report.results) > limit:
        print(f"\nShowing {limit} of {len(report.results)} matches; increase --limit to see more.")
    for error in report.errors:
        print(f"Warning: {terminal(error)}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GetMeAJob local job discovery")
    parser.add_argument("--version", action="version", version=f"GetMeAJob {__version__}")
    parser.add_argument("--config", type=Path, help="Path to a local TOML settings file")
    commands = parser.add_subparsers(dest="command")
    for name, help_text in (("search", "Search configured real employer boards"), ("demo", "Offline synthetic demonstration")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--config", type=Path, default=argparse.SUPPRESS)
        command.add_argument("--limit", type=int, default=20, help="Maximum displayed results (all fetched jobs are stored)")
        command.add_argument("--json", action="store_true", help="Print structured results; null means unknown")
        if name == "search":
            command.add_argument("--all-enabled", action="store_true", help="Search all enabled, freshly verified registry sources")
            command.add_argument("--offline", action="store_true", help="Use cached registry boards on an in-memory snapshot; no network or writes")
            command.add_argument("--dry-run", action="store_true", help="Preview sources without network or writes")
            command.add_argument("--force", action="store_true", help="Bypass board cache freshness, never rate limits")
            command.add_argument("--max-requests", type=int, help="Global request cap; 0 disables this cap")
    commands.add_parser("serve", help="Start the authenticated local resume interface")
    employer_cli.configure(commands)
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.config)
        if args.command == "serve":
            from getmeajob.dashboard import serve
            serve(settings)
            return 0
        max_requests = getattr(args, "max_requests", None)
        if max_requests is not None:
            if not 0 <= max_requests <= 100000:
                raise ConfigurationError("--max-requests must be between 0 and 100000.")
            settings = replace(settings, discovery=replace(settings.discovery, max_requests=max_requests or None))
        if args.command == "employers":
            return employer_cli.run(args, settings)
        if args.command is None:
            print(f"GetMeAJob {__version__} — Phase 2 job discovery ready; Phase 2.5 employer registry available.")
            print(f"Future dashboard address: http://{settings.host}:{settings.port} (not running).")
            print("Use 'search --config config/settings.toml' for real jobs or 'demo' for synthetic listings.")
            return 0
        if args.limit < 1:
            raise ConfigurationError("--limit must be at least 1.")
        if args.command == "demo":
            # In-memory SQLite: demo data never enters the live database or creates files.
            settings = replace(settings, boards=())
            with JobStore(Path(":memory:")) as store:
                jobs = synthetic_jobs()
                report = SearchReport(save_and_filter(jobs, settings, store), len(jobs), 0, (), store.count(), mode="demo")
                if not args.json:
                    print("OFFLINE DEMO — all employers and listings below are fictional.")
                display(report, args.limit, args.json)
            return 0
        if args.offline and not args.all_enabled:
            raise ConfigurationError("Offline search requires --all-enabled; run demo for synthetic listings.")
        if args.dry_run or args.offline:
            with snapshot_store(settings.database) as store:
                registry = Registry(store)
                if args.dry_run:
                    boards = [registry.board(row) for row in registry.eligible(settings.discovery.verification_hours)] if args.all_enabled else settings.boards
                    print(json.dumps({"dry_run": True, "requests": 0,
                        "sources": [{"provider": board.source, "board": board.token, "employer": board.employer} for board in boards],
                        "max_requests": settings.discovery.max_requests}, indent=2))
                else:
                    report = search_enabled(registry, settings, client_for(settings, registry, offline=True), offline=True)
                    if not args.json:
                        print("OFFLINE SNAPSHOT — saved postings, not a live availability check.")
                    display(report, args.limit, args.json)
                return 0 if args.dry_run else (1 if report.errors else 0)
        if args.all_enabled:
            with JobStore(settings.database) as store:
                registry = Registry(store)
                if not registry.eligible(settings.discovery.verification_hours):
                    raise ConfigurationError("No enabled, freshly verified sources. Run employers discover, then employers verify.")
                report = search_enabled(registry, settings, client_for(settings, registry), force=args.force)
                display(report, args.limit, args.json)
                return 1 if report.errors else 0
        if not settings.boards:
            raise ConfigurationError("No employer boards configured. Copy config/search.example.toml to config/settings.toml and edit [[boards]].")
        with JobStore(settings.database) as store:
            progress = lambda message: print(terminal(message), file=sys.stderr, flush=True)
            registry = Registry(store)
            disabled = {row[0] for row in registry.db.execute("SELECT provider FROM provider_policies WHERE enabled=0")} | set(settings.discovery.disabled_providers)
            blocked = {(row["provider"], row["region"], row["board_identifier"]) for row in registry.rows() if not registry.enabled(row)}
            settings = replace(settings, boards=tuple(board for board in settings.boards
                if board.source not in disabled and (board.source, board.region, board.token) not in blocked))
            client = client_for(settings, registry)
            report = search(settings, store, progress=progress,
                            adapters=adapters_for(client, settings.discovery.include_compensation))
            report = replace(report, requests=client.requests)
            display(report, args.limit, args.json)
            return 1 if report.errors else 0
    except (ConfigurationError, SourceError, UnicodeError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except (sqlite3.Error, OSError) as exc:
        print(f"Local storage error ({type(exc).__name__}); check the database path, permissions and available disk space.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Search interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
