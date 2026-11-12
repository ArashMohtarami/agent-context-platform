"""Supported empty-store commissioning for the descriptive development pilot.

The separate protocol probe project never enters the two task snapshots. Actual
HTTPS enrollment, independent approval and recipient issuance are mandatory.
Only fresh owned fictional recipient secrets are written to the purpose cache.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import secrets
import shutil
import signal
import tempfile
import time
from pathlib import Path

import httpx

from app.integrations.doctor import mcp_session, tls_context
from scripts import evaluation_agent_value as helpers
from scripts.evaluation_lifecycle import Evaluation, activation_evidence, atomic
from scripts.evaluation_recipient import grant
from scripts.focused_process import Child, FocusedError

from . import calibration, delivery, driver, native, session

CREDENTIALS = driver.ROOT / ".private/agent-value-credentials/mcp"
BASE = driver.ROOT / ".private/v8-agent-value-planning/phase-4/commissions"
PROJECTS = {"harbor": helpers.OTHER, "parcel": helpers.PILOT_OTHER}


def readonly(org, project):
    return [
        grant(a, k, project)
        for a, k in (
            ("projects:read", "project_directory"),
            ("context:read", "project"),
            ("search:read", "project"),
        )
    ] + [grant("agent:self", "organization_knowledge", org)]


def distributed(evaluation, *args):
    # Child.wait always stops owned descendants on failure/signal. The evaluator's
    # output is temporary; only its allowlisted manifests become evidence.
    with tempfile.TemporaryDirectory(prefix="acp-pilot-command-") as folder:
        code = Child(
            [str(driver.ROOT / "scripts/evaluate.sh"), *args, "--instance", evaluation.instance],
            cwd=driver.ROOT,
            env=evaluation.env,
            log=Path(folder) / "command.log",
        ).wait(600)
    if code:
        raise FocusedError("DISTRIBUTED_PILOT_COMMISSION_REFUSED")


def start(activation, cache):
    """Model-free actual commissioning, never called by a provider/test fixture."""
    from tests.integration.test_agent_value_mcp import (
        approve_pair,
        interrupt_resume,
        parent_policy,
        real_protocols,
    )
    from tests.integration.test_evaluation_bootstrap import enroll, login

    from . import pilot

    activation, activation_hashes = activation_evidence(str(activation))
    identity = secrets.token_hex(16)
    evaluation = Evaluation(identity)
    BASE.mkdir(mode=0o700, parents=True, exist_ok=True)
    CREDENTIALS.mkdir(mode=0o700, parents=True, exist_ok=True)
    native.private(BASE, directory=True)
    native.private(CREDENTIALS, directory=True)
    evidence, credentials = BASE / identity, CREDENTIALS / identity
    evidence.mkdir(mode=0o700)
    credentials.mkdir(mode=0o700)
    owner = {"uid": os.getuid(), "instance": identity, "credential_path": str(credentials)}
    atomic(credentials / "owner.json", owner)
    atomic(evidence / "owner.json", owner)
    bundle = {
        "version": 1,
        "kind": "actual-owned-pilot-commission-pending",
        "instance": identity,
        "source_sha256": driver.digest(pilot.sources()),
        "protocols_verified": False,
        "policy_variant": "development-pilot-three-projects-v1",
        "activation": str(activation),
        "activation_hashes": activation_hashes,
        "snapshots": {},
        "stage": "starting",
    }
    atomic(evidence / "commission.json", bundle)
    values = []
    try:
        distributed(
            evaluation,
            "start",
            "--approve-root",
            "fictional-v8-agent-value-pilot-initial",
            "--activation-run",
            str(activation),
            "--api-image",
            "acp-evaluation-api:p04",
            "--console-image",
            "acp-v6-console:phase15",
        )
        evaluation.load()
        interrupt_resume(evaluation)  # genuine distributed SIGTERM and supported resume
        distributed(evaluation, "start")
        _, owner_password, owner_seed = enroll(evaluation, "owner")
        _, worker_password, worker_seed = enroll(evaluation, "worker")
        values.extend([owner_password, owner_seed, worker_password, worker_seed])
        atomic(
            credentials / "recipients.json",
            {
                "owner": {"password": owner_password, "seed": owner_seed},
                "worker": {"password": worker_password, "seed": worker_seed},
            },
        )
        distributed(evaluation, "start")
        evaluation.load()
        owner_browser = login(evaluation, "owner", owner_password, owner_seed)
        base = "/api/v6/organizations/eval-" + identity
        for project in (helpers.PROJECT, helpers.OTHER, helpers.PILOT_OTHER):
            owner_browser.request(
                "POST",
                base + "/knowledge/projects",
                {
                    "project_id": project,
                    "name": "Fictional " + project,
                    "description": "Disposable isolated development snapshot or protocol probe",
                    "repository": "https://example.test/" + project,
                    "environment": "evaluation",
                },
            )
        parent = approve_pair(evaluation, owner_browser, revision=0, extended=False)
        helpers.root_review(
            evaluation, parent, "fictional-v8-agent-value-pilot-policy-v1", pilot=True
        )
        owner_browser = login(evaluation, "owner", owner_password, owner_seed)
        parent = approve_pair(evaluation, owner_browser, revision=1, extended=True, pilot=True)
        worker = login(evaluation, "worker", worker_password, worker_seed)
        broad = [credentials / "commission-a", credentials / "commission-b"]
        receipts = [
            helpers.issue_client(evaluation, worker, file, client)
            for file, client in zip(broad, helpers.CLIENTS, strict=True)
        ]
        tokens = [p.read_text().strip() for p in broad]
        values.extend(tokens)
        model = helpers.enable_local_model(evaluation, cache)

        async def bounded_protocols():
            async with asyncio.timeout(600):
                return await real_protocols(
                    evaluation, tokens, worker, receipts, evidence / "probe-markdown"
                )

        proof = asyncio.run(bounded_protocols())
        bundle.update(stage="probe-boundaries-verified", protocol_proof=proof, model=model)
        atomic(evidence / "commission.json", bundle)

        async def import_snapshots():
            snapshots = {}
            async with httpx.AsyncClient(
                base_url=evaluation.data["origin"],
                verify=tls_context(evaluation.state / "tls/ca.crt"),
                trust_env=False,
                timeout=30,
                headers={"Authorization": "Bearer " + tokens[0]},
            ) as http:
                for task in calibration.TASKS:
                    project = PROJECTS[driver.dataset.task(task)["project_id"]]
                    records = []
                    for request in delivery.import_requests(task, project):
                        response = await http.post("/api/v1/context", json=request)
                        if response.status_code not in {200, 201}:
                            raise FocusedError("ACTUAL_SNAPSHOT_IMPORT_REFUSED")
                        records.append(response.json())
                    snapshots[task] = delivery.freeze(task, project, records)
            return snapshots

        bundle["snapshots"] = asyncio.run(import_snapshots())
        # Revoke the actual broad commissioning credential before fresh narrow
        # delivery. B was already revoked on connected stdio + HTTP sessions.
        worker.request(
            "POST",
            base
            + "/managed-clients/"
            + helpers.CLIENTS[0]
            + "/credentials/"
            + receipts[0]["key_id"]
            + "/revoke",
            {"expected_revision": receipts[0]["revision"]},
        )
        owner_browser = login(evaluation, "owner", owner_password, owner_seed)
        parent = parent_policy(owner_browser, base, evaluation.data["members"]["owner"]["user_id"])
        for client, project in zip(helpers.CLIENTS, PROJECTS.values(), strict=True):
            inspected = owner_browser.request("GET", base + "/managed-clients/" + client)["client"]
            owner_browser.request(
                "POST",
                base + "/managed-clients",
                {
                    "client_id": client,
                    "name": "Read-only isolated development snapshot",
                    "subject_id": evaluation.data["members"]["worker"]["user_id"],
                    "parent_id": parent["_id"],
                    "expected_parent_generation": parent["generation"],
                    "expected_revision": inspected["revision"],
                    "tuples": readonly("eval-" + identity, project),
                    "expires_at": parent["expires_at"],
                },
            )
        worker = login(evaluation, "worker", worker_password, worker_seed)
        narrow = [credentials / "pilot-a", credentials / "pilot-b"]
        keys = [
            helpers.issue_client(evaluation, worker, p, client, pilot=True)
            for p, client in zip(narrow, helpers.CLIENTS, strict=True)
        ]
        values.extend(p.read_text().strip() for p in narrow)
        bundle.update(
            origin=evaluation.data["origin"],
            expires_at=parent["expires_at"],
            clients={
                source: {**key, "token_file": p.name, "project_id": PROJECTS[source]}
                for source, key, p in zip(PROJECTS, keys, narrow, strict=True)
            },
            images=evaluation.data["images"],
            credentials_directory=str(credentials),
            ca_file=str(evaluation.state / "tls/ca.crt"),
        )
        for p in broad:
            p.unlink()
        bundle["kind"] = "actual-owned-pilot-commission"
        atomic(evidence / "commission.json", bundle)
        for task in calibration.TASKS:
            verify_live(bundle, task, protocols=True)
        distributed(evaluation, "stop")
        distributed(evaluation, "start")
        for task in calibration.TASKS:
            verify_live(bundle, task, protocols=True)
        bundle.update(stage="ready", protocols_verified=True)
        values.extend(helpers.retained_control_values(evaluation.state))
        paths = [p for p in evidence.rglob("*") if p.is_file()]
        audit = helpers.audit_artifacts(paths, values)
        atomic(evidence / "retention.json", audit)
        atomic(evidence / "commission.json", bundle)
        return evidence / "commission.json"
    except BaseException:
        bundle["stage"] = "failed-or-interrupted"
        atomic(evidence / "commission.json", bundle)
        # No model exists at commissioning time. Retain the exact manifest and
        # failure; cleanup only this newly owned resource set.
        if evaluation.manifest.exists():
            evaluation.load()
            evaluation.reset()
        remove_credentials(credentials, owner, failed=True)
        atomic(
            evidence / "failure-cleanup.json",
            {
                "owned_platform_removed": not evaluation.state.exists(),
                "recipient_files_removed": True,
            },
        )
        raise


def connection(bundle, task_id, arm):
    if arm != "mcp":
        return {}
    source = driver.dataset.task(task_id)["project_id"]
    record = bundle["clients"][source]
    credential_root = CREDENTIALS / bundle["instance"]
    native.private(credential_root, directory=True)
    return {
        "mcp": {"url": bundle["origin"] + "/mcp", "tools": list(native.TOOLS)},
        "token_file": credential_root / record["token_file"],
        "ca": Path(bundle["ca_file"]),
    }


def verify_live(bundle, task_id, *, protocols=False):
    """Actual complete authorized export + exact project credential, no writes."""
    handoff = importlib.import_module("examples.agent-handoff.smoke")
    evaluation = Evaluation(bundle["instance"])
    evaluation.load(resume=True)
    if driver.read(BASE / bundle["instance"] / "commission.json") != bundle:
        raise FocusedError("COMMISSION_RECEIPT_CHANGED")
    snapshot = bundle["snapshots"][task_id]
    source = driver.dataset.task(task_id)["project_id"]
    record = bundle["clients"][source]
    file = CREDENTIALS / bundle["instance"] / record["token_file"]
    native.private(file)
    token = file.read_text().strip()

    async def check():
        async with httpx.AsyncClient(
            base_url=bundle["origin"],
            verify=tls_context(Path(bundle["ca_file"])),
            trust_env=False,
            timeout=30,
            headers={"Authorization": "Bearer " + token},
        ) as http:
            identity = await http.get("/api/v4/gateway/identity")
            if identity.status_code != 200 or identity.json()["client_id"] != record["client_id"]:
                raise FocusedError("ACTUAL_PILOT_CREDENTIAL_IDENTITY_CHANGED")
            rows = []
            for state in ("active", "archived", "superseded"):
                response = await http.get(
                    "/api/v1/context",
                    params={"project_id": snapshot["project_id"], "status": state, "limit": 50},
                )
                if response.status_code != 200 or response.json().get("next_cursor"):
                    raise FocusedError("ACTUAL_COMPLETE_EXPORT_REQUIRED")
                rows.extend(response.json()["items"])
            if delivery.freeze(task_id, snapshot["project_id"], rows) != snapshot:
                raise FocusedError("ACTUAL_SNAPSHOT_CHANGED")
            denied = await http.get(
                "/api/v1/context/" + bundle["protocol_proof"]["context_id"],
                params={"project_id": helpers.PROJECT},
            )
            if denied.status_code not in {403, 404}:
                raise FocusedError("PILOT_PROBE_PROJECT_BOUNDARY_FAILED")
            if protocols:
                first = next(r for r in snapshot["records"] if r["status"] == "active")
                deadline = time.monotonic() + 120
                while True:
                    response = await http.post(
                        "/api/v1/context/search",
                        json={
                            "project_id": snapshot["project_id"],
                            "query": first["title"],
                            "limit": 10,
                        },
                    )
                    if response.status_code != 200:
                        raise FocusedError("ACTUAL_VECTOR_SEARCH_REFUSED")
                    found = next(
                        (
                            r
                            for r in response.json()["results"]
                            if r["context"]["context_id"] == first["context_id"]
                        ),
                        None,
                    )
                    if found and "vector" in found["methods"]:
                        break
                    if time.monotonic() >= deadline:
                        raise FocusedError("ACTUAL_PROJECT_VECTOR_RESULT_MISSING")
                    await asyncio.sleep(0.5)
                selected = handoff.Selection(
                    origin=bundle["origin"],
                    project_id=snapshot["project_id"],
                    negative_project_id=helpers.PROJECT,
                    run_id=bundle["instance"],
                )
                for transport in ("stdio", "http"):
                    async with mcp_session(
                        selected.connection(transport), http, token, Path(bundle["ca_file"])
                    ) as mcp:
                        await mcp.initialize()
                        await handoff.resolved(mcp, snapshot["project_id"])
                        for row in snapshot["records"]:
                            actual = handoff.data(
                                await handoff.call(
                                    mcp,
                                    "get_context",
                                    {
                                        "project_id": snapshot["project_id"],
                                        "context_id": row["context_id"],
                                    },
                                )
                            )["context"]
                            if {k: actual[k] for k in delivery.FIELDS} != row:
                                raise FocusedError("ACTUAL_M_C_METADATA_OR_CONTENT_MISMATCH")
                        await handoff.indexed(
                            mcp,
                            "search_knowledge",
                            {
                                "project_id": snapshot["project_id"],
                                "sources": ["project_context"],
                                "query": first["title"],
                                "limit": 10,
                                "token_budget": 8000,
                            },
                            importlib.import_module("app.projects.types").context_key(
                                snapshot["project_id"],
                                importlib.import_module("bson").ObjectId(first["context_id"]),
                            ),
                        )

    asyncio.run(check())


def capture_catalogs(bundle, evidence):
    """Construct each actual native catalog; every model request is denied locally."""
    results = {}
    for arm in driver.dataset.ARMS:
        target = evidence / ("catalog-" + arm)
        target.mkdir(mode=0o700)
        task = calibration.TASKS[0]
        verify_live(bundle, task)
        report = session.execute(
            delivery.inputs(task, arm, bundle["snapshots"][task]),
            target,
            capture=True,
            **connection(bundle, task, arm),
        )
        if (
            report["model_requests_forwarded"] != 0
            or not report["catalog"]
            or report["code"] != "CATALOG_CAPTURED"
            or report["account_after"] is None
            or not report["runtime_removed"]
        ):
            raise FocusedError("ACTUAL_MODEL_FREE_CATALOG_CAPTURE_REQUIRED")
        session.gateway.verify_catalog(report["catalog"], mcp=arm == "mcp")
        results[arm] = report["catalog"]["tools_sha256"]
    atomic(evidence / "catalogs.json", results)
    return results


def validate_credentials(credentials, owner, *, failed=False):
    driver.identity(credentials.name)
    if credentials.parent != CREDENTIALS or credentials.resolve() != credentials:
        raise FocusedError("FOREIGN_PILOT_CREDENTIAL_PATH")
    native.private(credentials, directory=True)
    if (
        owner
        != {
            "uid": os.getuid(),
            "instance": credentials.name,
            "credential_path": str(credentials),
        }
        or driver.read(credentials / "owner.json") != owner
    ):
        raise FocusedError("FOREIGN_PILOT_CREDENTIAL_CLEANUP_REFUSED")
    allowed = {"owner.json", "recipients.json", "pilot-a", "pilot-b"}
    if failed:
        allowed |= {"commission-a", "commission-b"}
    if any(
        p.name not in allowed or not p.is_file() or p.is_symlink() or p.stat().st_nlink != 1
        for p in credentials.iterdir()
    ):
        raise FocusedError("FOREIGN_PILOT_CREDENTIAL_ENTRY")


def remove_credentials(credentials, owner, *, failed=False):
    validate_credentials(credentials, owner, failed=failed)
    shutil.rmtree(credentials)


def cleanup(bundle):
    from tests.integration.test_evaluation_bootstrap import login

    driver.identity(bundle["instance"])
    evaluation = Evaluation(bundle["instance"])
    credentials = CREDENTIALS / bundle["instance"]
    native.private(credentials, directory=True)
    owner = driver.read(credentials / "owner.json")
    validate_credentials(credentials, owner)
    if evaluation.manifest.exists():
        evaluation.load()
        actor = driver.read(credentials / "recipients.json")["worker"]
        browser = login(evaluation, "worker", actor["password"], actor["seed"])
        base = "/api/v6/organizations/eval-" + evaluation.instance + "/managed-clients/"
        for row in bundle["clients"].values():
            browser.request(
                "POST",
                base + row["client_id"] + "/credentials/" + row["key_id"] + "/revoke",
                {"expected_revision": row["revision"]},
            )
        evaluation.reset()
    remove_credentials(credentials, owner)
    return {
        "owned_platform_removed": not evaluation.state.exists(),
        "recipient_files_removed": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--activation-run", type=Path, required=True)
    parser.add_argument("--model-cache", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)

    def interrupted(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    try:
        path = start(args.activation_run, args.model_cache)
        print(json.dumps({"commission": str(path)}))
        return 0
    except (FocusedError, OSError, ValueError, KeyError, TypeError, KeyboardInterrupt):
        print(json.dumps({"status": "refused", "code": "PILOT_COMMISSION_INCOMPLETE"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
