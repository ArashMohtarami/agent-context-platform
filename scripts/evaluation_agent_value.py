"""Explicit additions to an already commissioned owned evaluator; MIT source.

No fixture authority rows, anonymous setup route, provider or default grant change.
The actual empty-store journey still starts through distributed evaluate.sh.
"""

import hashlib
import json
import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts.evaluation_lifecycle import atomic, file_hash, private_path
from scripts.evaluation_operator import agent_value_policy
from scripts.evaluation_recipient import grant
from scripts.focused_process import FocusedError

PROJECT = "evaluation-notes"
OTHER = "evaluation-agent-value-other"
CLIENTS = ("evaluation-handoff-a", "evaluation-handoff-b")


def cli_identity():
    from scripts.evaluation_lifecycle import ROOT

    frozen = json.loads(
        (ROOT / ".private/v8-agent-value-planning/phase-0/cli-help.json").read_text()
    )
    binary = Path(frozen["codex"]["path"]).resolve(strict=True)
    if not binary.is_file():
        raise FocusedError("installed primary CLI is a prerequisite")
    return {"path": str(binary), "sha256": file_hash(binary)}


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def domain(org, client=None, *, writer=True):
    shared = [
        {"action": a, "resource_kind": k, "selector": "include", "ids": [PROJECT, OTHER]}
        for a, k in (
            ("projects:read", "project_directory"),
            ("context:read", "project"),
            ("search:read", "project"),
        )
    ] + [grant("agent:self", "organization_knowledge", org)]
    if writer:
        shared += [grant("context:write", "project", PROJECT)]
    return (
        shared
        + [grant(a, "project", PROJECT) for a in ("memory:read", "memory:write")]
        + [
            {
                "action": a,
                "resource_kind": "client_memory",
                "selector": "include",
                "ids": [client] if client else list(CLIENTS),
            }
            for a in ("memory:read", "memory:write")
        ]
    )


def own(worker):
    return [
        grant(a, "member", worker)
        for a in ("credentials:issue:own", "credentials:revoke:own", "clients:read:own")
    ]


def root_review(evaluation, root, reference):
    from app.identity.models import MANAGEMENT_ACTIONS

    evaluation.load(resume=True)
    org = "eval-" + evaluation.instance
    owner = evaluation.data["members"]["owner"]["user_id"]
    worker = evaluation.data["members"]["worker"]["user_id"]
    policy = {
        "tuples": [r for r in root["tuples"] if r["action"] in MANAGEMENT_ACTIONS],
        "ceiling": domain(org) + own(worker),
        "targets": {"mode": "include", "ids": [worker]},
        "allowed_roles": ["contributor"],
        "expires_at": min(
            datetime.fromisoformat(evaluation.data["expires_at"]),
            datetime.now(UTC) + timedelta(hours=2),
        ).isoformat(),
    }
    review = {
        "version": 1,
        "org_id": org,
        "owner_id": owner,
        "worker_id": worker,
        "projects": [PROJECT, OTHER],
        "clients": list(CLIENTS),
        "approval_reference": reference,
        "policy_sha256": hashlib.sha256(canonical(policy)).hexdigest(),
    }
    agent_value_policy(
        {"org_id": org, "expires_at": evaluation.data["expires_at"]},
        {"schema": 42, "members": evaluation.data["members"]},
        review,
        policy,
    )
    evaluation.stop_traffic()  # actual owned writers stop before operator service call
    atomic(evaluation.state / "intent/agent-value-root.json", policy)
    atomic(evaluation.state / "intent/agent-value-review.json", review)
    for name in ("agent-value-root.json", "agent-value-review.json"):
        relative = "intent/" + name
        evaluation.data["files"][relative] = file_hash(evaluation.state / relative)
    evaluation.checkpoint("agent-value-root-review")
    result = evaluation.maintenance("agent-value-root")
    if not result["approved"]:
        raise FocusedError("explicit operator review refused")
    evaluation.serve(42)
    evaluation.checkpoint("commissioned")
    return result


