#!/usr/bin/env python
"""Rewrite every attrs_*.json so its coded-field keys are the long
human-readable names ("hair", "background", …) instead of the short
2-letter codes ("hc", "bg", …).

Round-trip safe: load() + save() now translate transparently, so this
script is a one-shot bulk migration. Re-running is a no-op.

Usage:
    venv/bin/python tools/migrate_storage_keys.py            # all projects
    venv/bin/python tools/migrate_storage_keys.py AIX TEST   # specific ones

Backups are saved as attrs_<proj>.json.bak-key-migrate.
"""
import argparse
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("AISEARCH_SKIP_MODEL", "1")

from aisearch_attrs import _STORAGE_KEY_MAP

DATA = os.path.join(ROOT, "data")


def _translate_keys_dict(d):
    """Return (new_dict, count_of_renames). Idempotent."""
    if not isinstance(d, dict):
        return d, 0
    out = {}
    n = 0
    for k, v in d.items():
        nk = _STORAGE_KEY_MAP.get(k, k)
        if nk != k:
            n += 1
        out[nk] = v
    return out, n


def migrate_file(path):
    """Translate short → long keys at:
      1. The top-level entry dict (e.g. entry['hc'] → entry['hair']).
      2. The embedded AItan block inside entry['meta']['_aitan'] —
         that's the portable metadata copy and lives nested.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return 0, 0
    n_entries = 0
    n_translated_keys = 0
    for fpath, entry in data.items():
        if not isinstance(entry, dict):
            continue
        # Top-level
        new_entry, n_top = _translate_keys_dict(entry)
        # Nested AItan block in meta
        n_aitan = 0
        meta = new_entry.get("meta")
        if isinstance(meta, dict):
            ait = meta.get("_aitan")
            if isinstance(ait, dict):
                new_ait, n_aitan = _translate_keys_dict(ait)
                if n_aitan:
                    new_meta = dict(meta)
                    new_meta["_aitan"] = new_ait
                    new_entry["meta"] = new_meta
        translated = n_top + n_aitan
        if translated:
            data[fpath] = new_entry
            n_entries += 1
            n_translated_keys += translated
    if n_translated_keys:
        shutil.copy2(path, path + ".bak-key-migrate")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    return n_entries, n_translated_keys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("projects", nargs="*")
    args = ap.parse_args()

    targets = []
    if args.projects:
        for p in args.projects:
            f = os.path.join(DATA, f"attrs_{p}.json")
            if os.path.exists(f):
                targets.append(f)
            else:
                print(f"missing: {f}", file=sys.stderr)
    else:
        for f in sorted(os.listdir(DATA)):
            if (f.startswith("attrs_") and f.endswith(".json")
                    and ".bak" not in f and "tags" not in f):
                targets.append(os.path.join(DATA, f))

    if not targets:
        print("nothing to migrate")
        return 0

    grand_e = grand_k = 0
    for path in targets:
        n_e, n_k = migrate_file(path)
        grand_e += n_e; grand_k += n_k
        rel = os.path.basename(path)
        if n_k:
            print(f"  {rel}: {n_e} entries, {n_k} keys → long form")
        else:
            print(f"  {rel}: already long-form")
    print(f"\nTotal: {grand_e} entries, {grand_k} short keys converted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
