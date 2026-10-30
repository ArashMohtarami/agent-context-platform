"""Sanitized synthetic numbers, not captured vendor/model evidence."""

import importlib
import json

import pytest

usage = importlib.import_module("benchmarks.agent-value-runner.usage")
RAW = {
    "input_tokens": 100,
    "cached_input_tokens": 40,
    "output_tokens": 20,
    "reasoning_output_tokens": 5,
}


def test_selected_adapter_normalizes_turn_cache_and_reasoning_without_double_count():
    # Proven synthetic inclusion illustrates arithmetic; CLI inclusion remains unproven.
    result = usage.normalize(RAW, cache_in_input=True, reasoning_in_output=True)
    assert result["uncached_input"] == 60 and result["nonreasoning_output"] == 15
    assert result["total_tokens"] == 120 and result["cache_write"] is None
    unresolved = usage.normalize(RAW, cache_in_input=None, reasoning_in_output=None)
    assert unresolved["total_tokens"] is None and unresolved["uncached_input"] is None
    row = {"id": "a", "scope": "turn", "watermark": 1, "raw": RAW}
    assert usage.aggregate([row, row])["raw"] == RAW
    second = {**row, "id": "b", "watermark": 2}
    assert usage.aggregate([row, second])["raw"]["input_tokens"] == 200
    cumulative = [
        {**row, "scope": "session"},
        {**second, "scope": "session", "raw": {k: 2 * v for k, v in RAW.items()}},
    ]
    assert usage.aggregate(cumulative)["raw"]["input_tokens"] == 200
    for rows in (
        [row, {**row, "raw": {**RAW, "input_tokens": 101}}],
        [row, cumulative[-1]],
        list(
            reversed(
                [
                    {**row, "scope": "session", "raw": {k: 2 * v for k, v in RAW.items()}},
                    {**second, "scope": "session"},
                ]
            )
        ),
    ):
        with pytest.raises(ValueError):
            usage.aggregate(rows)
    for bad in (
        {**RAW, "cached_input_tokens": 101},
        {**RAW, "reasoning_output_tokens": 21},
        {**RAW, "output_tokens": True},
        {**RAW, "input_tokens": -1},
        {**RAW, "new": 0},
    ):
        with pytest.raises(ValueError):
            usage.normalize(bad, cache_in_input=True, reasoning_in_output=True)
    stream = usage.Stream("0.166.0")
    for event in (
        {"type": "thread.started", "thread_id": "synthetic"},
        {"type": "turn.started"},
        {"type": "turn.completed", "usage": RAW},
        {"type": "turn.completed", "usage": RAW},
    ):
        stream.consume(event)
    report = stream.report()
    assert report["observed"]["raw"] == RAW and report["normalized_total"] is None
    assert "DUPLICATE_OR_ORPHAN_COMPLETION" in report["flags"]


def test_missing_receipts_model_fallback_and_unknown_events_are_explicit():
    with pytest.raises(ValueError, match="UNREVIEWED_CLI_VERSION"):
        usage.Stream("future-version")
    assert usage.aggregate([]) == {"raw": None, "coverage": "missing", "records": 0}
    assert usage.resolution("requested", None)["matched"] is None
    assert usage.resolution("requested", "fallback")["launch_blocked"]
    assert not usage.resolution("requested", "requested")["launch_blocked"]
    stream = usage.Stream("0.166.0")
    for event in (
        {"type": "turn.started"},
        {"type": "turn.failed", "error": {"message": "discard"}},
        {"type": "request.retry", "tokens": 12},
        {"type": "context.compacted", "tokens": 20},
        {"type": "turn.started"},
        {"type": "turn.completed", "usage": {**RAW, "unknown": 1}},
        {"type": "turn.started"},
    ):
        stream.consume(event)
    report = stream.report()
    assert report["observed"]["raw"] is None and report["billed_spend"] is None
    assert report["resolved_model"] is None and not report["spend_supported"]
    assert {
        "UNKNOWN_EVENT",
        "FAILED_CALL_COVERAGE_UNKNOWN",
        "MISSING_TURN_RECEIPT",
        "UNKNOWN_OR_MISSING_USAGE",
        "NO_USAGE_RECEIPT",
    } <= set(report["flags"])


def test_stream_filter_drops_secrets_and_reasoning_text():
    secret = "fictional-secret-DO-NOT-KEEP"
    reasoning = "fictional-private-reasoning-DO-NOT-KEEP"
    stream = usage.Stream("0.166.0")
    for event in (
        {"type": "thread.started", "thread_id": secret},
        {"type": "turn.started"},
        {"type": "item.started", "item": {"id": secret, "type": "reasoning", "text": reasoning}},
        {
            "type": "item.completed",
            "item": {
                "id": secret,
                "type": "command_execution",
                "command": secret,
                "aggregated_output": reasoning,
                "headers": {"Authorization": secret},
            },
        },
        {"type": "error", "message": secret},
        {"type": "new-event", secret: reasoning},
        {"type": "turn.started"},
        {"type": "turn.completed", "usage": RAW},
    ):
        stream.consume(event)
    report = stream.report()
    serialized = json.dumps(report)
    assert (
        secret not in serialized
        and reasoning not in serialized
        and "Authorization" not in serialized
    )
    assert report["observed"]["raw"] == RAW
    assert report["coverage"] == "incomplete" and report["normalized_total"] is None
