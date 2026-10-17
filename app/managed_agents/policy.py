"""Tuple-preserving intersection and bounded legacy metadata projections."""

from dataclasses import dataclass
from typing import Literal

from app.models.agent import MAX_AGENT_PROJECTS
from app.models.api_key import MAX_PROFILES, MAX_PROJECTS
from app.security.policy.evaluation import PolicyDenied
from app.security.policy.models import Grant


@dataclass(frozen=True)
class LegacyProjection:
    mode: Literal["none", "include", "all_in_org"]
    ids: tuple[str, ...] = ()


def _included(action: str, kind: str, identifiers: list[str]) -> list[Grant]:
    # One input tuple permits 50 IDs; a union may legitimately contain 100.
    # Keep valid bounded tuples instead of widening or dropping that union.
    return [
        Grant.model_validate(
            {
                "action": action,
                "resource_kind": kind,
                "selector": "include",
                "ids": identifiers[offset : offset + 50],
            }
        )
        for offset in range(0, len(identifiers), 50)
    ]


def intersect(*layers: tuple[Grant, ...]) -> tuple[Grant, ...]:
    if not layers:
        return ()
    result = layers[0]
    for layer in layers[1:]:
        pairs = {(g.action, g.resource_kind) for g in result} & {
            (g.action, g.resource_kind) for g in layer
        }
        output: list[Grant] = []
        for action, kind in sorted(pairs):
            left = [g for g in result if (g.action, g.resource_kind) == (action, kind)]
            right = [g for g in layer if (g.action, g.resource_kind) == (action, kind)]

            def selection(rows: list[Grant]) -> set[str] | None:
                return (
                    None
                    if any(g.selector == "all_in_org" for g in rows)
                    else {i for g in rows if g.selector == "include" for i in g.ids}
                )

            a, b = selection(left), selection(right)
            ids = b if a is None else a if b is None else a & b
            if ids is None:
                output.append(
                    Grant.model_validate(
                        {"action": action, "resource_kind": kind, "selector": "all_in_org"}
                    )
                )
            elif ids:
                output.extend(_included(action, kind, sorted(ids)))
        result = tuple(output)
    return result


def restrict(tuples: tuple[Grant, ...], **bounds: tuple[str, ...] | None) -> tuple[Grant, ...]:
    """Apply V1/V4 resource ceilings without combining different actions."""
    result: list[Grant] = []
    for grant in tuples:
        selected = bounds.get(grant.resource_kind)
        if selected is None:
            result.append(grant)
            continue
        ids = (
            set(selected)
            if grant.selector == "all_in_org"
            else set(grant.ids) & set(selected)
            if grant.selector == "include"
            else set()
        )
        result.extend(_included(grant.action, grant.resource_kind, sorted(ids)))
    return tuple(result)


def projection(tuples: tuple[Grant, ...], *kinds: str) -> LegacyProjection:
    """Legacy metadata bounds declared dimensions; it never supplies authority."""
    selected = [g for g in tuples if g.resource_kind in kinds]
    if any(g.selector == "all_in_org" for g in selected):
        return LegacyProjection(mode="all_in_org")
    identifiers = sorted({i for g in selected if g.selector == "include" for i in g.ids})
    maximum = (
        MAX_PROFILES if kinds == ("creator_profile",) else min(MAX_PROJECTS, MAX_AGENT_PROJECTS)
    )
    if len(identifiers) > maximum:
        raise PolicyDenied("declared resources exceed the bounded legacy credential/client limit")
    return LegacyProjection(mode="include" if identifiers else "none", ids=tuple(identifiers))
