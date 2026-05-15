"""
File Manager window — Nemo-style folder browser.

Phase-1 cut: navigate folders, view icon grid of files+folders, multi-select,
Ctrl+wheel to resize thumbnails. Thumbnails load asynchronously in a worker
thread so the window paints instantly even on big folders.
"""
import math
import os
import shutil
import time

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QListWidget,
                              QListWidgetItem, QLabel, QPushButton, QLineEdit,
                              QMessageBox, QInputDialog, QMenu, QStackedWidget,
                              QTableWidget, QTableWidgetItem, QHeaderView,
                              QAbstractItemView, QTreeWidget, QTreeWidgetItem,
                              QSplitter, QDialog, QCheckBox, QGridLayout,
                              QSizePolicy)
from PyQt6.QtCore import (Qt, QSize, QUrl, QMimeData, QThread, pyqtSignal,
                          QPoint, QPointF, QRectF, QTimer)
from PyQt6.QtGui import (QPixmap, QIcon, QImageReader, QPainter, QImage,
                         QColor, QPen, QFont, QShortcut, QKeySequence, QAction,
                         QDrag)

import aisearch_logic as logic

VERSION = "2.5.8"


_VALID_EXTS = tuple(ext.lower() for ext in (logic.EXT_IMG + logic.EXT_VID))

# Persist across navigations so revisits are instant. Key: "{path}|{mtime}".
# Values are QPixmap decoded at _DECODE_SIZE — a single high-res master that
# Qt scales smoothly inside the icon view, so Ctrl+Wheel resize doesn't have
# to re-decode anything (or even re-key the cache).
_THUMB_CACHE: dict = {}
_THUMB_CACHE_MAX = 500
_DECODE_SIZE = 128   # source pixmap size. Big enough for sharp display
                     # at the default 32-px tree thumb plus moderate
                     # Ctrl+Wheel zoom; small enough that decoding a
                     # child folder's worth of images / video first
                     # frames doesn't backlog the loader. Beyond 128 px
                     # zoom the icon scales bilinearly (some softness)
                     # — same trade-off Nemo makes.


# ── Open / Open with… (module-level so other UIs can use them) ────────────
_IMAGE_APPS_LINUX = [
    ("Pix",      ["pix"]),
    ("GIMP",     ["gimp"]),
    ("Krita",    ["krita"]),
    ("xviewer",  ["xviewer"]),
    ("eog",      ["eog"]),
    ("feh",      ["feh"]),
]
_VIDEO_APPS_LINUX = [
    ("VLC",       ["vlc"]),
    ("MPV",       ["mpv"]),
    ("Celluloid", ["celluloid"]),
    ("MPlayer",   ["mplayer"]),
]
_IMAGE_APPS_MAC = [
    ("Preview",    ["open", "-a", "Preview"]),
    ("GIMP",       ["open", "-a", "GIMP"]),
    ("Pixelmator", ["open", "-a", "Pixelmator"]),
]
_VIDEO_APPS_MAC = [
    ("QuickTime", ["open", "-a", "QuickTime Player"]),
    ("VLC",       ["open", "-a", "VLC"]),
    ("IINA",      ["open", "-a", "IINA"]),
]
_IMAGE_APPS_WIN = [
    ("Paint",     ["mspaint.exe"]),
    ("GIMP",      ["gimp"]),
    ("IrfanView", ["i_view64.exe"]),
]
_VIDEO_APPS_WIN = [
    ("VLC",       ["vlc"]),
    ("MPV",       ["mpv"]),
]


def open_default(path):
    """Open `path` with the system default app. Platform-dispatched:
    Windows uses os.startfile, macOS uses `open`, Linux uses xdg-open.
    User's OS-level MIME associations decide which program runs."""
    import sys, subprocess
    try:
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


def open_with(cmd, path):
    """Run a chosen program against `path`. `cmd` is the platform-
    specific argv list ready to receive the file path appended."""
    import subprocess
    try:
        args = cmd if isinstance(cmd, list) else [cmd]
        subprocess.Popen(args + [path])
    except (FileNotFoundError, Exception):
        pass


def app_choices(path):
    """Return [(label, argv)] pairs for apps installed on the system
    that handle this file's type. Only candidates whose leading
    executable is on PATH are returned."""
    import sys, shutil as _sh
    ext = os.path.splitext(path)[1].lower()
    if sys.platform == "win32":
        img, vid = _IMAGE_APPS_WIN, _VIDEO_APPS_WIN
    elif sys.platform == "darwin":
        img, vid = _IMAGE_APPS_MAC, _VIDEO_APPS_MAC
    else:
        img, vid = _IMAGE_APPS_LINUX, _VIDEO_APPS_LINUX
    candidates = (vid + img) if ext in logic.EXT_VID else img
    out = []
    for label, argv in candidates:
        exe = argv[0]
        if _sh.which(exe):
            out.append((label, argv))
    return out


def _stamp_rim(pixmap, color, width=3):
    """Stamp a colored border around the pixmap (mutates in place)."""
    p = QPainter(pixmap)
    pen = QPen(QColor(color))
    pen.setWidth(width)
    pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    p.setPen(pen)
    half = width / 2
    p.drawRect(int(half), int(half),
               pixmap.width() - width, pixmap.height() - width)
    p.end()
    return pixmap


# Rim color per (is_video, locked) state. User-overridable via
# Appearance settings (config keys below) so the colors aren't
# hard-baked. Defaults follow the project's color convention:
#   unlocked video → bright green (default video marker)
#   locked   video → dark   green (video marker + locked tint)
#   unlocked pic   → no rim
#   locked   pic   → dark   red   (locked-pic marker)
_RIM_DEFAULTS = {
    "rim_video_open": "#00ff00",
    "rim_video_lock": "#1a6a1a",
    "rim_pic_lock":   "#a01a1a",
}


def _rim_color_for(is_video, locked, cfg=None):
    cfg = cfg or {}
    if is_video and locked:
        return cfg.get("rim_video_lock", _RIM_DEFAULTS["rim_video_lock"])
    if is_video:
        return cfg.get("rim_video_open", _RIM_DEFAULTS["rim_video_open"])
    if locked:
        return cfg.get("rim_pic_lock", _RIM_DEFAULTS["rim_pic_lock"])
    return None   # unlocked picture — no rim


def _drop_wants_copy(ev):
    """Single source of truth for FM drop copy/move intent."""
    return (
        bool(ev.modifiers() & Qt.KeyboardModifier.ControlModifier)
        or ev.dropAction() == Qt.DropAction.CopyAction
        or ev.proposedAction() == Qt.DropAction.CopyAction
    )


def _local_paths_from_drop(ev):
    return [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]


def _drop_paths_allowed(src_paths, target_dir, is_copy):
    """Reject impossible folder drops consistently across all FM views.

    Ctrl-copy onto the exact same folder is allowed; it creates a nested
    duplicate there. Placing a folder inside one of its descendants is
    never allowed because it has no stable filesystem meaning.
    """
    for src in src_paths:
        try:
            if not os.path.isdir(src):
                continue
            src_abs = os.path.abspath(src)
            tgt_abs = os.path.abspath(target_dir)
            if not is_copy and tgt_abs == src_abs:
                return False
            if tgt_abs.startswith(src_abs + os.sep):
                return False
        except Exception:
            pass
    return True


def _handle_fm_url_drop(ev, target_dir, move_files_into, error_parent=None):
    """Common FM URL drop path used by icon, tree, and carousel views."""
    if not ev.mimeData().hasUrls():
        ev.ignore(); return False
    srcs = _local_paths_from_drop(ev)
    if not srcs or not target_dir or not os.path.isdir(target_dir):
        ev.ignore(); return False
    is_copy = _drop_wants_copy(ev)
    if not _drop_paths_allowed(srcs, target_dir, is_copy):
        ev.ignore(); return False
    mode = "copy" if is_copy else "move"
    ev.acceptProposedAction()
    try:
        move_files_into(srcs, target_dir, mode=mode)
    except Exception as e:
        import traceback
        QMessageBox.critical(
            error_parent, "Drop Error",
            f"Failed to {mode} files:\n{e}\n{traceback.format_exc()}")
    return True


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
        # NOTE: rim is now stamped at icon-apply time (so lock-state
        # changes can update the rim without invalidating the cache).
        # The cached pixmap stays plain.
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
        self._fm_pending_collapse = False
        self.viewport().installEventFilter(self)

    def eventFilter(self, obj, event):
        # Defer all selection logic to Qt's native ExtendedSelection
        # handling — it already implements Nemo-style deferred selection
        # (press on a selected item in a multi-selection doesn't collapse
        # until release, unless a drag fires first). The previous custom
        # implementation tried to consume the press and re-do this logic
        # manually but ended up collapsing the selection on plain clicks,
        # so the user couldn't drag a multi-selection. Now we only track
        # press position for drag detection and let Qt handle selection.
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
                # never consume — Qt's native handler runs
            elif t == event.Type.MouseMove:
                if (event.buttons() & Qt.MouseButton.LeftButton
                        and self._fm_press_pos is not None
                        and self._fm_press_item is not None):
                    cur = event.position().toPoint()
                    if (cur - self._fm_press_pos).manhattanLength() > self._DRAG_THRESHOLD:
                        # Start drag with all currently selected items.
                        # _start_url_drag pulls selectedItems() itself —
                        # Qt's deferred selection has preserved the
                        # full multi-selection on the press.
                        item = self._fm_press_item
                        self._fm_press_pos = None
                        self._fm_press_item = None
                        self._start_url_drag(seed_item=item)
                        return True
                    # Below threshold and press was on an item: suppress
                    # Qt's MouseMove unconditionally so it doesn't
                    # rubberband-extend the selection while we wait for
                    # the drag threshold. With ExtendedSelection, even a
                    # single-item press + tiny mouse drift was being read
                    # as "rubberband range-extend from this anchor",
                    # which felt like the user was holding Shift. Mirrors
                    # main-app FileTable.mouseMoveEvent.
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
        paths = []
        for it in items:
            data = it.data(Qt.ItemDataRole.UserRole)
            if data and data != ".." and os.path.exists(data):
                paths.append(data)
        paths = _prune_descendants(paths)
        urls = [QUrl.fromLocalFile(p) for p in paths]
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
        # Include LinkAction so external apps that import files by
        # reference (Kdenlive, OBS, image editors) can pick it. Without
        # Link, Kdenlive's drop handler bails because the only offered
        # actions involve copying or moving the file off disk, which it
        # doesn't want to do. Internal FM drops still default to Move.
        from PyQt6.QtWidgets import QApplication as _QApp
        default_action = (
            Qt.DropAction.CopyAction
            if _QApp.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier
            else Qt.DropAction.MoveAction)
        drag.exec(Qt.DropAction.MoveAction | Qt.DropAction.CopyAction
                  | Qt.DropAction.LinkAction,
                  default_action)

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
        _handle_fm_url_drop(ev, target, self._fm.move_files_into, self)


