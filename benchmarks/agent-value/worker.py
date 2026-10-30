"""Untrusted fixture worker. No expected answers, references or grader code here."""

import importlib
import json
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, "/work")
requests = json.load(sys.stdin)
api = importlib.import_module("api")
rows = []
for request in requests:
    before = json.dumps(request["input"], sort_keys=True)
    result = api.handle(request["input"])
    if before != json.dumps(request["input"], sort_keys=True):
        raise ValueError("caller input was mutated")
    rows.append({"case_id": request["case_id"], "result": result})
print(json.dumps({"version": 1, "rows": rows}, allow_nan=False, separators=(",", ":")))
