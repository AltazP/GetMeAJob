"""Small, strict configuration boundary using only Python's standard library."""

from dataclasses import dataclass
from pathlib import Path
import tomllib


class ConfigurationError(ValueError):
    """The local configuration is invalid or cannot be read."""


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8000

    def __post_init__(self) -> None:
        if self.host != "127.0.0.1":
            raise ConfigurationError("dashboard.host must be 127.0.0.1.")
        if type(self.port) is not int or not 1024 <= self.port <= 65535:
            raise ConfigurationError("dashboard.port must be an integer from 1024 to 65535.")


def load_settings(path: Path | None = None) -> Settings:
    """Use safe defaults unless an explicit TOML configuration is provided."""
    if path is None:
        return Settings()
    try:
        with path.open("rb") as stream:
            document = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        # Avoid printing file content or parser details that could contain secrets.
        raise ConfigurationError("Cannot read configuration; check the file and TOML syntax.") from exc
    if set(document) - {"dashboard"}:
        raise ConfigurationError("Only the dashboard configuration section is supported in Phase 1.")
    dashboard = document.get("dashboard", {})
    if not isinstance(dashboard, dict) or set(dashboard) - {"host", "port"}:
        raise ConfigurationError("dashboard supports only host and port.")
    return Settings(**dashboard)
