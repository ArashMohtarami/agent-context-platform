# Tenant foundation and legacy adoption (V6 Phase 1)

This guide describes the guarded schema-36 adoption stage of the implemented V6
runtime. It seals all organizations until the subsequent explicit
[isolation cutover](tenant-isolation.md). The current full schema head is 42;
follow [staged migration/recovery](migration-recovery.md) rather than enabling
all later flags at once. The console, identity and policy contracts are described
in [dashboard](dashboard.md) and [organization management](organization-management.md).
There is no anonymous owner claim, public org registration or tenant restore.

Adoption was allocated at the actual pre-V6 migration head 35, so its version is
36. Enable foundation only for this stage. Older configurations refuse an
installation containing 36, but a pre-V6 binary cannot know that guard: stop all
old writers and fence their database credentials. Worker flags are attestations,
not process discovery. Quarantine remains operator-only at application interfaces,
not physically isolated from trusted installation database operators.

## Storage and identity contracts

`app/db/tenant_inventory.py` maps every one of the 37 served collections to its
model and structural references. All are tenant-owned, including keys, logs,
client templates, conversations, retry reservations and ingestion jobs.
`organizations`, `_schema_migrations`, `_mongot_health_probe`, `_tenant_control`,
`_tenant_preservation` and `_tenant_quarantine` are installation metadata. Unknown
collections or shapes receive no guessed owner. A nonempty pre-V6 collection
using a reserved V6 metadata name is refused before schema changes. Preserve
its backup and have an operator classify/rename that collection before retrying;
its name or email fields never establish ownership. This phase creates no private
global human identity collection.

Adoption selects exactly one organization explicitly. It adds `org_id`,
`identity_version=1` and `legacy_adoption_id`; it retains existing ObjectIds,
authored fields, actor labels, timestamps and revisions. Each adopted record has
an operator-only preservation manifest containing the original BSON and checksum.
The record and manifest commit in one
[replica-set transaction](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/crud/transactions/), with a fenced,
renewable maintenance lease and a compare-and-set check of the complete source.
New tenant records use version 2. Models freeze the three ownership/identity
fields; repository updates exclude them and constrain the stored owner/scheme.
New request DTOs cannot select the legacy scheme; normal repository inserts
reject version 1 even if a caller constructs a storage model.

Version 1 preserves the original SHA-256 of NUL-separated namespace/key parts.
Version 2 takes the first 12 SHA-256 bytes of UTF-8 JSON
`["acp-id",2,namespace,org_id,...parts]`, with `ensure_ascii=False` and separators
`(',', ':')`. Memory dimensions are scope, project-or-empty, client-or-empty and
external ID. Content integrity hashes remain hashes of content. Derived logical
snapshot/document/job/review IDs are protected by the tenant compound natural
constraints and version 2 document identity. Random document IDs remain random.
Natural-key lookups use organization plus natural key, allowing adopted IDs to
remain unchanged on update. No request selects a legacy hash fallback.

