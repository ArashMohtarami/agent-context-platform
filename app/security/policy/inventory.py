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