def enable_local_model(evaluation, cache):
    from benchmarks.retrieval.model import model_identity

    identity = model_identity(cache)  # hashes only; no model load/download
    evaluation.load(resume=True)
    evaluation.stop_traffic()
    target = evaluation.state / "model"
    if target.exists():
        raise FocusedError("explicit model setup is write-once")
    target.mkdir(mode=0o700)
    for name, expected in identity["sha256"].items():
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(identity["snapshot"]) / name, destination)
        if file_hash(destination) != expected:
            raise FocusedError("local model copy changed")
        relative = "model/" + name
        evaluation.data["files"][relative] = expected
    config = json.loads((evaluation.state / "compose.json").read_text())
    api = config["services"]["context-api"]
    api["environment"].update(
        EMBEDDING_ENABLED="true",
        EMBEDDING_MODEL="/evaluation-model",
        EMBEDDING_DEVICE="cpu",
        EMBEDDING_ALLOW_DOWNLOADS="false",
        HF_HUB_OFFLINE="1",
        HF_HUB_DISABLE_TELEMETRY="1",
    )
    api["volumes"].append(f"{target}:/evaluation-model:ro")
    atomic(evaluation.state / "compose.json", config)
    evaluation.data["files"]["compose.json"] = file_hash(evaluation.state / "compose.json")
    evaluation.data["embedding_mode"] = "pinned real local CPU model; offline"
    evaluation.data["embedding_model"] = {
        k: v for k, v in identity.items() if k not in {"cache", "snapshot"}
    }
    evaluation.checkpoint("starting-local-embeddings")
    evaluation.up("context-api", "console")
    evaluation.ready()
    evaluation.checkpoint("commissioned")
    return evaluation.data["embedding_model"]


def issue_client(evaluation, browser, destination, client_id):
    if client_id not in CLIENTS:
        raise FocusedError("exact separately approved handoff client required")
    destination = Path(destination).absolute()
    private_path(destination.parent)
    if destination.resolve() != destination or os.path.lexists(destination):
        raise FocusedError("fresh recipient file required")
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_WRONLY, 0o600)
    try:
        root = "/api/v6/organizations/eval-" + evaluation.instance + "/managed-clients/" + client_id
        inspected = browser.request("GET", root)
        if any(r["status"] == "active" for r in inspected["credentials"]):
            raise FocusedError("existing issuance requires explicit recipient revoke/replacement")
        client = inspected["client"]
        issued = browser.request(
            "POST",
            root + "/credentials",
            {
                "issuance_id": "v8-recipient-" + client_id,
                "expected_client_revision": client["revision"],
                "tuples": client["tuples"],
                "expires_at": client["expires_at"],
            },
        )
        token = issued.pop("token", None)
        if not issued.get("delivered") or not token:
            raise FocusedError("one-time delivery uncertain; no automatic replay")
        with os.fdopen(fd, "w") as output:
            fd = -1
            output.write(token + "\n")
            output.flush()
            os.fsync(output.fileno())
        return {
            "client_id": client_id,
            "key_id": issued["key_id"],
            "revision": issued["revision"],
            "saved_once": True,
        }
    finally:
        if fd != -1:
            os.close(fd)


def selected_activation():
    from scripts.evaluation_lifecycle import ROOT, activation_evidence

    selection = ROOT / ".private/v8-agent-value-planning/phase-3/activation-selection.json"
    private_path(selection, directory=False)
    if selection.stat().st_size > 4096:
        raise FocusedError("bounded explicit activation selection required")
    value = json.loads(selection.read_text())
    if set(value) != {"run"} or not isinstance(value["run"], str):
        raise FocusedError("exact activation selection required")
    folder, hashes = activation_evidence(value["run"])
    return {"path": str(folder), "selection_sha256": file_hash(selection), "evidence": hashes}


def retained_control_values(state):
    """Read only fresh owned secret values, excluding public config placeholders."""
    private_path(state)
    secrets = state / "secrets"
    if {p.name for p in secrets.iterdir()} != {"root", "app", "sync", "keyfile", "mfa", "oidc"}:
        raise FocusedError("unknown owned secret inventory")
    if json.loads((secrets / "oidc").read_text()) != []:
        raise FocusedError("this scenario declares no external identity providers")
    mfa = json.loads((secrets / "mfa").read_text())
    if (
        set(mfa) != {"active", "keys"}
        or not isinstance(mfa["keys"], dict)
        or mfa["active"] not in mfa["keys"]
    ):
        raise FocusedError("unknown owned MFA key format")
    values = [(secrets / name).read_text().strip() for name in ("root", "app", "sync", "keyfile")]
    values += list(mfa["keys"].values())
    values += [(state / "tls" / name).read_text().strip() for name in ("ca.key", "server.key")]
    if any(not isinstance(value, str) or len(value) < 32 for value in values):
        raise FocusedError("unknown or empty owned credential value")
    return values


def audit_artifacts(paths, values):
    """Fixed errors only; never retain or print the values being audited."""
    if not values:
        raise FocusedError("explicit owned credential audit required")
    scanned = 0
    for path in paths:
        if path.is_symlink() or path.stat().st_size > 32 * 1024 * 1024:
            raise FocusedError("unbounded retained metadata refused")
        body = path.read_bytes()
        if any(value and value.encode() in body for value in values):
            raise FocusedError("retained owned credential detected")
        if path.name in {"handoff.jsonl", "auth.json", "reasoning.txt"}:
            raise FocusedError("undeclared retained sensitive artifact")
        scanned += 1
    return {
        "credential_values_absent": True,
        "scanned_files": scanned,
        "reasoning_text": "not produced; no model or provider run",
    }
