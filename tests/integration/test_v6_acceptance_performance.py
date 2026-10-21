"""Measured disposable workloads; declared upper latency budgets are release gates."""

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
        "cpu_model": next(
            (
                line.split(":", 1)[1].strip()
                for line in Path("/proc/cpuinfo").read_text().splitlines()
                if line.startswith("model name")
            ),
            "unreported",
        ),
        "host_load_average_1_5_15_min": list(os.getloadavg()),
        "memory_kib": Path("/proc/meminfo").read_text().splitlines()[0],
        "host": "shared; not the dedicated Phase0 reference host",
        "mongodb_cpu_limit": 2,
        "mongot_cpu_limit": 2,
        "storage": "disposable Docker volumes"
        if os.environ.get("V6_TEST_VOLUME_STORAGE") == "1"
        else "bounded tmpfs",
    }
    (directory / (name + ".json")).write_text(json.dumps(data, indent=2) + "\n")


@pytest.mark.parametrize("policy_shape", ["repeated", "distinct"])
async def test_measured_bounded_argon2_work_and_policy_reads(
    managed_agents_db, tmp_path, policy_shape
):
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

    project_ids = []
    if policy_shape == "distinct":
        from app.identity.models import MANAGEMENT_ACTIONS
        from app.management.memberships import Memberships
        from app.management.models import RootApproval
        from app.models.project import ProjectDocument
        from app.security.policy.models import Grant, Selector

        with tenant_scope(TenantContext("org-a")):
            for number in range(100):
                project = ProjectDocument(
                    project_id=f"policy-performance-{number:03d}",
                    name=f"Fictional project {number}",
                    description="Distinct-resource policy workload",
                    repository="https://example.test/repo",
                    environment="disposable",
                )
                await h.db.projects.insert_one(project.model_dump(by_alias=True))
                project_ids.append(project.project_id)
            root = await h.db.policy_assignments.find_one({"_id": h.root})
            extra = tuple(
                Grant(
                    action="context:read",
                    resource_kind="project",
                    selector="include",
                    ids=project_ids[offset : offset + 50],
                )
                for offset in (0, 50)
            )
            approved = await Memberships(h.engine).operator_approve_root(
                "org-a",
                "owner",
                RootApproval(
                    tuples=tuple(
                        Grant.model_validate(x)
                        for x in root["tuples"]
                        if x["action"] in MANAGEMENT_ACTIONS
                    ),
                    ceiling=tuple(Grant.model_validate(x) for x in root["ceiling"]) + extra,
                    targets=Selector.model_validate(root["targets"]),
                    allowed_roles=tuple(root["allowed_roles"]),
                    expires_at=utc_now() + timedelta(days=5),
                ),
                "fictional-independent-high-cardinality-policy-review",
            )
            h.root_generation = approved["generation"]
    existing = await h.db.policy_assignments.count_documents({"org_id": "org-a"})
    for number in range(max(0, 100 - existing)):
        await h.governance.issue(
            "org-a",
            "owner",
            Issue(
                subject_id="member",
                role="contributor",
                parent_id=h.root,
                expected_parent_generation=h.root_generation,
                tuples=(
                    Grant(
                        action="context:read",
                        resource_kind="project",
                        selector="include",
                        ids=[project_ids[number]],
                    ),
                )
                if project_ids
                else (h.domain[0],),
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
        "auth-policy-performance-" + policy_shape,
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
                "grant_shape": policy_shape,
                "distinct_project_resources": len({i for g in snapshot["tuples"] for i in g.ids}),
            },
            "database_command_roundtrips": distribution(database),
            "database_measurement": "sum of PyMongo command durations; parallel reads overlap; "
            "includes network, not server CPU",
        },
    )
    # A faster hash with the same pinned work parameters is permitted. Reducing
    # the parameters to meet a latency budget is not.
    assert distribution(serial)["p95_ms"] <= 500
    assert distribution(policy)["p95_ms"] <= 50


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
    raw_filter = {"org_id": "org-a", "created_at": {"$gte": max(begin, h.begin), "$lt": end}}
    # Verify the permitted population independently, outside the timed load.
    # Repeating a 900k-row validation count after EACH reader completes would
    # silently add twenty full scans that contend with the remaining readers.
    raw = await h.db.agent_activity.count_documents(raw_filter)
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
                assert response.json()["observed_count"] == raw

        await read()  # cold request separate from 20 concurrent readers
        cold = samples.pop()
        cold_status = statuses.pop()
        successful.clear()
        await asyncio.gather(*(read() for _ in range(20)))
    assert await h.db.agent_activity.count_documents(raw_filter) == raw
    evidence(
        tmp_path,
        f"overview-performance-{org_a_tenths}0-{10 - org_a_tenths}0",
        {
            "corpus_events": total,
            "stored_population": expected,
            "skew": f"{org_a_tenths}0/{10 - org_a_tenths}0",
            "seed_seconds": seed_seconds,
            "concurrency": 20,
            "authenticated_subjects": 1,
            "query_variants": 1,
            "cache_condition": "one cold fill then concurrent identical authorized queries",
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
    # Exercise the complete shipped row cap. Select the exclusive timestamp
    # of row 5001 outside the timed work, then generate all 5000 real rows.
    from app.summaries.models import MAX_REPORT_ROWS
    from app.summaries.reports import Reports

    dates = (
        await h.db.agent_activity.find(raw_filter, {"created_at": 1, "_id": 0})
        .sort("created_at", 1)
        .limit(MAX_REPORT_ROWS + 1)
        .to_list(MAX_REPORT_ROWS + 1)
    )
    assert len(dates) == MAX_REPORT_ROWS + 1
    narrow = SummaryQuery(start=begin, end=dates[-1]["created_at"], scope="org")
    with tenant_scope(TenantContext("org-a")):
        reports = Reports(h.engine)
        started = perf_counter()
        receipt = await reports.create("org-a", "owner", narrow)
        csv, metadata = await reports.download("org-a", "owner", receipt["report_id"])
        elapsed = (perf_counter() - started) * 1000
    assert metadata["observed_count"] == MAX_REPORT_ROWS
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
    assert distribution(successful)["p95_ms"] <= 1000
    assert elapsed <= 30000


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
    assert commit_ms <= 1000


async def test_measured_foreground_directory_during_bounded_real_ingestion(
    managed_agents_db, tmp_path, monkeypatch, performance_embeddings
):
    from contextlib import asynccontextmanager

    from app.services.research_ingestion_service import ResearchIngestionLimiter
    from tests.v6_research_console_fixtures import PROJECT, prepare_research

    h = managed_agents_db
    await prepare_research(h)
    h.app.state.embeddings = performance_embeddings
    member = await h.db.organization_memberships.find_one({"org_id": "org-a", "user_id": "member"})
    user = await h.db.users.find_one({"_id": "member"})
    existing = await h.db.organization_memberships.count_documents({"org_id": "org-a"})
    for number in range(200 - existing):
        identifier = f"contention-person-{number:03d}"
        await h.db.users.insert_one({**user, "_id": identifier, "display_name": "Fictional person"})
        await h.db.organization_memberships.insert_one(
            {
                **member,
                "_id": identifier,
                "user_id": identifier,
                "display_name": "Fictional person",
                "bootstrap_owner": False,
                "bootstrap_intent": None,
            }
        )
    active, peak = 0, 0
    both = asyncio.Event()
    intervals = []
    reserve = ResearchIngestionLimiter.reserve

    @asynccontextmanager
    async def observed(self):
        nonlocal active, peak
        async with reserve(self):
            active += 1
            peak = max(peak, active)
            if active == 2:
                both.set()
            start = perf_counter()
            try:
                yield
            finally:
                intervals.append((start, perf_counter()))
                active -= 1

    monkeypatch.setattr(ResearchIngestionLimiter, "reserve", observed)
    async with (
        browser(h, "owner") as (reader, read_headers),
        browser(h, "member") as (writer, write_headers),
    ):
        base = "/api/v6/organizations/org-a/research"
        for number in range(2):
            response = await writer.post(
                base + "/sources",
                headers=write_headers,
                json={
                    "research_project_id": PROJECT,
                    "source_id": f"contention-{number}",
                    "source_type": "manual",
                    "uri": f"manual:contention-{number}",
                    "title": f"Fictional bounded source {number}",
                    "source_class": "user_provided",
                },
            )
            assert response.status_code == 201, response.text

        async def readers():
            async def read():
                start = perf_counter()
                response = await reader.get(
                    "/api/v6/organizations/org-a/members?limit=20", headers=read_headers
                )
                elapsed = (perf_counter() - start) * 1000
                assert response.status_code == 200, response.text
                assert len(response.json()["items"]) == 20
                return start, elapsed

            return await asyncio.gather(*(read() for _ in range(20)))

        await readers()  # connection warm-up is not part of the distribution
        baseline = await readers()
        tasks = [
            asyncio.create_task(
                writer.post(
                    f"{base}/projects/{PROJECT}/sources/contention-{number}/ingest",
                    headers=write_headers,
                    json={
                        "content": (
                            f"Fictional source {number}. Bounded retries preserve evidence.\n\n"
                            * 1024
                        )[:8192],
                        "content_type": "text/plain",
                    },
                )
            )
            for number in range(2)
        ]
        try:
            started = asyncio.create_task(both.wait())
            try:
                done, _ = await asyncio.wait(
                    [started, *tasks], timeout=10, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done - {started}:
                    response = task.result()
                    assert response.status_code == 200, response.text
                assert both.is_set(), "two real ingestion slots did not overlap"
            finally:
                started.cancel()
                await asyncio.gather(started, return_exceptions=True)
            foreground = await readers()
            results = await asyncio.gather(*tasks)
            for response in results:
                assert response.status_code == 200, response.text
                assert response.json()["job"]["status"] == "completed", response.text
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    normal = distribution([elapsed for _, elapsed in baseline])
    busy = distribution([elapsed for _, elapsed in foreground])
    stored_chunks = {"org_id": "org-a", "source_id": {"$in": ["contention-0", "contention-1"]}}
    chunk_count = await h.db.research_chunks.count_documents(stored_chunks)
    assert chunk_count > 0
    assert await h.db.research_chunks.distinct("embedding_model", stored_chunks) == [
        performance_embeddings.model
    ]
    assert await h.db.research_chunks.distinct("embedding_dimensions", stored_chunks) == [
        performance_embeddings.dimensions
    ]
    overlap = sum(any(begin <= start <= end for begin, end in intervals) for start, _ in foreground)
    evidence(
        tmp_path,
        "ingestion-contention-performance",
        {
            "directory_population": 200,
            "mongodb_pool_minimum": h.db.client.options.pool_options.min_pool_size,
            "mongodb_pool_maximum": h.db.client.options.pool_options.max_pool_size,
            "database_datetime_timezone": str(h.db.codec_options.tzinfo),
            "foreground_readers": 20,
            "page_size": 20,
            "configured_ingestion_slots": 2,
            "observed_peak_ingestions": peak,
            "real_completed_jobs": len(results),
            "submitted_bytes_per_job": 8192,
            "embedding_model": performance_embeddings.model,
            "embedding_dimensions": performance_embeddings.dimensions,
            "embedding_torch_threads": 2,
            "persisted_embedded_chunks": chunk_count,
            "foreground_starts_during_ingestion": overlap,
            "baseline": normal,
            "with_ingestion": busy,
            "maximum_p95_ratio": 1.25,
            "observed_p95_ratio": busy["p95_ms"] / normal["p95_ms"],
            "source": "two bounded local text ingestions through real session/domain/DB adapters; "
            "no provider or authority mock",
        },
    )
    assert peak == 2 and overlap == 20
    assert normal["p95_ms"] <= 500
    assert busy["p95_ms"] <= 500
    assert busy["p95_ms"] <= normal["p95_ms"] * 1.25
