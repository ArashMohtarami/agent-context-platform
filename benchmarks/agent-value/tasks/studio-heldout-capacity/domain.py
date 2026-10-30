def fits(rows, room, start, end, seats, capacity):
    changes = [(start, seats), (end, -seats)]
    for b in rows.values():
        if b["room"] == room:
            changes.extend([(b["start"], b["seats"]), (b["end"], -b["seats"])])
    occupied = 0
    for _at, delta in sorted(changes, key=lambda x: (x[0], -x[1])):
        occupied += delta
        if occupied > capacity:
            return False
    return True


def execute(store, c):
    if c["action"] != "book":
        raise ValueError("action")
    s = store.data
    if type(c["seats"]) is not int or c["seats"] <= 0 or c["end"] <= c["start"]:
        raise ValueError("interval")
    if c["room"] not in s["rooms"]:
        raise ValueError("room")
    if c["booking_id"] in s["bookings"]:
        raise ValueError("conflict")
    if not fits(s["bookings"], c["room"], c["start"], c["end"], c["seats"], s["rooms"][c["room"]]):
        raise ValueError("capacity")
    s["bookings"][c["booking_id"]] = {k: c[k] for k in ("room", "start", "end", "seats")}
    return {"booking_id": c["booking_id"]}
