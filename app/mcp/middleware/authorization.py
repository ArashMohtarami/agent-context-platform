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
        self._action: str | None = None

    def require_scope(self, scope: Scope) -> None:
        self._action = scope
        if not self._context.allows_scope(scope):
            raise GatewayError(
                GatewayErrorCode.FORBIDDEN, f"this client is not authorized for {scope}"
            )
        try:
            self._configured.require_scope(scope)
        except AuthorizationError as error:
            raise GatewayError(GatewayErrorCode.FORBIDDEN, str(error)) from error

    def require_project(self, project_id: str | None) -> None:
        if self._context.action_resources is not None and project_id is not None:
            self.require_resource(
                "project_directory"
                if self._action and self._action.startswith("projects:")
                else "project",
                project_id,
            )
        elif not self._context.allows_project(project_id):
            raise GatewayError(
                GatewayErrorCode.PROJECT_FORBIDDEN,
                "this client is not authorized for that project",
            )
        try:
            self._configured.require_project(project_id)
        except AuthorizationError as error:
            raise GatewayError(GatewayErrorCode.PROJECT_FORBIDDEN, str(error)) from error

    def require_profile(self, profile_id: str | None) -> None:
        if self._context.action_resources is not None and profile_id is not None:
            self.require_resource("creator_profile", profile_id)
        elif (
            profile_id is not None
            and self._context.allowed_profile_ids is not None
            and profile_id not in self._context.allowed_profile_ids
        ):
            raise GatewayError(
                GatewayErrorCode.FORBIDDEN, "this client is not authorized for that profile"
            )
        try:
            self._configured.require_profile(profile_id)
        except AuthorizationError as error:
            raise GatewayError(GatewayErrorCode.FORBIDDEN, str(error)) from error

    def require_resource(self, kind: str, identifier: str) -> None:
        if self._context.action_resources is not None and not any(
            g.action == self._action
            and g.resource_kind == kind
            and g.selection().contains(identifier)
            for g in self._context.action_resources
        ):
            raise GatewayError(
                GatewayErrorCode.FORBIDDEN, "operation is outside the current action/resource grant"
            )

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
        configured_profiles = self._configured.profiles
        credential_profiles = self._context.allowed_profile_ids
        profiles = (
            credential_profiles
            if configured_profiles is None
            else configured_profiles
            if credential_profiles is None
            else tuple(p for p in configured_profiles if p in credential_profiles)
        )
        result: dict[str, object] = {
            "org_id": self._context.org_id,
            "scopes": scopes,
            "projects": projects,
            "profiles": ("all" if profiles is None else list(profiles)),
        }
        if self._context.action_resources is not None:
            result["projects"] = []
            result["profiles"] = []
            result["action_resources"] = [
                g.model_dump() for g in self._context.action_resources if g.action in scopes
            ]
        return result
