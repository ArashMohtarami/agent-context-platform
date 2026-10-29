"""Deterministic record-level scoring. No database, provider or agent execution."""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.skills.chunker import estimate_tokens

ROOT = Path(__file__).resolve().parent
type Arm = Literal["markdown", "lexical", "hybrid"]
type Stage = Literal["quality", "cold", "warm"]
type Status = Literal["ok", "denied", "timeout", "error", "incomplete"]
ARMS: tuple[Arm, ...] = ("markdown", "lexical", "hybrid")


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    identity: str
    content: str
    score: float
    runtime_id: str


class Attempt(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    query_id: str
    arm: Arm
    stage: Stage
    repeat: int = Field(ge=0, le=3)
    status: Status
    error_code: str | None = None
    latency_ms: float = Field(ge=0)
    items: list[Item] = Field(max_length=50)
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class Report(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    version: Literal[1]
    identities: dict[str, Any]
    settings: dict[str, Any]
    setup: dict[str, Any]
    attempts: list[Attempt]
    summaries: dict[str, Any]
    comparisons: dict[str, Any]
    coding_benefit: Literal["not measured"]
    limitations: list[str]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity(record: dict[str, Any]) -> str:
    return "|".join(str(record[k]) for k in ("org_id", "source", "id", "revision"))


def render_context(items: list[Item]) -> str:
    """Complete data-only context: identity/provenance framing plus whole bodies."""
    return "\n\n".join(f"[{item.identity}]\n{item.content}" for item in items)


def load_dataset(root: Path = ROOT) -> dict[str, Any]:
    seal = json.loads((root / "freeze.json").read_text())
    for name, expected in seal["sha256"].items():
        path = root / name
        if path.resolve() != path.absolute() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("dataset path escaped frozen root")
        if digest(path) != expected:
            raise ValueError("frozen input changed: " + name)
    data = {
        name: json.loads((root / f"{name}.json").read_text())
        for name in ("corpus", "queries", "split", "settings")
    }
    records, queries = data["corpus"]["records"], data["queries"]["queries"]
    by_id = {identity(r): r for r in records}
    if len(by_id) != len(records) or not 1 <= len(records) <= 200:
        raise ValueError("duplicate or unbounded corpus")
    if len(queries) != 60 or len({q["id"] for q in queries}) != 60:
        raise ValueError("exactly sixty unique queries required")
    families = data["split"]["families"]
    if set(families["dev"]) & set(families["holdout"]):
        raise ValueError("family split leakage")
    tasks = json.loads((root / "tasks.json").read_text())["tasks"]
    if len(tasks) != 12 or any(t["family"] in families["holdout"] for t in tasks):
        raise ValueError("coding tasks reuse retrieval holdout families")
    expected_categories = dict(
        exact=4, conceptual=4, decision=4, conflicting=4, no_support=2, forbidden=2
    )
    for split, multiplier in (("dev", 1), ("holdout", 2)):
        selected = [q for q in queries if q["split"] == split]
        if Counter(q["category"] for q in selected) != {
            k: v * multiplier for k, v in expected_categories.items()
        }:
            raise ValueError("category denominator changed")
        if set(q["family"] for q in selected) != set(families[split]):
            raise ValueError("split manifest disagrees with labels")
    for q in queries:
        if bool(q["relevant"]) != q["answerable"]:
            raise ValueError("answerability disagrees with labels")
        permitted = {
            identity(r)
            for r in records
            if r["org_id"] == q["org_id"]
            and r["project_id"] in (None, q["project_id"])
            and r["client_id"] in (None, q["client_id"])
        }
        if set(q["forbidden"]) != set(by_id) - permitted:
            raise ValueError("incomplete boundary labels")
        for key, grade in q["relevant"].items():
            if key not in permitted or grade not in (1, 2, 3):
                raise ValueError("forbidden or invalid relevance label")
            if by_id[key]["state"] != "active" or by_id[key]["family"] != q["family"]:
                raise ValueError("stale support or family leakage")
    data["freeze"] = seal
    return data


def score_query(query: dict[str, Any], attempt: Attempt, k: int = 5) -> dict[str, Any]:
    """Collapse chunk identities at first rank; retain actual order for ties.

    A short successful list is complete and earns no padding. Transport
    truncation is status=incomplete. Scan ALL raw payload items for leaks,
    including duplicates/tails and failed responses, before metric selection.
    """
    leaked = [
        i
        for i in attempt.items
        if i.identity in query["forbidden"]
        or any(marker in i.content for marker in query["forbidden_text"])
    ]
    distinct = list(dict.fromkeys(i.identity for i in attempt.items))
    ranked = distinct[:k] if attempt.status == "ok" else []
    grades = query["relevant"]
    hits = [pos for pos, key in enumerate(ranked, 1) if grades.get(key, 0) > 0]
    ideal = sorted(grades.values(), reverse=True)[:k]
    dcg = sum(
        (2 ** grades.get(key, 0) - 1) / math.log2(pos + 1) for pos, key in enumerate(ranked, 1)
    )
    idcg = sum((2**grade - 1) / math.log2(pos + 1) for pos, grade in enumerate(ideal, 1))
    return {
        "recall_at_5": len(hits) / len(grades) if grades else None,
        "mrr_at_5": (1 / hits[0] if hits else 0) if grades else None,
        "ndcg_at_5": dcg / idcg if idcg else None,
        "unsupported": not grades and attempt.status == "ok" and bool(attempt.items),
        "empty_success": attempt.status == "ok" and not attempt.items,
        "failed": attempt.status != "ok",
        "forbidden_items": len(leaked),
        "raw_items": len(attempt.items),
        "duplicates": len(attempt.items) - len(distinct),
        "adjacent_score_ties": sum(
            a.score == b.score for a, b in zip(attempt.items, attempt.items[1:], strict=False)
        ),
        "utf8_bytes": sum(len(i.content.encode("utf-8")) for i in attempt.items),
        "estimated_tokens": sum(estimate_tokens(i.content) for i in attempt.items),
        "framed_utf8_bytes": len(render_context(attempt.items).encode("utf-8")),
        "framed_estimated_tokens": estimate_tokens(render_context(attempt.items)),
    }


def distribution(values: list[float]) -> dict[str, Any]:
    ordered = sorted(values)
    return {
        "n": len(values),
        **{
            name: ordered[math.ceil(len(ordered) * p) - 1] if ordered else None
            for name, p in (("p50_ms", 0.5), ("p95_ms", 0.95), ("max_ms", 1.0))
        },
    }


def aggregate(queries: list[dict[str, Any]], attempts: list[Attempt]) -> dict[str, Any]:
    """Intention-to-run denominators; missing/extra/duplicate quality rows are errors."""
    by_query = {a.query_id: a for a in attempts}
    if len(by_query) != len(attempts) or set(by_query) != {q["id"] for q in queries}:
        raise ValueError("planned/recorded query mismatch")
    rows = [score_query(q, by_query[q["id"]]) for q in queries]
    answerable = [r for q, r in zip(queries, rows, strict=True) if q["answerable"]]
    unsupported = [r for q, r in zip(queries, rows, strict=True) if not q["answerable"]]

    def mean(items: list[dict[str, Any]], key: str) -> float | None:
        return sum(r[key] for r in items) / len(items) if items else None

    return {
        "planned": len(queries),
        "recorded": len(attempts),
        "successful": sum(a.status == "ok" for a in attempts),
        "errors": sum(r["failed"] for r in rows),
        "error_codes": dict(
            Counter(a.error_code or a.status for a in attempts if a.status != "ok")
        ),
        "answerable": len(answerable),
        "no_support": len(unsupported),
        "intention_to_run": {
            k: mean(answerable, k) for k in ("recall_at_5", "mrr_at_5", "ndcg_at_5")
        },
        "successful_only": {
            k: mean([r for r in answerable if not r["failed"]], k)
            for k in ("recall_at_5", "mrr_at_5", "ndcg_at_5")
        },
        "unsupported_queries": sum(r["unsupported"] for r in unsupported),
        "unsupported_rate": mean(unsupported, "unsupported"),
        "no_support_errors": sum(r["failed"] for r in unsupported),
        "empty_successes": sum(r["empty_success"] for r in rows),
        "forbidden_queries": sum(r["forbidden_items"] > 0 for r in rows),
        "forbidden_items": sum(r["forbidden_items"] for r in rows),
        "blocked": any(r["forbidden_items"] for r in rows),
        "complete": not any(r["failed"] for r in rows),
        "context_totals": {
            k: sum(r[k] for r in rows)
            for k in (
                "raw_items",
                "duplicates",
                "utf8_bytes",
                "estimated_tokens",
                "framed_utf8_bytes",
                "framed_estimated_tokens",
            )
        },
        "successful_latency": distribution([a.latency_ms for a in attempts if a.status == "ok"]),
    }


def bm25(
    query: str, documents: list[dict[str, str]], *, k: int = 5, budget: int = 4000
) -> list[Item]:
    """Fixed Unicode BM25 over an ALREADY AUTHORIZED Markdown snapshot."""
    tokens = [re.findall(r"\w+", d["content"].casefold()) for d in documents]
    if not tokens:
        return []
    average = sum(map(len, tokens)) / len(tokens)
    # Stable summation order as well as stable final tie-breaking across processes.
    terms = sorted(set(re.findall(r"\w+", query.casefold())))
    scored = []
    for doc, words in zip(documents, tokens, strict=True):
        counts = Counter(words)
        value = 0.0
        for term in terms:
            tf = counts[term]
            df = sum(term in row for row in tokens)
            idf = math.log(1 + (len(tokens) - df + 0.5) / (df + 0.5))
            denominator = tf + 1.2 * (1 - 0.75 + 0.75 * len(words) / (average or 1))
            value += idf * tf * 2.2 / denominator if denominator else 0
        if value > 0:
            scored.append(
                Item(
                    identity=doc["identity"],
                    content=doc["content"],
                    score=value,
                    runtime_id=doc["runtime_id"],
                )
            )
    output: list[Item] = []
    used = 0
    for item in sorted(scored, key=lambda i: (-i.score, i.identity)):
        size = estimate_tokens(item.content)
        if used + size <= budget:
            output.append(item)
            used += size
            if len(output) == k:
                break
    return output


def paired_comparison(
    queries: list[dict[str, Any]],
    attempts: list[Attempt],
    samples: int = 2000,
    seed: int = 20261024,
) -> dict[str, Any]:
    """Family-cluster paired recall uncertainty, not general market superiority."""
    indexed = {(a.arm, a.query_id): a for a in attempts if a.stage == "quality"}
    deltas: dict[str, list[float]] = {}
    raw = []
    for q in queries:
        if not q["answerable"]:
            continue
        h = score_query(q, indexed["hybrid", q["id"]])["recall_at_5"]
        m = score_query(q, indexed["markdown", q["id"]])["recall_at_5"]
        delta = h - m
        raw.append({"query_id": q["id"], "family": q["family"], "delta": delta})
        deltas.setdefault(q["family"], []).append(delta)
    families = sorted(deltas)
    rng = random.Random(seed)  # noqa: S311 -- reproducible resampling, not credentials
    boot = []
    for _ in range(samples):
        values = [v for family in rng.choices(families, k=len(families)) for v in deltas[family]]
        boot.append(sum(values) / len(values))
    boot.sort()
    return {
        "paired_queries": raw,
        "families": len(families),
        "resamples": samples,
        "seed": seed,
        "recall_delta": sum(r["delta"] for r in raw) / len(raw),
        "family_bootstrap_95_percentile": [
            boot[math.ceil(samples * 0.025) - 1],
            boot[math.ceil(samples * 0.975) - 1],
        ],
    }
