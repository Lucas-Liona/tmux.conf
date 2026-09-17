#!/usr/bin/env bash
# Searchable keybinding reference, replacing `list-keys -N` on prefix+?.
#
# The default dumps ~100 bindings as raw config lines into a pager, which is a
# reference, not a cheatsheet. This is the tmux equivalent of which-key: type
# "session" and see every binding that touches sessions.
#
# Reference only -- selecting a row exits without running anything. Executing a
# binding from here would fire it against this popup rather than the pane you
# came from, which is never what you want.
set -euo pipefail

tmux list-keys 2>/dev/null | python3 -c '
import re, sys

# bind-key [-r] [-n] -T <table> <key> <command...>
pat = re.compile(r"^bind-key\s+(?:(-[rn]+)\s+)?-T\s+(\S+)\s+(\S+)\s+(.*)$")
rows = []
for line in sys.stdin:
    m = pat.match(line.rstrip("\n"))
    if not m:
        continue
    flags, table, key, cmd = m.groups()
    cmd = " ".join(cmd.split())
    table = {"prefix": "prefix", "root": "root  ", "copy-mode-vi": "copy  "}.get(table, table[:6])
    rep = "r" if flags and "r" in flags else " "
    rows.append((table, key, rep, cmd))

# prefix table first: it is what someone pressing ? is usually looking for
order = {"prefix": 0, "root  ": 1, "copy  ": 2}
rows.sort(key=lambda r: (order.get(r[0], 9), r[1].lower()))

for table, key, rep, cmd in rows:
    short = cmd if len(cmd) <= 90 else cmd[:87] + "..."
    print(f"{table}\t{key}\t{rep}\t{short}\t{cmd}")
' | fzf --delimiter=$'\t' --with-nth=1,2,3,4 \
        --height=100% --reverse --prompt="tmux keys > " \
        --header=$'table   key        r  command                    (r = repeatable)' \
        --preview='printf "%s" {5} | fold -s -w 70' \
        --preview-window=down,6,wrap \
    >/dev/null || true
