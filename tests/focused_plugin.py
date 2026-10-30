"""Exact collection/outcome evidence for the focused runner; no fixture activation."""

import json
from pathlib import Path

import pytest

from app.core.config import Settings, get_settings

# Applied before test modules are imported. Never consult deployment dotenv.
Settings.model_config["env_file"] = None
get_settings.cache_clear()


def pytest_addoption(parser):
    parser.addoption("--focused-report", required=True)
    parser.addoption("--focused-expected")


def pytest_configure(config):
    config._focused = {"nodes": [], "events": [], "collection_errors": 0}


def pytest_collection_finish(session):
    data = session.config._focused
    data["nodes"] = [item.nodeid for item in session.items]
    expected = session.config.getoption("--focused-expected")
    if expected and data["nodes"] != json.loads(Path(expected).read_text()):
        raise pytest.UsageError("focused planned-versus-collected mismatch; no tests executed")


def pytest_collectreport(report):
    if report.failed:
        # Collection failure is also captured by exit code; no sensitive failure body.
        _collection_errors.append(report.nodeid)


_collection_errors = []


def pytest_runtest_logreport(report):
    _events.append(
        {
            "nodeid": report.nodeid,
            "when": report.when,
            "outcome": report.outcome,
            "xfail": hasattr(report, "wasxfail"),
            "duration": report.duration,
        }
    )


_events = []


def pytest_sessionfinish(session, exitstatus):
    data = session.config._focused
    data.update(
        events=_events, collection_errors=len(_collection_errors), exitstatus=int(exitstatus)
    )
    path = Path(session.config.getoption("--focused-report"))
    temporary = path.with_suffix(".pending")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)
