#!/usr/bin/env python3
"""Pins for prefix+O (scripts/open-spec): which panes get Claude Code's two keys, and which get none.

    python3 ~/.config/tmux/scripts/open-spec-check.py

Two throwaway tmux servers, never the live one, and no real claude: the pane's program is a stub
that writes down every byte it is sent. The inner server gets tmux.conf's own bind line; the outer
one holds a client of it in a pane, so the key is pressed as a key and the status line can be read.
What claude then does with the keys is not pinned here: that was one real run, in the commit.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
CONF = HERE.parent / "tmux.conf"
RECORDER = """#!/usr/bin/env python3
import os, sys, termios, tty
tty.setraw(0)
with open(sys.argv[1], "ab", buffering=0) as log:
    log.write(b"up\\n")
    while True:
        log.write(os.read(0, 64))
"""
RIGHT_THEN_CTRL_G = b"\x1b[C\x07"
WRAPPER = 'trap : INT; "$0" "$@"; exec zsh'  # what prefix+T starts claude under (handoff.py)


class Keys(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls.dir.name)
        cls.inner, cls.outer = str(cls.tmp / "inner"), str(cls.tmp / "outer")
        for name in ("claude", "recorder"):  # the same program under two names: only the name differs
            (cls.tmp / name).write_text(RECORDER)
            (cls.tmp / name).chmod(0o755)
        bind = [line.replace("~/.config/tmux/scripts/", f"{HERE}/")
                for line in CONF.read_text().splitlines() if line.startswith('bind -N "Open the prompt suggestion')]
        assert len(bind) == 1, bind
        (cls.tmp / "bind.conf").write_text(bind[0] + "\n")
        cls.tmux(cls.inner, "-f", "/dev/null", "new-session", "-d", "-s", "pins", "-x", "100", "-y", "20", "cat")
        cls.tmux(cls.inner, "source-file", str(cls.tmp / "bind.conf"))
        cls.tmux(cls.outer, "-f", "/dev/null", "new-session", "-d", "-s", "terminal", "-x", "100", "-y", "20",
                 f"env -u TMUX tmux -S {cls.inner} attach -t pins")

    @classmethod
    def tearDownClass(cls):
        for sock in (cls.outer, cls.inner):
            cls.tmux(sock, "kill-server")
        cls.dir.cleanup()

    @staticmethod
    def tmux(sock, *args):
        env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")}
        return subprocess.run(["tmux", "-S", sock, *args], capture_output=True, text=True, env=env).stdout.rstrip("\n")

    def window(self, *command):
        """A window in view running command, its recorder up; the bytes it has been sent since are sent()."""
        self.log = self.tmp / f"{self.id().rsplit('.', 1)[1]}.log"
        pane = self.tmux(self.inner, "new-window", "-t", "pins:", "-P", "-F", "#{pane_id}", *command, str(self.log))
        self.addCleanup(self.tmux, self.inner, "kill-pane", "-t", pane)
        for _ in range(100):
            if self.log.exists() and self.log.read_bytes().startswith(b"up\n"):
                return
            time.sleep(0.05)
        self.fail(f"{command} did not start")

    def sent(self):
        return self.log.read_bytes()[len(b"up\n"):]

    def press_prefix_O(self):
        self.tmux(self.outer, "send-keys", "-t", "terminal:", "C-b", "O")

    def status_line(self):
        return self.tmux(self.outer, "capture-pane", "-p", "-t", "terminal:").split("\n")[-1]

    def wait(self, what, said):
        for _ in range(60):
            if what():
                return
            time.sleep(0.05)
        self.fail(said())

    def test_claude_in_front_gets_right_then_ctrl_g(self):
        self.window(str(self.tmp / "claude"))
        self.press_prefix_O()
        self.wait(lambda: self.sent() == RIGHT_THEN_CTRL_G, lambda: f"sent {self.sent()!r}")
        self.assertNotIn("open spec", self.status_line())

    def test_claude_under_the_window_wrapper_gets_them_too(self):
        """tmux calls that pane's command zsh; the terminal's foreground group still holds claude."""
        self.window(shutil.which("zsh") or "zsh", "-c", WRAPPER, str(self.tmp / "claude"))
        self.press_prefix_O()
        self.wait(lambda: self.sent() == RIGHT_THEN_CTRL_G, lambda: f"sent {self.sent()!r}")

    def test_any_other_program_gets_a_message_and_no_keys(self):
        self.window(str(self.tmp / "recorder"))
        self.press_prefix_O()
        self.wait(lambda: "open spec: no claude in this pane" in self.status_line(), self.status_line)
        time.sleep(0.3)
        self.assertEqual(self.sent(), b"")


if __name__ == "__main__":
    unittest.main(verbosity=2)
