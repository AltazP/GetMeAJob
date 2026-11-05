"""Offline city-centre distances; ambiguous place names remain unresolved."""

from dataclasses import dataclass
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


# Approximate city centres, not street addresses. No network geocoder or user tracking.
_CENTRES = (
    ("Waterloo", 43.4643, -80.5204),
    ("Kitchener", 43.4516, -80.4925),
    ("Cambridge", 43.3616, -80.3144),
    ("Guelph", 43.5448, -80.2482),
    ("Brantford", 43.1394, -80.2644),
    ("Hamilton", 43.2557, -79.8711),
    ("Burlington", 43.3255, -79.7990),
    ("London", 42.9849, -81.2453),
    ("Mississauga", 43.5890, -79.6441),
    ("Toronto", 43.6532, -79.3832),
    ("Ottawa", 45.4215, -75.6972),
)
DEFAULT_PLACES = tuple(
    Place(f"{city}, Ontario", lat, lon, tuple(
        f"{city}, {suffix}" for suffix in (
            "Ontario", "ON", "Ontario, Canada", "ON, Canada", "Canada", "ON, CA"
        )
    )) for city, lat, lon in _CENTRES
)


def resolve(location: str, places: tuple[Place, ...] = DEFAULT_PLACES) -> Place | None:
    # Strip explicit workplace labels, but never use fuzzy substrings (London != London, UK).
    cleaned = re.sub(r"\b(remote|hybrid|on[ -]site)\b", "", location, flags=re.I)
    key = normalized(cleaned)
    matches = [place for place in places
               if key in {normalized(alias) for alias in (place.name, *place.aliases)}]
    return matches[0] if len(matches) == 1 else None


def distance_km(latitude: float, longitude: float, place: Place) -> float:
    lat1, lat2 = math.radians(latitude), math.radians(place.latitude)
    dlat = lat2 - lat1
    dlon = math.radians(place.longitude - longitude)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0088 * 2 * math.asin(math.sqrt(min(1, max(0, a))))
