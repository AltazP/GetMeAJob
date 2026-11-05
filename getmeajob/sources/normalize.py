"""Conservative normalization of explicit source fields and labelled text."""

from html import unescape
from html.parser import HTMLParser
import re
from urllib.parse import urlsplit

from getmeajob.models import number
from getmeajob.sources import SourceError


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def plain(value: object) -> str:
    if not isinstance(value, str):
        return ""
    parser = TextParser()
    # Greenhouse descriptions can contain double-encoded HTML.
    parser.feed(unescape(unescape(value)))
    return " ".join(" ".join(parser.parts).split())


def required(row: dict, key: str) -> str:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value).strip():
        raise SourceError(f"Posting missing required field: {key}.")
    return str(value).strip()


def application_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError
        _ = parsed.port
    except ValueError as exc:
        raise SourceError("Posting has an invalid application URL.") from exc
    return value


EMPLOYMENT = {
    "full-time": "full-time", "full time": "full-time", "fulltime": "full-time",
    "part-time": "part-time", "part time": "part-time", "parttime": "part-time",
    "intern": "internship", "internship": "internship",
    "co-op": "co-op", "coop": "co-op", "co op": "co-op",
    "contract": "contract", "contractor": "contract",
}
WORK = {"remote": "remote", "hybrid": "hybrid", "on-site": "on-site", "onsite": "on-site", "on site": "on-site"}
LEVEL = {"entry-level": "entry-level", "entry level": "entry-level", "mid-level": "mid-level",
         "senior": "senior", "executive": "executive"}
INTERVAL = {"year": "year", "yearly": "year", "annual": "year", "annually": "year",
            "per year": "year", "hour": "hour", "hourly": "hour", "per hour": "hour",
            "month": "month", "monthly": "month", "week": "week", "weekly": "week"}


def mapped(value: object, mapping: dict[str, str]) -> str | None:
    return mapping.get(value.strip().casefold()) if isinstance(value, str) else None


def employment(value: object, title: str) -> str | None:
    if value:  # Unrecognized structured values stay unknown rather than overriding them.
        return mapped(value, EMPLOYMENT)
    patterns = {"internship": r"\b(?:internship|intern)\b", "co-op": r"\bco[- ]?op\b",
                "full-time": r"\bfull[- ]time\b", "part-time": r"\bpart[- ]time\b",
                "contract": r"\bcontract(?:or)?\b"}
    matches = [kind for kind, pattern in patterns.items() if re.search(pattern, title, re.I)]
    return matches[0] if len(matches) == 1 else None


def arrangement(value: object, locations: tuple[str, ...]) -> str | None:
    if value:
        return mapped(value, WORK)
    found = {kind for kind, pattern in {
        "remote": r"\bremote\b", "hybrid": r"\bhybrid\b", "on-site": r"\bon[- ]?site\b"
    }.items() if any(re.search(pattern, location, re.I) for location in locations)}
    return next(iter(found)) if len(found) == 1 else None


def experience(title: str, description: str, level: object = None, years: object = None):
    normalized_level = mapped(level, LEVEL)
    if normalized_level is None and re.search(r"\bentry[- ]level\b", title, re.I):
        normalized_level = "entry-level"
    minimum = number(years)
    evidence = "Explicit source metadata: minimum years experience" if minimum is not None else None
    if minimum is None:
        pattern = r"\b(?:at least|minimum(?: of)?)\s+(\d+(?:\.\d+)?)\s*\+?\s+years?(?:\s+of)?\s+(?:[\w-]+\s+){0,5}experience\b"
        matches = []
        for clause in re.split(r"[;\n]|\.(?:\s|$)", description):
            if re.search(r"\b(?:preferred|bonus|nice to have|ideally|desired|desirable|not required|or)\b", clause, re.I):
                continue
            matches.extend(re.finditer(pattern, clause, re.I))
        if matches:
            strongest = max(matches, key=lambda match: float(match[1]))
            minimum, evidence = float(strongest[1]), strongest[0]
        elif re.search(r"\bno (?:prior )?experience (?:is )?required\b", description, re.I):
            minimum, evidence = 0.0, "No experience required (explicit source text)"
    return normalized_level, minimum, evidence
