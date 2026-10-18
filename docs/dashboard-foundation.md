# V6 dashboard frontend foundation

Phase 9 adds a React console at `/console/` and two bounded, session-only read
contracts: `GET /api/v6/me/console` and `GET /api/v6/me/security`. It does not add
organization administration, data dashboards, credentials management or operator
screens. The real authorized organization/activity APIs remain available for
later frontend phases. The console starts disabled (`V6_CONSOLE_ENABLED=false`).
Enable it only after the earlier tenant, identity, policy and managed-machine
cutovers have passed and the managed schema-42 control is activated. Enabling the
flag does not perform or bypass those cutovers. Schema head stays 42.

## Same-origin deployment

Build `dashboard/dist` using the pinned Node version and `npm ci`. Deploy that
static directory with its `licenses.txt` beside the existing API, behind one
operator-configured HTTPS origin. The backend Docker production image remains
API-only. Frontend source, lockfile, tests and licenses are included in public
source releases; `node_modules`, private fixtures, browser traces and generated
builds are excluded. This is a separate static deployment, not a Git submodule.
Do not run the development server as a production identity boundary.

Set `V6_BROWSER_ORIGIN` to that exact HTTPS origin and `V6_CONSOLE_ENABLED=true`
on the API (with the previously approved V6 flags and mounted identity secrets).
Keep `V6_OIDC_REGISTRATIONS_FILE` on the backend only. Configure each approved
provider's exact existing callback `/api/v6/auth/oidc/callback` and logout return
`/api/v6/auth/oidc/logged-out` on this origin. For browser HTML requests only,
successful callbacks now redirect to fixed local console paths; failures redirect
to a neutral fixed error page. JSON clients retain their prior responses. OAuth
codes/state never pass to SPA state, storage or frontend URLs.

A TLS reverse proxy should serve `/console/assets/` from the build, use
`index.html` only as the fallback for `/console/` routes, and proxy `/api/` to the
existing API. Preserve the original HTTPS scheme using the installation's
explicitly trusted proxy configuration. Use this response policy for console HTML
and assets (the API keeps its stricter JSON policy):

```text
Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: no-referrer
Cross-Origin-Resource-Policy: same-origin
Cache-Control: no-store
```

Inline styles are limited to the style policy for Radix focus/scroll primitives;
scripts remain local external files without `unsafe-inline` or `unsafe-eval`.
Do not broaden `connect-src` to the provider: the server exchanges authorization
codes. Do not cache identity HTML or `/api/v6` responses at the reverse proxy or
register a service worker. Use host-only Secure/HttpOnly `__Host-` session cookies.
No API key, IdP token or browser credential belongs in frontend configuration.

## Context and personal security

The bootstrap returns only the signed-in person's verified active memberships,
organization display names/auth policies, current action-resource tuples and
safe own security metadata. It never returns emails, issuer subjects, credential
hashes, MFA seed ciphertext or recovery-code hashes. Counts of remaining recovery
codes and verified issuer labels are deliberate own-security fields. Each store
read is bounded to 101 rows, with up to 100 memberships/identities displayed.
This foundation is intended for people with at most 100 organization memberships;
expand the own-membership pagination contract before operating above that bound.
No global directory is exposed by this contract.

All mutations use the current in-memory CSRF token, same-origin credentials and
server policy context. Error recovery refreshes context but never replays a
mutation. Session cookies cannot authenticate V1–V5/MCP and bearer tokens cannot
authenticate these browser endpoints. Local MFA/recovery, invitation verification,
password reset/change, identity linking and own human-session revocation call the
existing real session APIs. SSO-required organization entry always rechecks its
approved provider and assurance. The UI never offers a local fallback for it.
An enrolled local factor may step up a current verified SSO session for a
personal security action; it does not replace that session’s approved provider
or its organization entry policy. Provider downtime has no automatic fallback. IdP revocation behavior remains the
bounded contract documented in [SSO identity](sso-identity.md).

Organization switches first cancel requests and clear React Query data, component
state, selections, recovery codes and sensitive forms. A dirty form offers Keep
editing or Discard and switch. The new session/CSRF and policy context replace
the old ones; every query key includes organization, organization-policy version,
policy-anchor revision, membership revision, session-context ID and local context
generation. A late response is rejected even if its transport ignores abort.
The server still authorizes every request: route guards are only presentation.
Old organization URLs redirect to the current verified context without mounting
old private content. BFCache restoration hides private DOM before revalidation.
Unavailable session verification hides private views and requires reconnection.
Cross-tab session changes broadcast only a content-free invalidation message.
Nothing private is persisted in browser local/session storage or query caches.

## Acceptance and budgets

The three focused component files exercise the real typed client with mocked
transport responses; they do not establish cookie, OIDC or persisted replay
security. The three focused Playwright files use the real HTTPS API and Keycloak.
`tests/integration/test_v6_dashboard_browser.py` is the corresponding Python
integration fixture/selector; without its explicit disposable infrastructure it
skips and does not establish the gate. The npm browser command sets up that
infrastructure automatically. No real invitations are sent.

The initial JavaScript budget is **300 KiB gzip across all emitted chunks**;
`npm run build` measures/enforces it. Targets for a representative cold navigation
are LCP ≤2.5 s, CLS ≤0.1 and INP ≤200 ms at 4× CPU slowdown, 10 Mbps/100 ms RTT.
These are acceptance budgets, not benchmark results. Provider latency is outside
that client-navigation budget. Test at 360/768/1440 CSS pixels, both themes,
keyboard-only focus, reduced motion and forced colors. The 200% equivalent specimen uses a real browser with a 720 CSS-pixel
viewport and device-pixel ratio 2 (1440 physical pixels). This checks reflow at
the corresponding browser-zoom geometry; browser-toolbar gestures and OS scaling
are not exercised. Screen-reader interoperability is not claimed. Automated axe checks
supplement screenshot and focus review; they do not replace manual accessibility
review. Controls target 44 px, visible 2 px focus, WCAG AA 4.5:1 ordinary text and
3:1 non-text contrast. Status always includes text.

The source/build uses the existing four-block SVG mark and local system fonts.
The specimen surface explicitly labels all sample data. Unknown/empty/error and
loading states remain distinct; no unfinished module displays pretend live data.

The local browser verification environment used Node 24.22.0, Playwright 1.64.1
and installed Google Chrome 147.0.7727.55 with real MongoDB 8.3.9 and HTTPS
Keycloak 26.8.1. The Playwright Chromium download returned an HTTP 403 location
restriction, so that bundled browser was unavailable. Results establish the
installed Chrome profile; Firefox, WebKit and the unavailable bundled version
are not covered. Fixture certificates are disposable self-signed test CAs, not
production certificates. No production deployment or real mail delivery is part
of this verification.
