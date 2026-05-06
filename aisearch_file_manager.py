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
                              QMessageBox, QInputDialog, QMenu, QStackedWidget,
                              QTableWidget, QTableWidgetItem, QHeaderView,
                              QAbstractItemView, QTreeWidget, QTreeWidgetItem)
from PyQt6.QtCore import Qt, QSize, QUrl, QMimeData, QThread, pyqtSignal, QPoint
from PyQt6.QtGui import (QPixmap, QIcon, QImageReader, QPainter, QImage,
                         QColor, QPen, QShortcut, QKeySequence, QAction, QDrag)

import aisearch_logic as logic

VERSION = "2.3a"


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
        # Disable Qt's own drag/drop machinery — it keeps showing STOP
        # cursors based on per-item flag checks that fight against our
        # custom dropEvent. We start the drag ourselves via the viewport
        # eventFilter and handle drops via our own dragEnter/Move/drop.
        self.setAcceptDrops(True)
        self.setDragEnabled(False)
        self.setDragDropMode(QListWidget.DragDropMode.NoDragDrop)
        self._install_viewport_filter()
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
        import sys
        print(f"[FM] _start_url_drag seed={seed_item.text() if seed_item else None}",
              file=sys.stderr, flush=True)
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
        import sys
        print(f"[FM] dragEnterEvent hasUrls={ev.mimeData().hasUrls()}", file=sys.stderr, flush=True)
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dragMoveEvent(self, ev):
        # Always accept anywhere over the list — dropEvent decides what
        # the target actually is. Without an accept here, Qt shows STOP.
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dropEvent(self, ev):
        import sys
        print(f"[FM] dropEvent hasUrls={ev.mimeData().hasUrls()}", file=sys.stderr, flush=True)
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
        try:
            self._fm.move_files_into(srcs, target)
        except Exception as e:
            import traceback
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Drop Error", f"Failed to move files:\n{e}\n{traceback.format_exc()}")


