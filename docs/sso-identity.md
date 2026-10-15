# SSO and active organization context

V6 Phase 4 adds session-only OIDC, exact identity linking and explicit organization
switching. It requires the verified Phase 2 isolation gate and Phase 3 local
identity/MFA. It creates no corpus grants and exposes no team or data dashboard.
All features remain disabled by default. Schema 39 follows the actual schema-38
head. Applying it revokes existing browser sessions once, preserves their history,
and refuses older runtimes. Repeating the migration preserves new sessions.
Drain API/browser workers before cutover. The migration raises the local identity
control marker to 39 before adding OIDC evidence; existing schema-38 workers
therefore fail their per-request identity gate, while new workers without SSO
configuration refuse startup. Sessions without version-39 authentication evidence
are refused by the SSO runtime. In-flight work must be drained operationally.

## Installation configuration

Enable `V6_SSO=true` alongside the existing tenant isolation and local identity
settings. Mount a private JSON registration file and set
`V6_OIDC_REGISTRATIONS_FILE` to its container path. A deployment override must
explicitly mount this file and any private CA file read-only. Never commit these
files or expose them through frontend configuration. Each registration belongs
to exactly one organization. Example structure (replace the documentation IP,
hostnames and secret through your secret manager before use):

```json
[
  {
    "registration_id": "company-keycloak",
    "org_id": "company",
    "issuer": "https://login.example.com/realms/company",
    "client_id": "agent-context-company",
    "client_secret": "REPLACE_WITH_A_RANDOM_SECRET_FROM_YOUR_SECRET_MANAGER",
    "redirect_uri": "https://context.example.com/api/v6/auth/oidc/callback",
    "post_logout_redirect_uri": "https://context.example.com/api/v6/auth/oidc/logged-out",
    "authorization_endpoint": "https://login.example.com/realms/company/protocol/openid-connect/auth",
    "token_endpoint": "https://login.example.com/realms/company/protocol/openid-connect/token",
    "jwks_uri": "https://login.example.com/realms/company/protocol/openid-connect/certs",
    "end_session_endpoint": "https://login.example.com/realms/company/protocol/openid-connect/logout",
    "connect_ip": "203.0.113.10",
    "algorithms": ["RS256"],
    "mfa_acr": [],
    "mfa_amr": [],
    "require_mfa": false,
    "assurance_version": 1,
    "max_session_seconds": 3600
  }
]
```

The operator approves exact endpoints and a connection IP. Outbound HTTP pins
that IP while retaining the original HTTPS hostname for TLS SNI/certificate and
Host validation. DNS is never consulted. Provider endpoints must share the
issuer authority. Only discovery, JWKS and token URLs may be fetched; responses
are bounded to 128 KiB, four concurrent flows, five-second HTTP timeouts and a
twelve-second operation deadline. Redirects, environment proxies, userinfo,
dynamic registration and browser-supplied URLs are disabled. IP/trust-root,
client secret or assurance changes require a reviewed registration update,
worker restart and explicit org reapproval; existing evidence fails closed.
`ca_file`, if supplied, adds an explicit private trust root through a mounted
PEM file; its content hash is part of the approved registration version.
TLS verification is never disabled, including disposable tests.

Approve a mounted registration and policy through the installation host CLI:

```sh
contextctl --org-id company identity configure-sso \
  --registration-id company-keycloak --policy sso_preferred \
  --approval-reference CHANGE-123
```

Configuration increments the org policy version, invalidating previous policy
contexts. `local_only` permits local login and rejects provider login.
`sso_preferred` permits either explicit login method. `sso_required` accepts only
that org's approved issuer, client registration and configured assurance rules.
An outage never changes these policies or automatically selects local login.

Initial enrollment remains operator-invited local enrollment. While the org is
`sso_preferred`, establish local MFA and link the exact external identity before
switching the org to `sso_required`. There is no public signup, automatic
membership creation or email-based migration. An already linked identity cannot
be moved to another global user by this flow.

## Reference provider configuration

