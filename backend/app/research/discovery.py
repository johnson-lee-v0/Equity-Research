"""Bounded public source discovery and provenance-safe page fetching.

The hosted model is allowed to suggest search questions and public URLs during
the A01 discovery stage.  A URL or a search snippet is never evidence by
itself.  This module fetches the page through a small SSRF-resistant client;
only the returned page text is imported into the namespace and supplied to
normal analyst prompts.
"""
from __future__ import annotations

import html
import hashlib
import http.client
import ipaddress
import re
import socket
import ssl
from dataclasses import dataclass
from html.parser import HTMLParser
from time import monotonic
from typing import Iterable
from urllib.parse import urljoin, urlsplit

from ..config import settings
from .sec_access import DENIAL_MAX_BYTES, SecCooldownError, denial_diagnostics, denial_message, is_sec_host, sec_request_gate


_MAX_REDIRECTS = 3
_DEFAULT_TIMEOUT = 20.0
_DEFAULT_MAX_BYTES = 5_000_000
_HTML_MEDIA_TYPES = {"text/html", "application/xhtml+xml"}
_TEXT_MEDIA_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "application/json",
    "text/csv",
    "application/xml",
    "text/xml",
}
_BINARY_SIGNATURES = (b"%PDF-", b"PK\x03\x04", b"\x7fELF", b"MZ")


def _source_user_agent(hostname: str, configured_identity: str | None = None) -> str:
    """Use the configured contact identity only for SEC requests.

    SEC archive requests are routinely rejected when the user agent has no
    contact address.  Discovery used to send a hard-coded identifier and
    therefore ignored the existing ``ROAD2M_SEC_USER_AGENT`` setting used by
    the SEC connector.  Keep the unconfigured default explicit so local
    environments still surface SEC availability honestly; a configured
    address is picked up by both fetch paths.
    """
    host = str(hostname or "").casefold().rstrip(".")
    if host != "sec.gov" and not host.endswith(".sec.gov"):
        return "Road2M local research source archive/1.0"
    configured = str(configured_identity if configured_identity is not None else getattr(settings, "sec_user_agent", "") or "").strip()
    return configured or "Road2M local research; contact not configured"


class _PageTextParser(HTMLParser):
    """Convert fetched HTML into bounded, line-oriented text for locators."""

    _SKIP = {"script", "style", "noscript", "template", "svg", "ix:hidden"}
    _BLOCK = {"p", "div", "li", "tr", "br", "h1", "h2", "h3", "h4", "section", "article"}
    _CELL = {"td", "th"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self.parts: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.lower()
        if normalized_tag in self._SKIP:
            self._skip += 1
        elif not self._skip and normalized_tag in self._CELL:
            # Keep adjacent table cells distinct (for example, ``1`` and ``2``
            # must not become the misleading value ``12``).
            self.parts.append(" ")
        elif not self._skip and normalized_tag in self._BLOCK:
            self.parts.append("\n")
        link_attribute = {"a": "href", "iframe": "src", "embed": "src", "object": "data"}.get(normalized_tag)
        if not self._skip and link_attribute and len(self.links) < 500:
            href = next((value for name, value in attrs if name.lower() == link_attribute), None)
            if href and len(href) <= 3000:
                self.links.append(href)

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        if normalized_tag in self._SKIP and self._skip:
            self._skip -= 1
        elif not self._skip and normalized_tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            value = html.unescape(data)
            value = re.sub(r"[ \t\r\f\v]+", " ", value)
            if value.strip():
                self.parts.append(value)


def _public_addresses(host: str, port: int | None) -> list[str]:
    """Resolve a host and reject local, private, or otherwise non-public IPs."""
    try:
        literal = ipaddress.ip_address(host)
        addresses = [literal]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)
        except (OSError, socket.gaierror) as exc:
            raise ValueError("host could not be resolved") from exc
        addresses = []
        for info in infos:
            sockaddr = info[4]
            if not sockaddr:
                continue
            try:
                addresses.append(ipaddress.ip_address(str(sockaddr[0])))
            except ValueError:
                continue
    if not addresses:
        raise ValueError("host has no address")
    for address in addresses:
        if not address.is_global or address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_multicast or address.is_unspecified:
            raise ValueError("host resolves to a non-public address")
    return [str(item) for item in addresses]


def _validated_public_url(url: str) -> tuple[str, list[str]]:
    """Return a normalized URL and the public addresses already checked for it."""
    try:
        candidate = url.strip()
        parsed = urlsplit(candidate)
        port = parsed.port
    except (AttributeError, ValueError) as exc:
        raise ValueError("URL is malformed") from exc
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("only public HTTPS URLs are eligible for discovery")
    if parsed.port not in (None, 443):
        raise ValueError("only standard HTTPS port 443 is eligible for discovery")
    # Check the untrimmed input as well so a trailing newline cannot be
    # silently accepted by ``str.strip`` before it reaches the request path.
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in url):
        raise ValueError("URL contains control characters")
    if parsed.fragment:
        # Fragments do not reach HTTP servers and make provenance ambiguous.
        candidate = candidate.split("#", 1)[0]
    # Resolve once for this URL and pass the checked address into the direct
    # connection below. This closes the DNS-rebinding window between the
    # validation lookup and socket creation.
    return candidate, _public_addresses(parsed.hostname, port)


