#!/usr/bin/env python3
"""Measure the pinned local password policy with synthetic secrets, never accounts."""

import argparse
import asyncio
import json
import os
import platform
import resource
import statistics
from pathlib import Path
from time import perf_counter

from app.identity.crypto import PasswordWork, random_token


async def benchmark(samples: int) -> dict[str, object]:
    work = PasswordWork()
    password = random_token()
    try:
        encoded = await work.hash(password)
        timings = []
        for _ in range(samples):
            start = perf_counter()
            if not await work.verify(encoded, password):
                raise RuntimeError("synthetic password verification failed")
            timings.append((perf_counter() - start) * 1000)
        started = perf_counter()
        results = await asyncio.gather(*(work.verify(encoded, password) for _ in range(4)))
        if not all(results):
            raise RuntimeError("synthetic concurrent verification failed")
        concurrent_ms = (perf_counter() - started) * 1000
        model = next(
            (
                line.partition(":")[2].strip()
                for line in Path("/proc/cpuinfo").read_text().splitlines()
                if line.startswith("model name")
            ),
            "unknown",
        )
        cgroup = Path("/sys/fs/cgroup/cpu.max")
        return {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cpu_model": model,
            "logical_cpus": os.cpu_count(),
            "cpu_affinity_count": len(os.sched_getaffinity(0)),
            "cgroup_cpu_max": cgroup.read_text().strip() if cgroup.exists() else None,
            "policy": {
                "algorithm": "Argon2id",
                "memory_kib": 65536,
                "time_cost": 3,
                "parallelism": 4,
                "salt_bytes": 16,
                "hash_bytes": 32,
                "maximum_concurrent_jobs": 4,
            },
            "samples": samples,
            "verification_ms": {
                "min": round(min(timings), 2),
                "median": round(statistics.median(timings), 2),
                "p95": round(sorted(timings)[max(0, (95 * len(timings) + 99) // 100 - 1)], 2),
                "max": round(max(timings), 2),
            },
            "four_concurrent_total_ms": round(concurrent_ms, 2),
            "process_peak_rss_kib_linux": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "limitations": (
                "Local warm synthetic run; shared-host scheduling, "
                "not a production throughput guarantee"
            ),
        }
    finally:
        work.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=20, choices=range(5, 101))
    args = parser.parse_args()
    print(json.dumps(asyncio.run(benchmark(args.samples)), indent=2))


if __name__ == "__main__":
    main()
