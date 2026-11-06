"""Is a posting still open? Conservative checks that say CLOSED only on strong evidence.

Adapted from `.github/scripts/check_closed_jobs.py` in negarprh/Canadian-Tech-Internships-2027, used with
the permission of its owner as relayed by this project's user. The decision rules, closed-page phrases and
the per-provider checks (Workday's own job API, Greenhouse, SmartRecruiters and Oracle APIs, Ashby page data,
Phenom page data, ATS redirect-to-search detection) follow that script. The network layer is this project's:
paced, budgeted, certificate-checked, public-IP-pinned requests; robots.txt is read first and a disallowed
address is never fetched; redirects are never followed to another site; every failure is UNKNOWN, not CLOSED.
"""
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
import json
import re
from typing import Literal
from urllib.parse import urlencode, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

from datetime import datetime, timedelta, timezone

from getmeajob.sources import SourceError
from getmeajob.sources.http import BudgetExceeded

Status = Literal["OPEN", "CLOSED", "UNKNOWN"]
FRESH_HOURS = 24
USER_AGENT = "GetMeAJob"


@dataclass(frozen=True)
class Check:
    status: Status
    reason: str


# Specific messages from ATS error/closed pages. Generic words such as "closed" cause false positives.
CLOSED_PHRASES = {
    "generic": (
        "this position has been filled", "position has been filled", "this job posting is no longer active", "this job posting has expired",
        "this posting has closed", "this job is no longer available", "this job is no longer posted", "this job is closed",
        "this position is closed", "this position is no longer available", "this role is no longer available",
        "this vacancy is no longer available", "this opening is no longer available", "no longer accepting applications",
        "no longer accepting candidates", "requisition closed", "job requisition is no longer available", "job posting is no longer available",
        "the job you are looking for is no longer available", "the job you are trying to view is no longer available",
        "the requisition you are looking for is no longer available", "the job posting you are looking for does not exist",
        "this job posting does not exist", "this job no longer exists", "this position no longer exists", "this requisition no longer exists",
        "this requisition does not exist", "this opportunity no longer exists", "this opportunity does not exist",
        "the page you are looking for doesn't exist", "the page you are looking for does not exist", "looks like this job no longer exists",
    ),
    "workday": ("job closed", "job posting has closed", "job application is no longer available", "this job does not exist",
                "the job you are looking for does not exist"),
}
KNOWN_ATS = {"workday", "greenhouse", "lever", "ashby", "eightfold", "icims", "smartrecruiters", "successfactors"}
SEARCH_PATH_PARTS = ("/search", "/jobsearch", "/jobs/search", "/careers/search")
LOCALE_SEGMENT = re.compile(r"^[a-z]{2}-[a-z]{2}$", re.IGNORECASE)


class Unknown(Exception):
    """Raised for anything inconclusive (robots, network, odd responses); the answer is then UNKNOWN."""


class VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(); self.parts: list[str] = []; self._ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "template"}:
            self._ignored += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "template"} and self._ignored:
            self._ignored -= 1

    def handle_data(self, data):
        if not self._ignored:
            self.parts.append(data)


def readable_text(html: str) -> str:
    parser = VisibleText()
    try:
        parser.feed(html); parser.close(); content = " ".join(parser.parts)
    except Exception:
        content = html
    return re.sub(r"\s+", " ", unescape(content)).casefold()


