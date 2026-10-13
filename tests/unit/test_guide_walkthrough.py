"""Validate real request schemas and the starter helper's meaningful safety guards."""

import copy
import importlib
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from app.schemas.content import (
    ContentApprovalRequest,
    ContentBriefCreateRequest,
    ContentExportRequest,
    ContentIdeaCreateRequest,
    ContentItemCreateRequest,
    ContentItemUpdateRequest,
    ContentPublicationReviewRequest,
    ContentPublishRequest,
    ContentVersionCreateRequest,
)
from app.schemas.context import ContextCreateRequest
from app.schemas.creator import CreatorProfileCreateRequest
from app.schemas.project import ProjectCreateRequest

ROOT = Path(__file__).resolve().parents[2]


class Api:
    def __init__(self, error_class):
        self.error_class = error_class
        self.records = {}
        self.calls = []
        self.race_on_export = False

    def request(self, method, path, body=None):
        self.calls.append((method, path, copy.deepcopy(body)))
        route = urlsplit(path).path
        if method == "GET":
            if route == "/api/v2/content-ideas":
                return {"items": self.records.get("ideas", []), "next_cursor": None}
            if route not in self.records:
                raise self.error_class(method, path, 404)
            return copy.deepcopy(self.records[route])
        schemas = {
            "/api/v1/projects": ProjectCreateRequest,
            "/api/v1/context": ContextCreateRequest,
            "/api/v2/creator-profiles": CreatorProfileCreateRequest,
            "/api/v2/content-ideas": ContentIdeaCreateRequest,
            "/api/v2/content-briefs": ContentBriefCreateRequest,
            "/api/v2/content": ContentItemCreateRequest,
        }
        if route in schemas:
            schemas[route].model_validate(body)
            item = copy.deepcopy(body)
            if route == "/api/v1/context":
                item.update(context_id="fact-1", status="active", revision=1)
                item = self.records.setdefault("/api/v1/context/fact-1", item)
            elif route == "/api/v2/content-ideas":
                if self.records.get("ideas"):
                    raise self.error_class(method, path, 409)
                self.records["ideas"] = [item]
            else:
                field = {
                    "/api/v1/projects": "project_id",
                    "/api/v2/creator-profiles": "profile_id",
                    "/api/v2/content-briefs": "brief_id",
                    "/api/v2/content": "content_id",
                }[route]
                if route == "/api/v2/content":
                    item.update(status="drafting", revision=1, current_version=1)
                self.records[route + "/" + item[field]] = item
            return copy.deepcopy(item)
        content_path, _, action = route.partition("/content/")
        assert content_path == "/api/v2"
        identity, _, action = action.partition("/")
        item = self.records["/api/v2/content/" + identity]
        schema = {
            "": ContentItemUpdateRequest,
            "versions": ContentVersionCreateRequest,
            "approve": ContentApprovalRequest,
            "publication-review": ContentPublicationReviewRequest,
            "export": ContentExportRequest,
            "publish": ContentPublishRequest,
        }[action]
        schema.model_validate(body)
        if "expected_revision" in body:
            assert body["expected_revision"] == item["revision"]
        if action == "publication-review":
            return {"publishable": True, "findings": []}
        if action == "export":
            if self.race_on_export:
                item["revision"] += 1
            return {"text": item["body"] + "\n"}
        if action == "versions":
            item.update(body=body["body"], status="drafting")
            item["current_version"] += 1
        elif action in {"approve", "publish"}:
            item["status"] = "approved" if action == "approve" else "published"
        else:
            item["status"] = body["status"]
        item["revision"] += 1
        return (
            {"content": copy.deepcopy(item)}
            if action in {"approve", "publish"}
            else copy.deepcopy(item)
        )


@pytest.fixture
def guide(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(ROOT / "docs/guide/examples"))
    module = importlib.import_module("walkthrough")
    for name, value in {
        "ACP_PROJECT_ID": "notes-api",
        "ACP_PROFILE_ID": "writer",
        "ACP_CONTENT_ID": "notes-first-lesson",
        "ACP_WORK_DIR": str(tmp_path / "notes"),
        "ACP_PROJECT_NAME": "Notes",
        "ACP_REPO_URL": "https://example.org/notes",
    }.items():
        monkeypatch.setenv(name, value)
    api = Api(module.DemoApiError)
    return module.Walkthrough(api), api


