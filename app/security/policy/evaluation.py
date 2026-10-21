"""Pure lineage evaluation, also used inside the shared-anchor transaction."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from app.security.policy.catalog import can_appoint, role_allows
from app.security.policy.models import Grant, Issue, Selector, ceiling_hash, covered
from app.security.principal import AuthorizationError

type Row = dict[str, Any]


class PolicyDenied(AuthorizationError):
    """Safe refusal without disclosing another subject or resource."""


class PolicyConflict(PolicyDenied):
    """A fresh policy/context must be obtained before another attempt."""


def grants(row: Mapping[str, Any], field: str = "tuples") -> tuple[Grant, ...]:
    return tuple(Grant.model_validate(value) for value in row[field])


class _SnapshotGrants:
    """Decode/hash/index immutable rows during one synchronous evaluation.

    This object never outlives effective(); authority, members and generations
    are still rechecked from the next request's fresh database snapshot.
    """

    def __init__(self) -> None:
        self.parsed: dict[tuple[int, str], tuple[Grant, ...]] = {}
        self.hashes: dict[int, str] = {}
        self.targets: dict[int, Selector] = {}
        self.bounds: dict[int, dict[tuple[str, str], tuple[bool, set[str]]]] = {}

    def grants(self, row: Row, field: str = "tuples") -> tuple[Grant, ...]:
        key = (id(row), field)
        if key not in self.parsed:
            self.parsed[key] = grants(row, field)
        return self.parsed[key]

    def ceiling_hash(self, row: Row) -> str:
        key = id(row)
        if key not in self.hashes:
            self.hashes[key] = ceiling_hash(self.grants(row, "ceiling"))
        return self.hashes[key]

    def target(self, row: Row) -> Selector:
        key = id(row)
        if key not in self.targets:
            self.targets[key] = Selector.model_validate(row["targets"])
        return self.targets[key]

    def covers(self, parent: Row, grant: Grant) -> bool:
        if grant.selector == "none":
            return True
        key = id(parent)
        if key not in self.bounds:
            index: dict[tuple[str, str], tuple[bool, set[str]]] = {}
            for value in self.grants(parent, "ceiling"):
                pair = value.action, value.resource_kind
                _, ids = index.setdefault(pair, (False, set()))
                if value.selector == "include":
                    ids.update(value.ids)
                if value.selector == "all_in_org":
                    index[pair] = True, ids
            self.bounds[key] = index
        all_in_org, ids = self.bounds[key].get((grant.action, grant.resource_kind), (False, set()))
        return all_in_org or (grant.selector == "include" and set(grant.ids) <= ids)


def active_chain(
    row: Row,
    assignments: Mapping[str, Row],
    members: Mapping[str, Row],
    users: Mapping[str, Row],
    now: datetime,
    teams: Mapping[str, Row] | None = None,
    team_links: Mapping[tuple[str, str], Row] | None = None,
    *,
    _parsed: _SnapshotGrants | None = None,
) -> tuple[Row, ...]:
    """Generations and membership revisions never reattach old descendants."""
    parsed = _parsed if _parsed is not None else _SnapshotGrants()
    chain: list[Row] = []
    seen: set[str] = set()
    while True:
        member, user = members.get(row["subject_id"]), users.get(row["subject_id"])
        if (
            row["_id"] in seen
            or len(chain) >= 4
            or row["status"] != "active"
            or not row["starts_at"] <= now < row["expires_at"]
            or row["subject_kind"] != "human"
            or not member
            or not user
            or member["org_id"] != row["org_id"]
            or member["status"] != "active"
            or not member["verified_at"]
            or user["status"] != "active"
            or member["revision"] != row["subject_revision"]
            or member["role"] != row["role"]
            or row["ceiling_hash"] != parsed.ceiling_hash(row)
        ):
            raise PolicyDenied("assignment ancestry is unavailable")
        if teams is not None and member["role"] == "team_lead" and not row.get("team_id"):
            raise PolicyDenied("lead authority requires a reviewed named team")
        if row.get("team_id"):
            team = (teams or {}).get(row["team_id"])
            link = (team_links or {}).get((row["team_id"], row["subject_id"]))
            if (
                not team
                or not link
                or team["org_id"] != row["org_id"]
                or link["org_id"] != row["org_id"]
                or team["status"] != "active"
                or link["status"] != "active"
                or team["generation"] != row.get("team_generation")
                or link["revision"] != row.get("team_link_revision")
            ):
                raise PolicyDenied("team authority is unavailable")
        seen.add(row["_id"])
        chain.append(row)
        if row["parent_id"] is None:
            if row["issuer_id"] != "installation_operator":
                raise PolicyDenied("root authority is unavailable")
            return tuple(chain)
        parent = assignments.get(row["parent_id"])
        if (
            not parent
            or parent["org_id"] != row["org_id"]
            or parent["generation"] != row["parent_generation"]
            or parent["subject_id"] != row["issuer_id"]
            or parent["subject_id"] == row["subject_id"]
            or row["expires_at"] > parent["expires_at"]
            or row["remaining_depth"] >= parent["remaining_depth"]
            or not (
                (
                    row.get("team_delegation")
                    and row.get("team_id")
                    and row["remaining_depth"] == 0
                    and not row["allowed_roles"]
                    and parsed.target(row).mode == "none"
                    and any(
                        g.action == "grants:delegate"
                        and g.resource_kind == "team"
                        and g.selection().contains(row["team_id"])
                        for g in parsed.grants(parent)
                    )
                )
                or (
                    not row.get("team_delegation")
                    and parsed.target(parent).contains(row["subject_id"])
                )
            )
            or not parsed.target(row).within(parsed.target(parent))
            or row["role"] not in parent["allowed_roles"]
            or not can_appoint(parent["role"], row["role"])
            or not set(row["allowed_roles"]) <= set(parent["allowed_roles"])
            or any(
                not parsed.covers(parent, g)
                for g in (*parsed.grants(row), *parsed.grants(row, "ceiling"))
            )
        ):
            raise PolicyDenied("assignment ancestry is unavailable")
        row = parent


def validate_issue(
    parent: Row, request: Issue, issuer_id: str, now: datetime, *, team_authorized: bool = False
) -> None:
    if (
        request.subject_id == issuer_id
        or parent["subject_id"] != issuer_id
        or not any(
            g.action == "grants:delegate"
            and (
                (
                    not team_authorized
                    and g.resource_kind == "member"
                    and g.selection().contains(request.subject_id)
                )
                or (
                    team_authorized
                    and g.resource_kind == "team"
                    and g.selection().contains(str(request.team_id))
                )
            )
            for g in grants(parent)
        )
        or parent["generation"] != request.expected_parent_generation
        or parent["status"] != "active"
        or parent["remaining_depth"] <= request.remaining_depth
        or not now < request.expires_at <= parent["expires_at"]
        or request.role not in parent["allowed_roles"]
        or not can_appoint(parent["role"], request.role)
        or (
            not team_authorized
            and not Selector.model_validate(parent["targets"]).contains(request.subject_id)
        )
        or (
            team_authorized
            and (
                request.targets.mode != "none"
                or request.remaining_depth != 0
                or bool(request.allowed_roles)
            )
        )
        or not request.targets.within(Selector.model_validate(parent["targets"]))
        or not set(request.allowed_roles) <= set(parent["allowed_roles"])
        or any(not can_appoint(request.role, role) for role in request.allowed_roles)
    ):
        raise PolicyDenied("delegation is outside the approved envelope")
    for grant in (*request.tuples, *request.ceiling):
        if not covered(grant, grants(parent, "ceiling")):
            raise PolicyDenied("delegation is outside the approved ceiling")
    # A delegation ceiling is issuance authority, not personal use. A lead may
    # sponsor a reviewer without becoming a reviewer themselves.
    for grant in request.tuples:
        if (
            grant.selector != "none"
            and ":" in grant.action
            and grant.action.split(":")[0]
            in {
                "org",
                "members",
                "teams",
                "grants",
                "clients",
                "credentials",
                "activity",
                "audit",
                "reports",
                "reviews",
            }
            and not role_allows(request.role, grant.action, explicit=True)
        ):
            # Domain audit:self:read is not a management permission.
            from app.identity.models import MANAGEMENT_ACTIONS

            if grant.action in MANAGEMENT_ACTIONS:
                raise PolicyDenied("the role cannot hold that management capability")
    if (
        any(g.selector == "all_in_org" for g in (*request.tuples, *request.ceiling))
        or request.targets.mode == "all_in_org"
    ):
        if not any(
            g.action == "grants:all-in-org"
            and g.resource_kind == "organization"
            and g.selection().contains(parent["org_id"])
            for g in grants(parent)
        ):
            raise PolicyDenied("all_in_org needs distinct current approval")


def effective(
    org_id: str,
    subject_id: str,
    assignments: Mapping[str, Row],
    members: Mapping[str, Row],
    users: Mapping[str, Row],
    now: datetime,
    teams: Mapping[str, Row] | None = None,
    team_links: Mapping[tuple[str, str], Row] | None = None,
) -> tuple[tuple[Grant, ...], list[Row]]:
    parsed = _SnapshotGrants()
    tuples: list[Grant] = []
    explanations: list[Row] = []
    for row in assignments.values():
        if row["org_id"] != org_id or row["subject_id"] != subject_id:
            continue
        try:
            chain = active_chain(
                row, assignments, members, users, now, teams, team_links, _parsed=parsed
            )
        except PolicyDenied:
            explanations.append(
                {
                    "assignment_id": row["_id"],
                    "effective": False,
                    "reason": "inactive_or_stale_ancestry",
                }
            )
            continue
        valid = parsed.grants(row)
        tuples.extend(valid)
        explanations.append(
            {
                "assignment_id": row["_id"],
                "effective": True,
                "generation": row["generation"],
                "expires_at": row["expires_at"],
                "lineage": [{"id": p["_id"], "generation": p["generation"]} for p in chain],
                "tuples": [g.model_dump() for g in valid],
            }
        )
    return tuple(tuples), explanations
