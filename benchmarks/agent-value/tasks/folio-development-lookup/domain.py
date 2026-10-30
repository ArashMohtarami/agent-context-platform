def execute(store, c):
    if c["action"] != "lookup":
        raise ValueError("action")
    query = c["query"].casefold()
    rows = store.data["invoices"]
    return {
        "ids": sorted(
            r["id"]
            for r in rows
            if query == r["description"].casefold()
            and (c.get("tag") is None or c["tag"] in r["tags"])
        )
    }
