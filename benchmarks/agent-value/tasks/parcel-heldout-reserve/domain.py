def execute(store, c):
    if c["action"] != "reserve":
        raise ValueError("action")
    counts = {}
    for row in c["lines"]:
        if type(row["qty"]) is not int or row["qty"] <= 0:
            raise ValueError("quantity")
        counts[row["sku"]] = row["qty"]
    s = store.data
    old = s["reservations"].get(c["reservation_id"])
    if old is not None:
        if old != counts:
            raise ValueError("conflict")
        return {"reservation_id": c["reservation_id"]}
    for sku, qty in counts.items():
        if sku not in s["inventory"] or s["inventory"][sku] < qty:
            raise ValueError("stock")
    for sku, qty in counts.items():
        s["inventory"][sku] -= qty
    s["reservations"][c["reservation_id"]] = counts
    return {"reservation_id": c["reservation_id"]}
