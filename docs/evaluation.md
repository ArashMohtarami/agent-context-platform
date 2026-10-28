# Disposable local evaluation

This path commissions a **fictional, disposable organization**, separately from
an existing installation. It is not production activation or release approval.
Full release reconciliation remains open. Do not restore evaluation volumes into
production. The current commissioning mode disables embeddings and downloads;
it verifies real lexical/vector search infrastructure with a temporary probe,
not semantic retrieval quality. No paid model or external mail provider is used.

## Prepare reviewed inputs

Use Linux, Python 3.12+, the exact development lock, Docker with its local Unix
socket, Compose 2.24.4+, and OpenSSL. The evaluator rejects remote Docker contexts
and uses explicit configuration without reading deployment `.env`, existing
secrets, backups, database volumes or agent configuration. It never pulls/builds
images automatically. Database and search need 2 GiB/2 CPUs each; the API has a
2 GiB/2 CPU limit and console 128 MiB. These limits are not a minimum-host or
capacity guarantee. Preflight requires 4 GiB free disk for disposable state.

Build the API and console from the reviewed checkout, using the existing pinned
Dockerfile and preloaded locked wheels/npm dependencies. Record cold preparation
time separately. These commands do not start the installation:

```sh
docker build --target production -t acp-evaluation-api:local .
docker build --target console -t acp-evaluation-console:local .
```

Preload the exact MongoDB/search images in
[`deploy/evaluation/inputs.json`](../deploy/evaluation/inputs.json). An offline
installation must prepare build inputs separately. No `latest` tag is supported.
An explicitly selected reviewed local API/console tag can be passed with
`--api-image` and `--console-image`; the manifest freezes its image ID. The API's
actual runtime fingerprint is checked before domain/schema commissioning.

Isolation activation requires genuine current reports. Inspect the complete
focused plan, then execute it:

```sh
env -u DOCKER_HOST .venv/bin/python scripts/test-focused.py --phase activation --plan
env -u DOCKER_HOST .venv/bin/python scripts/test-focused.py --phase activation --run
```

The runner refuses inherited selection flags; remove any reported development
flags explicitly. It selects three required unit modules, eleven real isolation
cases, then the single cutover case consuming the two earlier reports. A required
skip/failure or missing service is incomplete. Keep the emitted run directory.
Current source, selectors, results, XML fingerprints and service image identities
must match for reuse. Tests changed after a run invalidate that run. Reports are
private and are not bundled with the evaluator or public source.

## Review root intent and start

The installation operator must review this bounded intent before passing an
approval reference. The proposed owner can create projects in this fictional
organization. Its delegation ceiling includes current/future projects **within
this evaluation organization only**, for project listing, context read/write and
search. The intent also includes `agent:self` on this exact evaluation
organization for MCP capability discovery; it grants no machine administration
or session writes. A ceiling is not the owner's personal corpus access. The operator later
approves management over the named worker, contributor role, own credential
issue/revoke and client metadata. Root authority expires within 24 hours. The
worker/client receive only the actual `evaluation-notes` project, for at most
two hours. No private memory or independent content review grant is implied.

```sh
./scripts/evaluate.sh start --approve-root YOUR_REVIEW_REFERENCE \
  --activation-run .private/v7-competitive-planning/phase-1/runs/activation/RUN_ID
```

The command prints a unique instance ID, canonical private state path and exact
loopback HTTPS origin. Save the ID; every later operation requires it. Ports and
Docker names are unique. MongoDB/search have no published ports. Fresh secrets,
private local handoff, MFA keyring, CA and leaf certificate are generated for
this instance. No default password, shared admin token or anonymous owner claim
exists. Manifest/checkpoints contain no passwords, sessions or client tokens.

The command uses the actual empty-store operator adoption, verified quiescent
dump/manifest and six-module isolation activation. It starts the staged HTTPS
identity service at schema 39, then waits for two recipients. This is not a test
fixture installer. No privileged domain or identity rows are inserted directly.

## Trust and enroll recipients

The instance CA is `STATE/tls/ca.crt`; inspect its fingerprint locally before
trusting it. The terminal client uses this CA only for this exact loopback HTTPS
origin. Browser use requires an **explicit** import into a dedicated temporary
browser profile. The evaluator never edits OS trust, accepts a certificate
warning, or uses an ignore-TLS flag. Delete that profile/trust when finished.

