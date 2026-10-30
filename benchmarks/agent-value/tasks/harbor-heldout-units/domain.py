def execute(store, c):
    if c["action"] != "totals":
        raise ValueError("action")
    s = store.data
    if c["station_id"] not in s["stations"]:
        raise ValueError("station")
    payload = {k: c[k] for k in ("station_id", "day", "quantities")}
    old = s["batches"].get(c["batch_id"])
    if old is not None:
        if old != payload:
            raise ValueError("conflict")
        return {"millilitres": s["receipts"][c["batch_id"]]}
    total = 0
    for row in c["quantities"]:
        if row["unit"] not in {"L", "mL"} or type(row["value"]) is not int or row["value"] < 0:
            raise ValueError("quantity")
        total += row["value"] * (1)
    key = c["station_id"] + ":" + c["day"]
    s["totals"][key] = s["totals"].get(key, 0) + total
    s["batches"][c["batch_id"]] = payload
    s["receipts"][c["batch_id"]] = total
    return {"millilitres": total}
