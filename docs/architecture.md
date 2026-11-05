# GetMeAJob architecture (Phase 2.5)

The application is a local Python package. Standard-library argparse/TOML/SQLite,
HTTP/TLS and unittest keep runtime and development free of external services.

```text
CLI / explicit local TOML
    ├── demo → synthetic records → in-memory SQLite → filters → Terminal
    ├── search → configured ATS adapters → existing job persistence/filtering
    └── employers
          ├── CSV / configured boards / official HN hiring feed
          ├── permitted page links + robots checks
          └── Registry.add → CANDIDATE / unsupported records + evidence
                           ↓ separate verify command
                     official ATS endpoints + schema validation
                           ↓ VERIFIED, cached normalized snapshot
                     search --all-enabled
                           ↓ ACTIVE, existing job persistence/filtering
                           ↓ coverage: observed local counts + network/check times
```

`sources/` implements the existing JobSource protocol. Greenhouse/Lever are preserved;
Ashby uses its public posting API and excludes unlisted records. `models.Job` adds
optional department, original URL and compensation fields. Existing stored payloads
remain readable without those optional fields. `filters.py` and `locations.py` retain
Phase 2 behavior and honest unknown handling.

`employer_discovery.py` extracts leads from public data. `urls.py` contains strict
syntax validation and exact supported provider patterns. Neither can verify a source.
`source_management.py` coordinates official endpoint checks, reusable source snapshots
and enabled-source searches. `employer_cli.py` exposes registry commands independently
of Codex. The original configured-board search remains available.

`JsonClient` handles allowlists, byte limits, budgets, per-host pacing, bounded retries
and persistent host pauses. `network.SafeOpener` rejects redirects and uses a pinned
public-IP socket with hostname-verified TLS, preventing DNS rebinding between check
and connection. Employer HTML is additionally host-permitted and robots-checked;
HTMLParser extracts data without executing scripts. API hosts and page hosts are
separate trust boundaries. Authentication and application actions do not exist here.

## SQLite evolution

The tagged Phase 2 database has SQLite `user_version=0` and three tables: `jobs`,
`observations`, `identities`. Their schema and existing rows remain intact.

Migration 1 adds employers, employer_keys, career_sources, source_evidence,
provider_policies, discovery_items, host_pauses and refresh_runs. Migration 2 adds
source_cache. A legacy version-0 database receives both additive changes in one
explicit transaction; a version-1 database receives the cache table transactionally.
Migration errors roll back; future versions are refused. Opening the DB applies the
migration. No destructive data conversion or implicit job-row replacement occurs.

- employers 1 → many career_sources; keys/aliases map duplicate identities.
- career_sources 1 → many source_evidence; one normalized cached board response.
- existing observations retain source/board/posting identity and canonical job IDs.
- provider_policies mask whole providers; employer/source flags mask individuals.
- discovery_items advance through seen feed entries/pages without re-downloading all.
- host_pauses carry access/rate-limit deadlines across CLI invocations.
- refresh_runs record collection metrics separately from actual source network times.

All SQL input values are parameterized. Registry identity merges preserve evidence
and related records; job-level deduplication still uses Phase 2 source IDs, original/
application URL aliases and exact known-content fingerprints. SQLite foreign keys
remain enabled. Cached data never overwrites a newer job observation, and reusing a
snapshot does not fabricate a new network-check timestamp.

Dry-run/offline opens the on-disk database read-only, backs it into an in-memory SQLite
connection and applies any migrations only there. A missing DB is represented by an
empty in-memory database. No persistent database is created in preview mode. Offline
results identify themselves as snapshots. All DB/cache/backup files stay ignored.

No distributed queue, scheduler, resume processing, AI inference, server, email,
credential collection or application submission is introduced. Those remain later
phases requiring authorization. [Phase 2.5 details and limitations](phase-2-5.md).
