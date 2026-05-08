import cv2, os, shutil, torch, subprocess, io
from PyQt6.QtWidgets import (QMenu, QDialog, QVBoxLayout, QHBoxLayout,
                              QPushButton, QLabel, QCheckBox)
from PyQt6.QtGui import QPixmap
from PIL import Image
from aisearch_config import FolderPickerDialog
import aisearch_attrs as _attrs_mod
from attr_viewer import _lang_label as _t

# Ver 1.65 - Enhanced Rename/Delete sync and UI masking
VERSION = "2.4.3"


def get_thumbnail_pixmap(path, size=(350, 350)):
    """Returns (QPixmap or None, error_message or None)."""
    if not path or not os.path.exists(path):
        return None, "File not found"
    try:
        if path.lower().endswith(('.mp4', '.mkv', '.mov', '.avi')):
            cap = cv2.VideoCapture(path)
            mid = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) * 0.5)
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(mid, 0))
            ret, frame = cap.read()
            cap.release()
            img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)) if ret else Image.new('RGB', size, (50, 50, 50))
        else:
            img = Image.open(path).convert('RGB')
        img.thumbnail(size, Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        pixmap = QPixmap()
        pixmap.loadFromData(buf.getvalue())
        return pixmap, None
    except Exception as e:
        return None, str(e)

# Alias for any remaining callers
get_thumbnail_photoimage = get_thumbnail_pixmap


def open_external_viewer(path, keep_open=True):
    if not os.path.exists(path): return
    import sys
    if sys.platform == "win32":
        os.startfile(path); return
    if sys.platform == "darwin":
        subprocess.Popen(["open", path]); return
    # Linux: try dedicated apps, fall back to xdg-open
    is_video = path.lower().endswith(('.mp4', '.mkv', '.mov', '.avi', '.webm'))
    cmd = "celluloid" if is_video else "xviewer"
    if not keep_open:
        try: subprocess.run(["pkill", cmd], stderr=subprocess.DEVNULL)
        except: pass
    try:
        subprocess.Popen([cmd, path])
    except FileNotFoundError:
        subprocess.Popen(["xdg-open", path])


def create_context_menu(parent_widget, app_instance):
    menu = QMenu(parent_widget)
    menu.addAction(_t("🗂 File Manager / 🗂 ファイルマネージャ"),
                   app_instance._open_fm_for_current_row)
    menu.addAction(_t("📝 Rename (F2) / 📝 改名 (F2)"),        lambda: app_instance.rename_file(from_menu=True))
    menu.addAction(_t("📦 Move to... (M) / 📦 移動... (M)"),     app_instance.move_to_folder_manually)
    menu.addSeparator()
    menu.addAction(_t("🗑️ Delete / 🗑️ 削除"),             app_instance.delete_file)
    return menu


def show_context_menu(global_pos, menu):
    menu.exec(global_pos)


def _numbered_path(dest_path):
    base, ext = os.path.splitext(dest_path)
    n = 1
    candidate = f"{base}_{n}{ext}"
    while os.path.exists(candidate):
        n += 1
        candidate = f"{base}_{n}{ext}"
    return candidate


def resolve_conflict(dest_path, mode, parent_win=None):
    if not os.path.exists(dest_path):
        return dest_path, False
    if mode == "always_overwrite":
        return dest_path, True
    if mode == "always_rename":
        return _numbered_path(dest_path), False
    if mode == "size_check":
        return dest_path, True   # fallback; real logic in _resolve_with_size
    if mode == "always_ask":
        ans = _ask_conflict_dialog(dest_path, parent_win)
        if ans == "overwrite": return dest_path, True
        elif ans == "rename":  return _numbered_path(dest_path), False
        else:                  return None, False
    return dest_path, True


def _ask_conflict_dialog(dest_path, parent_win=None, suggest_overwrite=False):
    result = ["cancel"]
    dlg = QDialog(parent_win)
    dlg.setWindowTitle(_t("File Conflict / ファイル競合"))
    dlg.setFixedSize(340, 160)
    layout = QVBoxLayout(dlg)
    name = os.path.basename(dest_path)
    hint = _t(" (same size) / （同じサイズ）") if suggest_overwrite else _t(" (different size) / （サイズ違い）")
    layout.addWidget(QLabel(_t(f'"{name}" already exists{hint}.\nWhat do you want to do? / "{name}" は既に存在します{hint}。\nどうしますか？')))
    dont_ask = QCheckBox(_t("Do not show again / 次回から表示しない"))
    layout.addWidget(dont_ask)
    bf = QHBoxLayout()

    cfg = getattr(parent_win, 'config', None)

    def _set(v):
        result[0] = v
        if dont_ask.isChecked() and cfg is not None and v != "cancel":
            cfg["conflict_confirm"] = False
            import aisearch_config as _cfg
            _cfg.save_config(cfg)
        dlg.accept()

    ow = QPushButton(_t("Overwrite / 上書き")); ow.clicked.connect(lambda: _set("overwrite"))
    import aisearch_config as cfg
    ow.setStyleSheet(cfg.btn_ss("btn_write", None))
    rn = QPushButton(_t("Rename / 改名"));    rn.clicked.connect(lambda: _set("rename"))
    ca = QPushButton(_t("Cancel / キャンセル"));    ca.clicked.connect(lambda: _set("cancel"))
    if suggest_overwrite:
        ow.setDefault(True)
    else:
        rn.setDefault(True)
    bf.addWidget(ow); bf.addWidget(rn); bf.addWidget(ca)
    layout.addLayout(bf)
    dlg.exec()
    return result[0]


def _resolve_with_size(dest_path, src_path, mode, parent_win=None):
    if not os.path.exists(dest_path):
        return dest_path, False
    if mode == "size_check":
        cfg = getattr(parent_win, 'config', None)
        # Default is overwrite; show dialog only if conflict_confirm is True
        if not cfg or not cfg.get("conflict_confirm", True):
            return dest_path, True   # silently overwrite

        try:
            same = os.path.getsize(src_path) == os.path.getsize(dest_path)
        except OSError:
            same = False
        ans = _ask_conflict_dialog(dest_path, parent_win, suggest_overwrite=same)
        if ans == "overwrite": return dest_path, True
        elif ans == "rename":  return _numbered_path(dest_path), False
        else:                  return None, False
    return resolve_conflict(dest_path, mode, parent_win)


def _remove_from_data(data, path):
    if not data or "paths" not in data: return
    norm = os.path.normpath(path)
    idx = next((i for i, p in enumerate(data["paths"]) if os.path.normpath(p) == norm), None)
    if idx is not None:
        data["paths"].pop(idx)
        keep = [i for i in range(len(data["paths"]) + 1) if i != idx]
        data["embeddings"] = data["embeddings"][keep]


def move_file_physically(old_path, query_path, data, project_name, mode="size_check", parent_win=None):
    target_dir = os.path.dirname(os.path.abspath(query_path))
    dest_path = os.path.join(target_dir, os.path.basename(old_path))
    if os.path.abspath(old_path) == os.path.abspath(dest_path):
        return old_path, data, None
    final_path, overwrite = _resolve_with_size(dest_path, old_path, mode, parent_win)
    if final_path is None:
        return None, data, "cancelled"
    try:
        shutil.move(old_path, final_path)
        if data and "paths" in data:
            if overwrite: _remove_from_data(data, dest_path)
            if old_path in data["paths"]:
                idx = data["paths"].index(old_path)
                data["paths"][idx] = final_path
                torch.save(data, os.path.join(_attrs_mod.DATA_DIR, f"features_{project_name}.pt"))
        return final_path, data, None
    except Exception as e:
        return None, data, str(e)


def open_in_nemo(path):
    """Reveal path in the system file manager (cross-platform).
    For files on Linux: opens the parent folder in the file manager.
    Bare `nemo file.jpg` would launch the default image viewer, not
    the file manager — that's not what the user wants when they pick
    'Open in Nemo' from a context menu."""
    if not os.path.exists(path): return
    import sys
    abs_path = os.path.abspath(path)
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", abs_path]); return
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", abs_path]); return
    # Linux: nemo / thunar / dolphin all support --select to highlight
    # a specific file within its parent folder. Nautilus uses the file
    # path directly. Fall back to opening the parent folder.
    parent = abs_path if os.path.isdir(abs_path) else os.path.dirname(abs_path)
    for fm, args in (
        ("nemo",     ["--no-desktop", abs_path] if os.path.isdir(abs_path) else [parent]),
        ("nautilus", [abs_path] if os.path.isdir(abs_path) else [abs_path]),
        ("thunar",   [parent]),
        ("dolphin",  [parent]),
    ):
        try:
            subprocess.Popen([fm] + args); return
        except FileNotFoundError:
            continue
    subprocess.Popen(["xdg-open", parent])


def execute_manual_move(old_path, target_dir, data, project_name, mode="size_check", parent_win=None):
    dest_path = os.path.join(target_dir, os.path.basename(old_path))
    final_path, overwrite = _resolve_with_size(dest_path, old_path, mode, parent_win)
    if final_path is None: return None, data, "cancelled"
    try:
        shutil.move(old_path, final_path)
        if data and "paths" in data:
            if overwrite: _remove_from_data(data, dest_path)
            if old_path in data["paths"]:
                idx = data["paths"].index(old_path)
                data["paths"][idx] = final_path
                torch.save(data, os.path.join(_attrs_mod.DATA_DIR, f"features_{project_name}.pt"))
        return final_path, data, None
    except Exception as e:
        return None, data, str(e)


def trash_file(path):
    """Move file to trash. Returns (trash_path, None) or (None, error_str).
    On Linux uses XDG trash; on Windows/macOS uses ~/.aisearch_trash for undo support."""
    import sys, time
    stem, ext = os.path.splitext(os.path.basename(path))

    if sys.platform == "linux":
        # XDG trash — full .trashinfo for desktop integration
        trash_dir = os.path.expanduser("~/.local/share/Trash")
        files_dir = os.path.join(trash_dir, "files")
        info_dir  = os.path.join(trash_dir, "info")
        os.makedirs(files_dir, exist_ok=True)
        os.makedirs(info_dir,  exist_ok=True)
        trash_name = os.path.basename(path)
        dest       = os.path.join(files_dir, trash_name)
        i = 2
        while os.path.exists(dest):
            trash_name = f"{stem}_{i}{ext}"; dest = os.path.join(files_dir, trash_name); i += 1
        info_path = os.path.join(info_dir, trash_name + ".trashinfo")
        try:
            with open(info_path, 'w') as f:
                f.write(f"[Trash Info]\nPath={os.path.abspath(path)}\n"
                        f"DeletionDate={time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
            shutil.move(path, dest)
            return dest, None
        except Exception as e:
            if os.path.exists(info_path): os.remove(info_path)
            return None, str(e)
    else:
        # Windows / macOS: stage in ~/.aisearch_trash so undo (restore) works
        trash_dir = os.path.join(os.path.expanduser("~"), ".aisearch_trash")
        os.makedirs(trash_dir, exist_ok=True)
        trash_name = os.path.basename(path)
        dest = os.path.join(trash_dir, trash_name)
        i = 2
        while os.path.exists(dest):
            trash_name = f"{stem}_{i}{ext}"; dest = os.path.join(trash_dir, trash_name); i += 1
        try:
            shutil.move(path, dest)
            return dest, None
        except Exception as e:
            return None, str(e)


def restore_from_trash(trash_path, original_path):
    """Restore a trashed file back to its original location."""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(original_path)), exist_ok=True)
        shutil.move(trash_path, original_path)
        # Clean up XDG .trashinfo if present (Linux only)
        info_path = os.path.join(
            os.path.expanduser("~/.local/share/Trash/info"),
            os.path.basename(trash_path) + ".trashinfo")
        if os.path.exists(info_path):
            os.remove(info_path)
        return True, None
    except Exception as e:
        return False, str(e)

def delete_file_physically(path):
    trash_path, err = trash_file(path)
    return (True, None) if trash_path else (False, err)


def select_and_move_file(parent_win, old_path, data, project_name, start_dir, mode="size_check"):
    picker = FolderPickerDialog(parent_win, initialdir=start_dir, title="Move to...")
    target_dir = picker.result
    if not target_dir: return None, data, None, start_dir
    dest_path = os.path.join(target_dir, os.path.basename(old_path))
    final_path, overwrite = _resolve_with_size(dest_path, old_path, mode, parent_win)
    if final_path is None: return None, data, None, start_dir
    try:
        shutil.move(old_path, final_path)
        if data and "paths" in data:
            if overwrite: _remove_from_data(data, dest_path)
            if old_path in data["paths"]:
                idx = data["paths"].index(old_path)
                data["paths"][idx] = final_path
                torch.save(data, os.path.join(_attrs_mod.DATA_DIR, f"features_{project_name}.pt"))
        return final_path, data, None, target_dir
    except Exception as e:
        return None, data, str(e), target_dir
