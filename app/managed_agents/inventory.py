"""Authority state is tenant owned; no machine collection is a shared corpus."""

COLLECTION_OWNERSHIP = {
    "service_accounts": "tenant owned; verified custodian/team and independent reviewer approval",
    "managed_clients": "tenant owned; verified sponsor, parent generation and copied policy",
    "managed_credentials": "tenant owned; secret-free delivery receipt and hashed key reference",
    "_managed_agents_control": "installation metadata; schema42 cutover and key reconciliation",
}
