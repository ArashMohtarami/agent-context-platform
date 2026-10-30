# A Notes API fact across two MCP clients

This is a deterministic **protocol demonstration**, using two distinct issued
managed-client credentials, a real stdio process, a fresh Streamable HTTP MCP
session and persisted project context. It calls no model provider and transfers
no chat transcript. It does not execute Codex, Claude Code or Gemini CLI. No
vendor application/version is verified by the client labels in platform metadata.
Original code, the fictional fixture and documentation are MIT project source.

The [teaching Notes API](../starter-notes-api/README.md) is the source of the
deliberately saved fact in [fact.json](fact.json). It has six behavioral tests,
no authentication and no key expiry; its retry contract is not evidence of
production multi-user safety. Directly saving the verified fact creates authored
project context, not an independent research/content review or approved skill.

## Prepare explicit disposable authority

Use a current reviewed API build from the [evaluation guide](../../docs/evaluation.md) and
[connection preview/doctor](../../docs/client-onboarding.md) first. Never use
live projects, secrets, installed client settings or an admin/shared token as demo
inputs. Use one fictional organization, a selected Notes project and another
**permitted** project in that organization. Names below are examples, not defaults.

The default evaluator creates one project/client and **does not approve private
memory**. That default is intentionally unchanged. This fuller demo requires
additional explicit review; a default evaluation token alone is insufficient.
Before reviewing the new root ceiling, create the second fictional project
through the normal console project screen. Independently approve two client
metadata records with currently permitted project grants for the verified worker.
Their IDs must already exist before the operator can include their
`client_memory` references in a reviewed ceiling.
The installation operator reviews a complete bounded root policy through the
existing stopped-writer maintenance operation (inside that disposable instance's
configured operator environment):

```sh
contextctl --org-id FICTIONAL_ORG management approve-root \
  --user-id VERIFIED_OWNER --policy-file /reviewed/handoff-root.json \
  --approval-reference YOUR_EXPLICIT_REVIEW_REFERENCE \
  --writers-stopped --worker-schema-version 42
```

Use actual IDs. This replaces the reviewed management tuples and ceiling, not a
merge: preserve the reviewed management tuples, worker targets/allowed role,
expiry and existing
necessary ceiling. Add only the bounded domain permissions below to the reviewed
ceiling. The JSON schema is `app/management/models.py::RootApproval` with
`tuples`, `ceiling`, `targets`, `allowed_roles` and `expires_at`.
Keep the policy file private. Stop writers for the operator change and resume
only that disposable instance. Changing a root generation invalidates old
descendants: the owner must review/reissue the worker assignment and client
approvals against the new generation; do not edit persisted grants directly.
This example does not automate commissioning or read the operator's credentials.

After the review, reapprove the worker and both clients against the new root
generation using the exact domain grants below. Resource references must exist
before included grants can be approved. An owner cannot approve their
own client as that same worker. The worker assignment and approved client/key
rights must all cover these action/resource pairs:

| Action | Writer A (stdio) | Reader B (HTTP) |
| --- | --- | --- |
| `agent:self` | `organization_knowledge`: exact fictional org | Same |
| `projects:read` | `project_directory`: Notes project | Notes and permitted second project |
| `context:read` | `project`: Notes project | Notes and permitted second project |
| `context:write` | `project`: Notes project | None needed |
| `search:read` | None needed | `project`: Notes and permitted second project |
| `memory:read`, `memory:write` | Both `project`: Notes and `client_memory`: A only | Both `project`: Notes and `client_memory`: B only |

Each tuple uses `selector: "include"`, the exact resource ID in `ids` and no
unrelated conditions. For example, a recipient's own private-memory grant is
`{"action":"memory:read","resource_kind":"client_memory","selector":"include","ids":["handoff-a"]}`.
Both controls use `project_agent` scope on the selected Notes project, requiring
both project and client-memory dimensions. Both are intentional writes; B is not described as an
entirely read-only credential. Current sponsor/team/parent rights and expiry
still apply. No local tool filter establishes these grants.

