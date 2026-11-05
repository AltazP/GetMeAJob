"""Automatic refresh with macOS launchd: 3 runs per weekday, 1 per weekend day.

A run re-verifies stale sources, refreshes jobs (repeating passes because each pass has a
per-host request cap), then emails your saved presets. Everything is a normal user-level
LaunchAgent; nothing system-wide is installed. launchd runs a job it missed while the Mac
slept as soon as the Mac wakes.
"""
from dataclasses import replace
import fcntl
import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "com.getmeajob.refresh"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
WEEKDAY_TIMES = ("08:00", "12:30", "17:00")
WEEKEND_TIMES = ("09:00",)
MAX_PASSES = 6
ROOT = Path(__file__).resolve().parents[1]


def parse_times(text: str) -> tuple[tuple[int, int], ...]:
    times = []
    for part in text.split(","):
        hour, _, minute = part.strip().partition(":")
        if not (hour.isdigit() and minute.isdigit() and 0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
            raise ValueError(f"'{part.strip()}' is not a time like 08:00.")
        times.append((int(hour), int(minute)))
    if not 1 <= len(times) <= 6:
        raise ValueError("Use between 1 and 6 times.")
    return tuple(times)


def calendar(weekday_times=WEEKDAY_TIMES, weekend_times=WEEKEND_TIMES) -> list[dict]:
    """launchd weekdays: 1=Monday .. 5=Friday, 6=Saturday, 0=Sunday."""
    entries = []
    for days, times in (((1, 2, 3, 4, 5), parse_times(",".join(weekday_times))), ((6, 0), parse_times(",".join(weekend_times)))):
        for day in days:
            for hour, minute in times:
                entries.append({"Weekday": day, "Hour": hour, "Minute": minute})
    return entries


def plist(config: Path, python: str | None = None, root: Path = ROOT, weekday_times=WEEKDAY_TIMES, weekend_times=WEEKEND_TIMES) -> dict:
    logs = root / "data" / "logs"
    return {"Label": LABEL, "WorkingDirectory": str(root),
            "ProgramArguments": [python or sys.executable, "-m", "getmeajob", "schedule", "run", "--config", str(config)],
            "StartCalendarInterval": calendar(weekday_times, weekend_times),
            "StandardOutPath": str(logs / "refresh.log"), "StandardErrorPath": str(logs / "refresh.log"),
            "EnvironmentVariables": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}, "RunAtLoad": False, "ProcessType": "Background"}


def install(config: Path, runner=subprocess.run, path: Path = PLIST, **options) -> Path:
    config = config.resolve()
    if not config.is_file():
        raise ValueError(f"Config file not found: {config}")
    data = plist(config, **options)
    Path(data["StandardOutPath"]).parent.mkdir(parents=True, exist_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(data))
    domain = f"gui/{os.getuid()}"
    runner(["launchctl", "bootout", f"{domain}/{LABEL}"], capture_output=True)   # replace an older copy; ignore "not loaded"
    done = runner(["launchctl", "bootstrap", domain, str(path)], capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError("launchctl could not load the schedule: " + (done.stderr or "").strip()[:200])
    return path


def uninstall(runner=subprocess.run, path: Path = PLIST) -> bool:
    runner(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], capture_output=True)
    existed = path.exists()
    path.unlink(missing_ok=True)
    return existed


def status(runner=subprocess.run, path: Path = PLIST) -> str:
    if not path.exists():
        return "Not installed."
    done = runner(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"], capture_output=True, text=True)
    return "Installed and loaded." if done.returncode == 0 else "Installed but not loaded; run schedule install again."


def refresh(settings, notify_after: bool = True, log=print) -> int:
    """One scheduled run. A lock file stops two runs from overlapping."""
    from getmeajob import notify_cli
    from getmeajob.registry import Registry
    from getmeajob.source_management import client_for, verify, search_enabled
    from getmeajob.storage import JobStore
    lock_path = settings.database.parent / "refresh.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            log("Another refresh is already running; skipping this one.")
            return 0
        # Each pass is capped per host, so repeat until nothing is deferred. A global cap of 0 means none.
        settings = replace(settings, discovery=replace(settings.discovery, max_requests=None))
        failures = 0
        with JobStore(settings.database) as store:
            registry = Registry(store)
            for number in range(MAX_PASSES):
                report = verify(registry, settings, client_for(settings, registry))
                if not report.get("planned_sources"):
                    break
            for number in range(MAX_PASSES):
                if not registry.eligible(settings.discovery.verification_hours):
                    log("No verified sources to search."); break
                result = search_enabled(registry, settings, client_for(settings, registry))
                deferred = [e for e in result.errors if "deferred" in e]
                failures = len(result.errors) - len(deferred)
                log(f"Pass {number + 1}: {result.fetched} postings from {result.successful_boards} source(s), {len(deferred)} deferred, {failures} failed.")
                if not deferred:
                    break
        if notify_after:
            notify_cli.after_search(settings)
        return 0


def configure(commands):
    import argparse
    group = commands.add_parser("schedule", help="Automatic refresh and email alerts with macOS launchd")
    group.add_argument("--config", type=Path, default=argparse.SUPPRESS)
    actions = group.add_subparsers(dest="schedule_action", required=True)
    install_parser = actions.add_parser("install", help="Turn on the schedule (3 runs per weekday, 1 per weekend day)")
    install_parser.add_argument("--config", type=Path, default=argparse.SUPPRESS)
    install_parser.add_argument("--weekday-times", default=",".join(WEEKDAY_TIMES), help="Comma-separated HH:MM, default 08:00,12:30,17:00")
    install_parser.add_argument("--weekend-time", default=",".join(WEEKEND_TIMES), help="Comma-separated HH:MM, default 09:00")
    actions.add_parser("uninstall", help="Turn the schedule off")
    actions.add_parser("status", help="Show whether the schedule is installed")
    run_parser = actions.add_parser("run", help="Run one refresh now (this is what launchd runs)")
    run_parser.add_argument("--config", type=Path, default=argparse.SUPPRESS)
    run_parser.add_argument("--no-notify", action="store_true", help="Refresh without emailing presets")


def run(args, config_path: Path | None) -> int:
    action = args.schedule_action
    if action == "install":
        path = install(config_path or ROOT / "config" / "live-discovery.toml", weekday_times=tuple(args.weekday_times.split(",")),
                       weekend_times=tuple(args.weekend_time.split(",")))
        print(f"Schedule installed ({path}).")
        print("Weekdays:", args.weekday_times, "| Weekends:", args.weekend_time, "(Mac must be on or asleep; missed runs happen on wake).")
        print("Log: data/logs/refresh.log. Turn off with: .venv/bin/python -m getmeajob schedule uninstall")
        return 0
    if action == "uninstall":
        print("Schedule removed." if uninstall() else "No schedule was installed.")
        return 0
    if action == "status":
        print(status())
        return 0
    return 2
