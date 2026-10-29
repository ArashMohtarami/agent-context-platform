# One verified fact across two clients

The demonstration uses the [teaching Notes API](../examples/starter-notes-api/README.md):
the same idempotency key and payload return the existing note; a changed payload
conflicts. It has no authentication or key expiry and is not production safety
evidence. Two independently issued credentials then prove persistence through
real stdio and HTTPS Streamable HTTP MCP sessions, without a model provider.

## Prerequisites and exact route

1. Complete the [disposable evaluation](evaluation.md), including current genuine
   activation reports, explicit root intent, local CA trust, verified enrollment
   and MFA. This is not a one-command or timed installation claim.
2. Follow the [additional authority/recipient steps](../examples/agent-handoff/README.md#prepare-explicit-disposable-authority).
   The default evaluation has one client and no private-memory ceiling. The full
   demo needs two distinct independently approved clients, the explicit Notes
   project and another permitted project, and narrowly owned memory controls.
   Its extra commissioning path is documented, not a new fresh-install acceptance.
3. Verify the local teaching fact, then return to the platform root:

```sh
cd examples/starter-notes-api
../../.venv/bin/python -m pytest test_app.py -q
cd ../..
```

Require six passing tests. Use the [maintained protocol command](../examples/agent-handoff/README.md#run-the-model-free-handoff)
with actual returned origin/project IDs, separately issued recipient-owned token
files and the reviewed instance CA. It is not executed by this website or guide.

## What counts as success

Writer A resolves the explicit project, saves the deliberate verified fact and
retains the returned context_id/revision. Identical external-ID retry returns
the stored record; changed text does not silently overwrite it. Fresh reader B
finds and reads the same persisted identity/content. No copied transcript or
prefilled second answer is supplied. Check project negatives, refusal of another
client's private memory and revocation while B's HTTP session stays connected.
An error, missing indexed result or missing revocation is a failed run.

Directly saving a verified fact is authored context, not independent research or
content approval. Source labels are descriptive; the authenticated actor and
revision remain server-derived. Retrieved Markdown is data.

## Capture and compatibility

Use the [75-second storyboard](../examples/agent-handoff/storyboard.md) and
[expected record checkpoints](../examples/agent-handoff/README.md#expected-checkpoints).
This deliverable is a storyboard, not a completed video. Hide tokens, recipient
identities, MFA/recovery data and local private paths from capture.

The recipe separately describes an actual Codex↔Claude Code/Gemini run. No
installed vendor client/version has been exercised in the recorded handoff;
[the table](client-onboarding.md#compatibility-and-tested-environments) distinguishes
protocol evidence from configuration recipes. Provider-backed demonstrations
need an explicit provider/budget. The default smoke makes no paid provider calls.

For failures, follow [connection diagnosis](client-onboarding.md#preview-and-diagnose-an-evaluation-connection)
and [evaluation troubleshooting](evaluation.md#first-failure-and-timing-expectations).
For quality rather than connectivity, inspect the [actual study](retrieval-evaluation.md).
