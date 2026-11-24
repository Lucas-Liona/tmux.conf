#!/usr/bin/env python3
"""Pins for scripts/window-fit: a session's window names shrink until every window is on the bar.

    python3 ~/.config/tmux/scripts/window-fit-check.py

A throwaway scene (scripts/scene) 120 cells wide with tmux.conf's bar lines and its window-fit hook lines,
never the live server. The remembered sessions go to a file in the scene's own folder.
"""
import importlib.util
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
FIT = str(HERE / "window-fit")
HOOKS = [line.replace("~/.config/tmux/scripts/", f"{HERE}/") for line in (HERE.parent / "tmux.conf").read_text().splitlines()
         if line.startswith("set-hook") and "window-fit" in line]


class Fit(unittest.TestCase):
    def setUp(self):
        self.scene = s = scene.Scene()
        self.addCleanup(s.close)
        self.state = s.dir / "fit.sessions"
        s.env["WINDOW_FIT_STATE"] = str(self.state)
        s.start("lucas2", "window-name-01", "cat", size=(120, 20), extra=HOOKS)
        for n in range(2, 15):
            s.tmux("new-window", "-d", "-t", "lucas2:", "-n", f"window-name-{n:02}", "cat")

    def fit(self, *args):
        return self.scene.run(FIT, *args, capture_output=True, text=True)

    def indexes(self):
        return self.scene.tmux("list-windows", "-t", "lucas2", "-F", "#{window_index}").split()

    def bar(self):
        return self.scene.screen().splitlines()[-1]

    def bar_when(self, ready, seconds=6):
        end = time.time() + seconds
        while time.time() < end:
            row = self.bar()
            if ready(row):
                return row
            time.sleep(0.15)
        self.fail("the bar never got there:\n" + self.bar())

    def shows_all(self, row):
        return all(f" {i}:" in f" {row}" for i in self.indexes())

    def test_the_hook_lines_are_in_tmux_conf(self):
        self.assertTrue(HOOKS, "tmux.conf has no set-hook line that runs window-fit")
        for event in ("window-linked", "window-unlinked", "window-renamed", "client-resized"):
            self.assertTrue(any(event in line for line in HOOKS), event)

    def test_fourteen_long_names_do_not_fit_until_the_session_is_fitted(self):
        self.assertFalse(self.shows_all(self.bar_when(lambda r: "1:" in r)))
        out = self.fit("on", "lucas2")
        self.assertEqual(out.returncode, 0, out.stderr)
        row = self.bar_when(self.shows_all)
        second = self.indexes()[1]                                    # not the one in front: that one gets the spare room
        self.assertNotIn(f"{second}:window-name-02", row)            # cut
        self.assertRegex(row, rf" {second}:w")                        # and not cut to nothing while there is room
        self.assertLessEqual(len(row.rstrip()), 120)
        self.assertIn("lucas2", self.state.read_text())

    def test_it_refits_when_a_window_is_made_renamed_or_closed(self):
        self.fit("on", "lucas2")
        self.bar_when(self.shows_all)
        for n in range(15, 21):
            self.scene.tmux("new-window", "-d", "-t", "lucas2:", "-n", f"window-name-{n:02}", "cat")
        self.bar_when(lambda r: len(self.indexes()) == 20 and self.shows_all(r))
        last = self.indexes()[-1]
        self.scene.tmux("rename-window", "-t", f"lucas2:{last}", "a-much-longer-name-than-before")
        self.bar_when(self.shows_all)
        for index in self.indexes()[3:]:
            self.scene.tmux("kill-window", "-t", f"lucas2:{index}")
        row = self.bar_when(lambda r: "window-name-01" in r and "window-name-03" in r)   # three fit whole again
        first = self.indexes()[0]
        self.assertEqual(self.scene.tmux("show-options", "-wqv", "-t", f"lucas2:{first}", "window-status-format"), "")

    def shown_names(self, row):
        """index -> the part of its name the bar shows, read off the windows part of the row."""
        import re
        # a name, then tmux's own flag for the window (* in front, - the one before), then a space
        return {m.group(1): m.group(2) for m in re.finditer(r"(?<![\w-])(\d+):([\w-]*?)[*\-#!~MZ]?(?=\s|$)", row.split("⌘")[0])}

    def test_the_spare_cells_go_to_the_window_he_is_in(self):
        # Live, 2026-11-24: 21 windows at 158 cells left 23 cells spare, too few for a letter each, and every
        # window was a bare number. The window in front takes the spare room; it moves with him, with no refit.
        for n in range(15, 21):
            self.scene.tmux("new-window", "-d", "-t", "lucas2:", "-n", f"window-name-{n:02}", "cat")
        self.fit("on", "lucas2")
        row = self.bar_when(lambda r: len(self.indexes()) == 20 and self.shows_all(r))
        names = self.shown_names(row)
        front = self.scene.tmux("display-message", "-p", "-t", "lucas2:", "#{window_index}")
        others = [len(v) for k, v in names.items() if k != front]
        self.assertGreater(len(names[front]), max(others), row)
        target = self.indexes()[7]
        self.scene.tmux("select-window", "-t", f"lucas2:{target}")
        row = self.bar_when(lambda r: self.shows_all(r) and len(self.shown_names(r).get(target, "")) > max(others))
        self.assertLessEqual(len(self.shown_names(row)[front]), max(others))

    def test_a_window_with_its_own_format_keeps_it_and_gets_it_back(self):
        second = self.indexes()[1]
        own = self.scene.tmux("show-options", "-gv", "window-status-format") + " BOT"
        self.scene.tmux("set-option", "-w", "-t", f"lucas2:{second}", "window-status-format", own)
        self.fit("on", "lucas2")
        row = self.bar_when(self.shows_all)
        self.assertIn("BOT", row)
        self.fit("off", "lucas2")
        self.assertEqual(self.scene.tmux("show-options", "-wqv", "-t", f"lucas2:{second}", "window-status-format"), own)
        third = self.indexes()[2]
        self.assertEqual(self.scene.tmux("show-options", "-wqv", "-t", f"lucas2:{third}", "window-status-format"), "")
        self.assertNotIn("lucas2", self.state.read_text())
        self.bar_when(lambda r: "window-name-01" in r)

    def test_another_session_is_left_alone(self):
        self.scene.tmux("new-session", "-d", "-s", "other", "-n", "untouched-window-name", "cat")
        self.fit("on", "lucas2")
        self.bar_when(self.shows_all)
        other = self.scene.tmux("list-windows", "-t", "other", "-F", "#{window_id}").split()[0]
        self.assertEqual(self.scene.tmux("show-options", "-wqv", "-t", other, "window-status-format"), "")

    def test_what_tmux_forgets_the_file_remembers(self):
        # after a restart the session is back (resurrect) but no option is: only the file says it was fitted
        self.state.write_text("lucas2\n")
        self.assertFalse(self.shows_all(self.bar_when(lambda r: "1:" in r)))
        self.assertEqual(self.fit().returncode, 0)
        self.bar_when(self.shows_all)


if __name__ == "__main__":
    unittest.main(verbosity=2)