def validate_public_url(url: str) -> str:
    """Validate an HTTPS URL before each request and redirect."""
    candidate, _ = _validated_public_url(url)
    return candidate


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to a resolved public IP while retaining hostname TLS/SNI."""

    def __init__(self, hostname: str, address: str, *, timeout: float, context: ssl.SSLContext) -> None:
        super().__init__(hostname, port=443, timeout=timeout, context=context)
        self._resolved_address = address

    def connect(self) -> None:
        self.sock = socket.create_connection((self._resolved_address, 443), self.timeout)
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self._tunnel_host or self.host)


@dataclass(frozen=True, slots=True)
class FetchedSource:
    requested_url: str
    final_url: str
    content: str
    title: str
    retrieved_at: str
    error: str | None = None
    original_bytes: bytes | None = None
    document_metadata: dict | None = None
    links: tuple[str, ...] = ()
    http_diagnostics: dict | None = None


def _public_page_links(values: Iterable[str], base_url: str) -> tuple[str, ...]:
    """Record bounded HTTPS links as provenance; never fetch them implicitly."""
    links = []
    for value in values:
        if len(links) >= 500:
            break
        try:
            parsed = urlsplit(urljoin(base_url, value))
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or len(parsed.geturl()) > 3000:
                continue
            url = parsed._replace(fragment="").geturl()
            if url not in links:
                links.append(url)
        except ValueError:
            continue
    return tuple(links)


def _retrieved_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _looks_binary_payload(raw: bytes) -> bool:
    """Reject common binary payloads even when a server lies about its MIME type."""
    if any(raw.startswith(signature) for signature in _BINARY_SIGNATURES):
        return True
    sample = raw[:4096]
    if b"\x00" in sample:
        return True
    control_bytes = sum(
        byte < 0x09 or 0x0E <= byte < 0x20 or byte == 0x7F
        for byte in sample
    )
    return control_bytes > max(8, len(sample) // 20)


def _host_header(parsed) -> str:
    """Format the original HTTPS authority for the request Host header."""
    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    # Non-443 ports are rejected during URL validation. Keep this branch for
    # callers that construct a parsed URL independently of that validator.
    return hostname if parsed.port in (None, 443) else f"{hostname}:{parsed.port}"


def fetch_public_page(url: str, *, max_bytes: int = _DEFAULT_MAX_BYTES, timeout: float = _DEFAULT_TIMEOUT, sec_user_agent: str | None = None) -> FetchedSource:
    """Fetch one page while preserving the exact final URL and retrieval time."""
    requested = url.strip()
    retrieved_at = _retrieved_now()
    raw = b""
    content_type = ""
    current = requested
    http_diagnostics = None
    try:
        max_bytes = int(max_bytes)
        timeout = float(timeout)
        if max_bytes <= 0:
            raise ValueError("configured source size limit must be positive")
        if timeout <= 0:
            raise ValueError("configured page fetch timeout must be positive")
        deadline = monotonic() + timeout
        current, addresses = _validated_public_url(requested)
        context = ssl.create_default_context()
        raw = b""
        content_type = ""
        final_url = current
        for redirect_no in range(_MAX_REDIRECTS + 1):
            parsed = urlsplit(current)
            sec_host = is_sec_host(parsed.hostname or "")
            if sec_host:
                sec_request_gate.acquire(parsed.hostname or "", deadline=deadline)
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError("page fetch exceeded its time budget")
            connection = _PinnedHTTPSConnection(parsed.hostname or "", addresses[0], timeout=remaining, context=context)
            try:
                path = parsed.path or "/"
                if parsed.query:
                    path += "?" + parsed.query
                if any(ord(char) < 0x20 or ord(char) == 0x7F for char in path):
                    raise ValueError("URL contains control characters")
                connection.request(
                    "GET",
                    path,
                    headers={
                        "Accept": "text/html, text/plain, application/xhtml+xml, application/json, text/csv, application/pdf",
                        "Accept-Encoding": "identity",
                        "User-Agent": _source_user_agent(parsed.hostname or "", sec_user_agent),
                        "Host": _host_header(parsed),
                    },
                )
                response = connection.getresponse()
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.getheader("Location")
                    if not location:
                        raise ValueError("redirect did not provide a target")
                    if redirect_no >= _MAX_REDIRECTS:
                        raise ValueError("too many redirects")
                    current, addresses = _validated_public_url(urljoin(current, location))
                    continue
                if response.status < 200 or response.status >= 300:
                    if sec_host and response.status in {403, 429}:
                        # Error HTML is diagnostic only and never enters the
                        # evidence archive. Even a stalled denial body must
                        # leave the status and cooldown intact.
                        denial_body = b""
                        try:
                            if connection.sock is not None:
                                connection.sock.settimeout(max(0.001, min(2.0, deadline - monotonic())))
                            denial_body = response.read1(min(DENIAL_MAX_BYTES, max_bytes))
                        except (OSError, TimeoutError, http.client.HTTPException):
                            pass
                        http_diagnostics = sec_request_gate.denied(parsed.hostname or "", denial_diagnostics(response.status, denial_body, response.getheader("Retry-After")))
                        raise ValueError(denial_message(http_diagnostics))
                    raise ValueError(f"source returned HTTP {response.status}")
                if sec_host:
                    sec_request_gate.succeeded(parsed.hostname or "")
                content_type = str(response.getheader("Content-Type") or "").lower()
                media_type = content_type.split(";", 1)[0].strip()
                if media_type not in _TEXT_MEDIA_TYPES | {"application/pdf"}:
                    raise ValueError("source returned an unsupported or binary content type")
                if str(response.getheader("Content-Encoding") or "identity").lower() not in {"", "identity"}:
                    raise ValueError("compressed source content is unsupported")
                length_header = response.getheader("Content-Length")
                if length_header:
                    content_length = int(length_header)
                    if content_length < 0 or content_length > max_bytes:
                        raise ValueError("page exceeds the configured source size limit")
                chunks: list[bytes] = []
                total = 0
                while total <= max_bytes:
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        raise TimeoutError("page fetch exceeded its time budget")
                    if connection.sock is not None:
                        connection.sock.settimeout(remaining)
                    # ``read`` may wait for the entire requested body on some
                    # response implementations.  ``read1`` asks for one
                    # bounded chunk, while the socket timeout above is reset
                    # from the remaining absolute deadline on every loop.
                    chunk = response.read1(min(64 * 1024, max_bytes + 1 - total))
                    if monotonic() > deadline:
                        raise TimeoutError("page fetch exceeded its time budget")
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError("page exceeds the configured source size limit")
                raw = b"".join(chunks)
                if media_type == "application/pdf":
                    from .pdf_extract import extract_pdf
                    extracted = extract_pdf(raw,timeout=min(20, max(1,deadline-monotonic())))
                    return FetchedSource(requested,current,extracted["content"],urlsplit(current).hostname or current,retrieved_at,original_bytes=raw,document_metadata=extracted["metadata"])
                if _looks_binary_payload(raw):
                    raise ValueError("source returned PDF or binary content")
                final_url = current
                break
            finally:
                connection.close()
        else:
            raise ValueError("too many redirects")
        charset_match = re.search(r"charset=([\w.-]+)", content_type)
        encoding = charset_match.group(1) if charset_match else "utf-8"
        try:
            decoded = raw.decode(encoding, errors="replace")
        except LookupError:
            decoded = raw.decode("utf-8", errors="replace")
        links: tuple[str, ...] = ()
        if content_type.split(";", 1)[0].strip() in _HTML_MEDIA_TYPES or re.search(r"<\s*(html|body|article|main)\b", decoded[:20_000], flags=re.IGNORECASE):
            parser = _PageTextParser()
            parser.feed(decoded)
            links = _public_page_links(parser.links, final_url)
            decoded = "".join(parser.parts)
        decoded = "\n".join(line.strip() for line in decoded.splitlines() if line.strip())
        if not decoded:
            raise ValueError("page returned no readable text")
        title = urlsplit(final_url).hostname or final_url
        if content_type.split(";", 1)[0].strip() in _HTML_MEDIA_TYPES:
            title_match = re.search(r"<title[^>]*>(.*?)</title>", raw.decode(encoding, errors="replace"), flags=re.IGNORECASE | re.DOTALL)
            if title_match:
                title = re.sub(r"\s+", " ", html.unescape(title_match.group(1))).strip()[:300] or title
        return FetchedSource(requested, final_url, decoded, title, retrieved_at, links=links)
    except Exception as exc:
        if isinstance(exc, SecCooldownError):
            http_diagnostics = exc.diagnostic
        error = str(exc)[:500]
        is_pdf = content_type.split(";",1)[0].strip() == "application/pdf" and raw.startswith(b"%PDF-") and len(raw) <= max_bytes
        metadata = {"mime_type":"application/pdf","original_hash":hashlib.sha256(raw).hexdigest(),"extraction_version":"pdf-layout.v1","status":"unavailable","error":error,"pages":[]} if is_pdf else None
        return FetchedSource(requested,current,"",requested,retrieved_at,error,original_bytes=raw if is_pdf else None,document_metadata=metadata,http_diagnostics=http_diagnostics)


def fetch_public_pages(urls: Iterable[str], *, max_sources: int = 10, max_bytes: int = _DEFAULT_MAX_BYTES, timeout: float = _DEFAULT_TIMEOUT) -> list[FetchedSource]:
    """Fetch a deduplicated bounded URL list; failures remain explicit records."""
    output: list[FetchedSource] = []
    seen: set[str] = set()
    for raw_url in urls:
        if not isinstance(raw_url, str):
            continue
        url = raw_url.strip()
        if not url or url in seen:
            continue
        seen.add(url)
        if len(output) >= max(0, int(max_sources)):
            break
        output.append(fetch_public_page(url, max_bytes=max_bytes, timeout=timeout))
    return output
