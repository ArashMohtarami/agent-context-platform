"""Real service/index evaluation against a caller-supplied disposable fixture.

No deployment settings, credentials or database connections are discovered here.
The selected integration test owns commissioning and cleanup. Timings are the
authenticated service boundary, not HTTP/MCP or vendor-agent timings.
"""

from __future__ import annotations

import asyncio
import os
import platform
import resource
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from app.db.tenant_collection import scoped_collection
from app.embeddings.config import EmbeddingConfiguration
from app.embeddings.local import LocalEmbeddingProvider
from app.repositories.context_repository import MongoProjectContextRepository
from app.repositories.memory_repository import MongoMemoryRepository
from app.repositories.skill_chunk_repository import MongoSkillChunkRepository
from app.repositories.skill_repository import MongoSkillRepository
from app.retrieval.filters import RetrievalFilters
from app.security.principal import AuthenticationError, AuthorizationError
from app.security.tenant import TenantContext, require_tenant, tenant_scope
from app.security.tenant_policy import policy_scope
from app.services.embedding_service import EmbeddingService
from app.services.retrieval_service import RetrievalService
from app.skills.chunker import estimate_tokens
from benchmarks.retrieval.model import load_pinned_model, model_identity
from benchmarks.retrieval.scorer import (
    ARMS,
    Arm,
    Attempt,
    Item,
    Report,
    Stage,
    Status,
    aggregate,
    bm25,
    distribution,
    paired_comparison,
    render_context,
)

ROOT = Path(__file__).resolve().parent


def hardware() -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "logical_cpus": os.cpu_count(),
        "cpu_model": next(
            (
                line.split(":", 1)[1].strip()
                for line in Path("/proc/cpuinfo").read_text().splitlines()
                if line.startswith("model name")
            ),
            "unknown",
        ),
        "memory": Path("/proc/meminfo").read_text().splitlines()[0],
        "load_1_5_15": list(os.getloadavg()),
        "host": "shared; not dedicated reference host",
        "process_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "mongodb_cpus": 2,
        "mongot_cpus": 2,
        "storage": "owned disposable tmpfs",
    }


async def load_model(
    cache: str, settings: dict[str, Any]
) -> tuple[EmbeddingService, dict[str, Any]]:
    import torch

    pinned = model_identity(cache)
    torch.set_num_threads(2)
    model = EmbeddingService(
        LocalEmbeddingProvider(
            EmbeddingConfiguration(
                model=pinned["name"],
                dimensions=384,
                device="cpu",
                batch_size=32,
                max_batch_texts=256,
                max_text_characters=20000,
                normalize=True,
                cache_directory=Path(cache),
                allow_downloads=False,
            ),
            loader=lambda configuration, device: load_pinned_model(
                configuration, device, pinned["snapshot"]
            ),
        )
    )
    started = perf_counter()
    info = await model.start()
    if info.device != settings["model"]["device"] or info.dimensions != 384:
        raise ValueError("loaded model differs from frozen contract")
    return model, {
        **pinned,
        "loaded": asdict(info),
        "load_ms": (perf_counter() - started) * 1000,
        "torch_threads": torch.get_num_threads(),
        "torch": torch.__version__,
    }