class _FMItem(QTreeWidgetItem):
    """QTreeWidgetItem that always groups folders before files, no
    matter which column the user sorted by. ".." always pins to the
    top. Within the same kind, the default per-column comparison
    applies, so click-header sort still does what you'd expect."""
    KIND_RANK = {"up": 0, "dir": 1, "file": 2}

    def __init__(self, columns, kind="file"):
        super().__init__(columns)
        self._kind = kind

    def __lt__(self, other):
        a = self.KIND_RANK.get(self._kind, 2)
        b = self.KIND_RANK.get(getattr(other, "_kind", "file"), 2)
        if a != b:
            # When sorting descending Qt inverts the result of __lt__,
            # which would put files above folders. Compare via the
            # tree's current sort order so the kind ordering is stable
            # regardless of direction.
            tree = self.treeWidget()
            descending = (tree is not None
                          and tree.header().sortIndicatorOrder()
                              == Qt.SortOrder.DescendingOrder)
            return a > b if descending else a < b
        return super().__lt__(other)


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
        # Inline-rename: when the user commits an edit on column 0,
        # itemChanged fires and we run the on-disk rename. The lock
        # blocks our own revert setText() from re-entering.
        self._editing_lock = False
        self.itemChanged.connect(self._on_item_changed)
        # Edge-scroll during drag: when the cursor hovers near the
        # top or bottom of the viewport while dragging, the tree
        # scrolls so the user can drop on rows that are off-screen.
        # Speed scales with how close the cursor is to the edge.
        from PyQt6.QtCore import QTimer
        self._drag_scroll_timer = QTimer(self)
        self._drag_scroll_timer.setInterval(40)   # ~25 fps
        self._drag_scroll_timer.timeout.connect(self._drag_scroll_tick)
        self._drag_scroll_speed = 0   # px per tick; sign = direction
        # Manual drag start via viewport eventFilter (same pattern as
        # main's FileTable, which works in PyQt6).
        self._press_pos  = None
        self._press_item = None
        self.viewport().installEventFilter(self)
        # cache_key → QTreeWidgetItem for async-loaded thumbnails
        self._items_by_key = {}
        self._thumb_loader = None
        self._pending_collapse = False  # Nemo-style: collapse multi-select
                                        # only on release if no drag fired
        self._thumb_size = self._TREE_THUMB_SIZE   # mutable via Ctrl+Wheel

    # ── Population ───────────────────────────────────────────────────────────
    def _collect_expanded(self):
        """Return the set of folder paths currently expanded so a
        repopulate can restore them."""
        out = set()
        def visit(item):
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if (item.isExpanded() and data
                    and data != ".." and data != self._PLACEHOLDER):
                out.add(data)
            for i in range(item.childCount()):
                visit(item.child(i))
        for i in range(self.topLevelItemCount()):
            visit(self.topLevelItem(i))
        return out

    def _restore_expanded(self, paths):
        """Walk the tree and re-expand items whose path is in `paths`.
        Expanding triggers _on_expand which lazily populates children,
        so deeper expansions cascade correctly."""
        if not paths:
            return
        # BFS so a parent expands before we look at its children
        queue = [self.topLevelItem(i)
                 for i in range(self.topLevelItemCount())]
        while queue:
            it = queue.pop(0)
            data = it.data(0, Qt.ItemDataRole.UserRole)
            if data in paths and not it.isExpanded():
                it.setExpanded(True)
            for i in range(it.childCount()):
                queue.append(it.child(i))

    def populate_root(self, dir_path):
        # Cancel any prior loader without blocking the UI. Hold a ref in
        # _retired_loaders until its `finished` signal lands, so Qt
        # doesn't abort on QThread destruction.
        if self._thumb_loader is not None:
            if not hasattr(self, "_retired_loaders"):
                self._retired_loaders = []
            old = self._thumb_loader
            old.cancel()
            self._retired_loaders.append(old)
            old.finished.connect(
                lambda _ldr=old: (
                    self._retired_loaders.remove(_ldr)
                    if _ldr in self._retired_loaders else None))
            self._thumb_loader = None
        # Snapshot expansion before we wipe the tree, so a
        # repopulate (e.g. Ctrl+Wheel resize) doesn't collapse folders
        # the user had open.
        prev_expanded = self._collect_expanded()
        self._items_by_key.clear()
        # Disable sorting during bulk insert — addTopLevelItem with sort
        # enabled is O(log n) per insert + layout cost on every add. We
        # re-enable at the end so the final view is sorted.
        self.setSortingEnabled(False)
        self.clear()
        if os.path.dirname(dir_path) != dir_path:
            up = _FMItem(["..", "", "", ""], kind="up")
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
        # Re-expand whatever was expanded before. _on_expand will lazily
        # populate each one's children (and recursive expand cascades
        # via the BFS in _restore_expanded).
        self._restore_expanded(prev_expanded)
        self._kick_thumb_loader()

    def _kick_thumb_loader(self):
        """Start (or restart) the async thumbnail loader for any items
        still missing icons. The old loader is cancelled and kept alive
        in `_retired_loaders` until its `finished` signal fires — this
        avoids the 2-second main-thread wait that previously blocked
        mouse / drag events whenever you changed folder or expanded a
        subfolder. Old emits past cancel are guarded inside
        _ThumbLoader.run(); duplicates are harmless because
        _on_thumb_ready pops from _items_by_key on first hit."""
        pending = [(k, p) for k, (it, p) in self._items_by_key.items()]
        if not pending:
            return
        if not hasattr(self, "_retired_loaders"):
            self._retired_loaders = []
        if self._thumb_loader is not None:
            old = self._thumb_loader
            old.cancel()
            self._retired_loaders.append(old)
            # Drop our hard ref once the thread actually finishes; that's
            # what keeps Qt from aborting on QThread destruction.
            old.finished.connect(
                lambda _ldr=old: (
                    self._retired_loaders.remove(_ldr)
                    if _ldr in self._retired_loaders else None))
        self._thumb_loader = _ThumbLoader(pending, _DECODE_SIZE, self)
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
                # Cached pixmaps are decoded at _DECODE_SIZE and the cache
                # key no longer includes thumb_size, so resizing the icon
                # view is the only work needed per wheel tick — Qt scales
                # the existing pixmaps smoothly. No tree rebuild, no
                # decode, no cache miss.
                self.setIconSize(QSize(new_size, new_size))
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
            item.setIcon(0, QIcon(self._rim_for(_path, pixmap)))
        except RuntimeError:
            # Item was deleted (e.g. tree was cleared mid-load)
            pass

    def _rim_for(self, path, pixmap):
        """Return a pixmap with the appropriate rim stamped on a copy
        (cache stays plain). Rim color depends on file kind (video vs.
        picture) and lock state (entry["editable"] in attrs_data).
        Colors are user-overridable via Appearance config keys.

        Also overlays a cyan border when the file is a face-sample
        (matches the main table's _row_rim_icon behavior). Lets the
        user spot training samples in the FM tree at a glance."""
        import aisearch_attrs as _am
        ext = os.path.splitext(path)[1].lower()
        is_video = ext in logic.EXT_VID
        app = self._fm.app
        attrs_data = getattr(app, "attrs_data", {}) or {}
        locked = not _am.is_editable(attrs_data, path)
        cfg = getattr(app, "config", {}) or {}
        color = _rim_color_for(is_video, locked, cfg)
        try:
            is_sample = bool(getattr(app, "_is_face_sample", lambda _p: False)(path))
        except Exception:
            is_sample = False
        if not color and not is_sample:
            return pixmap
        out = pixmap.copy()
        if color:
            _stamp_rim(out, color)
        if is_sample:
            # Overlay a thin cyan border on top of (or instead of) the
            # kind/lock rim. Same color the main-table _row_rim_icon
            # uses so the two views are consistent.
            try:
                from PyQt6.QtGui import QPainter as _QP, QPen as _QPen, QColor as _QC
                p = _QP(out)
                try:
                    pen = _QPen(_QC("#00d0ff"))
                    pen.setWidth(2)
                    p.setPen(pen)
                    w, h = out.width(), out.height()
                    p.drawRect(1, 1, max(1, w - 2), max(1, h - 2))
                finally:
                    p.end()
            except Exception:
                pass
        return out

    def _make_folder_item(self, name, full):
        it = _FMItem([name, "", "", "Folder"], kind="dir")
        it.setData(0, Qt.ItemDataRole.UserRole, full)
        it.setIcon(0, self._fm._folder_icon())
        # Editable so F2 can edit the name in place. NoEditTriggers
        # is set on the tree, so editing only starts when we call
        # editItem() explicitly (from _rename_selected).
        it.setFlags(it.flags() | Qt.ItemFlag.ItemIsEditable)
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
        it = _FMItem([name, size, date, type_text], kind="file")
        it.setData(0, Qt.ItemDataRole.UserRole, full)
        it.setFlags(it.flags() | Qt.ItemFlag.ItemIsEditable)
        # Thumbnail icon — cached if available, otherwise queued
        cache_key = f"{full}|{mtime}"
        cached = _THUMB_CACHE.get(cache_key)
        if cached is not None:
            it.setIcon(0, QIcon(self._rim_for(full, cached)))
        else:
            self._items_by_key[cache_key] = (it, full)
        return it

    def refresh_all_rims(self):
        """Walk the tree (including already-expanded children) and re-stamp
        the rim on every file item's icon based on current attrs_data.
        Cheaper than populate_root because we don't clear / rebuild —
        just update icons in place. Used after a rename / lock toggle
        so files in expanded subfolders get the new rim without
        depending on a re-expand cycle."""
        from PyQt6.QtCore import Qt as _Qt
        def visit(it):
            try:
                data = it.data(0, _Qt.ItemDataRole.UserRole)
                if data and data != ".." and data != self._PLACEHOLDER:
                    if os.path.isfile(data):
                        # Look up cached pixmap. If absent, the icon is
                        # already either an emoji folder or queued for
                        # async load — _on_thumb_ready will rim it then.
                        try:
                            mtime = os.path.getmtime(data)
                        except OSError:
                            mtime = 0
                        cache_key = f"{data}|{mtime}"
                        cached = _THUMB_CACHE.get(cache_key)
                        if cached is not None:
                            it.setIcon(0, QIcon(self._rim_for(data, cached)))
            except RuntimeError:
                return
            for i in range(it.childCount()):
                visit(it.child(i))
        for i in range(self.topLevelItemCount()):
            visit(self.topLevelItem(i))

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
        # Defer all selection logic to Qt's native ExtendedSelection —
        # it implements Nemo-style deferred selection out of the box
        # (press on a selected row in a multi-selection doesn't collapse
        # until release, unless a drag fires first). The previous custom
        # implementation consumed the press and re-did this logic, which
        # left plain clicks collapsing the selection so the user could
        # not drag a multi-selection. Now we only track press position
        # for drag detection.
        if obj is self.viewport():
            t = event.type()
            if t == event.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                pos = event.position().toPoint()
                self._press_pos = pos
                self._press_item = self.itemAt(pos)
                # never consume — Qt's native handler runs
            elif t == event.Type.MouseMove:
                if (event.buttons() & Qt.MouseButton.LeftButton
                        and self._press_pos is not None
                        and self._press_item is not None):
                    cur = event.position().toPoint()
                    if (cur - self._press_pos).manhattanLength() > self._DRAG_THRESHOLD:
                        self._press_pos = None
                        item = self._press_item
                        self._press_item = None
                        self._start_url_drag(seed_item=item)
                        return True
                    # Below threshold and press was on a row: suppress
                    # MouseMove unconditionally so Qt's ExtendedSelection
                    # doesn't rubberband-extend the selection while we
                    # wait for the drag threshold. Without this even a
                    # single-row press + tiny drift reads as "Shift+drag
                    # range-extend".
                    return True
            elif t == event.Type.MouseButtonRelease:
                self._press_pos = None
                self._press_item = None
        return super().eventFilter(obj, event)

    def _start_url_drag(self, seed_item=None):
        # Always include the press item, even if Qt's late selection
        # update dropped it out of selectedItems(). Union them so a
        # multi-selection drag never silently loses one row.
        items = list(self.selectedItems())
        seed_item = seed_item or self._press_item
        if seed_item is not None and seed_item not in items:
            items.insert(0, seed_item)
        if not items:
            return
        paths = []
        seen = set()   # dedupe — the same path could appear via the
                       # press item AND a duplicate selection entry
        for it in items:
            data = it.data(0, Qt.ItemDataRole.UserRole)
            if not data or data == ".." or data == self._PLACEHOLDER:
                continue
            if not os.path.exists(data):
                continue
            if data in seen:
                continue
            seen.add(data)
            paths.append(data)
        # Drop descendants of selected folders so an expanded folder
        # plus its visible children moves as one item, not as folder +
        # loose files (which would extract the children at the target).
        paths = _prune_descendants(paths)
        urls = [QUrl.fromLocalFile(p) for p in paths]
        if not urls:
            return
        mime = QMimeData()
        mime.setUrls(urls)
        drag = QDrag(self)
        drag.setMimeData(mime)
        # Default action mirrors what the user gestured at drag-start:
        # Ctrl held → Copy (so Qt shows the +copy cursor immediately),
        # otherwise Move. Qt still flips on the fly if the user toggles
        # Ctrl mid-drag. Include LinkAction so external apps that import
        # files by reference (Kdenlive, OBS, image editors) can pick it.
        from PyQt6.QtWidgets import QApplication as _QApp
        default_action = (
            Qt.DropAction.CopyAction
            if _QApp.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier
            else Qt.DropAction.MoveAction)
        drag.exec(Qt.DropAction.MoveAction | Qt.DropAction.CopyAction
                  | Qt.DropAction.LinkAction,
                  default_action)

    # ── Drop handling ────────────────────────────────────────────────────────
    _EDGE_BAND = 28      # px from top/bottom that triggers auto-scroll
    _EDGE_MAX_SPEED = 12 # max px per 40 ms tick

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dragMoveEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()
            # Update auto-scroll based on cursor distance from viewport
            # edges. Speed grows linearly as the cursor enters the band.
            vp_h = self.viewport().height()
            y = ev.position().toPoint().y()
            band = self._EDGE_BAND
            if y < band:
                # Top edge — scroll up; closer to edge → faster.
                frac = max(0.0, min(1.0, (band - y) / band))
                self._drag_scroll_speed = -int(self._EDGE_MAX_SPEED * frac) or -1
            elif y > vp_h - band:
                frac = max(0.0, min(1.0, (y - (vp_h - band)) / band))
                self._drag_scroll_speed = int(self._EDGE_MAX_SPEED * frac) or 1
            else:
                self._drag_scroll_speed = 0
            if self._drag_scroll_speed and not self._drag_scroll_timer.isActive():
                self._drag_scroll_timer.start()
            elif not self._drag_scroll_speed and self._drag_scroll_timer.isActive():
                self._drag_scroll_timer.stop()
        else:
            ev.ignore()

    def dragLeaveEvent(self, ev):
        self._drag_scroll_timer.stop()
        self._drag_scroll_speed = 0
        super().dragLeaveEvent(ev)

    def _drag_scroll_tick(self):
        if not self._drag_scroll_speed:
            return
        sb = self.verticalScrollBar()
        sb.setValue(sb.value() + self._drag_scroll_speed)

    def dropEvent(self, ev):
        self._drag_scroll_timer.stop()
        self._drag_scroll_speed = 0
        if not ev.mimeData().hasUrls():
            ev.ignore(); return
        target = None
        pos = ev.position().toPoint()
        item = self.itemAt(pos)
        if item is not None:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data == "..":
                target = os.path.dirname(self._fm._cur_dir)
            elif data == self._PLACEHOLDER:
                # Lazy-expand placeholder child — use the parent folder
                _p = item.parent()
                if _p is not None:
                    pdata = _p.data(0, Qt.ItemDataRole.UserRole)
                    if pdata and os.path.isdir(pdata):
                        target = pdata
            elif data and os.path.isdir(data):
                target = data
            elif data and os.path.isfile(data):
                # Dropped on a file row → its parent folder is the
                # target. Without this branch the drop fell through to
                # _cur_dir (one level up) when the user aimed at the
                # row of a file inside an expanded folder.
                target = os.path.dirname(data)
        if not target:
            target = self._fm._cur_dir
        _handle_fm_url_drop(ev, target, self._fm.move_files_into, self)

    def _on_double_click(self, item, col):
        target = item.data(0, Qt.ItemDataRole.UserRole)
        if target == "..":
            self._fm._go_up()
        elif target and os.path.isdir(target):
            self._fm.navigate(target)
        elif target and os.path.isfile(target):
            ext = os.path.splitext(target)[1].lower()
            if ext in logic.EXT_VID:
                # Videos open in the system default player (mpv / vlc /
                # celluloid / whatever's wired to the .mp4 mime type).
                # The in-app preview can't seek/scrub like a real player.
                try:
                    import subprocess
                    subprocess.Popen(["xdg-open", target])
                except Exception:
                    pass
            else:
                ph = getattr(self._fm.app, "preview_handler", None)
                if ph:
                    try: ph.show(target)
                    except Exception: pass

    def _on_item_changed(self, item, col):
        # Fired both for our own setText calls and for user inline edits.
        # _editing_lock skips the recursive case (revert on failure).
        if self._editing_lock or col != 0:
            return
        full = item.data(0, Qt.ItemDataRole.UserRole)
        if not full or full == ".." or full == self._PLACEHOLDER:
            return
        new_name = item.text(0).strip()
        old_name = os.path.basename(full)
        if not new_name or new_name == old_name:
            return
        self._fm._handle_inline_rename(item, full, new_name)


