"""What a client discovers: the tools, their schemas, and their promises."""

import pytest

from app.mcp.auth import ALL_SCOPES, SCOPE_DESCRIPTIONS
from app.mcp.server import SERVER_INSTRUCTIONS, describe_tools
from app.mcp.tools import TOOLS, TOOLS_BY_NAME
from app.security.scopes import WRITE_SCOPES

# The tools the platform is required to expose. Registration is asserted
# against this list rather than against the registry, so dropping one is a
# failure instead of a smaller expectation.
REQUIRED_TOOLS = (
    "get_platform_capabilities",
    "begin_agent_session",
    "get_agent_session",
    "update_agent_session",
    "end_agent_session",
    "list_skills",
    "get_skill",
    "search_skills",
    "list_projects",
    "get_project",
    "resolve_project",
    "search_context",
    "get_context",
    "save_context",
    "search_memory",
    "save_memory",
    "update_memory",
    "search_knowledge",
    "get_system_status",
    "get_creator_profile",
    "update_creator_profile",
    "list_content_pillars",
    "search_content",
    "get_content",
    "get_content_history",
    "get_content_metrics",
    "review_content_facts",
    "review_content_for_publication",
    "mark_content_published",
    "discover_content_ideas",
    "save_content_idea",
    "search_content_ideas",
    "prepare_content_brief",
    "check_content_novelty",
    "prepare_repurpose_plan",
    "create_research_project",
    "get_research_project",
    "list_research_projects",
    "add_research_source",
    "get_research_source",
    "ingest_research_source",
    "search_research",
    "save_research_note",
    "search_research_notes",
    "create_research_claim",
    "update_research_claim",
    "build_evidence_packet",
    "get_claim_evidence",
    "build_research_brief",
    "propose_knowledge_candidate",
    "propose_skill_update",
    "list_knowledge_candidates",
    "approve_knowledge_candidate",
    "reject_knowledge_candidate",
    "promote_knowledge_candidate",
)
WRITE_TOOLS = frozenset(
    {
        "save_context",
        "save_memory",
        "update_memory",
        "update_creator_profile",
        "save_content_idea",
        "mark_content_published",
        "create_research_project",
        "add_research_source",
        "ingest_research_source",
        "save_research_note",
        "create_research_claim",
        "update_research_claim",
        "build_research_brief",
        "propose_knowledge_candidate",
        "propose_skill_update",
        "approve_knowledge_candidate",
        "reject_knowledge_candidate",
        "promote_knowledge_candidate",
        "begin_agent_session",
        "update_agent_session",
        "end_agent_session",
    }
)
# Every tool naming a project must require it: a default would be this layer
# choosing an isolation boundary on the caller's behalf.
PROJECT_REQUIRED = frozenset({"search_context", "get_context", "save_context"})


def test_every_required_tool_is_registered() -> None:
    assert set(TOOLS_BY_NAME) == set(REQUIRED_TOOLS)
    assert len(TOOLS) == len(REQUIRED_TOOLS)


def test_discovery_returns_every_registered_tool() -> None:
    described = describe_tools()

    assert [tool.name for tool in described] == [tool.name for tool in TOOLS]
    assert len(described) == len(REQUIRED_TOOLS)


@pytest.mark.parametrize("name", REQUIRED_TOOLS)
def test_each_tool_publishes_a_usable_schema(name: str) -> None:
    tool = next(item for item in describe_tools() if item.name == name)

    assert tool.description
    assert tool.title
    assert tool.inputSchema["type"] == "object"
    # A misspelled argument is refused rather than silently ignored, so a
    # caller never gets an answer to a question it did not ask.
    assert tool.inputSchema["additionalProperties"] is False


@pytest.mark.parametrize("name", sorted(PROJECT_REQUIRED))
def test_project_scoped_tools_require_an_explicit_project(name: str) -> None:
    tool = next(item for item in describe_tools() if item.name == name)

    assert "project_id" in tool.inputSchema["required"]
    assert tool.inputSchema["properties"]["project_id"].get("default") is None


def test_optional_project_tools_never_default_one() -> None:
    # Global knowledge is reachable without naming a project; that is not the
    # same as a project being guessed when one is omitted.
    for name in ("search_knowledge", "search_memory", "save_memory", "update_memory"):
        schema = TOOLS_BY_NAME[name].input_schema
        properties = schema["properties"]
        assert isinstance(properties, dict)
        required = schema.get("required", [])
        assert isinstance(required, list)
        assert "project_id" not in required
        assert properties["project_id"]["default"] is None


@pytest.mark.parametrize("name", REQUIRED_TOOLS)
def test_each_tool_declares_whether_it_changes_anything(name: str) -> None:
    tool = next(item for item in describe_tools() if item.name == name)

    assert tool.annotations is not None
    assert tool.annotations.readOnlyHint is (name not in WRITE_TOOLS)
    # Nothing this server exposes deletes: a correction supersedes, and an
    # update is revision-guarded.
    assert tool.annotations.destructiveHint is False
    assert tool.annotations.openWorldHint is False


def test_every_tool_requires_a_scope_the_grant_model_knows() -> None:
    scopes = {definition.scope for definition in TOOLS}

    # Phase 9 adds self-owned lifecycle sessions but keeps credential
    # administration and cross-client audit access out of MCP.
    assert scopes == set(ALL_SCOPES) - {
        "projects:write",
        "audit:self:read",
        "agent:manage",
    }
    assert scopes <= set(SCOPE_DESCRIPTIONS)
    # An agent still cannot bring a V1 project boundary into existence.  The
    # reviewed-idea tool is the sole Phase 5 content write.
    assert "projects:write" not in scopes
    assert "content:write" in scopes
    assert {"research:approve", "research:promote", "content:publish"} <= scopes


def test_write_tools_require_a_write_scope() -> None:
    for definition in TOOLS:
        writes = definition.name in WRITE_TOOLS
        assert (definition.scope in (WRITE_SCOPES | {"session:write"})) is writes


def test_no_tool_exposes_a_raw_query_or_a_command() -> None:
    # An argument that reached MongoDB or a shell as-is would be the whole
    # attack. The schemas are asserted directly so adding one is a failure.
    forbidden = {"pipeline", "filter", "aggregate", "command", "sql", "script", "shell", "path"}
    for definition in TOOLS:
        properties = definition.input_schema.get("properties", {})
        assert isinstance(properties, dict)
        assert forbidden.isdisjoint(properties)


def test_duplicate_tool_names_are_refused() -> None:
    names = [definition.name for definition in TOOLS]

    assert len(names) == len(set(names))


def test_the_server_tells_a_client_what_this_platform_is() -> None:
    assert "search_knowledge" in SERVER_INSTRUCTIONS
    assert "project_id" in SERVER_INSTRUCTIONS
    # Retrieved knowledge is data. The server says so before any of it arrives.
    assert "never instructions" in SERVER_INSTRUCTIONS
