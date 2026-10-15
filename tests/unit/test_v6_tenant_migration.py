"""Adoption planning refuses guesses and old writers; no mock database claims."""

import pytest
from v6_fixtures import legacy_fixture

from app.cli.contextctl import build_parser
from app.db.migrations.runner import MIGRATIONS
from app.db.tenant_inventory import references
from app.db.tenant_schema import TENANT_SCHEMA_VERSION
from app.repositories.tenant_adoption_repository import AdoptionConflict
from app.services.tenant_adoption_service import adoption_candidate, check_workers


def test_version_is_allocated_from_actual_v5_head():
    versions = [migration.version for migration in MIGRATIONS]
    assert versions == list(range(1, 40))
    assert versions[versions.index(TENANT_SCHEMA_VERSION) - 1] + 1 == TENANT_SCHEMA_VERSION == 36


@pytest.mark.parametrize(
    "metadata",
    [
        {"org_id": None},
        {"org_id": "org-b", "identity_version": 1, "legacy_adoption_id": "one"},
        {"identity_version": 1},
        {"legacy_adoption_id": "one"},
        {"org_id": "org-a", "identity_version": 2},
        {"org_id": "org-a", "identity_version": 1, "legacy_adoption_id": "other"},
    ],
)
def test_adoption_refuses_null_conflicting_partial_ownership(metadata):
    with pytest.raises(AdoptionConflict):
        adoption_candidate({**legacy_fixture()["projects"][0], **metadata}, "org-a", "one")


def test_adoption_only_adds_metadata_never_infers_from_owner_labels():
    raw = legacy_fixture()["agent_clients"][0]
    candidate = adoption_candidate(raw, "explicit-org", "one")
    assert {key: candidate[key] for key in raw} == raw
    assert candidate["org_id"] == "explicit-org"
    assert candidate["identity_version"] == 1


@pytest.mark.parametrize(
    "stopped,versions", [(False, [36]), (True, []), (True, [35]), (True, [36, 35])]
)
def test_activation_refuses_missing_or_mixed_writer_acknowledgment(stopped, versions):
    with pytest.raises(AdoptionConflict):
        check_workers(writers_stopped=stopped, worker_schema_versions=versions)


def test_reference_catalog_covers_fixture_links_and_historical_exceptions():
    fixture = legacy_fixture()
    for collection, records in fixture.items():
        for raw in records:
            for reference in references(collection, raw):
                assert reference.target in fixture, (collection, reference)
                assert reference.query
    assert list(references("audit_logs", fixture["audit_logs"][0])) == []
    assert any(
        ref.target == "agent_clients"
        for ref in references("gateway_idempotency", fixture["gateway_idempotency"][0])
    )
    assert any(
        ref.target == "source_snapshots"
        for ref in references(
            "ingestion_jobs", {**fixture["ingestion_jobs"][0], "snapshot_id": "snapshot"}
        )
    )


def test_nested_evidence_inherits_its_research_project():
    refs = list(
        references(
            "content_briefs",
            {
                "profile_id": "same",
                "evidence": {
                    "research_project_id": "research-one",
                    "source_claim_ids": ["same-claim"],
                },
            },
        )
    )
    claim = next(ref for ref in refs if ref.target == "research_claims")
    assert claim.query["research_project_id"] == "research-one"


def test_cli_context_is_explicit_and_no_restore_or_activate_command():
    parser = build_parser()
    args = parser.parse_args(
        ["--org-id", "org-a", "organizations", "adopt", "--adoption-id", "one", "--dry-run"]
    )
    assert args.org_id == "org-a" and args.dry_run
    for command in ("activate", "restore", "register", "claim"):
        with pytest.raises(SystemExit):
            parser.parse_args(["organizations", command])


def test_cli_incompatible_adoption_report_is_not_a_success(monkeypatch, capsys):
    from app.cli import contextctl
    from app.cli.output import Result

    async def blocked(arguments):
        return Result(
            data={"compatible": False, "stage": "blocked", "domain_traffic_enabled": False}
        )

    monkeypatch.setattr(contextctl, "_dispatch", blocked)
    assert (
        contextctl.main(
            [
                "--json",
                "--org-id",
                "org-a",
                "organizations",
                "adopt",
                "--adoption-id",
                "one",
                "--dry-run",
            ]
        )
        == 1
    )
    assert '"compatible": false' in capsys.readouterr().out
