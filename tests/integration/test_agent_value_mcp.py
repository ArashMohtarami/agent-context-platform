"""One real owned installer handoff plus OS boundaries; never a vendor/model run."""

import asyncio
import ctypes
import errno
import hashlib
import importlib
import json
import os
import resource
import signal
import subprocess
import time
from contextlib import AsyncExitStack
from pathlib import Path

import httpx
import pytest
from bson import ObjectId

from app.integrations.doctor import DiagnosisError, mcp_session, tls_context
from app.projects.types import context_key
from scripts.evaluation_agent_value import (
    CLIENTS,
    OTHER,
    PROJECT,
    audit_artifacts,
    cli_identity,
    domain,
    enable_local_model,
    issue_client,
    own,
    retained_control_values,
    root_review,
)
from scripts.evaluation_lifecycle import ROOT, Evaluation, atomic, verify_resource
from scripts.evaluation_recipient import grant
from scripts.focused_process import FocusedError
from tests.integration.test_evaluation_bootstrap import command, enroll, login

pytestmark = pytest.mark.integration
handoff = importlib.import_module("examples.agent-handoff.smoke")
dataset = importlib.import_module("benchmarks.agent-value.dataset")
grading = importlib.import_module("benchmarks.agent-value.grading")


def parent_policy(owner, base, owner_id):
    rows = owner.request("GET", base + "/assignments?subject_id=" + owner_id)["assignments"]
    return next(
        r
        for r in rows
        if r["parent_id"] is None and r["remaining_depth"] > 0 and r["status"] == "active"
    )


def approve_pair(evaluation, owner, *, revision, extended):
    org = "eval-" + evaluation.instance
    base = "/api/v6/organizations/" + org
    owner_id = evaluation.data["members"]["owner"]["user_id"]
    worker_id = evaluation.data["members"]["worker"]["user_id"]
    assert owner_id != worker_id and owner.request("GET", "/api/v6/me")["user_id"] == owner_id
    parent = parent_policy(owner, base, owner_id)
    # Initial approvals deliberately have only the existing installation ceiling.
    initial = (
        [grant("projects:read", "project_directory", PROJECT)]
        + [grant(a, "project", PROJECT) for a in ("context:read", "context:write", "search:read")]
        + [grant("agent:self", "organization_knowledge", org)]
    )
    bounds = domain(org) if extended else initial
    owner.request(
        "POST",
        base + "/assignments",
        {
            "subject_id": worker_id,
            "parent_id": parent["_id"],
            "expected_parent_generation": parent["generation"],
            "role": "contributor",
            "tuples": bounds + own(worker_id),
            "expires_at": parent["expires_at"],
        },
    )
    for index, client in enumerate(CLIENTS):
        owner.request(
            "POST",
            base + "/managed-clients",
            {
                "client_id": client,
                "name": "Fictional separate recipient " + client,
                "subject_id": worker_id,
                "parent_id": parent["_id"],
                "expected_parent_generation": parent["generation"],
                "expected_revision": revision,
                "tuples": domain(org, client, writer=index == 0) if extended else initial,
                "expires_at": parent["expires_at"],
            },
        )
    return parent


