#!/usr/bin/env python3
"""Keep the workmux sidebar out of tmux-resurrect's save files.

The sidebar is an ordinary pane (pane 1, leftmost) running `workmux
_sidebar-run`. resurrect saves it with an empty command, restores it as a bare
shell, and workmux then adds a real sidebar beside the stand-in; every
save/restore cycle grows each window by one shell. The restore-time killer
(resurrect-drop-sidebars.sh) guesses by pane index. This runs at save time,
when the live server still says which panes are sidebars, so it is exact.

Two resurrect hooks, one script:

  layout <savefile>   @resurrect-hook-post-save-layout: before the "did the
                      save change" check, so identical saves still dedupe.
                      Drops the sidebar rows, renumbers panes, fixes layouts.
  contents            @resurrect-hook-post-save-all: pane contents were
                      captured from the live server, named by live index, and
                      archived. Rename them to the renumbered indexes. The
                      layout step leaves a marker saying which panes it
                      dropped; this step consumes it, so it is exact and
                      cannot run twice on one archive.

A save with no live sidebars is left byte-identical.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

D = "\t"


def live_sidebars():
    """{(session, window_index): (pane_index, pane_id_number)} for live sidebars."""
    out = subprocess.run(
        ["tmux", "list-panes", "-a", "-F",
         "#{session_name}\t#{window_index}\t#{pane_index}\t#{pane_id}\t#{@workmux_role}"],
        capture_output=True, text=True, check=True).stdout
    found = {}
    for line in out.splitlines():
        session, window, index, pane_id, role = line.split(D)
        if role == "sidebar":
            found[(session, window)] = (int(index), int(pane_id.lstrip("%")))
    return found


# --- tmux layout strings: "csum,WxH,X,Y{...}" -------------------------------
# A node is a dict: w, h, x, y, and either id (leaf) or kind ("{" or "[") and
# children. "{" splits left-right, "[" top-bottom.

def parse_layout(text):
    pos = 0

    def node():
        nonlocal pos
        m = re.compile(r"(\d+)x(\d+),(\d+),(\d+)").match(text, pos)
        n = {"w": int(m[1]), "h": int(m[2]), "x": int(m[3]), "y": int(m[4])}
        pos = m.end()
        if pos < len(text) and text[pos] in "{[":
            n["kind"] = text[pos]
            close = "}" if text[pos] == "{" else "]"
            pos += 1
            n["children"] = []
            while True:
                n["children"].append(node())
                if text[pos] == ",":
                    pos += 1
                    continue
                assert text[pos] == close, text
                pos += 1
                break
        else:
            m = re.compile(r",(\d+)").match(text, pos)
            n["id"] = int(m[1])
            pos = m.end()
        return n

    root = node()
    assert pos == len(text), text
    return root


def serialize(n):
    head = f"{n['w']}x{n['h']},{n['x']},{n['y']}"
    if "id" in n:
        return f"{head},{n['id']}"
    close = "}" if n["kind"] == "{" else "]"
    return head + n["kind"] + ",".join(serialize(c) for c in n["children"]) + close


def checksum(s):
    """tmux's layout_checksum (layout-custom.c)."""
    c = 0
    for ch in s:
        c = (c >> 1) + ((c & 1) << 15)
        c = (c + ord(ch)) & 0xFFFF
    return f"{c:04x}"


def remove_leaf(n, pane_id):
    """Remove the leaf, hand its space to its neighbour, collapse single-child
    containers. Returns the new node (None if n itself was the leaf)."""
    if "id" in n:
        return None if n["id"] == pane_id else n
    kids = n["children"]
    for i, kid in enumerate(kids):
        if kid.get("id") == pane_id:
            axis = "w" if n["kind"] == "{" else "h"
            j = i + 1 if i + 1 < len(kids) else i - 1
            kids[j][axis] += kid[axis] + 1  # +1 for the border line
            del kids[i]
            break
        new = remove_leaf(kid, pane_id)
        if new is not kid:
            kids[i] = new
            break
    if len(kids) == 1:
        only = kids[0]
        only.update({k: n[k] for k in ("w", "h", "x", "y")})
        return only
    return n


