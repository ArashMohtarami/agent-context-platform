#!/usr/bin/env python3
"""Measure what this deployment actually does, against the documented targets.

Run it against a live platform. It issues each kind of call many times and
reports p50, p95, and p99 next to the target for that operation, so the answer
to "is it fast enough" is a measurement rather than an opinion.

    python scripts/benchmark.py --base-url http://127.0.0.1:8000 \
        --token-file ./secrets/benchmark-api-key --check

`--check` exits non-zero when a p95 misses its target, which is what makes this
usable as a smoke test in a deploy pipeline. Without it the script only
reports, which is what you want while tuning.

The key needs `search:read`, `skills:read`, `memory:read`, `context:read`, and
`system:read`. Nothing here writes, so it is safe against a live deployment,
though the numbers will include whatever else that deployment is doing.
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import httpx

# Default smoke-test targets, in milliseconds, against p95. Tune these for your
# deployment; they are regression checks rather than performance guarantees.
TARGETS: Final[dict[str, float]] = {
    "health_live": 50.0,
    "health_ready": 500.0,
    "metadata_lookup": 100.0,
    "lexical_search": 500.0,
    "vector_search": 500.0,
    "hybrid_search": 500.0,
    "unified_retrieval": 500.0,
    "memory_search": 500.0,
    "context_search": 500.0,
    "metrics_scrape": 100.0,
}
DEFAULT_ROUNDS: Final = 30
DEFAULT_WARMUP: Final = 3


@dataclass(slots=True)
class Measurement:
    """Every timing taken for one operation."""

    name: str
    samples: list[float] = field(default_factory=list)
    failures: int = 0

    def record(self, milliseconds: float) -> None:
        """Keep one timing."""
        self.samples.append(milliseconds)

    @property
    def ordered(self) -> list[float]:
        """Return the timings in order, for reading quantiles off."""
        return sorted(self.samples)

    def quantile(self, fraction: float) -> float:
        """Return the nearest-rank quantile, so every figure was measured."""
        ordered = self.ordered
        if not ordered:
            return float("nan")
        rank = max(1, min(len(ordered), round(fraction * len(ordered) + 0.5)))
        return ordered[rank - 1]

    def summary(self) -> dict[str, float | int]:
        """Describe this operation the way the report quotes it."""
        ordered = self.ordered
        return {
            "samples": len(ordered),
            "failures": self.failures,
            "p50": self.quantile(0.5),
            "p95": self.quantile(0.95),
            "p99": self.quantile(0.99),
            "min": ordered[0] if ordered else float("nan"),
            "max": ordered[-1] if ordered else float("nan"),
            "mean": statistics.fmean(ordered) if ordered else float("nan"),
        }


async def measure(
    name: str,
    call: Callable[[], Awaitable[Any]],
    *,
    rounds: int,
    warmup: int,
) -> Measurement:
    """Run one operation repeatedly and time each attempt.

    The warm-up rounds are discarded rather than averaged in: the first call
    pays for a connection, a cold index page, and in one case a model load, and
    including that would describe a deployment nobody experiences after the
    first second.
    """
    measurement = Measurement(name=name)
    for attempt in range(warmup + rounds):
        started = time.perf_counter()
        try:
            await call()
        except Exception:
            measurement.failures += 1
            continue
        elapsed = (time.perf_counter() - started) * 1_000
        if attempt >= warmup:
            measurement.record(elapsed)
    return measurement


def render(measurements: Sequence[Measurement]) -> str:
    """Render the results as a table, with each target beside its measurement."""
    header = f"{'OPERATION':<20} {'N':>4} {'p50':>9} {'p95':>9} {'p99':>9} {'TARGET':>9}  VERDICT"
    lines = [header, "-" * len(header)]
    for measurement in measurements:
        figures = measurement.summary()
        target = TARGETS.get(measurement.name)
        verdict = "—"
        if target is not None and figures["samples"]:
            verdict = "ok" if figures["p95"] <= target else "OVER"
        if figures["failures"]:
            verdict = f"{verdict} ({figures['failures']} failed)"
        lines.append(
            f"{measurement.name:<20} {figures['samples']:>4} "
            f"{figures['p50']:>8.1f}m {figures['p95']:>8.1f}m {figures['p99']:>8.1f}m "
            f"{('—' if target is None else f'{target:.0f}ms'):>9}  {verdict}"
        )
    return "\n".join(lines)


def missed(measurements: Sequence[Measurement]) -> list[str]:
    """Name every operation whose p95 is over its target, or that failed."""
    over = []
    for measurement in measurements:
        figures = measurement.summary()
        target = TARGETS.get(measurement.name)
        if figures["failures"]:
            over.append(f"{measurement.name} ({figures['failures']} failed)")
        elif target is not None and figures["samples"] and figures["p95"] > target:
            over.append(f"{measurement.name} (p95 {figures['p95']:.0f}ms > {target:.0f}ms)")
    return over


async def run(arguments: argparse.Namespace) -> int:
    """Benchmark one deployment and report what it did."""
    token = None
    if arguments.token_file:
        token = Path(arguments.token_file).read_text(encoding="utf-8").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    project = arguments.project_id

    async with httpx.AsyncClient(
        base_url=arguments.base_url,
        headers=headers,
        timeout=60.0,
        trust_env=False,
    ) as client:

        async def get(path: str, **params: object) -> Any:
            response = await client.get(path, params=params or None)
            response.raise_for_status()
            return response

        async def post(path: str, body: dict[str, object]) -> Any:
            response = await client.post(path, json=body)
            response.raise_for_status()
            return response

        plan: list[tuple[str, Callable[[], Awaitable[Any]]]] = [
            ("health_live", lambda: get("/health/live")),
            ("health_ready", lambda: get("/health/ready")),
            ("metadata_lookup", lambda: get("/api/v1/skills", limit=10)),
            (
                "lexical_search",
                lambda: post("/api/v1/skills/search/lexical", {"query": arguments.query}),
            ),
            (
                "vector_search",
                lambda: post("/api/v1/skills/search/vector", {"query": arguments.query}),
            ),
            (
                "hybrid_search",
                lambda: post("/api/v1/skills/search/hybrid", {"query": arguments.query}),
            ),
            (
                "unified_retrieval",
                lambda: post(
                    "/api/v1/search",
                    {"query": arguments.query, **({"project_id": project} if project else {})},
                ),
            ),
            ("memory_search", lambda: post("/api/v1/memory/search", {"query": arguments.query})),
        ]
        if project:
            plan.append(
                (
                    "context_search",
                    lambda: post(
                        "/api/v1/context/search",
                        {"query": arguments.query, "project_id": project},
                    ),
                )
            )
        if token:
            plan.append(("metrics_scrape", lambda: get("/metrics")))

        measurements = [
            await measure(name, call, rounds=arguments.rounds, warmup=arguments.warmup)
            for name, call in plan
        ]

    if arguments.json:
        print(json.dumps({m.name: m.summary() for m in measurements}, indent=2))
    else:
        print(render(measurements))

    over = missed(measurements)
    if over:
        print("\nover target: " + "; ".join(over), file=sys.stderr)
    return 1 if (arguments.check and over) else 0


def parse(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the benchmark's arguments."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--token-file",
        help="file holding an API key; never the key itself, which argv would expose",
    )
    parser.add_argument("--project-id", help="a project to include in the retrieval benchmarks")
    parser.add_argument("--query", default="how do we prevent duplicate payments?")
    parser.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS)
    parser.add_argument("--warmup", type=int, default=DEFAULT_WARMUP)
    parser.add_argument("--json", action="store_true", help="print the measurements as JSON")
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero when a p95 misses its target",
    )
    return parser.parse_args(argv)


def main() -> None:
    """Run the benchmark."""
    raise SystemExit(asyncio.run(run(parse())))


if __name__ == "__main__":
    main()
