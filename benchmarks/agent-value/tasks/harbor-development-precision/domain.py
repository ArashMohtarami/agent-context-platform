from decimal import Decimal, InvalidOperation


def execute(store, c):
    if c["action"] != "display":
        raise ValueError("action")
    try:
        value = Decimal(c["value"])
    except InvalidOperation:
        raise ValueError("measurement") from None
    if not value.is_finite() or value < 0:
        raise ValueError("measurement")
    rounded = value.quantize(Decimal(".01"), rounding="ROUND_DOWN")
    return {"value": format(abs(rounded), ".2f")}
