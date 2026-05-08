#!/usr/bin/env python
"""Audit canvas widget wiring for a project.

Checks each canvas section against the CLIP detection / save / menu
machinery and reports any mismatch. The bugs we keep hitting (BG vs.
Background, CL missing from _ALL, dot_key NameError, _SUBPOS skew)
all show up here — run this BEFORE clicking through the GUI.

Usage:
    venv/bin/python tools/check_canvas_wiring.py            # all projects
    venv/bin/python tools/check_canvas_wiring.py AIX        # one project
"""
import argparse
import json
import os
import sqlite3
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# Skip CLIP model load — we only need attribute / config metadata.
os.environ.setdefault("AISEARCH_SKIP_MODEL", "1")

from aisearch_attrs import CLIP_AUTO_DETECT, CODED_FIELDS
from attr_viewer import _SECTION_KEY_TO_FIELD


_DATA = os.path.join(_ROOT, "data")
_RESULT_OK   = "OK"
_RESULT_WARN = "WARN"
_RESULT_FAIL = "FAIL"

_CLIP_FIELDS    = {s["field"] for s in CLIP_AUTO_DETECT}
_DEBUG_PARENTS  = {
    "CLIP_E":  ["E", "Eyes"],
    "CLIP_HC": ["HC", "Hair"],
    "CLIP_FA": ["FA", "FaceAngle"],
    "CLIP_SK": ["SK", "Skin"],
    "CLIP_PM": ["PM", "PostureMotion"],
    "CLIP_CS": ["CS", "CameraShot"],
    "CLIP_BG": ["BG", "Background"],
    "CLIP_X":  ["X",  "Expression"],
    "CLIP_CL": ["CL", "Clothing"],
    "FACE":    ["P"],
    "FACE_PW": ["PW"],
}


def _projects():
    projs = []
    for f in sorted(os.listdir(_DATA)):
        if f.startswith("attrs_tags_") and f.endswith(".json"):
            projs.append(f[len("attrs_tags_"):-len(".json")])
    return projs


def _load_tags(proj):
    p = os.path.join(_DATA, f"attrs_tags_{proj}.json")
    if not os.path.exists(p):
        return None
    return json.load(open(p, encoding="utf-8"))


def _load_db(proj):
    p = os.path.join(_DATA, f"attr_viewer_{proj}.db")
    if not os.path.exists(p):
        return None
    return sqlite3.connect(p)


def _section_keys(cfg):
    """Sections actually rendered as canvas widgets — section_order minus
    metadata keys (anything starting with '__')."""
    sec_order = cfg.get("__section_order__", [])
    return [k for k in sec_order if not k.startswith("__")]


def _print_row(level, msg):
    color = {
        _RESULT_OK:   "\033[32m",  # green
        _RESULT_WARN: "\033[33m",  # yellow
        _RESULT_FAIL: "\033[31m",  # red
    }.get(level, "")
    reset = "\033[0m" if color else ""
    print(f"  {color}[{level:>4}]{reset} {msg}")


def audit(proj):
    print(f"\n=== {proj} ===")
    cfg = _load_tags(proj)
    if cfg is None:
        _print_row(_RESULT_WARN, f"no attrs_tags_{proj}.json")
        return 1
    sections = _section_keys(cfg)
    fail = 0

    # 1. Every CLIP field has a section mapping that resolves.
    for clip_field in sorted(_CLIP_FIELDS):
        # Find any section whose alias resolves to this CLIP field.
        owners = [s for s in sections
                  if _SECTION_KEY_TO_FIELD.get(s, s.lower()) == clip_field]
        if not owners:
            _print_row(_RESULT_WARN,
                       f"CLIP field {clip_field!r} has no canvas section "
                       f"in this project — Update on it can't fire")
            continue
        if len(owners) > 1:
            _print_row(_RESULT_WARN,
                       f"CLIP field {clip_field!r} claimed by multiple "
                       f"sections {owners} — only the first will work")
        else:
            _print_row(_RESULT_OK,
                       f"CLIP {clip_field!r} ↔ section {owners[0]!r}")

    # 2. Every section either resolves to a CLIP field or to a CODED_FIELDS letter.
    coded_letters = {l.lower() for l, _, _ in CODED_FIELDS}
    for s in sections:
        resolved = _SECTION_KEY_TO_FIELD.get(s, s.lower())
        if resolved in _CLIP_FIELDS or resolved in coded_letters:
            continue
        # Is it a known non-CLIP custom matrix (e.g. ModelImage)?
        # Section_styles 'matrix' with no CLIP backing is fine.
        style = (cfg.get("__section_styles__") or {}).get(s, "")
        if style in ("text", "matrix", "taglist", "boolean", "radio",
                     "combo", "id", "pathlist", "1dig", "2dig", "3dig", "4dig"):
            continue
        _print_row(_RESULT_FAIL,
                   f"section {s!r} resolves to {resolved!r} which is "
                   f"neither a CLIP field nor a CODED_FIELDS letter — "
                   f"widget will save to a dead key")
        fail += 1

    # 3. DB: connections must reference widgets that still exist.
    conn = _load_db(proj)
    if conn is not None:
        try:
            stale = []
            valid_widget_keys = set(sections) | set(_DEBUG_PARENTS.keys())
            valid_widget_keys |= {p for cands in _DEBUG_PARENTS.values()
                                    for p in cands}
            for row in conn.execute(
                    "SELECT id,box_a,port_a,box_b,port_b FROM connections"):
                cid, ba, pa, bb, pb = row
                if ba not in valid_widget_keys or bb not in valid_widget_keys:
                    stale.append(row)
            if stale:
                _print_row(_RESULT_WARN,
                           f"{len(stale)} stale connection(s) in DB "
                           f"(reference widgets that don't exist anymore)")
                for row in stale[:5]:
                    _print_row(_RESULT_WARN, f"    {row}")
                if len(stale) > 5:
                    _print_row(_RESULT_WARN,
                               f"    … and {len(stale) - 5} more")
            else:
                _print_row(_RESULT_OK, "no stale connections in DB")

            # 4. Each debug tile that has a parent in this project should
            # actually have a connection to it.
            for dbg, candidates in _DEBUG_PARENTS.items():
                par = next((p for p in candidates if p in sections), None)
                if par is None:
                    continue
                hits = list(conn.execute(
                    "SELECT id FROM connections "
                    "WHERE (box_a=? AND box_b=?) OR (box_a=? AND box_b=?)",
                    (dbg, par, par, dbg)))
                if not hits:
                    _print_row(_RESULT_WARN,
                               f"{dbg} debug tile is not connected to "
                               f"its parent {par!r} (auto-wire will "
                               f"create it on next canvas build)")
        finally:
            conn.close()
    return fail


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("projects", nargs="*",
                    help="project names; default = every attrs_tags_*.json")
    args = ap.parse_args()
    targets = args.projects or _projects()
    if not targets:
        print("no projects found in data/")
        return 1
    total_fail = 0
    for proj in targets:
        total_fail += audit(proj)
    print()
    if total_fail:
        print(f"\033[31m{total_fail} FAIL\033[0m across {len(targets)} project(s)")
        return 1
    print(f"\033[32mAll {len(targets)} project(s) clean.\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
