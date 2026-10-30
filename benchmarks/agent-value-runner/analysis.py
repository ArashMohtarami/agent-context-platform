"""Prespecified paired family bootstrap. Pure numeric inputs; no task outputs."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence


def quantile(values: Sequence[float], p: float) -> float:
    if (
        not values
        or type(p) not in {int, float}
        or not math.isfinite(p)
        or not 0 <= p <= 1
        or any(type(v) not in {int, float} or not math.isfinite(v) for v in values)
    ):
        raise ValueError("nonempty values and probability required")
    ordered = sorted(values)
    index = (len(ordered) - 1) * p
    lower = math.floor(index)
    upper = math.ceil(index)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def indices(size: int, count: int, seed: int):
    """Uniform rejection samples from a pinned SHA256 counter stream."""
    if (
        type(size) is not int
        or not 1 <= size <= 256
        or type(count) is not int
        or not 1 <= count <= 2560000
        or type(seed) is not int
    ):
        raise ValueError("bounded cluster count required")
    counter = 0
    selected = []
    ceiling = 256 - 256 % size
    while len(selected) < count:
        data = hashlib.sha256(f"v8-paired-family-v1:{seed}:{counter}".encode()).digest()
        counter += 1
        selected.extend(b % size for b in data if b < ceiling)
    return selected[:count]


def paired_bootstrap(families: Mapping[str, Sequence[float]], *, draws=10000, seed=20261030):
    """Each family's R/M/C means keep its three attempts together."""
    if type(draws) is not int or not 1 <= draws <= 10000 or type(seed) is not int:
        raise ValueError("explicit bounded bootstrap settings required")
    keys = sorted(families)
    if not keys or len(keys) > 256:
        raise ValueError("nonempty bounded family map required")
    rows = [families[k] for k in keys]
    if any(
        len(row) != 3
        or any(type(v) not in {int, float} or not math.isfinite(v) or not 0 <= v <= 1 for v in row)
        for row in rows
    ):
        raise ValueError("one finite R/M/C completion mean per family required")
    differences = [row[2] - row[1] for row in rows]
    samples = indices(len(rows), len(rows) * draws, seed)
    distribution = [
        sum(differences[samples[i + j]] for j in range(len(rows))) / len(rows)
        for i in range(0, len(samples), len(rows))
    ]
    warnings = []
    if len(rows) != 20:
        warnings.append("not-the-primary20-family-design; descriptive example only")
    if len(set(differences)) == 1:
        warnings.append("degenerate-family-differences; no assumed precision or power")
    if all(v in {0, 1} for row in rows for v in row):
        warnings.append("all family means at boundaries")
    return {
        "version": 1,
        "method": "paired-family-cluster-bootstrap; SHA256 rejection stream v1; type7 quantiles",
        "seed": seed,
        "draws": draws,
        "families": len(rows),
        "q": [sum(row[a] for row in rows) / len(rows) for a in range(3)],
        "c_minus_m": sum(differences) / len(rows),
        "interval95": [quantile(distribution, 0.025), quantile(distribution, 0.975)],
        "warnings": warnings,
        "claim_approved": False,
    }


def project_sensitivity(families, projects, *, draws=10000, seed=20261030):
    """Resample projects, retaining all their families/arms/repeats together."""
    primary = paired_bootstrap(families, draws=draws, seed=seed)
    if set(projects) != set(families) or any(
        not isinstance(p, str) or not p for p in projects.values()
    ):
        raise ValueError("one explicit project for every family required")
    groups = sorted(set(projects.values()))
    if len(groups) > 256:
        raise ValueError("bounded project count required")
    differences = [
        [families[k][2] - families[k][1] for k in sorted(families) if projects[k] == project]
        for project in groups
    ]
    samples = indices(len(groups), len(groups) * draws, seed)
    distribution = []
    for i in range(0, len(samples), len(groups)):
        selected = [d for j in samples[i : i + len(groups)] for d in differences[j]]
        distribution.append(sum(selected) / len(selected))
    return {
        "version": 1,
        "method": "paired-project-cluster sensitivity; SHA256 rejection stream v1; type7",
        "seed": seed,
        "draws": draws,
        "projects": len(groups),
        "families": len(families),
        "c_minus_m": primary["c_minus_m"],
        "interval95": [quantile(distribution, 0.025), quantile(distribution, 0.975)],
        "warnings": ["few project clusters; weak precision; no generalization guarantee"],
        "claim_approved": False,
    }


def latency(durations):
    """Observed successful seconds only; absent measurements stay explicitly null."""
    if not isinstance(durations, list) or any(
        v is not None and (type(v) not in {int, float} or not math.isfinite(v) or v < 0)
        for v in durations
    ):
        raise ValueError("explicit finite nonnegative seconds or missing required")
    observed = sorted(v for v in durations if v is not None)
    return {
        "n": len(observed),
        "missing": len(durations) - len(observed),
        "p50": observed[math.ceil(0.5 * len(observed)) - 1] if observed else None,
        "p95": observed[math.ceil(0.95 * len(observed)) - 1] if observed else None,
        "method": "nearest rank; observed successes only",
    }


def efficiency_interval(families, *, draws=10000, seed=20261030):
    """All-attempt numerators per completion; any undefined draw blocks a CI.

    Caller supplies already reconciled non-overlapping tokens OR actual spend,
    never both in one numerator. Missing coverage is None, never zero. This
    numeric function cannot establish receipt coverage or approve a claim.
    """
    if (
        not isinstance(families, Mapping)
        or not 1 <= len(families) <= 256
        or type(draws) is not int
        or not 1 <= draws <= 10000
        or type(seed) is not int
    ):
        raise ValueError("bounded family map and bootstrap settings required")
    rows = [families[k] for k in sorted(families)]
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) != {"numerators", "completions"}
            or not isinstance(row["numerators"], list | tuple)
            or not isinstance(row["completions"], list | tuple)
            or len(row["numerators"]) != 3
            or len(row["completions"]) != 3
            or any(type(c) is not int or not 0 <= c <= 3 for c in row["completions"])
            or any(
                v is not None and (type(v) not in {int, float} or not math.isfinite(v) or v < 0)
                for v in row["numerators"]
            )
        ):
            raise ValueError("explicit R/M/C all-attempt numerators and completion counts required")
    result = {
        "version": 1,
        "method": "paired-family all-attempt ratio bootstrap; SHA256 rejection v1; type7",
        "seed": seed,
        "draws": draws,
        "families": len(rows),
        "per_completion": None,
        "c_minus_m": None,
        "interval95": None,
        "undefined_draws": None,
        "coverage": "missing" if any(None in r["numerators"] for r in rows) else "supplied",
        "claim_approved": False,
    }
    if result["coverage"] == "missing":
        return result

    def ratios(selected):
        counts = [sum(r["completions"][a] for r in selected) for a in range(3)]
        return [
            sum(r["numerators"][a] for r in selected) / counts[a] if counts[a] else None
            for a in range(3)
        ]

    point = ratios(rows)
    result["per_completion"] = point
    if point[1] is not None and point[2] is not None:
        result["c_minus_m"] = point[2] - point[1]
    samples = indices(len(rows), len(rows) * draws, seed)
    distribution = []
    undefined = 0
    for i in range(0, len(samples), len(rows)):
        values = ratios([rows[j] for j in samples[i : i + len(rows)]])
        if values[1] is None or values[2] is None:
            undefined += 1
        else:
            distribution.append(values[2] - values[1])
    result["undefined_draws"] = undefined
    if not undefined:
        result["interval95"] = [quantile(distribution, 0.025), quantile(distribution, 0.975)]
    return result
