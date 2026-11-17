#!/usr/bin/env python3
"""Pins for the ! badge: the needs command (~/.local/bin/needs, chezmoi), its badge and its click menu.

    python3 ~/.config/tmux/scripts/needs-check.py

Two throwaway tmux servers, never the live one. The inner one gets tmux.conf's own lines for the
badge and the clicks (scripts pointed beside this file), and needs files of its own (NEEDS_FILE,
NEEDS_CACHE). The outer one holds a client of it in a pane: bytes sent there are the mouse.
A "dialog" here is a pane that prints the lines a trust dialog draws, then sleeps.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
CONF = HERE.parent / "tmux.conf"
NEEDS = os.environ.get("NEEDS_BIN", str(Path.home() / ".local/bin/needs"))
LINES = re.compile(r"^(set -g (mouse|pane-border-format|status-left) |bind -n MouseDown1Status )")
LEFT, MOVE = 0, 35
# What Claude Code's trust dialog leaves at the bottom of a pane (live, main:3.2, 2026-11-17 08:4x)
TRUST = ["Quick safety check: Is this a project you created or one you trust?", "❯ No, exit",
         "  Yes, I trust this folder", "Enter to confirm · Esc to cancel"]


def printing(lines, then=()):
    """A pane program that prints lines and stays."""
    return "printf '%s\\n' " + " ".join("'" + line.replace("'", "'\\''") + "'" for line in [*lines, *then]) + "; exec sleep 600"


class Badge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        tmp = Path(cls.dir.name)
        cls.inner, cls.outer = str(tmp / "inner"), str(tmp / "outer")
        cls.asks, cls.cache = tmp / "needs.jsonl", tmp / "needs.json"
        cls.env = {"NEEDS": NEEDS, "NEEDS_FILE": str(cls.asks), "NEEDS_CACHE": str(cls.cache)}
        lines = [line.replace("~/.config/tmux/scripts/", f"{HERE}/")
                 for line in CONF.read_text().splitlines() if LINES.match(line)]
        assert len(lines) == 4, lines
        conf = tmp / "badge.conf"
        conf.write_text("\n".join(lines + ['set -g @thm_red "#ed8796"', 'set -g @thm_crust "#181926"',
                                           "set -s escape-time 20", "set -g status-interval 1"]) + "\n")
        cls.tmux(cls.inner, "-f", "/dev/null", "new-session", "-d", "-s", "work", "-n", "home", "-x", "120", "-y", "30",
                 "cat", env=cls.env)
        cls.tmux(cls.inner, "source-file", str(conf))
        cls.tmux(cls.outer, "-f", "/dev/null", "new-session", "-d", "-s", "terminal", "-x", "120", "-y", "30",
                 f"env -u TMUX tmux -S {cls.inner} attach -t work")

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

    def needs(self, *args):
        env = {**os.environ, **self.env, "TMUX": f"{self.inner},0,0"}
        env.pop("TMUX_PANE", None)
        return subprocess.run([NEEDS, *args], capture_output=True, text=True, env=env)

    def count(self):
        self.needs("--write")
        return int(self.tmux(self.inner, "show-options", "-gqv", "@needs_count") or 0)

    def setUp(self):
        self.asks.unlink(missing_ok=True)
        self.cache.unlink(missing_ok=True)
        self.tmux(self.inner, "switch-client", "-t", "work:home")  # first: killing the client's session detaches it
        for session in self.tmux(self.inner, "list-sessions", "-F", "#{session_name}").split("\n"):
            if session != "work":
                self.tmux(self.inner, "kill-session", "-t", session)
        self.assertEqual(self.count(), 0)

    def keys(self, *keys):
        self.tmux(self.outer, "send-keys", "-t", "terminal:", *keys)

    def tearDown(self):
        self.keys("Escape")
        time.sleep(0.1)

    def screen(self):
        return self.tmux(self.outer, "capture-pane", "-p", "-t", "terminal:").split("\n")

    def where(self, text):
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

    def click(self, text, at=None):
        time.sleep(0.35)  # a press within 300 ms of the last one is tmux's double click
        at = at or self.where(text)
        self.assertIsNotNone(at, f"{text!r} not on screen:\n" + "\n".join(self.screen()))
        for button, down in ((MOVE, True), (LEFT, True), (LEFT, False)):
            self.tmux(self.outer, "send-keys", "-t", "terminal:", "-l",
                      f"\033[<{button};{at[0]};{at[1]}{'M' if down else 'm'}")
            time.sleep(0.05)

    def badge(self):
        """The status-left badge, on the client's last row (the border line has one too, at the top)."""
        rows = self.screen()
        self.assertIn("! ", rows[-1])
        return rows[-1].index("! ") + 2, len(rows)

    def dialog(self, lines=TRUST, then=()):
        """A pane in a session of its own that ends in a dialog; its pane id."""
        return self.tmux(self.inner, "new-session", "-d", "-P", "-F", "#{pane_id}", "-s", "elsewhere", "-n", "asker",
                         "-x", "120", "-y", "30", printing(lines, then))

    def test_a_pane_ending_in_a_trust_dialog_raises_n_by_one_and_the_menu_jumps_to_it(self):
        pane = self.dialog()
        time.sleep(0.3)
        self.assertEqual(self.count(), 1)
        item = json.loads(self.needs("--json").stdout)["items"][0]
        self.assertEqual((item["kind"], item["pane"], item["where"]), ("trust", pane, "elsewhere:0.0"))
        self.wait_for("! 1 ")  # status left, and the border line now on
        self.assertEqual(self.tmux(self.inner, "show-options", "-gqv", "pane-border-status"), "top")
        self.click("! 1", self.badge())
        self.wait_for("trust dialog in asker")
        self.click("trust dialog in asker")
        end = time.time() + 5
        while time.time() < end and self.tmux(self.inner, "list-clients", "-F", "#{client_session}") != "elsewhere":
            time.sleep(0.05)
        self.assertEqual(self.tmux(self.inner, "list-clients", "-F", "#{client_session} #{pane_id}"), f"elsewhere {pane}")

    def test_a_dialog_quoted_above_an_input_box_is_not_counted(self):
        self.dialog(then=["─" * 40, "❯ ", "─" * 40, "  ? for shortcuts"])
        time.sleep(0.3)
        self.assertEqual(self.count(), 0)

    def test_a_permission_prompt_and_a_codex_approval_count(self):
        self.dialog(["Bash command", "  rm -rf build", "Do you want to proceed?", "❯ 1. Yes",
                     "  2. Yes, and don't ask again for rm commands", "  3. No, and tell Claude what to do differently (esc)"])
        self.tmux(self.inner, "new-window", "-d", "-t", "elsewhere:", printing(
            ["Would you like to run the following command?", "  $ make", "› 1. Yes, just this once", "  2. No"]))
        time.sleep(0.3)
        self.needs("--write")  # --json reuses a scan under 5 s old
        self.assertEqual(sorted(i["kind"] for i in json.loads(self.needs("--json").stdout)["items"]), ["codex", "permission"])

    def test_add_and_done_change_n_and_the_text_says_it(self):
        made = self.needs("add", "pick the Zebar colour", "--where", "/tmp/nowhere.md")
        self.assertEqual((made.returncode, made.stdout.strip()), (0, "1"))
        self.assertEqual(self.tmux(self.inner, "show-options", "-gqv", "@needs_count"), "1")  # at once, not on the next tick
        self.assertIn("pick the Zebar colour", self.needs().stdout)
        self.needs("add", "second")
        self.assertEqual(self.count(), 2)
        self.assertEqual(self.needs("done", "1").returncode, 0)
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.needs("done", "1").returncode, 1)  # not open any more
        self.needs("done", "2")
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.tmux(self.inner, "show-options", "-gv", "pane-border-status"), "off")  # the row given back
        self.assertEqual(self.needs().stdout.strip(), "nothing waits on you")

    def test_the_badge_is_on_the_border_line_and_the_status_badge_opens_the_list(self):
        self.needs("add", "a decision only Lucas makes")
        self.wait_for("! 1 ")
        self.assertTrue(self.screen()[0].startswith("── ! 1 "))  # top left of the pane's border line
        self.click("! 1", self.badge())
        self.wait_for("a decision only Lucas makes")

    def test_an_ask_with_a_file_opens_it_read_only_in_a_popup(self):
        note = Path(self.dir.name) / "decide.md"
        note.write_text("pick-one-of-these-three\n")
        self.needs("add", "read the note", "--where", str(note))
        self.wait_for("! 1 ")
        self.click("! 1", self.badge())
        self.wait_for("read the note")
        self.click("read the note")
        self.wait_for("pick-one-of-these-three", seconds=30)  # nvim with Lucas's config, in the popup
        self.assertEqual(note.read_text(), "pick-one-of-these-three\n")
        self.keys(":q", "Enter")
        self.wait_for("pick-one-of-these-three", gone=True, seconds=10)

    def test_an_ask_with_nowhere_shows_its_text(self):
        self.needs("add", "say yes or no to the plan")
        self.wait_for("! 1 ")
        self.click("! 1", self.badge())
        self.wait_for("say yes or no to the plan")
        self.click("say yes or no to the plan")
        self.wait_for("say yes or no to the plan")  # now the message on the status line, not the menu
        self.assertNotIn("waiting on you", "\n".join(self.screen()))  # the menu closed
        # -N -d 8000: keys do not clear it, so wait it out, or it covers the next pin's status line
        self.wait_for("say yes or no to the plan", gone=True, seconds=12)

    def test_the_status_job_keeps_n_without_anyone_running_needs(self):
        """No --write from the test: status-left's #() job (scripts/needs-job) does it."""
        self.dialog()
        end = time.time() + 15
        while time.time() < end and self.tmux(self.inner, "show-options", "-gqv", "@needs_count") != "1":
            time.sleep(0.2)
        self.assertEqual(self.tmux(self.inner, "show-options", "-gqv", "@needs_count"), "1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
