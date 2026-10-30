"""Hand-checkable formulas, frozen split integrity and paired-study controls."""

import importlib.util
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from benchmarks.retrieval import coding
from benchmarks.retrieval.scorer import (
    ROOT,
    Attempt,
    Item,
    Report,
    aggregate,
    bm25,
    digest,
    distribution,
    identity,
    load_dataset,
    paired_comparison,
    score_query,
)


def query(answerable=True):
    return dict(
        id="tiny",
        family="tiny",
        answerable=answerable,
        relevant={"a": 3, "b": 1} if answerable else {},
        forbidden=["foreign"],
        forbidden_text=["FORBIDDEN"],
    )


def attempt(ids=(), status="ok", arm="hybrid", **changes):
    return Attempt(
        query_id="tiny",
        arm=arm,
        stage="quality",
        repeat=0,
        status=status,
        latency_ms=10,
        items=[Item(identity=i, content=i, score=1, runtime_id=i) for i in ids],
        **changes,
    )


def test_hand_calculated_rank_metrics_duplicates_and_ties():
    row = score_query(query(), attempt(["x", "b", "b", "a"]))
    assert row["recall_at_5"] == 1
    assert row["mrr_at_5"] == 0.5
    assert row["ndcg_at_5"] == pytest.approx((1 / math.log2(3) + 7 / 2) / (7 + 1 / math.log2(3)))
    assert row["raw_items"] == 4 and row["duplicates"] == 1
    assert row["adjacent_score_ties"] == 3


def test_short_empty_and_truncated_results_are_not_padded():
    assert score_query(query(), attempt(["b"]))["recall_at_5"] == 0.5
    assert score_query(query(), attempt([]))["ndcg_at_5"] == 0
    assert score_query(query(), attempt(["x", "y", "z", "u", "v", "a"]))["recall_at_5"] == 0
    truncated = score_query(query(), attempt(["a"], status="incomplete"))
    assert truncated["failed"] and truncated["recall_at_5"] == 0


def test_no_support_is_separate_and_denial_is_never_abstention_or_fast_success():
    q = query(False)
    assert score_query(q, attempt())["recall_at_5"] is None
    assert score_query(q, attempt(["x"]))["unsupported"]
    denied = attempt(status="denied", error_code="AUTHORIZATION")
    result = aggregate([q], [denied])
    assert result["no_support_errors"] == 1 and result["empty_successes"] == 0
    assert result["successful_latency"]["n"] == 0
    assert result["intention_to_run"]["recall_at_5"] is None
    assert not result["complete"]


@pytest.mark.parametrize("status", ["denied", "timeout", "error", "incomplete"])
def test_failed_answerable_attempts_stay_in_denominator(status):
    result = aggregate([query()], [attempt(status=status)])
    assert result["intention_to_run"]["recall_at_5"] == 0
    assert result["successful_only"]["recall_at_5"] is None
    assert result["errors"] == 1


def test_forbidden_scan_includes_tail_duplicates_and_failed_payload():
    a = attempt(["a", "b", "c", "d", "e", "foreign", "foreign"], status="error")
    row = score_query(query(), a)
    assert row["forbidden_items"] == 2
    text_leak = attempt(["a"])
    text_leak.items[0].content = "prefix FORBIDDEN suffix"
    assert aggregate([query()], [text_leak])["blocked"]


def test_nearest_rank_latency_and_context_estimation_are_checkable():
    assert distribution([40, 10, 30, 20]) == dict(n=4, p50_ms=20, p95_ms=40, max_ms=40)
    assert distribution([])["p95_ms"] is None
    a = attempt(["a"])
    a.items[0].content = "é x!"
    r = score_query(query(), a)
    assert r["utf8_bytes"] == 5 and r["estimated_tokens"] == 3


def test_bm25_positive_only_stable_ties_and_complete_budget():
    docs = [dict(identity=i, runtime_id=i, content="alpha beta") for i in ("z", "a")]
    assert [i.identity for i in bm25("alpha", docs)] == ["a", "z"]
    assert bm25("unseen", docs) == []
    assert [i.identity for i in bm25("alpha", docs, budget=2)] == ["a"]
    assert bm25("alpha", docs, budget=1) == []


def test_missing_extra_duplicate_outcomes_fail_reconciliation():
    for rows in ([], [attempt(), attempt()], [attempt().model_copy(update={"query_id": "extra"})]):
        with pytest.raises(ValueError, match="mismatch"):
            aggregate([query()], rows)


