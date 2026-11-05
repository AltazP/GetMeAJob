# Product roadmap

Phases 1, 2 and 2.5 are completed and tested. Phase 2.5 is released at v0.2.5 (078dca6).
Phase 3 is implemented and tested locally, pending push/release approval. Phase 3.5 and later are planning only.

Phase 3 completed scope and remaining phases:

PHASE 3 (implemented locally; see phase-3.md for limits):
- Secure local PDF/DOCX resume upload page
- Editable and verified user profile
- Multiple resume versions
- Explainable resume-to-job matching
- Optional free local AI with a deterministic fallback
- Personalized and truthful cover letters
- Editable and downloadable application packages
- Simple local interface using FastAPI/Jinja2
- Structured resume records and evidence-preserving text cleanup
- Collapsible section review, whole-position editing and explicit bulk verification
- Preview-and-confirm reprocessing that preserves old resume versions and verified facts

PHASE 3.5:
- Top 15 opportunity prioritization
- Separate resume match and opportunity scores
- Job freshness and career relevance
- Relevant coursework
- Verified university alumni connections
- Previous employer hiring activity
- Historical job posting comparisons
- Verified recruiter information
- Evidence links and timestamps

PHASE 4:
- Polished local dashboard
- Smart Apply Queue
- Reusable verified application answers
- Application package review
- Secure batch approval
- Gmail email notifications
- One daily digest
- Separate application submission authorization

PHASE 4.5:
- Recruiter and alumni networking research
- Personalized LinkedIn and email outreach drafts
- User-assisted LinkedIn research
- Separate outreach approval
- No unauthorized LinkedIn scraping or automatic messaging

PHASE 5:
- macOS launchd scheduled searches
- Three weekday job searches
- One weekend scan per day
- Daily email digest
- Error logging and missed-run recovery
- Run independently of Codex

PHASE 6:
- Permitted Playwright application autofill
- Explicit authorization before submission
- Manual fallback for unsupported sites
- Final confirmation
- Duplicate submission prevention
- Application status and confirmation tracking

PERMANENT PRODUCT RULES:

- Free to operate.
- Local-first and Mac-compatible.
- Minimal user interaction.
- No paid APIs or hosting.
- No invented qualifications or achievements.
- No fabricated employer or alumni information.
- No unauthorized scraping or automated LinkedIn messaging.
- No application submission without explicit user approval.
- No resumes, credentials, access tokens or personal databases committed to GitHub.
- Preserve existing functionality and database records.
- Run tests before meaningful commits.
- Make multiple meaningful commits throughout development.
- Ask before future GitHub pushes.
- Keep project documentation updated after every phase.


Architecture: Python CLI, SQLite jobs and employer registry, public Greenhouse/Lever/Ashby adapters, HN discovery, bounded pinned HTTPS transport. Phase 3 adds FastAPI/Jinja2 and a separate ignored private SQLite database. See architecture.md and phase documentation.

Git: preserve history and data, test before meaningful commits, make separate tested milestones. Future pushes and v0.3.0 require approval. Approval for v0.2.5 did not authorize later pushes.
