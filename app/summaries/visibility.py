"""One pre-aggregation predicate for detail, counts, buckets and reports."""

from app.identity.repository import Row
from app.management.boundaries import has
from app.security.policy.evaluation import PolicyDenied
from app.summaries.models import SummaryQuery

SOURCES = {"gateway_activity": "agent_activity", "governance": "governance_events"}


def visibility(snapshot: Row, query: SummaryQuery) -> Row:
    org, actor = snapshot["anchor"]["org_id"], snapshot["member"]["user_id"]
    parts: list[Row] = [
        {"org_id": org},
        {"created_at": {"$gte": query.start, "$lt": query.end}},
    ]
    if query.source == "governance":
        if snapshot["member"]["role"] == "team_lead" or not has(
            snapshot, "audit:read:org", "organization", org
        ):
            raise PolicyDenied("organization audit authority is required")
        # Checkpoints and report descriptors are control records, not activity.
        parts.append({"action": {"$not": {"$regex": "^summary\\."}}})
    else:
        action = "activity:read:" + {"own": "self", "team": "team", "org": "org"}[query.scope]
        if query.scope == "org":
            # Explicit organization metadata assignment, never a role bypass.
            if snapshot["member"]["role"] == "team_lead" or not has(
                snapshot, action, "organization", org
            ):
                raise PolicyDenied("organization activity authority is required")
        else:
            if query.scope == "own":
                if not has(snapshot, action, "member", actor):
                    raise PolicyDenied("own activity authority is required")
                parts.append({"sponsor_id": actor, "sponsorship_revision": {"$gte": 1}})
            else:
                team = snapshot.get("teams", {}).get(query.team_id)
                if (
                    not team
                    or team["status"] != "active"
                    or not has(snapshot, action, "team", str(query.team_id))
                ):
                    raise PolicyDenied("team activity is outside current delegated authority")
                # Never join today's worker membership to historical events.
                parts.append(
                    {"sponsor_team_id": query.team_id, "sponsorship_revision": {"$gte": 1}}
                )
            # Metadata access is action/resource coupled too. A team envelope
            # alone cannot expose every project. NONE remains an empty selector.
            grants = [
                g for g in snapshot["tuples"] if g.action == action and g.resource_kind == "project"
            ]
            projects = sorted({i for g in grants if g.selector == "include" for i in g.ids})
            parts.append(
                {"project_id": {"$type": "string"}}
                if any(g.selector == "all_in_org" for g in grants)
                else {"project_id": {"$in": projects}}
            )
        if query.team_id:
            parts.append({"sponsor_team_id": query.team_id})
        if query.project_id:
            parts.append({"project_id": query.project_id})
        if query.client_id:
            parts.append({"client_id": query.client_id})
    if query.outcome:
        parts.append(
            {"status": query.outcome}
            if query.source == "gateway_activity"
            else {"$expr": {"$eq": [query.outcome, "committed"]}}
        )
    return {"$and": parts}
