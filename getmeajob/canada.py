"""Deterministic "is this location in Canada?" test for free-text job locations.

Evidence only: the word Canada, a Canadian province name, a province code after a comma
(Toronto, ON), or a city name that is not shared with a well-known place elsewhere.
Shared names (London, Victoria, Kingston, Windsor, Cambridge, Hamilton, Waterloo...)
need Canada or a province next to them. Anything else is not counted as Canadian.
"""
import re
import unicodedata

PROVINCES = ("Alberta", "British Columbia", "Manitoba", "New Brunswick", "Newfoundland and Labrador",
             "Nova Scotia", "Ontario", "Prince Edward Island", "Quebec", "Saskatchewan",
             "Northwest Territories", "Nunavut", "Yukon")
CODES = ("AB", "BC", "MB", "NB", "NL", "NS", "ON", "PE", "QC", "SK", "NT", "NU", "YT")
UNAMBIGUOUS_CITIES = ("Toronto", "Vancouver", "Montreal", "Calgary", "Edmonton", "Ottawa", "Mississauga", "Kitchener",
                      "Winnipeg", "Saskatoon", "Regina", "Markham", "Brampton", "Oakville", "Guelph", "Quebec City",
                      "Fredericton", "Charlottetown", "Burnaby", "Laval", "Gatineau", "Richmond Hill", "Vaughan",
                      "Kelowna", "Moncton", "Waterloo, ON", "Kitchener-Waterloo")

_NAMES = re.compile(r"(?<!\w)(?:canada|canadian|" + "|".join(re.escape(p.casefold()) for p in PROVINCES)
                    + "|" + "|".join(re.escape(c.casefold()) for c in UNAMBIGUOUS_CITIES) + r")(?!\w)")
_CODE = re.compile(r"[,\-(/]\s*(?:" + "|".join(CODES) + r")(?![A-Za-z])")


def _plain(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def location_in_canada(location: str) -> bool:
    text = _plain(location)
    return bool(_NAMES.search(text.casefold()) or _CODE.search(text))


def in_canada(locations) -> bool | None:
    """True/False for a job with locations; None when the source gave no location (unknown)."""
    if not locations:
        return None
    return any(location_in_canada(loc) for loc in locations)