def ats_name(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    for needle, name in (("myworkdayjobs", "workday"), ("workday", "workday"), ("greenhouse", "greenhouse"), ("lever.co", "lever"), ("ashbyhq", "ashby"),
                         ("eightfold", "eightfold"), ("icims.com", "icims"), ("smartrecruiters.com", "smartrecruiters"),
                         ("successfactors", "successfactors"), ("sapfioritalent", "successfactors")):
        if needle in host:
            return name
    return "oracle" if host.endswith(".oraclecloud.com") else "generic"


def closed_phrase(text: str, provider: str) -> str | None:
    for phrase in CLOSED_PHRASES["generic"] + CLOSED_PHRASES.get(provider, ()):
        if phrase in text:
            return phrase
    return None


def workday_api_url(url: str) -> str | None:
    parsed = urlparse(url); host = (parsed.hostname or "").lower(); parts = [p for p in parsed.path.split("/") if p]
    if host.endswith("myworkdaysite.com"):
        if len(parts) < 5 or parts[0].casefold() != "recruiting" or parts[3].casefold() != "job":
            return None
        tenant, site, job_parts = parts[1], parts[2], parts[4:]
    else:
        tenant = host.split(".")[0]
        if parts and LOCALE_SEGMENT.fullmatch(parts[0]):
            parts = parts[1:]
        if len(parts) < 3 or parts[1].casefold() != "job":
            return None
        site, job_parts = parts[0], parts[2:]
    return urlunparse((parsed.scheme, parsed.netloc, f"/wday/cxs/{tenant}/{site}/job/" + "/".join(job_parts), "", "", ""))


def greenhouse_api_url(url: str) -> str | None:
    parsed = urlparse(url); parts = [p for p in parsed.path.split("/") if p]
    if (parsed.hostname or "").lower() not in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
        return None
    if len(parts) != 3 or parts[1].casefold() != "jobs" or not parts[2].isdigit():
        return None
    return f"https://boards-api.greenhouse.io/v1/boards/{parts[0]}/jobs/{parts[2]}"


def smartrecruiters_api_url(url: str) -> str | None:
    parsed = urlparse(url); parts = [p for p in parsed.path.split("/") if p]
    if (parsed.hostname or "").lower() != "jobs.smartrecruiters.com" or len(parts) != 2:
        return None
    return f"https://api.smartrecruiters.com/v1/companies/{parts[0]}/postings/{parts[1]}"


class SmartRecruitersExpiredButton(HTMLParser):
    def __init__(self) -> None:
        super().__init__(); self.in_disabled_button = False; self.parts: list[str] = []; self.expired = False

    def handle_starttag(self, tag, attrs):
        if tag == "button":
            self.in_disabled_button = "disabled" in dict(attrs); self.parts = []

    def handle_data(self, data):
        if self.in_disabled_button:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "button" and self.in_disabled_button:
            label = re.sub(r"\s+", " ", "".join(self.parts)).strip().casefold()
            self.expired |= label in {"sorry, this job has expired", "désolé, ce poste a expiré"}
            self.in_disabled_button = False


def redirect_is_search_or_landing(original: str, final: str, provider: str) -> bool:
    if provider not in KNOWN_ATS or original == final:
        return False
    source, target = urlparse(original), urlparse(final)
    if source.hostname != target.hostname:
        return False
    path = target.path.rstrip("/").casefold()
    return any(part in path for part in SEARCH_PATH_PARTS) or path in {"", "/", "/en-us", "/en-ca", "/fr-ca", "/jobs", "/careers"}


def _embedded_json(html: str, marker: str):
    match = re.search(marker, html)
    if not match:
        return None
    try:
        return json.JSONDecoder().raw_decode(html[match.end():])[0]
    except ValueError:
        return None


def check_ashby_page(url: str, html: str) -> Check:
    parsed = urlparse(url)
    route = re.fullmatch(r"/[^/]+/([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})(?:/application)?/?", parsed.path)
    if parsed.hostname != "jobs.ashbyhq.com" or route is None:
        return Check("UNKNOWN", "Unsupported Ashby job route")
    payload = _embedded_json(html, r"\bwindow\.__appData\s*=\s*")
    if isinstance(payload, dict) and payload.get("maintenanceMode") is False:
        if "posting" in payload and payload["posting"] is None:
            return Check("CLOSED", "Ashby page explicitly returned posting: null")
        posting = payload.get("posting")
        if isinstance(posting, dict) and str(posting.get("id", "")).casefold() == route[1].casefold() and posting.get("title"):
            return Check("OPEN", "Ashby page returned the requested posting")
    return Check("UNKNOWN", "Ashby page has no conclusive posting data")


def check_phenom_page(url: str, html: str) -> Check | None:
    payload = _embedded_json(html, r"\bphApp\.ddo\s*=\s*")
    if payload is None:
        return None
    detail = payload.get("jobDetail") if isinstance(payload, dict) else None
    if isinstance(detail, dict) and detail.get("status") == 200:
        data = detail.get("data"); job = data.get("job") if isinstance(data, dict) else None
        route = re.search(r"/job/([^/]+)(?:/|$)", urlparse(url).path)
        if (isinstance(job, dict) and route and job.get("jobId") == route[1] and job.get("title")
                and str(job.get("postingStatus", "")).casefold() == "open"):
            return Check("OPEN", "Phenom returned the requested job with postingStatus OPEN")
    return Check("UNKNOWN", "Phenom page has no conclusive posting state")


class Checker:
    """Runs the checks through the project's hardened client (`client.probe`)."""

    def __init__(self, client):
        self.client = client
        self.robots: dict[str, RobotFileParser | None] = {}

    def _allowed(self, url: str) -> None:
        host = urlparse(url).hostname
        if host not in self.robots:
            try:
                answer = self.client.probe(f"https://{host}/robots.txt", page_hosts=(host,), max_bytes=100000, accept="text/plain")
            except BudgetExceeded:
                raise
            except SourceError as exc:
                raise Unknown(f"robots.txt could not be read ({exc})") from None
            if answer.status in (404, 410):
                self.robots[host] = None   # no robots file: nothing is disallowed
            elif answer.status == 200:
                parser = RobotFileParser(); parser.parse(answer.body.decode("utf-8", "replace").splitlines()); self.robots[host] = parser
            else:
                raise Unknown(f"robots.txt unavailable (HTTP {answer.status})")
        parser = self.robots[host]
        if parser is not None and not parser.can_fetch(USER_AGENT, url):
            raise Unknown("robots.txt does not allow checking this address")

    def get(self, url: str, accept: str = "application/json,text/html;q=0.8"):
        self._allowed(url)
        try:
            return self.client.probe(url, page_hosts=(urlparse(url).hostname,), accept=accept)
        except BudgetExceeded:
            raise   # a spent request budget is not an answer about the job; callers stop instead of recording UNKNOWN
        except SourceError as exc:
            raise Unknown(str(exc)) from None

    def page(self, url: str):
        """GET an HTML page, following at most three redirects within the same site. Returns (probe, final_url)."""
        current = url
        for _ in range(4):
            answer = self.get(current, "text/html,application/xhtml+xml")
            if answer.status in (301, 302, 303, 307, 308) and answer.location:
                target = urlparse(answer.location if "://" in answer.location else urlunparse(urlparse(current)._replace(path=answer.location, query="")))
                if target.hostname != urlparse(url).hostname or target.scheme != "https":
                    raise Unknown("redirected to a different site (not followed)")
                current = urlunparse(target)
                continue
            return answer, current
        raise Unknown("too many redirects")

    # ---- per-provider API checks ---------------------------------------------------------------------------------
    def workday(self, url: str) -> Check | None:
        endpoint = workday_api_url(url)
        if endpoint is None:
            return None
        answer = self.get(endpoint)
        if answer.status in (404, 410):
            return Check("CLOSED", f"Workday API HTTP {answer.status}")
        try:
            payload = json.loads(answer.body)
        except ValueError:
            payload = None
        if answer.status == 200:
            job = payload.get("jobPostingInfo") if isinstance(payload, dict) else None
            if isinstance(job, dict) and job.get("id") and job.get("title"):
                return Check("OPEN", "Workday API returned jobPostingInfo")
            return Check("UNKNOWN", "Workday API returned 200 without a job posting")
        if answer.status == 403 and isinstance(payload, dict) and str(payload.get("errorCode", "")).casefold() == "s22" \
                and str(payload.get("message", "")).casefold() == "permission denied":
            return Check("CLOSED", "Workday API S22: permission denied")
        return Check("UNKNOWN", f"Workday API HTTP {answer.status} (inconclusive)")

    def public_api(self, url: str, provider: str) -> Check | None:
        endpoint = {"greenhouse": greenhouse_api_url, "smartrecruiters": smartrecruiters_api_url}.get(provider, lambda _: None)(url)
        if endpoint is None:
            return None
        answer = self.get(endpoint)
        if answer.status in (404, 410):
            return Check("CLOSED", f"{provider} API HTTP {answer.status}")
        if answer.status != 200:
            return Check("UNKNOWN", f"{provider} API HTTP {answer.status} (inconclusive)")
        try:
            payload = json.loads(answer.body)
        except ValueError:
            return Check("UNKNOWN", f"{provider} API returned invalid JSON")
        title = payload.get("title") or payload.get("name") if isinstance(payload, dict) else None
        if title and (payload.get("id") or payload.get("uuid")):
            return Check("OPEN", f"{provider} API returned a published job")
        return Check("UNKNOWN", f"{provider} API returned 200 without a job posting")

    def oracle(self, url: str) -> Check:
        parsed = urlparse(url)
        route = re.fullmatch(r"/hcmUI/CandidateExperience/[^/]+/sites/([\w-]+)/job/(\d+)(?:/apply)?/?", parsed.path)
        if route is None:
            return Check("UNKNOWN", "Unsupported Oracle job route")
        site, job_id = route.groups()
        query = urlencode({"onlyData": "true", "finder": f'ById;Id="{job_id}",siteNumber={site}'})
        answer = self.get(urlunparse((parsed.scheme, parsed.netloc, "/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails", "", query, "")))
        if answer.status != 200:
            return Check("UNKNOWN", f"Oracle API HTTP {answer.status} (inconclusive)")
        try:
            payload = json.loads(answer.body)
        except ValueError:
            return Check("UNKNOWN", "Oracle API returned invalid JSON")
        if isinstance(payload, dict):
            items = payload.get("items")
            if items == [] and payload.get("count") == 0 and payload.get("hasMore") is False:
                return Check("CLOSED", "Oracle API returned no posting for this job and site")
            if isinstance(items, list) and any(isinstance(j, dict) and str(j.get("Id")) == job_id and j.get("Title") for j in items):
                return Check("OPEN", "Oracle API returned the requested job")
        return Check("UNKNOWN", "Oracle API returned an unrecognized job response")

    # ---- the decision procedure ----------------------------------------------------------------------------------
    def check(self, url: str) -> Check:
        """CLOSED only with a high-confidence signal; anything else inconclusive is UNKNOWN."""
        provider = ats_name(url)
        try:
            if provider == "oracle":
                return self.oracle(url)
            workday_result = None
            if provider == "workday":
                workday_result = self.workday(url)
                if workday_result is not None and workday_result.status != "UNKNOWN":
                    return workday_result
            api_result = self.public_api(url, provider)
            if api_result is not None and (api_result.status == "CLOSED" or (api_result.status == "OPEN" and provider != "smartrecruiters")):
                return api_result
            try:
                answer, final = self.page(url)
            except Unknown:
                if workday_result is not None:
                    return workday_result
                raise
            if answer.status in (404, 410):
                return Check("CLOSED", f"HTTP {answer.status}")
            if answer.status >= 400:
                return Check("UNKNOWN", f"HTTP {answer.status} (not treated as closed)")
            html = answer.body.decode("utf-8", "replace")
            if provider == "ashby":
                return check_ashby_page(final, html)
            if redirect_is_search_or_landing(url, final, provider):
                return Check("CLOSED", f"Redirected to ATS search/landing page: {final}")
            if provider == "smartrecruiters":
                parser = SmartRecruitersExpiredButton(); parser.feed(html); parser.close()
                if parser.expired:
                    return Check("CLOSED", "SmartRecruiters application button says this job has expired")
                return api_result or Check("UNKNOWN", "SmartRecruiters page has no conclusive posting state")
            phenom = check_phenom_page(url, html)
            if phenom is not None:
                return phenom
            phrase = closed_phrase(readable_text(html), provider)
            if phrase:
                return Check("CLOSED", f"Closed-page message: {phrase!r} ({provider})")
            if workday_result is not None:
                return workday_result   # a Workday 200 HTML page is only its app shell, not proof the requisition exists
            if api_result is not None:
                return api_result
            return Check("OPEN", "No closure signal")
        except Unknown as exc:
            return Check("UNKNOWN", str(exc))


def ensure_check(store, checker: Checker, job_id: int, url: str, hours: float = FRESH_HOURS) -> Check:
    """Return a recent stored answer, or run the check and store it. BudgetExceeded propagates (nothing is stored)."""
    saved = store.open_checks([job_id]).get(job_id)
    if saved and saved["url"] == url and hours > 0:
        try:
            if datetime.now(timezone.utc) - datetime.fromisoformat(saved["checked"]) < timedelta(hours=hours):
                return Check(saved["status"], saved["reason"])
        except ValueError:
            pass
    result = checker.check(url)
    store.save_open_check(job_id, url, result.status, result.reason)
    return result