Reference provider: Keycloak 26.8.1, image pinned in
`tests/infrastructure/v6-oidc.compose.yml`. Create a confidential OpenID Connect
client with Standard Flow enabled, S256 PKCE required, exact callback URI and
exact post-logout URI above. Disable Direct Access Grants and implicit flow.
Retain the built-in `basic` client scope (including subject and authentication
time) and `acr`; removing required claims causes token validation to fail closed.
Enable HTTPS with a valid certificate; require HTTPS for the realm. No wildcard
redirects, origins or registration endpoints are used.

Empty MFA rules convey **no MFA assurance**, even if an IdP sends a claim named
`mfa`, an `acr` value of `1`, or an administrator group. To trust provider MFA,
first review and enforce the provider authentication flow, then allow exact
`mfa_acr` values and/or required `mfa_amr` methods. All configured conditions must
match. `require_mfa=true` rejects every provider login/link without that proof.
Do not label a password-only flow as MFA. Increment `assurance_version` when
rules change. The disposable second realm uses required password and OTP
executions with Keycloak's native AMR mapper, rather than hardcoding an MFA claim.

Official sources checked for this implementation:

- [Authlib HTTP clients and Code + PKCE](https://docs.authlib.org/en/stable/oauth2/client/http/index.html)
- [OpenID Connect Core token validation](https://openid.net/specs/openid-connect-core-1_0.html#IDTokenValidation)
- [Keycloak endpoints and logout](https://www.keycloak.org/securing-apps/oidc-layers)
- [Keycloak TLS configuration](https://www.keycloak.org/server/enabletls)
- [Keycloak authentication execution references and AMR](https://www.keycloak.org/docs/latest/server_admin/index.html#_authentication-flows)

Authlib 1.9.1 and joserfc 1.7.5 are pinned BSD-3-Clause dependencies, verified against
[Authlib metadata](https://pypi.org/project/Authlib/1.9.1/) and
[joserfc metadata](https://pypi.org/project/joserfc/1.7.5/); cryptography
remains the existing maintained backend. Downloaded wheel hashes are checked
against official PyPI metadata. The Authlib HTTPX compatibility adapter supports
our pinned HTTPX 0.28.1; it emits an upstream deprecation warning recommending
HTTPX2. A future HTTP client migration requires its own compatibility review.
Keycloak is [Apache-2.0](https://github.com/keycloak/keycloak/blob/26.8.1/LICENSE.txt)
and runs only as disposable test infrastructure here.

## Browser contracts

All routes below use the existing Secure/HttpOnly/host-only session cookies.
Every POST requires the exact Origin and matching CSRF token. Authorization
headers are rejected. V1–V5 and MCP continue to reject browser cookies.

| Method and `/api/v6` path | Admission and result |
| --- | --- |
| `POST /auth/oidc/start` | Preauth cookie + CSRF, `{org_id}`; returns an approved authorization URL. |
| `GET /auth/oidc/callback` | Exact registered URI, unique state/code parameters, same browser cookie, persisted single-use transaction, nonce and S256 verifier. |
| `POST /auth/identities/link` | Current session + recent MFA, `{org_id}` own active membership; authenticates the target identity freshly and invalidates all old user sessions. Requires fresh login afterward. |
| `POST /auth/oidc/reauthenticate` | Current session, `{org_id, purpose: "switch" or "reauth"}`; explicit target-provider login, same exact user identity required. |
| `GET /me/organizations` | Only the caller's active, verified memberships with minimal directory fields. |
| `POST /me/active-organization` | Own active target membership and target auth policy; rotates session/CSRF and returns a fresh policy context. No selector conveys authority. |
| `GET /organizations/{org_id}/session-context` | Path must match active org; `X-Session-Context` must match the current context. |
| `POST /auth/oidc/logout` | Revokes the application session before returning the approved provider logout URL. |
| `GET /auth/oidc/logged-out` | Single-use logout state acknowledgement; conveys no authentication. |

OIDC login callbacks are the narrow mutation exception to Origin/CSRF headers;
their persisted, five-minute, single-use browser/purpose/org-bound state is the
CSRF defense. State hashes and encrypted PKCE verifiers stay in private MongoDB.
No token or verifier is stored in browser local/session storage or readable
session cookies. Code, ID/access/refresh tokens are not persisted or returned.
The access token exists only during ID token validation. Exact issuer/subject
is the identity key. Equal email, verified email, roles and groups are ignored.
Provider errors and validation diagnostics are generic and secret-free.

Sessions record verified identity ID, issuer, registration/version, client ID,
authentication time, assurance version and verified `acr`/`amr`. Unconfigured
provider assurance is reported as `authenticated`, never represented as a local
password or fake machine identity. Local MFA can step up an OIDC session without
turning its provider proof into a local login. Privileged actions still require
MFA within ten minutes.
`/me` distinguishes missing MFA enrollment from an enrolled user needing recent
step-up, so an ordinary OIDC login does not suggest enrolling an existing factor again.

Organization switching checks both memberships and current policy afresh, then
revokes the old session and changes the CSRF token and policy context ID. The
new session does not extend the original absolute expiry. Another org's provider
registration never satisfies an SSO-required target, even for the same user.
Use explicit target reauthentication when a direct switch is refused. Org paths
must equal the active org. A supplied stale `X-Session-Context` is rejected on
self routes too. Future UI must attach this context, cancel old queries and
clear org caches/drafts/download intents before presenting the new organization.
There is no browser management UI in this phase.

Responses are `no-store` with `Referrer-Policy: no-referrer`. Application logging
redacts OAuth credentials, and the browser boundary strips callback query strings
before Uvicorn access logging. Configure external ingress/access logs to omit
query strings on `/api/v6/auth/oidc/*` as well. Do not enable third-party HTTP
wire/debug logging or capture callback URLs in analytics.

## Revocation and emergency operation

Membership suspension, user disable/epoch changes, policy/registration changes,
identity removal, session logout and expiry are checked locally on every request.
SSO sessions expire at the earliest of the ID token expiry, configured provider
session maximum, existing session absolute expiry and idle expiry. There is no
refresh-token extension.

This phase does not implement SCIM, backchannel logout or continuous token
introspection. Disabling an account or logging out at the IdP therefore does not
instantly terminate an already issued application session. The bounded expiry
above and explicit application suspension/revocation apply. Fresh provider login
uses `prompt=login` and `max_age=0`, and disabled accounts cannot authenticate.
RP-initiated logout revokes the local app session even if the provider is down;
the browser must complete Keycloak's confirmation page to end its provider SSO
session. Closing that page does not prove provider logout completed.

An installation operator may approve a specific active user/member for at most
60 minutes of local MFA access to a required org:

```sh
contextctl --org-id company identity break-glass --user-id USER_ID \
  --minutes 15 --approval-reference INCIDENT-456
```

The exception is scoped to exact org/user, membership revision, policy version
and expiry, is audited, requires an existing active local MFA factor, and grants
no corpus permissions. There is no automatic exception or MFA removal. Operator
recovery and subsequent grant approval remain separate from this login exception.

## Storage and verification

`organization_oidc` and `organization_break_glass` are tenant-owned operator-only
authority records. `oidc_transactions` is private global identity security state,
always accessed through purpose/browser/session/org checks. `_sso_identity_control`
is installation metadata. Existing `user_identities` retains its unique exact
provider/subject constraint; OIDC providers use the exact issuer string while
local identities retain `local`. Browser evidence never enters org directory DTOs.
These collections are excluded from domain repository access and included only
in installation-operator whole-database backups. A reserved collection name is
recognized at activation only with both its schema marker and migration ledger.
Private registration files and keyrings are mounted installation storage.

Run the focused commands from the Phase 4 request, or the complete disposable
gate with `scripts/test-v6-sso.sh`. The latter runs all unit tests, real Phase 2
isolation, Phase 3 local identity, then real HTTPS Keycloak tests with separate
MongoDB stacks between selectors. `V6_SSO_DEVELOPMENT_ONLY=1` is a development
selector and cannot establish prerequisite gates. Missing provider configuration
produces an integration skip and means the Phase 4 evidence gate is incomplete.
No invitations are sent to real recipients; all mail uses the private test sink.

Phase 4 reference verification passed 2,809 unit tests, 12 real tenant-isolation
tests, 9 real local-identity tests and all 9 real Keycloak tests, with zero skips.
Ruff, strict mypy and the public source audit also passed. These are correctness
results on disposable infrastructure, not production latency or capacity measurements.
