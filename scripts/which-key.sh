#!/usr/bin/env bash
# which-key for the tmux prefix: press C-a and pause, and a popup lists what
# each next key does. Type to filter, enter runs the binding in the pane you
# came from, esc closes. A key pressed before the popup appears works as always.
#
# Started in the background by the root C-a binding (the prefix option is None
# so that binding can exist):
#   which-key.sh <client> <press number> <window, pane and layout at the press>
# After the delay the popup opens only if this client is still in the prefix
# table, no newer C-a was pressed, and window, pane and layout are unchanged.
# The last check is for repeat bindings (C-a C-h h h): they leave the client in
# the prefix table but always move or resize something.
#
#   which-key.sh --menu <rows>   the popup itself: prints the chosen key, or nothing
#   which-key.sh --rank <rows> <query>   the menu rows, keys matching the query first
set -euo pipefail

if [ "${1:-}" = --rank ]; then
    # fzf scores "g" in "Agent jump" as high as the key g itself, so every
    # keystroke reloads the rows with the typed key on top: exact key, then keys
    # starting with the query, then the rest. fzf still filters and ranks by
    # match quality; --tiebreak=index makes this order win the ties.
    awk -F'\t' -v q="$3" '
        { split($2, f, " "); key = f[1] }
        q == ""                { print; next }
        key == q               { exact[++e] = $0; next }
        index(key, q) == 1     { prefix[++p] = $0; next }
                               { rest[++r] = $0 }
        END { for (i = 1; i <= e; i++) print exact[i]
              for (i = 1; i <= p; i++) print prefix[i]
              for (i = 1; i <= r; i++) print rest[i] }' "$2"
    exit 0
fi

if [ "${1:-}" = --menu ]; then
    rows=$2
    fzf --delimiter=$'\t' --with-nth=2 --tiebreak=index < "$rows" \
          --bind "change:reload(~/.config/tmux/scripts/which-key.sh --rank '$rows' {q})" \
          --height=100% --reverse --prompt="C-a > " \
          --header=$'type to filter · enter runs it · esc closes  (r = repeatable)' \
      | cut -f1 || true
    exit 0
fi

# One row per prefix binding: the key in config syntax, a tab, then what shows.
# list-keys gives the command, list-keys -N tmux's own description where the
# binding has one (most defaults do, ours have -N notes in tmux.conf).
build_rows() {
    { tmux list-keys -T prefix; echo; tmux list-keys -N -T prefix; } | python3 -c '
import re, sys

bound, notes, in_notes = [], {}, False
for line in sys.stdin:
    line = line.rstrip("\n")
    if not line:
        in_notes = True
        continue
    if in_notes:
        key, _, note = line.partition(" ")
        notes[key] = note.strip()
        continue
    m = re.match(r"^bind-key\s+(-r\s+)?-T\s+prefix\s+(\S+)\s+(.*)$", line)
    if m:
        bound.append((m.group(2), bool(m.group(1)), " ".join(m.group(3).split())))

def plain(token):  # list-keys escapes keys like \; \" \# for the config parser
    return token[1:] if len(token) == 2 and token[0] == "\\" else token

def order(key):
    if len(key) == 1 and key.islower(): return (0, key)
    if len(key) == 1 and key.isupper(): return (1, key.lower())
    if len(key) == 1 and key.isdigit(): return (2, key)
    if key.startswith("C-"): return (3, key.lower())
    if key.startswith("M-"): return (4, key.lower())
    return (5, key.lower())

noise = ("Mouse", "Wheel", "Double", "Triple", "Secondary", "Any")
rows = [(t, plain(t), r, c) for t, r, c in bound if not plain(t).startswith(noise)]
for token, key, repeat, cmd in sorted(rows, key=lambda row: order(row[1])):
    what = notes.get(key) or cmd
    r = "r" if repeat else " "
    print(f"{token}\t{key:<8} {r}  {what[:110]}")
'
}

client=$1 press=$2 before=$3
# The job starts ~0.1 s after the press (measured on this machine), so 0.4
# puts the popup near the half second nvim's which-key uses.
delay=0.4
state='#{window_id} #{pane_id} #{window_layout}'

# Build the rows while waiting, so the popup shows its frame and its text
# together and still opens on time. Wasted on a fast key, but it is ~30 ms.
rows=$(mktemp) pick=$(mktemp)
trap 'rm -f "$rows" "$pick"' EXIT
build_rows > "$rows" &
sleep "$delay"
wait
[ "$(tmux show-option -gqv @wk_n)" = "$press" ] || exit 0
# list-clients, not display-message -c: from a background job, display-message
# fills client fields from the most recently active client, which need not be
# the one that pressed C-a.
now=$(tmux list-clients -F "#{client_name}|#{client_key_table}|$state" | awk -F'|' -v c="$client" '$1 == c')
[ "$(cut -d'|' -f2 <<<"$now")" = prefix ] || exit 0
[ "$(cut -d'|' -f3- <<<"$now")" = "$before" ] || exit 0

tmux display-popup -c "$client" -x C -y S -w 90% -h 45% \
    -E "~/.config/tmux/scripts/which-key.sh --menu '$rows' > '$pick'" || true
key=$(cat "$pick")

if [ -z "$key" ]; then
    tmux switch-client -c "$client" -T root
    exit 0
fi
# Replay the key as if typed after the prefix, so the binding runs against the
# pane the client is on, not this popup. The token is already in config syntax,
# so tmux's own parser unescapes it.
printf 'switch-client -c "%s" -T prefix\nsend-keys -K -c "%s" %s\n' \
    "$client" "$client" "$key" | tmux source-file -