[Unique compound indexes](https://www.mongodb.com/docs/manual/core/index-unique/)
prefix `org_id`, including unique natural keys and partial active
skill constraints. New compound indexes are built before historical indexes are
dropped. TTL cleanup indexes remain single-field, and API key IDs remain globally
unique for credential resolution. Strict collection validators require non-null
ownership and pin version 1 to the chosen adoption org and adoption ID.
[MongoDB shape validation](https://www.mongodb.com/docs/manual/core/schema-validation/) cannot compare an old and new document to enforce
immutability; frozen models and repository write filters supply that guarantee
for application writes. A trusted DB operator can bypass validation.

Same-org closure includes project/client/profile/skill references, structured
content/research evidence, source/snapshot/document/chunk chains, candidate and
promotion references, supersession links, key restrictions and jobs. Actor labels,
retired key/owner labels, diagnostic retrieval selections and refused audit or
activity targets remain historical descriptions, not required live foreign keys.
External conversation session labels are not agent-session IDs. Arbitrary payload
metadata is inert. Ambiguous, dangling and foreign-owner structural links fail
closure. Legacy empty key projects/profiles and client project lists are recorded
as explicit `all_in_org` modes in the preservation manifest; grant conversion
and enforcement follow the isolation and managed-policy cutovers.

## Operator rehearsal

Use a **disposable copy** of a verified full-database backup first. Preserve the
archive and its checksum/count manifest. The existing backup facility remains
installation-wide; `--org-id` is refused on backup/restore commands. There is no
tenant restore. Credentials and archive contents must remain operator-only.

1. Stop all old API/MCP processes, imports and workers. Confirm each replacement
   maintenance binary understands schema 36. Do not run mixed writers.
2. Take and verify a whole-database backup with its collection counts. Record
   the selected organization and a stable adoption ID outside served data.
3. Configure database operator credentials and run the following commands:

```sh
contextctl --org-id existing-org organizations provision --name 'Existing organization' --slug existing-org
contextctl --json --org-id existing-org organizations adopt --adoption-id adoption-20261014 --dry-run
contextctl --json --org-id existing-org organizations adopt --adoption-id adoption-20261014 \
  --backup-archive /operator/pre-v6.archive --backup-manifest /operator/pre-v6.manifest.json \
  --writers-stopped --worker-schema-version 36
```

A dry run changes neither documents nor the migration ledger. Incompatible dry
runs and blocked applies return exit code 1 with the structured report on stdout. The apply command
validates the backup database, archive bytes/checksum and source coverage/counts.
Resume uses the same org, adoption ID and original verified backup. After an
interruption, rerun exactly the apply command. An unexpired competing lease
refuses a second maintainer. Transaction rollback removes a partially written
preservation manifest too. A final audit checks actual MongoDB candidate schemas (without model-default
invention), reference closure, preserved-record presence and preservation
counts against the original backup before installing strict tenant contracts.
Repeating a prepared adoption rechecks closure without rewriting records.

Unknown or incompatible records move transactionally to `_tenant_quarantine`,
with their full original BSON, checksum, reason and unresolved status. Quarantine
is outside domain repositories and routes; reports contain identifiers and issue
codes, not authored content or credentials. Unresolved quarantine **blocks**
preparation even when served collections appear clean. Missing source records,
including records lost during maintenance, also fail preservation counts. Do not
mark a quarantine item resolved merely because it was moved. Repair requires
operator review against the backup, an explicit classification, complete reference
repair and a new rehearsal. This release offers no automatic repair or destructive
quarantine-clear command.

Use explicit `--org-id` on provisioning, imports, keys, agent administration and
index maintenance after foundation provisioning. Tenant writes require completed
adoption and a registered sealed org. These are trusted operator operations, not
browser permissions. A whole-installation rollback requires an isolated verified
restore, old-writer shutdown and review before exposing traffic; migration 36 has
no automatic downgrade.

## Evidence commands

The new focused unit targets retain the requested names:

```sh
python -m pytest -q tests/unit/test_v6_tenant_models.py tests/unit/test_v6_tenant_migration.py
python -m pytest -q tests/unit/test_indexes.py tests/unit/test_models.py tests/unit/test_agent_identity_migration.py tests/unit/test_client_profile_migration.py tests/unit/test_backup_catalog.py
```

The real selector requires an explicitly named disposable loopback replica set
and its Docker container (prefix `acp-v6-phase1-test-`). It creates only uniquely
named `acp_v6_*` databases and drops them in fixture cleanup. It uses a real
`mongodump` archive. Without the environment, it skips and supplies **no**
integration evidence:

```sh
V6_TEST_MONGODB_URI='mongodb://127.0.0.1:PORT/?replicaSet=SET&directConnection=true' \
V6_TEST_MONGODB_CONTAINER=acp-v6-phase1-test-DATE \
python -m pytest -q tests/integration/test_v6_tenant_adoption.py
```

The fixture covers every collection with real pre-V6 validators, preserved actors
and revisions, linked content/research evidence, keys and jobs. Acceptance checks
strict validators and tenant uniqueness on MongoDB, adopted-object updates,
transaction rollback, interrupted/repeated adoption, unresolved quarantine,
backup/writer refusal and the API/MCP traffic seal. This stage-specific selector alone does not establish runtime isolation,
search-policy, dashboard or human-auth acceptance; those require their real gates.
