# GetMeAJob

A free, private, local-first AI job-search and application assistant for macOS.
The application will run independently of Codex. No paid APIs, hosting subscriptions,
or unnecessary software installations are planned.

## Current status: Phase 1 only

The Python project, local configuration validation and initial automated tests are
implemented. Running the application prints a readiness message and exits. It does
not search jobs, run AI, send email, start a dashboard, install schedules, fill forms
or submit applications yet.

## Requirements and setup

- macOS (Apple Silicon or Intel).
- Python 3.11 or newer; development was verified with Python 3.12.6.
- Git for development.

Phase 1 uses only Python's standard library. No package downloads are required.
Run these commands in Terminal, one line at a time:

```sh
cd ~/Projects/GetMeAJob
python3 -m venv .venv
.venv/bin/python -m getmeajob
```

The first command opens the project folder. The second creates an isolated Python
environment inside `.venv`; run it once when setting up the project. The last command
runs the application. No environment activation or Codex session is needed.

Expected output:

```text
GetMeAJob 0.1.0 — Phase 1 foundation ready.
Future dashboard address: http://127.0.0.1:8000 (not running).
Job discovery, AI matching, email, scheduling and application actions are not implemented yet.
```

The displayed dashboard address is reserved for a later phase and cannot be opened yet.
If `python3` is older than 3.11, use an installed Python 3.11+ interpreter to create
the environment; Python 3.11 supplies the built-in TOML configuration reader.

## Optional local configuration

The application uses safe defaults without a configuration file. To customize the
future dashboard port:

```sh
cp config/settings.example.toml config/settings.toml
.venv/bin/python -m getmeajob --config config/settings.toml
```

Edit `config/settings.toml` in a text editor. It is excluded from Git. The host must
stay `127.0.0.1`; the port must be an integer between 1024 and 65535. Unknown settings
and unreadable or invalid files produce an error and exit code 2. An explicit
`--config` is required to load a file; local files are never loaded silently.
Do not place passwords or API tokens in configuration files.

## Tests

```sh
cd ~/Projects/GetMeAJob
.venv/bin/python -m unittest discover -s tests -v
```

Tests cover safe defaults, configuration validation, error handling, and the real
Python module command. They require no internet access, accounts or personal data.

## Project layout

```text
getmeajob/                    Python application package and command-line entry point
config/settings.example.toml  Safe example configuration
tests/                       Automated tests (standard-library unittest)
docs/                        Design and environment notes
pyproject.toml               Python compatibility and package metadata
AGENTS.md                    Project goals and developer rules
.gitignore                   Rules excluding local and private files
```

The `tests/` and `docs/` directories live directly in the project root.
The source checkout runs directly; installing the package is optional and may
require downloading the setuptools build tool. It is not part of Phase 1 setup.

## Planned phases

1. **Foundation (implemented):** environment checks, structure, documentation,
   Python setup, example configuration, basic entry point and tests.
2. **Discovery:** legitimate job sources and free APIs, search filters, normalized
   postings and duplicate removal.
3. **Matching:** verified resume and experience matching, relevance scores and
   customized, truthful cover letters; optional free local AI if hardware supports it.
4. **Review:** a secure local dashboard, email notifications and authenticated
   application approvals.
5. **Scheduling:** macOS launchd searches three times each weekday and once each
   weekend day. Times and timezone will be configured during that phase.
6. **Optional assistance:** permitted Playwright application autofill and submission
   assistance, subject to explicit authenticated approval.

Planned filters include city and distance in kilometres; remote, hybrid or on-site;
internship, co-op, full-time, part-time or contract; title, industry, keywords,
experience level, salary and date posted.

Python, SQLite, FastAPI, Jinja2, Playwright and launchd are the preferred stack.
Later dependencies will be added only when needed. Local AI compatibility has not
been assessed; Phase 1 does not install or download models.

## Privacy and application safety

- Keep resumes, personal information and generated application materials in the
  ignored `private/` directory; keep databases in ignored `data/`.
- `.gitignore` also excludes local configuration, credentials, environments,
  browser sessions, common document formats, databases and logs. It cannot identify
  personal information in arbitrary filenames: review every staged change.
- Never commit tokens, credentials, resumes, private databases or personal data.
  Later credential storage should use macOS Keychain, not tracked files.
- The future dashboard must bind to `127.0.0.1` and authenticate approval actions.
- Treat job descriptions as untrusted input. Never invent qualifications. Require
  human review whenever application details are uncertain.
- Never submit without explicit authenticated approval, or bypass CAPTCHAs,
  access controls or job-platform restrictions.

## Contribution workflow

Read `AGENTS.md` before changing the project. Keep changes within the approved
phase, run tests before every commit, make descriptive milestone commits, and
preserve existing Git history. Push completed milestones only after the user
approves. No GitHub push is part of initial setup.
