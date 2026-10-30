def execute(store, c):
    if c["action"] != "alerts":
        raise ValueError("action")
    rows = sorted(c["samples"], key=lambda r: r["at"])
    if len({r["at"] for r in rows}) != len(rows):
        raise ValueError("timestamp")
    streak = 0
    times = []
    for row in rows:
        streak = streak + 1 if row["confident"] and row["value"] >= c["threshold"] else 0
        if streak == 1:
            times.append(row["at"])
    return {"times": times}
