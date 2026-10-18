"""Session-only, tenant-bound Phase 10 console projections and previews."""

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query

from app.api.routes.managed_agents import agents
from app.identity.browser import CurrentBrowser
from app.identity.models import Input
from app.identity.repository import Row
from app.management.adapters import ManagementAdapters
from app.models.common import Identifier
from app.security.policy.governance import Governance
from app.security.policy.models import Issue

router = APIRouter(prefix="/api/v6/organizations/{org_id}/management", tags=["management console"])


@router.get("/context")
async def authoring_context(org_id: str, context: CurrentBrowser) -> Row:
    service = agents(context)
    return await ManagementAdapters(service.engine).context(org_id, context.row["user_id"])


@router.post("/policy-preview")
async def policy_preview(org_id: str, body: Issue, context: CurrentBrowser) -> Row:
    engine = agents(context, mutation=True).engine
    async with engine.transaction() as tx:
        snapshot = await engine.snapshot(org_id, context.row["user_id"], tx)
        return await Governance(engine).issue_in_transaction(snapshot, tx, body, preview=True)


@router.get("/catalog/{kind}")
async def catalog(
    org_id: str,
    kind: Literal["clients", "services", "templates", "sessions", "bindings"],
    context: CurrentBrowser,
    prefix: Annotated[str, Query(max_length=128)] = "",
    status: Annotated[str | None, Query(max_length=32)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: str | None = None,
    client_id: str | None = None,
) -> Row:
    return await ManagementAdapters(agents(context).engine).page(
        org_id,
        context.row["user_id"],
        kind,
        prefix=prefix,
        status=status,
        limit=limit,
        cursor=cursor,
        client_id=client_id,
    )


@router.get("/templates/{profile_id}/impact")
async def template_impact(org_id: str, profile_id: str, context: CurrentBrowser) -> Row:
    return await ManagementAdapters(agents(context).engine).template_impact(
        org_id, context.row["user_id"], profile_id
    )


class Revision(Input):
    expected_revision: Annotated[int, Query(ge=1)]


@router.post("/clients/{client_id}/sessions/{session_id}/end")
async def end_session(
    org_id: str, client_id: str, session_id: str, body: Revision, context: CurrentBrowser
) -> Row:
    return await ManagementAdapters(agents(context, mutation=True).engine).end_session(
        org_id, context.row["user_id"], client_id, session_id, body.expected_revision
    )


@router.get("/ownership-transfers")
async def transfers(org_id: str, context: CurrentBrowser) -> Row:
    engine = agents(context).engine
    actor = context.row["user_id"]
    await engine.snapshot(org_id, actor)
    rows = await engine.store.list(
        "ownership_transfers",
        {
            "org_id": org_id,
            "$or": [{"owner_id": actor}, {"target_id": actor}],
        },
    )
    fields = (
        "_id",
        "owner_id",
        "target_id",
        "owner_revision",
        "target_revision",
        "policy_revision",
        "status",
        "expires_at",
    )
    return {"items": [{k: r[k] for k in fields} for r in rows[:100]], "truncated": len(rows) > 100}


@router.get("/members/{user_id}")
async def member_detail(org_id: str, user_id: str, context: CurrentBrowser) -> Row:
    from app.management.boundaries import has
    from app.management.directory import MEMBER_FIELDS, member_query
    from app.security.policy.catalog import can_appoint
    from app.security.policy.evaluation import PolicyDenied

    engine = agents(context).engine
    actor = context.row["user_id"]
    snapshot = await engine.snapshot(org_id, actor)
    row = await engine.store.find(
        "organization_memberships",
        {
            "$and": [member_query(snapshot), {"org_id": org_id, "user_id": user_id}],
        },
    )
    if not row:
        raise PolicyDenied("member is unavailable")
    teams = []
    for (team_id, user), link in snapshot["team_links"].items():
        if user == user_id and (
            has(snapshot, "teams:read", "organization", org_id)
            or has(snapshot, "members:read", "team", team_id)
        ):
            team_row = snapshot["teams"][team_id]
            teams.append(
                {
                    "team_id": team_id,
                    "name": team_row["name"],
                    "team_revision": team_row["revision"],
                    "status": link["status"],
                    "revision": link["revision"],
                }
            )
    actions = []
    for action in ("members:suspend", "members:reinstate", "grants:delegate", "grants:read"):
        try:
            engine.require(snapshot, action, "member", user_id)
        except PolicyDenied:
            continue
        if action in {"members:suspend", "members:reinstate", "grants:delegate"} and (
            actor == user_id or not can_appoint(snapshot["member"]["role"], row["role"])
        ):
            continue
        if (
            action in {"members:suspend", "members:reinstate"}
            and snapshot["member"]["role"] == "team_lead"
            and any(
                user == user_id
                and link["status"] == "active"
                and not has(snapshot, action, "team", team_id)
                for (team_id, user), link in snapshot["team_links"].items()
            )
        ):
            continue
        actions.append(action)
    return {"member": {k: row[k] for k in MEMBER_FIELDS}, "teams": teams, "actions": actions}


class Binding(Input):
    expected_revision: Annotated[int, Query(ge=0)]
    project_id: Identifier
    binding_type: Literal["git_remote", "repository_url", "repository_name", "workspace_alias"]
    value: Annotated[str, Query(min_length=1, max_length=2048)]


@router.put("/bindings/{binding_id}")
async def put_binding(
    org_id: str, binding_id: Identifier, body: Binding, context: CurrentBrowser
) -> Row:
    try:
        return await ManagementAdapters(agents(context, mutation=True).engine).put_binding(
            org_id, context.row["user_id"], binding_id, body.model_dump()
        )
    except ValueError:
        raise HTTPException(422, "binding value was not accepted") from None


@router.post("/bindings/{binding_id}/delete")
async def delete_binding(
    org_id: str, binding_id: str, body: Revision, context: CurrentBrowser
) -> Row:
    return await ManagementAdapters(agents(context, mutation=True).engine).delete_binding(
        org_id, context.row["user_id"], binding_id, body.expected_revision
    )


@router.get("/own-invitations")
async def own_invitations(
    org_id: str,
    context: CurrentBrowser,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: str | None = None,
) -> Row:
    engine = agents(context).engine
    snapshot = await engine.snapshot(org_id, context.row["user_id"])
    from app.management.directory import Directory

    return await Directory(engine).page(
        "management_invitations",
        {"org_id": org_id, "user_id": context.row["user_id"]},
        "_id",
        ("_id", "org_id", "user_id", "issuer_id", "policy", "status", "revision", "expires_at"),
        snapshot,
        limit,
        cursor,
    )
