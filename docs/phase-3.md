# Phase 3 — Local resume intelligence

Phase 3 adds an authenticated FastAPI/Jinja2 interface to the existing discovery
engine. No resume, job description or credential is sent to a hosted AI provider.
No email, scheduling, networking outreach, application autofill or submission is
implemented. Phase 3.5 and later remain planning only.

## Start and stop on your Mac

The development environment already has the required packages. From Terminal:

```sh
cd ~/Projects/GetMeAJob
.venv/bin/python -m getmeajob serve
```

On a fresh checkout, create the environment and install the pinned free packages:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-phase3.txt
.venv/bin/python -m getmeajob serve
```

The server binds exclusively to **127.0.0.1**, normally port 8000. The normal address
is `http://127.0.0.1:8000`. Keep Terminal open; press **Control+C** there to stop the
server. Restart with the same command. It runs independently of Codex.

On the first start, Terminal prints a one-time `/setup?token=...` link. Open that
exact link and choose a local password of at least 12 characters. The secret prevents
another website from claiming initial setup. Do not share it. Later starts use the
normal address and the password. There is no default password. No personal account
was created during development; tests used disposable synthetic profiles.

A custom existing configuration is accepted before the command:

```sh
.venv/bin/python -m getmeajob --config config/settings.toml serve
```

Its dashboard port and discovery database path are respected. Dashboard host validation
still requires 127.0.0.1. Use the numeric address, not localhost, because Host headers
are checked exactly. This is a local HTTP application, not a remotely hosted service.

## Ordinary browser workflow

1. **Resumes:** upload a text-based PDF or DOCX and give it a version label. The
   original bytes and extracted text are stored locally. Every upload is a new,
   immutable original resume version. Choose a default with Make default.
2. **Review your profile:** use the collapsible Personal Information, Education,
   Experience, Skills, Projects and Other Information sections. New uploads extract
   complete employment records with company, job title, location, dates and associated
   achievement bullets. Edit a record inline and Save, Verify or Reject it. Each
   section also offers Save section edits and Save and Verify Section. The latter
   requires a checked confirmation that you reviewed all records and warnings in it.
   The header shows pending, verified and rejected counts. Add missing facts using
   the browser. Extraction never verifies anything. Here, verified means confirmed
   by you, not authenticated by a university or employer. Editing a verified record
   makes it pending until you verify the correction. Its original evidence and
   earlier value/status history remain available.
3. **Final review:** Review and Verify All shows every saved pending fact and its
   warning/source evidence. Check the explicit confirmation box to verify those
   pending facts together. Rejected facts remain rejected. Unsaved edits are not
   included; save them first. A changed profile makes a stale review form fail.
4. **Existing resumes:** open an older resume and select Preview structured
   reprocessing. Compare the preview against the original, then confirm. This makes
   a new resume version from the stored original text with all facts pending. It does
   not change the original resume, verified facts, packages or default selection.
   Verify the new copy and choose Make default on the Resumes page if wanted.
5. **Matching jobs:** compare your versions against real stored jobs. Search by
   employer/role and browse 20 results per page, newest database check first. Only
   the current page is matched against your resume versions. The best score selects
   a recommended resume; equal scores prefer the default. Scores are recalculated
   when that page is opened, so edits to verified facts or stored job descriptions
   are reflected without a stale score cache. Existing stored postings may be stale.
   Opening this page never searches the internet. A busy or invalid local job database
   shows a retryable error page rather than leaving the browser loading indefinitely.
6. **Prepare draft package:** use verified facts to generate a deterministic cover
   letter and a snapshot of the original job, selected resume, score/evidence,
   uncertainty and missing information. No verified facts means no generated letter.
7. **Review and edit:** change the letter, add your signature/contact details and
   select draft or reviewed. Saving creates a new package version; previous versions
   remain intact. Reviewed never authorizes submission.
8. **Download:** TXT, DOCX and PDF contain the saved letter version. ZIP includes
   the original selected resume, letter text/DOCX and package JSON with job description,
   source URL, score and verified evidence. The uploaded resume itself is not rewritten;
   review it separately for claims you have not verified. Downloads require login.

