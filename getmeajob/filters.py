"""Deterministic filters; unknown values never silently become verified matches."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from getmeajob.locations import DEFAULT_PLACES, Place, distance_km, resolve
from getmeajob.models import Job


@dataclass(frozen=True)
class SearchFilters:
    titles: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    excluded_keywords: tuple[str, ...] = ()
    companies: tuple[str, ...] = ()
    excluded_companies: tuple[str, ...] = ()
    industries: tuple[str, ...] = ()
    employment_types: tuple[str, ...] = ()
    work_arrangements: tuple[str, ...] = ()
    experience_levels: tuple[str, ...] = ()
    max_experience_years: float | None = None
    minimum_salary: float | None = None
    salary_currency: str = "CAD"
    salary_interval: str = "year"
    posted_within_days: int | None = None
    city: str = "Waterloo, Ontario"
    latitude: float = 43.4643
    longitude: float = -80.5204
    radius_km: float | None = 100
    include_remote_outside_radius: bool = False
    include_unknown: bool = False


@dataclass(frozen=True)
class Match:
    accepted: bool
    distance_km: float | None = None
    unknown_fields: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()


def evaluate(job: Job, filters: SearchFilters, places: tuple[Place, ...] = DEFAULT_PLACES,
             now: datetime | None = None) -> Match:
    reasons, unknown = [], []
    text = f"{job.role}\n{job.description}".casefold()
    if filters.titles and not any(term.casefold() in job.role.casefold() for term in filters.titles):
        reasons.append("title")
    if any(term.casefold() not in text for term in filters.keywords):
        reasons.append("keywords")
    if any(term.casefold() in text for term in filters.excluded_keywords):
        reasons.append("excluded keyword")
    employer = job.employer.casefold()
    if filters.companies and employer not in {item.casefold() for item in filters.companies}:
        reasons.append("company")
    if employer in {item.casefold() for item in filters.excluded_companies}:
        reasons.append("excluded company")

    def check(field: str, value: object, allowed: tuple[str, ...]) -> None:
        if allowed:
            if value is None:
                unknown.append(field)
            elif str(value).casefold() not in {item.casefold() for item in allowed}:
                reasons.append(field)

    check("industry", job.industry, filters.industries)
    check("employment type", job.employment_type, filters.employment_types)
    check("work arrangement", job.work_arrangement, filters.work_arrangements)
    check("experience level", job.experience_level, filters.experience_levels)
    if filters.max_experience_years is not None:
        if job.minimum_experience_years is None:
            unknown.append("experience years")
        elif job.minimum_experience_years > filters.max_experience_years:
            reasons.append("experience years")
    if filters.minimum_salary is not None:
        salary = job.salary
        if (salary.minimum is None or salary.currency is None or salary.interval is None):
            unknown.append("salary")
        elif (salary.currency.casefold() != filters.salary_currency.casefold()
              or salary.interval != filters.salary_interval
              or salary.minimum < filters.minimum_salary):
            reasons.append("salary")
    if filters.posted_within_days is not None:
        if job.posted_at is None:
            unknown.append("posting date")
        else:
            posted = datetime.fromisoformat(job.posted_at)
            now = now or datetime.now(timezone.utc)
            if not now - timedelta(days=filters.posted_within_days) <= posted <= now:
                reasons.append("posting date")
    distances = [distance_km(filters.latitude, filters.longitude, place)
                 for location in job.locations if (place := resolve(location, places))]
    distance = min(distances) if distances else None
    if filters.radius_km is not None:
        bypass = filters.include_remote_outside_radius and job.work_arrangement == "remote"
        if not bypass:
            if distance is not None and distance <= filters.radius_km:
                pass
            elif len(distances) < len(job.locations) or not job.locations:
                unknown.append("location distance")
            else:
                reasons.append("location distance")
    if unknown and not filters.include_unknown:
        reasons.extend(f"unknown {field}" for field in unknown)
    return Match(not reasons, distance, tuple(unknown), tuple(reasons))
