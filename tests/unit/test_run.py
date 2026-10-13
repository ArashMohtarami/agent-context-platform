"""Production server entry-point tests."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from app.core.config import Settings
from app.run import main, options


def build_settings(tmp_path: Path, **overrides: object) -> Settings:
    """Return valid settings with the database configuration filled in."""
    password_file = tmp_path / "mongodb-password"
    password_file.write_text("test-only-password", encoding="utf-8")
    return Settings(
        _env_file=None,
        mongodb_uri="mongodb://mongodb:27017",
        mongodb_database="agent_context_test",
        mongodb_username="application_test_user",
        mongodb_password_file=password_file,
        **overrides,  # type: ignore[arg-type]
    )


def test_main_configures_logging_before_starting_uvicorn() -> None:
    settings = MagicMock(log_level="WARNING")

    with (
        patch("app.run.get_settings", return_value=settings),
        patch("app.run.configure_logging") as configure_logging,
        patch("app.run.uvicorn.run") as run,
        patch("app.run.options", return_value={"port": 8000}) as build,
    ):
        main()

    configure_logging.assert_called_once_with("WARNING")
    build.assert_called_once_with(settings)
    run.assert_called_once_with("app.main:app", port=8000)


def test_the_server_binds_the_container_port_and_logs_through_the_application(
    tmp_path: Path,
) -> None:
    chosen = options(build_settings(tmp_path, log_level="WARNING"))

    assert chosen["host"] == "0.0.0.0"  # noqa: S104 - constrained by Compose networking.
    assert chosen["port"] == 8000
    assert chosen["log_config"] is None
    assert chosen["log_level"] == "warning"
    # The request middleware emits the one structured access event per request.
    assert chosen["access_log"] is False


def test_a_shutdown_is_bounded_so_a_deploy_cannot_hang_on_one_request(
    tmp_path: Path,
) -> None:
    chosen = options(build_settings(tmp_path, api_shutdown_timeout_seconds=7))

    assert chosen["timeout_graceful_shutdown"] == 7


def test_requests_in_flight_are_bounded(tmp_path: Path) -> None:
    # Retrieval holds an embedding model and a database connection, so
    # accepting without limit turns a burst into an out-of-memory kill rather
    # than a queue.
    chosen = options(build_settings(tmp_path, api_max_concurrent_requests=30))

    assert chosen["limit_concurrency"] == 30


def test_an_idle_connection_is_not_held_forever(tmp_path: Path) -> None:
    chosen = options(build_settings(tmp_path, api_keep_alive_seconds=11))

    assert chosen["timeout_keep_alive"] == 11


def test_the_server_does_not_name_itself_in_every_response(tmp_path: Path) -> None:
    # This API answers programs. Telling a scanner which server and version it
    # is talking to helps the scanner and nobody else.
    assert options(build_settings(tmp_path))["server_header"] is False


def test_forwarded_headers_are_trusted_only_when_the_settings_allowlist_them(
    tmp_path: Path,
) -> None:
    chosen = options(
        build_settings(
            tmp_path,
            api_proxy_headers=True,
            api_forwarded_allow_ips="10.42.0.0/24",
        )
    )

    assert chosen["proxy_headers"] is True
    assert chosen["forwarded_allow_ips"] == "10.42.0.0/24"


def test_the_defaults_are_the_ones_a_deployment_gets(tmp_path: Path) -> None:
    chosen = options(build_settings(tmp_path))

    assert chosen["timeout_graceful_shutdown"] == 20
    assert chosen["limit_concurrency"] == 50
    assert chosen["timeout_keep_alive"] == 5
