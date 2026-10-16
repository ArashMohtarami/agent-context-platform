"""Bounded declarative assignments. Missing selectors mean NONE, never ALL."""

from datetime import datetime
from hashlib import sha256
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.identity.models import RootTuple
from app.models.common import Identifier
from app.security.policy.catalog import HUMAN_ROLES


class Selector(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["none", "include", "all_in_org"] = "none"
    ids: tuple[Identifier, ...] = Field(default=(), max_length=50)

    @model_validator(mode="after")
    def valid(self) -> Self:
        if (self.mode == "include") != bool(self.ids) or len(set(self.ids)) != len(self.ids):
            raise ValueError("include requires unique IDs; other selectors have no IDs")
        return self

    def contains(self, identifier: str) -> bool:
        return self.mode == "all_in_org" or (self.mode == "include" and identifier in self.ids)

    def within(self, parent: "Selector") -> bool:
        return (
            self.mode == "none"
            or parent.mode == "all_in_org"
            or (
                self.mode == "include"
                and parent.mode == "include"
                and set(self.ids) <= set(parent.ids)
            )
        )


class Grant(RootTuple):
    selector: Literal["none", "include", "all_in_org"] = "none"

    def selection(self) -> Selector:
        return Selector(mode=self.selector, ids=tuple(self.ids))


def covered(grant: Grant, ceiling: tuple[Grant, ...]) -> bool:
    """Union ONLY selectors with the same action AND resource kind."""
    matches = [
        g for g in ceiling if (g.action, g.resource_kind) == (grant.action, grant.resource_kind)
    ]
    if grant.selector == "none":
        return True
    if any(g.selector == "all_in_org" for g in matches):
        return True
    ids = {identifier for g in matches if g.selector == "include" for identifier in g.ids}
    return grant.selector == "include" and set(grant.ids) <= ids


class Issue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_id: Identifier
    role: str = "contributor"
    parent_id: Identifier
    expected_parent_generation: int = Field(ge=1)
    tuples: tuple[Grant, ...] = Field(default=(), max_length=100)
    ceiling: tuple[Grant, ...] = Field(default=(), max_length=100)
    targets: Selector = Field(default_factory=Selector)
    allowed_roles: tuple[str, ...] = Field(default=(), max_length=8)
    remaining_depth: int = Field(default=0, ge=0, le=2)
    expires_at: datetime

    @model_validator(mode="after")
    def valid(self) -> Self:
        if self.role not in HUMAN_ROLES or any(r not in HUMAN_ROLES for r in self.allowed_roles):
            raise ValueError("unknown membership role")
        if "org_owner" in self.allowed_roles or len(set(self.allowed_roles)) != len(
            self.allowed_roles
        ):
            raise ValueError("ownership cannot be delegated")
        if self.expires_at.tzinfo is None:
            raise ValueError("expiry requires a timezone")
        return self


def ceiling_hash(grants: tuple[Grant, ...]) -> str:
    return sha256("\n".join(sorted(g.model_dump_json() for g in grants)).encode()).hexdigest()