def interrupt_resume(evaluation):
    process = subprocess.Popen(  # noqa: S603 -- exact distributed command, own instance
        [str(ROOT / "scripts/evaluate.sh"), "start", "--instance", evaluation.instance],
        env=evaluation.env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 180
        while json.loads(evaluation.manifest.read_text())["stage"] != "checking-search":
            assert process.poll() is None and time.monotonic() < deadline
            time.sleep(0.1)
        process.terminate()
        assert process.wait(timeout=240) != 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
    evaluation.load()
    assert evaluation.data["stage"] == "interrupted"


async def export_equal(http, session, records, destination):
    result = await http.get("/api/v1/context", params={"project_id": PROJECT, "limit": 50})
    assert result.status_code == 200
    body = result.json()
    assert not body.get("next_cursor")
    actual = {r["context_id"]: r for r in body["items"]}
    assert set(actual) == {r["context_id"] for r in records}
    destination.mkdir(mode=0o700)
    for record in records:
        handoff.same_record(actual[record["context_id"]], record)
        fetched = handoff.data(
            await handoff.call(
                session,
                "get_context",
                {
                    "project_id": PROJECT,
                    "context_id": record["context_id"],
                },
            )
        )["context"]
        handoff.same_record(fetched, record)
        # M receives the complete authorized export, exactly matching C's content.
        target = destination / (str(ObjectId(record["context_id"])) + ".md")
        target.write_bytes(actual[record["context_id"]]["content"].encode())
        target.chmod(0o600)
        assert target.read_bytes() == fetched["content"].encode()
    atomic(destination / "records.json", body)
    return hashlib.sha256(
        dataset.canonical(sorted(records, key=lambda r: r["context_id"]))
    ).hexdigest()


async def real_protocols(evaluation, tokens, worker, receipts, markdown):
    selection = handoff.Selection(
        origin=evaluation.data["origin"],
        project_id=PROJECT,
        negative_project_id=OTHER,
        run_id=evaluation.instance,
    )
    ca = evaluation.state / "tls/ca.crt"
    clients = [
        httpx.AsyncClient(
            base_url=selection.origin,
            verify=tls_context(ca),
            trust_env=False,
            timeout=8,
            headers={"Authorization": "Bearer " + token},
        )
        for token in tokens
    ]
    async with clients[0] as a_http, clients[1] as b_http:
        identities = []
        for http in clients:
            result = await http.get("/api/v4/gateway/identity")
            assert result.status_code == 200
            identities.append(result.json())
        assert [r["client_id"] for r in identities] == list(CLIENTS)
        assert identities[0]["org_id"] == identities[1]["org_id"] == "eval-" + evaluation.instance
        selected = dataset.task("parcel-development-tracking")
        snapshot = dataset.snapshot(selected)
        markdown.mkdir(mode=0o700)
        records = []
        async with mcp_session(selection.connection("stdio"), a_http, tokens[0], ca) as a:
            await a.initialize()
            assert (await a.list_tools()).tools
            await handoff.resolved(a, PROJECT)
            for row in snapshot:
                assert row["revision"] == 1  # selected complete ordinary development snapshot
                content = row["content"]
                assert (
                    content == row["content"]
                    and hashlib.sha256(content.encode()).hexdigest() == row["sha256"]
                )
                requested = {
                    "project_id": PROJECT,
                    "type": "convention",
                    "title": row["record_id"],
                    "content": content,
                    "source": row["source"],
                    "importance": 0.5,
                    "status": row["status"],
                    "tags": ["v8-development"],
                    "external_id": row["record_id"],
                }
                # The existing public ContentText contract trims outer whitespace.
                # Only this protocol fixture uses canonical authorized exports for M/C;
                # sealed primary task inputs and their hashes are not changed.
                requested = handoff.SaveContextArguments.model_validate(requested).model_dump(
                    exclude_none=True
                )
                saved = handoff.data(await handoff.call(a, "save_context", requested))["context"]
                handoff.validate_saved(saved, requested)
                assert saved["revision"] == row["revision"] and saved["status"] == row["status"]
                handoff.same_record(
                    handoff.data(await handoff.call(a, "save_context", requested))["context"], saved
                )
                records.append(saved)
            request = {
                "project_id": PROJECT,
                "type": "convention",
                "title": "Fictional parcel handoff",
                "content": (
                    "Parcel retry receipts preserve the original tracking identifier "
                    "across duplicate submissions."
                ),
                "source": "fictional-v8-handoff",
                "importance": 0.5,
                "status": "active",
                "tags": ["v8-handoff"],
                "external_id": "v8-handoff-fact",
            }
            fact = handoff.data(await handoff.call(a, "save_context", request))["context"]
            handoff.same_record(
                handoff.data(
                    await handoff.call(
                        a, "save_context", {**request, "content": "must not overwrite"}
                    )
                )["context"],
                fact,
            )
            memory_args = {
                "scope": "project_agent",
                "project_id": PROJECT,
                "type": "handoff-probe",
                "content": "Fictional private parcel sentinel A",
                "importance": 0.1,
                "confidence": 1,
                "source": "fictional-v8-handoff",
                "external_id": "private-a",
            }
            memory = handoff.data(await handoff.call(a, "save_memory", memory_args))["memory"]
            own_read = await a_http.get(
                "/api/v1/memory/" + memory["memory_id"],
                params={"scope": "project_agent", "project_id": PROJECT},
            )
            assert (
                own_read.status_code == 200 and own_read.json()["content"] == memory_args["content"]
            )
        revoked = False
        try:
            async with AsyncExitStack() as stack:
                b = await stack.enter_async_context(
                    mcp_session(selection.connection("http"), b_http, tokens[1], ca)
                )
                bs = await stack.enter_async_context(
                    mcp_session(selection.connection("stdio"), b_http, tokens[1], ca)
                )
                for session in (b, bs):
                    await session.initialize()
                    assert (await session.list_tools()).tools
                    await handoff.resolved(session, PROJECT)
                # Complete permitted M bytes, all record identities/revisions and C reads are equal.
                # The separate handoff fact was created only after the snapshot records.
                snapshot_rows = await b_http.get(
                    "/api/v1/context", params={"project_id": PROJECT, "limit": 50}
                )
                assert snapshot_rows.status_code == 200
                assert {r["external_id"] for r in snapshot_rows.json()["items"]} == {
                    r["record_id"] for r in snapshot
                } | {"v8-handoff-fact"}
                export_hash = await export_equal(b_http, b, [*records, fact], markdown / "initial")
                key = context_key(PROJECT, ObjectId(fact["context_id"]))
                query = {
                    "query": "duplicate parcel tracking receipt",
                    "project_id": PROJECT,
                    "sources": ["project_context"],
                    "limit": 10,
                    "token_budget": 8000,
                }
                await handoff.indexed(b, "search_knowledge", query, key)
                # Check actual real-vector retrieval, not merely a keyword/direct-read success.
                deadline = time.monotonic() + 120
                while True:
                    search = await b_http.post(
                        "/api/v1/context/search",
                        json={
                            "project_id": PROJECT,
                            "query": "duplicate parcel tracking receipt",
                            "limit": 10,
                        },
                    )
                    assert search.status_code == 200
                    found = next(
                        (
                            r
                            for r in search.json()["results"]
                            if r["context"]["context_id"] == fact["context_id"]
                        ),
                        None,
                    )
                    if found and "vector" in found["methods"]:
                        break
                    assert time.monotonic() < deadline, "real model vector result missing"
                    await asyncio.sleep(0.5)
                await handoff.resolved(b, OTHER)
                negative = handoff.data(
                    await handoff.call(b, "search_knowledge", {**query, "project_id": OTHER})
                )
                assert all(row.get("key") != key for row in negative["results"])
                await handoff.refused(
                    b,
                    "get_context",
                    {"project_id": OTHER, "context_id": fact["context_id"]},
                    {"NOT_FOUND"},
                )
                await handoff.refused(
                    b, "save_context", {**request, "external_id": "b-cannot-write"}, {"FORBIDDEN"}
                )
                b_memory = handoff.data(
                    await handoff.call(
                        b,
                        "save_memory",
                        {
                            **memory_args,
                            "content": "Fictional private parcel sentinel B",
                            "external_id": "private-b",
                        },
                    )
                )["memory"]
                private = await handoff.indexed(
                    b,
                    "search_memory",
                    {
                        "query": "Fictional private parcel sentinel",
                        "scopes": ["project_agent"],
                        "project_id": PROJECT,
                        "types": ["handoff-probe"],
                        "limit": 10,
                    },
                    "memory:" + b_memory["memory_id"],
                )
                assert all(
                    r.get("key") != "memory:" + memory["memory_id"]
                    and r.get("content") != memory_args["content"]
                    for r in private["results"]
                )
                denial = await b_http.get(
                    "/api/v1/memory/" + memory["memory_id"],
                    params={"scope": "project_agent", "project_id": PROJECT},
                )
                assert denial.status_code == 404
                await handoff.refused(
                    b,
                    "save_memory",
                    {
                        **memory_args,
                        "external_id": "cannot-supersede-a",
                        "supersedes": memory["memory_id"],
                    },
                    {"NOT_FOUND", "FORBIDDEN"},
                )
                updated = await a_http.patch(
                    "/api/v1/context/" + fact["context_id"],
                    params={"project_id": PROJECT},
                    json={
                        "expected_revision": fact["revision"],
                        "content": request["content"]
                        + " The receipt revision is checked before every update.",
                    },
                )
                assert updated.status_code == 200
                revised = updated.json()
                assert (
                    revised["context_id"] == fact["context_id"]
                    and revised["revision"] == fact["revision"] + 1
                )
                stale = await a_http.patch(
                    "/api/v1/context/" + fact["context_id"],
                    params={"project_id": PROJECT},
                    json={
                        "expected_revision": fact["revision"],
                        "content": "stale must not overwrite",
                    },
                )
                assert stale.status_code == 409
                final_hash = await export_equal(
                    b_http, bs, [*records, revised], markdown / "revised"
                )
                status = evaluation.maintenance("agent-value-readiness")
                assert all(status["real_model_vectors"].values())
                for name in ("project_context", "memories"):
                    assert len(status["indexes"][name]) >= 2
                    assert all(
                        r["status"] == "READY" and r["queryable"] for r in status["indexes"][name]
                    )
                base = (
                    "/api/v6/organizations/eval-"
                    + evaluation.instance
                    + "/managed-clients/"
                    + CLIENTS[1]
                )
                worker.request(
                    "POST",
                    base + "/credentials/" + receipts[1]["key_id"] + "/revoke",
                    {"expected_revision": receipts[1]["revision"]},
                )
                for session in (b, bs):
                    await handoff.refused(
                        session,
                        "get_context",
                        {"project_id": PROJECT, "context_id": revised["context_id"]},
                        {"UNAUTHENTICATED", "FORBIDDEN"},
                    )
                revoked = True
        except DiagnosisError as error:
            if not revoked or error.code not in {"UNAUTHENTICATED", "FORBIDDEN"}:
                raise
        assert revoked
        assert (
            await a_http.get(
                "/api/v1/context/" + revised["context_id"], params={"project_id": PROJECT}
            )
        ).status_code == 200
        return {
            "distinct_clients": True,
            "transports": ["stdio", "streamable-http"],
            "snapshot_records": len(snapshot),
            "snapshot_and_fact_export_sha256": export_hash,
            "revised_export_sha256": final_hash,
            "context_id": revised["context_id"],
            "revision": revised["revision"],
            "content_sha256": hashlib.sha256(revised["content"].encode()).hexdigest(),
            "ready": status,
            "connected_b_revoked_both_transports": revoked,
            "vendor_agent_mcp_use": "unmeasured; generic protocol clients only",
        }


def test_empty_store_two_distinct_clients_handoff_and_owned_cleanup(tmp_path):
    evaluation = Evaluation(os.environ["ACP_EVALUATION_ACCEPTANCE_INSTANCE"])
    evidence = os.environ["ACP_EVALUATION_ACTIVATION_RUN"]
    report = Path(os.environ["ACP_FOCUSED_LIFECYCLE_REPORT"])
    assert not evaluation.state.exists()
    started = time.monotonic()
    files = [tmp_path / "recipient-a", tmp_path / "recipient-b"]
    sensitive = []
    cleaned = False
    try:
        result = subprocess.run(  # noqa: S603 -- distributed maintained command, exact empty owned ID
            [
                str(ROOT / "scripts/evaluate.sh"),
                "start",
                "--instance",
                evaluation.instance,
                "--approve-root",
                "fictional-v8-agent-value-initial",
                "--activation-run",
                evidence,
                "--api-image",
                "acp-evaluation-api:p04",
                "--console-image",
                "acp-v6-console:phase15",
            ],
            env=evaluation.env,
            capture_output=True,
            text=True,
            timeout=600,
        )
        assert result.returncode == 0, result.stderr
        evaluation.load()
        assert evaluation.data["stage"] == "awaiting-enrollment"
        interrupt_resume(evaluation)
        command(evaluation, "start")
        _, owner_password, owner_seed = enroll(evaluation, "owner")
        _, worker_password, worker_seed = enroll(evaluation, "worker")
        sensitive.extend([owner_password, owner_seed, worker_password, worker_seed])
        command(evaluation, "start")
        evaluation.load()
        assert evaluation.data["stage"] == "commissioned"
        owner = login(evaluation, "owner", owner_password, owner_seed)
        base = "/api/v6/organizations/eval-" + evaluation.instance
        for project in (PROJECT, OTHER):
            owner.request(
                "POST",
                base + "/knowledge/projects",
                {
                    "project_id": project,
                    "name": "Fictional " + project,
                    "description": "Disposable V8 boundary",
                    "repository": "https://example.test/" + project,
                    "environment": "evaluation",
                },
            )
        parent = approve_pair(evaluation, owner, revision=0, extended=False)
        root_review(evaluation, parent, "fictional-v8-agent-value-memory-review")
        owner = login(evaluation, "owner", owner_password, owner_seed)
        parent = approve_pair(evaluation, owner, revision=1, extended=True)
        generation = parent["generation"]
        with pytest.raises(FocusedError, match="HTTP 403"):
            owner.request(
                "POST",
                base + "/managed-clients/" + CLIENTS[0] + "/credentials",
                {
                    "issuance_id": "owner-must-not-receive",
                    "expected_client_revision": 2,
                    "tuples": [],
                    "expires_at": parent["expires_at"],
                },
            )
        worker = login(evaluation, "worker", worker_password, worker_seed)
        with pytest.raises(FocusedError, match="HTTP 403"):
            worker.request(
                "POST",
                base + "/managed-clients",
                {
                    "client_id": CLIENTS[0],
                    "name": "self-approval denied",
                    "expected_revision": 2,
                    "subject_id": evaluation.data["members"]["worker"]["user_id"],
                    "parent_id": parent["_id"],
                    "expected_parent_generation": generation,
                    "tuples": domain("eval-" + evaluation.instance, CLIENTS[0]),
                    "expires_at": parent["expires_at"],
                },
            )
        receipts = [
            issue_client(evaluation, worker, path, client)
            for path, client in zip(files, CLIENTS, strict=True)
        ]
        tokens = [path.read_text().strip() for path in files]
        sensitive.extend(tokens)
        assert tokens[0] != tokens[1] and all(path.stat().st_mode & 0o077 == 0 for path in files)
        inspected = worker.request("GET", base + "/managed-clients/" + CLIENTS[0])["client"]
        replay = worker.request(
            "POST",
            base + "/managed-clients/" + CLIENTS[0] + "/credentials",
            {
                "issuance_id": "v8-recipient-" + CLIENTS[0],
                "expected_client_revision": inspected["revision"],
                "tuples": inspected["tuples"],
                "expires_at": inspected["expires_at"],
            },
        )
        assert not replay["delivered"] and "token" not in replay
        model = enable_local_model(evaluation, os.environ["ACP_FOCUSED_MODEL_CACHE"])

        async def bounded():
            async with asyncio.timeout(600):
                return await real_protocols(
                    evaluation, tokens, worker, receipts, tmp_path / "markdown"
                )

        proof = asyncio.run(bounded())
        command(evaluation, "doctor")
        command(evaluation, "stop")
        command(evaluation, "start")
        evaluation.load()
        assert evaluation.data["stage"] == "commissioned"
        owner = login(evaluation, "owner", owner_password, owner_seed)
        assert (
            parent_policy(owner, base, evaluation.data["members"]["owner"]["user_id"])["generation"]
            == generation
        )

        async def persisted():
            async with httpx.AsyncClient(
                base_url=evaluation.data["origin"],
                verify=tls_context(evaluation.state / "tls/ca.crt"),
                trust_env=False,
                timeout=8,
            ) as http:
                found = await http.get(
                    "/api/v1/context/" + proof["context_id"],
                    params={"project_id": PROJECT},
                    headers={"Authorization": "Bearer " + tokens[0]},
                )
                assert found.status_code == 200 and found.json()["revision"] == proof["revision"]
                assert (
                    hashlib.sha256(found.json()["content"].encode()).hexdigest()
                    == proof["content_sha256"]
                )
                denied = await http.get(
                    "/api/v1/projects", headers={"Authorization": "Bearer " + tokens[1]}
                )
                assert denied.status_code in {401, 403}

        asyncio.run(persisted())
        # Read-only inspection of a foreign resource: refusal before any mutation.
        foreign = evaluation.inspect("network", "bridge")
        with pytest.raises(FocusedError):
            verify_resource("network", foreign, "bridge", evaluation.instance)
        proof.update(
            seconds=round(time.monotonic() - started, 3),
            model=model,
            human_effort=(
                "unmeasured; automated fictional recipients with real separate identities/MFA"
            ),
            provider_calls=0,
            signal_checkpoint="interrupted; genuine resume completed",
            root_generation_preserved=True,
            foreign_cleanup_refused=True,
            retention="separate successful audit receipt required",
        )
        atomic(report / "handoff-proof.json", proof)
    finally:
        # Recipient files belong to recipients, outside evaluator reset ownership.
        for path in files:
            path.unlink(missing_ok=True)
        try:
            if evaluation.state.exists():
                try:
                    evaluation.archive_diagnostics(report / "owned-diagnostics")
                    sensitive.extend(retained_control_values(evaluation.state))
                    retention = audit_artifacts(
                        [p for p in report.rglob("*") if p.is_file()], sensitive
                    )
                    atomic(report / "retention-proof.json", retention)
                finally:
                    names = evaluation.names
                    command(evaluation, "reset")
                    assert not evaluation.state.exists()
                    evaluation.state = tmp_path
                    (tmp_path / "logs").mkdir(exist_ok=True)
                    for kind, items in names.items():
                        assert all(evaluation.inspect(kind, item) is None for item in items)
                    cleaned = True
        finally:
            atomic(
                report / "cleanup-proof.json",
                {
                    "owned_resources_absent": cleaned,
                    "recipient_files_absent": all(not p.exists() for p in files),
                },
            )


def network_filter(output):
    """Native filter for trusted model-free CLI metadata; threads allowed, sockets denied."""
    lib = ctypes.CDLL("libseccomp.so.2")
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_rule_add_array.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint,
        ctypes.c_void_p,
    ]
    lib.seccomp_export_bpf.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    context = lib.seccomp_init(0x7FFF0000)
    assert context
    try:
        for name in grading.DENIED_SYSCALLS:
            if name in {
                "clone",
                "clone3",
                "fork",
                "vfork",
                "kill",
                "tkill",
                "tgkill",
                "socketpair",
            }:
                continue
            number = lib.seccomp_syscall_resolve_name(name.encode())
            if number >= 0:
                assert (
                    lib.seccomp_rule_add_array(context, 0x00050000 | errno.EPERM, number, 0, None)
                    == 0
                )
        assert lib.seccomp_export_bpf(context, output.fileno()) == 0
        output.seek(0)
    finally:
        lib.seccomp_release(context)


