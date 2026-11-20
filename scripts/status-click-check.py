#!/usr/bin/env python3
"""Pins for the ⌘ on the status bar: the click, the menu it reads from a file, and what an item runs.

    python3 ~/.config/tmux/scripts/status-click-check.py

Two throwaway tmux servers, never the live one. The inner one gets tmux.conf's own lines for the
button (pointed at the scripts beside this file) and a menu file of its own. The outer one holds
a client of it in a pane: bytes sent to that pane are the mouse, and its screen is what Lucas
would see. A popup runs zsh -i, so these pins load his real zsh setup, and one opens his nvim.
"""
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "status-click"
CONF = HERE.parent / "tmux.conf"
DEFAULTS = HERE.parent / "click-menu.conf"
LINES = re.compile(r"^(set -g (mouse|@title_role|@click_label|status|status-format\[\d\]|status-right) |bind -n MouseDown1Status |"
                   r"run-shell .*(MouseDown3|status-click))")
LEFT, RIGHT, MOVE = 0, 2, 35  # SGR mouse buttons; 35 is a move with none held

_loader = SourceFileLoader("status_click", str(SCRIPT))
status_click = importlib.util.module_from_spec(importlib.util.spec_from_loader("status_click", _loader))
_loader.exec_module(status_click)

MENU = """\
# a comment

say hi      h  sh: print popup-says-$STATUS_PANE-in-$PWD-menu-$STATUS_MENU; read -k 1
rename      r  tmux: rename-window '#{session_name} clicked'
no key  tmux: set -g @clicked "it's set"
"""


class Parse(unittest.TestCase):
    def test_fields_by_spaces_or_tabs_key_optional_blank_lines_are_rules(self):
        got = status_click.items("# c\n\na b  k  sh: x  y | z\n\n\nt\ttmux: q\nlast\tz\ttmux: w\n\n")
        self.assertEqual(got, [(3, "a b", "k", "sh:", "x  y | z"), (4, "", "", "", ""),
                               (6, "t", "", "tmux:", "q"), (7, "last", "z", "tmux:", "w")])

    def test_a_line_that_does_not_parse_is_kept_as_bad(self):
        for bad in ("just words", "label  sh:", "label  key  sh: x", "label  k  zsh: x", "one space sh: x"):
            self.assertEqual(status_click.items(bad), [(1, bad, "", None, "")], bad)

    def test_the_menu_file_is_the_one_beside_tmux_conf(self):
        """Whichever checkout the script runs from, with no path written into it."""
        self.assertEqual(status_click.MENU, DEFAULTS)

    def test_every_line_of_the_shipped_menu_parses(self):
        got = status_click.items(DEFAULTS.read_text())
        self.assertEqual([item for item in got if item[3] is None], [])
        self.assertEqual([item[1] for item in got if item[3]],
                         ["! #{@needs_count} waiting · Lucas: #{@lucas_state} · Lucas 2: #{@lucas2_state}", "panel", "aq", "local models", "skills in this pane", "hand off (prefix T)", "new session", "ask claude",
                          "edit this menu"])


    def test_the_shipped_hand_off_and_skills_items_are_what_prefix_T_and_B_run(self):
        """Word for word, so the menu cannot drift from the keys."""
        bound = {m[1]: m[2] for m in re.finditer(r'^bind -N "[^"]*" ([TB]) (run-shell -b .*)$', CONF.read_text(), re.M)}
        shipped = {item[1]: item[4] for item in status_click.items(DEFAULTS.read_text())}
        self.assertEqual(shipped["hand off (prefix T)"], bound["T"])
        self.assertEqual(shipped["skills in this pane"], bound["B"])


class Files(unittest.TestCase):
    def test_two_clients_get_two_files_and_a_gone_clients_file_is_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "default.click-menu"
            gone = Path(f"{base}-999999999")  # no such pid
            gone.write_text("old\n")
            mine, theirs = Path(f"{base}-{os.getpid()}"), Path(f"{base}-{os.getppid()}")
            status_click.menu("/dev/pts/1", "%1", mine)
            status_click.menu("/dev/pts/2", "%2", theirs)
            self.assertIn("--do /dev/pts/1 %1 ", mine.read_text())
            self.assertIn("--do /dev/pts/2 %2 ", theirs.read_text())
            self.assertFalse(gone.exists())


