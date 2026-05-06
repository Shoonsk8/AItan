"""
File Manager window — Nemo-style folder browser.

Phase-1 cut: navigate folders, view icon grid of files+folders, multi-select,
Ctrl+wheel to resize thumbnails. Thumbnails load asynchronously in a worker
thread so the window paints instantly even on big folders.
"""
import os
import shutil
import time

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QListWidget,
                              QListWidgetItem, QLabel, QPushButton, QLineEdit,
                              QMessageBox, QInputDialog, QMenu)
from PyQt6.QtCore import Qt, QSize, QUrl, QMimeData, QThread, pyqtSignal, QPoint
from PyQt6.QtGui import (QPixmap, QIcon, QImageReader, QPainter, QImage,
                         QColor, QPen, QShortcut, QKeySequence, QAction, QDrag)

import aisearch_logic as logic


_VALID_EXTS = tuple(ext.lower() for ext in (logic.EXT_IMG + logic.EXT_VID))

# Persist across navigations so revisits are instant. Key: (path, mtime, size).
# Values are QPixmap (cheap to wrap in QIcon at use time). Bounded.
_THUMB_CACHE: dict = {}
_THUMB_CACHE_MAX = 500


def _draw_video_rim(pixmap, width=3, color="#00ff00"):
    """Stamp a green border around a thumbnail to mark it as a video.
    Matches the rim style used on the main-window drop zone."""
    p = QPainter(pixmap)
    pen = QPen(QColor(color))
    pen.setWidth(width)
    pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    p.setPen(pen)
    # Inset by half-width so the stroke lands inside the bounds
    half = width / 2
    p.drawRect(int(half), int(half),
               pixmap.width() - width, pixmap.height() - width)
    p.end()
    return pixmap


