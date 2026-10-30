def execute(store, c):
    if c["action"] != "cancel":
        raise ValueError("action")
    s = store.data
    key = c["booking_id"]
    if key not in s["bookings"]:
        raise ValueError("booking")
    if key in s["credits"]:
        return {"credit": s["credits"][key]}
    paid = s["bookings"][key]["paid"]
    minutes = c["minutes_before_start"]
    credit = paid // 2 if minutes > 0 else 0
    s["credits"][key] = credit
    s["bookings"][key]["status"] = "cancelled"
    return {"credit": credit}
