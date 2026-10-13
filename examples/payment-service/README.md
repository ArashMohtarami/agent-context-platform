# payment-service example

A fictional project with enough recorded knowledge that a request like *"Implement payment refund
support"* can be answered from what the project already decided, rather than from guesswork.
All organizations, endpoints, operational incidents, and business rules in this
fixture are invented for retrieval demonstrations.

## What it contains

| Context item | Type | What a refund needs from it |
| --- | --- | --- |
| Payment service architecture | `architecture` | Which endpoint, which downstream systems |
| Keycloak integration and required scopes | `integration` | `payments:refund` is separate from `payments:write` |
| Payment provider behavior, including refunds | `integration` | Refunds are asynchronous; reconcile on the webhook |
| Wallet behavior on payment and refund | `business_rule` | Split refunds by `funding_breakdown` |
| Ledger rules | `business_rule` | Append-only; write the pair on `refund.succeeded` |
| Idempotency rules | `architecture_decision` | `Idempotency-Key` reaches the provider too |

Plus two project memories: refund changes need a payments-team reviewer, and tests never call the
real provider.

The content is written to exercise both halves of hybrid retrieval — prose for the vector side, and
exact technical vocabulary (`INSUFFICIENT_SCOPE`, `payments:refund`, `wallet:write`,
`Idempotency-Key`, `refund.succeeded`, `/api/v1/payments`) for the lexical side.

## Loading it

The platform must be running and ready. Use a development database and a scoped
key that can create `payment-service` and write its context and memory:

```bash
curl --fail http://127.0.0.1:8000/health/ready
umask 077
docker compose exec -T context-api python -m app.cli.contextctl --json keys create \
  --name payment-example --scope projects:write \
  --scope context:write --scope memory:write \
  --project payment-service | jq -er .key > ./secrets/payment-example-key
.venv/bin/python examples/payment-service/load.py \
  --token-file ./secrets/payment-example-key
```

The project is created through the Context API, because bringing an isolation boundary into
existence is an operator's decision. Everything stored inside it goes through the **MCP tools**, so
loading the example is also a check that `save_context` and `save_memory` work end to end.

Every item carries an `external_id`, so running it twice stores one copy rather than eight more.

Point it somewhere else with `--api-base-url http://127.0.0.1:8001`.
The loader also accepts `CONTEXTCTL_API_TOKEN_FILE`, so `make load-example` works
after setting that variable in an activated development environment. It reads
the configured file, uses bearer authentication for both API and MCP writes,
and does not print the credential.

## Using it

Ask Codex, in a session configured per [docs/codex.md](../../docs/codex.md):

> Implement payment refund support in payment-service.

It should call `search_knowledge` with `project_id="payment-service"` and receive the ledger rules,
the idempotency rules, the provider's refund behaviour, and the wallet split — ranked together, in
one call, well inside the token budget.

## Removing it

Nothing in the platform deletes: context and memory are archived rather than destroyed. Set
`status` to `archived` on the items you no longer want retrieved, or drop the records directly in
MongoDB if you are resetting a development database.
