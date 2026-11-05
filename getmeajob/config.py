"""Strict local TOML configuration; examples contain no credentials."""

from dataclasses import dataclass, field, fields
import math
from pathlib import Path
import re
import tomllib

from getmeajob.filters import SearchFilters
from getmeajob.locations import DEFAULT_PLACES, Place, resolve
from getmeajob.sources import Board, SourceError
from getmeajob.urls import public_url, PROVIDERS


class ConfigurationError(ValueError):
    """The local configuration is invalid or cannot be read."""


@dataclass(frozen=True)
class DiscoverySettings:
    hn_enabled: bool = True
    hn_story_id: int | None = None
    max_items: int = 20
    page_hosts: tuple[str, ...] = ()
    refresh_minutes: float = 60
    verification_hours: float = 24
    max_requests: int | None = 100
    per_host_requests: int = 50
    per_source_requests: int = 20
    timeout_seconds: float = 20
    include_compensation: bool = True
    disabled_providers: tuple[str, ...] = ()


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8000
    search: SearchFilters = field(default_factory=SearchFilters)
    discovery: DiscoverySettings = field(default_factory=DiscoverySettings)
    boards: tuple[Board, ...] = ()
    places: tuple[Place, ...] = DEFAULT_PLACES
    database: Path = Path("data/jobs.sqlite3")
    request_interval_seconds: float = 1.0

    def __post_init__(self) -> None:
        if self.host != "127.0.0.1":
            raise ConfigurationError("dashboard.host must be 127.0.0.1.")
        if type(self.port) is not int or not 1024 <= self.port <= 65535:
            raise ConfigurationError("dashboard.port must be an integer from 1024 to 65535.")


def _table(value, name: str, allowed: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) - allowed:
        raise ConfigurationError(f"{name} contains unsupported settings or is not a table.")
    return value