No application is submitted, even after marking a package reviewed. User edits are
labelled in package metadata and require your factual review; the deterministic
claim guarantee applies to generated text, not arbitrary text you type afterward.

## Explainable matching

Default weights are skills 45, job title 20, experience & projects 20 and education & coursework 15,
editable in Scoring. Weights must be finite, nonnegative and have a positive total.
Settings saved by earlier versions (three weights) are upgraded by adding the default
title weight; they are not discarded.

- **Skills** average two things: coverage of the vocabulary terms a posting names that
  appear in your verified facts, and a strength that is full at four matches (so a
  posting listing 15 tools does not cap you at 20%). The vocabulary (`getmeajob/resume/vocabulary.py`, about 330
  names and synonyms) covers technology, data analytics, finance, accounting,
  consulting, marketing, HR, operations and engineering. Synonyms count ("JS" is
  JavaScript, "MS Excel" is Excel, "FP&A"). Whole terms only (Java is not JavaScript);
  short aliases such as R, SAP and HR count only when written in capitals. A term in
  the job title counts double.
- **Job title** is the share of the posting's title words (minus generic words such as
  Senior, Intern, Remote) found in your verified employment, education, skills,
  tools, projects and certifications.
- **Experience & projects** and **Education & coursework** check whether those verified
  facts mention the posting's vocabulary skills or title words (full credit at three
  skills). Education with no overlap is left out rather than counted as zero, because a
  degree rarely names a posting's tools.
- A component with no evidence on one side is **left out and its weight is shared by
  the others** instead of counting as zero. For example, a posting naming no
  recognisable skill is scored on title/experience/coursework and shown as **low
  evidence**. The card lists configured weight and effective share per component.
- Each card starts with a one-sentence plain-English summary, then the detail.
- Skill mentions are not necessarily mandatory requirements. Unevidenced skills are
  uncertain, not proof the candidate lacks them. Eligibility and experience duration
  stay unknown. Pending, rejected and personal facts never count.
- Scores measure text relevance, not hiring probability or opportunity quality.
  Algorithm version 3 replaced the earlier three-component score, so scores differ
  from earlier builds. Phase 3.5 prioritization has not been implemented.

## Job filters and the Matching jobs page

Filters (collapsible panel; Apply, Save preferences and Reset are separate buttons):
job field, included role words and excluded title words, employment type, work
arrangement, experience level, city + province + radius, remote jobs that name your
region, minimum salary with currency and pay period, posted within 1/3/7/14/30 days,
included and excluded companies, minimum match %, sort (highest match, most recent,
company, job title) and a policy for unknown details.

Semantics: choices **within** one filter are OR; **different** filters are AND. Role
and company text is case-insensitive "contains" (company "Delta" finds "Delta, Inc.").
Excluded keywords apply to the job **title** only. Filters run before pagination, so
every page shows 20 matching jobs and the count is the true number of matches.
Role and keyword lists accept commas or new lines (companies: new lines or
semicolons), at most 15 items of 60 characters.

Shared logic: the dashboard converts choices into the Phase 2 `SearchFilters` and calls
the same `evaluate()` as the command line. Extensions are opt-in fields whose defaults
leave command-line behavior unchanged.

**Canada only** is on by default (choose Anywhere in the Region box to change it). A job
counts as Canadian only when a location names Canada, a Canadian province, a province
code after a comma (Toronto, ON) or a city not shared with a famous place elsewhere
(bare "London" or "Victoria" do not count). Jobs with no location at all follow the
unknown-details setting. Rules: `getmeajob/canada.py`.

**Sorting** links (Highest match, Most recent, Company, Job title) sit above the results.

**Job field** (`getmeajob/classify.py`) is deterministic and explains itself on every
card. Evidence order: the board's explicit industry, the source's department, then
title keywords (the longest matching keyword wins; ties follow a fixed priority; the
reason lists other fields that also matched). A department of "Engineering" is
refined to Technology when the title is clearly software. With no supporting
evidence the job is **Other**. It never uses personal data and is a heuristic: expect
some misclassified titles.

