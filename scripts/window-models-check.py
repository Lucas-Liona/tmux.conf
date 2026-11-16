#!/usr/bin/env python3
"""Pins for the model beside a Claude window's name (scripts/window-models).

    python3 ~/.config/tmux/scripts/window-models-check.py

A throwaway tmux server, never the live one, and a temp directory standing in for ~/.claude
(WINDOW_MODELS_CLAUDE). The "claude" in a pane is a stub of that name that only waits; its session
file and transcript are written by the pin, shaped like Claude Code 2.1.325's.
"""
import json
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
SCRIPT = HERE / "window-models"
CONF = HERE.parent / "tmux.conf"
WRAPPER = 'trap : INT; "$0" "$@"; exec zsh'  # what prefix+T starts claude under (handoff.py)


def reply(model, text="ok"):
    return json.dumps({"type": "assistant", "isSidechain": False, "message": {
        "model": model, "role": "assistant", "content": [{"type": "text", "text": text}]}}, separators=(",", ":"))


def switched(answer):
    """What /model leaves in the transcript: its answer, as a user record."""
    return json.dumps({"type": "user", "message": {"role": "user", "content": f"<local-command-stdout>{answer}</local-command-stdout>"}},
                      separators=(",", ":"))


class Models(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="window-models-check-"))
        self.sock = str(self.tmp / "tmux.sock")
        self.claude_dir = self.tmp / "dot-claude"
        (self.claude_dir / "sessions").mkdir(parents=True)
        stub = self.tmp / "claude"
        stub.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(600)\n")
        stub.chmod(0o755)
        self.tmux("-f", "/dev/null", "new-session", "-d", "-s", "pin", "-n", "plain", "cat")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(self.tmux, "kill-server")
        self.sessions = 0

    def tmux(self, *args):
        env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")}
        return subprocess.run(["tmux", "-S", self.sock, *args], capture_output=True, text=True, env=env).stdout.rstrip("\n")

    def claude_window(self, name, *lines, wrapped=False, cwd="/home/lucas/some.project"):
        """A window whose pane runs the stub claude, with a session file and a transcript of lines.
        Returns (window id, the transcript)."""
        command = ["zsh", "-c", WRAPPER, str(self.tmp / "claude")] if wrapped else [str(self.tmp / "claude")]
        window, tty = self.tmux("new-window", "-d", "-t", "pin:", "-n", name, "-P", "-F", "#{window_id} #{pane_tty}", *command).split()
        for _ in range(100):  # the stub's pid: the one process on that terminal with claude in its command line
            rows = subprocess.run(["ps", "-o", "pid=,args=", "-t", tty], capture_output=True, text=True).stdout.splitlines()
            pids = [row.split()[0] for row in rows if "python3" in row.split()[1] and row.split()[2:3] == [str(self.tmp / "claude")]]
            if pids:
                break
            time.sleep(0.05)
        self.sessions += 1
        session = f"00000000-0000-4000-8000-{self.sessions:012d}"
        (self.claude_dir / "sessions" / f"{pids[0]}.json").write_text(json.dumps({"pid": int(pids[0]), "sessionId": session, "cwd": cwd}))
        project = self.claude_dir / "projects" / "".join(c if c.isalnum() else "-" for c in cwd)
        project.mkdir(parents=True, exist_ok=True)
        transcript = project / f"{session}.jsonl"
        transcript.write_text("".join(line + "\n" for line in lines))
        return window, transcript

    def refresh(self):
        run = subprocess.run([str(SCRIPT)], capture_output=True, text=True,
                             env={**os.environ, "TMUX": f"{self.sock},0,0", "WINDOW_MODELS_CLAUDE": str(self.claude_dir)})
        self.assertEqual((run.returncode, run.stdout, run.stderr), (0, "", ""))  # a #() job: what it prints is drawn

    def model(self, window):
        return self.tmux("show-options", "-wqv", "-t", window, "@claude_model")

    def test_a_claude_window_shows_its_model_and_any_other_window_nothing(self):
        window, _ = self.claude_window("coord", reply("claude-opus-5-5"))
        self.refresh()
        self.assertEqual(self.model(window), "opus")
        self.assertEqual(self.model("pin:plain"), "")
        text = [line for line in CONF.read_text().splitlines() if line.startswith("set -g @catppuccin_window_text ")]
        self.assertEqual(len(text), 1)
        drawn = text[0].split(" ", 3)[3].strip('"')  # the window name's format, as tmux.conf gives it to catppuccin
        self.assertEqual(self.tmux("display-message", "-p", "-t", window, drawn), " coord · opus")
        self.assertEqual(self.tmux("display-message", "-p", "-t", "pin:plain", drawn), " plain")

    def test_the_last_word_in_the_transcript_wins(self):
        window, transcript = self.claude_window("lane", reply("claude-opus-5-5"), reply("claude-fable-5-2"))
        self.refresh()
        self.assertEqual(self.model(window), "fable")
        for line, want in [(switched("Set model to `Sonnet 5.5` and saved as your default for new sessions"), "sonnet"),
                           (reply("claude-haiku-5-5-20261001"), "haiku"),
                           (switched("Kept model as `Opus 5.5`"), "opus")]:
            with transcript.open("a") as f:
                f.write(line + "\n")
            self.refresh()
            self.assertEqual(self.model(window), want)

    def test_only_a_record_of_its_own_counts(self):
        """Not a reply that quotes one, not a subagent's, not the placeholder model of a failed call."""
        quoting = reply("claude-opus-5-5", 'the file says {"type":"assistant","message":{"model":"claude-haiku-5-5"}} and'
                                           ' "content":"<local-command-stdout>Set model to `Sonnet 5.5`')
        side = json.dumps({"type": "assistant", "isSidechain": True, "message": {"model": "claude-haiku-5-5"}}, separators=(",", ":"))
        window, _ = self.claude_window("lane", quoting, side, reply("<synthetic>"))
        self.refresh()
        self.assertEqual(self.model(window), "opus")

    def test_a_long_record_after_the_last_reply_does_not_hide_it(self):
        pasted = json.dumps({"type": "user", "message": {"role": "user", "content": "x" * 200_000}}, separators=(",", ":"))
        window, _ = self.claude_window("lane", reply("claude-opus-5-5"), pasted)
        self.refresh()
        self.assertEqual(self.model(window), "opus")

    def test_no_reply_yet_shows_nothing_and_a_claude_that_left_is_cleared(self):
        quiet, _ = self.claude_window("fresh")
        window, _ = self.claude_window("coord", reply("claude-opus-5-5"))
        self.refresh()
        self.assertEqual((self.model(quiet), self.model(window)), ("", "opus"))
        self.tmux("respawn-pane", "-k", "-t", window, "cat")  # claude gone, the window stays
        time.sleep(0.3)
        self.refresh()
        self.assertEqual(self.model(window), "")

    def test_claude_under_the_window_wrapper_and_a_directory_with_dots(self):
        window, _ = self.claude_window("lane", reply("claude-fable-5-2"), wrapped=True, cwd="/home/lucas/.workmux/tmux/tmux")
        self.refresh()
        self.assertEqual(self.model(window), "fable")

    def test_a_transcript_kept_under_another_directorys_name_is_still_found(self):
        """A session resumed from a different directory: the file is where it was first written."""
        window, transcript = self.claude_window("lane", reply("claude-opus-5-5"))
        elsewhere = self.claude_dir / "projects" / "-somewhere-else"
        elsewhere.mkdir()
        transcript.rename(elsewhere / transcript.name)
        self.refresh()
        self.assertEqual(self.model(window), "opus")


    def test_adopt_writes_the_label_into_a_windows_own_format_once_and_only_there(self):
        """workmux's copy: the format as it was, its status icon after it."""
        label = "#{?#{@claude_model}, · #{@claude_model},}"
        text = [line for line in CONF.read_text().splitlines() if line.startswith("set -g @catppuccin_window_text ")]
        self.assertEqual(text[0].split(" ", 3)[3], f'" #W{label}"')  # the script's label is tmux.conf's
        own = "#[fg=red]#I #[fg=blue] #W#[none]#{?@workmux_status, #{@workmux_status},}"
        window, _ = self.claude_window("coord", reply("claude-opus-5-5"))
        for option in ("window-status-format", "window-status-current-format"):
            self.tmux("set-option", "-w", "-t", window, option, own)
        adopt = lambda: subprocess.run([str(SCRIPT), "--adopt"], env={**os.environ, "TMUX": f"{self.sock},0,0"})
        adopt()
        want = f"#[fg=red]#I #[fg=blue] #W{label}#[none]#{{?@workmux_status, #{{@workmux_status}},}}"
        for option in ("window-status-format", "window-status-current-format"):
            self.assertEqual(self.tmux("show-options", "-wqv", "-t", window, option), want)
            self.assertEqual(self.tmux("show-options", "-wqv", "-t", "pin:plain", option), "")  # no copy: none made
        adopt()
        self.assertEqual(self.tmux("show-options", "-wqv", "-t", window, "window-status-format"), want)
        self.refresh()
        self.tmux("set-option", "-w", "-t", window, "@workmux_status", "done")
        self.assertEqual(self.tmux("display-message", "-p", "-t", window, own.replace(" #W", " #W" + label)[len("#[fg=red]#I #[fg=blue]"):]
                                   .replace("#[none]", "")), " coord · opus done")


if __name__ == "__main__":
    unittest.main(verbosity=2)
