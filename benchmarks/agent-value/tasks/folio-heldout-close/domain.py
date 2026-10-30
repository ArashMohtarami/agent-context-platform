def execute(store, c):
    if c["action"] != "close":
        raise ValueError("action")
    s = store.data
    period = c["period"]
    if period in s["receipts"]:
        return {"journal_id": s["receipts"][period]}
    rows = [v for v in s["invoices"].values() if v["period"] == period and v["status"] == "open"]
    for row in rows:
        row["status"] = "frozen"
    journal_id = "close-" + period
    s["journal"].append({"id": journal_id, "cents": sum(v["cents"] for v in rows)})
    if not c["export_available"]:
        raise ValueError("export")
    s["receipts"][period] = journal_id
    return {"journal_id": journal_id}