def _string(value, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{name} must be nonempty text.")
    return value.strip()


def _number(value, name: str, minimum: float, maximum: float = float("inf")) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be a finite number between {minimum} and {maximum}.")
    return float(value)


def _coordinates(table: dict, name: str) -> tuple[float, float]:
    if "latitude" not in table or "longitude" not in table:
        raise ConfigurationError(f"{name} requires both latitude and longitude.")
    return (_number(table["latitude"], f"{name}.latitude", -90, 90),
            _number(table["longitude"], f"{name}.longitude", -180, 180))


def load_settings(path: Path | None = None) -> Settings:
    if path is None:
        return Settings()
    try:
        with path.open("rb") as stream:
            document = tomllib.load(stream)
    except (OSError, ValueError) as exc:
        raise ConfigurationError("Cannot read configuration; check the file and TOML syntax.") from exc
    _table(document, "configuration", {"dashboard", "search", "boards", "locations", "storage", "network", "discovery"})
    dashboard = _table(document.get("dashboard", {}), "dashboard", {"host", "port"})
    storage = _table(document.get("storage", {}), "storage", {"database"})
    network = _table(document.get("network", {}), "network", {"request_interval_seconds"})
    interval = _number(network.get("request_interval_seconds", 1), "network.request_interval_seconds", 1, 60)
    database = Path(_string(storage.get("database", "data/jobs.sqlite3"), "storage.database")).expanduser()
    places = list(DEFAULT_PLACES)
    location_rows = document.get("locations", [])
    if not isinstance(location_rows, list):
        raise ConfigurationError("locations must be an array of tables.")
    for row in location_rows:
        row = _table(row, "locations", {"name", "latitude", "longitude", "aliases"})
        name = _string(row.get("name"), "locations.name")
        latitude, longitude = _coordinates(row, "locations")
        aliases = row.get("aliases", [])
        if not isinstance(aliases, list) or any(not isinstance(item, str) or not item.strip() for item in aliases):
            raise ConfigurationError("locations.aliases must be an array of nonempty strings.")
        places.append(Place(name, latitude, longitude, tuple(aliases)))
    data = dict(_table(document.get("search", {}), "search", {item.name for item in fields(SearchFilters)} | {"use_radius"}))
    use_radius = data.pop("use_radius", True)
    if type(use_radius) is not bool:
        raise ConfigurationError("search.use_radius must be true or false.")
    tuple_fields = {item.name for item in fields(SearchFilters) if isinstance(item.default, tuple)}
    for key in tuple_fields & data.keys():
        value = data[key]
        if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
            raise ConfigurationError(f"search.{key} must be an array of nonempty strings.")
        data[key] = tuple(item.strip() for item in value)
    for key in ("include_remote_outside_radius", "include_unknown"):
        if key in data and type(data[key]) is not bool:
            raise ConfigurationError(f"search.{key} must be true or false.")
    for key in ("radius_km", "minimum_salary", "max_experience_years"):
        if key in data:
            data[key] = _number(data[key], f"search.{key}", 0)
    for key in ("city", "salary_currency", "salary_interval"):
        if key in data:
            data[key] = _string(data[key], f"search.{key}")
    if "latitude" in data or "longitude" in data:
        data["latitude"], data["longitude"] = _coordinates(data, "search")
    elif "city" in data:
        place = resolve(data["city"], tuple(places))
        if place is None:
            raise ConfigurationError("Unknown search city: provide explicit latitude and longitude.")
        data["latitude"], data["longitude"] = place.latitude, place.longitude
    if "posted_within_days" in data and (type(data["posted_within_days"]) is not int or data["posted_within_days"] < 0):
        raise ConfigurationError("search.posted_within_days must be a nonnegative integer.")
    for key, allowed in {
        "employment_types": {"internship", "co-op", "full-time", "part-time", "contract"},
        "work_arrangements": {"remote", "hybrid", "on-site"},
        "experience_levels": {"entry-level", "mid-level", "senior", "executive"},
    }.items():
        if key in data and set(data[key]) - allowed:
            raise ConfigurationError(f"search.{key} contains an unsupported value.")
    if not use_radius:
        data["radius_km"] = None
    search = SearchFilters(**data)
    if not re.fullmatch(r"[A-Za-z]{3}", search.salary_currency) or search.salary_interval not in {"year", "month", "week", "hour"}:
        raise ConfigurationError("Use a three-letter salary_currency and year/month/week/hour salary_interval.")
    board_rows = document.get("boards", [])
    if not isinstance(board_rows, list):
        raise ConfigurationError("boards must be an array of tables.")
    boards = []
    for row in board_rows:
        row = _table(row, "boards", {"source", "token", "employer", "industry", "region"})
        source = _string(row.get("source"), "boards.source")
        token = _string(row.get("token"), "boards.token")
        employer = _string(row.get("employer"), "boards.employer")
        region = row.get("region", "global")
        if source not in {"greenhouse", "lever", "ashby"} or not re.fullmatch(r"[A-Za-z0-9_-]{1,150}", token):
            raise ConfigurationError("Use greenhouse, lever or ashby and a board token, not a URL.")
        if region not in ("global", "eu") or (source != "lever" and region != "global"):
            raise ConfigurationError("Only Lever supports region = eu.")
        industry = _string(row["industry"], "boards.industry") if "industry" in row else None
        boards.append(Board(source, token, employer, industry, region))
    if len({(board.source, board.region, board.token) for board in boards}) != len(boards):
        raise ConfigurationError("Duplicate employer boards in configuration.")
    options = dict(_table(document.get("discovery", {}), "discovery", {item.name for item in fields(DiscoverySettings)}))
    for key in ("hn_enabled", "include_compensation"):
        if key in options and type(options[key]) is not bool:
            raise ConfigurationError(f"discovery.{key} must be true or false.")
    for key in ("hn_story_id", "max_items", "max_requests", "per_host_requests", "per_source_requests"):
        if key in options and (type(options[key]) is not int or not (0 if key == "max_requests" else 1) <= options[key] <= 100000):
            raise ConfigurationError(f"discovery.{key} must be a bounded positive integer (max_requests=0 disables the global cap).")
    if options.get("max_requests") == 0:
        options["max_requests"] = None
    for key, maximum in (("refresh_minutes", 10080), ("verification_hours", 720), ("timeout_seconds", 60)):
        if key in options:
            options[key] = _number(options[key], f"discovery.{key}", 1, maximum)
    for key in ("page_hosts", "disabled_providers"):
        if key in options:
            values = options[key]
            if not isinstance(values, list) or any(not isinstance(item, str) or not item.strip() for item in values):
                raise ConfigurationError(f"discovery.{key} must be an array of nonempty strings.")
            options[key] = tuple(values)
    if set(options.get("disabled_providers", ())) - PROVIDERS:
        raise ConfigurationError("discovery.disabled_providers must contain supported provider names.")
    try:
        for host in options.get("page_hosts", ()):
            if public_url("https://" + host) != "https://" + host + "/":
                raise SourceError("Use exact lowercase hostnames only.")
    except SourceError as exc:
        raise ConfigurationError("discovery.page_hosts must contain public HTTPS hostnames without paths or ports.") from exc
    return Settings(**dashboard, search=search, discovery=DiscoverySettings(**options), boards=tuple(boards), places=tuple(places),
                    database=database, request_interval_seconds=interval)