class Evaluation:
    def __init__(
        self,
        database: Any,
        auth: Any,
        token: str,
        embedder: EmbeddingService,
        runtime_ids: dict[str, str],
        settings: dict[str, Any],
    ) -> None:
        self.db, self.auth, self.token, self.embedder = database, auth, token, embedder
        self.runtime_ids, self.settings = runtime_ids, settings
        self.snapshots: dict[tuple[str, str, str, str], list[dict[str, str]]] = {}
        self.skill_runtime: dict[tuple[str, str], str] = {}

    def service(self, arm: str) -> RetrievalService:
        return RetrievalService(
            MongoSkillRepository(self.db),
            MongoSkillChunkRepository(self.db),
            self.embedder if arm == "hybrid" else None,
            contexts=lambda project: MongoProjectContextRepository(self.db, project),
            memories=lambda project, client: MongoMemoryRepository(
                self.db, project_id=project, client_id=client, scope="project_agent"
            ),
            clock=lambda: datetime.fromisoformat(self.settings["reference_time"]),
        )

    async def snapshot(
        self, query: dict[str, Any], policy_fingerprint: str
    ) -> list[dict[str, str]]:
        """Read only through the current server-approved search envelope.

        The exporter never receives a foreign corpus and then filters it locally.
        Current authentication is still checked for every Markdown query below.
        """
        key = (query["org_id"], query["project_id"], query["client_id"], policy_fingerprint)
        if key in self.snapshots:
            return self.snapshots[key]
        result: list[dict[str, str]] = []
        for collection, filters in (
            ("project_context", {"project_id": query["project_id"], "status": "active"}),
            (
                "memories",
                {
                    "status": "active",
                    "$or": [
                        {"scope": "global"},
                        {"scope": "project", "project_id": query["project_id"]},
                        {"scope": "agent", "client_id": query["client_id"]},
                        {
                            "scope": "project_agent",
                            "client_id": query["client_id"],
                            "project_id": query["project_id"],
                        },
                    ],
                },
            ),
            ("skill_chunks", {}),
        ):
            rows = (
                await scoped_collection(self.db, collection, require_tenant)
                .find(filters)
                .to_list(200)
            )
            for row in rows:
                record = self.runtime_ids[str(row["_id"])]
                content = row["content"]
                if not isinstance(content, str):
                    raise ValueError("snapshot content must be text")
                result.append(
                    {"identity": record, "content": content, "runtime_id": str(row["_id"])}
                )
        self.snapshots[key] = sorted(result, key=lambda r: r["identity"])
        return self.snapshots[key]

    async def retrieve(self, query: dict[str, Any], arm: str) -> tuple[list[Item], dict[str, Any]]:
        principal = await self.auth.authenticate(self.token)
        if (principal.org_id, principal.client_id) != (query["org_id"], query["client_id"]):
            raise AuthorizationError("benchmark subject mismatch")
        if principal.managed_policy is None:
            raise AuthorizationError("real managed policy required")
        with (
            tenant_scope(TenantContext(principal.org_id, principal.cursor_secret)),
            policy_scope(principal.managed_policy, "search:read"),
        ):
            principal.require_scope("search:read")
            principal.require_project(query["project_id"])
            if arm == "markdown":
                documents = await self.snapshot(query, principal.managed_policy.fingerprint)
                return bm25(query["query"], documents), {"exported_records": len(documents)}
            service = self.service(arm)
            matches = await service.gather_sources(
                query["query"],
                limit=5,
                project_id=query["project_id"],
                memory_client_id=query["client_id"],
                methods=("lexical",) if arm == "lexical" else ("vector", "lexical"),
            )
            context = service.rank_context(
                query["query"],
                matches,
                limit=5,
                token_budget=4000,
                project_id=query["project_id"],
                filters=RetrievalFilters(
                    project_id=query["project_id"], memory_client_id=query["client_id"]
                ),
            )
            result = []
            for item in context.items:
                if item.source == "active_skill":
                    # Real returned chunk keys, not its rank or a matching label.
                    runtime_id = self.skill_runtime[
                        str(item.metadata["skill_id"]), str(item.metadata["chunk_id"])
                    ]
                else:
                    runtime_id = str(
                        item.metadata[
                            "context_id" if item.source == "project_context" else "memory_id"
                        ]
                    )
                result.append(
                    Item(
                        identity=self.runtime_ids[runtime_id],
                        content=item.content,
                        score=item.score,
                        runtime_id=runtime_id,
                    )
                )
            return result, dict(context.diagnostics.as_mapping())

    async def attempt(self, query: dict[str, Any], arm: Arm, stage: Stage, repeat: int) -> Attempt:
        started = perf_counter()
        items: list[Item] = []
        diagnostics: dict[str, Any] = {}
        status: Status = "ok"
        error_code = None
        try:
            async with asyncio.timeout(15):
                items, diagnostics = await self.retrieve(query, arm)
                if estimate_tokens(render_context(items)) > 4000:
                    status, error_code = "incomplete", "COMPLETE_CONTEXT_BUDGET"
        except (AuthenticationError, AuthorizationError):
            status, error_code = "denied", "AUTHORIZATION"
        except TimeoutError:
            status, error_code = "timeout", "DEADLINE"
        except Exception as error:
            status, error_code = "error", type(error).__name__
        return Attempt(
            query_id=query["id"],
            arm=arm,
            stage=stage,
            repeat=repeat,
            status=status,
            error_code=error_code,
            items=items,
            diagnostics=diagnostics,
            latency_ms=(perf_counter() - started) * 1000,
        )


