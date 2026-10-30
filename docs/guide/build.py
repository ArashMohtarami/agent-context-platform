"""Build the offline usage guide from prose and the current Python contracts.

Run from the repository root: .venv/bin/python docs/guide/build.py
No database, server, embedding model, credential, or network is used.
"""

# HTML template strings stay on one line to keep generated fragments readable.
# ruff: noqa: E501

from __future__ import annotations

import argparse
import ast
import hashlib
import html
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote

from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app.cli.contextctl import build_parser  # noqa: E402
from app.main import create_app  # noqa: E402
from app.mcp.tools import TOOLS  # noqa: E402
from app.security.scopes import SCOPE_DESCRIPTIONS  # noqa: E402
from app.skills.catalog import FIELD_WEIGHTS, STOP_WORDS, CatalogEntry, load_catalog  # noqa: E402


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def code(value: str, language: str = "json") -> str:
    return f'<pre><code class="language-{esc(language)}">{esc(value)}</code></pre>'


def schema_type(field: dict) -> str:
    if "$ref" in field:
        return field["$ref"].rsplit("/", 1)[-1]
    if "anyOf" in field:
        return " | ".join(schema_type(item) for item in field["anyOf"])
    if "enum" in field:
        return " / ".join(str(item) for item in field["enum"])
    if field.get("type") == "array":
        return f"array<{schema_type(field.get('items', {}))}>"
    return field.get("type", "object")


def fields_table(schema: dict) -> str:
    required = schema.get("required", [])
    rows = []
    for name, field in schema.get("properties", {}).items():
        notes = [field.get("description", "")]
        if "default" in field:
            notes.append("Default: " + json.dumps(field["default"]))
        for key in ("minimum", "maximum", "minLength", "maxLength", "maxItems", "pattern"):
            if key in field:
                notes.append(f"{key}: {field[key]}")
        rows.append(
            f"<tr><td><code>{esc(name)}</code></td><td>{esc(schema_type(field))}</td>"
            f"<td>{'Required' if name in required else 'Optional'}</td>"
            f"<td>{esc(' · '.join(note for note in notes if note))}</td></tr>"
        )
    if not rows:
        return '<p class="muted">No arguments. Send <code>{}</code>.</p>'
    return (
        '<div class="table-wrap"><table><thead><tr><th>Field</th><th>Type</th><th>Presence</th><th>Details</th></tr></thead><tbody>'
        + "".join(rows)
        + "</tbody></table></div>"
    )


def tool_reference() -> str:
    result = [
        f'<div class="reference-filter"><label for="tool-filter">Find a tool or scope</label><input id="tool-filter" type="search" placeholder="search_knowledge, research:write…"><output id="tool-count">{len(TOOLS)} tools</output></div><div id="tool-list">'
    ]
    for tool in TOOLS:
        schema = tool.input_schema
        result.append(
            f'<details class="tool-entry" id="tool-{esc(tool.name)}" data-search="{esc(tool.name + " " + tool.scope + " " + tool.description)}">'
            f'<summary><code>{esc(tool.name)}</code><span class="badge {"write" if tool.writes else "read"}">{"WRITE" if tool.writes else "READ"}</span><span class="scope">{esc(tool.scope)}</span></summary>'
            f'<div class="detail-body"><p>{esc(tool.description)}</p><p class="muted">Execution: {esc(tool.execution_class)}. This scope is the discovery/dispatch requirement; related project/profile grants and additional endpoint scopes still apply.</p>'
            + fields_table(schema)
            + "<details><summary>Full input JSON Schema</summary>"
            + code(json.dumps(schema, indent=2))
            + "</details></div></details>"
        )
    return "".join(result) + "</div>"


