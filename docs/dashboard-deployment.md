# Console deployment and distribution

**Release readiness is not approved.** See [verification and known limits](verification.md)
for the unresolved authorization-expiry and overview workload gates. The following
describes implemented deployment behavior, not permission to deploy.


The implemented console lives in `dashboard/` in this repository. It shares one
HTTPS origin with session-only `/api/v6`, the existing bearer APIs and `/mcp`.
The optional public [marketing/documentation site](website.md) is a separate
static host: it has no admin cookie, API upstream, runtime data or secret mounts.
Use a different hostname for that site; do not proxy it into this origin.

## Build and TLS ingress

Build from a reviewed checkout with Docker Compose **2.24.4 or later** (`!reset`
and `!override` are used). Provision the pinned Python wheels (or preload the reviewed offline cache), then:

```sh
./scripts/prepare-docker-wheels.sh
docker compose -f docker-compose.yml -f deploy/console/compose.yml build context-api console
```

The console target uses digest-pinned Node 24.22.0, `npm ci` and the committed
lockfile, license inventory, TypeScript and build-budget checks. It copies static
assets and notices into digest-pinned unprivileged nginx. No Node server, browser
bundle secret, CDN, runtime npm install or `VITE_*` variable is used. The API
runtime receives Git/GSSAPI/CA files plus Debian notices from a matching pinned
Debian 13 Python image; its build does not resolve mutable apt packages or need
Debian mirrors. Python dependencies still use the exact offline wheel lock.
When updating either Python base, review them together and rebuild/run the gate.

Set these non-secret deployment values in your private environment:

```dotenv
CONSOLE_SERVER_NAME=console.example.test
CONSOLE_BIND_IP=127.0.0.1
CONSOLE_HTTPS_PORT=8443
V6_BROWSER_ORIGIN=https://console.example.test:8443
CONSOLE_CERT_FILE=./secrets/console-chain.crt
CONSOLE_KEY_FILE=./secrets/console.key
V6_MFA_KEYRING_SECRET=./secrets/v6-mfa-keyring.json
V6_OIDC_REGISTRATIONS_SECRET=./secrets/v6-oidc-registrations.json
V6_PROVIDER_CA_DIRECTORY=./secrets/provider-ca
V6_MAIL_HOST_DIRECTORY=./secrets/mail-handoff
```

Use lowercase ASCII hostnames and omit `:443` for the default HTTPS port.
The hostname and external port must match the **exact** browser origin, certificate
SAN and provider callback registration. DNS hostnames and IPv4 SAN certificates
are supported; e.g. `127.0.0.1` with a locally trusted development CA. This template
does not support IPv6 literal server names. Trust the issuing CA on clients;
production browsers must not use an ignored certificate error. For remote use,
set an explicit host bind address and DNS/certificate, with the firewall exposing
only this TLS port. No public HTTP listener or HTTP password/MCP administration
is installed. A remote client uses `https://host[:port]/mcp`; stdio retains its
private API service path and the same current credential/sponsor checks.

The overlay removes **all API host ports**, enables all prerequisite V6 flags,
and trusts only `CONSOLE_PROXY_IP` (default `172.29.15.2`) on a dedicated internal
`CONSOLE_SUBNET` (`172.29.15.0/28`). If this overlaps another network, change the
subnet, proxy and API IPs together. The gateway also joins a separate `console_public` network for its published TLS
port; the API does not join that network. No wildcard proxy trust is accepted. The
edge overwrites forwarded scheme/address headers, rejects other Host values,
passes API/MCP errors without interception and disables proxy caches/buffering.
The app rechecks HTTPS, cookie/bearer separation, CSRF/Origin and current policy.
An external load balancer is not implicitly trusted: this template terminates
TLS directly; changing that topology needs a new reviewed trust configuration.

Only `/console/` extensionless navigation uses the SPA fallback. Missing files,
`/console/assets/*`, APIs, MCP and other paths retain their errors. HTML and API
responses are `no-store`; successful hashed assets are immutable, while failed
asset requests are `no-store`. CSP uses local scripts/fonts/connect, no objects
or frames; inline styles support the existing accessible primitives. Framing,
referrers, sniffing and device permissions are restricted. HSTS is one year on
this host (no automatic preload or subdomain policy). Ingress access and request-error logs are disabled because request-line
diagnostics can contain OIDC codes; use content-free API diagnostics and readiness. The
container healthcheck checks the built index and proxied API readiness; its
loopback-only TLS probe does not establish external certificate trust.

