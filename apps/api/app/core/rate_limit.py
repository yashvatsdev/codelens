"""Application-level API rate limiting for CodeLens.

Architecture
------------
Implemented as a Starlette middleware using the ``limits`` library
(the same backend used by ``slowapi``).

Middleware-based limiting is preferred here because it requires no changes
to individual route function signatures: many CodeLens routes do not accept
a ``Request`` parameter, so decorator-based approaches (slowapi) would have
required adding ``Request`` to every affected route.

Current storage
---------------
``MemoryStorage`` -- process-local, in-memory.

LIMITATION: counters are not shared across multiple worker processes or
Render replicas.  If CodeLens is scaled horizontally, the effective limit
seen by a client is ``configured_limit x num_processes``.

Upgrade path: replace ``MemoryStorage()`` with
``RedisStorage("redis://...")`` and the rest of the code is unchanged.

How limits are applied
----------------------
``ROUTE_LIMITS`` is a list of ``(HTTP_method, path_glob, limit_string)``
tuples evaluated top-to-bottom.  The first matching rule wins.  If no rule
matches the global ``DEFAULT_LIMIT`` is used. Specific rules additionally share
that client-wide budget, except health probes with their own limits. Counters
use canonical operation patterns, not individual resource IDs or raw paths.

Path globs use ``fnmatch`` syntax.  ``*`` matches any characters within a
single path segment (or across segments -- fnmatch treats the whole string).

Client identification
---------------------
Authenticated requests: the session signature, expiry and subject are verified
before identity is used. Public auth flows always use the remote IP.

Unauthenticated requests: ``request.client.host`` (remote IP) is used.

``X-Forwarded-For`` is intentionally NOT trusted: the deployment proxy
configuration has not been audited for header sanitisation.

How to change limits
--------------------
Edit ``ROUTE_LIMITS`` or ``DEFAULT_LIMIT`` in this module.  No route-file
changes are required.

Test isolation
--------------
Call ``reset_limiter()`` between tests to flush all in-memory counters.
This prevents counter leakage between test cases.

Limitations
-----------
* Process-local only (see above).
* Path matching uses fnmatch -- complex regex patterns are not supported.
* ``Retry-After`` value is approximate.
"""

from __future__ import annotations

import fnmatch
import logging
import time
from typing import Callable

import jwt
from limits import parse as parse_limit
from limits.storage import MemoryStorage
from limits.strategies import FixedWindowRateLimiter
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import settings
from app.core.security import decode_session_user_id

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Storage container -- replaced atomically by reset_limiter() during tests.
# dispatch() accesses via module globals (not captured closures) so that
# reset_limiter() takes effect immediately.
# ---------------------------------------------------------------------------
class _State:
    storage: MemoryStorage
    limiter: FixedWindowRateLimiter

    def __init__(self):
        self.storage = MemoryStorage()
        self.limiter = FixedWindowRateLimiter(self.storage)


_state = _State()


def reset_limiter() -> None:
    """Flush all rate-limit counters.

    Call this in test setUp / tearDown to prevent counter leakage between
    test cases.  Replaces the shared state object so the middleware picks up
    fresh counters on the very next request.
    """
    global _state
    _state = _State()


# ---------------------------------------------------------------------------
# Global default -- applies when no specific rule matches.
# ---------------------------------------------------------------------------
DEFAULT_LIMIT = "60/minute"

