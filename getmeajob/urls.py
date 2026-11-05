"""Strict URL syntax and exact ATS patterns; detection never grants verification."""

from dataclasses import dataclass
import ipaddress
import re
from urllib.parse import parse_qs, unquote, urlsplit, urlunsplit

from getmeajob.sources import SourceError

PROVIDERS = {"greenhouse", "lever", "ashby"}
TOKEN = re.compile(r"[A-Za-z0-9_-]{1,150}\Z")


def public_url(url: str) -> str:
    if not isinstance(url, str) or any(ord(c) < 33 for c in url) or "\\" in url:
        raise SourceError("Unsafe URL syntax.")
    try:
        p = urlsplit(url)
        host = (p.hostname or "").encode("idna").decode("ascii").lower()
        if (p.scheme != "https" or p.username or p.password or p.port not in (None, 443)
                or not re.fullmatch(r"[a-z0-9.-]+", host) or "." not in host
                or host.endswith((".local", ".localhost", ".internal", ".home", ".lan", "."))):
            raise ValueError
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError
        if any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-") for label in host.split(".")):
            raise ValueError
    except (ValueError, UnicodeError) as exc:
        raise SourceError("Use a public HTTPS hostname without credentials or a custom port.") from exc
    return urlunsplit(("https", host, p.path or "/", p.query, ""))


@dataclass(frozen=True)
class DetectedBoard:
    provider: str
    token: str
    region: str = "global"


def detect(url: str) -> DetectedBoard | None:
    p = urlsplit(public_url(url))
    parts = [unquote(part) for part in p.path.split("/") if part]
    provider, token, region = None, None, "global"
    if p.hostname in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
        provider = "greenhouse"
        token = parse_qs(p.query).get("for", [None])[0] if parts[:1] == ["embed"] else (parts[0] if parts else None)
    elif p.hostname == "boards-api.greenhouse.io" and parts[:2] == ["v1", "boards"]:
        provider, token = "greenhouse", parts[2] if len(parts) > 2 else None
    elif p.hostname in {"jobs.lever.co", "jobs.eu.lever.co"}:
        provider, token = "lever", parts[0] if parts else None
        region = "eu" if p.hostname == "jobs.eu.lever.co" else "global"
    elif p.hostname == "jobs.ashbyhq.com":
        provider, token = "ashby", parts[0] if parts else None
    if provider and token and TOKEN.fullmatch(token):
        return DetectedBoard(provider, token, region)
    return None


def unsupported_provider(url: str) -> str:
    host = urlsplit(public_url(url)).hostname
    return "workday" if host.endswith(".myworkdayjobs.com") or host == "myworkdayjobs.com" else "custom"


def board_url(provider: str, token: str, region: str = "global") -> str:
    if provider not in PROVIDERS or not TOKEN.fullmatch(token) or region not in {"global", "eu"} or (provider != "lever" and region != "global"):
        raise SourceError("Unsupported provider, board token or region.")
    host = {"greenhouse": "job-boards.greenhouse.io", "lever": "jobs.eu.lever.co" if region == "eu" else "jobs.lever.co", "ashby": "jobs.ashbyhq.com"}[provider]
    return f"https://{host}/{token}"
