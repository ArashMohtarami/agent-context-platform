def execute(store, c):
    if c["action"] != "quote":
        raise ValueError("action")
    values = [c[k] for k in ("weight_g", "length_mm", "width_mm", "height_mm")]
    if any(type(v) is not int or v <= 0 for v in values):
        raise ValueError("measurement")
    weight, length, width, height = values
    _volume = (length * width * height + 4999) // 5000
    charged = weight
    return {"charged_g": charged, "cents": ((charged + 499) // 500) * 175}
