# Local human identity and browser security

Phase 3 adds an optional invitation-only local identity API. Optional
[Phase 4 SSO and organization switching](sso-identity.md) build on these sessions.
The dashboard, team management and effective permission engine remain unavailable.
A browser session has no corpus access in these phases.
Existing V1–V5 APIs and MCP continue to require organization-bound bearer keys.

Complete the [real Phase 2 isolation gate](tenant-isolation.md) and its explicit
operator activation first. Migration **38** refuses a sealed, mixed-worker or
unclassified installation. It adds strict private collections without rewriting
authored records, ObjectIds, historical actors or machine credentials. Older
runtime configurations refuse an installation with migration 38 applied.

## Installation boundary

Enable `V6_LOCAL_IDENTITY=true` alongside `V6_TENANT_FOUNDATION=true`,
`V6_TENANT_ISOLATION=true` and `API_AUTH_ENABLED=true`. Configure:

| Setting | Contract |
| --- | --- |
| `V6_BROWSER_ORIGIN` | One exact HTTPS origin, such as `https://context.example.test`; no trailing slash, credentials, path or wildcard |
| `V6_MFA_KEYRING_FILE` | Read-only installation secret mount containing the versioned AES-256-GCM keyring |
| `V6_MAIL_SINK_DIRECTORY` | Explicit writable private directory for local test/manual delivery; mode 0700, message files 0600 |

The keyring JSON shape is `{"active":"key-1","keys":{"key-1":"BASE64URL_32_RANDOM_BYTES"}}`.
Generate key material with an operating-system CSPRNG, outside public source.
For example, create a new file without printing or overwriting a key:

```bash
umask 077
mkdir -p secrets
python - <<'PY'
import base64, json, secrets
from pathlib import Path
with Path("secrets/local-mfa-keyring.json").open("x") as output:
    json.dump({"active": "key-1", "keys": {
        "key-1": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    }}, output)
PY
```

Mount the file read-only and grant only the runtime user access. Retain old key
versions while any factor uses them; changing the active key affects new factors.
Services load the keyring at startup; apply keyring changes through a controlled
operator restart while retaining every key still needed by stored factors.
Removing a required old key fails authentication closed. Encryption binds each
seed to its user and factor ID, so swapping ciphertext between records fails.
Back up keys separately from the database under installation operator controls.
Loss of every applicable key requires documented operator recovery; password
reset cannot decrypt, remove or bypass MFA.

The default deployment remains disabled. Supply an explicit Compose override
with these environment values, a read-only keyring mount, and a private writable
mail-sink mount when enabling this phase. A TLS reverse proxy must forward only
through the existing trusted proxy configuration. The identity boundary checks
the effective request scheme and accepts HTTPS only. No HTTP development cookie
fallback is enabled.

The mail port is `MailDelivery`; the implemented adapter is `LocalMailSink`.
It writes only to the operator-selected private directory and has no outbound
transport. Messages contain recipient, purpose and challenge token; they are
secrets, never logs or public fixtures. Consume/delete them under the local
operator's retention policy. A production mail provider adapter is separate work.

## Enrollment and owner bootstrap

There is no anonymous registration or first-login owner claim. An operator with
private host/database access chooses the organization and approval reference:

```bash
contextctl --org-id ORG identity invite --email alice@example.test \
  --name Alice --approval-reference APPROVAL_REFERENCE

contextctl --org-id ORG identity provision-owner --email owner@example.test \
  --name Owner --approval-reference APPROVAL_REFERENCE \
  --root-delegation-file /private/reviewed-root-tuples.json \
  --approve-project-bootstrap
```

The root file is a bounded, nonempty JSON array of explicit tuples, for example
`{"action":"projects:read","resource_kind":"project","selector":"include","ids":["project-a"],"conditions":{}}`.
The operator reviews the complete delegation ceiling; no wildcard action or
implicit corpus bypass is accepted. Owner provisioning separately records the
approved `projects:read` and `projects:write` **directory** bootstrap policy and
the root ceiling in a `pending_phase5` intent. No permission assignment or
effective grant record is created. Phase 5 must activate these approved records
through its engine; further self/corpus grants need independent approval or
documented operator recovery. The initial-owner unique index prevents concurrent
bootstrap claims. No web route can call this operator path.

