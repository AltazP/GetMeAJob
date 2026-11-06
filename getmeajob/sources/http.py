"""Paced, bounded public GET requests with persistent host pauses and request budgets."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPException
import json
import math
from pathlib import Path
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request

from getmeajob.network import SafeOpener
from getmeajob.sources import SourceError
from getmeajob.urls import public_url


class BudgetExceeded(SourceError):
    """A run reached a configured limit; this is not a broken source."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    defaults = ssl.get_default_verify_paths()
    if defaults.cafile is None and defaults.capath is None and Path("/etc/ssl/cert.pem").is_file():
        context.load_verify_locations(cafile="/etc/ssl/cert.pem")
    return context


def retry_seconds(value: str | None, default: float = 3600) -> float:
    if value:
        try:
            seconds = float(value)
            if math.isfinite(seconds):
                return max(1, seconds)
        except ValueError:
            try:
                parsed = parsedate_to_datetime(value)
                return max(1, (parsed - datetime.now(timezone.utc)).total_seconds())
            except (ValueError, TypeError):
                pass
    return default


@dataclass(frozen=True)
class Probe:
    """One GET answered as-is: status, redirect target (never followed) and a size-capped body."""
    status: int
    location: str | None
    body: bytes


class JsonClient:
    HOSTS = {"boards-api.greenhouse.io", "api.lever.co", "api.eu.lever.co", "api.ashbyhq.com", "hacker-news.firebaseio.com"}
    MAX_BYTES = 20 * 1024 * 1024

    def __init__(self, interval: float = 1.0, timeout: float = 20.0,
                 opener=None, sleep=time.sleep, clock=time.monotonic,
                 max_requests: int | None = None, per_host_limit: int | None = None,
                 per_source_limit: int | None = None, state=None, offline: bool = False, memoize: bool = False):
        self.interval, self.timeout = max(1.0, interval), timeout
        self.opener = opener or SafeOpener(tls_context())
        self.sleep, self.clock = sleep, clock
        self.last_request: dict[str, float] = {}
        self.blocked_hosts: set[str] = set()
        self.max_requests, self.per_host_limit, self.per_source_limit = max_requests, per_host_limit, per_source_limit
        self.requests, self.host_counts, self.source_counts = 0, {}, {}
        self.source = "default"
        self.state, self.offline = state, offline
        self.memo, self.memoize = {}, memoize

    def _budget(self, host):
        if self.offline:
            raise BudgetExceeded("Offline mode prohibits network requests.")
        if ((self.max_requests is not None and self.requests >= self.max_requests)
            or (self.per_host_limit is not None and self.host_counts.get(host, 0) >= self.per_host_limit)
            or (self.per_source_limit is not None and self.source_counts.get(self.source, 0) >= self.per_source_limit)):
            raise BudgetExceeded("Configured request budget reached; remaining work is deferred.")
        self.requests += 1
        self.host_counts[host] = self.host_counts.get(host, 0) + 1
        self.source_counts[self.source] = self.source_counts.get(self.source, 0) + 1

    def _pause(self, host, seconds):
        self.blocked_hosts.add(host)
        if self.state:
            self.state.pause(host, seconds)

    def read(self, url: str, *, page_hosts: tuple[str, ...] = (), max_bytes: int | None = None) -> bytes:
        url = public_url(url)
        host = urlsplit(url).hostname
        if host not in self.HOSTS | set(page_hosts):
            raise SourceError("Only supported public HTTPS API hosts or explicitly permitted page hosts are allowed.")
        if host in self.blocked_hosts or (self.state and self.state.paused(host)):
            raise SourceError("Host paused after rate limiting or access denial; retry after the persisted pause.")
        limit = max_bytes or self.MAX_BYTES
        for attempt in range(3):
            self._budget(host)
            wait = self.interval - (self.clock() - self.last_request.get(host, float("-inf")))
            if wait > 0:
                self.sleep(wait)
            self.last_request[host] = self.clock()
            request = Request(url, headers={"Accept": "text/html,text/plain" if page_hosts else "application/json",
                                           "User-Agent": "GetMeAJob/0.2.5 (public career source discovery)"})
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    raw = response.read(limit + 1)
                if len(raw) > limit:
                    raise SourceError("API/page response exceeded the safe size limit.")
                return raw
            except HTTPError as exc:
                retry = exc.headers.get("Retry-After") if exc.headers else None
                if exc.code in (401, 403, 429) or (retry and exc.code >= 500):
                    self._pause(host, retry_seconds(retry, 86400 if exc.code in (401, 403) else 3600))
                    raise SourceError(f"HTTP {exc.code}: host paused; Retry-After: {retry or 'not provided'}. Retry later.") from exc
                if exc.code in (500, 502, 503, 504) and attempt < 2:
                    self.sleep(2 ** (attempt + 1))
                    continue
                raise SourceError(f"HTTP {exc.code}: request failed (check board, region or public access).") from exc
            except (URLError, TimeoutError, OSError, HTTPException) as exc:
                reason = exc.reason if isinstance(exc, URLError) else exc
                if isinstance(reason, ssl.SSLCertVerificationError):
                    raise SourceError("TLS certificate verification failed. Check Python/system CA certificates; verification remains enabled.") from exc
                if attempt < 2:
                    self.sleep(2 ** (attempt + 1))
                    continue
                raise SourceError("Network request failed after three attempts; check connectivity and TLS certificates.") from exc
        raise SourceError("Request failed.")

    def probe(self, url: str, *, page_hosts: tuple[str, ...], max_bytes: int = 1024 * 1024,
              accept: str = "application/json,text/html;q=0.8") -> Probe:
        """A single paced, budgeted GET that reports 3xx/4xx/5xx instead of raising, and never follows redirects.

        Used to ask whether a posting still exists. Access denials (401/403/429) pause the host exactly as read() does.
        """
        url = public_url(url)
        host = urlsplit(url).hostname
        if host not in self.HOSTS | set(page_hosts):
            raise SourceError("Only supported public HTTPS hosts or explicitly permitted page hosts are allowed.")
        if host in self.blocked_hosts or (self.state and self.state.paused(host)):
            raise SourceError("Host paused after rate limiting or access denial; retry after the persisted pause.")
        self._budget(host)
        wait = self.interval - (self.clock() - self.last_request.get(host, float("-inf")))
        if wait > 0:
            self.sleep(wait)
        self.last_request[host] = self.clock()
        request = Request(url, headers={"Accept": accept, "User-Agent": "GetMeAJob/0.2.5 (checking whether a saved posting is still open)"})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(max_bytes + 1)
                if len(raw) > max_bytes:
                    raise SourceError("Response exceeded the safe size limit.")
                return Probe(int(getattr(response, "status", 200) or 200), None, raw)
        except HTTPError as exc:
            retry = exc.headers.get("Retry-After") if exc.headers else None
            if exc.code in (401, 403, 429):
                self._pause(host, retry_seconds(retry, 86400 if exc.code in (401, 403) else 3600))
            try:
                body = exc.read(max_bytes) if exc.fp else b""
            except (OSError, ValueError):
                body = b""
            return Probe(exc.code, exc.headers.get("Location") if exc.headers else None, body)
        except (URLError, TimeoutError, OSError, HTTPException) as exc:
            reason = exc.reason if isinstance(exc, URLError) else exc
            if isinstance(reason, ssl.SSLCertVerificationError):
                raise SourceError("TLS certificate verification failed; verification remains enabled.") from exc
            raise SourceError("Network request failed.") from exc

    def get(self, url: str):
        if self.memoize and url in self.memo:
            return self.memo[url]
        raw = self.read(url)
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise SourceError("API returned invalid JSON; no content was executed.") from exc
        if self.memoize:
            self.memo[url] = result
        return result
