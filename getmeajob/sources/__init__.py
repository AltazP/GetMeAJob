"""Public employer-board adapters. No application submission endpoints are used."""

from dataclasses import dataclass
from typing import Protocol

from getmeajob.models import Job


@dataclass(frozen=True)
class Board:
    source: str
    token: str
    employer: str
    industry: str | None = None  # Explicit user-supplied classification, never inferred.
    region: str = "global"


class SourceError(Exception):
    """A board could not be fetched safely or completely."""


class JobSource(Protocol):
    def fetch(self, board: Board) -> list[Job]: ...
