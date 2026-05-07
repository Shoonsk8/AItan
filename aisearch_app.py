import os, shutil, subprocess, io, torch, threading, queue, json, datetime
from PIL import Image



from PyQt6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                              QLabel, QPushButton, QTableWidget, QTableWidgetItem,
                              QAbstractItemView, QHeaderView, QFrame,
                              QMessageBox, QDialog, QCheckBox, QApplication,
                              QLineEdit, QSpinBox, QProgressBar, QComboBox, QTextEdit,
                              QGridLayout)
from PyQt6.QtCore import Qt, QTimer, QUrl, QMimeData, QPoint, QItemSelectionModel, QFileSystemWatcher, QEvent, pyqtSignal
from PyQt6.QtGui import QPixmap, QShortcut, QKeySequence, QIcon, QCursor, QDrag, QColor, QFont

from sentence_transformers import util as st_util
import aisearch_logic as logic
from aisearch_settings import SettingsView
import aisearch_front_page as front_page
import aisearch_config as cfg
import aisearch_feedback as feedback
import aisearch_preview
import aisearch_attrs as attrs_mod
from aisearch_file_manager import FileManagerWindow
from attr_viewer import _lang_label as _t

VERSION = "2.4.2"


# ── Custom table item types for correct column sorting ──────────────────────

class NumericItem(QTableWidgetItem):
    def __lt__(self, other):
        try:   return float(self.text()) < float(other.text())
        except: return super().__lt__(other)

class SizeItem(QTableWidgetItem):
    def __init__(self, text):
        super().__init__(text)
        self.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    def _bytes(self, text):
        try:
            p = text.split(); num = float(p[0]); unit = p[1].upper()
            return num * {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}.get(unit, 1)
        except: return 0.0
    def __lt__(self, other):
        return self._bytes(self.text()) < self._bytes(other.text())

class DateItem(QTableWidgetItem):
    """Table cell that stores a raw mtime, displays a readable date, sorts by mtime."""
    def __init__(self, mtime):
        self._mtime = mtime
        super().__init__(DateItem._fmt(mtime))
        self.setFlags(self.flags() & ~Qt.ItemFlag.ItemIsEditable)

    @staticmethod
    def _fmt(mtime):
        try:
            return datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return ""

    def __lt__(self, other):
        try:    return self._mtime < other._mtime
        except: return super().__lt__(other)


_VIDEO_EXTS = (".mp4", ".mkv", ".mov", ".m4v", ".avi", ".webm", ".wmv")
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff", ".tif")

def _file_type_str(path):
    if not path:
        return ""
    ext = os.path.splitext(path)[1].lower()
    if ext in _VIDEO_EXTS:
        return "Video"
    if ext in _IMAGE_EXTS:
        return "Pic"
    return ""


# ── Drop zone label / frame ───────────────────────────────────────────────────

def _url_drop_handler(event, callback):
    """Shared URL drop logic for drop-zone widgets."""
    for url in event.mimeData().urls():
        path = url.toLocalFile()
        if os.path.exists(path) and callback:
            callback(path)
            break
    event.accept()

class _ThresholdCombo(QComboBox):
    """QComboBox of a few discrete dup-similarity thresholds, with a SpinBox-
    compatible API (value/setValue/valueChanged) so existing callers don't
    need to know the underlying widget changed."""
    valueChanged = pyqtSignal(int)

    _VALUES = [100, 99, 95, 90, 80, 70]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.wheelEvent = lambda e: e.ignore()
        for v in self._VALUES:
            self.addItem(f"{v}%", v)
        self.currentIndexChanged.connect(
            lambda _: self.valueChanged.emit(self.value()))

    def value(self) -> int:
        d = self.currentData()
        return int(d) if d is not None else self._VALUES[0]

    def setValue(self, v: int):
        try:
            v = int(v)
        except Exception:
            return
        # snap to the nearest allowed value
        v = min(self._VALUES, key=lambda x: abs(x - v))
        idx = self._VALUES.index(v)
        if idx != self.currentIndex():
            self.setCurrentIndex(idx)


