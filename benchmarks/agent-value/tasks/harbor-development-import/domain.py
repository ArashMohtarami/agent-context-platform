def execute(store, c):
    if c["action"] != "import":
        raise ValueError("action")
    s = store.data
    if c["station_id"] not in s["stations"]:
        raise ValueError("station")
    payload = {"station_id": c["station_id"], "samples": c["samples"]}
    old = s["batches"].get(c["batch_id"])
    if old is not None:
        if old != payload:
            raise ValueError("conflict")
        return {"count": len(c["samples"])}
    seen = {v["at"] for v in s["samples"] if v["station_id"] == c["station_id"]}
    for v in c["samples"]:
        if type(v["at"]) is not int or type(v["reading"]) is not int or v["reading"] < 0:
            raise ValueError("sample")
        seen.add(v["at"])
        s["samples"].append({**v, "station_id": c["station_id"]})
    s["samples"].sort(key=lambda v: (v["station_id"], v["at"]))
    s["batches"][c["batch_id"]] = payload
    s["audit"].append(c["batch_id"])
    return {"count": len(c["samples"])}
