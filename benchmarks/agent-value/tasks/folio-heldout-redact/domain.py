def redact(value):
    if isinstance(value, dict):
        return {
            k: ("[REDACTED]" if k in {"password", "access_token", "secret"} else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def execute(store, c):
    if c["action"] != "redact":
        raise ValueError("action")
    return {"payload": redact(c["payload"])}
