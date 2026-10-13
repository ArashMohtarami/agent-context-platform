# Security policy

## Reporting a vulnerability

Use the public repository hosting service's private vulnerability-reporting
feature when it is enabled. Include the affected commit/version, a minimal
reproduction, impact, and a proposed fix if available. Redact real credentials,
private knowledge, database dumps, and client identities.

If private reporting is unavailable, open a minimal issue asking a maintainer
for a private reporting channel. Leave exploit details and sensitive material
out of that public issue. A security email address has not been designated in
this source distribution.

There is no published long-term support schedule. Reports should identify the
exact version or commit; fixes are developed against the current maintained
branch and any backport is announced with the release.

## Credentials exposed in a commit

Revoke or rotate the credential promptly, including separate agent credentials
and database credentials affected by the exposure. Removing a file from the
latest tree does not remove it from Git history or invalidate existing copies.
Review the affected history and any distributed copies when handling the
exposure.

## Deployment boundaries

The default Compose stack binds the API to loopback and keeps database/search
ports private. Enable authentication, issue separate least-privilege client
credentials, and use HTTPS for remote MCP/API access. A public introduction
website can be served separately from the authenticated platform.

Review [security](docs/security.md), [deployment](docs/deployment.md), and
[backup recovery](docs/backup.md) for operator controls. Retrieved documents and
research sources remain untrusted data. Publication scans and source labels
assist review; they do not replace authorization or a human disclosure decision.
