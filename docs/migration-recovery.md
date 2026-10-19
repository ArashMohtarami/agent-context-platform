# Maintenance migration, cutover and recovery

This is an installation-operator procedure. Organization roles and tenant CSV
reports do not authorize schema changes, raw archives or database restore.
Rehearse on a disposable copy first. [Release verification](verification.md)
remains incomplete; the procedure is not an approval to open production traffic.

## Prepare and preserve

Record the reviewed checkout and image digests, actual schema ledger, every
API/MCP/import/job binary version, organization/adoption choices and independent
approval reference in private operator storage. Current migration head is 42;
pre-V6 head is 35. Do not guess a new migration number or run later flags against
an unclassified database.

Block ingress and stop **all** writers, connected MCP runtimes, imports and jobs.
Fence/rotate old database credentials so a forgotten pre-V6 binary cannot resume.
The `--writers-stopped` and worker-version options are attestations, not process
discovery. An old binary may not know the new startup guard. A single-node
replica set supplies transactions, not automatic host failover.

Take a quiescent whole-database backup and verify its archive hash and per-collection
manifest. Keep the original archive unchanged through adoption/resume. Back up
mounted key material separately: all retained MFA keyring versions, TLS private
keys, approved OIDC registrations/secrets and provider configuration. If using
Keycloak, back up PostgreSQL and provider keys separately; a realm export or
MongoDB archive does not recover the provider.

## Apply staged contracts

Use the current binary with later-stage flags disabled until that stage is
explicitly reached. Never launch the all-flags console overlay as the migration
runner for an unadopted installation. Run operator commands inside a private
configured maintenance container; the host CLI does not inherit Compose secrets.
The per-stage guides describe required mounts and reviewed tuple/policy files.

| Stage | Operator action and evidence | Traffic boundary |
| --- | --- | --- |
| 36 | Explicitly provision the adoption org, dry-run and apply/resume adoption using the original verified backup | All organizations sealed |
| 37 | Prepare isolation, run the current unit and real MongoDB/mongot gate, then explicitly activate the selected org with genuine matching evidence | Tenant domain access only after verification; additional orgs sealed |
| 38 | Enable local identity after verified isolation; mount keyring and private mail sink; provision invited initial owner with approved root/project bootstrap intent | No anonymous ownership or effective corpus assignment |
| 39 | Enable SSO engine, mounted approved registrations (or explicit empty list for local-only), exact HTTPS origin; reauthenticate as required | Old schema-38 sessions revoked; no provider/email trust shortcut |
| 40 | Explicit policy bootstrap activates the recorded root ceiling and directory assignments, with independent approval and transactional audit | Role names do not imply corpus access |
| 41 | Explicit human-management activation and reviewed root/team policy | No unknown sponsorship grandfathering |
| 42 | Prepare managed agents, inventory every old key, explicitly claim verified ownership/custody or revoke, then activate | No unmanaged admin bypass; recipient-only managed issuance |

Start the schema-36 rehearsal with fictional names changed to your reviewed org
and private archive paths:

```sh
contextctl --org-id YOUR_ORG organizations provision --name 'Your organization' --slug your-org
contextctl --json --org-id YOUR_ORG organizations adopt --adoption-id YOUR_ADOPTION --dry-run
contextctl --json --org-id YOUR_ORG organizations adopt --adoption-id YOUR_ADOPTION \
  --backup-archive /operator/pre-v6.archive --backup-manifest /operator/pre-v6.manifest.json \
  --writers-stopped --worker-schema-version 36
```

Resume uses the same org, adoption ID and original backup. Preserve ObjectIds,
actors, revisions and authored BSON. Unknown ownership is never inferred from
email/name/descriptive owner fields. Quarantined unresolved data remains preserved
outside served domain collections and **blocks activation**; moving it is not
proof of compatible adoption. Compare every original record and same-org reference
closure, not just a total count.

Continue with [adoption](tenant-adoption.md), [isolation](tenant-isolation.md),
[local identity](local-identity.md), [SSO](sso-identity.md),
[policy bootstrap](management-policy.md),
[human management](human-organization-management.md) and
[managed-machine cutover](managed-agents.md), in that order. Worker version 40/41/42
in policy commands must match the enabled configuration. Do not reuse an earlier
stage's example number on a fully enabled schema-42 installation.

Before opening the console, verify strict validators and compound uniqueness,
matching logical IDs/private sentinels in two orgs, adopted-object updates,
search index readiness/prefilters, both MCP transports, current sponsorship and
job/download revocation. Record current genuine evidence; do not fabricate JUnit
or reuse reports from another source revision. Only then configure the same-origin
[HTTPS overlay](dashboard-deployment.md). No live service is restarted by the
disposable verification commands below.

## Rehearse recovery

1. Keep original ingress and writers stopped. Restore into a uniquely named
   isolated database/stack using [backup](backup.md) operator commands. Never
   restore through a running tenant API or accept an org selector as authority
   for `--drop`.
2. Recover the correct retained keyring first. Check archive/manifests, each
   collection count, representative exact BSON, IDs, actor/revision history and
   same-org references. A combined count alone can hide missing collections.
3. Verify encrypted factor decryption with recovered keys and fail-closed refusal
   for missing/mismatched keys. Password reset does not repair lost encryption
   keys or remove MFA. Treat raw archives as private identity and corpus data.
4. Rebuild/reconcile derived lexical/vector indexes and verify actual search.
   Search volumes/model caches are not primary backups. Restore the external IdP
   from its separate supported database/key recovery procedure.
5. A rollback can revive old credentials or grants. Revoke/rotate sessions, keys
   and outstanding challenges, review authority generations/ownership and repeat
   the applicable current cutover checks before reopening ingress. Never mix
   old/unscoped workers with the restored database.
6. Record the isolated rehearsal's commands, fingerprints, hashes, collection
   comparisons, key checks and remaining limits. Keep archives, secrets, raw logs,
   screenshots and operator identifiers out of source/site exports.

## Evidence scope

The committed real adoption fixture starts with actual pre-V6 validators/data,
uses a real dump and checks preserved identity/reference closure plus interrupted
and repeated migration. The backup fixture materializes an all-collection archive,
compares restored BSON and exercises recovered MFA key decryption. Hosting uses
real distribution images and TLS, with production-mode disposable Keycloak.
The earlier distribution rehearsal recorded three real management-backup checks,
four real hosting checks and three HTTPS browser hosting journeys with no skips.
Its runtime/test fingerprints predate the present hardening changes; it is not
current final-source release approval. The current acceptance runner must repeat
and reconcile the applicable gates after all blockers are fixed. These are
executable rehearsals, not a claim that a particular installation has been
migrated or recovered.

```sh
bash scripts/test-v6-isolation.sh
bash scripts/test-v6-deployment.sh
bash scripts/test-v6-acceptance.sh
```

The complete acceptance runner reconciles the historical suites and current
fixtures. Required infrastructure skips or blocking performance/authorization
failures leave the gate open; see [known limits](verification.md). A production
operator must also record its own private cutover/recovery rehearsal. No public
release, deployment, invitation delivery or push is performed by this guide.
