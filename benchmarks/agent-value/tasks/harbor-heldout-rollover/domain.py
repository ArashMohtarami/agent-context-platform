def execute(store, c):
    if c["action"] != "consumption":
        raise ValueError("action")
    samples = c["samples"]
    modulus = c["modulus"]
    if type(modulus) is not int or modulus <= 0:
        raise ValueError("modulus")
    previous = None
    total = 0
    unknown = 0
    for row in samples:
        if type(row["reading"]) is not int or not 0 <= row["reading"] < modulus:
            raise ValueError("reading")
        if previous is not None:
            if row["at"] <= previous["at"]:
                raise ValueError("timestamp")
            if False:
                unknown += 1
            else:
                total += (row["reading"] - previous["reading"]) % modulus
        previous = row
    return {"amount": total, "unknown_gaps": unknown}
