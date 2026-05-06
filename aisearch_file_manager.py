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
                              QAbstractItemView, QTreeWidget, QTreeWidgetItem,
                              QSplitter)
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
        # Interactive on every column → user can drag dividers to resize
        for c in range(4):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(False)
        hdr.setSectionsClickable(True)   # explicit — needed for sort-on-click
        # Sensible defaults so the columns aren't all the same width
        self.setColumnWidth(0, 380)   # Name
        self.setColumnWidth(1, 90)    # Size
        self.setColumnWidth(2, 140)   # Date
        self.setColumnWidth(3, 70)    # Type
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAcceptDrops(True)
        self.setRootIsDecorated(True)   # show expand triangles on top-level
        # uniformRowHeights(True) caches the first row's height; icons
        # resized later (Ctrl+Wheel) wouldn't grow rows. Off so each row
        # tracks its own content.
        self.setUniformRowHeights(False)
        self.setAllColumnsShowFocus(True)
        # Bigger indent so nesting depth is visible. Leaving the branch
        # styling at Qt's default — earlier custom border-image rules
        # also wiped out the expand triangles, since Qt uses the same
        # branch border-image to render both the lines AND the triangle
        # for items with children.
        self.setIndentation(28)
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
        # Cancel any prior loader and wait for it to exit before
        # dropping the reference (Qt aborts if a QThread is destroyed
        # while still running).
        if self._thumb_loader is not None:
            self._thumb_loader.cancel()
            if self._thumb_loader.isRunning():
                self._thumb_loader.wait(2000)
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
        still missing icons. Cancels any running loader, waits for it
        to actually exit (otherwise Qt aborts with 'terminate called
        without an active exception' when the old QThread is dropped
        while still running), then starts a fresh one — _items_by_key
        only contains UNloaded items, so no work is duplicated."""
        pending = [(k, p) for k, (it, p) in self._items_by_key.items()]
        if not pending:
            return
        if self._thumb_loader is not None:
            self._thumb_loader.cancel()
            # Wait for the run loop to actually return before we drop
            # the reference. Cancel only sets a flag — the thread may
            # be mid-decode. Bound the wait so a slow video decode
            # can't freeze us forever.
            if not self._thumb_loader.wait(2000):
                # Thread didn't exit in 2 s — leave it alone, Python
                # will deal with it. This shouldn't happen in practice
                # since the cancel flag is checked between every file.
                pass
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


class FilePane(QWidget):
    """One pane of the FM. Self-contained: toolbar (back/fwd/up + path)
    + tree, with its own navigation state. The tree's `_fm` reference
    points here so all the existing tree drag/drop/navigation hooks
    keep working unchanged."""

    def __init__(self, fm, initial_dir):
        super().__init__()
        self.fm = fm                  # main FileManagerWindow (shared ops)
        self._cur_dir     = None
        self._history     = []
        self._history_idx = -1

        v = QVBoxLayout(self)
        v.setContentsMargins(2, 2, 2, 2)
        v.setSpacing(2)

        tb = QHBoxLayout()
        self.btn_back = QPushButton("◀")
        self.btn_fwd  = QPushButton("▶")
        self.btn_up   = QPushButton("▲")
        for b in (self.btn_back, self.btn_fwd, self.btn_up):
            b.setFixedWidth(28)
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

        self.tree = _FMTreeList(self)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        v.addWidget(self.tree, 1)

        if initial_dir and os.path.isdir(initial_dir):
            self.navigate(initial_dir)

    # Surface that the tree expects ─────────────────────────────────────────
    @property
    def app(self):
        return self.fm.app

    def _folder_icon(self):
        return self.fm._folder_icon()

    def move_files_into(self, src_paths, target_dir):
        # Delegate the heavy lifting to FM (touches app data + disk
        # stores), then refresh both panes if dual-pane is active.
        self.fm.move_files_into(src_paths, target_dir)

    # ── Navigation ──────────────────────────────────────────────────────────
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

    def _on_path_edit_enter(self):
        p = self.path_edit.text().strip()
        if p and os.path.isdir(p):
            self.navigate(p)
        else:
            self.path_edit.setText(self._cur_dir or "")

    def _refresh(self):
        self._cur_dir = self._history[self._history_idx]
        self.path_edit.setText(self._cur_dir)
        self.tree.populate_root(self._cur_dir)
        self._update_nav_buttons()

    def _update_nav_buttons(self):
        self.btn_back.setEnabled(self._history_idx > 0)
        self.btn_fwd.setEnabled(self._history_idx < len(self._history) - 1)
        self.btn_up.setEnabled(
            bool(self._cur_dir)
            and os.path.dirname(self._cur_dir) != self._cur_dir)

    # ── Selected-paths helpers (used by context menu / shortcuts) ───────────
    def _path_at_pos(self, pos):
        it = self.tree.itemAt(pos)
        return it.data(0, Qt.ItemDataRole.UserRole) if it else None

    def _current_path(self):
        it = self.tree.currentItem()
        return it.data(0, Qt.ItemDataRole.UserRole) if it else None

    def _selected_paths(self):
        out = []
        for it in self.tree.selectedItems():
            d = it.data(0, Qt.ItemDataRole.UserRole)
            if d and d != ".." and d != _FMTreeList._PLACEHOLDER:
                out.append(d)
        return out

    # ── Context menu / file ops (delegate to FM for app sync) ───────────────
    def _on_context_menu(self, pos):
        path = self._path_at_pos(pos)
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
            act_open = QAction("Open in Nemo", self)
            act_open.triggered.connect(lambda _, p=path: self.fm._open_in_nemo(p))
            menu.addAction(act_open)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

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
        self.fm.refresh_all()

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
            QMessageBox.warning(self, "Rename", f"Already exists:\n{new_path}")
            return
        try:
            os.rename(old_path, new_path)
        except Exception as e:
            QMessageBox.critical(self, "Rename", f"Could not rename:\n{e}")
            return
        if os.path.isfile(new_path):
            self.fm._sync_in_memory(old_path, new_path)
            try:
                import aisearch_attrs as _am
                _am.flush_path_renames_to_stores(
                    {old_path: new_path},
                    getattr(self.app, "current_project", None))
            except Exception:
                pass
        elif os.path.isdir(new_path):
            self.fm._sync_folder_rename(old_path, new_path)
        self.fm.refresh_all()

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
                self.fm._remove_from_app_state(p)
            except Exception as e:
                errors.append(f"{os.path.basename(p)}: {e}")
        if errors:
            QMessageBox.warning(self, "Trash",
                f"Errors:\n" + "\n".join(errors[:10]))
        self.fm.refresh_all()


class FileManagerWindow(QWidget):
    """Top-level FM window. Holds 1 (single) or 2 (dual) FilePanes in
    a horizontal QSplitter. Toggle button switches between modes."""

    DEFAULT_THUMB = 96

    def __init__(self, app, initial_dir):
        # Parent to the main window with Window flag so we stay a separate
        # top-level window but share its lifecycle — closing main closes
        # the FM too.
        super().__init__(app, Qt.WindowType.Window)
        self.app = app
        self.setWindowTitle(f"AItan — File Manager  Ver {VERSION}")
        self.resize(1200, 720)

        self._folder_icon_cached = None
        self._initial_dir = initial_dir

        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(4)

        # Top toolbar: only the dual-pane toggle for now.
        tb = QHBoxLayout()
        self.btn_pane_toggle = QPushButton("▥ Dual pane")
        self.btn_pane_toggle.setToolTip("Toggle single / dual pane")
        self.btn_pane_toggle.clicked.connect(self._toggle_dual_pane)
        tb.addWidget(self.btn_pane_toggle)
        tb.addStretch(1)
        v.addLayout(tb)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        v.addWidget(self.splitter, 1)

        # Start in single-pane mode.
        self._panes: list[FilePane] = []
        self._add_pane(initial_dir)

        # Shortcuts
        QShortcut(QKeySequence("F2"), self, activated=self._rename_active)
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self,
                  activated=self._delete_active)

    # ── Pane management ─────────────────────────────────────────────────────
    def _add_pane(self, initial_dir):
        pane = FilePane(self, initial_dir)
        self._panes.append(pane)
        self.splitter.addWidget(pane)

    def _toggle_dual_pane(self):
        if len(self._panes) == 1:
            # Open second pane at the same dir as the first
            cur = self._panes[0]._cur_dir or self._initial_dir
            self._add_pane(cur)
            self.btn_pane_toggle.setText("▣ Single pane")
        else:
            # Tear down the second pane
            second = self._panes.pop()
            second.setParent(None)
            second.deleteLater()
            self.btn_pane_toggle.setText("▥ Dual pane")

    def _active_pane(self) -> FilePane:
        # Pane that owns the focused widget; fall back to the first.
        fw = self.focusWidget()
        for p in self._panes:
            w = fw
            while w is not None:
                if w is p:
                    return p
                w = w.parentWidget()
        return self._panes[0]

    def refresh_all(self):
        for p in self._panes:
            p._refresh()

    # ── External entry point used by AISearchApp ────────────────────────────
    def navigate(self, path):
        """Called by main app's right-arrow handler. Navigate the active
        pane (or the first pane if focus is elsewhere)."""
        self._active_pane().navigate(path)

    # ── Shortcut handlers — route to the active pane ────────────────────────
    def _rename_active(self):
        self._active_pane()._rename_selected()

    def _delete_active(self):
        self._active_pane()._delete_selected()

    # ── Shared icon helpers ─────────────────────────────────────────────────
    def _folder_icon(self):
        if self._folder_icon_cached is None:
            size = 32
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
        self.refresh_all()

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

    # Per-pane menus / shortcuts: each FilePane owns its own context
    # menu, rename, delete, new-folder. Only the cross-pane refresh
    # and app-data sync helpers stay on the FM.

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
        # Wait for any in-flight thumbnail loaders to exit before Qt
        # tears down their owners. Without the wait, dropping the
        # QThread reference mid-run aborts with "terminate called
        # without an active exception".
        for pane in self._panes:
            tl = getattr(pane.tree, "_thumb_loader", None)
            if tl is not None and tl.isRunning():
                tl.cancel()
                tl.wait(2000)
        super().closeEvent(ev)
