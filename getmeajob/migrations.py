"""Additive registry migration. Existing job rows and payloads remain untouched."""

import sqlite3

VERSION = 1


def migrate(connection: sqlite3.Connection) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version > VERSION:
        raise sqlite3.DatabaseError("Database schema is newer than this application; refusing to downgrade.")
    if version == VERSION:
        return
    # Explicit transaction: executescript's implicit commit must not split the migration.
    try:
        connection.executescript("""
            BEGIN IMMEDIATE;
            CREATE TABLE employers (
                id INTEGER PRIMARY KEY, canonical_name TEXT NOT NULL,
                official_website TEXT, enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
                first_discovered TEXT NOT NULL
            );
            CREATE TABLE employer_keys (
                key TEXT PRIMARY KEY, employer_id INTEGER NOT NULL REFERENCES employers(id)
            );
            CREATE TABLE career_sources (
                id INTEGER PRIMARY KEY, employer_id INTEGER NOT NULL REFERENCES employers(id),
                source_key TEXT NOT NULL UNIQUE, careers_page TEXT,
                provider TEXT NOT NULL, board_identifier TEXT, region TEXT NOT NULL DEFAULT 'global',
                discovery_source TEXT NOT NULL, evidence_url TEXT,
                status TEXT NOT NULL CHECK(status IN ('CANDIDATE','VERIFIED','ACTIVE','TEMPORARILY_UNAVAILABLE','UNSUPPORTED','DISABLED')),
                enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
                first_discovered TEXT NOT NULL, last_verified TEXT, last_successful_fetch TEXT,
                last_checked TEXT, failure_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT, next_check TEXT, job_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE source_evidence (
                source_id INTEGER NOT NULL REFERENCES career_sources(id),
                method TEXT NOT NULL, url TEXT NOT NULL,
                UNIQUE(source_id, method, url)
            );
            CREATE TABLE provider_policies (provider TEXT PRIMARY KEY, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)));
            CREATE TABLE discovery_items (key TEXT PRIMARY KEY, checked_at TEXT NOT NULL);
            CREATE TABLE host_pauses (host TEXT PRIMARY KEY, until_time TEXT NOT NULL);
            CREATE TABLE refresh_runs (
                id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT NOT NULL,
                fetched INTEGER NOT NULL, new_jobs INTEGER NOT NULL, failed_sources INTEGER NOT NULL,
                successful_sources INTEGER NOT NULL
            );
            CREATE INDEX career_sources_employer ON career_sources(employer_id);
            PRAGMA user_version = 1;
            COMMIT;
        """)
    except Exception:
        connection.rollback()
        raise
