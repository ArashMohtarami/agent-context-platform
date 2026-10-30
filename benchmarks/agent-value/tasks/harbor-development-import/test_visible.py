"""Only these two named unittest cases are visible agent checks."""

import json
import unittest
from pathlib import Path

import api


class Visible(unittest.TestCase):
    def test_health(self):
        self.assertEqual(
            api.handle({"state": {"keep": 1}, "commands": [{"action": "health"}]}),
            {"results": [{"ok": True}], "state": {"keep": 1}},
        )

    def test_contract(self):
        row = json.loads(Path(__file__).with_name("visible.json").read_text())
        self.assertEqual(api.handle(row["input"]), row["expected"])
