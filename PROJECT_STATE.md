# Project state

Current development phase: Phase 3 implemented locally, pending push/release approval.
Phase 3.5 and later have not started.

Completed and tested: all Phase 1/2/2.5 discovery features plus PDF/DOCX resume upload,
preview, editable verified facts/history, multiple resume versions/default selection,
explainable configurable relevance matching/recommended versions, deterministic
verified-fact cover letters, optional constrained existing-local-model ordering,
versioned editable packages, TXT/DOCX/PDF/ZIP downloads and authenticated loopback-only
FastAPI/Jinja2 interface. No external application actions are implemented.

Latest complete test run: 146 passed, 0 failed (November 5, 2026). The original 131
tests are unchanged. Synthetic fixtures only; setup layout and PDF export visually
checked. One test-only HTTPX deprecation warning remains; no runtime failure.

Branch: main. Latest feature commit: 57005dc (browser interface). Earlier
local commits: 4f0ee46 (context), 78d33f1 (resume core), 0e61cc2 (score correction).
This state/documentation commit follows the implementation. For the current commit,
run `git log -1 --oneline`; a file cannot contain its own future commit hash.

Published: Phase 2.5 main and annotated v0.2.5 both point to
078dca6cbea48e0f888d8529cd6d30e9acf91ab6. Existing v0.2.0 unchanged. No Phase 3
commits have been pushed and v0.3.0 has not been created.

Known limits: heuristic section extraction and vocabulary-based relevance, no OCR,
no comprehensive eligibility/experience-duration verification, standard-font PDF
coverage, strict DOCX external-link rejection, no password-recovery UI, no at-rest
encryption beyond OS permissions, and the existing discovery coverage/freshness limits.
Local AI is optional existing Ollama only and can reorder facts, not generate prose.
Detailed controls and caveats: docs/phase-3.md.

Outstanding features: opportunity prioritization/evidence research (3.5), polished
queue/batch approvals/Gmail digest (4), assisted outreach (4.5), launchd/recovery (5),
permitted autofill and separately authorized submission/tracking (6). Full roadmap:
docs/PRODUCT_ROADMAP.md. No paid services or system-wide software installs.

Next development task: user acceptance of the local Phase 3 browser workflow, then
request approval to push these commits and release annotated v0.3.0. Do not create
that tag or begin Phase 3.5 before authorization.

Run: `.venv/bin/python -m getmeajob serve`; browser http://127.0.0.1:8000 after startup.
First run uses the one-time setup link printed in Terminal. Stop with Control+C.
