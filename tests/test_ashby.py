"""Synthetic Ashby API fixtures, including optional compensation and unlisted jobs."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from getmeajob.config import load_settings
from getmeajob.models import Job, Salary
from getmeajob.sources import Board, SourceError
from getmeajob.sources.ashby import AshbySource
from tests.test_discovery import FakeClient


BOARD = Board("ashby", "fictional", "Fictional Employer")


def row(**changes):
    return {"id": "fictional-id", "isListed": True, "title": "Python Developer", "location": "Waterloo, Ontario",
            "secondaryLocations": [{"location": "Guelph, Ontario"}], "department": "Engineering",
            "descriptionPlain": "Python and SQL", "employmentType": "FullTime", "isRemote": True,
            "workplaceType": "Hybrid", "publishedAt": "2026-11-01T12:00:00Z",
            "jobUrl": "https://jobs.ashbyhq.com/fictional/1", "applyUrl": "https://jobs.ashbyhq.com/fictional/1/application",
            **changes}


class AshbyTests(unittest.TestCase):
    def test_normalization_and_original_urls(self):
        item = AshbySource.normalize(row(), BOARD)
        self.assertEqual(item.department, "Engineering")
        self.assertEqual(item.employment_type, "full-time")
        self.assertEqual(item.work_arrangement, "hybrid")
        self.assertEqual(len(item.locations), 2)
        self.assertNotEqual(item.application_url, item.original_url)
        self.assertEqual(item.posted_at, "2026-11-01T12:00:00+00:00")
        self.assertEqual(Job.from_dict(item.to_dict()), item)

    def test_only_explicitly_public_jobs_collected(self):
        client = FakeClient({"jobs": [row(), row(isListed=False), row(isListed=None), {"title": "Hidden"}]})
        self.assertEqual(len(AshbySource(client).fetch(BOARD)), 1)
        self.assertIn("includeCompensation=true", client.urls[0])
        with self.assertRaises(SourceError):
            AshbySource.normalize(row(isListed=False), BOARD)

    def test_optional_compensation_can_be_disabled(self):
        client = FakeClient({"jobs": []})
        self.assertEqual(AshbySource(client, include_compensation=False).fetch(BOARD), [])
        self.assertIn("includeCompensation=false", client.urls[0])

    def test_missing_metadata_not_inferred_from_is_remote(self):
        item = AshbySource.normalize(row(id=None, workplaceType=None, employmentType=None,
                                        location=None, secondaryLocations=[], department=None, publishedAt=None), BOARD)
        self.assertIsNone(item.work_arrangement)
        self.assertIsNone(item.employment_type)
        self.assertIsNone(item.department)
        self.assertIsNone(item.posted_at)
        self.assertEqual(item.locations, ())
        self.assertEqual(item.salary, Salary())
        self.assertEqual(item.source_id, item.original_url)

    def test_salary_and_other_components_kept_separate(self):
        pay = {"compensationTierSummary": "Source summary", "summaryComponents": [
            {"compensationType": "Salary", "interval": "1 YEAR", "currencyCode": "CAD", "minValue": 60000, "maxValue": 80000},
            {"compensationType": "EquityPercentage", "interval": "NONE", "minValue": 1, "maxValue": 2},
        ], "compensationTiers": [{"title": "Canada", "components": []}]}
        item = AshbySource.normalize(row(compensation=pay), BOARD)
        self.assertEqual(item.salary, Salary(60000, 80000, "CAD", "year"))
        self.assertEqual(item.compensation["tiers"][0]["title"], "Canada")
        self.assertEqual(len(item.compensation["components"]), 2)

    def test_multiple_salary_components_not_merged_into_guessed_range(self):
        pay = {"summaryComponents": [{"compensationType": "Salary", "minValue": 100}, {"compensationType": "Salary", "minValue": 200}]}
        self.assertEqual(AshbySource.normalize(row(compensation=pay), BOARD).salary, Salary())

    def test_schema_and_required_url_errors(self):
        for data in ({}, [], {"jobs": "bad"}, {"jobs": [None]}, {"jobs": [row(applyUrl="javascript:bad")]}):
            with self.subTest(data=data), self.assertRaises(SourceError):
                AshbySource(FakeClient(data)).fetch(BOARD)

    def test_ashby_config_is_supported(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "settings.toml"
            path.write_text('[[boards]]\nsource="ashby"\ntoken="fictional"\nemployer="Fictional"')
            self.assertEqual(load_settings(path).boards[0].source, "ashby")
