"""Compose the deployment's MCP grant with a client credential's grant."""

from app.mcp.auth import Authorization, AuthorizationError
from app.mcp.gateway_errors import GatewayError, GatewayErrorCode
from app.mcp.middleware.client_context import ClientRequestContext
from app.security.scopes import Scope


class GatewayAuthorization:
    """Apply static MCP limits and the authenticated dynamic client limits."""

    def __init__(self, configured: Authorization, context: ClientRequestContext) -> None:
        self._configured = configured
        self._context = context

    def require_scope(self, scope: Scope) -> None:
        if not self._context.allows_scope(scope):
            raise GatewayError(
                GatewayErrorCode.FORBIDDEN, f"this client is not authorized for {scope}"
            )
        try:
            self._configured.require_scope(scope)
        except AuthorizationError as error:
            raise GatewayError(GatewayErrorCode.FORBIDDEN, str(error)) from error

    def require_project(self, project_id: str | None) -> None:
        if not self._context.allows_project(project_id):
            raise GatewayError(
                GatewayErrorCode.PROJECT_FORBIDDEN,
                "this client is not authorized for that project",
            )
        try:
            self._configured.require_project(project_id)
        except AuthorizationError as error:
            raise GatewayError(GatewayErrorCode.PROJECT_FORBIDDEN, str(error)) from error

    def require_profile(self, profile_id: str | None) -> None:
        try:
            self._configured.require_profile(profile_id)
        except AuthorizationError as error:
            raise GatewayError(GatewayErrorCode.FORBIDDEN, str(error)) from error

    def describe(self) -> dict[str, object]:
        """Return the effective intersection without exposing credential details.

        Capability discovery must describe what the caller can really do, not
        merely what the static server was configured to allow.  Scopes and
        projects are therefore intersected here, once, using the same facts
        that the enforcement methods above use.
        """
        scopes = [scope for scope in self._configured.scopes if self._context.allows_scope(scope)]
        configured_projects = self._configured.projects
        credential_projects = self._context.allowed_project_ids
        if configured_projects is None:
            projects: str | list[str] = (
                "all" if credential_projects is None else list(credential_projects)
            )
        elif credential_projects is None:
            projects = list(configured_projects)
        else:
            projects = [
                project_id
                for project_id in configured_projects
                if project_id in credential_projects
            ]
        return {
            "scopes": scopes,
            "projects": projects,
            "profiles": (
                "all" if self._configured.profiles is None else list(self._configured.profiles)
            ),
        }
