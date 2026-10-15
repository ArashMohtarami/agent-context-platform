"""Explicit installation-operator cutover; no served activation route exists."""

from collections.abc import Sequence
from hashlib import sha256
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from app.db.initializer import initialize_database
from app.db.tenant_inventory import MODEL_BY_COLLECTION, references
from app.db.tenant_schema import INSTALLATION_COLLECTIONS, TENANT_COLLECTIONS
from app.security.tenant import TenantContext, require_tenant, tenant_scope
from app.security.tenant_policy import current_policy

EVIDENCE_MODULES = (
    "test_v6_tenant_authorization",
    "test_v6_tenant_repositories",
    "test_v6_search_isolation",
    "test_v6_tenant_isolation",
    "test_v6_mcp_isolation",
    "test_v6_job_isolation",
)


def _fingerprint(root: Path, paths: Sequence[Path]) -> str:
    digest = sha256()
    for path in sorted(paths):
        digest.update(str(path.relative_to(root)).encode() + b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def source_fingerprint() -> str:
    """Runtime bytes match both a checkout and the production COPY app image."""
    root = Path(__file__).resolve().parents[2]
    return _fingerprint(root, list(root.joinpath("app").rglob("*.py")))


def suite_fingerprint() -> str | None:
    """Test provenance is separate: production images intentionally omit tests."""
    root = Path(__file__).resolve().parents[2]
    if not root.joinpath("tests").is_dir():
        return None
    paths = [
        *root.joinpath("tests").rglob("*.py"),
        *root.joinpath("tests/infrastructure").glob("v6-*"),
        *[
            root / name
            for name in ("pyproject.toml", "docker-compose.yml", ".env.example")
            if root.joinpath(name).is_file()
        ],
    ]
    return _fingerprint(root, paths)


def verify_evidence(paths: Sequence[Path]) -> list[str]:
    """Validate operator-supplied test reports, not a substitute for human review."""
    modules: set[str] = set()
    digests = []
    suites: set[str] = set()
    for path in paths:
        data = path.read_bytes()
        if len(data) > 10_000_000:
            raise ValueError("isolation evidence exceeds the report limit")
        root = ElementTree.fromstring(data)  # noqa: S314 - trusted local operator reports.
        properties = [
            node.get("value")
            for node in root.iter("property")
            if node.get("name") == "v6_source_sha256"
        ]
        if properties != [source_fingerprint()]:
            raise ValueError("isolation evidence does not match this public source tree")
        tested = [
            node.get("value")
            for node in root.iter("property")
            if node.get("name") == "v6_tests_sha256"
        ]
        expected_suite = suite_fingerprint()
        if (
            len(tested) != 1
            or not isinstance(tested[0], str)
            or len(tested[0]) != 64
            or (expected_suite is not None and tested[0] != expected_suite)
        ):
            raise ValueError("isolation evidence does not match the public test source tree")
        suites.add(tested[0])
        cases = list(root.iter("testcase"))
        if not cases or any(
            list(case) and any(child.tag in {"failure", "error", "skipped"} for child in case)
            for case in cases
        ):
            raise ValueError("isolation evidence contains failures, errors, skips or no tests")
        modules.update(case.get("classname", "").split(".")[-1] for case in cases)
        digests.append(sha256(data).hexdigest())
    if len(suites) != 1:
        raise ValueError("isolation reports used different test source trees")
    if set(EVIDENCE_MODULES) - modules:
        raise ValueError("all six focused isolation modules require passing evidence")
    return digests


class TenantIsolationService:
    def __init__(self, database: Any) -> None:
        self.database = database

    def _operator(self) -> str:
        policy = current_policy()
        if policy is not None and not policy.operator:
            raise ValueError("installation operator context is required")
        return require_tenant().org_id

    async def prepare(
        self, *, writers_stopped: bool, worker_schema_versions: Sequence[int]
    ) -> None:
        self._operator()
        if not writers_stopped or list(worker_schema_versions) != [37]:
            raise ValueError("stop all writers and attest only scoped schema-37 workers")
        await initialize_database(self.database, tenant_foundation=True, tenant_isolation=True)

    async def activate(
        self,
        *,
        writers_stopped: bool,
        worker_schema_versions: Sequence[int],
        evidence: Sequence[Path],
    ) -> dict[str, object]:
        org_id = self._operator()
        if not writers_stopped or list(worker_schema_versions) != [37]:
            raise ValueError("mixed or unscoped workers are refused; stop writers for cutover")
        digests = verify_evidence(evidence)
        db = self.database
        control = await db["_tenant_control"].find_one({"_id": "installation"})
        organization = await db["organizations"].find_one({"org_id": org_id})
        if (
            not control
            or control.get("schema_version") != 37
            or control.get("stage") not in {"isolating", "isolated"}
            or not organization
            or organization.get("status") not in {"sealed", "active"}
        ):
            raise ValueError("prepared schema-37 adoption and an explicit sealed org are required")
        if await db["_tenant_quarantine"].count_documents({"status": "unresolved"}):
            raise ValueError("unresolved preserved ownership blocks activation")
        actual = set(await db.list_collection_names())
        unknown = actual - set(TENANT_COLLECTIONS) - set(INSTALLATION_COLLECTIONS)
        if unknown:
            raise ValueError("unclassified collections block activation")
        for collection in TENANT_COLLECTIONS:
            async for row in db[collection].find({}):
                owner = row.get("org_id")
                if (
                    not isinstance(owner, str)
                    or await db["organizations"].count_documents({"org_id": owner}) != 1
                ):
                    raise ValueError("unclassified active ownership blocks activation")
                with tenant_scope(TenantContext(owner)):
                    MODEL_BY_COLLECTION[collection].model_validate(
                        row, context={"from_storage": True}
                    )
                for reference in references(collection, row):
                    if (
                        reference.target not in TENANT_COLLECTIONS
                        or await db[reference.target].count_documents(
                            {"$and": [reference.query, {"org_id": owner}]}
                        )
                        != 1
                    ):
                        raise ValueError("unresolved or foreign references block activation")
        # OS/database operator is the trust boundary. Attestations do not discover
        # other processes: all binaries sharing credentials must be stopped.
        await db["organizations"].update_one({"org_id": org_id}, {"$set": {"status": "active"}})
        await db["_tenant_control"].update_one(
            {"_id": "installation"},
            {
                "$set": {
                    "stage": "isolated",
                    "domain_traffic_enabled": True,
                    "worker_schema_versions": [37],
                    "isolation_evidence_sha256": digests,
                }
            },
        )
        return {"org_id": org_id, "stage": "isolated", "domain_traffic_enabled": True}
