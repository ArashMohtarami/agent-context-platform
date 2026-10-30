def execute(store, c):
    if c["action"] != "reverse":
        raise ValueError("action")
    s = store.data
    payload = {k: c[k] for k in ("invoice_id", "cents")}
    if type(c["cents"]) is not int or c["cents"] <= 0:
        raise ValueError("amount")
    if c["invoice_id"] not in s["invoices"]:
        raise ValueError("invoice")
    old = s["reversals"].get(c["reversal_id"])
    if old is not None:
        if old != payload:
            raise ValueError("conflict")
        return {"reversal_id": c["reversal_id"]}
    _used = sum(r["cents"] for r in s["reversals"].values() if r["invoice_id"] == c["invoice_id"])
    if c["cents"] > s["invoices"][c["invoice_id"]]:
        raise ValueError("overdraw")
    s["reversals"][c["reversal_id"]] = payload
    s["ledger"].append(
        {"id": c["reversal_id"], "invoice_id": c["invoice_id"], "cents": -c["cents"]}
    )
    return {"reversal_id": c["reversal_id"]}
