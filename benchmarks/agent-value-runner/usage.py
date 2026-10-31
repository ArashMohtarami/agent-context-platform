"""Numeric allowlist for documented Codex exec events; never retain raw streams.

CLI docs establish completed-turn fields, not request coverage or counter
inclusion. Unknown semantics remain null and block spend/efficiency claims.
"""

from __future__ import annotations

import re
from itertools import pairwise
from typing import Any

COUNTERS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")
ITEMS = {
    "agent_message",
    "reasoning",
    "command_execution",
    "file_change",
    "mcp_tool_call",
    "web_search",
    "plan_update",
}


def number(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= 10**15:
        raise ValueError("INVALID_NUMERIC_RECEIPT")
    return value


def normalize(raw: dict, *, cache_in_input: bool | None, reasoning_in_output: bool | None):
    """Only proven inclusion permits subtraction. Never sum overlapping counters."""
    if set(raw) != set(COUNTERS):
        raise ValueError("MISSING_OR_UNKNOWN_COUNTER")
    values = {key: number(raw[key]) for key in COUNTERS}
    i, c, o, r = (values[k] for k in COUNTERS)
    if (cache_in_input is True and c > i) or (reasoning_in_output is True and r > o):
        raise ValueError("IMPOSSIBLE_SUBCOUNTER")
    complete = cache_in_input is True and reasoning_in_output is True
    return {
        "raw": values,
        "uncached_input": i - c if cache_in_input is True else None,
        "cached_read": c if cache_in_input is True else None,
        "nonreasoning_output": o - r if reasoning_in_output is True else None,
        "reasoning_output": r if reasoning_in_output is True else None,
        "cache_write": None,  # not emitted; not zero and not an extra billable bucket
        "total_tokens": i + o if complete else None,
        "inclusion_proven": complete,
    }


def aggregate(records: list[dict]) -> dict:
    """Reconcile one view: turn deltas OR a final cumulative watermark, never both."""
    unique = {}
    for row in records:
        if set(row) != {"id", "scope", "watermark", "raw"} or row["scope"] not in {
            "turn",
            "session",
        }:
            raise ValueError("UNKNOWN_USAGE_VIEW")
        if not isinstance(row["id"], str) or not row["id"]:
            raise ValueError("MISSING_RECEIPT_ID")
        number(row["watermark"])
        if row["id"] in unique and unique[row["id"]] != row:
            raise ValueError("CONFLICTING_DUPLICATE_RECEIPT")
        normalize(row["raw"], cache_in_input=None, reasoning_in_output=None)
        unique[row["id"]] = row
    rows = list(unique.values())
    if not rows:
        return {"raw": None, "coverage": "missing", "records": 0}
    if len({r["scope"] for r in rows}) != 1:
        raise ValueError("OVERLAPPING_TURN_AND_SESSION_VIEWS")
    if rows[0]["scope"] == "session":
        rows.sort(key=lambda r: r["watermark"])
        if len({r["watermark"] for r in rows}) != len(rows) or any(
            b["raw"][k] < a["raw"][k] for a, b in pairwise(rows) for k in COUNTERS
        ):
            raise ValueError("CUMULATIVE_COUNTER_REGRESSION")
        raw = rows[-1]["raw"].copy()
    else:
        if len({r["watermark"] for r in rows}) != len(rows):
            raise ValueError("DUPLICATE_TURN_WATERMARK")
        raw = {k: sum(r["raw"][k] for r in rows) for k in COUNTERS}
    return {"raw": raw, "coverage": "observed-view-only", "records": len(rows)}


def resolution(requested: str, observed: str | None) -> dict:
    # A trusted numeric receipt may supply this separately; no invented CLI event.
    if not isinstance(requested, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,101}", requested):
        raise ValueError("INVALID_MODEL_ID")
    if observed is not None and (
        not isinstance(observed, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,101}", observed)
    ):
        raise ValueError("INVALID_MODEL_ID")
    return {
        "resolved_model": observed,
        "matched": None if observed is None else requested == observed,
        "launch_blocked": observed is None or requested != observed,
    }


def response_usage(raw):
    """Documented Responses wire usage; native JSONL/session views stay separate.

    This requires the actual per-response object, including both detail views.
    Matching arithmetic alone is not evidence of the upstream billing route.
    Cache writes remain an optional subset of input, never an extra token total.
    """
    if not isinstance(raw, dict) or set(raw) != {
        "input_tokens",
        "input_tokens_details",
        "output_tokens",
        "output_tokens_details",
        "total_tokens",
    }:
        raise ValueError("UNREVIEWED_RESPONSE_USAGE_FIELDS")
    incoming, outgoing = raw["input_tokens_details"], raw["output_tokens_details"]
    if (
        not isinstance(incoming, dict)
        or not {"cached_tokens"} <= set(incoming) <= {"cached_tokens", "cache_write_tokens"}
        or not isinstance(outgoing, dict)
        or set(outgoing) != {"reasoning_tokens"}
    ):
        raise ValueError("UNREVIEWED_RESPONSE_DETAIL_FIELDS")
    value = normalize(
        {
            "input_tokens": raw["input_tokens"],
            "cached_input_tokens": incoming["cached_tokens"],
            "output_tokens": raw["output_tokens"],
            "reasoning_output_tokens": outgoing["reasoning_tokens"],
        },
        cache_in_input=True,
        reasoning_in_output=True,
    )
    if number(raw["total_tokens"]) != value["total_tokens"]:
        raise ValueError("INCONSISTENT_RESPONSE_TOTAL")
    cache_write = incoming.get("cache_write_tokens")
    if cache_write is not None:
        if number(cache_write) > value["uncached_input"]:
            raise ValueError("OVERLAPPING_CACHE_WRITE_SUBCOUNT")
        value["cache_write"] = cache_write
        value["uncached_input"] -= cache_write
    value["uncached_input_includes_unreported_writes"] = cache_write is None
    return value


class Responses:
    """One receipt for every bounded HTTP request, including failures/missingness."""

    def __init__(self, model):
        self.model = model
        self.rows = []
        self.identities = set()
        self.flags = set()

    def start(self, *, prompt_sha256, tools_sha256, effort, tier):
        if any(
            not isinstance(s, str) or not re.fullmatch(r"[a-f0-9]{64}", s)
            for s in (prompt_sha256, tools_sha256)
        ):
            raise ValueError("EXPLICIT_WIRE_INPUT_HASHES_REQUIRED")
        if effort != "high" or tier != "default":
            raise ValueError("UNREVIEWED_WIRE_SETTINGS")
        self.rows.append(
            {
                "request": len(self.rows) + 1,
                "prompt_sha256": prompt_sha256,
                "tools_sha256": tools_sha256,
                "effort": effort,
                "requested_tier": tier,
                "response_sha256": None,
                "http_status": None,
                "resolved_model": None,
                "resolved_tier": None,
                "usage": None,
                "terminal": False,
            }
        )
        return len(self.rows)

    def finish(self, number_, status, response):
        if type(number_) is not int or not 1 <= number_ <= len(self.rows):
            raise ValueError("UNKNOWN_PROVIDER_REQUEST")
        row = self.rows[number_ - 1]
        if row["terminal"]:
            raise ValueError("DUPLICATE_PROVIDER_REQUEST_COMPLETION")
        row["terminal"] = True
        row["http_status"] = number(status)
        if status != 200 or not isinstance(response, dict):
            self.flags.add("FAILED_REQUEST_USAGE_UNKNOWN")
            return
        identity = response.get("id")
        if not isinstance(identity, str) or not re.fullmatch(
            r"resp_[A-Za-z0-9_-]{1,180}", identity
        ):
            self.flags.add("MISSING_PROVIDER_RESPONSE_ID")
            return
        # Hash opaque identity, never retain the response text/reasoning or auth.
        import hashlib

        fingerprint = hashlib.sha256(identity.encode()).hexdigest()
        if fingerprint in self.identities:
            self.flags.add("DUPLICATE_PROVIDER_RESPONSE_ID")
            return
        self.identities.add(fingerprint)
        row["response_sha256"] = fingerprint
        observed = response.get("model")
        if isinstance(observed, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,101}", observed):
            row["resolved_model"] = observed
        if observed != self.model:
            self.flags.add("MODEL_RESOLUTION_CHANGED_OR_UNKNOWN")
        tier = response.get("service_tier")
        if isinstance(tier, str) and tier in {"default", "priority", "flex", "auto", "scale"}:
            row["resolved_tier"] = tier
        if tier != "default":
            self.flags.add("SERVICE_TIER_CHANGED_OR_UNKNOWN")
        if response.get("status") != "completed":
            self.flags.add("PROVIDER_RESPONSE_NOT_COMPLETED")
        try:
            row["usage"] = response_usage(response.get("usage"))
        except (TypeError, ValueError):
            self.flags.add("RESPONSE_USAGE_UNKNOWN")

    def report(self):
        flags = set(self.flags)
        if not self.rows or any(not r["terminal"] or r["usage"] is None for r in self.rows):
            flags.add("MISSING_REQUEST_RECEIPT")
        complete = not flags
        return {
            "adapter": "codex-0.166.0-responses-https-v1",
            "scope": "request",
            "requests": self.rows,
            "coverage": "complete-observed-route" if complete else "incomplete",
            "flags": sorted(flags),
            "normalized_total": sum(r["usage"]["total_tokens"] for r in self.rows)
            if complete
            else None,
            "resolved_model": self.model if complete else None,
            "billed_spend": None,
            "cash_savings_claim_supported": False,
            "native_session_totals_added": False,
        }


class Stream:
    """Stateful stream parser. Everything not explicitly numeric is discarded."""

    def __init__(self, cli_version: str):
        if cli_version != "0.166.0":
            raise ValueError("UNREVIEWED_CLI_VERSION")
        self.started = 0
        self.active = False
        self.records: list[dict] = []
        self.flags: set[str] = set()
        self.events = 0
        self.tools = 0
        self.thread = False

    def consume(self, event: Any) -> None:
        self.events += 1
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            self.flags.add("UNKNOWN_EVENT")
            return
        kind = event["type"]
        fields = {
            "thread.started": {"type", "thread_id"},
            "turn.started": {"type"},
            "turn.completed": {"type", "usage"},
            "turn.failed": {"type", "error"},
            "error": {"type", "message"},
            "item.started": {"type", "item"},
            "item.updated": {"type", "item"},
            "item.completed": {"type", "item"},
        }
        if kind not in fields:
            self.flags.add("UNKNOWN_EVENT")  # retries/compaction are not assumed free
            return
        if set(event) != fields[kind]:
            self.flags.add("UNKNOWN_OR_MISSING_EVENT_FIELDS")
        if kind == "thread.started":
            if self.thread:
                self.flags.add("DUPLICATE_THREAD")
            self.thread = True
        elif kind == "turn.started":
            if self.active:
                self.flags.add("UNFINISHED_TURN")
            self.started += 1
            self.active = True
        elif kind == "turn.completed":
            if not self.active:
                self.flags.add("DUPLICATE_OR_ORPHAN_COMPLETION")
                return
            self.active = False
            try:
                value = normalize(event.get("usage"), cache_in_input=None, reasoning_in_output=None)
            except (TypeError, ValueError):
                self.flags.add("UNKNOWN_OR_MISSING_USAGE")
                return
            self.records.append(
                {
                    "id": f"turn-{self.started}",
                    "scope": "turn",
                    "watermark": self.started,
                    "raw": value["raw"],
                }
            )
        elif kind in {"turn.failed", "error"}:
            self.flags.add("FAILED_CALL_COVERAGE_UNKNOWN")
            self.active = False
        elif kind.startswith("item."):
            item = event.get("item")
            if not isinstance(item, dict) or item.get("type") not in ITEMS:
                self.flags.add("UNKNOWN_ITEM")
            elif kind == "item.started" and item["type"] in {
                "command_execution",
                "mcp_tool_call",
                "web_search",
            }:
                self.tools += 1
                if item["type"] == "web_search":
                    self.flags.add("UNDECLARED_TOOL")
            # No item text, commands, arguments, output, IDs or reasoning is kept.

    def report(self) -> dict:
        flags = self.flags.copy()
        if self.active or self.started != len(self.records):
            flags.add("MISSING_TURN_RECEIPT")
        if not self.records:
            flags.add("NO_USAGE_RECEIPT")
        flags.update(
            {
                "REQUEST_COVERAGE_UNPROVEN",
                "INCLUSION_SEMANTICS_UNPROVEN",
                "RESOLVED_MODEL_UNOBSERVED",
                "BILLED_SPEND_UNOBSERVED",
            }
        )
        return {
            "adapter": "codex-exec-jsonl-0.166.0-v1",
            "events": self.events,
            "turns_started": self.started,
            "turns_completed": len(self.records),
            "tool_starts": self.tools,
            "records": self.records,
            "observed": aggregate(self.records),
            "normalized_total": None,
            "resolved_model": None,
            "billed_spend": None,
            "coverage": "incomplete",
            "spend_supported": False,
            "flags": sorted(flags),
        }