**Experience level** uses the source's level, or an explicit word in the title (Intern,
Co-op, Junior, Senior, Sr, Principal, Director...), or Internship when the employment
type is internship/co-op. Otherwise Unknown. "Staff" and "Manager" are not treated as
senior.

**Unknown details.** Anything the source did not supply is shown as Unknown and never
guessed. When a filter needs a detail that is Unknown, the radio button "Jobs with
unknown details" decides: include (default) or hide. For distance filters, a location
that is present but not on the offline city list (for example "Espoo") is a separate
"unplaced" case and is hidden unless you tick the unplaced box; a job with no location
at all follows the unknown-details setting. A remote job is accepted for your region
only when its location text names your province or Canada. That is not proof of work
authorization, timezone or visa eligibility. No network geocoder is used; the offline
list has about 30 Canadian cities and unlisted cities give a validation message.

**Saved preferences** are one versioned JSON setting in `private/profiles.sqlite3`
(never the jobs database or tracked config). Apply uses the URL only; Save writes the
setting; Reset deletes only that setting. Malformed or tampered JSON loads as defaults.
Links stay short (non-default values only) and contain no secrets.

**Match cache.** Highest-match sorting and minimum-match filtering need scores for
every job that passes the cheap filters. Scores are cached in `match_cache`
(`private/profiles.sqlite3`, additive migration to schema 3) keyed by job id and
resume id and validated by: SHA-256 of the exact job payload, a hash of the resume's
verified non-personal facts, and a hash of the algorithm version, vocabulary and
weights. Editing, verifying or rejecting a fact, changing a posting or changing
weights each cause recomputation of only the affected entries. The cache is derived
and rebuildable; deleting it loses nothing. At most 4,000 uncached scores are
computed per request; if more are needed a notice asks you to reload. With no resume
that has verified facts, score filtering and match sorting are skipped with a notice.

**Page extras:** employer and source counts, "New" marker for jobs first stored after
your previous visit (fixed for the browser session), per-card source label, "Not
interested" hide/restore (private database only; the jobs database is read-only), and
a stale-data warning with the refresh command when the newest check is 3 or more days
old. Every page says that stored jobs are not live listings and may be closed.

**Performance** (this Mac, real read-only database of 15,365 jobs from 89 employer
boards, one synthetic resume): about 1.6 s to filter, sort and render any page. The
first time you sort by match or set a minimum match, scoring all 15,365 jobs takes
about four page loads (each spends at most 6 seconds and shows a notice); afterwards
every request takes about 1.6 s. Per-posting vocabulary terms are cached too
(`job_features`, keyed by the exact posting), so editing a resume or changing weights
only repeats cheap comparisons. With 1,342 jobs a page takes 0.2 s. The earlier
unbounded approach took 30 s for 1,342 jobs. A test with 1,200 synthetic jobs fails
if the page exceeds a few seconds. Known limit: every request still reads all stored
postings (about 1 s at 15,000), so very large databases (50,000+) will feel slower.

## Email alerts (presets)

On Matching jobs, set filters and use **Save as email preset** (up to 10). Presets are
stored in `private/profiles.sqlite3` (schema 4: `notify_presets`, `notify_sent`). After a
refresh, `notify send` (or `search ... --notify`) builds, for every enabled preset, the
postings that match it and have not been emailed for that preset, ranked by match, and
sends one digest per preset (at most 30 postings; the rest roll over). A posting is
emailed once per preset; dismissed ("Not interested") postings are skipped. A failed send
marks nothing as sent, so the postings are retried next time.

```sh
.venv/bin/python -m getmeajob notify setup --address YOU@gmail.com   # once; app password goes to Keychain
.venv/bin/python -m getmeajob notify test
.venv/bin/python -m getmeajob notify preview     # writes private/digests/*.html, sends nothing
.venv/bin/python -m getmeajob notify send
.venv/bin/python -m getmeajob search --all-enabled --max-requests 0 --notify --config config/live-discovery.toml
```

