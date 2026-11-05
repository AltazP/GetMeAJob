"""SSRF boundary: resolve public addresses once, pin the socket, verify TLS hostname."""

from http.client import HTTPSConnection
import ipaddress
import socket
from urllib.error import HTTPError
from urllib.parse import urlsplit

from getmeajob.sources import SourceError
from getmeajob.urls import public_url


def addresses(host: str, timeout: float):
    # System resolver timeout follows macOS resolver policy; socket I/O uses timeout.
    rows = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not rows:
        raise SourceError("Hostname resolved to no addresses.")
    for family, kind, protocol, _, address in rows:
        ip = ipaddress.ip_address(address[0])
        translated = isinstance(ip, ipaddress.IPv6Address) and (
            ip.ipv4_mapped is not None or ip.sixtofour is not None or ip.teredo is not None
            or ip in ipaddress.ip_network("64:ff9b::/96") or ip in ipaddress.ip_network("64:ff9b:1::/48"))
        if not ip.is_global or translated:
            raise SourceError("Hostname resolves to a non-public address; request blocked.")
    return sorted(rows, key=lambda row: row[0] != socket.AF_INET)


class PinnedConnection(HTTPSConnection):
    def __init__(self, host, resolved, context, timeout):
        super().__init__(host, timeout=timeout, context=context)
        self.resolved = resolved

    def connect(self):
        family, kind, protocol, _, address = self.resolved
        raw = socket.socket(family, kind, protocol)
        try:
            raw.settimeout(self.timeout)
            raw.connect(address)
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


class Response:
    def __init__(self, response, connection):
        self.response, self.connection = response, connection
        self.headers = response.headers

    def read(self, size):
        return self.response.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.response.close()
        self.connection.close()


class SafeOpener:
    """No redirects, cookies, proxy environment variables or second DNS lookup."""
    def __init__(self, context):
        self.context = context

    def open(self, request, timeout):
        parsed = urlsplit(public_url(request.full_url))
        resolved = addresses(parsed.hostname, timeout)[0]
        connection = PinnedConnection(parsed.hostname, resolved, self.context, timeout)
        try:
            target = parsed.path + ("?" + parsed.query if parsed.query else "")
            connection.request("GET", target, headers=dict(request.header_items()))
            response = connection.getresponse()
            if response.status >= 300:
                status, headers = response.status, response.headers
                response.close()
                connection.close()
                raise HTTPError(request.full_url, status, "Public request failed", headers, None)
            return Response(response, connection)
        except BaseException:
            connection.close()
            raise