## Identity secrets, local operation and invitations

Create private host directories (0700), mount secret files read-only, and make
files readable by the container UID without opening their parent directory.
The image's `app` account owns the mail handoff directory; inspect its UID/GID
with `docker compose run --rm --no-deps context-api id` before setting ownership.
The operator needs privileged access to this private directory. Secret managers
should supply unique MongoDB passwords/keyfile, TLS private key, MFA keyring,
and approved confidential OIDC client registrations. Provider CA files are
public trust anchors, mounted separately at `/run/provider-ca/`.

The versioned MFA file has the existing SeedVault format: an `active` key ID and
`keys` mapping of IDs to URL-safe-base64 **random 32-byte keys**. Generate these
through your secret manager, retain retired versions while ciphertext refers to
them, and escrow a separate encrypted recovery copy. Never put a real example
key in source or logs. Local passwords are Argon2id hashes in MongoDB, not mounted
plaintext passwords. Browser sessions/challenges/recovery codes are stored as
opaque hashes; there is no invented static browser-session signing secret.
Installation cursor secrets and authority generations are database state.

For local-only operation mount an explicit `[]` registrations JSON file and use
organizations whose policy is `local_only`. The SSO engine remains enabled for
the Phase 5 policy contract, but there is no selectable provider or discovery
request. Existing SSO-required organizations **do not** fall back when the file
is empty or the provider is unavailable. Before retiring a provider, an operator
must deliberately approve the policy transition while that registration is
still available; a file change alone never changes organization policy.

Enrollment is invitation-only. Follow [local identity](local-identity.md),
[human management](human-organization-management.md) and [management policy](management-policy.md)
to provision an initial owner with explicit root/bootstrap approval. Starting the
Compose overlay does not adopt legacy data, claim an owner or activate an org.
The shipped delivery port is a **private local mail sink**, not a production SMTP
sender. An operator hands the purpose-bound, expiring single-use code to the
verified recipient over an approved channel; never publish/mount this directory
through nginx or paste codes into issue trackers. A production delivery adapter
can implement the existing MailDelivery port separately; this release makes no
SMTP/deliverability claim. Clear expired handoff files under your retention policy.
Do not send test invitations to real addresses during verification.

## External OIDC and optional Keycloak

Use [SSO identity](sso-identity.md) for the exact approved registration schema,
pinned provider IP/CA, issuer, client ID/secret and assurance rules. Register only:
`<origin>/api/v6/auth/oidc/callback` and
`<origin>/api/v6/auth/oidc/logged-out` as exact redirect/logout URLs. The provider
must support Authorization Code + S256 PKCE. Equal emails never link identities;
configured `acr`/`amr` assurance and exact provider membership remain required.
Keep the confidential client secret in the mounted registry, never the frontend.
After a registration/IP/CA/secret change, reapprove with the host operator CLI:

```sh
docker compose -f docker-compose.yml -f deploy/console/compose.yml exec -T context-api \
  python -m app.cli.contextctl --org-id YOUR_ORG identity configure-sso \
  --registration-id APPROVED_PROVIDER --policy sso_preferred \
  --approval-reference YOUR_REVIEW_RECORD
```

`local_only`, `sso_preferred` and `sso_required` have their existing meanings;
provider downtime supplies no implicit fallback. Explicit temporary break-glass
recovery requires the existing operator command, local MFA and approval record.
See SSO docs for actual provider/account revocation limits; no instant IdP
revocation is promised.

`deploy/keycloak/compose.yml` is an **optional separate** single-node production
mode Keycloak 26.8.1 + pinned PostgreSQL 16.15 setup. Set `KEYCLOAK_ORIGIN`, its exact
TLS hostname/port, bind address, certificate/key files, unique DB password and
initial bootstrap operator secret using its required variables. For initial commissioning
add `-f deploy/keycloak/bootstrap.compose.yml` after the provider Compose file; ordinary
operation omits that overlay and does not mount or read a bootstrap secret. It uses `start`,
not `start-dev`, no public HTTP, no published database port and a persistent
`keycloak_data` volume. Finish provider setup, remove the commissioning overlay/secret from the provider
configuration, restrict admin access and configure realm MFA/clients explicitly.
It is a starting topology, not an HA, availability or capacity claim. For a
managed/external IdP, do not start this stack.

