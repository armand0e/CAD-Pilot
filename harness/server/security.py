from starlette.responses import JSONResponse
from .protocol import same_origin


class LocalWorkspaceSecurity:
    """CSRF/CSWSH protection for the loopback-only, single-user workspace."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        host = headers.get("host", "")
        origin = headers.get("origin")
        valid = same_origin(origin, host)
        if scope["type"] == "websocket" and not valid:
            return await send({"type": "websocket.close", "code": 4403})
        if scope["type"] == "http" and scope.get("method") not in {"GET", "HEAD", "OPTIONS"}:
            if not valid or headers.get("sec-fetch-site") == "cross-site":
                return await JSONResponse({"detail": "Cross-origin control requests are not allowed"}, status_code=403)(scope, receive, send)
            # Control requests are tiny; only the attachment upload route may carry a file (40 MiB + multipart framing).
            limit = 41 * 1024 * 1024 if scope.get("path", "").endswith("/attachments") else 32768
            try:
                if int(headers.get("content-length", "0")) > limit:
                    return await JSONResponse({"detail": "Request too large"}, status_code=413)(scope, receive, send)
            except ValueError:
                return await JSONResponse({"detail": "Invalid content length"}, status_code=400)(scope, receive, send)
            # Also bound chunked bodies; Content-Length is not a trustworthy size limit.
            messages, size = [], 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                size += len(message.get("body", b""))
                if size > limit:
                    return await JSONResponse({"detail": "Request too large"}, status_code=413)(scope, receive, send)
                messages.append(message)
                if not message.get("more_body", False):
                    break
            original_receive = receive
            async def replay_body():
                return messages.pop(0) if messages else await original_receive()
            receive = replay_body

        async def secured(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"same-origin"),
                    (b"cross-origin-resource-policy", b"same-origin"),
                    (b"cache-control", b"no-store"),
                    (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")]
            await send(message)
        await self.app(scope, receive, secured)
