#!/usr/bin/env python
"""Run hair-color CLIP detection on one or more images and print the
top scores, so you can spot-check whether the discriminative prompt
rewrite is actually picking the right color.

Usage:
    venv/bin/python tools/test_hair_colors.py IMAGE [IMAGE ...]
    venv/bin/python tools/test_hair_colors.py --dir DIR     # all images in DIR
    venv/bin/python tools/test_hair_colors.py --expect blonde IMAGE  # assert blonde wins
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)


# ── Color name lookup ────────────────────────────────────────────────────────
_COLOR_LABELS = {
    "0": "no-hair", "1": "black",    "2": "dark-brown", "3": "light-brown",
    "4": "blonde",  "5": "platinum", "6": "red",        "7": "pink",
    "8": "ginger",  "9": "gray",     "a": "white",      "b": "blue",
    "c": "yellow",  "d": "green",    "e": "rainbow",    "f": "neon",
}


def _gather_paths(args):
    paths = []
    if args.dir:
        for name in sorted(os.listdir(args.dir)):
            p = os.path.join(args.dir, name)
            if os.path.isfile(p) and name.lower().endswith(
                    (".jpg", ".jpeg", ".png", ".webp", ".bmp")):
                paths.append(p)
    paths.extend(args.images)
    return paths


def _hair_color_spec(specs):
    """Find the HC color spec — pos 3 after the 2026-05 alignment fix."""
    for sp in specs:
        if sp["field"] == "HC" and sp["pos"] == 3:
            return sp
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("images", nargs="*", help="image paths to test")
    ap.add_argument("--dir", help="run on every image in this directory")
    ap.add_argument("--expect", help="color name (or hex code) the top "
                                      "prediction must match — exits 1 on mismatch")
    ap.add_argument("--top", type=int, default=5, help="how many top entries to show")
    args = ap.parse_args()

    paths = _gather_paths(args)
    if not paths:
        ap.error("no images provided — pass paths or --dir")

    # Lazy import — model loading is slow.
    print("loading CLIP model…", file=sys.stderr)
    import aisearch_logic as logic
    from aisearch_attrs import inspect_clip_scores

    name_to_code = {v: k for k, v in _COLOR_LABELS.items()}
    expected_code = None
    if args.expect:
        e = args.expect.lower()
        expected_code = e if e in _COLOR_LABELS else name_to_code.get(e)
        if expected_code is None:
            ap.error(f"unknown --expect value {args.expect!r}; "
                     f"valid: {sorted(_COLOR_LABELS.values())}")

    fail = 0
    for path in paths:
        if not os.path.isfile(path):
            print(f"\n{path}: missing")
            fail += 1
            continue
        try:
            emb = logic.extract_feature(path)
        except Exception as e:
            print(f"\n{path}: extract_feature error: {e}")
            fail += 1
            continue
        if emb is None:
            print(f"\n{path}: no embedding")
            fail += 1
            continue
        specs = inspect_clip_scores(emb)
        sp = _hair_color_spec(specs)
        if sp is None:
            print(f"\n{path}: no HC color spec — ran an unconfigured project?")
            fail += 1
            continue

        opts = sp["options"]   # already sorted desc by score
        winner = sp["winner"] or "—"
        win_label = _COLOR_LABELS.get(winner, "?")
        argmax_code = opts[0][0] if opts else None
        argmax_label = _COLOR_LABELS.get(argmax_code, "?")
        print(f"\n{os.path.basename(path)}")
        print(f"  threshold-gated winner = {winner} ({win_label})")
        print(f"  argmax (what auto_detect actually writes) = "
              f"{argmax_code} ({argmax_label})  score={opts[0][2]:.4f}")
        for code, _, score in opts[:args.top]:
            label = _COLOR_LABELS.get(code, "?")
            mark = " *" if code == argmax_code else "  "
            print(f"  {mark} {code} {label:<11} {score:.4f}")

        if expected_code is not None:
            ok = (argmax_code == expected_code)
            tag = "PASS" if ok else "FAIL"
            print(f"  [{tag}] expected {expected_code} ({_COLOR_LABELS[expected_code]}), "
                  f"argmax {argmax_code} ({_COLOR_LABELS.get(argmax_code, '?')})")
            if not ok:
                fail += 1

    if expected_code is not None and fail:
        print(f"\n{fail} mismatch(es)", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
