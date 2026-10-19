"""Governance storage ownership; no corpus/public directory access."""

COLLECTION_OWNERSHIP = {
    "policy_anchors": "tenant owned; shared authority serialization anchor",
    "policy_assignments": "tenant owned; governance only",
    "policy_credentials": "tenant owned; explicit operator reviewed adoption adapters",
    "governance_events": "tenant owned; transactional content-free governance audit",
    "governance_outbox": "tenant owned; transactional governance delivery state",
    "ownership_transfers": "tenant owned; distinct verified owner acceptance",
    "review_authorship": "tenant owned; verified human authorship; unknown sponsors denied",
    "_management_policy_control": "installation metadata",
}


TRANSIENT_STATE = {
    "evaluation._SnapshotGrants": (
        "tenant-owned authority inputs; one synchronous evaluation only; decoded rows/hashes "
        "are never reused across requests or snapshots; ancestry, expiry, membership and org "
        "checks still run; no storage, secret material or persistent cache"
    ),
}
READ_CONFIRMATION = {
    "PolicyEngine.confirm_read": (
        "current org/subject visibility is re-evaluated by Summaries.confirm_current; "
        "bounded atomic majority write on the unchanged active org anchor linearizes delivery; "
        "governance retains transactional fencing and retention narrowing advances revision"
    ),
}
