"""Greenhouse public Job Board GET API, one configured employer at a time."""

from getmeajob.models import Job, timestamp
from getmeajob.sources import Board, SourceError
from getmeajob.sources.http import JsonClient
from getmeajob.sources.normalize import application_url, arrangement, employment, experience, plain, required


class GreenhouseSource:
    def __init__(self, client: JsonClient):
        self.client = client

    def fetch(self, board: Board) -> list[Job]:
        data = self.client.get(f"https://boards-api.greenhouse.io/v1/boards/{board.token}/jobs?content=true")
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            raise SourceError("Greenhouse returned an unexpected job-list schema.")
        return [self.normalize(row, board) for row in data["jobs"]]

    @staticmethod
    def normalize(row: dict, board: Board) -> Job:
        if not isinstance(row, dict):
            raise SourceError("Greenhouse posting is not an object.")
        title = required(row, "title")
        description = plain(row.get("content"))
        location = row.get("location")
        label = location.get("name") if isinstance(location, dict) else None
        locations = (label.strip(),) if isinstance(label, str) and label.strip() else ()
        metadata = row.get("metadata") or []
        if not isinstance(metadata, list):
            raise SourceError("Greenhouse metadata is not a list.")
        fields = {item["name"].casefold(): item.get("value") for item in metadata
                  if isinstance(item, dict) and isinstance(item.get("name"), str)}
        level, years, evidence = experience(title, description, fields.get("experience level"),
                                            fields.get("minimum years experience"))
        return Job(
            source="greenhouse", board=board.token, source_id=required(row, "id"),
            employer=board.employer, role=title,
            application_url=application_url(required(row, "absolute_url")),
            locations=locations, description=description,
            employment_type=employment(fields.get("employment type"), title),
            work_arrangement=arrangement(fields.get("work arrangement"), locations),
            posted_at=timestamp(row.get("first_published")),
            source_updated_at=timestamp(row.get("updated_at")), industry=board.industry,
            experience_level=level, minimum_experience_years=years, experience_evidence=evidence,
        )
