"""Run after website/build.py; real source/site contracts, not runtime acceptance."""

import hashlib
import json
import re
import tarfile
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt

from scripts import prepare_open_source_release as release
from website.build import DocumentLinks

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "dist/site"
RENDERER = MarkdownIt("commonmark").enable("table")


def markdown_contract(path):
    tokens = RENDERER.parse(path.read_text())
    headings = set()
    counts = {}
    links = []
    for index, token in enumerate(tokens):
        if token.type == "heading_open":
            inline = tokens[index + 1]
            label = "".join(
                child.content for child in inline.children or [] if child.type != "html_inline"
            ).lower()
            slug = re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", label))
            count = counts.get(slug, 0)
            counts[slug] = count + 1
            headings.add(slug + (f"-{count}" if count else ""))
        for child in token.children or []:
            if child.type in {"link_open", "image"}:
                links.append(child.attrGet("href") or child.attrGet("src"))
    return headings, links


def test_maintained_registry_links_anchors_logo_and_hashes_are_current():
    registry = json.loads((ROOT / "docs/guide/public-sources.json").read_text())
    manifest = json.loads((ROOT / "docs/guide/source-manifest.json").read_text())
    required = {
        "docs/dashboard.md",
        "docs/organization-management.md",
        "docs/dashboard-deployment.md",
        "docs/migration-recovery.md",
        "docs/verification.md",
        "docs/evaluation.md",
        "docs/client-onboarding.md",
        "docs/demo.md",
        "docs/retrieval-evaluation.md",
        "docs/alternatives.md",
    }
    assert required <= set(registry)
    assert {row["path"] for row in manifest} == set(registry)
    assert all(release.eligible(PurePosixPath(name)) for name in registry)
    for row in manifest:
        assert hashlib.sha256((ROOT / row["path"]).read_bytes()).hexdigest() == row["sha256"]
    contracts = {
        ROOT / name: markdown_contract(ROOT / name)
        for name in release.audited_files(ROOT)
        if name.suffix == ".md"
    }
    broken = []
    for path, (_, links) in contracts.items():
        for href in links:
            url = urlsplit(href)
            if url.scheme or url.netloc:
                continue
            # Narrative links render from the generated guide, while embedded
            # source documents retain their original directory contract.
            base = (
                ROOT / "docs/usage-guide.html" if path == ROOT / "docs/guide/content.md" else path
            )
            target = (base.parent / unquote(url.path)).resolve() if url.path else base
            if not target.is_relative_to(ROOT) or not target.exists():
                broken.append(f"{path.relative_to(ROOT)} -> {href}")
            elif url.fragment and target.suffix == ".md":
                anchors = contracts.get(target, markdown_contract(target))[0]
                if unquote(url.fragment) not in anchors:
                    broken.append(f"{path.relative_to(ROOT)} -> {href}")
    assert not broken, "Broken maintained-document links:\n" + "\n".join(broken)
    assert (SITE / "assets/favicon.svg").read_bytes() == (
        ROOT / "website/assets/favicon.svg"
    ).read_bytes()
    guide = (SITE / "docs/usage-guide.html").read_text()
    assert (ROOT / "website/assets/favicon.svg").read_text().strip().replace(
        "<svg ",
        '<svg class="brand-mark" width="38" height="38" aria-hidden="true" focusable="false" ',
        1,
    ) in guide
    parser = DocumentLinks()
    parser.feed(guide)
    for name in required:
        identity = "source-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        assert identity in parser.ids


def test_extracted_site_download_is_exact_audited_source_with_frontend_licenses(tmp_path):
    archive = SITE / "downloads/agent-context-platform-source.tar.gz"
    checksum = (SITE / "downloads/SHA256SUMS").read_text()
    assert checksum == hashlib.sha256(archive.read_bytes()).hexdigest() + "  " + archive.name + "\n"
    with tarfile.open(archive, "r:gz") as contents:
        members = contents.getmembers()
        assert members and all(member.isfile() for member in members)
        assert all(member.uid == member.gid == member.mtime == 0 for member in members)
        for member in members:
            relative = PurePosixPath(member.name).relative_to("agent-context-platform")
            assert release.eligible(relative), relative
        contents.extractall(tmp_path, filter="data")
    extracted = tmp_path / "agent-context-platform"
    expected = release.audited_files(ROOT)
    assert release.audited_files(extracted) == expected
    assert release.DASHBOARD_REQUIRED <= {str(name) for name in expected}
    assert any(str(name).startswith("dashboard/src/") for name in expected)
    assert any(str(name).startswith("dashboard/licenses/") for name in expected)
    repeat = tmp_path / "repeat.tar.gz"
    release.write_archive(extracted, release.audited_files(extracted), repeat)
    assert repeat.read_bytes() == archive.read_bytes()


def test_site_manifest_and_html_links_cover_real_export_without_runtime_state():
    manifest = json.loads((SITE / "site-manifest.json").read_text())
    actual = {str(path.relative_to(SITE)) for path in SITE.rglob("*") if path.is_file()}
    assert {row["path"] for row in manifest} == actual - {"site-manifest.json"}
    documents = {}
    for row in manifest:
        path = SITE / row["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"]
        parts = PurePosixPath(row["path"]).parts
        assert not (set(parts) & release.PRIVATE_PARTS)
        if path.suffix == ".html":
            parser = DocumentLinks()
            parser.feed(path.read_text())
            documents[path] = parser
    for path, parser in documents.items():
        for href in parser.links:
            url = urlsplit(href)
            if url.scheme or url.netloc:
                continue
            target = (
                (SITE / unquote(url.path).lstrip("/"))
                if url.path.startswith("/")
                else (path.parent / unquote(url.path) if url.path else path)
            )
            target = target.resolve()
            if target.is_dir():
                target /= "index.html"
            assert target.is_relative_to(SITE) and target.is_file(), (path, href)
            if url.fragment and target in documents:
                assert unquote(url.fragment) in documents[target].ids, (path, href)


def test_first_handoff_examples_match_actual_mcp_schemas_and_fact():
    from jsonschema import Draft202012Validator

    from app.mcp.tools import TOOLS

    prose = (ROOT / "docs/guide/content.md").read_text()
    handoff = prose.split("### First handoff: a verified Notes API fact", 1)[1].split(
        "### Extend the pattern", 1
    )[0]
    calls = [json.loads(block) for block in re.findall(r"```json\n(.*?)\n```", handoff, re.S)]
    assert len(calls) == 3
    schemas = {tool.name: tool.input_schema for tool in TOOLS}
    for name, payload in zip(
        ("resolve_project", "save_context", "search_knowledge"), calls, strict=True
    ):
        Draft202012Validator(schemas[name]).validate(payload)
    fact = json.loads((ROOT / "examples/agent-handoff/fact.json").read_text())
    assert calls[1] == fact
    assert calls[0]["project_id"] == calls[2]["project_id"] == fact["project_id"]
    assert calls[2]["sources"] == ["project_context"]
    assert calls[2]["limit"] == 5 and calls[2]["token_budget"] == 2000
