"""Source search must preserve the parser/import contract and technical query meaning."""

import json
from pathlib import Path

import pytest

from app.skills.catalog import load_catalog, search_catalog
from app.skills.errors import SkillDirectoryError, SkillParseError
from scripts.skill_catalog import main

ROOT = Path(__file__).resolve().parents[2] / "skills"


@pytest.fixture
def catalog():
    return load_catalog(ROOT)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("django async", "django-async"),
        ("fastapi lifespan", "fastapi-async-lifecycle"),
        ("postgresql backfill", "postgresql-migrations"),
        ("content review", "content-review"),
        ("typescript contracts", "typescript-contracts"),
        ("django-rest-framework", "django-rest-framework"),
    ],
)
def test_technical_queries_rank_applicable_guidance(catalog, query, expected):
    matches = search_catalog(catalog, query)
    assert matches[0].entry.skill.metadata.id == expected
    assert matches[0].score > 0
    assert matches[0].sections


def test_filter_intersection_and_no_answer_queries(catalog):
    matches = search_catalog(catalog, "django", category="django", tags=("async", "testing"))
    assert [match.entry.skill.metadata.id for match in matches] == ["django-testing"]
    assert not search_catalog(catalog, "django", category="frontend")
    assert not search_catalog(catalog, "unknowntermnohit")
    assert not search_catalog(catalog, ".*()[]")
    assert not search_catalog(catalog, "the and of")


def test_empty_query_is_stable_bounded_discovery(catalog):
    all_matches = search_catalog(catalog)
    assert len(all_matches) == len(catalog)
    assert [item.entry.skill.metadata.id for item in all_matches] == sorted(
        item.skill.metadata.id for item in catalog
    )
    assert len(search_catalog(catalog, limit=3)) == 3
    assert all(item.entry.chunk_count > 0 for item in all_matches)
    assert len({item.entry.skill.metadata.id for item in all_matches}) == len(catalog)


def test_query_normalization_and_question_words(catalog):
    assert search_catalog(catalog, "How to use Django async") == search_catalog(
        catalog, "django async"
    )
    assert search_catalog(catalog, "SYNC_TO_ASYNC")[0].entry.skill.metadata.id == "django-async"


@pytest.mark.parametrize("kwargs", [{"limit": 0}, {"limit": 501}, {"query": "x" * 501}])
def test_search_rejects_unbounded_inputs(catalog, kwargs):
    with pytest.raises(ValueError):
        search_catalog(catalog, **kwargs)


def test_source_catalog_rejects_duplicate_ids_before_returning_results(tmp_path):
    root = tmp_path / "skills"
    root.mkdir()
    content = (ROOT / "django/async-django.md").read_text()
    (root / "first.md").write_text(content)
    (root / "second.md").write_text(content)
    with pytest.raises(SkillParseError, match="multiple canonical files"):
        load_catalog(root)


def test_source_catalog_rejects_external_symlink(tmp_path):
    root = tmp_path / "skills"
    root.mkdir()
    (root / "escape.md").symlink_to(ROOT / "django/async-django.md")
    with pytest.raises(SkillDirectoryError):
        load_catalog(root)


def test_cli_reports_hashes_and_optional_bodies_without_an_api(capsys):
    assert main(["--json", "search", "django-async", "--limit", "1", "--content"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["count"] == 1
    row = data["items"][0]
    assert row["id"] == "django-async"
    assert len(row["content_hash"]) == 64
    assert row["source_path"] == "skills/django/async-django.md"
    assert row["body"] and row["chunks"] > 0


def test_invalid_source_fails_without_printing_its_body(tmp_path, capsys):
    path = tmp_path / "broken.md"
    path.write_text("sensitive-source-marker\n")
    assert main(["--directory", str(tmp_path), "validate"]) == 1
    output = capsys.readouterr()
    assert "Catalog error" in output.err
    assert "sensitive-source-marker" not in output.err + output.out