def test_frozen_family_split_labels_and_cross_tenant_copies():
    data = load_dataset()
    records = data["corpus"]["records"]
    assert len(records) == 71 and all(r["license"] == "MIT" for r in records)
    assert len({q["family"] for q in data["queries"]["queries"]}) == 30
    assert {r["source"] for r in records} == {"project_context", "active_skill", "memory"}
    a = next(r for r in records if r["org_id"] == "org-a")
    b = next(r for r in records if r["org_id"] == "org-b" and r["id"] == a["id"])
    assert identity(a) != identity(b) and a["id"] == b["id"]
    assert all(not (set(q["relevant"]) & set(q["forbidden"])) for q in data["queries"]["queries"])


def test_canonical_skill_fixtures_match_frozen_bodies_after_parser_normalization():
    from app.skills.parser import SkillParser

    parser = SkillParser(max_file_bytes=32000)
    records = load_dataset()["corpus"]["records"]
    for record in records:
        if record["source"] != "active_skill":
            continue
        directory = ROOT / "skills" / record["org_id"]
        parsed = parser.parse_file(directory / (record["id"] + ".md"), directory)
        assert parsed.body == record["content"].strip()
        assert parsed.metadata.trust_level == "trusted"


def test_frozen_input_tampering_fails_before_rankings(tmp_path):
    for name in (
        "corpus.json",
        "queries.json",
        "split.json",
        "settings.json",
        "freeze.json",
        "model-lock.json",
        "tasks.json",
    ):
        (tmp_path / name).write_bytes((ROOT / name).read_bytes())
    (tmp_path / "queries.json").write_text("{}\n")
    with pytest.raises(ValueError, match="frozen input changed"):
        load_dataset(tmp_path)


@pytest.mark.parametrize("fault", ["split", "forbidden", "answerability", "duplicates", "sentinel"])
def test_inconsistent_authored_labels_fail_even_with_matching_file_hashes(tmp_path, fault):
    seal = json.loads((ROOT / "freeze.json").read_text())
    for name in seal["sha256"]:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    if fault == "split":
        path = tmp_path / "split.json"
        data = json.loads(path.read_text())
        data["families"]["holdout"].append(data["families"]["dev"][0])
    else:
        path = tmp_path / "queries.json"
        data = json.loads(path.read_text())
        q = data["queries"][0]
        if fault == "forbidden":
            q["relevant"] = {q["forbidden"][0]: 3}
        elif fault == "answerability":
            q["relevant"] = {}
        elif fault == "duplicates":
            data["queries"][1]["id"] = q["id"]
        else:
            q["forbidden"].pop()
    path.write_text(json.dumps(data))
    seal["sha256"][path.name] = digest(path)
    (tmp_path / "freeze.json").write_text(json.dumps(seal))
    with pytest.raises(ValueError):
        load_dataset(tmp_path)


def test_report_schema_is_current_and_invalid_outcomes_fail():
    assert json.loads((ROOT / "report-schema.json").read_text()) == Report.model_json_schema()
    with pytest.raises(ValidationError):
        attempt().model_copy(update={"status": "invented"})  # copy deliberately skips validation
        Attempt.model_validate({**attempt().model_dump(), "status": "invented"})
    with pytest.raises(ValidationError):
        Attempt.model_validate({**attempt().model_dump(), "latency_ms": -1})


def test_family_cluster_bootstrap_pairs_queries_deterministically():
    rows = [attempt(["a", "b"], arm="hybrid"), attempt([], arm="markdown")]
    result = paired_comparison([query()], rows, samples=100, seed=7)
    assert result["families"] == 1
    assert result["recall_delta"] == 1
    assert result["family_bootstrap_95_percentile"] == [1, 1]
    assert result == paired_comparison([query()], rows, samples=100, seed=7)


