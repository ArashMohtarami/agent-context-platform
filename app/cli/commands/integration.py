"""Non-mutating setup guidance for supported client integrations."""

import argparse

from app.cli.commands import SubParsers
from app.cli.config import CliSettings
from app.cli.output import Result
from app.integrations import DEFAULT_INTEGRATION_REGISTRY
from app.integrations.claude_code import (
    MCP_URL_ENV,
    TOKEN_ENV,
    project_configuration,
    project_configuration_json,
    user_scope_command,
)
from app.integrations.gemini_cli import (
    MCP_URL_ENV as GEMINI_MCP_URL_ENV,
)
from app.integrations.gemini_cli import (
    TOKEN_ENV as GEMINI_TOKEN_ENV,
)
from app.integrations.gemini_cli import (
    mcp_add_command,
    project_settings_configuration,
    settings_configuration,
    settings_configuration_json,
    settings_path,
    user_settings_configuration,
)


async def list_integrations(_: argparse.Namespace, __: CliSettings) -> Result:
    """List client-neutral integration metadata without touching credentials."""
    records = [item.as_dict() for item in DEFAULT_INTEGRATION_REGISTRY.list()]
    return Result(
        data={"integrations": records},
        lines=[
            "  ".join(("ID", "CLIENT TYPE", "TRANSPORT", "DOCUMENTATION")),
            "  ".join(("--", "-----------", "---------", "-------------")),
            *[
                "  ".join(
                    (
                        str(item["integration_id"]),
                        str(item["client_type"]),
                        str(item["transport"]),
                        str(item["documentation_path"]),
                    )
                )
                for item in records
            ],
        ],
    )


async def show_integration(args: argparse.Namespace, _: CliSettings) -> Result:
    """Render token-free onboarding material for one supported client."""
    integration = DEFAULT_INTEGRATION_REGISTRY.get(str(args.integration_id))
    instructions = integration.instructions()
    data = instructions.as_dict()
    return Result(
        data=data,
        lines=[
            f"{instructions.metadata.display_name} ({instructions.metadata.transport})",
            f"Documentation: {instructions.metadata.documentation_path}",
            "Required environment variables:",
            *[f"  {name}" for name in instructions.environment_variables],
            "This command prints only token-free configuration references.",
        ],
    )


async def validate_integration(args: argparse.Namespace, _: CliSettings) -> Result:
    """Run configuration-only diagnostics; never make a model or MCP call."""
    integration = DEFAULT_INTEGRATION_REGISTRY.get(str(args.integration_id))
    validation = integration.validate()
    data = {
        "integration": integration.metadata.as_dict(),
        **validation.as_dict(),
    }
    return Result(
        data=data,
        lines=[
            f"{integration.metadata.display_name} local configuration preflight:",
            *[f"  {check.name}: {check.status} — {check.detail}" for check in validation.checks],
            "No network, MCP endpoint, or external model call was made.",
        ],
    )


async def claude_code_setup(_: argparse.Namespace, __: CliSettings) -> Result:
    """Print safe Claude Code setup material without changing any client state."""
    return Result(
        data={
            "environment_variables": [MCP_URL_ENV, TOKEN_ENV],
            "endpoint": f"${{{MCP_URL_ENV}}}",
            "user_scope_command": user_scope_command(),
            "project_mcp_json": project_configuration(),
        },
        lines=[
            "Set these in the Claude Code process environment:",
            f"  {MCP_URL_ENV}=https://agents.example.com/mcp",
            f"  {TOKEN_ENV}=<dedicated Agent Context credential>",
            "Recommended user-scope command (prints only; not executed):",
            f"  {user_scope_command()}",
            "Project .mcp.json (contains environment references, never a token):",
            project_configuration_json(),
            "This helper does not modify Claude configuration or shell profiles.",
        ],
    )


async def gemini_cli_setup(args: argparse.Namespace, __: CliSettings) -> Result:
    """Print safe Gemini CLI setup material without changing client state."""
    scope = str(args.scope)
    read_only = bool(args.read_only)
    command = mcp_add_command(scope=scope, read_only=read_only)
    settings = settings_configuration(read_only=read_only)
    rendered_settings = settings_configuration_json(read_only=read_only)
    location = settings_path(scope)
    data = {
        "environment_variables": [GEMINI_MCP_URL_ENV, GEMINI_TOKEN_ENV],
        "endpoint": f"${{{GEMINI_MCP_URL_ENV}}}",
        "scope": scope,
        "settings_path": location,
        "read_only": read_only,
        "settings": settings,
        # Retain the Phase 1 fields for programmatic callers while exposing the
        # selected scope explicitly above.
        "user_settings_json": user_settings_configuration(read_only=read_only),
        "project_settings_json": project_settings_configuration(read_only=read_only),
        "recommended_gemini_mcp_add_command": command,
        "verification_commands": ["gemini mcp list", "/mcp list"],
    }
    if args.show_json:
        return Result(data=data, lines=[rendered_settings])
    return Result(
        data=data,
        lines=[
            "Set these in the Gemini CLI process environment:",
            f"  {GEMINI_MCP_URL_ENV}=https://agents.example.com/mcp",
            f"  {GEMINI_TOKEN_ENV}=<dedicated Agent Context credential>",
            f"Current {scope}-scope command (prints only; not executed):",
            f"  {command}",
            f"Place this fragment in {location}:",
            rendered_settings,
            (
                "Tool filters are client-side discovery controls; gateway scopes, "
                "project grants, and approvals remain authoritative."
            ),
            "Verify with `gemini mcp list`, then `/mcp list` in Gemini CLI.",
            "This helper does not edit Gemini configuration, shell profiles, or trust settings.",
        ],
    )


def add_arguments(groups: SubParsers) -> None:
    """Add safe generic discovery plus existing client-specific setup helpers."""
    parser = groups.add_parser("integration", help="print supported client integration setup")
    integrations = parser.add_subparsers(dest="integration", required=True)
    listing = integrations.add_parser("list", help="list supported MCP client integrations")
    listing.set_defaults(handler=list_integrations, needs_platform=True)
    showing = integrations.add_parser("show", help="show token-free setup for one integration")
    showing.add_argument("integration_id", help="for example: codex, claude-code, gemini-cli")
    showing.set_defaults(handler=show_integration, needs_platform=True)
    validating = integrations.add_parser(
        "validate", help="validate local integration environment without connecting"
    )
    validating.add_argument("integration_id", help="for example: gemini-cli")
    validating.set_defaults(handler=validate_integration, needs_platform=True)
    claude_code = integrations.add_parser("claude-code", help="Claude Code remote MCP guidance")
    commands = claude_code.add_subparsers(dest="integration_command", required=True)
    setup = commands.add_parser("setup", help="print non-mutating setup guidance")
    setup.set_defaults(handler=claude_code_setup, needs_platform=True)
    gemini_cli = integrations.add_parser("gemini-cli", help="Gemini CLI remote MCP guidance")
    commands = gemini_cli.add_subparsers(dest="integration_command", required=True)
    setup = commands.add_parser("setup", help="print non-mutating setup guidance")
    setup.add_argument(
        "--scope",
        choices=("user", "project"),
        default="user",
        help="render the user or repository settings location (default: user)",
    )
    setup.add_argument(
        "--read-only",
        action="store_true",
        help="limit Gemini discovery to the gateway's registered read-only tools",
    )
    setup.add_argument(
        "--show-json",
        action="store_true",
        help="print only the token-free settings JSON in non-JSON output mode",
    )
    setup.set_defaults(handler=gemini_cli_setup, needs_platform=True)
