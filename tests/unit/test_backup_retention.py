"""Which backups survive, and which are let go.

The policy is pure, so this is the one part of backup and recovery that can be
examined exhaustively without a database, a filesystem, or an hour of waiting.
Everything asserted here is a rule an operator is relying on months later.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.backup.naming import archive_name, taken_at
from app.backup.retention import (
    DEFAULT_DAILY,
    DEFAULT_MONTHLY,
    DEFAULT_WEEKLY,
    RetentionPolicy,
    select,
)

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)


def at(*, days: int = 0, hours: int = 0, weeks: int = 0) -> str:
    """Return the name of a backup taken that long before now."""
    return archive_name(NOW - timedelta(days=days, hours=hours, weeks=weeks))


def decide(names: list[str], **policy: int) -> tuple[set[str], set[str]]:
    """Apply a policy and return what it keeps and removes."""
    decision = select(names, policy=RetentionPolicy(**policy), now=NOW)
    return set(decision.keep), set(decision.remove)


def test_the_documented_default_is_seven_four_three() -> None:
    policy = RetentionPolicy()

    assert (policy.daily, policy.weekly, policy.monthly) == (7, 4, 3)
    assert (DEFAULT_DAILY, DEFAULT_WEEKLY, DEFAULT_MONTHLY) == (7, 4, 3)


def test_one_backup_a_day_keeps_a_week_of_them() -> None:
    names = [at(days=day) for day in range(14)]

    keep, remove = decide(names, daily=7, weekly=0, monthly=0)

    assert keep == {at(days=day) for day in range(7)}
    assert remove == {at(days=day) for day in range(7, 14)}


def test_several_backups_in_one_day_cost_one_daily_slot_not_several() -> None:
    # The rule is "seven days", not "seven files". A busy afternoon must not
    # evict a week of history.
    today = [at(hours=hour) for hour in range(6)]
    older = [at(days=day) for day in range(1, 7)]

    keep, _ = decide([*today, *older], daily=7, weekly=0, monthly=0)

    # Only the newest of today survives the daily tier, and every earlier day
    # still has its own.
    assert at(hours=0) in keep
    assert all(name not in keep for name in today[1:])
    assert set(older) <= keep


def test_a_gap_in_the_history_does_not_consume_a_slot() -> None:
    # Periods are counted from the backups that exist, not from the calendar,
    # so a week of downtime does not silently expire everything that survived.
    names = [at(days=0), at(days=40), at(days=80), at(days=120)]

    keep, remove = decide(names, daily=3, weekly=0, monthly=0)

    assert keep == {at(days=0), at(days=40), at(days=80)}
    assert remove == {at(days=120)}


def test_the_tiers_overlap_rather_than_add_up() -> None:
    # The newest backup is the newest of its day, its week, and its month, so
    # it is kept once. The totals are a ceiling, not a quota.
    names = [at(days=day) for day in range(120)]

    keep, _ = decide(names, daily=7, weekly=4, monthly=3)

    assert len(keep) <= DEFAULT_DAILY + DEFAULT_WEEKLY + DEFAULT_MONTHLY
    assert at(days=0) in keep


def test_the_monthly_tier_reaches_back_further_than_the_others() -> None:
    # Three monthly means three calendar months that have a backup, not ninety
    # days: from mid-September it reaches the last backup of July. What matters
    # is that it reaches past where the daily and weekly tiers stop.
    names = [at(days=day) for day in range(120)]

    with_monthly, _ = decide(names, daily=7, weekly=4, monthly=3)
    without_monthly, _ = decide(names, daily=7, weekly=4, monthly=0)

    assert min(with_monthly) < min(without_monthly)
    assert {taken_at(name).month for name in with_monthly} == {9, 8, 7}


def test_each_tier_can_be_switched_off_independently() -> None:
    names = [at(days=day) for day in range(90)]

    daily_only, _ = decide(names, daily=3, weekly=0, monthly=0)
    weekly_only, _ = decide(names, daily=0, weekly=2, monthly=0)
    monthly_only, _ = decide(names, daily=0, weekly=0, monthly=2)

    assert len(daily_only) == 3
    assert len(weekly_only) == 2
    assert len(monthly_only) == 2


def test_nothing_is_removed_when_everything_still_fits() -> None:
    names = [at(days=day) for day in range(3)]

    keep, remove = decide(names, daily=7, weekly=4, monthly=3)

    assert keep == set(names)
    assert remove == set()


def test_an_empty_directory_decides_nothing() -> None:
    keep, remove = decide([])

    assert (keep, remove) == (set(), set())


def test_a_backup_from_the_future_is_never_deleted() -> None:
    # A clock that went backwards, or a file copied from another host. It is
    # never safe to delete something on the strength of a timestamp saying the
    # present has not happened yet.
    ahead = archive_name(NOW + timedelta(days=2))
    names = [ahead, *(at(days=day) for day in range(30))]

    keep, remove = decide(names, daily=1, weekly=0, monthly=0)

    assert ahead in keep
    assert ahead not in remove


def test_a_kept_backup_says_which_tier_saved_it() -> None:
    # `prune --dry-run` has to explain itself: an operator about to delete a
    # backup deserves to see why the others stayed.
    names = [at(days=day) for day in range(60)]

    decision = select(names, policy=RetentionPolicy(), now=NOW)

    assert decision.reasons[at(days=0)] == ("daily", "weekly", "monthly")
    assert all(decision.reasons[name] for name in decision.keep)
    assert all(name not in decision.reasons for name in decision.remove)


def test_every_backup_is_either_kept_or_removed_and_never_both() -> None:
    names = [at(hours=hour * 7) for hour in range(200)]

    decision = select(names, policy=RetentionPolicy(), now=NOW)

    assert set(decision.keep) | set(decision.remove) == set(names)
    assert not set(decision.keep) & set(decision.remove)


def test_the_decision_does_not_depend_on_the_order_it_was_given() -> None:
    names = [at(days=day) for day in range(40)]

    forwards = select(names, policy=RetentionPolicy(), now=NOW)
    backwards = select(list(reversed(names)), policy=RetentionPolicy(), now=NOW)

    assert forwards == backwards


@pytest.mark.parametrize(
    "policy",
    [
        {"daily": -1},
        {"weekly": -1},
        {"monthly": -1},
        {"daily": 0, "weekly": 0, "monthly": 0},
    ],
)
def test_a_policy_that_could_not_be_honoured_is_refused(policy: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        RetentionPolicy(**policy)


def test_a_boolean_is_not_a_count() -> None:
    # `True` is an integer to Python and would quietly mean "keep one".
    with pytest.raises(ValueError):
        RetentionPolicy(daily=True)
