"""Safe domain errors for research-project boundaries."""


class ResearchProjectAlreadyExistsError(Exception):
    """The stable research-project identifier is already in use."""


class ResearchProjectNotFoundError(Exception):
    """A requested research project does not exist."""


class RelatedProjectNotFoundError(Exception):
    """A requested V1 project link does not refer to an existing project."""


class ResearchSourceNotFoundError(Exception):
    """A requested registered source does not exist."""


class ResearchSourceAlreadyExistsError(Exception):
    """The requested source is already present by identifier or duplicate evidence metadata."""


class SourceSnapshotAlreadyExistsError(Exception):
    """An immutable snapshot identifier or content hash is already registered."""


class ResearchNoteAlreadyExistsError(Exception):
    """The requested note identifier already exists in its research project."""


class ResearchNoteNotFoundError(Exception):
    """A requested research note does not exist in the declared project."""


class ResearchClaimAlreadyExistsError(Exception):
    """The requested claim identifier already exists in its research project."""


class ResearchClaimNotFoundError(Exception):
    """A requested research claim does not exist in the declared project."""


class ResearchSourceReferenceNotFoundError(Exception):
    """A note source reference is absent, cross-project, or not authorized."""


class ResearchBriefAlreadyExistsError(Exception):
    """The requested research brief identifier already exists in its project."""


class ResearchBriefNotFoundError(Exception):
    """A requested research brief is absent, cross-project, or not authorized."""


class ResearchCitationAlreadyExistsError(Exception):
    """The requested citation identifier is already used by its claim."""


class ResearchCitationProvenanceError(Exception):
    """A source, snapshot, chunk, locator, or excerpt cannot support this citation."""


class ResearchCitationNotFoundError(Exception):
    """A claim citation is absent, cross-project, or not authorized."""


class KnowledgeCandidateAlreadyExistsError(Exception):
    """A candidate identifier is already present in its research project."""


class KnowledgeCandidateNotFoundError(Exception):
    """A candidate is absent, cross-project, or outside the caller's historical scope."""


class SkillUpdateProposalAlreadyExistsError(Exception):
    """A generated proposal identifier is already used in this research project."""


class SkillUpdateProposalNotFoundError(Exception):
    """A skill proposal is absent, cross-project, or outside retained authorization."""


class SkillUpdateProposalSkillNotFoundError(Exception):
    """The named canonical V1 skill has no active version."""


class SkillUpdateProposalVersionMismatchError(Exception):
    """The requested base version is not the current active canonical skill version."""


class SkillUpdateProposalProvenanceError(Exception):
    """Submitted claim or citation evidence is absent, cross-project, or unauthorized."""


class CandidateReviewStateError(Exception):
    """A candidate cannot make the requested review transition."""


class CandidateProvenanceError(Exception):
    """A candidate's requested claim or citation evidence is absent or unauthorized."""


class CandidateTargetProjectNotFoundError(Exception):
    """A requested V1 target project does not exist."""


class CandidatePromotionStateError(Exception):
    """A candidate is not approved, was promoted already, or cannot be superseded."""


class CandidatePromotionTargetError(Exception):
    """The approved candidate has no supported Phase 10 V1 destination."""


class RevalidationNotNeededError(Exception):
    """No observable review deadline or retained-source change needs a proposal."""
