#!/usr/bin/env bash
# fzf popup for window-to-session.sh: pick an existing session, or type a new name.
# Enter takes the highlighted match (or the typed text if nothing matches);
# ctrl-n takes the typed text literally, for a new name that happens to prefix an old one.
# Reads the window and client from @w2s_window / @w2s_client, set by the binding (display-popup does not expand formats).
set -euo pipefail

win=$(tmux show-option -gqv @w2s_window) client=$(tmux show-option -gqv @w2s_client)
here=$(tmux display-message -p -t "$win" '#{session_name}')

out=$(tmux list-sessions -F '#{session_name}'$'\t''#{session_windows}w#{?session_attached, attached,}' \
        | awk -F'\t' -v here="$here" '$1 != here' \
        | fzf --print-query --expect=ctrl-n --delimiter=$'\t' \
              --height=100% --reverse --prompt="send window to > " \
              --header=$'enter: pick / create typed name    ctrl-n: create typed name' || true)

# fzf prints: query, key pressed (blank for enter), then the selected line if any.
{ read -r query; read -r key; read -r picked; } <<<"$out" || true
if [ "$key" = ctrl-n ] || [ -z "$picked" ]; then name=$query; else name=${picked%%$'\t'*}; fi

[ -n "$name" ] || exit 0
exec ~/.config/tmux/scripts/window-to-session.sh "$win" "$client" "$name"
