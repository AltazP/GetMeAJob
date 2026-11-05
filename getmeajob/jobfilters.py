"""Dashboard job filters: strict validation, safe defaults, and the bridge to filters.evaluate().

The same parser reads form posts, URLs and saved preferences, so a tampered value can only
ever fall back to a safe default or raise a visible validation message.
"""
from dataclasses import dataclass
import json
import re
from urllib.parse import urlencode

from getmeajob.classify import FIELDS, LEVELS
from getmeajob.filters import SearchFilters
from getmeajob.locations import PROVINCES, Place, find_centre

PREFERENCES_VERSION = 1
EMPLOYMENT = {"internship": "Internship", "co-op": "Co-op", "full-time": "Full-time", "part-time": "Part-time", "contract": "Contract"}
ARRANGEMENTS = {"remote": "Remote", "hybrid": "Hybrid", "on-site": "On-site"}
LEVEL_VALUES = {level.casefold(): level for level in LEVELS}
POSTED = {1: "Last 24 hours", 3: "3 days", 7: "7 days", 14: "14 days", 30: "30 days"}
CURRENCIES = ("CAD", "USD", "EUR", "GBP")
INTERVALS = {"year": "per year", "month": "per month", "week": "per week", "hour": "per hour"}
SORTS = {"match": "Highest match", "recent": "Most recent", "company": "Company", "title": "Job title"}
MAX_ITEMS, MAX_TEXT = 15, 60
FIELD_KEYS = ("applied", "q", "field", "role", "exclude", "type", "arrangement", "level", "city", "province", "radius",
              "remote_region", "unplaced", "canada", "salary", "currency", "interval", "posted", "company", "exclude_company", "min_score", "sort", "unknown")


class FilterError(ValueError):
    """Raised with a message that is safe and useful to show to the user."""


def split_list(value: object, separators: str) -> tuple[str, ...]:
    """Trim, drop control characters, de-duplicate case-insensitively, preserve order."""
    if not isinstance(value, str):
        return ()
    result, seen = [], set()
    for part in re.split(separators, value):
        item = re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", part)).strip()
        if item and item.casefold() not in seen:
            seen.add(item.casefold()); result.append(item)
    if len(result) > MAX_ITEMS or any(len(item) > MAX_TEXT for item in result):
        raise FilterError(f"Use at most {MAX_ITEMS} items of {MAX_TEXT} characters or fewer in each list.")
    return tuple(result)


@dataclass(frozen=True)
class JobFilters:
    q: str = ""
    fields: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    excluded_keywords: tuple[str, ...] = ()
    employment_types: tuple[str, ...] = ()
    work_arrangements: tuple[str, ...] = ()
    experience_levels: tuple[str, ...] = ()
    city: str = ""
    province: str = ""
    radius_km: float = 100.0
    remote_in_region: bool = False
    include_unplaced: bool = False
    minimum_salary: float | None = None
    currency: str = "CAD"
    interval: str = "year"
    posted_days: int | None = None
    companies: tuple[str, ...] = ()
    excluded_companies: tuple[str, ...] = ()
    minimum_score: int = 0
    sort: str = "recent"
    include_unknown: bool = True
    canada_only: bool = True


def choices(raw: object, allowed: dict | tuple, label: str) -> tuple[str, ...]:
    values = raw if isinstance(raw, (list, tuple)) else [raw] if raw else []
    chosen = []
    for value in values:
        key = str(value).strip()
        match = next((a for a in allowed if str(a).casefold() == key.casefold()), None)
        if match is None:
            raise FilterError(f"'{key[:40]}' is not a valid {label} choice.")
        if match not in chosen:
            chosen.append(match)
    return tuple(chosen)


def number(raw: object, label: str, low: float, high: float) -> float | None:
    text = str(raw).strip().replace(",", "") if raw is not None else ""
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        raise FilterError(f"{label} must be a number.") from None
    if value != value or not low <= value <= high:
        raise FilterError(f"{label} must be between {low:g} and {high:g}.")
    return value


