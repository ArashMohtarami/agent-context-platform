"""Outcome oracle for twelve fictional tasks. Never supplied to a coding agent."""

import re
from decimal import ROUND_HALF_UP, Decimal


def cursor(value):
    if type(value) is not int or value < 0:
        raise ValueError("invalid sequence")
    return format(value, "x")


def ttl(found):
    return 45 if found else 7


def retry(attempt, error):
    return min(2**attempt, 32) if error == "network" else None


def money(value):
    return int(Decimal(value).quantize(Decimal(".01"), rounding=ROUND_HALF_UP) * 100)


def slug(value):
    lowered = value.strip().translate(
        str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
    )
    result = re.sub(r"[ _]+", "-", lowered)
    if not result:
        raise ValueError("blank slug")
    return result


def order(values):
    return sorted(values, key=lambda value: (value["priority"], value["sequence"]))


def deadline(now, expires_at):
    return now >= expires_at


def filename(value):
    return value.lower().endswith((".txt", ".md"))


def percent(value):
    if type(value) is not int:
        raise ValueError("invalid percentage")
    return max(0, min(100, value))


def overlay(base, overrides):
    return {**base, **{key: value for key, value in overrides.items() if value is not None}}


def path(value):
    return (
        bool(value)
        and not value.startswith("/")
        and "\\" not in value
        and all(part not in {"", ".", ".."} for part in value.split("/"))
    )


def lane(value):
    if type(value) is not int or value < 0:
        raise ValueError("invalid sequence")
    return value % 7
