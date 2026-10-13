"""Tiny authenticated REST client for the guide's examples (stdlib only)."""

import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener


class DemoApiError(RuntimeError):
    """Expose only the HTTP status so repeatable examples can handle known missing records."""

    def __init__(self, method, path, status):
        self.status = status
        super().__init__(f"{method} {path} returned HTTP {status}; inspect the resource/scopes")


class DemoApi:
    """Read the operator token from its file, keeping it out of argv/output."""

    def __init__(self):
        self.base = os.environ.get("CONTEXTCTL_API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
        parsed = urlsplit(self.base)
        local_http = parsed.scheme == "http" and parsed.hostname in {
            "127.0.0.1",
            "localhost",
            "::1",
        }
        if parsed.scheme != "https" and not local_http:
            raise ValueError("Use HTTPS or direct loopback HTTP for the API base URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
            raise ValueError("The API base URL must be a credential-free origin")
        self.token = Path(os.environ["CONTEXTCTL_API_TOKEN_FILE"]).read_text().strip()
        self.opener = build_opener(ProxyHandler({}))

    def request(self, method, path, body=None):
        encoded = json.dumps(body).encode() if body is not None else None
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("Use a relative API path beginning with one slash")
        request = Request(  # noqa: S310 - scheme/origin validated above
            self.base + path,
            data=encoded,
            method=method,
            headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"},
        )
        try:
            with self.opener.open(request, timeout=120) as response:
                return json.load(response)
        except HTTPError as error:
            # Print only status, never server bodies or credential material.
            raise DemoApiError(method, path, error.code) from None
