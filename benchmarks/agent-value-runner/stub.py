"""Authored numeric event producer, used ONLY by isolated model-free validation."""

import json
import os
import socket
import sys
import time
from pathlib import Path


def emit(value):
    print(json.dumps(value), flush=True)


request = json.load(sys.stdin)
scenario = request["scenario"]
Path("/work/home/.codex").mkdir(parents=True)
emit({"type": "thread.started", "thread_id": "synthetic-thread"})
emit({"type": "turn.started"})
if scenario == "timeout":
    time.sleep(10)
elif scenario == "interrupt":
    os._exit(23)
elif scenario == "overflow":
    print("x" * 131072, flush=True)
elif scenario == "probe":
    denied = []
    for path in request["probes"]:
        try:
            Path(path).read_bytes()
            denied.append(False)
        except OSError:
            denied.append(True)
    try:
        socket.socket()
        denied.append(False)
    except PermissionError:
        denied.append(True)
    try:
        os.fork()
        denied.append(False)
    except PermissionError:
        denied.append(True)
    denied.append(not any(k in os.environ for k in ("OPENAI_API_KEY", "CODEX_API_KEY", "TOKEN")))
    try:
        Path("/inputs/api.py").write_text("tampered")
        denied.append(False)
    except OSError:
        denied.append(True)
    try:
        Path("/work/fill").write_bytes(b"x" * (16 * 1024 * 1024))
        denied.append(False)
    except OSError:
        denied.append(True)
    emit({"type": "stub.probe", "denied": denied})
usage = {
    "input_tokens": 100,
    "cached_input_tokens": 40,
    "output_tokens": 20,
    "reasoning_output_tokens": 5,
}
if scenario == "bad-usage":
    usage["new_counter"] = 12
emit({"type": "turn.completed", "usage": usage})