Rules: Gmail only (SMTP over TLS to smtp.gmail.com), from your address to the same
address. The app password is read from macOS Keychain at send time and is never written
to a file, setting or log. Emails contain postings, scores, a reason line and links, never
resume text or personal facts; job text is HTML-escaped and only http(s) links are used.
The web interface can create, pause and delete presets but has no route that sends mail.
Scheduling (launchd) is not implemented yet, so a digest goes out when you run the
refresh command with `--notify`. Presets need a resume version with verified facts.

## Automatic refresh (launchd)

```sh
.venv/bin/python -m getmeajob schedule install      # 3 runs per weekday, 1 per weekend day
.venv/bin/python -m getmeajob schedule status
.venv/bin/python -m getmeajob schedule uninstall
.venv/bin/python -m getmeajob schedule run          # one refresh now (this is what launchd runs)
```

Default times: weekdays 08:00, 12:30, 17:00; weekends 09:00 (`--weekday-times 07:30,13:00`
and `--weekend-time 10:00` change them). `install` writes a user-level LaunchAgent
(`~/Library/LaunchAgents/com.getmeajob.refresh.plist`, no admin rights, nothing
system-wide) that runs the project's own Python with `config/live-discovery.toml`
(`--config` to choose another). Each run re-verifies stale sources, refreshes jobs
(repeating up to 6 passes because each pass is capped per host), then sends your preset
digests. A lock file stops overlapping runs; failures never lose or repeat postings.
launchd runs a run missed during sleep when the Mac wakes; a powered-off Mac misses it.
The log is `data/logs/refresh.log`. The first scheduled email may ask macOS for Keychain
access: choose Always Allow (run `notify test` once first to grant it).

## Apply assistant

On a package you have set to **reviewed**, **Open the application and fill it** starts
Chrome (your installed Google Chrome, driven by Playwright; no browser download) on the
posting's application page and fills the plain fields from **Applicant details** (first/last name, email,
phone, city, LinkedIn, website) plus your resume file and the package cover letter
(file upload or text box, whichever the form has). Then it stops. **It never submits.** A
banner explains this, and you read everything, answer the rest and press Submit
yourself. "I submitted this application myself" only records it for your own tracking.

Safety rules: only https pages on `boards.greenhouse.io`, `job-boards.greenhouse.io`,
`jobs.lever.co`, `jobs.eu.lever.co` and `jobs.ashbyhq.com` are opened (no redirects to
other hosts are followed by the assistant itself; the page may navigate normally). Custom
dropdowns, selects, checkboxes, demographic, salary, work-authorization,
company/school and free-text questions are never filled; required ones left empty are
listed on the package page. The only click it makes is a plain "Apply" link to reveal
a form. No CAPTCHA, login, LinkedIn or Easy Apply handling. Job pages are untrusted
text; nothing on them can change the steps. The assistant runs in its own process
started by an authenticated, CSRF-protected request with a fixed argument list.

Setup: `.venv/bin/pip install -r requirements-phase6.txt` (project-local), then save
**Applicant details** in the browser. Limitation: verified only on synthetic forms and mocks;
real career sites change, so expect some fields to be left for you. Forms with
custom widgets (Workday etc.) are unsupported by design.

## Employer coverage

`examples/employers.starter.csv` is a reviewed public list of about 150 candidate
employers (technology, fintech/finance, Canadian companies) with Greenhouse, Lever or
Ashby board addresses. Import and verification go through the existing registry:

```sh
.venv/bin/python -m getmeajob employers import --file examples/employers.starter.csv --config config/live-discovery.toml
.venv/bin/python -m getmeajob employers verify --max-requests 0 --config config/live-discovery.toml
.venv/bin/python -m getmeajob search --all-enabled --max-requests 0 --config config/live-discovery.toml
.venv/bin/python -m getmeajob employers coverage --config config/live-discovery.toml
```

