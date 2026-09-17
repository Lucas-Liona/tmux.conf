#!/usr/bin/env bash
# Pick a workmux worktree with fzf, then run an action on it.
# Usage: workmux-fzf.sh <open|close|remove|merge>
#
# Parses `workmux list --json` rather than the table: the table shows BRANCH,
# but open/close/remove take the HANDLE, and those differ whenever
# worktree_naming is basename (docs/media-phase-2 -> media-phase-2).
set -euo pipefail

action="${1:?usage: workmux-fzf.sh <open|close|remove|merge>}"

sel=$(workmux list --json 2>/dev/null | python3 -c '
import json, sys
for w in json.load(sys.stdin):
    if w["is_main"]:          # open/close/remove do not apply to the main checkout
        continue
    state = "open" if w["is_open"] else "closed"
    dirty = "*" if w["has_uncommitted_changes"] else " "
    agents = ",".join(w.get("agent_statuses") or []) or "-"
    print("{}\t{} {:<7} {:<30} {}".format(w["handle"], dirty, state, w["branch"], agents))
' | fzf --delimiter=$'\t' --with-nth=2.. \
        --height=100% --reverse --prompt="workmux ${action} > " \
        --header=$'  * = uncommitted        state    branch                         agents' \
    | cut -f1)

[ -n "${sel}" ] || exit 0
workmux "${action}" "${sel}"
