# Project state

Current development phase: Phase 3 correction implemented locally, pending push/release approval.
Phase 3.5 and later have not started.

Completed and tested: all Phase 1/2/2.5 discovery features plus PDF/DOCX resume upload,
preview, editable verified facts/history, multiple resume versions/default selection,
explainable configurable relevance matching/recommended versions, deterministic
verified-fact cover letters, optional constrained existing-local-model ordering,
versioned editable packages, TXT/DOCX/PDF/ZIP downloads and authenticated loopback-only
FastAPI/Jinja2 interface. The correction adds structured personal/education/position/
skills/projects/other extraction, conservative Unicode cleanup with original evidence,
collapsible grouped review, whole-position edits, explicit section/final verification,
and preview-confirm reprocessing into a new all-pending resume version. Existing
resumes, verified facts, packages and jobs remain intact. No external application
actions are implemented.

Latest complete test run: 172 passed, 0 failed (November 5, 2026). The original 148
tests are unchanged. Synthetic fixtures only; setup layout and PDF export visually
checked. One test-only HTTPX deprecation warning remains; no runtime failure.

Branch: main. Correction commits: f3f66b1 (structured extraction and additive
private schema), 84ec7c5 (grouped review and explicit verification). Earlier Phase 3
local commits: 4f0ee46, 78d33f1, 0e61cc2, 57005dc, 0057f3f, 4cdb44b.
For the current documentation commit, run `git log -1 --oneline`; a file cannot
contain its own future commit hash.

Published: Phase 2.5 main and annotated v0.2.5 both point to
078dca6cbea48e0f888d8529cd6d30e9acf91ab6. Existing v0.2.0 unchanged. No Phase 3
commits have been pushed and v0.3.0 has not been created.

Latest fix: matching jobs now reads one read-only SQLite page of 20 latest distinct
jobs, then scores only that page. Search/order/count are server-side; stored jobs and
profiles are unchanged. There is no score cache, so changed verified facts or job
descriptions are reflected on the next request. Database failures show HTTP 503.
On the existing 1,342-job/3-resume dataset, read-only load-and-score work fell from
30.8 seconds to 0.9 seconds. An isolated loopback server with the existing jobs DB
and a temporary synthetic profile rendered 20 cards over HTTP in 0.076 seconds.
Password forms retain same-origin referrer policy and explicit actions.

Known limits: heuristic section/position extraction (columns, unusual headings,
ambiguous employer/title layouts and unmarked bullet boundaries need review) and
vocabulary-based relevance, no OCR,
no comprehensive eligibility/experience-duration verification, standard-font PDF
coverage, strict DOCX external-link rejection, no password-recovery UI, no at-rest
encryption beyond OS permissions, and the existing discovery coverage/freshness limits.
Local AI is optional existing Ollama only and can reorder facts, not generate prose.
Detailed controls and caveats: docs/phase-3.md.

Outstanding features: opportunity prioritization/evidence research (3.5), polished
queue/batch approvals/Gmail digest (4), assisted outreach (4.5), launchd/recovery (5),
permitted autofill and separately authorized submission/tracking (6). Full roadmap:
docs/PRODUCT_ROADMAP.md. No paid services or system-wide software installs.

Next development task: user acceptance of the corrected local Phase 3 browser
workflow. Do not push Phase 3, create v0.3.0 or begin Phase 3.5 without authorization.

Run: `.venv/bin/python -m getmeajob serve`; browser http://127.0.0.1:8000 after startup.
First run uses the one-time setup link printed in Terminal. If an older server is
running, stop it with Control+C and restart. Existing users log in with their current
password. Open an older resume, preview structured reprocessing, confirm the new
pending copy, review/verify it and optionally make that copy the default.
