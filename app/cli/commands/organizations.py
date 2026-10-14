"""Operator-only provisioning and adoption. No activation/tenant restore route."""

import argparse
from pathlib import Path

from app.cli.commands import SubParsers
from app.cli.errors import CliError, UsageError
from app.cli.output import Result, fields
from app.core.config import Settings
from app.db.client import MongoDBClient
from app.db.initializer import initialize_database
from app.models.organization import OrganizationDocument
from app.repositories.organization_repository import MongoOrganizationRepository
from app.repositories.tenant_adoption_repository import (
    AdoptionConflict,
    MongoTenantAdoptionRepository,
)
from app.security.tenant import current_tenant
from app.services.tenant_adoption_service import TenantAdoptionService


async def operate(arguments: argparse.Namespace, settings: Settings) -> Result:
    context = current_tenant()
    if context is None:
        raise UsageError("organization operations require explicit --org-id")
    mongodb = MongoDBClient(settings)
    await mongodb.connect(require_search=False)
    try:
        # A dry run is read-only, including the schema ledger.
        if arguments.action == "provision" or not arguments.dry_run:
            await initialize_database(mongodb.database, tenant_foundation=True)
        if arguments.action == "provision":
            organization = OrganizationDocument(
                org_id=context.org_id, name=arguments.name, slug=arguments.slug
            )
            await MongoOrganizationRepository(mongodb.database).create(organization)
            data: dict[str, object] = {"org_id": organization.org_id, "status": organization.status}
        else:
            service = TenantAdoptionService(MongoTenantAdoptionRepository(mongodb.database))
            if arguments.dry_run:
                report = await service.dry_run(
                    org_id=context.org_id, adoption_id=arguments.adoption_id
                )
            else:
                if arguments.backup_archive is None or arguments.backup_manifest is None:
                    raise UsageError(
                        "apply requires verified whole-database backup archive and manifest"
                    )
                report = await service.apply(
                    org_id=context.org_id,
                    adoption_id=arguments.adoption_id,
                    archive=arguments.backup_archive,
                    manifest_path=arguments.backup_manifest,
                    writers_stopped=arguments.writers_stopped,
                    worker_schema_versions=arguments.worker_schema_version,
                )
            data = report.describe()
        return Result(data=data, lines=fields(data))
    except AdoptionConflict as error:
        raise CliError(str(error)) from error
    finally:
        await mongodb.close()


def add_arguments(subparsers: SubParsers) -> None:
    group = subparsers.add_parser(
        "organizations", help="operator sealed registry and legacy adoption"
    )
    actions = group.add_subparsers(dest="action", required=True)
    provision = actions.add_parser("provision", help="provision a sealed, ownerless organization")
    provision.add_argument("--name", required=True)
    provision.add_argument("--slug", required=True)
    provision.set_defaults(handler=operate, needs_platform=False)
    adopt = actions.add_parser("adopt", help="dry-run or resume explicit legacy adoption")
    adopt.add_argument("--adoption-id", required=True)
    adopt.add_argument("--dry-run", action="store_true")
    adopt.add_argument("--backup-archive", type=Path)
    adopt.add_argument("--backup-manifest", type=Path)
    adopt.add_argument("--writers-stopped", action="store_true")
    adopt.add_argument("--worker-schema-version", type=int, action="append", default=[])
    adopt.set_defaults(handler=operate, needs_platform=False)
