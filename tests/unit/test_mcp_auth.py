"""The authorization hook: what a grant covers, and what it refuses."""

from pathlib import Path

import pytest

from app.core.config import Settings
from app.mcp.auth import (
    ALL_SCOPES,
    Authorization,
    AuthorizationError,
    build_authorization,
    parse_scopes,
)

PROJECT = "sampleco-payment"
OTHER_PROJECT = "sampleco-wallet"
PROFILE = "personal-brand"
OTHER_PROFILE = "team-brand"


def settings_for(tmp_path: Path, **overrides: object) -> Settings:
    """Build settings that differ only in their MCP grant."""
    password_file = tmp_path / "mongodb-password"
    password_file.write_text("test-only-password", encoding="utf-8")
    values: dict[str, object] = {
        "_env_file": None,
        "mongodb_uri": "mongodb://mongodb:27017",
        "mongodb_database": "agent_context_test",
        "mongodb_username": "application_test_user",
        "mongodb_password_file": password_file,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_an_unconfigured_grant_covers_everything_local() -> None:
    grant = build_authorization()

    assert grant.scopes == ALL_SCOPES
    assert grant.projects is None
    assert grant.allows_project(PROJECT)
    assert grant.profiles is None
    assert grant.allows_profile(PROFILE)


def test_a_grant_can_be_narrowed_to_some_scopes() -> None:
    grant = build_authorization(["search:read", "skills:read"])

    assert grant.allows_scope("search:read")
    assert not grant.allows_scope("memory:write")


def test_a_grant_can_be_narrowed_to_some_projects() -> None:
    grant = build_authorization(projects=[PROJECT])

    assert grant.allows_project(PROJECT)
    assert not grant.allows_project(OTHER_PROJECT)


def test_naming_no_project_is_always_allowed() -> None:
    # Omitting a project addresses global knowledge, which belongs to everyone.
    # It is never read as "any project": that would infer an identity.
    grant = build_authorization(projects=[PROJECT])

    assert grant.allows_project(None)


def test_a_grant_can_be_narrowed_to_some_creator_profiles() -> None:
    grant = build_authorization(profiles=[PROFILE])

    assert grant.allows_profile(PROFILE)
    assert not grant.allows_profile(OTHER_PROFILE)
    with pytest.raises(AuthorizationError):
        grant.require_profile(OTHER_PROFILE)


def test_a_missing_scope_is_refused_by_name() -> None:
    grant = build_authorization(["search:read"])

    with pytest.raises(AuthorizationError, match="memory:write"):
        grant.require_scope("memory:write")


def test_a_refused_project_is_not_named_back_to_the_caller() -> None:
    grant = build_authorization(projects=[PROJECT])

    with pytest.raises(AuthorizationError) as failure:
        grant.require_project(OTHER_PROJECT)

    assert OTHER_PROJECT not in str(failure.value)


@pytest.mark.parametrize("scopes", [["search:write"], ["nonsense"], ["Search:Read"], [""]])
def test_an_unknown_scope_is_rejected_rather_than_ignored(scopes: list[str]) -> None:
    # Dropping an unrecognized name would quietly widen a grant meant to narrow.
    with pytest.raises(ValueError, match="unknown scope"):
        parse_scopes(scopes)


def test_a_repeated_scope_is_rejected() -> None:
    with pytest.raises(ValueError, match="unique"):
        parse_scopes(["search:read", "search:read"])


def test_a_grant_of_nothing_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one scope"):
        Authorization(scopes=())


def test_an_empty_project_allow_list_is_refused() -> None:
    # Nobody intends "no project at all"; it is a configuration mistake.
    with pytest.raises(ValueError, match="at least one project"):
        Authorization(projects=())


def test_a_grant_describes_itself_without_naming_a_credential() -> None:
    described = build_authorization(["search:read"], [PROJECT]).describe()

    assert described == {"scopes": ["search:read"], "projects": [PROJECT], "profiles": "all"}


def test_settings_build_the_grant_the_deployment_configured(tmp_path: Path) -> None:
    settings = settings_for(
        tmp_path,
        mcp_scopes="search:read, skills:read",
        mcp_project_ids=f"{PROJECT},{OTHER_PROJECT}",
        mcp_profile_ids=PROFILE,
    )

    grant = settings.mcp_authorization()

    assert grant.scopes == ("search:read", "skills:read")
    assert grant.projects == (PROJECT, OTHER_PROJECT)
    assert grant.profiles == (PROFILE,)


def test_an_empty_setting_reads_as_unconfigured(tmp_path: Path) -> None:
    settings = settings_for(tmp_path, mcp_scopes="  ,  ", mcp_project_ids="", mcp_profile_ids="")

    grant = settings.mcp_authorization()

    assert grant.scopes == ALL_SCOPES
    assert grant.projects is None
    assert grant.profiles is None


def test_a_misconfigured_grant_fails_while_settings_load(tmp_path: Path) -> None:
    # Refused at startup rather than on the first call it would have blocked.
    with pytest.raises(ValueError, match="unknown scope"):
        settings_for(tmp_path, mcp_scopes="everything")


def test_the_api_token_is_read_but_never_rendered(tmp_path: Path) -> None:
    token_file = tmp_path / "mcp-token"
    token_file.write_text("secret-token-value\n", encoding="utf-8")
    settings = settings_for(tmp_path, mcp_api_token_file=token_file)

    assert settings.mcp_api_token_value() == "secret-token-value"
    assert "secret-token-value" not in repr(settings)
    assert "secret-token-value" not in str(settings.model_dump())


def test_a_deployment_may_configure_no_token_at_all(tmp_path: Path) -> None:
    assert settings_for(tmp_path).mcp_api_token_value() is None


def test_an_optional_gateway_session_identifier_is_validated(tmp_path: Path) -> None:
    settings = settings_for(tmp_path, mcp_session_id="codex-session-1")

    assert settings.mcp_session_id == "codex-session-1"
    with pytest.raises(ValueError):
        settings_for(tmp_path, mcp_session_id="not a safe identifier")


def test_an_empty_optional_token_file_is_unconfigured(tmp_path: Path) -> None:
    settings = settings_for(tmp_path, mcp_api_token_file="")

    assert settings.mcp_api_token_file is None
    assert settings.mcp_api_token_value() is None


def test_two_token_sources_are_refused(tmp_path: Path) -> None:
    token_file = tmp_path / "mcp-token"
    token_file.write_text("secret", encoding="utf-8")

    with pytest.raises(ValueError, match="at most one"):
        settings_for(tmp_path, mcp_api_token="secret", mcp_api_token_file=token_file)


def test_an_empty_token_is_refused(tmp_path: Path) -> None:
    token_file = tmp_path / "mcp-token"
    token_file.write_text("\n", encoding="utf-8")
    settings = settings_for(tmp_path, mcp_api_token_file=token_file)

    with pytest.raises(ValueError, match="must not be empty"):
        settings.mcp_api_token_value()


@pytest.mark.parametrize(
    "url",
    [
        "mongodb://127.0.0.1:27017",
        "http://user:pass@api.invalid",
        "not-a-url",
        "http://api.invalid?token=secret",
        "file:///etc/passwd",
    ],
)
def test_an_unusable_api_address_is_refused(tmp_path: Path, url: str) -> None:
    with pytest.raises(ValueError):
        settings_for(tmp_path, mcp_api_base_url=url)