def native_limits():
    for kind, cap in (
        (resource.RLIMIT_CPU, 10),
        (resource.RLIMIT_AS, 1073741824),
        (resource.RLIMIT_FSIZE, 1048576),
        (resource.RLIMIT_NOFILE, 64),
        (resource.RLIMIT_CORE, 0),
    ):
        resource.setrlimit(kind, (cap, cap))


def native_inventory(tmp_path):
    binary = cli_identity()
    home = tmp_path / "vendor"
    (home / "home/.codex").mkdir(parents=True)
    commands = [
        ["--version"],
        ["mcp", "add", "--help"],
        ["mcp", "list", "--help"],
        ["mcp", "get", "--help"],
        ["mcp", "add", "v8-model-free", "--url", "https://127.0.0.1:9/mcp"],
        ["mcp", "list", "--json"],
        ["mcp", "get", "v8-model-free", "--json"],
    ]
    rows = []
    with (tmp_path / "filter").open("w+b") as policy:
        network_filter(policy)
        prefix = [
            "/usr/bin/bwrap",
            "--unshare-all",
            "--unshare-user",
            "--share-net",
            "--die-with-parent",
            "--new-session",
            "--disable-userns",
            "--cap-drop",
            "ALL",
            "--clearenv",
            "--ro-bind",
            "/usr",
            "/usr",
            "--ro-bind",
            "/lib",
            "/lib",
            "--ro-bind",
            "/lib64",
            "/lib64",
            "--ro-bind",
            binary["path"],
            "/vendor/codex",
            "--bind",
            str(home),
            "/work",
            "--size",
            "67108864",
            "--tmpfs",
            "/tmp",  # noqa: S108 -- private bounded tmpfs inside sandbox
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--setenv",
            "HOME",
            "/work/home",
            "--setenv",
            "CODEX_HOME",
            "/work/home/.codex",
            "--setenv",
            "XDG_CONFIG_HOME",
            "/work/home/.config",
            "--setenv",
            "PATH",
            "/usr/bin:/bin",
            "--remount-ro",
            "/",
            "--chdir",
            "/work",
            "--seccomp",
            str(policy.fileno()),
            "/vendor/codex",
            "--no-daemon",
        ]
        for index, arguments in enumerate(commands):
            policy.seek(0)  # bwrap consumes the inherited filter descriptor each time
            with (
                (tmp_path / f"vendor-{index}.out").open("w+b") as stdout,
                (tmp_path / f"vendor-{index}.err").open("w+b") as stderr,
            ):
                process = subprocess.Popen(  # noqa: S603 -- fixed model-free commands, denied sockets
                    prefix + arguments,
                    stdout=stdout,
                    stderr=stderr,
                    stdin=subprocess.DEVNULL,
                    env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
                    pass_fds=(policy.fileno(),),
                    start_new_session=True,
                    preexec_fn=native_limits,
                )
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    raise AssertionError("bounded model-free inventory timed out") from None
                stdout.seek(0)
                stderr.seek(0)
                text, error = stdout.read(1048577).decode(), stderr.read(1048577).decode()
                assert process.returncode == 0, {
                    "argv": arguments,
                    "exit": process.returncode,
                    "stderr": error,
                }
                assert len(text) <= 1048576
                rows.append(
                    {"argv": arguments, "exit": process.returncode, "stdout": text, "stderr": error}
                )
    listed = json.loads(rows[-2]["stdout"])
    inspected = json.loads(rows[-1]["stdout"])
    assert (
        len(listed) == 1
        and listed[0]["name"] == "v8-model-free"
        and inspected["name"] == "v8-model-free"
    )
    return {
        "binary": binary,
        "commands": rows,
        "network": (
            "socket/connect denied; anonymous socketpairs allowed only for "
            "trusted CLI signal runtime; no mounted host sockets"
        ),
        "vendor_agent_mcp_use": "unmeasured; add/list/get inspect configuration only",
        "generic_protocol_discovery": "separate commissioning test initializes actual MCP tools",
    }