`verify` and `search` stop after about 30 requests per host per run, so repeat them
until the output no longer plans new sources. Only boards that pass the official
endpoint check are searched; guessed addresses that do not exist stay unverified
and are listed by `coverage`. Verification is not an endorsement of any employer.
Large banks, consultancies and many retailers use Workday or custom sites, which are
not supported, so they will not appear. Same-title postings at one employer are kept
separate when their URLs differ (they are often distinct requisitions).

## Application links and cover letters (latest)

Every job card now has **Open the posting / apply on the company site** (http/https links only)
and a label: **Auto-fill available** or **Apply on the site yourself**. Many companies show
Greenhouse jobs on their own site; for those the assistant opens Greenhouse's official embed
form (`job-boards.greenhouse.io/embed/job_app?for=<board>&token=<id>`, built from the job's
own board name and numeric ID). Forms may include a reCAPTCHA that you solve yourself.

Cover letters are still built only from your verified, non-personal facts, with fixed wording
around them: an opening naming the role and employer plus the skills the posting names that your
facts contain; your most relevant experience as sentences ("In my role as ... at ..., I built ...");
relevant projects; education; a closing; and a sign-off with your name from Applicant details
(`[Your name]` appears if none is saved). Skills the posting wants but your facts lack are never
claimed. Review and edit every letter.

## Regions, uploaded cover letters and the improver

**Region** (Canada only by default) can be Canada, United States, both or anywhere. A location
counts for a country only with explicit evidence (country words, state/province codes,
well-known cities); ambiguous names such as a bare "London" count for neither, and "San
Francisco, CA | Toronto, ON" is judged city by city. Rules: `getmeajob/regions.py`.

