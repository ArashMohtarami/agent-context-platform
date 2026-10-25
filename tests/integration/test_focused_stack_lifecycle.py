"""One actual disposable startup/search/error-cleanup check, no installer claim."""

import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

from scripts.focused_stack import OwnedStack

pytestmark = pytest.mark.integration


def test_owned_stack_queries_search_and_cleans_after_error():
    identifier = os.environ.get("ACP_FOCUSED_LIFECYCLE_ID")
    # The existing full release runner also discovers this maintained test. Its
    # explicit disposable fixture can authorize an independent owned rehearsal;
    # this does not set a release flag or launch a nested test runner.
    if not identifier:
        assert urlsplit(os.environ.get("V6_TEST_MONGODB_URI", "")).hostname == "127.0.0.1"
        assert os.environ.get("V6_TEST_MONGODB_CONTAINER", "").startswith("acp-v6-phase2-mongodb-")
        identifier = uuid4().hex + "-lifecycle"
    report = Path(os.environ.get("ACP_FOCUSED_LIFECYCLE_REPORT") or os.environ["V6_REPORT_DIR"])
    stack = OwnedStack(identifier, dict(os.environ), report)
    if os.environ.get("ACP_FOCUSED_LIFECYCLE_OWNER"):
        stack.owner = os.environ["ACP_FOCUSED_LIFECYCLE_OWNER"]
    with pytest.raises(RuntimeError, match="intentional lifecycle failure"):
        with stack:
            # __enter__ proves actual replica identity, READY lexical/vector
            # indexes and successful queries with isolated fictional data.
            assert stack.state.is_dir()
            assert stack.inspect("container", stack.containers[0])["State"]["Running"]
            raise RuntimeError("intentional lifecycle failure")
    assert not stack.state.exists()
    assert all(stack.inspect("container", name) is None for name in stack.containers)
    assert stack.inspect("network", stack.network) is None
