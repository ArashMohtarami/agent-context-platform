"""Build a reviewable static website, without reading credentials or runtime data.

Run from the checkout: .venv/bin/python website/build.py
The output directory is always dist/site. Only this directory should be served.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import subprocess
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
OUTPUT = ROOT / "dist/site"
PUBLIC_DOCS = frozenset(
    json.loads((ROOT / "docs/guide/public-sources.json").read_text(encoding="utf-8"))
)
ROOT_DOWNLOADS = frozenset(
    {
        "LICENSE",
        "README.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "CHANGELOG.md",
        "NOTICE",
        "Dockerfile",
        "docker-compose.yml",
        "Makefile",
        "pyproject.toml",
        "requirements.lock",
        "requirements-dev.lock",
        "requirements-test.lock",
        "deploy/website/compose.yml",
        "deploy/website/nginx.conf",
        "deploy/licenses/mongodb-database-tools/README.md",
        "deploy/licenses/mongodb-database-tools/LICENSE.md",
        "deploy/licenses/mongodb-database-tools/APACHE-2.0.txt",
        "deploy/licenses/mongodb-database-tools/THIRD-PARTY-NOTICES",
    }
)
SOURCE_SUFFIXES = frozenset({".py", ".sh", ".md", ".toml", ".txt", ".json", ".yaml", ".yml"})
PRIVATE_TEXT = re.compile(r"agent-context-platform-(?:plan|v[1-5])\.md", re.I)
FORBIDDEN_TEXT: tuple[str, ...] = ()


class DocumentLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        fields = dict(attrs)
        if fields.get("id"):
            self.ids.add(str(fields["id"]))
        for attribute in ("href", "src"):
            if fields.get(attribute):
                self.links.append(str(fields[attribute]))


def safe_source(relative: str) -> Path:
    path = ROOT / relative
    if not path.resolve().is_relative_to(ROOT):
        raise ValueError(f"File leaves the checkout: {relative}")
    if any(part.is_symlink() for part in (path, *path.parents[: len(Path(relative).parts)])):
        raise ValueError(f"Symlinks are not included in the website: {relative}")
    if any(part.startswith(".") for part in Path(relative).parts):
        raise ValueError(f"Hidden files are not public site inputs: {relative}")
    allowed = (
        relative in ROOT_DOWNLOADS
        or relative in PUBLIC_DOCS
        or relative in {"website/README.md", "website/build.py"}
        or relative == "docs/usage-guide.html"
        or (
            relative.startswith(("skills/", "examples/", "app/", "tests/", "scripts/"))
            and path.suffix in SOURCE_SUFFIXES
        )
        or (
            relative.startswith("docs/guide/")
            and path.suffix in SOURCE_SUFFIXES | {".html", ".css", ".js", ".svg"}
        )
    )
    if not allowed:
        raise ValueError(f"Link is outside the reviewed public file inventory: {relative}")
    if not path.is_file():
        raise ValueError(f"Public linked file does not exist: {relative}")
    return path


def read_public(path: Path) -> str:
    content = path.read_text(encoding="utf-8")
    if (
        PRIVATE_TEXT.search(content)
        or str(Path.home()) + "/" in content
        or any(value.casefold() in content.casefold() for value in FORBIDDEN_TEXT)
    ):
        raise ValueError(
            f"Private organization/path/planning reference in {path.relative_to(ROOT)}"
        )
    return content


def local_target(document: str, href: str, output: Path) -> tuple[str, str] | None:
    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc:
        return None
    if not parsed.path:
        candidate = output / document
    elif parsed.path.startswith("/"):
        candidate = output / unquote(parsed.path).lstrip("/")
    else:
        candidate = (output / document).parent / unquote(parsed.path)
    target = candidate.resolve()
    if not target.is_relative_to(output.resolve()):
        raise ValueError(f"Link escapes static website: {document} -> {href}")
    relative = str(target.relative_to(output.resolve()))
    return "index.html" if relative == "." else relative, unquote(parsed.fragment)


def assemble(output: Path) -> tuple[int, int]:
    files: set[str] = set()
    documents: dict[str, DocumentLinks] = {}
    directories: set[str] = set()

    def write(relative: str, content: str) -> None:
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        files.add(relative)

    def add(relative: str) -> None:
        if relative in files:
            return
        source = safe_source(relative)
        write(relative, read_public(source))

    for asset in sorted((HERE / "assets").iterdir()):
        if asset.is_symlink() or not asset.is_file() or asset.suffix not in {".css", ".js", ".svg"}:
            raise ValueError(f"Unexpected website asset: {asset.name}")
        write("assets/" + asset.name, read_public(asset))
    # The source page lives under website/ and can be opened directly. In the
    # exported site it lives at the root, alongside docs/ and LICENSE.
    landing_page = read_public(HERE / "index.html")
    landing_page = landing_page.replace(
        'href="../docs/usage-guide.html', 'href="docs/usage-guide.html'
    ).replace('href="../LICENSE"', 'href="LICENSE"')
    write("index.html", landing_page)
    add("docs/usage-guide.html")
    add("LICENSE")
    add("deploy/website/compose.yml")
    add("deploy/website/nginx.conf")

    def directory_index(relative: str) -> str:
        if relative not in {
            "docs/guide",
            "docs/clients",
            "skills",
            "examples",
            "website",
            "deploy/licenses/mongodb-database-tools",
        }:
            raise ValueError(f"Unreviewed directory link: {relative}")
        if relative not in directories:
            directories.add(relative)
            source = ROOT / relative
            links = []
            for path in sorted(source.rglob("*")):
                name = str(path.relative_to(ROOT))
                if (
                    not path.is_file()
                    or path.is_symlink()
                    or (path.suffix not in SOURCE_SUFFIXES and name not in ROOT_DOWNLOADS)
                ):
                    continue
                if "__pycache__" in path.parts or any(
                    part.startswith(".") for part in path.relative_to(ROOT).parts
                ):
                    continue
                if name.startswith("docs/clients/") and name not in PUBLIC_DOCS:
                    continue
                add(name)
                link = str(path.relative_to(source))
                links.append(
                    f'<li><a href="{html.escape(link, quote=True)}">{html.escape(link)}</a></li>'
                )
            write(
                relative + "/index.html",
                '<!doctype html><html lang="en"><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1">'
                "<title>Public source files</title><body><h1>Public source files</h1>"
                '<p><a href="/">Back to the website</a></p><ul>'
                + "".join(links)
                + "</ul></body></html>",
            )
        return relative + "/index.html"

    while True:
        pending = sorted(name for name in files if name.endswith(".html") and name not in documents)
        if not pending:
            break
        for name in pending:
            parser = DocumentLinks()
            parser.feed((output / name).read_text(encoding="utf-8"))
            documents[name] = parser
            for href in parser.links:
                result = local_target(name, href, output)
                if result is None:
                    continue
                target, _ = result
                if not target:
                    target = "index.html"
                if (ROOT / target).is_dir():
                    target = directory_index(target.rstrip("/"))
                if target not in files:
                    add(target)

    broken = []
    for name, parser in documents.items():
        for href in parser.links:
            result = local_target(name, href, output)
            if result is None:
                continue
            target, fragment = result
            target = target or "index.html"
            if target in directories:
                target += "/index.html"
            if target not in files:
                broken.append(f"{name} -> {href}: missing file")
            elif fragment and target in documents and fragment not in documents[target].ids:
                broken.append(f"{name} -> {href}: missing anchor")
    if broken:
        raise ValueError("Broken static links:\n" + "\n".join(broken))
    manifest = [
        {"path": name, "sha256": hashlib.sha256((output / name).read_bytes()).hexdigest()}
        for name in sorted(files)
    ]
    (output / "site-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return len(files), len(documents)


def build(skip_guide: bool) -> None:
    if not skip_guide:
        subprocess.run(  # noqa: S603 - Fixed local builder, without shell execution.
            [sys.executable, str(ROOT / "docs/guide/build.py")], cwd=ROOT, check=True
        )
    OUTPUT.parent.mkdir(exist_ok=True)
    if OUTPUT.is_symlink():
        raise ValueError("dist/site must be a directory, not a symlink")
    with tempfile.TemporaryDirectory(prefix=".site-build-", dir=OUTPUT.parent) as temporary:
        staged = Path(temporary)
        file_count, page_count = assemble(staged)
        # Keep the output directory itself stable for an existing container bind
        # mount. A failed validation leaves the previous public files untouched.
        OUTPUT.mkdir(exist_ok=True)
        OUTPUT.chmod(0o755)
        public_files = {path.relative_to(staged) for path in staged.rglob("*") if path.is_file()}
        for relative in sorted(public_files):
            target = OUTPUT / relative
            if any(part.is_symlink() for part in (target, *target.parents)):
                raise ValueError(f"Symlink in the existing output directory: {relative}")
            target.parent.mkdir(parents=True, exist_ok=True)
            directory = target.parent
            while directory.is_relative_to(OUTPUT):
                directory.chmod(0o755)
                if directory == OUTPUT:
                    break
                directory = directory.parent
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as prepared:
                prepared.write((staged / relative).read_bytes())
            prepared_path = Path(prepared.name)
            prepared_path.chmod(0o644)
            prepared_path.replace(target)
        for path in sorted(OUTPUT.rglob("*"), reverse=True):
            if path.is_symlink() or (
                path.is_file() and path.relative_to(OUTPUT) not in public_files
            ):
                path.unlink()
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()
    print(
        f"Built dist/site: {file_count} public files; "
        f"{page_count} HTML pages; local links verified."
    )


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument(
        "--skip-guide", action="store_true", help="Reuse an already rebuilt public guide."
    )
    cli.add_argument(
        "--forbid-text",
        action="append",
        default=[],
        help="Reject a private name or phrase in published files; may be repeated.",
    )
    args = cli.parse_args()
    FORBIDDEN_TEXT = tuple(args.forbid_text)
    build(args.skip_guide)
