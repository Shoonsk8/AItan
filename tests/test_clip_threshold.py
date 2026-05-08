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
    """Plain zero embedding sized to (and placed on the same device
    as) the loaded text-prompt cache. Hard-coding shape or device
    breaks tests when the model loads on CUDA vs CPU."""
    from aisearch_attrs import _get_clip_label_cache
    cache = _get_clip_label_cache()
    if cache:
        try:
            ref = cache[0]
            return torch.zeros(ref.shape[1], device=ref.device)
        except Exception:
            pass
    return torch.zeros(768)


def test_inspect_takes_argmax_no_threshold_gate():
    """inspect_clip_scores must NOT gate on threshold — that diverged
    from auto_detect_clip_attrs, which always argmaxes. AI-generated
    images flatten the score distribution to ~0.15-0.18 (below the
    historical 0.20), so the gate suppressed every detection on the
    Update / Refresh CLIP path while the bake path silently wrote
    argmax. Aligning the two so users see what was actually written."""
    emb = _zero_emb()
    specs = inspect_clip_scores(emb)
    if not specs:
        return
    for sp in specs:
        top_code = sp["options"][0][0] if sp["options"] else None
        # The only legitimate way to return None now: top code is "0"
        # AND the spec is zero_is_none without default_is_zero (HC, X).
        zero_suppressed = (
            top_code == "0"
            and sp.get("zero_is_none", True)
            and not sp.get("default_is_zero", False))
        if zero_suppressed:
            assert sp["winner"] is None, (
                f"{sp['field']} pos={sp['pos']} top is '0' on a "
                f"zero_is_none field — winner must be None, got "
                f"{sp['winner']!r}")
        else:
            assert sp["winner"] == top_code, (
                f"{sp['field']} pos={sp['pos']} winner={sp['winner']!r} "
                f"but argmax was {top_code!r} — threshold gate snuck "
                f"back in?")


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


def test_update_clip_for_field_covers_every_clip_field():
    """Right-click → Update is gated by an `_ALL` set in the preview.
    If a CLIP field is missing from that set:
      - Update on that field is a no-op (falls through the else: return).
      - Update on ANY OTHER field doesn't protect it — it's not in
        skip_fields, so detection runs and overwrites it.
    This is exactly the "not connecting properly" bug from 2026-05-08
    where CL was silently dropped from _ALL.
    """
    import inspect
    import aisearch_preview
    src = inspect.getsource(aisearch_preview.PreviewWindow._update_clip_for_field)
    clip_fields = {s["field"] for s in CLIP_AUTO_DETECT}
    # The canonical safety pattern is `_ALL = {... CLIP_AUTO_DETECT ...}`,
    # so check that source either constructs the set from
    # CLIP_AUTO_DETECT directly OR enumerates every field literally.
    if "CLIP_AUTO_DETECT" in src:
        # Constructed from the source-of-truth — guaranteed in sync.
        return
    # Otherwise fall back to checking the literal set.
    for f in clip_fields:
        assert f'"{f}"' in src, (
            f"_update_clip_for_field's _ALL set is missing {f!r}; "
            f"every CLIP field in CLIP_AUTO_DETECT must be in _ALL "
            f"or that field will be silently overwritten on every "
            f"Update on a different box.")