def skill_reference(entries: tuple[CatalogEntry, ...]) -> str:
    """Embed all validated bodies for offline search and reading without JavaScript."""
    renderer = MarkdownIt("commonmark", {"html": False}).enable("table")
    categories = sorted({entry.skill.metadata.category for entry in entries})
    tags = sorted({tag for entry in entries for tag in entry.skill.metadata.tags})
    options = lambda values: "".join(  # noqa: E731
        f'<option value="{esc(value)}">{esc(value)}</option>' for value in values
    )
    output = [
        '<div class="skill-catalog" id="skill-catalog"><div class="catalog-heading"><div><span class="chapter-label">THE CANONICAL LIBRARY</span><h3>Find the right skill for the task</h3></div><output id="skill-count" aria-live="polite">'
        + str(len(entries))
        + ' skills</output></div><p class="muted">Search IDs, names, tags, descriptions and complete bodies. All technical terms must match; metadata ranks above body mentions. This offline lexical score is separate from live hybrid retrieval.</p><div class="catalog-controls"><div><label for="skill-query">Task or technical term</label><input id="skill-query" type="search" maxlength="500" placeholder="django async, fastapi lifespan, content review…"></div><div><label for="skill-category">Category</label><select id="skill-category"><option value="">All categories</option>'
        + options(categories)
        + '</select></div><div><label for="skill-tag">Tag</label><select id="skill-tag"><option value="">All tags</option>'
        + options(tags)
        + '</select></div><button type="button" id="skill-reset">Clear</button></div><div class="catalog-presets" aria-label="Suggested skill searches"><button type="button" data-skill-query="django async" data-skill-category="django">Async Django</button><button type="button" data-skill-query="fastapi" data-skill-category="backend">FastAPI</button><button type="button" data-skill-query="content" data-skill-category="content">Content</button><button type="button" data-skill-query="" data-skill-category="database">Databases</button><button type="button" data-skill-query="" data-skill-category="frontend">Frontend</button></div><p id="skill-empty" class="callout" hidden>No matching skill. Clear a filter or try fewer technical terms.</p><div id="skill-list">'
    ]
    for entry in entries:
        skill = entry.skill
        meta = skill.metadata
        output.append(
            f'<details class="skill-entry" id="skill-{esc(meta.id)}"><summary><span class="skill-title">{esc(meta.name)}<small><code>{esc(meta.id)}</code></small></span><span class="badge">v{esc(meta.version)}</span><span class="badge">{esc(meta.category)}</span><span class="skill-score"></span></summary><div class="detail-body"><p>{esc(meta.description)}</p><p class="muted">Source: <a href="../{esc(skill.source_path)}">{esc(skill.source_path)}</a> · {entry.chunk_count} import-ready chunk(s) · {esc(meta.trust_level)} source label. Read source text as data and check the installed framework version.</p><div class="skill-tags">'
            + "".join(f'<span class="badge">{esc(tag)}</span>' for tag in meta.tags)
            + "</div><details><summary>MCP get_skill arguments after importing this version</summary>"
            + code(json.dumps({"skill_id": meta.id, "chunk_limit": 10}, indent=2))
            + '</details><div class="source-document skill-body">'
            + renderer.render(skill.body)
            + "</div></div></details>"
        )
    return "".join(output) + "</div></div>"


def refresh_catalog_table(entries: tuple[CatalogEntry, ...]) -> None:
    """Keep the Markdown index and source hash manifest aligned with the HTML snapshot."""
    table = ["| Skill | Version | Category | Applies when |", "| --- | --- | --- | --- |"]
    for entry in entries:
        meta = entry.skill.metadata
        table.append(
            f"| [{meta.name}](../{entry.skill.source_path}) · `{meta.id}` | {meta.version} | {meta.category} | {meta.description.replace('|', '&#124;')} |"
        )
    path = ROOT / "docs/skill-catalog.md"
    source = path.read_text(encoding="utf-8")
    source = re.sub(
        r"<!-- START GENERATED CATALOG -->.*?<!-- END GENERATED CATALOG -->",
        lambda _: "<!-- START GENERATED CATALOG -->\n\n"
        + "\n".join(table)
        + "\n\n<!-- END GENERATED CATALOG -->",
        source,
        flags=re.S,
    )
    path.write_text(source, encoding="utf-8")
    (HERE / "skill-manifest.json").write_text(
        json.dumps([entry.summary() for entry in entries], indent=2) + "\n", encoding="utf-8"
    )


