"""Bounded native Plus HTTPS route and numeric per-request receipt capture.

The controller owns the temporary CA/proxy. No task key, account identifier,
Authorization header, prompt, response body or reasoning text is logged.
API-key endpoints, purchases, arbitrary CONNECT hosts and compact/retry routes
are denied. A wire receipt is evidence of that route, not a subscription invoice.
"""

from __future__ import annotations

import hashlib
import http.client
import http.server
import json
import re
import selectors
import socket
import socketserver
import ssl
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from scripts.focused_process import FocusedError

from . import native
from .usage import Responses

HOSTS = {"chatgpt.com", "auth.openai.com"}
POSTS = {("chatgpt.com", "/backend-api/codex/responses"), ("auth.openai.com", "/oauth/token")}
GETS = {("chatgpt.com", "/backend-api/codex/models"), ("chatgpt.com", "/backend-api/wham/usage")}
HOP = {
    "connection",
    "proxy-connection",
    "transfer-encoding",
    "content-length",
    "host",
    "accept-encoding",
    "proxy-authorization",
}


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class Policy:
    def __init__(
        self,
        *,
        requests=32,
        request_bytes=262144,
        response_bytes=16777216,
        model_requests=False,
        tools_sha256=None,
        persist=None,
    ):
        for value, maximum in ((requests, 32), (request_bytes, 262144), (response_bytes, 16777216)):
            if type(value) is not int or not 1 <= value <= maximum:
                raise FocusedError("UNREVIEWED_NATIVE_ROUTE_BOUND")
        if type(model_requests) is not bool or (
            model_requests
            and (
                not isinstance(tools_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", tools_sha256)
            )
        ):
            raise FocusedError("SEALED_NATIVE_TOOL_CATALOG_REQUIRED")
        self.maximum = requests
        self.request_bytes = request_bytes
        self.response_bytes = response_bytes
        self.receipts = Responses(native.MODEL)
        self.rejections = 0
        self.lock = threading.Lock()
        self.model_requests = model_requests
        self.tools_sha256 = tools_sha256
        self.persist = persist
        self.received = 0
        self.routes = 0

    def received_bytes(self, size):
        with self.lock:
            self.received += size
            if self.received > self.response_bytes:
                raise FocusedError("NATIVE_AGGREGATE_RESPONSE_BOUND")

    def finish(self, number, status, completed):
        with self.lock:
            self.receipts.finish(number, status, completed)
            if self.persist is not None:
                self.persist(self.receipts.report())

    def request(self, host, method, path, data):
        parsed = urlsplit(path)
        if (
            host not in HOSTS
            or parsed.scheme
            or parsed.netloc
            or parsed.fragment
            or len(data) > self.request_bytes
            or (method == "POST" and (host, parsed.path) not in POSTS)
            or (method == "GET" and (host, parsed.path) not in GETS)
            or method not in {"GET", "POST"}
        ):
            self.rejections += 1
            raise FocusedError("UNDECLARED_NATIVE_EGRESS")
        if parsed.query and (
            parsed.path != "/backend-api/codex/models"
            or set(parse_qs(parsed.query)) != {"client_version"}
            or parse_qs(parsed.query)["client_version"] != [native.VERSION]
        ):
            raise FocusedError("UNDECLARED_NATIVE_QUERY")
        with self.lock:
            self.routes += 1
            if self.routes > 64:
                raise FocusedError("NATIVE_ROUTE_COUNT_BOUND")
        if parsed.path != "/backend-api/codex/responses":
            return data, None
        if not self.model_requests:
            raise FocusedError("MODEL_FREE_PREFLIGHT_ONLY")
        body = json.loads(data)
        if (
            not isinstance(body, dict)
            or set(body)
            != {
                "client_metadata",
                "include",
                "input",
                "model",
                "parallel_tool_calls",
                "prompt_cache_key",
                "reasoning",
                "store",
                "stream",
                "text",
                "tool_choice",
            }
            or body.get("model") != native.MODEL
            or not isinstance(body.get("reasoning"), dict)
            or body["reasoning"] != {"effort": "high", "context": "all_turns"}
            or body.get("stream") is not True
            or body.get("store") is not False
            or "previous_response_id" in body
        ):
            raise FocusedError("UNREVIEWED_NATIVE_MODEL_OR_REQUEST")
        # Preserve the installed CLI's actual Responses Lite request. Ordinary
        # API max_output_tokens/truncation fields are not proven on this route.
        # The byte/request/wall/cgroup bounds are separate from a provider-side
        # token ceiling, which remains unverified and blocks the existing driver.
        inputs = body.get("input")
        if not isinstance(inputs, list):
            raise FocusedError("EXPLICIT_NATIVE_INPUT_REQUIRED")
        prompt = {
            "instructions": body.get("instructions"),
            "input": [x for x in inputs if not isinstance(x, dict) or x.get("type") != "reasoning"],
        }
        tools = [
            x.get("tools")
            for x in inputs
            if isinstance(x, dict) and x.get("type") == "additional_tools"
        ]
        if (
            len(tools) != 1
            or not isinstance(tools[0], list)
            or digest(tools[0]) != self.tools_sha256
        ):
            raise FocusedError("NATIVE_TOOL_CATALOG_CHANGED")
        with self.lock:
            if (
                len(self.receipts.rows) >= self.maximum
                or self.receipts.flags
                or any(not r["terminal"] for r in self.receipts.rows)
            ):
                raise FocusedError("NATIVE_REQUEST_LIMIT_OR_UNRECONCILED_RECEIPT")
            number = self.receipts.start(
                prompt_sha256=digest(prompt),
                tools_sha256=digest(tools[0]),
                effort="high",
                tier="default",
            )
            if self.persist is not None:
                self.persist(self.receipts.report())  # before the possible provider side effect
        return data, number


class Events:
    """Inspect SSE numerics in memory, dropping every text/reasoning delta."""

    def __init__(self):
        self.pending = b""
        self.completed = None
        self.events = 0

    def end(self):
        if self.pending.strip():
            raise FocusedError("TRUNCATED_PROVIDER_EVENT")

    def consume(self, data):
        self.pending += data
        if len(self.pending) > 1048576:
            raise FocusedError("PROVIDER_EVENT_BOUND")
        while b"\n" in self.pending:
            line, self.pending = self.pending.split(b"\n", 1)
            if not line.startswith(b"data:"):
                continue
            content = line[5:].strip()
            if content == b"[DONE]":
                continue
            event = json.loads(content)
            if not isinstance(event, dict):
                raise FocusedError("INVALID_PROVIDER_EVENT")
            if self.completed is not None:
                raise FocusedError("PROVIDER_EVENT_AFTER_TERMINAL")
            self.events += 1
            if self.events > 100000:
                raise FocusedError("PROVIDER_EVENT_COUNT_BOUND")
            if event.get("type") in {
                "response.completed",
                "response.incomplete",
                "response.failed",
            }:
                if self.completed is not None:
                    raise FocusedError("DUPLICATE_PROVIDER_TERMINAL_EVENT")
                raw = event.get("response")
                if isinstance(raw, dict):
                    self.completed = {
                        k: raw.get(k) for k in ("id", "model", "status", "service_tier", "usage")
                    }


def certificates(folder: Path):
    native.private(folder, directory=True)
    now = datetime.now(UTC)
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Owned V8 native route")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=4))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    (folder / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    contexts = {}
    for host in sorted(HOSTS):
        private_key = ec.generate_private_key(ec.SECP256R1())
        cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
            .issuer_name(ca.subject)
            .public_key(private_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(hours=4))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
            .sign(key, hashes.SHA256())
        )
        certfile, keyfile = folder / (host + ".pem"), folder / (host + ".key")
        certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        keyfile.write_bytes(
            private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        keyfile.chmod(0o600)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.set_alpn_protocols(["http/1.1"])
        context.load_cert_chain(certfile, keyfile)
        contexts[host] = context
    return contexts


class Route:
    """One owned temporary local proxy; no global trust or network settings edit."""

    def __init__(self, folder, policy, *, socket_path=None, mcp_origin=None, mcp_ca=None):
        self.policy = policy
        self.contexts = certificates(folder)
        self.mcp_peer = None
        if (mcp_origin is None) != (mcp_ca is None):
            raise FocusedError("OWNED_MCP_TLS_CA_REQUIRED")
        if mcp_origin is not None:
            peer = urlsplit(mcp_origin)
            if (
                peer.scheme != "https"
                or peer.hostname != "127.0.0.1"
                or not peer.port
                or peer.path
                or peer.query
                or peer.fragment
                or peer.username
                or peer.password
            ):
                raise FocusedError("EXACT_OWNED_MCP_ORIGIN_REQUIRED")
            if (
                not isinstance(mcp_ca, Path)
                or mcp_ca.absolute() != mcp_ca.resolve()
                or mcp_ca.is_symlink()
                or not mcp_ca.is_file()
                or mcp_ca.stat().st_nlink != 1
                or mcp_ca.stat().st_size > 65536
            ):
                raise FocusedError("EXACT_OWNED_MCP_CA_FILE_REQUIRED")
            certificate = mcp_ca.read_bytes()
            ssl.create_default_context().load_verify_locations(cadata=certificate.decode("ascii"))
            with (folder / "ca.pem").open("ab") as bundle:
                bundle.write(b"\n" + certificate)
            self.mcp_peer = (peer.hostname, peer.port)
        self.slots = threading.BoundedSemaphore(4)
        self.active = set()
        self.active_lock = threading.Lock()
        self.stopping = threading.Event()
        route = self

        class Inner(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_):
                pass

            def do_GET(self):
                self.forward()

            def do_POST(self):
                self.forward()

            def forward(self):
                number = None
                status = 0
                events = Events()
                upstream = None
                tracked = None
                finished = False
                terminal_data = b""
                try:
                    if self.headers.get("Transfer-Encoding") or self.headers.get(
                        "Content-Encoding"
                    ):
                        raise FocusedError("UNREVIEWED_REQUEST_ENCODING")
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 <= size <= route.policy.request_bytes:
                        raise FocusedError("NATIVE_REQUEST_BODY_BOUND")
                    data = self.rfile.read(size)
                    if len(data) != size:
                        raise FocusedError("INCOMPLETE_NATIVE_REQUEST")
                    host = self.server.connect_host
                    data, number = route.policy.request(host, self.command, self.path, data)
                    upstream = http.client.HTTPSConnection(
                        host, timeout=10, context=ssl.create_default_context()
                    )
                    headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
                    headers.update(
                        {
                            "Host": host,
                            "Content-Length": str(len(data)),
                            "Accept-Encoding": "identity",
                        }
                    )
                    upstream.connect()
                    tracked = upstream.sock
                    if tracked is not None:
                        with route.active_lock:
                            route.active.add(tracked)
                    upstream.request(self.command, self.path, data or None, headers)
                    response = upstream.getresponse()
                    status = response.status
                    self.send_response(status)
                    for k, v in response.getheaders():
                        if k.lower() not in HOP:
                            self.send_header(k, v)
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    while data := response.read1(65536):
                        route.policy.received_bytes(len(data))
                        if number is not None:
                            events.consume(data)
                            if events.completed is not None:
                                # Do not release completion to the CLI until
                                # upstream EOF and durable receipt commitment.
                                # This closes the next-request/EOF race without
                                # buffering any full response or reasoning text.
                                terminal_data += data
                                if len(terminal_data) > 1048576:
                                    raise FocusedError("PROVIDER_TERMINAL_BOUND")
                                continue
                        self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                        self.wfile.flush()
                    if number is not None:
                        events.end()
                        route.policy.finish(number, status, events.completed)
                        finished = True
                    if terminal_data:
                        self.wfile.write(
                            f"{len(terminal_data):x}\r\n".encode() + terminal_data + b"\r\n"
                        )
                    self.wfile.write(b"0\r\n\r\n")
                except (OSError, ValueError, FocusedError, http.client.HTTPException):
                    self.close_connection = True
                    if number is None:
                        route.policy.rejections += 1
                finally:
                    if upstream is not None:
                        upstream.close()
                    if tracked is not None:
                        with route.active_lock:
                            route.active.discard(tracked)
                    if number is not None and not finished:
                        # A terminal object preceding a broken transport does
                        # not establish complete request/usage coverage.
                        route.policy.finish(number, status, None)

        class Connect(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_CONNECT(self):
                tracked = self.connection
                peer_socket = None
                allowed = {h + ":443" for h in HOSTS}
                mcp = ":".join(map(str, route.mcp_peer)) if route.mcp_peer else None
                if (
                    route.stopping.is_set()
                    or self.path not in allowed | ({mcp} if mcp else set())
                    or not route.slots.acquire(blocking=False)
                ):
                    route.policy.rejections += 1
                    self.send_error(403)
                    return
                try:
                    self.connection.settimeout(60)
                    with route.active_lock:
                        route.active.add(self.connection)
                    if self.path == mcp:
                        # Preserve real MCP TLS/certificate validation end to
                        # end. This tunnel can reach only the exact owned peer.
                        with socket.create_connection(route.mcp_peer, timeout=10) as target:
                            peer_socket = target
                            with route.active_lock:
                                route.active.add(target)
                            self.send_response(200)
                            self.end_headers()
                            with selectors.DefaultSelector() as ready:
                                ready.register(self.connection, selectors.EVENT_READ, target)
                                ready.register(target, selectors.EVENT_READ, self.connection)
                                total = 0
                                while events := ready.select(60):
                                    for event, _ in events:
                                        data = event.fileobj.recv(65536)
                                        if not data:
                                            return
                                        total += len(data)
                                        if total > 16777216:
                                            return
                                        event.data.sendall(data)
                        return
                    host = self.path[:-4]
                    self.send_response(200)
                    self.end_headers()
                    with route.contexts[host].wrap_socket(
                        self.connection, server_side=True
                    ) as connection:
                        with route.active_lock:
                            route.active.discard(tracked)
                            tracked = connection
                            route.active.add(tracked)

                        # Per-connection immutable host, never a global routing slot.
                        class State:
                            connect_host = host

                        Inner(connection, self.client_address, State())
                except (OSError, ValueError):
                    pass
                finally:
                    with route.active_lock:
                        route.active.discard(tracked)
                        if peer_socket is not None:
                            route.active.discard(peer_socket)
                    self.close_connection = True
                    route.slots.release()

        if socket_path is None:
            self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Connect)
            self.url = "http://127.0.0.1:" + str(self.server.server_port)
        else:

            class UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
                daemon_threads = False

            self.server = UnixServer(str(socket_path), Connect)
            socket_path.chmod(0o600)
            self.url = "unix:" + str(socket_path)
        self.server.daemon_threads = False
        original_get_request = self.server.get_request

        def bounded_request():
            connection, address = original_get_request()
            connection.settimeout(5)
            return connection, address

        self.server.get_request = bounded_request
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        self.stopping.set()
        with self.active_lock:
            connections = list(self.active)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if self.thread.is_alive():
            self.server.shutdown()
        self.server.server_close()
        if self.thread.ident is not None:
            self.thread.join(timeout=5)