class _FMCarouselView(QWidget):
    """3D-ring carousel of subfolders for one FilePane. Wheel rotates,
    click selects, double-click navigates into that folder. Companion to
    _FMTreeList — same data source (current dir), different visualization.
    Ported from /mnt/1TBSSD/CarouselUI."""

    _REP_NAMES = ("0.jpg", "0.jpeg", "0.png", "0.webp", "0.bmp")

    selection_changed = pyqtSignal(int)  # emits selected_index

    def __init__(self, pane):
        super().__init__()
        self.pane = pane
        self.items = []
        self.selected_index = 0
        self.display_position = 0.0
        self.target_position = 0.0
        self._pix_cache = {}
        self.hit_map = []
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self._press_pos = None
        self._press_folder = None
        # Tight vertical sizing — height matches the painted content,
        # so there's no dead space below the folder label. Horizontal
        # is Expanding so the ring fills available width.
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate)
        self._timer.start(16)

    # ── Geometry helpers (sizeHint + paintEvent share these so the
    # widget is exactly as tall as what's drawn) ─────────────────────────
    @staticmethod
    def _radius_for(width):
        return int(min(max(120, width * 0.30), 380))

    @classmethod
    def _layout_for(cls, width):
        radius = cls._radius_for(width)
        face_size = int(max(80, min(150, radius * 0.36)))
        sel_size = int(face_size * 1.32)
        # Place the selected face top ~4 px from the top of the widget;
        # the back-half of the ring will spill above (clipped by parent),
        # which matches the "ring close to top, top may be hidden" spec.
        cy = max(0, int(sel_size / 2 - radius * 0.15)) + 4
        label_top = cy + int(radius * 0.15) + sel_size // 2 + 4
        label_h = 24
        return radius, face_size, sel_size, cy, label_top, label_h

    def sizeHint(self):
        w = max(self.width(), 600)
        _, _, _, _, label_top, label_h = self._layout_for(w)
        return QSize(w, label_top + label_h + 2)

    def minimumSizeHint(self):
        _, _, _, _, label_top, label_h = self._layout_for(400)
        return QSize(400, label_top + label_h + 2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Width changed → recompute height via sizeHint.
        self.updateGeometry()

    def populate(self, dir_path):
        self.items = []
        self._pix_cache = {}
        if dir_path and os.path.isdir(dir_path):
            try:
                entries = sorted(os.listdir(dir_path))
            except OSError:
                entries = []
            for name in entries:
                if name.startswith("."):
                    continue
                full = os.path.join(dir_path, name)
                if os.path.isdir(full):
                    self.items.append((full, self._rep_image_for(full)))
        if self.selected_index >= len(self.items):
            self.selected_index = 0
        self.display_position = float(self.selected_index)
        self.target_position = float(self.selected_index)
        self.update()
        self.selection_changed.emit(self.selected_index)

    @classmethod
    def _rep_image_for(cls, folder):
        try:
            files = [n for n in os.listdir(folder)
                     if os.path.isfile(os.path.join(folder, n))
                     and os.path.splitext(n)[1].lower() in _VALID_EXTS]
        except OSError:
            return None
        if not files:
            return None
        by_name = {n.lower(): n for n in files}
        for cand in cls._REP_NAMES:
            if cand in by_name:
                return os.path.join(folder, by_name[cand])
        return os.path.join(folder, sorted(files)[0])

    def _pixmap_for(self, path, size):
        if not path:
            return None
        key = (path, int(size))
        if key in self._pix_cache:
            return self._pix_cache[key]
        px = _make_thumb_pixmap(path, int(size))
        if px is None:
            return None
        if px.width() != size or px.height() != size:
            x = max(0, (px.width() - int(size)) // 2)
            y = max(0, (px.height() - int(size)) // 2)
            px = px.copy(x, y, int(size), int(size))
        self._pix_cache[key] = px
        return px

    def _animate(self):
        if not self.items:
            return
        diff = self.target_position - self.display_position
        if abs(diff) > 0.001:
            self.display_position += diff * 0.18
            if abs(self.target_position - self.display_position) < 0.01:
                self.display_position = self.target_position
            self.update()
        else:
            self.display_position %= max(1, len(self.items))
            self.target_position %= max(1, len(self.items))

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.fillRect(self.rect(), QColor("#151515"))
        if not self.items:
            p.setPen(QColor("#bbbbbb"))
            p.setFont(QFont("Arial", 14))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "No subfolders here")
            return
        self.hit_map = []
        w = self.width()
        cx = w / 2
        radius, face_size, sel_size, cy, label_top, label_h = self._layout_for(w)
        self._draw_ring(p, cx, cy, radius)
        self._draw_faces(p, cx, cy, radius, face_size, sel_size)
        folder = self.items[self.selected_index][0]
        p.setPen(QColor("#f0c64d"))
        p.setFont(QFont("Arial", 13, QFont.Weight.Bold))
        p.drawText(QRectF(0, label_top, w, label_h),
                   Qt.AlignmentFlag.AlignCenter,
                   os.path.basename(folder))

    def _draw_ring(self, p, cx, cy, radius):
        xr = radius
        yr = int(radius * 0.16)
        thick = int(radius * 0.07)
        top = cy - yr
        bot = cy + yr
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor("#242424"), 5))
        p.drawArc(QRectF(cx - xr, top - thick, xr * 2, bot - top), 0, 180 * 16)
        p.setPen(QPen(QColor("#858585"), 10))
        p.drawArc(QRectF(cx - xr, top, xr * 2, bot - top), 180 * 16, 180 * 16)
        p.setPen(QPen(QColor("#373737"), 3))
        p.drawArc(QRectF(cx - xr * 0.86, top + thick * 0.8, xr * 1.72, bot - top),
                  180 * 16, 180 * 16)

    def _draw_faces(self, p, cx, cy, radius, face_size, sel_size):
        count = len(self.items)
        max_each = min(4, count - 1) if count > 1 else 0
        step = math.radians(24 if count > 5 else 34)
        xr = radius * 0.82
        yr = radius * 0.15
        center_slot = math.floor(self.display_position)
        positions = []
        for slot in range(center_slot - max_each, center_slot + max_each + 2):
            index = slot % count
            folder, rep = self.items[index]
            offset = slot - self.display_position
            if abs(offset) > max_each + 0.75:
                continue
            angle = math.pi / 2 - offset * step
            x = cx + math.cos(angle) * xr
            y = cy + math.sin(angle) * yr
            distance = abs(offset)
            selected = distance < 0.08
            size = int(sel_size - min(1.0, distance) * (sel_size - face_size * 0.9))
            size = int(max(face_size * 0.52, size - max(0, distance - 1) * face_size * 0.08))
            brightness = max(0.4, 1.0 - distance * 0.12)
            positions.append((selected, distance, index, folder, rep, x, y, size, brightness))

        for selected, distance, index, folder, rep, x, y, size, brightness in sorted(
                positions, key=lambda row: (row[0], -row[1])):
            rect = QRectF(x - size / 2, y - size / 2, size, size)
            px = self._pixmap_for(rep, size) if rep else None
            if px is not None:
                p.drawPixmap(rect.toRect(), px)
                if brightness < 0.98:
                    p.fillRect(rect, QColor(0, 0, 0, int((1.0 - brightness) * 150)))
            else:
                p.setBrush(QColor("#252525"))
                p.setPen(QPen(QColor("#777777"), 2))
                p.drawRect(rect)
                p.setPen(QColor("#aaaaaa"))
                p.setFont(QFont("Arial", 10))
                name = os.path.basename(folder)
                if len(name) > 10:
                    name = name[:9] + "…"
                p.drawText(rect, Qt.AlignmentFlag.AlignCenter, name)
            border = QColor("#f0c64d") if selected else QColor("#777777")
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(border, 4 if selected else 2))
            p.drawRect(rect.adjusted(-4, -4, 4, 4))
            self.hit_map.append((rect.adjusted(-8, -8, 8, 8), index, folder))

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta:
            self._rotate_by(-1 if delta > 0 else 1)
        event.accept()

    def mousePressEvent(self, event):
        self._press_pos = None
        self._press_folder = None
        for rect, index, _folder in reversed(self.hit_map):
            if rect.contains(event.position()):
                self._select_index(index)
                if event.button() == Qt.MouseButton.LeftButton:
                    self._press_pos = event.position().toPoint()
                    self._press_folder = _folder
                return

    def mouseMoveEvent(self, event):
        if (event.buttons() & Qt.MouseButton.LeftButton
                and self._press_pos is not None
                and self._press_folder is not None):
            cur = event.position().toPoint()
            if (cur - self._press_pos).manhattanLength() > _FMTreeList._DRAG_THRESHOLD:
                folder = self._press_folder
                self._press_pos = None
                self._press_folder = None
                self._start_url_drag(folder)
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._press_pos = None
        self._press_folder = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        for rect, _index, folder in reversed(self.hit_map):
            if rect.contains(event.position()):
                self.pane.navigate(folder)
                return

    def _start_url_drag(self, folder):
        if not folder or not os.path.isdir(folder):
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(folder)])
        drag = QDrag(self)
        drag.setMimeData(mime)
        rep = self._rep_image_for(folder)
        px = self._pixmap_for(rep, 96) if rep else None
        if px is not None:
            drag.setPixmap(px)
            drag.setHotSpot(QPoint(px.width() // 2, px.height() // 2))
        from PyQt6.QtWidgets import QApplication as _QApp
        default_action = (
            Qt.DropAction.CopyAction
            if _QApp.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier
            else Qt.DropAction.MoveAction)
        drag.exec(Qt.DropAction.MoveAction | Qt.DropAction.CopyAction
                  | Qt.DropAction.LinkAction,
                  default_action)

    def _folder_at_pos(self, pos):
        for rect, _index, folder in reversed(self.hit_map):
            if rect.contains(pos):
                return folder
        if self.items:
            return self.items[self.selected_index][0]
        return self.pane._cur_dir

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
        target = self._folder_at_pos(ev.position())
        _handle_fm_url_drop(ev, target, self.pane.fm.move_files_into, self)

    def _rotate_by(self, direction):
        if not self.items:
            return
        self._select_index(self.selected_index + direction, direction=direction)

    def _select_index(self, index, direction=None):
        if not self.items:
            return
        count = len(self.items)
        index = index % count
        if direction:
            self.target_position += 1 if direction > 0 else -1
        else:
            current = self.target_position
            diff = (index - current + count / 2) % count - count / 2
            self.target_position = current + diff
        prev = self.selected_index
        self.selected_index = index
        if prev != index:
            self.selection_changed.emit(index)


class _FMCarouselPanel(QWidget):
    """Carousel mode container: wheel on top, nemo-style tree below
    showing the carousel-selected folder's contents. Double-clicking a
    folder in either view navigates the whole pane to that folder."""

    def __init__(self, pane):
        super().__init__()
        self.pane = pane
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)

        self.ring = _FMCarouselView(pane)
        # Ring widget sizes itself to its painted content via sizeHint;
        # no explicit min/max height needed here.
        lay.addWidget(self.ring)

        # Second _FMTreeList showing the contents of whichever folder
        # the ring currently has selected. Shares the same FilePane so
        # navigation, context menus, drag/drop, etc. all behave the
        # same as the primary tree.
        self.contents = _FMTreeList(pane)
        self.contents.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.contents.customContextMenuRequested.connect(pane._on_context_menu)
        # Single-click / arrow-key → live preview, same as the primary tree.
        self.contents.currentItemChanged.connect(pane._on_current_changed)
        lay.addWidget(self.contents, 1)

        self.ring.selection_changed.connect(self._on_ring_changed)

    def populate(self, dir_path):
        self.ring.populate(dir_path)
        if not self.ring.items:
            self.contents.populate_root(dir_path)

    def _on_ring_changed(self, _index):
        if not self.ring.items:
            return
        folder, _rep = self.ring.items[self.ring.selected_index]
        self.contents.populate_root(folder)


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
        self.btn_back = QPushButton("◀ Back")
        self.btn_back.setToolTip("Previous directory in history")
        self.btn_fwd  = QPushButton("Fwd ▶")
        self.btn_fwd.setToolTip("Next directory in history")
        self.btn_up   = QPushButton("▲ Up")
        self.btn_up.setToolTip("Parent directory")
        _btn_ss = (
            "QPushButton { padding: 4px 10px; font-weight: bold; "
            "min-height: 22px; }")
        for b in (self.btn_back, self.btn_fwd, self.btn_up):
            b.setMinimumWidth(70)
            b.setStyleSheet(_btn_ss)
        self.btn_back.clicked.connect(self._go_back)
        self.btn_fwd.clicked.connect(self._go_forward)
        self.btn_up.clicked.connect(self._go_up)
        tb.addWidget(self.btn_back)
        tb.addWidget(self.btn_fwd)
        tb.addWidget(self.btn_up)
        self.path_edit = QLineEdit()
        self.path_edit.returnPressed.connect(self._on_path_edit_enter)
        tb.addWidget(self.path_edit, 1)
        self.btn_view_mode = QPushButton("◎ Carousel")
        self.btn_view_mode.setToolTip("Switch between tree and carousel view")
        self.btn_view_mode.setStyleSheet(_btn_ss)
        self.btn_view_mode.clicked.connect(self._toggle_view_mode)
        tb.addWidget(self.btn_view_mode)
        v.addLayout(tb)

        # Filename filter — case-insensitive substring on each item's
        # displayed name. ".." stays visible. Reapplied after every
        # _refresh() so navigating preserves the filter.
        self._fn_filter_text = ""
        _fn_row = QHBoxLayout()
        _fn_row.setContentsMargins(0, 0, 0, 0)
        _fn_row.setSpacing(4)
        _fn_lbl = QLabel("🔍")
        _fn_lbl.setToolTip("Filter by filename")
        self.fn_filter_input = QLineEdit()
        self.fn_filter_input.setPlaceholderText(
            "filter by filename… (Esc to clear)")
        self.fn_filter_input.setClearButtonEnabled(True)
        self.fn_filter_input.textChanged.connect(self._on_fn_filter_changed)
        from PyQt6.QtGui import QShortcut as _QSc, QKeySequence as _QKs
        _esc_sc = _QSc(_QKs("Escape"), self.fn_filter_input)
        _esc_sc.setContext(Qt.ShortcutContext.WidgetShortcut)
        _esc_sc.activated.connect(lambda: (self.fn_filter_input.clear(),
                                           self.tree.setFocus()))
        _fn_row.addWidget(_fn_lbl)
        _fn_row.addWidget(self.fn_filter_input, 1)
        v.addLayout(_fn_row)

        self.tree = _FMTreeList(self)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        # Single-click / arrow-key selection → live preview, same as
        # the main page's behavior.
        self.tree.currentItemChanged.connect(self._on_current_changed)

        # Carousel view — wheel on top + tree showing the selected
        # folder's contents below. Each pane keeps its own toggle state
        # so dual-pane can be (tree, carousel) or any other mix.
        self.carousel = _FMCarouselPanel(self)

        self.view_stack = QStackedWidget()
        self.view_stack.addWidget(self.tree)      # index 0
        self.view_stack.addWidget(self.carousel)  # index 1
        v.addWidget(self.view_stack, 1)

        # Status line: live count of selected items + total size
        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet("color:#888; font-size:9pt; padding:1px 4px;")
        v.addWidget(self.status_lbl)
        self.tree.itemSelectionChanged.connect(self._update_status)
        self._update_status()

        if initial_dir and os.path.isdir(initial_dir):
            self.navigate(initial_dir)

    # Surface that the tree expects ─────────────────────────────────────────
    @property
    def app(self):
        return self.fm.app

    def _folder_icon(self):
        return self.fm._folder_icon()

    def move_files_into(self, src_paths, target_dir, mode="move"):
        # Delegate the heavy lifting to FM (touches app data + disk
        # stores), then refresh both panes if dual-pane is active.
        self.fm.move_files_into(src_paths, target_dir, mode=mode)

    # ── Navigation ──────────────────────────────────────────────────────────
    def navigate(self, path):
        path = os.path.abspath(path)
        if not os.path.isdir(path):
            return
        self._history = self._history[:self._history_idx + 1]
        self._history.append(path)
        self._history_idx = len(self._history) - 1
        self._refresh()

    def navigate_and_select(self, file_path):
        """Navigate to the file's parent folder and highlight the file
        in the tree. If the file's parent is already current, just
        re-select. Skips the navigate (and history push) when no folder
        change is needed so the user doesn't get a redundant Back step.
        """
        file_path = os.path.abspath(file_path)
        parent = os.path.dirname(file_path)
        if not os.path.isdir(parent):
            return
        if os.path.normpath(parent) != os.path.normpath(self._cur_dir or ""):
            self.navigate(parent)
        # Find the row whose UserRole matches the file path.
        target_norm = os.path.normpath(file_path)
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            d = it.data(0, Qt.ItemDataRole.UserRole)
            if d and os.path.normpath(d) == target_norm:
                self.tree.setCurrentItem(it)
                self.tree.scrollToItem(it)
                self.tree.setFocus()
                return

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
        self.carousel.populate(self._cur_dir)
        self._update_nav_buttons()
        # Preserve the filename filter across navigation
        self._apply_fn_filter()

    def _toggle_view_mode(self):
        if self.view_stack.currentIndex() == 0:
            self.view_stack.setCurrentIndex(1)
            self.btn_view_mode.setText("▤ Tree")
        else:
            self.view_stack.setCurrentIndex(0)
            self.btn_view_mode.setText("◎ Carousel")

    def _on_fn_filter_changed(self, text):
        self._fn_filter_text = text or ""
        self._apply_fn_filter()

    def _apply_fn_filter(self):
        """Hide tree items whose displayed name doesn't contain the
        filter substring. ".." is always visible. Only top-level items
        are filtered — once the user expands a folder, its children
        show unfiltered (consistent with how the rest of the FM
        navigation works)."""
        needle = (self._fn_filter_text or "").strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if not it:
                continue
            if it.data(0, Qt.ItemDataRole.UserRole) == "..":
                it.setHidden(False)
                continue
            if not needle:
                it.setHidden(False)
            else:
                it.setHidden(needle not in it.text(0).lower())

    def _update_status(self):
        """Live status line — selection count + total size of selected files."""
        paths = self._selected_paths()
        if not paths:
            self.status_lbl.setText("")
            return
        n = len(paths)
        total = 0
        files = 0
        for p in paths:
            try:
                if os.path.isfile(p):
                    total += os.path.getsize(p)
                    files += 1
            except OSError:
                pass
        try:
            size_text = logic.get_sz_readable_from_bytes(total)
        except Exception:
            size_text = self._fmt_bytes(total)
        if files == n:
            self.status_lbl.setText(f"{n} selected — {size_text}")
        else:
            # Mix of files + folders
            self.status_lbl.setText(
                f"{n} selected ({files} file{'s' if files != 1 else ''} — {size_text})")

    @staticmethod
    def _fmt_bytes(n):
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024:
                return f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} PB"

    def _on_current_changed(self, current, previous):
        """Single-click / arrow-key selection → live preview, mirroring
        the main-page table's itemSelectionChanged → handle_preview hookup."""
        if current is None:
            return
        target = current.data(0, Qt.ItemDataRole.UserRole)
        if (not target or target == ".."
                or target == _FMTreeList._PLACEHOLDER
                or not os.path.isfile(target)):
            return
        ph = getattr(self.app, "preview_handler", None)
        if ph is not None:
            try:
                ph.show(target)
            except Exception:
                pass

    def _update_nav_buttons(self):
        self.btn_back.setEnabled(self._history_idx > 0)
        self.btn_fwd.setEnabled(self._history_idx < len(self._history) - 1)
        self.btn_up.setEnabled(
            bool(self._cur_dir)
            and os.path.dirname(self._cur_dir) != self._cur_dir)

    # ── Selected-paths helpers (used by context menu / shortcuts) ───────────
    def _active_tree(self):
        """Tree currently visible to the user. In carousel mode the
        user interacts with the carousel's contents tree, not the
        (hidden) primary tree — without this routing, Delete / Rename
        / context-menu file ops all read an empty selection."""
        if (hasattr(self, "view_stack")
                and self.view_stack.currentIndex() == 1
                and hasattr(self, "carousel")):
            return self.carousel.contents
        return self.tree

    def _path_at_pos(self, pos):
        it = self._active_tree().itemAt(pos)
        return it.data(0, Qt.ItemDataRole.UserRole) if it else None

    def _current_path(self):
        it = self._active_tree().currentItem()
        return it.data(0, Qt.ItemDataRole.UserRole) if it else None

    def _selected_paths(self):
        out = []
        for it in self._active_tree().selectedItems():
            d = it.data(0, Qt.ItemDataRole.UserRole)
            if d and d != ".." and d != _FMTreeList._PLACEHOLDER:
                out.append(d)
        return out

    # ── Context menu / file ops (delegate to FM for app sync) ───────────────
    def _other_pane(self):
        """Return the OTHER FilePane when the FM is in dual-pane mode,
        else None."""
        panes = self.fm._panes
        if len(panes) != 2:
            return None
        return panes[1] if panes[0] is self else panes[0]

    def _on_context_menu(self, pos):
        path = self._path_at_pos(pos)
        menu = QMenu(self)
        act_new = QAction("New Folder", self)
        act_new.triggered.connect(self._new_folder)
        menu.addAction(act_new)
        if path and path != "..":
            menu.addSeparator()
            # Dual-pane only: navigate the OTHER pane to this folder.
            other = self._other_pane()
            if other is not None and os.path.isdir(path):
                first_pane = self.fm._panes[0]
                label = ("Show in right pane"
                         if other is not first_pane
                         else "Show in left pane")
                act_other = QAction(label, self)
                act_other.triggered.connect(
                    lambda _, p=path, o=other: o.navigate(p))
                menu.addAction(act_other)
                menu.addSeparator()
            # Open / Open with — only meaningful for files
            if os.path.isfile(path):
                # Lock / Unlock toggle
                import aisearch_attrs as _am
                _attrs = getattr(self.app, "attrs_data", {}) or {}
                _is_locked = not _am.is_editable(_attrs, path)
                act_lock = QAction(
                    "🔓 Unlock" if _is_locked else "🔒 Lock", self)
                act_lock.triggered.connect(
                    lambda _, p=path: self._toggle_lock(p))
                menu.addAction(act_lock)
                menu.addSeparator()
                act_open_default = QAction("Open", self)
                act_open_default.triggered.connect(
                    lambda _, p=path: self.fm._open_default(p))
                menu.addAction(act_open_default)
                open_with = menu.addMenu("Open with")
                for app_name, cmd in self.fm._app_choices(path):
                    a = QAction(app_name, self)
                    a.triggered.connect(
                        lambda _, c=cmd, p=path: self.fm._open_with(c, p))
                    open_with.addAction(a)
                menu.addSeparator()
            act_rename = QAction("Rename (F2)", self)
            act_rename.triggered.connect(self._rename_selected)
            menu.addAction(act_rename)
            act_delete = QAction("Move to Trash (Del)", self)
            act_delete.triggered.connect(self._delete_selected)
            menu.addAction(act_delete)
            act_open_loc = QAction("Open in Nemo", self)
            act_open_loc.triggered.connect(
                lambda _, p=path: self.fm._open_in_nemo(p))
            menu.addAction(act_open_loc)
        menu.exec(self._active_tree().viewport().mapToGlobal(pos))

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
        # Nemo-style inline rename: the name cell becomes an editable
        # QLineEdit. Commit on Enter, cancel on Esc. The rename runs
        # from _handle_inline_rename when the editor closes.
        tree = self._active_tree()
        item = tree.currentItem()
        if item is None:
            return
        full = item.data(0, Qt.ItemDataRole.UserRole)
        if not full or full == "..":
            return
        tree.editItem(item, 0)

    def _handle_inline_rename(self, item, old_path, new_name):
        """Called from _FMTreeList._on_item_changed once the editor
        closes with a non-empty, changed name. Performs the on-disk
        rename + in-memory sync, or reverts the name on failure."""
        old_name = os.path.basename(old_path)
        if "/" in new_name or "\\" in new_name:
            QMessageBox.warning(self, "Rename",
                "Name cannot contain '/' or '\\'.")
            self._revert_item_name(item, old_name)
            return
        new_path = os.path.join(os.path.dirname(old_path), new_name)
        if os.path.exists(new_path):
            QMessageBox.warning(self, "Rename", f"Already exists:\n{new_path}")
            self._revert_item_name(item, old_name)
            return
        try:
            os.rename(old_path, new_path)
        except Exception as e:
            QMessageBox.critical(self, "Rename", f"Could not rename:\n{e}")
            self._revert_item_name(item, old_name)
            return
        # Update the item's stored path (UserRole) so future renames
        # work from the new path. Also setText explicitly — Qt's inline
        # editor is supposed to commit the new text before itemChanged
        # fires, but in some environments/timing the cell still shows
        # the old name; setting it again is safe (editing_lock blocks
        # the recursive _on_item_changed) and guarantees the display
        # reflects what's actually on disk.
        # Lock the tree the item actually lives in — could be the primary
        # tree OR the carousel's contents tree.
        item_tree = item.treeWidget() or self.tree
        item_tree._editing_lock = True
        try:
            item.setData(0, Qt.ItemDataRole.UserRole, new_path)
            item.setText(0, new_name)
        finally:
            item_tree._editing_lock = False
        renames = {}
        if os.path.isfile(new_path):
            self.fm._sync_in_memory(old_path, new_path)
            renames[old_path] = new_path
            try:
                import aisearch_attrs as _am
                _am.flush_path_renames_to_stores(
                    renames, getattr(self.app, "current_project", None))
            except Exception:
                pass
            # Auto-lock on rename — same gesture as the preview
            # 🪪 Rename and the main-app rename. Sets editable=False
            # so the scanner / Apply Rules skip this file going forward.
            try:
                attrs_data = getattr(self.app, "attrs_data", None)
                if isinstance(attrs_data, dict):
                    attrs_data.setdefault(new_path, {})["editable"] = False
            except Exception:
                pass
        elif os.path.isdir(new_path):
            renames = self.fm._sync_folder_rename(old_path, new_path)
        # Persist attrs.json + features.pt — without this the in-memory
        # path swap was lost on next launch and the file looked like
        # it had no attributes (entry stuck under the old path key).
        self.fm._save_app_data()
        # Mirror the rename in the main window's table so any visible
        # row pointing at the old path flips to the new one.
        self.fm._update_main_table_paths(renames)
        # Other panes may be viewing the same dir — refresh them.
        # Our own pane already shows the new name (inline edit committed
        # the text), so don't rebuild it.
        for p in self.fm._panes:
            if p is not self:
                p._refresh()

    def _revert_item_name(self, item, old_name):
        item_tree = item.treeWidget() or self.tree
        item_tree._editing_lock = True
        try:
            item.setText(0, old_name)
        finally:
            item_tree._editing_lock = False

    def _toggle_lock(self, path):
        """Right-click → Lock / Unlock toggle. Delegates to the main
        app's _toggle_file_lock so attrs.json + main-table state stay
        in sync; the main app already calls fm._fm_win.refresh_all()
        which redraws our pane's rim."""
        if hasattr(self.app, "_toggle_file_lock"):
            self.app._toggle_file_lock(path)

    def _delete_selected(self):
        paths = self._selected_paths()
        if not paths:
            return
        if len(paths) == 1:
            msg = f"Move to trash:\n{paths[0]}"
        else:
            msg = f"Move {len(paths)} item(s) to trash?"
        # Honor the global "Ask confirmation before moving to Trash"
        # setting (Settings → Settings → Trash Options). Adds a
        # "Don't show again" checkbox so the user can turn it off
        # right from the dialog; toggling back on is via Settings.
        if self.app.config.get("delete_confirm", True):
            from PyQt6.QtWidgets import QCheckBox
            box = QMessageBox(self)
            box.setWindowTitle("Trash")
            box.setText(msg)
            box.setStandardButtons(QMessageBox.StandardButton.Yes
                                   | QMessageBox.StandardButton.No)
            box.setDefaultButton(QMessageBox.StandardButton.Yes)
            cb = QCheckBox("Don't show again (toggle in Settings → Trash)")
            box.setCheckBox(cb)
            ans = box.exec()
            if ans != QMessageBox.StandardButton.Yes:
                return
            if cb.isChecked():
                self.app.config["delete_confirm"] = False
                try:
                    import aisearch_config as _cfg
                    _cfg.save_config(self.app.config,
                                     getattr(self.app, "current_project", None))
                except Exception:
                    pass
        # Pick a sibling to focus AFTER delete — prefer the row directly
        # below the bottom-most selected row, falling back to the row
        # above the top-most. Captured before any deletion so it can't
        # be wiped by a refresh. Keeps the FM focused so the next file
        # shows in preview without a click.
        tree = self._active_tree()
        sel_set = set(paths)
        sel_items = [it for it in tree.selectedItems()
                     if it.data(0, Qt.ItemDataRole.UserRole) in sel_set]
        next_focus_item = None
        if sel_items:
            def _row(it):
                par = it.parent() or tree.invisibleRootItem()
                return par.indexOfChild(it)
            last  = max(sel_items, key=_row)
            first = min(sel_items, key=_row)
            par = last.parent() or tree.invisibleRootItem()
            for i in range(par.indexOfChild(last) + 1, par.childCount()):
                sib = par.child(i)
                sp  = sib.data(0, Qt.ItemDataRole.UserRole)
                if (sp and sp != ".."
                        and sp != _FMTreeList._PLACEHOLDER
                        and sp not in sel_set):
                    next_focus_item = sib
                    break
            if next_focus_item is None:
                par2 = first.parent() or tree.invisibleRootItem()
                for i in range(par2.indexOfChild(first) - 1, -1, -1):
                    sib = par2.child(i)
                    sp  = sib.data(0, Qt.ItemDataRole.UserRole)
                    if (sp and sp != ".."
                            and sp != _FMTreeList._PLACEHOLDER
                            and sp not in sel_set):
                        next_focus_item = sib
                        break

        import aisearch_front_page as _fp
        errors = []
        any_removed = False
        trashed = set()
        for p in paths:
            if not os.path.exists(p):
                continue
            try:
                _tp, err = _fp.trash_file(p)
                if err:
                    errors.append(f"{os.path.basename(p)}: {err}")
                    continue
                self.fm._remove_from_app_state(p)
                trashed.add(p)
                any_removed = True
            except Exception as e:
                errors.append(f"{os.path.basename(p)}: {e}")
        # Persist the in-memory mutations (attrs_data + features.pt)
        # once at the end so the deletions survive a relaunch.
        if any_removed:
            self.fm._save_app_data()
        if errors:
            QMessageBox.warning(self, "Trash",
                f"Errors:\n" + "\n".join(errors[:10]))
        # Surgical tree update — remove only the rows we trashed,
        # leaving the rest of the tree (and its scroll position) alone.
        # A full refresh would rebuild the tree, dropping focus and
        # whatever expansion state the user had.
        for it in list(sel_items):
            sp = it.data(0, Qt.ItemDataRole.UserRole)
            if sp in trashed:
                par = it.parent() or tree.invisibleRootItem()
                par.removeChild(it)
        # The OTHER pane(s) may have been viewing the same dir, so
        # refresh those — but not us.
        for p in self.fm._panes:
            if p is not self:
                p._refresh()
        # Park focus on the chosen next sibling. setCurrentItem fires
        # currentItemChanged, which auto-shows the next file in preview.
        if next_focus_item is not None:
            try:
                tree.setCurrentItem(next_focus_item)
                tree.scrollToItem(next_focus_item)
            except RuntimeError:
                pass
        tree.setFocus()