def prepare(guide):
    helper, api = guide
    helper.scaffold()
    helper.register()
    helper.local_file("fact.md").write_text("Verified local behavior, with its teaching limits.")
    helper.save_fact()
    helper.local_file("draft.md").write_text("A reviewed draft explaining the verified example.")
    helper.save_draft()
    return helper, api


@pytest.mark.parametrize("platform", ["linkedin", "medium", "github"])
def test_complete_flow_uses_valid_schemas_and_explicit_revisions(guide, platform):
    helper, api = guide
    helper.platform = platform
    prepare(guide)
    helper.review()
    helper.approve(2)
    helper.export()
    helper.published(3)
    assert helper.checked_content()["status"] == "published"
    assert helper.local_file(helper.content + "-approved-r3.md").exists()
    assert (
        api.records["/api/v2/content/" + helper.content]["source_refs"][0]["source_id"] == "fact-1"
    )


def test_scaffold_refuses_occupied_folder_and_keeps_existing_code(guide):
    helper, _ = guide
    helper.work.mkdir()
    original = helper.work / "app.py"
    original.write_text("existing code")
    with pytest.raises(ValueError, match="empty"):
        helper.scaffold()
    helper.workspace()
    assert original.read_text() == "existing code"
    helper.workspace()
    assert (helper.work / ".gitignore").read_text().count("/.agent-context/") == 1


def test_register_is_repeatable_and_preserves_existing_preferences(guide):
    helper, api = prepare(guide)
    api.records["/api/v2/creator-profiles/writer"]["audiences"] = ["real existing audience"]
    helper.register()
    assert api.records["/api/v2/creator-profiles/writer"]["audiences"] == ["real existing audience"]
    api.records["/api/v1/projects/notes-api"]["repository"] = "https://example.org/another"
    with pytest.raises(ValueError, match="another repository"):
        helper.register()


def test_identical_retry_reuses_records_and_changed_text_makes_new_version(guide):
    helper, api = prepare(guide)
    helper.save_fact()
    helper.save_draft()
    assert helper.checked_content()["revision"] == 2
    helper.approve(2)
    helper.local_file("draft.md").write_text("A corrected local draft.")
    helper.save_draft()
    assert helper.checked_content()["status"] == "review"
    assert helper.checked_content()["current_version"] == 2
    assert helper.checked_content()["revision"] == 5
    assert any(path.endswith("/versions") for _, path, _ in api.calls)


def test_changed_fact_cannot_silently_reuse_old_context(guide):
    helper, _ = prepare(guide)
    helper.local_file("fact.md").write_text("Changed fact")
    with pytest.raises(ValueError, match="different text"):
        helper.save_fact()
    with pytest.raises(ValueError, match="Source changed"):
        helper.save_draft()


@pytest.mark.parametrize("action", ["approve", "published"])
def test_stale_human_revision_never_triggers_mutation(guide, action):
    helper, api = prepare(guide)
    before = len(api.calls)
    with pytest.raises(ValueError):
        getattr(helper, action)(999)
    assert all(method == "GET" for method, _, _ in api.calls[before:])


def test_export_requires_approval_and_preserves_changed_local_file(guide):
    helper, _ = prepare(guide)
    with pytest.raises(ValueError, match="Approve"):
        helper.export()
    helper.approve(2)
    helper.export()
    path = helper.local_file(helper.content + "-approved-r3.md")
    path.write_text("human edited export")
    with pytest.raises(ValueError, match="not overwritten"):
        helper.export()
    assert path.read_text() == "human edited export"


def test_concurrent_export_change_never_receives_old_revision_label(guide):
    helper, api = prepare(guide)
    helper.approve(2)
    api.race_on_export = True
    with pytest.raises(ValueError, match="changed during export"):
        helper.export()
    assert not list(helper.folder().glob("*approved*.md"))


def test_wrong_project_and_symlink_workspace_are_refused(guide, tmp_path):
    helper, api = prepare(guide)
    api.records[helper.path]["project_ids"] = ["another-project"]
    with pytest.raises(ValueError, match="another project"):
        helper.approve(2)
    shutil_target = tmp_path / "elsewhere"
    shutil_target.mkdir()
    for path in helper.folder().iterdir():
        path.unlink()
    helper.folder().rmdir()
    (helper.work / ".agent-context").symlink_to(shutil_target)
    with pytest.raises(ValueError, match="symlink"):
        helper.workspace()
