"""migrate_collapse_keys.py
One-shot: collapse the multiple storage keys per CODED_FIELDS field down
to a single canonical lowercase-letter key in every attrs_*.json.

Per field (letter, label) tries:
    entry[letter]           — matrix sections that use the bare uppercase letter (A, X)
    entry[label]            — matrix sections that use the spelled-out section name (Tool, Background)
    entry[letter.lower()]   — filename-parsed legacy
    entry["cf_" + lower]    — CLIP/metadata auto-detection

First non-empty wins; result is written to entry[letter.lower()] and the
other keys are removed. Idempotent — running twice is a no-op.

Each touched JSON is backed up to <name>.bak-collapse-<ts> before write.
"""
import json
import os
import sys
import time

_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _DIR)
from aisearch_attrs import CODED_FIELDS  # noqa


def _first_nonempty(entry, keys):
    """Return (key, value) of the first key with a non-empty string value, or (None, None)."""
    for k in keys:
        v = entry.get(k)
        if isinstance(v, str) and v.strip():
            return k, v.strip()
    return None, None


def collapse_entry(entry):
    """Collapse all CODED_FIELDS storage keys to canonical lowercase. Returns
    a (changed, fields_collapsed) tuple."""
    if not isinstance(entry, dict):
        return False, 0
    changed = False
    fields_collapsed = 0
    for letter, label, _digits in CODED_FIELDS:
        if letter == "J":
            continue   # j stays as-is (timestamp, no matrix conflict)
        lk = letter.lower()
        # Candidate keys, in priority order
        candidates = [letter, label, lk, f"cf_{lk}"]
        # De-dup while preserving order
        _seen = set()
        candidates = [k for k in candidates if k and not (k in _seen or _seen.add(k))]
        winner_key, winner_val = _first_nonempty(entry, candidates)
        # Drop all candidate keys (canonical key gets re-written below)
        for k in candidates:
            if k in entry:
                del entry[k]
                changed = True
        # Write the winning value to the canonical lowercase letter key
        if winner_val:
            entry[lk] = winner_val
            fields_collapsed += 1
    return changed, fields_collapsed


def collapse_file(path):
    """Collapse all entries in one attrs_*.json. Returns dict of stats."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return {"error": str(e), "changed": False}
    if not isinstance(data, dict):
        return {"error": "not a dict", "changed": False}

    total_entries = 0
    changed_entries = 0
    total_fields = 0
    for fpath, entry in data.items():
        if not isinstance(entry, dict):
            continue
        total_entries += 1
        ch, n = collapse_entry(entry)
        if ch:
            changed_entries += 1
            total_fields += n

    if changed_entries == 0:
        return {"error": None, "changed": False,
                "entries": total_entries, "fields": 0}

    bak = path + f".bak-collapse-{int(time.time())}"
    os.rename(path, bak)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return {"error": None, "changed": True,
            "entries": total_entries, "changed_entries": changed_entries,
            "fields": total_fields, "bak": os.path.basename(bak)}


def main():
    import glob
    data_dir = os.path.join(_DIR, "data")
    pattern = os.path.join(data_dir, "attrs_*.json")
    targets = [
        f for f in sorted(glob.glob(pattern))
        if not any(s in f for s in (".bak", ".pre-restore", ".wiped-"))
        and not os.path.basename(f).startswith("attrs_tags")
    ]
    if not targets:
        print("No attrs_*.json files found under", data_dir)
        return
    grand_total_entries = 0
    grand_changed_entries = 0
    grand_total_fields = 0
    print(f"Collapsing storage keys across {len(targets)} attrs JSON files…\n")
    for f in targets:
        name = os.path.basename(f)
        r = collapse_file(f)
        if r.get("error"):
            print(f"  SKIP {name}: {r['error']}")
            continue
        if not r["changed"]:
            print(f"  ok   {name}  ({r['entries']} entries, nothing to collapse)")
            continue
        print(f"  done {name}  ({r['changed_entries']}/{r['entries']} entries, "
              f"{r['fields']} field collapses)  bak={r['bak']}")
        grand_total_entries += r["entries"]
        grand_changed_entries += r["changed_entries"]
        grand_total_fields += r["fields"]
    print(f"\nTotal: {grand_changed_entries} entries collapsed across "
          f"{grand_total_fields} fields. Backups left as *.bak-collapse-<ts>.")


if __name__ == "__main__":
    main()
