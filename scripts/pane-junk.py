#!/usr/bin/env python3
"""Print the pane ids of junk shells, for pane-tour:

    pane-junk | pane-tour -              see them, then decide
    pane-junk | pane-tour - --instant    kill them
    pane-junk --why                      id, where, and why

A junk shell is an idle zsh/bash (no child process, not a workmux sidebar)
whose whole history is its own prompt repeated: what old resurrect restores
left behind, sidebar debris and a compinit startup error included. A shell
whose history is an exact copy of one kept in the same window is junk too.
Anything with real output is kept, and every window keeps one real pane.
"""
import collections
import hashlib
import re
import subprocess
import sys

PROMPT = re.compile(r"^(❯|>)( .*)?$|^(~|/)[^\s]*( [^\s]{1,40}){0,4}$")  # "❯ cmd" or "<dir> [git bits]"
DIRLINE = re.compile(r"^(~|/)[^\s]*( [^\s]{1,40}){0,4}$")                  # the "<dir> [git bits]" half only
FRAGMENT = re.compile(r"^[▌│─━┌└┐┘\s]|^.{0,3}$")                       # sidebar pieces, stray bits


def tmux(*args: str) -> str:
    return subprocess.run(["tmux", *args], capture_output=True, text=True).stdout


def has_children(pid: str) -> bool:
    try:
        return bool(open(f"/proc/{pid}/task/{pid}/children").read().split())
    except OSError:
        return True  # cannot tell: treat as busy


def content(pane: str) -> list[str]:
    """The history minus prompts and noise; empty means nothing was ever done here."""
    lines = [l.rstrip().rstrip("│").rstrip() for l in tmux("capture-pane", "-p", "-J", "-S", "-", "-E", "-", "-t", pane).splitlines()]
    lines = [l for l in lines if l.strip()]
    # Pieces of the directory line count as noise, never pieces of a typed
    # command: `echo hi` prints "hi", which is a piece of "❯ echo hi".
    prompts = [l.strip() for l in lines if DIRLINE.match(l.strip())]
    def noise(line: str) -> bool:
        s = line.strip()
        return bool(PROMPT.match(s) or FRAGMENT.match(line) or s.startswith("compinit:")
                    or any(s in p for p in prompts))  # a cut-off piece of the prompt
    return [l for l in lines if not noise(l)]


def main() -> int:
    why = "--why" in sys.argv[1:]
    fmt = "#{window_id}|#{pane_index}|#{pane_id}|#{pane_pid}|#{pane_current_command}|#{@workmux_role}|#{session_name}:#{window_index}.#{pane_index}"
    windows = collections.defaultdict(list)
    for line in tmux("list-panes", "-a", "-F", fmt).splitlines():
        wid, index, pane, pid, cmd, role, where = line.split("|")
        if role != "sidebar":
            windows[wid].append((int(index), pane, pid, cmd, where))
    for panes in windows.values():
        seen, junk = set(), []
        for _, pane, pid, cmd, where in sorted(panes):
            if cmd not in ("zsh", "bash") or has_children(pid):
                continue
            body = content(pane)
            print_key = hashlib.sha1("\n".join(body).encode()).hexdigest() if body else None
            if print_key is None:
                junk.append((pane, where, "only prompts"))
            elif print_key in seen:
                junk.append((pane, where, "copy of a kept pane"))
            else:
                seen.add(print_key)
        if len(junk) == len(panes):
            junk = junk[1:]  # never empty a window
        for pane, where, reason in junk:
            print(f"{pane} {where} {reason}" if why else pane)
    return 0


if __name__ == "__main__":
    sys.exit(main())
