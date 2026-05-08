#!/usr/bin/env python
"""One-shot rename: align canvas section name with the CLIP field key.

Removes the alias shim by making "BG" the single name everywhere:
  attrs_tags_*.json:
    section_order entry "Background"  → "BG"
    section_styles key  "Background"  → "BG"
    section_groups entry "Background" → "BG"
    __col_names__ key   "Background"  → "BG"   (value array kept)
    "Background_Table"                → "BG_Table"

  .py source:
    "Background_Table"                → "BG_Table"
    aisearch_attrs.py default tag-group key                                      → "BG_Table"

After this runs, _HUMAN_LABEL_ALIASES and the parent-candidates logic
in attr_viewer.py become dead code and can be removed.

Idempotent — safe to re-run.
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")


def rename_in_json(path):
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)

    changed = False

    def _swap_in_dict(d, oldk, newk):
        nonlocal changed
        if not isinstance(d, dict):
            return
        if oldk in d and newk not in d:
            # Preserve order: rebuild dict with the same keys but renamed
            new_d = {}
            for k, v in d.items():
                new_d[newk if k == oldk else k] = v
            d.clear()
            d.update(new_d)
            changed = True

    # Top-level keys
    _swap_in_dict(cfg, "Background_Table", "BG_Table")
    _swap_in_dict(cfg, "Background",       "BG")

    # __section_styles__ key
    if "__section_styles__" in cfg:
        _swap_in_dict(cfg["__section_styles__"], "Background", "BG")

    # __col_names__ key (value array of column display labels stays as-is)
    if "__col_names__" in cfg:
        _swap_in_dict(cfg["__col_names__"], "Background", "BG")

    # __section_order__ list
    if isinstance(cfg.get("__section_order__"), list):
        for i, k in enumerate(cfg["__section_order__"]):
            if k == "Background":
                cfg["__section_order__"][i] = "BG"
                changed = True

    # __section_groups__ dict-of-lists
    if isinstance(cfg.get("__section_groups__"), dict):
        for grp, items in cfg["__section_groups__"].items():
            if isinstance(items, list):
                for i, k in enumerate(items):
                    if k == "Background":
                        items[i] = "BG"
                        changed = True

    # __conditions__, __hidden_for__ keyed by widget key
    for meta_key in ("__conditions__", "__hidden_for__"):
        if isinstance(cfg.get(meta_key), dict):
            _swap_in_dict(cfg[meta_key], "Background", "BG")

    if changed:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    return changed


def rename_in_py(path):
    with open(path, encoding="utf-8") as f:
        src = f.read()
    new_src = src.replace("Background_Table", "BG_Table")
    if new_src != src:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_src)
        return True
    return False


def main():
    json_files = []
    py_files = []
    for f in sorted(os.listdir(DATA)):
        if f.startswith("attrs_tags") and f.endswith(".json") and ".bak" not in f:
            json_files.append(os.path.join(DATA, f))
    for f in sorted(os.listdir(ROOT)):
        if f.endswith(".py") and not f.startswith("test_"):
            py_files.append(os.path.join(ROOT, f))

    n_json = sum(rename_in_json(p) for p in json_files)
    n_py   = sum(rename_in_py(p)   for p in py_files)
    print(f"Renamed in {n_json}/{len(json_files)} json file(s) and "
          f"{n_py}/{len(py_files)} .py file(s).")


if __name__ == "__main__":
    main()
