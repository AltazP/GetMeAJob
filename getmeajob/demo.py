"""Clearly synthetic fixtures, independent of live APIs and the live database."""

from dataclasses import replace

from getmeajob.models import Job, Salary


def synthetic_jobs() -> list[Job]:
    first = Job(
        source="demo-greenhouse", board="fictional", source_id="1", employer="Fictional Maple Labs",
        role="Entry-level Python Developer", application_url="https://example.invalid/jobs/1",
        locations=("Waterloo, Ontario",), description="Python and SQLite. No prior experience required.",
        salary=Salary(60000, 75000, "CAD", "year"), employment_type="full-time",
        work_arrangement="hybrid", posted_at="2026-11-01T00:00:00+00:00",
        industry="Software", experience_level="entry-level", minimum_experience_years=0,
        experience_evidence="No prior experience required (synthetic)",
    )
    return [
        first,
        replace(first, source="demo-lever", source_id="same-job", application_url=first.application_url + "?utm_source=demo"),
        Job(source="demo-lever", board="fictional", source_id="2", employer="Fictional Cedar Tools",
            role="Software Co-op", application_url="https://example.invalid/jobs/2",
            locations=("Guelph, Ontario",), description="Python co-op opportunity.", employment_type="co-op"),
        replace(first, source_id="3", role="Senior Developer", locations=("Ottawa, Ontario",),
                application_url="https://example.invalid/jobs/3", minimum_experience_years=8),
        Job(source="demo-greenhouse", board="fictional", source_id="4", employer="Fictional Unknown Fields",
            role="Software Developer", application_url="https://example.invalid/jobs/4"),
    ]
