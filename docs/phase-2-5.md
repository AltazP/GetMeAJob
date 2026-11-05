# Phase 2.5 — Employer discovery and source expansion

GetMeAJob 0.2.5 extends the existing Phase 2 engine. It runs on Python 3.11+ using
only the standard library. No paid services, accounts, model downloads or Codex
runtime are required. Phase 3 has not begun.

## How discovery grows the registry

Discovery and verification are separate commands. A discovered URL is evidence of
a candidate, not proof that a working public job board exists.

1. **Automatic public feed:** the documented [Hacker News API](https://github.com/HackerNews/API)
   supplies the `whoishiring` account's recent submissions. GetMeAJob checks at most
   six story candidates to find its latest official “Who is hiring?” thread. A known
   official story ID can also be configured.
2. A bounded batch of top-level employer comments is inspected. Only explicit
   pipe-delimited employer headers supply names. The parser never invents a company
   name from a domain or ATS token. It extracts supported ATS links and explicit
   career-page links, records the comment's evidence URL, and remembers processed
   IDs so later runs advance through the thread. Deleted/dead comments are skipped.
   This is lead discovery from self-reported public data, not legal identity checking.
3. **CSV imports** and existing configured boards supply additional candidates.
   Importing and finding links never performs verification implicitly.
4. **Employer-page extraction** checks imported/configured career or home pages
   only when their exact hostname is listed in `discovery.page_hosts`. It checks
   robots.txt first, honors disallows, crawl delay and request rate, and extracts
   ordinary anchor/iframe links without executing JavaScript. Same-host career links
   become additional page records for a later bounded run, rather than a recursive
   unbounded crawl. Custom/Workday pages remain unsupported records.
5. `employers verify` calls a supported provider's documented public endpoint. Only a
   successful response with the expected job-list schema marks the board verified.
   `search --all-enabled` collects from enabled, freshly verified boards.

Hacker News's API represents user links with SEO `rel=nofollow`. Those URLs are read
as fields in the permitted public API data and recorded as candidates; discovery does
not open those URLs. Career-page crawling separately honors link/meta nofollow and
robots.txt. Verification uses the official ATS API, never an unrestricted page crawl.

Feed coverage is biased toward employers that post hiring-thread links. Comments
without a clear employer header, plain URLs without anchor markup, unusual formats,
JavaScript-only pages and employers outside this feed can be missed. Optional general
web-search providers are not implemented. The feed does not discover every employer.

## Supported providers and honest metadata

| Provider | Verification and collection endpoint | Coverage |
| --- | --- | --- |
| Greenhouse | `GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` | Existing Phase 2 normalization and public board access |
| Lever | `GET https://api.lever.co/v0/postings/{token}?mode=json&skip=N&limit=100` | Existing global/EU support and bounded pagination |
| Ashby | `GET https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true` | Explicitly listed public jobs, department, locations, description, employment/workplace type, compensation, last publication, original job URL and application URL |

See [Ashby's official public API documentation](https://developers.ashbyhq.com/docs/public-job-posting-api).
Ashby records are collected only when `isListed` is exactly true. Missing/false flags
exclude a posting. `isRemote` alone is ambiguous between remote and hybrid, so only
`workplaceType` determines the arrangement. Unknown fields remain null/Unknown.
`publishedAt` means the last publication date, which may reflect republication.

Compensation retains source summaries, tiers and component types separately. Only a
single explicit summary Salary component supplies the normal salary filter; multiple
salary components are not combined into an invented range. Bonus/equity amounts are
never salary. Currency and period conversions remain unsupported. If no stable Ashby
ID is present, its actual original job URL supplies identity. Old Phase 2 payloads
remain readable with the new optional fields absent.

Verification proves that the public endpoint responded with a valid listing schema;
it does not authenticate corporate ownership of a self-reported employer name. A
valid empty board can verify without currently having jobs. URL patterns alone,
HTTP success without a valid job schema, and blocked endpoints never verify a board.

## Registry, states and safe migrations

[Architecture and schema](architecture.md) describe the additive SQLite migrations.
Existing jobs, observations and duplicate identities are preserved. Employer records
hold stable IDs, canonical names, optional supplied official websites, enabled flags
and discovery timestamps. Career-source records hold employer ID, careers/evidence
URLs, discovery method, provider/board/region, status, enabled flag, verification and
successful-fetch timestamps, failure count, last error, next retry and last job count.
Multiple evidence records and identity aliases are retained.

| State | Meaning |
| --- | --- |
| CANDIDATE | Discovered/imported source awaiting endpoint verification |
| VERIFIED | Successful supported endpoint/schema check, possibly an empty board |
| ACTIVE | Verified source successfully used in a registry search, including fresh cached data |
| TEMPORARILY_UNAVAILABLE | A checked source failed; error and retry time retained |
| UNSUPPORTED | Workday/custom page, no source details, or a page not permitted for fetching |
| DISABLED | Source or employer explicitly disabled |

Freshness is checked dynamically: stale verified sources are excluded from registry
search until reverified. The default is 24 hours. A failure preserves prior success
history and adds exponential source retry delay from one minute up to one day.
`--force` rechecks otherwise-fresh boards but cannot bypass source retry delays,
host pauses, access restrictions, robots or request budgets.

Deduplication uses exact normalized names, supplied official website hostnames and
(provider, region, board) identity. Bridging identities merges employers and retains
sources/evidence. Canonical names are not overwritten by later feed labels. Similar
names are not fuzzy-matched. Exact-name/shared-domain aliases can still be ambiguous
for unrelated namesakes or subsidiaries; this is not a corporate-identity database.
No guessed website or manufactured ATS identifier is stored.

## Exact Terminal commands

Run from the existing checkout. Your Phase 1 `.venv` is sufficient:

```sh
cd ~/Projects/GetMeAJob
.venv/bin/python -m getmeajob employers discover --config config/registry.example.toml
.venv/bin/python -m getmeajob employers verify --config config/registry.example.toml
.venv/bin/python -m getmeajob search --all-enabled --config config/registry.example.toml
.venv/bin/python -m getmeajob employers coverage --config config/registry.example.toml
.venv/bin/python -m getmeajob employers list --config config/registry.example.toml
```

With no `--config`, employer commands still work using `data/jobs.sqlite3`, Waterloo
filters and automatic HN discovery defaults. The example additionally seeds three
real public employer boards; remove its `[[boards]]` sections for feed-only growth.
Repeat discovery/verification to process subsequent bounded batches. Candidates never
become active simply because another command imported them.

To customize without overwriting existing local settings:

```sh
cp -n config/registry.example.toml config/settings.toml
```

If local settings already exist, edit them and merge the `[discovery]` example.
Change filter/coordinate settings as documented in the README. Add exact page hosts
only after checking that their public access and terms permit your use. A page host
is permission to inspect that host's public pages, not permission to bypass robots or
access controls. The API host allowlist is fixed in code and separate from page hosts.

CSV columns are `name` (required), `website`, `careers_url`, `ats_url` (optional).
Use UTF-8. Quoted names containing commas are supported. Unknown columns, malformed
rows and unsafe URLs are rejected. Names alone create unsupported records: the
system does not invent missing URLs. Every row is validated on a snapshot before live
writes; valid imports are idempotent and safe to repeat after interruption.

```sh
.venv/bin/python -m getmeajob employers import --config config/registry.example.toml --file examples/employers.example.csv
.venv/bin/python -m getmeajob employers import --config config/settings.toml --file private/employers.csv
```

Personal CSVs, registry/cache databases, local configuration and backups stay ignored.
Only the reviewed public example CSV is exempted from the CSV exclusion.

Control sources using IDs from `employers list`:

```sh
.venv/bin/python -m getmeajob employers disable --source 5
.venv/bin/python -m getmeajob employers enable --source 5
.venv/bin/python -m getmeajob employers disable --employer 2
.venv/bin/python -m getmeajob employers disable --provider lever
.venv/bin/python -m getmeajob employers enable --provider lever
```

Add `--config config/settings.toml` to these commands when using a different database.
Re-enabling an employer/source returns it to candidate status and requires fresh
verification. Provider policies mask all that provider's boards without changing their
individual states. Configuration `disabled_providers` adds further exclusions;
provider enable cannot override that configuration. Regular configured-board searches
also honor stored disable controls.

Offline and preview operations never issue requests or modify/create the on-disk DB:

```sh
.venv/bin/python -m getmeajob employers discover --dry-run
.venv/bin/python -m getmeajob employers verify --dry-run
.venv/bin/python -m getmeajob employers import --file examples/employers.example.csv --dry-run
.venv/bin/python -m getmeajob search --all-enabled --dry-run
.venv/bin/python -m getmeajob search --all-enabled --offline
.venv/bin/python -m getmeajob demo
```

Dry-run uses an in-memory snapshot to preview local work; it cannot predict future
network discoveries or verify candidates. Offline search uses saved snapshots of
currently eligible sources, keeps their actual check timestamps, and labels JSON
`mode=offline`. Missing caches are reported. Sources whose verification is stale or
disabled are not revived by offline mode. `demo` remains entirely fictional.

To refresh endpoints or re-extract previously seen discovery entries:

```sh
.venv/bin/python -m getmeajob employers verify --force --max-requests 30
.venv/bin/python -m getmeajob employers discover --recheck --max-requests 30
.venv/bin/python -m getmeajob search --all-enabled --force --max-requests 30
```

## Performance, network safety and coverage

The successful normalized board snapshot defaults to a 60-minute cache. Immediate
searches after verification reuse it; repeated local checks do not claim a new network
fetch or replace newer observations with older snapshots. Unchanged job payloads
preserve modification timestamps. Refresh after expiry is a bounded full board GET;
ETag/delta APIs are not implemented. Cached jobs may disappear at the source before
expiry. Removed postings remain history; automatic closure detection is not included.

Budgets count every HTTP attempt, including retries and robots requests. Configure
`max_requests` (default 100, example 50; zero removes only this cap),
`per_host_requests` (50, example 30), `per_source_requests` (20), `max_items` (20,
example 10), and `timeout_seconds` (20). `--max-requests` overrides only the global
budget. HN lookups and comment requests share a discovery-source budget; a budget can
end a batch early. Deferred work is reported and does not mark an ATS board broken.

Requests are paced at least one second per host. Exponential backoff allows at most
three attempts. HTTP 401/403/429 or a server Retry-After pause prevents further host
requests and persists across commands. Default pauses without Retry-After are one hour
for rate limits and one day for access denials. Source retries have separate bounded
backoff. Do not run concurrent commands to circumvent pacing; subsecond pacing is
per process, while pauses persist in SQLite. The OS resolver follows macOS DNS timeout
policy; socket connections, TLS and reads use the configured timeout.

Only HTTPS, credential-free domain URLs on port 443 are accepted for discovery.
Before a connection, all resolved addresses must be globally routable; local/private,
metadata, mixed public/private, and IP-translation/tunnelling ranges are blocked. The
socket is pinned to a checked address while TLS verifies the original hostname. There
is no second hostname resolution, proxy environment support, redirect following,
cookie authentication, CAPTCHA handling or JavaScript execution. Page bodies are
bounded to 1 MiB, robots to 100 KB, API bodies to 20 MiB. All content is untrusted data.

`employers coverage` reports observed local counts: known/verified employers,
active/verified enabled boards, stale/disabled/failed/unsupported sources, collected
canonical jobs per provider, and last successful **network** fetch time. Per-provider
counts can overlap when a job appears on multiple providers. The last collection run
records actual postings handled, successful/failed/deferred sources, and nonnegative
net growth of unique stored jobs (`new_jobs_last_run`). This is not a worldwide
coverage estimate, a count of all available vacancies, or an alert/event stream.
Verification snapshots are not counted as collected jobs until searched.

## Other free APIs researched; unsupported integrations

- [Arbeitnow's published API](https://www.arbeitnow.com/blog/job-board-api) offers a
  keyless baseline and European/UK feeds. It is a potential future adapter, not
  implemented or counted in current coverage; geographical relevance differs from
  the Waterloo example.
- [Remotive's public API](https://github.com/remotive-com/remote-jobs-api) has source
  attribution/link-back requirements, redistribution restrictions, delayed data and
  conservative polling guidance. It was researched but not integrated. Its public
  baseline does not require adopting the separately offered paid API.
- Hacker News is integrated for employer **leads**, not as a fourth job-posting ATS.
- LinkedIn/Indeed restricted scraping, Workday, arbitrary custom-site job parsing,
  general search engines and all other ATS providers remain unsupported. No paid
  discovery service or hidden authenticated API is used.

For a new ATS, implement `JobSource.fetch(board) -> list[Job]`, add exact URL patterns,
register its documented public API host, config validation and adapter factory, then
add schema/error/listing/privacy tests. A discovery feed is a separate bounded function
that returns candidates/evidence via Registry.add; it must not self-verify. Confirm
terms and baseline cost, test offline, and isolate failures before enabling a source.

## Live verification and tests — November 5, 2026

The observed local registry contains 9 employers and 4 verified/active employer boards:
Stripe (Greenhouse), Palantir Technologies (Lever), Ashby (Ashby), and ICEYE (Ashby).
ICEYE was discovered from the public hiring thread, then separately verified through
its official endpoint; its 199 postings were collected without a manually configured
board. Eight unsupported career-source records remain stored without being scraped.

The final collection handled 1,342 real postings: 762 Greenhouse, 309 Lever and 271
Ashby postings (72 Ashby plus 199 ICEYE). Default Waterloo filtering returned 10 unique
matches. The search reused four fresh snapshots and made zero additional requests.
The last run added 199 unique jobs. Counts change as public boards change.

The pre-migration Phase 2 database was backed up under ignored `data/backups/` before
live verification. Integrity and foreign-key checks passed. Automated fixtures verify
byte-for-byte preservation of legacy payloads during migration. All 131 tests passed;
the original 77 Phase 1/2 tests were retained unchanged. Tests cover CSV import, source
patterns, identity merges, listing normalization, optional metadata, verification,
health/staleness, snapshots, budgets/rate limits, partial failures, robots/nofollow,
unsafe DNS/redirects, disabled sources and CLI offline/dry-run behavior.

## Changed-file inventory

New implementation: `getmeajob/urls.py`, `migrations.py`, `registry.py`, `network.py`,
`employer_discovery.py`, `source_management.py`, `employer_cli.py`, and
`getmeajob/sources/ashby.py`.

Updated implementation: `getmeajob/__init__.py`, `__main__.py`, `config.py`,
`models.py`, `storage.py`, `discovery.py`, `sources/http.py`, and `pyproject.toml`.

New tests: `tests/test_registry.py`, `tests/test_ashby.py`,
`tests/test_source_expansion.py`. New examples: `config/registry.example.toml`,
`examples/employers.example.csv`. Documentation/privacy: `README.md`, `AGENTS.md`,
`.gitignore`, `docs/architecture.md`, `docs/phase-2-5.md`, and a historical note in
`docs/phase-2.md`. No local registry, CSV imports, credentials or personal data are
part of the commits. No Phase 3 work or new release tag is included.
