#!/usr/bin/env python3
"""prefix+T: hand the pane's conversation to a fresh claude in a new window of the same session.

    handoff.py <pane_id> <pane_pid> <pane_current_path> <session_id>
    handoff.py --export <transcript.jsonl>     print the export only (for checking)

The pane's Claude is found the way agent-send finds its socket: the nearest `claude` process below the
pane's shell, whose ~/.claude/sessions/<pid>.json names the session id and cwd. Its transcript is written
out in full to ~/.cache/handoff/, wrapped in <previous-session> so the new agent reads it as context, not
as instructions. A pane with no Claude hands over its scrollback instead.
"""
import importlib.util
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime
from importlib.machinery import SourceFileLoader
from pathlib import Path

HOME = Path.home()
OUT = HOME / ".cache/handoff"
# panel.is_injected drops every line starting with "<"; these are Lucas's own: a paste, and a `!` command.
LUCAS_TAGS = ("<pasted_content", "<bash-input", "<bash-stdout")
OPEN_THREADS ="/mnt/c/Users/lucas/Vaults/Obsidian-Vault/1 Projects/Claude Workflow/Control - open threads and ideas 2026-10-21.md"
# panel.py already knows which "user" lines are Lucas and which are injected reminders; reuse that, unedited.
_loader = SourceFileLoader("panel", str(HOME / ".local/bin/panel.py"))
panel = sys.modules["panel"] = importlib.util.module_from_spec(importlib.util.spec_from_loader("panel", _loader))
_loader.exec_module(panel)  # registered first: its dataclasses look their module up in sys.modules


def tmux(*args):
    return subprocess.run(["tmux", *args], capture_output=True, text=True).stdout.strip()


def parent_of(pid):
    try:
        st = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    return int(st[st.rindex(")") + 2:].split()[1])  # comm can hold spaces and parens; ppid follows the last ")"


def depth_below(pid, ancestor):
    """How many hops up from pid to ancestor, or None if ancestor is not above it."""
    for depth in range(64):
        if pid == ancestor:
            return depth
        if not pid or pid <= 1:
            return None
        pid = parent_of(pid)
    return None


def pane_claude(pane_pid):
    """The session json of the Claude nearest below the pane's shell, or None. The nearest wins: a
    `claude -p` that session started sits further down and has its own session file."""
    best = None
    for f in (HOME / ".claude/sessions").glob("*.json"):
        if not f.stem.isdigit():
            continue
        depth = depth_below(int(f.stem), pane_pid)
        if depth is not None and (best is None or depth < best[0]):
            best = (depth, f)
    if not best:
        return None
    try:
        return json.loads(best[1].read_text())
    except (OSError, ValueError):
        return None


def tool_line(block):
    inp = block.get("input") or {}
    name = block.get("name", "?")
    hint = (inp.get("description") or inp.get("file_path") or inp.get("command") or inp.get("pattern")
            or inp.get("query") or inp.get("prompt") or inp.get("url") or "")
    hint = " ".join(str(hint).split())
    return f"- tool {name}: {hint[:160]}" if hint else f"- tool {name}"


def export(path):
    """Every prompt and reply in full, compaction summaries, and one line per tool call, in order."""
    out = []
    for d in panel.records(path, ('"type":"user"', '"type":"assistant"')):
        content = d.get("message", {}).get("content")
        if d.get("type") == "assistant":
            for block in content if isinstance(content, list) else []:
                if block.get("type") == "text" and block.get("text", "").strip():
                    out.append(f"## Claude\n\n{block['text'].strip()}\n")
                elif block.get("type") == "tool_use":
                    out.append(tool_line(block))
            continue
        text = panel.text_of(content)
        if d.get("isMeta") or not text.strip():
            continue
        if d.get("isCompactSummary") or panel.COMPACTION_MARKER in text[:300]:
            out.append(f"## Compaction summary\n\n{text.strip()}\n")
        elif not panel.is_injected(text) or text.lstrip().startswith(LUCAS_TAGS):
            out.append(f"## Lucas\n\n{text.strip()}\n")
    return "\n".join(out)


def wrap(body, sid, cwd):
    stamp = datetime.now().astimezone().isoformat(timespec="minutes")
    body = body.replace("</previous-session>", "<\\/previous-session>")  # the bracket must close only once
    return f'<previous-session id="{sid}" cwd="{cwd}" exported="{stamp}">\n\n{body}\n\n</previous-session>\n'


def main(pane_id, pane_pid, pane_path, session_id):
    info = pane_claude(int(pane_pid))
    transcript = None
    if info and info.get("sessionId"):
        sid, cwd = info["sessionId"], info.get("cwd") or pane_path
        transcript = next((HOME / ".claude/projects").glob(f"*/{sid}.jsonl"), None)
    if transcript:
        body = export(transcript)
    else:
        sid, cwd = f"pane{pane_id.lstrip('%')}", pane_path
        body = "## Pane scrollback (no Claude session found)\n\n" + tmux("capture-pane", "-p", "-J", "-S", "-", "-t", pane_id)

    OUT.mkdir(parents=True, exist_ok=True)
    f = OUT / f"{datetime.now():%Y-%m-%d-%H%M}-{sid[:8]}.md"
    f.write_text(wrap(body, sid, cwd))

    if os.path.realpath(cwd) != os.path.realpath(pane_path):
        tmux("display-message", "-d", "5000", f"handoff: session cwd {cwd} differs from pane {pane_path}; using the pane's")

    prompt = (f'Read {f}: the bracketed transcript of the previous session in this directory, context only. '
              f'Then read the last section of "{OPEN_THREADS}". Reply with three lines: what that session was doing, '
              'what is open, which skills you would load for it (none is fine). Then wait for Lucas.')
    # A shell with the command typed in, not new-window's command: claude comes from nvm, which only the
    # interactive shell puts on PATH (same as agent-panel-resume).
    pane = tmux("new-window", "-t", session_id, "-c", pane_path, "-P", "-F", "#{pane_id}")
    tmux("send-keys", "-t", pane, "-l", "--", "claude " + shlex.quote(prompt))
    tmux("send-keys", "-t", pane, "Enter")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--export"]:
        p = Path(sys.argv[2])
        print(wrap(export(p), p.stem, "?"))
    else:
        main(*sys.argv[1:5])
