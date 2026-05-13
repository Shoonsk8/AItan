"""Recovery script: rewrite the first P-code of every file under
/mnt/1TBSSD/AIX/Sophie/0Base/face/00-20 back to P001.

The face-detection auto-assign bug in execute_generate overwrote the
user's FanView-applied P001 with whatever face_recognition guessed
(P031, P013, P022, P05b, P000, …). This script reverses that damage
on disk AND syncs the project's attrs_<project>.json + features_<project>.pt
so the running app's stored state matches.

Usage:
  python recover_sophie_p001.py              # dry-run
  python recover_sophie_p001.py --apply      # actually rename

REQUIREMENT: stop the AIsearch app before running with --apply.
Backups of the two store files are written next to the originals
(`.bak-pid-recovery` suffix).
"""

import os
import sys
import re
import json
import shutil

ROOT          = "/mnt/1TBSSD/AIX/Sophie/0Base/face/00-20"
PROJECT       = "AIX"
DATA_DIR      = "/mnt/1TBSSD/AIsearch/data"
ATTRS_JSON    = os.path.join(DATA_DIR, f"attrs_{PROJECT}.json")
FEATURES_PT   = os.path.join(DATA_DIR, f"features_{PROJECT}.pt")
TARGET_PID    = "001"

# Match the leading person-code:  P + 3 hex chars  OR  PA + 3 hex chars.
# Same shape as `parse_coded_filename` expects.
_P_RE = re.compile(r'^P(A?)([0-9a-f]{3})')


def compute_new_name(old_name):
    """Return the corrected filename, or None if no rename is needed."""
    m = _P_RE.match(old_name)
    if not m:
        return None
    suffix_len = 1 + (1 if m.group(1) else 0) + 3  # 'P' + optional 'A' + 3 hex
    current_pid = m.group(2)
    if current_pid == TARGET_PID and not m.group(1):
        return None
    return "P" + TARGET_PID + old_name[suffix_len:]


def main(apply=False):
    rename_plan = []  # list of (old_path, new_path)
    for dirpath, _, files in os.walk(ROOT):
        for f in files:
            new_name = compute_new_name(f)
            if not new_name:
                continue
            rename_plan.append((os.path.join(dirpath, f),
                                os.path.join(dirpath, new_name)))

    if not rename_plan:
        print(f"No files need renaming under {ROOT}.")
        return

    print(f"\nFiles to rename ({len(rename_plan)}):\n")
    for old, new in rename_plan:
        print(f"  {os.path.relpath(old, ROOT)}\n"
              f"    -> {os.path.basename(new)}")

    if not apply:
        print(f"\n[dry-run] Re-run with --apply to perform the rename.")
        print(f"          The AIsearch app should be CLOSED before --apply.")
        return

    # ── Apply ─────────────────────────────────────────────────────────────
    # Lazy torch import — only needed for --apply.
    import torch

    # Snapshot the stores first so the operation is reversible.
    print()
    if os.path.exists(ATTRS_JSON):
        shutil.copy2(ATTRS_JSON, ATTRS_JSON + ".bak-pid-recovery")
        print(f"Backup: {ATTRS_JSON}.bak-pid-recovery")
    if os.path.exists(FEATURES_PT):
        shutil.copy2(FEATURES_PT, FEATURES_PT + ".bak-pid-recovery")
        print(f"Backup: {FEATURES_PT}.bak-pid-recovery")

    with open(ATTRS_JSON) as f:
        attrs = json.load(f)
    data = torch.load(FEATURES_PT, map_location="cpu")

    renamed = 0
    skipped_collision = 0
    skipped_missing   = 0
    for old, new in rename_plan:
        if not os.path.exists(old):
            print(f"  MISSING:   {old}")
            skipped_missing += 1
            continue
        if os.path.exists(new):
            print(f"  COLLISION: {new} exists, skipping {old}")
            skipped_collision += 1
            continue
        os.rename(old, new)
        # attrs JSON
        if old in attrs:
            entry = attrs.pop(old)
            entry["person_id"] = TARGET_PID
            attrs[new] = entry
        # features.pt
        try:
            paths = data["paths"]
            if old in paths:
                paths[paths.index(old)] = new
        except (KeyError, ValueError):
            pass
        renamed += 1

    with open(ATTRS_JSON, "w") as f:
        json.dump(attrs, f, indent=2, ensure_ascii=False)
    torch.save(data, FEATURES_PT)

    print(f"\nDone. Renamed {renamed}.")
    if skipped_collision:
        print(f"  Collisions skipped: {skipped_collision}")
    if skipped_missing:
        print(f"  Missing-on-disk skipped: {skipped_missing}")
    print("\nRestart the AIsearch app to pick up the corrected state.")


if __name__ == "__main__":
    main(apply=("--apply" in sys.argv))
