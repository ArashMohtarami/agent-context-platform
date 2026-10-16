"""Current named-team boundaries; no authority is inferred from role or owner_id."""

from app.identity.repository import Row
from app.security.policy.evaluation import PolicyDenied, active_chain, grants, validate_issue
from app.security.policy.models import Issue, Selector


def has(snapshot: Row, action: str, kind: str, identifier: str) -> bool:
    return any(
        g.action == action and g.resource_kind == kind and g.selection().contains(identifier)
        for g in snapshot["tuples"]
    )


def permits_member(snapshot: Row, action: str, user_id: str) -> bool:
    return any(
        team["status"] == "active"
        and team["org_id"] == snapshot["anchor"]["org_id"]
        and has(snapshot, action, "team", team_id)
        and (link := snapshot.get("team_links", {}).get((team_id, user_id))) is not None
        and link["org_id"] == snapshot["anchor"]["org_id"]
        and link["status"] == "active"
        for team_id, team in snapshot.get("teams", {}).items()
    )


def team_scope(
    snapshot: Row, request: Issue, *, pending_link: bool = False
) -> tuple[Row | None, Row | None, bool]:
    """Compute the alternate team envelope from current parent authority, never input alone."""
    if request.team_id is None:
        if snapshot["member"]["role"] == "team_lead" or request.role == "team_lead":
            raise PolicyDenied("team leads require an explicit named team scope")
        return None, None, False
    team = snapshot.get("teams", {}).get(request.team_id)
    link = snapshot.get("team_links", {}).get((request.team_id, request.subject_id))
    if (
        not team
        or team["status"] != "active"
        or team["generation"] != request.expected_team_generation
    ):
        raise PolicyDenied("team scope changed or is unavailable")
    if not pending_link and (not link or link["status"] != "active"):
        raise PolicyDenied("verified active team membership is required")
    parent = snapshot["assignments"].get(request.parent_id)
    direct = bool(
        parent
        and snapshot["member"]["role"] != "team_lead"
        and Selector.model_validate(parent["targets"]).contains(request.subject_id)
        and any(
            g.action == "grants:delegate"
            and g.resource_kind == "member"
            and g.selection().contains(request.subject_id)
            for g in grants(parent)
        )
    )
    alternate = bool(
        parent
        and not direct
        and any(
            g.action == "grants:delegate"
            and g.resource_kind == "team"
            and g.selection().contains(request.team_id)
            for g in grants(parent)
        )
    )
    if snapshot["member"]["role"] == "team_lead" and (
        not alternate or parent.get("team_id") != request.team_id
    ):
        raise PolicyDenied("lead authority is outside its delegated team")
    if request.role == "team_lead":
        for g in (*request.tuples, *request.ceiling):
            if (
                g.action.startswith(("members:", "grants:", "teams:"))
                and not (
                    g.action == "grants:read"
                    and g.resource_kind == "member"
                    and g.selector == "include"
                    and g.ids == [request.subject_id]
                )
                and g.selector != "none"
                and (
                    g.resource_kind != "team"
                    or g.selector != "include"
                    or g.ids != [request.team_id]
                )
            ):
                raise PolicyDenied("lead management authority must name only its delegated team")
    return team, link, alternate


def validate_parent(
    snapshot: Row, request: Issue, *, pending_link: bool = False
) -> tuple[Row, Row | None, Row | None, bool]:
    from app.models.common import utc_now

    parent = snapshot["assignments"].get(request.parent_id)
    if not parent:
        raise PolicyDenied("parent authority is unavailable")
    active_chain(
        parent,
        snapshot["assignments"],
        snapshot["members"],
        snapshot["users"],
        utc_now(),
        snapshot.get("teams"),
        snapshot.get("team_links"),
    )
    team, link, alternate = team_scope(snapshot, request, pending_link=pending_link)
    validate_issue(
        parent, request, snapshot["member"]["user_id"], utc_now(), team_authorized=alternate
    )
    return parent, team, link, alternate
