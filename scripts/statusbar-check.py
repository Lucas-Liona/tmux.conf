#!/usr/bin/env python3
"""Pins for where the bar's two halves sit (Lucas, 2026-11-20: "put this on the bottom!!! keep the status on the
top that part is fine", with a screenshot of the windows row; then "Status in border").

    python3 ~/.config/tmux/scripts/statusbar-check.py

tmux 3.4 puts every status line on one side, so the status half moved into the active pane's top border:
- Top row, the border: the ! badge, ⇄ last session, Lucas's state and fleet's line, left; the skill badge and gen,
  right. A border takes no clicks in 3.4, so these only show.
- Last row, the bar: the session pill and the windows, left; a clickable ! badge, the ⌘ button and the
  directory, right.
A throwaway scene (scripts/scene) with tmux.conf's bar lines and the live colours, never the live server.
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
NEEDS = os.environ.get("NEEDS_BIN", str(Path.home() / ".local/bin/needs"))
FLEET = "Lucas active · lucas2 working · 0 lanes working · lab idle"


class Layout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scene = s = scene.Scene()
        s.start("lucas2", "work", "cat", live_colours=True,
                extra=["set -g @lucas_state active", f"set -g @fleet_line '{FLEET}'", "set -g @gen 61"])
        s.tmux("new-session", "-d", "-s", "zsh-conf", "cat")
        s.tmux("switch-client", "-t", "zsh-conf")
        s.tmux("switch-client", "-t", "lucas2")  # now ⇄ zsh-conf
        s.run(NEEDS, "add", "a pin's ask", capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.scene.close()

    def rows(self, ready, seconds=8):
        end = time.time() + seconds
        while time.time() < end:
            rows = self.scene.screen().splitlines()
            if ready(rows):
                return rows
            time.sleep(0.1)
        self.fail("not ready:\n" + self.scene.screen())

    def test_top_row_is_the_border_with_the_status_half(self):
        top = self.rows(lambda r: "! 1" in r[0] and "Lucas active" in r[0])[0]
        self.assertIn("─", top)  # drawn on the border line; the old top status line had none
        for text in ("! 1", "⇄ zsh-conf", "Lucas active", "lucas2 working", "0 lanes working", "61"):
            self.assertIn(text, top)
        self.assertLess(top.index("! 1"), top.index("⇄ zsh-conf"))
        self.assertNotIn("⌘", top)

    def test_last_row_is_the_bar_with_the_windows_and_a_clickable_badge(self):
        bottom = self.rows(lambda r: "⌘" in r[-1])[-1]
        for text in ("lucas2", "work", "! 1", "⌘"):
            self.assertIn(text, bottom)
        self.assertLess(bottom.index("work"), bottom.index("! 1"))
        self.assertLess(bottom.index("! 1"), bottom.index("⌘"))
        for text in ("⇄", "Lucas active"):
            self.assertNotIn(text, bottom)

    def test_the_bar_is_one_line(self):
        rows = self.rows(lambda r: "⌘" in r[-1])
        self.assertNotIn("⌘", rows[-2])
        self.assertEqual(self.scene.tmux("show-options", "-gv", "status"), "on")


if __name__ == "__main__":
    unittest.main(verbosity=2)
