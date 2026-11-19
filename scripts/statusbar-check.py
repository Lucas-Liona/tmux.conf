#!/usr/bin/env python3
"""Pins for the bar's two lines (Lucas, 2026-11-19: "top: ! 23 … top: ⇄ zsh-conf 61 Lucas active … bottom:
the windows … bottom right: menu ⌘ lucas2. fix the order").

    python3 ~/.config/tmux/scripts/statusbar-check.py

Row 0, the top line: the ! badge on the left; ⇄ last session, gen, Lucas's state and fleet's line on the right.
Row 1: the session pill and the windows on the left; the ⌘ button and the directory on the right.
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


class TwoLines(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scene = s = scene.Scene()
        s.start("lucas2", "work", "cat", live_colours=True,
                extra=["set -g @lucas_state active", "set -g @fleet_line 'Lucas active · lucas2 working · 0 lanes working · lab idle'",
                       "set -g @gen 61"])
        s.tmux("new-session", "-d", "-s", "zsh-conf", "cat")
        s.tmux("switch-client", "-t", "zsh-conf")
        s.tmux("switch-client", "-t", "lucas2")  # now ⇄ zsh-conf
        s.run(NEEDS, "add", "a pin's ask", capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.scene.close()

    def rows(self, want, seconds=8):
        end = time.time() + seconds
        while time.time() < end:
            rows = self.scene.screen().splitlines()
            if want in "\n".join(rows[:2]):
                return rows
            time.sleep(0.1)
        self.fail(f"{want!r} not in the top two rows:\n" + "\n".join(self.scene.screen().splitlines()[:3]))

    def test_top_line_holds_the_badge_left_and_the_fleet_right(self):
        top = self.rows("! 1")[0]
        self.assertTrue(top.lstrip().startswith("! 1"), top)
        for text in ("⇄ zsh-conf", "61", "Lucas active", "lucas2 working", "0 lanes working"):
            self.assertIn(text, top)
        self.assertLess(top.index("! 1"), top.index("⇄ zsh-conf"))
        self.assertNotIn("⌘", top)
        self.assertNotIn("work", top.replace("working", ""))

    def test_bottom_line_holds_the_windows_left_and_the_button_right(self):
        bottom = self.rows("⌘")[1]
        self.assertIn("lucas2", bottom)
        self.assertIn("work", bottom)
        self.assertIn("⌘", bottom)
        self.assertLess(bottom.index("work"), bottom.index("⌘"))
        for text in ("! 1", "⇄", "Lucas active"):
            self.assertNotIn(text, bottom)

    def test_the_pane_border_follows_both_lines(self):
        self.assertTrue(self.rows("⌘")[2].startswith("─"), self.rows("⌘")[:3])


if __name__ == "__main__":
    unittest.main(verbosity=2)
