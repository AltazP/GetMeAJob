# Project state

Current phase: Phase 3 authorized, implementation next.
Completed: Phase 1 foundation; Phase 2 discovery/filtering/persistence/deduplication;
Phase 2.5 employer registry, CSV/HN discovery, verification, Ashby, source health,
coverage, cache and network safeguards. All 131 tests passed (0 failed).

Branch: main. Latest implementation commit: 078dca6cbea48e0f888d8529cd6d30e9acf91ab6.
Phase 2.5 commits and annotated v0.2.5 were pushed and verified. v0.2.0 unchanged.
This document's own context-consolidation commit follows that implementation commit;
use git log -1 --oneline for the current documentation commit.

Outstanding: every Phase 3 item and the roadmap's later phases. Known limitations:
bounded feed coverage, unsupported custom/Workday parsing, unknown job metadata,
city-centre radius estimates, snapshot freshness and no automatic closure detection.

Architecture and permanent rules: docs/architecture.md, docs/PRODUCT_ROADMAP.md,
AGENTS.md. Next task: implement secure local resume upload and verified profile storage,
then explainable matching, truthful cover letters and reviewable packages. Never
submit applications. Phase 3 commits and release tag need approval before publication.
