"""Validate or search canonical Git skills locally; never opens a network or database."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.skills.catalog import load_catalog, search_catalog  # noqa: E402
from app.skills.errors import SkillError  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    """Print machine-readable or human-readable source results, with bounded searches."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT / "skills")
    parser.add_argument("--json", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate", help="Parse and chunk all source files without importing")
    search = commands.add_parser("search", help="Rank local metadata and full Markdown bodies")
    search.add_argument("query", nargs="?", default="")
    search.add_argument("--category")
    search.add_argument("--tag", action="append", default=[])
    search.add_argument("--limit", type=int, default=50)
    search.add_argument(
        "--content", action="store_true", help="Include the complete matched bodies"
    )
    arguments = parser.parse_args(argv)
    try:
        entries = load_catalog(arguments.directory)
        if arguments.command == "validate":
            data = {
                "skills": len(entries),
                "chunks": sum(entry.chunk_count for entry in entries),
                "categories": sorted({entry.skill.metadata.category for entry in entries}),
            }
            print(json.dumps(data, indent=2) if arguments.json else f"Valid source catalog: {data}")
        else:
            matches = search_catalog(
                entries,
                arguments.query,
                category=arguments.category,
                tags=tuple(arguments.tag),
                limit=arguments.limit,
            )
            rows = [
                {
                    **match.entry.summary(),
                    "score": match.score,
                    "sections": match.sections,
                    **({"body": match.entry.skill.body} if arguments.content else {}),
                }
                for match in matches
            ]
            if arguments.json:
                print(json.dumps({"items": rows, "count": len(rows)}, indent=2))
            else:
                for row in rows:
                    print(
                        f"{row['id']}  v{row['version']}  [{row['category']}]  score={row['score']}"
                    )
                    print(f"  {row['description']}\n  {row['source_path']}")
                    if row["sections"]:
                        print("  Sections: " + "; ".join(row["sections"]))
                    if arguments.content:
                        print("\n" + row["body"] + "\n")
                print(f"{len(rows)} matching source skills (local lexical search)")
    except (SkillError, OSError, ValueError) as error:
        print(f"Catalog error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
