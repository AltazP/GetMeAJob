# GetMeAJob

A free, private, local-first job-search and application assistant for macOS.
It runs directly on your Mac, independently of Codex, with no paid APIs or hosting.

## Current status: Phase 3 local resume intelligence

Real job discovery works through the public Greenhouse, Lever and Ashby job-posting APIs.
Searches use configured boards or the growing verified employer registry, apply your filters, store
postings in SQLite and display unique matching jobs in Terminal. These APIs do
**not** search every employer on their platforms. The included employers are working
API examples, not a personalized list of recommended employers.

An offline demo uses clearly fictional employers and an in-memory database.
Resume upload, grouped profile review, explainable matching, reviewable letters and package
downloads now work in the local browser interface. Email, scheduling, outreach, autofill
and submission remain future phases. Running the application with no subcommand prints a readiness
message; it does not start a server.

## Start the private resume interface

Your existing environment is ready:

```sh
cd ~/Projects/GetMeAJob
.venv/bin/python -m getmeajob serve
```

On first start, open the one-time setup link printed in Terminal and create your
local password. Later use **http://127.0.0.1:8000**. Keep Terminal open; stop with
**Control+C**. If the server is already running, restart it with the command above.
The page groups Personal Information, Education, Experience, Skills, Projects and
Other Information into collapsible sections. Edit and verify one record, verify a
reviewed section, or use Review and Verify All with explicit confirmation. Each
employment record keeps its company, title, dates, location and achievements together.

For a resume uploaded before this correction, open it and select **Preview structured
reprocessing**. Check the preview, then confirm creation of a new pending version.
The old resume and its verified facts stay available. Review and verify the new copy,
then make it your default from the Resumes page if desired. Matching, letter editing
and TXT/DOCX/PDF/ZIP downloads remain browser-based.

**Matching jobs** shows 20 stored jobs per page with a Filters panel (job field,
role words, employment type, work arrangement, level, location and distance, salary,
posted date, companies, minimum match, sorting). Apply tries filters, Save preferences
keeps them for next time, Reset clears only the saved filters. Details such as a
source did not provide are shown as Unknown. The page never runs a live search and the
jobs are stored copies: always check the original posting is still open. To refresh
jobs run `.venv/bin/python -m getmeajob search --all-enabled --config config/live-discovery.toml`.
Full filter semantics, scoring weights and the match cache are in docs/phase-3.md.

On a fresh checkout, first install the free project-local dependencies:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-phase3.txt
```

Personal data stays in ignored `private/profiles.sqlite3`. Generated claims use only
facts you explicitly verify. Scores indicate relevance, not hiring probability.
Nothing is submitted. Optional local AI only reorders verified facts using an existing
local model; no model or service is installed or started automatically.
Read [Phase 3 setup, security and limitations](docs/phase-3.md),
[project state](PROJECT_STATE.md) and [the complete roadmap](docs/PRODUCT_ROADMAP.md).

## Discover employers automatically

The new registry records employer identity, source evidence, health and verification.
The public Hacker News hiring feed finds employer leads without entering every board
manually. CSV imports and permitted career-page link extraction can add more leads.
Discovery creates candidates; a separate official-endpoint check must verify them.

```sh
cd ~/Projects/GetMeAJob
.venv/bin/python -m getmeajob employers discover --config config/registry.example.toml
.venv/bin/python -m getmeajob employers verify --config config/registry.example.toml
.venv/bin/python -m getmeajob search --all-enabled --config config/registry.example.toml
.venv/bin/python -m getmeajob employers coverage --config config/registry.example.toml
```

The example seeds three public boards and enables bounded automatic feed discovery.
No keys, account or new installation is needed. Repeat discovery to inspect the next
batch. All new boards stay candidates until verified. Workday/custom pages are retained
as unsupported records. HTML extraction needs explicitly permitted page hosts and
robots access. Supported ATS providers are **Greenhouse, Lever (global/EU), and Ashby**.

Read [the Phase 2.5 guide](docs/phase-2-5.md) for CSV imports, source/provider controls,
offline/dry-run commands, settings, exact verification rules and limitations, and
[the architecture](docs/architecture.md) for the additive migration design. Existing
jobs are preserved. Fresh snapshots avoid immediate duplicate board downloads.
Phase 3 is implemented locally; later phases have not begun.

## Run it

Requirements: macOS, Python 3.11+ and Git for development. Development was verified
on Apple Silicon with Python 3.12.6. Discovery uses the standard library. The optional Phase 3 interface uses free Python
packages installed only in .venv. No API keys or paid services are required.

Run these commands in Terminal:

```sh
cd ~/Projects/GetMeAJob
# Only needed once on a new checkout; the existing Phase 1 environment is ready.
python3 -m venv .venv
.venv/bin/python -m getmeajob demo
```

Run a real search immediately with the included settings:

```sh
.venv/bin/python -m getmeajob search --config config/search.example.toml
```

This queries Stripe's Greenhouse board and Palantir's global Lever board, then filters
for locations within 100 km of Waterloo, Ontario. Live results change over time.
The command prints progress, original application links, known metadata, unknowns,
and local discovery timestamps. It does not open or fill an application.

The offline demo needs no network and does not change your live database:

```sh
.venv/bin/python -m getmeajob demo
```

With default filters it fetches five synthetic postings, stores four unique jobs
in memory, and displays two matching jobs. One posting is deliberately duplicated,
one is outside the radius and one has an unknown location.

## Configure your search

Create your ignored local settings file, then edit it in any text editor:

```sh
cp -n config/search.example.toml config/settings.toml
.venv/bin/python -m getmeajob search --config config/settings.toml
```

`cp -n` keeps an existing settings file. If you already have Phase 1 settings, add
`[search]` and `[[boards]]` sections from the search example to that file. The old
`config/settings.example.toml` remains a valid dashboard-only example, but contains
no employer boards and cannot run a live search by itself. Files are loaded only
when you explicitly supply `--config`.

Edit the `[[boards]]` entries to choose employers:

```toml
[[boards]]
source = "greenhouse"
token = "stripe"
employer = "Stripe"

