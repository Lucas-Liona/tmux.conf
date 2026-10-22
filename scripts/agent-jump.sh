#!/usr/bin/env bash
# prefix+a N / n / p: switch the pressing client to the Nth, next or previous
# agent in workmux sidebar order (the number each sidebar row leads with).
# `workmux sidebar jump` reads the same list, but its switch-client has no -c,
# so tmux moves whichever client was active last, not always the one that pressed.
# Usage: agent-jump.sh <N|next|prev> <client> <pane_id> <window_id> <session_name>
set -euo pipefail

what=$1 client=$2 pane=$3 window=$4 session=$5

read -ra agents <<<"$(tmux show-option -gqv @workmux_sidebar_agents)"

# The sidebar's "this session only" filter narrows the numbers it draws; follow it.
if [ "$(tmux show-option -gqv @workmux_sidebar_filter)" = session ]; then
    read -ra agents <<<"$(tmux list-panes -s -t "=$session" -F '#{pane_id}' \
        | grep -Fxf - <(printf '%s\n' "${agents[@]}") | tr '\n' ' ')"
fi

n=${#agents[@]}
if [ "$n" -eq 0 ]; then
    tmux display-message -c "$client" "no agents in the sidebar"
    exit 0
fi

if [ "$what" != next ] && [ "$what" != prev ]; then
    if [ "$what" -gt "$n" ]; then
        tmux display-message -c "$client" "no agent $what (sidebar has $n)"
        exit 0
    fi
    tmux switch-client -c "$client" -t "${agents[what - 1]}"
    exit 0
fi

# Where we are: this pane if it is an agent, else the first agent in this window.
here=-1
window_panes=$(tmux list-panes -t "$window" -F '#{pane_id}')
for i in "${!agents[@]}"; do
    if [ "${agents[$i]}" = "$pane" ]; then here=$i; break; fi
    if [ "$here" -lt 0 ] && grep -qFx "${agents[$i]}" <<<"$window_panes"; then here=$i; fi
done

if [ "$what" = next ]; then
    [ "$here" -lt 0 ] && target=0 || target=$(( (here + 1) % n ))
else
    [ "$here" -lt 0 ] && target=$(( n - 1 )) || target=$(( (here - 1 + n) % n ))
fi

tmux switch-client -c "$client" -t "${agents[$target]}"