def test_real_sandbox_denies_host_grader_foreign_workspace_and_secret_access(tmp_path):
    report = Path(os.environ["ACP_FOCUSED_LIFECYCLE_REPORT"])
    controls = tmp_path / "host-controls"
    controls.mkdir()
    secret = controls / "provider-control-secret"
    secret.write_text("fictional-throwaway-provider-control-secret")
    sibling = controls / "sibling-workspace"
    sibling.mkdir()
    (sibling / "fact.txt").write_text("foreign control")
    implementation = tmp_path / "implementation"
    implementation.mkdir()
    paths = [
        str(secret),
        str(sibling),
        str(ROOT / ".private/v8-agent-value-planning/phase-1/hidden"),
        str(ROOT / ".git"),
        "/var/run/docker.sock",
        "/proc/1/root",
        "/home",
    ]
    # This is task-controlled code executed under the actual immutable grader sandbox.
    # Only probe paths enter it; fake secret bytes and hidden labels stay in the controller.
    (implementation / "api.py").write_text(
        """import os, socket
def handle(request):
    denied = {}
    for name in request["paths"]:
        try:
            with open(name, "rb") as stream: stream.read(1)
        except OSError: denied[name] = not os.path.exists(name)
        else: denied[name] = False
    sockets = []
    for family in (socket.AF_INET, socket.AF_INET6, socket.AF_UNIX):
        try: socket.socket(family).close()
        except PermissionError: sockets.append(True)
        else: sockets.append(False)
    try: os.fork()
    except PermissionError: fork = True
    else: raise RuntimeError("fork allowed")
    try: open("/work/escape", "w").write("bad")
    except OSError: readonly = True
    else: readonly = False
    clean = not any("KEY" in k or "TOKEN" in k or "SECRET" in k for k in os.environ)
    return {"paths": denied, "sockets": sockets, "fork": fork,
            "readonly": readonly, "env_clean": clean}
"""
    )
    result = grading.sandbox(
        implementation, [{"case_id": "actual-os-boundary", "input": {"paths": paths}}]
    )
    assert result["status"] == "completed"
    observed = result["rows"][0]["result"]
    assert all(observed["paths"].values()) and observed["sockets"] == [True] * 3
    assert observed["fork"] and observed["readonly"] and observed["env_clean"]
    atomic(
        report / "sandbox-proof.json",
        {
            "actual_os_probe": observed,
            "namespace": (
                "separate mount/PID/user; host network namespace retained, all sockets denied"
            ),
            "scope": (
                "model-free untrusted task tools; paid vendor credential broker remains blocked"
            ),
        },
    )
    atomic(report / "vendor-inventory.json", native_inventory(tmp_path))