def load_solution(name):
    path = ROOT / "tasks" / (name + ".py")
    spec = importlib.util.spec_from_file_location("fictional_" + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("task", coding.tasks(), ids=lambda t: t["id"])
def test_twelve_behavioral_task_checks_accept_oracle_and_reject_broken_solution(task):
    result = coding.check_solution(load_solution("reference"), task)
    assert result["success"] and result["checks"] >= 2
    assert not coding.check_solution(load_solution("starter"), task)["success"]
    # A universal constant cannot pass even if one ordinary example matches.
    broken = SimpleNamespace(**{task["id"]: lambda *args: task["cases"][0].get("expected")})
    assert not coding.check_solution(broken, task)["success"]


def test_paired_plan_has_108_fresh_equivalent_budgeted_attempts_and_missing_is_failure():
    plan = coding.paired_plan(
        agent="fictional-agent@1",
        model="fictional-model@1",
        repo_commit="a" * 40,
        budget=dict(seconds=60, input_tokens=4000, output_tokens=1000, money_total=1),
    )
    attempts = plan["attempts"]
    assert len(attempts) == len({a["workspace"] for a in attempts}) == 108
    for task in coding.tasks():
        rows = [a for a in attempts if a["task_id"] == task["id"]]
        assert len(rows) == 9 and all(a["fresh_session"] for a in rows)
        assert len({a["prompt_sha256"] for a in rows}) == 1
        assert all(a["budget"] == rows[0]["budget"] for a in rows)
    assert coding.reconcile(plan, [])["success_by_arm"] == dict(
        repository=0, markdown=0, platform=0
    )
    assert not coding.reconcile(plan, [])["complete"]
    with pytest.raises(ValueError):
        coding.reconcile(plan, [dict(id="unknown", status="ok")])


def test_paired_study_requires_explicit_pins_and_budget():
    with pytest.raises(ValueError):
        coding.paired_plan(agent="", model="", repo_commit="HEAD", budget={})
    with pytest.raises(ValueError):
        coding.paired_plan(agent="x", model="y", repo_commit="a" * 40, budget={})


def test_coding_reconciliation_requires_all_real_checks_and_preserves_failed_attempts():
    plan = coding.paired_plan(
        agent="fictional@1",
        model="fictional@1",
        repo_commit="a" * 40,
        budget=dict(seconds=60, input_tokens=4000, output_tokens=1000, money_total=1),
    )
    rows = [
        dict(id=a["id"], status="error", checks=0, passed=0, elapsed_seconds=2)
        for a in plan["attempts"]
    ]
    first = plan["attempts"][0]
    count = next(len(t["cases"]) for t in coding.tasks() if t["id"] == first["task_id"])
    rows[0].update(status="ok", checks=count, passed=count)
    r = coding.reconcile(plan, rows)
    assert r["complete"] and r["planned"] == r["recorded"] == 108
    assert sum(r["success_by_arm"].values()) == 1
    assert len(r["paired"]) == 36
    rows[0].update(checks=1, passed=1)  # a partial checker is not a task pass
    assert sum(coding.reconcile(plan, rows)["success_by_arm"].values()) == 0
    rows[0].update(checks=count, passed=count, elapsed_seconds=61)
    assert sum(coding.reconcile(plan, rows)["success_by_arm"].values()) == 0
    rows[0].update(status="timeout")
    assert coding.reconcile(plan, rows)["rows"][0]["status"] == "timeout"
    with pytest.raises(ValueError):
        coding.reconcile(plan, [*rows, rows[0]])


def test_model_cache_identity_rejects_missing_or_modified_artifacts(tmp_path):
    from benchmarks.retrieval.model import model_identity

    with pytest.raises(ValueError):
        model_identity(str(tmp_path))
    with pytest.raises(ValueError):
        model_identity("relative")


def test_model_identity_detects_modified_weights_before_any_load(tmp_path, monkeypatch):
    from benchmarks.retrieval import model

    lock = tmp_path / "lock"
    lock.mkdir()
    cache = tmp_path / "cache"
    snapshot = cache / "models--sentence-transformers--all-MiniLM-L6-v2/snapshots/frozen"
    snapshot.mkdir(parents=True)
    weights = snapshot / "model.safetensors"
    weights.write_bytes(b"fixture-only-identity-no-vectors")
    (lock / "model-lock.json").write_text(
        json.dumps(
            dict(name="fixture-only", revision="frozen", sha256={weights.name: digest(weights)})
        )
    )
    monkeypatch.setattr(model, "ROOT", lock)
    assert model.model_identity(str(cache))["bytes"] == weights.stat().st_size
    weights.write_bytes(b"changed")
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        model.model_identity(str(cache))


def test_model_loader_uses_hashed_snapshot_instead_of_mutable_cache_alias(monkeypatch):
    from app.embeddings.config import EmbeddingConfiguration
    from benchmarks.retrieval import model

    seen = []
    sentinel = object()

    def load(configuration, device):
        seen.append((configuration.model, configuration.allow_downloads, device))
        return sentinel

    monkeypatch.setattr(model, "load_sentence_transformer", load)
    config = EmbeddingConfiguration(model="canonical-name", dimensions=384, allow_downloads=False)
    assert model.load_pinned_model(config, "cpu", "/explicit/hashed/snapshot") is sentinel
    assert seen == [("/explicit/hashed/snapshot", False, "cpu")]
    assert config.model == "canonical-name"


def test_public_corpus_source_hashes_are_not_placeholder_labels():
    seal = load_dataset()["freeze"]
    assert seal["labels_authored_before_rankings"] is True
    assert seal["sha256"]["corpus.json"] == digest(ROOT / "corpus.json")
    assert Path("benchmarks/retrieval/LICENSE").read_bytes() == Path("LICENSE").read_bytes()
