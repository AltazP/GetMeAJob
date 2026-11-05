"""Source-independent discovery records. None means the source did not tell us."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import math


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def timestamp(value: object) -> str | None:
    """Accept explicit ISO timestamps, never replace a missing date with today."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def number(value: object) -> float | None:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        return None
    return float(value)


@dataclass(frozen=True)
class Salary:
    minimum: float | None = None
    maximum: float | None = None
    currency: str | None = None
    interval: str | None = None


@dataclass(frozen=True)
class Job:
    source: str
    board: str
    source_id: str
    employer: str
    role: str
    application_url: str
    locations: tuple[str, ...] = ()
    description: str = ""
    salary: Salary = field(default_factory=Salary)
    employment_type: str | None = None
    work_arrangement: str | None = None
    posted_at: str | None = None
    source_updated_at: str | None = None
    source_created_at: str | None = None
    industry: str | None = None
    experience_level: str | None = None
    minimum_experience_years: float | None = None
    experience_evidence: str | None = None
    department: str | None = None
    original_url: str | None = None
    compensation: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Job":
        data = dict(data)
        data["salary"] = Salary(**data["salary"])
        data["locations"] = tuple(data["locations"])
        return cls(**data)