For the local handoff rehearsal, each fictional recipient uses its own terminal
turn. The installation operator controls the private local mail sink, just as it
controls raw database access; this is not external email verification. Challenges
are selected by exact recipient/purpose and submitted through the real
browser-bound HTTPS/CSRF endpoints. Use distinct unique passwords:

```sh
./scripts/evaluate.sh start --instance INSTANCE_ID --enroll owner
./scripts/evaluate.sh start --instance INSTANCE_ID --enroll worker
./scripts/evaluate.sh start --instance INSTANCE_ID
```

Enrollment prompts on an interactive terminal. Store each recipient's displayed
MFA seed and one-time recovery codes privately. They are never checkpoints or
application logs. Finish pending verification in the same enrollment process;
if that browser-bound process is lost before verification, reset this disposable
instance and start fresh. Do not mark the identity verified manually. Existing
verified accounts/passwords/MFA are not reset by repeated starts.

After both recipients verify enrollment and confirm MFA, resume performs the
explicit schema 40/41/42 operator cutovers with API writers stopped. It checks an
empty legacy-key inventory, activates managed agents and records the reviewed
root generation. Repeated starts preserve identities, root generation and
existing credentials. Interrupted operator stages resume from actual schema and
audit state; original adoption archives/keys remain required. Changed source,
inputs, image IDs or expired approval refuse resume. `stop`/owned `reset` remain
available after source changes. Interrupted input preparation requires reset.

The final console shows the fictional evaluation organization name. The CLI
always labels evaluation state and keeps `production_approval: false`.

## Approve and deliver one client credential

The owner authenticates with a fresh MFA code and explicitly approves the
distinct worker's project use and client policy:

```sh
./scripts/evaluate.sh start --instance INSTANCE_ID --approve-client
```

This creates the fictional Notes project through the normal project API,
delegates bounded use to the worker and approves `evaluation-worker`. It uses
current parent generation and service authorization; the worker cannot approve
itself. No content is published. The verified worker then authenticates and
chooses a **new** file in its own private directory:

```sh
umask 077
mkdir -p /tmp/my-fictional-evaluation-client
./scripts/evaluate.sh start --instance INSTANCE_ID \
  --token-output /tmp/my-fictional-evaluation-client/platform-token
```

The worker receives plaintext once, directly into the chosen 0600 file. Existing
files/symlinks are refused. The operator's manifest stores no token. A crash/lost
delivery requires an explicit revoke/replacement through the console; start does
not replay or silently issue another token. This recipient-owned file remains
outside reset ownership: delete it yourself when finished. Human sessions remain
separate from bearer credentials. Client setup/handoff is covered separately in
[client onboarding](client-onboarding.md).

## Inspect, resume and remove

```sh
./scripts/evaluate.sh status --instance INSTANCE_ID
./scripts/evaluate.sh doctor --instance INSTANCE_ID
./scripts/evaluate.sh stop --instance INSTANCE_ID
./scripts/evaluate.sh start --instance INSTANCE_ID
./scripts/evaluate.sh reset --instance INSTANCE_ID
```

`status` reads redacted checkpoints. `doctor` checks current inputs/images,
ownership and verified HTTPS database/search-coordinator readiness without
writing data. Startup separately requires actual READY/queryable lexical/vector
queries against its removed disposable probe. Doctor does not prove a particular
client's grants, vector-query readiness at that later instant or retrieval relevance.
`stop` preserves state. Reset prints the exact resources/path before deletion and
removes only that instance's label/path-verified containers, networks, volumes
and state. It requires the exact ID, refuses foreign resources/symlinks/live
volume names, and never prunes Docker globally. Failed/interrupted startup leaves
private diagnostics and stops this instance's traffic; inspect its checkpoint,
resume or reset explicitly. Interrupted cleanup stays visible and is retryable.

Raw logs, reports, invitation messages, CA/private keys and backups live under
ignored `.private/` state and must never be exported. A successful evaluation is
not approval for production, external client compatibility, adoption or benchmark
claims. See [verification](verification.md) and [focused tests](focused-tests.md).