[[boards]]
source = "lever"
token = "palantir"
employer = "Palantir Technologies"
region = "global"
```

Find a token in the employer's public career link: `boards.greenhouse.io/TOKEN`,
`job-boards.greenhouse.io/TOKEN`, or `jobs.lever.co/TOKEN`. Enter only `TOKEN`, not a
full URL. Ashby uses `jobs.ashbyhq.com/TOKEN` and `source = "ashby"`. For `jobs.eu.lever.co/TOKEN`, set Lever's `region = "eu"`. Use the same
employer name on different boards for consistent content-based deduplication.
Optionally add `industry = "Your verified classification"` to a board; industry
comes from your explicit classification, not a guessed department or company name.

The example documents every supported setting. Filter behavior:

| Setting in `[search]` | Meaning |
| --- | --- |
| `titles` | Any listed phrase in the role title, case-insensitive |
| `keywords` | Every listed phrase in the title or description |
| `excluded_keywords` | Reject any listed phrase in the title or description |
| `companies`, `excluded_companies` | Exact employer names, case-insensitive; exclusions win |
| `industries` | Exact board classifications, case-insensitive |
| `employment_types` | Any of internship, co-op, full-time, part-time, contract |
| `work_arrangements` | Any of remote, hybrid, on-site |
| `experience_levels` | Any of entry-level, mid-level, senior, executive |
| `max_experience_years` | Maximum explicit minimum years of experience required |
| `minimum_salary` | Required lower end of a published salary range |
| `salary_currency`, `salary_interval` | Exact comparable currency and period; no conversions |
| `posted_within_days` | Publication within the last N days; never substituted with update/creation date |
| `city`, `latitude`, `longitude`, `radius_km` | Search centre and straight-line radius in kilometres |
| `use_radius = false` | Disable the location-radius filter |
| `include_remote_outside_radius = true` | Allow explicitly remote jobs beyond the radius; eligibility still needs review |
| `include_unknown = true` | Include and flag jobs with missing metadata for an active filter |

Empty arrays disable that filter. Different filter groups are combined with AND.
Optional numeric limits are disabled by leaving their lines commented out.
By default, an active filter rejects a job when the needed metadata is unknown.
Explicit nonmatching values are always rejected, even when `include_unknown` is true.

For example, replace the corresponding settings within the existing `[search]` section:

```toml
titles = ["Developer", "Engineer"]
keywords = ["Python"]
excluded_keywords = ["Senior"]
work_arrangements = ["hybrid", "remote"]
# Uncomment only if you want jobs with missing filter metadata included and flagged:
# include_unknown = true
```

Be aware that sources often omit salary, employment type, experience and posting
date. Combining strict metadata filters can legitimately produce zero matches.
Results are filter matches, not verified resume matches or guarantees of eligibility.

## Location handling

The default is Waterloo, Ontario (`43.4643`, `-80.5204`) with a 100 km radius.
Distances use approximate city centres and the Haversine formula, not driving routes
or workplace street addresses. Toronto's centre is about 94 km away by this measure.

An offline gazetteer covers qualified Ontario location labels for Waterloo,
Kitchener, Cambridge, Guelph, Brantford, Hamilton, Burlington, London, Mississauga,
Toronto and Ottawa. Bare city names such as `London`, unfamiliar formatting,
combined free-text locations and broad regions may remain unresolved. No network
geocoder guesses their coordinates.

When changing your centre, change **both** latitude and longitude along with the
city label. Alternatively remove both coordinates and use a recognized qualified
city name; unrecognized centres require explicit coordinates. Add verified exact
`[[locations]]` aliases as shown at the end of the search example to recognize more
employer labels. Conflicting aliases stay unresolved. Lever's structured multiple
locations are supported; any resolved location inside the radius qualifies.

Remote listings still need a resolvable in-radius location by default. Opting into
remote jobs outside the radius does not check countries of residence, work permits,
timezones or employer restrictions; read the original posting.

## Storage, unknowns and failures

Live data is stored in ignored `data/jobs.sqlite3`, relative to your current working
directory. Run from the repository root. The database stores all successfully fetched
postings, including those excluded by your current filters. Results display only
postings fetched successfully during the current search, so old stored jobs are not
silently presented as current vacancies. Disappeared postings are retained as history;
this phase does not mark jobs closed.

Deduplication uses stable source/board/posting IDs, canonical application URLs, and
exact employer/title/location/description content fingerprints when content is known.
It preserves every source observation. Different URLs with differently worded reposts
may remain separate; matching titles alone never establishes a duplicate.

Each canonical job and source observation records `first_discovered`, `last_checked`
and `last_updated` in UTC. Rechecking an unchanged observation changes only its check
time. New/changed observations update local modification time. Original source update
and creation timestamps remain separate fields. Missing values print `Unknown` or
JSON `null`; [the discovery notes](docs/phase-2.md) explain source-specific coverage.

Requests are HTTPS GETs to supported public API hosts only, paced at least one second
apart per host. Timeouts, response size and pagination are bounded. Transient failures
get at most three attempts. Access denial, rate limits, or a server `Retry-After`
pause stop that host and persist its deadline across commands; follow the reported
delay before a new run. There is no login, CAPTCHA handling, redirect following or bypass behavior.
A failing board reports a warning while successful boards still return results.

Exit codes: `0` = successful search (including zero matches); `1` = source/storage
failure or partial search; `2` = configuration/usage error; `130` = interrupted.
Use `--limit 50` to display more results, or `--json` for structured output:

```sh
.venv/bin/python -m getmeajob search --config config/settings.toml --limit 50 --json
```

If Python's own certificate bundle is missing, the HTTP client uses macOS's existing
`/etc/ssl/cert.pem` bundle while retaining certificate and hostname verification.
No extra certificate package is installed. Further TLS failures are reported as
errors; verification is never disabled.

## Tests and development

```sh
.venv/bin/python -m unittest discover -s tests -v
```

The 148 offline automated tests cover configuration, normalization, filtering,
locations, SQLite persistence and timestamps, deduplication, source isolation,
pagination, HTTP failures and rate limits, TLS verification, terminal safety and CLI
behavior. Tests use synthetic data and no accounts or network access.

The package stays modular: `sources/` contains API adapters, `models.py` defines the
normalized job record, `locations.py` and `filters.py` evaluate searches, `storage.py`
handles SQLite, and `discovery.py` orchestrates a run. New sources can implement the
`JobSource.fetch(board)` interface and be registered in discovery/configuration.

Read [AGENTS.md](AGENTS.md) before editing. Run tests before every meaningful commit,
preserve history and unrelated work, and push only after user approval. Never commit
credentials, resumes, personal data, local settings or databases. Keep personal inputs
in ignored `private/`. The existing `.gitignore` also excludes credentials, browser
sessions and common personal document formats; always review staged files.

## Remaining phases and unsupported sources

Greenhouse, Lever and Ashby public employer-board APIs are supported; Lever EU routing
is tested offline. The live checks used global endpoints. The official HN API supplies
employer leads. LinkedIn, Indeed, Workday and arbitrary custom job-page parsing remain
unsupported. Discovery is bounded and cannot find every employer; no authenticated
platform access or restricted scraping is included.

Phase 1 and 2 are complete. Phase 2.5 registry and source expansion are implemented. Phase 3 adds
verified resume matching and truthful cover letters; Phase 4 the authenticated local
dashboard and email; Phase 5 launchd searches three times per weekday and once per
weekend day; Phase 6 optional permitted Playwright assistance. These later phases
require further authorization.

The future dashboard must bind to `127.0.0.1`; credentials must be stored securely;
job descriptions remain untrusted data; qualifications must never be invented;
uncertain application details require review; and submission always requires explicit
authenticated approval. The intended later stack remains SQLite, FastAPI, Jinja2,
launchd, permitted Playwright flows and free local AI where hardware supports it.
