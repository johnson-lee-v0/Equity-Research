from __future__ import annotations

import ssl

import pytest

from backend.app.research import discovery
from backend.app.research.sec_access import SecRequestGate


class FakeSocket:
    def __init__(self) -> None:
        self.timeouts: list[float] = []

    def settimeout(self, timeout: float) -> None:
        self.timeouts.append(timeout)


class FakeResponse:
    def __init__(self, *, status: int = 200, headers: dict[str, str] | None = None, chunks: list[bytes] | None = None) -> None:
        self.status = status
        self.headers = {key.lower(): value for key, value in (headers or {}).items()}
        self.chunks = list(chunks or [])
        self.read_calls: list[int] = []

    def getheader(self, name: str) -> str | None:
        return self.headers.get(name.lower())

    def read1(self, size: int = -1) -> bytes:
        self.read_calls.append(size)
        return self.chunks.pop(0) if self.chunks else b""

    # Keep the conventional method for unrelated callers; production fetch
    # code deliberately uses ``read1`` so its deadline applies per chunk.
    def read(self, size: int = -1) -> bytes:
        return self.read1(size)


class FakeConnection:
    instances: list[FakeConnection] = []
    responses: list[FakeResponse] = []

    def __init__(self, hostname: str, address: str, *, timeout: float, context: ssl.SSLContext) -> None:
        self.hostname = hostname
        self.address = address
        self.timeout = timeout
        self.context = context
        self.sock = FakeSocket()
        self.request_args: tuple[str, str, dict[str, str]] | None = None
        self.response = self.responses.pop(0)
        self.instances.append(self)

    def request(self, method: str, path: str, *, headers: dict[str, str]) -> None:
        self.request_args = (method, path, headers)

    def getresponse(self) -> FakeResponse:
        return self.response

    def close(self) -> None:
        return None


@pytest.fixture
def fake_transport(monkeypatch: pytest.MonkeyPatch):
    FakeConnection.instances = []
    FakeConnection.responses = []
    monkeypatch.setattr(discovery, "_PinnedHTTPSConnection", FakeConnection)
    monkeypatch.setattr(discovery, "_public_addresses", lambda host, port: ["93.184.216.34"])
    monkeypatch.setattr(discovery, "sec_request_gate", SecRequestGate(interval=0))
    return FakeConnection


def test_pinned_connection_connects_to_ip_but_keeps_hostname_for_tls(monkeypatch: pytest.MonkeyPatch) -> None:
    socket_calls: list[tuple[tuple[str, int], float | None]] = []
    raw_socket = object()
    wrapped_socket = object()

    class Context:
        def __init__(self) -> None:
            self.server_hostname: str | None = None

        def wrap_socket(self, sock: object, *, server_hostname: str) -> object:
            assert sock is raw_socket
            self.server_hostname = server_hostname
            return wrapped_socket

    context = Context()

    def create_connection(target: tuple[str, int], timeout: float | None) -> object:
        socket_calls.append((target, timeout))
        return raw_socket

    monkeypatch.setattr(discovery.socket, "create_connection", create_connection)
    connection = discovery._PinnedHTTPSConnection("origin.example", "93.184.216.34", timeout=3.5, context=context)  # type: ignore[arg-type]

    connection.connect()

    assert socket_calls == [(('93.184.216.34', 443), 3.5)]
    assert context.server_hostname == "origin.example"
    assert connection.sock is wrapped_socket


def test_fetch_reuses_each_dns_result_and_preserves_https_host(fake_transport, monkeypatch: pytest.MonkeyPatch) -> None:
    dns_calls: list[tuple[str, int | None]] = []

    def resolve(host: str, port: int | None) -> list[str]:
        dns_calls.append((host, port))
        return ["93.184.216.34"]

    monkeypatch.setattr(discovery, "_public_addresses", resolve)
    fake_transport.responses = [FakeResponse(headers={"Content-Type": "text/plain"}, chunks=[b"answer"])]

    result = discovery.fetch_public_page("https://Origin.Example:443/research?q=1")

    assert result.error is None
    assert result.content == "answer"
    assert dns_calls == [("origin.example", 443)]
    connection = fake_transport.instances[0]
    assert connection.address == "93.184.216.34"
    assert connection.request_args is not None
    method, path, headers = connection.request_args
    assert method == "GET"
    assert path == "/research?q=1"
    assert headers["Host"] == "origin.example"


def test_fetch_uses_configured_sec_user_agent(fake_transport, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        discovery.settings,
        "sec_user_agent",
        "Road2M research archive/1.0; contact=research@example.invalid",
    )
    fake_transport.responses = [FakeResponse(headers={"Content-Type": "text/plain"}, chunks=[b"answer"])]

    result = discovery.fetch_public_page("https://www.sec.gov/Archives/source")

    assert result.error is None
    assert fake_transport.instances[0].request_args is not None
    assert fake_transport.instances[0].request_args[2]["User-Agent"] == (
        "Road2M research archive/1.0; contact=research@example.invalid"
    )


