def execute(store, c):
    if c["action"] != "dispatch":
        raise ValueError("action")
    s = store.data
    r = s["reservations"].get(c["reservation_id"])
    if r is None:
        raise ValueError("reservation")
    if r["status"] == "dispatched":
        return {"shipment_id": r["shipment_id"]}
    if r["status"] != "ready":
        raise ValueError("state")
    shipment_id = "ship-" + c["reservation_id"]
    r.update(status="dispatched", shipment_id=shipment_id)
    s["shipments"].append({"id": shipment_id, "reservation_id": c["reservation_id"]})
    if not c["courier_available"]:
        raise ValueError("courier")
    s["events"].append({"type": "dispatched", "shipment_id": shipment_id})
    return {"shipment_id": shipment_id}
