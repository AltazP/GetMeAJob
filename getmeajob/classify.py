"""Explainable job-field and experience-level classification from stored metadata only.

Evidence order: explicit board industry, then source department, then job title keywords.
Nothing is guessed: a job with no supporting evidence is classified as "Other".
"""
import re

from getmeajob.models import Job

FIELDS = ("Finance", "Accounting", "Technology", "Data Analytics", "Consulting",
          "Marketing", "HR", "Operations", "Engineering", "Other")
# Used only to break exact ties between equally specific keywords.
PRIORITY = ("Accounting", "Finance", "Data Analytics", "Marketing", "HR", "Consulting",
            "Technology", "Engineering", "Operations")

KEYWORDS = {
    "Accounting": ("accountant", "accounting", "accounts payable", "accounts receivable", "bookkeeper", "bookkeeping",
                   "auditor", "audit", "payroll", "tax", "controller", "general ledger", "cpa"),
    "Finance": ("finance", "financial", "fp&a", "investment", "investor", "treasury", "banking", "banker", "trader",
                "trading", "portfolio", "credit", "risk analyst", "equity", "wealth", "underwriter", "actuary", "fund"),
    "Data Analytics": ("data analyst", "data analytics", "analytics", "business intelligence", "bi analyst", "data scientist",
                       "data science", "data engineer", "data engineering", "statistician", "insights", "reporting analyst",
                       "quantitative analyst", "analytics engineer"),
    "Technology": ("software", "developer", "devops", "site reliability", "sre", "frontend", "front-end", "backend",
                   "back-end", "full stack", "full-stack", "cloud", "platform engineer", "machine learning", "ml engineer",
                   "security engineer", "cybersecurity", "it support", "systems administrator", "mobile engineer",
                   "qa engineer", "infrastructure", "programmer", "web developer", "technical support", "solutions engineer", "ai engineer"),
    "Consulting": ("consultant", "consulting", "advisory", "strategy", "business analyst", "management consultant", "solutions architect"),
    "Marketing": ("marketing", "brand", "seo", "content", "social media", "communications", "growth", "campaign",
                  "demand generation", "product marketing", "public relations", "copywriter", "community manager", "sales", "account executive"),
    "HR": ("human resources", "recruiter", "recruiting", "talent", "people operations", "people partner", "hr ", "compensation and benefits", "learning and development"),
    "Operations": ("operations", "supply chain", "logistics", "procurement", "coordinator", "administrator", "customer success",
                   "customer support", "project manager", "program manager", "business operations", "office manager", "facilities", "support specialist"),
    "Engineering": ("engineer", "mechanical", "electrical", "civil", "hardware", "manufacturing", "firmware", "robotics", "aerospace", "chemical"),
}
# Source departments/industries that name a field directly.
EXPLICIT = {
    "Finance": ("finance", "financial services", "fintech", "banking", "investment"),
    "Accounting": ("accounting", "audit", "tax"),
    "Technology": ("technology", "software", "information technology", "it", "saas", "cloud"),
    "Data Analytics": ("data", "analytics", "data science"),
    "Consulting": ("consulting", "professional services", "advisory"),
    "Marketing": ("marketing", "sales", "brand", "growth", "communications"),
    "HR": ("people", "human resources", "hr", "talent", "recruiting"),
    "Operations": ("operations", "supply chain", "logistics", "customer success", "customer support", "support", "legal", "administration"),
    "Engineering": ("engineering", "research and development", "r&d", "manufacturing", "hardware"),
}


_COMPILED = {}


def _compiled(table):
    key = id(table)
    if key not in _COMPILED:
        _COMPILED[key] = [(field, word.strip(), re.compile(r"(?<![a-z0-9])" + re.escape(word.strip()) + r"(?![a-z0-9])"))
                          for field, words in table.items() for word in words]
    return _COMPILED[key]


def _hits(text, table):
    """Return {field: longest matching keyword}. Longer keywords are more specific."""
    found = {}
    lowered = f" {re.sub(r'[^a-z0-9&+/ -]+', ' ', text.casefold())} "
    for field, word, pattern in _compiled(table):
        if len(word) > len(found.get(field, "")) and pattern.search(lowered):
            found[field] = word
    return found


def _choose(found):
    best = max(len(word) for word in found.values())
    tied = [field for field in PRIORITY if field in found and len(found[field]) == best]
    return tied[0]


def classify_field(job: Job) -> tuple[str, str]:
    """Return (field, human-readable reason)."""
    title = _hits(job.role, KEYWORDS)
    for label, value in (("industry", job.industry), ("department", job.department)):
        if not value:
            continue
        found = _hits(value, EXPLICIT)
        if not found:
            continue
        field = _choose(found)
        # Engineering is a very common department name at software employers: let a clearly
        # software/technology job title refine it, and say so.
        if field == "Engineering" and "Technology" in title and len(title["Technology"]) >= 6:
            return "Technology", f"Source {label} '{value}' is Engineering; title keyword '{title['Technology']}' indicates software/technology."
        return field, f"Source {label} '{value}' names {field}."
    if title:
        field = _choose(title)
        others = sorted(f for f in title if f != field)
        note = f" Also matched {', '.join(others)}; kept the most specific keyword." if others else ""
        return field, f"Title keyword '{title[field]}' suggests {field}.{note}"
    return "Other", "No industry, department or title keyword supported a specific field."


LEVELS = ("Internship", "Entry-level", "Junior", "Mid-level", "Senior")
_TITLE_LEVELS = (
    ("internship", r"\b(?:intern|internship|co-?op|student)\b"),
    ("entry-level", r"\b(?:entry[- ]level|new grad(?:uate)?|graduate)\b"),
    ("junior", r"\b(?:junior|jr\.?)\b"),
    ("senior", r"\b(?:senior|sr\.?|principal|director|head of|vp)\b"),
)


def effective_level(job: Job) -> tuple[str | None, str]:
    """Explicit source level first, then an explicit title word. Never guessed from salary or years."""
    if job.experience_level:
        return job.experience_level, "Source metadata"
    if job.employment_type in ("internship", "co-op"):
        return "internship", f"Employment type is {job.employment_type}"
    for level, pattern in _TITLE_LEVELS:
        if re.search(pattern, job.role, re.I):
            return level, "Word in job title"
    return None, "Unknown"
