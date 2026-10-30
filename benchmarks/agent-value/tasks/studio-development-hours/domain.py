from datetime import UTC, datetime


def execute(store, c):
    if c["action"] != "open":
        raise ValueError("action")
    at = datetime.fromisoformat(c["at"])
    if at.tzinfo is None:
        raise ValueError("timezone")
    at = at.replace(tzinfo=UTC)
    return {"open": at.weekday() < 5 and 9 <= at.hour < 17}