def fit(n, w, h, x, y):
    """Give n the box (w, h, x, y) and lay its children out inside it, scaling
    along the split axis in proportion to their current sizes."""
    n.update({"w": w, "h": h, "x": x, "y": y})
    if "id" in n:
        return
    kids = n["children"]
    horizontal = n["kind"] == "{"
    axis = "w" if horizontal else "h"
    total = sum(k[axis] for k in kids)
    avail = (w if horizontal else h) - (len(kids) - 1)
    sizes = [k[axis] * avail // total for k in kids]
    sizes[-1] += avail - sum(sizes)
    offset = x if horizontal else y
    for kid, size in zip(kids, sizes):
        if horizontal:
            fit(kid, size, h, offset, y)
        else:
            fit(kid, w, size, x, offset)
        offset += size + 1


def strip_layout(layout, pane_id):
    root = parse_layout(layout[5:])
    if f",{pane_id}" not in layout:
        return layout
    new = remove_leaf(root, pane_id)
    if new is None:
        return layout  # the only pane; leave the window alone
    fit(new, root["w"], root["h"], root["x"], root["y"])
    body = serialize(new)
    return f"{checksum(body)},{body}"


# --- the save file ----------------------------------------------------------
# pane  session window active flags index title dir pane_active cmd fullcmd
# window session window name active flags layout automatic_rename

def strip_file(path, sidebars):
    with open(path, encoding="utf-8", errors="surrogateescape") as f:
        lines = f.read().split("\n")
    out = []
    changed = 0
    for line in lines:
        f = line.split(D)
        if f[0] == "pane" and (f[1], f[2]) in sidebars:
            index, _ = sidebars[(f[1], f[2])]
            if int(f[5]) == index and f[9] == "workmux":
                changed += 1
                continue
            if int(f[5]) > index:
                f[5] = str(int(f[5]) - 1)
                line = D.join(f)
        elif f[0] == "window" and (f[1], f[2]) in sidebars:
            _, pane_id = sidebars[(f[1], f[2])]
            f[6] = strip_layout(f[6], pane_id)
            line = D.join(f)
        out.append(line)
    if changed:
        tmp = path + ".strip"
        with open(tmp, "w", encoding="utf-8", errors="surrogateescape") as fh:
            fh.write("\n".join(out))
        os.replace(tmp, path)
    return changed


def rename_contents(archive, sidebars):
    """pane contents are named pane-<session>:<window>.<index> by live index."""
    if not sidebars or not os.path.exists(archive):
        return 0
    work = tempfile.mkdtemp(prefix="resurrect-strip-")
    try:
        with tarfile.open(archive) as tar:
            tar.extractall(work, filter="data")
        d = os.path.join(work, "pane_contents")
        renamed = 0
        for (session, window), (index, _) in sidebars.items():
            prefix = f"pane-{session}:{window}."
            names = [n for n in os.listdir(d) if n.startswith(prefix)]
            by_index = {int(n[len(prefix):]): n for n in names if n[len(prefix):].isdigit()}
            if index not in by_index:
                continue
            os.remove(os.path.join(d, by_index.pop(index)))
            for i in sorted(i for i in by_index if i > index):
                os.rename(os.path.join(d, by_index[i]), os.path.join(d, f"{prefix}{i - 1}"))
            renamed += 1
        if renamed:
            tmp = archive + ".strip"
            with tarfile.open(tmp, "w:gz") as tar:
                tar.add(d, arcname="./pane_contents")
            os.replace(tmp, archive)
        return renamed
    finally:
        shutil.rmtree(work, ignore_errors=True)


def resurrect_dir():
    opt = subprocess.run(["tmux", "show-option", "-gqv", "@resurrect-dir"],
                         capture_output=True, text=True).stdout.strip()
    opt = opt or "~/.local/share/tmux/resurrect"
    return os.path.expanduser(opt.replace("$HOME", "~"))


def marker_path():
    return os.path.join(resurrect_dir(), ".sidebars-stripped")


def main():
    mode = sys.argv[1]
    if mode == "layout":
        sidebars = live_sidebars()
        n = strip_file(sys.argv[2], sidebars)
        with open(marker_path(), "w") as f:
            json.dump([[s, w, i, p] for (s, w), (i, p) in sidebars.items()], f)
        print(f"stripped {n} sidebar panes")
    elif mode == "contents":
        try:
            with open(marker_path()) as f:
                sidebars = {(s, w): (i, p) for s, w, i, p in json.load(f)}
            os.remove(marker_path())
        except FileNotFoundError:
            sidebars = {}
        n = rename_contents(os.path.join(resurrect_dir(), "pane_contents.tar.gz"), sidebars)
        print(f"renamed contents in {n} windows")
    else:
        sys.exit(f"usage: {sys.argv[0]} layout <savefile> | contents")


if __name__ == "__main__":
    main()
