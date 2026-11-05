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

Default weights are skills 60, relevant employment 25 and coursework 15, editable in
Scoring. Weights must be finite, nonnegative and have a positive total.

- Skill matching measures mention coverage from a small explicit vocabulary (for
  example Python, SQL, SQLite, C++, Excel, communication). Boundaries distinguish
  Java from JavaScript. Only verified profile facts supply evidence.
- Employment/coursework components measure overlap of distinct meaningful words in
  verified facts and the posting, saturating at four overlaps per component.
- The weighted total is normalized to 0–100. Explanations show components, weights,
  matched skills, unevidenced skills and relevant fact text.
- Skill mentions are not necessarily mandatory requirements. Unevidenced skills are
  uncertain, not proof the candidate lacks them. Unparsed eligibility and experience
  duration remain unknown. This phase does not automatically declare a confirmed
  mismatch from missing resume information.
- Scores measure text relevance, not hiring probability or opportunity quality.
  This is a deterministic baseline, not semantic understanding or comprehensive
  requirements extraction. All stored jobs are compared, not only a prior CLI search's
  matching subset. Phase 3.5 prioritization has not been implemented.

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

The full suite has **172 passing tests, zero failures**. Original Phase 1/2/2.5 tests
are unchanged. Synthetic fixtures cover actual PDF/DOCX extraction, malformed files,
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
