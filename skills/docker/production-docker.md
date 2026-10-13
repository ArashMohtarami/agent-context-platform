---
id: docker-production
name: Production Docker and Compose
version: 1.0.0
description: Build reproducible containers and operate Compose services with explicit secrets, network boundaries,
  readiness, persistent state and recovery. Use for Dockerfile, build and deployment changes.
category: operations
trust_level: trusted
tags:
- docker
- compose
- containers
- secrets
- deployment
---

# Production Docker and Compose

## Understand build and runtime inputs

Read the Dockerfile, build context, ignore rules, lock files, Compose services, healthchecks, mounts
and secret paths. Distinguish image contents from runtime-injected state. Pin dependencies and
select a maintained base image consistent with the project's platform/Python requirements.
Use multi-stage builds when they remove build tools/artifacts from runtime. Inspect actual cache
invalidation and offline wheel/model needs rather than assuming a cached build is reproducible.
[Build practices](https://docs.docker.com/build/building/best-practices/).

## Preserve boundaries and data

Keep secrets outside image layers and source control; build arguments are not a secret store.
Use runtime secrets or another explicit injection mechanism. Run with a non-root account when
compatible, matching mounted-file ownership and required writable paths. Publish only intended
service ports, often loopback for a local gateway. A private Compose network does not replace
authentication.

Identify durable volumes, derived caches, backup locations and disposable scratch state. Normal
shutdown must preserve durable volumes. Never use `down --volumes` as an unexplained repair step.
An image rebuild must not silently reset application data or database identity.

## Startup, capacity and shutdown

Use readiness checks for the dependencies an operation needs; liveness should avoid causing
restart loops during a downstream outage. Startup ordering is not permanent health supervision.
Budget workers, pools and model memory together. Forward termination signals and allow owned
clients/tasks to close within a known grace period.

Verify rendered Compose configuration, a build, startup/readiness, intended port exposure and a
representative request when runtime behavior changes. For recovery work, inspect mounted state and
backups first. Report version, resource and network assumptions alongside the observed result.
