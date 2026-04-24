import os, shutil, subprocess, io, torch, threading, queue, json, datetime
from PIL import Image



from PyQt6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                              QLabel, QPushButton, QTableWidget, QTableWidgetItem,
                              QAbstractItemView, QHeaderView, QFrame,
                              QMessageBox, QDialog, QCheckBox, QApplication,
                              QLineEdit, QSpinBox, QProgressBar, QComboBox, QTextEdit,
                              QGridLayout)
from PyQt6.QtCore import Qt, QTimer, QUrl, QMimeData, QPoint, QItemSelectionModel, QFileSystemWatcher, QEvent
from PyQt6.QtGui import QPixmap, QShortcut, QKeySequence, QIcon, QCursor, QDrag, QColor, QFont

from sentence_transformers import util as st_util
import aisearch_logic as logic
from aisearch_settings import SettingsView
import aisearch_front_page as front_page
import aisearch_config as cfg
import aisearch_feedback as feedback
import aisearch_preview
import aisearch_attrs as attrs_mod
from attr_viewer import _lang_label as _t

VERSION = "1.97"


# ── Custom table item types for correct column sorting ──────────────────────

class NumericItem(QTableWidgetItem):
    def __lt__(self, other):
        try:   return float(self.text()) < float(other.text())
        except: return super().__lt__(other)

class SizeItem(QTableWidgetItem):
    def _bytes(self, text):
        try:
            p = text.split(); num = float(p[0]); unit = p[1].upper()
            return num * {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}.get(unit, 1)
        except: return 0.0
    def __lt__(self, other):
        return self._bytes(self.text()) < self._bytes(other.text())

