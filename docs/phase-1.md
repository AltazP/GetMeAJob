# Phase 1 environment and design record

Environment inspected on November 5, 2026:

| Item | Observed value |
| --- | --- |
| macOS | 26.0.1, build 25A362 |
| Processor architecture | arm64 (Apple Silicon) |
| Python | 3.12.6 |
| Python location | /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 |
| pip | 24.2 (not needed for this phase) |
| Git | 2.39.5 (Apple Git-154) |
| Initial branch | main, tracking origin/main |
| Initial history | 646a163 — Initial commit |
| Initial tracked files | README.md only |

No compatibility blocker was found for Phase 1. Python 3.11 is the minimum because
configuration uses the standard-library `tomllib` module. The inspected Python
3.12.6 satisfies this. The exact processor model could not be queried in the
restricted environment; architecture was verified using `uname -m`. RAM and local
AI performance have not been assessed and are not needed for this phase.

Git initially had no effective author identity. The user supplied one for local
repository configuration; it is intentionally omitted from project files.

The foundation uses a flat Python package so `python -m getmeajob` runs from the
checkout without an installation step. `pyproject.toml` also describes an optional
installable package; packaging uses setuptools, which is not required to run from
the checkout. There are no third-party runtime dependencies.

Configuration rejects unsupported keys, unsafe hosts and invalid ports. No server
is started. The CLI performs a readiness check and exits without network requests,
background tasks, database creation or application actions. This keeps the first
milestone directly testable and avoids introducing features from later phases.

Fourteen standard-library unit/integration tests cover configuration and CLI
behavior, including subprocess execution of the real entry point. Actual local
settings, environments, future resumes, credentials, databases and generated
applications are excluded from version control.