class Click(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        tmp = Path(cls.dir.name)
        cls.inner, cls.outer, cls.menu = str(tmp / "inner"), str(tmp / "outer"), tmp / "menu.conf"
        cls.work = tmp / "work"
        cls.work.mkdir()
        lines = [line.replace("~/.config/tmux/scripts/", f"{HERE}/")
                 for line in CONF.read_text().splitlines() if LINES.match(line)]
        assert len(lines) == 8, lines  # one bar line since 2026-11-20: status on, no status-format lines
        conf = tmp / "click.conf"
        # escape-time as on the live server: at tmux's 500 ms a lone Escape swallows the mouse bytes after it
        conf.write_text("\n".join(lines + ['set -g @thm_mauve "#c6a0f6"', "set -s escape-time 20"]) + "\n")
        cls.tmux(cls.inner, "-f", "/dev/null", "new-session", "-d", "-s", "riverboat", "-x", "120", "-y", "30",
                 "-c", str(cls.work), "cat", env={"STATUS_CLICK_MENU": str(cls.menu)})
        cls.tmux(cls.inner, "source-file", str(conf))
        cls.tmux(cls.outer, "-f", "/dev/null", "new-session", "-d", "-s", "terminal", "-x", "120", "-y", "30",
                 f"env -u TMUX tmux -S {cls.inner} attach -t riverboat")

    @classmethod
    def tearDownClass(cls):
        for sock in (cls.outer, cls.inner):
            cls.tmux(sock, "kill-server")
        cls.dir.cleanup()

    @staticmethod
    def tmux(sock, *args, env=None):
        base = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")}
        return subprocess.run(["tmux", "-S", sock, *args], capture_output=True, text=True,
                              env={**base, **(env or {})}).stdout.rstrip("\n")

    def setUp(self):
        """A fresh window in view, the test menu, and nothing drawn over the client."""
        self.menu.write_text(MENU)
        self.pane = self.tmux(self.inner, "new-window", "-t", "riverboat:", "-P", "-F", "#{pane_id}",
                              "-c", str(self.work), "cat")
        self.wait_for("menu ⌘")
        # A message left by the last pin sits on the bar's first line, and the first click only clears it. With
        # one line it also hid the ⌘, so the wait above covered it; with two the ⌘ stays in view.
        self.wait_for("changed since the menu opened", gone=True)

    def tearDown(self):
        self.keys("Escape")  # a menu or a popup left open by a failed pin
        time.sleep(0.1)
        self.tmux(self.inner, "display-popup", "-C", "-t", self.pane)
        self.tmux(self.inner, "kill-window", "-t", self.pane)

    def screen(self):
        return self.tmux(self.outer, "capture-pane", "-p", "-t", "terminal:").split("\n")

    def where(self, text):
        """(column, row) of text on the client's screen, 1-based, one cell in; None when it is not there."""
        for row, line in enumerate(self.screen(), 1):
            if text in line:
                return line.index(text) + 2, row
        return None

    def wait_for(self, text, gone=False, seconds=6.0):
        end = time.time() + seconds
        while time.time() < end:
            if (self.where(text) is None) == gone:
                return
            time.sleep(0.05)
        self.fail(f"{text!r} {'still' if gone else 'not'} on screen:\n" + "\n".join(self.screen()))

    def mouse(self, button, at, down=True):
        self.tmux(self.outer, "send-keys", "-t", "terminal:", "-l", f"\033[<{button};{at[0]};{at[1]}{'M' if down else 'm'}")

    def click(self, text, button=LEFT):
        """What a hand does: move onto it, press, let go."""
        time.sleep(0.35)  # a press within 300 ms of the last one is tmux's double click, not a click
        at = self.where(text)
        self.assertIsNotNone(at, f"{text!r} not on screen:\n" + "\n".join(self.screen()))
        self.mouse(MOVE, at)
        time.sleep(0.05)
        self.mouse(button, at)
        time.sleep(0.05)
        self.mouse(button, at, down=False)

    def keys(self, *keys):
        self.tmux(self.outer, "send-keys", "-t", "terminal:", *keys)

    def option(self, name):
        return self.tmux(self.inner, "show-options", "-gqv", name)

    def test_the_label_is_the_role_and_the_badge(self):
        self.tmux(self.inner, "rename-window", "-t", self.pane, "control")
        self.wait_for("control ⌘")
        self.tmux(self.inner, "set-option", "-p", "-t", self.pane, "@skill_badge", "meta +1")
        self.wait_for("control · meta +1 ⌘")
        self.tmux(self.inner, "set-option", "-w", "-t", self.pane, "automatic-rename", "on")
        self.wait_for("meta +1 ⌘")

    def test_a_click_opens_the_menu_and_it_stays_after_the_button_comes_up(self):
        self.click("menu ⌘")
        self.wait_for("say hi")
        time.sleep(0.5)
        for text in ("say hi", "(h)", "rename", "(r)", "no key"):
            self.assertIsNotNone(self.where(text), text)

    def test_clicking_an_sh_item_runs_it_in_a_popup_in_the_panes_directory(self):
        self.click("menu ⌘")
        self.wait_for("say hi")
        self.click("say hi")
        self.wait_for(f"popup-says-{self.pane}-in-{self.work}-menu-{self.menu}", seconds=45)  # zsh -i: up to 11 s under load
        self.keys("x")  # read -k 1 returns, the code ends, the popup closes
        self.wait_for("popup-says", gone=True)

    def test_a_bad_first_line_still_opens_the_menu(self):
        """Its greyed label starts with "-", which display-menu read as a flag before the "--"."""
        self.menu.write_text("this line has no action\n" + MENU)
        self.click("menu ⌘")
        self.wait_for("line 1: this line has no action")
        self.wait_for("say hi")

    def test_the_menu_is_written_for_this_client_alone(self):
        """Red team 2026-11-17 #14: one file per server let two clients' clicks overwrite each other's menu."""
        pid = self.tmux(self.inner, "list-clients", "-F", "#{client_pid}")
        self.click("menu ⌘")
        self.wait_for("say hi")
        mine = Path(f"{self.inner}.click-menu-{pid}")
        self.assertTrue(mine.exists())
        self.assertFalse(Path(f"{self.inner}.click-menu").exists())

    def test_a_menu_that_cannot_be_built_says_why_instead_of_a_dead_click(self):
        self.menu.write_bytes(b"say hi  sh: true \xff\n")  # not UTF-8: reading it raises
        self.click("menu ⌘")
        self.wait_for("click menu failed: UnicodeDecodeError")
        self.wait_for("edit this menu")

    def test_an_sh_line_edited_while_the_menu_is_open_is_refused_not_run(self):
        """Red team 2026-11-17 #14: the click ran whatever sat on the line number by then."""
        self.click("menu ⌘")
        self.wait_for("say hi")
        text = self.menu.read_text()
        self.assertIn("say hi      h  sh: print popup-says", text)
        self.menu.write_text(text.replace("sh: print popup-says", "sh: print swapped-in; read -k 1 #"))
        self.click("say hi")
        self.wait_for("changed since the menu opened")
        time.sleep(1)
        self.assertNotIn("swapped-in", self.screen())
        self.assertNotIn("popup-says", self.screen())

    def test_a_tmux_item_runs_with_the_clicked_panes_formats_by_key_or_by_mouse(self):
        self.click("menu ⌘")
        self.wait_for("rename")
        self.keys("r")
        self.wait_for("riverboat clicked")
        self.click(" ⌘")  # the label before it is now the window's new name
        self.wait_for("no key")
        self.click("no key")
        for _ in range(40):
            if self.option("@clicked"):
                break
            time.sleep(0.05)
        self.assertEqual(self.option("@clicked"), "it's set")

    def test_an_edit_shows_on_the_next_click_with_nothing_reloaded(self):
        with self.menu.open("a") as f:
            f.write("added just now  sh: true\n")
        self.click("menu ⌘")
        self.wait_for("added just now")

    def test_a_bad_line_is_greyed_with_its_number_and_the_rest_works(self):
        with self.menu.open("a") as f:
            f.write("this line has no action\n")
        self.click("menu ⌘")
        self.wait_for("line 6: this line has no action")
        self.keys("r")
        self.wait_for("riverboat clicked")

    def test_no_file_still_offers_the_way_in(self):
        self.menu.unlink()
        self.click("menu ⌘")
        self.wait_for("edit this menu")

    def test_a_right_click_opens_the_file_in_the_editor(self):
        self.click("menu ⌘", RIGHT)
        self.wait_for("say hi      h  sh: print", seconds=20)  # the file's own text, in nvim
        self.keys(":q!")
        time.sleep(0.3)  # his nvim drops an Enter that arrives in the same burst as the command
        self.keys("Enter")
        self.wait_for("say hi      h  sh: print", gone=True)

    def test_the_rest_of_the_bar_is_as_it_was(self):
        """A left click on a window's name selects it; a right click there is the stock menu, not the editor."""
        first = self.tmux(self.inner, "list-windows", "-t", "riverboat", "-F", "#{window_index}:#{window_name}").split("\n")[0]
        self.click(first)
        for _ in range(40):
            active = self.tmux(self.inner, "display-message", "-p", "-t", "riverboat:", "#{window_index}:#{window_name}")
            if active == first:
                break
            time.sleep(0.05)
        self.assertEqual(active, first)
        self.click(first, RIGHT)
        self.wait_for("Swap Left")
        self.keys("Escape")
        self.tmux(self.inner, "select-window", "-t", self.pane)

    # 2026-11-20: "⇄ last session" left the bar for the pane border ("Status in border"), which takes no clicks
    # in tmux 3.4, so its click test was removed with that choice. prefix Tab still flips sessions.

    def test_binding_twice_wraps_once(self):
        before = self.tmux(self.inner, "list-keys", "-T", "root", "MouseDown3Status")
        self.assertEqual(before.count("mouse_status_range"), 1, before)
        subprocess.run([str(SCRIPT), "--bind"], env={**os.environ, "TMUX": f"{self.inner},0,0"})
        self.assertEqual(self.tmux(self.inner, "list-keys", "-T", "root", "MouseDown3Status"), before)



class Folder(unittest.TestCase):
    """Lucas, daily note 2026-11-18: "keep menu popup button next to folder". A long label (a pane's skill
    badges) sat between the ⌘ and the folder and pushed the folder off the bar ("luca")."""

    def test_the_button_sits_beside_the_folder_and_the_folder_is_whole(self):
        loader = SourceFileLoader("scene", str(HERE / "scene"))
        scene = importlib.util.module_from_spec(importlib.util.spec_from_loader("scene", loader))
        loader.exec_module(scene)
        s = scene.Scene()
        self.addCleanup(s.close)
        s.start("lucas2", "zsh", "sh", size=(153, 39), live_colours=True)
        folder = s.dir / "lucas"
        folder.mkdir()
        s.tmux("send-keys", "-t", "lucas2:", f"cd {folder}", "Enter")
        s.tmux("set-option", "-p", "-t", "lucas2:", "@skill_badge", "time-budget playwright collect +10")
        end, row = time.monotonic() + 8, ""
        while time.monotonic() < end:
            row = s.screen().split("\n")[-1].rstrip()  # the bar: one line, the last row (2026-11-20)
            if re.search(r"lucas[^A-Za-z0-9]*$", row):  # the folder pill ends in its cap glyph
                break
            time.sleep(0.2)
        self.assertTrue(re.search(r"lucas[^A-Za-z0-9]*$", row), f"the folder is cut or missing: {row!r}")
        between = row[row.rindex("⌘") + 1:row.rindex("lucas")]
        self.assertFalse(re.search(r"[A-Za-z0-9]", between), f"between the ⌘ and the folder: {between!r}")
        self.assertNotIn("time-budget playwright collect +10", row)  # the label is cut short, before the ⌘


if __name__ == "__main__":
    unittest.main(verbosity=2)
