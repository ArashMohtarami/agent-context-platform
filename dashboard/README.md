# Organization console foundation (V6 Phase 9)

This repository owns the frontend in `dashboard/`. It uses the V6 session APIs
on the **same HTTPS origin** under `/api/v6`, with the application at `/console/`.
The console includes identity/security, organization management and the authorized
knowledge workspace. No invented activity data is shown.
The `/console/specimens` page contains explicitly labeled sample components.

Use Node **24.22.0** (`.node-version`) and npm 10 or later. All direct versions
and the complete dependency graph are locked. From the repository root:

```sh
npm --prefix dashboard ci
npm --prefix dashboard run lint
npm --prefix dashboard run typecheck
npm --prefix dashboard run test:unit -- src/auth/auth.test.tsx src/shell/org-switch.test.tsx src/security/my-security.test.tsx
npm --prefix dashboard run licenses
npm --prefix dashboard run build
npm --prefix dashboard exec -- playwright install --with-deps chromium
npm --prefix dashboard run test:e2e -- e2e/auth.spec.ts e2e/org-switch.spec.ts e2e/my-security.spec.ts
```

The browser command provisions and removes its own disposable MongoDB replica
set, mongot and HTTPS Keycloak containers through the existing isolation runner.
It needs Docker Compose, the repository `.venv` and pinned Python dependencies,
8 GiB available memory, and a built `dashboard/dist`. It never loads the deployment
`.env`. The API is a real Uvicorn HTTPS server with the real application routes,
persisted session/challenge state and a private local mail sink. The built SPA is
served with the same security headers as the deployment example. A private
filesystem control channel resets fictional fixtures between tests; there is no
test login endpoint or fixture API in the product. The browser accepts only the
disposable self-signed test certificate; backend provider transport still verifies
its pinned test CA and exact approved HTTPS endpoints.

`V6_TEST_PYTHON` can select another prepared Python environment. The default
MongoDB fixture limit is 3 GiB. `V6_REPORT_DIR` selects private infrastructure
reports. Browser artifacts default to ignored `dashboard/test-results/`. If the
Playwright browser download is unavailable, `PLAYWRIGHT_CHROMIUM_EXECUTABLE` may
select an installed Chromium browser; record that executable's actual version.
This fallback is not a claim of coverage for other engines or versions.

For local CSS work, `npm --prefix dashboard run dev` serves specimens. Real login
requires the same-origin HTTPS setup below; the development server and preview
server do not synthesize an identity or proxy arbitrary origins. There are no
frontend credential/environment variables, persistent query caches or CDN assets.

See [deployment and security](../docs/dashboard-foundation.md),
[dependency review](DEPENDENCIES.md), [runtime notices](THIRD_PARTY_NOTICES.txt),
and the complete [locked license inventory](dependency-licenses.json).

Phase 10 management routes and verification commands are documented in
[dashboard management](../docs/dashboard-management.md). The focused component
and browser selectors retain the filenames requested for Phase 10. Real API
adapter coverage is `tests/integration/test_v6_management_adapters.py`, selected
by `V6_MANAGEMENT_ADAPTERS_ONLY=1 bash scripts/test-v6-isolation.sh`.

Phase 11 knowledge adapters, ownership boundaries and focused verification commands
are documented in [dashboard knowledge](../docs/dashboard-knowledge.md).

Phase 12 evidence, ingestion, independent review and controlled promotion are
documented in [dashboard research](../docs/dashboard-research.md).
