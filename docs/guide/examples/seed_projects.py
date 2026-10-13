"""Seed the shipped payment fixture using authentication, plus two sample projects.

Run once on a development platform from the repository root:
  .venv/bin/python docs/guide/examples/seed_projects.py
Requires the operator scopes listed in chapter 02. Existing projects are read
and checked; context/memory creates use stable external IDs.
"""

import json
from pathlib import Path

from api_client import DemoApi


def ensure_project(api, project):
    try:
        api.request("POST", "/api/v1/projects", project)
    except RuntimeError as error:
        if "HTTP 409" not in str(error):
            raise
        existing = api.request("GET", "/api/v1/projects/" + project["project_id"])
        if existing["repository"] != project["repository"]:
            raise RuntimeError(
                "Existing project has a different repository; choose a new ID"
            ) from None


def main():
    api = DemoApi()
    fixture = json.loads(
        (
            Path(__file__).resolve().parents[3] / "examples/payment-service/knowledge.json"
        ).read_text()
    )
    ensure_project(api, fixture["project"])
    for item in fixture["context"]:
        api.request(
            "POST",
            "/api/v1/context",
            {"project_id": "payment-service", "source": "example", **item},
        )
    for item in fixture["memory"]:
        api.request("POST", "/api/v1/memory", {"source": "example", **item})
    for project_id, name, content, tags in [
        (
            "storefront-web",
            "Storefront Web",
            "Sample project: guest checkout must preserve keyboard focus "
            "and announce validation errors.",
            ["checkout", "accessibility"],
        ),
        (
            "analytics-pipeline",
            "Analytics Pipeline",
            "Sample project: partition daily imports by UTC date "
            "and deduplicate using immutable source event IDs.",
            ["pipeline", "idempotency"],
        ),
    ]:
        ensure_project(
            api,
            {
                "project_id": project_id,
                "name": name,
                "description": "Guide example; replace with your own project facts.",
                "repository": "https://github.com/example/" + project_id,
                "environment": "development",
                "tags": tags,
            },
        )
        api.request(
            "POST",
            "/api/v1/context",
            {
                "project_id": project_id,
                "type": "convention",
                "title": name + " example rule",
                "content": content,
                "importance": 0.8,
                "source": "example",
                "tags": tags,
                "external_id": project_id + "-guide-rule",
            },
        )
    print("Seeded three example projects. Context/memory creates are repeatable.")


if __name__ == "__main__":
    main()
