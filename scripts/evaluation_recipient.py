"""Recipient-driven HTTPS client. Sessions and one-time secrets stay in memory."""

import getpass
import http.cookiejar
import json
import os
import re
import ssl
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts.focused_process import FocusedError


class Browser:
    def __init__(self, origin, ca):
        if not re.fullmatch(r"https://127\.0\.0\.1:[0-9]{1,5}", origin):
            raise FocusedError("recipient requires the exact loopback HTTPS evaluation origin")
        self.origin = origin
        self.csrf = ""
        self.context_id = ""
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=ca)),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        )
        self.request("GET", "/api/v6/auth/csrf")

    def request(self, method, path, data=None):
        if not path.startswith("/api/") or path.startswith("//"):
            raise FocusedError("recipient client accepts only relative API paths")
        request = urllib.request.Request(  # noqa: S310 -- validated evaluation origin
            self.origin + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={
                "Content-Type": "application/json",
                "Origin": self.origin,
                "X-CSRF-Token": self.csrf,
                **({"X-Session-Context": self.context_id} if self.context_id else {}),
            },
            method=method,
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                body = response.read(2_000_000)
        except urllib.error.HTTPError as error:
            raise FocusedError(
                f"recipient API refused {method} {path}: HTTP {error.code}"
            ) from None
        result = json.loads(body) if body else {}
        if "csrf_token" in result:
            self.csrf = result["csrf_token"]
        if "policy_context" in result:
            self.context_id = result["policy_context"]["context_id"]
        return result

    def login(self, org, email, password, code=""):
        challenge = self.request(
            "POST",
            "/api/v6/auth/local/login",
            {"org_id": org, "email": email, "password": password},
        )
        return self.request(
            "POST", "/api/v6/auth/local/complete", {"token": challenge["challenge"], "code": code}
        )


def handoff(state, recipient, purpose):
    # Exact recipient/purpose only. Nothing prints or persists a challenge token.
    messages = []
    for path in (state / "handoff").glob("*.json"):
        if path.is_symlink() or path.stat().st_mode & 0o077:
            raise FocusedError("unsafe local recipient handoff")
        message = json.loads(path.read_text())
        if message["recipient"] == recipient and message["purpose"] == purpose:
            messages.append((path.stat().st_mtime_ns, message))
    if not messages:
        raise FocusedError("recipient handoff missing; inspect the private local sink")
    return max(messages, key=lambda pair: pair[0])[1]["token"]


def enroll(evaluation, recipient):
    if not os.isatty(0) or not os.isatty(1):
        raise FocusedError("enrollment needs the recipient's interactive terminal")
    email = recipient + "@example.test"
    browser = Browser(evaluation.data["origin"], evaluation.state / "tls/ca.crt")
    password = getpass.getpass("Choose a unique password for fictional " + email + ": ")
    if password != getpass.getpass("Repeat password: "):
        raise FocusedError("password confirmation mismatch")
    browser.request(
        "POST",
        "/api/v6/auth/invitations/accept",
        {"token": handoff(evaluation.state, email, "invite"), "password": password},
    )
    browser.request(
        "POST", "/api/v6/auth/local/verify", {"token": handoff(evaluation.state, email, "verify")}
    )
    browser.login("eval-" + evaluation.instance, email, password)
    factor = browser.request("POST", "/api/v6/auth/mfa/enroll", {})
    print("Add this one-time seed to this recipient's authenticator:", factor["seed"])
    confirmed = browser.request(
        "POST",
        "/api/v6/auth/mfa/confirm",
        {"code": getpass.getpass("Current authenticator code: ")},
    )
    print("Save these one-time recovery codes privately:", " ".join(confirmed["recovery_codes"]))
    browser.request("POST", "/api/v6/auth/logout", {})
    print("Recipient verified and MFA confirmed. Resume start with this exact instance.")


def session(evaluation, recipient):
    if not os.isatty(0) or not os.isatty(1):
        raise FocusedError("recipient authority needs an interactive terminal")
    browser = Browser(evaluation.data["origin"], evaluation.state / "tls/ca.crt")
    browser.login(
        "eval-" + evaluation.instance,
        recipient + "@example.test",
        getpass.getpass("Recipient password: "),
        getpass.getpass("Fresh authenticator code: "),
    )
    return browser


def grant(action, kind, identifier):
    return {"action": action, "resource_kind": kind, "selector": "include", "ids": [identifier]}


def approve_client(evaluation, browser):
    """Owner explicitly approves a distinct worker; no worker self-approval."""
    org = "eval-" + evaluation.instance
    root = "/api/v6/organizations/" + org
    owner = browser.request("GET", "/api/v6/me")["user_id"]
    worker = evaluation.data["members"]["worker"]["user_id"]
    if owner != evaluation.data["members"]["owner"]["user_id"] or owner == worker:
        raise FocusedError("independent verified owner approval required")
    projects = browser.request("GET", root + "/knowledge/projects")["items"]
    project = "evaluation-notes"
    if not any(row["project_id"] == project for row in projects):
        browser.request(
            "POST",
            root + "/knowledge/projects",
            {
                "project_id": project,
                "name": "Fictional evaluation Notes",
                "description": "Disposable sample only",
                "repository": "https://example.test/fictional-notes",
                "environment": "evaluation",
            },
        )
    rows = browser.request("GET", root + "/assignments?subject_id=" + owner)["assignments"]
    parent = next(
        row
        for row in rows
        if row["parent_id"] is None and row["remaining_depth"] > 0 and row["status"] == "active"
    )
    domain = (
        [grant("projects:read", "project_directory", project)]
        + [grant(a, "project", project) for a in ("context:read", "context:write", "search:read")]
        + [grant("agent:self", "organization_knowledge", org)]
    )
    worker_rows = browser.request("GET", root + "/assignments?subject_id=" + worker)["assignments"]
    expiry = min(
        datetime.fromisoformat(evaluation.data["expires_at"]),
        datetime.now(UTC) + timedelta(hours=2),
    ).isoformat()
    if not any(
        row["parent_id"] == parent["_id"]
        and row["parent_generation"] == parent["generation"]
        and row["status"] == "active"
        for row in worker_rows
    ):
        browser.request(
            "POST",
            root + "/assignments",
            {
                "subject_id": worker,
                "parent_id": parent["_id"],
                "expected_parent_generation": parent["generation"],
                "role": "contributor",
                "tuples": domain
                + [
                    grant(a, "member", worker)
                    for a in ("credentials:issue:own", "credentials:revoke:own", "clients:read:own")
                ],
                "expires_at": expiry,
            },
        )
    clients = browser.request("GET", root + "/managed-clients")["items"]
    if not any(row["client_id"] == "evaluation-worker" for row in clients):
        browser.request(
            "POST",
            root + "/managed-clients",
            {
                "client_id": "evaluation-worker",
                "name": "Fictional worker client",
                "subject_id": worker,
                "parent_id": parent["_id"],
                "expected_parent_generation": parent["generation"],
                "tuples": domain,
                "expires_at": expiry,
            },
        )
    return {"client_id": "evaluation-worker", "independent_approval": True}


def issue(evaluation, browser, destination):
    # Reserve a fresh recipient-chosen file BEFORE the irreversible one-time
    # delivery. A crash leaves a visible empty file, never an automatic reissue.
    destination = Path(destination).absolute()
    if (
        destination.resolve() != destination
        or not destination.parent.is_dir()
        or destination.parent.stat().st_mode & 0o077
    ):
        raise FocusedError("choose a new file inside a private, non-symlinked recipient directory")
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        org = "eval-" + evaluation.instance
        root = "/api/v6/organizations/" + org + "/managed-clients/evaluation-worker"
        inspected = browser.request("GET", root)
        if any(row["status"] == "active" for row in inspected["credentials"]):
            raise FocusedError("credential already issued; use explicit console revoke/replacement")
        client = inspected["client"]
        receipt = browser.request(
            "POST",
            root + "/credentials",
            {
                "issuance_id": "recipient-" + __import__("secrets").token_hex(16),
                "expected_client_revision": client["revision"],
                "tuples": client["tuples"],
                "expires_at": client["expires_at"],
            },
        )
        if not receipt.get("delivered") or not receipt.get("token"):
            raise FocusedError("one-time delivery unavailable; revoke/replace explicitly")
        with os.fdopen(descriptor, "w") as output:
            descriptor = -1
            output.write(receipt.pop("token") + "\n")
            output.flush()
            os.fsync(output.fileno())
        return {"key_id": receipt["key_id"], "saved_once": True}
    finally:
        if descriptor != -1:
            os.close(descriptor)
