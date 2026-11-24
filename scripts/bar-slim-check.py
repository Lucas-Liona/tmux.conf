#!/usr/bin/env python3
"""Pins for scripts/bar-slim: one session's bottom bar without the session pill, the red count and the folder pill.

    python3 ~/.config/tmux/scripts/bar-slim-check.py

A throwaway scene (scripts/scene) with tmux.conf's bar lines, never the live server.
"""
import importlib.util
import os
import sys
import time
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
_loader = SourceFileLoader("scene", str(HERE / "scene"))
scene = importlib.util.module_from_spec(importlib.util.spec_from_loader("scene", _loader))
_loader.exec_module(scene)
SLIM = str(HERE / "bar-slim")
NEEDS = os.environ.get("NEEDS_BIN", str(Path.home() / ".local/bin/needs"))


class Slim(unittest.TestCase):
    def setUp(self):
        self.scene = s = scene.Scene()
        self.addCleanup(s.close)
        self.state = s.dir / "slim.sessions"
        s.env["BAR_SLIM_STATE"] = str(self.state)
        s.start("lucas2", "work", "cat", live_colours=True)
        s.tmux("new-session", "-d", "-s", "other", "-n", "elsewhere", "cat")
        s.run(NEEDS, "add", "a pin's ask", capture_output=True)

    def bar(self, ready, seconds=20):
        end = time.time() + seconds
        while time.time() < end:
            row = self.scene.screen().splitlines()[-1]
            if ready(row):
                return row
            time.sleep(0.1)
        self.fail("the bar never got there:\n" + self.scene.screen().splitlines()[-1])

    def test_on_takes_the_three_pieces_off_and_keeps_the_menu_and_the_jobs(self):
        full = self.bar(lambda r: "⌘" in r and "! 1" in r)
        self.assertIn("lucas2", full)
        out = self.scene.run(SLIM, "on", "lucas2", capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        row = self.bar(lambda r: "! 1" not in r)
        self.assertIn("⌘", row)
        self.assertIn("work", row)
        self.assertNotIn("lucas2", row)                       # the session pill
        for side in ("status-left", "status-right"):
            mine = self.scene.tmux("show-options", "-v", "-t", "lucas2", side)
            for job in [part for part in self.scene.tmux("show-options", "-gv", side).split("#(")[1:]]:
                self.assertIn("#(" + job.split(")")[0] + ")", mine, "a job the bar starts was cut")
        self.assertEqual(self.scene.tmux("show-options", "-qv", "-t", "other", "status-right"), "")   # still the global one
        self.assertEqual(self.state.read_text(), "lucas2\n")

    def test_off_gives_the_full_bar_back(self):
        self.scene.run(SLIM, "on", "lucas2", capture_output=True)
        self.bar(lambda r: "! 1" not in r)
        self.scene.run(SLIM, "off", "lucas2", capture_output=True)
        row = self.bar(lambda r: "! 1" in r)
        self.assertIn("lucas2", row)
        self.assertEqual(self.state.read_text(), "")

    def test_what_tmux_forgets_the_file_remembers(self):
        self.state.write_text("lucas2\n")
        self.assertEqual(self.scene.run(SLIM, capture_output=True).returncode, 0)
        self.bar(lambda r: "⌘" in r and "! 1" not in r)


if __name__ == "__main__":
    unittest.main(verbosity=2)
