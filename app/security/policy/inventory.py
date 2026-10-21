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
    "request.state.acp_tenant_cursor_secret": (
        "installation cursor-signing metadata from the current maintenance read only; "
        "never authority, accepted from headers, persisted, returned or reused across requests"
    ),
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

QUERY_GATES = {
    "IdentityStore.read_human_session": (
        "exact hashed human session with current expiry and revocation predicates; verified "
        "active same-org membership, exact user/revision before global user/identity joins; "
        "current user auth epoch and org policy version; exact verified login identity/user/"
        "provider; one-row bounds, 3s limit, no disk spill or retained identity rows; current "
        "provider policy and final atomic session update still required; transaction mutations "
        "join on their existing session with exact original context bindings, retaining all "
        "anchor/user/membership/session write fences and the final session-expiry check"
    ),
    "PolicyEngine.read_inputs": (
        "fresh active org anchor; nontransaction reads require current installation control "
        "IDs/schema versions before private joins; org prefilter on organizations/assignments/"
        "teams/team links/"
        "memberships before bounded joins; global users by exact same-org membership IDs; "
        "1001-row probes, 3s limit, no disk spill or retained authority; existing ancestry/"
        "generation/expiry/action-resource evaluation and final fences remain required"
    ),
    "PolicyEngine.snapshot(tx)": (
        "same org and subject predicates through one bounded aggregation on the existing "
        "transaction session; installation controls remain current reads outside that snapshot; "
        "shared anchor CAS still serializes governance mutations and issuance"
    ),
}
