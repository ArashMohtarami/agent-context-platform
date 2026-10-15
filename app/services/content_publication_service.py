"""Phase 8 publication review and explicit approval orchestration."""

from typing import Protocol

from app.content.errors import ContentApprovalBlockedError, ContentApprovalStateError
from app.content.publication import (
    PublicationReview,
    PublicationSafetyPolicy,
    review_content_for_publication,
)
from app.models.content import ContentItemDocument, ContentStatus
from app.security.tenant import TenantContextRequired, require_tenant
from app.security.tenant_policy import policy_allows
from app.security.tenant_service import tenant_service


class ContentPublicationRepository(Protocol):
    """The existing lifecycle service surface needed by approval."""

    async def get_content(self, profile_id: str, content_id: str) -> ContentItemDocument: ...

    async def update_content(
        self,
        profile_id: str,
        content_id: str,
        *,
        expected_revision: int,
        changes: dict[str, object],
    ) -> ContentItemDocument: ...


@tenant_service
class ContentPublicationService:
    """Review current content and make the existing ``review → approved`` step explicit."""

    def __init__(
        self,
        content: ContentPublicationRepository,
        *,
        policy: PublicationSafetyPolicy,
    ) -> None:
        self._content = content
        self._policy = policy

    async def review_content_for_publication(
        self, profile_id: str, content_id: str
    ) -> PublicationReview:
        """Scan one current item without mutating it or retaining matched data."""
        item = await self._content.get_content(profile_id, content_id)
        return self.review_item_for_publication(item)

    def review_item_for_publication(self, item: ContentItemDocument) -> PublicationReview:
        """Review an item already authorized by the API boundary."""
        if item.org_id != require_tenant().org_id or not policy_allows(
            item.model_dump(by_alias=True, mode="python"), "content_items"
        ):
            raise TenantContextRequired("publication review is unavailable in this tenant")
        return review_content_for_publication(item, policy=self._policy)

    async def approve_content(
        self,
        profile_id: str,
        content_id: str,
        *,
        expected_revision: int,
    ) -> tuple[ContentItemDocument, PublicationReview]:
        """Approve a reviewed, current item only after a fresh safety scan."""
        item = await self._content.get_content(profile_id, content_id)
        if item.status is not ContentStatus.REVIEW:
            raise ContentApprovalStateError("content must be in review before it can be approved")
        if item.revision != expected_revision:
            # The lifecycle service performs the authoritative optimistic update;
            # refusing an already stale scan avoids treating it as a fresh review.
            raise ContentApprovalStateError("content item changed after it was read")
        review = self.review_item_for_publication(item)
        if not review.publishable:
            raise ContentApprovalBlockedError(
                "approval is blocked by high-severity publication safety findings"
            )
        approved = await self._content.update_content(
            profile_id,
            content_id,
            expected_revision=expected_revision,
            changes={"status": ContentStatus.APPROVED.value},
        )
        return approved, review
