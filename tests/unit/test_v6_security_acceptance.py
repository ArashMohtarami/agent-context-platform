"""Acceptance runner correctness is separate from the real enforcement gate."""

import os
import subprocess
from pathlib import Path

import pytest
from v6_acceptance import ROOT, leaks, matrix, reconcile, reconcile_browser, validate_inventory

from app.security.policy.models import Grant, covered
from app.security.tenant_policy import ResourceGrant, TenantPolicy


def test_attack_matrix_resolves_to_real_source_tests_and_required_boundaries():
    scenarios = matrix()
    assert len(scenarios) == 13
    assert len(validate_inventory(scenarios)) >= 100
    assert all("tests/integration/" in target for rows in scenarios.values() for target in rows)
    for required in (
        "tenant_isolation",
        "oidc_provider",
        "policy_fencing",
        "managed_client_revocation",
    ):
        assert any(f"test_v6_{required}.py::" in t for t in validate_inventory(scenarios))
    broken = {**scenarios, "unimplemented": ["tests/integration/missing.py::test_missing"]}
    with pytest.raises(ValueError):
        validate_inventory(broken)


@pytest.mark.parametrize("defect", ["skipped", "failure", "error", "missing", "empty"])
def test_evidence_checker_never_turns_an_incomplete_gate_green(tmp_path: Path, defect: str):
    report = tmp_path / "gate.xml"
    case = '<testcase classname="tests.integration.test_v6_security_acceptance" name="test_case">'
    case += f"<{defect}/>" if defect in {"skipped", "failure", "error"} else ""
    case += "</testcase>"
    report.write_text("<testsuite>" + ("" if defect == "empty" else case) + "</testsuite>")
    target = "test_absent" if defect == "missing" else "test_case"
    with pytest.raises(ValueError):
        reconcile(report, {"tests/integration/test_v6_security_acceptance.py::" + target})


def test_complete_parameterized_report_reconciles_and_scanner_never_prints_secret(tmp_path):
    report = tmp_path / "gate.xml"
    report.write_text(
        '<testsuite><testcase classname="tests.integration.test_v6_security_acceptance" '
        'name="test_case[sponsor]"/></testsuite>'
    )
    assert (
        reconcile(report, {"tests/integration/test_v6_security_acceptance.py::test_case"})["tests"]
        == 1
    )
    secret = b"synthetic-acceptance-credential-unique"
    artifact = tmp_path / "artifact.txt"
    artifact.write_bytes(b"unsafe=" + secret)
    assert leaks([artifact], [secret]) == [str(artifact)]
    assert secret.decode() not in str(leaks([artifact], [secret]))
    assert (ROOT / "dashboard/e2e/org-switch.spec.ts").is_file()


def test_action_resource_coupling_survives_union_and_default_none():
    ceiling = tuple(
        Grant(action=a, resource_kind="project", selector="include", ids=[r])
        for a, r in (("context:read", "project-a"), ("context:write", "project-b"))
    )
    policy = TenantPolicy(
        "org-a",
        "person",
        tuple(ResourceGrant(g.action, g.resource_kind, g.selector, tuple(g.ids)) for g in ceiling),
    )
    assert policy.selector("context:read", "project") == ("project-a",)
    assert policy.selector("context:write", "project") == ("project-b",)
    assert policy.selector("context:write", "skill") == ()
    assert not covered(
        Grant(
            action="context:write", resource_kind="project", selector="include", ids=["project-a"]
        ),
        ceiling,
    )
    assert Grant(action="context:write", resource_kind="project").selector == "none"


def test_evidence_cannot_combine_reports_from_another_source_revision(tmp_path):
    path = tmp_path / "source-bound.xml"
    path.write_text(
        "<testsuites><testsuite><properties>"
        '<property name="v6_source_sha256" value="old"/></properties>'
        '<testcase classname="tests.integration.example" name="test_real"/>'
        "</testsuite></testsuites>"
    )
    with pytest.raises(ValueError, match="different source"):
        reconcile(path, set(), fingerprints={"v6_source_sha256": "current"})
    assert reconcile(path, set(), fingerprints={"v6_source_sha256": "old"})["tests"] == 1


@pytest.mark.parametrize(
    "selector",
    ["V6_LEGACY_DEVELOPMENT_ONLY", "V6_SECURITY_DEVELOPMENT_ONLY", "V6_DEPLOYMENT_TESTS"],
)
def test_complete_gate_refuses_inherited_partial_selectors_before_docker(tmp_path, selector):
    bin_path = tmp_path / "bin"
    bin_path.mkdir()
    docker = bin_path / "docker"
    docker.write_text("#!/bin/sh\nexit 99\n")
    docker.chmod(0o700)
    result = subprocess.run(  # noqa: S603 -- fixed local runner and isolated fake Docker
        ["/bin/bash", str(ROOT / "scripts/test-v6-acceptance.sh")],
        cwd=ROOT,
        env={
            "PATH": str(bin_path) + os.pathsep + os.defpath,
            "V6_ACCEPTANCE_MODEL_CACHE": str(tmp_path),
            "PLAYWRIGHT_CHROMIUM_EXECUTABLE": "/bin/true",
            "V6_REPORT_DIR": str(tmp_path / "reports"),
            selector: "1",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "refuses development selectors" in result.stderr


@pytest.mark.parametrize("defect", ["skipped", "failure", "missing", "empty"])
def test_browser_skips_and_omitted_source_files_cannot_pass_the_gate(tmp_path, defect):
    report = tmp_path / "browser.xml"
    name = "security.spec.ts" if defect == "missing" else "auth.spec.ts"
    case = f'<testcase classname="{name}" name="real local login">'
    case += f"<{defect}/>" if defect in {"failure", "skipped"} else ""
    case += "</testcase>"
    report.write_text(
        "<testsuites><testsuite>"
        + ("" if defect == "empty" else case)
        + "</testsuite></testsuites>"
    )
    with pytest.raises(ValueError):
        reconcile_browser(report, {"auth.spec.ts"})
    report.write_text(
        "<testsuites><testsuite>"
        '<testcase classname="auth.spec.ts" name="real local login"/>'
        "</testsuite></testsuites>"
    )
    assert reconcile_browser(report, {"auth.spec.ts"})["tests"] == 1


def test_complete_gate_never_reuses_an_older_success_manifest(tmp_path):
    bin_path = tmp_path / "bin"
    bin_path.mkdir()
    docker = bin_path / "docker"
    docker.write_text("#!/bin/sh\nexit 99\n")
    docker.chmod(0o700)
    directory = tmp_path / "reports"
    directory.mkdir()
    previous = directory / "complete.json"
    previous.write_text('{"previous": true}')
    result = subprocess.run(  # noqa: S603 -- fixed runner and fake Docker only
        ["/bin/bash", str(ROOT / "scripts/test-v6-acceptance.sh")],
        cwd=ROOT,
        env={
            "PATH": str(bin_path) + os.pathsep + os.defpath,
            "V6_ACCEPTANCE_MODEL_CACHE": str(tmp_path),
            "PLAYWRIGHT_CHROMIUM_EXECUTABLE": "/bin/true",
            "V6_REPORT_DIR": str(directory),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "fresh evidence directory" in result.stderr
    assert previous.read_text() == '{"previous": true}'
