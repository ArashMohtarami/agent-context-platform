"""Measured disposable workloads. Targets are reported separately from correctness assertions."""

import asyncio
import json
import math
import os
import platform
from datetime import timedelta
from pathlib import Path
from time import perf_counter

import pytest
from bson import ObjectId
from pymongo import AsyncMongoClient, monitoring
from v6_summary_fixtures import params, prepare_summary

from app.identity.crypto import AuthBusy, PasswordWork
from app.models.common import normalize_mongo_timestamp, utc_now
from app.security.tenant import TenantContext, tenant_scope
from app.summaries.models import SummaryQuery
from tests.integration.test_v6_policy_fencing import browser

pytestmark = pytest.mark.integration


def distribution(samples):
    ordered = sorted(samples)
    return {
        "samples": len(samples),
        "p50_ms": ordered[math.ceil(len(ordered) * 0.5) - 1],
        "p95_ms": ordered[math.ceil(len(ordered) * 0.95) - 1],
        "max_ms": ordered[-1],
    }


def evidence(tmp_path, name, data):
    directory = Path(os.environ.get("V6_REPORT_DIR", str(tmp_path)))
    directory.mkdir(parents=True, exist_ok=True)
    data["hardware"] = {
        "platform": platform.platform(),
        "logical_cpus": os.cpu_count(),
        "memory_kib": Path("/proc/meminfo").read_text().splitlines()[0],
        "host": "shared; not the dedicated Phase0 reference host",
        "mongodb_cpu_limit": 2,
        "mongot_cpu_limit": 2,
        "storage": "disposable Docker volumes"
        if os.environ.get("V6_TEST_VOLUME_STORAGE") == "1"
        else "bounded tmpfs",
    }
    (directory / (name + ".json")).write_text(json.dumps(data, indent=2) + "\n")


async def test_measured_bounded_argon2_work_and_policy_reads(managed_agents_db, tmp_path):
    h = managed_agents_db
    passwords = PasswordWork()
    secret = "synthetic-benchmark-password-only"
    encoded = await passwords.hash(secret)
    serial = []
    try:
        for _ in range(12):
            start = perf_counter()
            assert await passwords.verify(encoded, secret)
            serial.append((perf_counter() - start) * 1000)
        active = [asyncio.create_task(passwords.verify(encoded, secret)) for _ in range(4)]
        await asyncio.sleep(0)  # all four tasks reserve real native work slots
        with pytest.raises(AuthBusy):
            await passwords.verify(encoded, secret)
        assert all(await asyncio.gather(*active))
    finally:
        passwords.close()
    # Fill the real organization policy to the reviewed 100-assignment workload.
    from app.security.policy.models import Issue

    existing = await h.db.policy_assignments.count_documents({"org_id": "org-a"})
    for _ in range(max(0, 100 - existing)):
        await h.governance.issue(
            "org-a",
            "owner",
            Issue(
                subject_id="member",
                role="contributor",
                parent_id=h.root,
                expected_parent_generation=h.root_generation,
                tuples=(h.domain[0],),
                expires_at=utc_now() + timedelta(days=1),
            ),
        )
    assignment_count = await h.db.policy_assignments.count_documents({"org_id": "org-a"})
    assert assignment_count == 100
    from app.identity.repository import IdentityStore
    from app.security.policy.engine import PolicyEngine

    class Timings(monitoring.CommandListener):
        def __init__(self):
            self.samples = []

        def started(self, event):
            pass

        def succeeded(self, event):
            self.samples.append(event.duration_micros / 1000)

        def failed(self, event):
            self.samples.append(event.duration_micros / 1000)

    listener = Timings()
    client = AsyncMongoClient(
        os.environ["V6_TEST_MONGODB_URI"], tz_aware=True, event_listeners=[listener]
    )
    measured = PolicyEngine(
        IdentityStore(client[h.db.name]), human_management=True, managed_agents=True
    )
    policy, database = [], []
    try:
        await measured.snapshot("org-a", "member")  # warm connection separately
        for _ in range(30):
            listener.samples.clear()
            start = perf_counter()
            snapshot = await measured.snapshot("org-a", "member")
            assert snapshot["tuples"]
            policy.append((perf_counter() - start) * 1000)
            database.append(sum(listener.samples))
    finally:
        await client.close()
    evidence(
        tmp_path,
        "auth-policy-performance",
        {
            "password": distribution(serial),
            "password_target_ms": [100, 500],
            "argon2id": {
                "memory_kib": 65536,
                "time_cost": 3,
                "parallelism": 4,
                "maximum_concurrent_hashes": 4,
                "queued_hashes": 0,
            },
            "policy": distribution(policy),
            "policy_target_p95_ms": 50,
            "policy_workload": {
                "stored_assignments": assignment_count,
                "grant_shape": "repeated bounded same-resource assignments",
            },
            "database_command_roundtrips": distribution(database),
            "database_measurement": "PyMongo command durations; includes network, not server CPU",
        },
    )


