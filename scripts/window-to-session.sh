#!/usr/bin/env bash
# Move one window, with its panes intact, into a session of its own (or into an
# existing session if the name is taken). Everything is addressed by id, never by
# "the current window", because run-shell has no client to resolve that against.
#
# The client is switched BEFORE the move: detach-on-destroy is on, so moving the
# last window out of a session would otherwise destroy the session under the client.
#
# usage: window-to-session.sh <window_id> <client_name> <session_name>
win=$1 client=$2 name=$3
[ -n "$name" ] || exit 0

# tmux rewrites "." and ":" in session names, so match on the stored name, not -t.
dest=$(tmux list-sessions -F '#{session_id}' -f "#{==:#{session_name},$name}")
if [ -n "$dest" ]; then
  placeholder=
else
  # new-session needs a first window; it is swapped out for the real one below.
  read -r dest placeholder < <(tmux new-session -d -P -F '#{session_id} #{window_id}' -s "$name")
fi

[ -n "$dest" ] || exit 1
tmux switch-client -c "$client" -t "$dest"
tmux move-window -d -s "$win" -t "$dest:"
tmux select-window -t "$win"
[ -z "$placeholder" ] || tmux kill-window -t "$placeholder"
tmux move-window -r -t "$dest:"
