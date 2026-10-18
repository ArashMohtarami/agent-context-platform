"""Phase8 paths reuse classified storage; no filesystem cache or report payload."""

STORAGE = {
    "agent_activity": "tenant-owned; event-time sponsorship and shared metadata predicate",
    "governance_events": "tenant-owned; audit predicate; control records excluded",
    "governance_outbox": "tenant-owned; transactional report/checkpoint governance delivery only",
    "policy_anchors": "tenant-owned; shared policy fence for every summary/report operation",
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
        "browser+org match; exact action-resource grants; Mongo pre-group predicate"
    ),
    "GET /api/v6/organizations/{org_id}/activity": (
        "same predicate; bounded signed cursor; audit requires audit:read:org"
    ),
    "POST /api/v6/organizations/{org_id}/reports": (
        "CSRF+Origin+recent MFA; reports:export; same visibility; transactional receipt/audit"
    ),
    "GET /api/v6/organizations/{org_id}/reports/{report_id}/download": (
        "recipient/current policy+membership generation; expiry/MFA; same predicate and retention"
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
