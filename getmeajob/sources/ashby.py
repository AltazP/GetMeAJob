"""Ashby documented public job-board GET API; unlisted jobs are never collected."""

from getmeajob.models import Job, Salary, number, timestamp
from getmeajob.sources import Board, SourceError
from getmeajob.sources.normalize import application_url, employment, experience, plain, required


class AshbySource:
    def __init__(self, client, include_compensation: bool = True):
        self.client, self.include_compensation = client, include_compensation

    def fetch(self, board: Board) -> list[Job]:
        include = str(self.include_compensation).lower()
        data = self.client.get(f"https://api.ashbyhq.com/posting-api/job-board/{board.token}?includeCompensation={include}")
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            raise SourceError("Ashby returned an unexpected job-list schema.")
        jobs = []
        for row in data["jobs"]:
            if not isinstance(row, dict):
                raise SourceError("Ashby posting is not an object.")
            if row.get("isListed") is True:
                jobs.append(self.normalize(row, board))
        return jobs

    @staticmethod
    def normalize(row: dict, board: Board) -> Job:
        if row.get("isListed") is not True:
            raise SourceError("Ashby posting is not explicitly publicly listed.")
        original = application_url(required(row, "jobUrl"))
        title = required(row, "title")
        description = plain(row.get("descriptionPlain") or row.get("descriptionHtml"))
        secondary = row.get("secondaryLocations") or []
        if not isinstance(secondary, list):
            raise SourceError("Ashby secondaryLocations is not a list.")
        labels = [row.get("location"), *(item.get("location") for item in secondary if isinstance(item, dict))]
        locations = tuple(dict.fromkeys(label.strip() for label in labels if isinstance(label, str) and label.strip()))
        pay = row.get("compensation")
        if pay is not None and not isinstance(pay, dict):
            raise SourceError("Ashby compensation is not an object.")
        summary, compensation = [], None
        if pay is not None:
            summary = pay.get("summaryComponents") or []
            tiers = pay.get("compensationTiers") or []
            if not isinstance(summary, list) or not isinstance(tiers, list):
                raise SourceError("Ashby compensation components/tiers must be lists.")
            def components(items):
                if not isinstance(items, list):
                    raise SourceError("Ashby compensation components must be a list.")
                return [{"type": plain(item.get("compensationType")), "currency": plain(item.get("currencyCode")) or None,
                         "interval": plain(item.get("interval")) or None, "minimum": number(item.get("minValue")),
                         "maximum": number(item.get("maxValue"))} for item in items if isinstance(item, dict)]
            compensation = {"summary": plain(pay.get("compensationTierSummary")) or None,
                "components": components(summary),
                "tiers": [{"title": plain(tier.get("title")) or None, "summary": plain(tier.get("tierSummary")) or None,
                           "components": components(tier.get("components") or [])} for tier in tiers if isinstance(tier, dict)]}
        salary = Salary()
        salaries = [item for item in (compensation or {}).get("components", []) if item["type"] == "Salary"]
        if len(salaries) == 1:
            item = salaries[0]
            minimum, maximum = item["minimum"], item["maximum"]
            if minimum is not None and maximum is not None and maximum < minimum:
                minimum = maximum = None
            salary = Salary(minimum, maximum, item["currency"],
                            {"1 YEAR": "year", "1 MONTH": "month", "1 WEEK": "week", "1 HOUR": "hour"}.get(item["interval"]))
        level, years, evidence = experience(title, description)
        return Job(source="ashby", board=board.token, source_id=str(row.get("id") or original),
            employer=board.employer, role=title, locations=locations, description=description,
            original_url=original, application_url=application_url(required(row, "applyUrl")),
            department=plain(row.get("department")) or None, compensation=compensation, salary=salary,
            employment_type=employment(row.get("employmentType"), title),
            # isRemote=true can mean hybrid; only workplaceType resolves that ambiguity.
            work_arrangement={"Remote": "remote", "Hybrid": "hybrid", "OnSite": "on-site"}.get(row.get("workplaceType")) if isinstance(row.get("workplaceType"), str) else None,
            posted_at=timestamp(row.get("publishedAt")), industry=board.industry,
            experience_level=level, minimum_experience_years=years, experience_evidence=evidence)