def _prune_descendants(paths):
    """Drop any path that lives under another path in the same selection.
    A folder move carries its children implicitly — including those
    children separately would lift them out of the folder at the target.
    Used by both drag-start sites (icon list + tree list)."""
    norm_pairs = [(os.path.normpath(os.path.abspath(p)), p) for p in paths]
    norm_set   = {n for n, _ in norm_pairs}
    keep = []
    for n, orig in norm_pairs:
        # walk parents — if any parent is also in the selection, skip
        parent = os.path.dirname(n)
        skipped = False
        while parent and parent != os.path.dirname(parent):
            if parent in norm_set:
                skipped = True
                break
            parent = os.path.dirname(parent)
        if not skipped:
            keep.append(orig)
    return keep


def _suggest_unique_name(target_dir, name):
    """Nemo-style auto-rename suggestion: ``name (1).ext`` then ``(2)``…"""
    stem, ext = os.path.splitext(name)
    i = 1
    while True:
        candidate = f"{stem} ({i}){ext}"
        if not os.path.exists(os.path.join(target_dir, candidate)):
            return candidate
        i += 1


def _copy_path_for_duplicate(src, dst):
    """Copy a file or folder for FM Ctrl-drop.

    Special case: copying a folder into itself creates ``src/basename``.
    Plain ``shutil.copytree`` creates that destination and can then see it
    while walking the source, so exclude the destination basename at the
    source root.
    """
    if not os.path.isdir(src):
        shutil.copy2(src, dst)
        return
    src_abs = os.path.abspath(src)
    dst_abs = os.path.abspath(dst)
    ignore = None
    if os.path.dirname(dst_abs) == src_abs:
        dst_name = os.path.basename(dst_abs)

        def ignore(_dir, names):
            if os.path.abspath(_dir) == src_abs and dst_name in names:
                return {dst_name}
            return set()

    shutil.copytree(src, dst, ignore=ignore)