class DateItem(QTableWidgetItem):
    """Table cell that stores a raw mtime, displays JD + readable date, sorts by mtime."""
    def __init__(self, mtime):
        self._mtime = mtime
        super().__init__(DateItem._fmt(mtime))
        self.setFlags(self.flags() & ~Qt.ItemFlag.ItemIsEditable)

    @staticmethod
    def _fmt(mtime):
        try:
            dt = datetime.datetime.fromtimestamp(mtime)
            a  = (14 - dt.month) // 12
            y  = dt.year + 4800 - a
            m  = dt.month + 12 * a - 3
            jd = (dt.day + (153 * m + 2) // 5
                  + 365 * y + y // 4 - y // 100 + y // 400 - 32045)
            return f"JD{jd} · {dt.strftime('%Y-%m-%d %H:%M')}"
        except Exception:
            return ""

    def __lt__(self, other):
        try:    return self._mtime < other._mtime
        except: return super().__lt__(other)


# ── Drop zone label / frame ───────────────────────────────────────────────────

def _url_drop_handler(event, callback):
    """Shared URL drop logic for drop-zone widgets."""
    for url in event.mimeData().urls():
        path = url.toLocalFile()
        if os.path.exists(path) and callback:
            callback(path)
            break
    event.accept()

class DropZoneLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(_t("DROP IMAGE / 画像をドロップ"), parent)
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._drop_callback = None

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
        super().__init__(0, 5, parent)
        self.setHorizontalHeaderLabels([_t("Score / スコア"), _t("Size / サイズ"),
                                         _t("Name / 名前"), _t("Path / パス"),
                                         _t("Date / 日付")])
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSortingEnabled(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.horizontalHeader().setSortIndicatorShown(True)
        self.horizontalHeader().setStretchLastSection(False)
        for _col in range(5):
            self.horizontalHeader().setSectionResizeMode(_col, QHeaderView.ResizeMode.Interactive)
        self.verticalHeader().setVisible(False)
        self.setShowGrid(False)
        self.setAlternatingRowColors(True)
        self.setColumnWidth(0,  90)
        self.setColumnWidth(1, 100)
        self.setColumnWidth(3, 300)
        self.setColumnWidth(4, 130)

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
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.viewport().installEventFilter(self)

    def get_row_path(self, row):
        item = self.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def set_row_path(self, row, path):
        item = self.item(row, 0)
        if item: item.setData(Qt.ItemDataRole.UserRole, path)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            item = self.itemAt(event.pos())
            row  = self.row(item) if item else -1
            self._drag_src_row   = row
            self._drag_press_pos = event.pos()
            self._drag_active    = False
            sel_rows = {idx.row() for idx in self.selectionModel().selectedRows()}
            if row >= 0 and row in sel_rows:
                if self._tab_held:
                    # Tab+click: deselect the clicked row
                    index = self.model().index(row, 0)
                    self.selectionModel().select(
                        index,
                        QItemSelectionModel.SelectionFlag.Deselect |
                        QItemSelectionModel.SelectionFlag.Rows)
                    return
                if len(sel_rows) > 1:
                    # Plain click on selected row: defer collapse to release
                    # (so drag can still operate on the full multi-selection)
                    self._collapse_to_row = row
                    index = self.model().index(row, 0)
                    self.selectionModel().setCurrentIndex(
                        index, QItemSelectionModel.SelectionFlag.NoUpdate)
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (event.buttons() & Qt.MouseButton.LeftButton) and self._drag_src_row is not None:
            if (event.pos() - self._drag_press_pos).manhattanLength() > 5:
                self._drag_active = True
            if self._drag_active:
                item     = self.itemAt(event.pos())
                tgt_row  = self.row(item) if item else -1
                sel_rows = {idx.row() for idx in self.selectionModel().selectedRows()}
                if tgt_row >= 0 and tgt_row not in sel_rows:
                    self.setCursor(QCursor(Qt.CursorShape.DragMoveCursor))
                else:
                    self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
                return   # don't let Qt change selection during drag
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        if self._drag_active and self._drag_src_row is not None:
            item     = self.itemAt(event.pos())
            tgt_row  = self.row(item) if item else -1
            sel_rows = [idx.row() for idx in self.selectionModel().selectedRows()]
            if tgt_row >= 0 and tgt_row not in sel_rows and self.move_callback:
                self.move_callback(sel_rows, tgt_row)
        elif not self._drag_active and self._collapse_to_row >= 0:
            # Plain click on multi-selected row with no drag: collapse to single row
            index = self.model().index(self._collapse_to_row, 0)
            self.selectionModel().select(
                index,
                QItemSelectionModel.SelectionFlag.ClearAndSelect |
                QItemSelectionModel.SelectionFlag.Rows)
            self.selectionModel().setCurrentIndex(
                index, QItemSelectionModel.SelectionFlag.NoUpdate)
        self._drag_src_row    = None
        self._drag_active     = False
        self._collapse_to_row = -1
        super().mouseReleaseEvent(event)

    def eventFilter(self, obj, event):
        if obj is self.viewport():
            t = event.type()
            if t == event.Type.DragEnter:
                if event.mimeData().hasUrls() and not self._drag_active:
                    event.acceptProposedAction(); return True
            elif t == event.Type.DragMove:
                if event.mimeData().hasUrls() and not self._drag_active:
                    event.acceptProposedAction(); return True
            elif t == event.Type.Drop:
                if event.mimeData().hasUrls() and not self._drag_active:
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

        # Header
        self.header = QFrame()
        self.header.setMinimumHeight(380)
        h_layout = QHBoxLayout(self.header)
        h_layout.setContentsMargins(15, 15, 15, 15)

        # Thumbnail — DropZoneFrame so border-area drops also work on Linux/xcb
        thumb_outer = DropZoneFrame()
        thumb_outer.setFixedSize(350, 350)
        thumb_layout = QVBoxLayout(thumb_outer)
        thumb_layout.setContentsMargins(0, 0, 0, 0)
        self.drop_zone = DropZoneLabel()
        self.drop_zone._drop_callback = self.on_drop
        thumb_outer._drop_callback     = self.on_drop
        thumb_layout.addWidget(self.drop_zone)
        self.thumb_outer = thumb_outer
        h_layout.addWidget(thumb_outer)

        # Info panel
        self.info_widget = QWidget()
        info_layout = QVBoxLayout(self.info_widget)
        info_layout.setContentsMargins(20, 0, 0, 0)

        self.btn_settings = QPushButton(_t("⚙ SETTINGS / ⚙ 設定"))
        self.btn_settings.setStyleSheet(
            "background-color: #6c757d; color: white; font-weight: bold; padding: 6px 12px;")
        self.btn_settings.clicked.connect(self._open_settings)
        info_layout.addWidget(self.btn_settings, alignment=Qt.AlignmentFlag.AlignRight)

        # Logo — under the settings button, right-aligned
        self._lbl_logo = QLabel()
        self._lbl_logo.setFixedSize(160, 160)
        self._lbl_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_logo.setStyleSheet("background: transparent;")
        info_layout.addWidget(self._lbl_logo, alignment=Qt.AlignmentFlag.AlignRight)
        # Load logo once (PNG with transparency — no dark/light swap needed)
        _logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aisearch_logo.png")
        if os.path.exists(_logo_path):
            _px = QPixmap(_logo_path).scaled(
                160, 160,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            self._lbl_logo.setPixmap(_px)

        self.lbl_proj_hdr = QLabel(_t("PROJECT: / プロジェクト："))
        self.lbl_proj_hdr.setStyleSheet("color: #00ff00; font-weight: bold;")
        info_layout.addWidget(self.lbl_proj_hdr)

        self.lbl_project = QLabel(self.current_project)
        pfs = self.config.get("project_font_size", 30)
        self.lbl_project.setStyleSheet(f"font-size: {pfs}pt; font-weight: bold;")
        info_layout.addWidget(self.lbl_project)

        self.lbl_base_dir = QLabel(_t("Base:  / ベース： "))
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

        self._btn_back = QPushButton(_t("⏮ Back / ⏮ 戻る"))
        self._btn_back.setToolTip(_t("Go back to previously viewed file / 直前に表示したファイルに戻る"))
        self._btn_back.setStyleSheet(
            "QPushButton { background-color: #4a4a6a; color: white; font-weight: bold; "
            "padding: 6px 10px; border: 2px solid #6a6a9a; }"
            "QPushButton:hover { background-color: #5a5a8a; }"
            "QPushButton:pressed { background-color: #2a2a4a; }"
            "QPushButton:disabled { background-color: #2a2a2a; color: #555; border-color: #333; }")
        self._btn_back.clicked.connect(self._go_back)
        mode_col.addWidget(self._btn_back)

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
        self.spin_threshold = QSpinBox()
        self.spin_threshold.wheelEvent = lambda e: e.ignore()
        self.spin_threshold.setRange(70, 100)
        self.spin_threshold.setValue(self.config.get("dup_threshold", 95))
        self.spin_threshold.setSuffix("%")
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
        btn_scan = QPushButton(_t("⟳ Scan / ⟳ スキャン"))
        btn_scan.setToolTip(_t("Force rescan (clear cache) / 強制再スキャン（キャッシュ消去）"))
        btn_scan.clicked.connect(self._force_rescan)
        btn_scan.setStyleSheet(
            "QPushButton { background-color: #6f42c1; color: white; font-weight: bold; "
            "padding: 4px 14px; border: 2px solid #9b6dff; border-radius: 3px; }"
            "QPushButton:hover { background-color: #9b6dff; border-color: white; }"
            "QPushButton:pressed { background-color: #4a2a8a; }")
        dup_row3.addWidget(btn_scan)
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

        self._dup_controls_widget.hide()
        mode_and_dup.addWidget(self._dup_controls_widget)
        mode_and_dup.addStretch()

        # Allow window to shrink below natural button widths
        for _i in range(mode_and_dup.count()):
            _item = mode_and_dup.itemAt(_i)
            if _item and _item.widget():
                _item.widget().setMinimumWidth(0)
        info_layout.addLayout(mode_and_dup)

        # Search progress bar (hidden by default)
        self.search_progress = QProgressBar()
        self.search_progress.setRange(0, 0)   # indeterminate animation while searching
        self.search_progress.setFixedHeight(4)
        self.search_progress.setTextVisible(False)
        self.search_progress.setStyleSheet(
            "QProgressBar { border: none; background: transparent; }"
            "QProgressBar::chunk { background: #4a90d9; }")
        self.search_progress.hide()
        info_layout.addWidget(self.search_progress)

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
        h_layout.addWidget(self.info_widget, stretch=1)

        main_layout.addWidget(self.header)


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
        self.table.cellDoubleClicked.connect(lambda r, c: self.on_double_click())
        self.table.cellClicked.connect(self._on_group_cell_click)
        self.popup_menu = front_page.create_context_menu(self.table, self)
        main_layout.addWidget(self.table)
        self._apply_colors()
        self.reload_fonts()
        self._apply_header_theme()
        col_widths = self.config.get("col_widths", {})
        for col, default in [(0, 90), (1, 100), (2, 400), (3, 300), (4, 130)]:
            self.table.setColumnWidth(col, col_widths.get(str(col), default))

    def _apply_colors(self):
        c = self.config.get("colors", cfg.DEFAULT_COLORS)
        sel = c.get("selection", cfg.DEFAULT_COLORS["selection"])
        r, g, b = int(sel[1:3], 16), int(sel[3:5], 16), int(sel[5:7], 16)
        text_color = "black" if (r * 299 + g * 587 + b * 114) / 1000 > 128 else "white"
        self.table.setStyleSheet(
            f"QTableWidget::item:selected {{ background-color: {sel}; color: {text_color}; }}")
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
        # lbl_base_dir is rebuilt by _set_base_dir_label; just force a refresh by reading current label
        _base = ""
        if hasattr(self, '_base_dir_label_value'):
            _base = self._base_dir_label_value
        self.lbl_base_dir.setText(_t(f"Base: {_base} / ベース： {_base}") if _base else _t("Base:  / ベース： "))
        # Mode buttons
        self.btn_mode_search.setText(_t("🔍 Search / 🔍 検索"))
        self.btn_mode_search.setToolTip(_t("Switch to Search mode / 検索モードに切り替え"))
        self.btn_find_dups.setText(_t("♊ Duplicates / ♊ 重複"))
        self.btn_find_dups.setToolTip(_t("Find duplicates (Shift+click to force rescan) / 重複を検索（Shift+クリックで強制再スキャン）"))
        self.btn_browse.setText(_t("📂 Browse / 📂 閲覧"))
        self.btn_browse.setToolTip(_t("Browse folder contents (ls mode) / フォルダ内容を閲覧（lsモード）"))
        self._btn_back.setText(_t("⏮ Back / ⏮ 戻る"))
        self._btn_back.setToolTip(_t("Go back to previously viewed file / 直前に表示したファイルに戻る"))
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
        self.btn_undo.setToolTip(
            self._undo_stack[-1]["desc"] if self._undo_stack else _t("Nothing to undo / 元に戻す操作なし"))
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
                                                   _t("Date / 日付")])
        else:
            self.table.setHorizontalHeaderLabels([_t("Score / スコア"), _t("Size / サイズ"),
                                                   _t("Name / 名前"), _t("Path / パス"),
                                                   _t("Date / 日付")])
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
        self.header.setStyleSheet(f"background-color: {header_bg};")
        self.thumb_outer.setStyleSheet(
            f"background-color: {thumb_bg}; border: 3px ridge {thumb_brd};")
        self.drop_zone.setStyleSheet(
            f"color: {drop_color}; font-weight: bold; background-color: {thumb_bg};")
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

        # Project / Scene button
        btn_more = QPushButton("📝 Title / Scene…")
        btn_more.setStyleSheet(
            "padding: 2px 6px; color: #e0e0e0; background-color: #555;")
        btn_more.clicked.connect(self.edit_attrs)
        vbox.addWidget(btn_more)

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
        QShortcut(QKeySequence("a"),            self, self.edit_attrs)
        QShortcut(QKeySequence("A"),            self, self.edit_attrs)
        self._attr_win = None
        self.table.selectionModel().selectionChanged.connect(self._on_selection_changed_attrs)

    def _on_selection_changed_attrs(self):
        if not (hasattr(self, '_attr_win') and self._attr_win and self._attr_win.isVisible()):
            return
        row = self._current_row()
        if row < 0: return
        path = self.table.get_row_path(row)
        if path:
            self._attr_win_load(path)

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

    def _push_undo(self, action):
        name = os.path.basename(action.get("orig_path") or action.get("old_path") or "")
        if action["type"] == "move":
            dest = os.path.basename(os.path.dirname(action["new_path"]))
            action["desc"] = f"Move  {name}  →  …/{dest}/"
        else:
            action["desc"] = f"Delete  {name}"
        self._undo_stack.append(action)
        if len(self._undo_stack) > 20:
            self._undo_stack.pop(0)
        self._update_undo_btn()

    def _update_undo_btn(self):
        has = bool(self._undo_stack)
        self.btn_undo.setEnabled(has)
        self.btn_undo.setToolTip(self._undo_stack[-1]["desc"] if has else _t("Nothing to undo / 元に戻す操作なし"))

    def _undo_last(self):
        if not self._undo_stack:
            return
        action = self._undo_stack.pop()
        self._update_undo_btn()
        if action["type"] == "move":
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
            lw.addItem(action["desc"])
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
                    if action["type"] == "move":   self._undo_move(action)
                    elif action["type"] == "delete": self._undo_delete(action)
            dlg.accept()
        btn_undo_to.clicked.connect(_undo_to)
        lw.itemDoubleClicked.connect(lambda: _undo_to())
        lw.setCurrentRow(0)
        dlg.exec()

    def _undo_move(self, action):
        old_path, new_path = action["old_path"], action["new_path"]
        if not os.path.exists(new_path):
            QMessageBox.warning(self, _t("Undo / 元に戻す"), _t(f"Cannot undo move: file not found at\n{new_path} / 元に戻せません：ファイルが見つかりません\n{new_path}"))
            return
        try:
            shutil.move(new_path, old_path)
        except Exception as e:
            QMessageBox.critical(self, _t("Undo Error / 元に戻すエラー"), str(e)); return
        if self.data and "paths" in self.data:
            for i, p in enumerate(self.data["paths"]):
                if os.path.normpath(p) == os.path.normpath(new_path):
                    self.data["paths"][i] = old_path
                    torch.save(self.data, os.path.join(attrs_mod.DATA_DIR, f"features_{self.current_project}.pt"))
                    break
        for r in range(self.table.rowCount()):
            if os.path.normpath(self.table.get_row_path(r) or "") == os.path.normpath(new_path):
                self.table.set_row_path(r, old_path)
                self.table.item(r, 2).setText(os.path.basename(old_path))
                self.table.item(r, 3).setText(self._mask_path(old_path))
                self._select_row(r)
                break

    def _undo_delete(self, action):
        orig_path, trash_path = action["orig_path"], action["trash_path"]
        success, err = front_page.restore_from_trash(trash_path, orig_path)
        if not success:
            QMessageBox.critical(self, _t("Undo Error / 元に戻すエラー"), _t(f"Could not restore:\n{err} / 復元できません：\n{err}")); return
        if self.data is not None and "paths" in self.data:
            emb = action.get("emb")
            if emb is not None:
                self.data["paths"].append(orig_path)
                self.data["embeddings"] = torch.cat([self.data["embeddings"], emb.unsqueeze(0)])
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
        bg = action.get("bg_color")
        if bg and bg.color().isValid():
            for col in range(self.table.columnCount()):
                if self.table.item(row, col):
                    self.table.item(row, col).setBackground(bg)
        self._select_row(row)
        self._rebuild_dup_display_data()
        self._save_dup_results()

    # ── Attributes ───────────────────────────────────────────────────────────

    def edit_attrs(self):
        row = self._current_row()
        if row < 0: return
        path = self.table.get_row_path(row)
        if not path: return

        if not hasattr(self, '_attr_win') or self._attr_win is None:
            self._attr_win = self._build_attr_window()

        self._attr_win_load(path)
        self._attr_win.show()
        self._attr_win.raise_()
        self._attr_win.activateWindow()

    def _build_attr_window(self):
        from PyQt6.QtWidgets import QGroupBox, QRadioButton, QScrollArea, QButtonGroup
        win = QWidget(None)
        win.setWindowFlag(Qt.WindowType.Window)
        win.setWindowTitle(_t("Attributes / 属性"))
        win.resize(340, 580)
        win._aw_path = None

        outer = QVBoxLayout(win)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        inner  = QWidget();     layout = QVBoxLayout(inner)
        layout.setSpacing(6)
        scroll.setWidget(inner); outer.addWidget(scroll)

        win._aw_checks = {}
        win._aw_none_rb = None
        win._aw_qual_rbs = {}
        win._aw_none_res = None
        win._aw_res_rbs = {}
        win._aw_audio_checks = {}
        win._aw_g_aud = None

        # Usage
        g_meta = QGroupBox(_t("Usage / 用途")); lm = QVBoxLayout(g_meta)
        lm.addWidget(QLabel(_t("Title: / タイトル：")))
        win._aw_proj_edit = QLineEdit(); lm.addWidget(win._aw_proj_edit)
        win._aw_scene_lbl = QLabel(_t("Scene: / シーン：")); lm.addWidget(win._aw_scene_lbl)
        win._aw_scene_edit = QLineEdit(); lm.addWidget(win._aw_scene_edit)
        layout.addWidget(g_meta)

        # Note
        g_note = QGroupBox(_t("Note / ノート")); ln = QVBoxLayout(g_note)
        win._aw_note_edit = QTextEdit(); win._aw_note_edit.setFixedHeight(55); ln.addWidget(win._aw_note_edit)
        layout.addWidget(g_note)

        # Confirmed
        win._aw_confirmed_cb = QCheckBox(_t("Confirmed different (hide from dup results) / 異なると確認済み（重複結果から非表示）"))
        layout.addWidget(win._aw_confirmed_cb)

        # Buttons
        btn_row = QHBoxLayout()
        btn_save  = QPushButton(_t("Save / 保存"))
        btn_save.setStyleSheet(cfg.btn_ss("btn_write", self.config))
        btn_clear = QPushButton(_t("Clear / クリア"))
        btn_close = QPushButton(_t("Close / 閉じる")); btn_close.clicked.connect(win.close)

        def _save():
            path = win._aw_path
            if not path: return
            tags = [k for k, cb in win._aw_checks.items() if cb.isChecked()]
            tags += [k for k, rb in win._aw_qual_rbs.items() if rb.isChecked()]
            tags += [k for k, rb in win._aw_res_rbs.items() if rb.isChecked()]
            tags += [k for k, cb in win._aw_audio_checks.items() if cb.isChecked()]
            is_video = path.lower().endswith(logic.EXT_VID)
            attrs_mod.set_file(self.attrs_data, path,
                               tags=tags,
                               note=win._aw_note_edit.toPlainText().strip(),
                               confirmed=win._aw_confirmed_cb.isChecked(),
                               project=win._aw_proj_edit.text().strip(),
                               scene=win._aw_scene_edit.text().strip() if is_video else "",
                               editable=entry.get("editable", True))
            attrs_mod.save(self.current_project, self.attrs_data)
            row = self._current_row()
            self._refresh_inline_attrs(path)
            if (self.preview_handler.window and self.preview_handler.window.isVisible()
                    and self.preview_handler.current_path == path):
                self.preview_handler.window._refresh_attrs(path)
            if row >= 0:
                self._refresh_attrs_indicator(row, path)
            self._highlight_unmarked_rows()
            if self.btn_hide_confirmed.isChecked():
                self._apply_confirmed_filter(True)

        def _clear():
            for cb in win._aw_checks.values(): cb.setChecked(False)
            for cb in win._aw_audio_checks.values(): cb.setChecked(False)
            if win._aw_none_rb: win._aw_none_rb.setChecked(True)
            if win._aw_none_res: win._aw_none_res.setChecked(True)
            win._aw_proj_edit.clear(); win._aw_scene_edit.clear()
            win._aw_note_edit.clear(); win._aw_confirmed_cb.setChecked(False)

        btn_save.clicked.connect(_save)
        btn_clear.clicked.connect(_clear)
        btn_row.addWidget(btn_save); btn_row.addWidget(btn_clear); btn_row.addWidget(btn_close)
        outer.addLayout(btn_row)

        return win

    def _attr_win_load(self, path):
        """Load a file's attributes into the open attr window."""
        win = self._attr_win
        if win is None: return
        win._aw_path = path
        win.setWindowTitle(_t(f"Attributes — {os.path.basename(path)} / 属性 — {os.path.basename(path)}"))

        entry    = attrs_mod.get(self.attrs_data, path)
        cur_tags = set(entry.get("tags", []))
        is_video = path.lower().endswith(logic.EXT_VID)

        for key, cb in win._aw_checks.items():
            cb.setChecked(key in cur_tags)

        has_qual = bool(cur_tags & attrs_mod.QUALITY_TAGS)
        if win._aw_none_rb: win._aw_none_rb.setChecked(not has_qual)
        for key, rb in win._aw_qual_rbs.items():
            rb.setChecked(key in cur_tags)

        has_res = bool(cur_tags & attrs_mod.RESOLUTION_TAGS)
        if win._aw_none_res: win._aw_none_res.setChecked(not has_res)
        for key, rb in win._aw_res_rbs.items():
            rb.setChecked(key in cur_tags)

        if win._aw_g_aud: win._aw_g_aud.setVisible(is_video)
        win._aw_scene_lbl.setVisible(is_video)
        win._aw_scene_edit.setVisible(is_video)
        for key, cb in win._aw_audio_checks.items():
            cb.setChecked(key in cur_tags)

        win._aw_proj_edit.setText(entry.get("project", ""))
        win._aw_scene_edit.setText(entry.get("scene", "") if is_video else "")
        win._aw_note_edit.setPlainText(entry.get("note", ""))
        win._aw_confirmed_cb.setChecked(entry.get("confirmed", False))

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

        # Determine which groups are fully confirmed-hidden
        hidden_confirmed = set()
        if hide_confirmed:
            for label, rows in groups.items():
                if all(bool(attrs_mod.get(self.attrs_data, self.table.get_row_path(r)))
                       for r in rows):
                    hidden_confirmed.add(label)

        for label in order:
            rows = groups[label]
            if label in hidden_confirmed:
                for r in rows:
                    self.table.setRowHidden(r, True)
            elif label in self._collapsed_groups:
                # Show only first (representative) row
                for i, r in enumerate(rows):
                    self.table.setRowHidden(r, i > 0)
            else:
                for r in rows:
                    self.table.setRowHidden(r, False)
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
        if self.preview_handler.window:
            self.preview_handler.window.close()
        g = self.geometry()
        self.config["main_geometry"] = [g.x(), g.y(), g.width(), g.height()]
        self.config["col_widths"] = {str(col): self.table.columnWidth(col) for col in range(5)}
        cfg.save_config(self.config, getattr(self, "current_project", None))
        self._save_dup_results()
        event.accept()

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
        _ph = getattr(self, 'preview_handler', None)
        if _ph and _ph.window:
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

        label = ", ".join(self.base_dirs) if self.base_dirs else ""
        self._base_dir_label_value = label
        self.lbl_base_dir.setText(_t(f"Base: {label} / ベース： {label}") if label else _t("Base:  / ベース： "))
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
        # Skip if a settings scan/rename is in progress
        if getattr(self, '_watcher_paused', False): return
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
        _auto_rename = attrs_mod.load_filename_config(self.current_project).get("auto_rename", False)
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
                self.attrs_data = attrs_mod.auto_set_all(
                    self.attrs_data, path, self.current_project)
            except Exception:
                pass
            try:
                _clip_fields = {"hc", "fa", "sk", "e", "b", "wh", "pm", "cs", "bg"}
                _clip_updates = attrs_mod.auto_detect_clip_attrs(
                    emb, self.attrs_data.get(path, {}), allowed_fields=_clip_fields)
                if _clip_updates:
                    self.attrs_data.setdefault(path, {}).update(_clip_updates)
            except Exception:
                pass
            # Face detection — assign person ID (000 if no face found)
            try:
                pid_detected = attrs_mod.detect_or_assign_person_id(
                    path, self.current_project)
                _stored_pid = (self.attrs_data.get(path) or {}).get("person_id", "")
                if pid_detected is None:
                    pid_detected = "000"   # no face detected
                if pid_detected != _stored_pid:
                    self.attrs_data.setdefault(path, {})["person_id"] = pid_detected
                    attrs_dirty = True
            except Exception:
                pass
            attrs_dirty = True

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

    def _on_threshold_changed(self, v):
        self.config.update({"dup_threshold": v})
        cfg.save_config(self.config)
        # In dup mode: auto-load cached results for the new threshold if available
        if self.config.get("last_mode") == "dup" and os.path.exists(self._dup_file_path()):
            self._load_dup_results(update_spinner=False)
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

    def _find_duplicates_by_hash(self):
        """100% mode: full disk hash scan of all files in base_dirs — like Czkawka."""
        if not self.base_dirs:
            QMessageBox.information(self, _t("Find Duplicates / 重複検索"), _t("No base directory set. / ベースディレクトリが設定されていません。"))
            return
        import hashlib
        from collections import defaultdict

        self.btn_find_dups.setEnabled(False)
        self.btn_find_dups.setText(_t("Hashing... / ハッシュ計算中..."))
        self._dup_result_summary = ""
        self.lbl_dup_status.setText("")
        self._dup_queue = queue.Queue()
        base_dirs = list(self.base_dirs)
        _media_exts = tuple(logic.EXT_IMG + logic.EXT_VID)

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
                # 1. Collect all media files grouped by size (skip zero-byte files)
                size_map = defaultdict(list)
                for base in base_dirs:
                    for root, _, files in os.walk(base):
                        for fname in files:
                            fpath = os.path.join(root, fname)
                            if not _is_media(fpath):
                                continue
                            try:
                                sz = os.path.getsize(fpath)
                                if sz > 0:
                                    size_map[sz].append(fpath)
                            except OSError:
                                pass

                # 2. For same-size candidates, compute MD5 hash
                def _md5(path):
                    h = hashlib.md5()
                    with open(path, 'rb') as f:
                        for chunk in iter(lambda: f.read(65536), b''):
                            h.update(chunk)
                    return h.hexdigest()

                hash_map = defaultdict(list)
                for size, fpaths in size_map.items():
                    if len(fpaths) < 2:
                        continue
                    for fpath in fpaths:
                        try:
                            hash_map[(size, _md5(fpath))].append(fpath)
                        except OSError:
                            pass

                # 3. Build groups — same hash = same bytes = duplicate regardless of extension
                groups_data = []
                for fpaths in hash_map.values():
                    if len(fpaths) < 2:
                        continue
                    members = [{"path": p, "sim": 1.0} for p in fpaths]
                    members.sort(key=lambda m: os.path.getsize(m["path"]), reverse=True)
                    groups_data.append(members)

                # Sort: most files in group first
                groups_data.sort(key=len, reverse=True)
                self._dup_queue.put(("hash_done", groups_data))
            except Exception as e:
                self._dup_queue.put(("error", str(e)))

        threading.Thread(target=_worker, daemon=True).start()
        self._dup_poll_timer = QTimer(self)
        self._dup_poll_timer.timeout.connect(self._poll_dup_queue)
        self._dup_poll_timer.start(200)

    def _find_duplicates(self):
        """Enter dup mode. Load cached results if available; otherwise just show controls.
        Actual scan is triggered by the ⟳ Scan button."""
        self._update_mode_buttons("dup")
        self._dup_controls_widget.show()
        self.config["last_mode"] = "dup"
        if os.path.exists(self._dup_file_path()):
            self._load_dup_results(update_spinner=False)
        else:
            self.lbl_dup_status.setText(_t("Press ⟳ Scan to find duplicates. / ⟳スキャンを押して重複を検索"))

    def _run_dup_scan(self):
        """Actually run the duplicate scan (called by ⟳ Scan button)."""

        threshold = self.spin_threshold.value() / 100.0
        if threshold >= 1.0:
            self._find_duplicates_by_hash()
            return

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

                sim = st_util.cos_sim(embs, embs).cpu()   # (N, N)

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

                # Cosine similarity pass — report progress every 5%
                q.put(("progress", f"Comparing pairs…  0%"))
                _step = max(1, n // 20)
                _last_pct = 0
                for i in range(n):
                    pct = int(i / n * 100)
                    if pct >= _last_pct + 5:
                        _last_pct = pct
                        q.put(("progress", f"Comparing pairs… {pct:3d}%"))
                    for j in range(i + 1, n):
                        if sim[i][j].item() >= threshold:
                            if not _same_ext(i, j):
                                continue
                            if not _sizes_ok(i, j):
                                continue
                            if exact_mode and _file_hash(paths[i]) != _file_hash(paths[j]):
                                continue
                            adj[i].append(j)
                            adj[j].append(i)

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
                    return max(sim[idx][rep].item() for idx in group[1:]) if len(group) > 1 else 1.0
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
            self.lbl_dup_status.setText(payload)
            return                        # keep polling
        self._dup_poll_timer.stop()
        self.btn_find_dups.setText("♊ Duplicates")
        if msg == "error":
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
                sim_score = 1.0 if rank == 0 else sim[idx][rep].item()
                item0.setData(Qt.ItemDataRole.UserRole + 1, sim_score)
                color = self._dup_color(sim_score, g_idx)
                for col in range(self.table.columnCount()):
                    self.table.item(row, col).setBackground(color)
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
                    "sim":  1.0 if rank == 0 else round(sim[idx][rep].item(), 6)
                })
            result.append(members)
        return result

    def _dup_file_path(self):
        pct = self.spin_threshold.value()
        suffix = "hash" if pct >= 100 else f"{pct}pct"
        return os.path.join(attrs_mod.DATA_DIR, f"dups_{self.current_project}_{suffix}.json")

    def _save_dup_results(self):
        if not self._dup_display_data:
            return
        data = {
            "project":   self.current_project,
            "threshold": self.spin_threshold.value(),
            "groups":    self._dup_display_data,
        }
        with open(self._dup_file_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    def _load_dup_results(self, update_spinner=True):
        name = self._dup_file_path()
        if not os.path.exists(name):
            return
        try:
            with open(name, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        pct = data.get("threshold") or self.spin_threshold.value()
        if update_spinner:
            self.spin_threshold.setValue(pct)
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
        groups_data = [
            [e for e in g if _is_media_path(e)]
            for g in data["groups"]
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

    def _rebuild_dup_display_data(self):
        """Rebuild _dup_display_data from current table rows (after deletions)."""
        groups = {}
        order  = []
        for r in range(self.table.rowCount()):
            label = self.table.item(r, 0).data(Qt.ItemDataRole.UserRole + 2) or self.table.item(r, 0).text().strip()
            path  = self.table.get_row_path(r)
            sim   = self.table.item(r, 0).data(Qt.ItemDataRole.UserRole + 1) or 1.0
            if label not in groups:
                groups[label] = []
                order.append(label)
            groups[label].append({"path": path, "sim": sim})
        self._dup_display_data = [groups[k] for k in order if len(groups[k]) > 1] or None

    def _display_dup_from_data(self, groups_data):
        """Display duplicate groups from saved/loaded data (no sim tensor needed)."""
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for g_idx, members in enumerate(groups_data):
            grp_label = f"G{g_idx + 1}"
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
                for col in range(self.table.columnCount()):
                    self.table.item(row, col).setBackground(color)
        self._cleanup_singleton_groups()
        self._highlight_unmarked_rows()
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

    def _update_row(self, row, old_path, final_path, overwrite, dest_path, protect_rows=None):
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
        self.run_search(path)

    def run_search(self, p):
        if not self.data:
            if getattr(self, '_browse_dir', None):
                self._exit_browse_mode()
            self._update_mode_buttons("search")
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
            self.drop_zone.setPixmap(scaled)
            self.drop_zone.setText("")
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
            if frame_px and not frame_px.isNull():
                scaled = frame_px.scaled(330, 330,
                                         Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation)
                self.drop_zone.setPixmap(scaled)
                self.drop_zone.setText("")
            else:
                self.drop_zone.setPixmap(QPixmap())
                self.drop_zone.setText(_t("▶ VIDEO / ▶ 動画") if ext in ('.mp4', '.mkv', '.mov', '.avi', '.webm') else "?")

        # Show preview immediately before the background search starts
        self.preview_handler.show(self.query_path)
        self._refresh_inline_attrs(self.query_path)

        # Lock preview so it doesn't update again until search finishes
        self._lock_preview = True

        # Restore "Score" column header; exit browse mode if active; clear table immediately
        if self._browse_dir:
            self._exit_browse_mode()
        self.table.setHorizontalHeaderLabels([_t("Score / スコア"), _t("Size / サイズ"), _t("Name / 名前"), _t("Path / パス"), _t("Date / 日付")])
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)   # clear browse/old listing immediately, don't wait for worker
        self.config["last_mode"] = "search"
        cfg.save_config(self.config, getattr(self, "current_project", None))
        self._update_mode_buttons("search")
        self.btn_find_dups.setEnabled(False)
        self.search_progress.show()
        self.statusBar().showMessage("Analyzing image…")

        import threading, queue as _queue
        _q = _queue.Queue()
        _query_path  = self.query_path
        _data        = self.data          # snapshot — avoids race if project switches
        _feedback    = self.feedback_data

        _path_idx    = getattr(self, '_path_idx', {})

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
                k = min(self.config.get("max_search_results", 300), len(_data["paths"]))
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
                top   = (cand_sims[order], cand_idx[order])
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
        for s, i in zip(top[0], top[1]):
            fp = data["paths"][i]
            if os.path.abspath(fp) == query_path: continue
            if not os.path.exists(fp): continue
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
        if emb is not None:
            self.query_emb = emb
        self._search_running  = False
        self.search_progress.hide()
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
        pw = getattr(getattr(self, 'preview_handler', None), 'window', None)
        if pw:
            pw.set_mode_color(_sep_colors.get(mode, '#1a1a1a'))

    def _enter_search_mode(self):
        """Return to search mode — search selected row, last query, or just reset."""
        if getattr(self, '_browse_dir', None):
            self._exit_browse_mode()
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
            self.table.setHorizontalHeaderLabels([_t("Score / スコア"), _t("Size / サイズ"), _t("Name / 名前"), _t("Path / パス"), _t("Date / 日付")])
            self.table.setRowCount(0)
            self.config["last_mode"] = "search"
            cfg.save_config(self.config, getattr(self, "current_project", None))
            self._update_mode_buttons("search")

    def _enter_browse_mode(self, directory=None):
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

        self.table.setHorizontalHeaderLabels(["#", _t("Size / サイズ"), _t("Name / 名前"), _t("Path / パス"), _t("Date / 日付")])
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
        front_page.show_context_menu(self.table.mapToGlobal(pos), self.popup_menu)

    def open_folder(self):
        row = self._current_row()
        if row >= 0:
            front_page.open_in_nemo(self.table.get_row_path(row))

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
            self.drop_zone.setPixmap(scaled)
            self.drop_zone.setText("")
        else:
            # Video — extract first frame
            frame_px = None
            if ext in ('.mp4', '.mkv', '.mov', '.avi', '.webm'):
                try:
                    import cv2, numpy as np
                    from PyQt6.QtGui import QImage
                    cap = cv2.VideoCapture(path)
                    ret, frame = cap.read()
                    cap.release()
                    if ret:
                        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        h, w, ch = rgb.shape
                        qimg = QImage(rgb.data, w, h, w * ch, QImage.Format.Format_RGB888)
                        frame_px = QPixmap.fromImage(qimg)
                except Exception:
                    pass
            if frame_px and not frame_px.isNull():
                scaled = frame_px.scaled(330, 330,
                                         Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation)
                self.drop_zone.setPixmap(scaled)
                self.drop_zone.setText("")
            else:
                self.drop_zone.setPixmap(QPixmap())
                self.drop_zone.setText(_t("▶ VIDEO / ▶ 動画") if ext in ('.mp4', '.mkv', '.mov', '.avi', '.webm') else "?")

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

        self.preview_handler.show(path)
        self._refresh_inline_attrs(path)

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
                                      if r.get("field") and '/' in r.get("pattern", "")]
            self._path_rules_cache_key = key
        return self._path_rules_cache

    def _remove_missing_file(self, row, path):
        """Remove a file that no longer exists from the table, DB, and embeddings."""
        # Remove from attrs DB
        self.attrs_data.pop(path, None)
        attrs_mod.save(self.current_project, self.attrs_data)
        # Remove from embeddings
        if self.data and "paths" in self.data and path in self.data["paths"]:
            idx  = self.data["paths"].index(path)
            keep = [i for i in range(len(self.data["paths"])) if i != idx]
            self.data["paths"]      = [self.data["paths"][i] for i in keep]
            self.data["embeddings"] = self.data["embeddings"][keep]
            torch.save(self.data, os.path.join(attrs_mod.DATA_DIR, f"features_{self.current_project}.pt"))
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

    # ── Move / rename / delete ───────────────────────────────────────────────

    def on_right_key_press(self):
        row = self._current_row()
        if row < 0: return
        # In browse mode: right arrow re-enters browse on selected file's directory
        if self._browse_dir:
            self._enter_browse_mode()
            self._raise_preview()
            return
        # In search mode, row 0 is the query file — enter browse instead of moving it
        if row == 0 and self.query_path:
            self._enter_browse_mode()
            self._raise_preview()
            return
        if not self.query_path: return

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
            # In browse mode: left arrow exits browse and restores the previous search
            if getattr(self, '_browse_dir', None):
                self._exit_browse_mode()
                if self.query_path and os.path.exists(self.query_path):
                    self.run_search(self.query_path)
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

        self._update_row(row, src_path, final_path, overwrite, dest_path)
        self._post_move_dup_cleanup()
        self._select_row(min(row, self.table.rowCount() - 1))

    def _handle_drag_move(self, src_rows, target_row):
        target_path = self.table.get_row_path(target_row)
        if not target_path: return
        target_dir = os.path.dirname(os.path.abspath(target_path))
        mode       = self.config.get("move_conflict", "size_check")
        db_changed = False

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

            self._update_row(src_row, src_path, final_path, overwrite, dest_path)

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

        new_path, self.data, err, chosen_dir = front_page.select_and_move_file(
            self, old_path, self.data, self.current_project, self.last_move_dir,
            mode=self.config.get("move_conflict", "size_check"))

        if new_path:
            self.last_move_dir = chosen_dir
            self.config["last_move_dir"] = chosen_dir
            cfg.save_config(self.config, getattr(self, "current_project", None))
            self._update_row(row, old_path, new_path,
                             new_path == os.path.join(chosen_dir, os.path.basename(old_path)),
                             os.path.join(chosen_dir, os.path.basename(old_path)))
            self._post_move_dup_cleanup()
            next_row = row + 1 if row + 1 < self.table.rowCount() else row - 1
            if next_row >= 0:
                self._select_row(next_row)
                if row == 0:
                    self._rebase_to_row(next_row)
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
                self._push_undo({"type": "delete", "orig_path": path, "trash_path": trash_path,
                                  "row": row, "emb": emb, **row_snap})
                if self.data and "paths" in self.data and path in self.data["paths"]:
                    idx  = self.data["paths"].index(path)
                    keep = [i for i in range(len(self.data["paths"])) if i != idx]
                    self.data["paths"]      = [self.data["paths"][i] for i in keep]
                    self.data["embeddings"] = self.data["embeddings"][keep]
                self.table.removeRow(row)
                deleted_any = True
            elif err:
                errors.append(err)

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
