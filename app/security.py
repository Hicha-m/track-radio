import asyncio
import secrets

from fastapi import HTTPException, Request

from app.config import settings


def require_admin(request: Request):
    expected = settings.admin_token.get_secret_value()
    if not expected:
        raise HTTPException(503, "Administration désactivée : configurer ADMIN_TOKEN côté serveur.")
    supplied = request.headers.get("authorization", "")
    if not secrets.compare_digest(supplied.encode(), f"Bearer {expected}".encode()):
        raise HTTPException(401, "Clé administrateur invalide.")


class SecurityMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if scope["method"] in {"POST", "PUT", "PATCH"}:
            body = bytearray()
            try:
                async with asyncio.timeout(10):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        chunk = message.get("body", b"")
                        if len(body) + len(chunk) > 8192:
                            await send(
                                {"type": "http.response.start", "status": 413, "headers": []}
                            )
                            return await send(
                                {"type": "http.response.body", "body": b"Request too large"}
                            )
                        body.extend(chunk)
                        if not message.get("more_body", False):
                            break
            except TimeoutError:
                await send({"type": "http.response.start", "status": 408, "headers": []})
                return await send({"type": "http.response.body", "body": b"Request timeout"})

            original_receive = receive
            delivered = False

            async def buffered_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await original_receive()

            receive = buffered_receive

        async def secured_send(message):
            if message["type"] == "http.response.start":
                policy = (
                    b"default-src 'self'; media-src http: https:; img-src 'self' https:; "
                    b"object-src 'none'; frame-ancestors 'none'; base-uri 'self'"
                )
                if scope["path"] in {"/docs", "/redoc", "/docs/oauth2-redirect"}:
                    policy += (
                        b"; script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
                        b"style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
                        b"img-src 'self' https://fastapi.tiangolo.com data:"
                    )
                message["headers"] = list(message.get("headers", [])) + [
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"content-security-policy", policy),
                ]
            await send(message)

        await self.app(scope, receive, secured_send)
