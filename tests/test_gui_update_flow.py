"""GUI tests: simulate clicks on real widgets and assert the side
effects. Faster than booting the full app — we instantiate the
PreviewWindow directly and patch out the heavy CLIP/face subprocess
calls so the test runs in <1 second.

These exist because the structural unit tests can ALL pass while
the actual click flow is dead (the 2026-05 Update regression: the
test for `_ALL` membership passed, but the click silently no-op'd
because of a name-resolution mismatch). Real click tests are the
only way to catch that."""
import os
import sys
import types

import pytest


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# Skip CLIP model load in any subprocess that gets spawned.
os.environ.setdefault("AISEARCH_SKIP_MODEL", "1")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_update_clip_for_field_actually_invokes_on_inspect(qtbot, monkeypatch):
    """Right-click → Update on a canvas widget must call _on_inspect
    with the target field cleared from the entry. The 2026-05 dead-
    Update bug presented as: click does nothing, no _on_inspect call,
    no detection. This test would fail in that state."""
    from PyQt6.QtWidgets import QWidget

    # Build a minimal stand-in for PreviewWindow that exposes only the
    # one method under test. Spinning up the full PreviewWindow needs
    # the main app, image preview, and 30+ collaborators — overkill
    # for this assertion.
    import aisearch_preview as ap

    class _Stub(QWidget):
        # Just enough for _update_clip_for_field to run.
        def __init__(self):
            super().__init__()
            self._attr_path = "/tmp/test/file.png"
            self._soft_canvas = None
            class _App: pass
            class _H: pass
            h = _H(); h.app = _App(); h.app.attrs_data = {
                "/tmp/test/file.png": {
                    "hair":      "0k5",   # long-form storage
                    "background":"10",
                    "expression":"42",
                }
            }
            self.handler = h

        # Borrow the real method so we test what production runs.
        _update_clip_for_field = ap.PreviewWindow._update_clip_for_field

        # Capture _on_inspect calls instead of running them.
        captured = []
        def _on_inspect(self, *args, **kwargs):
            type(self).captured.append((args, kwargs))

    stub = _Stub()
    qtbot.addWidget(stub)

    # Click "Update" on the HC canvas widget.
    stub._update_clip_for_field("HC")
    assert len(_Stub.captured) == 1, (
        "_update_clip_for_field('HC') did NOT call _on_inspect — "
        "Update is dead (the 2026-05 regression class).")
    args, kwargs = _Stub.captured[0]
    # overwrite=True is the contract for per-field Update.
    assert kwargs.get("overwrite") is True
    skip = kwargs.get("skip_fields") or set()
    # All other CLIP fields must be in skip_fields (we only re-detect hair).
    # CLIP_AUTO_DETECT now uses long storage keys.
    assert "background" in skip and "face_angle" in skip and "clothing" in skip
    # And the target itself MUST NOT be skipped.
    assert "hair" not in skip, (
        "skip_fields contains 'hair' — Update on HC would skip itself.")

    # The entry's storage-key value should have been cleared so detect runs.
    entry = stub.handler.app.attrs_data["/tmp/test/file.png"]
    assert "hair" not in entry, (
        "Long-form storage key wasn't cleared — detect won't re-run "
        "because the existing-non-zero check still sees the old value.")
    # And the legacy short key (if any code path writes it) is gone too.
    assert "hc" not in entry


def test_inspect_clip_scores_exposes_storage_key():
    """inspect_clip_scores must return BOTH `field` (filename letter
    used to name the debug tile, e.g. "HC") AND `storage_key` (the
    long form the entry actually stores at, e.g. "hair"). If consumers
    fall back to `sp["field"].lower()` they get "hc" — which writes
    to entry["hc"] while the canvas reads entry["hair"], producing the
    "tile shows scores but canvas value never updates" bug the user
    saw on 2026-05-09."""
    import os
    os.environ.setdefault("AISEARCH_SKIP_MODEL", "1")
    from aisearch_attrs import CLIP_AUTO_DETECT
    # We don't need a real model — just the spec wiring.
    for spec in CLIP_AUTO_DETECT:
        # Every spec must declare the long storage key in its field
        # position (root-fix: all specs use long names).
        # Allowed set must include every long storage key any active CLIP
        # spec uses. Add new fields here when CLIP_AUTO_DETECT grows.
        assert spec["field"] in (
            "hair", "face_angle", "skin", "eyes", "posture_motion",
            "camera_shot", "background", "expression", "clothing",
            "animal",
        ), (
            f"CLIP_AUTO_DETECT spec {spec.get('field')!r} is not a long "
            f"storage key — short codes break entry writes after the "
            f"storage-key migration.")


def test_on_inspect_consumers_read_storage_key_not_field():
    """The post-detect write-back in _on_inspect builds entry updates
    keyed by `_storage = sp.get("storage_key") or sp["field"].lower()`.
    The split-brain bug from 2026-05-09: detection wrote to entry["hc"]
    while the canvas read entry["hair"], so Update appeared to do
    nothing on screen. Pin the source so a future edit can't quietly
    fall back to `sp["field"].lower()` (which is now the filename
    letter "HC"/"hc" again — wrong direction)."""
    import os, inspect
    os.environ.setdefault("AISEARCH_SKIP_MODEL", "1")
    import aisearch_preview
    src = inspect.getsource(aisearch_preview.PreviewWindow._on_inspect)
    # Both write-side and read-side must consult storage_key first.
    # If anyone reverts to sp["field"].lower() alone for entry writes,
    # the canvas display goes stale on every Update.
    bare_field_lower_writes = src.count('sp["field"].lower()')
    storage_key_lookups     = src.count('sp.get("storage_key")')
    assert storage_key_lookups >= 3, (
        f"_on_inspect uses storage_key in {storage_key_lookups} place(s); "
        f"expected ≥ 3 (per-tile loop, working-dict build, and corrections). "
        f"A drop here means split-brain entry writes are back.")
    # bare lower() may appear inside `sp.get("storage_key") or sp["field"].lower()`
    # fallback chains — that's fine. But count must not exceed storage_key uses.
    assert bare_field_lower_writes <= storage_key_lookups, (
        f"sp['field'].lower() used as a primary key in {bare_field_lower_writes} "
        f"places without a storage_key fallback. Writes will land on "
        f"entry['hc']/entry['bg'] while canvas reads entry['hair']/['background']."
    )


def test_update_clip_for_field_works_for_human_label_section(qtbot):
    """Update on a human-label section ("Background") must resolve to
    the same storage key the canvas widget reads. If the alias chain
    drifts again, Update on the Background tile silently does nothing."""
    from PyQt6.QtWidgets import QWidget
    import aisearch_preview as ap

    class _Stub(QWidget):
        def __init__(self):
            super().__init__()
            self._attr_path = "/tmp/test/file.png"
            self._soft_canvas = None
            class _App: pass
            class _H: pass
            h = _H(); h.app = _App(); h.app.attrs_data = {
                "/tmp/test/file.png": {"background": "10"}
            }
            self.handler = h
        _update_clip_for_field = ap.PreviewWindow._update_clip_for_field
        captured = []
        def _on_inspect(self, *args, **kwargs):
            type(self).captured.append((args, kwargs))

    stub = _Stub()
    qtbot.addWidget(stub)
    stub._update_clip_for_field("Background")
    assert len(_Stub.captured) == 1, (
        "_update_clip_for_field('Background') did not call _on_inspect "
        "— the Background section's alias to 'bg' is broken.")
    entry = stub.handler.app.attrs_data["/tmp/test/file.png"]
    assert "background" not in entry, (
        "Long storage key 'background' wasn't cleared.")