An invitation names an exact local identity and an organization membership.
Possession permits only that transition. Enrollment sets a local password and
sends a separate browser-bound verification challenge to the same recipient.
Verification activates only the invited membership. For an existing local
identity, accepting another invitation also requires its current password and
active MFA; it cannot replace that identity's credentials. Equal email/domain
does not create authority, merge identities or activate another membership.
Local login into an `sso_required` organization is refused.

## Browser API contracts

Start with `GET /api/v6/auth/csrf`. It creates a short-lived pre-authentication
cookie or returns the current session's CSRF token. Keep that token only in
memory and send it as `X-CSRF-Token`; every POST also needs the exact configured
`Origin`. Public authentication POSTs require a valid pre-authentication cookie.
The login challenge is bound to that browser and is not a data session.
After expiry or revocation, this endpoint clears invalid HttpOnly cookies and
issues fresh pre-authentication state so re-login needs no manual cookie removal.

| Endpoint under `/api/v6` | Boundary and result |
| --- | --- |
| `POST /auth/invitations/accept` | Invitation token and password; existing accounts also prove MFA; sends verification through the configured sink |
| `POST /auth/local/verify` | Purpose-bound, browser-bound verification token; activates only the pending invited membership |
| `POST /auth/local/login` | Local identity, password and requested org; the selector is checked against verified active membership; returns a five-minute login challenge |
| `POST /auth/local/complete` | Browser-bound login challenge plus TOTP/recovery when configured; issues a new opaque session |
| `POST /auth/password-reset/request` | Generic 202 for known/unknown eligible local identities, with a minimum response duration and shared throttles |
| `POST /auth/password-reset/consume` | Reset token and new password; atomically consumes token and revokes local sessions; does not log in, disable MFA or reinstate membership |
| `GET /me` | Verified own user, minimal active membership, assurance and session ID; no credential/factor/provider data or effective corpus access |
| `GET /me/sessions` | Bounded own-session metadata; no cookie/token hashes |
| `POST /me/sessions/{session_id}/revoke` | Own public session ID, current session, CSRF/Origin and recent MFA |
| `POST /auth/session/rotate` | Revokes the old token, rotates token and CSRF; preserves the original absolute deadline |
| `POST /auth/logout` | Revokes the current session and clears the cookie; remains possible after the MFA step-up window |
| `POST /auth/mfa/enroll`, `/auth/mfa/confirm` | Recent password proof; encrypted pending factor expires after ten minutes; confirmation consumes a TOTP step, rotates the session and returns recovery codes once |
| `POST /auth/step-up` | Consumes a fresh TOTP/recovery code and rotates session/CSRF; ten-minute privileged window |
| `POST /auth/mfa/recovery` | Recent MFA plus a fresh TOTP; replaces recovery hashes, revokes other sessions and returns new codes once |
| `POST /auth/mfa/remove` | Recent MFA; disables the factor, clears recovery hashes and invalidates all local sessions |
| `POST /auth/password/change` | Recent MFA and current password; changes Argon2id hash and invalidates all local sessions |

Password-only sessions permit self-security setup and identification; they never
create a corpus principal. Both session cookies use `Secure`, `HttpOnly`,
`SameSite=Lax`, host-only scope and `Path=/`. The authenticated cookie is
`__Host-acp-session`; pre-authentication uses `__Host-acp-login` and lasts fifteen
minutes. Human sessions have a thirty-minute idle deadline and eight-hour
absolute deadline, checked on every request before touching idle time. TTL is
cleanup only. User epoch, membership revision/status and organization login policy
are checked live. Revocation/password/MFA changes serialize on the user fence;
membership-sensitive transitions also touch the member fence. Sensitive state,
challenge consumption and identity audit commit together in replica-set
transactions. A failed audit aborts its security transition.

Browser cookies are rejected by V1–V5 and HTTP MCP, even when a bearer is also
supplied. `/api/v6` rejects bearer headers. Stdio MCP has no browser-cookie
authentication path. No token belongs in browser local/session storage, URLs,
frontend environment variables or logs. Validation errors on identity endpoints
never echo submitted passwords, factors or challenge values. Verified human
actors carry user ID, org, local auth method and assurance; historical actor
shapes remain readable and no machine key/client ID is fabricated.

