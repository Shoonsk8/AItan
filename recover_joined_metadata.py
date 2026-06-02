#!/usr/bin/env python3
"""recover_joined_metadata.py — one-shot recovery for AItsugi-joined files
whose `note` / `related` / `editable=False` got wiped from attrs JSON when
auto-rename moved the entry to a coded path (the bug fixed in v2.7+).

What it does
============
1.  Loads data/attrs_<PROJECT>.json (default project: AIX).
2.  For every entry that LOOKS like a join output (filename starts with
    "joined_", OR `meta`/embedded AItan block has START:/END: lines)
    AND is missing note / related / editable=False:
        - reads the file's embedded AItan{} block via
          aisearch_attrs._read_embedded_aitan_block(path)
        - if the block has `note` containing 'AItsugi' OR `related` —
          treat the file as a recoverable join output
        - merges the embedded `note` and `related` back into the entry
          (existing values take priority — never overwrites user edits)
        - sets entry["editable"] = False (AItan block doesn't carry the
          flag; locking is the documented policy for join outputs)
3.  Writes a backup (.bak-recover-<ts>) before saving the JSON back.
4.  Prints a per-file summary of what was restored, what was already
    present, and what couldn't be recovered (file missing on disk,
    no embedded block, etc.).

Dry run is the default. Pass --apply to actually write.

Usage
-----
  venv/bin/python recover_joined_metadata.py                 # dry run, AIX
  venv/bin/python recover_joined_metadata.py --apply         # write changes
  venv/bin/python recover_joined_metadata.py --project AI2   # different project
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from collections import Counter

import aisearch_attrs as attrs_mod


def looks_like_join_output(path: str, entry: dict) -> bool:
    """A joined file by either of:
       - basename starts with 'joined_' (the original VJ output naming), or
       - already has an existing 'related' list (we want to top up its note too), or
       - the embedded AItan block reveals AItsugi note text (handled by caller)."""
    bn = os.path.basename(path)
    if bn.lower().startswith("joined_"):
        return True
    note = (entry.get("note") or "").strip()
    if note.startswith("AItsugi") or "AItsugi\n" in note:
        return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", default="AIX",
                    help="Project name (default: AIX). Reads/writes data/attrs_<NAME>.json.")
    ap.add_argument("--apply", action="store_true",
                    help="Actually write changes. Without this, runs as a dry-run.")
    ap.add_argument("--include-all-coded", action="store_true",
                    help="Also probe entries with coded basenames (P00…X…) "
                         "that may be renamed join outputs. Slower (one ffprobe "
                         "per video) but catches the 26 broken files identified.")
    args = ap.parse_args()

    project = args.project
    attrs_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "data", f"attrs_{project}.json")
    if not os.path.isfile(attrs_path):
        print(f"ERROR: {attrs_path} not found", file=sys.stderr)
        return 2

    with open(attrs_path, encoding="utf-8") as f:
        data = json.load(f)

    # Decide which paths to probe.
    # Tier 1: anything that already smells like a join output (cheap check, no I/O).
    candidates: list[str] = []
    for p, e in data.items():
        if not isinstance(e, dict):
            continue
        if looks_like_join_output(p, e):
            candidates.append(p)

    # Tier 2 (opt-in): all coded-basename entries. The broken files all have
    # coded names because auto-rename moved them — we have to probe every
    # video to find which were originally AItsugi outputs.
    if args.include_all_coded:
        _vid_exts = (".mp4", ".mkv", ".mov", ".avi", ".webm")
        for p, e in data.items():
            if not isinstance(e, dict) or p in candidates:
                continue
            if not p.lower().endswith(_vid_exts):
                continue
            candidates.append(p)

    print(f"Project: {project}")
    print(f"Attrs file: {attrs_path}")
    print(f"Total entries: {len(data)}")
    print(f"Candidates to probe: {len(candidates)} "
          f"(--include-all-coded: {args.include_all_coded})")
    print()

    stats = Counter()
    restored: list[tuple[str, list[str]]] = []   # (path, [restored_keys])

    for path in candidates:
        entry = data[path]
        if not isinstance(entry, dict):
            continue
        if not os.path.exists(path):
            stats["missing_on_disk"] += 1
            continue

        # Skip if already healthy — nothing to do.
        if ((entry.get("note") or "").strip()
                and (entry.get("related") or [])
                and entry.get("editable") is False):
            stats["already_healthy"] += 1
            continue

        # Probe the embedded AItan block.
        try:
            block = attrs_mod._read_embedded_aitan_block(path)
        except Exception:
            block = None
        if not isinstance(block, dict):
            stats["no_embedded_block"] += 1
            continue

        # Treat as recoverable only if the block actually carries join-output
        # markers — embedded note starting with 'AItsugi' or a non-empty related list.
        _e_note = (block.get("note") or "").strip()
        _e_rel  = block.get("related") or []
        if not (_e_note.startswith("AItsugi") or _e_rel):
            stats["not_a_join_output"] += 1
            continue

        # Merge — embedded values fill GAPS only; never overwrite present data.
        # (Follows AItan's "auto detection never overwrites existing values" rule.)
        changes: list[str] = []
        cur_note = (entry.get("note") or "").strip()
        if _e_note and _e_note not in cur_note:
            entry["note"] = (cur_note + "\n" + _e_note).strip()
            changes.append("note")
        cur_rel = list(entry.get("related") or [])
        if _e_rel:
            added_rel = False
            for r in _e_rel:
                if isinstance(r, str) and r and r not in cur_rel:
                    cur_rel.append(r)
                    added_rel = True
            if added_rel:
                entry["related"] = cur_rel
                changes.append("related")
        # Lock (editable=False) is policy for join outputs, not stored in
        # the embedded block — set defensively when the block confirms
        # this file IS a join output.
        if entry.get("editable") is not False:
            entry["editable"] = False
            changes.append("editable=False")

        if changes:
            restored.append((path, changes))
            stats["restored"] += 1
        else:
            stats["nothing_to_change"] += 1

    print("Summary")
    print("-------")
    for k, v in sorted(stats.items()):
        print(f"  {k:24s} {v:5d}")
    print()

    if restored:
        print(f"Files restored ({len(restored)}):")
        for p, ks in restored:
            print(f"  [{', '.join(ks)}]  …{p[-90:]}")
        print()

    if not args.apply:
        print("Dry run complete. Re-run with --apply to write.")
        return 0

    # Backup before writing.
    ts = int(time.time())
    bak = f"{attrs_path}.bak-recover-{ts}"
    shutil.copy(attrs_path, bak)
    print(f"Backup written: {bak}")

    # Atomic-ish write — tmp + rename.
    tmp = attrs_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, attrs_path)
    print(f"Updated:        {attrs_path}")
    print(f"Restored {stats['restored']} entries.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
