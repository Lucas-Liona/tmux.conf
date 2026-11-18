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
from importlib.machinery import SourceFileLoader
import importlib.util
import types
_loader = SourceFileLoader("scene", str(Path(__file__).resolve().parent / "scene"))
scene = importlib.util.module_from_spec(importlib.util.spec_from_loader("scene", _loader))
_loader.exec_module(scene)
isolated = scene.isolated  # the one helper: scripts/scene

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
CONF = HERE.parent / "tmux.conf"
NEEDS = os.environ.get("NEEDS_BIN", str(Path.home() / ".local/bin/needs"))
LINES = re.compile(r"^(set -g (mouse|pane-border-format|pane-border-status|status-left) |bind -n MouseDown1Status )")
LEFT, MOVE = 0, 35
# What Claude Code's trust dialog leaves at the bottom of a pane (live, main:3.2, 2026-11-17 08:4x)
TRUST = ["Quick safety check: Is this a project you created or one you trust?", "❯ No, exit",
         "  Yes, I trust this folder", "Enter to confirm · Esc to cancel"]


def printing(lines, then=()):
    """A pane program that prints lines and stays."""
    return "printf '%s\\n' " + " ".join("'" + line.replace("'", "'\\''") + "'" for line in [*lines, *then]) + "; exec sleep 600"


class Guard(unittest.TestCase):
    """With TEST=1, needs refuses to touch live state; the suite refuses to start without temp paths. HOME and
    TMUX_TMPDIR point into a temp dir, so even a needs without the guard writes nowhere real."""

    def run_needs(self, home, **env):
        base = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE", "NEEDS_FILE", "NEEDS_CACHE")}
        return subprocess.run([NEEDS, "add", "a test ask"], capture_output=True, text=True,
                              env={**base, "HOME": home, "TMUX_TMPDIR": home, "TEST": "1", **env})

    def test_needs_under_test_refuses_an_unset_file_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as home:
            run = self.run_needs(home, TMUX=f"{home}/none,0,0")
            self.assertNotEqual(run.returncode, 0)
            self.assertFalse((Path(home) / ".cache").exists(), "it wrote the default (live) path")

    def test_needs_under_test_refuses_the_live_tmux_server(self):
        with tempfile.TemporaryDirectory() as home:
            run = self.run_needs(home, NEEDS_FILE=f"{home}/n.jsonl", NEEDS_CACHE=f"{home}/n.json")  # no TMUX
            self.assertNotEqual(run.returncode, 0)
            self.assertFalse((Path(home) / "n.jsonl").exists())

    def test_the_suite_refuses_to_start_without_temp_paths(self):
        live = str(Path.home() / ".cache/panel/needs.jsonl")
        with self.assertRaises(SystemExit):
            isolated({"NEEDS_FILE": live, "NEEDS_CACHE": "/tmp/x/n.json", "TMUX": "/tmp/x/s,0,0"}, ("NEEDS_FILE", "NEEDS_CACHE"))
        with self.assertRaises(SystemExit):
            isolated({"NEEDS_CACHE": "/tmp/x/n.json", "TMUX": "/tmp/x/s,0,0"}, ("NEEDS_FILE", "NEEDS_CACHE"))
        ok = isolated({"NEEDS_FILE": "/tmp/x/n.jsonl", "NEEDS_CACHE": "/tmp/x/n.json", "TMUX": "/tmp/x/s,0,0"},
                      ("NEEDS_FILE", "NEEDS_CACHE"))
        self.assertEqual(ok["TEST"], "1")


class Writers(unittest.TestCase):
    def test_two_writers_at_once_never_crash(self):
        """The bar's job and an agent's needs add both write the cache. With one shared .part file, the
        second rename found it already moved: 2 FileNotFoundError in 120 writes (2026-11-18)."""
        with tempfile.TemporaryDirectory() as tmp:
            env = isolated({**os.environ, "NEEDS_FILE": f"{tmp}/n.jsonl", "NEEDS_CACHE": f"{tmp}/n.json", "TMUX": f"{tmp}/none,0,0"},
                           ("NEEDS_FILE", "NEEDS_CACHE"))
            loop = f"for i in $(seq 60); do {NEEDS} --write; done"
            writers = [subprocess.Popen(["sh", "-c", loop], env=env, stderr=subprocess.PIPE, text=True) for _ in range(2)]
            errors = "".join(w.communicate()[1] for w in writers)
            self.assertNotIn("Traceback", errors)
            self.assertEqual(sorted(os.listdir(tmp)), ["n.json"])  # no temp file left behind