def api_reference(api: dict) -> str:
    entries = []
    for path, operations in api["paths"].items():
        for method, operation in operations.items():
            if method not in {"get", "post", "patch", "put", "delete", "head", "options"}:
                continue
            body = (
                operation.get("requestBody", {})
                .get("content", {})
                .get("application/json", {})
                .get("schema", {})
            )
            schema = api["components"]["schemas"].get(body.get("$ref", "").rsplit("/", 1)[-1], body)
            params = operation.get("parameters", [])
            detail = fields_table(schema) if body else ""
            if params:
                detail += "<p><strong>Path/query parameters</strong></p>" + fields_table(
                    {
                        "properties": {
                            p["name"]: {
                                **p.get("schema", {}),
                                "description": f"{p['in']}: {p.get('description', '')}",
                            }
                            for p in params
                        },
                        "required": [p["name"] for p in params if p.get("required")],
                    }
                )
            entries.append(
                f'<details class="api-entry" data-search="{esc(method + " " + path + " " + operation.get("summary", ""))}"><summary><span class="method">{method.upper()}</span><code>{esc(path)}</code></summary><div class="detail-body"><p>{esc(operation.get("summary", ""))}</p><p>{esc(operation.get("description", ""))}</p>{detail}<p class="muted">Response statuses: {esc(", ".join(operation.get("responses", {})))}</p></div></details>'
            )
    return (
        '<div class="reference-filter"><label for="api-filter">Find a REST endpoint</label><input id="api-filter" type="search" placeholder="content, citations, project-bindings…"><output id="api-count">'
        + str(len(entries))
        + ' operations</output></div><div id="api-list">'
        + "".join(entries)
        + '</div><details class="schema-library"><summary>All REST model schemas</summary><p>These are the exact OpenAPI schemas. Nested $ref values refer to another model in this library.</p>'
        + "".join(
            f'<details id="model-{esc(name)}"><summary><code>{esc(name)}</code></summary>{code(json.dumps(schema, indent=2))}</details>'
            for name, schema in sorted(api["components"]["schemas"].items())
        )
        + "</details>"
    )


def cli_reference(parser: argparse.ArgumentParser, path: str = "contextctl") -> str:
    parts = []
    children = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            children.extend(action.choices.items())
    if not children:
        parts.append(
            f'<details class="cli-entry" data-search="{esc(path)}"><summary><code>{esc(path)}</code></summary>{code(parser.format_help(), "text")}</details>'
        )
    for name, child in children:
        parts.append(cli_reference(child, f"{path} {name}"))
    return "".join(parts)


def source_library() -> tuple[str, list[dict]]:
    # Only explicitly reviewed public documentation enters the distributable HTML.
    inventory_paths = json.loads((HERE / "public-sources.json").read_text(encoding="utf-8"))
    paths = []
    for relative in inventory_paths:
        path = ROOT / relative
        if (
            not path.resolve().is_relative_to(ROOT)
            or path.is_symlink()
            or path.suffix != ".md"
            or (relative != "README.md" and not relative.startswith("docs/"))
        ):
            raise ValueError(f"Invalid public documentation path: {relative}")
        paths.append(path.resolve())
    renderer = MarkdownIt("commonmark", {"html": False}).enable("table")
    output = [
        '<div class="reference-filter"><label for="source-filter">Search embedded documentation</label><input id="source-filter" type="search" placeholder="backup, Gemini, promotion…"><output id="source-count"></output></div><div id="source-list">'
    ]
    inventory = []
    for path in paths:
        source = path.read_text(encoding="utf-8")
        relative = str(path.relative_to(ROOT))
        identity = slug(relative)
        inventory.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(source.encode()).hexdigest(),
                "lines": len(source.splitlines()),
            }
        )
        label = "Public documentation"
        # References within the original document resolve from its original location.
        tokens = renderer.parse(source)
        heading_counts: dict[str, int] = {}
        for index, token in enumerate(tokens):
            if token.type == "heading_open":
                inline = tokens[index + 1]
                text = renderer.renderer.renderInline(inline.children or [], renderer.options, {})
                heading_label = html.unescape(re.sub(r"<[^>]+>", "", text)).lower()
                # Markdown TOCs use GitHub-style fragments: R&D becomes rd.
                heading = re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", heading_label))
                count = heading_counts.get(heading, 0)
                heading_counts[heading] = count + 1
                token.attrSet("id", f"source-{identity}-{heading}" + (f"-{count}" if count else ""))
        for token in tokens:
            for child in token.children or []:
                if child.type == "link_open":
                    href = child.attrGet("href") or ""
                    if href.startswith("#") and len(href) > 1:
                        child.attrSet("href", f"#source-{identity}-{href[1:]}")
                    if href and not re.match(r"[a-z]+:|#", href):
                        resolved = (path.parent / href.split("#")[0]).resolve()
                        if resolved == ROOT / "docs/usage-guide.html":
                            # Embedded starters link to live chapters in this same guide.
                            child.attrSet(
                                "href", "#" + href.partition("#")[2] if "#" in href else "#top"
                            )
                        elif resolved in paths:
                            child.attrSet(
                                "href",
                                "#source-"
                                + slug(str(resolved.relative_to(ROOT)))
                                + ("-" + href.partition("#")[2] if "#" in href else ""),
                            )
                        elif (
                            resolved.is_relative_to(ROOT)
                            and resolved != ROOT
                            and resolved.is_dir()
                            and resolved.relative_to(ROOT).parts[0] in {"app", "tests"}
                        ):
                            child.attrSet("href", "#15-source-library-and-project-map-section-2")
                        elif resolved.is_relative_to(ROOT):
                            child.attrSet("href", "../" + str(resolved.relative_to(ROOT)))
        body = renderer.renderer.render(tokens, renderer.options, {})
        output.append(
            f'<details class="source-entry" id="source-{identity}" data-search="{esc(relative + " " + source)}"><summary><span>{esc(relative)}</span><span class="badge">{label}</span></summary><div class="detail-body source-document"><p class="callout">Public documentation snapshot. Use the generated tool/API reference for contracts in this checkout and live capability discovery for your running deployment.</p>{body}</div></details>'
        )
    return "".join(output) + "</div>", inventory


