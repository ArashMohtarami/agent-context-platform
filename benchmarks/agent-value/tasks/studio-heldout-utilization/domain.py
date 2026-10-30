def execute(store, c):
    if c["action"] != "report":
        raise ValueError("action")
    if c["end"] <= c["start"]:
        raise ValueError("interval")
    spans = sorted(
        (max(c["start"], b["start"]), min(c["end"], b["end"]))
        for b in store.data["bookings"]
        if b["room"] == c["room"] and b["end"] > c["start"] and b["start"] < c["end"]
    )
    total = 0
    last = c["start"]
    for start, end in spans:
        total += end - start
        last = max(last, end)
    return {"minutes": total}