def parse(data) -> JobFilters:
    """Build filters from a mapping with .getlist() (forms/queries) or a plain dict (saved JSON)."""
    def many(name):
        if hasattr(data, "getlist"):
            return data.getlist(name)
        value = data.get(name, [])
        return value if isinstance(value, list) else [value]

    def one(name, default=""):
        if hasattr(data, "getlist"):
            values = data.getlist(name)
            return values[0] if values else default
        value = data.get(name, default)
        return value if not isinstance(value, (list, dict)) else default

    def text(name, limit=MAX_TEXT):
        value = re.sub(r"[\x00-\x1f\x7f]", " ", str(one(name))).strip()
        if len(value) > limit:
            raise FilterError(f"Keep each text field to {limit} characters or fewer.")
        return value

    def lines(name, separators):
        return split_list("\n".join(str(x) for x in many(name)), separators)

    province = text("province")
    if province:
        province = next((n for n, c in PROVINCES.items() if province.casefold() in (n.casefold(), c.casefold())), None)
        if province is None:
            raise FilterError("Choose a Canadian province from the list.")
    posted = one("posted")
    posted = int(posted) if str(posted).strip().isdigit() else None
    if posted is not None and posted not in POSTED:
        raise FilterError("Choose a posting date option from the list.")
    sort = text("sort") or "recent"
    if sort not in SORTS:
        raise FilterError("Choose a sort order from the list.")
    currency = (text("currency") or "CAD").upper()
    interval = text("interval") or "year"
    if currency not in CURRENCIES or interval not in INTERVALS:
        raise FilterError("Choose a currency and pay period from the lists.")
    radius = number(one("radius"), "Distance", 1, 20000)
    score = number(one("min_score"), "Minimum match", 0, 100)
    unknown = one("unknown", "1")
    return JobFilters(
        q=text("q", 120), fields=choices(many("field"), FIELDS, "job field"),
        roles=lines("role", r"[\n,;]"), excluded_keywords=lines("exclude", r"[\n,;]"),
        employment_types=choices(many("type"), EMPLOYMENT, "employment type"),
        work_arrangements=choices(many("arrangement"), ARRANGEMENTS, "work arrangement"),
        experience_levels=tuple(LEVEL_VALUES[v.casefold()] for v in choices(many("level"), LEVEL_VALUES, "experience level")),
        city=text("city"), province=province or "", radius_km=radius if radius is not None else 100.0,
        remote_in_region=str(one("remote_region")) in ("1", "true", "True"),
        include_unplaced=str(one("unplaced")) in ("1", "true", "True"),
        minimum_salary=number(one("salary"), "Minimum salary", 0, 10_000_000), currency=currency, interval=interval,
        posted_days=posted, companies=lines("company", r"[\n;]"), excluded_companies=lines("exclude_company", r"[\n;]"),
        minimum_score=int(score or 0), sort=sort, include_unknown=str(unknown) not in ("0", "false", "False"),
        canada_only=str(one("canada", "1")) not in ("0", "false", "False"))


def load_preferences(raw: str | None) -> JobFilters:
    """Saved JSON that is malformed, tampered with or from another version yields safe defaults."""
    if not raw:
        return JobFilters()
    try:
        saved = json.loads(raw)
        if not isinstance(saved, dict) or saved.get("version") != PREFERENCES_VERSION or not isinstance(saved.get("filters"), dict):
            return JobFilters()
        return parse(saved["filters"])
    except (ValueError, TypeError, AttributeError):
        return JobFilters()


def dump_preferences(filters: JobFilters) -> str:
    return json.dumps({"version": PREFERENCES_VERSION, "filters": to_query(filters)}, sort_keys=True)


def to_query(filters: JobFilters) -> dict:
    """Non-default values only, in the form parse() accepts. Short, bounded, no secrets."""
    out: dict = {}
    d = JobFilters()
    for name, key in (("fields", "field"), ("employment_types", "type"), ("work_arrangements", "arrangement"), ("experience_levels", "level")):
        if getattr(filters, name):
            out[key] = list(getattr(filters, name))
    for name, key in (("roles", "role"), ("excluded_keywords", "exclude"), ("companies", "company"), ("excluded_companies", "exclude_company")):
        if getattr(filters, name):
            out[key] = "\n".join(getattr(filters, name))
    for name, key in (("q", "q"), ("city", "city"), ("province", "province")):
        if getattr(filters, name):
            out[key] = getattr(filters, name)
    if filters.city and filters.radius_km != d.radius_km:
        out["radius"] = f"{filters.radius_km:g}"
    if filters.remote_in_region:
        out["remote_region"] = "1"
    if filters.include_unplaced:
        out["unplaced"] = "1"
    if filters.minimum_salary is not None:
        out["salary"] = f"{filters.minimum_salary:g}"
        out["currency"], out["interval"] = filters.currency, filters.interval
    if filters.posted_days is not None:
        out["posted"] = str(filters.posted_days)
    if filters.minimum_score:
        out["min_score"] = str(filters.minimum_score)
    if filters.sort != d.sort:
        out["sort"] = filters.sort
    if not filters.include_unknown:
        out["unknown"] = "0"
    if not filters.canada_only:
        out["canada"] = "0"
    return out


