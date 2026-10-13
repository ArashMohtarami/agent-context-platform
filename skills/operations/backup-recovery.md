---
id: backup-recovery
name: Verified Backup and Recovery
version: 1.0.0
description: Create and verify backup/recovery procedures that preserve durable state, isolate restore tests and
  rebuild derived indexes. Use for backup changes and explicitly intended recovery.
category: operations
trust_level: trusted
tags:
- backup
- restore
- recovery
- retention
- disaster-recovery
---

# Verified Backup and Recovery

## Inventory what must survive

Identify database records, identity/grants, histories, durable blobs, canonical Git sources, secrets
and rebuildable caches/indexes. Define required recovery point and recovery time with the operator.
A copy on the same failing host is not a complete off-host strategy. Protect backup access because
it can expose historical private data and credential verification material.

Use manifests/checksums and record the backup's consistency mechanism. Coordinate related stores
when one workflow spans them. Retention must preserve the required recovery window and avoid
pruning the only verified usable copy. A filename or successful archive command is not proof of
recoverability.

## Restore in isolation first

Verify checksums, then restore into a separate database/namespace and inspect counts, relationships,
known records and representative reads. Rebuild derived indexes and validate model/vector
compatibility. Test application authorization and client identities as well as business records.
Do not treat readiness alone as proof that the desired state was restored.

For an intended real recovery, select a specific verified backup, stop or quiesce competing writes,
record the target, perform the procedure and reconcile state before resuming traffic. A restore
replaces durable state; do not execute it as a diagnostic experiment on a working deployment.

## Deliver an executable runbook

Document exact environment-specific commands, required credentials, restore target, validation,
resume sequence, rollback/reconciliation limits and estimated duration. Keep destructive flags
clearly tied to the intended recovery operation. In this platform, search indexes are derived and
Git skills remain canonical; whole-database backups include versions, research, creator records,
agent identities and grants.

Exercise the runbook periodically in isolation. Report the backup ID, restored state and smoke
workflow evidence, without publishing secrets or private records.
