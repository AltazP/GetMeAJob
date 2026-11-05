"""Bounded, paced HTTPS GETs with no redirects or protection bypasses."""

from http.client import HTTPException
import json
from pathlib import Path
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from getmeajob.sources import SourceError


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def tls_context() -> ssl.SSLContext:
    """Keep verification on; python.org macOS installs may lack their own CA bundle."""
    context = ssl.create_default_context()
    defaults = ssl.get_default_verify_paths()
    if defaults.cafile is None and defaults.capath is None and Path("/etc/ssl/cert.pem").is_file():
        context.load_verify_locations(cafile="/etc/ssl/cert.pem")
    return context


class JsonClient:
    HOSTS = {"boards-api.greenhouse.io", "api.lever.co", "api.eu.lever.co"}
    MAX_BYTES = 20 * 1024 * 1024

    def __init__(self, interval: float = 1.0, timeout: float = 20.0,
                 opener=None, sleep=time.sleep, clock=time.monotonic):
        self.interval = max(1.0, interval)
        self.timeout = timeout
        self.opener = opener or build_opener(NoRedirect(), HTTPSHandler(context=tls_context()))
        self.sleep, self.clock = sleep, clock
        self.last_request: dict[str, float] = {}
        self.blocked_hosts: set[str] = set()

    def get(self, url: str):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc not in self.HOSTS:
            raise SourceError("Only supported public HTTPS API hosts are permitted.")
        host = parsed.netloc
        if host in self.blocked_hosts:
            raise SourceError("Host paused for this search after rate limiting or access denial.")
        for attempt in range(3):
            wait = self.interval - (self.clock() - self.last_request.get(host, float("-inf")))
            if wait > 0:
                self.sleep(wait)
            self.last_request[host] = self.clock()
            request = Request(url, headers={"Accept": "application/json", "User-Agent": "GetMeAJob/0.2 (public job discovery)"})
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    raw = response.read(self.MAX_BYTES + 1)
                if len(raw) > self.MAX_BYTES:
                    raise SourceError("API response exceeded the safe size limit.")
                return json.loads(raw)
            except HTTPError as exc:
                if exc.code in (401, 403, 429):
                    # No retries on access denial or throttling, including other boards on this host.
                    self.blocked_hosts.add(host)
                    retry = exc.headers.get("Retry-After", "not provided") if exc.headers else "not provided"
                    if exc.code == 429:
                        raise SourceError(f"HTTP 429: host paused; Retry-After: {retry}. Retry later.") from exc
                    raise SourceError(f"HTTP {exc.code}: access denied; host paused.") from exc
                if exc.code == 503 and exc.headers and exc.headers.get("Retry-After"):
                    self.blocked_hosts.add(host)
                    raise SourceError("HTTP 503: server requested a pause; retry after " + exc.headers["Retry-After"] + ".") from exc
                if exc.code in (500, 502, 503, 504) and attempt < 2:
                    self.sleep(2 ** (attempt + 1))
                    continue
                raise SourceError(f"HTTP {exc.code}: board request failed (check token and region).") from exc
            except (URLError, TimeoutError, OSError, HTTPException) as exc:
                reason = exc.reason if isinstance(exc, URLError) else exc
                if isinstance(reason, ssl.SSLCertVerificationError):
                    raise SourceError("TLS certificate verification failed. Check your Python/system CA certificates; verification has not been disabled.") from exc
                if attempt < 2:
                    self.sleep(2 ** (attempt + 1))
                    continue
                raise SourceError("Network request failed after three attempts; check connectivity and TLS certificates.") from exc
            except (ValueError, UnicodeError, RecursionError) as exc:
                raise SourceError("API returned invalid JSON; no content was executed.") from exc
        raise SourceError("Request failed.")
