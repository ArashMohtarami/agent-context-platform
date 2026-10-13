"""Which backups to keep, and which to let go.

Three tiers, counted in periods rather than in files: the newest backup of each
of the last 7 days, of each of the last 4 ISO weeks, and of each of the last 3
months. A backup can satisfy several tiers at once and is then kept once, so
the totals are a ceiling and not a quota — a deployment that backs up hourly
keeps at most fourteen backups, not fourteen per day.

Counting periods rather than files is what makes the policy mean what it says.
"Seven daily" should survive a week of outage during which nothing ran; a rule
that kept the last seven *files* would, after one busy afternoon, hold seven
backups all from that afternoon and nothing older.

Pure: it takes names and an instant and returns a decision. Deleting is the
caller's business, which is what makes the rule testable without a filesystem
and reviewable without running anything.
"""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from app.backup.naming import taken_at

# The documented default. Roughly three months of history in fourteen files.
DEFAULT_DAILY: Final = 7
DEFAULT_WEEKLY: Final = 4
DEFAULT_MONTHLY: Final = 3

type Period = tuple[int, ...]


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """How many periods of each tier to keep."""

    daily: int = DEFAULT_DAILY
    weekly: int = DEFAULT_WEEKLY
    monthly: int = DEFAULT_MONTHLY

    def __post_init__(self) -> None:
        """Reject a policy that would keep nothing or cannot be counted."""
        for name in ("daily", "weekly", "monthly"):
            value = getattr(self, name)
            if isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} retention must be zero or more")
        if self.daily + self.weekly + self.monthly == 0:
            raise ValueError("a retention policy must keep at least one period")


@dataclass(frozen=True, slots=True)
class Decision:
    """What to keep, what to remove, and why each kept backup survived.

    `reasons` exists so `prune --dry-run` can explain itself. An operator about
    to delete a backup deserves to see which tier saved the ones that stayed.
    """

    keep: tuple[str, ...]
    remove: tuple[str, ...]
    reasons: dict[str, tuple[str, ...]]


def select(archives: Iterable[str], *, policy: RetentionPolicy, now: datetime) -> Decision:
    """Decide which backups to keep under this policy.

    Newest first within each period, and a backup from the future — a clock
    that went backwards, a file copied from another host — is always kept. It
    is never safe to delete something on the strength of a timestamp that says
    the present has not happened yet.
    """
    dated = sorted(
        ((name, taken_at(name)) for name in archives),
        key=lambda item: item[1],
        reverse=True,
    )
    reasons: dict[str, list[str]] = {}
    for tier, periods, key in (
        ("daily", policy.daily, _day),
        ("weekly", policy.weekly, _week),
        ("monthly", policy.monthly, _month),
    ):
        for name in _newest_per_period(dated, periods=periods, key=key, now=now):
            reasons.setdefault(name, []).append(tier)
    for name, created_at in dated:
        if created_at > now:
            reasons.setdefault(name, []).insert(0, "ahead of the clock")

    keep = tuple(name for name, _ in dated if name in reasons)
    remove = tuple(name for name, _ in dated if name not in reasons)
    return Decision(
        keep=keep,
        remove=remove,
        reasons={name: tuple(tiers) for name, tiers in reasons.items()},
    )


def _newest_per_period(
    dated: Sequence[tuple[str, datetime]],
    *,
    periods: int,
    key: Callable[[datetime], Period],
    now: datetime,
) -> list[str]:
    """Return the newest backup in each of the most recent `periods` periods.

    Periods are counted from the backups themselves, not from the calendar, so
    a gap in the history does not consume a slot. Seven daily backups means the
    seven most recent days that *have* one.
    """
    kept: list[str] = []
    seen: list[Period] = []
    for name, created_at in dated:
        if created_at > now:
            continue
        period = key(created_at)
        if period in seen:
            continue
        if len(seen) >= periods:
            break
        seen.append(period)
        kept.append(name)
    return kept


def _day(moment: datetime) -> Period:
    """Return the calendar day one instant falls in."""
    return (moment.year, moment.month, moment.day)


def _week(moment: datetime) -> Period:
    """Return the ISO week one instant falls in."""
    year, week, _ = moment.isocalendar()
    return (year, week)


def _month(moment: datetime) -> Period:
    """Return the calendar month one instant falls in."""
    return (moment.year, moment.month)
