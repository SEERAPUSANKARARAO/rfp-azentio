"""An optional shared access key for the whole API.

The app has no user accounts. On a public address that would let anyone start drafting on the owner's model keys and read
or delete data, so one shared key can be switched on by setting APP_ACCESS_KEY. It is off when the variable is empty.

It is a plain ASGI middleware, not Starlette's BaseHTTPMiddleware, so the drafting progress stream is not buffered.
EventSource cannot set headers, so the key may also come as ?access_key=.
"""

from __future__ import annotations

import hmac
from urllib.parse import parse_qs

from starlette.responses import JSONResponse

OPEN_PATHS = {"/api/health"}  # tells the app whether a key is needed, so it can ask for one


class AccessKeyMiddleware:
    def __init__(self, app, key: str = ""):
        self.app, self.key = app, key.strip()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.key or scope["method"] == "OPTIONS":
            return await self.app(scope, receive, send)  # a browser's preflight carries no credentials by design
        path = scope["path"]
        if not path.startswith("/api") or path in OPEN_PATHS:
            return await self.app(scope, receive, send)
        given = dict(scope["headers"]).get(b"x-access-key", b"").decode()
        if not given:
            given = (parse_qs(scope.get("query_string", b"").decode()).get("access_key") or [""])[0]
        if hmac.compare_digest(given.encode(), self.key.encode()):
            return await self.app(scope, receive, send)
        await JSONResponse({"detail": "Access key required or wrong."}, status_code=401)(scope, receive, send)
