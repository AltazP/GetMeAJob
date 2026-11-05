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


def terminal(value: object) -> str:
    """Prevent untrusted job text from injecting terminal control sequences."""
    return "".join(char if not unicodedata.category(char).startswith("C") else " " for char in str(value))


def display(report: SearchReport, limit: int, as_json: bool) -> None:
    if as_json:
        print(json.dumps({
            "fetched": report.fetched, "matching_jobs": len(report.results), "stored_jobs": report.stored_jobs,
            "successful_boards": report.successful_boards, "errors": report.errors,
            "results": [{**item.job.to_dict(), **item.record, "distance_km": item.match.distance_km,
                         "unknown_filter_fields": item.match.unknown_fields} for item in report.results[:limit]],
        }, indent=2, ensure_ascii=True))
        return
    print(f"Fetched {report.fetched} postings; {len(report.results)} unique matching jobs; {report.stored_jobs} unique jobs stored.")
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
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.config)
        if args.command is None:
            print(f"GetMeAJob {__version__} — Phase 2 job discovery ready.")
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
                report = SearchReport(save_and_filter(jobs, settings, store), len(jobs), 0, (), store.count())
                if not args.json:
                    print("OFFLINE DEMO — all employers and listings below are fictional.")
                display(report, args.limit, args.json)
            return 0
        if not settings.boards:
            raise ConfigurationError("No employer boards configured. Copy config/search.example.toml to config/settings.toml and edit [[boards]].")
        with JobStore(settings.database) as store:
            progress = lambda message: print(terminal(message), file=sys.stderr, flush=True)
            report = search(settings, store, progress=progress)
            display(report, args.limit, args.json)
            return 1 if report.errors else 0
    except ConfigurationError as exc:
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
