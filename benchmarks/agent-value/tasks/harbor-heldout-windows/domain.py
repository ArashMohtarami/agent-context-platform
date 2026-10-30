def execute(store, c):
    if c["action"] != "windows":
        raise ValueError("action")
    start, end, width = c["start"], c["end"], c["width"]
    if type(width) is not int or width <= 0 or end <= start:
        raise ValueError("window")
    rows = []
    for lo in range(start, end, width):
        hi = min(lo + width, end)
        values = [r["value"] for r in c["samples"] if lo <= r["at"] <= hi]
        rows.append({"start": lo, "end": hi, "count": len(values), "sum": sum(values)})
    return {"buckets": rows}