def query_string(filters: JobFilters, offset: int = 0) -> str:
    """Pagination/apply URL query. `offset` stays first so links look like /jobs?offset=20&..."""
    pairs = [("offset", str(offset)), ("applied", "1")]
    for key, value in to_query(filters).items():
        pairs.extend((key, v) for v in value) if isinstance(value, list) else pairs.append((key, value))
    return urlencode(pairs)


def centre(filters: JobFilters) -> Place | None:
    return find_centre(filters.city, filters.province) if filters.city else None


def validate_location(filters: JobFilters) -> Place | None:
    """Return the resolved centre, or raise a helpful message instead of guessing coordinates."""
    if not filters.city:
        if filters.remote_in_region and not filters.province:
            raise FilterError("Choose a city or province before restricting remote jobs to a region.")
        return None
    place = centre(filters)
    if place is None:
        raise FilterError(f"'{filters.city}' could not be located on the offline city list"
                          f"{' for ' + filters.province if filters.province else ' (add a province if the name is shared)'}. "
                          "No coordinates were guessed; choose a listed Canadian city.")
    return place


def search_filters(filters: JobFilters) -> SearchFilters:
    """Map dashboard choices onto the shared Phase 2 SearchFilters/evaluate() definition."""
    place = validate_location(filters)
    region_terms = ()
    if filters.remote_in_region:
        province = place.province if place else filters.province
        region_terms = tuple(t for t in (province, PROVINCES.get(province, ""), "Canada") if t)
    kwargs = dict(
        titles=filters.roles, excluded_keywords=filters.excluded_keywords, companies=filters.companies,
        excluded_companies=filters.excluded_companies, employment_types=filters.employment_types,
        work_arrangements=filters.work_arrangements, experience_levels=tuple(l.casefold() for l in filters.experience_levels),
        minimum_salary=filters.minimum_salary, salary_currency=filters.currency, salary_interval=filters.interval,
        posted_within_days=filters.posted_days, include_unknown=filters.include_unknown,
        company_match="contains", excluded_scope="title", remote_regions=region_terms,
        include_remote_outside_radius=filters.remote_in_region)
    if place:
        kwargs.update(city=place.name, latitude=place.latitude, longitude=place.longitude, radius_km=filters.radius_km)
    else:
        kwargs["radius_km"] = None
    return SearchFilters(**kwargs)


def province_matches(locations: tuple[str, ...], province: str) -> bool:
    from getmeajob.filters import remote_names_region
    return remote_names_region(locations, (province, PROVINCES[province]))


def chips(filters: JobFilters) -> list[str]:
    """Human-readable list of every active filter."""
    out = []
    if filters.canada_only: out.append("Canada only")
    if filters.q: out.append(f"Search: {filters.q}")
    if filters.fields: out.append("Field: " + ", ".join(filters.fields))
    if filters.roles: out.append("Role contains: " + ", ".join(filters.roles))
    if filters.excluded_keywords: out.append("Title excludes: " + ", ".join(filters.excluded_keywords))
    if filters.employment_types: out.append("Type: " + ", ".join(EMPLOYMENT[v] for v in filters.employment_types))
    if filters.work_arrangements: out.append("Arrangement: " + ", ".join(ARRANGEMENTS[v] for v in filters.work_arrangements))
    if filters.experience_levels: out.append("Level: " + ", ".join(filters.experience_levels))
    if filters.city: out.append(f"Within {filters.radius_km:g} km of {filters.city}{', ' + filters.province if filters.province else ''}")
    elif filters.province: out.append(f"Province: {filters.province}")
    if filters.remote_in_region: out.append("Remote jobs naming my region included")
    if filters.city and filters.include_unplaced: out.append("Locations not on the offline city list included")
    if filters.minimum_salary is not None: out.append(f"Salary ≥ {filters.minimum_salary:g} {filters.currency} {INTERVALS[filters.interval]}")
    if filters.posted_days is not None: out.append("Posted: " + POSTED[filters.posted_days])
    if filters.companies: out.append("Companies: " + ", ".join(filters.companies))
    if filters.excluded_companies: out.append("Excluding companies: " + ", ".join(filters.excluded_companies))
    if filters.minimum_score: out.append(f"Match ≥ {filters.minimum_score}%")
    return out
