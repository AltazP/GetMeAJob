"""Persistent employer identity, source evidence and verification lifecycle."""

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from getmeajob.locations import normalized
from getmeajob.models import utc_now
from getmeajob.sources import Board, SourceError
from getmeajob.urls import PROVIDERS, board_url, detect, public_url, unsupported_provider


def after(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def elapsed(value: str | None, seconds: float) -> bool:
    return value is None or datetime.fromisoformat(value) <= datetime.now(timezone.utc) - timedelta(seconds=seconds)


class Registry:
    def __init__(self, store):
        self.store, self.db = store, store.connection

    def add(self, name: str, website: str | None = None, careers_url: str | None = None,
            ats_url: str | None = None, method: str = "manual", evidence: str | None = None) -> int:
        if not isinstance(name, str) or not name.strip() or len(name) > 200 or any(ord(c) < 32 for c in name):
            raise SourceError("Employer name must be nonempty text of at most 200 characters.")
        website = public_url(website) if website else None
        url = public_url(ats_url or careers_url or website) if (ats_url or careers_url or website) else None
        evidence = public_url(evidence) if evidence else url
        detected = detect(url) if url else None
        provider = detected.provider if detected else (unsupported_provider(url) if url else "custom")
        source_key = f"{provider}:{detected.region}:{detected.token}" if detected else f"page:{url or normalized(name)}"
        keys = ["name:" + normalized(name)]
        if website:
            keys.append("website:" + urlsplit(website).hostname.removeprefix("www."))
        now = utc_now()
        with self.db:
            ids = {row[0] for key in keys for row in self.db.execute("SELECT employer_id FROM employer_keys WHERE key=?", (key,))}
            ids.update(row[0] for row in self.db.execute("SELECT employer_id FROM career_sources WHERE source_key=?", (source_key,)))
            if ids:
                employer_id = min(ids)
                for other in ids - {employer_id}:
                    self.db.execute("UPDATE career_sources SET employer_id=? WHERE employer_id=?", (employer_id, other))
                    self.db.execute("UPDATE employer_keys SET employer_id=? WHERE employer_id=?", (employer_id, other))
                    self.db.execute("UPDATE employers SET enabled=MIN(enabled,(SELECT enabled FROM employers WHERE id=?)),first_discovered=MIN(first_discovered,(SELECT first_discovered FROM employers WHERE id=?)) WHERE id=?", (other, other, employer_id))
                    self.db.execute("DELETE FROM employers WHERE id=?", (other,))
                if website:
                    self.db.execute("UPDATE employers SET official_website=COALESCE(official_website,?) WHERE id=?", (website, employer_id))
            else:
                employer_id = self.db.execute("INSERT INTO employers(canonical_name,official_website,first_discovered) VALUES(?,?,?)", (name.strip(), website, now)).lastrowid
            for key in keys:
                self.db.execute("INSERT OR IGNORE INTO employer_keys VALUES(?,?)", (key, employer_id))
            self.db.execute("""INSERT INTO career_sources(employer_id,source_key,careers_page,provider,board_identifier,region,
                discovery_source,evidence_url,status,first_discovered) VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(source_key) DO NOTHING""", (
                employer_id, source_key, public_url(careers_url) if careers_url else url, provider,
                detected.token if detected else None, detected.region if detected else "global",
                method, evidence, "CANDIDATE" if detected or url else "UNSUPPORTED", now))
            source_id = self.db.execute("SELECT id FROM career_sources WHERE source_key=?", (source_key,)).fetchone()[0]
            if evidence:
                self.db.execute("INSERT OR IGNORE INTO source_evidence VALUES(?,?,?)", (source_id, method, evidence))
        return source_id

    def source_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM career_sources").fetchone()[0]

    def rows(self) -> list[dict]:
        return [dict(row) for row in self.db.execute("""SELECT s.*,e.canonical_name,e.official_website,
            e.enabled AS employer_enabled,COALESCE(p.enabled,1) AS provider_enabled
            FROM career_sources s JOIN employers e ON e.id=s.employer_id
            LEFT JOIN provider_policies p ON p.provider=s.provider ORDER BY s.id""")]

    def eligible(self, verify_hours: float = 24) -> list[dict]:
        return [row for row in self.rows() if self.enabled(row) and row["status"] in {"VERIFIED", "ACTIVE"}
                and not elapsed(row["last_verified"], verify_hours * 3600)]

    @staticmethod
    def enabled(row: dict) -> bool:
        return bool(row["enabled"] and row["employer_enabled"] and row["provider_enabled"])

    @staticmethod
    def board(row: dict) -> Board:
        return Board(row["provider"], row["board_identifier"], row["canonical_name"], region=row["region"])

    def success(self, source_id: int, count: int, active: bool = False) -> None:
        now = utc_now()
        with self.db:
            self.db.execute("""UPDATE career_sources SET status=?,last_verified=?,last_successful_fetch=?,last_checked=?,
                failure_count=0,last_error=NULL,next_check=NULL,job_count=? WHERE id=? AND enabled=1""",
                ("ACTIVE" if active else "VERIFIED", now, now, now, count, source_id))

    def failure(self, source_id: int, error: str, unsupported: bool = False) -> None:
        row = self.db.execute("SELECT failure_count FROM career_sources WHERE id=?", (source_id,)).fetchone()
        failures = row[0] + 1
        with self.db:
            self.db.execute("UPDATE career_sources SET status=?,last_checked=?,failure_count=?,last_error=?,next_check=? WHERE id=?",
                            ("UNSUPPORTED" if unsupported else "TEMPORARILY_UNAVAILABLE", utc_now(), failures,
                             error[:500], after(min(86400, 60 * 2 ** min(failures - 1, 10))), source_id))

    def set_enabled(self, enabled: bool, *, employer_id: int | None = None,
                    source_id: int | None = None, provider: str | None = None) -> None:
        with self.db:
            if provider:
                if provider not in PROVIDERS | {"workday", "custom"}:
                    raise SourceError("Unknown provider.")
                self.db.execute("INSERT INTO provider_policies VALUES(?,?) ON CONFLICT(provider) DO UPDATE SET enabled=excluded.enabled", (provider, int(enabled)))
                return
            if employer_id is not None:
                cursor = self.db.execute("UPDATE employers SET enabled=? WHERE id=?", (int(enabled), employer_id))
                where, identity = "employer_id", employer_id
            else:
                cursor = self.db.execute("SELECT id FROM career_sources WHERE id=?", (source_id,))
                if cursor.fetchone() is None:
                    raise SourceError("Unknown source ID.")
                where, identity = "id", source_id
            if employer_id is not None and not cursor.rowcount:
                raise SourceError("Unknown employer ID.")
            self.db.execute(f"UPDATE career_sources SET enabled=?,status=?,last_verified=NULL WHERE {where}=?",
                            (int(enabled), "CANDIDATE" if enabled else "DISABLED", identity))

    def import_csv(self, path: Path) -> int:
        if path.stat().st_size > 2 * 1024 * 1024:
            raise SourceError("CSV exceeds 2 MiB limit.")
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            allowed = {"name", "website", "careers_url", "ats_url"}
            if not reader.fieldnames or "name" not in reader.fieldnames or set(reader.fieldnames) - allowed or len(set(reader.fieldnames)) != len(reader.fieldnames):
                raise SourceError("CSV columns must be name, website, careers_url, ats_url (name required).")
            try:
                rows = list(reader)
            except csv.Error as exc:
                raise SourceError("CSV could not be parsed safely.") from exc
        if len(rows) > 10000:
            raise SourceError("CSV exceeds 10,000 employer rows.")
        # Validate every row on a snapshot before writing anything to the live registry.
        from getmeajob.storage import JobStore
        with JobStore(Path(":memory:")) as preview:
            self.db.backup(preview.connection)
            candidate = Registry(preview)
            for index, row in enumerate(rows, 2):
                if None in row or any(value is None for value in row.values()):
                    raise SourceError(f"Malformed CSV row {index}.")
                try:
                    candidate.add(**{key: value.strip() or None for key, value in row.items()})
                except SourceError as exc:
                    raise SourceError(f"Invalid CSV row {index}: {exc}") from exc
        # Validated input; add() commits individual idempotent rows, safe to repeat if interrupted.
        for row in rows:
            self.add(**{key: value.strip() or None for key, value in row.items()}, method="csv")
        return len(rows)

    def paused(self, host: str) -> bool:
        row = self.db.execute("SELECT until_time FROM host_pauses WHERE host=?", (host,)).fetchone()
        return row is not None and datetime.fromisoformat(row[0]) > datetime.now(timezone.utc)

    def pause(self, host: str, seconds: float) -> None:
        try:
            until = after(seconds)
        except OverflowError:
            until = datetime.max.replace(tzinfo=timezone.utc).isoformat()
        with self.db:
            self.db.execute("INSERT INTO host_pauses VALUES(?,?) ON CONFLICT(host) DO UPDATE SET until_time=MAX(until_time,excluded.until_time)", (host, until))

    def cache(self, source_id: int, jobs: list, fetched_at: str | None = None) -> None:
        import json
        with self.db:
            self.db.execute("INSERT INTO source_cache VALUES(?,?,?) ON CONFLICT(source_id) DO UPDATE SET payload=excluded.payload,fetched_at=excluded.fetched_at",
                            (source_id, json.dumps([job.to_dict() for job in jobs], ensure_ascii=True), fetched_at or utc_now()))

    def cached(self, source_id: int, minutes: float | None = 60):
        import json
        from getmeajob.models import Job
        row = self.db.execute("SELECT payload,fetched_at FROM source_cache WHERE source_id=?", (source_id,)).fetchone()
        if row is None or (minutes is not None and elapsed(row[1], minutes * 60)):
            return None
        return [Job.from_dict(item) for item in json.loads(row[0])], row[1]

    def seen(self, key: str, seconds: float | None = None) -> bool:
        row = self.db.execute("SELECT checked_at FROM discovery_items WHERE key=?", (key,)).fetchone()
        return row is not None and (seconds is None or not elapsed(row[0], seconds))

    def mark_seen(self, key: str) -> None:
        with self.db:
            self.db.execute("INSERT INTO discovery_items VALUES(?,?) ON CONFLICT(key) DO UPDATE SET checked_at=excluded.checked_at", (key, utc_now()))

    def coverage(self, verification_hours: float = 24) -> dict:
        rows = self.rows()
        enabled = [row for row in rows if self.enabled(row)]
        eligible = self.eligible(verification_hours)
        last = self.db.execute("SELECT * FROM refresh_runs ORDER BY id DESC LIMIT 1").fetchone()
        return {
            "metrics": "Observed local database counts; not estimates of all employers or vacancies",
            "total_known_employers": self.db.execute("SELECT COUNT(*) FROM employers").fetchone()[0],
            "verified_employers": len({row["employer_id"] for row in eligible}),
            "active_employer_boards": sum(row["status"] == "ACTIVE" for row in eligible),
            "verified_enabled_boards": len(eligible),
            "stale_sources": sum(row["status"] in {"VERIFIED", "ACTIVE"} and elapsed(row["last_verified"], verification_hours * 3600) for row in enabled),
            "supported_providers": sorted(PROVIDERS),
            "jobs_collected_per_provider": {row[0]: row[1] for row in self.db.execute("SELECT source,COUNT(DISTINCT job_id) FROM observations GROUP BY source")},
            "unique_jobs_stored": self.store.count(),
            "failed_sources": sum(row["status"] == "TEMPORARILY_UNAVAILABLE" for row in rows),
            "unsupported_sources": sum(row["status"] == "UNSUPPORTED" or row["provider"] not in PROVIDERS for row in rows),
            "disabled_sources": sum(not self.enabled(row) for row in rows),
            "last_successful_refresh": self.db.execute("SELECT MAX(last_successful_fetch) FROM career_sources").fetchone()[0],
            "last_run": dict(last) if last else None,
            "new_jobs_last_run": last["new_jobs"] if last else 0,
        }
