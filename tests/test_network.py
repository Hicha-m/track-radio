import asyncio
import socket

import httpx
import pytest

from app.services.network import PublicTransport, resolve_public, validate_url


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://224.0.0.1",
        "http://[ff02::1]",
        "http://localhost",
        "http://127.0.0.1",
        "http://10.0.0.1",
        "http://169.254.169.254",
        "http://[::1]",
        "http://[::ffff:127.0.0.1]",
        "https://user:secret@example.com",
        "http://example.com:5432",
        "http://[fe80::1%eth0]",
    ],
)
def test_reject_non_public_url(url):
    with pytest.raises(ValueError):
        validate_url(url)


def test_reject_dns_with_private_answer(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ],
    )
    with pytest.raises(ValueError):
        resolve_public(validate_url("https://radio.example"))


def test_pins_verified_ip_preserves_host_and_tls_name(monkeypatch):
    requests = []

    async def run():
        transport = PublicTransport()
        transport.transports[("https", "radio.example", None)] = httpx.MockTransport(
            lambda request: requests.append(request) or httpx.Response(200)
        )
        monkeypatch.setattr("app.services.network.resolve_public", lambda url: "93.184.216.34")
        async with httpx.AsyncClient(transport=transport) as client:
            await client.get("https://radio.example/live")

    asyncio.run(run())
    assert requests[0].url.host == "93.184.216.34"
    assert requests[0].headers["Host"] == "radio.example"
    assert requests[0].extensions["sni_hostname"] == "radio.example"


def test_redirect_to_private_address_is_blocked(monkeypatch):
    async def run():
        transport = PublicTransport()
        transport.transports[("https", "radio.example", None)] = httpx.MockTransport(
            lambda request: httpx.Response(302, headers={"Location": "http://127.0.0.1/"})
        )
        monkeypatch.setattr("app.services.network.resolve_public", lambda url: "93.184.216.34")
        async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
            with pytest.raises(ValueError):
                await client.get("https://radio.example/live")

    asyncio.run(run())
