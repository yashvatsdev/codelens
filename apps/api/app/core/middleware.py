
import typing
from starlette.types import ASGIApp, Receive, Scope, Send, Message

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

        # 1. Check Content-Length upfront
        content_length_header = next(
            (v for k, v in scope["headers"] if k.lower() == b"content-length"), None
        )
        if content_length_header is not None:
            try:
                if int(content_length_header) > self.max_body_size:
                    await self._send_413(send)
                    return
            except ValueError:
                pass  # Ignore malformed Content-Length and let it fail downstream

        # 2. Track chunked or unspecified body size
        total_bytes = 0

        async def receive_wrapper() -> Message:
            nonlocal total_bytes
            message = await receive()
            if message["type"] == "http.request":
                body = message.get("body", b"")
                total_bytes += len(body)
                if total_bytes > self.max_body_size:
                    raise RequestBodyTooLargeError("REQUEST_BODY_TOO_LARGE")
            return message

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

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except RequestBodyTooLargeError:
            await self._send_413(send)

    async def _send_413(self, send: Send) -> None:
        response_body = b'{"detail": "Request body too large.", "code": "REQUEST_BODY_TOO_LARGE"}'
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(response_body)).encode("ascii")),
            (b"x-content-type-options", b"nosniff"),
            (b"x-frame-options", b"DENY"),
            (b"referrer-policy", b"strict-origin-when-cross-origin"),
        ]
        await send({"type": "http.response.start", "status": 413, "headers": headers})
        await send({"type": "http.response.body", "body": response_body})

