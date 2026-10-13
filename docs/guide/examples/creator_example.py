"""Store a synthetic creator idea, brief, and draft, then move it into review.

Run once with creator:write, content:write and content:read. It never approves,
exports, publishes, or calls an external provider. IDs must be new.
"""

from api_client import DemoApi

PROFILE = "guide-creator"
IDEA = "guide-idempotency-idea"
BRIEF = "guide-idempotency-brief"
CONTENT = "guide-idempotency-post"


def main():
    api = DemoApi()
    api.request(
        "POST",
        "/api/v2/creator-profiles",
        {
            "profile_id": PROFILE,
            "name": "Tutorial creator",
            "audiences": ["backend engineers"],
            "domains": ["software engineering"],
            "writing_preferences": ["Explain one concrete mechanism with a small example."],
            "language_preferences": ["English"],
        },
    )
    api.request(
        "POST",
        "/api/v2/content-pillars",
        {
            "profile_id": PROFILE,
            "pillar_id": "guide-engineering",
            "name": "Practical engineering",
            "description": "Synthetic tutorial examples.",
            "priority": 0.8,
        },
    )
    api.request(
        "POST",
        "/api/v2/content-ideas",
        {
            "profile_id": PROFILE,
            "idea_id": IDEA,
            "title": "One retry, one result",
            "description": "Explain the role of an idempotency key with an invented example.",
            "platforms": ["linkedin"],
            "origin": {"type": "manual"},
            "priority": 0.7,
        },
    )
    api.request(
        "POST",
        "/api/v2/content-briefs",
        {
            "profile_id": PROFILE,
            "brief_id": BRIEF,
            "idea_id": IDEA,
            "platform": "linkedin",
            "objective": "Explain the synthetic example without claiming provider behavior.",
            "primary_message": "A stable request key can identify a repeated operation.",
            "audience": ["backend engineers"],
            "supporting_points": ["Label the example as hypothetical."],
        },
    )
    item = api.request(
        "POST",
        "/api/v2/content",
        {
            "profile_id": PROFILE,
            "content_id": CONTENT,
            "brief_id": BRIEF,
            "platform": "linkedin",
            "content_type": "post",
            "title": "One retry, one result",
            "body": (
                "Imagine a tutorial service receiving the same request twice. "
                "A stable idempotency key lets it recognize the retry "
                "and return the stored result. "
                "This is a hypothetical example; each real provider defines its own retention "
                "and replay rules."
            ),
            "topic_tags": ["idempotency"],
            "pillar_id": "guide-engineering",
        },
    )
    item = api.request(
        "PATCH",
        f"/api/v2/content/{CONTENT}",
        {
            "profile_id": PROFILE,
            "expected_revision": item["revision"],
            "status": "review",
        },
    )
    review = api.request(
        "POST", f"/api/v2/content/{CONTENT}/publication-review", {"profile_id": PROFILE}
    )
    print("Draft saved, version:", item["current_version"], "status:", item["status"])
    print(
        "Publication scan publishable:", review["publishable"], "findings:", len(review["findings"])
    )
    print("A human reviews the prose and scan before the separate approval step.")


if __name__ == "__main__":
    main()
