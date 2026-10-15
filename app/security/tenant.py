"""Explicit trusted organization context for operator work and future auth adapters.

This is a context carrier, not an authorization decision. No request header,
project, email domain, or absent identifier can establish an organization.
"""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class TenantContextRequired(ValueError):
    """A tenant operation was attempted without trusted organization context."""


@dataclass(frozen=True, slots=True)
class TenantContext:
    """One immutable organization, selected by a verified server/operator boundary."""

    org_id: str
    cursor_secret: bytes | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.org_id, str) or _IDENTIFIER.fullmatch(self.org_id) is None:
            raise ValueError("an explicit valid organization identifier is required")


_current: ContextVar[TenantContext | None] = ContextVar("tenant_context", default=None)


def current_tenant() -> TenantContext | None:
    """Return explicit context; never manufacture a default organization."""
    return _current.get()


def require_tenant() -> TenantContext:
    context = current_tenant()
    if context is None:
        raise TenantContextRequired("trusted organization context is required")
    return context


def establish_tenant(context: TenantContext) -> None:
    """Bind verified server identity; request selectors must never call this."""
    _current.set(context)


@contextmanager
def tenant_request_boundary() -> Iterator[None]:
    token = _current.set(None)
    try:
        yield
    finally:
        _current.reset(token)


@contextmanager
def tenant_scope(context: TenantContext) -> Iterator[TenantContext]:
    """Bind explicit trusted context and restore it, including on task failure."""
    token = _current.set(context)
    try:
        yield context
    finally:
        _current.reset(token)
