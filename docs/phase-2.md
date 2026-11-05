# Phase 2 discovery: design and verification

Historical record of the tagged v0.2.0 milestone. Current source expansion and registry
behavior are documented in [Phase 2.5](phase-2-5.md) and [architecture](architecture.md).

Phase 2 extends the Phase 1 package. It requires Python 3.11+ and has no third-party
runtime or test dependencies. No cover letters, email, scheduling, server, application
filling or submission are implemented.

## Source contracts

The integrations follow the official [Greenhouse Job Board API](https://docs.greenhouse.io/job-board.html)
and [Lever Postings API](https://github.com/lever/postings-api). Greenhouse documents
public read-only GET access without authentication. Lever documents public published
postings, employer-specific sites, structured fields and pagination. Neither adapter
is a universal search across employers. Only configured boards are requested.

| Normalized field | Greenhouse | Lever |
| --- | --- | --- |
| Employer | Explicit board configuration | Explicit board configuration |
| Role / source ID | `title` / `id` | `text` / `id` |
| Application URL | `absolute_url` | `applyUrl` |
| Locations | `location.name` | `categories.location` and `categories.allLocations` |
| Description | Decoded HTML `content` | Description, extra lists and closing text |
| Salary | Unknown: this adapter uses the list endpoint, which does not document a structured salary | Optional `salaryRange` with explicit amounts, currency and interval |
| Posting date | `first_published` if actually present | Unknown; creation time is not proof of publication |
| Source updated / created | `updated_at` kept separately | `createdAt`, when present, kept separately |
| Employment type | Recognized exposed `Employment Type` custom metadata, or explicit title labels | Recognized `categories.commitment`, or explicit title labels when absent |
| Work arrangement | Recognized `Work Arrangement` custom metadata or explicit location labels | Recognized `workplaceType`, or location labels when absent |
| Experience | Recognized explicit metadata/title labels and conservative minimum-years text | Recognized optional category level/title labels and conservative minimum-years text |
| Industry | Optional user-supplied board classification | Optional user-supplied board classification |

Greenhouse metadata keys recognized case-insensitively are `Employment Type`,
`Work Arrangement`, `Experience Level` and numeric `Minimum Years Experience`.
Custom fields are employer-specific and often absent. Recognized level values are
entry-level, mid-level, senior and executive. A senior or junior title alone does not
supply an experience requirement; the exact entry-level label is recognized.

Minimum-years text extraction recognizes explicit phrases such as “At least 2 years
of experience” and “Minimum of 2.5 years of professional experience.” It conservatively
ignores clauses containing preferred/bonus/alternative qualifications, and recognizes
explicit “No prior experience required.” The matching evidence is retained for review.
Other phrasing stays unknown. This is a limited deterministic text extractor, not
resume matching, a complete requirements parser or an inference about eligibility.

Greenhouse salary detail endpoints and prose salary extraction are not implemented.
A dollar sign does not establish a currency, and a range without a known pay period
cannot be compared to an annual minimum. Salary comparison uses the guaranteed lower
bound and never assumes annual hours or exchange rates. Optional metadata not exposed
in these list responses remains unknown even if visible on a separate career page.

## Persistence and duplicate identity

SQLite contains canonical `jobs`, per-source `observations`, and `identities` linking
stable URLs/content to a job. Writes use parameterized statements and transactions;
foreign keys are enabled. Repeated source/board/posting IDs update one observation.
URL normalization removes known tracking fields/fragments, preserves identifiers such
as `gh_jid`, and equates Lever's posting and `/apply` links. Content identity requires
an exact normalized employer/title/location/description combination with a nonempty
description and locations; no fuzzy title-only merging is used. Identical templates
can still represent distinct openings, and differently worded reposts may remain
separate: this phase cannot resolve every ambiguous requisition identity.

When observations connect previously separate identities, the groups are merged
transactionally and all source records are retained. The earliest discovery time is
preserved. `last_checked` records successful observation, not proof a vacancy is still
open. `last_updated` is a local change time, not the source publication date. Normalized
payloads are persisted as JSON with unknowns represented by nulls. The CLI uses current
observations, preferring a matching observation with fewer uncertain filter fields;
it does not synthesize missing metadata from another source.

The demo uses a fresh in-memory SQLite database on every run and synthetic source
names and `.invalid` application URLs. It never fetches online or writes demo rows to
the live database. The configured live database is excluded from Git.

## Network and safety boundaries

The HTTP client only makes GET requests to hardcoded Greenhouse/Lever HTTPS hosts.
Board-token validation prevents arbitrary URL input. Redirects are refused. Requests
are paced per host at a configurable minimum interval of one second, with a 20-second
socket timeout and 20 MiB response limit. Lever uses pages of 100 with a 100-page cap;
repeated IDs or hitting the cap report an incomplete-board error rather than claiming
complete results. Malformed required listing fields reject that board's batch.

Transient network and selected server failures allow at most three attempts with
backoff. HTTP 401/403/429 and HTTP 503 with Retry-After pause the host for the remainder
of the current run. There is no automatic retry after these pauses. Pause state is
not retained across separate CLI invocations: observe the reported source delay before
manually retrying. Do not launch concurrent searches to circumvent pacing. Enable only
boards whose public access and terms permit your use; remove any restricted board.

Descriptions are plain text data, never executed or passed to a shell or model.
Application URLs are validated as HTTP(S) links and displayed only. Terminal control
characters are removed; JSON uses escaped characters. The dashboard host validation
from Phase 1 remains enforced even though no server runs in this phase.

## Verification on November 5, 2026

- All 77 standard-library automated tests passed offline.
- Live search using the committed example configuration succeeded for both boards:
  Stripe Greenhouse: 761 postings; Palantir Lever: 309 postings.
- The default Waterloo 100 km straight-line radius produced 10 unique matches from
  those 1,070 fetched postings. These counts are a point-in-time observation, not
  an expected invariant or a guarantee the postings remain available.
- One example was Stripe's [Backend Engineer, Payments & Risk](https://stripe.com/jobs/search?gh_jid=8260985),
  with location `Toronto, Canada`, approximately 94 km from the Waterloo centre.
  Salary and employment type were unknown in the fetched data.
- Repeating the Stripe search preserved the unique row count and first-discovered
  timestamps; check times advanced while unchanged update times stayed unchanged.
- SQLite integrity check returned `ok`; foreign-key check found no problems.
- The live database and local environment were confirmed ignored by Git.
- Python's default CA paths on this Mac contained no certificate bundle. Loading the
  existing macOS `/etc/ssl/cert.pem` bundle fixed HTTPS without installing packages or
  disabling certificate/hostname checks. Automated tests cover this fallback.

Live checking also encountered an empty employer board and a 404 from another board.
The command handled both without losing successful Greenhouse results. The final
example was switched to the verified nonempty Palantir board. EU routing is tested
with synthetic responses; no live EU board was checked.
