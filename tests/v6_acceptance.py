"""Fail-closed release evidence reconciliation; this never establishes runtime evidence itself."""

import ast
import json
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "tests/v6_acceptance_matrix.json"


def matrix() -> dict[str, list[str]]:
    return json.loads(MATRIX.read_text())


def validate_inventory(scenarios: dict[str, list[str]]) -> set[str]:
    if not scenarios or any(not targets for targets in scenarios.values()):
        raise ValueError("empty acceptance category")
    targets = {target for values in scenarios.values() for target in values}
    for target in targets:
        filename, function = target.split("::")
        path = ROOT / filename
        if path.parent != ROOT / "tests/integration" or not path.is_file():
            raise ValueError("acceptance target outside integration inventory")
        functions = {
            n.name
            for n in ast.parse(path.read_text()).body
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        if function not in functions or not function.startswith("test_"):
            raise ValueError("acceptance test disappeared: " + target)
    return targets


def reconcile(
    report: Path, required: set[str], *, fingerprints: dict[str, str] | None = None
) -> dict[str, int]:
    # Locally produced pytest XML only; never a supplied upload or network response.
    root = ET.parse(report).getroot()  # noqa: S314
    if fingerprints is not None:
        suites = [root] if root.tag == "testsuite" else root.findall(".//testsuite")
        if not suites:
            raise ValueError("release report has no source-bound suite")
        for suite in suites:
            properties = {
                p.attrib["name"]: p.attrib["value"] for p in suite.findall("./properties/property")
            }
            if any(properties.get(k) != v for k, v in fingerprints.items()):
                raise ValueError("release report was produced against different source")
    cases = root.findall(".//testcase")
    if (
        not cases
        or root.findall(".//error")
        or root.findall(".//failure")
        or root.findall(".//skipped")
    ):
        raise ValueError("release gate has empty, failed, errored or skipped cases")
    observed = {
        case.attrib["classname"].replace(".", "/") + ".py::" + case.attrib["name"].split("[")[0]
        for case in cases
    }
    missing = required - observed
    if missing:
        raise ValueError("release gate missing targets: " + ", ".join(sorted(missing)))
    return {"tests": len(cases), "failures": 0, "errors": 0, "skipped": 0}


def leaks(paths: list[Path], needles: list[bytes]) -> list[str]:
    if not needles or any(len(needle) < 16 for needle in needles):
        raise ValueError("secret scan requires full synthetic secrets")
    # Return paths only. Never copy the matching secret into an evidence report.
    return [str(path) for path in paths if any(n in path.read_bytes() for n in needles)]


def reconcile_browser(report: Path, required_files: set[str]) -> dict[str, int]:
    root = ET.parse(report).getroot()  # noqa: S314 -- locally produced Playwright XML
    cases = root.findall(".//testcase")
    if not cases or any(root.findall(".//" + tag) for tag in ("error", "failure", "skipped")):
        raise ValueError("browser gate is empty, failed or skipped")
    if required_files - {case.attrib["classname"] for case in cases}:
        raise ValueError("browser gate omitted required source files")
    return {"tests": len(cases), "failures": 0, "errors": 0, "skipped": 0}
