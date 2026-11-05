"""Offline city-centre distances; ambiguous place names remain unresolved."""

from dataclasses import dataclass
from functools import lru_cache
import math
import re


def normalized(text: str) -> str:
    return re.sub(r"[^\w]+", " ", text.casefold()).strip()


@dataclass(frozen=True)
class Place:
    name: str
    latitude: float
    longitude: float
    aliases: tuple[str, ...] = ()
    city: str = ""
    province: str = ""  # Full province name; empty for user-configured places.


PROVINCES = {"Alberta": "AB", "British Columbia": "BC", "Manitoba": "MB", "New Brunswick": "NB",
             "Newfoundland and Labrador": "NL", "Nova Scotia": "NS", "Ontario": "ON",
             "Prince Edward Island": "PE", "Quebec": "QC", "Saskatchewan": "SK"}

# Approximate city centres, not street addresses. No network geocoder or user tracking.
_CENTRES = (
    ("Waterloo", "Ontario", 43.4643, -80.5204),
    ("Kitchener", "Ontario", 43.4516, -80.4925),
    ("Cambridge", "Ontario", 43.3616, -80.3144),
    ("Guelph", "Ontario", 43.5448, -80.2482),
    ("Brantford", "Ontario", 43.1394, -80.2644),
    ("Hamilton", "Ontario", 43.2557, -79.8711),
    ("Burlington", "Ontario", 43.3255, -79.7990),
    ("London", "Ontario", 42.9849, -81.2453),
    ("Mississauga", "Ontario", 43.5890, -79.6441),
    ("Toronto", "Ontario", 43.6532, -79.3832),
    ("Ottawa", "Ontario", 45.4215, -75.6972),
    ("Markham", "Ontario", 43.8561, -79.3370),
    ("Brampton", "Ontario", 43.7315, -79.7624),
    ("Oakville", "Ontario", 43.4675, -79.6877),
    ("Kingston", "Ontario", 44.2312, -76.4860),
    ("Windsor", "Ontario", 42.3149, -83.0364),
    ("St. Catharines", "Ontario", 43.1594, -79.2469),
    ("Barrie", "Ontario", 44.3894, -79.6903),
    ("Vancouver", "British Columbia", 49.2827, -123.1207),
    ("Victoria", "British Columbia", 48.4284, -123.3656),
    ("Calgary", "Alberta", 51.0447, -114.0719),
    ("Edmonton", "Alberta", 53.5461, -113.4938),
    ("Saskatoon", "Saskatchewan", 52.1332, -106.6700),
    ("Regina", "Saskatchewan", 50.4452, -104.6189),
    ("Winnipeg", "Manitoba", 49.8951, -97.1384),
    ("Montreal", "Quebec", 45.5017, -73.5673),
    ("Quebec City", "Quebec", 46.8139, -71.2080),
    ("Halifax", "Nova Scotia", 44.6488, -63.5752),
    ("Fredericton", "New Brunswick", 45.9636, -66.6431),
    ("St. John's", "Newfoundland and Labrador", 47.5615, -52.7126),
    ("Charlottetown", "Prince Edward Island", 46.2382, -63.1311),
)
DEFAULT_PLACES = tuple(
    Place(f"{city}, {province}", lat, lon, tuple(
        f"{city}, {suffix}" for suffix in (
            province, PROVINCES[province], f"{province}, Canada", f"{PROVINCES[province]}, Canada", "Canada",
            f"{PROVINCES[province]}, CA"
        )
    ), city, province) for city, province, lat, lon in _CENTRES
)


def find_centre(city: str, province: str = "") -> Place | None:
    """Resolve a typed city (and optional province name or code) against the offline table.

    Returns None when the city is unknown or, without a province, matches more than one place.
    """
    wanted = normalized(city)
    codes = {name: code for name, code in PROVINCES.items()}
    found = [p for p in DEFAULT_PLACES if normalized(p.city) == wanted]
    if province:
        key = normalized(province)
        found = [p for p in found if key in {normalized(p.province), normalized(codes[p.province])}]
    return found[0] if len(found) == 1 else None


@lru_cache(maxsize=8)
def _alias_index(places: tuple[Place, ...]) -> dict[str, tuple[Place, ...]]:
    index: dict[str, list[Place]] = {}
    for place in places:
        for key in {normalized(alias) for alias in (place.name, *place.aliases)}:
            index.setdefault(key, []).append(place)
    return {key: tuple(found) for key, found in index.items()}


def resolve(location: str, places: tuple[Place, ...] = DEFAULT_PLACES) -> Place | None:
    # Strip explicit workplace labels, but never use fuzzy substrings (London != London, UK).
    cleaned = re.sub(r"\b(remote|hybrid|on[ -]site)\b", "", location, flags=re.I)
    matches = _alias_index(places).get(normalized(cleaned), ())
    return matches[0] if len(matches) == 1 else None


def distance_km(latitude: float, longitude: float, place: Place) -> float:
    lat1, lat2 = math.radians(latitude), math.radians(place.latitude)
    dlat = lat2 - lat1
    dlon = math.radians(place.longitude - longitude)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0088 * 2 * math.asin(math.sqrt(min(1, max(0, a))))