**My cover letters** (nav bar) stores letters you upload (PDF, DOCX, TXT) or paste, privately in
`private/profiles.sqlite3` (schema 6). When preparing a package choose **Auto-generate a new letter**
or **Improve and tailor my letter**. The improver keeps your sentences and: fills [Company]/[Role]
placeholders, names the employer in the greeting, tightens hedges ("I believe I" becomes "I"), cleans
spacing, inserts one paragraph tying the job to your verified skills and most relevant verified
experience, and signs with your name. The package page lists **what changed** and **suggestions not
applied** (generic phrases such as "team player", length over about 420 words, leftover [brackets], a
reminder to check for another company's name). It never adds a skill or experience that is not a
verified fact. Review and edit before use.

## Truthful letters and optional local AI

The default generator uses actual role/employer text and up to eight literal verified
profile facts. It invents no experience, metrics, awards, employer mission, recruiters
or alumni. The generic signature `Applicant` is a visible review placeholder; replace
it in the editor. Job descriptions are data and cannot instruct the generator.

Optional AI is narrowly constrained to reordering those verified fact IDs for
readability. Enter an **already installed** model in the package form to opt into a
request to an **already running** Ollama server at 127.0.0.1:11434. Returned IDs must
be a complete valid permutation; generated prose is never accepted. Errors or an
unavailable model fall back to deterministic ordering. Only selected verified facts
are sent to that loopback service. This application does not install/start Ollama,
download models, select a hosted endpoint or require a paid API. Hardware/model
compatibility depends on your existing local setup; no model was downloaded or local
AI service enabled during development.

## Security and private persistence

ProfileStore maintains an additive private schema in `private/profiles.sqlite3`,
separate from discovery data. It stores resume BLOBs, extracted text, facts/history,
settings/password hash and immutable package versions. The private directory uses
0700 permissions and DB file 0600. Data is **not encrypted** by this application;
macOS account access and disk protections remain important. There is no password
recovery/reset interface in this phase. Never delete the DB to recover a password
without first preserving your resume/package data.

Authentication uses salted scrypt password hashes, random server-side sessions with
one-hour idle expiration, session/CSRF rotation on login, HttpOnly SameSite=Strict
cookies and five login attempts per minute. Cookies are local HTTP cookies; TLS/Secure
cookies and remote deployment are not supported. Logout and process restart invalidate
authentication. CSRF tokens bind to sessions and every write form, including upload,
setup and login. Exact Host, Origin/Fetch-Site checks, no CORS, no-store responses,
escaped Jinja templates and restrictive CSP prevent common local-browser attacks.
Access logs and proxy headers are disabled. There is no public API documentation UI.

Uploads have an 8 MiB file limit and bounded request bodies. Filenames are reduced
to safe display basenames; no upload filename determines a filesystem destination.
PDF signatures and DOCX ZIP structures are checked. ZIP expansion is limited to
20 MiB/500 members; traversal, encrypted/macro members and external relationships
are refused. DOCX resumes with external hyperlinks may need a copy without links.
Extraction runs in a disposable subprocess with a 15-second wall timeout, CPU limit,
30-page PDF limit and 100,000-character text limit. Additional address-space limits
apply on non-macOS platforms; macOS relies on size/page/time controls rather than a
reliable hard memory limit. This is not antivirus or an OS sandbox.

Scanned/image-only, encrypted or malformed documents produce a useful browser error;
OCR is not installed. DOCX table text is supported, but layouts and section detection
are heuristic. The structured parser recognizes common headings and position layouts.
Contact details before a heading stay in Personal Information. Standard ligatures and
nonbreaking spaces are normalized with a visible note; private-use contact icons are
removed only when surrounding contact text makes them decorative. Unknown PDF
character IDs, replacement characters and likely encoding artifacts are retained
and flagged for comparison with the original. Original text is never overwritten.
Columns, unusual headings, ambiguous company/title ordering and unmarked achievement
boundaries still need review. Wrapped bullet lines are joined conservatively; their
verbatim source lines remain in each record's evidence. PDF exports use standard fonts
and may not render every non-Latin glyph; DOCX/TXT remain alternatives. Originals
are stored, not converted in place.

No resumes, passwords, sessions, original documents or packages belong in Git.
Existing private/data exclusions cover all of these. The interface reads the jobs
DB in read-only mode and never migrates or replaces discovery records.

## Verification and delivery

The full suite has **289 passing tests, zero failures** (172 earlier tests are unchanged; 117 new ones cover filters, scoring, caches, email presets, the schedule and the apply assistant). Synthetic fixtures cover actual PDF/DOCX extraction, malformed files,
ZIP traversal/external links, pending/verified/rejected facts, history/defaults,
relevance/evidence, AI output constraints, package versions, authentication, CSRF,
Host/Origin checks, upload limits, session rotation/restart, login throttling, XSS
escaping, browser routes and all four download formats. The correction adds synthetic
tests for multiline bullets, position grouping, contact classification, Unicode
cleanup, section/bulk authorization, migration and preservation, and factual letter
generation. Job-page tests cover a large synthetic database, 20-result pagination,
newest observation selection, score freshness and a database error page. The setup layout and a
synthetic PDF export were visually inspected. No real resume or credential was used.
The installed Starlette test client emits a non-failing HTTPX deprecation warning;
it does not affect normal dashboard operation.

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Phase 2.5 was pushed and tagged v0.2.5 at 078dca6; v0.2.0 remains unchanged.
Phase 3 development commits remain local. v0.3.0 is a proposed release tag only,
and must not be created or pushed before explicit approval. See PROJECT_STATE.md and
PRODUCT_ROADMAP.md for current implementation/release state and future scope.


### Password-form browser compatibility fix

The response policy is `Referrer-Policy: same-origin`, not `no-referrer`. For ordinary
form POSTs, no-referrer can serialize the Origin header as null, which the app correctly
rejects. Same-origin preserves the local form origin while preventing referrer leakage
to external origins. Setup/login have explicit query-free action URLs. Null, foreign,
cross-site and wrong-port origins remain blocked, as do missing/invalid CSRF tokens.
See the [Fetch Origin-header algorithm](https://fetch.spec.whatwg.org/#append-a-request-origin-header).
After upgrading a running server, stop and restart it and use the newly printed setup
link if a password has not yet been saved; previously loaded forms retain old headers.
