def execute(store, c):
    if c["action"] != "move":
        raise ValueError("action")
    s = store.data
    key = c["booking_id"]
    if key not in s["bookings"]:
        raise ValueError("booking")
    row = s["bookings"][key]
    if row["room"] == c["room"] and row["start"] == c["start"] and row["end"] == c["end"]:
        return {"booking_id": key}
    if c["room"] not in s["rooms"] or c["end"] <= c["start"]:
        raise ValueError("room")
    seats = row["seats"]
    del s["bookings"][key]
    changes = [(c["start"], seats), (c["end"], -seats)]
    for b in s["bookings"].values():
        if b["room"] == c["room"]:
            changes.extend([(b["start"], b["seats"]), (b["end"], -b["seats"])])
    occupied = 0
    for _at, delta in sorted(changes, key=lambda x: (x[0], x[1])):
        occupied += delta
        if occupied > s["rooms"][c["room"]]:
            raise ValueError("capacity")
    s["bookings"][key] = {"room": c["room"], "start": c["start"], "end": c["end"], "seats": seats}
    s["events"].append({"booking_id": key, "type": "moved"})
    return {"booking_id": key}