async def run(
    evaluator: Evaluation,
    data: dict[str, Any],
    identities: dict[str, Any],
    setup: dict[str, Any],
    *,
    journal: Path | None = None,
) -> Report:
    queries = data["queries"]["queries"]
    attempts: list[Attempt] = []
    if journal is not None:
        journal.touch(mode=0o600, exist_ok=False)

    def record(attempt: Attempt) -> None:
        attempts.append(attempt)
        if journal is not None:
            with journal.open("a") as output:
                output.write(attempt.model_dump_json() + "\n")

    # First query is first after declared readiness, not cold OS/model caches.
    for arm in ARMS:
        record(await evaluator.attempt(queries[0], arm, "cold", 0))
    for query in queries:
        for arm in ARMS:
            record(await evaluator.attempt(query, arm, "quality", 0))
    for repeat in range(1, 4):
        for query in queries:
            for arm in ARMS:
                record(await evaluator.attempt(query, arm, "warm", repeat))
    summaries: dict[str, Any] = {}
    for split in ("dev", "holdout"):
        selected = [q for q in queries if q["split"] == split]
        summaries[split] = {}
        for arm in ARMS:
            quality = [
                a
                for a in attempts
                if a.stage == "quality"
                and a.arm == arm
                and a.query_id in {q["id"] for q in selected}
            ]
            summary = aggregate(selected, quality)
            summary["categories"] = {
                category: aggregate(
                    [q for q in selected if q["category"] == category],
                    [
                        a
                        for a in quality
                        if a.query_id in {q["id"] for q in selected if q["category"] == category}
                    ],
                )
                for category in sorted({q["category"] for q in selected})
            }
            summary["warm_passes"] = [
                {
                    "repeat": repeat,
                    "latency": distribution(
                        [
                            a.latency_ms
                            for a in attempts
                            if a.stage == "warm"
                            and a.repeat == repeat
                            and a.arm == arm
                            and a.status == "ok"
                            and a.query_id in {q["id"] for q in selected}
                        ]
                    ),
                    "errors": sum(
                        a.status != "ok"
                        for a in attempts
                        if a.stage == "warm"
                        and a.repeat == repeat
                        and a.arm == arm
                        and a.query_id in {q["id"] for q in selected}
                    ),
                }
                for repeat in range(1, 4)
            ]
            summaries[split][arm] = summary
    comparisons: dict[str, Any] = {
        split: paired_comparison([q for q in queries if q["split"] == split], attempts)
        for split in ("dev", "holdout")
    }
    h, m = summaries["holdout"]["hybrid"], summaries["holdout"]["markdown"]
    comparisons["targets"] = {
        "recall_0_80": h["intention_to_run"]["recall_at_5"] >= 0.8,
        "absolute_lift_0_10": comparisons["holdout"]["recall_delta"] >= 0.1,
        "no_unsupported_increase": h["unsupported_rate"] <= m["unsupported_rate"],
        "zero_forbidden_all_stages": not any(score_forbidden(data, a) for a in attempts),
        "all_attempts_successful": all(a.status == "ok" for a in attempts),
    }
    comparisons["blocked_arms"] = [
        arm for arm in ARMS if any(score_forbidden(data, a) for a in attempts if a.arm == arm)
    ]
    return Report(
        version=1,
        identities={**identities, "hardware": hardware()},
        settings=data["settings"],
        setup=setup,
        attempts=attempts,
        summaries=summaries,
        comparisons=comparisons,
        coding_benefit="not measured",
        limitations=[
            "One fictional corpus, one managed org-a subject; not customer/adoption evidence.",
            "Service timings include real auth; no HTTP/TLS/vendor application latency claim.",
            "Readiness probes prefill DB/model caches; cold means first study query only.",
            "Fixed scoring time; actual policy expiry uses the real clock, never frozen.",
            "Markdown export is authorized; live revocation is rechecked per query.",
            "No ranking/threshold tuning; holdout is evaluated, not untouched future tuning data.",
            "No paid provider, model-generated answer or coding-task benefit measured.",
        ],
    )


def score_forbidden(data: dict[str, Any], attempt: Attempt) -> int:
    from benchmarks.retrieval.scorer import score_query

    query = next(q for q in data["queries"]["queries"] if q["id"] == attempt.query_id)
    return int(score_query(query, attempt)["forbidden_items"])
