"""Community internship lists published on GitHub, read as discovery leads.

Three public files are fetched (one request each, fixed https://raw.githubusercontent.com addresses, through the same
size-capped, paced, certificate-checked client as the job-board APIs):

- SimplifyJobs/Summer2027-Internships  .github/scripts/listings.json   (structured JSON)
- negarprh/Canadian-Tech-Internships-2027  README.md                    (Markdown table, Canada only)
- speedyapply/2027-SWE-College-Jobs  README.md                          (HTML/Markdown table, USA internships)

None of these repositories carries a licence, so none of their code is used: these parsers are original and read
only the published list data (company, title, location, link, date). Each posting links to the employer's own
application page; its provenance is kept in `source`. Closed or inactive rows are skipped. A listed job is a lead,
not proof that the vacancy is open.
"""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from html import unescape

from getmeajob.models import Job, Salary, timestamp
from getmeajob.sources import SourceError
from getmeajob.sources.normalize import application_url

HOST = "raw.githubusercontent.com"
LISTS = {
    "simplify": "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json",
    "negarprh": "https://raw.githubusercontent.com/negarprh/Canadian-Tech-Internships-2027/main/README.md",
    "speedyapply": "https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/README.md",
}
LABELS = {"simplify": "Simplify list", "negarprh": "Canadian Tech Internships list", "speedyapply": "SpeedyApply list"}
MAX_BYTES = 20 * 1024 * 1024
MAX_ROWS = 20000


def _digest(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:20]


def _employment(title: str) -> str:
    return "co-op" if re.search(r"\bco[- ]?op\b", title, re.I) else "internship"


def _clean(text: object, limit: int = 300) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", str(text or "")))).strip()[:limit]


def _link(url: str | None) -> str | None:
    try:
        return application_url(url.strip()) if url and url.strip().startswith("https://") else None
    except SourceError:
        return None


def parse_simplify(raw: bytes) -> list[Job]:
    try:
        rows = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise SourceError("The Simplify list is not valid JSON; nothing was imported.") from exc
    if not isinstance(rows, list):
        raise SourceError("The Simplify list has an unexpected shape; nothing was imported.")
    jobs = []
    for row in rows[:MAX_ROWS]:
        if not isinstance(row, dict) or not row.get("active") or row.get("is_visible") is False:
            continue
        url, title, company = _link(row.get("url")), _clean(row.get("title")), _clean(row.get("company_name"), 120)
        if not (url and title and company and row.get("id")):
            continue
        locations = tuple(_clean(x, 120) for x in row.get("locations") or [] if _clean(x, 120)) if isinstance(row.get("locations"), list) else ()
        posted = None
        if isinstance(row.get("date_posted"), (int, float)):
            posted = datetime.fromtimestamp(row["date_posted"], timezone.utc).isoformat()
        notes = [f"Community list category: {_clean(row.get('category'), 80)}." if row.get("category") else "",
                 f"Terms: {', '.join(_clean(t, 30) for t in row['terms'])}." if isinstance(row.get("terms"), list) and row["terms"] else "",
                 f"Visa sponsorship noted by the list: {_clean(row.get('sponsorship'), 40)}." if row.get("sponsorship") else ""]
        jobs.append(Job(source="simplify", board="simplifyjobs", source_id=_clean(row["id"], 80), employer=company, role=title,
                        application_url=url, locations=locations, description=" ".join(n for n in notes if n),
                        employment_type=_employment(title), posted_at=posted, department=_clean(row.get("category"), 80) or None))
    return jobs


def _cells(line: str) -> list[str]:
    return [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def _table_rows(text: str, first_columns: tuple[str, ...]):
    """Yield {column name: cell} for every row of every Markdown table whose header starts with `first_columns`."""
    header = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            if line and not line.startswith("<!--"):
                header = None   # table ended; a later section may start another one
            continue
        cells = _cells(line)
        names = tuple(_clean(c, 40).casefold() for c in cells)
        if names[:len(first_columns)] == first_columns:
            header = names; continue
        if header is None or re.fullmatch(r"\|[\s:|-]+\|?", line):
            continue
        if len(cells) >= len(header):
            yield dict(zip(header, cells))


def _last_link(cell: str) -> str | None:
    found = re.findall(r"\]\((https://[^)\s]+)\)|href=\"(https://[^\"]+)\"", cell)
    return next((a or b for a, b in reversed(found)), None)


def _split_locations(text: str) -> tuple[str, ...]:
    parts = [p.strip() for p in re.split(r"\s*(?:;|<br\s*/?>|\|)\s*", _clean(text, 400)) if p.strip()]
    return tuple(dict.fromkeys(re.sub(r"\s*\+\d+$", "", p) for p in parts))


def parse_negarprh(text: str) -> list[Job]:
    jobs, company = [], ""
    for row in _table_rows(text, ("company", "role")):
        name = row["company"]
        if name and name != "↳":
            company = _clean(name, 120)
        apply = row.get("apply", "")
        if "closed" in apply.casefold():
            continue
        url, title = _link(_last_link(apply)), _clean(row["role"])
        if not (url and title and company):
            continue
        posted = None
        try:
            posted = datetime.strptime(_clean(row.get("date posted"), 20), "%b %d, %Y").replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            pass
        jobs.append(Job(source="negarprh", board="canadian-tech-internships", source_id=_digest(url), employer=company, role=title,
                        application_url=url, locations=_split_locations(row["location"]),
                        description="Community list: verified Canada-based or Canada-eligible internship.",
                        employment_type=_employment(title), posted_at=posted))
    return jobs


def parse_speedyapply(text: str, now: datetime | None = None) -> list[Job]:
    now = now or datetime.now(timezone.utc)
    jobs = []
    for row in _table_rows(text, ("company", "position")):
        url, company, title = _link(_last_link(row.get("posting", ""))), _clean(row["company"], 120), _clean(row["position"])
        if not (url and title and company):
            continue
        salary = Salary()
        money = re.fullmatch(r"\$(\d+(?:\.\d+)?)/hr", _clean(row.get("salary"), 40))
        if money:   # this list is for US positions and quotes US dollars
            salary = Salary(float(money[1]), None, "USD", "hour")
        days = re.fullmatch(r"(\d{1,3})d", _clean(row.get("age"), 10))
        posted = timestamp((now - timedelta(days=int(days[1]))).isoformat()) if days else None
        jobs.append(Job(source="speedyapply", board="swe-college-jobs-usa", source_id=_digest(url), employer=company, role=title,
                        application_url=url, locations=_split_locations(row["location"]),
                        description="Community list: software engineering internship in the USA.",
                        salary=salary, employment_type=_employment(title), posted_at=posted))
    return jobs


PARSERS = {"simplify": lambda raw: parse_simplify(raw), "negarprh": lambda raw: parse_negarprh(raw.decode("utf-8", "replace")),
           "speedyapply": lambda raw: parse_speedyapply(raw.decode("utf-8", "replace"))}


def fetch_leads(client, names=("simplify", "negarprh", "speedyapply")) -> tuple[dict[str, list[Job]], dict[str, str]]:
    """Download and parse the chosen lists. Returns ({name: jobs}, {name: error}); one failure never blocks the others."""
    found, errors = {}, {}
    for name in names:
        client.source = f"leads:{name}"
        try:
            found[name] = PARSERS[name](client.read(LISTS[name], page_hosts=(HOST,), max_bytes=MAX_BYTES))
        except SourceError as exc:
            errors[name] = str(exc)
    return found, errors
