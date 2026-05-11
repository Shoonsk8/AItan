#!/usr/bin/env python3
"""Strip CLIP/FACE debug description text from attrs_*.json files.

These keys are debug/inspection caches that bloat the saved metadata. The
running app recomputes them on demand (clip_inspect_mode=always) and shows
them in the preview's debug panels — they don't need to be persisted.

Usage:
    python strip_clip_face.py                 # all projects (data/attrs_*.json)
    python strip_clip_face.py AIX             # one project
    python strip_clip_face.py AIX AI PIC      # several projects
    python strip_clip_face.py --dry-run       # report without writing

A timestamped backup <file>.bak-<ts> is created beside each modified file.
Safe to re-run — already-clean files report 0 keys and aren't rewritten.
"""
import json
import os
import sys
import time
import glob
import shutil
import argparse

# Keys that are CLIP/FACE debug caches and should never be persisted.
STRIP_KEYS = frozenset({
    "CLIP",
    "CLIP_HC", "CLIP_FA", "CLIP_SK", "CLIP_PM",
    "CLIP_E", "CLIP_CS", "CLIP_BG", "CLIP_X",
    "CLIP_A",
    "FACE",
    "_project",   # runtime marker that also leaks into saves
})

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def strip_file(path: str, dry_run: bool = False) -> dict:
    """Strip debug keys from one attrs_*.json file. Returns stats dict."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    per_key_count = {k: 0 for k in STRIP_KEYS}
    total_stripped = 0
    entries_touched = 0

    for _path, entry in data.items():
        if not isinstance(entry, dict):
            continue
        hits = [k for k in STRIP_KEYS if k in entry]
        if not hits:
            continue
        entries_touched += 1
        for k in hits:
            per_key_count[k] += 1
            total_stripped += 1
            if not dry_run:
                del entry[k]

    stats = {
        "file": path,
        "total_entries": len(data),
        "entries_touched": entries_touched,
        "total_stripped": total_stripped,
        "per_key": {k: v for k, v in per_key_count.items() if v},
        "size_before": os.path.getsize(path),
        "dry_run": dry_run,
    }

    if total_stripped and not dry_run:
        # Back up the original, then atomic-write the cleaned version
        bak = f"{path}.bak-{int(time.time())}"
        shutil.copy2(path, bak)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
        stats["backup"] = bak
        stats["size_after"] = os.path.getsize(path)
    else:
        stats["size_after"] = stats["size_before"]

    return stats


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("projects", nargs="*",
                    help="Project names to clean (e.g. AIX AI). Omit to process all.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would be stripped without writing.")
    args = ap.parse_args()

    if args.projects:
        targets = []
        for name in args.projects:
            # "default" → attrs_default.json would be wrong; default project has no suffix
            fname = f"attrs_{name}.json"
            path = os.path.join(DATA_DIR, fname)
            if not os.path.exists(path):
                print(f"!! {fname} not found")
                continue
            targets.append(path)
    else:
        # All per-project attrs files (exclude tags/workspace/rules/person variants)
        exclude_markers = ("tags", "workspace", "rules", "person", "aliases",
                           "groups", "corrections", "metadata_mapping")
        targets = []
        for p in sorted(glob.glob(os.path.join(DATA_DIR, "attrs_*.json"))):
            name = os.path.basename(p).lower()
            if any(m in name for m in exclude_markers):
                continue
            targets.append(p)

    if not targets:
        print("No files to process.")
        return 1

    print(f"{'DRY RUN: ' if args.dry_run else ''}Processing {len(targets)} file(s):\n")

    total_stripped = 0
    for path in targets:
        stats = strip_file(path, dry_run=args.dry_run)
        name = os.path.basename(path)
        if stats["total_stripped"] == 0:
            print(f"  {name:30} already clean ({stats['total_entries']} entries)")
            continue
        before = stats["size_before"]
        after = stats["size_after"]
        shrink = 100 * (before - after) // before if before else 0
        action = "WOULD STRIP" if args.dry_run else "stripped"
        print(f"  {name:30} {action} {stats['total_stripped']:4} keys "
              f"from {stats['entries_touched']:3}/{stats['total_entries']:3} entries "
              f"({before:,}B → {after:,}B, {shrink}% smaller)")
        for k, n in sorted(stats["per_key"].items()):
            print(f"      {k:12} x{n}")
        if not args.dry_run:
            print(f"      backup: {os.path.basename(stats['backup'])}")
        total_stripped += stats["total_stripped"]

    print(f"\nTotal keys {'that would be ' if args.dry_run else ''}stripped: {total_stripped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