class _FMTreeList(QTreeWidget):
    """Tree/details view: triangles expand folders inline. Lazy-loads
    children on first expand (so opening at a deep root is fast).
    4 columns: Name · Size · Date · Type. File rows show a small
    thumbnail icon (loaded async)."""

    _DRAG_THRESHOLD  = 5
    _PLACEHOLDER     = "__placeholder__"
    _TREE_THUMB_SIZE = 32

    def __init__(self, fm):
        super().__init__(fm)
        self._fm = fm
        self.setColumnCount(4)
        self.setHeaderLabels(["Name", "Size", "Date", "Type"])
        hdr = self.header()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAcceptDrops(True)
        self.setRootIsDecorated(True)   # show expand triangles on top-level
        self.setUniformRowHeights(True)
        self.setAllColumnsShowFocus(True)
        # Bigger indent + visible branch lines so nesting depth is obvious
        self.setIndentation(28)
        self.setAlternatingRowColors(True)
        self.setStyleSheet(
            "QTreeView::branch:has-siblings:!adjoins-item {"
            "  border-image: none; border-left: 1px solid #555; }"
            "QTreeView::branch:has-siblings:adjoins-item {"
            "  border-image: none; border-left: 1px solid #555; }"
            "QTreeView::branch:!has-children:!has-siblings:adjoins-item {"
            "  border-image: none; border-left: 1px solid #555; }")
        self.setIconSize(QSize(self._TREE_THUMB_SIZE, self._TREE_THUMB_SIZE))
        # Click column headers to re-sort. Default: Name column ascending.
        self.setSortingEnabled(True)
        self.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.itemDoubleClicked.connect(self._on_double_click)
        self.itemExpanded.connect(self._on_expand)
        # Manual drag start via viewport eventFilter (same pattern as
        # main's FileTable, which works in PyQt6).
        self._press_pos  = None
        self._press_item = None
        self.viewport().installEventFilter(self)
        # cache_key → QTreeWidgetItem for async-loaded thumbnails
        self._items_by_key = {}
        self._thumb_loader = None
        self._suppress_collapse = False
        self._thumb_size = self._TREE_THUMB_SIZE   # mutable via Ctrl+Wheel

    # ── Population ───────────────────────────────────────────────────────────
    def populate_root(self, dir_path):
        # Cancel any prior loader; clear the (stale) item map.
        if self._thumb_loader is not None:
            self._thumb_loader.cancel()
            self._thumb_loader = None
        self._items_by_key.clear()
        # Disable sorting during bulk insert — addTopLevelItem with sort
        # enabled is O(log n) per insert + layout cost on every add. We
        # re-enable at the end so the final view is sorted.
        self.setSortingEnabled(False)
        self.clear()
        if os.path.dirname(dir_path) != dir_path:
            up = QTreeWidgetItem(["..", "", "", ""])
            up.setData(0, Qt.ItemDataRole.UserRole, "..")
            up.setIcon(0, self._fm._folder_icon())
            self.addTopLevelItem(up)
        try:
            entries = sorted(os.listdir(dir_path), key=lambda n: n.lower())
        except OSError:
            entries = []
        for name in entries:
            if name.startswith('.'):
                continue
            full = os.path.join(dir_path, name)
            if os.path.isdir(full):
                self.addTopLevelItem(self._make_folder_item(name, full))
        for name in entries:
            if name.startswith('.'):
                continue
            full = os.path.join(dir_path, name)
            if os.path.isfile(full) and name.lower().endswith(_VALID_EXTS):
                self.addTopLevelItem(self._make_file_item(name, full))
        self.setSortingEnabled(True)
        self._kick_thumb_loader()

    def _kick_thumb_loader(self):
        """Start (or restart) the async thumbnail loader for any items
        still missing icons. Cancels any running loader and starts a
        fresh one — _items_by_key only contains UNloaded items (the
        completed-handler removes them as they finish), so no work is
        duplicated, and items added by lazy expansion mid-flight get
        picked up immediately."""
        pending = [(k, p) for k, (it, p) in self._items_by_key.items()]
        if not pending:
            return
        if self._thumb_loader is not None:
            self._thumb_loader.cancel()
        self._thumb_loader = _ThumbLoader(pending, self._thumb_size, self)
        self._thumb_loader.thumb_ready.connect(self._on_thumb_ready)
        self._thumb_loader.start()

    # ── Ctrl+Wheel resize ────────────────────────────────────────────────────
    _MIN_TREE_THUMB = 16
    _MAX_TREE_THUMB = 512

    def wheelEvent(self, ev):
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = ev.angleDelta().y()
            step = 8
            new_size = self._thumb_size + (step if delta > 0 else -step)
            new_size = max(self._MIN_TREE_THUMB, min(self._MAX_TREE_THUMB, new_size))
            if new_size != self._thumb_size:
                self._thumb_size = new_size
                self.setIconSize(QSize(new_size, new_size))
                # Re-render: tree items keyed by old size are stale — repopulate
                self._fm._refresh()
            ev.accept()
            return
        super().wheelEvent(ev)

    def _on_thumb_ready(self, cache_key, pixmap):
        if len(_THUMB_CACHE) >= _THUMB_CACHE_MAX:
            _THUMB_CACHE.pop(next(iter(_THUMB_CACHE)))
        _THUMB_CACHE[cache_key] = pixmap
        entry = self._items_by_key.pop(cache_key, None)
        if entry is None:
            return
        item, _path = entry
        try:
            item.setIcon(0, QIcon(pixmap))
        except RuntimeError:
            # Item was deleted (e.g. tree was cleared mid-load)
            pass

    def _make_folder_item(self, name, full):
        it = QTreeWidgetItem([name, "", "", "Folder"])
        it.setData(0, Qt.ItemDataRole.UserRole, full)
        it.setIcon(0, self._fm._folder_icon())
        # Placeholder child → triangle appears even before we've scanned.
        # The real children are loaded on first expand.
        try:
            has_any = False
            for _ in os.scandir(full):
                has_any = True
                break
            if has_any:
                ph = QTreeWidgetItem([""])
                ph.setData(0, Qt.ItemDataRole.UserRole, self._PLACEHOLDER)
                it.addChild(ph)
        except (OSError, PermissionError):
            pass
        return it

    def _make_file_item(self, name, full):
        size = ""
        date = ""
        mtime = 0
        try:
            mtime = os.path.getmtime(full)
            size = logic.get_sz_readable(full)
            import datetime as _dt
            date = _dt.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass
        ext = os.path.splitext(name)[1].lower().lstrip(".")
        type_text = ext.upper() if ext else "File"
        it = QTreeWidgetItem([name, size, date, type_text])
        it.setData(0, Qt.ItemDataRole.UserRole, full)
        # Thumbnail icon — cached if available, otherwise queued
        cache_key = f"{full}|{mtime}|{self._thumb_size}"
        cached = _THUMB_CACHE.get(cache_key)
        if cached is not None:
            it.setIcon(0, QIcon(cached))
        else:
            self._items_by_key[cache_key] = (it, full)
        return it

    def _on_expand(self, item):
        # Lazy expansion: replace the placeholder child with the real list
        if item.childCount() == 1:
            child = item.child(0)
            if child.data(0, Qt.ItemDataRole.UserRole) == self._PLACEHOLDER:
                item.removeChild(child)
                full = item.data(0, Qt.ItemDataRole.UserRole)
                if full and os.path.isdir(full):
                    try:
                        entries = sorted(os.listdir(full),
                                         key=lambda n: n.lower())
                    except (OSError, PermissionError):
                        entries = []
                    for name in entries:
                        if name.startswith('.'):
                            continue
                        sub = os.path.join(full, name)
                        if os.path.isdir(sub):
                            item.addChild(self._make_folder_item(name, sub))
                    for name in entries:
                        if name.startswith('.'):
                            continue
                        sub = os.path.join(full, name)
                        if (os.path.isfile(sub)
                                and name.lower().endswith(_VALID_EXTS)):
                            item.addChild(self._make_file_item(name, sub))
                    self._kick_thumb_loader()

    # ── Drag start ───────────────────────────────────────────────────────────
    def eventFilter(self, obj, event):
        if obj is self.viewport():
            t = event.type()
            if t == event.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                pos = event.position().toPoint()
                self._press_pos = pos
                self._press_item = self.itemAt(pos)
                # Plain click on an already-selected item in a multi-selection:
                # Qt would collapse to just that item right now, killing the
                # drag. Defer the collapse until release so a drag of the
                # full multi-selection still works.
                mods = event.modifiers()
                modifierless = not (mods & (
                    Qt.KeyboardModifier.ControlModifier |
                    Qt.KeyboardModifier.ShiftModifier))
                sel = self.selectedItems()
                if (modifierless and self._press_item is not None
                        and self._press_item in sel and len(sel) > 1):
                    self._suppress_collapse = True
                    return True   # swallow the press from Qt's default
                self._suppress_collapse = False
            elif t == event.Type.MouseMove:
                if (event.buttons() & Qt.MouseButton.LeftButton
                        and self._press_pos is not None
                        and self._press_item is not None):
                    cur = event.position().toPoint()
                    if (cur - self._press_pos).manhattanLength() > self._DRAG_THRESHOLD:
                        self._press_pos = None
                        self._suppress_collapse = False
                        self._start_url_drag()
                        return True
            elif t == event.Type.MouseButtonRelease:
                # No drag → if we suppressed the collapse on press, do it now.
                if (self._suppress_collapse
                        and self._press_item is not None):
                    self.clearSelection()
                    self._press_item.setSelected(True)
                    self.setCurrentItem(self._press_item)
                self._suppress_collapse = False
                self._press_pos = None
                self._press_item = None
        return super().eventFilter(obj, event)

    def _start_url_drag(self):
        items = self.selectedItems()
        if not items and self._press_item is not None:
            items = [self._press_item]
        if not items:
            return
        urls = []
        for it in items:
            data = it.data(0, Qt.ItemDataRole.UserRole)
            if data and data != ".." and data != self._PLACEHOLDER and os.path.exists(data):
                urls.append(QUrl.fromLocalFile(data))
        if not urls:
            return
        mime = QMimeData()
        mime.setUrls(urls)
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)

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
        target = None
        item = self.itemAt(ev.position().toPoint())
        if item is not None:
            data = item.data(0, Qt.ItemDataRole.UserRole)
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

    def _on_double_click(self, item, col):
        target = item.data(0, Qt.ItemDataRole.UserRole)
        if target == "..":
            self._fm._go_up()
        elif target and os.path.isdir(target):
            self._fm.navigate(target)
        elif target and os.path.isfile(target):
            ph = getattr(self._fm.app, "preview_handler", None)
            if ph:
                try: ph.show(target)
                except Exception: pass