def _make_thumb_pixmap(path, size):
    """Build a thumbnail QPixmap from the file. May return None on failure
    (corrupt / unsupported codec). Safe to call from a worker thread."""
    ext = os.path.splitext(path)[1].lower()
    is_video = ext in logic.EXT_VID
    try:
        if is_video:
            rgb = logic.get_video_thumbnail_rgb(path, first_only=True)
            if rgb is None:
                return None
            h, w = rgb.shape[:2]
            qimg = QImage(rgb.data, w, h, w * 3, QImage.Format.Format_RGB888).copy()
            px = QPixmap.fromImage(qimg).scaled(
                size, size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
        else:
            reader = QImageReader(path)
            reader.setAutoTransform(True)
            orig = reader.size()
            if orig.isValid() and max(orig.width(), orig.height()) > size * 2:
                sc = (size * 2) / max(orig.width(), orig.height())
                reader.setScaledSize(QSize(
                    max(1, int(orig.width() * sc)),
                    max(1, int(orig.height() * sc))))
            img = reader.read()
            if img.isNull():
                return None
            px = QPixmap.fromImage(img).scaled(
                size, size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
        if is_video:
            _draw_video_rim(px)
        return px
    except Exception:
        return None


class _ThumbLoader(QThread):
    """Walks a list of (key, path) requests, emits a thumbnail QPixmap for
    each. Cancellable — caller flips _cancel and the next iteration bails."""

    thumb_ready = pyqtSignal(str, object)  # cache_key, QPixmap

    def __init__(self, requests, size, parent=None):
        super().__init__(parent)
        self.requests = requests
        self.size = size
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        for cache_key, path in self.requests:
            if self._cancel:
                return
            px = _make_thumb_pixmap(path, self.size)
            if self._cancel:
                return
            if px is not None:
                self.thumb_ready.emit(cache_key, px)


class _FMIconList(QListWidget):
    """Icon-grid list with drop-accept for cross-window file URLs."""

    def __init__(self, fm):
        super().__init__(fm)
        self._fm = fm
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setMovement(QListWidget.Movement.Static)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        # Uniform-item sizing caches the first item's geometry and forces
        # all later items to match. Mixed icon types (folder emoji vs.
        # rendered thumbnails) and re-navigation made labels collapse.
        self.setUniformItemSizes(False)
        self.setWordWrap(True)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QListWidget.DragDropMode.DragDrop)
        self._install_viewport_filter()
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSpacing(6)

    # Manual drag-start via an event filter on the viewport. Qt's
    # built-in QAbstractItemView drag-detection wasn't firing in PyQt6
    # IconMode with custom items, and overriding mousePress/Move on the
    # widget itself missed events that the viewport receives directly.
    _DRAG_THRESHOLD = 5

    def _install_viewport_filter(self):
        self._fm_press_pos = None
        self._fm_press_item = None
        self.viewport().installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self.viewport():
            t = event.type()
            if t == event.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.LeftButton:
                    pos = event.position().toPoint()
                    self._fm_press_pos = pos
                    self._fm_press_item = self.itemAt(pos)
                else:
                    self._fm_press_pos = None
                    self._fm_press_item = None
            elif t == event.Type.MouseMove:
                if (event.buttons() & Qt.MouseButton.LeftButton
                        and self._fm_press_pos is not None
                        and self._fm_press_item is not None):
                    cur = event.position().toPoint()
                    if (cur - self._fm_press_pos).manhattanLength() > self._DRAG_THRESHOLD:
                        self._fm_press_pos = None
                        item = self._fm_press_item
                        self._fm_press_item = None
                        self._start_url_drag(seed_item=item)
                        return True
            elif t == event.Type.MouseButtonRelease:
                self._fm_press_pos = None
                self._fm_press_item = None
        return super().eventFilter(obj, event)

    def _start_url_drag(self, seed_item=None):
        items = self.selectedItems()
        # If selection is empty (or just-pressed item not in it yet),
        # fall back to the seed item so single click+drag in one motion
        # works without needing a separate selection click first.
        if not items and seed_item is not None:
            items = [seed_item]
        elif seed_item is not None and seed_item not in items:
            items = [seed_item] + [i for i in items if i is not seed_item]
        if not items:
            return
        urls = []
        for it in items:
            data = it.data(Qt.ItemDataRole.UserRole)
            if data and data != ".." and os.path.exists(data):
                urls.append(QUrl.fromLocalFile(data))
        if not urls:
            return
        mime = QMimeData()
        mime.setUrls(urls)
        drag = QDrag(self)
        drag.setMimeData(mime)
        first = items[0]
        ico = first.icon()
        if not ico.isNull():
            sz = self.iconSize()
            drag.setPixmap(ico.pixmap(sz))
            drag.setHotSpot(QPoint(sz.width() // 2, sz.height() // 2))
        drag.exec(Qt.DropAction.MoveAction)

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dragMoveEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dropEvent(self, ev):
        if not ev.mimeData().hasUrls():
            ev.ignore(); return
        item = self.itemAt(ev.position().toPoint())
        target = None
        if item:
            data = item.data(Qt.ItemDataRole.UserRole)
            if data == "..":
                target = os.path.dirname(self._fm._cur_dir)
            elif data and os.path.isdir(data):
                target = data
        if not target:
            target = self._fm._cur_dir
        srcs = [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]
        if not srcs or not os.path.isdir(target):
            ev.ignore(); return
        ev.acceptProposedAction()
        self._fm.move_files_into(srcs, target)


class FileManagerWindow(QWidget):
    DEFAULT_THUMB = 96
    MIN_THUMB     = 48
    MAX_THUMB     = 256

    def __init__(self, app, initial_dir):
        super().__init__()
        self.app = app
        self.setWindowTitle("AItan — File Manager")
        self.resize(900, 650)

        self._cur_dir       = None
        self._history       = []
        self._history_idx   = -1
        self._thumb_size    = self.DEFAULT_THUMB
        self._folder_icon_cached = None
        # path → row index, for async thumbnail apply lookups in the
        # current view (cleared on each refresh)
        self._row_of_key    = {}
        self._thumb_loader  = None

        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(4)

        tb = QHBoxLayout()
        self.btn_back = QPushButton("◀")
        self.btn_fwd  = QPushButton("▶")
        self.btn_up   = QPushButton("▲")
        for b in (self.btn_back, self.btn_fwd, self.btn_up):
            b.setFixedWidth(32)
        self.btn_back.clicked.connect(self._go_back)
        self.btn_fwd.clicked.connect(self._go_forward)
        self.btn_up.clicked.connect(self._go_up)
        tb.addWidget(self.btn_back)
        tb.addWidget(self.btn_fwd)
        tb.addWidget(self.btn_up)
        self.path_edit = QLineEdit()
        self.path_edit.returnPressed.connect(self._on_path_edit_enter)
        tb.addWidget(self.path_edit, 1)
        v.addLayout(tb)

        self.list = _FMIconList(self)
        self.list.itemDoubleClicked.connect(self._on_item_double_click)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._on_context_menu)
        v.addWidget(self.list, 1)
        self._apply_thumb_size()

        # F2 = rename selected, Delete = trash selected
        QShortcut(QKeySequence("F2"), self, activated=self._rename_selected)
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self,
                  activated=self._delete_selected)

        if initial_dir and os.path.isdir(initial_dir):
            self.navigate(initial_dir)

    # ── Navigation ───────────────────────────────────────────────────────────
    def navigate(self, path):
        path = os.path.abspath(path)
        if not os.path.isdir(path):
            return
        self._history = self._history[:self._history_idx + 1]
        self._history.append(path)
        self._history_idx = len(self._history) - 1
        self._refresh()

    def _go_back(self):
        if self._history_idx > 0:
            self._history_idx -= 1
            self._refresh()

    def _go_forward(self):
        if self._history_idx < len(self._history) - 1:
            self._history_idx += 1
            self._refresh()

    def _go_up(self):
        parent = os.path.dirname(self._cur_dir or "")
        if parent and parent != self._cur_dir and os.path.isdir(parent):
            self.navigate(parent)

    def _on_item_double_click(self, item):
        target = item.data(Qt.ItemDataRole.UserRole)
        if target == "..":
            self._go_up()
        elif target and os.path.isdir(target):
            self.navigate(target)
        elif target and os.path.isfile(target):
            ph = getattr(self.app, "preview_handler", None)
            if ph:
                try: ph.show(target)
                except Exception: pass

    def _on_path_edit_enter(self):
        p = self.path_edit.text().strip()
        if p and os.path.isdir(p):
            self.navigate(p)
        else:
            self.path_edit.setText(self._cur_dir or "")

    # ── Refresh / render ─────────────────────────────────────────────────────
    def _refresh(self):
        # Stop any in-flight thumbnail loader before changing the view —
        # otherwise stale thumbnails arrive after the new folder is shown.
        if self._thumb_loader is not None:
            self._thumb_loader.cancel()
            self._thumb_loader = None

        self._cur_dir = self._history[self._history_idx]
        self.path_edit.setText(self._cur_dir)
        self.list.clear()
        self._row_of_key.clear()

        # ".." entry
        if os.path.dirname(self._cur_dir) != self._cur_dir:
            it = QListWidgetItem("..")
            it.setData(Qt.ItemDataRole.UserRole, "..")
            it.setIcon(self._folder_icon())
            self.list.addItem(it)

        try:
            entries = sorted(os.listdir(self._cur_dir),
                             key=lambda n: n.lower())
        except OSError:
            entries = []

        # Folders first (instant — generic icon)
        for name in entries:
            if name.startswith('.'):
                continue
            full = os.path.join(self._cur_dir, name)
            if os.path.isdir(full):
                it = QListWidgetItem(name)
                it.setIcon(self._folder_icon())
                it.setData(Qt.ItemDataRole.UserRole, full)
                self.list.addItem(it)

        # Files: add with placeholder, queue real thumbnails
        thumb_requests = []
        placeholder_icon = self._placeholder_file_icon()
        for name in entries:
            if name.startswith('.'):
                continue
            full = os.path.join(self._cur_dir, name)
            if not (os.path.isfile(full) and name.lower().endswith(_VALID_EXTS)):
                continue
            it = QListWidgetItem(name)
            it.setData(Qt.ItemDataRole.UserRole, full)
            try:
                mtime = os.path.getmtime(full)
            except OSError:
                mtime = 0
            cache_key = f"{full}|{mtime}|{self._thumb_size}"
            cached = _THUMB_CACHE.get(cache_key)
            if cached is not None:
                it.setIcon(QIcon(cached))
            else:
                it.setIcon(placeholder_icon)
                thumb_requests.append((cache_key, full))
            row = self.list.count()
            self.list.addItem(it)
            self._row_of_key[cache_key] = row

        self._update_nav_buttons()

        # Kick off async thumbnail loader for misses
        if thumb_requests:
            self._thumb_loader = _ThumbLoader(
                thumb_requests, self._thumb_size, self)
            self._thumb_loader.thumb_ready.connect(self._on_thumb_ready)
            self._thumb_loader.start()

    def _on_thumb_ready(self, cache_key, pixmap):
        # Save in cache (bounded)
        if len(_THUMB_CACHE) >= _THUMB_CACHE_MAX:
            _THUMB_CACHE.pop(next(iter(_THUMB_CACHE)))
        _THUMB_CACHE[cache_key] = pixmap
        # Apply to the current view if still showing the right folder
        row = self._row_of_key.get(cache_key)
        if row is None:
            return
        item = self.list.item(row)
        if item is None:
            return
        item.setIcon(QIcon(pixmap))

    def _update_nav_buttons(self):
        self.btn_back.setEnabled(self._history_idx > 0)
        self.btn_fwd.setEnabled(self._history_idx < len(self._history) - 1)
        self.btn_up.setEnabled(
            bool(self._cur_dir) and
            os.path.dirname(self._cur_dir) != self._cur_dir)

    # ── Icons ────────────────────────────────────────────────────────────────
    def _folder_icon(self):
        if self._folder_icon_cached is None:
            self._folder_icon_cached = self._render_emoji_icon("📁")
        return self._folder_icon_cached

    def _placeholder_file_icon(self):
        # Cheap blank box — replaced async with the real thumbnail
        s = self._thumb_size
        px = QPixmap(s, s)
        px.fill(Qt.GlobalColor.transparent)
        return QIcon(px)

    def _render_emoji_icon(self, emoji):
        size = self._thumb_size
        px = QPixmap(size, size)
        px.fill(Qt.GlobalColor.transparent)
        p = QPainter(px)
        f = p.font()
        f.setPointSize(int(size * 0.55))
        p.setFont(f)
        p.drawText(px.rect(), Qt.AlignmentFlag.AlignCenter, emoji)
        p.end()
        return QIcon(px)

    # ── Resize via Ctrl+Wheel ────────────────────────────────────────────────
    def wheelEvent(self, ev):
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = ev.angleDelta().y()
            step = 16
            new_size = self._thumb_size + (step if delta > 0 else -step)
            new_size = max(self.MIN_THUMB, min(self.MAX_THUMB, new_size))
            if new_size != self._thumb_size:
                self._thumb_size = new_size
                self._folder_icon_cached = None
                self._apply_thumb_size()
                self._refresh()
            ev.accept()
        else:
            super().wheelEvent(ev)

    def _apply_thumb_size(self):
        s = self._thumb_size
        self.list.setIconSize(QSize(s, s))
        # Two-line label area below the icon. ~16 px per line + 8 px margin.
        self.list.setGridSize(QSize(s + 24, s + 48))

    # ── Move (drop target) ───────────────────────────────────────────────────
    def move_files_into(self, src_paths, target_dir):
        moved   = 0
        renames = {}            # old → new (for batched store flush)
        errors  = []
        for src in src_paths:
            if not os.path.exists(src):
                errors.append(f"Missing: {os.path.basename(src)}")
                continue
            dst = os.path.join(target_dir, os.path.basename(src))
            if os.path.normpath(src) == os.path.normpath(dst):
                continue
            try:
                shutil.move(src, dst)
                self._sync_in_memory(src, dst)
                renames[src] = dst
                moved += 1
            except Exception as e:
                errors.append(f"{os.path.basename(src)}: {e}")
        # Flush the on-disk stores ONCE (faces DB + features.pt) instead of
        # per-file. The .pt is ~120 MB so per-file disk I/O makes drops of
        # multi-row selections feel like the FM is hung.
        if renames:
            try:
                import aisearch_attrs as _am
                _am.flush_path_renames_to_stores(
                    renames, getattr(self.app, "current_project", None))
            except Exception:
                pass
        if errors:
            QMessageBox.warning(self, "Move",
                f"Moved {moved}; {len(errors)} error(s):\n" + "\n".join(errors[:10]))
        self._refresh()

    def _sync_in_memory(self, old_path, new_path):
        """Per-file in-memory updates only — disk-flush is batched by the
        caller (move_files_into) once all moves finish."""
        app = self.app
        try:
            paths = app.data["paths"] if app.data and "paths" in app.data else None
            if paths is not None:
                norm_old = os.path.normpath(os.path.abspath(old_path))
                for i, p in enumerate(paths):
                    if os.path.normpath(os.path.abspath(p)) == norm_old:
                        paths[i] = new_path
                        break
        except Exception:
            pass
        try:
            if old_path in app.attrs_data:
                app.attrs_data[new_path] = app.attrs_data.pop(old_path)
        except Exception:
            pass

    # ── Context menu ─────────────────────────────────────────────────────────
    def _on_context_menu(self, pos):
        item = self.list.itemAt(pos)
        menu = QMenu(self)

        act_new = QAction("New Folder", self)
        act_new.triggered.connect(self._new_folder)
        menu.addAction(act_new)

        if item is not None:
            data = item.data(Qt.ItemDataRole.UserRole)
            if data and data != "..":
                menu.addSeparator()
                act_rename = QAction("Rename (F2)", self)
                act_rename.triggered.connect(self._rename_selected)
                menu.addAction(act_rename)

                act_delete = QAction("Move to Trash (Del)", self)
                act_delete.triggered.connect(self._delete_selected)
                menu.addAction(act_delete)

                act_open_loc = QAction("Open in Nemo", self)
                act_open_loc.triggered.connect(
                    lambda: self._open_in_nemo(data))
                menu.addAction(act_open_loc)

        menu.exec(self.list.viewport().mapToGlobal(pos))

    # ── File operations ──────────────────────────────────────────────────────
    def _new_folder(self):
        if not self._cur_dir:
            return
        name, ok = QInputDialog.getText(
            self, "New Folder", "Folder name:", QLineEdit.EchoMode.Normal, "")
        if not ok:
            return
        name = name.strip()
        if not name or name in (".", ".."):
            return
        # Sanitize against path separators — confine to the current dir
        if "/" in name or "\\" in name:
            QMessageBox.warning(self, "New Folder",
                "Folder name cannot contain '/' or '\\'.")
            return
        target = os.path.join(self._cur_dir, name)
        try:
            os.makedirs(target, exist_ok=False)
        except FileExistsError:
            QMessageBox.warning(self, "New Folder",
                f"Folder already exists:\n{target}")
            return
        except Exception as e:
            QMessageBox.critical(self, "New Folder", f"Could not create:\n{e}")
            return
        self._refresh()

    def _rename_selected(self):
        item = self.list.currentItem()
        if item is None:
            return
        old_path = item.data(Qt.ItemDataRole.UserRole)
        if not old_path or old_path == "..":
            return
        old_name = os.path.basename(old_path)
        new_name, ok = QInputDialog.getText(
            self, "Rename", f"New name for '{old_name}':",
            QLineEdit.EchoMode.Normal, old_name)
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name or new_name == old_name:
            return
        if "/" in new_name or "\\" in new_name:
            QMessageBox.warning(self, "Rename",
                "Name cannot contain '/' or '\\'.")
            return
        new_path = os.path.join(os.path.dirname(old_path), new_name)
        if os.path.exists(new_path):
            QMessageBox.warning(self, "Rename",
                f"Already exists:\n{new_path}")
            return
        try:
            os.rename(old_path, new_path)
        except Exception as e:
            QMessageBox.critical(self, "Rename", f"Could not rename:\n{e}")
            return
        # Sync app state if it's a tracked file
        if os.path.isfile(new_path):
            self._sync_in_memory(old_path, new_path)
            try:
                import aisearch_attrs as _am
                _am.flush_path_renames_to_stores(
                    {old_path: new_path},
                    getattr(self.app, "current_project", None))
            except Exception:
                pass
        # If the renamed path is a folder, walk app.data for any files
        # whose path starts with old_path/ and remap.
        elif os.path.isdir(new_path):
            self._sync_folder_rename(old_path, new_path)
        self._refresh()

    def _delete_selected(self):
        items = self.list.selectedItems()
        if not items:
            return
        paths = [it.data(Qt.ItemDataRole.UserRole) for it in items]
        paths = [p for p in paths if p and p != ".."]
        if not paths:
            return
        if len(paths) == 1:
            msg = f"Move to trash:\n{paths[0]}"
        else:
            msg = f"Move {len(paths)} item(s) to trash?"
        if QMessageBox.question(self, "Trash", msg,
                                QMessageBox.StandardButton.Yes |
                                QMessageBox.StandardButton.No
                                ) != QMessageBox.StandardButton.Yes:
            return
        import aisearch_front_page as _fp
        errors = []
        for p in paths:
            if not os.path.exists(p):
                continue
            try:
                _tp, err = _fp.trash_file(p)
                if err:
                    errors.append(f"{os.path.basename(p)}: {err}")
                    continue
                # Sync app state
                self._remove_from_app_state(p)
            except Exception as e:
                errors.append(f"{os.path.basename(p)}: {e}")
        if errors:
            QMessageBox.warning(self, "Trash",
                f"Errors:\n" + "\n".join(errors[:10]))
        self._refresh()

    def _open_in_nemo(self, path):
        try:
            import aisearch_front_page as _fp
            _fp.open_in_nemo(path)
        except Exception:
            pass

    def _sync_folder_rename(self, old_dir, new_dir):
        """When a directory is renamed, remap any tracked file paths that
        live under it. Builds a renames dict so the on-disk stores can be
        flushed in a single batch."""
        app = self.app
        old_prefix = os.path.normpath(os.path.abspath(old_dir)) + os.sep
        new_prefix = os.path.normpath(os.path.abspath(new_dir)) + os.sep
        renames = {}
        try:
            paths = app.data["paths"] if app.data and "paths" in app.data else None
            if paths is not None:
                for i, p in enumerate(paths):
                    np = os.path.normpath(os.path.abspath(p))
                    if np.startswith(old_prefix):
                        new_p = new_prefix + np[len(old_prefix):]
                        renames[p] = new_p
                        paths[i] = new_p
        except Exception:
            pass
        try:
            for key in list(app.attrs_data.keys()):
                k_norm = os.path.normpath(os.path.abspath(key))
                if k_norm.startswith(old_prefix):
                    new_key = new_prefix + k_norm[len(old_prefix):]
                    app.attrs_data[new_key] = app.attrs_data.pop(key)
        except Exception:
            pass
        if renames:
            try:
                import aisearch_attrs as _am
                _am.flush_path_renames_to_stores(
                    renames, getattr(app, "current_project", None))
            except Exception:
                pass

    def _remove_from_app_state(self, path):
        """Remove a trashed path from app.data + attrs_data."""
        app = self.app
        try:
            if app.data and "paths" in app.data and path in app.data["paths"]:
                idx = app.data["paths"].index(path)
                keep = [i for i in range(len(app.data["paths"])) if i != idx]
                app.data["paths"] = [app.data["paths"][i] for i in keep]
                app.data["embeddings"] = app.data["embeddings"][keep]
        except Exception:
            pass
        try:
            app.attrs_data.pop(path, None)
        except Exception:
            pass

    # ── Cleanup ──────────────────────────────────────────────────────────────
    def closeEvent(self, ev):
        if self._thumb_loader is not None:
            self._thumb_loader.cancel()
            self._thumb_loader = None
        super().closeEvent(ev)