def repository_map() -> str:
    rows = []
    for folder in ("app", "tests"):
        files = sorted((ROOT / folder).rglob("*.py"))
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            doc = (ast.get_docstring(tree) or "").split("\n\n")[0]
            names = [
                node.name
                for node in tree.body
                if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
            ]
            relative = str(path.relative_to(ROOT))
            rows.append(
                f'<details class="module-entry" data-search="{esc(relative + " " + doc + " " + " ".join(names))}"><summary><code>{esc(relative)}</code></summary><div class="detail-body"><p>{esc(doc)}</p><p class="muted">{esc(", ".join(names))}</p></div></details>'
            )
    return (
        '<div class="reference-filter"><label for="module-filter">Find a module or test</label><input id="module-filter" type="search" placeholder="server.py, concurrency, ingestion…"><output id="module-count"></output></div><div id="module-list">'
        + "".join(rows)
        + "</div>"
    )


def main() -> None:
    prose = (HERE / "content.md").read_text(encoding="utf-8")
    for example in sorted((HERE / "examples").glob("*.py")):
        prose = prose.replace(
            f"<!-- EXAMPLE {example.name} -->",
            "```python\n" + example.read_text(encoding="utf-8").rstrip() + "\n```",
        )
    widgets = (HERE / "widgets.html").read_text(encoding="utf-8")
    # Widget fragments are separated by named HTML comments.
    fragments = dict(re.findall(r"<!-- START (\w+) -->(.*?)<!-- END \1 -->", widgets, re.S))
    entries = load_catalog(ROOT / "skills")
    refresh_catalog_table(entries)
    api = create_app().openapi()  # Builds schemas only; never enters the lifespan.
    sources, inventory = source_library()
    fragments.update(
        tools=tool_reference(),
        skills=skill_reference(entries),
        api=api_reference(api),
        cli=cli_reference(build_parser()),
        sources=sources,
        modules=repository_map(),
    )
    scopes = "\n".join(
        f"| `{name}` | {description} |" for name, description in SCOPE_DESCRIPTIONS.items()
    )
    prose = prose.replace("<!-- SCOPES -->", "| Scope | Capability |\n| --- | --- |\n" + scopes)
    renderer = MarkdownIt("commonmark", {"html": True}).enable("table")
    embedded_paths = {(ROOT / entry["path"]).resolve() for entry in inventory}

    def render_chapter(content: str) -> str:
        # Narrative links resolve from usage-guide.html, not the template folder.
        # Prefer the embedded copy so a downloaded guide stays self-contained.
        tokens = renderer.parse(content)
        for token in tokens:
            for child in token.children or []:
                if child.type != "link_open":
                    continue
                href = child.attrGet("href") or ""
                if not href or re.match(r"[a-z]+:|#|//", href):
                    continue
                document, separator, fragment = href.partition("#")
                resolved = (ROOT / "docs" / document).resolve()
                if resolved in embedded_paths:
                    child.attrSet(
                        "href",
                        "#source-"
                        + slug(str(resolved.relative_to(ROOT)))
                        + ("-" + fragment if separator else ""),
                    )
        return renderer.renderer.render(tokens, renderer.options, {})

    chapters = re.split(r"(?m)^## (\d{2}\. .+)\n", prose)
    body = render_chapter(chapters[0])
    navigation = []
    titles = [
        {"id": slug(chapters[index]), "title": chapters[index].split(". ", 1)[1]}
        for index in range(1, len(chapters), 2)
    ]
    groups = {
        1: "Getting started",
        5: "Working with context",
        11: "Skills & operations",
        14: "Reference & walkthrough",
    }
    for index in range(1, len(chapters), 2):
        title, content = chapters[index : index + 2]
        identity = slug(title)
        number, name = title.split(". ", 1)
        if int(number) in groups:
            if navigation:
                navigation.append("</details>")
            navigation.append(
                f'<details class="nav-group" open><summary>{esc(groups[int(number)])}</summary>'
            )
        navigation.append(
            f'<a href="#{identity}"><span>{esc(number)}</span>{esc(name)}<span class="nav-read" aria-label="Marked read" hidden>✓</span></a>'
        )
        chapter = render_chapter(content)
        narrative = re.sub(r"```.*?```|<!--.*?-->", "", content, flags=re.S)
        reading_minutes = max(1, round(len(re.findall(r"\w+", narrative)) / 200))
        heading_number = 0

        def heading_link(match: re.Match, chapter_id: str = identity) -> str:
            nonlocal heading_number
            heading_number += 1
            heading = match.group(1)
            label = html.unescape(re.sub(r"<[^>]+>", "", heading))
            anchor = f"{chapter_id}-section-{heading_number}"
            return f'<h3 id="{anchor}">{heading}<a class="heading-link" href="#{anchor}" aria-label="Link to {esc(label)}">#</a></h3>'

        chapter = re.sub(r"<h3>(.*?)</h3>", heading_link, chapter)
        for widget_name, fragment in fragments.items():
            chapter = chapter.replace(f"<!-- WIDGET {widget_name} -->", fragment)
        position = int(number) - 1
        previous = titles[position - 1] if position else {"id": "top", "title": "Guide overview"}
        following = (
            titles[position + 1]
            if position + 1 < len(titles)
            else {"id": "top", "title": "Return to the overview"}
        )
        pagination = f'<nav class="chapter-pagination" aria-label="Continue reading"><a href="#{previous["id"]}"><small>← PREVIOUS</small><strong>{esc(previous["title"])}</strong></a><a href="#{following["id"]}"><small>{"NEXT CHAPTER →" if position + 1 < len(titles) else "BACK TO TOP ↑"}</small><strong>{esc(following["title"])}</strong></a></nav>'
        body += f'<section id="{identity}" class="chapter"><div class="chapter-meta"><div class="chapter-label">CHAPTER {esc(number)} / {len(titles):02}</div><span class="chapter-duration" title="Estimated reading time for prose; code and reference libraries excluded.">{{{{ICON:clock}}}} About {reading_minutes} min read</span></div><h2>{esc(name)}</h2>{chapter}{pagination}</section>'
    navigation.append("</details>")
    data = json.dumps(
        {
            "chapters": titles,
            "toolCount": len(TOOLS),
            "sourceCount": len(inventory),
            "skills": [{**entry.summary(), "body": entry.skill.body} for entry in entries],
            "skillSearch": {"weights": FIELD_WEIGHTS, "stopWords": sorted(STOP_WORDS)},
        },
        ensure_ascii=False,
    ).replace("</", "<\\/")
    # Share the introduction site's vector mark, embedded so the downloaded
    # guide remains a single file with no image or favicon requests.
    brand_svg = (ROOT / "website/assets/favicon.svg").read_text(encoding="utf-8").strip()
    brand_logo = brand_svg.replace(
        "<svg ",
        '<svg class="brand-mark" width="38" height="38" aria-hidden="true" focusable="false" ',
        1,
    )
    output = (HERE / "shell.html").read_text(encoding="utf-8")
    for key, value in {
        "BRAND_LOGO": brand_logo,
        "FAVICON": "data:image/svg+xml," + quote(brand_svg, safe=""),
        "CSS": (HERE / "styles.css").read_text(),
        "JS": (HERE / "app.js").read_text(),
        "ICONS": (HERE / "icons.svg").read_text(),
        "BODY": body,
        "NAV": "".join(navigation),
        "DATA": data,
        "TOOLS": str(len(TOOLS)),
        "SKILLS": str(len(entries)),
        "SOURCES": str(len(inventory)),
        "CHAPTERS": str(len(titles)),
    }.items():
        output = output.replace("{{" + key + "}}", value)
    output = re.sub(
        r"\{\{ICON:([a-z-]+)\}\}",
        lambda match: f'<svg class="ui-icon" aria-hidden="true" focusable="false"><use href="#icon-{match.group(1)}"></use></svg>',
        output,
    )
    target = ROOT / "docs/usage-guide.html"
    target.write_text(output, encoding="utf-8")
    (HERE / "source-manifest.json").write_text(json.dumps(inventory, indent=2) + "\n")
    print(
        f"Built {target.relative_to(ROOT)} ({len(output):,} characters; {len(entries)} skills; {len(TOOLS)} tools; {len(inventory)} embedded sources)."
    )


if __name__ == "__main__":
    main()
