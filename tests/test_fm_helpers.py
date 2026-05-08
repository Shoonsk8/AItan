"""File Manager helper functions — pure logic, no Qt needed.

The drag/drop conflict dialog and the "prune descendants" logic for
folder-aware drag are easy to get wrong and easy to test in isolation.
"""
import os

import pytest

from aisearch_file_manager import _suggest_unique_name, _prune_descendants


def test_suggest_unique_name_when_target_empty(tmp_path):
    out = _suggest_unique_name(str(tmp_path), "x.png")
    assert out == "x (1).png"


def test_suggest_unique_name_increments(tmp_path):
    (tmp_path / "x (1).png").write_bytes(b"")
    (tmp_path / "x (2).png").write_bytes(b"")
    assert _suggest_unique_name(str(tmp_path), "x.png") == "x (3).png"


def test_suggest_unique_name_preserves_extension(tmp_path):
    out = _suggest_unique_name(str(tmp_path), "video.mp4")
    assert out.endswith(".mp4")
    assert out.startswith("video ")


def test_suggest_unique_name_no_extension(tmp_path):
    out = _suggest_unique_name(str(tmp_path), "README")
    # Style stays consistent — "README (1)" with no trailing dot.
    assert out == "README (1)"


def test_prune_descendants_drops_files_under_selected_folder(tmp_path):
    """Selecting a folder + its visible children should drag only the
    folder. Without this, the folder moves AND each child moves
    separately, which yanks files out of the folder at the target."""
    folder = tmp_path / "subdir"
    folder.mkdir()
    file_inside = folder / "x.png"
    file_inside.write_bytes(b"")
    sibling = tmp_path / "y.png"
    sibling.write_bytes(b"")

    selection = [str(folder), str(file_inside), str(sibling)]
    pruned = _prune_descendants(selection)
    pruned_set = {os.path.normpath(p) for p in pruned}
    assert os.path.normpath(str(folder))     in pruned_set
    assert os.path.normpath(str(sibling))    in pruned_set
    assert os.path.normpath(str(file_inside)) not in pruned_set, \
        "child of selected folder must be dropped — folder move carries it"


def test_prune_descendants_keeps_independent_paths(tmp_path):
    a = tmp_path / "a"; a.mkdir()
    b = tmp_path / "b"; b.mkdir()
    pruned = _prune_descendants([str(a), str(b)])
    assert {os.path.normpath(p) for p in pruned} == \
           {os.path.normpath(str(a)), os.path.normpath(str(b))}


def test_prune_descendants_handles_double_descent(tmp_path):
    """Three-level: outer / mid / leaf. Selecting all three should
    drop both mid and leaf; only outer remains."""
    outer = tmp_path / "outer"; outer.mkdir()
    mid   = outer / "mid";      mid.mkdir()
    leaf  = mid / "leaf.png";   leaf.write_bytes(b"")
    pruned = _prune_descendants([str(outer), str(mid), str(leaf)])
    pruned_set = {os.path.normpath(p) for p in pruned}
    assert pruned_set == {os.path.normpath(str(outer))}
