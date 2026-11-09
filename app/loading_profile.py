"""Opt-in administrator timing of SQL execution and API response preparation."""

import secrets
from contextvars import ContextVar
from time import perf_counter

from sqlalchemy import event

from app.config import settings

_trace = ContextVar("loading_profile", default=None)
PATHS = {"/api/stats/top", "/api/stations/now-playing", "/api/admin/dashboard"}


def instrument(engine):
    @event.listens_for(engine, "before_cursor_execute")
    def before(conn, cursor, statement, parameters, context, many):
        trace = _trace.get()
        if trace is not None:
            context.loading_started = perf_counter()

    @event.listens_for(engine, "after_cursor_execute")
    def after(conn, cursor, statement, parameters, context, many):
        trace = _trace.get()
        if trace is not None and hasattr(context, "loading_started"):
            elapsed = perf_counter() - context.loading_started
            trace["sql"] += elapsed
            trace["count"] += 1
            trace["max"] = max(trace["max"], elapsed)


class LoadingProfileMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        headers = dict(scope.get("headers", []))
        expected = settings.admin_token.get_secret_value()
        enabled = (
            scope["type"] == "http"
            and scope.get("path") in PATHS
            and headers.get(b"x-loading-profile") == b"1"
            and expected
            and secrets.compare_digest(
                headers.get(b"authorization", b""), f"Bearer {expected}".encode()
            )
        )
        if not enabled:
            return await self.app(scope, receive, send)
        started = perf_counter()
        trace = {"sql": 0.0, "count": 0, "max": 0.0}
        token = _trace.set(trace)

        async def timed_send(message):
            if message["type"] == "http.response.start":
                value = (
                    f"api;dur={(perf_counter() - started) * 1000:.2f}, "
                    f"sql_execute;dur={trace['sql'] * 1000:.2f}, "
                    f"sql_max;dur={trace['max'] * 1000:.2f}, "
                    f'sql_count;desc="{trace["count"]}"'
                )
                message = {
                    **message,
                    "headers": [*message.get("headers", []), (b"server-timing", value.encode())],
                }
            await send(message)

        try:
            await self.app(scope, receive, timed_send)
        finally:
            _trace.reset(token)
