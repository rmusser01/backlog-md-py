"""Shared HTTP request-header parsing for the loopback servers.

The browser board and the MCP HTTP daemon both bind loopback only and both
defend against DNS rebinding by checking the ``Host`` header. They had separate
copies of this parser and had already started to drift — one rejected userinfo
explicitly, the other only implicitly, via a hostname that happened to fail its
allow-list. Two independently-maintained answers to "what is a valid loopback
Host" is how a security control quietly stops matching itself, so there is one
answer here and both servers import it.

This module deliberately depends on nothing else in the package, so neither
server has to depend on the other.
"""
from __future__ import annotations

# Port assumed when a Host header carries none. It is never an ephemeral port, so
# a header without a port cannot accidentally match a bound server.
DEFAULT_HTTP_PORT = 80

LOOPBACK_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "::1"})


def bracketed_host(host: str) -> str:
    """Bracket an IPv6 literal for use in a URL authority."""
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def http_url(host: str, port: int, path: str = "") -> str:
    """Build an HTTP URL, bracketing IPv6 literals when needed."""
    return f"http://{bracketed_host(host)}:{port}{path}"


def parse_host_header(value: str) -> tuple[str, int | None] | None:
    """Split a Host header into ``(hostname, port)``; ``None`` when malformed.

    Hand-rolled rather than ``urlparse(f"//{value}")`` because urlparse treats
    the value as an authority and silently *drops* userinfo, so
    ``evil.example.com@127.0.0.1:PORT`` would parse as a plain loopback host and
    sail through a rebinding check. A Host header has no userinfo component at
    all, so an ``@`` is rejected outright.

    Handles the IPv6 bracket form (``[::1]:8080`` / ``[::1]``) and rejects the
    unbracketed form, which is not a valid Host header.
    """
    raw = value.strip()
    if not raw or "@" in raw:
        return None
    if raw.startswith("["):
        end = raw.find("]")
        if end == -1:
            return None
        hostname = raw[1:end]
        remainder = raw[end + 1 :]
    elif raw.count(":") > 1:
        return None
    else:
        hostname, separator, port_text = raw.partition(":")
        remainder = f":{port_text}" if separator else ""
    if not hostname:
        return None
    if not remainder:
        return hostname, None
    if not remainder.startswith(":"):
        return None
    port_text = remainder[1:]
    if not port_text.isdigit():
        return None
    return hostname, int(port_text)


def host_header_is_loopback(value: str | None, expected_port: int) -> bool:
    """True when ``value`` names a loopback host on ``expected_port``.

    Fails closed on a missing or malformed header: a browser always sends Host,
    so an absent one is not a case worth accommodating.
    """
    if value is None:
        return False
    parsed = parse_host_header(value)
    if parsed is None:
        return False
    hostname, port = parsed
    if hostname.casefold() not in LOOPBACK_HOSTNAMES:
        return False
    return (port if port is not None else DEFAULT_HTTP_PORT) == expected_port
