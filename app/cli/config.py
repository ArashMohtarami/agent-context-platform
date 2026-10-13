"""Configuration for the commands that talk to the Context API.

`contextctl` has two kinds of command. The ones an operator alone can run —
importing skills, rebuilding indexes, backing up — need the platform's own
settings, because they reach MongoDB directly. The rest ask the API, and those
need only the address and, where a deployment requires one, a token.

This is the second half. The first half is `Settings`, unchanged.
"""

from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_API_BASE_URL = "http://127.0.0.1:8000"


class CliSettings(BaseSettings):
    """Where `contextctl` finds the platform, from `CONTEXTCTL_*` variables.

    Separate from the MCP server's settings on purpose: an operator's terminal
    and an agent's tool server are different callers, and pointing one at a
    different deployment should not move the other.
    """

    model_config = SettingsConfigDict(
        env_prefix="CONTEXTCTL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    api_base_url: str = DEFAULT_API_BASE_URL
    api_token: SecretStr | None = None
    api_token_file: Path | None = None
    request_timeout_seconds: float = Field(default=30.0, gt=0.0, le=600.0)

    @field_validator("api_base_url")
    @classmethod
    def validate_api_base_url(cls, value: str) -> str:
        """Require a plain HTTP(S) origin without embedded credentials."""
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("CONTEXTCTL_API_BASE_URL must be an http or https URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("CONTEXTCTL_API_BASE_URL must not contain credentials")
        return value

    @model_validator(mode="after")
    def validate_token_source(self) -> "CliSettings":
        """Require at most one token source."""
        if self.api_token is not None and self.api_token_file is not None:
            raise ValueError(
                "configure at most one of CONTEXTCTL_API_TOKEN or CONTEXTCTL_API_TOKEN_FILE"
            )
        return self

    def api_token_value(self) -> str | None:
        """Read the token without ever rendering it.

        A file is the better source: a token in an environment variable is
        visible to every process that can read `/proc`, and ends up in shell
        history and CI logs.
        """
        if self.api_token is not None:
            value = self.api_token.get_secret_value()
        elif self.api_token_file is not None:
            try:
                value = self.api_token_file.read_text(encoding="utf-8").rstrip("\r\n")
            except OSError as error:
                raise ValueError("CONTEXTCTL_API_TOKEN_FILE cannot be read") from error
        else:
            return None
        if not value:
            raise ValueError("the API token must not be empty")
        return value
