"""Every Phase 6 authority record is tenant owned; none is a public corpus source."""

COLLECTION_OWNERSHIP = {
    "teams": "tenant owned; explicit teams:read/manage boundary",
    "team_memberships": "tenant owned; named team and member management boundary",
    "management_invitations": "tenant owned; issuer lineage, verified recipient, one-use challenge",
    "access_requests": "tenant owned; requester or explicitly authorized approver",
    "access_decisions": "tenant owned; immutable exact-request decision, governance transaction",
    "_human_management_control": "installation metadata; stopped-writer schema fence",
}
