"""Employer commands. Dry-run/offline work on an in-memory snapshot of local data."""

import argparse
from dataclasses import replace
import json
from pathlib import Path

from getmeajob.employer_discovery import discover
from getmeajob.registry import Registry
from getmeajob.source_management import client_for, snapshot_store, verify
from getmeajob.storage import JobStore


def add_options(parser):
    parser.add_argument("--config", type=Path, default=argparse.SUPPRESS)
    parser.add_argument("--offline", action="store_true", help="No network or persistent changes; inspect a local snapshot")
    parser.add_argument("--dry-run", action="store_true", help="Preview work without network or persistent changes")
    parser.add_argument("--max-requests", type=int, help="Global request cap; 0 disables only the global cap")


def configure(commands):
    employers = commands.add_parser("employers", help="Discover, verify and manage employer career sources")
    employers.add_argument("--config", type=Path, default=argparse.SUPPRESS)
    actions = employers.add_subparsers(dest="employer_action", required=True)
    for name in ("discover", "verify", "list", "import", "enable", "disable", "coverage"):
        action = actions.add_parser(name)
        add_options(action)
        if name == "discover":
            action.add_argument("--recheck", action="store_true", help="Re-extract seen feed entries and pages, respecting all limits")
            action.add_argument("--hn-story", type=int, help="Official Who is hiring story ID; otherwise find latest automatically")
        if name == "verify":
            action.add_argument("--force", action="store_true", help="Recheck freshness; rate-limit pauses still apply")
        if name == "import":
            action.add_argument("--file", type=Path, required=True)
        if name in {"enable", "disable"}:
            target = action.add_mutually_exclusive_group(required=True)
            target.add_argument("--employer", type=int)
            target.add_argument("--source", type=int)
            target.add_argument("--provider", choices=("greenhouse", "lever", "ashby", "workday", "custom"))


def run(args, settings):
    readonly = args.offline or args.dry_run
    if getattr(args, "hn_story", None) is not None:
        if args.hn_story <= 0:
            from getmeajob.config import ConfigurationError
            raise ConfigurationError("--hn-story must be positive.")
        settings = replace(settings, discovery=replace(settings.discovery, hn_enabled=True, hn_story_id=args.hn_story))
    with snapshot_store(settings.database) if readonly else JobStore(settings.database) as store:
        registry = Registry(store)
        client = client_for(settings, registry, offline=readonly)
        action = args.employer_action
        if action == "import":
            report = {"imported_rows": registry.import_csv(args.file)}
        elif action == "discover":
            report = discover(registry, settings, client, offline=readonly, recheck=args.recheck)
        elif action == "verify":
            report = verify(registry, settings, client, force=args.force, offline=readonly)
        elif action == "list":
            report = {"sources": [{**row, "effective_enabled": registry.enabled(row) and row["provider"] not in settings.discovery.disabled_providers}
                                  for row in registry.rows()]}
        elif action == "coverage":
            report = registry.coverage(settings.discovery.verification_hours)
        else:
            registry.set_enabled(action == "enable", employer_id=args.employer, source_id=args.source, provider=args.provider)
            report = {"updated": action, "employer": args.employer, "source": args.source, "provider": args.provider}
        report.update({"dry_run": args.dry_run, "offline": args.offline, "requests": client.requests})
        print(json.dumps(report, indent=2, ensure_ascii=True))
        return 1 if report.get("errors") else 0