class FileManagerWindow(QWidget):
    DEFAULT_THUMB = 96
    MIN_THUMB     = 48
    MAX_THUMB     = 256

    def __init__(self, app, initial_dir):
        # Parent to the main window with Window flag so we stay a separate
        # top-level window but share its lifecycle — closing main closes
        # the FM too.
        super().__init__(app, Qt.WindowType.Window)
        self.app = app
        self.setWindowTitle(f"AItan — File Manager  Ver {VERSION}")
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
        # View toggle: 📋 list ⇄ 🔲 icons
        self.btn_view_toggle = QPushButton("🔲")
        self.btn_view_toggle.setToolTip("Toggle list / icon view")
        self.btn_view_toggle.setFixedWidth(36)
        self.btn_view_toggle.clicked.connect(self._toggle_view)
        tb.addWidget(self.btn_view_toggle)
        v.addLayout(tb)

        # Stacked: tree/list view (default) + icon view
        self.stack       = QStackedWidget()
        self.list_table  = _FMTreeList(self)
        self.list_grid   = _FMIconList(self)
        self.list_grid.itemDoubleClicked.connect(self._on_item_double_click)
        self.list_grid.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_grid.customContextMenuRequested.connect(self._on_context_menu)
        self.list_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_table.customContextMenuRequested.connect(self._on_context_menu)
        self.stack.addWidget(self.list_table)   # index 0 = tree view
        self.stack.addWidget(self.list_grid)    # index 1 = icon view
        v.addWidget(self.stack, 1)
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
        self.list_grid.clear()
        self._row_of_key.clear()
        # Tree view manages its own population (handles lazy expansion).
        self.list_table.populate_root(self._cur_dir)

        try:
            entries = sorted(os.listdir(self._cur_dir),
                             key=lambda n: n.lower())
        except OSError:
            entries = []

        thumb_requests   = []
        placeholder_icon = self._placeholder_file_icon()
        folder_icon      = self._folder_icon()

        if os.path.dirname(self._cur_dir) != self._cur_dir:
            up = QListWidgetItem("..")
            up.setData(Qt.ItemDataRole.UserRole, "..")
            up.setIcon(folder_icon)
            up.setFlags(up.flags() | Qt.ItemFlag.ItemIsDropEnabled)
            self.list_grid.addItem(up)

        for name in entries:
            if name.startswith('.'):
                continue
            full = os.path.join(self._cur_dir, name)
            if os.path.isdir(full):
                grid_it = QListWidgetItem(name)
                grid_it.setData(Qt.ItemDataRole.UserRole, full)
                grid_it.setIcon(folder_icon)
                grid_it.setFlags(grid_it.flags() | Qt.ItemFlag.ItemIsDropEnabled)
                self.list_grid.addItem(grid_it)

        for name in entries:
            if name.startswith('.'):
                continue
            full = os.path.join(self._cur_dir, name)
            if not (os.path.isfile(full) and name.lower().endswith(_VALID_EXTS)):
                continue
            grid_it = QListWidgetItem(name)
            grid_it.setData(Qt.ItemDataRole.UserRole, full)
            grid_it.setFlags(grid_it.flags() | Qt.ItemFlag.ItemIsDropEnabled)
            try:
                mtime = os.path.getmtime(full)
            except OSError:
                mtime = 0
            cache_key = f"{full}|{mtime}|{self._thumb_size}"
            cached = _THUMB_CACHE.get(cache_key)
            if cached is not None:
                grid_it.setIcon(QIcon(cached))
            else:
                grid_it.setIcon(placeholder_icon)
                thumb_requests.append((cache_key, full))
            self._row_of_key[cache_key] = self.list_grid.count()
            self.list_grid.addItem(grid_it)

        self._update_nav_buttons()
        if thumb_requests:
            self._thumb_loader = _ThumbLoader(
                thumb_requests, self._thumb_size, self)
            self._thumb_loader.thumb_ready.connect(self._on_thumb_ready)
            self._thumb_loader.start()

    def _on_thumb_ready(self, cache_key, pixmap):
        if len(_THUMB_CACHE) >= _THUMB_CACHE_MAX:
            _THUMB_CACHE.pop(next(iter(_THUMB_CACHE)))
        _THUMB_CACHE[cache_key] = pixmap
        row = self._row_of_key.get(cache_key)
        if row is None:
            return
        item = self.list_grid.item(row)
        if item is None:
            return
        item.setIcon(QIcon(pixmap))

    # ── View toggle ──────────────────────────────────────────────────────────
    def _toggle_view(self):
        # 0 = list table, 1 = icon grid
        new_idx = 1 if self.stack.currentIndex() == 0 else 0
        self.stack.setCurrentIndex(new_idx)
        # Toolbar glyph reflects the OTHER view (the one a click would
        # take you to next).
        self.btn_view_toggle.setText("🔲" if new_idx == 0 else "📋")

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
        self.list_grid.setIconSize(QSize(s, s))
        # Two-line label area below the icon. ~16 px per line + 8 px margin.
        self.list_grid.setGridSize(QSize(s + 24, s + 48))

    # ── Move (drop target) ───────────────────────────────────────────────────
    @property
    def config(self):
        return self.app.config if hasattr(self, 'app') and hasattr(self.app, 'config') else {}

    def move_files_into(self, src_paths, target_dir):
        moved   = 0
        renames = {}            # old → new (for batched store flush)
        errors  = []
        batch   = []
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
                batch.append({"type": "move", "old_path": src, "new_path": dst})
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
                if getattr(self.app, "current_project", None):
                    _am.save(self.app.current_project, self.app.attrs_data)
                if hasattr(self.app, "_push_undo"):
                    self.app._push_undo(batch)
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

    # ── Active view helpers ─────────────────────────────────────────────────
    def _active_view(self):
        return self.stack.currentWidget()

    def _path_at(self, view, pos):
        """Path of the item at viewport pos in the given view, or None."""
        if isinstance(view, QTreeWidget):
            it = view.itemAt(pos)
            return it.data(0, Qt.ItemDataRole.UserRole) if it else None
        if isinstance(view, QListWidget):
            it = view.itemAt(pos)
            return it.data(Qt.ItemDataRole.UserRole) if it else None
        return None

    def _current_path(self):
        view = self._active_view()
        if isinstance(view, QTreeWidget):
            it = view.currentItem()
            return it.data(0, Qt.ItemDataRole.UserRole) if it else None
        if isinstance(view, QListWidget):
            it = view.currentItem()
            return it.data(Qt.ItemDataRole.UserRole) if it else None
        return None

    def _selected_paths(self):
        view = self._active_view()
        out = []
        if isinstance(view, QTreeWidget):
            for it in view.selectedItems():
                d = it.data(0, Qt.ItemDataRole.UserRole)
                if d and d != ".." and d != "__placeholder__":
                    out.append(d)
        elif isinstance(view, QListWidget):
            for it in view.selectedItems():
                d = it.data(Qt.ItemDataRole.UserRole)
                if d and d != "..":
                    out.append(d)
        return out

    # ── Context menu ─────────────────────────────────────────────────────────
    def _on_context_menu(self, pos):
        view = self._active_view()
        path = self._path_at(view, pos)
        menu = QMenu(self)

        act_new = QAction("New Folder", self)
        act_new.triggered.connect(self._new_folder)
        menu.addAction(act_new)

        if path and path != "..":
            menu.addSeparator()
            act_rename = QAction("Rename (F2)", self)
            act_rename.triggered.connect(self._rename_selected)
            menu.addAction(act_rename)

            act_delete = QAction("Move to Trash (Del)", self)
            act_delete.triggered.connect(self._delete_selected)
            menu.addAction(act_delete)

            act_open_loc = QAction("Open in Nemo", self)
            act_open_loc.triggered.connect(lambda _, p=path: self._open_in_nemo(p))
            menu.addAction(act_open_loc)

        menu.exec(view.viewport().mapToGlobal(pos))

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
        old_path = self._current_path()
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
        paths = self._selected_paths()
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
