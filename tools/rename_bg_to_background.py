#!/usr/bin/env python
"""Reverse of rename_background_to_bg.py — restore the human-readable
canvas section name `Background` while keeping `bg` (lowercase) as the
storage key and `BG` (uppercase) as the filename code.

User clarified: BG is the filename / coded-field letter; the canvas
section was supposed to stay named "Background" so the visible tile
title is human-readable. The earlier rename conflated those layers.

Idempotent — safe to re-run.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")


def _swap_in_dict(d, oldk, newk):
    if not isinstance(d, dict) or oldk not in d or newk in d:
        return False
    new_d = {}
    for k, v in d.items():
        new_d[newk if k == oldk else k] = v
    d.clear()
    d.update(new_d)
    return True


def rename_in_json(path):
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    changed = False
    if _swap_in_dict(cfg, "BG_Table", "Background_Table"): changed = True
    if _swap_in_dict(cfg, "BG", "Background"):             changed = True
    if "__section_styles__" in cfg and _swap_in_dict(cfg["__section_styles__"], "BG", "Background"): changed = True
    if "__col_names__"    in cfg and _swap_in_dict(cfg["__col_names__"],    "BG", "Background"): changed = True
    for meta_key in ("__conditions__", "__hidden_for__"):
        if isinstance(cfg.get(meta_key), dict):
            if _swap_in_dict(cfg[meta_key], "BG", "Background"): changed = True
    if isinstance(cfg.get("__section_order__"), list):
        for i, k in enumerate(cfg["__section_order__"]):
            if k == "BG":
                cfg["__section_order__"][i] = "Background"
                changed = True
    if isinstance(cfg.get("__section_groups__"), dict):
        for grp, items in cfg["__section_groups__"].items():
            if isinstance(items, list):
                for i, k in enumerate(items):
                    if k == "BG":
                        items[i] = "Background"
                        changed = True
    if changed:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    return changed


def rename_in_py(path):
    with open(path, encoding="utf-8") as f:
        src = f.read()
    new_src = src.replace("BG_Table", "Background_Table")
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
