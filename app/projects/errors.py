"""Safe domain errors for projects and project context."""


class ProjectError(RuntimeError):
    """Base error for the project and context lifecycle."""


class ProjectNotFoundError(ProjectError):
    """Raised when a requested project does not exist.

    Also raised when context is written for an unknown project, because context
    that belongs to no project would be unreachable and unattributable.
    """


class ProjectAlreadyExistsError(ProjectError):
    """Raised when a project identifier is already taken."""


class ContextRetrievalUnavailableError(ProjectError):
    """Raised when context retrieval is requested but not configured."""


class ContextNotFoundError(ProjectError):
    """Raised when a context item is absent from the requested project.

    A context item that exists in another project is reported the same way: one
    project must not learn what another project stores, including whether an
    identifier exists at all.
    """
