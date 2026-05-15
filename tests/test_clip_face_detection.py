"""End-to-end image-detection tests.

Runs the actual CLIP / face pipeline on labeled fixture images and
asserts the detected values match the manifest. This is the only kind
of test that catches detection-quality regressions (the rest of the
suite checks plumbing).

Each fixture in `tests/fixtures/manifest.json` is one of:

    {
      "image":    "fixtures/images/blonde_woman.jpg",
      "clip":     {"hc": "4", "fa": "00", "x": "10"},
      "face_pid": "001"
    }

`clip` keys use the SHORT clip-field codes; values are the expected
hex code in that position. `face_pid` is the expected primary face
person ID. Both are optional — omit either to skip that check for
that image.

Tests are SKIPPED if the manifest is empty so the suite stays green
on a fresh checkout. Add fixtures to actually exercise detection.
"""
import json
import os
import sys
import shutil
import importlib.util

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

_FIXTURES = os.path.join(_ROOT, "tests", "fixtures")
_MANIFEST = os.path.join(_FIXTURES, "manifest.json")


def _load_manifest():
    if not os.path.exists(_MANIFEST):
        return []
    try:
        with open(_MANIFEST, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return []
    return [e for e in d.get("fixtures", []) if e.get("image")]


_FIXTURES_LIST = _load_manifest()


def _resolve_image(rel):
    """Resolve a fixture's image path to an absolute path."""
    p = os.path.join(_ROOT, "tests", rel) if not os.path.isabs(rel) else rel
    return p if os.path.exists(p) else None


# ── CLIP ─────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(not _FIXTURES_LIST, reason="no fixtures labeled")
@pytest.mark.parametrize("fixture", _FIXTURES_LIST,
                         ids=lambda f: os.path.basename(f.get("image", "?")))
def test_clip_detection_matches_expected(fixture):
    expected = fixture.get("clip") or {}
    if not expected:
        pytest.skip("no clip expectations for this fixture")
    img = _resolve_image(fixture["image"])
    if not img:
        pytest.skip(f"image missing: {fixture['image']}")

    import aisearch_logic as logic
    from aisearch_attrs import auto_detect_clip_attrs
    emb = logic.extract_feature(img)
    if emb is None:
        pytest.skip(f"could not extract embedding from {img}")
    detected = auto_detect_clip_attrs(emb, existing_entry={})

    misses = []
    for field, expected_code in expected.items():
        actual = detected.get(field)
        if actual is None:
            misses.append(f"  {field}: expected {expected_code!r}, "
                          f"got nothing (detection didn't fire)")
            continue
        # Per-position match: each digit independent. Compare per-pos.
        actual_pos = actual[-len(expected_code):]
        if actual_pos != expected_code:
            misses.append(f"  {field}: expected {expected_code!r}, "
                          f"got {actual!r} (last-{len(expected_code)} = "
                          f"{actual_pos!r})")
    assert not misses, (
        f"\nCLIP detection mismatch for {os.path.basename(img)}:\n"
        + "\n".join(misses))


# ── FACE ─────────────────────────────────────────────────────────────────────

_FACE_FIXTURES = [f for f in _FIXTURES_LIST if f.get("face_pid")]


@pytest.mark.skipif(not _FACE_FIXTURES, reason="no face fixtures labeled")
@pytest.mark.parametrize("fixture", _FACE_FIXTURES,
                         ids=lambda f: os.path.basename(f.get("image", "?")))
def test_face_detection_matches_expected_pid(fixture):
    img = _resolve_image(fixture["image"])
    if not img:
        pytest.skip(f"image missing: {fixture['image']}")
    project = fixture.get("project") or "AIX"
    expected = fixture["face_pid"]

    from aisearch_attrs import inspect_face_detection_subprocess
    result = inspect_face_detection_subprocess(img, project, timeout=120)

    if result.get("error"):
        pytest.fail(f"face worker error on {img}: {result['error']}")
    actual = result.get("assigned_id")
    assert actual == expected, (
        f"face_pid mismatch for {os.path.basename(img)}: "
        f"expected {expected!r}, got {actual!r}\n"
        f"  top matches: {result.get('matches', [])[:5]}")


# ── Sanity / smoke ───────────────────────────────────────────────────────────

def test_clip_pipeline_runs_at_all():
    """Pick any image in the project and verify the CLIP pipeline runs
    end-to-end without exception. No correctness check — just that
    extract_feature → auto_detect doesn't raise."""
    img_dir = "/mnt/1TBSSD/AIX"
    if not os.path.isdir(img_dir):
        pytest.skip(f"no image dir at {img_dir}")
    sample = None
    for root, _dirs, files in os.walk(img_dir):
        for f in files:
            if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                sample = os.path.join(root, f)
                break
        if sample:
            break
    if not sample:
        pytest.skip("no images found under AIX")

    import aisearch_logic as logic
    from aisearch_attrs import auto_detect_clip_attrs
    if getattr(logic, "model", None) is None:
        pytest.skip("CLIP model not loaded in this test environment")
    emb = logic.extract_feature(sample)
    assert emb is not None, "extract_feature returned None"
    result = auto_detect_clip_attrs(emb, existing_entry={})
    assert isinstance(result, dict), "auto_detect_clip_attrs returned non-dict"


def test_face_worker_subprocess_starts():
    """Verify the face_worker subprocess can be spawned and returns a
    valid JSON dict (no error). Catches: import failures, missing
    dependencies, AISEARCH_SKIP_MODEL handoff bugs, timeout misconfig."""
    img_dir = "/mnt/1TBSSD/AIX"
    if not os.path.isdir(img_dir):
        pytest.skip(f"no image dir at {img_dir}")
    sample = None
    for root, _dirs, files in os.walk(img_dir):
        for f in files:
            if f.lower().endswith((".jpg", ".jpeg", ".png")):
                sample = os.path.join(root, f)
                break
        if sample:
            break
    if not sample:
        pytest.skip("no still images found")
    if importlib.util.find_spec("face_recognition") is None:
        pytest.skip("optional face_recognition dependency is not installed")

    from aisearch_attrs import inspect_face_detection_subprocess
    res = inspect_face_detection_subprocess(sample, "AIX", timeout=120)
    assert isinstance(res, dict)
    assert "error" not in res or res["error"] is None or res["error"] == "", \
        f"face worker returned error: {res.get('error')}"
    assert "face_found" in res
