"""Country tests for free-text job locations: Canada and the United States.

Evidence only (explicit country words, state/province codes after a comma, or well-known
cities that are not shared with places elsewhere). Anything else is not counted, so a
bare "London" or "Dublin" matches neither region. A location can match both ("US / Canada").
"""
import re
import unicodedata

from getmeajob.canada import location_in_canada

STATE_CODES = ("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC").split()
STATES = ("Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware", "Florida", "Hawaii", "Idaho", "Illinois",
          "Iowa", "Kansas", "Kentucky", "Louisiana", "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi", "Missouri", "Montana",
          "Nebraska", "Nevada", "New Hampshire", "New Jersey", "New Mexico", "New York", "North Carolina", "North Dakota", "Ohio", "Oklahoma",
          "Oregon", "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah", "Vermont", "Virginia",
          "West Virginia", "Wisconsin", "Wyoming", "District of Columbia")
CITIES = ("San Francisco", "New York", "New York City", "NYC", "Seattle", "Austin", "Boston", "Chicago", "Los Angeles", "Denver", "Atlanta",
          "Washington, D.C.", "Washington DC", "Palo Alto", "Mountain View", "San Jose", "San Mateo", "Foster City", "Menlo Park", "Bay Area",
          "Sunnyvale", "Redwood City", "Santa Clara", "Brooklyn", "Dallas", "Houston", "Miami", "Philadelphia", "Pittsburgh", "San Diego",
          "Salt Lake City", "Raleigh", "Boulder", "Phoenix", "Minneapolis", "Detroit", "Nashville", "Charlotte", "Las Vegas", "Santa Monica",
          "South San Francisco", "Cupertino", "Oakland", "Irvine", "Scottsdale", "Tampa", "Orlando", "Baltimore", "Honolulu")

_WORDS = re.compile(r"(?<!\w)(?:united states(?: of america)?|usa|u\.s\.a?\.?|"
                    + "|".join(re.escape(x.casefold()) for x in (*STATES, *CITIES)) + r")(?!\w)")
_CODE = re.compile(r"[,\-(/|]\s*(?:" + "|".join(STATE_CODES) + r")(?![A-Za-z])")
_US = re.compile(r"(?<![A-Za-z])US(?![A-Za-z])|\bUS-[A-Z]{2}\b")   # capitals only: "US", "US-CA-Menlo Park"; never the word "us"


def _plain(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def _part_in_us(text: str) -> bool:
    explicit = re.search(r"(?i:united states|usa)|(?<![A-Za-z])US(?![A-Za-z])", text)
    if location_in_canada(text) and not explicit:
        return False   # "Toronto, ON, CA" is Canada, not California
    return bool(_WORDS.search(text.casefold()) or _CODE.search(text) or _US.search(text))


def location_in_us(location: str) -> bool:
    # Multi-city strings ("San Francisco, CA | Toronto, ON") are judged one city at a time.
    return any(_part_in_us(part) for part in re.split(r"[|;]", _plain(location)))


MATCHERS = {"canada": location_in_canada, "us": location_in_us}


def in_regions(locations, regions) -> bool | None:
    """True/False for a job with locations; None when the source gave no location (unknown)."""
    if not locations:
        return None
    return any(MATCHERS[r](loc) for loc in locations for r in regions)