@pytest.mark.parametrize("org_a_tenths", [9, 5], ids=["skewed-90-10", "even-50-50"])
async def test_measured_skewed_events_bounded_real_overview_and_twenty_readers(
    managed_agents_db, tmp_path, org_a_tenths
):
    end = normalize_mongo_timestamp(utc_now())
    begin = end - timedelta(days=31)
    h = managed_agents_db
    # Synthetic installation history precedes the seeded corpus. The actual
    # checkpoint code still validates bounds and prohibits retention widening.
    await h.db["_managed_agents_control"].update_one(
        {"_id": "installation"}, {"$set": {"created_at": begin - timedelta(days=1)}}
    )
    h = await prepare_summary(h, begin=begin, end=end)
    # One million valid fixture-shaped rows, repeated client/project/team IDs.
    # Keep no prompts, bodies or synthetic credential material in this corpus.
    await h.db.agent_activity.delete_many({})
    sample = {
        "org_id": "org-a",
        "identity_version": 2,
        "event_id": "benchmark",
        "client_id": "retired-agent",
        "client_type": "codex",
        "operation": "search_knowledge",
        "tool": "search_knowledge",
        "project_id": "payment-service",
        "status": "succeeded",
        "latency_ms": 3,
        "sponsor_id": "member",
        "sponsor_team_id": "alpha",
        "sponsorship_revision": 1,
        "revision": 1,
    }
    # Start from an actual model so new fields/schema defaults cannot be omitted.
    from app.models.agent import AgentActivityDocument

    with tenant_scope(TenantContext("org-a")):
        template = AgentActivityDocument.model_validate(sample).model_dump(by_alias=True)
    total = int(os.environ.get("V6_PERFORMANCE_EVENTS", "1000000"))
    assert total >= 10000 and total % 10 == 0
    expected = {"org-a": total * org_a_tenths // 10, "org-b": total * (10 - org_a_tenths) // 10}
    start_seed = perf_counter()
    for offset in range(0, total, 1000):
        rows = []
        for index in range(offset, min(total, offset + 1000)):
            at = begin + timedelta(milliseconds=index * (31 * 86400 * 1000) // total)
            rows.append(
                {
                    **template,
                    "_id": ObjectId(),
                    "event_id": f"load-{index}",
                    "org_id": "org-a" if index % 10 < org_a_tenths else "org-b",
                    "created_at": at,
                    "updated_at": at,
                }
            )
        await h.db.agent_activity.insert_many(rows)
    assert {
        org: await h.db.agent_activity.count_documents({"org_id": org}) for org in expected
    } == expected
    seed_seconds = perf_counter() - start_seed
    query = SummaryQuery(start=begin, end=end, scope="org")
    samples, statuses, successful = [], [], []
    async with browser(h, "owner") as (client, headers):

        async def read():
            started = perf_counter()
            response = await client.get(
                "/api/v6/organizations/org-a/overview", params=params(query), headers=headers
            )
            elapsed = (perf_counter() - started) * 1000
            samples.append(elapsed)
            statuses.append(response.status_code)
            if response.status_code == 200:
                successful.append(elapsed)
                # The checkpoint limits retained history; do not mistake total
                # stored rows for the currently served authorized population.
                raw = await h.db.agent_activity.count_documents(
                    {"org_id": "org-a", "created_at": {"$gte": max(begin, h.begin), "$lt": end}}
                )
                assert response.json()["observed_count"] == raw

        await read()  # cold request separate from 20 concurrent readers
        cold = samples.pop()
        cold_status = statuses.pop()
        successful.clear()
        await asyncio.gather(*(read() for _ in range(20)))
    evidence(
        tmp_path,
        f"overview-performance-{org_a_tenths}0-{10 - org_a_tenths}0",
        {
            "corpus_events": total,
            "stored_population": expected,
            "skew": f"{org_a_tenths}0/{10 - org_a_tenths}0",
            "seed_seconds": seed_seconds,
            "concurrency": 20,
            "warm_all_attempts": distribution(samples),
            "warm_successful": distribution(successful) if successful else None,
            "cold_ms": cold,
            "cold_status": cold_status,
            "http_statuses": statuses,
            "target_p95_ms": 1000,
            "window_days": 31,
            "retention": "actual fixture checkpoint; authorized raw count verified",
            "reference_corpus": "events only; other Phase0 corpus sizes are not represented",
        },
    )
    # Refusals are never recorded as fast successful responses. The measurements
    # retain the exact status population for assessment against the target.
    # A one-hour subset is within the shipped 5000-row cap. The current
    # receipt/download policy and CSV renderer perform all actual work.
    from app.summaries.models import MAX_REPORT_ROWS
    from app.summaries.reports import Reports

    narrow = SummaryQuery(start=begin, end=begin + timedelta(hours=1), scope="org")
    with tenant_scope(TenantContext("org-a")):
        reports = Reports(h.engine)
        started = perf_counter()
        receipt = await reports.create("org-a", "owner", narrow)
        csv, metadata = await reports.download("org-a", "owner", receipt["report_id"])
        elapsed = (perf_counter() - started) * 1000
    assert 0 < metadata["observed_count"] <= MAX_REPORT_ROWS
    evidence(
        tmp_path,
        f"report-performance-{org_a_tenths}0-{10 - org_a_tenths}0",
        {
            "rows": metadata["observed_count"],
            "shipped_row_cap": MAX_REPORT_ROWS,
            "bytes": len(csv.encode()),
            "create_and_download_ms": elapsed,
            "target_ms": 30000,
            "lifetime_seconds": 300,
        },
    )
    assert cold_status == 200
    assert successful, "no successful overview in the concurrent workload"
    assert statuses == [200] * 20, "unchanged permitted overview workload must serve every reader"


async def test_measured_sponsor_offboarding_with_five_hundred_verified_clients(
    managed_agents_db, tmp_path
):
    from v6_managed_agent_fixtures import approval

    from app.managed_agents.models import ClientApproval, CredentialIssue
    from app.management.memberships import Memberships
    from app.security.principal import AuthenticationError

    h = managed_agents_db
    prototype = approval(h).model_dump()
    issued = []
    for number in range(500):
        row = await h.agents.approve(
            "org-a",
            "lead",
            ClientApproval.model_validate(
                {
                    **prototype,
                    "client_id": f"benchmark-client-{number:03d}",
                }
            ),
        )
        if number in (0, 499):
            command = CredentialIssue(
                issuance_id=f"benchmark-delivery-{number}",
                expected_client_revision=row["revision"],
                tuples=h.domain,
                expires_at=utc_now() + timedelta(hours=1),
            )
            delivered = await h.agents.issue("org-a", "member", row["client_id"], command)
            issued.append(delivered["token"])
    clients = await h.db.agent_clients.count_documents(
        {"org_id": "org-a", "client_id": {"$regex": "^benchmark-client-"}}
    )
    assert clients == 500
    for token in issued:
        assert (await h.app.state.auth.authenticate(token)).org_id == "org-a"
    member = await h.db.organization_memberships.find_one({"_id": "membership-member"})
    started = perf_counter()
    await Memberships(h.engine).change_status(
        "org-a", "owner", "member", member["revision"], "suspended"
    )
    commit_ms = (perf_counter() - started) * 1000
    for token in issued:
        with pytest.raises(AuthenticationError):
            await h.app.state.auth.authenticate(token)
    evidence(
        tmp_path,
        "offboarding-performance",
        {
            "verified_sponsored_clients": clients,
            "sampled_issued_credentials": len(issued),
            "suspension_commit_ms": commit_ms,
            "target_ms": 1000,
            "post_commit_authentication": "all sampled credentials denied",
            "transport_evidence": "separate active REST/stdio/HTTP MCP integration cases",
        },
    )