def _fmt_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def _fmt_mtime(path):
    try:
        return time.strftime("%Y-%m-%d %H:%M",
                             time.localtime(os.path.getmtime(path)))
    except Exception:
        return "?"


class _ConflictDialog(QDialog):
    """Nemo-style file-conflict prompt: Skip / Replace / Rename, with
    optional 'Apply to all' that the caller honors for remaining items."""

    SKIP, REPLACE, RENAME = "skip", "replace", "rename"

    def __init__(self, parent, src, dst, remaining=0):
        super().__init__(parent)
        self.setWindowTitle("Replace?")
        self.choice = self.SKIP
        self.new_name = os.path.basename(dst)
        self.apply_to_all = False

        target_dir = os.path.dirname(dst)
        name = os.path.basename(dst)

        v = QVBoxLayout(self)
        v.addWidget(QLabel(
            f"A file with the same name already exists in\n\"{target_dir}\""))

        grid = QGridLayout()
        grid.addWidget(QLabel("<b>Existing file</b>"), 0, 1)
        grid.addWidget(QLabel("<b>New file</b>"),      0, 2)
        try:
            old_size = _fmt_size(os.path.getsize(dst))
        except Exception:
            old_size = "?"
        try:
            new_size = _fmt_size(os.path.getsize(src))
        except Exception:
            new_size = "?"
        grid.addWidget(QLabel("Size:"),     1, 0)
        grid.addWidget(QLabel(old_size),    1, 1)
        grid.addWidget(QLabel(new_size),    1, 2)
        grid.addWidget(QLabel("Modified:"), 2, 0)
        grid.addWidget(QLabel(_fmt_mtime(dst)), 2, 1)
        grid.addWidget(QLabel(_fmt_mtime(src)), 2, 2)
        v.addLayout(grid)

        v.addWidget(QLabel("New filename:"))
        self.name_edit = QLineEdit(_suggest_unique_name(target_dir, name))
        v.addWidget(self.name_edit)

        if remaining > 0:
            self.apply_cb = QCheckBox(
                f"Apply this action to the remaining {remaining} conflict(s)")
            v.addWidget(self.apply_cb)
        else:
            self.apply_cb = None

        btns = QHBoxLayout()
        btn_skip    = QPushButton("Skip")
        btn_replace = QPushButton("Replace")
        btn_rename  = QPushButton("Rename")
        btn_skip.clicked.connect(lambda: self._done(self.SKIP))
        btn_replace.clicked.connect(lambda: self._done(self.REPLACE))
        btn_rename.clicked.connect(lambda: self._done(self.RENAME))
        btns.addStretch(1)
        btns.addWidget(btn_skip)
        btns.addWidget(btn_replace)
        btns.addWidget(btn_rename)
        v.addLayout(btns)

    def _done(self, choice):
        self.choice = choice
        self.new_name = self.name_edit.text().strip() or self.new_name
        if self.apply_cb is not None:
            self.apply_to_all = self.apply_cb.isChecked()
        self.accept()


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
        self.btn_pane_toggle = QPushButton("Dual pane")
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
        # Ctrl+Z routes to the main app's undo stack — FM moves are
        # already pushed there by move_files_into. Without this binding
        # the user had to click back on the main window to undo.
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self._undo_active)

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
            self.btn_pane_toggle.setText("Single pane")
        else:
            # Tear down the second pane
            second = self._panes.pop()
            second.setParent(None)
            second.deleteLater()
            self.btn_pane_toggle.setText("Dual pane")

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

    def refresh_rims_only(self):
        """Lighter than refresh_all — walk every pane's tree and update
        rim icons in place. No tree clear, no expansion-state dance,
        so files in expanded subfolders pick up the new rim immediately
        instead of waiting for a lazy re-expand."""
        for p in self._panes:
            try:
                p.tree.refresh_all_rims()
            except Exception:
                pass

    def remove_paths(self, paths):
        """Surgically prune tree items whose stored path is in `paths`
        from every pane. Used when files are trashed by code outside the
        FM (main table delete, dup delete, shift-drag) so the FM tree
        doesn't keep showing ghost rows. Preserves scroll position and
        expansion state, unlike refresh_all()."""
        if not paths:
            return
        norm_set = {os.path.normpath(os.path.abspath(p)) for p in paths if p}
        if not norm_set:
            return
        def prune(it):
            for i in range(it.childCount() - 1, -1, -1):
                child = it.child(i)
                try:
                    data = child.data(0, Qt.ItemDataRole.UserRole)
                except RuntimeError:
                    continue
                if (data and data != ".."
                        and data != _FMTreeList._PLACEHOLDER):
                    try:
                        if os.path.normpath(os.path.abspath(data)) in norm_set:
                            it.removeChild(child)
                            continue
                    except Exception:
                        pass
                prune(child)
        for p in self._panes:
            try:
                root = p.tree.invisibleRootItem()
                prune(root)
            except Exception:
                pass

    # ── External entry point used by AISearchApp ────────────────────────────
    def navigate(self, path):
        """Called by main app's right-arrow handler. Navigate the active
        pane (or the first pane if focus is elsewhere)."""
        self._active_pane().navigate(path)

    def navigate_to_file(self, file_path):
        """Open the FM at the file's parent folder AND select the file
        in the tree. Used by the main app's right-click → File Manager
        so the user can see exactly which folder owns the file."""
        if not file_path:
            return
        self._active_pane().navigate_and_select(file_path)

    # ── Shortcut handlers — route to the active pane ────────────────────────
    def _rename_active(self):
        self._active_pane()._rename_selected()

    def _delete_active(self):
        self._active_pane()._delete_selected()

    def _undo_active(self):
        if hasattr(self.app, "_undo_last") and self.app._undo_stack:
            self.app._undo_last()
            # Refresh both panes so the file reappears in its old folder
            # and disappears from the destination.
            self.refresh_all()

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

    def move_files_into(self, src_paths, target_dir, mode="move"):
        is_copy = (mode == "copy")
        moved   = 0           # also reused for "copied" count when is_copy
        renames = {}            # old → new (move only — for batched store flush)
        copies  = {}            # old → new (copy only)
        errors  = []
        skipped = []            # already-at-target or user chose Skip
        batch   = []
        # Persisted "Apply to all" decision: SKIP / REPLACE / RENAME
        sticky_choice = None
        # Pre-count conflicts so the "Apply to all (N remaining)" label is
        # accurate from the first prompt. Same-folder cases never prompt:
        # in move mode they're skipped silently; in copy mode they auto-
        # rename (the whole point of Ctrl+drop).
        def _is_conflict(s):
            if not os.path.exists(s):
                return False
            d = os.path.join(target_dir, os.path.basename(s))
            same_folder = os.path.normpath(s) == os.path.normpath(d)
            if same_folder:
                return False
            return os.path.exists(d)
        pending_conflicts = sum(1 for s in src_paths if _is_conflict(s))
        for src in src_paths:
            if not os.path.exists(src):
                errors.append(f"Missing: {os.path.basename(src)}")
                continue
            target_for_src = target_dir
            if os.path.isdir(src):
                try:
                    src_abs = os.path.abspath(src)
                    tgt_abs = os.path.abspath(target_for_src)
                    if not is_copy and tgt_abs == src_abs:
                        skipped.append(os.path.basename(src))
                        continue
                    if tgt_abs.startswith(src_abs + os.sep):
                        errors.append(
                            f"{os.path.basename(src)}: cannot place a folder inside itself")
                        continue
                except Exception:
                    pass
            dst = os.path.join(target_for_src, os.path.basename(src))
            if os.path.normpath(src) == os.path.normpath(dst):
                if is_copy:
                    # Ctrl+drop in same folder → duplicate with auto-name
                    dst = os.path.join(
                        target_for_src,
                        _suggest_unique_name(target_for_src,
                                             os.path.basename(src)))
                else:
                    skipped.append(os.path.basename(src))
                    continue
            if os.path.exists(dst):
                pending_conflicts -= 1
                if sticky_choice == _ConflictDialog.SKIP:
                    skipped.append(os.path.basename(src))
                    continue
                if sticky_choice == _ConflictDialog.RENAME:
                    dst = os.path.join(
                        target_for_src,
                        _suggest_unique_name(target_for_src, os.path.basename(src)))
                elif sticky_choice == _ConflictDialog.REPLACE:
                    pass  # fall through, shutil.{move,copy2} will overwrite
                else:
                    dlg = _ConflictDialog(self, src, dst,
                                          remaining=pending_conflicts)
                    dlg.exec()
                    if dlg.apply_to_all:
                        sticky_choice = dlg.choice
                    if dlg.choice == _ConflictDialog.SKIP:
                        skipped.append(os.path.basename(src))
                        continue
                    if dlg.choice == _ConflictDialog.RENAME:
                        new_name = dlg.new_name
                        # Guard: still conflicts? bump until unique so we
                        # don't silently overwrite when the user typed an
                        # existing name.
                        if os.path.exists(os.path.join(target_for_src, new_name)):
                            new_name = _suggest_unique_name(target_for_src, new_name)
                        dst = os.path.join(target_for_src, new_name)
                    # Replace: dst stays as-is, shutil overwrites.
            try:
                if is_copy:
                    _copy_path_for_duplicate(src, dst)
                    self._copy_in_memory(src, dst)
                    copies[src] = dst
                    moved += 1
                else:
                    shutil.move(src, dst)
                    self._sync_in_memory(src, dst)
                    renames[src] = dst
                    batch.append({"type": "move", "old_path": src,
                                  "new_path": dst})
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
        if copies:
            # Copies don't rename anything in the embedding stores, but the
            # new entries' attrs need to land on disk so the duplicates
            # come back tagged on next launch.
            try:
                import aisearch_attrs as _am
                if getattr(self.app, "current_project", None):
                    _am.save(self.app.current_project, self.app.attrs_data)
            except Exception:
                pass
        # Surface anything that wasn't processed so the user isn't left
        # wondering why a selection of N produced N - k results.
        verb_past  = "Copied" if is_copy else "Moved"
        title      = "Copy"   if is_copy else "Move"
        if errors or skipped:
            parts = [f"{verb_past} {moved}"]
            if skipped:
                parts.append(f"{len(skipped)} already in target: "
                             + ", ".join(skipped[:5])
                             + ("…" if len(skipped) > 5 else ""))
            if errors:
                parts.append(f"{len(errors)} error(s):\n"
                             + "\n".join(errors[:10]))
            QMessageBox.warning(self, title, "\n".join(parts))
        # Mirror moves into the main window's table rows. Copies don't
        # rename anything, so this is a no-op for is_copy.
        self._update_main_table_paths(renames)
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

    def _copy_in_memory(self, src_path, dst_path):
        """Ctrl+drop duplicate: original stays put, dst is a fresh entry
        that inherits the source's attrs (tags, person_id, note…) so the
        copy comes back tagged."""
        import copy as _copy
        app = self.app
        try:
            paths = app.data["paths"] if app.data and "paths" in app.data else None
            if paths is not None and dst_path not in paths:
                paths.append(dst_path)
        except Exception:
            pass
        try:
            if src_path in app.attrs_data:
                app.attrs_data[dst_path] = _copy.deepcopy(
                    app.attrs_data[src_path])
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

    def _open_default(self, path):
        open_default(path)

    def _open_with(self, cmd, path):
        open_with(cmd, path)

    def _app_choices(self, path):
        return app_choices(path)

    def _save_app_data(self):
        """Persist app.attrs_data + app.data to disk after FM-driven
        deletions / renames mutate them. Without this, the changes
        revert on next launch."""
        app = self.app
        proj = getattr(app, "current_project", None)
        try:
            import aisearch_attrs as _am
            if proj:
                _am.save(proj, app.attrs_data)
                if app.data is not None:
                    _am.atomic_torch_save(app.data, os.path.join(
                        _am.DATA_DIR, f"features_{proj}.pt"))
        except Exception:
            pass

    def _update_main_table_paths(self, renames):
        """Walk the main app's table and rewrite any row whose path is
        in `renames` to its new path. Without this, the main window
        keeps showing the old filename / dirname after an FM rename.
        Also rewrites query_path when the query (top file in search
        mode) is one of the renames — otherwise right-arrow keeps
        moving siblings into the OLD folder."""
        import sys as _sys
        print(f"[FM→MAIN] _update_main_table_paths renames={list(renames.items())[:3]}"
              f"{'...' if len(renames) > 3 else ''}",
              file=_sys.stderr, flush=True)
        if not renames:
            return
        try:
            norm = {os.path.normpath(os.path.abspath(k)): v
                    for k, v in renames.items()}
            table = getattr(self.app, "table", None)
            if table is not None and hasattr(table, "set_row_path"):
                for row in range(table.rowCount()):
                    rp = table.get_row_path(row)
                    if not rp:
                        continue
                    k = os.path.normpath(os.path.abspath(rp))
                    if k in norm:
                        table.set_row_path(row, norm[k])
            # Right-arrow uses query_path's dirname as the move target.
            # If the query was just renamed/moved, update it so the next
            # right-press targets the new folder.
            qp = getattr(self.app, "query_path", None)
            print(f"[FM→MAIN]   query_path before={qp!r}",
                  file=_sys.stderr, flush=True)
            if qp:
                k = os.path.normpath(os.path.abspath(qp))
                if k in norm:
                    self.app.query_path = norm[k]
                    print(f"[FM→MAIN]   query_path UPDATED to={norm[k]!r}",
                          file=_sys.stderr, flush=True)
                else:
                    print(f"[FM→MAIN]   query_path NOT in renames (looked up: {k!r})",
                          file=_sys.stderr, flush=True)
        except Exception as _e:
            print(f"[FM→MAIN]   exception: {_e!r}",
                  file=_sys.stderr, flush=True)

    def _sync_folder_rename(self, old_dir, new_dir):
        """When a directory is renamed, remap any tracked file paths that
        live under it. Builds a renames dict so the on-disk stores can be
        flushed in a single batch. Returns the renames dict so callers
        can pass it on to other consumers (e.g. main-table refresh)."""
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
        return renames

    def _remove_from_app_state(self, path):
        """Remove a trashed path from app.data, attrs_data, and the main
        window's visible table (so search / browse / dup results don't
        show ghost rows for files we just trashed)."""
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
        # Drop matching rows from the main table.
        try:
            table = getattr(app, "table", None)
            if table is not None and hasattr(table, "get_row_path"):
                norm = os.path.normpath(os.path.abspath(path))
                for row in range(table.rowCount() - 1, -1, -1):
                    rp = table.get_row_path(row)
                    if rp and os.path.normpath(os.path.abspath(rp)) == norm:
                        table.removeRow(row)
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