class Badge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scene = s = scene.Scene()  # TEST=1, a -L server of its own, every needs path inside s.dir
        cls.dir = types.SimpleNamespace(name=str(s.dir))
        cls.inner, cls.outer = s.inner, s.outer
        cls.asks, cls.cache = Path(s.env["NEEDS_FILE"]), Path(s.env["NEEDS_CACHE"])
        cls.opened, cls.clipped, cls.vault = s.opened, s.clipped, s.vault  # glz open, clip.exe: stubs
        s.env["NEEDS"] = NEEDS
        s.start("work", "home", "cat", size=(120, 30), lines=LINES,
                extra=['set -g @thm_red "#ed8796"', 'set -g @thm_crust "#181926"', "set -g status-interval 1"])
        cls.env = s.server_env()

    @classmethod
    def tearDownClass(cls):
        cls.scene.close()

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
        self.wait_for("! 1 ")  # status left, and the border line
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
        self.assertNotIn("! ", self.screen()[0])  # the border keeps its row (gen is on it) but loses the badge
        self.assertEqual(self.needs().stdout.strip(), "nothing waits on you")

    def test_the_badge_is_on_the_bar_not_the_border_and_it_opens_the_list(self):
        """Lucas, 2026-11-18, needs 49: "A": with the bar on top, the border copy of the badge went."""
        self.needs("add", "a decision only Lucas makes")
        self.wait_for("! 1 ")
        self.assertFalse(any("! 1" in row for row in self.screen() if row.lstrip().startswith("─")), self.screen()[:2])
        self.click("! 1", self.badge())
        self.wait_for("a decision only Lucas makes")

    def open_card(self, text):
        """The status badge, then the line holding text: its card opens in a popup."""
        self.wait_for("! ")
        self.click("! ", self.badge())
        self.wait_for(text)
        self.click(text)
        self.wait_for("q close", seconds=10)

    def log(self, path):
        return path.read_text() if path.exists() else ""

    def nvims(self):
        """nvim processes under the throwaway server: one Lucas or an agent starts elsewhere must not fail a pin."""
        parent = {}
        for proc in Path("/proc").iterdir():
            try:
                fields = (proc / "stat").read_text().rsplit(")", 1)[1].split()
                parent[proc.name] = (fields[1], (proc / "comm").read_text().strip())
            except (OSError, IndexError):
                continue
        server = self.tmux(self.inner, "display-message", "-p", "#{pid}")
        def under(pid):
            while pid in parent and pid != "1":
                pid = parent[pid][0]
                if pid == server:
                    return True
            return False
        return {pid for pid, (_, comm) in parent.items() if comm == "nvim" and under(pid)}

    def test_tiers_order_the_list_the_menu_and_the_text(self):
        for tier, text in (("3", "any-time thing"), ("0", "the risky one"), ("2", "a review"), ("1", "a yes or no")):
            self.needs("add", text, "--tier", tier)
        self.needs("add", "no tier given")  # 2, after the older tier-2 ask
        self.needs("--write")
        items = json.loads(self.needs("--json").stdout)["items"]
        self.assertEqual([(i["tier"], i["text"]) for i in items], [(0, "the risky one"), (1, "a yes or no"),
                         (2, "a review"), (2, "no tier given"), (3, "any-time thing")])
        text = self.needs().stdout
        self.assertLess(text.index("0 · do first"), text.index("the risky one"))
        self.assertLess(text.index("the risky one"), text.index("1 · one word from you"))
        self.assertLess(text.index("2 · read or review"), text.index("3 · any time"))
        self.click("! 5", self.badge())
        self.wait_for("any-time thing")
        rows = "\n".join(self.screen())
        order = ["0 · do first", "the risky one", "1 · one word from you", "a yes or no", "2 · read or review",
                 "a review", "no tier given", "3 · any time", "any-time thing"]
        self.assertEqual([rows.index(x) for x in order], sorted(rows.index(x) for x in order), rows)
        self.assertEqual(self.needs("add", "x", "--tier", "4").returncode, 2)

    def test_set_changes_an_ask(self):
        self.needs("add", "move this up")
        self.assertEqual(self.needs("set", "1", "--tier", "0", "--why", "it got urgent").returncode, 0)
        self.needs("--write")
        item = json.loads(self.needs("--json").stdout)["items"][0]
        self.assertEqual((item["tier"], item["why"], item["text"]), (0, "it got urgent", "move this up"))
        self.assertEqual(self.needs("set", "9", "--tier", "1").returncode, 1)  # not open

    def test_the_card_is_the_decision_and_its_action_opens_or_copies(self):
        self.needs("add", "merge the homelab PR", "--tier", "0", "--why", "thin pool at 86.8 percent",
                   "--recommend", "merge it today", "--action", "https://example.com/pull/186")
        self.open_card("merge the homelab PR")
        for text in ("#1 · 0 · do first", "thin pool at 86.8 percent", "merge it today", "https://example.com/pull/186",
                     "[a] open in the browser"):
            self.wait_for(text)
        self.keys("a")
        self.wait_for("opened in the browser")
        self.assertIn("https://example.com/pull/186", self.log(self.opened))
        self.keys("q")
        self.wait_for("q close", gone=True)
        self.needs("set", "1", "--action", "make apply")
        self.open_card("merge the homelab PR")
        self.wait_for("[a] copy the command")
        self.keys("a")
        self.wait_for("command copied")
        self.assertIn("make apply".encode("utf-16-le"), self.clipped.read_bytes())  # copied, never run
        self.keys("q")

    def test_a_png_opens_in_windows_and_never_in_nvim(self):
        """2026-11-18: choosing an item opened 9-phone-floor-after.png in nvim, raw bytes in a popup."""
        shot = Path(self.dir.name) / "9-phone-floor-after.png"
        shot.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 8)
        before = self.nvims()
        self.needs("add", "look at the phone floor", "--where", str(shot))
        self.open_card("look at the phone floor")
        self.wait_for("Windows")  # what [1] will do, said on the card
        self.keys("1")
        self.wait_for("opened in Windows")
        self.assertIn(str(shot), self.log(self.opened))
        self.assertEqual(self.nvims() - before, set())
        self.assertNotIn("PNG", "\n".join(self.screen()[:-1]))  # no bytes drawn
        self.keys("q")

    def test_evidence_opens_by_type_text_in_less_vault_notes_in_obsidian(self):
        note, binary, page = Path(self.dir.name) / "summary.md", Path(self.dir.name) / "blob.bin", self.vault / "Plan.md"
        note.write_text("pick-one-of-these-three\n")
        binary.write_bytes(bytes(range(256)))
        page.write_text("# plan\n")
        before = self.nvims()
        self.needs("add", "read the evidence", "--where", str(note), "--where", str(page), "--where", str(binary),
                   "--where", "https://example.com/x")
        self.open_card("read the evidence")
        self.wait_for("read here")
        self.keys("1")
        self.wait_for("pick-one-of-these-three", seconds=10)  # less, in the popup
        self.assertEqual(note.read_text(), "pick-one-of-these-three\n")
        self.keys("q")
        self.wait_for("q close")
        for key, target in (("2", str(page)), ("3", str(binary)), ("4", "https://example.com/x")):
            self.keys(key)
            end = time.time() + 5
            while time.time() < end and target not in self.log(self.opened):
                time.sleep(0.05)
            self.assertIn(target, self.log(self.opened))
        self.assertIn("Obsidian", "\n".join(self.screen()))
        self.assertEqual(self.nvims() - before, set())
        self.keys("q")

    def test_the_status_job_keeps_n_without_anyone_running_needs(self):
        """No --write from the test: status-left's #() job (scripts/needs-job) does it."""
        self.dialog()
        end = time.time() + 15
        while time.time() < end and self.tmux(self.inner, "show-options", "-gqv", "@needs_count") != "1":
            time.sleep(0.2)
        self.assertEqual(self.tmux(self.inner, "show-options", "-gqv", "@needs_count"), "1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