Back up **PostgreSQL separately**, using its supported consistent database backup
and restore process, plus provider signing/encryption keys, TLS keys, client
registrations and configuration. MongoDB backups cannot recover this provider.
Realm JSON export is configuration handoff, not a complete live database backup.
Rehearse provider recovery on an isolated database before relying on it.
These choices follow official [Keycloak production configuration](https://www.keycloak.org/server/configuration-production),
[container](https://www.keycloak.org/server/containers),
[database](https://www.keycloak.org/server/db) and
[hostname](https://www.keycloak.org/server/hostname) documentation; PostgreSQL pins
follow its [supported minor releases](https://www.postgresql.org/support/versioning/). Routing follows
[nginx location/try_files](https://nginx.org/en/docs/http/ngx_http_core_module.html#try_files)
and [proxy forwarding](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_set_header).

## Maintenance, backups and recovery

[Whole-installation backup/recovery](backup.md) remains host-operator-only.
Stop domain writers/jobs/MCP, block ingress, and record the old/new image digests,
source revision, schema head and all actual worker versions before adoption or
restore. Follow the [staged migration/recovery procedure](migration-recovery.md),
[tenant adoption](tenant-adoption.md) and
[isolation cutover](tenant-isolation.md); explicit organization selection,
preservation manifests, reference closure and current evidence are required.
Do not bypass activation by changing flags or moving unknown data. Extra orgs
stay sealed until their reviewed activation. All old/unscoped workers must be
stopped; never overlap worker generations against this database.

For console installations, export
`COMPOSE_FILE=docker-compose.yml:deploy/console/compose.yml` in the operator
shell and the scheduler's private environment file. This lets the existing
backup/restore scripts use the same identity flags and secret mounts; a bare
one-off base container does not inherit the overlay. After the reviewed cutover,
start with `docker compose -f docker-compose.yml -f deploy/console/compose.yml up -d context-api console`.
Keep ingress stopped during restore and use the documented one-off operator
container, rather than restoring through a running API.

Use a quiescent database for the count-and-dump backup/rehearsal: the archive is
a database-level `mongodump`, not an online cross-collection snapshot claim.
Every collection, including private global identities, memberships, sessions,
MFA, OIDC challenges, policy anchors/grants/audit/outbox, machine issuance/jobs,
summary coverage/reports and adoption preservation/quarantine is enumerated.
Managed restore verification checks each collection, not just one combined
count. Preserve mounted secret recovery copies separately from these raw private
archives. Manifest key fingerprints are commitments to the random key bytes,
not copies of the keys; restore checks reject a missing/mismatched keyring.
Recover all retained key versions before starting identity services. Losing
those keys cannot be repaired by resetting passwords or disabling MFA.

First restore into an isolated environment; verify exact records/references,
validators/indexes, key decryption, current session/policy behavior and search.
Treat a rollback to an older authority snapshot as security-sensitive: revoke
sessions/keys/challenges and repeat the applicable operator cutover before
opening ingress. Derived mongot indexes are rebuilt; neither index volumes nor
embedding caches replace primary MongoDB state. Tenant CSV reports are bounded,
policy-checked exports; they are **never** full backups, never expose raw archives,
and never authorize database `--drop` or live restore.

## Distribution and real acceptance gates

The public exporter includes `dashboard/` source, lockfile, pinned toolchain,
licenses/notices, component/browser test source and Docker/Compose/build scripts.
It excludes generated builds, node_modules, browser traces/reports, caches,
secrets and `.private/` at any depth. The guide registry contains only shipped,
maintained docs. No separate frontend repository/submodule is introduced.

```sh
python -m pytest -q tests/unit/test_v6_dashboard_deployment.py tests/unit/test_v6_backup_management_state.py
bash scripts/test-v6-deployment.sh
npm --prefix dashboard run test:e2e -- e2e/hosting.spec.ts
python scripts/prepare_open_source_release.py --output /tmp/acp-reviewed-source.tar.gz
```

The deployment script builds the real distribution images, uses uniquely named
Compose projects, fresh MongoDB/mongot, real TLS and production-mode Keycloak
with separate disposable PostgreSQL. It runs the requested management-backup
and hosting integration files. Playwright's hosting selector uses those same
images, not the older test-only SPA handler. It checks local MFA/session/CSRF,
real provider login/logout and console/API/MCP/asset boundaries. Backups are
actually materialized and compared to original BSON, including decryption using
a recovered keyring; mixed-worker startup is refused. No deployment `.env`, live
database or running production service is used/restarted. Browser certificates,
factors, people and secrets are synthetic. Record actual results/engine/runtime
limits for every run; successful mock/component tests do not establish this gate.
Extract the source archive, re-audit it and run locked frontend install/license/
static build there. Missing mirrors, images, wheels or browser infrastructure
must be reported as incomplete evidence, not a passing build.

## Complete acceptance and hardening

Use `scripts/test-v6-acceptance.sh` for the complete Python unit/integration and
real browser gate. Supply Node from the pinned dashboard toolchain on `PATH`,
`PLAYWRIGHT_CHROMIUM_EXECUTABLE`, and `V6_ACCEPTANCE_MODEL_CACHE` containing the
reviewed `sentence-transformers/all-MiniLM-L6-v2` model. Set `V6_REPORT_DIR` to a
fresh ignored private directory. The runner seals the public source inventory
and refuses changed source or inherited development selectors. The runner creates authenticated disposable storage,
copies MongoDB tools from its pinned image, builds the console/API images, and
starts the production-mode reference provider. It disables installation dotenv
loading in the integration process; never point these fixtures at an existing
database or reuse live credentials. Run the frontend checks as well:

```sh
npm --prefix dashboard run lint
npm --prefix dashboard run typecheck
npm --prefix dashboard run test:unit
npm --prefix dashboard run build
bash scripts/test-v6-acceptance.sh
```

`tests/v6_acceptance_matrix.json` maps security scenarios to actual integration
functions. The runner reconciles every integration source function with JUnit
results, including old MCP/three-client suites. Each module uses fresh
MongoDB/mongot storage; the complete large-corpus gate uses project-owned Docker
volumes, removed at every module boundary and on exit. Smaller development
selectors default to bounded tmpfs. A full vector corpus exceeds that tmpfs size;
do not shrink the corpus to turn a storage failure into a passing gate.
JUnit reports are retained and combined without hiding
failed batches. Source fingerprints must agree across the complete run. The runner refuses empty reports,
failures, errors, missing cases or skipped tests. Initial isolation rehearsal
reports bind operator cutover to the current runtime and test fingerprints.
Distribution fixtures also compare the running API image with the checkout
runtime fingerprint and record the actual API/console image IDs. Browser JUnit
checks reject skipped journeys, missing spec files and empty results; focused
selectors and development workloads never produce a complete release manifest.
The new credential issuance/offboarding race must leave no orphan credential or
receipt. Plaintext delivery is checked across every persisted fixture collection.
These checks supplement enforcement tests; an inventory match alone proves no
runtime isolation.

The older V1-V5 integration scenarios now run in a fresh, explicitly adopted
organization per test. Their initializer and standard-index assertions use the
current tenant schema; domain actions and legacy client behavior remain covered.
Operator provisioning has a trusted fixture context, while real API requests
authenticate their issued credential after discarding that ambient context.
The skill-router persistence journey also uses a real stored key. This adapter
is test setup, not an unscoped runtime compatibility mode. The separate V6
two-organization and managed-sponsor suites establish the stronger boundaries.

Performance artifacts declare hardware, actual corpus, cold/warm conditions,
latency sample counts, concurrency and response statuses. The default event
benchmark seeds one million synthetic events, once with a 90/10 tenant skew
and once with an even distribution. Other measurements cover 100 stored bounded
policy assignments, native Argon2 work, bounded reports and sponsor offboarding
with 500 verified clients. Hybrid measurements use 100,000 matching chunk IDs per
tenant and the real 384-dimensional local model; the declared synthetic text
prototypes repeat, so this is throughput/isolation evidence, not a relevance
evaluation. Embedding, lexical, vector and merge durations are recorded separately.
The remainder of the Phase 0 reference corpus must be measured and reported
separately. A conflict/refusal is not a fast successful request. Passing
correctness tests does not certify performance on a dedicated reference host.
Keep measurements, logs, screenshots, exports and manual-review notes private.
Inspect built assets and artifacts for synthetic secret leakage, and record
unmeasured workloads or exceeded targets explicitly.

Automated browser checks require a separate manual review of keyboard order,
visible focus and dialog return, true browser zoom, light/dark themes and
360/768/1440px layouts, delayed organization switching, empty/error/conflict and
coverage states, and chart table alternatives. Record the tested browser and
limitations. Automated accessibility scans alone do not establish WCAG 2.2 AA
conformance. Completion of this gate never publishes a release or restarts an
installation automatically.
