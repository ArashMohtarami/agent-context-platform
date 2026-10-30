"""Deterministic input commitments. No calibration, model call or automatic review."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from . import dataset


def public_files(root: Path) -> list[str]:
    value = json.loads(dataset.read(root, "inventory.json"))
    names = value["files"]
    if (
        value["version"] != 1
        or names != sorted(set(names))
        or "inventory.json" not in names
        or "seal.json" in names
    ):
        raise ValueError("explicit source inventory required")
    actual = {
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file()
        and "__pycache__" not in p.parts
        and p.relative_to(root).as_posix() != "seal.json"
    }
    if actual != set(names) or any(p.is_symlink() for p in root.rglob("*")):
        raise ValueError("public source inventory contains missing/extra/symlinked files")
    return names


def private_files(root: Path, manifest: dict[str, Any]) -> list[str]:
    names = []
    for task in manifest["tasks"]:
        prefix = task["task_id"] + "/"
        names.append(prefix + "checks.json")
        labels = json.loads(dataset.read(root, prefix + "checks.json"))
        if (
            set(labels) != {"task_id", "family_id", "cases", "rubric"}
            or labels["task_id"] != task["task_id"]
            or labels["family_id"] != task["family_id"]
        ):
            raise ValueError("private task identity changed")
        cases = labels["cases"]
        if len(cases) != task["grading_contract"]["required_cases"] or len(
            {c["case_id"] for c in cases}
        ) != len(cases):
            raise ValueError("private checker selection incomplete")
        for case in cases:
            if (
                set(case) != {"case_id", "input", "expected"}
                or set(case["input"]) != {"state", "commands"}
                or set(case["expected"]) != {"state", "results"}
            ):
                raise ValueError("private checker schema changed")
            if not case["input"]["commands"] or len(case["expected"]["results"]) != len(
                case["input"]["commands"]
            ):
                raise ValueError("private checker has missing behavior outcomes")
        for variant in ("reference", "alternative", "mutant", "wrong"):
            names.extend(prefix + variant + "/" + name for name in ("api.py", "domain.py"))
            if task["outcome"] == "safe_action":
                names.append(prefix + variant + "/response.json")
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if not p.is_dir()}
    if actual != set(names) or any(p.is_symlink() for p in root.rglob("*")):
        raise ValueError("private bundle contains missing/extra/symlinked files")
    return sorted(names)


def build(private_root: Path, root: Path = dataset.ROOT) -> dict[str, Any]:
    manifest = dataset.load(root)
    public = {name: dataset.sha(dataset.read(root, name)) for name in public_files(root)}
    private = {
        name: dataset.sha(dataset.read(private_root, name))
        for name in private_files(private_root, manifest)
    }
    return {
        "version": 1,
        "kind": "initial-input-seal",
        "public": public,
        "private": private,
        "public_sha256": dataset.sha(dataset.canonical(public)),
        "private_sha256": dataset.sha(dataset.canonical(private)),
        "tasks": 32,
        "development": 8,
        "heldout": 24,
        "review_status": "author-checked; independent-task-design-license-review-pending",
        "execution_authorized": False,
    }


def commitment(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value[key]
        for key in (
            "version",
            "kind",
            "public",
            "public_sha256",
            "private_sha256",
            "tasks",
            "development",
            "heldout",
            "review_status",
            "execution_authorized",
        )
    }


def verify(value: dict[str, Any], private_root: Path, root: Path = dataset.ROOT) -> None:
    if value != build(private_root, root):
        raise ValueError("seal changed: inputs, checks, references, split or design differ")


def write_once(path: Path, content: bytes) -> None:
    if path.exists():
        if path.is_symlink() or path.read_bytes() != content:
            raise ValueError("existing seal cannot be replaced")
        return
    with path.open("xb") as stream:
        stream.write(content)
    path.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("create", "verify"))
    parser.add_argument("--private-root", required=True, type=Path)
    parser.add_argument("--private-seal", required=True, type=Path)
    args = parser.parse_args()
    if args.operation == "create":
        value = build(args.private_root)
        write_once(args.private_seal, dataset.canonical(value))
    else:
        value = json.loads(args.private_seal.read_text())
        verify(value, args.private_root)
    print(
        json.dumps(
            {
                "status": "verified",
                "public_sha256": value["public_sha256"],
                "private_sha256": value["private_sha256"],
                "agent_attempts": 0,
                "execution_authorized": False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
