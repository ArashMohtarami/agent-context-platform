"""CSV encoding and export policy contracts, without claiming persisted evidence."""

import csv
from io import StringIO
from unittest.mock import Mock

import pytest

from app.security.policy.engine import PolicyEngine
from app.security.policy.evaluation import PolicyDenied
from app.summaries.models import REPORT_TTL_SECONDS
from app.summaries.reports import Reports, csv_cell
from tests.unit.test_v6_dashboard_read_models import g, query, snapshot


@pytest.mark.parametrize(
    "value",
    [
        '=HYPERLINK("https://invalid.example")',
        "+1",
        "-1",
        "@SUM(A1)",
        " \t=1",
        "\r=1",
        "\n=1",
        "\ufeff\t=1",
        "\u00a0=1",
        "\x00=1",
    ],
)
def test_csv_formula_prefixes_are_neutralized_even_when_quoted_or_whitespace_prefixed(value):
    out = StringIO(newline="")
    csv.writer(out).writerow([csv_cell(value), "safe,comma"])
    row = next(csv.reader(StringIO(out.getvalue())))
    assert row[0] == "'" + value and row[1] == "safe,comma"


def test_export_requires_export_permission_and_the_same_activity_visibility():
    assert REPORT_TTL_SECONDS == 300  # Phase0 ready/download lifetime contract
    engine = PolicyEngine(Mock(), human_management=True, managed_agents=True)
    reports = Reports(engine)
    export = g("reports:export", "organization", "org-a")
    activity = g("activity:read:org", "organization", "org-a")
    for grants in ((), (export,), (activity,)):
        with pytest.raises(PolicyDenied):
            reports.authorize(snapshot(*grants, role="org_owner"), query(scope="org"))
    assert reports.authorize(snapshot(export, activity, role="org_owner"), query(scope="org"))[
        "$and"
    ][0] == {"org_id": "org-a"}


def test_empty_and_ordinary_csv_cells_are_preserved():
    assert csv_cell(None) == "" and csv_cell("normal-value") == "normal-value"
