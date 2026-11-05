"""Lever public postings GET API with bounded pagination, global and EU regions."""

from datetime import datetime, timezone

from getmeajob.models import Job, Salary, number
from getmeajob.sources import Board, SourceError
from getmeajob.sources.http import JsonClient
from getmeajob.sources.normalize import (
    INTERVAL, application_url, arrangement, employment, experience, mapped, plain, required,
)


class LeverSource:
    PAGE_SIZE = 100
    MAX_PAGES = 100

    def __init__(self, client: JsonClient):
        self.client = client

    def fetch(self, board: Board) -> list[Job]:
        host = "api.eu.lever.co" if board.region == "eu" else "api.lever.co"
        jobs, seen = [], set()
        for page in range(self.MAX_PAGES):
            rows = self.client.get(f"https://{host}/v0/postings/{board.token}?mode=json&skip={page * self.PAGE_SIZE}&limit={self.PAGE_SIZE}")
            if not isinstance(rows, list):
                raise SourceError("Lever returned an unexpected job-list schema.")
            for row in rows:
                job = self.normalize(row, board)
                if job.source_id in seen:
                    raise SourceError("Lever repeated a posting while paginating; retry this board later.")
                seen.add(job.source_id)
                jobs.append(job)
            if len(rows) < self.PAGE_SIZE:
                return jobs
        raise SourceError("Lever pagination exceeded the safety limit; results were not marked complete.")

    @staticmethod
    def normalize(row: dict, board: Board) -> Job:
        if not isinstance(row, dict):
            raise SourceError("Lever posting is not an object.")
        title = required(row, "text")
        categories = row.get("categories") or {}
        if not isinstance(categories, dict):
            raise SourceError("Lever categories is not an object.")
        labels = categories.get("allLocations") or []
        if not isinstance(labels, list):
            raise SourceError("Lever allLocations is not a list.")
        locations = tuple(dict.fromkeys(label.strip() for label in [categories.get("location"), *labels]
                                       if isinstance(label, str) and label.strip()))
        lists = row.get("lists") or []
        if not isinstance(lists, list):
            raise SourceError("Lever description lists is not a list.")
        description = " ".join(filter(None, [plain(row.get("descriptionPlain") or row.get("description")),
            *(plain(item.get("content")) for item in lists if isinstance(item, dict)),
            plain(row.get("additionalPlain") or row.get("additional"))]))
        pay = row.get("salaryRange") or {}
        if not isinstance(pay, dict):
            raise SourceError("Lever salaryRange is not an object.")
        minimum, maximum = number(pay.get("min")), number(pay.get("max"))
        if minimum is not None and maximum is not None and maximum < minimum:
            minimum = maximum = None
        currency = pay.get("currency")
        currency = currency.upper() if isinstance(currency, str) and len(currency) == 3 else None
        level, years, evidence = experience(title, description, categories.get("level"))
        created = None
        raw_created = number(row.get("createdAt"))
        if raw_created is not None:
            try:
                created = datetime.fromtimestamp(raw_created / 1000, timezone.utc).isoformat()
            except (ValueError, OverflowError, OSError):
                pass
        return Job(
            source="lever", board=f"{board.region}:{board.token}", source_id=required(row, "id"),
            employer=board.employer, role=title,
            application_url=application_url(required(row, "applyUrl")),
            locations=locations, description=description,
            salary=Salary(minimum, maximum, currency, mapped(pay.get("interval"), INTERVAL)),
            employment_type=employment(categories.get("commitment"), title),
            work_arrangement=arrangement(row.get("workplaceType"), locations),
            # createdAt is creation, not verified publication; posted_at stays unknown.
            source_created_at=created, industry=board.industry,
            experience_level=level, minimum_experience_years=years, experience_evidence=evidence,
        )
