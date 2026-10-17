"""Phase8 contracts and source inventory; real aggregation is proved separately."""

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from app.api.routes.summaries import router
from app.db.tenant_inventory import MODEL_BY_COLLECTION
from app.identity.repository import COLLECTION_OWNERSHIP
from app.security.policy.evaluation import PolicyDenied
from app.security.policy.models import Grant
from app.security.tenant import TenantContext, TenantContextRequired, tenant_scope
from app.security.tenant_policy import TenantPolicy, policy_scope
from app.summaries.coverage import coverage
from app.summaries.inventory import EXCLUDED_SOURCES, ROUTES, STORAGE
from app.summaries.models import SummaryQuery
from app.summaries.service import Summaries
from app.summaries.visibility import SOURCES, visibility

NOW = datetime(2026, 10, 17, 12, tzinfo=UTC)


def query(**changes):
    return SummaryQuery.model_validate({"start": NOW - timedelta(hours=1), "end": NOW, **changes})


def snapshot(*grants, role="team_lead"):
    return {
        "anchor": {"org_id": "org-a", "revision": 1},
        "member": {"user_id": "lead", "role": role},
        "tuples": grants,
        "teams": {"alpha": {"status": "active"}},
    }


def g(action, kind, *ids):
    return Grant(action=action, resource_kind=kind, selector="include", ids=list(ids))


@pytest.mark.parametrize(
    "changes",
    [
        {"start": NOW.replace(tzinfo=None)},
        {"end": NOW - timedelta(hours=2)},
        {"start": NOW - timedelta(days=32)},
        {"timezone": "Not/AZone"},
        {"scope": "team"},
        {"scope": "own", "team_id": "alpha"},
        {"source": "governance", "scope": "team", "team_id": "alpha"},
        {"source": "retrieval_logs"},
        {"prompt": "private"},
    ],
)
def test_bounded_time_timezone_source_and_filter_contract(changes):
    with pytest.raises(ValidationError):
        query(**changes)


def test_role_does_not_replace_explicit_activity_or_audit_assignment():
    for role in ("org_owner", "org_admin", "executive", "team_lead", "auditor"):
        with pytest.raises(PolicyDenied):
            visibility(snapshot(role=role), query(scope="org"))
        with pytest.raises(PolicyDenied):
            visibility(snapshot(role=role), query(source="governance", scope="org"))
    with pytest.raises(PolicyDenied):
        Summaries(Mock(managed_agents=False))


def test_missing_foreign_and_machine_contexts_cannot_enter_human_reads():
    with pytest.raises(TenantContextRequired):
        Summaries.context("org-a", "lead")
    with tenant_scope(TenantContext("org-b")), pytest.raises(PolicyDenied):
        Summaries.context("org-a", "lead")
    machine = TenantPolicy("org-a", "lead", (), managed=True, subject_kind="machine")
    with tenant_scope(TenantContext("org-a")), policy_scope(machine), pytest.raises(PolicyDenied):
        Summaries.context("org-a", "lead")


def test_collector_gaps_prevent_complete_historical_governance_claims():
    q = query(source="governance", scope="org")
    declaration = {
        "coverage_start": NOW - timedelta(days=1),
        "retained_from": NOW - timedelta(days=1),
        "through": NOW,
        "available": True,
        "storage_retention": "installation_managed",
        "gaps": [{"start": NOW - timedelta(minutes=20), "end": NOW - timedelta(minutes=10)}],
    }
    result = coverage(q, {"change": declaration, "created_at": NOW}, NOW)
    assert result.state == "partial" and len(result.gaps) == 1


def test_team_resources_are_coupled_to_the_activity_action_and_no_current_worker_join():
    selected = visibility(
        snapshot(
            g("activity:read:team", "team", "alpha"),
            g("activity:read:team", "project", "allowed-project"),
            g("context:read", "project", "other-project"),
            g("activity:read:self", "project", "own-project"),
        ),
        query(scope="team", team_id="alpha"),
    )
    assert {"org_id": "org-a"} in selected["$and"]
    assert {"project_id": {"$in": ["allowed-project"]}} in selected["$and"]
    assert {"sponsor_team_id": "alpha", "sponsorship_revision": {"$gte": 1}} in selected["$and"]
    assert "team_memberships" not in str(selected)
    none = visibility(
        snapshot(g("activity:read:team", "team", "alpha")), query(scope="team", team_id="alpha")
    )
    assert {"project_id": {"$in": []}} in none["$and"]


@pytest.mark.parametrize(
    "source,state,changes",
    [
        ("governance", "complete", {}),
        ("gateway_activity", "partial", {}),
        ("governance", "unavailable", {"available": False}),
        ("governance", "stale", {"through": NOW - timedelta(minutes=20)}),
        ("governance", "partial", {"retained_from": NOW - timedelta(minutes=30)}),
        (
            "governance",
            "absent_history",
            {
                "coverage_start": NOW + timedelta(seconds=1),
                "retained_from": NOW + timedelta(seconds=1),
            },
        ),
    ],
)
def test_explicit_coverage_states_do_not_infer_collection_from_zero(source, state, changes):
    q = query(source=source, scope="org")
    declaration = {
        "coverage_start": NOW - timedelta(days=1),
        "retained_from": NOW - timedelta(days=1),
        "through": NOW,
        "available": True,
        "storage_retention": "installation_managed",
        **changes,
    }
    result = coverage(q, {"change": declaration, "created_at": NOW}, NOW)
    assert result.state == state and result.watermark == declaration["through"]
    assert result.population and result.collection_contract and result.served_history_days == 90
    assert coverage(q, None, NOW).state == "missing"


def test_every_phase8_route_storage_and_query_source_reconciles_with_source():
    actual = {f"{method} {route.path}" for route in router.routes for method in route.methods}
    assert actual == set(ROUTES)
    assert all(
        any(
            getattr(dep.call, "__name__", "") == "current_browser"
            for dep in route.dependant.dependencies
        )
        for route in router.routes
    )
    assert set(STORAGE) <= set(COLLECTION_OWNERSHIP) | set(MODEL_BY_COLLECTION)
    assert set(SOURCES.values()) == {"agent_activity", "governance_events"}
    used = set()
    for path in Path("app/summaries").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "db"
                and isinstance(node.slice, ast.Constant)
            ):
                used.add(node.slice.value)
    assert used <= set(STORAGE)
    assert {"retrieval_logs", "audit_logs", "identity_audit_events", "metrics"} == set(
        EXCLUDED_SOURCES
    )
