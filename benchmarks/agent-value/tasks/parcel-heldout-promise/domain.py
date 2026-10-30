from datetime import UTC, datetime, timedelta


def business(day):
    while day.weekday() > 4:
        day += timedelta(days=1)
    return day


def execute(store, c):
    if c["action"] != "promise":
        raise ValueError("action")
    at = datetime.fromisoformat(c["ordered_at"])
    if at.tzinfo is None:
        raise ValueError("timezone")
    at = at.astimezone(UTC)
    day = at.date()
    if at.hour >= 17:
        day += timedelta(days=1)
    departure = business(day)
    delivery = departure
    for _ in range(2):
        delivery = business(delivery + timedelta(days=1))
    return {"departure": str(departure), "delivery": str(delivery)}
