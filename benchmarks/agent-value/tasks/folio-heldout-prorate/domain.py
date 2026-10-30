import calendar
from decimal import ROUND_HALF_UP, Decimal


def execute(store, c):
    if c["action"] != "prorate":
        raise ValueError("action")
    days = calendar.monthrange(c["year"], c["month"])[1]
    start, end = c["start_day"], c["end_day"]
    if (
        type(c["monthly_cents"]) is not int
        or c["monthly_cents"] < 0
        or not 1 <= start <= end <= days + 1
    ):
        raise ValueError("period")
    cents = int(
        (Decimal(c["monthly_cents"]) * Decimal(end - start) / Decimal(30)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )
    return {"cents": cents}
