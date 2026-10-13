"""Repeatable chapter 16 commands. Credentials come from api_client's token file.

The scaffold refuses occupied directories. Approval and publication require a
revision supplied by a human; no command contacts a social network.
"""

import argparse
import json
import os
import re
import shutil
from pathlib import Path
from urllib.parse import urlencode

from api_client import DemoApi, DemoApiError


class Walkthrough:
    def __init__(self, api=None):
        self.api = api
        self.project = self.identifier("ACP_PROJECT_ID")
        self.profile = self.identifier("ACP_PROFILE_ID")
        self.content = self.identifier("ACP_CONTENT_ID")
        self.work = Path(os.environ["ACP_WORK_DIR"]).expanduser()
        if not self.work.is_absolute():
            raise ValueError("ACP_WORK_DIR must be an absolute path")
        self.platform = os.environ.get("ACP_CONTENT_PLATFORM", "linkedin")
        if self.platform not in {"linkedin", "medium", "github"}:
            raise ValueError("Choose linkedin, medium or github")
        self.title = os.environ.get("ACP_CONTENT_TITLE", "One retry, one note")
        self.query = "?" + urlencode({"profile_id": self.profile})
        self.path = "/api/v2/content/" + self.content

    @staticmethod
    def identifier(name):
        value = os.environ[name]
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,79}", value):
            raise ValueError(name + " must use 1-80 lowercase letters, digits and hyphens")
        return value

    def request(self, method, path, body=None):
        if self.api is None:
            self.api = DemoApi()
        return self.api.request(method, path, body)

    def optional(self, path):
        try:
            return self.request("GET", path)
        except DemoApiError as error:
            if error.status != 404:
                raise
            return None

    def folder(self):
        if not self.work.is_dir():
            raise ValueError("Choose an existing repository or run scaffold first")
        folder = self.work / ".agent-context"
        if folder.is_symlink():
            raise ValueError(".agent-context must not be a symlink")
        folder.mkdir(exist_ok=True)
        ignore = self.work / ".gitignore"
        if ignore.is_symlink():
            raise ValueError(".gitignore must not be a symlink")
        text = ignore.read_text() if ignore.exists() else ""
        if "/.agent-context/" not in text.splitlines():
            with ignore.open("a") as stream:
                stream.write("\n/.agent-context/\n")
        return folder

    def local_file(self, name):
        path = self.folder() / name
        if path.is_symlink():
            raise ValueError("Local walkthrough files must not be symlinks")
        return path

    def scaffold(self):
        if self.work.is_symlink() or (self.work.exists() and any(self.work.iterdir())):
            raise ValueError("New-project folder must be empty; existing code is never replaced")
        source = Path(__file__).resolve().parents[3] / "examples" / "starter-notes-api"
        self.work.mkdir(parents=True, exist_ok=True)
        for name in ("notes_app.py", "test_app.py", "requirements.txt", "README.md", ".gitignore"):
            shutil.copyfile(source / name, self.work / name)
        self.folder()
        print("Notes API copied. Next install its requirements and run its tests.")

    def workspace(self):
        self.folder()
        print("Existing repository kept; .agent-context/ is ready and locally ignored.")

    def register(self):
        path = "/api/v1/projects/" + self.project
        item = self.optional(path)
        repository = os.environ["ACP_REPO_URL"]
        if item is None:
            item = self.request(
                "POST",
                "/api/v1/projects",
                {
                    "project_id": self.project,
                    "name": os.environ["ACP_PROJECT_NAME"],
                    "description": "Project used in the end-to-end guide; facts require review.",
                    "repository": repository,
                    "environment": "development",
                    "tags": ["walkthrough"],
                },
            )
        if item["repository"] != repository:
            raise ValueError("Project ID already belongs to another repository; choose its real ID")
        profile = self.optional("/api/v2/creator-profiles/" + self.profile)
        if profile is None:
            self.request(
                "POST",
                "/api/v2/creator-profiles",
                {
                    "profile_id": self.profile,
                    "name": "Project writing profile",
                    "audiences": ["backend engineers"],
                    "domains": ["software engineering"],
                    "writing_preferences": ["Explain one verified mechanism with a small example."],
                    "language_preferences": ["English"],
                },
            )
        print("Project and writing profile ready; existing profile preferences were preserved.")

    def save_fact(self):
        text = self.local_file("fact.md").read_text().strip()
        if not text:
            raise ValueError("Review and fill .agent-context/fact.md first")
        item = self.request(
            "POST",
            "/api/v1/context",
            {
                "project_id": self.project,
                "type": "implementation",
                "title": "Verified lesson: " + self.title,
                "content": text,
                "importance": 0.8,
                "source": "human-review",
                "tags": ["verified-lesson"],
                "external_id": self.content + "-fact",
            },
        )
        if item["content"] != text:
            raise ValueError(
                "That fact ID has different text. Use a new content ID or guarded context PATCH"
            )
        self.local_file(self.content + "-source.json").write_text(
            json.dumps(
                {
                    "project_id": self.project,
                    "context_id": item["context_id"],
                },
                indent=2,
            )
            + "\n"
        )
        print("Fact saved. context_id:", item["context_id"])

    def source(self):
        saved = json.loads(self.local_file(self.content + "-source.json").read_text())
        if saved["project_id"] != self.project:
            raise ValueError("Saved fact belongs to another project")
        item = self.request(
            "GET",
            "/api/v1/context/"
            + saved["context_id"]
            + "?"
            + urlencode({"project_id": self.project}),
        )
        if (
            item["status"] != "active"
            or item["content"] != self.local_file("fact.md").read_text().strip()
        ):
            raise ValueError("Source changed or is inactive; review and save the fact again")
        return {
            "source_type": "project_context",
            "source_id": item["context_id"],
            "project_id": self.project,
        }

    def ensure_idea(self, ref):
        identity = self.content + "-idea"
        origin = {
            "type": "project_context",
            "project_id": self.project,
            "source_ids": [ref["source_id"]],
        }
        try:
            self.request(
                "POST",
                "/api/v2/content-ideas",
                {
                    "profile_id": self.profile,
                    "idea_id": identity,
                    "title": self.title,
                    "description": "Explain the human-reviewed project lesson.",
                    "platforms": [self.platform],
                    "origin": origin,
                    "priority": 0.7,
                },
            )
            return identity
        except DemoApiError as error:
            if error.status != 409:
                raise
        cursor = None
        while True:
            query = {"profile_id": self.profile, "limit": 50}
            if cursor:
                query["cursor"] = cursor
            page = self.request("GET", "/api/v2/content-ideas?" + urlencode(query))
            for item in page["items"]:
                if item["idea_id"] == identity:
                    if item["origin"] != origin or item["platforms"] != [self.platform]:
                        raise ValueError(
                            "Existing idea has another origin or platform; use a new content ID"
                        )
                    return identity
            cursor = page.get("next_cursor")
            if not cursor:
                raise ValueError("Idea conflict could not be resolved; inspect authorized records")

    def checked_content(self):
        item = self.request("GET", self.path + self.query)
        if item["project_ids"] != [self.project] or item["platform"] != self.platform:
            raise ValueError("Content belongs to another project or destination")
        return item

    def save_draft(self):
        body = self.local_file("draft.md").read_text().strip()
        if not body:
            raise ValueError("Write .agent-context/draft.md first")
        ref = self.source()
        idea = self.ensure_idea(ref)
        brief_id = self.content + "-brief"
        brief_path = "/api/v2/content-briefs/" + brief_id
        brief = self.optional(brief_path + self.query)
        if brief is None:
            brief = self.request(
                "POST",
                "/api/v2/content-briefs",
                {
                    "profile_id": self.profile,
                    "brief_id": brief_id,
                    "idea_id": idea,
                    "platform": self.platform,
                    "objective": "Teach the verified project lesson.",
                    "primary_message": "Explain the observed behavior and its limits.",
                    "audience": ["backend engineers"],
                    "source_refs": [ref],
                    "supporting_points": [
                        "Use fact.md; do not invent performance or production claims."
                    ],
                },
            )
        if brief["source_refs"] != [ref] or brief["platform"] != self.platform:
            raise ValueError("Existing brief has another source or platform; use a new content ID")
        item = self.optional(self.path + self.query)
        if item is None:
            item = self.request(
                "POST",
                "/api/v2/content",
                {
                    "profile_id": self.profile,
                    "content_id": self.content,
                    "brief_id": brief_id,
                    "platform": self.platform,
                    "content_type": "post" if self.platform == "linkedin" else "article",
                    "title": self.title,
                    "body": body,
                    "project_ids": [self.project],
                    "source_refs": [ref],
                    "tags": ["walkthrough"],
                },
            )
        else:
            item = self.checked_content()
            if item["source_refs"] != [ref] or item["title"] != self.title:
                raise ValueError("Existing draft has another source or title; use a new content ID")
            if item["status"] in {"published", "archived"}:
                raise ValueError("Use a new content ID for a published or archived item")
            if item["body"] != body:
                self.request(
                    "POST",
                    self.path + "/versions",
                    {
                        "profile_id": self.profile,
                        "expected_revision": item["revision"],
                        "body": body,
                        "change_reason": "Human updated the local draft after review.",
                    },
                )
                item = self.checked_content()
        if item["status"] == "drafting":
            item = self.request(
                "PATCH",
                self.path,
                {
                    "profile_id": self.profile,
                    "expected_revision": item["revision"],
                    "status": "review",
                },
            )
        print("Draft stored. status:", item["status"], "revision:", item["revision"])

    def review(self):
        item = self.checked_content()
        print("Review this exact revision:", item["revision"], "status:", item["status"])
        print("Title:", item["title"], "\n\n" + item["body"])
        scan = self.request("POST", self.path + "/publication-review", {"profile_id": self.profile})
        print("\nPublication scan (redacted findings):\n" + json.dumps(scan, indent=2))

    def approve(self, revision):
        item = self.checked_content()
        if item["revision"] != revision or item["status"] != "review":
            raise ValueError(
                "Review the current revision first; approval needs its exact review revision"
            )
        result = self.request(
            "POST",
            self.path + "/approve",
            {
                "profile_id": self.profile,
                "expected_revision": revision,
            },
        )
        print("Approved revision:", result["content"]["revision"])

    def export(self):
        item = self.checked_content()
        if item["status"] not in {"approved", "published"}:
            raise ValueError("Approve the current content before exporting")
        result = self.request(
            "POST",
            self.path + "/export",
            {
                "profile_id": self.profile,
                "mode": "markdown",
            },
        )
        if self.checked_content()["revision"] != item["revision"]:
            raise ValueError(
                "Content changed during export; review and export its current revision"
            )
        path = self.local_file(self.content + "-approved-r" + str(item["revision"]) + ".md")
        if path.exists() and path.read_text() != result["text"]:
            raise ValueError("Export file already has different text; it was not overwritten")
        path.write_text(result["text"])
        print("Exported:", path)
        print("If you post it manually, record this revision:", item["revision"])

    def published(self, revision):
        item = self.checked_content()
        if item["revision"] != revision or item["status"] != "approved":
            raise ValueError("Record only the exact approved revision you actually posted")
        result = self.request(
            "POST",
            self.path + "/publish",
            {
                "profile_id": self.profile,
                "expected_revision": revision,
                "provider": "manual",
                "idempotency_key": self.content + "-posted-r" + str(revision),
            },
        )
        print("Manual publication recorded:", result["content"]["status"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in (
        "scaffold",
        "workspace",
        "register",
        "save-fact",
        "save-draft",
        "review",
        "export",
    ):
        commands.add_parser(name)
    commands.add_parser("approve").add_argument("--reviewed-revision", type=int, required=True)
    commands.add_parser("published").add_argument("--posted-revision", type=int, required=True)
    args = parser.parse_args()
    try:
        guide = Walkthrough()
        if args.command == "approve":
            guide.approve(args.reviewed_revision)
        elif args.command == "published":
            guide.published(args.posted_revision)
        else:
            getattr(guide, args.command.replace("-", "_"))()
    except (ValueError, KeyError, OSError, DemoApiError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
