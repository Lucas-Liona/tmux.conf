#!/usr/bin/env python3
"""Pins for the model beside a Claude window's name (scripts/window-models and its job).

    python3 ~/.config/tmux/scripts/window-models-check.py

Throwaway tmux servers, never the live one, and a temp directory standing in for ~/.claude
(WINDOW_MODELS_CLAUDE). The "claude" in a pane is a stub of that name that only waits; its session
file and transcript are written by the pin, shaped like Claude Code 2.1.325's. The pins that need
the bar drawn hold a client of the server in a pane of a second one and read that pane.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
SCRIPT, JOB = HERE / "window-models", HERE / "window-models-job"
CONF = HERE.parent / "tmux.conf"
WRAPPER = 'trap : INT; "$0" "$@"; exec zsh'  # what prefix+T starts claude under (handoff.py)
_loader = SourceFileLoader("window_models", str(SCRIPT))
models = importlib.util.module_from_spec(importlib.util.spec_from_loader("window_models", _loader))
_loader.exec_module(models)
LABEL, FIRST = models.LABEL, models.FIRST_LABEL
WORKMUX = "#[fg=red]#I #[fg=blue] #W{}#[none]#{{?@workmux_status, #{{@workmux_status}},}}"  # its copy, with a label or ""


def reply(model, text="ok"):
    return json.dumps({"type": "assistant", "isSidechain": False, "message": {
        "model": model, "role": "assistant", "content": [{"type": "text", "text": text}]}}, separators=(",", ":"))


def switched(answer):
    """What /model leaves in the transcript: its answer, as a user record."""
    return json.dumps({"type": "user", "message": {"role": "user", "content": f"<local-command-stdout>{answer}</local-command-stdout>"}},
                      separators=(",", ":"))


class Server(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="window-models-check-"))
        self.sock = str(self.tmp / "tmux.sock")
        self.claude_dir = self.tmp / "dot-claude"
        (self.claude_dir / "sessions").mkdir(parents=True)
        stub = self.tmp / "claude"
        stub.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(600)\n")
        stub.chmod(0o755)
        self.tmux("-f", "/dev/null", "new-session", "-d", "-s", "pin", "-n", "plain", "-x", "100", "-y", "20", "cat")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(self.tmux, "kill-server")
        self.sessions = 0
        self.env = {**os.environ, "TMUX": f"{self.sock},0,0", "WINDOW_MODELS_CLAUDE": str(self.claude_dir)}

    def tmux(self, *args, sock=None):
        env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")}
        return subprocess.run(["tmux", "-S", sock or self.sock, *args], capture_output=True, text=True, env=env).stdout.rstrip("\n")

    def claude(self, *lines, window=None, name="lane", wrapped=False, cwd="/home/lucas/some.project"):
        """A pane running the stub claude, in a new window called name or split off window, with a session
        file and a transcript of lines. Returns (window id, pane id, the transcript)."""
        command = ["zsh", "-c", WRAPPER, str(self.tmp / "claude")] if wrapped else [str(self.tmp / "claude")]
        where = ["split-window", "-d", "-t", window] if window else ["new-window", "-d", "-t", "pin:", "-n", name]
        window, pane, tty = self.tmux(*where, "-P", "-F", "#{window_id} #{pane_id} #{pane_tty}", *command).split()
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
        return window, pane, transcript

    def refresh(self):
        run = subprocess.run([str(SCRIPT)], capture_output=True, text=True, env=self.env)
        self.assertEqual((run.returncode, run.stdout, run.stderr), (0, "", ""))  # what a #() job prints is drawn

    def model(self, window):
        return self.tmux("show-options", "-wqv", "-t", window, "@claude_model")

    def drawn(self, window, text=" #W" + LABEL):
        return self.tmux("display-message", "-p", "-t", window, text)


class Models(Server):
    def test_a_claude_window_shows_its_model_and_any_other_window_nothing(self):
        window, _, _ = self.claude(reply("claude-opus-5-5"), name="coord")
        self.refresh()
        self.assertEqual(self.model(window), "opus")
        self.assertEqual(self.model("pin:plain"), "")
        for option in ("@catppuccin_window_text", "@catppuccin_window_current_text"):
            text = [line for line in CONF.read_text().splitlines() if line.startswith(f"set -g {option} ")]
            self.assertEqual([line.split(" ", 3)[3] for line in text], [f'" #W{LABEL}"'], option)  # tmux.conf's is the script's
        self.assertEqual(self.drawn(window), " coord · opus")
        self.assertEqual(self.drawn("pin:plain"), " plain")

    def test_the_last_word_in_the_transcript_wins(self):
        window, _, transcript = self.claude(reply("claude-opus-5-5"), reply("claude-fable-5-2"))
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
        window, _, _ = self.claude(quoting, side, reply("<synthetic>"))
        self.refresh()
        self.assertEqual(self.model(window), "opus")

    def test_a_long_record_after_the_last_reply_does_not_hide_it(self):
        pasted = json.dumps({"type": "user", "message": {"role": "user", "content": "x" * 200_000}}, separators=(",", ":"))
        window, _, _ = self.claude(reply("claude-opus-5-5"), pasted)
        self.refresh()
        self.assertEqual(self.model(window), "opus")

    def test_no_reply_yet_shows_nothing_and_a_claude_that_left_is_cleared(self):
        quiet, _, _ = self.claude(name="fresh")
        window, _, _ = self.claude(reply("claude-opus-5-5"), name="coord")
        self.refresh()
        self.assertEqual((self.model(quiet), self.model(window)), ("", "opus"))
        self.tmux("respawn-pane", "-k", "-t", window, "cat")  # claude gone, the window stays
        time.sleep(0.3)
        self.refresh()
        self.assertEqual(self.model(window), "")

    def test_claude_under_the_window_wrapper_and_a_directory_with_dots(self):
        window, _, _ = self.claude(reply("claude-fable-5-2"), wrapped=True, cwd="/home/lucas/.workmux/tmux/tmux")
        self.refresh()
        self.assertEqual(self.model(window), "fable")

    def test_a_transcript_kept_under_another_directorys_name_is_still_found(self):
        """A session resumed from a different directory: the file is where it was first written."""
        window, _, transcript = self.claude(reply("claude-opus-5-5"))
        elsewhere = self.claude_dir / "projects" / "-somewhere-else"
        elsewhere.mkdir()
        transcript.rename(elsewhere / transcript.name)
        self.refresh()
        self.assertEqual(self.model(window), "opus")

    def test_two_claudes_in_a_window_the_active_pane_wins_else_the_lowest_whatever_the_file_order(self):
        """Neither active is the usual case: the workmux sidebar is a pane too."""
        window, first, _ = self.claude(reply("claude-opus-5-5"))
        _, second, _ = self.claude(reply("claude-fable-5-2"), window=window)
        other = self.tmux("split-window", "-t", window, "-P", "-F", "#{pane_id}", "cat")  # no -d: this one is active
        index = lambda pane: int(self.tmux("display-message", "-p", "-t", pane, "#{pane_index}"))
        lowest = "opus" if index(first) < index(second) else "fable"
        saved, listdir = dict(os.environ), models.os.listdir
        os.environ.update(self.env)
        try:
            for order in (sorted, lambda names: sorted(names, reverse=True)):  # both orders of the session files
                models.CLAUDE = str(self.claude_dir)
                models.os.listdir = lambda path, order=order: order(listdir(path))
                for active, want in ((other, lowest), (second, "fable"), (first, "opus"), (other, lowest)):
                    self.tmux("select-pane", "-t", active)
                    models.main()
                    self.assertEqual(self.model(window), want, (order, active))
        finally:
            models.os.listdir = listdir
            os.environ.clear()
            os.environ.update(saved)


class Leaving(Server):
    """What is left when the job stops or the commit is taken back."""

    def test_a_label_is_drawn_only_while_the_last_run_is_recent(self):
        window, _, _ = self.claude(reply("claude-opus-5-5"), name="coord")
        self.refresh()
        self.assertEqual(self.drawn(window), " coord · opus")
        stamp = int(self.tmux("show-options", "-sqv", "@claude_models_at"))
        self.assertLessEqual(abs(stamp - time.time()), 2)
        for age, want in ((models.EXPIRY - 3, " coord · opus"), (models.EXPIRY + 1, " coord"), (86400, " coord")):
            self.tmux("set-option", "-s", "@claude_models_at", str(int(time.time()) - age))
            self.assertEqual(self.drawn(window), want, age)
        self.tmux("set-option", "-su", "@claude_models_at")  # never run on this server
        self.assertEqual(self.drawn(window), " coord")

    def test_the_job_starts_the_script_once_however_often_tmux_runs_it(self):
        """tmux runs a #() job on any redraw in a new second, once for each client: the gate is what bounds it."""
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        shutil.copy(JOB, bin_dir / "window-models-job")
        ran = self.tmp / "ran"
        (bin_dir / "window-models").write_text(f"#!/bin/sh\necho x >> {ran}\n")
        (bin_dir / "window-models").chmod(0o755)
        job = lambda: subprocess.run([str(bin_dir / "window-models-job")], env=self.env, capture_output=True, text=True)
        runs = lambda: ran.read_text().count("x") if ran.exists() else 0
        for _ in range(6):
            out = job()
            self.assertEqual((out.returncode, out.stdout, out.stderr), (0, "", ""))
        time.sleep(0.3)
        self.assertEqual(runs(), 1)
        stamp = Path(self.sock + ".window-models")
        stamp.write_text(f"{int(time.time()) - 8}\n")  # eight seconds on: still shut
        job()
        time.sleep(0.3)
        self.assertEqual(runs(), 1)
        stamp.write_text(f"{int(time.time()) - 10}\n")  # the bar's second five-second redraw: open
        job()
        time.sleep(0.3)
        self.assertEqual(runs(), 2)

    def test_delete_the_script_and_every_label_goes_blank_also_in_a_windows_own_copy(self):
        """The whole path as tmux runs it: status-right's #() job, a client whose bar is read."""
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        for script in (SCRIPT, JOB):
            shutil.copy(script, bin_dir / script.name)
        window, _, _ = self.claude(reply("claude-opus-5-5"), name="coord")
        copied, _, _ = self.claude(reply("claude-fable-5-2"), name="stamped")
        self.tmux("set-option", "-w", "-t", copied, "window-status-format", "< #W" + LABEL + ">")  # a copy of its own
        self.tmux("set-environment", "-g", "WINDOW_MODELS_CLAUDE", str(self.claude_dir))
        self.tmux("set-option", "-g", "window-status-format", "[ #W" + LABEL + "]")
        self.tmux("set-option", "-g", "window-status-current-format", "[ #W" + LABEL + "]")
        self.tmux("set-option", "-g", "status-interval", "1")
        self.tmux("set-option", "-g", "status-right", f"#({bin_dir}/window-models-job)")
        outer = str(self.tmp / "outer.sock")
        self.tmux("-f", "/dev/null", "new-session", "-d", "-s", "terminal", "-x", "100", "-y", "20",
                  f"env -u TMUX tmux -S {self.sock} attach -t pin", sock=outer)
        self.addCleanup(self.tmux, "kill-server", sock=outer)
        bar = lambda: self.tmux("capture-pane", "-p", "-t", "terminal:", sock=outer).split("\n")[-1]

        def wait(what, seconds):
            end = time.time() + seconds
            while time.time() < end:
                if what(bar()):
                    return
                time.sleep(0.2)
            self.fail(bar())
        wait(lambda line: "[ coord · opus]" in line and "< stamped · fable>" in line, 15)
        for script in (SCRIPT, JOB):
            (bin_dir / script.name).unlink()
        gone = time.time()
        wait(lambda line: "[ coord]" in line and "< stamped>" in line, models.EXPIRY + 8)
        self.assertGreater(time.time() - gone, models.EXPIRY - 13)  # the last run was up to ten seconds before: it held, it did not flicker
        self.assertEqual(self.model(window), "opus")  # the option is still there, stale: the label is what must not show it
        self.assertNotIn("not ready", bar())
        for script in (SCRIPT, JOB):
            shutil.copy(script, bin_dir / script.name)
        wait(lambda line: "[ coord · opus]" in line and "< stamped · fable>" in line, 15)  # and back with the job

    def test_adopt_writes_the_label_into_a_windows_own_format_once_and_only_there(self):
        """workmux's copy: the format as it was, its status icon after it. One from before the expiry is brought up."""
        window, _, _ = self.claude(reply("claude-opus-5-5"), name="coord")
        older, _, _ = self.claude(reply("claude-opus-5-5"), name="older")
        for option in models.FORMATS:
            self.tmux("set-option", "-w", "-t", window, option, WORKMUX.format(""))
            self.tmux("set-option", "-w", "-t", older, option, WORKMUX.format(FIRST))
        adopt = lambda: subprocess.run([str(SCRIPT), "--adopt"], env=self.env)
        adopt()
        for option in models.FORMATS:
            for stamped in (window, older):
                self.assertEqual(self.tmux("show-options", "-wqv", "-t", stamped, option), WORKMUX.format(LABEL))
            self.assertEqual(self.tmux("show-options", "-wqv", "-t", "pin:plain", option), "")  # no copy: none made
        adopt()
        self.assertEqual(self.tmux("show-options", "-wqv", "-t", window, "window-status-format"), WORKMUX.format(LABEL))
        self.refresh()
        self.tmux("set-option", "-w", "-t", window, "@workmux_status", "done")
        self.assertEqual(self.drawn(window, " #W" + LABEL + "#{?@workmux_status, #{@workmux_status},}"), " coord · opus done")

    def test_remove_leaves_the_server_as_if_the_label_had_never_been(self):
        """The revert: tmux.conf no longer has the label, the server still does, in every place it was put."""
        window, _, _ = self.claude(reply("claude-opus-5-5"), name="coord")
        older, _, _ = self.claude(reply("claude-fable-5-2"), name="older")
        other = "#(/home/lucas/.config/tmux/plugins/tmux-continuum/scripts/continuum_save.sh)"
        rest = "#{?client_last_session,#[range=user|last] x #[norange],}#[range=user|click] y #[norange]"
        for job in ("#(~/.config/tmux/scripts/window-models-job)", "#(/home/lucas/.workmux/tmux/tmux/scripts/window-models)"):
            with self.subTest(job=job):
                self.tmux("set-option", "-g", "status-right", other + job + rest)
                self.tmux("set-option", "-g", "window-status-format", "#I #W" + LABEL + "#[none]")
                self.tmux("set-option", "-g", "window-status-current-format", "#I #W" + FIRST + "#[none]")
                for option in models.FORMATS:
                    self.tmux("set-option", "-w", "-t", window, option, WORKMUX.format(LABEL))
                    self.tmux("set-option", "-w", "-t", older, option, WORKMUX.format(FIRST))
                self.refresh()
                self.assertEqual((self.model(window), self.model(older)), ("opus", "fable"))
                subprocess.run([str(SCRIPT), "--remove"], env=self.env)
                self.assertEqual(self.tmux("show-options", "-gv", "status-right"), other + rest)
                for option in models.FORMATS:
                    self.assertEqual(self.tmux("show-options", "-gv", option), "#I #W#[none]")
                    for stamped in (window, older):
                        self.assertEqual(self.tmux("show-options", "-wqv", "-t", stamped, option), WORKMUX.format(""))
                self.assertEqual((self.model(window), self.model(older)), ("", ""))
                self.assertEqual(self.tmux("show-options", "-sqv", "@claude_models_at"), "")
                everything = self.tmux("show-options", "-s") + self.tmux("show-options", "-g") + "".join(
                    self.tmux("show-options", "-w", "-t", w) for w in self.tmux("list-windows", "-a", "-F", "#{window_id}").split())
                self.assertNotIn("claude_model", everything)


if __name__ == "__main__":
    unittest.main(verbosity=2)