The verified worker additionally needs `credentials:issue:own`,
`credentials:revoke:own` and `clients:read:own` on their own member resource. Use
the existing console to generate each client's token once, separately, into
recipient-owned regular 0600 files in a private directory. Neither the owner nor
the smoke can recover another recipient's plaintext. Lost delivery requires
explicit revoke/replacement. Keep human sessions separate from machine tokens.

Inspect current client metadata first and use its returned revision/expiry bound
for issuance. A timestamp retained before database persistence can have finer
precision than the stored bound; never extend expiry to get an issuance accepted.

The focused integration test uses maintained fictional managed/knowledge
fixtures and these audited approval/recipient APIs. It proves protocol/storage/
authorization behavior, **not** that fixture rows are a supported installer or
that this entire additional operator procedure was commissioned from scratch.

## Verify the local fact, then return to the platform root

```sh
cd examples/starter-notes-api
../../.venv/bin/python -m pytest test_app.py -q
cd ../..
```

Require six passing tests and inspect `notes_app.py` before saving the claim.
`--confirm-verified-notes` below is your deliberate confirmation; the smoke never
executes tests or creates JUnit. It records source hashes, not an invented test
receipt. Keep the actual test output separately in private evidence.

## Run the model-free handoff

Inspect the exact evaluation HTTPS origin and reviewed CA fingerprint. Trust only
that CA for this process; never disable TLS validation or silently install a CA.
Replace all paths and project IDs below. Both platform credentials must be for
different clients in the same organization. Use a fresh run ID for a new demo;
reuse the ID only when intentionally checking the same external record.

```sh
umask 077
mkdir -p /tmp/my-fictional-handoff
.venv/bin/python -I examples/agent-handoff/smoke.py \
  --origin https://127.0.0.1:ACTUAL_PORT \
  --project-id notes-api --negative-project-id other-notes \
  --run-id notes-demo-1 \
  --client-a-token-file /recipient/private/client-a-token \
  --client-b-token-file /recipient/private/client-b-token \
  --ca-file /evaluation/state/tls/ca.crt \
  --revocation-signal /tmp/my-fictional-handoff/revoked \
  --allow-disposable-writes --confirm-verified-notes
```

Replace `ACTUAL_PORT` with the actual numeric port. Omit `--ca-file` only with a
normally publicly trusted certificate. The signal must **not already exist**;
its parent must be a canonical, current-user-owned 0700 directory. The command
ignores inherited credentials/configuration, uses no paid model and has a
300-second deadline. Requests are bounded to 8 seconds; indexed fact/private
control waits to 45 seconds each; revocation confirmation to 120 seconds.

At `awaiting_recipient_revocation`, keep the process running. In the verified
worker's separate console session, revoke B's **specific issued key** using its
current revision. Do not suspend another client or reset the instance. Then, in
the worker's other private terminal, request the live recheck:

```sh
umask 077
printf 'revoked\n' > /tmp/my-fictional-handoff/revoked
```

The signal proves nothing by itself: the **same already initialized HTTP MCP
session** must actually refuse `get_context` before completion. Failure to
revoke, missing infrastructure, timeout or unexpected access exits nonzero and
does not print a successful final checkpoint. Delete only your own signal/token
files when finished; stop/reset the exact evaluation instance through its normal
commands. The smoke leaves its context and two labeled private-memory controls
in the disposable store; it performs no global cleanup or record deletion.

## Expected checkpoints

Output is JSON lines; IDs below must be actual returned values, never fabricated:

1. `preflight`: actual server identities establish distinct clients/same org;
   source paths/hashes identify Notes source/tests/fact. No private client/user
   names or token values are exported.
2. `saved`: A resolved the explicit project and saved the fact. Retain the actual
   `context_id`, `revision`, `project_id`, `source`. Identical retries and a changed
   payload with the same **external_id only** return the same existing record.
   This is create de-duplication: changed text is **not** an update and is **not**
   the Notes API's 409 behavior. Separate gateway `idempotency_key` payload-conflict
   semantics are not exercised here. Correct facts via the revision-aware API/
   console. If an old external ID contains different text, the smoke fails closed.