## Private data inventory and enforcement

| Collection | Ownership and gate |
| --- | --- |
| `users`, `user_identities`, `local_credentials` | Private global human identity; exact login/challenge lookup or verified self-security transaction only |
| `mfa_factors` | Private global human identity; own verified session or bound login transaction; encrypted seed, replay counter, hashed recovery codes |
| `auth_challenges` | Private global identity/security; fixed purpose, user, org/member, browser where applicable, epoch, expiry and atomic unused predicate |
| `browser_sessions` | Private global identity/security; opaque-token hash lookup, live authority checks; self-only bounded session listings |
| `auth_throttles` | Private global security metadata; hashed account/network bucket, fixed minute and atomic bounded admission |
| `identity_audit_events` | Private global security history; internal transactional writer, installation operator only; no directory/audit export API |
| `organization_memberships` | Tenant-owned governance; explicit operator org or verified own membership ID plus org/user/revision; minimal directory DTO |
| `_local_identity_control` | Installation metadata; guarded schema/version marker, no public write path |
| MFA keyring, local mail sink | Installation-mounted private storage; neither is a public download, ingestion/search source or tenant export |

`app/identity/repository.py` is the narrow allowlisted raw-driver boundary. The
identity package does not search/join/count/export corpus data, invoke jobs or
create effective policy. Existing whole-database backups include private identity
and remain installation-operator-only. Restore must reconcile session epochs,
membership/policy revisions and key availability before serving traffic; an old
backup must not silently resurrect access.

## Libraries and verification

Passwords use [argon2-cffi](https://argon2-cffi.readthedocs.io/en/stable/api.html)
25.1.0 with explicit Argon2id memory 65,536 KiB, time 3, parallelism 4, salt 16
bytes and output 32 bytes. A maximum of four native hash jobs is admitted, with
no waiting queue; cancellation retains its slot until native work finishes.
Passwords are bounded to 1,024 UTF-8 bytes and are never reversibly stored.
Unknown users perform dummy Argon2id verification under the same limits.

[PyOTP](https://pyauth.github.io/pyotp/) 2.10.0 supplies six-digit, thirty-second
TOTP with a one-step clock window. The accepted time step is atomically monotonic;
random recovery codes use purpose/user-bound SHA-256 hashes and atomic removal.
[cryptography](https://cryptography.io/en/latest/hazmat/primitives/aead/) 50.0.2
supplies AES-256-GCM, with fresh 96-bit nonces and user/factor associated data.
Direct and transitive versions are pinned in `requirements.lock`. Their upstream
license metadata is MIT (argon2-cffi, bindings and PyOTP), Apache-2.0 OR BSD-3-Clause
(cryptography), MIT-0 (cffi) and BSD-3-Clause (pycparser); retain installed wheel
license notices. These licenses do not change the platform's MIT license.

Network requests are limited to 120 per minute per direct peer; login/reset
requests to ten per minute per normalized account; login challenge attempts to
five per minute; session security mutations to ten per minute per user. Buckets
are persisted and shared by API workers; forwarded client headers are not used
as authority. Set deployment proxy/network limits for distributed abuse too.
At most twenty live sessions per user epoch are admitted under the user fence.

```bash
python -m pytest -q tests/unit/test_v6_local_auth.py \
  tests/unit/test_v6_browser_sessions.py tests/unit/test_v6_mfa.py
scripts/test-v6-identity.sh
python -m scripts.benchmark_local_auth --samples 20
```

The disposable runner retains the complete Phase 2 MongoDB/mongot gate and adds
`tests/integration/test_v6_local_identity.py`. Real persisted challenge/session
replay, transaction rollback, enrollment, verification, MFA/recovery, reset,
expiry, CSRF and revocation use synthetic identities, factors and a private test
mail sink. Logical clock advancement exercises expiry before TTL cleanup; the
password benchmark uses the real host clock. The benchmark records hardware,
policy, latency and process peak RSS without printing hashes or test secrets.
Its warm synthetic measurements are not production throughput results. Private
evidence, key material, message files and Graphify outputs stay out of releases.
