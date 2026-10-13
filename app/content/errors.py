"""Safe domain failures for V2 Phase 3 content storage."""


class ContentError(RuntimeError):
    """Base class for content-storage failures that are safe to expose."""


class ContentIdeaNotFoundError(ContentError):
    """The idea is not present inside the requested creator profile."""


class ContentIdeaAlreadyExistsError(ContentError):
    """The profile already owns an idea with this public identifier."""


class ContentBriefNotFoundError(ContentError):
    """The brief is not present inside the requested creator profile."""


class ContentBriefAlreadyExistsError(ContentError):
    """The profile already owns a brief with this public identifier."""


class ContentItemNotFoundError(ContentError):
    """The content item is not present inside the requested creator profile."""


class ContentItemAlreadyExistsError(ContentError):
    """The profile already owns a content item with this public identifier."""


class ContentVersionNotFoundError(ContentError):
    """The requested historical version is not present for this item."""


class ContentLinkAlreadyExistsError(ContentError):
    """The profile already owns an identical provenance link."""


class ContentCampaignAlreadyExistsError(ContentError):
    """The profile already owns a campaign with this public identifier."""


class ContentMetricAlreadyExistsError(ContentError):
    """The profile already owns a metric observation with this public identifier."""


class InvalidContentLifecycleTransitionError(ContentError):
    """A requested lifecycle state change is not permitted."""


class ContentVersionConflictError(ContentError):
    """The requested next version is no longer current."""


class ContentRetrievalUnavailableError(ContentError):
    """The requested content vector retrieval cannot currently be served."""


class ContentApprovalBlockedError(ContentError):
    """Configured high-severity publication findings prevent approval."""


class ContentApprovalStateError(ContentError):
    """Approval was requested outside the explicit review lifecycle step."""


class ContentPublishingStateError(ContentError):
    """Publishing was requested outside the explicit approved lifecycle step."""


class ContentAlreadyPublishedError(ContentError):
    """The current content representation has already been published."""


class PublisherUnavailableError(ContentError):
    """The requested optional publication provider is not configured."""


class PublisherDeliveryError(ContentError):
    """A configured provider failed without exposing provider response data."""
