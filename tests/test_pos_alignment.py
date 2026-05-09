"""Position alignment between CLIP detection and the canvas display.

This is the bug class that ate a month: CLIP_AUTO_DETECT writes a digit
to a slot that the canvas FieldWidget reads with a DIFFERENT meaning.
HC color was the most recent example — CLIP wrote color into pos 1
(rightmost) but the canvas read pos 1 as Length, so a detected blonde
showed up as "Long" hair on display.

These tests assert that for every (field, sub-field) pair, both modules
agree on which digit position holds it.
"""
import re

import pytest

from aisearch_attrs import CLIP_AUTO_DETECT


# Canvas truth — same dict from attr_viewer.py:_SUBPOS, mirrored here so
# the test fails if either side drifts. Keep in sync with _SUBPOS.
_CANVAS_SUBPOS = {
    "CL": {"Bot": 1, "BotColor": 2, "Top": 3, "TopColor": 4},
    "HC": {"Length": 1, "Style": 2, "Color": 3},
    "FA": {"Direction": 1, "Vert": 2, "Vertical": 2},
    "PM": {"Motion": 1, "Posture": 2},
    "CS": {"Light": 1, "Lighting": 1, "Angle": 2, "Shot": 3},
    "E":  {"Color": 1, "Additional": 2, "Modifier": 2},
}


def _clip_specs_for(field):
    """Return CLIP_AUTO_DETECT entries for `field` keyed by pos.
    Accepts either short letter ("hc") or long storage key ("hair") —
    translates short to long since spec["field"] is the long form."""
    from aisearch_attrs import _STORAGE_KEY_MAP
    long = _STORAGE_KEY_MAP.get(field.lower(), field.lower())
    return {s["pos"]: s for s in CLIP_AUTO_DETECT if s.get("field") == long}


def test_subpos_mirrors_attr_viewer():
    """Make sure the constant copied above still matches what the GUI
    actually uses — if attr_viewer.py changes _SUBPOS, this test must
    fail until the mirror is updated."""
    import attr_viewer
    src = open(attr_viewer.__file__, encoding="utf-8").read()
    # Loose check: every (field → sub-key → pos) we reference must
    # appear textually in attr_viewer's _SUBPOS literal. Looking up the
    # actual dict requires running the code path, which needs Qt.
    for field, mapping in _CANVAS_SUBPOS.items():
        for sub_key, pos in mapping.items():
            pat = rf'"{re.escape(sub_key)}":\s*{pos}\b'
            assert re.search(pat, src), (
                f"_SUBPOS in attr_viewer.py is missing/changed entry "
                f"{field}: {sub_key!r} → {pos}; mirror in this test or "
                f"the canvas + CLIP will disagree.")


@pytest.mark.parametrize("field, expected", [
    # field, {pos: hint that must appear in at least one prompt for that pos}
    ("hc", {1: "hair", 2: "hair", 3: "hair"}),
    ("cl", {1: "bottom", 2: "bottom", 3: "top", 4: "top"}),
    ("fa", {1: "facing", 2: ("tilted", "level", "head")}),
    ("pm", {1: ("motion", "posing", "still", "walking", "running"),
             2: ("standing", "sitting", "lying", "kneeling")}),
    ("cs", {1: ("light", "lit", "lighting"),
             2: ("angle", "level", "low", "high"),
             3: ("close-up", "wide", "medium", "shot", "body")}),
])
def test_clip_pos_descriptions_match_canvas_role(field, expected):
    """Every CLIP pos's prompts must talk about the SAME thing the
    canvas slot at that pos shows. If they don't, CLIP is writing a
    color into a length slot (or similar)."""
    specs = _clip_specs_for(field)
    for pos, hint in expected.items():
        assert pos in specs, f"CLIP_AUTO_DETECT missing {field} pos={pos}"
        prompts = " ".join(opt[1] for opt in specs[pos]["options"]).lower()
        hints = (hint,) if isinstance(hint, str) else hint
        assert any(h in prompts for h in hints), (
            f"CLIP_AUTO_DETECT[{field} pos={pos}] prompts don't mention any of "
            f"{hints!r} — that pos may be wired to the wrong sub-field.")


def test_hc_color_at_pos_3_after_2026_05_fix():
    """Pin the specific bug: HC color must live at pos 3 (leftmost),
    not pos 1, so that filename built from CLIP detection and canvas
    display agree on what's color vs. length."""
    specs = _clip_specs_for("hc")
    assert 3 in specs, "HC pos 3 spec missing"
    color_prompts = " ".join(o[1] for o in specs[3]["options"]).lower()
    # Color words ought to appear in the pos 3 prompts.
    assert any(w in color_prompts for w in
               ("blonde", "brunette", "black", "red", "blue", "white")), \
        "HC pos 3 prompts don't look like color prompts — was the recent " \
        "color↔length flip reverted?"
    # And length words MUST NOT.
    assert not any(w in color_prompts for w in
                   ("buzzcut", "shoulder", "waist", "bald")), \
        "HC pos 3 prompts mention length terms — color↔length got swapped."


def test_hc_length_at_pos_1():
    specs = _clip_specs_for("hc")
    assert 1 in specs, "HC pos 1 spec missing"
    length_prompts = " ".join(o[1] for o in specs[1]["options"]).lower()
    assert any(w in length_prompts for w in
               ("buzzcut", "shoulder", "waist", "bald", "long", "short")), \
        "HC pos 1 prompts don't look like length prompts."


def test_background_alias_resolves_to_storage_key():
    """Canvas section "Background" must resolve to the long storage
    key "background" — same convention all other sections follow
    after the storage-key rename. Earlier this resolved to the short
    "bg" and the Update entry-clear was popping the wrong slot."""
    from attr_viewer import _SECTION_KEY_TO_FIELD
    assert _SECTION_KEY_TO_FIELD.get("Background") == "background"


def test_alias_map_is_minimal():
    """Only deliberate canvas-name renames belong in the alias map.
    Adding more (Hair → hc, Eyes → e, etc.) creates shims for sections
    that aren't actually renamed; they would just hide future drift."""
    from attr_viewer import _HUMAN_LABEL_ALIASES
    assert set(_HUMAN_LABEL_ALIASES.keys()) == {"Background"}
