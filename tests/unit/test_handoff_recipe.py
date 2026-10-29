"""Pure demo selection/checkpoint/gate guards. Real authority is tested separately."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def recipe():
    spec = importlib.util.spec_from_file_location(
        "handoff_smoke", ROOT / "examples/agent-handoff/smoke.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "changes",
    [
        {"origin": "http://127.0.0.1:8000"},
        {"origin": "https://user:secret@example.test"},
        {"negative_project_id": "notes-api"},
        {"run_id": "../escape"},
    ],
)
def test_selection_refuses_implicit_or_unsafe_boundaries(recipe, changes):
    with pytest.raises(ValueError):
        recipe.Selection(
            **{
                "origin": "https://127.0.0.1:12345",
                "project_id": "notes-api",
                "negative_project_id": "other-notes",
                "run_id": "demo",
                **changes,
            }
        )


def test_fact_uses_valid_schema_and_verified_guide_payload(recipe):
    selection = recipe.Selection(
        origin="https://example.test",
        project_id="selected-notes",
        negative_project_id="other-notes",
        run_id="demo-1",
    )
    request = recipe.fact(selection)
    source = (ROOT / "docs/guide/content.md").read_text()
    assert request["content"] in source
    assert request["source"] == "guide-notes-api" and request["project_id"] == "selected-notes"
    assert request["external_id"] == "handoff-demo-1-fact-v1"
    assert {item["path"] for item in recipe.provenance()} == {
        "examples/starter-notes-api/notes_app.py",
        "examples/starter-notes-api/test_app.py",
        "examples/agent-handoff/fact.json",
    }


@pytest.mark.parametrize("field", ["context_id", "revision", "content", "project_id", "source"])
def test_plausible_text_never_substitutes_for_exact_record(recipe, field):
    expected = {
        "context_id": "a" * 24,
        "revision": 1,
        "content": "verified fact",
        "project_id": "notes-api",
        "title": "Fact",
        "source": "guide-notes-api",
        "external_id": "demo-fact",
    }
    actual = {**expected, field: 2 if field == "revision" else "plausible but different"}
    with pytest.raises(recipe.HandoffError, match="RECORD_MISMATCH"):
        recipe.same_record(actual, expected)


def test_old_external_id_with_changed_fact_fails_closed(recipe):
    requested = {
        "project_id": "notes-api",
        "content": "current reviewed fact",
        "source": "guide-notes-api",
        "title": "Fact",
    }
    saved = {**requested, "content": "old text", "context_id": "a" * 24, "revision": 1}
    with pytest.raises(recipe.HandoffError, match="EXTERNAL_ID_CONTENT_MISMATCH"):
        recipe.validate_saved(saved, requested)


@pytest.mark.parametrize("kind", ["existing", "symlink", "public-parent"])
def test_stale_or_foreign_revocation_signal_is_refused(recipe, tmp_path, kind):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    gate = private / "gate"
    if kind == "existing":
        gate.write_text("revoked\n")
    elif kind == "symlink":
        gate.symlink_to(private / "other")
    else:
        private.chmod(0o755)
    with pytest.raises(recipe.HandoffError, match="REVOCATION_SIGNAL_INVALID"):
        recipe.gate_path(gate, fresh=True)


async def test_signal_does_not_wait_forever_or_accept_arbitrary_content(recipe, tmp_path):
    tmp_path.chmod(0o700)
    gate = tmp_path / "gate"
    with pytest.raises(recipe.HandoffError, match="REVOCATION_NOT_CONFIRMED"):
        await recipe.wait_for_recipient(gate, deadline_seconds=0.01)
    gate.write_text("not actually revoked\n")
    gate.chmod(0o600)
    with pytest.raises(recipe.HandoffError, match="REVOCATION_SIGNAL_INVALID"):
        await recipe.wait_for_recipient(gate)


def test_argument_failure_never_echoes_pasted_secret_or_path(recipe, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["smoke.py", "--token", "private-value", "/private/recipient"])
    assert recipe.main() == 1
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["code"] == "ARGUMENTS_INVALID"
    assert "private-value" not in captured.out + captured.err
    assert "/private/recipient" not in captured.out + captured.err


@pytest.mark.parametrize(
    "body, expected",
    [
        ({"error": {"code": "VALIDATION_ERROR", "message": "private-value"}}, "VALIDATION_ERROR"),
        ({"error": {"code": "private-value", "message": "private-value"}}, "MCP_TOOL_FAILED"),
        (None, "MCP_TOOL_FAILED"),
    ],
)
def test_protocol_error_export_allowlists_codes(recipe, body, expected):
    assert recipe.protocol_error(body) == expected
