#!/usr/bin/env bash
# which-key for tmux: press C-a and pause, and a popup lists what each next key
# does. Type to filter, enter runs the binding in the pane you came from, esc
# closes. A key pressed before the popup appears works as always.
#
# Key tables are groups: a binding that switches to another table (prefix+a
# into the agent table) shows as "+agent (11 keys)", and that table gets the
# same pause-then-popup once you are in it. Picking a group from the popup
# opens the group's popup straight away.
#
# Started in the background by the root C-a binding (the prefix option is None
# so that binding can exist), and by each group's binding:
#   which-key.sh <client> <press number> <window, pane and layout at the press> [table]
# After the delay the popup opens only if this client is still in <table>
# (default prefix), no newer press happened, and window, pane and layout are
# unchanged. The last check is for repeat bindings (C-a C-h h h): they leave the
# client in the prefix table but always move or resize something.
#
#   which-key.sh --menu <rows> <table>    the popup itself: prints the chosen row, or nothing
#   which-key.sh --rank <rows> <query>    the menu rows, keys matching the query first
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
    rows=$2 table=$3
    prompt="C-a > "
    [ "$table" = prefix ] || prompt="C-a > $table > "
    fzf --delimiter=$'\t' --with-nth=2 --tiebreak=index < "$rows" \
          --bind "change:reload(~/.config/tmux/scripts/which-key.sh --rank '$rows' {q})" \
          --height=100% --reverse --prompt="$prompt" \
          --header=$'type to filter · enter runs it · esc closes  (r = repeatable, + = group)' \
      | cut -f1,3 || true
    exit 0
fi

# One row per binding in a table: the key in config syntax, what shows, and the
# table it opens if it is a group. list-keys gives every table's commands (to
# find groups and count their keys), list-keys -N tmux's own description where
# the binding has one (most defaults do, ours have -N notes in tmux.conf).
build_rows() {
    { tmux list-keys; echo; tmux list-keys -N -T "$1"; } | python3 -c '
import re, subprocess, sys

table = sys.argv[1]
bound, notes, in_notes = {}, {}, False  # bound: table -> [(token, repeat, command)]
for line in sys.stdin:
    line = line.rstrip("\n")
    if not line:
        in_notes = True
        continue
    if in_notes:
        key, _, note = line.partition(" ")
        notes[key] = note.strip()
        continue
    m = re.match(r"^bind-key\s+(-r\s+)?-T\s+(\S+)\s+(\S+)\s+(.*)$", line)
    if m:
        bound.setdefault(m.group(2), []).append((m.group(3), bool(m.group(1)), " ".join(m.group(4).split())))

def plain(token):  # list-keys escapes keys like \; \" \# for the config parser
    return token[1:] if len(token) == 2 and token[0] == "\\" else token

noise = ("Mouse", "Wheel", "Double", "Triple", "Secondary", "Any")
def keys_of(t):
    return [(tok, r, c) for tok, r, c in bound.get(t, []) if not plain(tok).startswith(noise)]

def group_of(cmd):
    """The table a binding switches to, if that is what it is for."""
    m = re.search(r"(?:^|; )switch-client -T (\S+)", cmd)
    return m.group(1) if m and m.group(1) != table and m.group(1) in bound else ""

def order(key):
    digit = len(key) == 1 and key.isdigit()
    if digit and table != "prefix": return (-1, key)  # agent 1-9 before n/p
    if len(key) == 1 and key.islower(): return (0, key)
    if len(key) == 1 and key.isupper(): return (1, key.lower())
    if digit: return (2, key)
    if key.startswith("C-"): return (3, key.lower())
    if key.startswith("M-"): return (4, key.lower())
    return (5, key.lower())

# The agent table jumps by sidebar position, which moves with the priority
# sort: say which agent each number reaches right now.
reaches = {}
if table == "agent":
    panes = subprocess.run(["tmux", "show-option", "-gqv", "@workmux_sidebar_agents"],
                           capture_output=True, text=True).stdout.split()
    for n, pane in enumerate(panes[:9], 1):
        reaches[str(n)] = subprocess.run(["tmux", "display-message", "-p", "-t", pane, "#{session_name}:#{window_index}"],
                                         capture_output=True, text=True).stdout.strip()

for token, repeat, cmd in sorted(keys_of(table), key=lambda row: order(plain(row[0]))):
    key, group = plain(token), group_of(cmd)
    what = notes.get(key) or cmd
    if group:
        what = f"+{group} ({len(keys_of(group))} keys) · {what}" if key in notes else f"+{group} ({len(keys_of(group))} keys)"
    if key in reaches:
        what += f" · {reaches[key]}"
    r = "r" if repeat else " "
    print(f"{token}\t{key:<8} {r}  {what[:110]}\t{group}")
' "$1"
}

client=$1 press=$2 before=$3 table=${4:-prefix}
# The job starts ~0.1 s after the press (measured on this machine), so 1.1
# opens the popup about 1.2 s in. nvim's half second popped it up mid-chord
# on keys already known by heart. A group picked from a popup opens at once:
# the pause is only for keys you might still type.
delay=1.1
if [ "$(tmux show-option -gqv @wk_now)" = 1 ]; then
    tmux set-option -gu @wk_now
    delay=0
fi
state='#{window_id} #{pane_id} #{window_layout}'

# Build the rows while waiting, so the popup shows its frame and its text
# together and still opens on time. Wasted on a fast key, but it is ~30 ms.
rows=$(mktemp) pick=$(mktemp)
trap 'rm -f "$rows" "$pick"' EXIT
build_rows "$table" > "$rows" &
sleep "$delay"
wait
[ "$(tmux show-option -gqv @wk_n)" = "$press" ] || exit 0
# list-clients, not display-message -c: from a background job, display-message
# fills client fields from the most recently active client, which need not be
# the one that pressed C-a.
now=$(tmux list-clients -F "#{client_name}|#{client_key_table}|$state" | awk -F'|' -v c="$client" '$1 == c')
[ "$(cut -d'|' -f2 <<<"$now")" = "$table" ] || exit 0
[ "$(cut -d'|' -f3- <<<"$now")" = "$before" ] || exit 0

tmux display-popup -c "$client" -x C -y S -w 90% -h 45% \
    -E "~/.config/tmux/scripts/which-key.sh --menu '$rows' '$table' > '$pick'" || true
IFS=$'\t' read -r key group < "$pick" || true

if [ -z "${key:-}" ]; then
    tmux switch-client -c "$client" -T root
    exit 0
fi
[ -n "${group:-}" ] && tmux set-option -g @wk_now 1
# Replay the key as if typed in this table, so the binding runs against the
# pane the client is on, not this popup. The token is already in config syntax,
# so tmux's own parser unescapes it.
printf 'switch-client -c "%s" -T %s\nsend-keys -K -c "%s" %s\n' \
    "$client" "$table" "$client" "$key" | tmux source-file -