# ---------------------------------------------------------------------------
# Per-route limit table.
# Format: (HTTP_method | "*", path_glob, limit_string)
# Evaluated top-to-bottom; first match wins.
# ---------------------------------------------------------------------------
ROUTE_LIMITS: list[tuple[str, str, str]] = [
    # Auth
    ("POST", "/auth/login",          "10/minute"),
    ("POST", "/auth/signup",         "5/minute"),
    ("GET",  "/auth/google/login",   "10/minute"),
    # Google callback is intentionally NOT rate-limited strictly: the browser
    # redirect from Google arrives here and the client cannot control the
    # timing.  The global default (60/min) still applies.

    # Repository metadata / connect  (cause GitHub API calls)
    ("POST", "/repositories/github/metadata",  "30/minute"),
    ("GET",  "/repositories/github/metadata",  "30/minute"),
    ("POST", "/repositories/github",           "10/minute"),
    ("POST", "/repositories",                  "10/minute"),

    # Scan  (expensive background job)
    ("POST", "/repositories/*/scan",           "5/hour"),
    ("POST", "/repositories/*/ingest",         "5/hour"),
    ("POST", "/repositories/*/analyze",        "5/hour"),

    # AI endpoints
    ("POST", "/repositories/*/ask",                        "20/hour"),
    ("POST", "/repositories/*/findings/*/explain",         "20/hour"),
    ("POST", "/repositories/*/findings/*/fix",             "10/hour"),
    ("POST", "/repositories/*/findings/*/test",            "10/hour"),
    ("POST", "/repositories/*/pull-requests/*/ai-review",  "5/hour"),
    ("POST", "/repositories/*/findings/*/apply-fix",       "10/hour"),
    ("POST", "/repositories/*/findings/*/create-pr",       "10/hour"),
    ("POST", "/repositories/*/pull-requests/*/findings/fix", "10/hour"),
    ("POST", "/repositories/*/pull-requests/*/comment",    "5/hour"),

    # Health  (raised limit so Render probes are never blocked)
    ("GET",  "/health",    "120/minute"),
    ("GET",  "/db/health", "30/minute"),
]


def _match_path(method: str, path: str) -> str:
    """Return the limit string for the first matching rule, or DEFAULT_LIMIT."""
    return _match_rule(method, path)[1]


def _canonical_path(path: str) -> str:
    return "/" + "/".join(segment for segment in path.split("/") if segment)


def _match_rule(method: str, path: str) -> tuple[str, str]:
    path = _canonical_path(path)
    for rule_method, rule_pattern, limit_str in ROUTE_LIMITS:
        if rule_method not in ("*", method):
            continue
        if fnmatch.fnmatchcase(path, rule_pattern):
            # Alternate repository-connection routes share one budget.
            bucket = "/repositories/connect" if rule_pattern in ("/repositories", "/repositories/github") and method == "POST" else rule_pattern
            return bucket, limit_str
    return "default", DEFAULT_LIMIT


def _get_client_key(request: Request) -> str:
    """Return a stable client identifier for rate-limit namespacing.

    Prefers the authenticated user-id (from the JWT cookie) so that a single
    user cannot bypass limits by rotating IP addresses.

    Falls back to ``request.client.host`` for unauthenticated requests.
    """
    cookie = request.cookies.get(settings.auth_cookie_name)
    if cookie and not _canonical_path(request.url.path).startswith("/auth/"):
        try:
            return f"user:{decode_session_user_id(cookie)}"
        except (jwt.InvalidTokenError, ValueError, TypeError):
            pass  # malformed token -- fall back to IP
    host = (request.client.host if request.client else None) or "unknown"
    return f"ip:{host}"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Starlette ASGI middleware that enforces per-client rate limits.

    Accesses ``_state`` via the module global on every request so that
    ``reset_limiter()`` takes effect immediately without restarting the app.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = _canonical_path(request.url.path)
        method = request.method

        bucket, limit_str = _match_rule(method, path)
        item = parse_limit(limit_str)
        key = _get_client_key(request)
        # A rule is one operation budget across all resource IDs and methods.
        namespace = f"rl:{bucket}"

        # Access via module global so reset_limiter() is respected.
        allowed = _state.limiter.hit(item, namespace, key)
        if allowed and bucket != "default" and path not in ("/health", "/db/health"):
            global_item = parse_limit(DEFAULT_LIMIT)
            allowed = _state.limiter.hit(global_item, "rl:default", key)
            if not allowed:
                item, namespace = global_item, "rl:default"

        if not allowed:
            try:
                stats = _state.limiter.get_window_stats(item, namespace, key)
                retry_after = max(1, int(stats.reset_time - time.time()))
            except Exception:
                retry_after = 60

            logger.warning(
                "Rate limit exceeded: %s %s client=%s limit=%s",
                method, path, key, limit_str,
            )
            return JSONResponse(
                status_code=429,
                content={
                    "detail": (
                        "Too many requests. "
                        "Please slow down and try again later."
                    ),
                    "code": "RATE_LIMIT_EXCEEDED",
                },
                headers={"Retry-After": str(retry_after)},
            )

        return await call_next(request)