class DropZoneLabel(QLabel):
    _bg_color = None  # class-level default; overridden per instance below

    def __init__(self, parent=None):
        super().__init__(_t("DROP IMAGE / 画像をドロップ"), parent)
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        from PyQt6.QtWidgets import QSizePolicy as _QSP
        self.setSizePolicy(_QSP.Policy.Ignored, _QSP.Policy.Ignored)
        self.setMinimumSize(1, 1)
        self._raw_pixmap = None
        self._drop_callback = None
        self.setObjectName("dropZoneLabel")
        self._bg_color = None    # set per-instance by main app
        self._border_color = None  # painted in paintEvent (bypasses QSS)
        self._border_width = 0

    def set_rim(self, color: str | None, width: int = 4):
        """Set the rim painted in paintEvent. None/empty clears the rim.
        Used instead of QSS border because the project-bg fillRect was
        overlapping the QSS frame and visually erasing it."""
        self._border_color = color or None
        self._border_width = int(width) if color else 0
        self.update()

    def paintEvent(self, ev):
        # Manually fill bg with project color — guaranteed to paint
        # regardless of QSS/palette quirks.
        if self._bg_color:
            from PyQt6.QtGui import QPainter, QColor as _QC
            p = QPainter(self)
            try:
                p.fillRect(self.rect(), _QC(self._bg_color))
            finally:
                p.end()
        super().paintEvent(ev)
        # Paint the rim AFTER super so it's on top of the pixmap and any
        # QSS layers. Using QPainter here avoids QSS-vs-paintEvent ordering
        # bugs where the border would silently disappear.
        if self._border_color and self._border_width > 0:
            from PyQt6.QtGui import QPainter, QPen, QColor as _QC
            from PyQt6.QtCore import Qt as _Qt
            p = QPainter(self)
            try:
                pen = QPen(_QC(self._border_color))
                pen.setWidth(self._border_width)
                pen.setJoinStyle(_Qt.PenJoinStyle.MiterJoin)
                p.setPen(pen)
                # Inset by half the pen width so the rim lands inside the rect
                inset = self._border_width / 2
                r = self.rect().adjusted(int(inset), int(inset),
                                         -int(inset), -int(inset))
                p.drawRect(r)
            finally:
                p.end()

    def setPixmap(self, px):
        # Keep the original around so we can rescale to current size on resize
        # without losing quality and without triggering layout feedback.
        from PyQt6.QtGui import QPixmap as _QPx
        if isinstance(px, _QPx) and not px.isNull():
            self._raw_pixmap = px
            self._render_scaled()
        else:
            self._raw_pixmap = None
            super().setPixmap(_QPx())

    def _render_scaled(self):
        if self._raw_pixmap is None or self._raw_pixmap.isNull():
            return
        w, h = max(self.width(), 1), max(self.height(), 1)
        scaled = self._raw_pixmap.scaled(w, h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        super().setPixmap(scaled)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._render_scaled()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls(): event.accept()
        else: event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls(): event.accept()

    def dropEvent(self, event):
        _url_drop_handler(event, self._drop_callback)


class DropZoneFrame(QFrame):
    """QFrame wrapper that accepts URL drops and forwards them to a callback.
    Used as the thumbnail container so border-area drops also work on Linux/xcb."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._drop_callback = None

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls(): event.accept()
        else: event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls(): event.accept()

    def dropEvent(self, event):
        _url_drop_handler(event, self._drop_callback)


# ── Results table ────────────────────────────────────────────────────────────

class FileTable(QTableWidget):
    def __init__(self, parent=None):
        super().__init__(0, 6, parent)
        self.setHorizontalHeaderLabels([_t("Score / スコア"), _t("Size / サイズ"),
                                         _t("Name / 名前"), _t("Path / パス"),
                                         _t("Date / 日付"), _t("Type / 種類")])
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSortingEnabled(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.horizontalHeader().setSortIndicatorShown(True)
        self.horizontalHeader().setStretchLastSection(False)
        for _col in range(6):
            self.horizontalHeader().setSectionResizeMode(_col, QHeaderView.ResizeMode.Interactive)
            # Size column (1) is right-aligned
            if _col == 1:
                self.model().setHeaderData(_col, Qt.Orientation.Horizontal, 
                                            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, 
                                            Qt.ItemDataRole.TextAlignmentRole)
        self.verticalHeader().setVisible(False)
        self.setShowGrid(False)
        self.setAlternatingRowColors(True)
        self.setColumnWidth(0,  90)
        self.setColumnWidth(1, 100)
        self.setColumnWidth(3, 300)
        self.setColumnWidth(4, 130)
        self.setColumnWidth(5, 60)

        self.move_callback      = None
        self.delete_callback    = None
        self.rename_callback    = None
        self.left_key_callback  = None
        self.right_key_callback = None
        self.drop_callback      = None
        self._drag_src_row    = None
        self._drag_press_pos  = None
        self._drag_active     = False
        self._tab_held        = False
        self._collapse_to_row = -1
        self._suppress_release_select = False
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.viewport().installEventFilter(self)
        # Force a clear, opaque selection highlight that overrides any
        # per-item background colors (score gradients, dup group tints).
        # Without this, items with explicit setBackground() bleed through
        # the selection and the highlight is barely visible — only on
        # cells that happen to have no explicit bg (like the Path column
        # in dup mode), giving the impression that "selection only works
        # on Path".
        self.setStyleSheet(
            "QTableWidget::item:selected { "
            "background-color: #4488dd; color: white; }")

    def get_row_path(self, row):
        item = self.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def set_row_path(self, row, path):
        item = self.item(row, 0)
        if item: item.setData(Qt.ItemDataRole.UserRole, path)
        # Refresh the visible Name + Path columns so a rename actually
        # shows up in the table. Header layout depends on the current view,
        # so we look up by header text rather than hard-coding indices.
        # _mask_path lives on the AISearchApp parent, but FileTable is
        # constructed without a Qt parent, so we walk up to find it.
        try:
            import os as _os
            _app = None
            _w = self
            for _ in range(8):
                _w = _w.parent() if _w else None
                if _w and hasattr(_w, "_mask_path"):
                    _app = _w
                    break
            _hdr_to_col = {}
            for _c in range(self.columnCount()):
                _h = self.horizontalHeaderItem(_c)
                if _h:
                    _hdr_to_col[_h.text().split(" / ")[0].strip()] = _c
            _name_col = _hdr_to_col.get("Name")
            if _name_col is not None:
                _it = self.item(row, _name_col)
                if _it: _it.setText(_os.path.basename(path))
            _path_col = _hdr_to_col.get("Path")
            if _path_col is not None:
                _it = self.item(row, _path_col)
                if _it:
                    if _app is not None:
                        _it.setText(_app._mask_path(path))
                    else:
                        _it.setText(_os.path.dirname(path))
        except Exception:
            pass

    def mousePressEvent(self, event):
        # Track drag start for the move-to-folder gesture, then defer all
        # selection logic to Qt's native ExtendedSelection handling.
        # Override-based selection tweaks repeatedly broke things; the
        # standard Qt behavior is what the user actually wants.
        if event.button() == Qt.MouseButton.LeftButton:
            item = self.itemAt(event.pos())
            row  = self.row(item) if item else -1
            self._drag_src_row   = row
            self._drag_press_pos = event.pos()
            self._drag_active    = False
            # Tab+click is the one explicit gesture we still own — deselect
            # just the clicked row, leave the rest of the multi-selection
            # in place. Modifier-aware clicks (Ctrl/Shift) fall through to
            # Qt unchanged.
            if self._tab_held and row >= 0:
                sel_rows = {idx.row() for idx in self.selectionModel().selectedRows()}
                if row in sel_rows:
                    index = self.model().index(row, 0)
                    self.selectionModel().select(
                        index,
                        QItemSelectionModel.SelectionFlag.Deselect |
                        QItemSelectionModel.SelectionFlag.Rows)
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (event.buttons() & Qt.MouseButton.LeftButton) and self._drag_src_row is not None:
            sel_rows = sorted({idx.row() for idx in self.selectionModel().selectedRows()})
            press_on_selected = self._drag_src_row in sel_rows
            # Threshold check — start a real Qt drag with file-URL MIME so
            # external drop targets (FM window, file managers) can receive
            # the move. Internal drops (drop on another row to move into
            # that row's directory) come back through dropEvent / the
            # viewport eventFilter, gated on event.source() == self.
            if not self._drag_active and (event.pos() - self._drag_press_pos).manhattanLength() > 5:
                self._drag_active = True
                urls = []
                for r in sel_rows:
                    p = self.get_row_path(r)
                    if p:
                        urls.append(QUrl.fromLocalFile(p))
                if urls:
                    mime = QMimeData()
                    mime.setUrls(urls)
                    drag = QDrag(self)
                    drag.setMimeData(mime)
                    drag.exec(Qt.DropAction.MoveAction)
                self._drag_src_row = None
                self._drag_active  = False
                self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
                return
            if press_on_selected:
                # Below threshold and press was on a selected row: suppress
                # Qt's default rubberband / selection-changing handler.
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        # QDrag now handles drop targeting (internal or external). The
        # old release-side move logic is gone — drops fire dropEvent /
        # the viewport filter instead.
        self._drag_src_row    = None
        self._drag_active     = False
        super().mouseReleaseEvent(event)

    def eventFilter(self, obj, event):
        if obj is self.viewport():
            t = event.type()
            if t == event.Type.DragEnter:
                if event.mimeData().hasUrls():
                    event.acceptProposedAction(); return True
            elif t == event.Type.DragMove:
                if event.mimeData().hasUrls():
                    event.acceptProposedAction(); return True
            elif t == event.Type.Drop:
                if event.mimeData().hasUrls():
                    # Internal drag (this same table → this same table) =
                    # move to target row's directory. External drops (Nemo,
                    # etc.) = treat as a search query.
                    if event.source() is self:
                        pos = event.position().toPoint()
                        item = self.itemAt(pos)
                        tgt_row = self.row(item) if item else -1
                        sel_rows = sorted({idx.row() for idx in self.selectionModel().selectedRows()})
                        if tgt_row >= 0 and tgt_row not in sel_rows and self.move_callback:
                            self.move_callback(sel_rows, tgt_row)
                            event.acceptProposedAction()
                            return True
                        event.ignore()
                        return True
                    for url in event.mimeData().urls():
                        path = url.toLocalFile()
                        if os.path.exists(path) and self.drop_callback:
                            self.drop_callback(path)
                            break
                    event.acceptProposedAction(); return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Delete and self.delete_callback:
            self.delete_callback()
        elif event.key() == Qt.Key.Key_F2 and self.rename_callback:
            self.rename_callback()
        elif event.key() == Qt.Key.Key_Left and self.left_key_callback:
            self.left_key_callback()
            event.accept()
        elif event.key() == Qt.Key.Key_Right and self.right_key_callback:
            self.right_key_callback()
            event.accept()
        elif event.key() == Qt.Key.Key_Tab:
            if not event.isAutoRepeat():
                self._tab_held = True
            event.accept()
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_Tab and not event.isAutoRepeat():
            self._tab_held = False
        super().keyReleaseEvent(event)


# ── Main application window ──────────────────────────────────────────────────

class AISearchApp(QMainWindow):
    def __init__(self):
        super().__init__()
        # Must be set on the top-level window so the OS/WM advertises it as a
        # drop target — without this, external drags (file manager) never enter
        # the window on Linux/xcb and child widgets never see them.
        self.setAcceptDrops(True)
        self.setWindowTitle(f"あいたん AItan — AI Media Search  Ver {VERSION}")
        self.resize(1500, 980)
        # Auto-hide the status bar when it has nothing to say. Empty bar
        # was eating vertical space at the bottom of the window even when
        # no scan / search was in progress.
        _sb = self.statusBar()
        _sb.setVisible(False)
        _sb.messageChanged.connect(lambda m: _sb.setVisible(bool(m)))

        # Load global config first to get last_project, then reload per-project
        _global_cfg    = cfg.load_config()
        _data_dir = attrs_mod.DATA_DIR
        db_files = sorted(
            [f for f in os.listdir(_data_dir) if f.startswith('features_') and f.endswith('.pt')],
            key=lambda f: os.path.getmtime(os.path.join(_data_dir, f)), reverse=True
        )
        fallback       = db_files[0].replace('features_', '').replace('.pt', '') if db_files else ""
        saved          = _global_cfg.get("last_project", "")
        self.current_project = saved if saved and os.path.exists(os.path.join(_data_dir, f"features_{saved}.pt")) else fallback

        self.config        = cfg.load_config(self.current_project)
        self.config["last_project"] = self.current_project  # ensure it's set
        saved_geom = self.config.get("main_geometry")
        if saved_geom and len(saved_geom) == 4:
            from PyQt6.QtGui import QGuiApplication
            x, y, w, h = saved_geom
            screens = QGuiApplication.screens()
            on_screen = any(
                s.geometry().contains(x + w // 2, y + h // 2)
                for s in screens
            )
            if on_screen:
                self.setGeometry(x, y, w, h)
            else:
                self.resize(1500, 980)
        self.last_move_dir = self.config.get("last_move_dir", os.path.expanduser("~"))
        self.keep_viewer_open = self.config.get("keep_viewer_open", True)

        self.data              = None
        self.base_dirs         = []
        self.query_path        = None
        self.query_emb         = None
        self._lock_preview     = False
        self.feedback_data     = None
        self._dup_display_data = None
        self._undo_stack       = []
        self.attrs_data        = {}
        self._emb_meta_scanned = set()
        self._collapsed_groups = set()
        self._watcher          = None
        self._browse_dir       = None
        # Resume position for stopped dup scans (survives restart via
        # dups_<PROJECT>_progress.json sidecar).
        self._dup_resume_index = 0

        self.preview_handler = aisearch_preview.PreviewHandler(self, self)

        self._setup_ui()
        self._setup_shortcuts()
        self.load_db()
        QTimer.singleShot(0, self.table.setFocus)
        # QApplication.instance().installEventFilter(self)  # disabled: causes settings button to fail

    # ── Focus management ─────────────────────────────────────────────────────

    # Widgets that legitimately hold keyboard focus (user is typing into them)
    _INPUT_TYPES = (QLineEdit, QTextEdit, QComboBox, QSpinBox)

    def eventFilter(self, obj, event):
        """Return arrow-key focus to the table after clicking toolbar controls."""
        if event.type() == QEvent.Type.FocusIn:
            if (not isinstance(obj, AISearchApp._INPUT_TYPES)
                    and isinstance(obj, (QPushButton, QCheckBox))
                    and obj.window() is self):
                QTimer.singleShot(0, self.table.setFocus)
        return super().eventFilter(obj, event)

    # ── UI construction ──────────────────────────────────────────────────────

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Header — 4 horizontal sections in a QSplitter so each is resizable.
        from PyQt6.QtWidgets import QSplitter as _QSplitter
        self.header = QFrame()
        h_layout = QHBoxLayout(self.header)
        h_layout.setContentsMargins(15, 10, 15, 10)
        self._header_splitter = _QSplitter(Qt.Orientation.Horizontal)
        self._header_splitter.setChildrenCollapsible(False)
        self._header_splitter.setHandleWidth(6)
        h_layout.addWidget(self._header_splitter)

        # Section 1 — Primary thumbnail (drop zone for the search query)
        # Section 1 — combined thumbnail area. Holds the drop-target label
        # ("DROP IMAGE") AND a dynamic grid for showing multiple selected
        # thumbnails (dup pair, search top+selected, dup grid, etc.).
        from PyQt6.QtWidgets import QGridLayout as _QGrid
        thumb_outer = DropZoneFrame()
        thumb_outer.setObjectName("thumbOuter")
        thumb_outer.setMinimumSize(150, 150)
        thumb_layout = _QGrid(thumb_outer)
        thumb_layout.setContentsMargins(0, 0, 0, 0)
        thumb_layout.setSpacing(2)
        self.drop_zone = DropZoneLabel()
        self.drop_zone._drop_callback = self.on_drop
        thumb_outer._drop_callback     = self.on_drop
        thumb_layout.addWidget(self.drop_zone, 0, 0)
        self.thumb_outer = thumb_outer
        self._strip_layout = thumb_layout
        self._strip_cells  = []
        self._header_splitter.addWidget(thumb_outer)

        # Info panel
        self.info_widget = QWidget()
        info_layout = QVBoxLayout(self.info_widget)
        info_layout.setContentsMargins(15, 0, 15, 0)

        # Section 4 — Settings + logo (built later after info section).
        # Construct the widgets here, place into section 4 below.
        self.btn_settings = QPushButton(_t("⚙ SETTINGS / ⚙ 設定"))
        self.btn_settings.setStyleSheet(
            "background-color: #6c757d; color: white; font-weight: bold; padding: 6px 12px;")
        self.btn_settings.clicked.connect(self._open_settings)

        self._lbl_logo = QLabel()
        self._lbl_logo.setFixedSize(240, 240)
        self._lbl_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_logo.setStyleSheet("background: transparent;")
        self._lbl_logo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._lbl_logo.setToolTip(_t("Click to toggle AI inspection / クリックでAI検査をオン/オフ"))
        # Pre-load both pixmaps so toggling between them is instant. The
        # off-variant is shown when clip_inspect_mode is "never"; clicking
        # the logo toggles between off and the previously-used active mode.
        _logo_dir = os.path.dirname(os.path.abspath(__file__))
        # Four AI inspect modes — rotational toggle on logo click cycles
        # none → face → clip → both → none. Each mode has its own pixmap.
        # User will supply face-only / clip-only images later; until then
        # the loader falls back to existing on/off pictures so the toggle
        # still functions visually.
        _user_dir   = "/mnt/1TBSSD/Test/AItan/logo/1"
        _both_path  = (f"{_user_dir}/AI smile.jpeg"
                       if os.path.exists(f"{_user_dir}/AI smile.jpeg")
                       else os.path.join(_logo_dir, "aisearch_logo.png"))
        _none_path  = (f"{_user_dir}/No AI No smile.jpeg"
                       if os.path.exists(f"{_user_dir}/No AI No smile.jpeg")
                       else os.path.join(_logo_dir, "aisearch_logo_off.png"))
        # Placeholders for face-only / clip-only — user will drop new images
        # at these paths and they'll auto-pick up.
        _face_path  = (f"{_user_dir}/AI face only.jpeg"
                       if os.path.exists(f"{_user_dir}/AI face only.jpeg")
                       else _both_path)
        _clip_path  = (f"{_user_dir}/AI clip only.jpeg"
                       if os.path.exists(f"{_user_dir}/AI clip only.jpeg")
                       else _both_path)
        def _load(p):
            return QPixmap(p).scaled(240, 240,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation) if os.path.exists(p) else None
        self._logo_pix_both = _load(_both_path)
        self._logo_pix_none = _load(_none_path) or self._logo_pix_both
        self._logo_pix_face = _load(_face_path) or self._logo_pix_both
        self._logo_pix_clip = _load(_clip_path) or self._logo_pix_both
        # Back-compat aliases — older code still references _logo_pix_on/off
        self._logo_pix_on  = self._logo_pix_both
        self._logo_pix_off = self._logo_pix_none
        self._refresh_logo_pixmap()
        # Make the QLabel clickable — a left click toggles inspection mode.
        self._lbl_logo.mousePressEvent = lambda ev: (
            self._toggle_inspect_mode() if ev.button() == Qt.MouseButton.LeftButton else None)
        # Stub _reposition_logo so older code that calls it doesn't crash
        # (logo is now in its own section, no overlay positioning needed).
        self._reposition_logo = lambda: None

        self.lbl_proj_hdr = QLabel(_t("PROJECT: / プロジェクト："))
        self.lbl_proj_hdr.setStyleSheet("color: #00ff00; font-weight: bold;")
        info_layout.addWidget(self.lbl_proj_hdr)

        self.lbl_project = QLabel(self.current_project)
        pfs = self.config.get("project_font_size", 30)
        self.lbl_project.setStyleSheet(f"font-size: {pfs}pt; font-weight: bold;")
        info_layout.addWidget(self.lbl_project)

        self.lbl_base_dir = QLabel("")
        self.lbl_base_dir.setWordWrap(True)
        # Show each base dir on its own line.
        self.lbl_base_dir.setTextFormat(Qt.TextFormat.PlainText)
        info_layout.addWidget(self.lbl_base_dir)

        # Mode buttons (vertical) + dup controls (right)
        from PyQt6.QtWidgets import QSizePolicy
        mode_and_dup = QHBoxLayout()
        mode_and_dup.setSpacing(8)

        self._mode_styles = {
            "search": ("background-color: #2a8ad4; color: white; font-weight: bold; padding: 6px 10px; border: 2px solid white;",
                       "background-color: #1a5a8a; color: #aaaaaa; font-weight: bold; padding: 6px 10px; border: 2px solid transparent;"),
            "dup":    ("background-color: #9b6dff; color: white; font-weight: bold; padding: 6px 10px; border: 2px solid white;",
                       "background-color: #6f42c1; color: #aaaaaa; font-weight: bold; padding: 6px 10px; border: 2px solid transparent;"),
            "browse": ("background-color: #3a8a3a; color: white; font-weight: bold; padding: 6px 10px; border: 2px solid white;",
                       "background-color: #2a4a2a; color: #aaaaaa; font-weight: bold; padding: 6px 10px; border: 2px solid transparent;"),
        }

        mode_col = QVBoxLayout()
        mode_col.setSpacing(4)
        self.btn_mode_search = QPushButton(_t("🔍 Search / 🔍 検索"))
        self.btn_mode_search.setToolTip(_t("Switch to Search mode / 検索モードに切り替え"))
        self.btn_mode_search.clicked.connect(self._enter_search_mode)
        mode_col.addWidget(self.btn_mode_search)

        self.btn_find_dups = QPushButton(_t("♊ Duplicates / ♊ 重複"))
        self.btn_find_dups.setToolTip(_t("Find duplicates (Shift+click to force rescan) / 重複を検索（Shift+クリックで強制再スキャン）"))
        self.btn_find_dups.clicked.connect(self._find_duplicates)
        mode_col.addWidget(self.btn_find_dups)

        self.btn_browse = QPushButton(_t("📂 Browse / 📂 閲覧"))
        self.btn_browse.setToolTip(_t("Browse folder contents (ls mode) / フォルダ内容を閲覧（lsモード）"))
        self.btn_browse.clicked.connect(lambda: self._enter_browse_mode())
        mode_col.addWidget(self.btn_browse)

        # Browse-mode only: re-apply path rules to every file under the
        # currently-browsed folder (recursive). Useful after editing a
        # /Folder/ rule to retag existing files without touching CLIP/face
        # detection. Hidden outside browse mode.
        self.btn_apply_rules = QPushButton(_t("🔧 Apply Rules / 🔧 規則適用"))
        self.btn_apply_rules.setToolTip(_t(
            "One click = one Down-arrow press: apply rules to the current "
            "file and move selection one row down. The file you leave gets "
            "auto-renamed by the navigation hook. / "
            "1クリック＝下矢印キー1回。現在のファイルにルール適用後、"
            "1行下へ移動。離れたファイルは自動改名されます。"))
        self.btn_apply_rules.clicked.connect(self._apply_rules_step)
        self.btn_apply_rules.hide()
        mode_col.addWidget(self.btn_apply_rules)

        mode_and_dup.addLayout(mode_col)

        # Dup-specific controls (hidden unless in dup mode)
        self._dup_controls_widget = QWidget()
        dup_controls = QVBoxLayout(self._dup_controls_widget)
        dup_controls.setContentsMargins(0, 0, 0, 0)
        dup_controls.setSpacing(3)

        # Row 1: czkawka buttons (hidden by default, shown via Settings)
        dup_row1 = QHBoxLayout()
        self._btn_dup_import = QPushButton(_t("Import czkawka / czkawka取込"))
        self._btn_dup_import.setToolTip(_t("Import duplicate results from czkawka (JSON format) / czkawka の重複結果を取り込み（JSON形式）"))
        self._btn_dup_import.clicked.connect(self._import_dup_json)
        self._btn_dup_import.setVisible(self.config.get("show_czkawka_buttons", False))
        dup_row1.addWidget(self._btn_dup_import)
        self._btn_dup_export = QPushButton(_t("Export czkawka / czkawka書出"))
        self._btn_dup_export.setToolTip(_t("Export current duplicate results as czkawka-compatible JSON / 現在の重複結果をczkawka互換JSONで書き出し"))
        self._btn_dup_export.clicked.connect(self._export_dup_json)
        self._btn_dup_export.setVisible(self.config.get("show_czkawka_buttons", False))
        dup_row1.addWidget(self._btn_dup_export)
        dup_row1.addStretch()
        dup_controls.addLayout(dup_row1)

        # Row 2: Threshold | status
        dup_row2 = QHBoxLayout()
        dup_row2.addWidget(QLabel(_t("Threshold: / 閾値：")))
        self.spin_threshold = _ThresholdCombo()
        self.spin_threshold.setValue(self.config.get("dup_threshold", 95))
        self.spin_threshold.setFixedWidth(70)
        self.spin_threshold.setToolTip(_t("Higher = only near-exact duplicates\nLower = similar images too / 高い＝ほぼ完全一致のみ\n低い＝類似画像も含む"))
        self.spin_threshold.valueChanged.connect(self._on_threshold_changed)
        self._dup_result_threshold = None
        self._dup_result_summary   = ""
        dup_row2.addWidget(self.spin_threshold)
        self.lbl_dup_status = QLabel("")
        self.lbl_dup_status.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        dup_row2.addWidget(self.lbl_dup_status)
        dup_row2.addStretch()
        dup_controls.addLayout(dup_row2)

        # Row 3: Scan | Hide confirmed
        dup_row3 = QHBoxLayout()
        self.btn_scan = QPushButton(_t("⟳ Scan / ⟳ スキャン"))
        self.btn_scan.setToolTip(_t("Force rescan (clear cache) / 強制再スキャン（キャッシュ消去）"))
        self.btn_scan.clicked.connect(self._force_rescan)
        self.btn_scan.setStyleSheet(
            "QPushButton { background-color: #6f42c1; color: white; font-weight: bold; "
            "padding: 4px 14px; border: 2px solid #9b6dff; border-radius: 3px; }"
            "QPushButton:hover { background-color: #9b6dff; border-color: white; }"
            "QPushButton:pressed { background-color: #4a2a8a; }")
        dup_row3.addWidget(self.btn_scan)
        # Resume + Rescan buttons — only visible after Stop has paused a scan.
        self.btn_dup_resume = QPushButton(_t("▶ Resume / ▶ 再開"))
        self.btn_dup_resume.setStyleSheet(
            "QPushButton { background-color: #2e7a2e; color: white; font-weight: bold; "
            "padding: 4px 14px; border: 2px solid #5fbb5f; border-radius: 3px; }"
            "QPushButton:hover { background-color: #3d8d3d; border-color: white; }")
        self.btn_dup_resume.hide()
        dup_row3.addWidget(self.btn_dup_resume)
        self.btn_dup_rescan = QPushButton(_t("✕ Cancel / ✕ 中止"))
        self.btn_dup_rescan.setStyleSheet(
            "QPushButton { background-color: #c14242; color: white; font-weight: bold; "
            "padding: 4px 14px; border: 2px solid #ff6b6b; border-radius: 3px; }"
            "QPushButton:hover { background-color: #d65a5a; border-color: white; }")
        self.btn_dup_rescan.hide()
        dup_row3.addWidget(self.btn_dup_rescan)
        self.btn_hide_confirmed = QPushButton(_t("👁 Hide confirmed / 👁 確認済を隠す"))
        self.btn_hide_confirmed.setCheckable(True)
        self.btn_hide_confirmed.setToolTip(_t("Hide files already confirmed as variants/different / バリアント・異なると確認済みのファイルを隠す"))
        self.btn_hide_confirmed.toggled.connect(self._apply_confirmed_filter)
        self.btn_hide_confirmed.toggled.connect(
            lambda checked: self.btn_hide_confirmed.setText(
                _t("👁 Unhide confirmed / 👁 確認済を表示") if checked else _t("👁 Hide confirmed / 👁 確認済を隠す")))
        dup_row3.addWidget(self.btn_hide_confirmed)
        dup_row3.addStretch()
        dup_controls.addLayout(dup_row3)

        # Rules + actions split across multiple rows so the dup-control bar
        # doesn't push the layout absurdly wide.
        # Row 4a: per-file rules (Smaller / Larger / Deeper / Shallower
        # / Older / Newer) — UNION semantics, multiple can be active.
        # Row 4b: group-level + invert + main action buttons.
        # Row 4c: media-type filters.
        self._dup_rule_checks = {}
        def _make_check(key, label):
            cb = QCheckBox(label)
            cb.setStyleSheet("color:#ddd; font-size:9pt; padding:0 4px;")
            cb.toggled.connect(self._refresh_dup_delete_marks)
            self._dup_rule_checks[key] = cb
            return cb

        dup_row4a = QHBoxLayout()
        for _k, _l in [
            ("same",      _t("Same size / 同サイズ")),
            ("smaller",   _t("Smaller / 小")),
            ("larger",    _t("Larger / 大")),
        ]:
            dup_row4a.addWidget(_make_check(_k, _l))
        dup_row4a.addStretch()
        dup_controls.addLayout(dup_row4a)

        dup_row4a2 = QHBoxLayout()
        dup_row4a2.addWidget(_make_check("deeper",    _t("Deeper / 深")))
        dup_row4a2.addWidget(_make_check("shallower", _t("Shallower / 浅")))
        dup_row4a2.addStretch()
        dup_controls.addLayout(dup_row4a2)

        dup_row4b = QHBoxLayout()
        dup_row4b.addWidget(_make_check("older",   _t("Older / 旧")))
        dup_row4b.addWidget(_make_check("newer",   _t("Newer / 新")))
        dup_row4b.addWidget(_make_check("reverse", _t("Reverse / 反転")))
        dup_row4b.addStretch()
        dup_controls.addLayout(dup_row4b)

        # Row 4c — media-type filters; hide groups made entirely of one type.
        dup_row4c = QHBoxLayout()
        self._chk_hide_pictures = QCheckBox(_t("📷 Hide pics / 📷 画像非表示"))
        self._chk_hide_pictures.setStyleSheet("color:#ddd; font-size:9pt; padding:0 4px;")
        self._chk_hide_pictures.toggled.connect(lambda _: self._apply_row_visibility())
        self._chk_hide_videos = QCheckBox(_t("🎬 Hide videos / 🎬 動画非表示"))
        self._chk_hide_videos.setStyleSheet("color:#ddd; font-size:9pt; padding:0 4px;")
        self._chk_hide_videos.toggled.connect(lambda _: self._apply_row_visibility())
        dup_row4c.addWidget(self._chk_hide_pictures)
        dup_row4c.addWidget(self._chk_hide_videos)
        dup_row4c.addStretch()
        dup_controls.addLayout(dup_row4c)

        # Row 4d — bulk action buttons (collapse/uncollapse/delete) under
        # the hide-pics/videos checkboxes.
        dup_row4d = QHBoxLayout()
        self.btn_dup_collapse = QPushButton(_t("📁 Collapse / 📁 折畳"))
        self.btn_dup_collapse.setToolTip(_t(
            "Collapse groups whose active rules mark at least one file. / "
            "選択条件に該当するファイルがあるグループを折り畳み。"))
        self.btn_dup_collapse.setStyleSheet(
            "QPushButton { background:#2e3a5e; color:#aaccff; "
            "border:1px solid #446699; padding:3px 10px; font-weight:bold; }"
            "QPushButton:hover { background:#3a4a7a; }")
        self.btn_dup_collapse.clicked.connect(self._collapse_dups_by_rule)
        dup_row4d.addWidget(self.btn_dup_collapse)
        self.btn_dup_uncollapse = QPushButton(_t("📂 Uncollapse / 📂 展開"))
        self.btn_dup_uncollapse.setToolTip(_t(
            "Expand all collapsed groups. / すべての折畳グループを展開。"))
        self.btn_dup_uncollapse.setStyleSheet(
            "QPushButton { background:#3a4a3a; color:#aaccaa; "
            "border:1px solid #557755; padding:3px 10px; font-weight:bold; }"
            "QPushButton:hover { background:#4a5e4a; }")
        self.btn_dup_uncollapse.clicked.connect(self._uncollapse_all_dups)
        dup_row4d.addWidget(self.btn_dup_uncollapse)
        self.btn_dup_delete = QPushButton(_t("🗑 Delete / 🗑 削除"))
        self.btn_dup_delete.setToolTip(_t(
            "Delete files matching any active rule across all groups. / "
            "選択条件に該当するファイルを全グループから削除。"))
        self.btn_dup_delete.setStyleSheet(
            "QPushButton { background:#7a2020; color:#ffaaaa; "
            "border:1px solid #aa3333; padding:3px 10px; font-weight:bold; }"
            "QPushButton:hover { background:#9a2020; }"
            "QPushButton:disabled { color:#666; background:#333; border-color:#555; }")
        self.btn_dup_delete.clicked.connect(self._delete_dups_by_rule)
        dup_row4d.addWidget(self.btn_dup_delete)
        dup_row4d.addStretch()
        dup_controls.addLayout(dup_row4d)

        self._dup_controls_widget.hide()
        mode_and_dup.addWidget(self._dup_controls_widget)
        mode_and_dup.addStretch()

        # Allow window to shrink below natural button widths
        for _i in range(mode_and_dup.count()):
            _item = mode_and_dup.itemAt(_i)
            if _item and _item.widget():
                _item.widget().setMinimumWidth(0)
        info_layout.addLayout(mode_and_dup)

        # Search progress + status: created here but added to main_layout
        # later (between the splitter and the table) so they're always
        # visible regardless of section sizing.
        self.search_status_label = QLabel("")
        self.search_status_label.setStyleSheet(
            "color: #4a90d9; font-weight: bold; font-size: 10pt; padding: 2px 4px;")
        self.search_status_label.hide()

        self.search_progress = QProgressBar()
        self.search_progress.setRange(0, 0)   # indeterminate animation while searching
        self.search_progress.setFixedHeight(14)
        self.search_progress.setTextVisible(False)
        self.search_progress.setStyleSheet(
            "QProgressBar { border: 1px solid #4a90d9; border-radius: 2px; "
            "  background: #1a1a1a; }"
            "QProgressBar::chunk { background: #4a90d9; }")
        self.search_progress.hide()

        # Compact "row N / total" position indicator that lives just above
        # the table — refreshed on selection change.
        self.row_position_label = QLabel("")
        self.row_position_label.setStyleSheet(
            "color: #adb5bd; font-size: 9pt; padding: 1px 4px;")
        self.row_position_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        undo_row = QHBoxLayout()
        self.btn_undo = QPushButton(_t("↩ Undo / ↩ 元に戻す"))
        self.btn_undo.setEnabled(False)
        self.btn_undo.setToolTip(_t("Nothing to undo / 元に戻す操作なし"))
        self.btn_undo.clicked.connect(self._undo_last)
        self.btn_undo.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.btn_undo.customContextMenuRequested.connect(lambda _: self._show_undo_history())
        undo_row.addWidget(self.btn_undo)
        btn_undo_hist = QPushButton("🕘")
        btn_undo_hist.setFixedWidth(30)
        btn_undo_hist.setToolTip(_t("View undo history (Ctrl+Shift+Z) / 履歴を表示（Ctrl+Shift+Z）"))
        btn_undo_hist.clicked.connect(self._show_undo_history)
        undo_row.addWidget(btn_undo_hist)
        undo_row.addStretch()
        info_layout.addLayout(undo_row)

        self.attr_panel = self._build_attr_panel()
        # self.attr_panel.hide()  # disabled
        # info_layout.addWidget(self.attr_panel)  # disabled — attrs live in preview window only

        info_layout.addStretch()
        self.info_widget.setMinimumWidth(0)
        self.info_widget.setMinimumHeight(0)
        from PyQt6.QtWidgets import QSizePolicy as _QSizePolicy
        self.info_widget.setSizePolicy(_QSizePolicy.Policy.Preferred,
                                       _QSizePolicy.Policy.Ignored)
        self.header.setMinimumHeight(0)
        self.header.setSizePolicy(_QSizePolicy.Policy.Preferred,
                                  _QSizePolicy.Policy.Ignored)
        # Section 3 — info_widget (PROJECT info + mode buttons + Undo)
        self._header_splitter.addWidget(self.info_widget)

        # Section 4 — settings button + logo + "disable preview window" checkbox
        _section4 = QWidget()
        _section4.setMinimumWidth(120)
        _s4_layout = QVBoxLayout(_section4)
        _s4_layout.setContentsMargins(0, 0, 0, 0)
        _s4_layout.setSpacing(4)
        _s4_layout.addWidget(self.btn_settings, alignment=Qt.AlignmentFlag.AlignRight)
        _s4_layout.addWidget(self._lbl_logo, alignment=Qt.AlignmentFlag.AlignRight)

        # ── RSS ceiling controls (under the logo) ─────────────────────────────
        # Spinner sets the threshold; live readout shows current RSS, colored
        # green/yellow/red based on proximity. When RSS exceeds the spinner
        # value, the next inspect attempt flips clip_inspect_mode to "never"
        # and the logo swaps to no-smile.
        from PyQt6.QtWidgets import QSpinBox as _QSB
        _ceil_row = QHBoxLayout()
        _ceil_row.setContentsMargins(0, 0, 0, 0)
        _ceil_row.setSpacing(4)
        # Stretch on the LEFT pushes the label + spinner to the right edge
        # so they line up with the right-aligned logo above.
        _ceil_row.addStretch()
        _ceil_lbl = QLabel(_t("AI off above: / AI停止閾値："))
        _ceil_lbl.setStyleSheet("color:#bbb; font-size:9pt;")
        _ceil_row.addWidget(_ceil_lbl)
        self._rss_ceiling_sb = _QSB()
        # Scale the ceiling range to actual system RAM. A 29 GB host
        # shouldn't be capped at 16 GB, and a low-RAM host shouldn't
        # display absurdly large defaults. Max = 90% of total RAM (with
        # a floor of 16 GB so high-RAM users always have headroom);
        # default = 50% of total RAM (capped at 8 GB).
        try:
            import psutil as _psutil
            _total_ram_mb = int(_psutil.virtual_memory().total / (1024 * 1024))
        except Exception:
            _total_ram_mb = 8000
        _max_ceiling = max(16000, int(_total_ram_mb * 0.9))
        _default_ceiling = max(1500, min(8000, int(_total_ram_mb * 0.5)))
        self._rss_ceiling_sb.setRange(500, _max_ceiling)
        self._rss_ceiling_sb.setSingleStep(100)
        self._rss_ceiling_sb.setSuffix(" MB")
        self._rss_ceiling_sb.setValue(int(self.config.get("clip_inspect_rss_limit_mb", _default_ceiling)))
        self._rss_ceiling_sb.setFixedWidth(110)
        self._rss_ceiling_sb.setToolTip(_t(
            "Above this RSS the app flips AI to 'never' to avoid OOM. / "
            "RSS の上限。これを超えたら AI を停止して OOM を防止。"))
        def _on_rss_ceiling_changed(v):
            self.config["clip_inspect_rss_limit_mb"] = int(v)
            cfg.save_config(self.config, getattr(self, "current_project", None))
        self._rss_ceiling_sb.valueChanged.connect(_on_rss_ceiling_changed)
        _ceil_row.addWidget(self._rss_ceiling_sb)
        _s4_layout.addLayout(_ceil_row)

        self._rss_now_lbl = QLabel("— MB")
        self._rss_now_lbl.setStyleSheet(
            "color:#8fc88f; font-family:monospace; font-size:9pt; padding:2px 6px;"
            "background:#1e2630; border-radius:3px;")
        self._rss_now_lbl.setToolTip(_t("Current process RSS / 現在のプロセスRSS"))
        _s4_layout.addWidget(self._rss_now_lbl, alignment=Qt.AlignmentFlag.AlignRight)

        from PyQt6.QtCore import QTimer as _QT
        def _refresh_rss():
            try:
                import psutil as _ps
                _rss = _ps.Process().memory_info().rss / (1024 * 1024)
            except Exception:
                self._rss_now_lbl.setText("— MB")
                return
            _ceil = float(self._rss_ceiling_sb.value())
            _ratio = _rss / _ceil if _ceil > 0 else 0
            if _ratio >= 1.0:
                _bg, _fg = "#3a1a1a", "#ff8888"
            elif _ratio >= 0.7:
                _bg, _fg = "#3a2e1a", "#ddaa55"
            else:
                _bg, _fg = "#1e2630", "#8fc88f"
            self._rss_now_lbl.setStyleSheet(
                f"color:{_fg}; font-family:monospace; font-size:9pt; padding:2px 6px;"
                f"background:{_bg}; border-radius:3px;")
            self._rss_now_lbl.setText(f"now: {_rss:.0f} MB")
        self._rss_refresh_timer = _QT(self)
        self._rss_refresh_timer.setInterval(2000)
        self._rss_refresh_timer.timeout.connect(_refresh_rss)
        self._rss_refresh_timer.start()
        _refresh_rss()

        self._chk_disable_preview = QCheckBox(_t("Disable preview / プレビュー無効"))
        self._chk_disable_preview.setToolTip(_t(
            "Don't open the preview window when selecting rows / 行選択時にプレビューウィンドウを開かない"))
        self._chk_disable_preview.setChecked(self.config.get("disable_preview", False))
        def _on_disable_preview_toggle(v):
            self.config["disable_preview"] = bool(v)
            cfg.save_config(self.config, getattr(self, "current_project", None))
        self._chk_disable_preview.toggled.connect(_on_disable_preview_toggle)
        _s4_layout.addWidget(self._chk_disable_preview, alignment=Qt.AlignmentFlag.AlignRight)
        _s4_layout.addStretch()
        self._header_splitter.addWidget(_section4)

        # Restore saved sizes if the user has dragged before, else default.
        # 3 sections: [thumb area, info, settings].
        def _apply_header_sizes():
            saved = self.config.get("header_splitter_sizes")
            if saved and len(saved) == self._header_splitter.count():
                self._header_splitter.setSizes(saved)
            else:
                # Default: thumb area gets the widest share
                self._header_splitter.setSizes([700, 400, 200])
        QTimer.singleShot(0, _apply_header_sizes)
        # Persist sizes whenever the user drags a divider
        def _save_header_sizes(_p, _i):
            self.config["header_splitter_sizes"] = self._header_splitter.sizes()
            cfg.save_config(self.config, getattr(self, "current_project", None))
        self._header_splitter.splitterMoved.connect(_save_header_sizes)

        # Wrap header + table in a vertical splitter so the user can drag the
        # divider to resize either section.
        from PyQt6.QtWidgets import QSplitter
        self._main_splitter = QSplitter(Qt.Orientation.Vertical)
        self._main_splitter.setChildrenCollapsible(False)
        self._main_splitter.setHandleWidth(6)
        self._main_splitter.addWidget(self.header)
        # Splitter only — search/dup progress is added INSIDE the splitter's
        # table section below so it sits at the table border instead of pushing
        # everything down from the top.
        main_layout.addWidget(self._main_splitter, stretch=1)

        # Debug log panel — hidden by default, toggle with Ctrl+Shift+D.
        # Mirrors stderr log output so you can monitor background workers,
        # snap cascades, etc. without keeping the terminal visible.
        from PyQt6.QtWidgets import QPlainTextEdit
        self._debug_panel = QPlainTextEdit()
        self._debug_panel.setReadOnly(True)
        self._debug_panel.setMaximumBlockCount(500)
        self._debug_panel.setFixedHeight(180)
        self._debug_panel.setStyleSheet(
            "background:#0a0a0a; color:#9fbf9f; font-family:monospace; "
            "font-size:8pt; border-top:1px solid #444;")
        self._debug_panel.hide()
        main_layout.addWidget(self._debug_panel)
        try:
            from aisearch_debug import set_panel as _set_dbg_panel
            _set_dbg_panel(self._debug_panel)
        except Exception:
            pass
        from PyQt6.QtGui import QShortcut, QKeySequence
        _dbg_sc = QShortcut(QKeySequence("Ctrl+Shift+D"), self)
        _dbg_sc.activated.connect(
            lambda: self._debug_panel.setVisible(not self._debug_panel.isVisible()))

        # Thumbs are sized by the horizontal header splitter — user can drag
        # to resize each section. No automatic sync is needed.
        self._sync_thumb_size = lambda: None


        # Results table
        self.table = FileTable()
        self.table.move_callback      = self._handle_drag_move
        self.table.delete_callback    = self.delete_file
        self.table.rename_callback    = self.rename_file
        self.table.left_key_callback  = self.on_left_key_press
        self.table.right_key_callback = self.on_right_key_press
        self.table.drop_callback      = self.on_drop
        self.table.customContextMenuRequested.connect(self._on_right_click)
        self.table.itemSelectionChanged.connect(self.handle_preview)
        self.table.itemSelectionChanged.connect(self._update_row_position_label)
        # Keep the dup-delete button label in sync with the live selection
        # count — Ctrl+click after a rule fires updates the count.
        self.table.itemSelectionChanged.connect(self._sync_dup_delete_btn)
        self.table.model().rowsInserted.connect(lambda *a: self._update_row_position_label())
        self.table.model().rowsRemoved.connect(lambda *a: self._update_row_position_label())
        self.table.cellDoubleClicked.connect(lambda r, c: self.on_double_click())
        self.table.cellClicked.connect(self._on_group_cell_click)
        # Wrap the table with the search/dup progress label + bar above it so
        # they appear at the top edge of the list rather than pushing the
        # whole header down.
        _table_wrap = QWidget()
        _table_wrap_lay = QVBoxLayout(_table_wrap)
        _table_wrap_lay.setContentsMargins(0, 0, 0, 0)
        _table_wrap_lay.setSpacing(0)
        _table_wrap_lay.addWidget(self.search_status_label)
        _table_wrap_lay.addWidget(self.search_progress)
        _table_wrap_lay.addWidget(self.row_position_label)
        _table_wrap_lay.addWidget(self.table, stretch=1)
        self._main_splitter.addWidget(_table_wrap)
        # Header gets just enough; table gets the rest by default
        self._main_splitter.setStretchFactor(0, 0)
        self._main_splitter.setStretchFactor(1, 1)
        # Restore saved splitter position if available, else default the
        # header to the project-to-undo span so the layout doesn't open with
        # a giant thumb that covers the table.
        def _apply_initial_splitter():
            _ss = self.config.get("main_splitter_sizes")
            if _ss and len(_ss) == 2:
                self._main_splitter.setSizes(_ss)
                return
            # Default — header height = project-to-undo span (computed)
            try:
                from PyQt6.QtCore import QPoint as _QP2
                top_in_iw = self.lbl_proj_hdr.mapTo(self.info_widget, _QP2(0, 0)).y()
                _anchor = self.btn_browse  # bottom of mode column
                bot_in_iw = _anchor.mapTo(self.info_widget, _QP2(0, _anchor.height())).y()
                span = max(bot_in_iw - top_in_iw, 200)
            except Exception:
                span = 350
            margins = h_layout.contentsMargins()
            header_h = span + margins.top() + margins.bottom()
            total_h = max(self._main_splitter.height(), header_h + 200)
            self._main_splitter.setSizes([header_h, total_h - header_h])
        QTimer.singleShot(0, _apply_initial_splitter)
        # Persist splitter position whenever user drags it
        def _save_splitter_sizes(_p, _i):
            self.config["main_splitter_sizes"] = self._main_splitter.sizes()
            cfg.save_config(self.config, getattr(self, "current_project", None))
        self._main_splitter.splitterMoved.connect(_save_splitter_sizes)
        self._apply_colors()
        self.reload_fonts()
        self._apply_header_theme()
        col_widths = self.config.get("col_widths", {})
        for col, default in [(0, 90), (1, 100), (2, 400), (3, 300), (4, 130), (5, 60)]:
            self.table.setColumnWidth(col, col_widths.get(str(col), default))

    def _apply_colors(self):
        c = self.config.get("colors", cfg.DEFAULT_COLORS)
        sel = c.get("selection", cfg.DEFAULT_COLORS["selection"])
        r, g, b = int(sel[1:3], 16), int(sel[3:5], 16), int(sel[5:7], 16)
        text_color = "black" if (r * 299 + g * 587 + b * 114) / 1000 > 128 else "white"
        # Selection highlight in three places so per-item brushes
        # (group shading, dup-mark red) can't hide it:
        #   1. QTableView selection-* properties — outranks per-item
        #      foregrounds for the selected row's text.
        #   2. ::item:selected stylesheet — covers the background.
        #   3. QPalette Highlight/HighlightedText — fallback for any
        #      delegate that bypasses the stylesheet.
        self.table.setStyleSheet(
            f"QTableView {{ selection-background-color: {sel}; "
            f"              selection-color: {text_color}; }} "
            f"QTableWidget::item:selected {{ background-color: {sel}; "
            f"                               color: {text_color}; }}")
        from PyQt6.QtGui import QPalette as _QP, QColor as _QC
        _pal = self.table.palette()
        _pal.setColor(_QP.ColorRole.Highlight, _QC(sel))
        _pal.setColor(_QP.ColorRole.HighlightedText, _QC(text_color))
        # Also set the inactive variants so the highlight stays readable
        # when the table loses focus (otherwise Qt washes them out).
        _pal.setColor(_QP.ColorGroup.Inactive, _QP.ColorRole.Highlight, _QC(sel))
        _pal.setColor(_QP.ColorGroup.Inactive, _QP.ColorRole.HighlightedText, _QC(text_color))
        self.table.setPalette(_pal)
        da = c.get("dup_a", cfg.DEFAULT_COLORS["dup_a"])
        db = c.get("dup_b", cfg.DEFAULT_COLORS["dup_b"])
        self._dup_shades = [
            (0.98, QColor(da[0]), QColor(db[0])),
            (0.90, QColor(da[1]), QColor(db[1])),
            (0.80, QColor(da[2]), QColor(db[2])),
            (0.00, QColor(da[3]), QColor(db[3])),
        ]
        self._score_colors  = c.get("score",      cfg.DEFAULT_COLORS["score"])
        self._unmarked_color = c.get("unmarked",   cfg.DEFAULT_COLORS["unmarked"])
        self._attr_color     = c.get("attr_label", cfg.DEFAULT_COLORS["attr_label"])

    def reload_colors(self):
        self._apply_colors()
        header = self.table.horizontalHeaderItem(0)
        if header and header.text() in ("Group", _t("Group / グループ")):
            self._recolor_dup_groups()

    def refresh_language(self):
        """Re-translate all main-window labels/buttons/tooltips after a language change."""
        # Top bar
        self.btn_settings.setText(_t("⚙ SETTINGS / ⚙ 設定"))
        self.lbl_proj_hdr.setText(_t("PROJECT: / プロジェクト："))
        _base = getattr(self, '_base_dir_label_value', "")
        self.lbl_base_dir.setText(_base)
        # Mode buttons
        self.btn_mode_search.setText(_t("🔍 Search / 🔍 検索"))
        self.btn_mode_search.setToolTip(_t("Switch to Search mode / 検索モードに切り替え"))
        self.btn_find_dups.setText(_t("♊ Duplicates / ♊ 重複"))
        self.btn_find_dups.setToolTip(_t("Find duplicates (Shift+click to force rescan) / 重複を検索（Shift+クリックで強制再スキャン）"))
        self.btn_browse.setText(_t("📂 Browse / 📂 閲覧"))
        self.btn_browse.setToolTip(_t("Browse folder contents (ls mode) / フォルダ内容を閲覧（lsモード）"))
        # Dup controls
        if hasattr(self, '_btn_dup_import'):
            self._btn_dup_import.setText(_t("Import czkawka / czkawka取込"))
            self._btn_dup_import.setToolTip(_t("Import duplicate results from czkawka (JSON format) / czkawka の重複結果を取り込み（JSON形式）"))
            self._btn_dup_export.setText(_t("Export czkawka / czkawka書出"))
            self._btn_dup_export.setToolTip(_t("Export current duplicate results as czkawka-compatible JSON / 現在の重複結果をczkawka互換JSONで書き出し"))
        self.spin_threshold.setToolTip(_t("Higher = only near-exact duplicates\nLower = similar images too / 高い＝ほぼ完全一致のみ\n低い＝類似画像も含む"))
        self.btn_hide_confirmed.setText(
            _t("👁 Unhide confirmed / 👁 確認済を表示") if self.btn_hide_confirmed.isChecked()
            else _t("👁 Hide confirmed / 👁 確認済を隠す"))
        self.btn_hide_confirmed.setToolTip(_t("Hide files already confirmed as variants/different / バリアント・異なると確認済みのファイルを隠す"))
        # Undo
        self.btn_undo.setText(_t("↩ Undo / ↩ 元に戻す"))
        self.btn_undo.setToolTip(
            _t(self._undo_stack[-1]["desc"]) if self._undo_stack else _t("Nothing to undo / 元に戻す操作なし"))
        # Inline attrs
        if hasattr(self, '_inline_note'):
            self._inline_note.setPlaceholderText(_t("Note… / ノート…"))
        if hasattr(self, '_confirmed_cb'):
            self._confirmed_cb.setToolTip(_t("Confirmed different / 異なると確認済み"))
        # Table headers — depend on current mode
        header_item = self.table.horizontalHeaderItem(0)
        cur_text = header_item.text() if header_item else ""
        if cur_text in ("Group", _t("Group / グループ")):
            self.table.setHorizontalHeaderLabels([_t("Group / グループ"), _t("Size / サイズ"),
                                                   _t("Name / 名前"), _t("Path / パス")])
        elif cur_text in ("#",):
            self.table.setHorizontalHeaderLabels(["#", _t("Size / サイズ"),
                                                   _t("Name / 名前"), _t("Path / パス"),
                                                   _t("Date / 日付"), _t("Type / 種類")])
        else:
            self.table.setHorizontalHeaderLabels([_t("Score / スコア"), _t("Size / サイズ"),
                                                   _t("Name / 名前"), _t("Path / パス"),
                                                   _t("Date / 日付"), _t("Type / 種類")])
        # Drop zone default text if it still shows a mode label
        if hasattr(self, 'drop_zone'):
            _dtxt = self.drop_zone.text()
            if _dtxt in ("DUPLICATES\nFINDER", _t("DUPLICATES\nFINDER / 重複\n検索")):
                self.drop_zone.setText(_t("DUPLICATES\nFINDER / 重複\n検索"))
            elif _dtxt in ("DROP IMAGE", _t("DROP IMAGE / 画像をドロップ")):
                self.drop_zone.setText(_t("DROP IMAGE / 画像をドロップ"))
            elif _dtxt in ("drop image or video", _t("drop image or video / 画像・動画をドロップ")):
                self.drop_zone.setText(_t("drop image or video / 画像・動画をドロップ"))

    def reload_fonts(self):
        # Table
        fs_table = self.config.get("table_font_size", 10)
        f_table = QFont("", fs_table)
        self.table.setFont(f_table)
        self.table.horizontalHeader().setStyleSheet(f"font-size: {fs_table}pt;")
        # Attr panels — rebuild so font applies cleanly (setFont unreliable with stylesheets)
        fs_attr = self.config.get("attr_font_size", 10)
        pw = self.preview_handler.window
        if pw:
            old_scroll = getattr(pw, '_attr_scroll', None)
            if old_scroll:
                visible = old_scroll.isVisible()
                layout = pw.layout()
                idx = layout.indexOf(old_scroll)
                layout.removeWidget(old_scroll)
                old_scroll.deleteLater()
                pw.attr_widget = pw._build_attr_panel()
                from PyQt6.QtWidgets import QScrollArea
                pw._attr_scroll = QScrollArea()
                pw._attr_scroll.setWidgetResizable(True)
                pw._attr_scroll.setWidget(pw.attr_widget)
                pw._attr_scroll.setVisible(visible)
                layout.insertWidget(idx, pw._attr_scroll)
                if hasattr(pw, 'current_path') and pw.current_path:
                    pw.load_file(pw.current_path)
            if hasattr(pw, '_info_box'):
                pw._info_box.setStyleSheet(
                    f"background-color: #1e1e1e; color: #ccc; border: none;"
                    f" font-family: monospace; font-size: {fs_attr}pt;")
        # Project name label — font size only; color is set by _apply_header_theme
        pfs = self.config.get("project_font_size", 30)
        self._apply_header_theme(font_size_only=True, pfs=pfs)
        # General — explicitly walk all widgets, skip those with dedicated font controls
        fs_ui = self.config.get("ui_font_size", 10)
        f_ui = QFont("", fs_ui)
        excluded = {self.table, self.lbl_project}
        excluded.update(self.table.findChildren(QWidget))
        if hasattr(self, 'attr_panel'):
            excluded.add(self.attr_panel)
            excluded.update(self.attr_panel.findChildren(QWidget))
        if pw and hasattr(pw, 'attr_widget'):
            excluded.add(pw.attr_widget)
            excluded.update(pw.attr_widget.findChildren(QWidget))
        for win in QApplication.instance().topLevelWidgets():
            for w in [win] + list(win.findChildren(QWidget)):
                if w not in excluded:
                    w.setFont(f_ui)

    def _apply_header_theme(self, font_size_only=False, pfs=None):
        theme = self.config.get("theme", "Dark")
        is_dark = theme != "Light"
        pfs = pfs if pfs is not None else self.config.get("project_font_size", 30)
        if is_dark:
            header_bg   = "#343a40"
            thumb_bg    = "#495057"
            thumb_brd   = "#666"
            drop_color  = "#ced4da"
            proj_color  = "white"
            base_color  = "#aaaaaa"
            status_color = "#adb5bd"
            hide_ss = (
                "QPushButton { background-color: #444; color: #ccc; border: 1px solid #666; padding: 3px 8px; }"
                "QPushButton:checked { background-color: #c0392b; color: white; border: 1px solid #e74c3c; }")
        else:
            header_bg   = "#dcdcdc"
            thumb_bg    = "#c0c0c0"
            thumb_brd   = "#888"
            drop_color  = "#333333"
            proj_color  = "#1a1a1a"
            base_color  = "#555555"
            status_color = "#666666"
            hide_ss = (
                "QPushButton { background-color: #c8c8c8; color: #333; border: 1px solid #999; padding: 3px 8px; }"
                "QPushButton:checked { background-color: #c0392b; color: white; border: 1px solid #e74c3c; }")

        self.lbl_project.setStyleSheet(
            f"color: {proj_color}; font-size: {pfs}pt; font-weight: bold;")
        if font_size_only:
            return
        # Per-project color — when set, becomes a visible border around the
        # thumbnail so the user can see which project is active even when an
        # image fills the area. Falls back to theme default.
        _proj_color = self.config.get("project_bg_color", "")
        _border_color = _proj_color if _proj_color else thumb_brd
        _border_w = 6 if _proj_color else 3   # thicker when explicitly set
        _bg_color = _proj_color if _proj_color else thumb_bg
        # Debug: log what color we're applying so we can tell if the config
        # has the color or if it falls back to the default theme bg.
        if os.environ.get("AISEARCH_DEBUG_BG"):
            print(f"[_apply_header_theme] project={getattr(self,'current_project',None)!r} "
                  f"proj_color={_proj_color!r} bg={_bg_color}")
        self.header.setStyleSheet(f"background-color: {header_bg};")
        # The right-pad widget paints reliably via palette+stylesheet — use
        # the same mechanism on EVERY thumbnail-related widget so they all
        # show the project color uniformly. Border lives only on thumb_outer.
        from PyQt6.QtGui import QPalette, QColor as _QC
        _col = _QC(_bg_color)
        def _paint_thumb_widget(_w):
            _pal = _w.palette()
            _pal.setColor(QPalette.ColorRole.Window, _col)
            _w.setAutoFillBackground(True)
            _w.setPalette(_pal)
        # thumb_outer: palette + ID-selector stylesheet for bg+border
        _paint_thumb_widget(self.thumb_outer)
        self.thumb_outer.setStyleSheet(
            f"#thumbOuter {{ background-color: {_bg_color}; "
            f"  border: {_border_w}px solid {_border_color}; }}")
        # drop_zone (the picture cell) — its paintEvent paints _bg_color
        # before drawing the pixmap, so the bg always wins.
        self.drop_zone._bg_color = _bg_color
        self.drop_zone.update()
        self.drop_zone.setStyleSheet(
            f"color: {drop_color}; font-weight: bold;")
        # Strip cells (dup/search mode) get the same paintEvent treatment.
        for _cell in getattr(self, "_strip_cells", []):
            _cell._bg_color = _bg_color
            _cell.update()
        self.info_widget.setStyleSheet(f"background-color: {header_bg};")
        self.lbl_base_dir.setStyleSheet(f"color: {base_color};")
        self.lbl_dup_status.setStyleSheet(f"color: {status_color};")
        self.btn_hide_confirmed.setStyleSheet(hide_ss)
        # Logo has transparent background — no swap needed for dark/light theme
        # Ensure table text is readable in both themes
        table_text = "#000000" if not is_dark else "#ffffff"
        self.table.setStyleSheet(
            self.table.styleSheet() +
            f" QTableWidget {{ color: {table_text}; }}")

    def reload_tag_groups(self, project=None):
        """Reload TAG_GROUPS from JSON for a project (or global default) and rebuild attr panels."""
        tf = attrs_mod.tags_file_for_project(project if project and project != "default" else None)
        attrs_mod.TAG_GROUPS = attrs_mod._load_tag_groups(tf)
        attrs_mod.TAGS = [item for group in attrs_mod.TAG_GROUPS.values() for item in group]
        attrs_mod.QUALITY_TAGS      = {k for k, _ in attrs_mod.TAG_GROUPS.get("Quality", [])}
        attrs_mod.SOURCE_TAGS       = {k for k, _ in attrs_mod.TAG_GROUPS.get("Source", [])} or {"comfyui", "a1111", "aix", "other_src"}
        attrs_mod.AUDIO_TAGS        = {k for k, _ in attrs_mod.TAG_GROUPS.get("Audio", [])}
        # Per-digit sub-tables
        attrs_mod.E_COLOR_TAGS      = {k for k, _ in attrs_mod.TAG_GROUPS.get("E_Color", [])}
        attrs_mod.HC_COLOR_TAGS     = {k for k, _ in attrs_mod.TAG_GROUPS.get("HC_Color", [])}
        attrs_mod.HC_STYLE_TAGS     = {k for k, _ in attrs_mod.TAG_GROUPS.get("HC_Style", [])}
        attrs_mod.HC_LENGTH_TAGS    = {k for k, _ in attrs_mod.TAG_GROUPS.get("HC_Length", [])}
        attrs_mod.FA_DIR_TAGS       = {k for k, _ in attrs_mod.TAG_GROUPS.get("FA_Dir", [])}
        attrs_mod.SK_TYPE_TAGS      = {k for k, _ in attrs_mod.TAG_GROUPS.get("SK_Type", [])}
        attrs_mod.B_SIZE_TAGS       = {k for k, _ in attrs_mod.TAG_GROUPS.get("B_Size", [])}
        attrs_mod.WH_HIP_TAGS       = {k for k, _ in attrs_mod.TAG_GROUPS.get("WH_Hip", [])}
        attrs_mod.PM_MOTION_TAGS    = {k for k, _ in attrs_mod.TAG_GROUPS.get("PM_Motion", [])}
        attrs_mod.CS_SHOT_TAGS      = {k for k, _ in attrs_mod.TAG_GROUPS.get("CS_Shot", [])}
        # Rebuild main attr panel
        if hasattr(self, 'attr_panel') and self.attr_panel:
            layout = self.attr_panel.parent().layout() if self.attr_panel.parent() else None
            if layout:
                idx = layout.indexOf(self.attr_panel)
                layout.removeWidget(self.attr_panel)
                self.attr_panel.deleteLater()
                self.attr_panel = self._build_attr_panel()
                self.attr_panel.hide()
                layout.insertWidget(idx, self.attr_panel)
        # Rebuild preview attr panel — swap the widget inside the existing scroll area
        pw = getattr(self.preview_handler, 'window', None)
        if pw and hasattr(pw, '_attr_scroll'):
            old_widget = pw._attr_scroll.widget()
            pw.attr_widget = pw._build_attr_panel()
            pw._attr_scroll.setWidget(pw.attr_widget)
            if old_widget:
                try:
                    old_widget.setParent(None)
                    old_widget.deleteLater()
                except RuntimeError:
                    pass  # already deleted by Qt
            if self.preview_handler.current_path:
                pw._refresh_attrs(self.preview_handler.current_path)
        # Reload soft canvas (AttrViewerWidget) so style changes (radio/taglist) take effect
        if pw and hasattr(pw, '_soft_canvas'):
            _cfg_path = attrs_mod.tags_file_for_project(project or getattr(self, 'current_project', None))
            pw._soft_canvas.reload(_cfg_path)
            pw._wire_canvas_bool_flags()
            if self.preview_handler.current_path:
                _entry = attrs_mod.get(self.attrs_data, self.preview_handler.current_path)
                pw._soft_canvas.load_file(self.preview_handler.current_path, _entry)
        # Also reload settings canvas if open
        _sv = getattr(self, '_settings_win', None)
        _cw = getattr(_sv, '_canvas_widget', None)
        if _cw:
            _cfg_path = attrs_mod.tags_file_for_project(project or getattr(self, 'current_project', None))
            _cw.reload(_cfg_path)

    # ── Inline attribute panel ───────────────────────────────────────────────

    def _build_attr_panel(self):
        panel = QWidget()
        panel.setStyleSheet("background-color: #2e2e2e;")
        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(6, 4, 6, 4)
        vbox.setSpacing(3)

        panel.setFont(QFont("", self.config.get("attr_font_size", 10)))

        # File info bar (resolution · ratio · fps · duration)
        self._inline_file_info = QLabel("")
        self._inline_file_info.setStyleSheet("color: #88aacc; font-size: 9pt;")
        self._inline_file_info.setWordWrap(False)
        vbox.addWidget(self._inline_file_info)

        # Note field (top)
        self._inline_note = QLineEdit()
        self._inline_note.setPlaceholderText(_t("Note… / ノート…"))
        self._inline_note.setStyleSheet(
            "background-color: #3a3a3a; color: #e0e0e0; border: 1px solid #555;")
        self._inline_note.editingFinished.connect(self._save_inline_attrs)
        vbox.addWidget(self._inline_note)

        # Quality + Resolution + Confirmed — only shown if defined in TAG_GROUPS
        r1 = QHBoxLayout()
        self._quality_combo = None
        self._res_combo = None
        _qual_pairs = attrs_mod.TAG_GROUPS.get("Quality", [])
        if _qual_pairs:
            lq = QLabel(_t("Q: / 品質：")); lq.setStyleSheet("color: #aaa;"); r1.addWidget(lq)
            self._quality_combo = QComboBox()
            self._quality_combo.wheelEvent = lambda e: e.ignore()
            self._quality_combo.addItem("—", "")
            for key, lbl in _qual_pairs:
                self._quality_combo.addItem(_t(lbl), key)
            self._quality_combo.setFixedWidth(70)
            self._quality_combo.currentIndexChanged.connect(self._save_inline_attrs)
            r1.addWidget(self._quality_combo)
        _res_pairs = attrs_mod.TAG_GROUPS.get("Resolution", [])
        if _res_pairs:
            lr = QLabel(_t("Res: / 解像：")); lr.setStyleSheet("color: #aaa;"); r1.addWidget(lr)
            self._res_combo = QComboBox()
            self._res_combo.wheelEvent = lambda e: e.ignore()
            self._res_combo.addItem("—", "")
            for key, lbl in _res_pairs:
                self._res_combo.addItem(_t(lbl), key)
            self._res_combo.setFixedWidth(80)
            self._res_combo.currentIndexChanged.connect(self._save_inline_attrs)
            r1.addWidget(self._res_combo)
        r1.addStretch()
        self._confirmed_cb = QCheckBox("≠")
        self._confirmed_cb.setToolTip(_t("Confirmed different / 異なると確認済み"))
        self._confirmed_cb.setStyleSheet("color: #e0e0e0;")
        self._confirmed_cb.toggled.connect(self._save_inline_attrs)
        r1.addWidget(self._confirmed_cb)
        vbox.addLayout(r1)

        # Variant checkboxes — 3 columns (only if Variant defined in TAG_GROUPS)
        self._inline_cbs = {}
        _variant_pairs = attrs_mod.TAG_GROUPS.get("Variant", [])
        if _variant_pairs:
            grid = QGridLayout()
            grid.setSpacing(2)
            grid.setContentsMargins(0, 0, 0, 0)
            for i, (key, label) in enumerate(_variant_pairs):
                cb = QCheckBox(_t(label))
                cb.setStyleSheet("color: #e0e0e0;")
                cb.toggled.connect(self._save_inline_attrs)
                self._inline_cbs[key] = cb
                grid.addWidget(cb, i // 3, i % 3)
            vbox.addLayout(grid)

        # Audio row — single-select combo (like Quality)
        self._inline_audio_rbs = {}   # unused but kept so refresh/save code is safe
        self._audio_combo = None
        _audio_pairs = attrs_mod.TAG_GROUPS.get("Audio", [])
        if _audio_pairs:
            r3 = QHBoxLayout()
            la = QLabel(_t("Audio: / 音声："))
            la.setStyleSheet("color: #aaa;")
            r3.addWidget(la)
            self._audio_combo = QComboBox()
            self._audio_combo.wheelEvent = lambda e: e.ignore()
            self._audio_combo.addItem("—", "")
            for key, label in _audio_pairs:
                self._audio_combo.addItem(_t(label), key)
            self._audio_combo.currentIndexChanged.connect(self._save_inline_attrs)
            r3.addWidget(self._audio_combo)
            r3.addStretch()
            vbox.addLayout(r3)

        self._inline_attr_path = None
        panel.setEnabled(False)
        return panel

    def _refresh_inline_attrs(self, path):
        self._inline_attr_path = path
        if not path:
            self.attr_panel.setEnabled(False)
            self._inline_file_info.setText("")
            return
        self.attr_panel.setEnabled(True)
        entry = attrs_mod.get(self.attrs_data, path)
        tags  = set(entry.get("tags", []))

        # File info — use cache if available, else extract in background
        meta = entry.get("meta", {})
        if meta:
            self._set_inline_file_info(meta)
        else:
            self._inline_file_info.setText("…")
            import threading
            _result = [None]
            _done   = threading.Event()
            def _extract(p=path):
                _result[0] = attrs_mod.extract_metadata(p)
                _done.set()
            def _poll(p=path):
                if _done.is_set():
                    if self._inline_attr_path == p and _result[0]:
                        self._set_inline_file_info(_result[0])
                else:
                    from PyQt6.QtCore import QTimer
                    QTimer.singleShot(100, _poll)
            threading.Thread(target=_extract, daemon=True).start()
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(100, _poll)

        for w in (self._quality_combo, self._res_combo, self._audio_combo,
                  self._confirmed_cb, self._inline_note):
            if w: w.blockSignals(True)
        for cb in self._inline_cbs.values():
            cb.blockSignals(True)

        self._inline_note.setText(entry.get("note", ""))
        if self._quality_combo:
            qual = next((k for k in attrs_mod.QUALITY_TAGS if k in tags), "")
            self._quality_combo.setCurrentIndex(max(0, self._quality_combo.findData(qual)))
        if self._res_combo:
            res  = next((k for k in attrs_mod.RESOLUTION_TAGS if k in tags), "")
            self._res_combo.setCurrentIndex(max(0, self._res_combo.findData(res)))
        if self._audio_combo:
            _audio_val = entry.get("audio", "")
            self._audio_combo.setCurrentIndex(max(0, self._audio_combo.findData(_audio_val)))
        for key, cb in self._inline_cbs.items():
            cb.setChecked(key in tags)
        self._confirmed_cb.setChecked(entry.get("confirmed", False))

        for w in (self._quality_combo, self._res_combo, self._audio_combo,
                  self._confirmed_cb, self._inline_note):
            if w: w.blockSignals(False)
        for cb in self._inline_cbs.values():
            cb.blockSignals(False)

    def _set_inline_file_info(self, meta):
        parts = [v for k, v in meta.items()
                 if k in ("Dimensions", "Ratio", "FPS", "Duration")]
        if meta.get("Seed"):
            parts.append(f"seed:{meta['Seed']}")
        self._inline_file_info.setText("  ·  ".join(parts))

    def _save_inline_attrs(self):
        path = self._inline_attr_path
        if not path:
            return
        tags = [k for k, cb in self._inline_cbs.items() if cb.isChecked()]
        # Audio: dedicated field (not stored in tags)
        _audio_val = (self._audio_combo.currentData() or "") if self._audio_combo else ""
        qual = self._quality_combo.currentData() if self._quality_combo else None
        if qual:
            tags.append(qual)
        res = self._res_combo.currentData() if self._res_combo else None
        if res:
            tags.append(res)
        confirmed = self._confirmed_cb.isChecked()
        note  = self._inline_note.text().strip()
        entry = attrs_mod.get(self.attrs_data, path)
        attrs_mod.set_file(self.attrs_data, path,
                           tags=tags,
                           note=note,
                           confirmed=confirmed,
                           project=entry.get("project", ""),
                           scene=entry.get("scene", ""),
                           audio=_audio_val,
                           speech=entry.get("speech", ""),
                           prompt=entry.get("prompt", ""),
                           neg_prompt=entry.get("neg_prompt", ""),
                           seed=entry.get("seed", ""),
                           person_id=entry.get("person_id", ""),
                           editable=entry.get("editable", True))
        attrs_mod.save(self.current_project, self.attrs_data)
        row = self._current_row()
        if row >= 0:
            self._refresh_attrs_indicator(row, path)
        self._highlight_unmarked_rows()
        if self.btn_hide_confirmed.isChecked():
            self._apply_confirmed_filter(True)

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("Left"),       self, self.on_left_key_press)
        QShortcut(QKeySequence("Right"),      self, self.on_right_key_press)
        QShortcut(QKeySequence("Ctrl+Down"),  self, lambda: self._move_to_neighbor(1))
        QShortcut(QKeySequence("Ctrl+Up"),    self, lambda: self._move_to_neighbor(-1))
        QShortcut(QKeySequence("F2"),         self, self.rename_file)
        QShortcut(QKeySequence("Delete"),     self, self.delete_file)
        QShortcut(QKeySequence("m"),          self, self.move_to_folder_manually)
        QShortcut(QKeySequence("M"),          self, self.move_to_folder_manually)
        QShortcut(QKeySequence("Home"),       self, self._go_to_first_row)
        QShortcut(QKeySequence("End"),        self, self._go_to_last_row)
        QShortcut(QKeySequence("Ctrl+Z"),       self, self._undo_last)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self, self._show_undo_history)

    def _go_to_first_row(self):
        for r in range(self.table.rowCount()):
            if not self.table.isRowHidden(r):
                self._select_row(r)
                return

    def _go_to_last_row(self):
        for r in range(self.table.rowCount() - 1, -1, -1):
            if not self.table.isRowHidden(r):
                self._select_row(r)
                return

    # ── Undo ─────────────────────────────────────────────────────────────────

    def _push_undo(self, actions):
        if not isinstance(actions, list):
            actions = [actions]
        if not actions:
            return

        if len(actions) == 1:
            action = actions[0]
            name = os.path.basename(action.get("orig_path") or action.get("old_path") or "")
            if action["type"] == "move":
                dest = os.path.basename(os.path.dirname(action["new_path"]))
                action["desc"] = f"Move  {name}  →  …/{dest}/ / 移動  {name}  →  …/{dest}/"
            else:
                action["desc"] = f"Delete  {name} / 削除  {name}"
            entry = action
        else:
            # Batch action
            count = len(actions)
            types = {a["type"] for a in actions}
            if "delete" in types and len(types) == 1:
                desc = f"Delete {count} files / {count}件を削除"
            elif "move" in types and len(types) == 1:
                desc = f"Move {count} files / {count}件を移動"
            else:
                desc = f"Batch action ({count}) / 複数操作（{count}件）"
            entry = {"type": "batch", "actions": actions, "desc": desc}

        self._undo_stack.append(entry)
        if len(self._undo_stack) > 100:
            self._undo_stack.pop(0)
        self._update_undo_btn()

    def _update_undo_btn(self):
        has = bool(self._undo_stack)
        self.btn_undo.setEnabled(has)
        self.btn_undo.setToolTip(_t(self._undo_stack[-1]["desc"]) if has else _t("Nothing to undo / 元に戻す操作なし"))

    def _undo_last(self):
        if not self._undo_stack:
            return
        action = self._undo_stack.pop()
        self._update_undo_btn()
        self._exec_undo(action)

    def _exec_undo(self, action):
        if action["type"] == "batch":
            for sub in action["actions"]:
                if sub["type"] == "move":
                    self._undo_move(sub, save_db=False, save_attrs=False, select=False)
                elif sub["type"] == "delete":
                    self._undo_delete(sub, save_db=False, save_attrs=False, select=False)
            if self.data:
                torch.save(self.data, os.path.join(attrs_mod.DATA_DIR, f"features_{self.current_project}.pt"))
            attrs_mod.save(self.current_project, self.attrs_data)
            self._rebuild_dup_display_data()
            self._save_dup_results()
        elif action["type"] == "move":
            self._undo_move(action)
        elif action["type"] == "delete":
            self._undo_delete(action)

    def _show_undo_history(self):
        if not self._undo_stack:
            QMessageBox.information(self, _t("Undo History / 履歴"), _t("Nothing to undo. / 元に戻す操作はありません。"))
            return
        from PyQt6.QtWidgets import QDialog, QListWidget, QVBoxLayout, QHBoxLayout, QPushButton
        dlg = QDialog(self)
        dlg.setWindowTitle(_t("Undo History / 履歴"))
        dlg.resize(480, 300)
        layout = QVBoxLayout(dlg)
        lw = QListWidget()
        # Show most recent at top
        for action in reversed(self._undo_stack):
            lw.addItem(_t(action["desc"]))
        layout.addWidget(lw)
        row_btns = QHBoxLayout()
        btn_undo_to = QPushButton(_t("Undo to selected / 選択まで戻す"))
        btn_cancel  = QPushButton(_t("Cancel / キャンセル"))
        row_btns.addWidget(btn_undo_to); row_btns.addWidget(btn_cancel)
        layout.addLayout(row_btns)
        btn_cancel.clicked.connect(dlg.reject)
        def _undo_to():
            idx = lw.currentRow()
            if idx < 0: return
            # idx=0 means most recent (top of stack), undo (idx+1) times
            for _ in range(idx + 1):
                if self._undo_stack:
                    action = self._undo_stack.pop()
                    self._update_undo_btn()
                    self._exec_undo(action)
            dlg.accept()
        btn_undo_to.clicked.connect(_undo_to)
        lw.itemDoubleClicked.connect(lambda: _undo_to())
        lw.setCurrentRow(0)
        dlg.exec()

    def _undo_move(self, action, save_db=True, save_attrs=True, select=True):
        old_path, new_path = action["old_path"], action["new_path"]
        if not os.path.exists(new_path):
            QMessageBox.warning(self, _t("Undo / 元に戻す"), _t(f"Cannot undo move: file not found at\n{new_path} / 元に戻せません：ファイルが見つかりません\n{new_path}"))
            return
        try:
            shutil.move(new_path, old_path)
        except Exception as e:
            QMessageBox.critical(self, _t("Undo Error / 元に戻すエラー"), str(e)); return
        # Restore query_path if the query file was moved
        if os.path.normpath(new_path) == os.path.normpath(self.query_path or ""):
            self.query_path = old_path
        # Restore attrs_data entry
        if new_path in self.attrs_data:
            self.attrs_data[old_path] = self.attrs_data.pop(new_path)
            if save_attrs: attrs_mod.save(self.current_project, self.attrs_data)
        elif old_path not in self.attrs_data:
            self.attrs_data[old_path] = {}

        import aisearch_attrs as _am
        _am.update_path_in_all_stores(new_path, old_path, self.current_project)

        if self.data and "paths" in self.data:
            for i, p in enumerate(self.data["paths"]):
                if os.path.normpath(p) == os.path.normpath(new_path):
                    self.data["paths"][i] = old_path
                    if save_db:
                        torch.save(self.data, os.path.join(attrs_mod.DATA_DIR, f"features_{self.current_project}.pt"))
                    break
        for r in range(self.table.rowCount()):
            if os.path.normpath(self.table.get_row_path(r) or "") == os.path.normpath(new_path):
                self.table.set_row_path(r, old_path)
                self.table.item(r, 2).setText(os.path.basename(old_path))
                self.table.item(r, 3).setText(self._mask_path(old_path))
                if select: self._select_row(r)
                break

    def _undo_delete(self, action, save_db=True, save_attrs=True, select=True):
        orig_path, trash_path = action["orig_path"], action["trash_path"]
        success, err = front_page.restore_from_trash(trash_path, orig_path)
        if not success:
            QMessageBox.critical(self, _t("Undo Error / 元に戻すエラー"), _t(f"Could not restore:\n{err} / 復元できません：\n{err}")); return
        # Restore attrs_data entry
        if "attrs" in action and action["attrs"] is not None:
            self.attrs_data[orig_path] = action["attrs"]
            if save_attrs: attrs_mod.save(self.current_project, self.attrs_data)
        elif orig_path not in self.attrs_data:
            self.attrs_data[orig_path] = {}

        if self.data is not None and "paths" in self.data:
            emb = action.get("emb")
            if emb is not None:
                self.data["paths"].append(orig_path)
                self.data["embeddings"] = torch.cat([self.data["embeddings"], emb.unsqueeze(0)])
                if save_db:
                    torch.save(self.data, os.path.join(attrs_mod.DATA_DIR, f"features_{self.current_project}.pt"))
        row = min(action["row"], self.table.rowCount())
        self.table.insertRow(row)
        score_item = QTableWidgetItem(action["score"])
        score_item.setFlags(score_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        score_item.setData(Qt.ItemDataRole.UserRole, orig_path)
        score_item.setData(Qt.ItemDataRole.UserRole + 1, action.get("sim_data"))
        self.table.setItem(row, 0, score_item)
        _date_item = DateItem(os.path.getmtime(orig_path)) if os.path.exists(orig_path) else DateItem(0)
        for col, (ItemCls, text) in enumerate(
                [(SizeItem, action["size"]), (QTableWidgetItem, action["name"]),
                 (QTableWidgetItem, action["masked_path"])], 1):
            item = ItemCls(text)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, col, item)
        self.table.setItem(row, 4, _date_item)
        _type_item = QTableWidgetItem(_file_type_str(orig_path))
        _type_item.setFlags(_type_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, 5, _type_item)
        bg = action.get("bg_color")
        if bg and bg.color().isValid():
            for col in range(self.table.columnCount()):
                if self.table.item(row, col):
                    self.table.item(row, col).setBackground(bg)
        if select: self._select_row(row)
        if save_db:
            self._rebuild_dup_display_data()
            self._save_dup_results()

    # ── Attributes ───────────────────────────────────────────────────────────

    def _refresh_attrs_indicator(self, row, path):
        entry = attrs_mod.get(self.attrs_data, path)
        item  = self.table.item(row, 2)
        if not item: return

        lines = []

        # File path (full, unmasked)
        lines.append(path)

        # Metadata: dimensions, ratio, duration, fps, audio, size
        meta = entry.get("meta", {}) if entry else {}
        meta_parts = []
        if "Dimensions" in meta:
            dim = meta["Dimensions"]
            ratio = meta.get("Ratio", "")
            meta_parts.append(f"{dim}  {ratio}".strip())
        if "Duration" in meta:
            meta_parts.append(meta["Duration"])
        if "FPS" in meta:
            meta_parts.append(meta["FPS"])
        if "Audio" in meta:
            meta_parts.append(f"🔊 {meta['Audio']}")
        elif meta.get("Dimensions"):  # video with no audio found
            is_vid = path.lower().endswith(('.mp4', '.mkv', '.mov', '.avi', '.webm'))
            if is_vid:
                meta_parts.append("🔇 no audio")
        if "File size" in meta:
            meta_parts.append(meta["File size"])
        if meta_parts:
            lines.append("  ·  ".join(meta_parts))

        # Tags
        if entry:
            tags    = entry.get("tags", [])
            project = entry.get("project", "")
            scene   = entry.get("scene", "")
            note    = entry.get("note", "")
            attr_parts = []
            if tags:    attr_parts.append(" · ".join(attrs_mod.tag_label(t) for t in tags))
            if project: attr_parts.append(f"🎬 {project}")
            if scene:   attr_parts.append(f"🎬 {scene}")
            if note:    attr_parts.append(note)
            if attr_parts:
                lines.append("  |  ".join(attr_parts))

        item.setToolTip("\n".join(lines))

    def _apply_confirmed_filter(self, hide):
        self._apply_row_visibility()

    def _apply_row_visibility(self):
        """Unified row visibility: respects collapse state + confirmed-hide filter."""
        header = self.table.horizontalHeaderItem(0)
        if not header or header.text() != "Group":
            return
        hide_confirmed = self.btn_hide_confirmed.isChecked()

        # Collect rows per group (in order)
        groups = {}
        order  = []
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if not item: continue
            label = item.data(Qt.ItemDataRole.UserRole + 2) or item.text().strip()
            if label not in groups:
                groups[label] = []
                order.append(label)
            groups[label].append(r)

        # "Hide confirmed" now hides COLLAPSED groups. Collapsing a group
        # (▶ arrow) is the user's way of saying "I've reviewed this — done".
        # Plus the picture/video filter toggles. Both are user-driven; no
        # auto-hide based on attrs entries.
        hidden_confirmed = set()
        _hide_pic = bool(getattr(self, "_chk_hide_pictures", None) and self._chk_hide_pictures.isChecked())
        _hide_vid = bool(getattr(self, "_chk_hide_videos", None) and self._chk_hide_videos.isChecked())
        if hide_confirmed or _hide_pic or _hide_vid:
            import aisearch_logic as _logic
            for label, rows in groups.items():
                _paths = [self.table.get_row_path(r) for r in rows]
                if hide_confirmed and label in self._collapsed_groups:
                    hidden_confirmed.add(label)
                    continue
                if _hide_pic and all(p.lower().endswith(_logic.EXT_IMG) for p in _paths if p):
                    hidden_confirmed.add(label)
                    continue
                if _hide_vid and all(p.lower().endswith(_logic.EXT_VID) for p in _paths if p):
                    hidden_confirmed.add(label)
                    continue

        # Track which currently-selected rows are about to be hidden so we
        # can drop them from the selection model — otherwise an action like
        # Delete would silently include rows the user can no longer see.
        _hidden_selected = set()
        _selected = {idx.row() for idx in self.table.selectionModel().selectedRows()}
        for label in order:
            rows = groups[label]
            if label in hidden_confirmed:
                for r in rows:
                    self.table.setRowHidden(r, True)
                    if r in _selected:
                        _hidden_selected.add(r)
            elif label in self._collapsed_groups:
                # Show only first (representative) row
                for i, r in enumerate(rows):
                    self.table.setRowHidden(r, i > 0)
                    if i > 0 and r in _selected:
                        _hidden_selected.add(r)
            else:
                for r in rows:
                    self.table.setRowHidden(r, False)
        # Drop now-hidden rows from the selection so subsequent actions
        # (Delete, Move To, etc.) only operate on rows the user can see.
        if _hidden_selected:
            from PyQt6.QtCore import QItemSelectionModel
            sel_model = self.table.selectionModel()
            for r in _hidden_selected:
                sel_model.select(self.table.model().index(r, 0),
                                 QItemSelectionModel.SelectionFlag.Rows
                                 | QItemSelectionModel.SelectionFlag.Deselect)
        self._recolor_dup_groups()
        self._highlight_unmarked_rows()

    def _on_group_cell_click(self, row, col):
        if col != 0: return
        header = self.table.horizontalHeaderItem(0)
        if not header or header.text() != "Group": return
        item = self.table.item(row, 0)
        if not item: return
        # Only toggle on the representative (▼/▶) row
        if not item.text().startswith(("▼", "▶")):
            return
        label = item.data(Qt.ItemDataRole.UserRole + 2)
        if not label: return
        if label in self._collapsed_groups:
            self._collapsed_groups.discard(label)
            item.setText(f"▼ {label}")
        else:
            self._collapsed_groups.add(label)
            item.setText(f"▶ {label}")
        self._apply_row_visibility()

    def _highlight_unmarked_rows(self):
        """In dup mode, orange text = no attributes set; auto contrast text = has attributes."""
        header = self.table.horizontalHeaderItem(0)
        if not header or header.text() != "Group":
            return
        for r in range(self.table.rowCount()):
            path   = self.table.get_row_path(r)
            marked = bool(attrs_mod.get(self.attrs_data, path))
            if not marked:
                color = QColor(self._unmarked_color)
            else:
                item0 = self.table.item(r, 0)
                bg = item0.background().color() if item0 else QColor()
                color = self._contrast_fg(bg)
            for col in range(self.table.columnCount()):
                item = self.table.item(r, col)
                if item:
                    item.setForeground(color)
            if marked:
                self._refresh_attrs_indicator(r, path)  # restore yellow on name col if needed

    def closeEvent(self, event):
        # If a DB scan is running, ask before closing — progress is
        # checkpointed on stop so the user can resume, but they should
        # know what's about to happen. Was: silently cancelled the
        # scan and exited. The dialogue also gives the user a chance
        # to back out if they hit X by accident mid-update.
        sw = getattr(self, "_settings_win", None)
        if sw is not None and getattr(sw, "_is_scanning", False):
            ans = QMessageBox.question(
                self,
                _t("Update Running / 更新中"),
                _t("A database update is in progress.\n"
                   "Closing will stop it (progress is saved — next "
                   "Update resumes).\n\nClose anyway?\n\n"
                   "データベース更新中です。\n"
                   "閉じると停止します（進行状況は保存されます）。\n\n"
                   "閉じますか？"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            # Request stop and exit. Was: pumped events for 800 ms via
            # processEvents() so CUDA tensors got released before the
            # daemon thread died — but processEvents() inside closeEvent
            # confuses Qt's close sequence and the window failed to
            # actually close. The daemon thread will die when the
            # process exits, the kernel reclaims its CUDA context, and
            # PyTorch's allocator pool gets freed by the OS.
            try:
                sw._unified_stop()
            except Exception:
                pass
        # Best-effort CUDA cache drop. Synchronous, fast.
        try:
            import torch as _torch
            if _torch.cuda.is_available():
                _torch.cuda.empty_cache()
        except Exception:
            pass

        if self.preview_handler.window:
            self.preview_handler.window.close()
        # FM is a separate top-level window — it's logically parented
        # to the main window but Qt won't auto-close it when we go
        # away. Without an explicit close, the FM stays visible after
        # the main app closes and keeps the process alive.
        fm_win = getattr(self, "_fm_win", None)
        if fm_win is not None:
            try:
                fm_win.close()
            except Exception:
                pass
        # Wrap the disk writes — if any of them raise we still want the
        # window to close, otherwise the user is stuck staring at an
        # un-closeable window.
        try:
            g = self.geometry()
            self.config["main_geometry"] = [g.x(), g.y(), g.width(), g.height()]
            self.config["col_widths"] = {str(col): self.table.columnWidth(col) for col in range(6)}
            cfg.save_config(self.config, getattr(self, "current_project", None))
        except Exception:
            pass
        try:
            self._save_dup_results()
        except Exception:
            pass
        if getattr(self, '_missing_save_dirty', False):
            try:
                self._flush_missing_removals()
            except Exception:
                pass
        event.accept()

    def _reset_project_memory(self):
        """Free per-project state that would otherwise leak across project
        switches: previous attrs_data (CLIP/FACE blobs), embedded-meta scan
        cache, extract_metadata cache, the AItan-block cache, the path index,
        and feedback. Force a GC sweep + drop torch/CUDA cache after."""
        # Old attrs_data: drop transient blobs explicitly so the dict's value
        # objects can be freed even if some other reference still holds the
        # outer dict briefly.
        _old_attrs = getattr(self, "attrs_data", None)
        if isinstance(_old_attrs, dict):
            for _e in _old_attrs.values():
                if isinstance(_e, dict):
                    for _k in list(_e.keys()):
                        if _k == "CLIP" or _k == "FACE" or _k.startswith("CLIP_"):
                            _e.pop(_k, None)
        # Drop bulky structures
        self.attrs_data = {}
        self._emb_meta_scanned = set()
        self._path_idx = {}
        if hasattr(self, "_extract_meta_cache"):
            self._extract_meta_cache = {}
        # Preview-window-side caches (if a preview is open)
        _pw = getattr(self, "preview_handler", None)
        if _pw is not None:
            _pwin = getattr(_pw, "window", None)
            if _pwin is not None:
                if hasattr(_pwin, "_aitan_block_cache"):
                    _pwin._aitan_block_cache = {}
                # Also clear any pending inspect/refresh debounce so they
                # don't fire against the now-gone old project.
                for _attr in ("_inspect_pending_args", "_refresh_pending_path"):
                    if hasattr(_pwin, _attr):
                        try: setattr(_pwin, _attr, None)
                        except Exception: pass
        # Drop the CLIP feature DB / embeddings tensor
        self.data = None
        # Drop dup-scan results and clear the table — without this the old
        # project's dup list (which can be 100s of MB after a low-threshold
        # scan) stays resident, and the gc/malloc_trim below has nothing to
        # release. Must happen BEFORE the trim, not after the new project
        # loads.
        self._dup_display_data = None
        self._dup_result_summary = ""
        try:
            self.table.setSortingEnabled(False)
            self.table.setRowCount(0)
            self.table.setSortingEnabled(True)
        except Exception:
            pass
        # Drop project-keyed module caches in aisearch_attrs — these survive
        # project switches because they're keyed by project name, so the old
        # project's face DB / corrections / rules stay resident forever.
        try:
            for _name in ("_faces_db_cache", "_corrections_cache",
                          "_fn_rules_cache", "_person_registry_cache"):
                _c = getattr(attrs_mod, _name, None)
                if isinstance(_c, dict):
                    _c.clear()
        except Exception:
            pass
        # Force collection
        try:
            import gc as _gc
            _gc.collect()
            try:
                import torch as _torch
                if _torch.cuda.is_available():
                    _torch.cuda.empty_cache()
            except Exception:
                pass
        except Exception:
            pass
        # Ask glibc to return freed arenas to the OS so the system monitor
        # actually shows a drop. Run on a daemon thread because malloc_trim
        # can take a noticeable time on a large heap, and we don't want to
        # freeze the GUI during a project switch.
        try:
            import threading as _thr, ctypes as _ct
            def _trim():
                try:
                    _ct.CDLL("libc.so.6").malloc_trim(0)
                except Exception:
                    pass
            _thr.Thread(target=_trim, daemon=True).start()
        except Exception:
            pass

    def _require_database(self, action_label="this action"):
        """Show a dialog telling the user they need a project + database
        for the requested action. Returns True if a database is available
        (caller should proceed), False if the user needs to set one up
        (caller should bail). Used by search / dup / any feature that
        needs the CLIP feature DB."""
        if self.data and self.data.get("paths"):
            return True
        QMessageBox.information(
            self,
            _t("No database / データベースなし"),
            _t(f"{action_label} needs a project with a built database.\n\n"
               f"Open Settings → Database, register a project with at least one "
               f"directory, then click Scan to build the database.\n\n"
               f"設定 → データベースでプロジェクトを登録し、スキャンしてください。"),
        )
        return False

    def _ai_mode(self):
        """Return current AI inspect mode: 'none' | 'face' | 'clip' | 'both'.
        Derived from face_inspect_mode + clip_inspect_mode (each 'never' or
        'when_empty'). Falls back to legacy clip_inspect_mode for unmigrated
        configs: clip_inspect_mode == 'never' → 'none', else → 'both'."""
        _f = self.config.get("face_inspect_mode")
        _c = self.config.get("clip_inspect_mode", "when_empty")
        # First-time migration from single-flag legacy config
        if _f is None:
            _f = "never" if _c == "never" else "when_empty"
            self.config["face_inspect_mode"] = _f
        face_on = (_f != "never")
        clip_on = (_c != "never")
        if face_on and clip_on:  return "both"
        if face_on:              return "face"
        if clip_on:              return "clip"
        return "none"

    def _set_ai_mode(self, mode):
        """Apply 'none' | 'face' | 'clip' | 'both' to face/clip flags."""
        face_on = mode in ("face", "both")
        clip_on = mode in ("clip", "both")
        self.config["face_inspect_mode"] = "when_empty" if face_on else "never"
        self.config["clip_inspect_mode"] = "when_empty" if clip_on else "never"
        cfg.save_config(self.config, getattr(self, "current_project", None))
        self._refresh_logo_pixmap()
        # Sync the settings dialog combo if open (legacy CLIP-only dropdown)
        _sw = getattr(self, "_settings_win", None)
        if _sw is not None:
            _cb = getattr(_sw, "_clip_inspect_mode_cb", None)
            if _cb is not None:
                _i = _cb.findData(self.config["clip_inspect_mode"])
                if _i >= 0:
                    _cb.blockSignals(True)
                    _cb.setCurrentIndex(_i)
                    _cb.blockSignals(False)

    def _refresh_logo_pixmap(self):
        """Show the pixmap matching the current AI mode."""
        _m = self._ai_mode()
        _px = {
            "none": self._logo_pix_none,
            "face": self._logo_pix_face,
            "clip": self._logo_pix_clip,
            "both": self._logo_pix_both,
        }.get(_m, self._logo_pix_both)
        if _px is not None:
            self._lbl_logo.setPixmap(_px)
        # Tooltip reflects what's currently active so the user can tell at a
        # glance which subsystem(s) are running.
        _tt = {
            "none": _t("AI off (click to cycle: face → clip → both → off)"),
            "face": _t("Face only — click for CLIP only"),
            "clip": _t("CLIP only — click for both"),
            "both": _t("Face + CLIP — click for off"),
        }.get(_m, "")
        if _tt:
            self._lbl_logo.setToolTip(_tt)

    def _toggle_inspect_mode(self):
        """Logo click — rotate through none → face → clip → both → none."""
        _next = {"none": "face", "face": "clip",
                 "clip": "both", "both": "none"}
        self._set_ai_mode(_next.get(self._ai_mode(), "both"))

    def _open_file_manager(self, initial_dir=None):
        """Open (or focus + navigate) the File Manager window. Single
        instance per app session — re-open just brings it forward and
        re-points it at initial_dir."""
        win = getattr(self, '_fm_win', None)
        if win is None:
            self._fm_win = FileManagerWindow(self, initial_dir)
        else:
            if initial_dir:
                self._fm_win.navigate(initial_dir)
        self._fm_win.show()
        self._fm_win.raise_()
        self._fm_win.activateWindow()

    def _refresh_persons_tab_if_open(self):
        """Tell the Settings → Persons tab to rebuild its card grid so
        rep pic / BASE / sample-add operations reflect immediately."""
        sw = getattr(self, "_settings_win", None)
        if sw is not None and hasattr(sw, "_rebuild_person_groups"):
            try:
                sw._rebuild_person_groups()
            except Exception:
                pass

    def _set_rep_pic(self, pid, path):
        """Set `path` as the representative picture for person `pid` —
        updates faces_<project>.json's source_path field. Used from
        the main-table right-click menu when an old rep pic is no
        longer correct."""
        proj = getattr(self, "current_project", None)
        if not proj or not pid or not path or not os.path.exists(path):
            return
        try:
            db = attrs_mod.load_faces_db(proj)
            faces = db.get("faces", {})
            if pid not in faces:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.warning(self, _t("Set rep / 代表画像設定"),
                    _t(f"Person ID {pid} not found in faces DB.\n"
                       f"Run face detection on this file first.\n\n"
                       f"人物ID {pid} がfaces DBに見つかりません。\n"
                       f"このファイルで顔検出を先に実行してください。"))
                return
            faces[pid]["source_path"] = os.path.abspath(path)
            attrs_mod.save_faces_db(proj, db)
            self._refresh_persons_tab_if_open()
            self.statusBar().showMessage(
                _t(f"Rep pic for {pid} updated. / {pid} の代表画像を更新しました。"),
                4000)
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self, _t("Set rep / 代表画像設定"),
                f"Could not update rep pic:\n{e}")

    def _set_base_face(self, pid, path):
        """Wipe person `pid`'s face embeddings list and replace it with
        the single embedding extracted from `path`. Also updates
        source_path to `path`. Use when the person has accumulated
        bad / mismatched embeddings (P005 with random faces in it) —
        this discards every prior sample and pins the matcher to one
        canonical face. Non-destructive: previous BASE survives as a
        sample in the pool."""
        proj = getattr(self, "current_project", None)
        if not proj or not pid or not path or not os.path.exists(path):
            return
        from PyQt6.QtWidgets import QMessageBox
        try:
            import face_recognition
            if path.lower().endswith(('.mp4', '.mkv', '.mov', '.avi', '.webm')):
                import cv2
                cap = cv2.VideoCapture(path)
                ret, frame = cap.read()
                cap.release()
                if not ret or frame is None:
                    QMessageBox.warning(self, _t("Set BASE face / 基準顔の設定"),
                        _t("Could not decode the first frame of this video. / "
                           "この動画の最初のフレームを読み込めませんでした。"))
                    return
                img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            else:
                img = face_recognition.load_image_file(path)
            encs = face_recognition.face_encodings(img)
            if not encs:
                QMessageBox.warning(self, _t("Set BASE face / 基準顔の設定"),
                    _t("No face detected in this file. / "
                       "このファイルから顔を検出できませんでした。"))
                return
            enc = encs[0]
            db = attrs_mod.load_faces_db(proj)
            faces = db.get("faces", {})
            faces.setdefault(pid, {"embeddings": [], "source_path": ""})
            # Non-destructive: prepend the new BASE encoding to the
            # existing pool (so the previous BASE survives as a sample),
            # dedupe exact matches, and cap at 20.
            old_embs = list(faces[pid].get("embeddings", []))
            new_enc_list = enc.tolist()
            old_embs = [e for e in old_embs if e != new_enc_list]
            new_pool = [new_enc_list] + old_embs
            if len(new_pool) > 20:
                new_pool = new_pool[:20]
            faces[pid]["embeddings"]  = new_pool
            faces[pid]["source_path"] = os.path.abspath(path)
            attrs_mod.save_faces_db(proj, db)
            # Drop the in-memory cache so the next match call sees the
            # new single-embedding state.
            try:
                attrs_mod._faces_db_cache.pop(proj, None)
            except Exception:
                pass
            self._refresh_persons_tab_if_open()
            self.statusBar().showMessage(
                _t(f"BASE face for {pid} updated ({len(new_pool)} samples). / "
                   f"{pid} の基準顔を更新しました（{len(new_pool)} サンプル）。"),
                5000)
        except Exception as e:
            QMessageBox.critical(self, _t("Set BASE face / 基準顔の設定"),
                f"{e}")

    def _add_face_sample(self, pid, path):
        """Append the face encoding extracted from `path` to person
        `pid`'s embeddings list. Use to teach the matcher a NEW
        valid shot of an existing person without wiping the rest of
        the pool. Source_path stays whatever it was."""
        proj = getattr(self, "current_project", None)
        if not proj or not pid or not path or not os.path.exists(path):
            return
        from PyQt6.QtWidgets import QMessageBox
        try:
            import face_recognition
            if path.lower().endswith(('.mp4', '.mkv', '.mov', '.avi', '.webm')):
                import cv2
                cap = cv2.VideoCapture(path)
                ret, frame = cap.read()
                cap.release()
                if not ret or frame is None:
                    QMessageBox.warning(self, _t("Add face / 顔追加"),
                        _t("Could not decode the first frame. / "
                           "最初のフレームを読み込めませんでした。"))
                    return
                img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            else:
                img = face_recognition.load_image_file(path)
            encs = face_recognition.face_encodings(img)
            if not encs:
                QMessageBox.warning(self, _t("Add face / 顔追加"),
                    _t("No face detected in this file. / "
                       "このファイルから顔を検出できませんでした。"))
                return
            enc = encs[0]
            db = attrs_mod.load_faces_db(proj)
            faces = db.get("faces", {})
            faces.setdefault(pid, {"embeddings": [], "source_path": ""})
            samples = list(faces[pid].get("embeddings", []))
            samples.append(enc.tolist())
            # Keep at most 20 samples — same cap as the auto-detect path
            if len(samples) > 20:
                samples = samples[-20:]
            faces[pid]["embeddings"] = samples
            attrs_mod.save_faces_db(proj, db)
            try:
                attrs_mod._faces_db_cache.pop(proj, None)
            except Exception:
                pass
            self._refresh_persons_tab_if_open()
            self.statusBar().showMessage(
                _t(f"Added to {pid} (now {len(samples)} samples). / "
                   f"{pid} に追加しました（現在 {len(samples)} サンプル）。"),
                4000)
        except Exception as e:
            QMessageBox.critical(self, _t("Add face / 顔追加"), f"{e}")

    def _assign_new_person(self, path):
        """Allocate the next free person ID, seed the faces DB with the
        face encoding from `path`, and tag the file with that pid.
        Use when an existing pid is wrong and the file represents
        someone new (or someone we've never registered)."""
        proj = getattr(self, "current_project", None)
        if not proj or not path or not os.path.exists(path):
            return
        from PyQt6.QtWidgets import QMessageBox
        try:
            import face_recognition
            if path.lower().endswith(('.mp4', '.mkv', '.mov', '.avi', '.webm')):
                import cv2
                cap = cv2.VideoCapture(path)
                ret, frame = cap.read()
                cap.release()
                if not ret or frame is None:
                    QMessageBox.warning(self, _t("New person / 新規人物"),
                        _t("Could not decode the first frame. / "
                           "最初のフレームを読み込めませんでした。"))
                    return
                img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            else:
                img = face_recognition.load_image_file(path)
            encs = face_recognition.face_encodings(img)
            if not encs:
                QMessageBox.warning(self, _t("New person / 新規人物"),
                    _t("No face detected in this file. / "
                       "このファイルから顔を検出できませんでした。"))
                return
            enc = encs[0]
            db = attrs_mod.load_faces_db(proj)
            faces = db.get("faces", {})
            # Pick the next free pid — start at db.next_id and skip
            # over any already-used keys.
            n = max(int(db.get("next_id", 1)), 1)
            while n <= 0xfff and format(n, "03x") in faces:
                n += 1
            if n > 0xfff:
                QMessageBox.critical(self, _t("New person / 新規人物"),
                    _t("Person ID space exhausted (0xfff used). / "
                       "人物IDが枯渇しました（0xfff まで使用済み）。"))
                return
            new_id = format(n, "03x")
            faces[new_id] = {
                "embeddings": [enc.tolist()],
                "source_path": os.path.abspath(path),
            }
            db["next_id"] = n + 1
            attrs_mod.save_faces_db(proj, db)
            try:
                attrs_mod._faces_db_cache.pop(proj, None)
            except Exception:
                pass
            # Tag the file with the new pid
            entry = self.attrs_data.setdefault(path, {})
            entry["person_id"] = new_id
            attrs_mod.save(proj, self.attrs_data)
            # Refresh preview if it's open on this file
            try:
                pw = getattr(self.preview_handler, "window", None)
                if pw and getattr(self.preview_handler, "current_path", None) == path:
                    pw._refresh_attrs_inner(path)
            except Exception:
                pass
            self._refresh_persons_tab_if_open()
            self.statusBar().showMessage(
                _t(f"Assigned new person P{new_id}. / "
                   f"新規人物 P{new_id} を割当しました。"),
                5000)
        except Exception as e:
            QMessageBox.critical(self, _t("New person / 新規人物"), f"{e}")

    def _dismantle_face_assignment(self, path):
        """Wrong-face cleanup: drop this file's face sample from the
        currently-tagged pid in the faces DB, and clear person_id from
        the file's attrs entry. If the pid ends up with zero samples
        the pid is deleted entirely. Used from right-click → "Dismantle
        face data" when the user spots a misassignment.

        Does NOT rename the file — that's a separate decision (P-prefix
        in the filename can be removed via the rename dialog or a
        manual rename)."""
        proj = getattr(self, "current_project", None)
        if not proj or not path or not os.path.exists(path):
            return
        from PyQt6.QtWidgets import QMessageBox
        entry = self.attrs_data.get(path, {}) or {}
        pid = (entry.get("person_id") or "").strip()
        if not pid:
            QMessageBox.information(self, _t("Dismantle / 解除"),
                _t("This file has no person_id assigned. / "
                   "このファイルには人物IDが割当されていません。"))
            return
        ans = QMessageBox.question(
            self, _t("Dismantle face data / 顔データ解除"),
            _t(f"Remove this file's contribution to P{pid} from the "
               f"faces DB and clear its person_id?\n\n"
               f"P{pid} に対するこのファイルの寄与を顔DBから削除し、"
               f"person_id を解除しますか？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        if ans != QMessageBox.StandardButton.Yes:
            return
        result = attrs_mod.dismantle_face_assignment(path, proj, pid)
        if result is None:
            QMessageBox.warning(self, _t("Dismantle / 解除"),
                _t("Could not extract a face from this file. "
                   "person_id was cleared anyway. / "
                   "このファイルから顔を抽出できませんでした。"
                   "person_id は解除しました。"))
        # Clear person_id in attrs_data regardless — the user has
        # asked to disassociate this file from any pid.
        if path in self.attrs_data:
            self.attrs_data[path]["person_id"] = ""
            attrs_mod.save(proj, self.attrs_data)
        # Refresh preview if it's open on this file
        try:
            pw = getattr(self.preview_handler, "window", None)
            if pw and getattr(self.preview_handler, "current_path", None) == path:
                pw._refresh_attrs_inner(path)
        except Exception:
            pass
        self._refresh_persons_tab_if_open()
        if result:
            msg_parts = []
            if result["samples_removed"]:
                msg_parts.append(f"removed {result['samples_removed']} sample")
            if result["pid_deleted"]:
                msg_parts.append(f"P{pid} had no samples left — pid deleted")
            elif result["source_path_cleared"]:
                msg_parts.append(f"P{pid} rep pic cleared")
            self.statusBar().showMessage(
                _t(f"Dismantled: {', '.join(msg_parts) or 'attrs only'} / "
                   f"解除: {', '.join(msg_parts) or 'attrs のみ'}"),
                5000)

    def _open_fm_for_current_row(self):
        """Right-click → File Manager: open the FM at the parent folder
        of the currently-selected row AND highlight the file in the
        tree, so the user can see which folder owns the file. Falls
        back to query_path / base_dirs / home when nothing is selected."""
        row = self._current_row()
        path = self.table.get_row_path(row) if row >= 0 else None
        if not path or not os.path.exists(path):
            path = self.query_path
        if path and os.path.exists(path):
            parent = os.path.dirname(os.path.abspath(path))
            self._open_file_manager(parent)
            # Now highlight the file inside the tree.
            try:
                self._fm_win.navigate_to_file(os.path.abspath(path))
            except Exception:
                pass
        elif self.base_dirs:
            self._open_file_manager(self.base_dirs[0])
        else:
            self._open_file_manager(os.path.expanduser("~"))

    def _open_settings(self, tab=0):
        if not hasattr(self, '_settings_win') or self._settings_win is None:
            self._settings_win = SettingsView(self, self, tab)
        else:
            # Refresh filename rules, attr sections, metamap from disk each time settings opens
            _reload_fn = getattr(self._settings_win, '_reload_fn_rules', None)
            if _reload_fn:
                _reload_fn()
            _reload_meta = getattr(self._settings_win, '_reload_meta_rules', None)
            if _reload_meta:
                _reload_meta()
        self._settings_win.show()
        self._settings_win.raise_()
        self._settings_win.activateWindow()
        if tab:
            self._settings_win.tabs.setCurrentIndex(tab)

    # ── Project management ───────────────────────────────────────────────────

    def set_project(self, name):
        # Save current project's config before switching
        cfg.save_config(self.config, self.current_project)
        # Cancel any running dup scan and reset its UI state — otherwise the
        # "♊ Duplicates" button stays stuck on "Searching..." / "Hashing..."
        # if the user switches mid-scan.
        if getattr(self, "_search_running", False):
            try: self._search_cancel[0] = True
            except Exception: pass
            self._search_running = False
        self._dup_cancel = True
        self._dup_paused = False
        if hasattr(self, "_dup_poll_timer"):
            self._dup_poll_timer.stop()
        self.btn_find_dups.setText(_t("♊ Duplicates / ♊ 重複"))
        self.btn_find_dups.setEnabled(True)
        if hasattr(self, "btn_scan"):
            try: self.btn_scan.clicked.disconnect()
            except Exception: pass
            self.btn_scan.clicked.connect(self._force_rescan)
            self.btn_scan.setText(_t("⟳ Scan / ⟳ スキャン"))
            self.btn_scan.show()
        if hasattr(self, "spin_threshold"):
            self.spin_threshold.setEnabled(True)   # un-hide if Stop hid it before switch
        if hasattr(self, "btn_dup_resume"): self.btn_dup_resume.hide()
        if hasattr(self, "btn_dup_rescan"): self.btn_dup_rescan.hide()
        if hasattr(self, "search_status_label"): self.search_status_label.hide()
        if hasattr(self, "search_progress"): self.search_progress.hide()
        # Ensure per-project filename rules file exists so auto_rename doesn't
        # bleed from the global fallback file into newly-created projects.
        # Copy the global default so the new project inherits existing rules.
        if name and name != "default":
            _proj_rules_path = attrs_mod.filename_rules_save_path_for_project(name)
            if not os.path.exists(_proj_rules_path):
                _default_cfg = attrs_mod.load_filename_config(None)
                attrs_mod.save_filename_config(dict(_default_cfg), name)
        # Reset preview window so it recreates fresh (same as app restart).
        # This avoids stale splitter/attr state from the old project.
        # Only do this on an actual project switch — set_project is also
        # used as a "reload self.data from disk" hook (e.g. after a scan
        # checkpoint), and there's no reason to nuke the preview then.
        _ph = getattr(self, 'preview_handler', None)
        _project_changed = (name != getattr(self, 'current_project', None))
        if _project_changed and _ph and _ph.window:
            # Stop pending save timers BEFORE switching project data — otherwise
            # the 800ms debounce fires after attrs_data is replaced with the new
            # project's data, writing the old file's path into the wrong project.
            _timer = getattr(_ph.window, '_text_save_timer', None)
            if _timer:
                _timer.stop()
            _g = _ph.window.geometry()
            self.config["preview_geometry"] = [_g.x(), _g.y(), _g.width(), _g.height()]
            _ph.window.hide()
            _ph.window.deleteLater()
            _ph.window = None
            _ph._cached_pixmap      = None
            _ph._cached_pixmap_path = None
            _ph.zoom_factor         = 1.0
        self.current_project = name
        self._emb_meta_scanned = set()  # clear per-file embedded-meta scan cache on project switch
        # Load project-specific config (falls back to global default)
        self.config = cfg.load_config(name)
        self.config["last_project"] = name
        cfg.save_config(self.config, name)
        # Re-apply header theme so the new project's bg color shows up on
        # the thumbnail and drop zone immediately after switching.
        try:
            self._apply_header_theme()
        except Exception:
            pass
        # Also update last_project in global config so startup knows which project
        _g = cfg.load_config()
        _g["last_project"] = name
        cfg.save_config(_g)
        self.lbl_project.setText(name)
        self.reload_tag_groups(name)
        self.load_db()
        # Keep DB settings scan label in sync
        _sw = getattr(self, '_settings_win', None)
        if _sw and hasattr(_sw, '_update_scan_project_label'):
            _sw._update_scan_project_label()
        if _sw and hasattr(_sw, '_refresh_person_tab'):
            _sw._refresh_person_tab(name)
        # Sync auto_rename UI from filename config (per-project, no global fallback)
        _ar = attrs_mod.load_filename_config(name).get("auto_rename", False)
        if _sw:
            for _attr in ("chk_rename_on_scan", "check_auto_rename"):
                _chk = getattr(_sw, _attr, None)
                if _chk and _chk.isChecked() != _ar:
                    _chk.blockSignals(True)
                    _chk.setChecked(_ar)
                    _chk.blockSignals(False)
            # Sync filename rules tab combo to current project + reload rules
            _fn_cb = getattr(_sw, '_fn_proj_cb', None)
            if _fn_cb:
                _idx = _fn_cb.findText(name)
                if _idx >= 0:
                    _fn_cb.setCurrentIndex(_idx)
            # Reload rules + re-sync auto_rename + enable/disable container
            _reload_fn = getattr(_sw, '_reload_fn_rules', None)
            if _reload_fn:
                _reload_fn()
            # Sync attributes tab combo to current project + reload sections
            _attr_cb = getattr(_sw, '_attr_proj_cb', None)
            if _attr_cb:
                _idx = _attr_cb.findText(name)
                if _idx >= 0:
                    _attr_cb.setCurrentIndex(_idx)
            _reload_attr = getattr(_sw, '_reload_attr_sections', None)
            if _reload_attr:
                _reload_attr()
            # Sync metamap tab combo to current project + reload rules
            _meta_cb = getattr(_sw, '_meta_proj_cb', None)
            if _meta_cb:
                _idx = _meta_cb.findText(name)
                if _idx >= 0:
                    _meta_cb.setCurrentIndex(_idx)
            _reload_meta = getattr(_sw, '_reload_meta_rules', None)
            if _reload_meta:
                _reload_meta()
        _pw = getattr(getattr(self, "preview_handler", None), "window", None)
        if _pw:
            _chk = getattr(_pw, "_chk_auto_rename", None)
            if _chk and _chk.isChecked() != _ar:
                _chk.blockSignals(True)
                _chk.setChecked(_ar)
                _chk.blockSignals(False)

    def load_db(self):
        name = self.current_project.strip()
        # Drop the previous project's in-memory state before loading the new
        # one — otherwise CLIP/face debug blobs, metadata caches, and
        # attribute panel widgets accumulate across switches and balloon RSS.
        try:
            self._reset_project_memory()
        except Exception:
            pass
        self.data, _ = logic.load_db_logic(name)
        self.base_dirs = []
        self.feedback_data = feedback.load(name)
        self.attrs_data        = attrs_mod.load(name)
        self._emb_meta_scanned = set()
        # Build O(1) path→index lookup with realpath so symlinks don't cause misses
        if self.data and "paths" in self.data:
            self._path_idx = {os.path.realpath(p): i for i, p in enumerate(self.data["paths"])}
        else:
            self._path_idx = {}
        self._warmup_search_executor()

        if self.data:
            saved_dirs = self.data.get("base_dirs", [])
            if saved_dirs:
                self.base_dirs = [d.rstrip(os.sep) for d in saved_dirs if d]
            elif self.data.get("paths"):
                abs_paths = [os.path.abspath(p) for p in self.data["paths"] if p]
                try:
                    common = os.path.commonpath(abs_paths)
                    if os.path.isdir(common) and len(common) > 5:
                        self.base_dirs = [common]
                except (ValueError, IndexError):
                    pass

        # One base dir per line so long lists don't truncate or stretch the
        # header. The label has setWordWrap(True) so it lays out vertically.
        if self.base_dirs:
            label = "\n".join(self.base_dirs)
            self._base_dir_label_value = label
            self.lbl_base_dir.setText(label)
        else:
            self._base_dir_label_value = ""
            self.lbl_base_dir.setText("")
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.table.setSortingEnabled(True)
        self._dup_display_data = None
        self.config["last_mode"] = "search"
        self._update_mode_buttons("search")
        self.table.setFocus()
        # Restore previous search so the current file stays visible at the top
        if self.query_path and os.path.exists(self.query_path):
            QTimer.singleShot(0, lambda: self.run_search(self.query_path))
        self._apply_watch_dirs()

    def _warmup_search_executor(self):
        """Create the persistent search thread and warm up CLIP/CUDA in it immediately."""
        from concurrent.futures import ThreadPoolExecutor
        # Recreate executor on each project load so the thread is always fresh
        if hasattr(self, '_search_executor') and self._search_executor is not None:
            self._search_executor.shutdown(wait=False)
        self._search_executor = ThreadPoolExecutor(max_workers=1)
        def _warmup():
            try:
                import numpy as np
                from PIL import Image as _PIL
                img = _PIL.fromarray(np.zeros((32, 32, 3), dtype=np.uint8))
                logic.model.encode(img, convert_to_tensor=True)
            except Exception:
                pass
        self._search_executor.submit(_warmup)

    def _apply_watch_dirs(self):
        """Connect watcher to all configured watch_dirs. _scan_new_files handles
        project-scope filtering so only project files get indexed."""
        # watch_dirs is a global setting — always read from global config
        import aisearch_config as _cfg_mod
        _global_cfg = _cfg_mod.load_config()
        watch_dirs = [d for d in _global_cfg.get("watch_dirs", []) if os.path.isdir(d)]
        if self._watcher:
            self._watcher.directoryChanged.disconnect()
            self._watcher.deleteLater()
            self._watcher = None
        if not watch_dirs:
            if getattr(self, '_watch_debounce', None):
                self._watch_debounce.stop()
            if getattr(self, '_watch_fallback', None):
                self._watch_fallback.stop()
            return
        self._watcher = QFileSystemWatcher(watch_dirs, self)
        self._watcher.directoryChanged.connect(self._on_dir_changed)
        # Debounce timer — restarted on each directory change; fires once after quiet period
        if not getattr(self, '_watch_debounce', None):
            self._watch_debounce = QTimer(self)
            self._watch_debounce.setSingleShot(True)
            self._watch_debounce.timeout.connect(self._scan_new_files)
        # Periodic fallback: re-scan every 30s in case watcher missed an event
        if not getattr(self, '_watch_fallback', None):
            self._watch_fallback = QTimer(self)
            self._watch_fallback.timeout.connect(self._scan_new_files)
        self._watch_fallback.start(30_000)

    def _on_dir_changed(self, _path):
        # Restart the debounce timer — rapid changes collapse into one scan
        self._watch_debounce.start(2000)

    def _scan_new_files(self):
        """Add new files from watch_dirs to the current project DB."""
        try:
            self._do_scan_new_files()
        except Exception:
            pass  # never let an exception permanently kill watch-dir detection


    def _go_back(self):
        """Go back: enter Browse mode on the current file's directory."""
        self._enter_browse_mode()


    def _do_scan_new_files(self):
        if not self.data:
            # Fresh project — initialize empty data structure so watch can work
            self.data = {"paths": [], "embeddings": __import__("torch").empty((0, logic.EMBEDDING_DIM)).to(logic.device)}
        # Skip if a settings scan/rename is in progress. But first: if a
        # truly new file has landed in a watch_dir, ask the running scan
        # to stop. Long Updates would otherwise hold the new file
        # unindexed for hours; the scan saves a checkpoint on stop, so
        # progress isn't lost, and the next Update click resumes.
        if getattr(self, '_watcher_paused', False):
            try:
                import aisearch_config as _cfg_mod
                _wdirs = [d for d in _cfg_mod.load_config().get("watch_dirs", []) if os.path.isdir(d)]
                _exts  = logic.EXT_IMG + logic.EXT_VID
                _known = {os.path.normpath(p) for p in self.data.get("paths", [])}
                _has_new = False
                for _d in _wdirs:
                    for _f in os.listdir(_d):
                        if _f.lower().endswith(_exts):
                            _fp = os.path.normpath(os.path.join(_d, _f))
                            if _fp not in _known:
                                _has_new = True; break
                    if _has_new: break
                if _has_new:
                    _sw = getattr(self, '_settings_win', None)
                    if _sw is not None and getattr(_sw, '_is_scanning', False):
                        # Mark for auto-resume so _toggle_ui(False) restarts
                        # the Update once the watcher has indexed the new
                        # file. From the user's POV the scan just pauses.
                        self._scan_paused_by_watcher = True
                        _sw._unified_stop()
            except Exception:
                pass
            return
        # watch_dirs is global — read from global config regardless of current project
        import aisearch_config as _cfg_mod
        _global_cfg = _cfg_mod.load_config()
        scan_dirs = [d for d in _global_cfg.get("watch_dirs", []) if os.path.isdir(d)]
        if not scan_dirs: return

        paths = self.data.get("paths", [])
        exts  = logic.EXT_IMG + logic.EXT_VID

        # ── Detect missing files ──────────────────────────────────────────────
        missing_idx = [i for i, p in enumerate(paths) if not os.path.exists(p)]

        # ── Add new files from watch dirs ─────────────────────────────────────
        known = set(os.path.normpath(p) for p in paths)
        new_files = []
        for d in scan_dirs:
            if not os.path.isdir(d): continue
            for f in os.listdir(d):
                if f.lower().endswith(exts):
                    fp = os.path.normpath(os.path.join(d, f))
                    if fp not in known:
                        new_files.append(fp)

        # ── Match moved files by filename before removing them ────────────────
        # If a missing file has a unique basename match among new files, treat
        # it as a move: reuse the embedding, transfer attrs, skip re-extraction.
        if missing_idx and new_files:
            import aisearch_attrs as _am_tmp
            new_by_name = {}
            for fp in new_files:
                new_by_name.setdefault(os.path.basename(fp), []).append(fp)

            moved_attrs_dirty = False
            handled_missing = set()
            handled_new = set()
            for i in missing_idx:
                old_path = paths[i]
                candidates = new_by_name.get(os.path.basename(old_path), [])
                if len(candidates) == 1:
                    new_path = candidates[0]
                    self.data["paths"][i] = new_path
                    if old_path in self.attrs_data:
                        self.attrs_data[new_path] = self.attrs_data.pop(old_path)
                        moved_attrs_dirty = True
                    handled_missing.add(i)
                    handled_new.add(new_path)

            if handled_missing:
                missing_idx = [i for i in missing_idx if i not in handled_missing]
                new_files   = [fp for fp in new_files if fp not in handled_new]
                paths = self.data["paths"]
                known = set(os.path.normpath(p) for p in paths)
                if moved_attrs_dirty:
                    _am_tmp.save(self.current_project, self.attrs_data)
                torch.save(self.data,
                           os.path.join(_am_tmp.DATA_DIR,
                                        f"features_{self.current_project}.pt"))
                # Refresh table for moved files
                QTimer.singleShot(0, self.load_table)

        # ── Remove truly missing files (not matched as moves) ─────────────────
        if missing_idx:
            keep = [i for i in range(len(self.data["paths"])) if i not in set(missing_idx)]
            self.data["paths"]      = [self.data["paths"][i] for i in keep]
            self.data["embeddings"] = self.data["embeddings"][keep]
            paths = self.data["paths"]

        # Sort by mtime so we process and preview the most recently created file
        new_files.sort(key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0)

        import aisearch_attrs as attrs_mod
        added = 0
        attrs_dirty = False
        retry_files = []
        scan_renames = {}
        added_final_paths = []   # final path of each newly added file (after any renames)
        # Watch-dir auto-rename — always True now that the auto_rename
        # checkbox UI is gone. Watch is implicitly an explicit user setup
        # (they configured a watch dir), so renaming on detection is fine.
        _auto_rename = True
        # Track sizes from last check for two-stage stability test
        _prev_sizes = getattr(self, '_watch_prev_sizes', {})
        _next_sizes = {}
        try:
          for path in new_files:
            try:
                sz = os.path.getsize(path)
            except OSError:
                retry_files.append(path)
                continue
            if sz == 0:
                retry_files.append(path)
                continue
            # Two-stage size check: skip if size changed since last attempt (still writing)
            prev_sz = _prev_sizes.get(path)
            if prev_sz is not None and sz != prev_sz:
                _next_sizes[path] = sz
                retry_files.append(path)
                continue
            if prev_sz is None:
                # First sight — record size and defer one cycle to confirm stability
                _next_sizes[path] = sz
                retry_files.append(path)
                continue
            # prev_sz == sz and sz > 0 → file is stable, proceed
            emb = logic.extract_feature(path)
            if emb is None:
                retry_files.append(path)
                continue
            self.data["paths"].append(path)
            self.data["embeddings"] = torch.cat(
                [self.data["embeddings"], emb.unsqueeze(0)])
            added += 1
            try:
                # skip_heavy=True → no MediaPipe pose/shot detection on watch.
                # Those fire inside the preview via _on_inspect so the user sees
                # the detection happen instead of finding pre-filled fields.
                self.attrs_data = attrs_mod.auto_set_all(
                    self.attrs_data, path, self.current_project, skip_heavy=True)
            except Exception:
                pass
            # O (orientation / aspect), R (resolution), K (fps) — derived from
            # file dimensions, cheap; fill them on the live entry so the preview
            # shows them immediately. Only set codes that aren't already present.
            try:
                _ork = attrs_mod.detect_file_attrs(path)
                if _ork:
                    _e = self.attrs_data.setdefault(path, {})
                    for _fk in ("o", "r", "k"):
                        _fv = _ork.get(_fk, "")
                        if _fv and not _e.get(_fk):
                            _e[_fk] = _fv
                            attrs_dirty = True
            except Exception:
                pass
            # CLIP auto-detect and face detection are deliberately NOT run here.
            # Previously they fired in the watch scan before the preview even
            # opened, which (a) made the pop-up slow and (b) left the entry
            # "already determined" so the user never saw detection run. Those
            # detections now happen when the preview actually opens, visible
            # to the user. auto_set_all already handles filename parse + meta
            # extraction + MediaPipe pose/shot, which is cheap and useful.
            attrs_dirty = True

            # One-shot capture of original watch-zone filename (with extension)
            # into note — only if note is empty, so user-entered notes are
            # never overwritten.
            try:
                _e = self.attrs_data.setdefault(path, {})
                if not _e.get("note"):
                    _e["note"] = os.path.basename(path)
            except Exception:
                pass

            if _auto_rename:
                orig_stem = os.path.splitext(os.path.basename(path))[0]
                pid = (self.attrs_data.get(path) or {}).get("person_id", "")
                if pid and pid != "000":
                    try:
                        new_path = attrs_mod.rename_with_person_id(
                            self.attrs_data, path, pid, flush_stores=False,
                            skip_uncoded=False)
                        if new_path != path:
                            scan_renames[path] = new_path
                            self.data["paths"][-1] = new_path
                            path = new_path
                    except Exception:
                        pass
                try:
                    new_path = attrs_mod.apply_boolean_sync_rules(
                        self.attrs_data, path, self.current_project,
                        orig_stem=orig_stem)
                    if new_path != path:
                        scan_renames[path] = new_path
                        self.data["paths"][-1] = new_path
                        path = new_path
                except Exception:
                    pass
                # Bake detected O/R/K into filename
                try:
                    _entry = self.attrs_data.get(path) or {}
                    _cf = {k: _entry.get(f"cf_{k}", "")
                           for k in ("o", "r", "k")}
                    _cf = {k: v for k, v in _cf.items() if v}
                    if _cf:
                        _stem, _ext = os.path.splitext(os.path.basename(path))
                        _parts = attrs_mod.parse_coded_filename(_stem)
                        if _parts:
                            _chg = False
                            for _fk, _fv in _cf.items():
                                if not _parts.get(_fk):
                                    _parts[_fk] = _fv; _chg = True
                            if _chg:
                                _fo = attrs_mod.get_sync_field_order(self.current_project)
                                _df = not bool(_parts.get("persons"))
                                _ns = attrs_mod.build_coded_filename(_parts, date_first=_df, field_order=_fo)
                                if _ns and _ns != _stem:
                                    _np = attrs_mod.unique_path(
                                        os.path.join(os.path.dirname(path), _ns + _ext))
                                    if _np != path:
                                        os.rename(path, _np)
                                        if path in self.attrs_data:
                                            self.attrs_data[_np] = self.attrs_data.pop(path)
                                        scan_renames[path] = _np
                                        self.data["paths"][-1] = _np
                                        path = _np
                except Exception:
                    pass
            # Record the final path of this file (after any auto-renames).
            added_final_paths.append(path)
        finally:
            # Always update prev sizes — even if an exception cut the loop short.
            # Without this, files remain "first sight" forever on the next scan.
            self._watch_prev_sizes = _next_sizes

        if attrs_dirty:
            attrs_mod.save(self.current_project, self.attrs_data)
        if scan_renames:
            attrs_mod.flush_path_renames_to_stores(scan_renames, self.current_project,
                                                   update_clip_pt=False)

        if missing_idx or added:
            torch.save(self.data, os.path.join(attrs_mod.DATA_DIR, f"features_{self.current_project}.pt"))
            parts = []
            if added:       parts.append(f"{added} added")
            if missing_idx: parts.append(f"{len(missing_idx)} removed")
            self.statusBar().showMessage(f"Auto-updated: {', '.join(parts)}.", 4000)
            if added and added_final_paths:
                # added_final_paths already has the correct final path for each new file
                # (after any auto-renames), so no need to reconstruct via scan_renames.
                added_final_paths.sort(key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0)
                newest = added_final_paths[-1]
                def _browse_to_arrival(p=newest):
                    if hasattr(self, '_search_cancel'):
                        self._search_cancel[0] = True
                    self._search_running = False
                    self._lock_preview = False  # ensure preview updates even if search was cancelled
                    self._enter_browse_mode(os.path.dirname(os.path.abspath(p)))
                    target = os.path.normpath(p)
                    _found_row = -1
                    for row in range(self.table.rowCount()):
                        rpath = self.table.get_row_path(row)
                        if rpath and os.path.normpath(rpath) == target:
                            self._select_row(row)
                            self.table.scrollToTop()
                            _found_row = row
                            break
                    # Always force a direct preview show so the new file is displayed
                    # even if row selection didn't fire (e.g. same row already selected,
                    # or file was moved to a different directory by auto-rename).
                    self.preview_handler.show(p)
                    # Trigger CLIP inspect if mode = "on watch receive"
                    if self.config.get("clip_inspect_mode") == "watch":
                        pw = self.preview_handler.window
                        if pw:
                            pw._on_inspect()
                QTimer.singleShot(0, _browse_to_arrival)

        # Re-check pending files after 3s
        if retry_files:
            self._watch_debounce.start(3000)

    # ── Duplicate finder ─────────────────────────────────────────────────────

    def _update_header_layout_for_mode(self):
        """3-column header — section 1 is the wide thumbnail area, sections 3
        and 4 are info+settings. Section 2 has been removed entirely."""
        if not hasattr(self, "thumb_outer"):
            return
        self.thumb_outer.setVisible(True)

    def _on_threshold_changed(self, v):
        self.config.update({"dup_threshold": v})
        cfg.save_config(self.config)
        self._update_header_layout_for_mode()
        # In dup mode: only load the cache if its threshold matches the new
        # spinner value. Otherwise clear the table and prompt for rescan —
        # showing 99%-scan groups under a "100%" spinner label is misleading.
        if self.config.get("last_mode") == "dup":
            cached_thr = None
            try:
                if os.path.exists(self._dup_file_path()):
                    with open(self._dup_file_path(), encoding="utf-8") as f:
                        cached_thr = int(json.load(f).get("threshold", 0))
            except Exception:
                cached_thr = None
            if cached_thr == v:
                self._load_dup_results(update_spinner=False)
            else:
                # Clear stale display and tell the user
                self.table.setRowCount(0)
                self._dup_display_data = None
                if cached_thr:
                    self.lbl_dup_status.setText(_t(
                        f"Cache is from {cached_thr}% — press ⟳ Scan for {v}% / "
                        f"キャッシュは {cached_thr}% のもの — {v}% で⟳スキャンを押してください"))
                else:
                    self.lbl_dup_status.setText(_t("Press ⟳ Scan to find duplicates. / ⟳スキャンを押して重複を検索"))
        else:
            self._update_dup_status_label()

    def _set_dup_result(self, summary: str, threshold: int):
        """Record the threshold + summary of the currently displayed dup results."""
        self._dup_result_summary   = summary
        self._dup_result_threshold = threshold
        self._update_dup_status_label()

    def _update_dup_status_label(self):
        if not self._dup_result_summary:
            return
        scan_pct = self._dup_result_threshold
        cur_pct  = self.spin_threshold.value()
        if scan_pct is not None and cur_pct != scan_pct:
            self.lbl_dup_status.setText(
                f"⚠ Showing {scan_pct}% results — Rescan for {cur_pct}%")
        else:
            pct_str = f" @ {scan_pct}%" if scan_pct is not None else ""
            self.lbl_dup_status.setText(self._dup_result_summary + pct_str)

    def _force_rescan(self):
        f = self._dup_file_path()
        if os.path.exists(f):
            os.remove(f)
        self._run_dup_scan()

    def cleanup_legacy_resolution_tags(self):
        """Remove legacy resolution tags ('sd', '1k', '2k', '4k') from all
        entries in the current project. The R coded field replaces them."""
        legacy = {"sd", "1k", "2k", "4k"}
        if not self.attrs_data:
            QMessageBox.information(self, _t("Cleanup / 整理"),
                _t("No attrs loaded. / 属性が読み込まれていません。"))
            return
        affected = 0
        for path, entry in self.attrs_data.items():
            if not isinstance(entry, dict):
                continue
            tags = entry.get("tags", [])
            new_tags = [t for t in tags if t not in legacy]
            if len(new_tags) != len(tags):
                entry["tags"] = new_tags
                affected += 1
        if affected:
            attrs_mod.save(self.current_project, self.attrs_data)
        QMessageBox.information(self, _t("Cleanup / 整理"),
            _t(f"Removed legacy resolution tags from {affected} files / "
               f"{affected}件のファイルから古い解像度タグを削除しました"))

    def apply_path_rules_to_all(self):
        """Re-apply path-scoped filename rules ('/' rules) to every file in
        the current project. Useful after adding/changing a path rule for
        files that were imported earlier."""
        if not self.data or not self.data.get("paths"):
            QMessageBox.information(self, _t("Apply Rules / ルール適用"),
                _t("No files in project. / プロジェクトにファイルがありません。"))
            return
        path_rules = self.get_path_rules_cached()
        if not path_rules:
            QMessageBox.information(self, _t("Apply Rules / ルール適用"),
                _t("No path-scoped (/) rules to apply. / 適用する '/' ルールがありません。"))
            return
        changed_count = 0
        for p in list(self.data["paths"]):
            try:
                self.attrs_data, c = attrs_mod.apply_path_rules(
                    self.attrs_data, p, self.current_project, _path_rules=path_rules)
                if c: changed_count += 1
            except Exception:
                pass
        if changed_count:
            attrs_mod.save(self.current_project, self.attrs_data)
        QMessageBox.information(self, _t("Apply Rules / ルール適用"),
            _t(f"Updated {changed_count} files / {changed_count}件のファイルを更新しました"))



    # ── czkawka-compatible import/export (hidden — no UI buttons, kept for power users) ──

    def _import_dup_json(self):
        """Import czkawka-format duplicates JSON. Not exposed in UI but callable from console."""
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Import czkawka Duplicates JSON", os.path.expanduser("~"),
            "JSON Files (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as e:
            QMessageBox.warning(self, _t("Import Error / 取込エラー"), str(e))
            return

        groups_data = []
        for size_groups in raw.values():
            for group in size_groups:
                members = [{"path": m["path"], "sim": 1.0}
                           for m in group if os.path.exists(m["path"])]
                ext_buckets = {}
                for m in members:
                    ext = os.path.splitext(m["path"])[1].lower()
                    ext_buckets.setdefault(ext, []).append(m)
                for bucket in ext_buckets.values():
                    if len(bucket) > 1:
                        groups_data.append(bucket)

        if not groups_data:
            QMessageBox.information(self, _t("Import / 取込"), _t("No valid duplicate groups found. / 有効な重複グループが見つかりませんでした。"))
            return

        self.table.setHorizontalHeaderLabels([_t("Group / グループ"), _t("Size / サイズ"), _t("Name / 名前"), _t("Path / パス")])
        self.drop_zone.setPixmap(QPixmap())
        self.drop_zone.setText(_t("DUPLICATES\nFINDER / 重複\n検索"))
        self._collapsed_groups.clear()
        self._display_dup_from_data(groups_data)
        self._dup_display_data = groups_data
        total_files = sum(len(g) for g in groups_data)
        self._set_dup_result(f"{len(groups_data)} groups, {total_files} files (imported)", 0)
        self.config["last_mode"] = "dup"
        cfg.save_config(self.config, getattr(self, "current_project", None))
        self._update_mode_buttons("dup")

    def _export_dup_json(self):
        """Export current dup results as czkawka-format JSON. Not exposed in UI but callable."""
        if not self._dup_display_data:
            QMessageBox.information(self, _t("Export / 書出"), _t("No duplicate results to export. / 書き出す重複結果がありません。"))
            return
        from PyQt6.QtWidgets import QFileDialog
        import hashlib

        path, _ = QFileDialog.getSaveFileName(
            self, "Export czkawka Duplicates JSON", os.path.expanduser("~/aisearch_duplicates.json"),
            "JSON Files (*.json)")
        if not path:
            return

        def _md5(p):
            h = hashlib.md5()
            try:
                with open(p, "rb") as f:
                    for chunk in iter(lambda: f.read(65536), b""):
                        h.update(chunk)
                return h.hexdigest()
            except OSError:
                return ""

        out = {}
        for group in self._dup_display_data:
            entries = []
            for m in group:
                p = m["path"]
                try:
                    sz  = os.path.getsize(p)
                    mdt = int(os.path.getmtime(p))
                except OSError:
                    sz, mdt = 0, 0
                entries.append({"path": p, "modified_date": mdt,
                                "size": sz, "hash": _md5(p)})
            if entries:
                out.setdefault(str(entries[0]["size"]), []).append(entries)

        out = dict(sorted(out.items(), key=lambda x: int(x[0])))
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, indent=2)
            total = sum(len(g) for groups in out.values() for g in groups)
            QMessageBox.information(self, _t("Export / 書出"),
                _t(f"Exported {len(out)} size buckets, {total} files\n→ {path} / {len(out)} サイズバケット、{total} ファイルを書き出しました\n→ {path}"))
        except Exception as e:
            QMessageBox.warning(self, _t("Export Error / 書出エラー"), str(e))

    def _find_duplicates_by_hash(self, content_hash=False):
        """100%/99% mode: full disk hash scan of all files in base_dirs.
        content_hash=False (100%): byte-level MD5 — exact byte match.
        content_hash=True  (99%): decode images and hash pixel data only,
            ignoring embedded metadata (EXIF/AItan/etc.). Lets you re-find
            visual duplicates after baking metadata into them. Videos still
            use file-byte hashing."""
        if not self.base_dirs:
            QMessageBox.information(self, _t("Find Duplicates / 重複検索"), _t("No base directory set. / ベースディレクトリが設定されていません。"))
            return
        import hashlib
        from collections import defaultdict

        # During scan: ⟳ Scan → ⏹ Stop.
        # On Stop click: worker pauses, sends partial results so the user
        # can see what's been found so far. Stop hides; ▶ Resume + ⟳ Rescan
        # show so the user can continue from where they left off or restart.
        self._dup_cancel = False
        self._dup_paused = False
        self._dup_show_partial = False
        self._dup_cancelling_ui = False   # reset suppress flag for new scan
        if hasattr(self, "btn_scan"):
            try:
                self.btn_scan.clicked.disconnect()
            except Exception:
                pass
            def _stop_scan():
                self._dup_paused = True
                self._dup_show_partial = True   # worker will pause when it reaches a check
                # Save the resume position so a future relaunch picks up
                # from where the user stopped, not from the top.
                try:
                    _idx = int(getattr(self, "_dup_current_index", 0) or 0)
                    self._dup_resume_index = _idx
                    _prog_path = os.path.join(
                        attrs_mod.DATA_DIR,
                        f"dups_{self.current_project}_progress.json")
                    with open(_prog_path, "w") as _pf:
                        json.dump({"index": _idx}, _pf)
                except Exception:
                    pass
                # Build partial groups RIGHT NOW from the shared hash_map —
                # don't wait for worker to reach its next pause check.
                partial = []
                hm = getattr(self, "_dup_hash_map", None)
                if hm is not None:
                    for fpaths in hm.values():
                        if len(fpaths) < 2:
                            continue
                        members = [{"path": p, "sim": 1.0} for p in fpaths]
                        try:
                            members.sort(key=lambda m: os.path.getsize(m["path"]), reverse=True)
                        except Exception:
                            pass
                        partial.append(members)
                    partial.sort(key=len, reverse=True)
                if partial:
                    total_files = sum(len(g) for g in partial)
                    self._set_dup_result(f"{len(partial)} groups, {total_files} files (partial)",
                                         int(self.spin_threshold.value()))
                    self.table.setHorizontalHeaderLabels([_t("Group / グループ"), _t("Size / サイズ"), _t("Name / 名前"), _t("Path / パス")])
                    self._collapsed_groups.clear()
                    self._display_dup_from_data(partial)
                    self._dup_display_data = partial
                    # Persist partial results so they survive app close.
                    # Without this, Stop showed groups in memory only —
                    # exit + relaunch lost everything found so far.
                    try:
                        self._dup_result_threshold = int(self.spin_threshold.value())
                        self._save_dup_results()
                    except Exception:
                        pass
                else:
                    self.lbl_dup_status.setText(_t("Stopped — nothing found yet / 停止中 — まだ何も見つかっていません"))
                self.btn_scan.hide()
                if hasattr(self, "btn_dup_resume"):
                    self.btn_dup_resume.show()
                if hasattr(self, "btn_dup_rescan"):
                    self.btn_dup_rescan.show()
                # Keep the progress bar VISIBLE but FROZEN while paused so
                # the user sees the scan is "held". Switch from indeterminate
                # (range 0,0 = animation) to a fixed determinate state so
                # there's no motion. Also override the in-row dup status text
                # so it doesn't keep showing the last "Hashing… 50/2151" line.
                if not (self._dup_display_data and len(self._dup_display_data) > 0):
                    self.lbl_dup_status.setText(_t("⏸ Paused / ⏸ 一時停止"))
                if hasattr(self, "search_status_label"):
                    self.search_status_label.setText(_t("⏸ Paused / ⏸ 一時停止"))
                    self.search_status_label.show()
                if hasattr(self, "search_progress"):
                    self.search_progress.setRange(0, 1)
                    self.search_progress.setValue(1)
                    self.search_progress.show()
            self.btn_scan.clicked.connect(_stop_scan)
            self.btn_scan.setText(_t("⏹ Stop / ⏹ 停止"))
            if hasattr(self, "spin_threshold"):
                self.spin_threshold.setEnabled(False)
            self.btn_scan.show()
        # Wire Resume: unpause worker, hide Resume/Rescan, show Stop again.
        if hasattr(self, "btn_dup_resume"):
            try:
                self.btn_dup_resume.clicked.disconnect()
            except Exception:
                pass
            def _resume_scan():
                self._dup_paused = False
                self.btn_dup_resume.hide()
                if hasattr(self, "btn_dup_rescan"):
                    self.btn_dup_rescan.hide()
                self.btn_scan.show()
                self.lbl_dup_status.setText(_t("Resuming… / 再開中…"))
                # Update the global status label so it doesn't keep showing
                # "⏸ Paused" while the bar starts moving again.
                if hasattr(self, "search_status_label"):
                    self.search_status_label.setText(_t("♊ Resuming… / ♊ 再開中…"))
                    self.search_status_label.show()
                # Restart indeterminate animation — Qt won't restart on its
                # own after going from frozen (0,1) back to (0,0); hide/show
                # cycle forces the bar to re-init the animation.
                if hasattr(self, "search_progress"):
                    self.search_progress.hide()
                    self.search_progress.setRange(0, 0)
                    self.search_progress.show()
            self.btn_dup_resume.clicked.connect(_resume_scan)
            self.btn_dup_resume.hide()
        # Wire Cancel: terminate the paused worker and return to idle.
        # Threshold spinner becomes editable so user can change it before
        # starting a new scan.
        if hasattr(self, "btn_dup_rescan"):
            try:
                self.btn_dup_rescan.clicked.disconnect()
            except Exception:
                pass
            def _cancel_paused():
                # Reset UI INSTANTLY on the main thread — don't wait for the
                # worker to exit. Worker will exit cleanly in the background
                # and _poll_dup_queue will harmlessly re-apply the same reset.
                self._dup_cancel = True
                self._dup_paused = False
                self._dup_cancelling_ui = True   # block stale progress msgs
                self.btn_dup_rescan.hide()
                if hasattr(self, "btn_dup_resume"):
                    self.btn_dup_resume.hide()
                if hasattr(self, "btn_scan"):
                    try:
                        self.btn_scan.clicked.disconnect()
                    except Exception:
                        pass
                    self.btn_scan.clicked.connect(self._force_rescan)
                    self.btn_scan.setText(_t("⟳ Scan / ⟳ スキャン"))
                    self.btn_scan.show()
                if hasattr(self, "spin_threshold"):
                    self.spin_threshold.setEnabled(True)
                # Hide the global progress indicator and clear status text.
                if hasattr(self, "search_progress"):
                    self.search_progress.hide()
                if hasattr(self, "search_status_label"):
                    self.search_status_label.hide()
                self.lbl_dup_status.setText("")
                # Drop partial dup results so the table doesn't show a stale
                # half-complete list after the user cancels.
                self.table.setRowCount(0)
                self._dup_display_data = None
                self._dup_result_summary = ""
            self.btn_dup_rescan.clicked.connect(_cancel_paused)
            self.btn_dup_rescan.hide()
        # Clear existing results immediately so the user sees a fresh slate.
        self.table.setRowCount(0)
        self._dup_display_data = None
        self._dup_result_summary = ""
        self.lbl_dup_status.setText("")
        self._dup_queue = queue.Queue()
        base_dirs = list(self.base_dirs)
        _media_exts = tuple(logic.EXT_IMG + logic.EXT_VID)
        _IMG_EXTS = ('.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tiff', '.gif', '.avif')

        def _is_binary(p):
            try:
                with open(p, 'rb') as f:
                    return b'\x00' in f.read(512)
            except OSError:
                return True

        def _is_media(p):
            pl = p.lower()
            if pl.endswith(_media_exts):
                return True
            return _is_binary(p)

        def _worker():
            try:
                import time as _wtime
                # 1. Collect all media files grouped by size (skip zero-byte files)
                self._dup_queue.put(("progress", _t("Scanning files… / ファイル走査中…")))
                size_map = defaultdict(list)
                _scanned = 0
                def _check_pause_and_cancel():
                    while self._dup_paused and not self._dup_cancel:
                        _wtime.sleep(0.1)
                    return self._dup_cancel
                for base in base_dirs:
                    if _check_pause_and_cancel():
                        self._dup_queue.put(("hash_done", []))
                        return
                    for root, _, files in os.walk(base):
                        if _check_pause_and_cancel():
                            self._dup_queue.put(("hash_done", []))
                            return
                        for fname in files:
                            fpath = os.path.join(root, fname)
                            if not _is_media(fpath):
                                continue
                            try:
                                sz = os.path.getsize(fpath)
                                if sz > 0:
                                    size_map[sz].append(fpath)
                                    _scanned += 1
                                    if _scanned % 200 == 0:
                                        if _check_pause_and_cancel():
                                            self._dup_queue.put(("hash_done", []))
                                            return
                                        self._dup_queue.put(("progress",
                                            _t(f"Scanning… {_scanned} files / 走査中… {_scanned}件")))
                            except OSError:
                                pass

                # 2. Compute hash. content_hash mode decodes images and hashes
                # pixel data only — metadata changes don't affect the hash.
                def _md5(path):
                    h = hashlib.md5()
                    with open(path, 'rb') as f:
                        for chunk in iter(lambda: f.read(65536), b''):
                            h.update(chunk)
                    return h.hexdigest()

                def _content_md5(path):
                    if path.lower().endswith(_IMG_EXTS):
                        try:
                            from PIL import Image
                            with Image.open(path) as img:
                                img.load()
                                h = hashlib.md5()
                                # mode+size first so 1024×1024 RGB ≠ 1024×1024 RGBA
                                h.update(f"{img.mode}:{img.size}".encode())
                                h.update(img.tobytes())
                                return h.hexdigest()
                        except Exception:
                            pass
                    return _md5(path)   # videos / decode failures → fall back

                _hash = _content_md5 if content_hash else _md5
                # In content_hash mode, files of different sizes may still be
                # pixel-identical (different metadata sizes). So hash ALL media
                # files, not just same-size groups.
                if content_hash:
                    _to_hash = [(0, p) for fpaths in size_map.values() for p in fpaths]
                else:
                    _to_hash = [(s, p) for s, fpaths in size_map.items() if len(fpaths) >= 2 for p in fpaths]
                _hash_total = len(_to_hash)
                self._dup_queue.put(("progress",
                    _t(f"Hashing {_hash_total} candidates… / {_hash_total}件をハッシュ化中…")))
                import time as _time
                # Store hash_map on self so main thread can build partial
                # results instantly without waiting for the worker to reach
                # a pause check.
                self._dup_hash_map = defaultdict(list)
                hash_map = self._dup_hash_map
                def _build_partial_groups():
                    partial = []
                    for fpaths in hash_map.values():
                        if len(fpaths) < 2:
                            continue
                        members = [{"path": p, "sim": 1.0} for p in fpaths]
                        try:
                            members.sort(key=lambda m: os.path.getsize(m["path"]), reverse=True)
                        except Exception:
                            pass
                        partial.append(members)
                    partial.sort(key=len, reverse=True)
                    return partial
                # Rotate the start position so a stopped scan resumes from
                # where it left off — and wraps around so the skipped early
                # files still get hashed in the same run. Without this, a
                # second scan starts from the top and the user has to wait
                # through the same "no results" stretch (often deleted /
                # already-checked files) before live results reappear.
                _N = len(_to_hash)
                _start_off = int(getattr(self, "_dup_resume_index", 0)) % _N if _N else 0
                _persist_path = os.path.join(
                    attrs_mod.DATA_DIR,
                    f"dups_{self.current_project}_progress.json")
                for _step in range(1, _N + 1):
                    _idx = (_start_off + _step - 1) % _N
                    _i = _step  # progress counter (1..N)
                    size, fpath = _to_hash[_idx]
                    self._dup_current_index = _idx   # so Stop can capture
                    # Pause: block until resumed or cancelled. While paused,
                    # if the UI has asked for partial results (Stop pressed),
                    # emit them once.
                    while self._dup_paused and not self._dup_cancel:
                        if self._dup_show_partial:
                            self._dup_show_partial = False
                            self._dup_queue.put(("partial", _build_partial_groups()))
                        _time.sleep(0.1)
                    if self._dup_cancel:
                        # Persist the resume position so a future relaunch
                        # picks up here too (file is small JSON).
                        try:
                            with open(_persist_path, "w") as _pf:
                                json.dump({"index": _idx, "total": _N}, _pf)
                        except Exception:
                            pass
                        self._dup_resume_index = _idx
                        self._dup_queue.put(("hash_done", _build_partial_groups()))
                        return
                    try:
                        key = _hash(fpath) if content_hash else (size, _hash(fpath))
                        hash_map[key].append(fpath)
                    except OSError:
                        pass
                    if _i % 50 == 0 or _i == _hash_total:
                        # Count groups with 2+ members so the user sees
                        # progress feedback during long hash scans —
                        # how many duplicate groups have been discovered
                        # so far.
                        _found = sum(1 for v in hash_map.values() if len(v) >= 2)
                        self._dup_queue.put(("progress",
                            _t(f"Hashing… {_i}/{_hash_total}  ·  found {_found} groups / "
                               f"ハッシュ化… {_i}/{_hash_total}  ·  {_found} グループ発見")))
                    # Live results: every 500 files, emit a partial groups
                    # snapshot so the table populates as duplicates appear,
                    # not just at the end. The "partial" handler in
                    # _poll_dup_queue re-renders the table without ending
                    # the scan.
                    if _i % 500 == 0 or _i == _hash_total:
                        self._dup_queue.put(("partial", _build_partial_groups()))

                # 3. Build groups — same hash = same bytes = duplicate regardless of extension
                self._dup_queue.put(("progress", _t("Grouping… / グループ化中…")))
                groups_data = []
                for fpaths in hash_map.values():
                    if len(fpaths) < 2:
                        continue
                    members = [{"path": p, "sim": 1.0} for p in fpaths]
                    members.sort(key=lambda m: os.path.getsize(m["path"]), reverse=True)
                    groups_data.append(members)

                # Sort: most files in group first
                groups_data.sort(key=len, reverse=True)
                # Full pass complete — clear the resume pointer so the next
                # scan starts fresh from index 0.
                self._dup_resume_index = 0
                try:
                    if os.path.exists(_persist_path):
                        os.remove(_persist_path)
                except Exception:
                    pass
                self._dup_queue.put(("hash_done", groups_data))
            except Exception as e:
                self._dup_queue.put(("error", str(e)))

        threading.Thread(target=_worker, daemon=True).start()
        self._dup_poll_timer = QTimer(self)
        self._dup_poll_timer.timeout.connect(self._poll_dup_queue)
        self._dup_poll_timer.start(200)

    def _find_duplicates(self):
        """Enter dup mode. Load the most recent cached result for the
        current project (any threshold); show nothing if none exist."""
        if not self._require_database(_t("Duplicate detection / 重複検索")):
            return
        import glob as _glob
        self._update_mode_buttons("dup")
        self._dup_controls_widget.show()
        self.config["last_mode"] = "dup"
        # Restore resume position from the persisted sidecar so a stopped
        # scan continues from where it left off across restarts.
        try:
            _prog_path = os.path.join(
                attrs_mod.DATA_DIR,
                f"dups_{self.current_project}_progress.json")
            if os.path.exists(_prog_path):
                with open(_prog_path) as _pf:
                    self._dup_resume_index = int(json.load(_pf).get("index", 0))
            else:
                self._dup_resume_index = 0
        except Exception:
            self._dup_resume_index = 0
        # Find any saved dup file for this project, regardless of which
        # threshold the spinner is currently showing. The spinner gets
        # synced to whichever file we actually load. Without this, scans
        # saved at one threshold are invisible when the user opens dup
        # mode with the spinner on a different value.
        proj = self.current_project or ""
        cache_files = _glob.glob(os.path.join(
            attrs_mod.DATA_DIR, f"dups_{proj}_*.json"))
        # Exclude the resume-position sidecar — it shares the prefix but
        # holds {"index": N}, not scan groups. Loading it as a result
        # cache shows an empty dup view.
        cache_files = [p for p in cache_files
                       if not p.endswith("_progress.json")]
        # Pick the most recently modified one — that's what the user most
        # recently produced, even if they scrolled the spinner since.
        cache_files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        if cache_files:
            self._dup_cache_path_override = cache_files[0]
            self._load_dup_results(update_spinner=True)
            self._dup_cache_path_override = None
        else:
            self.table.setRowCount(0)
            self._dup_display_data = None
            self.lbl_dup_status.setText(_t(
                "Press ⟳ Scan to find duplicates. / ⟳スキャンを押して重複を検索"))
        # Ensure the first thumb is populated even if no row is selected yet —
        # show the top-of-list image so the section isn't blank.
        if self.table.rowCount() > 0:
            top_path = self.table.get_row_path(0)
            if top_path and os.path.exists(top_path):
                self._set_zone_image(self.drop_zone, top_path)
        # Apply final layout based on current threshold (99 hides thumb 1)
        self._update_header_layout_for_mode()

    def _run_dup_scan(self):
        """Actually run the duplicate scan (called by ⟳ Scan button).
        Threshold mapping:
          100% → byte-level hash (exact match)
          99%  → content hash (image pixel match, ignores metadata)
          < 99% → CLIP similarity"""
        pct = int(self.spin_threshold.value())
        if pct >= 100:
            self._find_duplicates_by_hash(content_hash=False)
            return
        if pct == 99:
            self._find_duplicates_by_hash(content_hash=True)
            return
        threshold = pct / 100.0

        if not self.data or not self.data.get("paths"):
            QMessageBox.information(self, _t("Find Duplicates / 重複検索"), _t("No database loaded. / データベースが読み込まれていません。"))
            return

        n = len(self.data["paths"])
        if n > 15000:
            if QMessageBox.question(self, _t("Find Duplicates / 重複検索"),
                _t(f"{n} files in DB — this may use a lot of memory. Continue? / DBに{n}ファイル — メモリを大量に使用する可能性があります。続けますか？"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            ) != QMessageBox.StandardButton.Yes:
                return

        self.btn_find_dups.setEnabled(False)
        self.btn_find_dups.setText(_t("Searching... / 検索中..."))
        # Same pause/resume/cancel flow as hash-mode Stop.
        self._dup_cancel = False
        self._dup_paused = False
        self._dup_cancelling_ui = False
        if hasattr(self, "btn_scan"):
            try:
                self.btn_scan.clicked.disconnect()
            except Exception:
                pass
            def _stop_scan():
                self._dup_paused = True
                self.btn_scan.hide()
                if hasattr(self, "btn_dup_resume"):
                    self.btn_dup_resume.show()
                if hasattr(self, "btn_dup_rescan"):
                    self.btn_dup_rescan.show()
                # Freeze + label the bar like hash-mode pause does.
                self.lbl_dup_status.setText(_t("⏸ Paused / ⏸ 一時停止"))
                if hasattr(self, "search_status_label"):
                    self.search_status_label.setText(_t("⏸ Paused / ⏸ 一時停止"))
                    self.search_status_label.show()
                if hasattr(self, "search_progress"):
                    self.search_progress.setRange(0, 1)
                    self.search_progress.setValue(1)
                    self.search_progress.show()
            self.btn_scan.clicked.connect(_stop_scan)
            self.btn_scan.setText(_t("⏹ Stop / ⏹ 停止"))
            if hasattr(self, "spin_threshold"):
                self.spin_threshold.setEnabled(False)
            self.btn_scan.show()
        # Wire Resume — clear pause flag, restore animation, hide Resume/Cancel
        if hasattr(self, "btn_dup_resume"):
            try:
                self.btn_dup_resume.clicked.disconnect()
            except Exception:
                pass
            def _resume_clip():
                self._dup_paused = False
                self.btn_dup_resume.hide()
                if hasattr(self, "btn_dup_rescan"):
                    self.btn_dup_rescan.hide()
                self.btn_scan.show()
                self.lbl_dup_status.setText(_t("Resuming… / 再開中…"))
                if hasattr(self, "search_status_label"):
                    self.search_status_label.setText(_t("♊ Resuming… / ♊ 再開中…"))
                    self.search_status_label.show()
                if hasattr(self, "search_progress"):
                    self.search_progress.hide()
                    self.search_progress.setRange(0, 0)
                    self.search_progress.show()
            self.btn_dup_resume.clicked.connect(_resume_clip)
            self.btn_dup_resume.hide()
        # Wire Cancel — kill paused worker and return to idle
        if hasattr(self, "btn_dup_rescan"):
            try:
                self.btn_dup_rescan.clicked.disconnect()
            except Exception:
                pass
            def _cancel_clip():
                self._dup_cancel = True
                self._dup_paused = False
                self._dup_cancelling_ui = True
                self.btn_dup_rescan.hide()
                if hasattr(self, "btn_dup_resume"):
                    self.btn_dup_resume.hide()
                if hasattr(self, "btn_scan"):
                    try:
                        self.btn_scan.clicked.disconnect()
                    except Exception:
                        pass
                    self.btn_scan.clicked.connect(self._force_rescan)
                    self.btn_scan.setText(_t("⟳ Scan / ⟳ スキャン"))
                    self.btn_scan.show()
                if hasattr(self, "spin_threshold"):
                    self.spin_threshold.setEnabled(True)
                if hasattr(self, "search_progress"):
                    self.search_progress.hide()
                if hasattr(self, "search_status_label"):
                    self.search_status_label.hide()
                self.lbl_dup_status.setText("")
                # Drop partial dup results so the table doesn't show a stale
                # half-complete list after the user cancels.
                self.table.setRowCount(0)
                self._dup_display_data = None
                self._dup_result_summary = ""
            self.btn_dup_rescan.clicked.connect(_cancel_clip)
            self.btn_dup_rescan.hide()
        self.lbl_dup_status.setText("")
        self._dup_queue = queue.Queue()

        threshold = self.spin_threshold.value() / 100.0
        all_paths = list(self.data["paths"])
        all_embs  = self.data["embeddings"]

        _media_exts = tuple(logic.EXT_IMG + logic.EXT_VID)

        def _is_binary(p):
            """Return True if file content is binary (image/video), False if text."""
            try:
                with open(p, 'rb') as f:
                    return b'\x00' in f.read(512)
            except OSError:
                return True  # can't read → keep it, let other checks fail

        def _is_media(p):
            pl = p.lower()
            if pl.endswith(_media_exts):
                return True          # known extension — trust it
            # No recognised extension: check content (Linux files need no extension)
            return _is_binary(p)

        # Filter to project base_dirs only — exclude watch-only dirs (e.g. Downloads)
        if self.base_dirs:
            def _in_project(p):
                pn = os.path.normpath(p)
                for bd in self.base_dirs:
                    bdn = os.path.normpath(bd)
                    if pn == bdn or pn.startswith(bdn + os.sep):
                        return True
                return False
            keep = [i for i, p in enumerate(all_paths)
                    if _in_project(p) and _is_media(p)]
        else:
            keep = [i for i, p in enumerate(all_paths) if _is_media(p)]
        paths = [all_paths[i] for i in keep]
        embs  = all_embs[keep] if keep else all_embs[:0]

        def _worker():
            try:
                import hashlib
                from collections import defaultdict

                q = self._dup_queue
                exact_mode = (threshold >= 1.0)
                n = len(paths)
                q.put(("progress", f"Computing similarity for {n} files…"))

                _hash_cache = {}
                def _file_hash(path):
                    if path in _hash_cache:
                        return _hash_cache[path]
                    h = hashlib.md5()
                    try:
                        with open(path, 'rb') as f:
                            for chunk in iter(lambda: f.read(65536), b''):
                                h.update(chunk)
                        result = h.hexdigest()
                    except OSError:
                        result = None
                    _hash_cache[path] = result
                    return result

                def _same_ext(i, j):
                    return (os.path.splitext(paths[i])[1].lower() ==
                            os.path.splitext(paths[j])[1].lower())

                _fsize_cache2 = {}
                def _fsize2(p):
                    if p not in _fsize_cache2:
                        try:    _fsize_cache2[p] = os.path.getsize(p)
                        except: _fsize_cache2[p] = 0
                    return _fsize_cache2[p]

                def _sizes_ok(i, j):
                    """True if file sizes are within 1.5× of each other."""
                    sa, sb = _fsize2(paths[i]), _fsize2(paths[j])
                    if sa == 0 or sb == 0:
                        return True
                    return max(sa, sb) <= min(sa, sb) * 1.5

                # Sparse similarity edges: (i, j) → sim, i < j. Built
                # below in a chunked similarity pass. Replaces the dense
                # N×N matrix that used ~N² × 4 B and OOM'd on big DBs.
                edges = {}

                class _SparseSim:
                    """Drop-in replacement for the old dense sim tensor.
                    .get(i, j) returns the stored edge sim if known,
                    otherwise computes it on demand from embeddings."""
                    def __init__(self, _edges, _embs):
                        self._e = _edges
                        self._embs = _embs
                    def get(self, i, j):
                        if i == j:
                            return 1.0
                        a, b = (i, j) if i < j else (j, i)
                        v = self._e.get((a, b))
                        if v is not None:
                            return v
                        try:
                            return float(st_util.cos_sim(
                                self._embs[i:i+1], self._embs[j:j+1])[0, 0])
                        except Exception:
                            return 0.0
                sim = _SparseSim(edges, embs)

                # Connected components: BFS
                adj = [[] for _ in range(n)]

                # Pre-pass: group by coded filename prefix (same persons + bg, same size)
                if not exact_mode:
                    q.put(("progress", f"Prefix pass ({n} files)…"))
                    _fsize_cache = {}
                    def _fsize_p(p):
                        if p not in _fsize_cache:
                            try:    _fsize_cache[p] = os.path.getsize(p)
                            except: _fsize_cache[p] = 0
                        return _fsize_cache[p]

                    def _sizes_similar(i, j):
                        sa, sb = _fsize_p(paths[i]), _fsize_p(paths[j])
                        if sa == 0 or sb == 0:
                            return True
                        lo, hi = min(sa, sb), max(sa, sb)
                        return hi <= lo * 1.05

                    prefix_map = defaultdict(list)
                    for i, p in enumerate(paths):
                        stem   = os.path.splitext(os.path.basename(p))[0]
                        prefix = attrs_mod.filename_group_key(stem)
                        if prefix is None:
                            continue
                        prefix_map[prefix].append(i)
                    for prefix_indices in prefix_map.values():
                        if len(prefix_indices) < 2:
                            continue
                        for a in range(len(prefix_indices)):
                            for b in range(a + 1, len(prefix_indices)):
                                i, j = prefix_indices[a], prefix_indices[b]
                                if not _same_ext(i, j):
                                    continue
                                if not _sizes_similar(i, j):
                                    continue
                                if j not in adj[i]: adj[i].append(j)
                                if i not in adj[j]: adj[j].append(i)

                # Cosine similarity pass — chunked so peak memory stays
                # bounded. Was: a single (N, N) cos_sim on GPU OOM'd at
                # ~1.8 GiB on big DBs. Now we slice the embedding matrix
                # into rows of CHUNK at a time, so peak is CHUNK × N × 4 B
                # (~45 MB for CHUNK=512, N=22000).
                q.put(("progress", f"Comparing pairs…  0%"))
                _last_pct = 0
                import time as _ctime
                CHUNK = 512
                for chunk_start in range(0, n, CHUNK):
                    chunk_end = min(chunk_start + CHUNK, n)
                    # Pause / cancel between chunks
                    while self._dup_paused and not self._dup_cancel:
                        _ctime.sleep(0.1)
                    if self._dup_cancel:
                        q.put(("error", _t("Scan stopped by user / ユーザーが中止しました")))
                        return
                    pct = int(chunk_start / n * 100)
                    if pct >= _last_pct + 5:
                        _last_pct = pct
                        q.put(("progress", f"Comparing pairs… {pct:3d}%"))
                    sims_block = st_util.cos_sim(embs[chunk_start:chunk_end], embs).cpu()
                    for i_local in range(chunk_end - chunk_start):
                        i = chunk_start + i_local
                        row = sims_block[i_local]
                        for j in range(i + 1, n):
                            # Pause/cancel inside the inner loop occasionally
                            if (j & 0xFFF) == 0:
                                while self._dup_paused and not self._dup_cancel:
                                    _ctime.sleep(0.1)
                                if self._dup_cancel:
                                    q.put(("error", _t("Scan stopped by user / ユーザーが中止しました")))
                                    return
                            v = float(row[j])
                            if v >= threshold:
                                if not _same_ext(i, j):
                                    continue
                                if not _sizes_ok(i, j):
                                    continue
                                if exact_mode and _file_hash(paths[i]) != _file_hash(paths[j]):
                                    continue
                                edges[(i, j)] = v
                                adj[i].append(j)
                                adj[j].append(i)
                    # Drop the block before the next chunk allocates
                    del sims_block

                q.put(("progress", _t("Finding groups… / グループ検出中…")))
                visited = [False] * n
                groups  = []
                for i in range(n):
                    if visited[i] or not adj[i]: continue
                    group = []
                    stack = [i]
                    while stack:
                        node = stack.pop()
                        if visited[node]: continue
                        visited[node] = True
                        group.append(node)
                        stack.extend(adj[node])
                    if len(group) > 1:
                        def _sz(idx):
                            try: return os.path.getsize(paths[idx])
                            except OSError: return 0
                        group.sort(key=_sz, reverse=True)
                        ext_buckets = {}
                        for idx in group:
                            ext = os.path.splitext(paths[idx])[1].lower()
                            ext_buckets.setdefault(ext, []).append(idx)
                        for bucket in ext_buckets.values():
                            if len(bucket) > 1:
                                groups.append(bucket)

                # Sort groups: highest similarity first
                def _group_max_sim(group):
                    rep = group[0]
                    return max(sim.get(idx, rep) for idx in group[1:]) if len(group) > 1 else 1.0
                groups.sort(key=_group_max_sim, reverse=True)
                q.put(("done", (groups, sim, paths)))
            except Exception as e:
                self._dup_queue.put(("error", str(e)))

        threading.Thread(target=_worker, daemon=True).start()
        self._dup_poll_timer = QTimer(self)
        self._dup_poll_timer.timeout.connect(self._poll_dup_queue)
        self._dup_poll_timer.start(200)

    def _poll_dup_queue(self):
        try:
            msg, payload = self._dup_queue.get_nowait()
        except queue.Empty:
            return
        if msg == "progress":
            # Suppress in-flight progress messages from the worker if user has
            # paused (Stop) or is cancelling — otherwise they keep re-showing
            # the bar/label after the main thread already hid it.
            if getattr(self, "_dup_paused", False) or getattr(self, "_dup_cancelling_ui", False):
                return
            self.lbl_dup_status.setText(payload)
            self.search_status_label.setText(f"♊ {payload}")
            self.search_status_label.show()
            self.search_progress.show()
            return                        # keep polling
        if msg == "partial":
            # Render partial results from an in-progress / paused worker.
            groups_data = payload
            total_files = sum(len(g) for g in groups_data)
            if groups_data:
                self._set_dup_result(f"{len(groups_data)} groups, {total_files} files (partial)",
                                     int(self.spin_threshold.value()))
                self.table.setHorizontalHeaderLabels([_t("Group / グループ"), _t("Size / サイズ"), _t("Name / 名前"), _t("Path / パス")])
                self._collapsed_groups.clear()
                self._display_dup_from_data(groups_data)
                self._dup_display_data = groups_data
                # Persist live partial results to disk so a crash / kill /
                # close mid-scan doesn't lose what was already found.
                try:
                    self._dup_result_threshold = int(self.spin_threshold.value())
                    self._save_dup_results()
                except Exception:
                    pass
            else:
                self.lbl_dup_status.setText(_t("Stopped — nothing found yet / 停止中 — まだ何も見つかっていません"))
            return                        # keep polling — worker still alive
        self._dup_poll_timer.stop()
        # btn_find_dups left untouched — no text change during scan to revert
        # Hide Resume + Rescan (only visible while paused)
        if hasattr(self, "btn_dup_resume"):
            self.btn_dup_resume.hide()
        if hasattr(self, "btn_dup_rescan"):
            self.btn_dup_rescan.hide()
        self._dup_paused = False
        self._dup_cancelling_ui = False
        # Restore Scan button: text, handler, AND visibility (Stop click hid it).
        if hasattr(self, "btn_scan"):
            try:
                self.btn_scan.clicked.disconnect()
            except Exception:
                pass
            self.btn_scan.clicked.connect(self._force_rescan)
            self.btn_scan.setText(_t("⟳ Scan / ⟳ スキャン"))
            self.btn_scan.show()
        if hasattr(self, "spin_threshold"):
            self.spin_threshold.setEnabled(True)
        # Hide the global indicator when scan is done (or on error)
        self.search_status_label.hide()
        self.search_progress.hide()
        if msg == "error":
            # User-cancellation isn't a critical error — just clear status
            if "Scan stopped by user" in payload or "中止" in payload:
                self.lbl_dup_status.setText(payload)
            else:
                QMessageBox.critical(self, _t("Duplicate Finder / 重複検索"), payload)
            return
        if msg == "hash_done":
            groups_data = payload
            total_files = sum(len(g) for g in groups_data)
            self._set_dup_result(f"{len(groups_data)} groups, {total_files} files", 100)
            self.table.setHorizontalHeaderLabels([_t("Group / グループ"), _t("Size / サイズ"), _t("Name / 名前"), _t("Path / パス")])
            self.drop_zone.setPixmap(QPixmap())
            self.drop_zone.setText(_t("DUPLICATES\nFINDER / 重複\n検索"))
            self._collapsed_groups.clear()
            self._display_dup_from_data(groups_data)
            self._dup_display_data = groups_data
            self._save_dup_results()
            self.config["last_mode"] = "dup"
            cfg.save_config(self.config, getattr(self, "current_project", None))
            self._update_mode_buttons("dup")
            if self.table.rowCount():
                self._select_row(0)
            self.table.setFocus()
            return
        groups, sim, paths = payload
        total_files = sum(len(g) for g in groups)
        self._set_dup_result(f"{len(groups)} groups, {total_files} files",
                             self.spin_threshold.value())
        self._dup_display_data = self._build_dup_display_data(groups, sim, paths)
        # Switch column header to "Group"
        self.table.setHorizontalHeaderLabels([_t("Group / グループ"), _t("Size / サイズ"), _t("Name / 名前"), _t("Path / パス")])
        # Switch to dup mode
        self.drop_zone.setPixmap(QPixmap())
        self.drop_zone.setText(_t("DUPLICATES\nFINDER / 重複\n検索"))
        self._collapsed_groups.clear()
        # self.attr_panel.show()  # disabled
        self._display_dup_groups(groups, sim, paths)
        self.config["last_mode"] = "dup"
        cfg.save_config(self.config, getattr(self, "current_project", None))
        self._update_mode_buttons("dup")
        if self.table.rowCount():
            self._select_row(0)
        self.table.setFocus()

    def _dup_color(self, sim_score, group_idx):
        family = group_idx % 2
        for threshold, col_a, col_b in self._dup_shades:
            if sim_score >= threshold:
                return col_a if family == 0 else col_b
        return self._dup_shades[-1][1] if family == 0 else self._dup_shades[-1][2]

    def _display_dup_groups(self, groups, sim, paths):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for g_idx, group in enumerate(groups):
            rep = group[0]
            grp_label = f"G{g_idx + 1}"
            for rank, idx in enumerate(group):
                score = f"{'▼ ' if rank == 0 else '  '}{grp_label}"
                row   = self._append_row(score,
                                         logic.get_sz_readable(paths[idx]),
                                         os.path.basename(paths[idx]),
                                         self._mask_path(paths[idx]),
                                         paths[idx])
                item0 = self.table.item(row, 0)
                item0.setData(Qt.ItemDataRole.UserRole + 2, grp_label)
                if rank == 0:
                    item0.setToolTip(_t("Click to collapse/expand group / クリックでグループを折りたたみ/展開"))
                sim_score = 1.0 if rank == 0 else sim.get(idx, rep)
                item0.setData(Qt.ItemDataRole.UserRole + 1, sim_score)
                color = self._dup_color(sim_score, g_idx)
                fg = self._contrast_fg(color)
                for col in range(self.table.columnCount()):
                    self.table.item(row, col).setBackground(color)
                    self.table.item(row, col).setForeground(fg)
        # Keep sorting OFF for dup results — enabling it scatters the groups
        if self.table.rowCount():
            self._select_row(0)

    # ── Save / Load duplicate results ────────────────────────────────────────

    def _build_dup_display_data(self, groups, sim, paths):
        """Convert live groups+sim tensor into a serialisable list for save/load."""
        result = []
        for group in groups:
            rep = group[0]
            members = []
            for rank, idx in enumerate(group):
                members.append({
                    "path": paths[idx],
                    "sim":  1.0 if rank == 0 else round(sim.get(idx, rep), 6)
                })
            result.append(members)
        return result

    def _dup_file_path(self):
        pct = self.spin_threshold.value()
        suffix = "hash" if pct >= 100 else f"{pct}pct"
        return os.path.join(attrs_mod.DATA_DIR, f"dups_{self.current_project}_{suffix}.json")

    def _compute_dup_marks(self):
        """Walk _dup_display_data and apply the active rule checkboxes to
        produce a set of paths marked for deletion. UNION of rules: a file
        is marked if ANY active rule matches it within its group. Reverse
        flips the per-group selection (marks become unmarks and vice
        versa). Empty rules (none active) → empty set."""
        marks = set()
        if not self._dup_display_data:
            return marks
        active = {k for k, cb in self._dup_rule_checks.items() if cb.isChecked()}
        if not active:
            return marks
        reverse = "reverse" in active
        criteria = active - {"reverse"}
        for group in self._dup_display_data:
            paths = []
            for m in group:
                p = m.get("path", "") if isinstance(m, dict) else str(m)
                if p:
                    paths.append(p)
            if len(paths) < 2:
                continue
            stats = {}
            for p in paths:
                try:
                    st = os.stat(p)
                    stats[p] = (st.st_size, st.st_mtime)
                except OSError:
                    stats[p] = (0, 0)
            sizes = {p: stats[p][0] for p in paths}
            mtimes = {p: stats[p][1] for p in paths}
            depths = {p: p.count(os.sep) for p in paths}
            mark_set = set()
            if "smaller" in criteria:
                _max = max(sizes.values())
                mark_set |= {p for p in paths if sizes[p] < _max}
            if "larger" in criteria:
                _min = min(sizes.values())
                mark_set |= {p for p in paths if sizes[p] > _min}
            if "deeper" in criteria:
                _min_d = min(depths.values())
                mark_set |= {p for p in paths if depths[p] > _min_d}
            if "shallower" in criteria:
                _max_d = max(depths.values())
                mark_set |= {p for p in paths if depths[p] < _max_d}
            if "older" in criteria:
                _newest = max(mtimes.values())
                mark_set |= {p for p in paths if mtimes[p] < _newest}
            if "newer" in criteria:
                _oldest = min(mtimes.values())
                mark_set |= {p for p in paths if mtimes[p] > _oldest}
            if "same" in criteria:
                # Group-level rule: if ALL files in this group share the same
                # byte size, mark every file in the group. Used by Collapse
                # (which collapses groups where every file is marked) — for
                # Delete this would mark all duplicates by hash, only useful
                # combined with another rule like Smaller/Older.
                if len(set(sizes.values())) == 1:
                    mark_set |= set(paths)
            if reverse:
                mark_set = set(paths) - mark_set
            marks |= mark_set
        return marks

    def _collapse_dups_by_rule(self):
        """Collapse every group where the active rules mark AT LEAST one
        file. The semantic is "I've decided about this group — hide it."
        Used after the user picks a deletion rule (e.g. Smaller): every
        multi-file group has a smaller file, so every group collapses,
        which matches the workflow: review → mark → hide → next round.

        For per-group rules like Same size, the same threshold applies —
        groups where every file shares a size mark every member, so they
        also qualify. Reverse flips the per-group selection inside
        _compute_dup_marks before this function sees the result."""
        if not self._dup_display_data:
            return
        marks = self._compute_dup_marks()
        if not marks:
            self.lbl_dup_status.setText(
                _t("No active rules — check at least one to collapse / "
                   "条件が選択されていません — 折畳には1つ以上選択"))
            return
        n_collapsed = 0
        # Re-derive group labels from the table since _dup_display_data
        # doesn't carry labels (just member lists).
        seen_labels = set()
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if not item:
                continue
            label = item.data(Qt.ItemDataRole.UserRole + 2)
            if not label or label in seen_labels:
                continue
            seen_labels.add(label)
            if label in self._collapsed_groups:
                continue
            group_rows = [_r for _r in range(self.table.rowCount())
                          if (self.table.item(_r, 0) and
                              self.table.item(_r, 0).data(Qt.ItemDataRole.UserRole + 2) == label)]
            group_paths = [self.table.get_row_path(_r) for _r in group_rows if self.table.get_row_path(_r)]
            if not group_paths:
                continue
            # Collapse if ANY file in the group is marked. With "Smaller"
            # alone this is every multi-file group; with "Same size" it's
            # every all-equal group; combinations work intuitively.
            if any(p in marks for p in group_paths):
                self._collapsed_groups.add(label)
                head_item = self.table.item(group_rows[0], 0)
                if head_item:
                    head_item.setText(f"▶ {label}")
                n_collapsed += 1
        # Reset rule checkboxes and clear delete marks so the row coloring
        # returns to normal group shading.
        for cb in self._dup_rule_checks.values():
            cb.blockSignals(True)
            cb.setChecked(False)
            cb.blockSignals(False)
        self._refresh_dup_delete_marks()  # clears red marks
        self._apply_row_visibility()
        # Clear the table selection so that any subsequent action (delete,
        # context menu, etc.) doesn't operate on rows the user can no
        # longer see — collapsing hides non-representative rows but they
        # stay in the selection model otherwise.
        self.table.clearSelection()
        self.lbl_dup_status.setText(
            _t(f"{n_collapsed} groups collapsed / {n_collapsed} グループ折畳"))

    def _uncollapse_all_dups(self):
        """Expand every collapsed group (clear _collapsed_groups). Restores
        the ▼ arrow on representative rows and re-shows hidden non-rep
        rows via _apply_row_visibility."""
        if not self._collapsed_groups:
            self.lbl_dup_status.setText(
                _t("Nothing to uncollapse / 展開する折畳グループなし"))
            return
        n = len(self._collapsed_groups)
        self._collapsed_groups.clear()
        # Restore arrows on representative rows
        seen_labels = set()
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if not item:
                continue
            label = item.data(Qt.ItemDataRole.UserRole + 2)
            if not label or label in seen_labels:
                continue
            seen_labels.add(label)
            # First row of each group is the representative — set ▼
            if item.text().startswith("▶"):
                item.setText(f"▼ {label}")
        self._apply_row_visibility()
        self.lbl_dup_status.setText(
            _t(f"{n} groups expanded / {n} グループ展開"))

    def _refresh_dup_delete_marks(self):
        """Recompute the rule-based mark set and apply it to the table's
        SELECTION (not a custom background brush). Now the rule output is
        seeded into Qt's selection model — meaning it looks identical to a
        normal selected row (yellow), Ctrl+click removes individual rows
        from the selection, Shift+click extends, etc. Delete operates on
        whatever's currently selected."""
        if self.config.get("last_mode") != "dup":
            return
        marks = self._compute_dup_marks()
        self._dup_marked_for_delete = marks
        from PyQt6.QtCore import QItemSelection, QItemSelectionModel
        sel_model = self.table.selectionModel()
        # Always clear first. Qt6's ClearAndSelect with an empty
        # QItemSelection is effectively a no-op — without an explicit
        # clear(), unchecking all rules would leave the previously
        # rule-marked rows visually selected, and Delete would still
        # operate on them.
        sel_model.clear()
        if not marks:
            self._sync_dup_delete_btn()
            return
        new_sel = QItemSelection()
        ncols = self.table.columnCount()
        for row in range(self.table.rowCount()):
            path = self.table.get_row_path(row)
            if path in marks:
                top_left = self.table.model().index(row, 0)
                bot_right = self.table.model().index(row, ncols - 1)
                new_sel.select(top_left, bot_right)
        sel_model.select(new_sel,
                         QItemSelectionModel.SelectionFlag.ClearAndSelect)
        # Update Delete button — count from selection so user-adjusted
        # selection (Ctrl+click after) reflects in the count.
        self._sync_dup_delete_btn()

    def _sync_dup_delete_btn(self):
        """Reflect the current visible-selected row count on the Delete
        button. Counts ONLY visible rows — hidden rows (collapsed groups,
        Hide pictures/videos filter) get dropped from the count so the
        user doesn't see 'Delete 320' when only 50 are visible.
        Connected to the selection model so manual Ctrl+click adjustments
        after a rule fires update the label live."""
        if not hasattr(self, "btn_dup_delete"):
            return
        sel_paths = set()
        for idx in self.table.selectionModel().selectedRows():
            r = idx.row()
            if self.table.isRowHidden(r):
                continue
            p = self.table.get_row_path(r)
            if p:
                sel_paths.add(p)
        n = len(sel_paths)
        if n:
            self.btn_dup_delete.setText(_t(f"🗑 Delete {n} / 🗑 削除 {n}"))
            self.btn_dup_delete.setEnabled(True)
        else:
            self.btn_dup_delete.setText(_t("🗑 Delete / 🗑 削除"))
            self.btn_dup_delete.setEnabled(False)

    def _delete_dups_by_rule(self):
        """Delete every file in the current VISIBLE table selection (rule
        marks + Ctrl+click refinements). Skips hidden rows so collapsed
        groups and the Hide pictures/videos filter actually protect their
        files instead of silently being included in the delete."""
        sel_paths = set()
        for idx in self.table.selectionModel().selectedRows():
            r = idx.row()
            if self.table.isRowHidden(r):
                continue
            p = self.table.get_row_path(r)
            if p:
                sel_paths.add(p)
        marks = list(sel_paths)
        if not marks:
            return
        _msg = _t(
            f"Move {len(marks)} files to trash? You can recover them from the "
            f"OS trash folder. / "
            f"{len(marks)} 件をゴミ箱へ移動しますか？OS のゴミ箱から復元可能。")
        if QMessageBox.question(self, _t("Trash duplicates / 重複ゴミ箱へ"), _msg,
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                                ) != QMessageBox.StandardButton.Yes:
            return
        # Use the same XDG-compliant trash mechanism as the rest of the
        # app (front_page.trash_file). Files land in ~/.local/share/Trash
        # with proper .trashinfo metadata so the file manager can show
        # and restore them. Never falls back to os.remove — if trashing
        # fails, the file is left in place and the error is reported.
        from aisearch_front_page import trash_file
        deleted = 0
        errors = []
        batch = []

        # Map paths to rows for snapshotting
        path_to_row = {}
        for r in range(self.table.rowCount()):
            path = self.table.get_row_path(r)
            if path: path_to_row[path] = r

        for p in marks:
            try:
                row = path_to_row.get(p, -1)
                emb = None
                if self.data and "paths" in self.data and p in self.data["paths"]:
                    idx = self.data["paths"].index(p)
                    emb = self.data["embeddings"][idx].clone()

                row_snap = {}
                if row >= 0:
                    row_snap = {
                        "score":       self.table.item(row, 0).text(),
                        "size":        self.table.item(row, 1).text() if self.table.item(row, 1) else "",
                        "name":        self.table.item(row, 2).text() if self.table.item(row, 2) else "",
                        "masked_path": self.table.item(row, 3).text() if self.table.item(row, 3) else "",
                        "sim_data":    self.table.item(row, 0).data(Qt.ItemDataRole.UserRole + 1),
                        "bg_color":    self.table.item(row, 0).background(),
                        "row":         row,
                    }

                if os.path.exists(p):
                    _tp, err = trash_file(p)
                    if err:
                        errors.append(f"{os.path.basename(p)}: {err}")
                        continue
                    deleted += 1
                    if _tp is not None:
                        batch.append({
                            "type": "delete", "orig_path": p, "trash_path": _tp,
                            "emb": emb, "attrs": self.attrs_data.get(p),
                            **(row_snap or {"row": self.table.rowCount(), "score": "0.0", "size": "",
                                            "name": os.path.basename(p), "masked_path": self._mask_path(p)})
                        })

                # Remove from attrs_data
                self.attrs_data.pop(p, None)
                # Remove from app.data["paths"]
                if self.data and "paths" in self.data and p in self.data["paths"]:
                    idx = self.data["paths"].index(p)
                    self.data["paths"].pop(idx)
                    if "embeddings" in self.data:
                        try:
                            import torch as _torch
                            mask = _torch.ones(len(self.data["embeddings"]), dtype=_torch.bool)
                            mask[idx] = False
                            self.data["embeddings"] = self.data["embeddings"][mask]
                        except Exception:
                            pass
            except Exception as e:
                errors.append(f"{os.path.basename(p)}: {e}")

        if batch:
            self._push_undo(batch)

        # Strip deleted paths out of the dup groups, drop singleton groups.
        # Filter the parallel `_dup_group_labels` list in lockstep so
        # surviving groups keep their original G-numbers — without this,
        # later groups shift up to fill the gaps and the user loses track
        # of what they were working on.
        if self._dup_display_data:
            old_labels = getattr(self, "_dup_group_labels", None)
            new_groups = []
            new_labels = []
            for g_idx, grp in enumerate(self._dup_display_data):
                kept = [m for m in grp
                        if (m.get("path", "") if isinstance(m, dict) else m) not in marks]
                if len(kept) > 1:
                    new_groups.append(kept)
                    if old_labels and g_idx < len(old_labels):
                        new_labels.append(old_labels[g_idx])
                    else:
                        new_labels.append(f"G{len(new_labels)+1}")
            self._dup_display_data = new_groups
            self._dup_group_labels = new_labels
        attrs_mod.save(self.current_project, self.attrs_data)
        self._dup_marked_for_delete = set()
        # Reset all rule checkboxes
        for cb in self._dup_rule_checks.values():
            cb.blockSignals(True)
            cb.setChecked(False)
            cb.blockSignals(False)
        # Explicitly clear table selection so deleted rows don't linger as
        # selected ghosts in the selection model. Rule checkbox signals
        # were blocked above, so _refresh_dup_delete_marks doesn't fire —
        # without this clear, the about-to-be-removed rows could remain
        # in the selection until the next user click.
        self.table.clearSelection()
        # Redisplay
        if self._dup_display_data:
            self._display_dup_from_data(self._dup_display_data)
            total_files = sum(len(g) for g in self._dup_display_data)
            self._set_dup_result(
                f"{len(self._dup_display_data)} groups, {total_files} files",
                self._dup_result_threshold or self.spin_threshold.value())
        else:
            self.table.setRowCount(0)
            self._set_dup_result("0 groups, 0 files",
                                 self._dup_result_threshold or self.spin_threshold.value())
        self._save_dup_results()
        msg = f"Deleted {deleted} of {len(marks)} files."
        if errors:
            msg += f"\n{len(errors)} errors:\n" + "\n".join(errors[:5])
            if len(errors) > 5:
                msg += f"\n…and {len(errors) - 5} more"
        QMessageBox.information(self, _t("Delete complete / 削除完了"), msg)

    def _save_dup_results(self):
        if not self._dup_display_data:
            return
        # Only save when actually in dup mode. Otherwise we can clobber a
        # good cache with corrupted data — when called from a non-dup
        # context, _dup_display_data may have been rebuilt from a non-dup
        # table where sim/label UserRole data is missing, producing one
        # giant group with sim=1.0 that overwrites the legitimate scan.
        if self.config.get("last_mode") != "dup":
            return
        # Save under the threshold that PRODUCED the data, NOT whatever the
        # spinner happens to read right now. The spinner can drift away
        # from the actual scan (user changes it without re-scanning) — if
        # we save with the spinner value the cache file ends up labelled
        # with a threshold that doesn't match its groups, and on next load
        # the user sees groups that violate the claimed threshold (e.g.
        # different-sized files clustered under "100%").
        actual_thr = getattr(self, "_dup_result_threshold", None)
        if not actual_thr:
            actual_thr = self.spin_threshold.value()
        # Path is keyed by the actual threshold's mode bucket, so a 100%
        # spinner-but-70%-data scenario lands in the right file.
        suffix = "hash" if actual_thr >= 100 else f"{actual_thr}pct"
        save_path = os.path.join(attrs_mod.DATA_DIR,
                                  f"dups_{self.current_project}_{suffix}.json")
        data = {
            "project":   self.current_project,
            "threshold": actual_thr,
            "groups":    self._dup_display_data,
        }
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    def _load_dup_results(self, update_spinner=True):
        # Allow caller to override the path so we can load whatever cache
        # exists for the project (not just whatever matches the spinner).
        name = getattr(self, "_dup_cache_path_override", None) or self._dup_file_path()
        if not os.path.exists(name):
            return
        try:
            with open(name, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        pct = data.get("threshold") or self.spin_threshold.value()
        # ALWAYS sync the spinner to the cached threshold — otherwise the
        # spinner can show one value while the displayed groups are from a
        # different scan threshold, leading to user confusion ("the spinner
        # says 100% but these results clearly aren't byte-identical").
        # If the user changes the spinner, they must press ⟳ Scan to refresh.
        self.spin_threshold.blockSignals(True)
        self.spin_threshold.setValue(int(pct))
        self.spin_threshold.blockSignals(False)
        # Apply the dup-99 vs other-mode header layout (hide thumb 1 if 99).
        self._update_header_layout_for_mode()
        _media_exts = tuple(logic.EXT_IMG + logic.EXT_VID)
        def _is_media_path(entry):
            p = entry.get("path", "") if isinstance(entry, dict) else str(entry)
            if p.lower().endswith(_media_exts):
                return True
            try:
                with open(p, 'rb') as _f:
                    return b'\x00' in _f.read(512)
            except OSError:
                return True  # missing file — keep so user can see/clean it up
        # Defensive: if the cache lacks "groups" (malformed, sidecar
        # mistakenly loaded, etc.), bail out cleanly so the dup view
        # shows nothing rather than crashing the whole flow.
        _src_groups = data.get("groups")
        if not isinstance(_src_groups, list):
            self._dup_display_data = None
            self.table.setRowCount(0)
            self.lbl_dup_status.setText(_t(
                "No valid scan cache. Press ⟳ Scan. / "
                "有効なスキャンキャッシュなし。⟳スキャンを押してください。"))
            return
        groups_data = [
            [e for e in g if _is_media_path(e)]
            for g in _src_groups
        ]
        groups_data = [g for g in groups_data if len(g) > 1]
        total_files = sum(len(g) for g in groups_data)
        self._set_dup_result(f"{len(groups_data)} groups, {total_files} files", pct)
        self.table.setHorizontalHeaderLabels([_t("Group / グループ"), _t("Size / サイズ"), _t("Name / 名前"), _t("Path / パス")])
        self.drop_zone.setPixmap(QPixmap())
        self.drop_zone.setText(_t("DUPLICATES\nFINDER / 重複\n検索"))
        self._collapsed_groups.clear()
        # self.attr_panel.show()  # disabled
        self._display_dup_from_data(groups_data)
        self._dup_display_data = groups_data
        self.config["last_mode"] = "dup"
        cfg.save_config(self.config, getattr(self, "current_project", None))
        self._update_mode_buttons("dup")

    def _replace_dup_display_path(self, old_path, new_path):
        """Swap old_path → new_path in every group of _dup_display_data
        so dup-mode lookups (e.g. handle_preview's `path in g_paths`)
        keep working after a rename. Called from every rename site so the
        in-memory dup data stays consistent with the table + filesystem."""
        if not self._dup_display_data or old_path == new_path:
            return
        for grp in self._dup_display_data:
            for m in grp:
                if isinstance(m, dict) and m.get("path") == old_path:
                    m["path"] = new_path

    def _rebuild_dup_display_data(self):
        """Rebuild _dup_display_data from current table rows (after deletions).
        Only meaningful in dup mode — in other modes the table doesn't carry
        the per-row sim score (UserRole+1) or group label (UserRole+2), and
        falling back to text/1.0 silently merges everything into one big
        bogus group. Skip cleanly when not in dup mode."""
        if self.config.get("last_mode") != "dup":
            return
        # Header check belt-and-suspenders: even within dup mode, a
        # transient header swap could leave non-dup data in column 0.
        hdr0 = self.table.horizontalHeaderItem(0)
        if not hdr0 or not hdr0.text().startswith("Group"):
            return
        groups = {}
        order  = []
        for r in range(self.table.rowCount()):
            it0 = self.table.item(r, 0)
            if not it0:
                continue
            label = it0.data(Qt.ItemDataRole.UserRole + 2)
            sim   = it0.data(Qt.ItemDataRole.UserRole + 1)
            # If the row truly lacks the dup-mode metadata, drop it instead
            # of inventing a group. This keeps stray non-dup rows out of
            # the dup data structure.
            if not label or sim is None:
                continue
            path = self.table.get_row_path(r)
            if not path:
                continue
            if label not in groups:
                groups[label] = []
                order.append(label)
            groups[label].append({"path": path, "sim": sim})
        self._dup_display_data = [groups[k] for k in order if len(groups[k]) > 1] or None

    def _display_dup_from_data(self, groups_data, reset_labels=False):
        """Display duplicate groups from saved/loaded data (no sim tensor needed).

        Group labels (G1, G2, …) stay STABLE for the lifetime of a scan/load
        via the parallel `_dup_group_labels` list. Without this, every
        rebuild after a delete shifted later groups up when an earlier one
        became a singleton, making the user lose track of which group they
        were working on.

        Caller responsibility:
          - On fresh scan / project switch: pass reset_labels=True so the
            parallel list is rebuilt as G1..Gn.
          - On delete-driven rebuild: caller must trim
            self._dup_group_labels in lockstep with groups_data so
            surviving groups keep their original labels.
        """
        if reset_labels or not hasattr(self, "_dup_group_labels") \
                or len(self._dup_group_labels) != len(groups_data):
            self._dup_group_labels = [f"G{i+1}" for i in range(len(groups_data))]
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for g_idx, members in enumerate(groups_data):
            grp_label = self._dup_group_labels[g_idx]
            for rank, member in enumerate(members):
                path = member["path"]
                if not os.path.exists(path):
                    continue
                score = f"{'▼ ' if rank == 0 else '  '}{grp_label}"
                row   = self._append_row(score,
                                         logic.get_sz_readable(path),
                                         os.path.basename(path),
                                         self._mask_path(path),
                                         path)
                item0 = self.table.item(row, 0)
                item0.setData(Qt.ItemDataRole.UserRole + 2, grp_label)
                if rank == 0:
                    item0.setToolTip(_t("Click to collapse/expand group / クリックでグループを折りたたみ/展開"))
                item0.setData(Qt.ItemDataRole.UserRole + 1, member["sim"])
                color = self._dup_color(member["sim"], g_idx)
                fg = self._contrast_fg(color)
                for col in range(self.table.columnCount()):
                    self.table.item(row, col).setBackground(color)
                    self.table.item(row, col).setForeground(fg)
        self._cleanup_singleton_groups()
        self._highlight_unmarked_rows()
        # Re-apply rule-based delete highlighting if any rules are active.
        # Without this, switching projects or rescanning loses the marks.
        if hasattr(self, "_dup_rule_checks"):
            self._refresh_dup_delete_marks()
        if self.table.rowCount():
            self._select_row(0)

    # ── Path display ─────────────────────────────────────────────────────────

    def _mask_path(self, path):
        d = os.path.dirname(os.path.abspath(path))
        for base in sorted(self.base_dirs, key=len, reverse=True):
            if d == base or d.startswith(base + os.sep):
                rel = d[len(base):]
                return rel.lstrip(os.sep) or "."
        return d

    # ── Table helpers ────────────────────────────────────────────────────────

    def _update_row_position_label(self):
        """Show 'N / total' for the currently selected row."""
        if not hasattr(self, "row_position_label"):
            return
        total = self.table.rowCount()
        row = self._current_row()
        if total <= 0:
            self.row_position_label.setText("")
        elif row < 0:
            self.row_position_label.setText(f"— / {total}")
        else:
            self.row_position_label.setText(f"{row + 1} / {total}")

    def _current_row(self):
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    def _selected_rows(self):
        return sorted({idx.row() for idx in self.table.selectionModel().selectedRows()})

    def _select_row(self, row):
        self.table.selectRow(row)
        self.table.scrollTo(self.table.model().index(row, 0))

    def _contrast_fg(self, bg_color):
        """Return black or white foreground for readable text on bg_color."""
        if not bg_color or not bg_color.isValid():
            return QColor("#000000") if self.config.get("theme") == "Light" else QColor("#ffffff")
        lum = (bg_color.red() * 299 + bg_color.green() * 587 + bg_color.blue() * 114) / 1000
        # In dark mode use a higher threshold — prefer white on medium-tone backgrounds
        threshold = 128 if self.config.get("theme") == "Light" else 160
        return QColor("#000000") if lum > threshold else QColor("#ffffff")

    def _score_color(self, score_str):
        try:
            s = float(score_str)
        except ValueError:
            return None
        sc = self._score_colors
        if   s >= 0.98: return QColor(sc[0])
        elif s >= 0.92: return QColor(sc[1])
        elif s >= 0.85: return QColor(sc[2])
        elif s >= 0.75: return QColor(sc[3])
        else:           return None

    def _append_row(self, score, size, name, masked_path, full_path):
        row = self.table.rowCount()
        self.table.insertRow(row)
        score_item = NumericItem(str(score))
        score_item.setData(Qt.ItemDataRole.UserRole, full_path)
        score_item.setFlags(score_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, 0, score_item)
        for col, (ItemCls, text) in enumerate([(SizeItem, size), (QTableWidgetItem, name), (QTableWidgetItem, masked_path)], 1):
            item = ItemCls(text)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, col, item)
        try:
            self.table.setItem(row, 4, DateItem(os.path.getmtime(full_path)))
        except Exception:
            self.table.setItem(row, 4, DateItem(0))
        _type_item = QTableWidgetItem(_file_type_str(full_path))
        _type_item.setFlags(_type_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, 5, _type_item)
        color = self._score_color(str(score))
        if color:
            fg = self._contrast_fg(color)
            for col in range(self.table.columnCount()):
                item = self.table.item(row, col)
                if item:
                    item.setBackground(color)
                    item.setForeground(fg)
        self._refresh_attrs_indicator(row, full_path)
        return row

    def _cleanup_singleton_groups(self):
        """In dup mode: remove any group down to one file, then recolor remaining groups."""
        header = self.table.horizontalHeaderItem(0)
        if not header or header.text() != "Group":
            return
        # Map base group label → list of row indices
        group_rows = {}
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item:
                label = item.data(Qt.ItemDataRole.UserRole + 2) or item.text().strip()
                group_rows.setdefault(label, []).append(r)
        # Remove singleton groups
        to_remove = [rows[0] for rows in group_rows.values() if len(rows) == 1]
        for r in sorted(to_remove, reverse=True):
            self.table.removeRow(r)
        # Recolor remaining groups so red/blue alternation stays correct
        self._recolor_dup_groups()

    def _recolor_dup_groups(self):
        """Re-apply alternating red/blue colors based on visible groups only."""
        header = self.table.horizontalHeaderItem(0)
        if not header or header.text() != "Group":
            return

        # Pass 1: assign color index only to groups that have ≥1 visible row
        group_color = {}   # label → display_idx
        display_idx = -1
        prev_label  = None
        for r in range(self.table.rowCount()):
            if self.table.isRowHidden(r):
                continue
            item0 = self.table.item(r, 0)
            if not item0:
                continue
            label = item0.data(Qt.ItemDataRole.UserRole + 2) or item0.text().strip()
            if label != prev_label:
                display_idx += 1
                prev_label   = label
            group_color.setdefault(label, display_idx)

        # Pass 2: apply colors to ALL rows (hidden rows keep consistent color)
        for r in range(self.table.rowCount()):
            item0 = self.table.item(r, 0)
            if not item0:
                continue
            label     = item0.data(Qt.ItemDataRole.UserRole + 2) or item0.text().strip()
            g_idx     = group_color.get(label, 0)
            sim_score = item0.data(Qt.ItemDataRole.UserRole + 1) or 1.0
            color = self._dup_color(sim_score, g_idx)
            fg    = self._contrast_fg(color)
            for col in range(self.table.columnCount()):
                item = self.table.item(r, col)
                if item:
                    item.setBackground(color)
                    item.setForeground(fg)

    def _post_move_dup_cleanup(self):
        header = self.table.horizontalHeaderItem(0)
        if header and header.text() in ("Group", _t("Group / グループ")):
            self._cleanup_singleton_groups()
            self._rebuild_dup_display_data()
            self._save_dup_results()

    def _update_row(self, row, old_path, final_path, overwrite, dest_path, protect_rows=None, push_undo=True):
        """Update a row after a move. Remove the overwritten row if needed.
        protect_rows: set of row indices that must never be removed (e.g. {0} for query row)."""
        # Capture dest attrs before the row is removed (needed for merge below)
        _dest_attrs = {}
        if overwrite and dest_path in self.attrs_data:
            _dest_attrs = dict(self.attrs_data[dest_path])

        if overwrite:
            for r in range(self.table.rowCount()):
                if r == row: continue
                if protect_rows and r in protect_rows: continue
                if os.path.normpath(self.table.get_row_path(r) or "") == os.path.normpath(dest_path):
                    self.table.removeRow(r)
                    if r < row: row -= 1
                    break
        self.table.item(row, 2).setText(os.path.basename(final_path))
        self.table.item(row, 3).setText(self._mask_path(final_path))
        self.table.set_row_path(row, final_path)
        if os.path.normpath(old_path) == os.path.normpath(self.query_path or ""):
            self.query_path = final_path
        if old_path != final_path:
            # Transfer attrs_data entry to new path
            if old_path in self.attrs_data:
                self.attrs_data[final_path] = self.attrs_data.pop(old_path)
            elif final_path not in self.attrs_data:
                self.attrs_data[final_path] = {}
            # Keep whichever entry has more non-empty structured fields.
            # Text fields (note, prompt, etc.) are excluded — they shouldn't
            # outweigh real structured metadata like person_id, tags, CLIP fields.
            if _dest_attrs:
                _TEXT_FIELDS = {"note", "prompt", "neg_prompt", "speech", "seed"}
                _src_attrs = self.attrs_data.get(final_path, {})
                def _count(d):
                    return sum(1 for k, v in d.items()
                               if k not in _TEXT_FIELDS and v not in (None, "", [], {}))
                if _count(_dest_attrs) > _count(_src_attrs):
                    self.attrs_data[final_path] = _dest_attrs
            attrs_mod.save(self.current_project, self.attrs_data)
            # Update preview if it's showing the moved file
            if self.preview_handler.current_path == old_path:
                self.preview_handler.current_path = final_path
            if push_undo:
                self._push_undo({"type": "move", "old_path": old_path, "new_path": final_path})

    # ── Drag-drop (top-level window must accept to enable child widget drops on Linux) ──
    # dragEnterEvent/dragMoveEvent advertise the window as a drop target to the OS/WM.
    # On Linux/xcb without this, external drags never enter and child widgets never see them.
    # dropEvent is intentionally omitted — each drop zone (DropZoneLabel, FileTable viewport)
    # handles its own drop; a window-level handler caused double on_drop() calls on Linux.

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()

    # ── Search ───────────────────────────────────────────────────────────────

    def on_drop(self, path):
        # Exit browse mode immediately so headers/bar update before search starts
        if getattr(self, '_browse_dir', None):
            self._exit_browse_mode()
        # Defer raise/activate to next event loop tick — on X11/Cinnamon, calling
        # activateWindow() during the drop event has no effect because the file
        # manager still owns focus; the timer fires after the drop completes.
        QTimer.singleShot(0, self.raise_)
        QTimer.singleShot(0, self.activateWindow)
        # No database yet → similarity search isn't possible, but preview /
        # tagging / attribute editing still are. Open the file in the preview
        # window so a fresh user can do something on first drop without
        # being forced through Settings → Register → Scan first.
        if not self.data:
            try:
                if hasattr(self, "preview_handler") and self.preview_handler:
                    self.preview_handler.show(path)
            except Exception:
                pass
            return
        self.run_search(path)

    def run_search(self, p):
        if not self.data:
            if getattr(self, '_browse_dir', None):
                self._exit_browse_mode()
            self._update_mode_buttons("search")
            # Explicit search invocation (button / re-run) without a DB —
            # tell the user. on_drop has its own preview fallback before
            # calling here, so this path is reached only by intentional
            # search triggers, not by drag-drop.
            self._require_database(_t("Similarity search / 類似検索"))
            return
        # Cancel any previous search by invalidating its token
        if hasattr(self, '_search_cancel'):
            self._search_cancel[0] = True
        _cancel = [False]
        self._search_cancel = _cancel

        self._search_running = True
        self.query_path = os.path.abspath(p)

        # Show image immediately — load at preview resolution (700px) only.
        # QImageReader.setScaledSize tells the JPEG decoder to use DCT scaling
        # (1/2, 1/4, or 1/8), which is ~10× faster than a full decode.
        from PyQt6.QtGui import QImageReader
        from PyQt6.QtCore import QSize as _QSize
        _reader = QImageReader(self.query_path)
        _reader.setAutoTransform(True)
        _orig = _reader.size()
        _PREVIEW_MAX = 700
        if _orig.isValid() and max(_orig.width(), _orig.height(), 1) > _PREVIEW_MAX:
            _sc = _PREVIEW_MAX / max(_orig.width(), _orig.height())
            _reader.setScaledSize(_QSize(max(1, int(_orig.width() * _sc)),
                                         max(1, int(_orig.height() * _sc))))
        _qimg = _reader.read()
        px = QPixmap.fromImage(_qimg) if not _qimg.isNull() else QPixmap()
        if not px.isNull():
            scaled = px.scaled(330, 330,
                               Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
            self._apply_header_theme()
            self.drop_zone.setPixmap(scaled)
            self.drop_zone.setText("")
            # Image query — clear any stale video rim
            if hasattr(self.drop_zone, "set_rim"):
                self.drop_zone.set_rim(None)
            # Pre-populate preview cache so _render skips its own PIL open
            self.preview_handler._cached_pixmap      = px
            self.preview_handler._cached_pixmap_path = self.query_path
        else:
            # Video — extract first frame with cv2 for the thumbnail
            ext = os.path.splitext(self.query_path)[1].lower()
            frame_px = None
            if ext in ('.mp4', '.mkv', '.mov', '.avi', '.webm'):
                try:
                    import cv2, numpy as np
                    from PyQt6.QtGui import QImage
                    cap = cv2.VideoCapture(self.query_path)
                    ret1, frame1 = cap.read()
                    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                    if total > 1:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, total - 1)
                        ret2, frame2 = cap.read()
                    else:
                        ret2, frame2 = False, None
                    cap.release()
                    if ret1:
                        if ret2 and frame2 is not None:
                            div_w = max(20, frame1.shape[1] // 48)
                            div = np.zeros((frame1.shape[0], div_w, 3), dtype=np.uint8)
                            div[:, :] = [0, 200, 0]  # BGR green
                            combined = np.concatenate([frame1, div, frame2], axis=1)
                        else:
                            combined = frame1
                        combined_rgb = cv2.cvtColor(combined, cv2.COLOR_BGR2RGB)
                        h, w, ch = combined_rgb.shape
                        qimg = QImage(combined_rgb.data, w, h, w * ch,
                                      QImage.Format.Format_RGB888)
                        frame_px = QPixmap.fromImage(qimg)
                except Exception:
                    pass
            _is_video_query = ext in ('.mp4', '.mkv', '.mov', '.avi', '.webm')
            if frame_px and not frame_px.isNull():
                scaled = frame_px.scaled(330, 330,
                                         Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation)
                self._apply_header_theme()
                self.drop_zone.setPixmap(scaled)
                self.drop_zone.setText("")
            else:
                self.drop_zone.setPixmap(QPixmap())
                self.drop_zone.setText(_t("▶ VIDEO / ▶ 動画") if _is_video_query else "?")
            if hasattr(self.drop_zone, "set_rim"):
                self.drop_zone.set_rim("#00ff00", 4) if _is_video_query else self.drop_zone.set_rim(None)

        # Show preview immediately before the background search starts
        self.preview_handler.show(self.query_path)
        self._refresh_inline_attrs(self.query_path)

        # Lock preview so it doesn't update again until search finishes
        self._lock_preview = True

        # Restore "Score" column header; exit browse mode if active; clear table immediately
        if self._browse_dir:
            self._exit_browse_mode()
        self.table.setHorizontalHeaderLabels([_t("Score / スコア"), _t("Size / サイズ"), _t("Name / 名前"), _t("Path / パス"), _t("Date / 日付"), _t("Type / 種類")])
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)   # clear browse/old listing immediately, don't wait for worker
        self.config["last_mode"] = "search"
        cfg.save_config(self.config, getattr(self, "current_project", None))
        self._update_mode_buttons("search")
        self.btn_find_dups.setEnabled(False)
        self.search_progress.show()
        self.search_status_label.setText(_t("🔍 Analyzing image… / 🔍 画像を解析中…"))
        self.search_status_label.show()
        self.statusBar().showMessage("Analyzing image…")
        try:
            from aisearch_debug import dbg as _dbg
            _dbg(f"search start: {p}")
        except Exception:
            pass

        import threading, queue as _queue
        _q = _queue.Queue()
        _query_path  = self.query_path
        _data        = self.data          # snapshot — avoids race if project switches
        _feedback    = self.feedback_data

        _path_idx    = getattr(self, '_path_idx', {})
        _base_dirs   = [os.path.normpath(d) for d in (self.base_dirs or [])]

        def _worker():
            try:
                # Fast path: file is already indexed — reuse stored embedding (O(1), symlink-safe)
                emb = None
                idx = _path_idx.get(os.path.realpath(_query_path))
                if idx is not None:
                    emb = _data["embeddings"][idx].unsqueeze(0)
                    _q.put(("status", "DB hit — searching…"))
                else:
                    _q.put(("status", "Not in DB — running CLIP…"))
                    emb = logic.extract_feature(_query_path)
                if emb is None:
                    _q.put(("error", "Could not extract features from image.")); return
                raw_sims = st_util.cos_sim(emb, _data["embeddings"])[0]
                # Restrict ranking to project base_dirs — DB contains every
                # watch-dir file (e.g. Downloads), but search results should
                # only come from the active project. If the filter would leave
                # 0 candidates (misconfigured base_dirs, paths normalised
                # differently, etc.), skip it instead of returning an empty
                # result list.
                n_allowed = len(_data["paths"])
                if _base_dirs and n_allowed:
                    _allowed = torch.zeros(n_allowed, dtype=torch.bool)
                    for _i, _p in enumerate(_data["paths"]):
                        _pn = os.path.normpath(_p)
                        for _bd in _base_dirs:
                            if _pn == _bd or _pn.startswith(_bd + os.sep):
                                _allowed[_i] = True
                                break
                    n_match = int(_allowed.sum().item())
                    if n_match > 0:
                        raw_sims = raw_sims.masked_fill(~_allowed.to(raw_sims.device), float("-inf"))
                        n_allowed = n_match
                # Rank against ALL eligible files. The display cap
                # (max_search_results) is applied at populate-time, not
                # here — otherwise files that rank below the cap by raw
                # cos_sim never get a chance to bubble up via the
                # same-dir proximity / feedback boosts.
                k = n_allowed
                if k == 0:
                    _q.put(("done", (emb, (raw_sims[:0], raw_sims[:0].long())))); return
                top_raw  = torch.topk(raw_sims, k=k)
                cand_idx = top_raw[1]
                cand_sims = raw_sims[cand_idx].clone()
                if _feedback and _feedback["query_embs"].shape[0] > 0:
                    cand_embs = _data["embeddings"][cand_idx]
                    boost     = feedback.boost_scores(emb, cand_embs, _feedback)
                    cand_sims = cand_sims + boost.to(cand_sims.device)
                # Directory proximity boost — surfaces nearby files above visually
                # similar but unrelated files. Boosts are small so visual similarity
                # still dominates; they just tip the balance when scores are close.
                #   same dir      → +0.04
                #   subdir        → +0.02
                #   parent dir    → +0.01
                _query_dir    = os.path.dirname(os.path.abspath(_query_path))
                _query_parent = os.path.dirname(_query_dir)
                _prox_boost = torch.zeros(len(cand_idx))
                for _ci, _raw_idx in enumerate(cand_idx.tolist()):
                    _d = os.path.dirname(os.path.abspath(_data["paths"][_raw_idx]))
                    if _d == _query_dir:
                        _prox_boost[_ci] = 0.04
                    elif _d.startswith(_query_dir + os.sep):
                        _prox_boost[_ci] = 0.02
                    elif _d == _query_parent:
                        _prox_boost[_ci] = 0.01
                cand_sims = cand_sims + _prox_boost.to(cand_sims.device)
                paths_arr     = [_data["paths"][i] for i in cand_idx.tolist()]
                def _dir_rank(p):
                    d = os.path.dirname(os.path.abspath(p))
                    if d == _query_dir:                    return 0  # C/B/ exactly
                    if d.startswith(_query_dir + os.sep):  return 1  # C/B/D/, C/B/E/, …
                    if d == _query_parent:                 return 2  # C/
                    return 3                                          # everything else
                alpha_order = sorted(range(len(paths_arr)),
                                     key=lambda i: (
                                         _dir_rank(paths_arr[i]),
                                         os.path.dirname(paths_arr[i]),
                                         os.path.basename(paths_arr[i])))
                pre = torch.tensor(alpha_order, dtype=torch.long)
                cand_sims = cand_sims[pre]
                cand_idx  = cand_idx[pre]
                order = torch.argsort(cand_sims, descending=True, stable=True)
                # top[0] is the displayed score (raw cos_sim), top[1] is the
                # path index. Boosts still drive ORDER, but the score column
                # reflects actual similarity — otherwise feedback / proximity
                # boosts pile a lot of mediocre matches at 0.9999 (the
                # 1.0-cap), making the column meaningless.
                _final_idx = cand_idx[order]
                _raw_at_final = raw_sims[_final_idx].cpu()
                top = (_raw_at_final, _final_idx)
                _q.put(("done", (emb, top)))
            except Exception as e:
                import traceback
                _q.put(("error", f"{e}\n\n{traceback.format_exc()}"))

        def _poll():
            # This search was superseded — stop polling silently
            if _cancel[0]:
                return
            try:
                msg, payload = _q.get_nowait()
            except Exception:
                # Still running — check again soon
                QTimer.singleShot(50, _poll)
                return
            # Re-check after dequeue in case a new search started while we waited
            if _cancel[0]:
                return
            if msg == "status":
                self.statusBar().showMessage(payload)
                self.search_status_label.setText(f"🔍 {payload}")
                QTimer.singleShot(50, _poll)
                return
            if msg == "error":
                # Show error in status bar; still display the query image as row 0
                first_line = payload.split("\n")[0]
                self.statusBar().showMessage(_t(f"Search error: {first_line} / 検索エラー: {first_line}"), 6000)
                self._populate_search_results(
                    (torch.zeros(0, device='cpu'), torch.zeros(0, dtype=torch.long, device='cpu')),
                    _query_path, _data)
                self._finish_search(None, None, _query_path)
            else:
                emb, top = payload
                self.statusBar().showMessage("Populating results…")
                self._populate_search_results(top, _query_path, _data)
                self._finish_search(emb, top, _query_path)

        if not hasattr(self, '_search_executor') or self._search_executor is None:
            self._warmup_search_executor()
        self._search_executor.submit(_worker)
        self.statusBar().showMessage("Analyzing image…")
        QTimer.singleShot(50, _poll)

    def _populate_search_results(self, top, query_path, data):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self._append_row("1.0000",
                          logic.get_sz_readable(query_path),
                          os.path.basename(query_path),
                          self._mask_path(query_path),
                          query_path)
        # Display cap — the worker ranks every eligible file, but the
        # table only shows the top N. Files skipped here (query itself,
        # missing on disk) don't count toward the cap.
        _display_cap = max(1, int(self.config.get("max_search_results", 500)))
        _displayed = 0
        for s, i in zip(top[0], top[1]):
            if _displayed >= _display_cap: break
            fp = data["paths"][i]
            if os.path.abspath(fp) == query_path: continue
            if not os.path.exists(fp): continue
            _displayed += 1
            # Cap at 0.9999 so the query image (1.0000) is always row 0 even after
            # feedback boost pushes some scores above 1.0
            score_str = f"{min(s.item(), 0.9999):.4f}"
            self._append_row(score_str,
                             logic.get_sz_readable(fp),
                             os.path.basename(fp),
                             self._mask_path(fp),
                             fp)
        # Sort by score descending — query image (1.0000) is always on top
        self.table.horizontalHeader().setSortIndicator(0, Qt.SortOrder.DescendingOrder)
        self.table.setSortingEnabled(True)
        if self.table.rowCount():
            self._select_row(0)
            QTimer.singleShot(50, lambda: self.table.setFocus())

    def _finish_search(self, emb, top, query_path):
        try:
            from aisearch_debug import dbg as _dbg
            _dbg(f"search done: {query_path}")
        except Exception:
            pass
        if emb is not None:
            self.query_emb = emb
        self._search_running  = False
        self.search_progress.hide()
        self.search_status_label.hide()
        self.statusBar().clearMessage()
        self.btn_find_dups.setEnabled(True)
        self._lock_preview = False
        # Trigger preview for the currently selected row (row 0 = query image)
        self.handle_preview()

    # ── Browse mode ──────────────────────────────────────────────────────────

    def _update_mode_buttons(self, mode):
        """Highlight the active mode button; show dup controls only in dup mode."""
        _sep_colors = {"search": "#2a8ad4", "dup": "#9b6dff", "browse": "#3a8a3a"}
        for btn, m in [(self.btn_mode_search, "search"),
                       (self.btn_find_dups,   "dup"),
                       (self.btn_browse,       "browse")]:
            active_ss, inactive_ss = self._mode_styles[m]
            btn.setStyleSheet(active_ss if m == mode else inactive_ss)
        self._dup_controls_widget.setVisible(mode == "dup")
        if hasattr(self, "btn_apply_rules"):
            self.btn_apply_rules.setVisible(mode == "browse")
        pw = getattr(getattr(self, 'preview_handler', None), 'window', None)
        if pw:
            pw.set_mode_color(_sep_colors.get(mode, '#1a1a1a'))

    def _enter_search_mode(self):
        """Return to search mode — search selected row, last query, or just reset."""
        if getattr(self, '_browse_dir', None):
            self._exit_browse_mode()
        self.config["last_mode"] = "search"
        self._update_header_layout_for_mode()
        # Prefer the currently selected file (useful when coming from dup/browse)
        path = None
        row = self._current_row()
        if row >= 0:
            p = self.table.get_row_path(row)
            if p and os.path.exists(p):
                path = p
        if path is None and self.query_path and os.path.exists(self.query_path):
            path = self.query_path
        if path:
            self.run_search(path)
        else:
            self.table.setHorizontalHeaderLabels([_t("Score / スコア"), _t("Size / サイズ"), _t("Name / 名前"), _t("Path / パス"), _t("Date / 日付"), _t("Type / 種類")])
            self.table.setRowCount(0)
            self.config["last_mode"] = "search"
            cfg.save_config(self.config, getattr(self, "current_project", None))
            self._update_mode_buttons("search")

    def _enter_browse_mode(self, directory=None):
        # Switching out of dup mode → restore section 1 visibility
        """Show all files in a directory (ls mode). directory=None uses selected row's folder."""
        if directory is None:
            row = self._current_row()
            if row >= 0:
                path = self.table.get_row_path(row)
                directory = os.path.dirname(os.path.abspath(path)) if path else None
            if not directory and self.base_dirs:
                directory = self.base_dirs[0]
            # Fall back to watch dir, then home
            if not directory:
                _watch = cfg.load_config().get("watch_dirs", [])
                _watch = [d for d in _watch if d and os.path.isdir(d)]
                if _watch:
                    directory = _watch[0]
            if not directory:
                directory = os.path.expanduser("~")
        if not directory or not os.path.isdir(directory):
            return

        self._browse_dir = directory
        self.config["last_mode"] = "browse"
        cfg.save_config(self.config, getattr(self, "current_project", None))
        self._update_mode_buttons("browse")
        self._update_header_layout_for_mode()

        valid_exts = tuple(
            ext.lower() for ext in (logic.EXT_IMG + logic.EXT_VID))
        try:
            entries = os.listdir(directory)
        except PermissionError:
            return
        files = sorted(
            (os.path.join(directory, f)
             for f in entries
             if f.lower().endswith(valid_exts)
             and os.path.isfile(os.path.join(directory, f))),
            key=lambda p: os.path.getmtime(p),
            reverse=True  # newest first
        )

        self.table.setHorizontalHeaderLabels(["#", _t("Size / サイズ"), _t("Name / 名前"), _t("Path / パス"), _t("Date / 日付"), _t("Type / 種類")])
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for i, fp in enumerate(files):
            self._append_row(str(i + 1),
                             logic.get_sz_readable(fp),
                             os.path.basename(fp),
                             self._mask_path(fp),
                             fp)
        self.table.horizontalHeader().setSortIndicator(4, Qt.SortOrder.DescendingOrder)
        self.table.setSortingEnabled(True)

        if self.table.rowCount():
            self._select_row(0)
            self.table.scrollToTop()
        self.table.setFocus()

    def _apply_rules_step(self):
        """Toggle the bulk Apply Rules walk.

        First click: starts a QTimer that processes every visible table
        row directly — parse filename rules → write to attrs → rename on
        disk if needed. Second click: stops mid-walk.

        Direct calls (no Down-arrow simulation, no preview rebuild per
        file). Status bar shows progress. One save at the end.
        """
        from PyQt6.QtCore import QTimer
        # Toggle off if already running.
        if getattr(self, "_apply_rules_running", False):
            self._apply_rules_running = False
            return
        tbl = self.table
        if tbl is None or tbl.rowCount() == 0:
            return
        proj = getattr(self, "current_project", None)
        rules = attrs_mod.load_filename_rules(proj)
        # Coded-field one-way rules — drive parse_filename_rules and
        # rename_file_to_match_entry below.
        one_way = [r for r in rules
                   if r.get("field") and (
                       r.get("one_way") or r.get("extract")
                       or '/' in r.get("pattern", ""))]
        # Tag-group rules (path-scoped, contain '/') — applied via
        # attrs_mod.apply_path_rules. Without this, rules like
        # "Nastia/" → ModelImage = a0 silently no-op'd.
        path_tag_rules = [r for r in rules
                          if r.get("tag_group")
                          and '/' in r.get("pattern", "")]
        # Snapshot the row → path map at start of run so concurrent table
        # mutations (e.g. row removal) don't shift indices mid-walk.
        paths = []
        for r in range(tbl.rowCount()):
            p = tbl.get_row_path(r)
            if p:
                paths.append((r, p))
        if not paths:
            return
        self._apply_rules_running = True
        self._apply_rules_paths = paths
        self._apply_rules_idx = 0
        self._apply_rules_one_way = one_way
        self._apply_rules_path_tag = path_tag_rules
        self._apply_rules_proj = proj
        self._apply_rules_renamed = 0
        try:
            self.btn_apply_rules.setText(_t("⏸ Stop / ⏸ 停止"))
        except Exception:
            pass
        if not hasattr(self, "_apply_rules_timer") or self._apply_rules_timer is None:
            self._apply_rules_timer = QTimer(self)
            self._apply_rules_timer.setSingleShot(False)
            self._apply_rules_timer.timeout.connect(self._apply_rules_tick)
        # Tick every 30ms, processing 25 files per tick → ~800 files/sec.
        # Fast enough to fly through 1000 files in 1.5s, slow enough that
        # the status bar update is readable and the toggle stays responsive.
        self._apply_rules_timer.start(30)

    def _apply_rules_tick(self):
        """One tick: process up to BATCH files directly. Updates status bar
        with progress. Stops when paths exhausted or toggle is turned off."""
        BATCH = 25
        if not getattr(self, "_apply_rules_running", False):
            self._apply_rules_finish()
            return
        paths = self._apply_rules_paths
        one_way = self._apply_rules_one_way
        path_tag_rules = getattr(self, "_apply_rules_path_tag", [])
        proj = self._apply_rules_proj
        i = self._apply_rules_idx
        n = len(paths)
        if i >= n:
            self._apply_rules_finish()
            return
        end = min(i + BATCH, n)
        for k in range(i, end):
            row, path = paths[k]
            if not os.path.exists(path):
                continue
            # 1. Apply detect rules to entry.
            if one_way:
                bn = os.path.basename(path)
                stem = os.path.splitext(bn)[0]
                od = attrs_mod.parse_filename_rules(stem, one_way, basename=bn, fullpath=path)
                if od:
                    entry = self.attrs_data.setdefault(path, {})
                    if "P" in od and od["P"] and entry.get("person_id") != od["P"]:
                        entry["person_id"] = od["P"]
                    for field, value in od.items():
                        if field == "P" or not value:
                            continue
                        if entry.get(field.lower()) != value:
                            entry[field.lower()] = value
            # 1b. Apply tag-group path rules (e.g. "Nastia/" → ModelImage = a0).
            # These were silently skipped before — only the coded-field
            # rules were honored. apply_path_rules handles the
            # tag_group / matrix-table semantics.
            if path_tag_rules:
                try:
                    self.attrs_data, _ = attrs_mod.apply_path_rules(
                        self.attrs_data, path, proj, _path_rules=path_tag_rules)
                except Exception:
                    pass
            # 2. Rename if attrs disagree with filename. defer_save so we
            # don't write the JSON N times during the walk.
            if attrs_mod.would_rename(self.attrs_data, path, proj):
                try:
                    new_path = attrs_mod.rename_file_to_match_entry(
                        self.attrs_data, path, project=proj, defer_save=True)
                    if new_path and new_path != path:
                        self._apply_rules_renamed += 1
                        # Update app.data["paths"] + the table row in place.
                        if self.data and "paths" in self.data and path in self.data["paths"]:
                            self.data["paths"][self.data["paths"].index(path)] = new_path
                        try:
                            self.table.set_row_path(row, new_path)
                        except Exception:
                            pass
                        # Keep dup-mode group data consistent.
                        self._replace_dup_display_path(path, new_path)
                except Exception:
                    pass
        self._apply_rules_idx = end
        try:
            self.statusBar().showMessage(
                f"Apply Rules: {end} / {n} files, {self._apply_rules_renamed} renamed", 2000)
        except Exception:
            pass

    def _apply_rules_finish(self):
        """Stop the walk, save attrs once, restore button text."""
        if getattr(self, "_apply_rules_timer", None):
            self._apply_rules_timer.stop()
        try:
            attrs_mod.save(getattr(self, "current_project", None), self.attrs_data)
        except Exception:
            pass
        renamed = getattr(self, "_apply_rules_renamed", 0)
        total = len(getattr(self, "_apply_rules_paths", []))
        self._apply_rules_running = False
        try:
            self.btn_apply_rules.setText(_t("🔧 Apply Rules / 🔧 規則適用"))
        except Exception:
            pass
        try:
            self.statusBar().showMessage(
                f"Apply Rules done: {renamed} of {total} files renamed.", 5000)
        except Exception:
            pass

    def _apply_path_rules_to_browse_dir(self):
        """Walk the currently-browsed directory recursively and re-apply path
        rules to every supported file. Override semantics — matching rules
        always win. Used to retag a folder's worth of files after editing
        a /Folder/ rule, without touching CLIP/face detection."""
        d = getattr(self, "_browse_dir", None)
        if not d or not os.path.isdir(d):
            return
        valid_exts = tuple(ext.lower() for ext in (logic.EXT_IMG + logic.EXT_VID))
        proj = getattr(self, "current_project", None)
        path_rules = self.get_path_rules_cached() or []
        if not path_rules:
            self.statusBar().showMessage(
                _t("No path rules to apply. / 適用するパス規則がありません。"), 3000)
            return
        n_total = n_changed = 0
        for root, _dirs, files in os.walk(d):
            for f in files:
                if not f.lower().endswith(valid_exts):
                    continue
                fp = os.path.join(root, f)
                n_total += 1
                self.attrs_data, ch = attrs_mod.apply_path_rules(
                    self.attrs_data, fp, proj, _path_rules=path_rules)
                if ch:
                    n_changed += 1
        # apply_path_rules saves per-file, but a final save is cheap and
        # guarantees the JSON reflects the in-memory state if any save was
        # skipped (e.g. one threw). Refresh preview if it's open.
        try:
            attrs_mod.save(proj, self.attrs_data)
        except Exception:
            pass
        ph = getattr(self, "preview_handler", None)
        pw = getattr(ph, "window", None) if ph else None
        if pw is not None:
            cur = getattr(pw, "_attr_path", None)
            if cur:
                try: pw._refresh_attrs_inner(cur)
                except Exception: pass
        self.statusBar().showMessage(
            _t(f"Path rules applied: {n_changed} of {n_total} files changed. / "
               f"パス規則適用：{n_total}件中{n_changed}件更新。"), 5000)

    def _exit_browse_mode(self):
        self._browse_dir = None
        self._update_mode_buttons("search")
        # Restore query image thumbnail (or clear if no query)
        if self.query_path and os.path.exists(self.query_path):
            self._update_drop_zone_thumb(self.query_path)
        else:
            self.drop_zone.setPixmap(QPixmap())
            self.drop_zone.setText(_t("DROP IMAGE / 画像をドロップ"))

    # ── Event handlers ───────────────────────────────────────────────────────

    def on_double_click(self):
        row = self._current_row()
        if row < 0: return
        path = self.table.get_row_path(row)
        if not path: return
        if self.config.get("dbl_click_spread", False):
            is_video = path.lower().endswith(('.mp4', '.mkv', '.mov', '.avi', '.webm'))
            self.preview_handler._toggle_physical_geometry(path, is_video)
        else:
            front_page.open_external_viewer(path, keep_open=self.keep_viewer_open)

    def _on_right_click(self, pos):
        item = self.table.itemAt(pos)
        if item:
            self.table.selectRow(self.table.row(item))
        # Build a fresh menu so Open with… can be populated per-file
        # type (the cached self.popup_menu can't do that).
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QAction
        import aisearch_file_manager as _fm
        menu = QMenu(self.table)
        row = self._current_row()
        path = self.table.get_row_path(row) if row >= 0 else None

        if path and os.path.isfile(path):
            act_open = QAction(_t("Open / 開く"), self)
            act_open.triggered.connect(lambda _, p=path: _fm.open_default(p))
            menu.addAction(act_open)
            sub = menu.addMenu(_t("Open with / アプリで開く"))
            for label, argv in _fm.app_choices(path):
                a = QAction(label, self)
                a.triggered.connect(lambda _, c=argv, p=path: _fm.open_with(c, p))
                sub.addAction(a)
            # Set this file as the rep pic for its assigned person
            entry = (self.attrs_data or {}).get(path) or {}
            pid = (entry.get("person_id") or "").strip()
            if pid and pid != "000":
                act_rep = QAction(
                    _t(f"Set as rep pic for {pid} / {pid} の代表画像に設定"), self)
                act_rep.triggered.connect(lambda _, p=path, q=pid: self._set_rep_pic(q, p))
                menu.addAction(act_rep)
                act_base = QAction(
                    _t(f"Set as BASE face for {pid} / {pid} の基準顔に設定"), self)
                act_base.triggered.connect(lambda _, p=path, q=pid: self._set_base_face(q, p))
                menu.addAction(act_base)
                act_add = QAction(
                    _t(f"Add this face to {pid} samples / {pid} のサンプルに追加"), self)
                act_add.triggered.connect(lambda _, p=path, q=pid: self._add_face_sample(q, p))
                menu.addAction(act_add)
                # Wrong-face cleanup: drop this file's contribution to
                # `pid` and clear person_id. Use when the assignment was
                # incorrect and you don't want this face polluting future
                # matches under that pid.
                act_dismantle = QAction(
                    _t(f"🚮 Dismantle face data from {pid} / "
                       f"🚮 {pid} から顔データを解除"), self)
                act_dismantle.triggered.connect(
                    lambda _, p=path: self._dismantle_face_assignment(p))
                menu.addAction(act_dismantle)
            # "Assign new person ID" lives on the preview window's P
            # attribute field (➕ button) — that's the workflow point
            # right after the user runs Update Face and sees the wrong
            # match.
            menu.addSeparator()

        menu.addAction(_t("🗂 File Manager / 🗂 ファイルマネージャ"),
                       self._open_fm_for_current_row)
        menu.addAction(_t("📝 Rename (F2) / 📝 改名 (F2)"),
                       lambda: self.rename_file(from_menu=True))
        menu.addAction(_t("📦 Move to... (M) / 📦 移動... (M)"),
                       self.move_to_folder_manually)
        menu.addSeparator()
        menu.addAction(_t("🗑️ Delete / 🗑️ 削除"), self.delete_file)
        menu.exec(self.table.mapToGlobal(pos))

    def _update_drop_zone_thumb(self, path):
        """Update the header thumbnail to show path (used in browse mode)."""
        if not path or not os.path.exists(path):
            return
        from PyQt6.QtGui import QImageReader
        from PyQt6.QtCore import QSize as _QSize
        ext = os.path.splitext(path)[1].lower()
        _reader = QImageReader(path)
        _reader.setAutoTransform(True)
        _orig = _reader.size()
        _PREVIEW_MAX = 700
        if _orig.isValid() and max(_orig.width(), _orig.height(), 1) > _PREVIEW_MAX:
            _sc = _PREVIEW_MAX / max(_orig.width(), _orig.height())
            _reader.setScaledSize(_QSize(max(1, int(_orig.width() * _sc)),
                                         max(1, int(_orig.height() * _sc))))
        _qimg = _reader.read()
        px = QPixmap.fromImage(_qimg) if not _qimg.isNull() else QPixmap()
        if not px.isNull():
            scaled = px.scaled(330, 330,
                               Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
            self._apply_header_theme()
            self.drop_zone.setPixmap(scaled)
            self.drop_zone.setText("")
            if hasattr(self.drop_zone, "set_rim"):
                self.drop_zone.set_rim(None)
        else:
            # Video — extract first AND last frame, joined with a green
            # divider (matches search-mode behavior so browsing videos
            # always shows the split view).
            frame_px = None
            if ext in ('.mp4', '.mkv', '.mov', '.avi', '.webm'):
                try:
                    import cv2, numpy as np
                    from PyQt6.QtGui import QImage
                    cap = cv2.VideoCapture(path)
                    ret1, frame1 = cap.read()
                    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                    if total > 1:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, total - 1)
                        ret2, frame2 = cap.read()
                    else:
                        ret2, frame2 = False, None
                    cap.release()
                    if ret1:
                        if ret2 and frame2 is not None:
                            div_w = max(20, frame1.shape[1] // 48)
                            div = np.zeros((frame1.shape[0], div_w, 3), dtype=np.uint8)
                            div[:, :] = [0, 200, 0]  # BGR green
                            combined = np.concatenate([frame1, div, frame2], axis=1)
                        else:
                            combined = frame1
                        rgb = cv2.cvtColor(combined, cv2.COLOR_BGR2RGB)
                        h, w, ch = rgb.shape
                        qimg = QImage(rgb.data, w, h, w * ch, QImage.Format.Format_RGB888)
                        frame_px = QPixmap.fromImage(qimg)
                except Exception:
                    pass
            _is_video_browse = ext in ('.mp4', '.mkv', '.mov', '.avi', '.webm')
            if frame_px and not frame_px.isNull():
                scaled = frame_px.scaled(330, 330,
                                         Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation)
                self._apply_header_theme()
                self.drop_zone.setPixmap(scaled)
                self.drop_zone.setText("")
            else:
                self.drop_zone.setPixmap(QPixmap())
                self.drop_zone.setText(_t("▶ VIDEO / ▶ 動画") if _is_video_browse else "?")
            if hasattr(self.drop_zone, "set_rim"):
                self.drop_zone.set_rim("#00ff00", 4) if _is_video_browse else self.drop_zone.set_rim(None)

    def handle_preview(self):
        if self._lock_preview: return
        row = self._current_row()
        if row < 0:
            self._refresh_inline_attrs(None)
            return
        path = self.table.get_row_path(row)
        if path and not os.path.exists(path):
            if row == 0:
                # Query image — never auto-remove; it may be on a slow/remote filesystem
                return
            self._remove_missing_file(row, path)
            return
        if self._browse_dir:
            # Debounce thumbnail update — skip rapid scrolling, only render when settled
            if not hasattr(self, '_thumb_timer'):
                from PyQt6.QtCore import QTimer as _QT
                self._thumb_timer = _QT(self)
                self._thumb_timer.setSingleShot(True)
                self._thumb_timer.timeout.connect(lambda: self._update_drop_zone_thumb(
                    self.table.get_row_path(self._current_row()) or ""))
            self._thumb_timer.start(80)
        # Synchronously update attrs_data with path rules so deferred _refresh_attrs sees correct values immediately
        _pr = self.get_path_rules_cached()
        if _pr:
            self.attrs_data, _ = attrs_mod.apply_path_rules(
                self.attrs_data, path, self.current_project, _path_rules=_pr)

        # Update the two thumbnails based on the current mode:
        #   dup mode    → thumb1 = 1st of group, thumb2 = 2nd of group
        #   search mode → thumb1 = top result (row 0), thumb2 = selected row
        #   browse mode → thumb1 = selected row, thumb2 cleared
        # In dup/search modes, a white border highlights whichever thumb
        # corresponds to the currently selected row.
        _mode = self.config.get("last_mode")
        _is_dup = _mode == "dup" and self._dup_display_data
        thumb1_path = thumb2_path = None
        strip_paths = []   # rest of group members (3rd, 4th, ...) for filmstrip
        # Build the full list of paths for the grid (drop_zone falls back to
        # placeholder when empty; strip cells render the actual thumbnails).
        all_paths = []
        if _is_dup:
            # Find the group containing `path` without stat-ing every member —
            # at low thresholds a group can have 100+ files, and the original
            # `os.path.exists` filter ran on every group member of every
            # group on every click, costing O(total_files) stat syscalls per
            # navigation. Now we locate the group by membership only, then
            # cap to the visible window, and existence-check just those.
            _STRIP_MAX = 8
            for grp in self._dup_display_data:
                grp_paths = [m["path"] for m in grp]
                if path in grp_paths:
                    if len(grp_paths) > _STRIP_MAX:
                        _i = grp_paths.index(path)
                        _half = _STRIP_MAX // 2
                        _start = max(0, min(_i - _half, len(grp_paths) - _STRIP_MAX))
                        window = grp_paths[_start:_start + _STRIP_MAX]
                    else:
                        window = grp_paths
                    all_paths = [p for p in window if os.path.exists(p)]
                    break
            if not all_paths and self.table.rowCount() > 0:
                top = self.table.get_row_path(0)
                if top: all_paths = [top]
        elif _mode == "search" and self.table.rowCount() > 0:
            top = self.table.get_row_path(0)
            all_paths = [top, path] if top and path != top else [top or path]
        elif _mode == "browse":
            all_paths = [path]
        # Strip cells render the entire path list (placeholder shown when empty)
        self._update_filmstrip_cells(all_paths, selected_path=path)
        # No outer rim (cells handle their own selection rim)
        self._set_zone_selected(self.thumb_outer, False)

        # Skip the preview window entirely if user disabled it (any mode).
        if getattr(self, "_chk_disable_preview", None) and self._chk_disable_preview.isChecked():
            self._refresh_inline_attrs(path)
            return

        self.preview_handler.show(path)
        self._refresh_inline_attrs(path)

    def _update_filmstrip_cells(self, paths, selected_path=None):
        """Lay out dup-mode group cells (members beyond the second) in a
        grid. Column count depends on the dup threshold:
            threshold = 100 → 2 columns (pairs are the common case)
            threshold < 100 → 4 columns (similarity matches usually have many)
        Selected cell gets a 3px white border. Click jumps the table."""
        # Choose grid column count by mode + threshold:
        #   search mode → 2 cols (TOP | SELECTED, side-by-side wide view)
        #   dup mode 100% → 2 cols (pair view)
        #   dup mode  <99 → 4 cols (multi-member grid)
        try:
            thr = int(self.spin_threshold.value()) if hasattr(self, "spin_threshold") else 100
        except Exception:
            thr = 100
        _mode = self.config.get("last_mode")
        if _mode == "search":
            cols = 2
        else:
            cols = 2 if thr >= 100 else 4
        # Grow cells if needed
        while len(self._strip_cells) < len(paths):
            cell = DropZoneLabel()
            cell._drop_callback = self.on_drop
            cell.setMinimumWidth(40)
            self._strip_cells.append(cell)
        # Detach everything from the layout so we can re-add in order.
        while self._strip_layout.count():
            _it = self._strip_layout.takeAt(0)
            _w = _it.widget() if _it else None
            if _w is not None:
                _w.setParent(self.thumb_outer)
        # When there are paths to display, hide the "DROP IMAGE" placeholder
        # and use strip cells. When empty, show the placeholder.
        if paths:
            self.drop_zone.hide()
            for idx, cell in enumerate(self._strip_cells):
                r, c = divmod(idx, cols)
                self._strip_layout.addWidget(cell, r, c)
        else:
            # Idle — show the drop-target placeholder taking the whole area
            self.drop_zone.show()
            self._strip_layout.addWidget(self.drop_zone, 0, 0)
        # Re-apply project bg before showing thumbnails so the color sticks
        self._apply_header_theme()
        # Update visible cells
        for i, cell in enumerate(self._strip_cells):
            if i < len(paths):
                p = paths[i]
                # Video filmstrip cells show the split first+last view to match
                # the big drop-zone thumbnail and the preview window. Each
                # video decode (first+last frame) is slow, so cache the
                # rendered path+mtime on the cell — when the user navigates
                # rows within the same dup group, the cells render the same
                # files and we only need to update the rim, not redecode.
                try:
                    _mt = os.path.getmtime(p)
                except OSError:
                    _mt = 0
                if (getattr(cell, "_thumb_path", None) != p or
                        getattr(cell, "_thumb_mtime", None) != _mt):
                    self._set_zone_image(cell, p, fast=False)
                    cell._thumb_path = p
                    cell._thumb_mtime = _mt
                # Border priority: purple = selected; green = video; else none.
                # Painted via DropZoneLabel.set_rim (paintEvent) instead of QSS
                # so the project-bg fill in paintEvent doesn't visually erase
                # the rim.
                _is_vid = p.lower().endswith(logic.EXT_VID)
                if p == selected_path:
                    cell.set_rim("#9b6dff", 4)
                elif _is_vid:
                    cell.set_rim("#00ff00", 4)
                else:
                    cell.set_rim(None)
                cell.show()
                cell.setCursor(Qt.CursorShape.PointingHandCursor)
                # Single click: jump table row + open preview window
                cell.mousePressEvent = lambda _e, _p=p: self._click_thumb(_p)
                cell.mouseDoubleClickEvent = lambda _e, _p=p: self._click_thumb(_p)
            else:
                cell.hide()

    def _jump_to_path(self, path):
        """Select the table row whose path matches."""
        for row in range(self.table.rowCount()):
            if self.table.get_row_path(row) == path:
                self._select_row(row)
                break

    def _click_thumb(self, path):
        """Thumbnail cell clicked: select the row + force-open the preview
        (overrides the 'Disable preview' checkbox — clicking a thumb is an
        explicit request to inspect that file)."""
        self._jump_to_path(path)
        if path and os.path.exists(path):
            self.preview_handler.show(path)
            pw = getattr(self.preview_handler, "window", None)
            if pw is not None:
                pw.setWindowState(pw.windowState() & ~Qt.WindowState.WindowMinimized)
                pw.show()
                pw.raise_()
                pw.activateWindow()

    def _set_zone_selected(self, zone_frame, on: bool):
        """Toggle a purple border around a thumbnail zone to show it matches
        the currently selected row. Purple matches the dup-mode accent and
        stands out better than white against bright/varied images."""
        if zone_frame is None:
            return
        zone_frame.setStyleSheet(
            "QFrame { border: 3px solid #9b6dff; }" if on
            else "QFrame { border: none; }")

    def _set_zone_image(self, zone, path, fast: bool = False):
        """Load `path` as a thumbnail into the given drop-zone label.
        fast=True → for video files, decode only the first frame (skips the
        slow last-frame seek). Used for small filmstrip cells; the big
        drop_zone keeps the combined first+last view.
        Pass path=None to clear the zone."""
        # Green outer rim for video files (mirrors the preview window).
        # Use set_rim so it paints in paintEvent — a QSS border was being
        # visually overlapped by the project-bg fillRect and disappearing.
        _is_vid = bool(path) and path.lower().endswith(logic.EXT_VID)
        if hasattr(zone, "set_rim"):
            zone.set_rim("#00ff00", 4) if _is_vid else zone.set_rim(None)
        if not path or not os.path.exists(path):
            zone.setPixmap(QPixmap())
            zone.setText("")
            return
        from PyQt6.QtGui import QImageReader
        from PyQt6.QtCore import QSize as _QSize
        _reader = QImageReader(path)
        _reader.setAutoTransform(True)
        _orig = _reader.size()
        _MAX = 700
        if _orig.isValid() and max(_orig.width(), _orig.height(), 1) > _MAX:
            _sc = _MAX / max(_orig.width(), _orig.height())
            _reader.setScaledSize(_QSize(max(1, int(_orig.width() * _sc)),
                                         max(1, int(_orig.height() * _sc))))
        _qimg = _reader.read()
        px = QPixmap.fromImage(_qimg) if not _qimg.isNull() else QPixmap()
        if px.isNull():
            try:
                from PyQt6.QtGui import QImage
                import aisearch_logic as _lg
                rgb = _lg.get_video_thumbnail_rgb(path, first_only=fast)
                if rgb is not None:
                    h, w, _ = rgb.shape
                    qi = QImage(rgb.data, w, h, w * 3, QImage.Format.Format_RGB888).copy()
                    px = QPixmap.fromImage(qi)
            except Exception:
                pass
        if px.isNull():
            zone.setPixmap(QPixmap())
            return
        # The DropZoneLabel rescales its pixmap automatically on resize, so
        # we just hand it the full-resolution image once.
        zone.setPixmap(px)
        zone.setText("")

    def get_path_rules_cached(self):
        """Return path-scoped filename rules, reloading only when the rules file changes."""
        proj = self.current_project or ""
        rules_file = attrs_mod.filename_rules_file_for_project(proj)
        try:
            mtime = os.path.getmtime(rules_file)
        except OSError:
            mtime = 0
        key = (proj, mtime)
        if getattr(self, '_path_rules_cache_key', None) != key:
            fn_rules = attrs_mod.load_filename_rules(proj)
            self._path_rules_cache = [r for r in fn_rules
                                      if (r.get("field") or r.get("tag_group"))
                                      and '/' in r.get("pattern", "")]
            self._path_rules_cache_key = key
        return self._path_rules_cache

    def _remove_missing_file(self, row, path):
        """Remove a file that no longer exists from the table, DB, and embeddings.
        Disk saves are debounced via _flush_missing_removals so rapid scrolling
        through many missing files doesn't freeze the UI on each one."""
        # In-memory removal — fast
        self.attrs_data.pop(path, None)
        if self.data and "paths" in self.data and path in self.data["paths"]:
            idx  = self.data["paths"].index(path)
            keep = [i for i in range(len(self.data["paths"])) if i != idx]
            self.data["paths"]      = [self.data["paths"][i] for i in keep]
            self.data["embeddings"] = self.data["embeddings"][keep]

        # Schedule a debounced save (writes both JSON and the 60+MB .pt once)
        if not getattr(self, '_missing_save_timer', None):
            from PyQt6.QtCore import QTimer as _QT
            self._missing_save_timer = _QT(self)
            self._missing_save_timer.setSingleShot(True)
            self._missing_save_timer.timeout.connect(self._flush_missing_removals)
        self._missing_save_dirty = True
        self._missing_save_timer.start(800)

        # Remove the row and select the next one
        was_query = (row == 0)
        self.table.removeRow(row)
        self._cleanup_singleton_groups()
        self._rebuild_dup_display_data()
        self._save_dup_results()
        new_row = min(row, self.table.rowCount() - 1)
        if new_row >= 0:
            self._select_row(new_row)
            self.table.setFocus()
            if was_query:
                self._rebase_to_row(new_row)

    def _flush_missing_removals(self):
        """Persist accumulated missing-file removals to disk in a single pass."""
        if not getattr(self, '_missing_save_dirty', False):
            return
        self._missing_save_dirty = False
        try:
            attrs_mod.save(self.current_project, self.attrs_data)
        except Exception:
            pass
        try:
            torch.save(self.data,
                       os.path.join(attrs_mod.DATA_DIR, f"features_{self.current_project}.pt"))
        except Exception:
            pass

    # ── Move / rename / delete ───────────────────────────────────────────────

    def on_right_key_press(self):
        row = self._current_row()
        if row < 0: return
        # In browse mode: right arrow re-enters browse on selected file's directory
        if self._browse_dir:
            self._enter_browse_mode()
            self._raise_preview()
            return
        # In search mode, row 0 is the query file — enter browse mode.
        # The File Manager window is opened from the right-click context
        # menu (Open File Manager…), not from the arrow key.
        if row == 0 and self.query_path:
            self._enter_browse_mode()
            self._raise_preview()
            return
        if not self.query_path: return
        # Top file's directory no longer exists (Nemo / external move took it
        # away). Without this guard the move resolves to a now-stale path —
        # in some shapes that lands at the project root. Halt, drop the
        # stale row 0, and promote row 1 as the new query so the next
        # right-press has a valid destination.
        if not os.path.isdir(os.path.dirname(os.path.abspath(self.query_path))):
            if self.table.rowCount() >= 2:
                next_path = self.table.get_row_path(1)
                self.table.removeRow(0)
                if next_path and os.path.exists(next_path):
                    self._rebase_to_row(0)
                    return
            self.query_path = None
            return

        sel_rows = self._selected_rows()
        rows_to_move = sorted([r for r in sel_rows if r != 0], reverse=True)  # high→low so removals don't shift
        multi = len(rows_to_move) > 1
        any_moved = False
        last_row = row

        needs_feedback_reload = False
        for r in rows_to_move:
            old_path = self.table.get_row_path(r)
            if not old_path:
                continue
            new_path, self.data, err = front_page.move_file_physically(
                old_path, self.query_path, self.data, self.current_project,
                mode=self.config.get("move_conflict", "size_check"), parent_win=self)
            if new_path:
                if self.query_emb is not None and self.data and "paths" in self.data:
                    if old_path in self.data["paths"]:
                        idx = self.data["paths"].index(old_path)
                        feedback.record(self.current_project, self.query_emb, self.data["embeddings"][idx])
                        needs_feedback_reload = True
                dest_path = os.path.join(os.path.dirname(os.path.abspath(self.query_path)),
                                         os.path.basename(old_path))
                self._update_row(r, old_path, new_path,
                                 new_path == dest_path,
                                 dest_path,
                                 protect_rows={0})
                any_moved = True
                last_row = r
            elif err and err != "cancelled":
                QMessageBox.critical(self, _t("Move Error / 移動エラー"), _t(f"Could not move file: {err} / ファイルを移動できません: {err}"))

        if any_moved:
            if needs_feedback_reload:
                self.feedback_data = feedback.load(self.current_project)
            self._post_move_dup_cleanup()
            if not multi:
                next_row = last_row + 1 if last_row + 1 < self.table.rowCount() else last_row - 1
                if next_row >= 0:
                    self._select_row(next_row)
            self._raise_preview()

    def _raise_preview(self):
        """Bring the preview window to the front without stealing keyboard focus."""
        w = getattr(self.preview_handler, 'window', None)
        if w and w.isVisible():
            w.raise_()
            QTimer.singleShot(0, self.table.setFocus)

    def _rebase_to_row(self, row):
        """Make the file at row the new search base (same as pressing left arrow)."""
        path = self.table.get_row_path(row)
        if not path:
            return
        self._lock_preview = True
        try:
            self.run_search(path)
        finally:
            self._lock_preview = False
        if self.preview_handler.window and self.preview_handler.window.isVisible():
            QTimer.singleShot(100, lambda: self.preview_handler.show(self.query_path))

    def on_left_key_press(self):
        if getattr(self, '_left_key_busy', False):
            return
        self._left_key_busy = True
        try:
            # In browse mode: left arrow runs a search on the file the
            # user is currently looking at. Was: ran self.query_path
            # (the *previous* search query), which after browsing into
            # a different folder could be a completely unrelated file —
            # the user reported "wrong file on completely different
            # folder, doesn't look like each other".
            if getattr(self, '_browse_dir', None):
                row = self._current_row()
                sel_path = self.table.get_row_path(row) if row >= 0 else None
                self._exit_browse_mode()
                target = (sel_path if (sel_path and os.path.exists(sel_path))
                          else self.query_path)
                if target and os.path.exists(target):
                    self.run_search(target)
                    self.table.scrollToTop()
                    self._select_row(0)
                return
            # In dup mode: left arrow switches to search on the selected file
            if self.config.get("last_mode") == "dup":
                row = self._current_row()
                path = self.table.get_row_path(row) if row >= 0 else None
                if not path and self.query_path and os.path.exists(self.query_path):
                    path = self.query_path
                if path:
                    self.run_search(path)
                return
            row = self._current_row()
            if row >= 0:
                self.run_search(self.table.get_row_path(row))
        finally:
            self._left_key_busy = False

    def _move_to_neighbor(self, direction):
        row      = self._current_row()
        if row < 0: return
        neighbor = row + direction
        if neighbor < 0 or neighbor >= self.table.rowCount(): return

        src_path      = os.path.abspath(self.table.get_row_path(row))
        neighbor_path = os.path.abspath(self.table.get_row_path(neighbor))
        target_dir    = os.path.dirname(neighbor_path)
        if os.path.dirname(src_path) == target_dir: return

        mode      = self.config.get("move_conflict", "size_check")
        dest_path = os.path.join(target_dir, os.path.basename(src_path))
        final_path, overwrite = front_page._resolve_with_size(dest_path, src_path, mode, self)
        if final_path is None: return

        try:
            shutil.move(src_path, final_path)
        except Exception as e:
            QMessageBox.critical(self, _t("Move Error / 移動エラー"), str(e)); return

        import aisearch_attrs as _am
        _am.update_path_in_all_stores(src_path, final_path, self.current_project)
        if self.data and "paths" in self.data:
            if overwrite: front_page._remove_from_data(self.data, dest_path)
            for i, p in enumerate(self.data["paths"]):
                if os.path.normpath(p) == os.path.normpath(src_path):
                    self.data["paths"][i] = final_path
                    torch.save(self.data, os.path.join(attrs_mod.DATA_DIR, f"features_{self.current_project}.pt"))
                    break

        self._push_undo({"type": "move", "old_path": src_path, "new_path": final_path})
        self._update_row(row, src_path, final_path, overwrite, dest_path, push_undo=False)
        self._post_move_dup_cleanup()
        self._select_row(min(row, self.table.rowCount() - 1))

    def _handle_drag_move(self, src_rows, target_row):
        target_path = self.table.get_row_path(target_row)
        if not target_path: return
        target_dir = os.path.dirname(os.path.abspath(target_path))
        mode       = self.config.get("move_conflict", "size_check")
        db_changed = False
        batch      = []

        for src_row in src_rows:
            src_path  = os.path.abspath(self.table.get_row_path(src_row))
            if os.path.dirname(src_path) == target_dir: continue
            dest_path = os.path.join(target_dir, os.path.basename(src_path))
            final_path, overwrite = front_page._resolve_with_size(dest_path, src_path, mode, self)
            if final_path is None: continue
            try:
                shutil.move(src_path, final_path)
            except Exception as e:
                QMessageBox.critical(self, _t("Move Error / 移動エラー"), str(e)); continue

            import aisearch_attrs as _am
            _am.update_path_in_all_stores(src_path, final_path, self.current_project)
            if self.data and "paths" in self.data:
                if overwrite: front_page._remove_from_data(self.data, dest_path)
                for i, p in enumerate(self.data["paths"]):
                    if os.path.normpath(p) == os.path.normpath(src_path):
                        self.data["paths"][i] = final_path; db_changed = True; break

            # If the query file (row 0 in search mode) was just moved, update
            # query_path to the new location. Without this, right-arrow moves
            # of remaining rows would target the old folder, even though the
            # query file (and the top thumbnail) now lives somewhere else.
            if (self.query_path
                    and os.path.normpath(os.path.abspath(self.query_path))
                        == os.path.normpath(src_path)):
                self.query_path = final_path

            batch.append({"type": "move", "old_path": src_path, "new_path": final_path})
            self._update_row(src_row, src_path, final_path, overwrite, dest_path, push_undo=False)

        if batch:
            self._push_undo(batch)

        self._post_move_dup_cleanup()
        if db_changed and self.data:
            torch.save(self.data, os.path.join(attrs_mod.DATA_DIR, f"features_{self.current_project}.pt"))

    def move_to_folder_manually(self):
        row = self._current_row()
        if row < 0: return
        old_path = self.table.get_row_path(row)

        if old_path and not attrs_mod.is_editable(self.attrs_data, old_path):
            QMessageBox.warning(self, _t("Locked / ロック中"), _t("This file is locked and cannot be moved. / このファイルはロックされているため移動できません。"))
            return

        # Start the picker at the file's own folder so moving to a sibling
        # directory is one step instead of navigating from the last target.
        _start = os.path.dirname(old_path) if old_path and os.path.isdir(os.path.dirname(old_path)) else self.last_move_dir
        new_path, self.data, err, chosen_dir = front_page.select_and_move_file(
            self, old_path, self.data, self.current_project, _start,
            mode=self.config.get("move_conflict", "size_check"))

        if new_path:
            self.last_move_dir = chosen_dir
            self.config["last_move_dir"] = chosen_dir
            cfg.save_config(self.config, getattr(self, "current_project", None))
            dest_path = os.path.join(chosen_dir, os.path.basename(old_path))
            self._push_undo({"type": "move", "old_path": old_path, "new_path": new_path})
            self._update_row(row, old_path, new_path,
                             new_path == dest_path,
                             dest_path,
                             push_undo=False)
            self._post_move_dup_cleanup()
            next_row = row + 1 if row + 1 < self.table.rowCount() else row - 1
            if next_row >= 0:
                self._select_row(next_row)
        elif err and err != "cancelled":
            QMessageBox.critical(self, _t("Move Error / 移動エラー"), _t(f"Could not move file: {err} / ファイルを移動できません: {err}"))

    def rename_file(self, from_menu=False):
        row = self._current_row()
        if row < 0: return
        # Defer when called from context menu so it fully closes first
        delay = 50 if from_menu else 0
        QTimer.singleShot(delay, lambda: self._start_rename(row))

    def _start_rename(self, row):
        old_path   = self.table.get_row_path(row)
        if not old_path: return
        old_name   = os.path.basename(old_path)
        base       = os.path.splitext(old_name)[0]
        name_item  = self.table.item(row, 2)
        if not name_item: return
        orig_flags = name_item.flags()
        delegate   = self.table.itemDelegate()

        # Clean up any stale connection from a previous rename
        try:
            delegate.closeEditor.disconnect(self._rename_close_handler)
        except Exception:
            pass

        def _on_close(editor, hint):
            from PyQt6.QtWidgets import QAbstractItemDelegate
            try:
                delegate.closeEditor.disconnect(_on_close)
            except Exception:
                pass
            name_item.setFlags(orig_flags)

            if hint == QAbstractItemDelegate.EndEditHint.RevertModelCache:
                name_item.setText(old_name)
                return

            new_name = editor.text().strip()
            if not new_name or new_name == old_name:
                name_item.setText(old_name)
                return

            new_path = os.path.join(os.path.dirname(old_path), new_name)
            try:
                os.rename(old_path, new_path)
                name_item.setText(new_name)
                self.table.set_row_path(row, new_path)
                if self.data and "paths" in self.data and old_path in self.data["paths"]:
                    idx = self.data["paths"].index(old_path)
                    self.data["paths"][idx] = new_path
                    torch.save(self.data, os.path.join(attrs_mod.DATA_DIR, f"features_{self.current_project}.pt"))
                if old_path in self.attrs_data:
                    self.attrs_data[new_path] = self.attrs_data.pop(old_path)
                    attrs_mod.save(self.current_project, self.attrs_data)
                attrs_mod.update_path_in_all_stores(old_path, new_path, self.current_project)
                if self.preview_handler.current_path == old_path:
                    self.preview_handler.current_path = new_path
                self._push_undo({"type": "move", "old_path": old_path, "new_path": new_path})
            except Exception as e:
                QMessageBox.critical(self, _t("Rename Error / 改名エラー"), str(e))
                name_item.setText(old_name)

        self._rename_close_handler = _on_close
        name_item.setFlags(orig_flags | Qt.ItemFlag.ItemIsEditable)
        delegate.closeEditor.connect(_on_close)
        self.table.setFocus()
        self.table.setCurrentItem(name_item)
        self.table.editItem(name_item)
        QTimer.singleShot(0, lambda: self._open_rename_editor(base, old_name))

    def _open_rename_editor(self, base, old_name):
        from PyQt6.QtGui import QFontMetrics
        for editor in self.table.viewport().findChildren(QLineEdit):
            # Widen the editor widget itself (doesn't affect column)
            needed = QFontMetrics(editor.font()).horizontalAdvance(old_name) + 40
            if needed > editor.width():
                editor.resize(min(needed, 900), editor.height())
            editor.setSelection(0, len(base))
            break

    def _confirm_trash(self):
        dlg = QDialog(self)
        dlg.setWindowTitle(_t("Trash / ゴミ箱"))
        dlg.setFixedSize(280, 120)
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel(_t("Move to Trash? / ゴミ箱に移動しますか？")))
        dont_ask = QCheckBox(_t("Do not ask again / 次回から確認しない"))
        layout.addWidget(dont_ask)
        result = [False]
        bf = QHBoxLayout()
        def _yes():
            result[0] = True
            if dont_ask.isChecked():
                self.config["delete_confirm"] = False
                cfg.save_config(self.config, getattr(self, "current_project", None))
            dlg.accept()
        yes_btn = QPushButton(_t("Move to Trash / ゴミ箱に移動")); yes_btn.clicked.connect(_yes)
        no_btn  = QPushButton(_t("Cancel / キャンセル"));        no_btn.clicked.connect(dlg.reject)
        bf.addWidget(yes_btn); bf.addWidget(no_btn)
        layout.addLayout(bf)
        dlg.exec()
        return result[0]

    def handle_external_paths(self, paths):
        """Called when another launch attempt sends file paths via the IPC
        socket (e.g. right-click → Open with AISearch). Same flow as a
        drag-and-drop onto the window: bring window to front and run CLIP
        search using the file as the query (populates the results table)."""
        # Bring window to front
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show(); self.raise_(); self.activateWindow()
        # Filter to media files
        _exts = (logic.EXT_IMG + logic.EXT_VID) if hasattr(logic, "EXT_IMG") else (
            ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".avif",
            ".mp4", ".mkv", ".mov", ".m4v", ".avi", ".webm")
        media = [p for p in paths if p.lower().endswith(_exts) and os.path.isfile(p)]
        if not media:
            return
        # Reuse the existing drag-and-drop entry point — it handles search,
        # table population, and preview opening with the right side effects.
        self.on_drop(media[0])

    def delete_file(self):
        # Don't fire if focus is in an input widget
        focused = QApplication.focusWidget()
        if focused and focused.__class__.__name__ in ("QLineEdit", "QTextEdit", "QPlainTextEdit"):
            return

        rows = self._selected_rows()
        if not rows:
            return

        # Filter locked files and warn once if any
        locked = [r for r in rows
                  if (p := self.table.get_row_path(r)) and not attrs_mod.is_editable(self.attrs_data, p)]
        rows = [r for r in rows if r not in locked]
        if locked:
            names = ", ".join(os.path.basename(self.table.get_row_path(r)) for r in locked)
            QMessageBox.warning(self, _t("Locked / ロック中"), _t(f"Skipped locked file(s):\n{names} / ロック中のファイルをスキップ：\n{names}"))
        if not rows:
            return

        if self.config.get("delete_confirm", True):
            if not self._confirm_trash():
                return

        first_row = rows[0]
        was_query = (first_row == 0 and self.config.get("last_mode") == "search")
        deleted_any = False
        errors = []
        batch = []

        # Delete bottom-to-top so row indices stay valid
        for row in sorted(rows, reverse=True):
            path = self.table.get_row_path(row)
            if not path:
                continue
            emb = None
            if self.data and "paths" in self.data and path in self.data["paths"]:
                emb_idx = self.data["paths"].index(path)
                emb = self.data["embeddings"][emb_idx].clone()
            row_snap = {
                "score":       self.table.item(row, 0).text(),
                "size":        self.table.item(row, 1).text() if self.table.item(row, 1) else "",
                "name":        self.table.item(row, 2).text() if self.table.item(row, 2) else "",
                "masked_path": self.table.item(row, 3).text() if self.table.item(row, 3) else "",
                "sim_data":    self.table.item(row, 0).data(Qt.ItemDataRole.UserRole + 1),
                "bg_color":    self.table.item(row, 0).background(),
            }
            trash_path, err = front_page.trash_file(path)
            if trash_path is not None:
                batch.append({"type": "delete", "orig_path": path, "trash_path": trash_path,
                              "row": row, "emb": emb, "attrs": self.attrs_data.get(path), **row_snap})
                if self.data and "paths" in self.data and path in self.data["paths"]:
                    idx  = self.data["paths"].index(path)
                    keep = [i for i in range(len(self.data["paths"])) if i != idx]
                    self.data["paths"]      = [self.data["paths"][i] for i in keep]
                    self.data["embeddings"] = self.data["embeddings"][keep]
                self.table.removeRow(row)
                deleted_any = True
            elif err:
                errors.append(err)

        if batch:
            self._push_undo(batch)

        if deleted_any:
            if self.data:
                torch.save(self.data, os.path.join(attrs_mod.DATA_DIR, f"features_{self.current_project}.pt"))
            self._cleanup_singleton_groups()
            self._rebuild_dup_display_data()
            self._save_dup_results()
            new_row = min(first_row, self.table.rowCount() - 1)
            if new_row >= 0:
                self._select_row(new_row)
                self.table.setFocus()
                if was_query:
                    self._rebase_to_row(new_row)
            else:
                # Table is now empty — clear preview, thumbnail, and attrs panel
                self._refresh_inline_attrs(None)
                self.drop_zone.setPixmap(QPixmap())
                self.drop_zone.setText(_t("drop image or video / 画像・動画をドロップ"))
                pw = getattr(getattr(self, "preview_handler", None), "window", None)
                if pw:
                    pw.label.setPixmap(QPixmap())
                    pw._refresh_attrs(None)
        if errors:
            QMessageBox.critical(self, _t("Trash Error / ゴミ箱エラー"), "\n".join(errors))
