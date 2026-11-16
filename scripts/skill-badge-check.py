#!/usr/bin/env python3
"""Pins for the top-right label: the terminal title tmux.conf builds, and the badge text in it.

    python3 ~/.config/tmux/scripts/skill-badge-check.py

Two throwaway tmux servers, never the live one. The inner one gets only tmux.conf's title lines
and a session named riverboat; the outer one holds a client of it in a pane and stands in for
WezTerm: the title the inner server sends its terminal is the outer pane's title.
"""
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
BADGE = HERE / "skill-badge"
CONF = HERE.parent / "tmux.conf"
TITLE_LINES = ("set -g set-titles", "set -g @title_role")


class Title(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        cls.inner, cls.outer = (str(Path(cls.dir.name) / name) for name in ("inner", "outer"))
        lines = [line for line in CONF.read_text().splitlines() if line.startswith(TITLE_LINES)]
        assert len(lines) == 3, lines
        conf = Path(cls.dir.name) / "title.conf"
        conf.write_text("\n".join(lines) + "\n")
        cls.tmux(cls.inner, "-f", "/dev/null", "new-session", "-d", "-s", "riverboat", "-x", "120", "-y", "30", "cat")
        cls.tmux(cls.inner, "source-file", str(conf))
        cls.tmux(cls.outer, "-f", "/dev/null", "new-session", "-d", "-s", "wezterm", "-x", "120", "-y", "30",
                 f"env -u TMUX tmux -S {cls.inner} attach -t riverboat")

    @classmethod
    def tearDownClass(cls):
        for sock in (cls.outer, cls.inner):
            cls.tmux(sock, "kill-server")
        cls.dir.cleanup()

    @staticmethod
    def tmux(sock, *args):
        env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")}
        return subprocess.run(["tmux", "-S", sock, *args], capture_output=True, text=True, env=env).stdout.strip()

    def setUp(self):
        """One fresh window per pin, in view, with a program's name and no badge."""
        self.pane = self.tmux(self.inner, "new-window", "-t", "riverboat:", "-P", "-F", "#{pane_id}", "cat")

    def badge(self, *args, stdin=None):
        """The real script against the inner server; as a hook it finds its pane through TMUX_PANE."""
        env = {**os.environ, "TMUX": f"{self.inner},0,0", "TMUX_PANE": self.pane}
        return subprocess.run([str(BADGE), *args], input=stdin, capture_output=True, text=True, env=env)

    def rename(self, name):
        self.tmux(self.inner, "rename-window", "-t", self.pane, name)

    def assertTitle(self, want):
        got = None
        for _ in range(40):
            got = self.tmux(self.outer, "display-message", "-p", "-t", "wezterm:", "#{pane_title}")
            if got == want:
                return
            time.sleep(0.05)
        self.assertEqual(got, want)

    def test_nothing_known_says_only_where(self):
        self.assertTitle("· riverboat")

    def test_a_named_window_is_the_role(self):
        self.rename("control")
        self.assertTitle("riverboat · control")

    def test_role_and_skills(self):
        self.rename("control")
        self.badge("add", self.pane, "meta")
        self.assertTitle("riverboat · control · meta")

    def test_skills_without_a_role(self):
        self.badge("add", self.pane, "meta")
        self.assertTitle("riverboat · meta")

    def test_the_three_newest_skills_then_a_count(self):
        for skill in ("where-things-live", "tmux-wsl", "coordinate", "control", "meta"):
            self.badge("add", self.pane, skill)
        self.assertTitle("riverboat · meta control coordinate +2")

    def test_a_preset_leads_its_skills(self):
        self.badge("set", self.pane, "--preset", "red-team", "debug", "tmux-wsl")
        self.assertTitle("riverboat · red-team: tmux-wsl debug")

    def test_a_name_with_a_slash_is_no_role(self):
        """WezTerm drops a title with a slash in it, and the bar would go blank."""
        self.rename("/home/lucas/Personal-Website")
        self.assertTitle("· riverboat")
        self.badge("add", self.pane, "meta")
        self.assertTitle("riverboat · meta")

    def test_a_window_named_as_its_session_is_no_role(self):
        self.rename("riverboat")
        self.assertTitle("· riverboat")

    def test_the_title_follows_the_window_in_view(self):
        self.rename("control")
        self.badge("add", self.pane, "meta")
        self.assertTitle("riverboat · control · meta")
        other = self.tmux(self.inner, "new-window", "-t", "riverboat:", "-P", "-F", "#{pane_id}", "cat")
        self.assertTitle("· riverboat")
        self.tmux(self.inner, "select-window", "-t", self.pane)
        self.assertTitle("riverboat · control · meta")
        self.tmux(self.inner, "kill-pane", "-t", other)

    def test_the_hook_adds_a_skill_claude_loaded_or_lucas_typed_and_clears_at_the_end(self):
        """The three events as Claude Code 2.1.325 documents them (code.claude.com/docs/en/hooks)."""
        self.rename("control")
        loaded = {"hook_event_name": "PostToolUse", "tool_name": "Skill", "tool_input": {"skill": "coordinate"}}
        typed = {"hook_event_name": "UserPromptExpansion", "expansion_type": "slash_command", "command_name": "meta",
                 "command_args": "", "command_source": "user", "prompt": "/meta"}
        self.assertEqual(self.badge("hook", stdin=json.dumps(loaded)).returncode, 0)
        self.assertTitle("riverboat · control · coordinate")
        self.badge("hook", stdin=json.dumps(typed))
        self.assertTitle("riverboat · control · meta coordinate")
        self.badge("hook", stdin=json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {}}))
        self.badge("hook", stdin="not json")
        self.assertTitle("riverboat · control · meta coordinate")
        self.badge("hook", stdin=json.dumps({"hook_event_name": "SessionEnd", "reason": "other"}))
        self.assertTitle("riverboat · control")


if __name__ == "__main__":
    unittest.main(verbosity=2)
