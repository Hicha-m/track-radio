"""HTTP public uniquement, avec connexion à l'IP vérifiée (anti DNS rebinding)."""

import asyncio
import ipaddress
import socket
import ssl

import httpx

from app.config import settings


def public_ip(value: str) -> bool:
    address = ipaddress.ip_address(value)
    address = getattr(address, "ipv4_mapped", None) or address
    return address.is_global and not (
        address.is_multicast
        or address.is_reserved
        or address.is_loopback
        or address.is_link_local
        or address.is_unspecified
    )


def validate_url(value: str) -> httpx.URL:
    try:
        url = httpx.URL(value)
    except httpx.InvalidURL as error:
        raise ValueError("URL invalide.") from error
    if (
        url.scheme not in {"http", "https"}
        or not url.host
        or url.username
        or url.password
        or url.port not in {None, 80, 443}
        or "%" in url.host
    ):
        raise ValueError("URL HTTP(S) publique requise, sans identifiants, sur le port 80 ou 443.")
    if url.host.lower() == "localhost" or url.host.lower().endswith(".localhost"):
        raise ValueError("Les adresses locales sont interdites.")
    try:
        address = ipaddress.ip_address(url.host)
    except ValueError:
        return url
    if not public_ip(str(address)):
        raise ValueError("Les adresses privées ou réservées sont interdites.")
    return url


def resolve_public(url: httpx.URL) -> str:
    try:
        entries = socket.getaddrinfo(
            url.host, url.port or (443 if url.scheme == "https" else 80), type=socket.SOCK_STREAM
        )
    except socket.gaierror as error:
        raise ValueError("Le nom de domaine ne peut pas être résolu.") from error
    addresses = sorted({entry[4][0] for entry in entries}, key=lambda ip: ":" in ip)
    if not addresses or not all(public_ip(address) for address in addresses):
        raise ValueError("La destination doit utiliser exclusivement des adresses IP publiques.")
    return addresses[0]


class PublicTransport(httpx.AsyncBaseTransport):
    def __init__(self):
        self.context = ssl.create_default_context()
        self.transports: dict[tuple, httpx.AsyncHTTPTransport] = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        # HTTPX calls the transport again for every redirect. Resolve once, then use
        # that exact IP for the connection; retain Host and TLS SNI/certificate checks.
        url = validate_url(str(request.url))
        address = await asyncio.to_thread(resolve_public, url)
        origin = (url.scheme, url.host, url.port)
        if origin not in self.transports:
            if len(self.transports) >= settings.max_stations * 4:
                raise ValueError("Trop de destinations HTTP distinctes.")
            self.transports[origin] = httpx.AsyncHTTPTransport(
                verify=self.context,
                trust_env=False,
                limits=httpx.Limits(
                    max_connections=4, max_keepalive_connections=1, keepalive_expiry=60
                ),
            )
        # Separate pools per original origin prevent reusing a TLS connection for
        # another hostname which happens to share the same IP address.
        pinned = httpx.Request(
            request.method,
            url.copy_with(host=address),
            headers=request.headers,
            stream=request.stream,
            extensions={**request.extensions, "sni_hostname": url.host},
        )
        return await self.transports[origin].handle_async_request(pinned)

    async def aclose(self):
        for transport in self.transports.values():
            await transport.aclose()


def metadata_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=PublicTransport(),
        trust_env=False,
        follow_redirects=True,
        max_redirects=5,
        timeout=settings.request_timeout_seconds,
        headers={"User-Agent": "RadioTracker/0.2", "Accept-Encoding": "identity"},
    )
