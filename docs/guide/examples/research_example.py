"""Create a synthetic source, claim, citation, and pending candidate.

This fixture is an invented tutorial policy, not verified provider guidance.
It writes development data once, uses no external fetch, and never approves
or promotes knowledge. Choose a new research ID before repeating the run.
"""

from api_client import DemoApi

RESEARCH = "guide-retry-evidence"
SOURCE = "guide-policy-source"
CLAIM = "guide-retry-claim"
CITATION = "guide-retry-citation"
STATEMENT = "The tutorial payment project retains retry keys for 24 hours."


def main():
    api = DemoApi()
    api.request(
        "POST",
        "/api/v3/research/projects",
        {
            "research_project_id": RESEARCH,
            "name": "Tutorial retry evidence",
            "question": "What does the synthetic tutorial policy say about retry retention?",
            "type": "technical_research",
            "related_project_ids": ["payment-service"],
        },
    )
    api.request(
        "POST",
        "/api/v3/research/sources",
        {
            "research_project_id": RESEARCH,
            "source_id": SOURCE,
            "source_type": "manual",
            "uri": "manual:tutorial-retry-policy",
            "title": "Synthetic tutorial retry policy",
            "source_class": "user_provided",
        },
    )
    ingested = api.request(
        "POST",
        f"/api/v3/research/sources/{SOURCE}/ingest",
        {
            "content": STATEMENT,
            "content_type": "text/plain",
            "title": "Tutorial policy",
        },
    )
    api.request(
        "POST",
        "/api/v3/research/claims",
        {
            "research_project_id": RESEARCH,
            "claim_id": CLAIM,
            "statement": STATEMENT,
            "status": "unreviewed",
        },
    )
    api.request(
        "POST",
        "/api/v3/research/citations",
        {
            "research_project_id": RESEARCH,
            "citation_id": CITATION,
            "claim_id": CLAIM,
            "source_id": SOURCE,
            "snapshot_id": ingested["snapshot"]["snapshot_id"],
            "locator": {"line_start": 1, "line_end": 1},
            "evidence_excerpt": STATEMENT,
            "relation": "supports",
        },
    )
    claim = api.request("GET", f"/api/v3/research/claims/{CLAIM}?research_project_id={RESEARCH}")
    api.request(
        "PATCH",
        f"/api/v3/research/claims/{CLAIM}",
        {
            "research_project_id": RESEARCH,
            "expected_revision": claim["revision"],
            "supporting_citations": [CITATION],
            "status": "supported",
            "confidence": 0.9,
            "confidence_rationale": "Exact excerpt from the synthetic tutorial fixture only.",
        },
    )
    candidate = api.request(
        "POST",
        "/api/v3/knowledge-candidates",
        {
            "research_project_id": RESEARCH,
            "candidate_id": "guide-retry-candidate",
            "candidate_type": "project_context",
            "target_project_id": "payment-service",
            "title": "Tutorial retry policy (synthetic)",
            "content": STATEMENT,
            "source_claim_ids": [CLAIM],
            "source_citation_ids": [CITATION],
            "confidence": 0.9,
            "confidence_rationale": "Tutorial fixture; a human must review applicability.",
        },
    )
    print("Candidate created:", candidate["candidate_id"], "status:", candidate["status"])
    print("Review evidence before choosing approval or promotion. No V1 knowledge was changed.")


if __name__ == "__main__":
    main()
