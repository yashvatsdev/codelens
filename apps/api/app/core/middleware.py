
import typing
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send, Message
from app.core.config import settings

MAX_REQUEST_BODY_BYTES = 1_000_000  # 1 MB

class RequestBodyTooLargeError(Exception):
    pass

class SecurityAndBodyLimitMiddleware:
    """
    Combined middleware for setting security headers and enforcing request body limits.
    Done in a single ASGI middleware to minimize overhead.
    """
    def __init__(self, app: ASGIApp, max_body_size: int = MAX_REQUEST_BODY_BYTES) -> None:
        self.app = app
        self.max_body_size = max_body_size

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        is_secure = scope.get("scheme") == "https"
        path = scope.get("path", "")

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                
                # Security Headers
                headers.append((b"x-content-type-options", b"nosniff"))
                headers.append((b"x-frame-options", b"DENY"))
                headers.append((b"referrer-policy", b"strict-origin-when-cross-origin"))
                headers.append((b"permissions-policy", b"camera=(), microphone=(), geolocation=()"))
                
                # No cache for auth
                if path.startswith("/auth/"):
                    headers.append((b"cache-control", b"no-store"))
                    
                # HSTS in production/https only
                if is_secure:
                    headers.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
                    
            await send(message)

        headers = {k.lower(): v for k, v in scope["headers"]}
        if scope.get("method") not in ("GET", "HEAD", "OPTIONS"):
            origin = headers.get(b"origin")
            if ((origin is not None and origin.decode("latin-1") != settings.frontend_url)
                    or (origin is None and headers.get(b"sec-fetch-site") == b"cross-site")):
                await JSONResponse(status_code=403, content={"detail": "Untrusted request origin"})(scope, receive, send_wrapper)
                return

        # Reject before parsing: parsers can catch exceptions from receive and
        # turn an overflow into 400, or start a response before the body is read.
        length = headers.get(b"content-length")
        if length is not None:
            try:
                if int(length) > self.max_body_size:
                    await self._send_413(send_wrapper)
                    return
            except ValueError:
                pass
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > self.max_body_size:
                await self._send_413(send_wrapper)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        delivered = False

        async def receive_wrapper() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, receive_wrapper, send_wrapper)

    async def _send_413(self, send: Send) -> None:
        response_body = b'{"detail": "Request body too large.", "code": "REQUEST_BODY_TOO_LARGE"}'
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(response_body)).encode("ascii")),
        ]
        await send({"type": "http.response.start", "status": 413, "headers": headers})
        await send({"type": "http.response.body", "body": response_body})

