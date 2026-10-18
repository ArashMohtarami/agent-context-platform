"""Real policy-rechecked, expiring, redacted bounded CSV downloads."""

import csv
import json
from datetime import timedelta
from io import StringIO
from uuid import uuid4

import pytest
from bson import ObjectId
from v6_summary_fixtures import grant, params

from app.models.common import utc_now
from app.security.policy.models import Issue
from app.security.tenant import TenantContext, tenant_scope
from app.summaries.coverage import REPORT_ACTION
from app.summaries.models import MAX_REPORT_ROWS
from app.summaries.operator import checkpoint
from tests.integration.test_v6_policy_fencing import browser

pytestmark = pytest.mark.integration
BASE = "/api/v6/organizations/org-a"


async def auditor(h):
    return await h.governance.issue(
        "org-a",
        "owner",
        Issue(
            subject_id="other",
            role="auditor",
            parent_id=h.root,
            expected_parent_generation=4,
            tuples=(grant("activity:read:org"), grant("audit:read:org"), grant("reports:export")),
            expires_at=utc_now() + timedelta(days=1),
        ),
    )


async def test_real_csv_matches_detail_aggregate_and_retention_no_foreign_content(summary_db):
    h = summary_db
    async with browser(h, "owner") as (client, headers):
        denied = await client.post(
            BASE + "/reports",
            json=h.query.model_dump(mode="json"),
            headers={k: v for k, v in headers.items() if k.lower() == "x-session-context"},
        )
        assert denied.status_code == 403
        created = await client.post(
            BASE + "/reports", json=h.query.model_dump(mode="json"), headers=headers
        )
        assert created.status_code == 201, created.text
        report = created.json()
        downloaded = await client.get(report["download_path"], headers=headers)
        assert downloaded.status_code == 200, downloaded.text
        assert downloaded.headers["cache-control"] == "no-store"
        rows = list(csv.DictReader(StringIO(downloaded.text)))
        overview = await client.get(BASE + "/overview", params=params(h.query), headers=headers)
        page = await client.get(BASE + "/activity", params=params(h.query), headers=headers)
        assert len(rows) == overview.json()["observed_count"] == page.json()["observed_count"] == 6
        assert {x["event_id"] for x in rows} == {x["event_id"] for x in page.json()["items"]}
        metadata = json.loads(downloaded.headers["x-summary-metadata"])
        assert metadata["coverage"]["state"] == "partial" and metadata["observed_count"] == 6
        assert h.event_ids["foreign-private-sentinel"] not in downloaded.text
        assert all(
            x not in downloaded.text
            for x in (
                "prompt",
                "transcript",
                "response",
                "latency_ms",
                "key_hash",
                "approval_reference",
            )
        )
    # A checkpoint can narrow served retention without rewriting archived data.
    with tenant_scope(TenantContext("org-a")):
        await checkpoint(
            h.engine,
            "org-a",
            h.declaration.model_copy(update={"retained_from": h.begin + timedelta(minutes=3)}),
        )
    async with browser(h, "owner") as (client, headers):
        retained = await client.get(report["download_path"], headers=headers)
        overview = await client.get(BASE + "/overview", params=params(h.query), headers=headers)
        assert retained.status_code == 200
        assert (
            len(list(csv.DictReader(StringIO(retained.text))))
            == overview.json()["observed_count"]
            == 4
        )
    async with browser(h, "other") as (client, headers):
        denied = await client.get(report["download_path"], headers=headers)
        assert denied.status_code == 403
    stored = await h.db.governance_events.find_one(
        {"action": REPORT_ACTION, "target_id": report["report_id"]}
    )
    assert stored["change"]["query"]["scope"] == "org"
    assert "items" not in stored["change"] and "payload" not in stored["change"]


