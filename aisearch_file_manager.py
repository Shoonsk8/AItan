"""
File Manager window — Nemo-style folder browser.

Phase-1 cut: navigate folders, view icon grid of files+folders, multi-select,
Ctrl+wheel to resize thumbnails. Drag-from-main-table → drop = move into the
target folder is wired through dropEvent + URL MIME (handled by main app's
drag overhaul, in a follow-up commit).
"""
import os
import shutil

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QListWidget,
                              QListWidgetItem, QLabel, QPushButton, QLineEdit,
                              QMessageBox)
from PyQt6.QtCore import Qt, QSize, QUrl, QMimeData
from PyQt6.QtGui import QPixmap, QIcon, QImageReader, QPainter, QFont, QImage

import aisearch_logic as logic


_VALID_EXTS = tuple(ext.lower() for ext in (logic.EXT_IMG + logic.EXT_VID))


class _FMIconList(QListWidget):
    """Icon-grid list with drop-accept for cross-window file URLs."""

    def __init__(self, fm):
        super().__init__(fm)
        self._fm = fm
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setMovement(QListWidget.Movement.Static)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.setUniformItemSizes(True)
        self.setWordWrap(True)
        self.setAcceptDrops(True)
        self.setDragEnabled(False)  # dragging FROM the FM is later work
        self.setSpacing(6)

    # ── Drop handling ────────────────────────────────────────────────────────
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
        # Resolve target: dropped onto an item (folder) → that folder;
        # otherwise the current directory itself.
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
    """Top-level Nemo-style file manager window. Single instance, owned by
    the main app. Opens at a given directory; navigates within it."""

    DEFAULT_THUMB = 96
    MIN_THUMB     = 48
    MAX_THUMB     = 256

    def __init__(self, app, initial_dir):
        super().__init__()
        self.app = app
        self.setWindowTitle("AItan — File Manager")
        self.resize(900, 650)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        self._cur_dir       = None
        self._history       = []
        self._history_idx   = -1
        self._thumb_size    = self.DEFAULT_THUMB
        # Pre-rendered folder icon, regenerated when thumb size changes
        self._folder_icon_cached = None

        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(4)

        # Toolbar: nav buttons + path
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

        # Icon grid
        self.list = _FMIconList(self)
        self.list.itemDoubleClicked.connect(self._on_item_double_click)
        v.addWidget(self.list, 1)
        self._apply_thumb_size()

        if initial_dir and os.path.isdir(initial_dir):
            self.navigate(initial_dir)

    # ── Navigation ───────────────────────────────────────────────────────────
    def navigate(self, path):
        path = os.path.abspath(path)
        if not os.path.isdir(path):
            return
        # Truncate forward history past current point
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
            # Reset to current — invalid path
            self.path_edit.setText(self._cur_dir or "")

    # ── Refresh / render ─────────────────────────────────────────────────────
    def _refresh(self):
        self._cur_dir = self._history[self._history_idx]
        self.path_edit.setText(self._cur_dir)
        self.list.clear()
        # ".." entry (unless at filesystem root)
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
        # Folders first, then files
        for name in entries:
            if name.startswith('.'):
                continue
            full = os.path.join(self._cur_dir, name)
            if os.path.isdir(full):
                it = QListWidgetItem(name)
                it.setIcon(self._folder_icon())
                it.setData(Qt.ItemDataRole.UserRole, full)
                self.list.addItem(it)
        for name in entries:
            if name.startswith('.'):
                continue
            full = os.path.join(self._cur_dir, name)
            if os.path.isfile(full) and name.lower().endswith(_VALID_EXTS):
                it = QListWidgetItem(name)
                it.setIcon(self._file_icon(full))
                it.setData(Qt.ItemDataRole.UserRole, full)
                self.list.addItem(it)
        self._update_nav_buttons()

    def _update_nav_buttons(self):
        self.btn_back.setEnabled(self._history_idx > 0)
        self.btn_fwd.setEnabled(self._history_idx < len(self._history) - 1)
        self.btn_up.setEnabled(
            bool(self._cur_dir) and
            os.path.dirname(self._cur_dir) != self._cur_dir)

    # ── Icons ────────────────────────────────────────────────────────────────
    def _folder_icon(self):
        if self._folder_icon_cached is None:
            size = self._thumb_size
            px = QPixmap(size, size)
            px.fill(Qt.GlobalColor.transparent)
            p = QPainter(px)
            f = p.font()
            f.setPointSize(int(size * 0.55))
            p.setFont(f)
            p.drawText(px.rect(), Qt.AlignmentFlag.AlignCenter, "📁")
            p.end()
            self._folder_icon_cached = QIcon(px)
        return self._folder_icon_cached

    def _file_icon(self, path):
        ext = os.path.splitext(path)[1].lower()
        size = self._thumb_size
        try:
            if ext in logic.EXT_VID:
                rgb = logic.get_video_thumbnail_rgb(path, first_only=True)
                if rgb is not None:
                    h, w = rgb.shape[:2]
                    qimg = QImage(rgb.data, w, h, w * 3, QImage.Format.Format_RGB888)
                    px = QPixmap.fromImage(qimg).scaled(
                        size, size,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)
                    return QIcon(px)
            else:
                reader = QImageReader(path)
                reader.setAutoTransform(True)
                # Hint the decoder to scale during decode (DCT scaling for JPEG)
                orig = reader.size()
                if orig.isValid() and max(orig.width(), orig.height()) > size * 2:
                    sc = (size * 2) / max(orig.width(), orig.height())
                    reader.setScaledSize(QSize(
                        max(1, int(orig.width() * sc)),
                        max(1, int(orig.height() * sc))))
                img = reader.read()
                if not img.isNull():
                    px = QPixmap.fromImage(img).scaled(
                        size, size,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)
                    return QIcon(px)
        except Exception:
            pass
        return QIcon()

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
        # Grid cell: thumb + label margin
        self.list.setGridSize(QSize(s + 24, s + 36))

    # ── Move (drop target) ───────────────────────────────────────────────────
    def move_files_into(self, src_paths, target_dir):
        """Move files into target_dir, updating the app's data + attrs.
        Used by drop-from-main-table and any future drag-within-FM."""
        moved = 0
        errors = []
        for src in src_paths:
            if not os.path.exists(src):
                errors.append(f"Missing: {os.path.basename(src)}")
                continue
            dst = os.path.join(target_dir, os.path.basename(src))
            if os.path.normpath(src) == os.path.normpath(dst):
                continue
            try:
                shutil.move(src, dst)
                self._sync_app_state(src, dst)
                moved += 1
            except Exception as e:
                errors.append(f"{os.path.basename(src)}: {e}")
        if errors:
            QMessageBox.warning(self, "Move",
                f"Moved {moved}; {len(errors)} error(s):\n" + "\n".join(errors[:10]))
        self._refresh()

    def _sync_app_state(self, old_path, new_path):
        """After a successful shutil.move, mirror the change in app.data,
        attrs_data, and disk stores so search results stay consistent."""
        app = self.app
        # DB paths
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
        # attrs_data
        try:
            if old_path in app.attrs_data:
                app.attrs_data[new_path] = app.attrs_data.pop(old_path)
        except Exception:
            pass
        # Sister stores (filename rules, faces, etc.)
        try:
            import aisearch_attrs as _am
            _am.update_path_in_all_stores(old_path, new_path,
                                          getattr(app, "current_project", None))
        except Exception:
            pass