def test_sec_contact_not_sent_to_other_publishers(fake_transport, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discovery.settings, "sec_user_agent", "Road2M; contact=operator@example.invalid")
    fake_transport.responses = [FakeResponse(headers={"Content-Type": "text/plain"}, chunks=[b"answer"])]
    result = discovery.fetch_public_page("https://publisher.example/source")
    assert result.error is None
    assert "operator@" not in fake_transport.instances[0].request_args[2]["User-Agent"]


def test_sec_denial_classifies_body_and_suppresses_next_archive_request(fake_transport):
    fake_transport.responses = [FakeResponse(status=403, chunks=[b"<title>Your Request Originates from an Undeclared Automated Tool</title>"])]
    rejected = discovery.fetch_public_page("https://www.sec.gov/Archives/a.htm")
    assert rejected.content == ""
    assert rejected.original_bytes is None
    assert rejected.http_diagnostics["category"] == "undeclared_automation"
    assert "undeclared automated tool" in rejected.error
    assert rejected.http_diagnostics["cooldown_seconds"] == 60

    suppressed = discovery.fetch_public_page("https://www.sec.gov/Archives/b.htm")
    assert "suppressed during cooldown" in suppressed.error
    assert len(fake_transport.instances) == 1
    # A blocked archive endpoint must not disable the working XBRL host.
    fake_transport.responses = [FakeResponse(headers={"Content-Type": "application/json"}, chunks=[b'{"cik":123}'])]
    assert discovery.fetch_public_page("https://data.sec.gov/submissions/CIK0000000123.json").error is None


@pytest.mark.parametrize(("status", "body", "category"), [
    (403, b"Request Rate Threshold Exceeded", "rate_limited"),
    (429, b"temporarily unavailable", "rate_limited"),
    (403, b"Access denied", "access_denied"),
])
def test_sec_denial_retains_only_bounded_known_diagnostics(fake_transport, status, body, category):
    response = FakeResponse(status=status, headers={"Retry-After": "120"}, chunks=[body])
    fake_transport.responses = [response]
    rejected = discovery.fetch_public_page("https://www.sec.gov/Archives/a.htm")
    assert rejected.http_diagnostics == {"http_status": status, "category": category, "retry_after_seconds": 120, "cooldown_seconds": 120}
    assert response.read_calls == [8192]
    assert rejected.original_bytes is None


def test_sec_connector_uses_same_safe_fetch_and_cooldown(fake_transport, tmp_path):
    from backend.app.config import Settings
    from backend.app.research.sec import SecConnector
    fake_transport.responses = [FakeResponse(status=403, chunks=[b"Access denied"])]
    discovery.fetch_public_page("https://data.sec.gov/submissions/CIK0000000123.json")
    connector = SecConnector(None, Settings(project_root=tmp_path, data_dir=tmp_path, sec_user_agent="fixture contact"))
    with pytest.raises(ValueError, match="suppressed during cooldown"):
        connector._get("https://data.sec.gov/submissions/CIK0000000456.json")
    assert len(fake_transport.instances) == 1


def test_sec_redirect_reapplies_contact_and_gate(fake_transport):
    fake_transport.responses = [
        FakeResponse(status=302, headers={"Location": "https://www.sec.gov/Archives/a.htm"}),
        FakeResponse(headers={"Content-Type": "text/plain"}, chunks=[b"filing"]),
    ]
    page = discovery.fetch_public_page("https://issuer.example/filing", sec_user_agent="fixture contact identity")
    assert page.error is None
    assert fake_transport.instances[0].request_args[2]["User-Agent"] == "Road2M local research source archive/1.0"
    assert fake_transport.instances[1].request_args[2]["User-Agent"] == "fixture contact identity"


def test_redirect_is_validated_for_public_dns_and_standard_port(fake_transport, monkeypatch: pytest.MonkeyPatch) -> None:
    dns_calls: list[str] = []

    def resolve(host: str, port: int | None) -> list[str]:
        dns_calls.append(host)
        return ["93.184.216.34"]

    monkeypatch.setattr(discovery, "_public_addresses", resolve)
    fake_transport.responses = [
        FakeResponse(status=302, headers={"Location": "https://second.example/next"}),
        FakeResponse(headers={"Content-Type": "text/html"}, chunks=[b"<p>done</p>"]),
    ]

    result = discovery.fetch_public_page("https://first.example/start")

    assert result.error is None
    assert result.final_url == "https://second.example/next"
    assert result.content == "done"
    # Each URL is resolved once and that checked IP is passed to its socket.
    assert dns_calls == ["first.example", "second.example"]
    assert [item.address for item in fake_transport.instances] == ["93.184.216.34", "93.184.216.34"]

    fake_transport.instances = []
    fake_transport.responses = [FakeResponse(status=302, headers={"Location": "https://second.example:8443/next"})]
    rejected = discovery.fetch_public_page("https://first.example/start")
    assert rejected.error is not None
    assert "standard HTTPS port 443" in rejected.error
    assert len(fake_transport.instances) == 1