async def test_real_revoked_policy_active_session_and_expired_reports_fail(summary_db):
    h = summary_db
    assignment = await auditor(h)
    async with browser(h, "other") as (client, headers):
        created = await client.post(
            BASE + "/reports", json=h.query.model_dump(mode="json"), headers=headers
        )
        assert created.status_code == 201, created.text
        path = created.json()["download_path"]
        assert (await client.get(path, headers=headers)).status_code == 200
        await h.governance.change("org-a", "owner", assignment["_id"], 1, revoke=True)
        denied = await client.get(path, headers=headers)
        assert denied.status_code == 403 and "event_id" not in denied.text
    # Even reissuing equal authority cannot resurrect a receipt tied to an old
    # policy generation. New reports are a new explicit approval of filters.
    await auditor(h)
    async with browser(h, "other") as (client, headers):
        assert (await client.get(path, headers=headers)).status_code == 403
        created = await client.post(
            BASE + "/reports", json=h.query.model_dump(mode="json"), headers=headers
        )
        assert created.status_code == 201, created.text
        report = created.json()
        await h.db.governance_events.update_one(
            {"action": REPORT_ACTION, "target_id": report["report_id"]},
            {"$set": {"change.expires_at": utc_now() - timedelta(seconds=1)}},
        )
        expired = await client.get(report["download_path"], headers=headers)
        assert expired.status_code == 403 and "event_id" not in expired.text
        wrong = await client.get(report["download_path"].replace("org-a", "org-b"), headers=headers)
        assert wrong.status_code == 403


async def test_real_governance_csv_formula_neutralization_and_body_redaction(summary_db):
    h = summary_db
    poison_id = '=HYPERLINK("https://invalid.example")'
    # BSON dates have millisecond precision; keep the synthetic record strictly
    # before the exclusive window end even on a sub-millisecond local round trip.
    now = utc_now() - timedelta(milliseconds=10)
    await h.db.governance_events.insert_one(
        {
            "_id": poison_id,
            "org_id": "org-a",
            "actor_id": "unknown_legacy",
            "action": "team_changed",
            "target_id": "private-object",
            "approval_reference": "PRIVATE_APPROVAL_SENTINEL",
            "change": {
                "prompt": "PRIVATE_PROMPT_SENTINEL",
                "response_body": "PRIVATE_BODY_SENTINEL",
            },
            "created_at": now,
        }
    )
    query = h.query.model_copy(
        update={"source": "governance", "start": now - timedelta(seconds=1), "end": utc_now()}
    )
    async with browser(h, "owner") as (client, headers):
        created = await client.post(
            BASE + "/reports", json=query.model_dump(mode="json"), headers=headers
        )
        assert created.status_code == 201, created.text
        response = await client.get(created.json()["download_path"], headers=headers)
        assert response.status_code == 200, response.text
        rows = list(csv.DictReader(StringIO(response.text)))
        poison = next(x for x in rows if x["event_id"] == "'" + poison_id)
        assert poison["attribution"] == "legacy_unattributed"
        assert all(
            x not in response.text
            for x in (
                "PRIVATE_APPROVAL_SENTINEL",
                "PRIVATE_PROMPT_SENTINEL",
                "PRIVATE_BODY_SENTINEL",
                "private-object",
            )
        )


async def test_real_large_report_refuses_partial_delivery_and_empty_foreign_filter(summary_db):
    h = summary_db
    sample = await h.db.agent_activity.find_one({"org_id": "org-a"})
    await h.db.agent_activity.insert_many(
        [{**sample, "_id": ObjectId(), "event_id": uuid4().hex} for _ in range(MAX_REPORT_ROWS)]
    )
    async with browser(h, "owner") as (client, headers):
        created = await client.post(
            BASE + "/reports", json=h.query.model_dump(mode="json"), headers=headers
        )
        assert created.status_code == 201, created.text
        refused = await client.get(created.json()["download_path"], headers=headers)
        assert refused.status_code == 403 and "event_id" not in refused.text
        foreign = h.query.model_copy(update={"project_id": "foreign-only-project"})
        created = await client.post(
            BASE + "/reports", json=foreign.model_dump(mode="json"), headers=headers
        )
        assert created.status_code == 201, created.text
        response = await client.get(created.json()["download_path"], headers=headers)
        assert response.status_code == 200 and list(csv.DictReader(StringIO(response.text))) == []
        assert json.loads(response.headers["x-summary-metadata"])["observed_count"] == 0