3. `retrieved`: after A closes, fresh B resolves the project, finds that exact
   record via real indexed `search_knowledge`, then `get_context` returns equal
   ID/revision/content/source. B receives a search question and ID, no transcript
   or prefilled answer. Direct read alone cannot pass the indexing checkpoint.
4. `boundaries`: B resolves a second allowed project; the fact is absent from
   its query and a direct `get_context` there returns `NOT_FOUND`. B's own private
   memory is a positive searchable control while A's is absent; direct REST read
   of A's project-agent memory under B's credential returns 404. There is currently no
   MCP `get_memory` tool, so that direct read deliberately uses the existing API.
5. `already_connected_revoked`, then `complete` with `status: "passed"`: B's open
   HTTP session refuses the retained context after real recipient revocation.

Only a real completed run establishes these checkpoints. Search/persistence
success is not relevance benchmarking, human understanding or adoption evidence.
Retrieved Markdown remains data. Full release reconciliation remains open.

## Focused automated reproduction

Use the [focused runner](../../docs/focused-tests.md) with its pinned offline
MongoDB/search prerequisites. Inspect **each** plan before running the same graph:

```sh
env -u DOCKER_HOST .venv/bin/python scripts/test-focused.py --phase p05-unit --plan
env -u DOCKER_HOST .venv/bin/python scripts/test-focused.py --phase p05-unit --run
env -u DOCKER_HOST .venv/bin/python scripts/test-focused.py --phase p05-runner --plan
env -u DOCKER_HOST .venv/bin/python scripts/test-focused.py --phase p05-runner --run
env -u DOCKER_HOST .venv/bin/python scripts/test-focused.py --phase p05-smoke --plan
env -u DOCKER_HOST .venv/bin/python scripts/test-focused.py --phase p05-smoke --run
env -u DOCKER_HOST .venv/bin/python scripts/test-focused.py --phase p05-regressions --plan
env -u DOCKER_HOST .venv/bin/python scripts/test-focused.py --phase p05-regressions --run
```

The smoke test executes this distributed command, actual child stdio, HTTPS MCP,
real MongoDB/mongot and normal recipient revoke. Regressions select only the
existing connected stdio/HTTP revoke variants and read-A/write-B/private-memory
case. No broad suite, activation dependency, vendor executable or provider runs
automatically. Private reports are not shipped with these assets.

## Optional demonstration in real coding applications

This is a separate manual exercise; **no vendor application is marked verified**
by the protocol run. Use [Codex](../../docs/codex.md) with the stdio recipe and
[Claude Code](../../docs/clients/claude-code.md) or
[Gemini CLI](../../docs/clients/gemini-cli.md) with its current HTTP recipe. Review
the [connection preview](../../docs/client-onboarding.md) and manually merge only
the selected server in a disposable client profile. Do not alter active settings
as part of this example's development/testing. Trust the scoped CA in the actual
client process. Use the distinct independently issued A/B keys and exact project.

Before any model-provider call, select the provider/model and explicit spending
budget; do not run if either is missing. The model-free run requires neither.
Record the actual installed `codex --version` and `claude --version` or
`gemini --version`, platform, transport, reviewed configuration shape (redacted),
run time, requested tools and result/error. A client is verified only for the
actual version/transport/journey you executed, including a failed outcome.

In a fresh A workspace/session, review the local six-test result/source and
deliberately call `resolve_project`, then `save_context` with `fact.json` adapted
only for the selected project/external run ID. Retain returned ID/revision. Close
A. In a new B workspace/session, provide **only** the project and question
“How does this Notes API handle an Idempotency-Key retry, and what are its limits?”
Do not paste A's transcript, the fact/answer or a generated response. Require B
to use `search_knowledge`, then inspect the returned full context with
`get_context`. Compare B's actual tool result with A's retained ID/revision/text;
its prose must attribute the source and retain teaching limits. Run the same
negative queries/private-memory checks and revoke the connected B key. Record
real refusals. If the application reconnects automatically, distinguish that
from the protocol smoke's continuously open-session check.

Use the [75-second storyboard](storyboard.md) for a truthful capture. It is a
storyboard, not a completed video or evidence that these vendor steps ran.
