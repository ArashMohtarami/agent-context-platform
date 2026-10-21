"""Summary paths reuse classified storage; no filesystem cache or report payload."""

TRANSIENT_STORAGE = {
    "OverviewCache.process_state": "installation metadata; private engine/source weak registry, "
    "lock, global generation, encoded-byte counter and monotonic observation clock; "
    "never persisted or exposed; no user authority retained",
    "OverviewCache.entries": "tenant-owned aggregates; exact tenant/subject/action-resource "
    "predicate/policy/retention/query key; 128 entries/4MiB/30s per engine/source; "
    "Mongo change stream drained before reuse, fail closed on source observation failure; "
    "current policy and natural expiry rechecked before every response",
    "ConcurrentReads.pending": "tenant-owned in-flight aggregates; same exact key plus source "
    "generation; 32 operations/100 waiters; cancellation-safe; no authority or secret cache",
    "OverviewCache.organizations": "tenant-owned invalidation counters; at most 512 orgs per "
    "engine/source; never serialized; unknown-owner updates clear conservatively",
    "OverviewCache.stream.buffer": "tenant-owned source org_id metadata only; "
    "internal invalidation observer; projection removes actor/content/change bodies; never exposed",
    "OverviewCache.stream.resume_token": "installation metadata; private observation cursor; "
    "never persisted or exposed; database client owns cursor lifetime",
}

STORAGE = {
    "agent_activity": "tenant-owned; event-time sponsorship and shared metadata predicate",
    "governance_events": "tenant-owned; audit predicate; control records excluded",
    "governance_outbox": "tenant-owned; transactional report/checkpoint governance delivery only",
    "policy_anchors": (
        "tenant-owned; final majority CAS for reads; transactional governance and retention fence"
    ),
    "organization_memberships": "tenant-owned; current subject and immutable event-time capture",
    "team_memberships": "tenant-owned; event-time capture only; no historical read join",
    "_managed_agents_control": "installation metadata; verified schema42 activation prerequisite",
}
ROUTES = {
    "GET /api/v6/organizations/{org_id}/audit-diff": (
        "current browser/org; audit:read:org; same time/retention predicate and policy fence; "
        "declared authority fields only"
    ),
    "GET /api/v6/organizations/{org_id}/overview": (
        "browser+org match; identity-only NONE principal; fresh transactional service "
        "action-resource snapshots before/after query; Mongo pre-group predicate"
    ),
    "GET /api/v6/organizations/{org_id}/activity": (
        "same predicate; bounded signed cursor; audit requires audit:read:org"
    ),
    "POST /api/v6/organizations/{org_id}/reports": (
        "CSRF+Origin+recent MFA; reports:export; same visibility; transactional receipt/audit"
    ),
    "GET /api/v6/organizations/{org_id}/reports/{report_id}/download": (
        "recipient/current policy+membership generation; expiry/MFA; same predicate and retention; "
        "fresh reports:export check after rendering and final CAS; synchronous expiry check "
        "after the final await, separate from activity rights"
    ),
}
EXISTING_READS = {
    "GET /api/v6/me/effective-access": "verified self; existing Phase6 safe explanations",
    "GET /api/v6/organizations/{org_id}/members/{user_id}/effective-access": (
        "Directory current delegated boundary; no foreign ancestry"
    ),
}
EXCLUDED_SOURCES = {
    "retrieval_logs": "contains query text; never summarized or exported",
    "audit_logs": "best effort route/security metadata; not the declared governance stream",
    "identity_audit_events": "private global security history; no tenant read/export",
    "metrics": "process-local observations; no historical series",
}
