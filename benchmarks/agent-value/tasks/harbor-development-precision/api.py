"""JSON command interface; transaction ownership lives here."""

from copy import deepcopy

import domain
from repository import Repository


def handle(envelope):
    store = Repository(envelope["state"])
    results = []
    for command in envelope["commands"]:
        before = store.snapshot()
        try:
            if command["action"] == "health":
                result = {"ok": True}
            else:
                result = domain.execute(store, deepcopy(command))
        except (ValueError, KeyError, TypeError) as error:
            store.restore(before)
            result = {"error": str(error.args[0]) if error.args else "invalid"}
        results.append(result)
    return {"results": results, "state": store.snapshot()}
