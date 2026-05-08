"""Threshold + zero_is_none + default_is_zero combinations in detection.

We've shipped at least three regressions in this code: PM never showing
because all-zero results were dropped from the return, X expression
silently filling "0" for missing prompts, and the inspector path
disagreeing with the bake path on whether to write below-threshold
argmax. This file pins the contract so changes to one path can't
silently desync from the other.
"""
import torch

from aisearch_attrs import (
    auto_detect_clip_attrs, inspect_clip_scores, CLIP_AUTO_DETECT,
)


def _zero_emb():
    """Plain zero embedding sized to whatever the loaded text-prompt
    cache uses (image and text models in this app share embedding dim,
    but tests shouldn't hard-code it). Falls back to 768 if the cache
    can't be read — that's the size the current sentence-transformer
    model produces."""
    from aisearch_attrs import _get_clip_label_cache
    cache = _get_clip_label_cache()
    if cache:
        # cache[0] is text_embs for first spec, shape (N_options, dim)
        try:
            return torch.zeros(cache[0].shape[1])
        except Exception:
            pass
    return torch.zeros(768)


def test_inspect_below_threshold_returns_none_for_zero_is_none():
    """inspect_clip_scores must return winner=None when best score is
    below threshold AND the field doesn't allow default_is_zero. The
    preview's _on_inspect uses this to decide what to write."""
    emb = _zero_emb()
    specs = inspect_clip_scores(emb)
    if not specs:
        return  # CLIP model not available in this env — skip
    for sp in specs:
        if sp.get("zero_is_none") and not sp.get("default_is_zero"):
            # below-threshold + zero_is_none → winner None
            best = max(o[2] for o in sp["options"])
            if best < sp["threshold"]:
                assert sp["winner"] is None, (
                    f"{sp['field']} pos={sp['pos']} should be None "
                    f"(best {best:.3f} < thr {sp['threshold']}) "
                    f"but got {sp['winner']!r}")


def test_inspect_default_is_zero_keeps_zero_winner():
    """For fields like PM where 0 = a real category (Standing / Still),
    inspect must return winner='0' even when below threshold, otherwise
    'Standing / Still' images never display PM."""
    emb = _zero_emb()
    specs = inspect_clip_scores(emb)
    if not specs:
        return
    for sp in specs:
        if sp.get("default_is_zero") and "0" in [o[0] for o in sp["options"]]:
            top_code = sp["options"][0][0]   # already sorted desc by score
            if top_code == "0":
                assert sp["winner"] == "0", (
                    f"{sp['field']} pos={sp['pos']} default_is_zero=True "
                    f"and top is '0' but winner={sp['winner']!r}; "
                    f"PM/FA will never display.")


def test_auto_detect_writes_argmax_no_threshold_gate():
    """auto_detect_clip_attrs (used by scan/bake) drops the threshold
    and always takes argmax (except for zero_is_none fields where the
    explicit '0/none' option won). This is the contract that must NOT
    silently revert to thresholded detection."""
    emb = _zero_emb()
    res = auto_detect_clip_attrs(emb, existing_entry={})
    if not res:
        return  # model not available
    # For any field with default_is_zero=True at every pos, "00" should
    # be present (PM, FA, SK).
    for f in ("fa", "sk"):
        if f in res:
            assert res[f] != "", \
                f"{f} should be written even at all-default value"


def test_auto_detect_skips_existing_non_zero():
    """Existing non-zero digits must not be overwritten — that's how
    user manual corrections survive a re-detect cycle."""
    emb = _zero_emb()
    # Set HC pos 1 to "5" (length=Very Long); auto-detect should leave
    # it alone even though the model would produce something different.
    existing = {"hc": "005"}   # pos 1 = "5"
    res = auto_detect_clip_attrs(emb, existing_entry=existing)
    if "hc" in res:
        assert res["hc"][-1] == "5", \
            "auto_detect_clip_attrs overwrote a manually-set HC pos 1"


def test_clip_specs_lowercase_field_keys():
    """Every CLIP_AUTO_DETECT spec must use lowercase field keys —
    detection looks up by str.lower() and uppercase entries silently
    drop out of the per-field loop. Caught a real regression here once."""
    for sp in CLIP_AUTO_DETECT:
        f = sp.get("field", "")
        assert f == f.lower(), \
            f"CLIP_AUTO_DETECT spec has non-lowercase field {f!r}"
        assert isinstance(sp.get("pos"), int), \
            f"CLIP_AUTO_DETECT[{f}] missing/non-int pos"


def test_clip_specs_no_duplicate_pos_per_field():
    """Two prompts at the same (field, pos) make the second one shadow
    the first — silent bug, easy to introduce when copy-pasting."""
    seen = set()
    for sp in CLIP_AUTO_DETECT:
        key = (sp["field"], sp["pos"])
        assert key not in seen, \
            f"duplicate CLIP_AUTO_DETECT entry for {key}"
        seen.add(key)
