"""File Manager helper functions — pure logic, no Qt needed.

The drag/drop conflict dialog and the "prune descendants" logic for
folder-aware drag are easy to get wrong and easy to test in isolation.
"""
import os
import sys
import types
from types import SimpleNamespace

import pytest

# Keep this helper test pure: aisearch_file_manager only needs the extension
# constants here, while aisearch_logic loads the CLIP model at import time.
_real_logic = sys.modules.get("aisearch_logic")
sys.modules["aisearch_logic"] = types.SimpleNamespace(
    EXT_IMG=(".png", ".jpg", ".jpeg", ".webp", ".bmp"),
    EXT_VID=(".mp4", ".mov", ".avi", ".mkv", ".webm"),
)
try:
    from aisearch_file_manager import (
        FileManagerWindow,
        _suggest_unique_name,
        _prune_descendants,
        _drop_paths_allowed,
    )
finally:
    if _real_logic is None:
        sys.modules.pop("aisearch_logic", None)
    else:
        sys.modules["aisearch_logic"] = _real_logic


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


def _bare_fm():
    fm = FileManagerWindow.__new__(FileManagerWindow)
    fm.app = SimpleNamespace(
        data={"paths": []},
        attrs_data={},
        current_project=None,
    )
    fm.refresh_all = lambda: None
    fm._update_main_table_paths = lambda _renames: None
    return fm


def test_move_files_into_ctrl_copy_duplicates_folder_in_same_parent(tmp_path):
    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "x.png").write_bytes(b"image")

    fm = _bare_fm()
    fm.move_files_into([str(folder)], str(tmp_path), mode="copy")

    copied = tmp_path / "folder (1)"
    assert copied.is_dir()
    assert (copied / "x.png").read_bytes() == b"image"
    assert folder.is_dir()


def test_move_files_into_ctrl_copy_folder_dropped_on_itself_copies_there(tmp_path):
    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "x.png").write_bytes(b"image")

    fm = _bare_fm()
    fm.move_files_into([str(folder)], str(folder), mode="copy")

    assert (folder / "folder" / "x.png").read_bytes() == b"image"
    assert not (folder / "folder" / "folder").exists()
    assert not (tmp_path / "folder (1)").exists()


def test_common_drop_policy_allows_copy_onto_same_folder(tmp_path):
    folder = tmp_path / "folder"
    folder.mkdir()

    assert _drop_paths_allowed([str(folder)], str(folder), is_copy=True)


def test_common_drop_policy_blocks_move_onto_same_folder(tmp_path):
    folder = tmp_path / "folder"
    folder.mkdir()

    assert not _drop_paths_allowed([str(folder)], str(folder), is_copy=False)


def test_common_drop_policy_blocks_folder_into_descendant(tmp_path):
    folder = tmp_path / "folder"
    child = folder / "child"
    child.mkdir(parents=True)

    assert not _drop_paths_allowed([str(folder)], str(child), is_copy=True)
    assert not _drop_paths_allowed([str(folder)], str(child), is_copy=False)
