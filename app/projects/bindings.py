"""Deterministic, credential-safe project-binding value normalization.

The resolver deliberately compares only explicit identifiers.  In particular,
it never reads a client workspace or turns a repository name into a semantic
search query.  Git's common SSH and HTTPS spellings share one canonical
repository locator so a binding created from either form is useful to both.
"""

import re
from urllib.parse import urlsplit

_MAX_VALUE_LENGTH = 2_048
_SCP_REMOTE = re.compile(r"^(?P<user>[A-Za-z0-9._-]+)@(?P<host>[A-Za-z0-9.-]+):(?P<path>[^\s]+)$")
_HOST = re.compile(r"^[A-Za-z0-9.-]+$")


class ProjectBindingValueError(ValueError):
    """A supplied binding is not a safe explicit project identifier."""


def normalize_binding_value(binding_type: str, value: str) -> str:
    """Return the exact persisted value used for deterministic comparison.

    Repository names and workspace aliases are deliberately only trimmed: case
    and punctuation can be meaningful on a source-control server or in a
    workspace convention.  Repository locators normalize host casing, optional
    ``.git``, and their standard SSH/HTTPS spellings; no network lookup occurs.
    """
    normalized = _clean(value)
    if binding_type in {"git_remote", "repository_url"}:
        return normalize_repository_locator(normalized)
    if binding_type in {"repository_name", "workspace_alias"}:
        return normalized
    raise ProjectBindingValueError("unsupported project binding type")


def normalize_repository_locator(value: str) -> str:
    """Canonicalize a standard SSH or HTTPS repository remote.

    The result is intentionally a locator (``host/owner/repository``), not a
    URL.  It contains no scheme, user name, password, query string, or fragment
    and therefore cannot retain a credential from an accidentally pasted URL.
    Non-default ports remain part of identity; default SSH/HTTPS ports do not.
    """
    match = _SCP_REMOTE.fullmatch(value)
    if match is not None:
        return _locator(match.group("host"), None, match.group("path"))
    canonical = _canonical_locator(value)
    if canonical is not None:
        return canonical

    parsed = urlsplit(value)
    scheme = parsed.scheme.lower()
    if scheme not in {"https", "ssh"}:
        raise ProjectBindingValueError("repository remotes must use SSH or HTTPS")
    if parsed.query or parsed.fragment:
        raise ProjectBindingValueError("repository remotes cannot contain a query or fragment")
    if parsed.hostname is None:
        raise ProjectBindingValueError("repository remote must name a host")
    if parsed.password is not None:
        raise ProjectBindingValueError("repository remotes cannot contain credentials")
    if parsed.username is not None and not (scheme == "ssh" and parsed.username == "git"):
        raise ProjectBindingValueError("repository remotes cannot contain credentials")
    try:
        port = parsed.port
    except ValueError as error:
        raise ProjectBindingValueError("repository remote has an invalid port") from error
    if port == 443 and scheme == "https":
        port = None
    if port == 22 and scheme == "ssh":
        port = None
    return _locator(parsed.hostname, port, parsed.path)


def _canonical_locator(value: str) -> str | None:
    """Accept a previously persisted canonical locator during model revalidation."""
    if "://" in value or "@" in value or "/" not in value:
        return None
    host_part, _, path = value.partition("/")
    host = host_part
    port: int | None = None
    if host_part.startswith("["):
        closing = host_part.find("]")
        if closing == -1:
            return None
        host = host_part[1:closing]
        remainder = host_part[closing + 1 :]
        if remainder:
            if not remainder.startswith(":") or not remainder[1:].isdigit():
                return None
            port = int(remainder[1:])
    elif ":" in host_part:
        host, port_text = host_part.rsplit(":", 1)
        if not port_text.isdigit():
            return None
        port = int(port_text)
    if not host or (":" not in host and _HOST.fullmatch(host) is None):
        return None
    if port is not None and not 1 <= port <= 65_535:
        return None
    return _locator(host, port, path)


def _clean(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ProjectBindingValueError("project binding value must not be empty")
    if len(normalized) > _MAX_VALUE_LENGTH:
        raise ProjectBindingValueError("project binding value is too long")
    if any(character.isspace() or ord(character) < 32 for character in normalized):
        raise ProjectBindingValueError("project binding value cannot contain whitespace")
    return normalized


def _locator(host: str, port: int | None, raw_path: str) -> str:
    canonical_host = host.lower().rstrip(".")
    if not canonical_host:
        raise ProjectBindingValueError("repository remote must name a host")
    if ":" in canonical_host:
        # IPv6 repositories are valid but need bracket notation to remain
        # unambiguous beside a non-default port.
        canonical_host = f"[{canonical_host}]"
    host_part = canonical_host if port is None else f"{canonical_host}:{port}"

    path = raw_path.strip("/")
    if not path:
        raise ProjectBindingValueError("repository remote must name a repository")
    segments = path.split("/")
    if any(
        not segment
        or segment in {".", ".."}
        or any(character in segment for character in {"%", "?", "#"})
        for segment in segments
    ):
        raise ProjectBindingValueError("repository remote has an unsafe path")
    if any(
        any(character.isspace() or ord(character) < 32 for character in segment)
        for segment in segments
    ):
        raise ProjectBindingValueError("repository remote has an unsafe path")
    if segments[-1].lower().endswith(".git"):
        segments[-1] = segments[-1][:-4]
    if not segments[-1]:
        raise ProjectBindingValueError("repository remote must name a repository")
    return f"{host_part}/{'/'.join(segments)}"
