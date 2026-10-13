# Self-managed mongot operations

## Architecture and support status

The Compose stack deploys MongoDB Community Search as a separate `mongot` process. The service uses
`mongodb/mongodb-community-search:1.70.4` pinned to the multi-architecture manifest digest
`sha256:63ebc805c6b55d027e345b5224d8713009f21911aedafb50b6c28c1cd9583e4f`.
MongoDB describes Community Search as a public-preview feature, so its production suitability,
upgrade behavior, and support constraints must be evaluated before relying on it for production
workloads.

The traffic flow is deliberately one-directional from the application's perspective:

```text
context-api -- authenticated PyMongo Async --> mongod (rs0)
                                              |       ^
                                              | gRPC  | authenticated sync
                                              v       |
                                            mongot ---+
```

`context-api` has no `mongot` hostname, port, credential, or client. It submits database and
coordinator commands to `mongod`; `mongod` owns the gRPC connection to
`mongot.agent_context:27028`. The network-qualified name avoids relying on Docker's search-domain
expansion in the gRPC resolver. Both sides explicitly disable TLS for this same-host
private-network channel (`searchTLSMode=disabled` on `mongod` and `tls.mode=disabled` on `mongot`).
If either side enables TLS, configure certificates on both rather than allowing the modes to
drift. `mongot` reads the replica set with its dedicated SCRAM user. That user is stored in the
`admin` database and has exactly the built-in `searchCoordinator` role.

Both services share only the Compose `agent_context` network, which is marked `internal`.
MongoDB port 27017 and `mongot` ports 27028 (gRPC), 8080 (health), and 9946 (metrics) are not
published to the host. TLS is disabled only for this single-host private-network deployment. If
the processes are ever split across hosts or the network trust boundary changes, enable TLS for
both synchronization and gRPC before deployment.

MongoDB state and `mongot` state are independent and persist in
`agent_context_mongodb_data` and `agent_context_mongot_data`. Back up and restore the MongoDB
replica set as the source of truth. Treat removal of either volume as destructive. Do not edit
the generated `/tmp/mongot.yml` inside the container; change the committed template and recreate
the service instead.

## Credentials

Set `MONGOT_SYNC_USERNAME` in the ignored `.env` file and provide the password through the file
referenced by `MONGOT_SYNC_PASSWORD_SECRET`. The password file must contain one non-empty value
with no trailing newline. The local generator creates a random username and password:

```bash
./scripts/generate-dev-secrets.sh
```

The password is mounted read-only at `/run/secrets/mongot-sync-password`. At startup the non-root
entrypoint copies it to an ephemeral owner-only file with mode `0400`, as required by `mongot`.
It is not copied into the runtime YAML, passed as a Compose environment variable, or printed in
logs. The idempotent `mongodb-init` job creates or updates the synchronization user at every
stack bootstrap, which also supports explicit password rotation followed by service recreation.

## Start and inspect

Prepare dependencies and start the complete stack:

```bash
python3.12 -m venv .venv
./scripts/generate-dev-secrets.sh
./scripts/prepare-docker-wheels.sh
docker compose config --quiet
docker compose up -d --build --wait
```

Inspect service health and `mongot` logs:

```bash
docker compose ps
docker compose logs --tail=100 mongot
make logs-mongot
```

Run the infrastructure verification commands:

```bash
make verify-mongodb
make verify-mongot
```

`verify-mongot` checks the private `/health` endpoint, confirms the synchronization user's exact
role and credentials, and executes `$listSearchIndexes` through `mongod` with the application
user. The final command briefly writes an isolated probe collection before checking it, then
removes the collection; this avoids MongoDB short-circuiting the coordinator check for a missing
namespace. It creates no search or vector index.

Restart or recreate only Community Search while retaining its volume:

```bash
docker compose restart mongot
docker compose up -d --no-deps --force-recreate mongot
```

Stop the stack without deleting either data volume:

```bash
docker compose down
```

After a synchronization credential rotation, update the secret file without a trailing newline,
then rerun bootstrap and recreate `mongot`:

```bash
docker compose run --rm mongodb-init
docker compose up -d --no-deps --force-recreate mongot
make verify-mongot
```

Do not use `docker compose down --volumes` in normal operations.

## Application integration

The application reconciles its Search and Vector Search indexes through MongoDB.
See [retrieval.md](retrieval.md) for the core knowledge indexes,
[content-retrieval.md](content-retrieval.md) for creator-content indexes, and
[research.md](research.md) for research retrieval. Embeddings are generated by
the application's local model, then indexed by Community Search.

Official references: [MongoDB Community Search repository](https://github.com/mongodb/mongot),
[Community Search 1.70.4 release](https://github.com/mongodb/mongot/releases/tag/v1.70.4), and
[self-managed configuration reference](https://www.mongodb.com/docs/upcoming/self-managed/search/configuration/reference/).
