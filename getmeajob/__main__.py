"""Run the Phase 1 readiness check; no network calls or background work."""

import argparse
from pathlib import Path
import sys

from getmeajob import __version__
from getmeajob.config import ConfigurationError, load_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GetMeAJob Phase 1 readiness check")
    parser.add_argument("--version", action="version", version=f"GetMeAJob {__version__}")
    parser.add_argument("--config", type=Path, help="Path to a local TOML settings file")
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.config)
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    print(f"GetMeAJob {__version__} — Phase 1 foundation ready.")
    print(f"Future dashboard address: http://{settings.host}:{settings.port} (not running).")
    print("Job discovery, AI matching, email, scheduling and application actions are not implemented yet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