def test_private_redirect_is_rejected_before_connecting(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = [FakeResponse(status=302, headers={"Location": "https://internal.example/secret"})]
    instances: list[FakeConnection] = []

    class RedirectConnection(FakeConnection):
        def __init__(self, hostname: str, address: str, *, timeout: float, context: ssl.SSLContext) -> None:
            super().__init__(hostname, address, timeout=timeout, context=context)
            instances.append(self)

    def resolve(host: str, port: int | None) -> list[str]:
        if host == "internal.example":
            raise ValueError("host resolves to a non-public address")
        return ["93.184.216.34"]

    monkeypatch.setattr(discovery, "_PinnedHTTPSConnection", RedirectConnection)
    monkeypatch.setattr(discovery, "_public_addresses", resolve)
    RedirectConnection.responses = responses

    result = discovery.fetch_public_page("https://public.example/start")

    assert result.error is not None
    assert "non-public" in result.error
    assert len(instances) == 1


@pytest.mark.parametrize(
    ("content_type", "body"),
    [
        ("application/pdf", b"%PDF-1.7\nnot extracted"),
        ("application/octet-stream", b"\x00\x01\x02binary"),
        ("text/plain", b"%PDF-1.7\nmislabelled binary"),
    ],
)
def test_malformed_pdf_and_other_binary_payloads_never_become_evidence(fake_transport, content_type: str, body: bytes) -> None:
    response = FakeResponse(headers={"Content-Type": content_type}, chunks=[body])
    fake_transport.responses = [response]

    result = discovery.fetch_public_page("https://example.com/source")

    assert result.error is not None
    assert result.content == ""
    assert "binary" in result.error.lower() or "unsupported" in result.error.lower()
    if content_type == "application/pdf":
        assert response.read_calls  # A valid PDF is now eligible for extraction.


def test_public_pdf_fetch_retains_original_and_page_locators(fake_transport):
    from backend.tests.test_pdf_evidence import pdf
    raw = pdf()
    fake_transport.responses = [FakeResponse(headers={"Content-Type":"application/pdf"},chunks=[raw])]
    result = discovery.fetch_public_page("https://example.com/filing.pdf")
    assert result.error is None
    assert result.original_bytes == raw
    assert result.document_metadata["page_count"] == 1
    assert "[PDF page 1]" in result.content


def test_read_timeout_is_reapplied_for_each_chunk_and_deadline_checked(monkeypatch: pytest.MonkeyPatch, fake_transport) -> None:
    clock = iter([100.0, 100.1, 100.2, 100.3, 100.4, 100.5, 100.6, 100.7])
    monkeypatch.setattr(discovery, "monotonic", lambda: next(clock))
    fake_transport.responses = [
        FakeResponse(headers={"Content-Type": "text/plain"}, chunks=[b"first", b"second", b""])
    ]

    result = discovery.fetch_public_page("https://example.com/source", timeout=5)

    assert result.error is None
    assert result.content == "firstsecond"
    timeouts = fake_transport.instances[0].sock.timeouts
    assert len(timeouts) == 3
    assert timeouts[0] > timeouts[1] > timeouts[2] > 0


def test_slow_drip_that_crosses_deadline_is_rejected(monkeypatch: pytest.MonkeyPatch, fake_transport) -> None:
    clock = iter([100.0, 100.1, 100.2, 105.1])
    monkeypatch.setattr(discovery, "monotonic", lambda: next(clock))
    response = FakeResponse(headers={"Content-Type": "text/plain"}, chunks=[b"late"])
    fake_transport.responses = [response]

    result = discovery.fetch_public_page("https://example.com/source", timeout=5)

    assert result.error is not None
    assert "time budget" in result.error
    assert len(response.read_calls) == 1


def test_table_cells_have_separators_and_rows_have_newlines() -> None:
    parser = discovery._PageTextParser()
    parser.feed("<table><tr><th>2024</th><th>2025</th></tr><tr><td>1</td><td>2</td></tr></table>")

    extracted = "".join(parser.parts)

    assert "2024 2025" in extracted
    assert "1 2" in extracted
    assert extracted.count("\n") >= 4


def test_nonpositive_limits_fail_before_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(discovery, "_public_addresses", lambda host, port: calls.append(host) or ["93.184.216.34"])

    too_small = discovery.fetch_public_page("https://example.com/source", max_bytes=0)
    too_short = discovery.fetch_public_page("https://example.com/source", timeout=0)

    assert "size limit" in (too_small.error or "")
    assert "timeout" in (too_short.error or "")
    assert calls == []
