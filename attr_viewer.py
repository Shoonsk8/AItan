"""
attr_viewer.py — Standalone attribute panel viewer.
Reads attrs_tags.json and renders every field automatically based on its style.
Fields are draggable. Position saved to SQLite.
"""
import sys, json, sqlite3, os
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QCheckBox, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QTextEdit, QPlainTextEdit, QGroupBox, QGridLayout, QScrollArea,
    QColorDialog, QMenu, QLineEdit,
)
from PyQt6.QtGui import QColor, QAction, QPainter, QPen, QBrush
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QPoint


# ── Language helper ──────────────────────────────────────────────────────────
_UI_LANG = {"val": "en"}   # "en" or "ja"

def _lang_label(text: str) -> str:
    """'English / 日本語' → return the half for the current language.
    Splits at the RIGHTMOST ' / ' whose right side CONTAINS a CJK character
    (Hiragana, Katakana, or Kanji). English-only labels with embedded '/'
    (e.g. 'Closed / No eyes') and mixed cases like 'Closed / No eyes / 閉じている／目なし'
    both work; an emoji-prefixed right side like '🗄 データベース' also works
    because we scan the whole right side for a CJK char, not just the first."""
    if " / " not in text:
        return text
    def _has_cjk(s):
        for c in s:
            if ('぀' <= c <= 'ゟ') or ('゠' <= c <= 'ヿ') or ('一' <= c <= '鿿'):
                return True
        return False
    # Find all ' / ' split positions, prefer the RIGHTMOST that yields CJK-containing right
    idx = len(text)
    while True:
        idx = text.rfind(" / ", 0, idx)
        if idx == -1:
            return text
        right = text[idx + 3:]
        if _has_cjk(right):
            left = text[:idx].strip()
            return left if _UI_LANG["val"] == "en" else right.strip()


# ── Coded-field key lookup ────────────────────────────────────────────────────
# Human labels for coded section keys (e.g. "E" → "Eyes")
def _build_coded_labels():
    try:
        import aisearch_attrs as _am
        return {letter: label for letter, label, _ in _am._DEFAULT_CODED_FIELDS}
    except Exception:
        return {}

_CODED_LABELS = _build_coded_labels()

# Maps section key (canvas widget name) → lowercase storage key.
#
# Three layers, intentionally distinct:
#   - filename code:   uppercase letter from CODED_FIELDS, e.g. "BG"
#   - storage key:     lowercase, e.g. "bg" (what attrs.json holds)
#   - canvas section:  human-readable, e.g. "Background"
#
# `Background` is the canvas section name; the alias resolves it down
# to the storage key `bg` so reads/writes hit the right slot. This is
# the ONLY direction (section → storage); never add the inverse.
_HUMAN_LABEL_ALIASES = {
    "Background": "bg",
}

def _build_section_to_field_key():
    try:
        import aisearch_attrs as _am
        _map = {}
        for letter, _, digits in _am.CODED_FIELDS:
            if digits == 0:
                continue
            _map[letter] = letter.lower()
        _map.update(_HUMAN_LABEL_ALIASES)
        return _map
    except Exception:
        return dict(_HUMAN_LABEL_ALIASES)

_SECTION_KEY_TO_FIELD = _build_section_to_field_key()

# ── Config ────────────────────────────────────────────────────────────────────

_DATA_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CONFIG_FILE = os.path.join(_DATA_DIR, "attrs_tags.json")
DB_FILE     = os.path.join(_DATA_DIR, "attr_viewer.db")

def _db_file_for_config(config_path):
    """Derive the per-project SQLite DB path from its config (attrs_tags) path."""
    base = os.path.basename(config_path)
    stem = os.path.splitext(base)[0]       # e.g. "attrs_tags_AIX"
    db_name = stem.replace("attrs_tags", "attr_viewer", 1) + ".db"
    return os.path.join(_DATA_DIR, db_name)

def _seed_project_db(config_path, dst_db):
    """Seed a new project DB from the global DB, keeping only rows whose keys
    exist in the project's config. Omits stale positions/connections/sizes for
    panels that don't exist in this project."""
    if not os.path.exists(DB_FILE):
        return
    try:
        cfg = load_config(config_path)
    except Exception:
        return
    valid_keys = set(cfg.get("__section_order__",
                             [k for k in cfg if not k.startswith("__")]))
    src = sqlite3.connect(DB_FILE)
    dst = sqlite3.connect(dst_db)
    init_db(dst)
    init_connections(dst)
    # layout (positions)
    for row in src.execute("SELECT key, x, y FROM layout"):
        if row[0] in valid_keys:
            dst.execute("INSERT OR REPLACE INTO layout VALUES (?,?,?)", row)
    # sizes
    for row in src.execute("SELECT key, w, h FROM sizes"):
        if row[0] in valid_keys:
            dst.execute("INSERT OR REPLACE INTO sizes VALUES (?,?,?)", row)
    # collapsed
    for row in src.execute("SELECT key, val FROM collapsed"):
        if row[0] in valid_keys:
            dst.execute("INSERT OR REPLACE INTO collapsed VALUES (?,?)", row)
    # group_colors — groups are shared config, keep all
    for row in src.execute("SELECT grp, color FROM group_colors"):
        dst.execute("INSERT OR REPLACE INTO group_colors VALUES (?,?)", row)
    # usage
    for row in src.execute("SELECT key, count FROM usage"):
        dst.execute("INSERT OR REPLACE INTO usage VALUES (?,?)", row)
    # connections — only if BOTH endpoints exist in this project
    init_connections(src)
    for row in src.execute("SELECT box_a, port_a, box_b, port_b FROM connections"):
        if row[0] in valid_keys and row[2] in valid_keys:
            dst.execute("INSERT INTO connections (box_a, port_a, box_b, port_b) VALUES (?,?,?,?)", row)
    dst.commit()
    src.close()
    dst.close()

# Maps canvas field key → attrs entry db key (for text fields)
_TEXT_KEY_MAP = {
    "positive_prompt": "prompt",
    "negative_prompt": "neg_prompt",
    "speech":          "speech",
    "note":            "note",
}

# Radio/select fields that store to a dedicated entry key instead of the tags list
_DEDICATED_FIELD_KEYS = {
    "Audio": "audio",
}

def load_config(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)

# ── Usage tracking (in-memory, persisted to SQLite) ──────────────────────────

_usage = {}  # key → count

def get_usage(key): return _usage.get(key, 0)
def inc_usage(key): _usage[key] = _usage.get(key, 0) + 1

def load_usage(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS usage (key TEXT PRIMARY KEY, count INTEGER)")
    for row in conn.execute("SELECT key, count FROM usage"):
        _usage[row[0]] = row[1]

def save_usage(conn, key):
    conn.execute("INSERT OR REPLACE INTO usage VALUES (?,?)", (key, _usage.get(key,0)))
    conn.commit()

# ── Position DB ───────────────────────────────────────────────────────────────

def init_db(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS layout (key TEXT PRIMARY KEY, x INTEGER, y INTEGER)")
    conn.execute("CREATE TABLE IF NOT EXISTS usage  (key TEXT PRIMARY KEY, count INTEGER)")
    conn.execute("CREATE TABLE IF NOT EXISTS group_colors (grp TEXT PRIMARY KEY, color TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS sizes (key TEXT PRIMARY KEY, w INTEGER, h INTEGER)")
    conn.execute("CREATE TABLE IF NOT EXISTS collapsed (key TEXT PRIMARY KEY, val INTEGER)")
    # Per-field popup mode for matrix combos: "grid" | "freq" | "alpha".
    # Plain combos store "freq" | "alpha" too. Right-click cycles, the
    # selected mode is restored on next load.
    conn.execute("CREATE TABLE IF NOT EXISTS popup_modes (key TEXT PRIMARY KEY, mode TEXT)")
    conn.commit()


def load_popup_modes(conn):
    """Return {key: mode} where mode is 'grid' / 'freq' / 'alpha'."""
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS popup_modes (key TEXT PRIMARY KEY, mode TEXT)")
        return {r[0]: r[1] for r in conn.execute("SELECT key, mode FROM popup_modes")}
    except Exception:
        return {}


def save_popup_mode(conn, key, mode):
    conn.execute("INSERT OR REPLACE INTO popup_modes VALUES (?,?)", (key, mode))
    conn.commit()

def load_collapsed(conn):
    return {r[0]: bool(r[1]) for r in conn.execute("SELECT key,val FROM collapsed")}

def save_collapsed(conn, key, val):
    conn.execute("INSERT OR REPLACE INTO collapsed VALUES (?,?)", (key, int(val)))
    conn.commit()

def load_positions(conn):
    return {r[0]: (r[1], r[2]) for r in conn.execute("SELECT key,x,y FROM layout")}

def save_position(conn, key, x, y):
    conn.execute("INSERT OR REPLACE INTO layout VALUES (?,?,?)", (key,x,y))
    conn.commit()

def load_sizes(conn):
    return {r[0]: (r[1], r[2]) for r in conn.execute("SELECT key,w,h FROM sizes")}

def save_size(conn, key, w, h):
    conn.execute("INSERT OR REPLACE INTO sizes VALUES (?,?,?)", (key,w,h))
    conn.commit()

def load_group_colors(conn):
    return {r[0]: r[1] for r in conn.execute("SELECT grp, color FROM group_colors")}

def save_group_color(conn, grp, color):
    conn.execute("INSERT OR REPLACE INTO group_colors VALUES (?,?)", (grp, color))
    conn.commit()

def init_connections(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS connections
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     box_a TEXT, port_a TEXT,
                     box_b TEXT, port_b TEXT)""")
    conn.commit()

def load_connections(conn):
    """Returns list of (id, box_a, port_a, box_b, port_b)."""
    init_connections(conn)
    return list(conn.execute("SELECT id, box_a, port_a, box_b, port_b FROM connections"))

def save_connection(conn, box_a, port_a, box_b, port_b):
    cur = conn.execute("INSERT INTO connections (box_a, port_a, box_b, port_b) VALUES (?,?,?,?)",
                       (box_a, port_a, box_b, port_b))
    conn.commit()
    return cur.lastrowid

def delete_connection(conn, cid):
    conn.execute("DELETE FROM connections WHERE id=?", (cid,))
    conn.commit()

# ── Port geometry ─────────────────────────────────────────────────────────────

PORT_NAMES = ["TL", "TR", "BL", "BR"]   # 4 corners only
_DOT_R     = 6   # dot radius
_DOT_HIT   = 12  # click detection radius

# Outward direction per corner: (sign_x, sign_y)
_PORT_DIR = {"TL": (-1, -1), "TR": (1, -1), "BL": (-1, 1), "BR": (1, 1)}

def _corner_pos(widget, port):
    """Exact canvas-coordinate box corner — used for snap (zero gap)."""
    sx, sy = _PORT_DIR.get(port, (0, 0))
    bx = widget.x() + (widget.width()  if sx > 0 else 0)
    by = widget.y() + (widget.height() if sy > 0 else 0)
    return QPoint(bx, by)

def _port_pos(widget, port):
    """Dot draw position: dot edge touches corner, dot body is fully visible."""
    sx, sy = _PORT_DIR.get(port, (0, 0))
    cp = _corner_pos(widget, port)
    return QPoint(cp.x() + sx * _DOT_R, cp.y() + sy * _DOT_R)

# ── Field widget ──────────────────────────────────────────────────────────────

_BTN_OFF = ("QPushButton{background:#333;color:#fff;border:1px solid #555;"
            "padding:3px 10px;border-radius:3px;font-size:10pt;}"
            "QPushButton:hover{background:#444;}")
_BTN_ON  = ("QPushButton{background:#4a7a4e;color:#fff;border:1px solid #6aaa6e;"
            "padding:3px 10px;border-radius:3px;font-size:10pt;font-weight:bold;}"
            "QPushButton:hover{background:#5a8a5e;}")
_CB_SS   = ("QComboBox{background:#2e2e2e;color:#fff;border:1px solid #555;"
            "padding:3px 8px;border-radius:3px;font-size:10pt;}"
            "QComboBox::drop-down{border:none;}"
            "QComboBox QAbstractItemView{background:#2e2e2e;color:#fff;"
            "selection-background-color:#4a7a4e;}")


class _ResizeHandle(QWidget):
    """Thin drag strip at the bottom of a FieldWidget — always receives mouse events."""
    def __init__(self, parent):
        super().__init__(parent)
        self.setFixedHeight(6)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setStyleSheet("background: rgba(255,255,255,18); border-top: 1px solid rgba(255,255,255,30);")
        self._start_pos  = None
        self._start_h    = None

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            if not getattr(self.parent(), "edit_mode", False):
                e.accept()
                return
            # Push undo before resize starts
            p = self.parent()
            cv = getattr(p, "_canvas_ref", None)
            viewer = getattr(cv, "_viewer", None)
            if viewer:
                viewer._push_undo()
            self._start_pos = e.globalPosition().toPoint()
            self._start_h   = p.height()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._start_pos is not None:
            dy = e.globalPosition().toPoint().y() - self._start_pos.y()
            new_h = max(60, self._start_h + dy)
            self.parent().resize(self.parent().width(), int(new_h))
            e.accept()

    def mouseReleaseEvent(self, e):
        if self._start_pos is not None:
            p = self.parent()
            try:
                save_size(p.conn, p.key, p.width(), p.height())
                p.resized.emit(p.key)
            except Exception:
                pass
            self._start_pos = None
            self._start_h   = None
        e.accept()


class _GridPopupCombo(QComboBox):
    """QComboBox that pops a 16x16 (or 4x4) grid of cells instead of
    the default linear list. Used for matrix-style fields where the
    option keys are 1- or 2-digit hex codes (e.g. X expression with
    "00"-"FF", BG with "0"-"F"). Cells without a defined label render
    as disabled placeholders so the geometry is preserved.

    The combo's text + currentData behavior is unchanged — only the
    popup display is overridden. So existing _on_select / _fill_combo
    code keeps working; this is purely a UI layer."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._popup_widget = None

    def showPopup(self):
        # The owning FieldWidget can flip _matrix_mode to "linear" via
        # right-click — in that case fall back to the standard linear
        # dropdown so freq/alpha sort remains useful.
        owner = self.parent()
        while owner is not None and not hasattr(owner, "_matrix_mode"):
            owner = owner.parent()
        if owner is not None and getattr(owner, "_matrix_mode", "grid") != "grid":
            super().showPopup()
            return
        # If the combo has 1- or 2-digit hex keys, show a grid.
        # Otherwise fall back to the standard list popup.
        keys = []
        for i in range(self.count()):
            data = self.itemData(i)
            if data is None or data == "":
                continue
            keys.append(str(data))
        if not keys:
            super().showPopup()
            return
        is_2digit = all(len(k) == 2 for k in keys)
        is_1digit = all(len(k) == 1 for k in keys) and not is_2digit
        if not (is_2digit or is_1digit):
            super().showPopup()
            return

        rows = cols = 16 if is_2digit else 4

        # Build label lookup from current items
        labels = {}
        for i in range(self.count()):
            data = self.itemData(i)
            if data is not None and data != "":
                labels[str(data)] = self.itemText(i)

        # Lazy-build the popup widget
        from PyQt6.QtWidgets import QFrame, QToolButton
        if self._popup_widget is not None:
            try:
                self._popup_widget.close()
                self._popup_widget.deleteLater()
            except Exception:
                pass
        popup = QFrame(self, Qt.WindowType.Popup)
        popup.setFrameShape(QFrame.Shape.Panel)
        popup.setStyleSheet(
            "QFrame { background:#222; border:1px solid #555; }")
        gl = QGridLayout(popup)
        gl.setContentsMargins(4, 4, 4, 4)
        gl.setSpacing(2)

        # Cell width — wider for 2-digit grids (X expression, etc.)
        # so 16-column rows show longer labels without ugly truncation.
        cell_w = 110 if is_2digit else 84
        cell_h = 28
        # Truncate label to whatever the cell width can hold at the
        # current font (font-size:9pt → ~6 px/char average).
        max_chars = max(8, (cell_w - 8) // 6)

        cur_data = self.currentData() or ""
        for r in range(rows):
            for c in range(cols):
                if is_2digit:
                    code = format(r, "x") + format(c, "x")
                else:
                    code = format(r * cols + c, "x")
                lbl = labels.get(code, "")
                btn = QToolButton(popup)
                btn.setText(lbl[:max_chars] if lbl else "")
                btn.setToolTip(f"{code}  {lbl}" if lbl else code)
                btn.setFixedSize(cell_w, cell_h)
                btn.setStyleSheet(
                    "QToolButton { color:#ddd; background:#2a2a2a; "
                    "border:1px solid #444; padding:1px 3px; "
                    "font-size:9pt; }"
                    "QToolButton:hover { background:#3a4a6a; }"
                    "QToolButton:disabled { color:#555; background:#222; }"
                )
                if not lbl:
                    btn.setEnabled(False)
                if code == cur_data:
                    btn.setStyleSheet(btn.styleSheet() +
                        "QToolButton { border:2px solid #4a90e2; }")
                btn.clicked.connect(
                    lambda _, k=code: self._on_grid_pick(k, popup))
                gl.addWidget(btn, r, c)

        # Position the popup just below the combo
        popup.adjustSize()
        gp = self.mapToGlobal(QPoint(0, self.height()))
        popup.move(gp)
        popup.show()
        self._popup_widget = popup

    def _on_grid_pick(self, code, popup):
        idx = self.findData(code)
        if idx >= 0:
            self.setCurrentIndex(idx)
        try:
            popup.close()
            popup.deleteLater()
        except Exception:
            pass
        self._popup_widget = None


class FieldWidget(QGroupBox):
    moved            = pyqtSignal(str, int, int)
    resized          = pyqtSignal(str)
    action_triggered = pyqtSignal(str, str)   # (key, action_name)

    _RESIZE_GRIP = 12   # px square in bottom-right corner that triggers resize


    def __init__(self, key, label, style, options, text_meta, conn,
                 color=None, group=None, group_peers=None, size=None,
                 collapsible=True, collapsed=False, hidden_for=None,
                 exclusive=False, parent=None):
        self._label_raw = label or key
        super().__init__(_lang_label(self._label_raw), parent)
        self.key          = key
        self.style        = style
        self.options      = options   # [[k, lbl], ...]
        self.conn         = conn
        self.drag_mode    = False
        self.edit_mode    = False
        self._drag_pos    = None
        self._resize_pos  = None
        self._resize_start_size = None
        self._resize_dir  = None
        self._sort_freq   = True
        # Popup display mode for matrix-style fields. "grid" uses the
        # 16×16 / 4×4 picker; "linear" falls back to a flat dropdown
        # that respects _sort_freq. Right-click cycles grid → freq →
        # alpha → grid. For non-matrix fields this flag is unused —
        # they always render a linear popup.
        self._matrix_mode = "grid"
        self._group       = group
        self._group_peers = group_peers or []
        self._bg_color    = color or "#2a2a2a"
        self._collapsible = collapsible
        self._collapsed   = False
        self._snap        = False
        self._hidden_for  = list(hidden_for or [])   # ["image", "video", ...]
        self._exclusive   = exclusive                 # single-select taglist (radio)
        self._cfg_path    = None   # set by _build so we can save back to JSON
        self._conditions  = []     # [{"source": key, "op": op, "value": val}, ...]
        self._selected    = False  # Ctrl+A selection for move-together
        self.setMouseTracking(True)

        self._apply_color(self._bg_color)

        # config reference for coded sub-tables
        self._cfg = text_meta.get("__config__") if isinstance(text_meta, dict) else None

        vlay = QVBoxLayout(self)
        vlay.setContentsMargins(4, 4, 4, 2)
        vlay.setSpacing(2)

        # Floating "≡" button at top-right corner — left-click opens the
        # same menu as right-click (Show / Update / Disconnect / Color /
        # Hide-for / Hide-when). Title click is preserved for collapse,
        # so users get menu access without losing the toggle gesture.
        from PyQt6.QtWidgets import QToolButton
        self._menu_btn = QToolButton(self)
        self._menu_btn.setText("≡")
        self._menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._menu_btn.setStyleSheet(
            "QToolButton{background:#3a3a3a;color:#ddd;border:1px solid #555;"
            "border-radius:3px;font-size:11pt;padding:0 4px;}"
            "QToolButton:hover{background:#4a4a4a;color:#fff;}")
        self._menu_btn.setFixedSize(20, 16)
        self._menu_btn.setToolTip(_lang_label("Field menu / フィールドメニュー"))
        self._menu_btn.clicked.connect(self._on_menu_btn_click)
        # Position is set in resizeEvent so it tracks the top-right
        # corner of the widget regardless of size changes.

        if style == "text":
            placeholder = (text_meta or {}).get("placeholder", "") if isinstance(text_meta, dict) else ""
            # QPlainTextEdit (not QTextEdit) — handles long debug dumps without
            # "QTextCursor::setPosition out of range" warnings on huge text.
            # Same API for setPlainText/toPlainText so the rest of the code
            # is unchanged.
            self._te = QPlainTextEdit()
            self._te.setMinimumHeight(0)
            self._te.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
            self._te.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self._te.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._te.setPlaceholderText(placeholder or _lang_label(f"{label}… / {label}…"))
            self._te.setStyleSheet(
                "background:#3a3a3a;color:#fff;border:1px solid #555;font-size:10pt;")
            # Save-on-focus-out: when the user clicks away from the text
            # field, immediately fire data_changed and a dedicated commit
            # signal so the parent saves *now* rather than after the
            # debounce timer. Defends against navigation losing typed text.
            _orig_focus_out = self._te.focusOutEvent
            def _focus_out_save(ev, _self=self, _orig=_orig_focus_out):
                _orig(ev)
                # Find the AttrViewerWidget ancestor and emit data_changed
                # so its _text_save_timer triggers and saves immediately.
                try:
                    p = _self.parent()
                    while p is not None and not hasattr(p, "data_changed"):
                        p = p.parent()
                    if p is not None:
                        p.data_changed.emit()
                except Exception:
                    pass
            self._te.focusOutEvent = _focus_out_save
            vlay.addWidget(self._te)

        elif style == "pathlist":
            # List widget of file/folder paths. Double-click opens a path in
            # the OS file manager. Add-file / add-folder / remove buttons.
            from PyQt6.QtWidgets import QListWidget, QFileDialog
            from PyQt6.QtGui import QDesktopServices
            self._pathlist = QListWidget()
            self._pathlist.setMinimumHeight(0)
            self._pathlist.setStyleSheet(
                "QListWidget { background:#262626; color:#cce0ff; "
                "border:1px solid #555; font-size:9pt; }"
                "QListWidget::item { padding:2px 4px; }"
                "QListWidget::item:selected { background:#3a5a8a; color:#fff; }")
            self._pathlist.setToolTip(
                _lang_label("Double-click to open in file manager / ダブルクリックでファイルマネージャーで開く"))
            def _open_item(item, _qd=QDesktopServices):
                p = item.text().strip()
                if p:
                    from PyQt6.QtCore import QUrl as _QU
                    _qd.openUrl(_QU.fromLocalFile(p))
            self._pathlist.itemDoubleClicked.connect(_open_item)
            vlay.addWidget(self._pathlist)

            def _emit_changed(_self=self):
                # Walk up to AttrViewerWidget and fire data_changed so the
                # parent saves through the normal canvas pipeline.
                p = _self.parent()
                while p is not None and not hasattr(p, "data_changed"):
                    p = p.parent()
                if p is not None:
                    p.data_changed.emit()

            def _add_path(want_dir, _self=self):
                # Open the picker in the current file's directory so the
                # user lands next to the asset they're tagging. Fall back
                # to home only when no current path is set yet.
                _cur = getattr(_self, "_cur_file_path", "") or ""
                if _cur and os.path.isfile(_cur):
                    start = os.path.dirname(_cur)
                elif _cur and os.path.isdir(_cur):
                    start = _cur
                else:
                    start = os.path.expanduser("~")
                if want_dir:
                    picked = QFileDialog.getExistingDirectory(
                        _self, "Add related folder", start)
                else:
                    picked, _ = QFileDialog.getOpenFileName(
                        _self, "Add related file", start)
                if not picked:
                    return
                existing = {_self._pathlist.item(i).text().strip()
                            for i in range(_self._pathlist.count())}
                if picked in existing:
                    return
                _self._pathlist.addItem(picked)
                _emit_changed()

            def _remove_paths(_self=self):
                rows = sorted({_self._pathlist.row(it)
                               for it in _self._pathlist.selectedItems()},
                              reverse=True)
                if not rows:
                    return
                for r in rows:
                    _self._pathlist.takeItem(r)
                _emit_changed()

            row_btns = QHBoxLayout()
            row_btns.setContentsMargins(0, 0, 0, 0)
            row_btns.setSpacing(3)
            btn_addf = QPushButton("📄")
            btn_addf.setToolTip(_lang_label("Add file / ファイルを追加"))
            btn_addf.setFixedWidth(30)
            btn_addf.clicked.connect(lambda: _add_path(False))
            row_btns.addWidget(btn_addf)
            btn_addd = QPushButton("📂")
            btn_addd.setToolTip(_lang_label("Add folder / フォルダを追加"))
            btn_addd.setFixedWidth(30)
            btn_addd.clicked.connect(lambda: _add_path(True))
            row_btns.addWidget(btn_addd)
            btn_rm = QPushButton("×")
            btn_rm.setToolTip(_lang_label("Remove selected / 選択した項目を削除"))
            btn_rm.setFixedWidth(30)
            btn_rm.clicked.connect(_remove_paths)
            row_btns.addWidget(btn_rm)
            row_btns.addStretch()
            vlay.addLayout(row_btns)
            # Delete key removes selected
            from PyQt6.QtGui import QShortcut, QKeySequence
            _del_sc = QShortcut(QKeySequence("Delete"), self._pathlist)
            _del_sc.activated.connect(_remove_paths)

        elif style in ("taglist", "boolean", "radio"):
            self._btns = {}
            from PyQt6.QtWidgets import QButtonGroup
            self._btn_group = QButtonGroup(self) if (style == "radio" or exclusive) else None
            if self._btn_group:
                self._btn_group.setExclusive(True)
            grid = QGridLayout(); grid.setSpacing(3)
            COLS = len(options) if style == "radio" else 4
            for i, (k, lbl) in enumerate(options):
                btn = QPushButton(_lang_label(lbl))
                btn._lbl_raw = lbl
                btn.setCheckable(True)
                btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                btn.setStyleSheet(_BTN_OFF)
                def _tog(checked, _k=k, _b=btn):
                    _b.setStyleSheet(_BTN_ON if checked else _BTN_OFF)
                btn.toggled.connect(_tog)
                self._btns[k] = btn
                if self._btn_group:
                    self._btn_group.addButton(btn)
                grid.addWidget(btn, i // COLS, i % COLS)
            vlay.addLayout(grid)

        elif style in ("1dig", "2dig", "3dig", "4dig") and self._cfg:
            # Coded field — one freq-sorted combo per sub-table, auto-detected by prefix.
            # Each entry is (sub_key, QComboBox, pos) where pos is the 1-based
            # digit position the combo controls (pos=1 = rightmost digit).
            self._coded_combos = []   # [(sub_key, QComboBox, pos), ...]
            sub_tables = [(k, v) for k, v in self._cfg.items()
                          if k.startswith(key + "_") and isinstance(v, list) and v]
            # Map sub-table suffix → digit position. Without this, combos were
            # indexed by config-iteration order, so a project that listed
            # CL_Top before CL_Bot would have the "Top" combo display the
            # bottom digit (pos 1) — confusing the user.
            _SUBPOS = {
                "CL": {"Bot": 1, "BotColor": 2, "Top": 3, "TopColor": 4},
                "HC": {"Length": 1, "Style": 2, "Color": 3},
                "FA": {"Direction": 1, "Vert": 2, "Vertical": 2},
                "PM": {"Motion": 1, "Posture": 2},
                "CS": {"Light": 1, "Lighting": 1, "Angle": 2, "Shot": 3},
                "E":  {"Color": 1, "Additional": 2, "Modifier": 2},
            }
            _pos_map = _SUBPOS.get(key, {})
            def _pos_for(sub_key):
                # Strip "<key>_" prefix
                _suffix = sub_key[len(key)+1:] if sub_key.startswith(key + "_") else sub_key
                if _suffix in _pos_map:
                    return _pos_map[_suffix]
                # Fall back to dict order (legacy behavior) — index in sub_tables
                return None
            # Sort by descending digit position — leftmost combo = highest
            # pos = leftmost digit in the stored value (natural left-to-right
            # reading order matches the value reading order). For CL:
            # TopColor (pos 4) | Top (3) | BotColor (2) | Bot (1).
            if _pos_map:
                sub_tables = sorted(
                    sub_tables,
                    key=lambda kv: -(_pos_for(kv[0]) or 0))
            if sub_tables:
                # Track sub-combos so the right-click toggle can refill
                # them all when the user flips freq ⇄ alpha sort.
                self._sub_combos = []   # list of (combo, options)
                for _idx, (sub_key, sub_opts) in enumerate(sub_tables):
                    sub_lbl = sub_key[len(key)+1:].replace("_", " ")
                    row = QHBoxLayout(); row.setSpacing(4)
                    row.addWidget(QLabel(sub_lbl + ":", styleSheet="color:#aaa;font-size:9pt;"))
                    cb = QComboBox(); cb.setStyleSheet(_CB_SS)
                    # ALWAYS add "—" (no selection) as the first option, even
                    # when "0" is a valid value. Otherwise the most-used real
                    # code lands at index 0 and becomes the silent default for
                    # any unset field — every freshly-saved file ended up with
                    # E33 HC333 PM33 etc. just because "3" was popular.
                    self._fill_sub_combo(cb, sub_opts)
                    cb.currentIndexChanged.connect(
                        lambda _, _k=sub_key, _cb=cb: (inc_usage(_cb.currentData() or ""),
                                                        save_usage(self.conn, _cb.currentData() or "")))
                    cb.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                    cb.customContextMenuRequested.connect(lambda _: self._toggle_sort())
                    _explicit_pos = _pos_for(sub_key) if _pos_map else None
                    _final_pos = _explicit_pos if _explicit_pos is not None else (_idx + 1)
                    self._coded_combos.append((sub_key, cb, _final_pos))
                    self._sub_combos.append((cb, sub_opts))
                    row.addWidget(cb, stretch=1)
                    vlay.addLayout(row)
            else:
                # No sub-tables found — plain hex text input
                self._hex_edit = QLineEdit()
                self._hex_edit.setPlaceholderText(_lang_label("hex… / hex…"))
                self._hex_edit.setStyleSheet(
                    "background:#3a3a3a;color:#fff;border:1px solid #555;"
                    "font-family:monospace;font-size:10pt;")
                vlay.addWidget(self._hex_edit)

        elif style == "id" and key == "J":
            # J is a timestamp — show decoded date as read-only label
            self._date_lbl = QLabel("—")
            self._date_lbl.setStyleSheet(
                "color:#aaa; font-family:monospace; font-size:9pt;")
            vlay.addWidget(self._date_lbl)

        elif style == "id":
            if key == "P":
                # P — editable person ID input + Detect button
                self._pid_edit = QLineEdit()
                self._pid_edit.setPlaceholderText(_lang_label("ID… / ID…"))
                self._pid_edit.setMaxLength(6)
                self._pid_edit.setStyleSheet(
                    "QLineEdit{background:#1e1e1e;color:#aaa;border:1px solid #444;"
                    "border-radius:2px;font-family:monospace;font-size:9pt;padding:1px 4px;}"
                    "QLineEdit:focus{border-color:#6a8a6a;}")
                # 👤 button — opens Settings → Persons and highlights the card
                # for this file's current ID. Replaces the old Detect button:
                # right-click → Update already runs face detection (read-only),
                # so a separate one-click "register new" doesn't add value to
                # this widget. Quick access to the registry is more useful.
                self._detect_btn = QPushButton("👤")
                self._detect_btn.setFixedHeight(20)
                self._detect_btn.setFixedWidth(28)
                self._detect_btn.setStyleSheet(
                    "QPushButton{background:#2e4a2e;color:#8fc88f;border:1px solid #4a6a4a;"
                    "border-radius:3px;font-size:9pt;padding:0;}"
                    "QPushButton:hover{background:#3a5e3a;}"
                    "QPushButton:disabled{color:#555;border-color:#333;background:#222;}")
                self._detect_btn.setToolTip(_lang_label("Open Persons settings and highlight this ID / 人物設定を開いてこのIDを強調表示"))
                self._detect_btn.clicked.connect(
                    lambda: self.action_triggered.emit(self.key, "edit_person"))
                # ➕ "Assign new person ID" — allocates the next free pid,
                # seeds the faces DB with this file's face encoding as
                # the base, and tags the file. Use after Update Face
                # shows that the auto-detected match is wrong.
                self._new_person_btn = QPushButton("➕")
                self._new_person_btn.setFixedHeight(20)
                self._new_person_btn.setFixedWidth(28)
                self._new_person_btn.setStyleSheet(
                    "QPushButton{background:#3a3a4e;color:#aabbe5;border:1px solid #4a5a7a;"
                    "border-radius:3px;font-size:9pt;padding:0;}"
                    "QPushButton:hover{background:#4a4a6a;}"
                    "QPushButton:disabled{color:#555;border-color:#333;background:#222;}")
                self._new_person_btn.setToolTip(_lang_label(
                    "Assign new person ID / 新規人物IDを割当"))
                self._new_person_btn.clicked.connect(
                    lambda: self.action_triggered.emit(self.key, "new_person"))
                _id_row = QHBoxLayout()
                _id_row.setContentsMargins(0, 0, 0, 0)
                _id_row.addWidget(self._pid_edit, stretch=1)
                _id_row.addWidget(self._detect_btn)
                _id_row.addWidget(self._new_person_btn)
                vlay.addLayout(_id_row)
            elif key in ("PI", "PW"):
                # PI / PW — plain text input + 👤 button (same shape as P).
                # Blank means "same as P" (PI: no face swap) or "none"
                # (PW: no other person). Placeholders make that explicit so
                # the field doesn't visually duplicate P when empty.
                self._pid_edit = QLineEdit()
                self._pid_edit.setPlaceholderText(
                    _lang_label("— (same as P) / — (Pと同じ)") if key == "PI" else _lang_label("— (none) / — (なし)"))
                self._pid_edit.setMaxLength(6 if key == "PI" else 48)
                self._pid_edit.setStyleSheet(
                    "QLineEdit{background:#1e1e1e;color:#aaa;border:1px solid #444;"
                    "border-radius:2px;font-family:monospace;font-size:9pt;padding:1px 4px;}"
                    "QLineEdit:focus{border-color:#6a8a6a;}")
                self._detect_btn = QPushButton("👤")
                self._detect_btn.setFixedHeight(20)
                self._detect_btn.setFixedWidth(28)
                self._detect_btn.setStyleSheet(
                    "QPushButton{background:#2e4a2e;color:#8fc88f;border:1px solid #4a6a4a;"
                    "border-radius:3px;font-size:9pt;padding:0;}"
                    "QPushButton:hover{background:#3a5e3a;}")
                self._detect_btn.setToolTip(_lang_label("Open Persons settings to assign an ID / 人物設定を開いてIDを割り当て"))
                self._detect_btn.clicked.connect(
                    lambda: self.action_triggered.emit(self.key, "edit_person"))
                _row = QHBoxLayout()
                _row.setContentsMargins(0, 0, 0, 0)
                _row.addWidget(self._pid_edit, stretch=1)
                _row.addWidget(self._detect_btn)
                vlay.addLayout(_row)
            else:
                # Other id keys (none in current FIELD_DEFS — A is a matrix now)
                self._id_lbl = QLabel("—")
                self._id_lbl.setStyleSheet(
                    "color:#aaa; font-family:monospace; font-size:9pt;")
                vlay.addWidget(self._id_lbl)

        elif style == "matrix" and not options:
            # Matrix with no entries yet — show a clear placeholder instead of editable combo
            _hint = QLabel(_lang_label("(no entries — add in Settings) / （項目なし — 設定で追加してください）"))
            _hint.setStyleSheet("color:#666; font-size:8pt; font-style:italic;")
            vlay.addWidget(_hint)

        elif style == "matrix" or (options and style not in ("text",)):
            # Matrix-style fields (X, A, ModelImage, ModelVideo, …) use
            # the grid popup so users can scan a 16×16 (or 4×4) layout
            # of code+label cells instead of a long flat dropdown.
            # Other tag-list-with-options fields stay as plain combos.
            if style == "matrix":
                self._cb = _GridPopupCombo()
            else:
                self._cb = QComboBox()
            self._cb.setStyleSheet(_CB_SS)
            self._cb.setMinimumWidth(160)
            # Sort/mode label. For matrix fields it cycles grid → freq →
            # alpha; for plain combos it cycles freq → alpha.
            self._sort_lbl = QLabel(self._sort_label_text())
            self._sort_lbl.setStyleSheet("color:#888;font-size:9pt;")
            self._fill_combo()
            self._cb.currentIndexChanged.connect(self._on_select)
            self._cb.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self._cb.customContextMenuRequested.connect(lambda _: self._toggle_sort())
            row = QHBoxLayout()
            row.addWidget(self._cb, stretch=1)
            row.addWidget(self._sort_lbl)
            vlay.addLayout(row)

        else:
            vlay.addWidget(QLabel("—", styleSheet="color:#666;font-size:8pt;"))

        self._resize_handle = _ResizeHandle(self)
        vlay.addWidget(self._resize_handle)

        self.adjustSize()
        self._expanded_height = None
        if collapsed:
            QTimer.singleShot(50, self._do_collapse)

    def _do_collapse(self):
        self._expanded_height = self.height()
        self._collapsed = True
        title_h = self.fontMetrics().height() + 16
        self.setFixedHeight(title_h)
        _rh = getattr(self, "_resize_handle", None)
        if _rh: _rh.hide()

    def _toggle_collapse(self):
        if not self._collapsible:
            return
        _rh = getattr(self, "_resize_handle", None)
        if self._collapsed:
            self._collapsed = False
            self.setMinimumHeight(0)
            self.setMaximumHeight(16777215)
            h = self._expanded_height or self.sizeHint().height()
            self.resize(self.width(), h)
            if _rh: _rh.show()
        else:
            self._expanded_height = self.height()
            self._collapsed = True
            title_h = self.fontMetrics().height() + 16
            self.setFixedHeight(title_h)
            if _rh: _rh.hide()
        save_collapsed(self.conn, self.key, self._collapsed)

    # ── Combo helpers ─────────────────────────────────────────────────────────

    def _fill_combo(self, preserve=False):
        cur = self._cb.currentData() if preserve else None
        # Alpha sort uses the language-stripped DISPLAY label (case-
        # insensitive), not the raw "EN / JP" string — otherwise the
        # English half always dominated the sort even in Japanese mode.
        # Freq sort still ties on the raw label so the order is stable.
        items = sorted(
            self.options,
            key=(lambda kv: (-get_usage(kv[0]), _lang_label(kv[1]).casefold()))
                if self._sort_freq
                else (lambda kv: _lang_label(kv[1]).casefold()))
        self._cb.blockSignals(True)
        self._cb.clear()
        # ALWAYS add "—" (no selection, empty data) at the top so an unset
        # entry shows up as empty. Was conditional on "0"/"none" not being
        # in the options, but sort-by-usage then put the most-used real code
        # at index 0 — every fresh file silently inherited X11 / BG11 / etc.
        self._cb.addItem("—", "")
        for k, lbl in items:
            self._cb.addItem(_lang_label(lbl), k)
        if cur:
            idx = self._cb.findData(cur)
            if idx >= 0:
                self._cb.setCurrentIndex(idx)
        self._cb.blockSignals(False)

    def _on_select(self, idx):
        key = self._cb.currentData() or (self._cb.currentText() if self._cb.isEditable() else "")
        if key:
            inc_usage(key)
            save_usage(self.conn, key)
            if self.options:
                self._fill_combo(preserve=True)

    def _fill_sub_combo(self, cb, opts):
        """Fill a coded-field sub-combo (HC_Color, CL_Top, etc.). Sort
        order honors the FieldWidget's _sort_freq flag — same toggle
        as the main combo, so a single right-click flips every sub-
        combo for a given coded field."""
        cur = cb.currentData()
        cb.blockSignals(True)
        cb.clear()
        cb.addItem("—", "")
        items = sorted(
            opts,
            key=(lambda kv: (-get_usage(kv[0]), _lang_label(kv[1]).casefold()))
                if self._sort_freq
                else (lambda kv: _lang_label(kv[1]).casefold()))
        for k, lbl in items:
            cb.addItem(_lang_label(lbl), k)
        if cur:
            idx = cb.findData(cur)
            if idx >= 0:
                cb.setCurrentIndex(idx)
        cb.blockSignals(False)

    def _sort_label_text(self):
        """Mode label shown next to the combo. Matrix fields cycle
        grid → freq → alpha; plain combos cycle freq → alpha."""
        if self.style == "matrix" and getattr(self, "_matrix_mode", "grid") == "grid":
            return _lang_label("grid / 格子")
        return (_lang_label("freq / 頻度") if self._sort_freq
                else _lang_label("alpha / 順序"))

    def _toggle_sort(self):
        # Matrix-style fields cycle through three popup modes:
        #   grid   → linear freq → linear alpha → grid …
        # Plain fields just toggle freq ↔ alpha.
        if self.style == "matrix":
            mode = getattr(self, "_matrix_mode", "grid")
            if mode == "grid":
                self._matrix_mode = "linear"
                self._sort_freq = True
            elif self._sort_freq:
                self._sort_freq = False
            else:
                self._matrix_mode = "grid"
                self._sort_freq = True
        else:
            self._sort_freq = not self._sort_freq
        # Persist the new mode so it survives a restart.
        try:
            save_popup_mode(self.conn, self.key, self._popup_mode_token())
        except Exception:
            pass
        # Update the label only if it exists (main-combo style); sub-
        # combo coded fields don't have one.
        if hasattr(self, "_sort_lbl") and self._sort_lbl is not None:
            self._sort_lbl.setText(self._sort_label_text())
            self._sort_lbl.setStyleSheet(
                "color:#888;font-size:9pt;" if self._sort_freq
                else "color:#8ab;font-size:9pt;font-style:italic;")

    def _popup_mode_token(self):
        """One-word serialization of (matrix_mode, sort_freq) for the
        popup_modes table. 'grid' implies the matrix grid popup; 'freq'
        or 'alpha' means linear popup with that sort order."""
        if self.style == "matrix" and getattr(self, "_matrix_mode", "grid") == "grid":
            return "grid"
        return "freq" if self._sort_freq else "alpha"

    def apply_popup_mode_token(self, token):
        """Restore a saved popup mode token onto this widget."""
        if token == "grid":
            if self.style == "matrix":
                self._matrix_mode = "grid"
            self._sort_freq = True
        elif token == "alpha":
            if self.style == "matrix":
                self._matrix_mode = "linear"
            self._sort_freq = False
        elif token == "freq":
            if self.style == "matrix":
                self._matrix_mode = "linear"
            self._sort_freq = True
        # any unknown token: leave defaults alone
        if self.options:
            self._fill_combo(preserve=True)
        # Refill all coded-field sub-combos for this widget. One toggle
        # per FieldWidget flips every sub-combo (HC_Color + HC_Style +
        # HC_Length share the same sort mode).
        for cb, opts in getattr(self, "_sub_combos", []):
            self._fill_sub_combo(cb, opts)

    # ── Data binding (preview integration) ───────────────────────────────────

    # Cache: option_key (lowercase) → CODED_FIELD letter (lowercase) for digits=0 fields
    @staticmethod
    def _coded_bool_lookup():
        try:
            import aisearch_attrs as _am
            return {lbl.lower(): letter.lower()
                    for letter, lbl, digits in _am.CODED_FIELDS if digits == 0}
        except Exception:
            return {}

    def load_soft(self, tags_set, entry):
        """Populate this widget from the current file's attrs."""
        if self.style in ("taglist", "boolean", "radio"):
            # Dedicated-field radio (e.g. Audio → entry["audio"])
            if self.style == "radio" and self.key in _DEDICATED_FIELD_KEYS:
                _fval = entry.get(_DEDICATED_FIELD_KEYS[self.key], "")
                for k, btn in getattr(self, "_btns", {}).items():
                    btn.blockSignals(True)
                    btn.setChecked(k == _fval)
                    btn.setStyleSheet(_BTN_ON if k == _fval else _BTN_OFF)
                    btn.blockSignals(False)
                return
            # Build coded-boolean map once (label→letter, e.g. "watermark"→"wm")
            _cb_map = FieldWidget._coded_bool_lookup()
            # Parse filename once if any coded booleans exist in this section
            _parsed_coded = None
            _coded_pos_on = set()   # positive coded-bool keys that are ON
            for k, btn in getattr(self, "_btns", {}).items():
                on = k in tags_set
                # If not found in tags, check coded boolean field in filename
                if not on and k in _cb_map:
                    if _parsed_coded is None:
                        try:
                            import aisearch_attrs as _am
                            _path = entry.get("path", "")
                            _stem = os.path.splitext(os.path.basename(_path))[0] if _path else ""
                            _parsed_coded = _am.parse_coded_filename(_stem) or {} if _stem else {}
                        except Exception:
                            _parsed_coded = {}
                    on = bool(_parsed_coded.get(_cb_map[k], ""))
                if on and k in _cb_map:
                    _coded_pos_on.add(k)
                btn.blockSignals(True)
                btn.setChecked(on)
                btn.setStyleSheet(_BTN_ON if on else _BTN_OFF)
                btn.blockSignals(False)
            # Radio coded-boolean: if all positive keys are OFF, activate the
            # complement button (the "False" option) so one button is always selected.
            if self.style == "radio":
                _pos_keys = {k for k in getattr(self, "_btns", {}) if k in _cb_map}
                if _pos_keys and not (_pos_keys & _coded_pos_on):
                    for k, btn in getattr(self, "_btns", {}).items():
                        if k not in _cb_map:
                            btn.blockSignals(True)
                            btn.setChecked(True)
                            btn.setStyleSheet(_BTN_ON)
                            btn.blockSignals(False)
                            break
        elif self.style == "matrix" or (self.options and self.style not in ("text", "1dig", "2dig", "3dig", "4dig", "id")):
            # Matrix value lives at the canonical lowercase letter key for
            # CODED_FIELDS sections (X→x, Tool→t, Background→bg, A→a) and at
            # the section name for non-coded matrix sections (ModelImage,
            # ModelVideo, Variant). Try canonical first, then fall back to
            # the legacy uppercase/section-name key for unmigrated entries.
            try:
                import aisearch_attrs as _am_codedfields
                _section_to_letter = {}
                for _l, _lbl, _d in _am_codedfields.CODED_FIELDS:
                    _section_to_letter[_l] = _l.lower()
                    _section_to_letter[_lbl] = _l.lower()
                _canon = _section_to_letter.get(self.key, self.key)
            except Exception:
                _canon = self.key
            val = entry.get(_canon, "") or entry.get(self.key, "")
            if val and not any(k == val for k, _ in self.options):
                val = ""
            if not val:
                val = next((k for k, _ in self.options if k in tags_set), "")
            # For "combo" coded fields (O/R/K), also check the coded filename then cf_ entry
            if self.style == "combo" and not val:
                _field_key = _SECTION_KEY_TO_FIELD.get(self.key, self.key.lower())
                _path = entry.get("path", "")
                if _path:
                    try:
                        import aisearch_attrs as _am
                        _stem = os.path.splitext(os.path.basename(_path))[0]
                        _parts = _am.parse_coded_filename(_stem)
                        if _parts:
                            _coded_val = _parts.get(_field_key, "")
                            if _coded_val and any(k == _coded_val for k, _ in self.options):
                                val = _coded_val
                    except Exception:
                        pass
                # Fall back to auto-detected value — watch-scan writes to
                # entry[field_key] (e.g. "o"), auto_set_all writes to entry["cf_<key>"]
                if not val:
                    _cf_val = (entry.get(_field_key, "")
                               or entry.get(f"cf_{_field_key}", ""))
                    if _cf_val and any(k == _cf_val for k, _ in self.options):
                        val = _cf_val
            cb = getattr(self, "_cb", None)
            if cb:
                cb.blockSignals(True)
                _idx = cb.findData(val)
                if _idx < 0:
                    # No stored value — prefer a null-equivalent option (e.g. "none"
                    # for audio, "0" for coded) over alphabetically-first AAC etc.
                    for _null_key in ("none", "0", ""):
                        _idx = cb.findData(_null_key)
                        if _idx >= 0:
                            break
                cb.setCurrentIndex(max(0, _idx))
                cb.blockSignals(False)
        elif self.style == "id" and self.key == "J":
            import aisearch_attrs as _am
            lbl = getattr(self, "_date_lbl", None)
            if lbl:
                coded = entry.get("coded", {})
                j_val = coded.get("j", "") if isinstance(coded, dict) else ""
                if not j_val:
                    _path = entry.get("path", "")
                    if _path:
                        j_val = _am.julian_id_for_file(_path)
                decoded = _am.julian_id_to_date(j_val) if j_val else "—"
                lbl.setText(decoded)
                lbl.setToolTip(f"Julian ID: {j_val}")
        elif self.style == "id":
            if self.key == "P":
                _pe = getattr(self, "_pid_edit", None)
                if _pe:
                    import aisearch_attrs as _am
                    val = entry.get("person_id", "")
                    _pe.blockSignals(True)
                    _pe.setText(val or "")
                    _proj = entry.get("_project") or getattr(self, "_project", None)
                    if val:
                        name = _am.get_person_id_label(_proj, val)
                        _pe.setToolTip(name if name and name != val else "")
                    else:
                        _pe.setToolTip("")
                    _pe.blockSignals(False)
                return
            if self.key == "PI":
                # PI (face-swap origin) — only show a value when explicitly
                # set AND it differs from P. Blank = "no swap, same as P"
                # (the placeholder reads "— (same as P)" so this is obvious).
                _pe = getattr(self, "_pid_edit", None)
                if _pe:
                    _pi  = (entry.get("pi") or "").strip().lower()
                    _pid = (entry.get("person_id") or "").strip().lower()
                    val = _pi if _pi and _pi != _pid else ""
                    _pe.blockSignals(True)
                    _pe.setText(val)
                    _pe.blockSignals(False)
                return
            if self.key == "PW":
                # PW (persons_with) — only show when there's real data.
                # Defaulting to P was wrong (PW means companions, defaulting
                # to self is nonsense). Blank = "no other person in frame"
                # (placeholder reads "— (none)").
                _pe = getattr(self, "_pid_edit", None)
                if _pe:
                    pws = [p for p in (entry.get("persons_with") or [])
                           if p and p.strip().lower() != "000"]
                    if not pws:
                        # Filename fallback (legacy PW token in coded filename)
                        import aisearch_attrs as _am
                        _path = entry.get("path", "")
                        _stem = os.path.splitext(os.path.basename(_path))[0] if _path else ""
                        _parsed = _am.parse_coded_filename(_stem) or {} if _stem else {}
                        pws = [p for p in (_parsed.get("persons_with") or [])
                               if p and p.strip().lower() != "000"]
                    val = ", ".join(pws)
                    _pe.blockSignals(True)
                    _pe.setText(val)
                    _pe.blockSignals(False)
                return
            # Fallback for other id keys (none in current FIELD_DEFS)
            lbl = getattr(self, "_id_lbl", None)
            if lbl:
                import aisearch_attrs as _am
                _path = entry.get("path", "")
                _stem = os.path.splitext(os.path.basename(_path))[0] if _path else ""
                _parsed = _am.parse_coded_filename(_stem) or {} if _stem else {}
                if self.key == "PW":
                    pws = _parsed.get("persons_with", [])
                    val = ", ".join(pws) if pws else ""
                else:
                    val = _parsed.get(self.key.lower(), "")
                lbl.setText(val if val else "—")
        elif self.style in ("1dig", "2dig", "3dig", "4dig"):
            # Resolve which key this field is stored under (e.g. section "H" → "hc")
            _field_key = _SECTION_KEY_TO_FIELD.get(self.key, self.key.lower())
            # Prefer attrs_data (manual input), fall back to filename
            val = entry.get(_field_key, "")
            if not val:
                _path = entry.get("path", "")
                if _path:
                    import aisearch_attrs as _am
                    _stem = os.path.splitext(os.path.basename(_path))[0]
                    _parsed = _am.parse_coded_filename(_stem) or {}
                    val = _parsed.get(_field_key, "")
            combos = getattr(self, "_coded_combos", [])
            hex_edit = getattr(self, "_hex_edit", None)
            if combos and val:
                # Each combo carries its own digit position (third tuple item).
                # pos=1 = rightmost digit, pos=N = leftmost. Padding uses the
                # max position seen so short values (e.g. "5" for HC) align.
                _max_pos = max((t[2] if len(t) >= 3 else (i + 1)
                                for i, t in enumerate(combos)), default=1)
                val_padded = val.zfill(_max_pos)
                for i, t in enumerate(combos):
                    cb = t[1]
                    pos = t[2] if len(t) >= 3 else (i + 1)
                    digit = val_padded[-pos] if len(val_padded) >= pos else ""
                    cb.blockSignals(True)
                    cb.setCurrentIndex(max(0, cb.findData(digit)) if digit else 0)
                    cb.blockSignals(False)
            elif hex_edit is not None:
                hex_edit.blockSignals(True)
                hex_edit.setText(val)
                hex_edit.blockSignals(False)
        elif self.style == "text":
            db_key = _TEXT_KEY_MAP.get(self.key, self.key)
            text = entry.get(db_key, "")
            # Cap CLIP/FACE/CLIP_*/FACE_PW debug dumps at 8KB. Old saves before
            # the cap was added carry 25k+ char strings that trigger
            # QTextCursor::setPosition out-of-range warnings on every reload.
            if isinstance(text, str) and len(text) > 8192 and (
                    self.key in ("CLIP", "FACE", "FACE_PW")
                    or self.key.startswith("CLIP_")):
                text = text[:8192] + "\n" + _lang_label("…(truncated) / …（切り捨て）")
            te = getattr(self, "_te", None)
            if te:
                te.blockSignals(True)
                te.setPlainText(text)
                te.blockSignals(False)
        elif self.style == "pathlist":
            db_key = _TEXT_KEY_MAP.get(self.key, self.key)
            paths = entry.get(db_key, []) or []
            pl = getattr(self, "_pathlist", None)
            if pl is not None:
                pl.blockSignals(True)
                pl.clear()
                for p in paths:
                    if isinstance(p, str) and p.strip():
                        pl.addItem(p)
                pl.blockSignals(False)
            # Remember the current file's path so the file/folder picker
            # opens next to it instead of home.
            self._cur_file_path = entry.get("path", "") or ""

    def collect_soft(self):
        """Return current widget value for saving back to attrs.
        Returns one of:
          ("tags", set_of_keys) — for taglist/boolean
          ("tag",  key_str)     — for matrix (single selection)
          ("text", db_key, str) — for text
          None                  — for coded/id styles (handled elsewhere)
        """
        if self.style == "radio":
            checked = next((k for k, btn in getattr(self, "_btns", {}).items() if btn.isChecked()), "")
            if self.key in _DEDICATED_FIELD_KEYS:
                # Store in dedicated entry field, not tags
                return ("text", _DEDICATED_FIELD_KEYS[self.key], checked)
            return ("tag", checked)
        elif self.style in ("taglist", "boolean"):
            return ("tags", {k for k, btn in getattr(self, "_btns", {}).items() if btn.isChecked()})
        elif self.style in ("1dig", "2dig", "3dig", "4dig"):
            combos = getattr(self, "_coded_combos", [])
            hex_edit = getattr(self, "_hex_edit", None)
            field_key = _SECTION_KEY_TO_FIELD.get(self.key, self.key.lower())
            if combos:
                any_set = any(t[1].currentData() for t in combos)
                if any_set:
                    # Build value indexed by each combo's explicit pos so
                    # display order doesn't have to match digit order.
                    _max_pos = max((t[2] if len(t) >= 3 else (i + 1)
                                    for i, t in enumerate(combos)), default=1)
                    digits = ["0"] * _max_pos
                    for i, t in enumerate(combos):
                        pos = t[2] if len(t) >= 3 else (i + 1)
                        # digits[0] is leftmost (highest pos), digits[-1] is rightmost (pos 1)
                        digits[_max_pos - pos] = t[1].currentData() or "0"
                    val = "".join(digits)
                else:
                    val = ""
            elif hex_edit is not None:
                val = hex_edit.text().strip()
            else:
                return None
            return ("coded", field_key, val)
        elif self.style == "combo" and self.options:
            # Coded combo (O/R/K) — value lives in the filename, not tags
            cb = getattr(self, "_cb", None)
            val = cb.currentData() or "" if cb else ""
            field_key = _SECTION_KEY_TO_FIELD.get(self.key, self.key.lower())
            return ("coded", field_key, val)
        elif self.style == "matrix" or (self.options and self.style not in ("text", "1dig", "2dig", "3dig", "4dig", "id", "radio")):
            cb = getattr(self, "_cb", None)
            # Matrix selections write to entry[field_key]. self.key is
            # already the canonical CLIP/storage key (BG, X, …) because
            # the project tag file uses the same names everywhere.
            field_key = _SECTION_KEY_TO_FIELD.get(self.key, self.key.lower())
            return ("matrix_field", field_key, cb.currentData() or "" if cb else "")
        elif self.style == "id" and self.key == "P":
            _pe = getattr(self, "_pid_edit", None)
            return ("text", "person_id", _pe.text().strip() if _pe else "")
        elif self.style == "id" and self.key == "PI":
            _pe = getattr(self, "_pid_edit", None)
            return ("text", "pi", _pe.text().strip() if _pe else "")
        elif self.style == "id" and self.key == "PW":
            _pe = getattr(self, "_pid_edit", None)
            return ("text", "pw", _pe.text().strip() if _pe else "")
        elif self.style == "text":
            db_key = _TEXT_KEY_MAP.get(self.key, self.key)
            te = getattr(self, "_te", None)
            return ("text", db_key, te.toPlainText() if te else "")
        elif self.style == "pathlist":
            db_key = _TEXT_KEY_MAP.get(self.key, self.key)
            pl = getattr(self, "_pathlist", None)
            paths = []
            if pl is not None:
                seen = set()
                for i in range(pl.count()):
                    t = pl.item(i).text().strip()
                    if t and t not in seen:
                        seen.add(t)
                        paths.append(t)
            return ("pathlist", db_key, paths)
        return None

    def set_edit_mode(self, on: bool):
        """Set edit mode — controls dots/resize availability (fields always interactive)."""
        self.edit_mode = on

    # ── Color ─────────────────────────────────────────────────────────────────

    def _apply_color(self, hex_color):
        self._bg_color = hex_color
        # Darken border slightly relative to background
        c = QColor(hex_color)
        border = QColor(max(0, c.red()-40), max(0, c.green()-40), max(0, c.blue()-40))
        if getattr(self, "_selected", False):
            border_color, border_width = "#4a9eff", 3   # blue highlight on select
        else:
            border_color, border_width = border.name(), 1
        self.setStyleSheet(
            f"QGroupBox{{background:{hex_color};border:{border_width}px solid {border_color};"
            "border-radius:2px;margin-top:0px;padding-top:16px;"
            "color:#fff;font-size:9pt;font-weight:bold;}"
            "QGroupBox::title{subcontrol-origin:padding;subcontrol-position:top left;"
            "top:2px;left:6px;padding:0 2px;}")

    def set_selected(self, on: bool):
        """Toggle selection state + blue border indicator."""
        self._selected = bool(on)
        self._apply_color(self._bg_color)

    def _pick_color(self, _pos=None):
        grp_label = f"group: {self._group}" if self._group else self.key
        _init_hex = self._bg_color if self._bg_color != "transparent" else "#222222"
        initial = QColor(_init_hex)
        color = QColorDialog.getColor(initial, self, f"Color for {grp_label}")
        if color.isValid():
            hex_c = color.name()
            for peer in self._group_peers:
                peer._apply_color(hex_c)
            if self._group:
                save_group_color(self.conn, self._group, hex_c)

    def _toggle_hidden_for(self, mode):
        """Add or remove a mode from this panel's hidden_for list and save to JSON."""
        m = mode.lower()
        if m in [x.lower() for x in self._hidden_for]:
            self._hidden_for = [x for x in self._hidden_for if x.lower() != m]
        else:
            self._hidden_for.append(m)
        # Save back to the config JSON
        if self._cfg_path:
            try:
                with open(self._cfg_path, encoding="utf-8") as f:
                    data = json.load(f)
                hf = data.get("__hidden_for__", {})
                if self._hidden_for:
                    hf[self.key] = self._hidden_for
                elif self.key in hf:
                    del hf[self.key]
                data["__hidden_for__"] = hf
                with open(self._cfg_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

    # ── Connection group helpers ──────────────────────────────────────────────

    def _connected_group_keys(self, cv):
        """Return set of all box keys in the same connected group as self (BFS)."""
        visited = {self.key}
        queue   = [self.key]
        while queue:
            k = queue.pop(0)
            for _, ba, pa, bb, pb in cv._connections:
                if ba == k and bb not in visited:
                    visited.add(bb); queue.append(bb)
                elif bb == k and ba not in visited:
                    visited.add(ba); queue.append(ba)
        return visited

    # ── Drag ──────────────────────────────────────────────────────────────────

    _GRIP = 8   # px edge thickness that activates resize

    def _resize_mode(self, pos):
        """Returns ('both'|'h'|'v'|None) depending on where pos is."""
        if self._collapsed:
            return None  # collapsed panels expand on click, never resize
        g = self._GRIP
        on_r = pos.x() >= self.width()  - g
        on_b = pos.y() >= self.height() - g
        if on_r and on_b: return "both"
        if on_r:          return "h"
        if on_b:          return "v"
        return None

    def _in_title(self, pos):
        return pos.y() < (self.fontMetrics().height() + 16)

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        pos = e.position().toPoint()
        mode = self._resize_mode(pos) if self.edit_mode else None
        if mode or self.drag_mode:
            cv = getattr(self, "_canvas_ref", None)
            viewer = getattr(cv, "_viewer", None)
            if viewer:
                viewer._push_undo()
        if mode:
            self._resize_pos = e.globalPosition().toPoint()
            self._resize_start_size = self.size()
            self._resize_dir = mode
            self.raise_()
        elif self.drag_mode:
            self._drag_pos = pos
            self.raise_()
        elif self._in_title(pos) and self._collapsible:
            self._toggle_collapse()

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        if self._resize_pos is not None:
            delta = e.globalPosition().toPoint() - self._resize_pos
            w = self._resize_start_size.width()
            h = self._resize_start_size.height()
            _min_h = 60
            _min_w = 150
            if self._resize_dir in ("h", "both"):
                w = max(_min_w, w + delta.x())
            if self._resize_dir in ("v", "both"):
                h = max(_min_h, h + delta.y())
            self.resize(int(w), int(h))
        elif self.drag_mode and self._drag_pos:
            new_pos = self.mapToParent(pos - self._drag_pos)
            if self._snap:
                G = 20
                new_pos.setX(round(new_pos.x() / G) * G)
                new_pos.setY(round(new_pos.y() / G) * G)
            old_x, old_y = self.x(), self.y()
            self.move(new_pos)
            dx = self.x() - old_x
            dy = self.y() - old_y
            if dx or dy:
                cv = getattr(self, "_canvas_ref", None)
                if cv:
                    wmap = {w.key: w for w in cv.widgets}
                    peers = set()
                    # Move connected-group peers with us (existing behavior)
                    if cv._connections:
                        peers.update(self._connected_group_keys(cv))
                    # Move all Ctrl+A-selected peers with us (only when self is selected)
                    if self._selected:
                        peers.update(w.key for w in cv.widgets if w._selected)
                    # Suppress moveEvent → moved → snap cascade on every peer so
                    # connected children that are ALSO in the selection don't get
                    # moved twice (once by this loop, once by the cascade).
                    to_move = [(wmap[k], wmap[k].x() + dx, wmap[k].y() + dy)
                               for k in peers if k != self.key and k in wmap]
                    for w, _nx, _ny in to_move:
                        w._snapping = True
                    try:
                        for w, nx, ny in to_move:
                            w.move(nx, ny)
                    finally:
                        for w, _nx, _ny in to_move:
                            w._snapping = False
        # Cursor
        mode = self._resize_mode(pos)
        if mode == "both": self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif mode == "h":  self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif mode == "v":  self.setCursor(Qt.CursorShape.SizeVerCursor)
        elif self.drag_mode: self.setCursor(Qt.CursorShape.SizeAllCursor)
        else: self.unsetCursor()

    def mouseReleaseEvent(self, e):
        # Always clear state first, then do side effects — prevents stuck-on-mouse
        resize_was_active = self._resize_pos is not None
        drag_was_active   = self._drag_pos is not None
        w, h = self.width(), self.height()
        x, y = self.x(), self.y()

        self._resize_pos        = None
        self._resize_start_size = None
        self._resize_dir        = None
        self._drag_pos          = None

        if resize_was_active:
            # Always re-emit resized — even a bare click on the resize edge
            # is treated as a "re-snap connected chain" gesture, which the user
            # relies on to flush misaligned tiles back into place.
            try:
                save_size(self.conn, self.key, w, h)
                self.resized.emit(self.key)
            except Exception:
                pass
        elif drag_was_active:
            try:
                self.moved.emit(self.key, x, y)
                # Save positions of all connected group members that moved with us
                cv = getattr(self, "_canvas_ref", None)
                if cv and cv._connections:
                    wmap = {w.key: w for w in cv.widgets}
                    for k in self._connected_group_keys(cv):
                        if k != self.key:
                            w = wmap.get(k)
                            if w:
                                save_position(self.conn, k, w.x(), w.y())
            except Exception:
                pass

    def resizeEvent(self, e):
        """Emit resized on every geometry change (not just user drag) so
        connected child tiles re-snap when Qt's layout auto-resizes us
        (e.g. when a text box grows to fit longer detection output)."""
        super().resizeEvent(e)
        # Pin the floating menu button to the top-right corner.
        btn = getattr(self, "_menu_btn", None)
        if btn is not None:
            btn.move(self.width() - btn.width() - 4, 2)
            btn.raise_()
        try:
            self.resized.emit(self.key)
        except Exception:
            pass

    def _on_menu_btn_click(self):
        """Left-click on the ≡ button opens the field menu just below it."""
        btn = self._menu_btn
        self._open_field_menu(btn.mapToGlobal(QPoint(0, btn.height())))

    def moveEvent(self, e):
        """Emit moved on every position change — catches programmatic .move()
        calls from _snap_child, so multi-level chains (A→B→C→D) fully cascade
        when an ancestor moves. The `_snapping` flag avoids re-entrant loops."""
        super().moveEvent(e)
        if getattr(self, "_snapping", False):
            return
        try:
            self.moved.emit(self.key, self.x(), self.y())
        except Exception:
            pass

    def contextMenuEvent(self, e):
        self._open_field_menu(e.globalPos())

    def _open_field_menu(self, global_pos):
        """Build and exec the per-widget menu at `global_pos`. Called from
        both right-click (contextMenuEvent) and left-click on the title
        bar — users asked for left-click access to Show / Update."""
        # Clear any stuck drag/resize state
        self._resize_pos = self._resize_start_size = self._resize_dir = self._drag_pos = None

        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#2a2a2a;color:#fff;border:1px solid #555;}"
            "QMenu::item:selected{background:#4a7a4e;}"
            "QMenu::separator{background:#555;height:1px;margin:2px 0;}")

        # "Show" — reveal the debug tile without re-running detection
        # "Update" — re-detect CLIP for this single field + reveal its debug tile
        # P and PW share the face-detection pipeline — Update on either runs
        # the same detection (P = primary face, PW = secondary faces). PI is
        # provenance-only and stays manual; assigned via the 👤 button →
        # Settings → Persons card buttons.
        # Show / Update show for any widget whose key resolves to a
        # CLIP/face field — direct keys (E, HC, …) AND human-label
        # sections like "Background" → "bg".
        _CLIP_TARGETS = {"e", "hc", "fa", "sk", "pm", "cs", "bg", "x", "cl",
                         "p", "pw"}
        _resolved = _SECTION_KEY_TO_FIELD.get(self.key, self.key.lower())
        act_show = act_update = None
        if _resolved in _CLIP_TARGETS:
            act_show   = menu.addAction(_lang_label("👁 Show / 👁 表示"))
            act_update = menu.addAction(_lang_label("🔄 Update / 🔄 更新"))

        # Everything below requires Editable (canvas edit_mode) to be on
        act_color = act_disc_this = act_disc_box = act_disc_all = act_cond = None
        mode_actions = {}
        cv = getattr(self, "_canvas_ref", None)
        my_conns = []
        if self.edit_mode:
            if act_update:
                menu.addSeparator()

            act_color = menu.addAction(_lang_label("🎨 Change Color… / 🎨 色を変更…"))
            menu.addSeparator()

            # ── Disconnect options ────────────────────────────────────────
            if cv:
                my_conns = [r for r in cv._connections if r[1] == self.key or r[3] == self.key]
                all_conns = cv._connections
                if my_conns:
                    act_disc_this = menu.addAction(_lang_label("Disconnect this dot / このドットを切断"))
                    act_disc_box  = menu.addAction(_lang_label("Disconnect all on this box / このボックスのすべてを切断"))
                if all_conns:
                    act_disc_all = menu.addAction(_lang_label("Disconnect all / すべて切断"))
                if my_conns or all_conns:
                    menu.addSeparator()

            # Hide for … checkable actions
            modes = [(_lang_label("Image / 画像"), "Image"), (_lang_label("Video / 動画"), "Video")]
            hidden_for = self._hidden_for or []
            for lbl, m in modes:
                a = QAction(_lang_label(f"Hide for {m} / {lbl}で隠す"), menu, checkable=True)
                a.setChecked(m.lower() in [x.lower() for x in hidden_for])
                menu.addAction(a)
                mode_actions[m] = a

            menu.addSeparator()
            cond_label = _lang_label(f"Hide when… ({len(self._conditions)}) / 非表示条件… ({len(self._conditions)})") if self._conditions else _lang_label("Hide when… / 非表示条件…")
            act_cond = menu.addAction(cond_label)

        # Nothing to show — skip the empty menu popup
        if menu.isEmpty():
            return

        chosen = menu.exec(global_pos)
        if chosen is None:
            return   # user dismissed menu without selecting
        if chosen == act_show:
            self.action_triggered.emit(self.key, "show_clip")
        elif chosen == act_update:
            self.action_triggered.emit(self.key, "update_clip")
        elif chosen == act_color:
            self._pick_color()
        elif chosen == act_disc_this and cv and my_conns:
            cv._remove_connection(my_conns[0][0])   # single — lets _remove_connection push
        elif chosen == act_disc_box and cv:
            viewer = getattr(cv, "_viewer", None)
            if viewer: viewer._push_undo()           # one snapshot for whole batch
            for row in list(my_conns):
                cv._remove_connection(row[0], _push=False)
        elif chosen == act_disc_all and cv:
            viewer = getattr(cv, "_viewer", None)
            if viewer: viewer._push_undo()           # one snapshot for whole batch
            for row in list(cv._connections):
                cv._remove_connection(row[0], _push=False)
        elif chosen == act_cond:
            self._open_conditions_dialog()
        else:
            for m, a in mode_actions.items():
                if chosen == a:
                    self._toggle_hidden_for(m)
                    break

    def _open_conditions_dialog(self):
        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                                     QScrollArea, QDialogButtonBox)
        dlg = QDialog(self)
        dlg.setWindowTitle(_lang_label(f"Hide conditions — {self.key} / 非表示条件 — {self.key}"))
        dlg.setMinimumWidth(520)
        dlg.setStyleSheet("background:#1e1e1e; color:#ddd;")

        vbox = QVBoxLayout(dlg)
        vbox.setSpacing(6)

        info = QLabel(_lang_label(
            "This panel is hidden when ANY condition below is satisfied.\n"
            "Source key = raw metadata key (same as MetaMap source keys). / "
            "以下のいずれかの条件が満たされた場合、このパネルは非表示になります。\n"
            "ソースキー = 生のメタデータキー（メタマップのソースキーと同じ）"))
        info.setStyleSheet("color:#999; font-size:9pt;")
        info.setWordWrap(True)
        vbox.addWidget(info)

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;}")
        rows_w = QWidget(); rows_l = QVBoxLayout(rows_w)
        rows_l.setSpacing(4); rows_l.setContentsMargins(0,0,0,0)
        rows_l.addStretch()
        scroll.setWidget(rows_w)
        vbox.addWidget(scroll, stretch=1)

        _OPS = [("equals", _lang_label("equals / 等しい")), ("not_eq", _lang_label("not equals / 等しくない")),
                ("contains", _lang_label("contains / 含む")), ("empty", _lang_label("is empty / no / none / 空・なし")),
                ("not_empty", _lang_label("is not empty / 空でない"))]
        row_data = []   # list of (src_edit, op_cb, val_edit)

        def _add_row(src="", op="empty", val=""):
            rw = QWidget()
            rl = QHBoxLayout(rw); rl.setContentsMargins(0,0,0,0); rl.setSpacing(4)
            src_e = QLineEdit(src)
            src_e.setPlaceholderText(_lang_label("Source key… / ソースキー…"))
            src_e.setStyleSheet("background:#2a2a2a;color:#f0f0f0;border:1px solid #666;padding:3px 5px;")
            rl.addWidget(src_e, stretch=3)
            op_cb = QComboBox()
            op_cb.wheelEvent = lambda ev: ev.ignore()
            op_cb.setStyleSheet("background:#2a2a2a;color:#f0f0f0;border:1px solid #666;")
            for op_id, op_lbl in _OPS:
                op_cb.addItem(op_lbl, op_id)
            idx = op_cb.findData(op)
            if idx >= 0: op_cb.setCurrentIndex(idx)
            rl.addWidget(op_cb, stretch=3)
            val_e = QLineEdit(val)
            val_e.setPlaceholderText(_lang_label("value… / 値…"))
            val_e.setStyleSheet("background:#2a2a2a;color:#f0f0f0;border:1px solid #666;padding:3px 5px;")
            def _sync_val(cur_op):
                needs_val = op_cb.currentData() not in ("empty", "not_empty")
                val_e.setVisible(needs_val)
            op_cb.currentIndexChanged.connect(lambda _: _sync_val(op_cb.currentData()))
            _sync_val(op)
            rl.addWidget(val_e, stretch=3)
            btn_x = QPushButton("✕"); btn_x.setFixedSize(22,22)
            btn_x.setStyleSheet("background:#552222;color:#ffaaaa;border:none;border-radius:3px;")
            def _remove(rw=rw, entry=None):
                entry and row_data.remove(entry)
                rw.deleteLater()
            entry = (src_e, op_cb, val_e)
            btn_x.clicked.connect(lambda: (row_data.remove(entry), rw.deleteLater()))
            rl.addWidget(btn_x)
            insert_at = rows_l.count() - 1
            rows_l.insertWidget(insert_at, rw)
            row_data.append(entry)

        for c in self._conditions:
            _add_row(c.get("source",""), c.get("op","empty"), c.get("value",""))

        btn_add = QPushButton("+ Add Condition")
        btn_add.setStyleSheet("background:#1a5a1a;color:#fff;font-weight:bold;padding:4px 10px;")
        btn_add.clicked.connect(lambda: _add_row())
        vbox.addWidget(btn_add)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.setStyleSheet("QPushButton{background:#333;color:#ddd;padding:4px 12px;border:1px solid #555;}")
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        vbox.addWidget(btns)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        self._conditions = []
        for src_e, op_cb, val_e in row_data:
            src = src_e.text().strip()
            op  = op_cb.currentData()
            val = val_e.text().strip()
            if src:
                self._conditions.append({"source": src, "op": op, "value": val})
        self._save_conditions()

    def _save_conditions(self):
        """Persist __conditions__ for this field back to the config JSON."""
        if not self._cfg_path:
            return
        try:
            with open(self._cfg_path, encoding="utf-8") as f:
                data = json.load(f)
            conds = data.get("__conditions__", {})
            if self._conditions:
                conds[self.key] = self._conditions
            elif self.key in conds:
                del conds[self.key]
            data["__conditions__"] = conds
            with open(self._cfg_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


# ── Anchor canvas ─────────────────────────────────────────────────────────────

class _AnchorCanvas(QWidget):
    """Canvas that draws corner dots + connection lines.

    Dots protrude OUTSIDE each box corner so they're always visible and
    clickable (they're in the canvas area, not covered by child widgets).

    Click-click workflow:
      1. Click a corner dot → turns yellow (pending)
      2. Click a corner dot on another box → connection created, second box snaps
      3. Right-click anywhere → cancel pending
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._widgets:     list         = []
        self._connections: list         = []   # [(id, box_a, port_a, box_b, port_b)]
        self._pending:     tuple | None = None  # (key, port_name)
        self._edit_on:     bool         = True  # mirrors Edit checkbox state

    # ── Hit test ──────────────────────────────────────────────────────────────

    def _find_port(self, pos):
        """Return (widget, port_name) if pos is within _DOT_HIT of a corner dot."""
        best_w, best_p, best_d2 = None, None, (_DOT_HIT + 1) ** 2
        for w in self._widgets:
            if not w.isVisible():
                continue
            for port in PORT_NAMES:
                pp = _port_pos(w, port)
                d2 = (pp.x() - pos.x()) ** 2 + (pp.y() - pos.y()) ** 2
                if d2 < best_d2:
                    best_d2 = d2
                    best_w, best_p = w, port
        return (best_w, best_p) if best_w else None

    def _connected_ports(self):
        s = set()
        for _, ba, pa, bb, pb in self._connections:
            s.add((ba, pa)); s.add((bb, pb))
        return s

    # ── Paint ─────────────────────────────────────────────────────────────────

    def paintEvent(self, e):
        super().paintEvent(e)
        wmap      = {w.key: w for w in self._widgets}
        connected = self._connected_ports()

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Connection lines
        for _, ba, pa, bb, pb in self._connections:
            wa = wmap.get(ba); wb = wmap.get(bb)
            if not (wa and wb):
                continue
            p.setPen(QPen(QColor("#5aaa5e"), 1, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawLine(_port_pos(wa, pa), _port_pos(wb, pb))

        # Corner dots — visible only when edit mode is on
        drag_on = any(getattr(w, "drag_mode", False) for w in self._widgets)
        if self._edit_on:
            r = _DOT_R
            for w in self._widgets:
                if not w.isVisible():
                    continue
                for port in PORT_NAMES:
                    pt = _port_pos(w, port)
                    is_pending = self._pending == (w.key, port)
                    is_conn    = (w.key, port) in connected
                    color = (QColor("#ffdd44") if is_pending else
                             QColor("#5aaa5e") if is_conn    else
                             QColor("#3a5a8a"))
                    p.setPen(QPen(QColor("#bbb"), 1))
                    p.setBrush(QBrush(color))
                    p.drawEllipse(pt.x() - r, pt.y() - r, r * 2, r * 2)

        # Dotted line following cursor while pending
        if self._pending and self._edit_on:
            src_w = wmap.get(self._pending[0])
            if src_w:
                from PyQt6.QtGui import QCursor
                mouse = self.mapFromGlobal(QCursor.pos())
                p.setPen(QPen(QColor("#ffdd44"), 1, Qt.PenStyle.DotLine))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawLine(_port_pos(src_w, self._pending[1]), mouse)
                QTimer.singleShot(30, self.update)

        p.end()

    # ── Mouse events — canvas receives clicks on the dot areas ────────────────

    def _dist_to_line(self, px, py, ax, ay, bx, by):
        """Distance from point (px,py) to line segment (ax,ay)-(bx,by)."""
        dx, dy = bx - ax, by - ay
        if dx == 0 and dy == 0:
            return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
        nx, ny = ax + t * dx, ay + t * dy
        return ((px - nx) ** 2 + (py - ny) ** 2) ** 0.5

    def _hit_connection(self, pos, threshold=8):
        """Return connection id of the line closest to pos, or None."""
        wmap = {w.key: w for w in self._widgets}
        best_cid, best_d = None, threshold + 1
        for cid, ba, pa, bb, pb in self._connections:
            wa = wmap.get(ba); wb = wmap.get(bb)
            if not (wa and wb):
                continue
            a = _port_pos(wa, pa); b = _port_pos(wb, pb)
            d = self._dist_to_line(pos.x(), pos.y(), a.x(), a.y(), b.x(), b.y())
            if d < best_d:
                best_d = d; best_cid = cid
        return best_cid

    def _hit_dot(self, pos):
        """Return (box_key, port) if pos is near a dot, else None."""
        result = self._find_port(pos)
        return result  # (widget, port) or None

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            # Left-click on empty canvas (not on a tile) → deselect all.
            # Tiles intercept clicks within their own geometry, so reaching
            # the canvas means the click landed on empty space.
            viewer = getattr(self, "_viewer", None)
            if viewer and any(w._selected for w in getattr(viewer, "widgets", [])):
                viewer._deselect_all()
            # Fall through so existing port/connection click logic still runs.
        if e.button() == Qt.MouseButton.RightButton:
            if self._pending:
                self._pending = None
                self.update()
                e.accept()
                return
            pos = e.position().toPoint()
            cv  = getattr(self, "_viewer", None)
            if not cv or not self._connections:
                e.accept()
                return

            # Find what was right-clicked: a dot or a line
            dot_hit  = self._find_port(pos)           # (widget, port) or None
            line_cid = self._hit_connection(pos)       # connection id or None

            # Determine the "this connection" and "this box" from context
            this_cid  = None
            this_box  = None
            if dot_hit:
                w, port = dot_hit
                this_box = w.key
                # Find the connection on this specific dot
                for cid, ba, pa, bb, pb in self._connections:
                    if (ba == this_box and pa == port) or (bb == this_box and pb == port):
                        this_cid = cid
                        break
            elif line_cid is not None:
                this_cid = line_cid
                # Box = the first endpoint of this connection
                for cid, ba, pa, bb, pb in self._connections:
                    if cid == line_cid:
                        this_box = ba
                        break

            menu = QMenu(self)
            menu.setStyleSheet(
                "QMenu{background:#2a2a2a;color:#fff;border:1px solid #555;}"
                "QMenu::item:selected{background:#7a3a3a;}"
                "QMenu::separator{background:#555;height:1px;margin:2px 0;}")

            act_this = act_box = act_all = None
            if this_cid is not None:
                act_this = menu.addAction(_lang_label("Disconnect this / これを切断"))
            if this_box is not None:
                box_conns = [r for r in self._connections if r[1] == this_box or r[3] == this_box]
                if len(box_conns) > 1 or (len(box_conns) == 1 and this_cid is None):
                    act_box = menu.addAction(_lang_label("Disconnect all on this box / このボックスのすべてを切断"))
            if len(self._connections) > 0:
                menu.addSeparator()
                act_all = menu.addAction(_lang_label("Disconnect all / すべて切断"))

            chosen = menu.exec(e.globalPosition().toPoint())
            if chosen is None:
                e.accept()
                return
            if chosen == act_this and this_cid is not None:
                cv._remove_connection(this_cid)
            elif chosen == act_box and this_box is not None:
                for row in list(self._connections):
                    if row[1] == this_box or row[3] == this_box:
                        cv._remove_connection(row[0])
            elif chosen == act_all:
                for row in list(self._connections):
                    cv._remove_connection(row[0])
            e.accept()
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        # Port clicks only when edit mode is on
        if not self._edit_on:
            return
        pos = e.position().toPoint()
        hit = self._find_port(pos)
        if not hit:
            return
        w, port = hit
        cv = getattr(self, "_viewer", None)
        if cv:
            cv._on_connect_started(w.key, port)
        e.accept()


# ── Embeddable widget (can live inside a tab or a standalone window) ──────────

class AttrViewerWidget(QWidget):
    """Canvas + toolbar — embeddable anywhere (Settings tab, preview panel…)."""
    data_changed     = pyqtSignal()        # emitted when any soft field value changes
    action_triggered = pyqtSignal(str, str)  # (key, action_name) — from FieldWidget buttons

    def __init__(self, config_path=CONFIG_FILE, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background:#1a1a1a; color:#ddd;")

        _db_path = _db_file_for_config(config_path)
        if _db_path != DB_FILE and not os.path.exists(_db_path):
            _seed_project_db(config_path, _db_path)
        self.conn = sqlite3.connect(_db_path)
        init_db(self.conn)
        load_usage(self.conn)

        self.cfg      = load_config(config_path)
        self.cfg_path = config_path
        self.widgets      = []
        self._connections = []   # [(id, box_a, port_a, box_b, port_b)]

        main = QVBoxLayout(self)
        main.setContentsMargins(8, 8, 8, 8)
        main.setSpacing(6)

        # ── Toolbar ───────────────────────────────────────────────────────────
        bar = QHBoxLayout()
        title = QLabel(os.path.basename(config_path))
        title.setStyleSheet("color:#888;font-size:8pt;")
        bar.addWidget(title)
        bar.addStretch()

        bar.addWidget(QLabel("Mode:", styleSheet="color:#888;font-size:8pt;"))
        self._mode_cb = QComboBox()
        self._mode_cb.addItems(["All", "Image", "Video"])
        self._mode_cb.setStyleSheet(
            "QComboBox{background:#2e2e2e;color:#fff;border:1px solid #555;"
            "padding:2px 8px;border-radius:3px;font-size:9pt;}"
            "QComboBox::drop-down{border:none;}"
            "QComboBox QAbstractItemView{background:#2e2e2e;color:#fff;"
            "selection-background-color:#4a7a4e;}")
        self._mode_cb.setFixedWidth(80)
        self._mode_cb.currentTextChanged.connect(self._apply_mode)
        bar.addWidget(self._mode_cb)

        self._snap_cb = QCheckBox("Editable")
        self._snap_cb.setStyleSheet("color:#ccc;")
        self._snap_cb.setToolTip("Editable mode: show dots, connect boxes")
        self._snap_cb.setChecked(False)   # off by default
        bar.addWidget(self._snap_cb)

        self._drag_cb = QCheckBox("Drag Mode")
        self._drag_cb.setStyleSheet("color:#ccc;")
        self._drag_cb.stateChanged.connect(self._set_drag)
        self._snap_cb.stateChanged.connect(self._set_snap)
        bar.addWidget(self._drag_cb)

        btn_auto_grid = QPushButton("▦ Auto Grid")
        btn_auto_grid.setToolTip("Arrange tiles in a clean group-separated grid")
        btn_auto_grid.setStyleSheet(
            "QPushButton{background:#383838;color:#ccc;border:1px solid #555;"
            "border-radius:3px;padding:2px 8px;font-size:8pt;}"
            "QPushButton:hover{background:#4a4a4a;}")
        btn_auto_grid.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_auto_grid.clicked.connect(self._auto_grid_layout)
        bar.addWidget(btn_auto_grid)
        self._toolbar_widget = QWidget()
        self._toolbar_widget.setLayout(bar)
        main.addWidget(self._toolbar_widget)

        # ── Canvas (free-position child widgets) ──────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(False)
        scroll.setStyleSheet("QScrollArea{border:none;background:#222;}")
        self.canvas = _AnchorCanvas()
        self.canvas._viewer = self
        self.canvas.setMinimumSize(1400, 1000)
        self.canvas.setStyleSheet("background:#222;")
        scroll.setWidget(self.canvas)
        main.addWidget(scroll)

        self._undo_stack = []

        self._build(load_positions(self.conn), load_group_colors(self.conn),
                    load_sizes(self.conn), load_collapsed(self.conn))
        # Apply initial edit state from the checkbox (default OFF)
        self._set_snap(self._snap_cb.isChecked())

        from PyQt6.QtGui import QShortcut, QKeySequence
        _undo_sc = QShortcut(QKeySequence("Ctrl+Z"), self)
        _undo_sc.activated.connect(self._do_undo)
        _selall_sc = QShortcut(QKeySequence("Ctrl+A"), self)
        _selall_sc.activated.connect(self._select_all)
        _desel_sc = QShortcut(QKeySequence("Esc"), self)
        _desel_sc.activated.connect(self._deselect_all)

    def _select_all(self):
        """Mark every visible tile as selected (Ctrl+A) so dragging any one of
        them moves the whole group together."""
        for w in self.widgets:
            if w.isVisible():
                w.set_selected(True)

    def _deselect_all(self):
        """Clear all selections (Esc)."""
        for w in self.widgets:
            if w._selected:
                w.set_selected(False)

    def reload(self, config_path):
        """Destroy current panels and rebuild from a new config file."""
        for w in self.widgets:
            w.setParent(None)
            w.deleteLater()
        self.widgets.clear()
        self.cfg      = load_config(config_path)
        self.cfg_path = config_path
        self._connections = []
        self._undo_stack.clear()
        # Switch to per-project DB
        self.conn.close()
        _db_path = _db_file_for_config(config_path)
        if _db_path != DB_FILE and not os.path.exists(_db_path):
            _seed_project_db(config_path, _db_path)
        self.conn = sqlite3.connect(_db_path)
        init_db(self.conn)
        load_usage(self.conn)
        self._build(load_positions(self.conn), load_group_colors(self.conn),
                    load_sizes(self.conn), load_collapsed(self.conn))
        # Re-apply current mode and edit state after rebuild
        self._apply_mode(self._mode_cb.currentText())
        self._set_snap(self._snap_cb.isChecked())

    def _build(self, positions, group_colors=None, sizes=None, collapsed_state=None):
        cfg         = self.cfg
        sec_order   = list(cfg.get("__section_order__", [k for k in cfg if not k.startswith("__")]))
        sec_styles  = dict(cfg.get("__section_styles__", {}))
        text_fields = cfg.get("__text_fields__", {})
        sec_groups   = cfg.get("__section_groups__", {})
        col_names    = cfg.get("__col_names__", {})
        parent_names = cfg.get("__parent_names__", {})
        # Per-field popup mode (grid / freq / alpha) — restored after each
        # widget is created so the right-click cycle survives a restart.
        popup_modes = load_popup_modes(self.conn)

        # Auto-fill for universal built-ins (FIELD_DEFS):
        #  1. Append any that aren't in saved order (unless explicitly deleted)
        #  2. Fill missing style entries in sec_styles (older project files saved
        #     section_order but no style → canvas rendered as plain text box)
        #  3. Coded fields with a {key}_Preset map → "combo" (dropdown of hex codes)
        try:
            from attribute_manager import FIELD_DEFS as _FD
            import aisearch_attrs as _am_mod
            _deleted = set(cfg.get("__deleted_sections__", []))
            _present = set(sec_order)
            # Suppress auto-append of an FD key (e.g. "BG") when the
            # project already has its human-label alias ("Background")
            # in section_order — otherwise the canvas ends up with two
            # widgets fighting for the same storage key.
            _alias_owners = set()
            for _alias, _resolved in _HUMAN_LABEL_ALIASES.items():
                if _alias in _present:
                    for _fd_k in _FD:
                        if _fd_k.lower() == _resolved:
                            _alias_owners.add(_fd_k)
            for _fd_key, (_fd_style, _) in _FD.items():
                # Append if missing AND not already covered by an alias.
                if (_fd_key not in _present
                        and _fd_key not in _deleted
                        and _fd_key not in _alias_owners):
                    sec_order.append(_fd_key)
                # Fill style if missing
                if _fd_key not in sec_styles and _fd_key not in _deleted:
                    _eff_style = _fd_style
                    _preset_key = f"{_fd_key}_Preset"
                    if (cfg.get(_preset_key) or
                            _am_mod._DEFAULT_TAG_GROUPS.get(_preset_key)):
                        _eff_style = "combo"
                    sec_styles[_fd_key] = _eff_style
        except Exception:
            pass

        # Build reverse map: field key → group name
        key_to_group  = {k: grp for grp, keys in sec_groups.items() for k in keys}
        # Fields that cannot be collapsed (always stay open)
        not_collapsible = set(cfg.get("__not_collapsible__", []))

        x, y = 20, 20   # default grid position for unsaved widgets
        col_w = 220

        # First pass — create all widgets
        for key in sec_order:
            if key.startswith("__"):
                continue
            style   = sec_styles.get(key, "")
            options = cfg.get(key, []) if isinstance(cfg.get(key), list) else []
            # Fall back to _DEFAULT_TAG_GROUPS for taglist/radio/combo with no project-level data
            if not options and style in ("taglist", "radio", "boolean", "combo"):
                try:
                    import aisearch_attrs as _am
                    options = (_am._DEFAULT_TAG_GROUPS.get(key + "_Preset")
                               or _am._DEFAULT_TAG_GROUPS.get(key, []))
                except Exception:
                    pass
            # For matrix fields with empty options, auto-collect from first sub-table
            if style == "matrix" and not options:
                for sub_k in sorted(cfg):
                    if (sub_k.startswith(key + "_") and sub_k not in sec_order
                            and isinstance(cfg.get(sub_k), list) and cfg[sub_k]):
                        options = cfg[sub_k]
                        break
            # For combo fields with empty options, fall back to {key}_Preset
            if style == "combo" and not options:
                preset = cfg.get(key + "_Preset")
                if isinstance(preset, list) and preset:
                    options = preset
            if style == "text":
                label = text_fields.get(key, {}).get("label", key.replace("_", " ").title())
            elif style == "matrix" and key in col_names and col_names[key]:
                label = col_names[key][0]
            elif key in _CODED_LABELS and _CODED_LABELS[key]:
                label = _CODED_LABELS[key]
            elif key in parent_names and parent_names[key]:
                label = parent_names[key]
            else:
                try:
                    import aisearch_attrs as _am
                    label = _am._DEFAULT_FIELD_NAMES.get(key, key)
                except Exception:
                    label = key
            if style == "text":
                tmeta = dict(text_fields.get(key) or {})
            elif style in ("1dig", "2dig", "3dig", "4dig"):
                tmeta = {"__config__": cfg}
            else:
                tmeta = None

            if not style:
                style = "matrix" if options else "text"

            grp         = key_to_group.get(key)
            color       = (group_colors or {}).get(grp, "#2a2a2a")
            sz          = (sizes or {}).get(key)
            collapsible = False
            collapsed   = False
            hidden_for  = cfg.get("__hidden_for__", {}).get(key, [])
            # Exclusive (single-select) when style is explicitly "radio"
            _auto_excl = (style == "radio")
            w = FieldWidget(key, label, style, options, tmeta, self.conn,
                            color=color, group=grp,
                            collapsible=collapsible, collapsed=collapsed,
                            hidden_for=hidden_for, exclusive=_auto_excl,
                            parent=self.canvas)
            w._cfg_path   = self.cfg_path
            # Derive project name from cfg_path so widgets can look up
            # per-project face DB / registry without relying on entry["_project"].
            try:
                _bn = os.path.basename(self.cfg_path or "")
                if _bn.startswith("attrs_tags_") and _bn.endswith(".json"):
                    w._project = _bn[len("attrs_tags_"):-len(".json")]
            except Exception:
                pass
            w._conditions = list(cfg.get("__conditions__", {}).get(key, []))
            # Restore saved popup mode (grid / freq / alpha) and refresh
            # the label + combo ordering to match.
            _saved_mode = popup_modes.get(key)
            if _saved_mode and hasattr(w, "apply_popup_mode_token"):
                w.apply_popup_mode_token(_saved_mode)
                if hasattr(w, "_sort_lbl") and w._sort_lbl is not None:
                    w._sort_lbl.setText(w._sort_label_text())
                    w._sort_lbl.setStyleSheet(
                        "color:#888;font-size:9pt;" if w._sort_freq
                        else "color:#8ab;font-size:9pt;font-style:italic;")
                if w.options:
                    try:
                        w._fill_combo(preserve=True)
                    except Exception:
                        pass
            px, py = positions.get(key, (x, y))
            px = max(0, min(px, 4000))   # clamp in case of corrupted saved position
            py = max(0, min(py, 4000))
            w.move(px, py)
            if sz:
                w.resize(sz[0], sz[1])
            w.show()
            # Don't save_position on every moved signal — that fires for
            # programmatic moves (Qt layout, snap cascades) too, which over
            # time drifts widgets in the DB. Only mouseReleaseEvent saves
            # positions for user-driven drags (already handled in FieldWidget).
            # Wire soft-field changes to data_changed signal
            for _btn in getattr(w, "_btns", {}).values():
                _btn.toggled.connect(lambda _checked, _w=w: self.data_changed.emit())
            for _btn in getattr(w, "_hex_grid_btns", {}).values():
                _btn.toggled.connect(lambda _checked, _w=w: self.data_changed.emit())
            _cb = getattr(w, "_cb", None)
            if _cb:
                _cb.currentIndexChanged.connect(lambda _: self.data_changed.emit())
            for _ct in getattr(w, "_coded_combos", []):
                _coded_cb = _ct[1]
                _coded_cb.currentIndexChanged.connect(lambda _: self.data_changed.emit())
            _te = getattr(w, "_te", None)
            if _te:
                _te.textChanged.connect(self.data_changed.emit)
            _pid_edit = getattr(w, "_pid_edit", None)
            if _pid_edit:
                _pid_edit.textChanged.connect(lambda _: self.data_changed.emit())
            # Bubble action buttons (e.g. Detect on P box)
            w.action_triggered.connect(self.action_triggered)
            # CLIP_*/FACE debug tiles are hidden by default — revealed via a
            # per-field Update action. Left-click-release on the debug tile
            # itself hides it (but drag/resize still works).
            if key.startswith("CLIP_") or key == "CLIP" or key == "FACE" or key == "FACE_PW":
                w.hide()
                _orig_release = w.mouseReleaseEvent
                def _hide_on_release(ev, _w=w, _orig=_orig_release):
                    was_drag = (_w._drag_pos is not None) or (_w._resize_pos is not None)
                    _orig(ev)
                    if ev.button() == Qt.MouseButton.LeftButton and not was_drag:
                        _w.hide()
                w.mouseReleaseEvent = _hide_on_release
            self.widgets.append(w)

            x += col_w
            if x > 900:
                x = 20; y += 160

        # Second pass — wire up group peers + anchors
        grp_map = {}
        for w in self.widgets:
            if w._group:
                grp_map.setdefault(w._group, []).append(w)
        for w in self.widgets:
            w._group_peers = grp_map.get(w._group, [w])
            w._canvas_ref  = self   # so _resize_te can propagate

        # Sync canvas refs
        self.canvas._widgets     = self.widgets
        self.canvas._connections = self._connections

        # Load saved connections
        keys = {w.key for w in self.widgets}
        for row in load_connections(self.conn):
            cid, ba, pa, bb, pb = row
            if ba in keys and bb in keys:
                self._connections.append(row)

        # Auto-wire CLIP_*/FACE debug tiles to their parent field tile (parent BL
        # → debug TL, so the debug panel anchors directly below the parent).
        # Only created if no connection already exists between the pair — user's
        # manual wiring is preserved.
        _DEBUG_PARENT = {
            "CLIP_E":  "E",  "CLIP_HC": "HC", "CLIP_FA": "FA", "CLIP_SK": "SK",
            "CLIP_PM": "PM", "CLIP_CS": "CS", "CLIP_BG": "Background",
            "CLIP_X":  "X",  "CLIP_CL": "CL",
            "FACE":    "P",
            "FACE_PW": "PW",
        }
        for _dbg, _par in _DEBUG_PARENT.items():
            if _dbg not in keys or _par not in keys:
                continue
            _already = any(
                {r[1], r[3]} == {_dbg, _par} for r in self._connections
            )
            if _already:
                continue
            _cid = save_connection(self.conn, _par, "BL", _dbg, "TL")
            self._connections.append((_cid, _par, "BL", _dbg, "TL"))

        # Re-snap all connections (parents first, then children transitively)
        snapped = set()
        def _snap_chain(key):
            if key in snapped:
                return
            snapped.add(key)
            for row in self._connections:
                _, ba, pa, bb, pb = row
                if ba == key:
                    self._snap_child(row)
                    _snap_chain(bb)
        # Find root parents (boxes that are never a child)
        child_keys = {pb for _, _, _, pb, _ in self._connections}
        root_keys  = [ba for _, ba, _, _, _ in self._connections if ba not in child_keys]
        for rk in root_keys:
            _snap_chain(rk)

        # Wire signals → connection propagation + canvas repaint
        for w in self.widgets:
            # Coalesce many move/resize events within a single Qt tick into a
            # single cascade — without this, a layout pass that touches N
            # widgets fires N×N snap_child calls (each event runs the whole
            # chain). _schedule_snap batches them and runs one cascade.
            w.moved.connect(lambda k, _x, _y: self._schedule_snap(k))
            w.resized.connect(lambda k: self._schedule_snap(k))

        # Defer a second snap pass until after Qt finishes laying out widgets —
        # initial _snap_chain runs before Qt resolves final tile sizes, which
        # leaves children misaligned. Same effect as user clicking the resize
        # edge to fire resized → cascade.
        def _final_snap(_rk=root_keys):
            for rk in _rk:
                self._apply_connections_for(rk)
        QTimer.singleShot(0, _final_snap)

        # Size the canvas exactly to fit current visible tiles + small margin.
        self._fit_canvas_to_widgets()

    # ── Undo ─────────────────────────────────────────────────────────────────

    def _push_undo(self):
        """Snapshot current positions, sizes, and connections onto the undo stack."""
        snapshot = {
            "positions":   {w.key: (w.x(), w.y()) for w in self.widgets},
            "sizes":       {w.key: (w.width(), w.height()) for w in self.widgets},
            "connections": list(self._connections),
        }
        self._undo_stack.append(snapshot)
        if len(self._undo_stack) > 30:
            self._undo_stack.pop(0)

    def _do_undo(self):
        """Restore the last snapshot from the undo stack."""
        if not self._undo_stack:
            return
        snap = self._undo_stack.pop()
        for w in self.widgets:
            pos = snap["positions"].get(w.key)
            sz  = snap["sizes"].get(w.key)
            if pos:
                w.move(*pos)
                save_position(self.conn, w.key, pos[0], pos[1])
            if sz:
                w.resize(*sz)
                save_size(self.conn, w.key, sz[0], sz[1])
        # Restore connections: clear all then re-insert
        for row in list(self._connections):
            delete_connection(self.conn, row[0])
        self._connections.clear()
        for _cid, ba, pa, bb, pb in snap["connections"]:
            new_cid = save_connection(self.conn, ba, pa, bb, pb)
            self._connections.append((new_cid, ba, pa, bb, pb))
        self.canvas._connections = self._connections
        self.canvas.update()

    def _create_connection(self, key_a, port_a, key_b, port_b):
        """Connect two corners. First clicked (key_a) is the parent (stays fixed);
        second clicked (key_b) is the child that snaps to it."""
        wmap = {w.key: w for w in self.widgets}
        wa = wmap.get(key_a)
        wb = wmap.get(key_b)
        if not (wa and wb):
            return
        self._push_undo()
        # Avoid duplicates (any direction)
        for row in self._connections:
            _, ba, pa, bb, pb = row
            if (ba == key_a and pa == port_a and bb == key_b and pb == port_b) or \
               (ba == key_b and pa == port_b and bb == key_a and pb == port_a):
                return
        # First clicked = parent (fixed), second clicked = child (snaps)
        parent_key, parent_port = key_a, port_a
        child_key,  child_port  = key_b, port_b
        cid = save_connection(self.conn, parent_key, parent_port, child_key, child_port)
        row = (cid, parent_key, parent_port, child_key, child_port)
        self._connections.append(row)
        self._snap_child(row)
        self._apply_connections_for(child_key)
        self.canvas.update()

    def _snap_child(self, conn_row):
        """Move child widget so its port aligns with parent's port."""
        _, parent_key, parent_port, child_key, child_port = conn_row
        wmap = {w.key: w for w in self.widgets}
        pw = wmap.get(parent_key)
        cw = wmap.get(child_key)
        if not (pw and cw):
            return
        # Parent corner in canvas coords
        pp = _corner_pos(pw, parent_port)
        # Child corner offset within child box
        csx, csy = _PORT_DIR.get(child_port, (0, 0))
        cx_off = cw.width()  if csx > 0 else 0
        cy_off = cw.height() if csy > 0 else 0
        # Move child so its corner sits exactly on parent's corner — no gap
        new_x = pp.x() - cx_off
        new_y = pp.y() - cy_off
        # Flag prevents moveEvent from re-emitting moved while we're mid-snap —
        # the outer _apply_connections_for cascade will iterate children explicitly.
        cw._snapping = True
        try:
            cw.move(new_x, new_y)
        finally:
            cw._snapping = False
        # Don't persist — child position is fully derived from connections, so
        # _snap_chain recomputes it on next load. Skipping the SQLite commit
        # here saves ~5ms per snap (30 snaps × 5ms = 150ms per cascade).

    def _schedule_snap(self, key):
        """Add `key` to the pending snap-cascade set and schedule a single
        flush on the next event loop tick. Coalesces many move/resize events
        from the same layout pass into one cascade with a shared visited set,
        avoiding the N² explosion of redundant snap_child calls."""
        if not hasattr(self, "_pending_snap_keys"):
            self._pending_snap_keys = set()
            self._pending_snap_scheduled = False
        self._pending_snap_keys.add(key)
        if not self._pending_snap_scheduled:
            self._pending_snap_scheduled = True
            QTimer.singleShot(0, self._flush_snap)

    def _flush_snap(self):
        keys = self._pending_snap_keys
        self._pending_snap_keys = set()
        self._pending_snap_scheduled = False
        if not keys:
            return
        try:
            from aisearch_debug import dbg as _dbg
            _dbg(f"flush_snap keys={sorted(keys)}")
        except Exception:
            pass
        # Single shared visited set: any key already touched by an earlier
        # cascade in this batch won't be processed again.
        visited = set()
        for k in keys:
            self._apply_connections_for(k, visited)
        # Tighten canvas size to the new tile bounds (no leftover empty area).
        self._fit_canvas_to_widgets()

    def _fit_canvas_to_widgets(self):
        """Resize the canvas to exactly contain visible tiles plus a small
        margin. Prevents the canvas from staying tall after widgets move up,
        and grows it when widgets move down/right."""
        visible = [w for w in self.widgets if w.isVisible()]
        if not visible:
            return
        bottom = max(w.y() + w.height() for w in visible)
        right  = max(w.x() + w.width()  for w in visible)
        self.canvas.setFixedSize(max(right + 40, 200), max(bottom + 40, 200))

    def _apply_connections_for(self, key, _visited=None):
        """Reposition all boxes connected as child to `key` (parent moved/resized)."""
        if _visited is None:
            _visited = set()
        if key in _visited:
            return
        _visited.add(key)
        wmap = {w.key: w for w in self.widgets}
        for row in self._connections:
            _, ba, pa, bb, pb = row
            if ba == key:
                self._snap_child(row)
                self._apply_connections_for(bb, _visited)
        self.canvas.update()

    def _remove_connection(self, cid, _push=True):
        """Remove a connection by its DB id."""
        if _push:
            self._push_undo()
        delete_connection(self.conn, cid)
        # Mutate in-place to preserve shared reference with canvas._connections
        to_remove = [r for r in self._connections if r[0] == cid]
        for r in to_remove:
            self._connections.remove(r)
        self.canvas.update()

    def _on_connect_started(self, key, port):
        """Click-click connection: first click selects port, second click connects."""
        pending = self.canvas._pending
        if pending is None:
            # First click — mark as pending
            self.canvas._pending = (key, port)
        elif pending == (key, port):
            # Same port clicked again — cancel
            self.canvas._pending = None
        elif pending[0] == key:
            # Different port on same box — switch selection
            self.canvas._pending = (key, port)
        else:
            # Second click on a different box — create connection.
            # Parent = more top-left tile (smaller x+y); child snaps to parent.
            # Using x+y (instead of just y) resolves ties when tiles are roughly
            # level — the one closer to origin stays put.
            self.canvas._pending = None
            key_a, port_a = pending
            key_b, port_b = key, port
            wmap = {w.key: w for w in self.widgets}
            wa = wmap.get(key_a)
            wb = wmap.get(key_b)
            if wa and wb and (wa.x() + wa.y()) <= (wb.x() + wb.y()):
                parent_key, parent_port = key_a, port_a
                child_key,  child_port  = key_b, port_b
            else:
                parent_key, parent_port = key_b, port_b
                child_key,  child_port  = key_a, port_a
            self._create_connection(parent_key, parent_port, child_key, child_port)
        self.canvas.update()

    def _set_drag(self, state):
        on = bool(state)
        for w in self.widgets:
            w.drag_mode = on

        if not on:
            self.canvas._pending = None   # cancel any pending connection
        self.canvas.update()

    def _set_snap(self, state):
        on = bool(state)
        self.canvas._edit_on = on
        for w in self.widgets:
            w.set_edit_mode(on)
        self.canvas.update()

    def _apply_mode(self, mode):
        """Show/hide panels based on each widget's own _hidden_for list."""
        mode_lower = mode.lower()   # "all", "image", "video"
        for w in self.widgets:
            # CLIP_*/FACE debug tiles start hidden and only appear on explicit
            # Update; mode switches should not force-show them.
            if w.key.startswith("CLIP_") or w.key in ("CLIP", "FACE", "FACE_PW"):
                continue
            if mode_lower == "all":
                w.setVisible(True)
            else:
                w.setVisible(mode_lower not in [m.lower() for m in w._hidden_for])

    def _align_left(self):
        if not self.widgets: return
        self._push_undo()
        min_x = min(w.x() for w in self.widgets)
        for w in self.widgets:
            w.move(min_x, w.y())
            save_position(self.conn, w.key, w.x(), w.y())
            self._apply_connections_for(w.key)

    def _align_top(self):
        if not self.widgets: return
        self._push_undo()
        min_y = min(w.y() for w in self.widgets)
        for w in self.widgets:
            w.move(w.x(), min_y)
            save_position(self.conn, w.key, w.x(), w.y())
            self._apply_connections_for(w.key)

    def _auto_grid_layout(self):
        """Disconnect everything and lay visible tiles out in a flowing grid,
        grouped by __section_groups__ — each group starts on a fresh row
        with extra vertical padding so groups are visually separated.
        Within a group tiles flow left-to-right; wide tiles wrap to the next
        line. CLIP_*/FACE debug tiles are skipped (they stay hidden and
        anchor to their parents when re-shown)."""
        if not self.widgets:
            return
        # Confirm before nuking connections — users were losing wires they
        # had spent time building because Auto Grid sits next to Layout in
        # the footer and the two buttons read as similar "arrange / save"
        # actions.
        if self._connections:
            from PyQt6.QtWidgets import QMessageBox
            reply = QMessageBox.question(
                self,
                _lang_label("Auto Grid / 自動グリッド"),
                _lang_label(
                    f"Auto Grid will remove all {len(self._connections)} "
                    f"connection(s) before rearranging tiles. Continue?\n"
                    f"自動グリッドは、タイルを再配置する前にすべての"
                    f"接続（{len(self._connections)} 件）を削除します。続行しますか？"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return
        self._push_undo()
        # 1. Drop all connections
        for row in list(self._connections):
            self._remove_connection(row[0], _push=False)
        # 2. Build group → keys mapping, honoring __group_order__
        sec_order = list(self.cfg.get("__section_order__", []))
        order_idx = {k: i for i, k in enumerate(sec_order)}
        sec_groups = self.cfg.get("__section_groups__", {}) or {}
        group_order = list(self.cfg.get("__group_order__") or sec_groups.keys())
        key_to_group = {k: g for g, keys in sec_groups.items() for k in keys}
        # Only currently-visible tiles get laid out — tiles hidden by mode
        # (Image/Video/__hidden_for__) shouldn't reserve grid slots and leave
        # vertical gaps. CLIP_*/FACE debug tiles are also skipped (always hidden
        # by default; revealed via right-click Show/Update with their own anchor).
        layoutable = [w for w in self.widgets
                      if w.isVisible()
                      and not (w.key.startswith("CLIP_") or w.key in ("CLIP", "FACE", "FACE_PW"))]
        # Bucket tiles by group; ungrouped tiles land in "__none__" at the end
        buckets = {g: [] for g in group_order}
        buckets["__none__"] = []
        for w in layoutable:
            g = key_to_group.get(w.key, "__none__")
            buckets.setdefault(g, []).append(w)
        for g in buckets:
            buckets[g].sort(key=lambda w: (order_idx.get(w.key, 10_000),
                                           w.y(), w.x()))
        # 3. Layout — each group starts a new row with extra gap
        pad_x, pad_y = 12, 12
        group_gap   = 24   # extra vertical space between groups
        start_x, start_y = 20, 20
        canvas_w = max(self.canvas.width(), 1200)
        cur_y = start_y
        for g in list(group_order) + ["__none__"]:
            tiles = buckets.get(g) or []
            if not tiles:
                continue
            cur_x, row_h = start_x, 0
            for w in tiles:
                ww, wh = w.width(), w.height()
                if cur_x + ww > canvas_w - start_x and cur_x > start_x:
                    cur_x = start_x
                    cur_y += row_h + pad_y
                    row_h = 0
                w.move(cur_x, cur_y)
                save_position(self.conn, w.key, cur_x, cur_y)
                cur_x += ww + pad_x
                if wh > row_h:
                    row_h = wh
            cur_y += row_h + group_gap
        # 4. Resize canvas to fit the new grid
        if layoutable:
            bottom = max(w.y() + w.height() for w in layoutable)
            right  = max(w.x() + w.width()  for w in layoutable)
            self.canvas.setMinimumHeight(max(1000, bottom + 40))
            self.canvas.setMinimumWidth(max(1400, right + 40))
        self.canvas.update()

    # ── Preview data binding ──────────────────────────────────────────────────

    # ── Condition evaluation ──────────────────────────────────────────────────

    _COND_EMPTY = {"", "no", "none", "false", "0", "n/a", "null"}

    def _eval_conditions(self, raw_meta):
        """Return set of field keys that should be hidden based on conditions."""
        hidden = set()
        for w in self.widgets:
            for cond in w._conditions:
                src = cond.get("source", "")
                op  = cond.get("op", "empty")
                val = cond.get("value", "").lower()
                raw_val = str(raw_meta.get(src, "")).strip().lower()
                match = False
                if   op == "equals":    match = raw_val == val
                elif op == "not_eq":    match = raw_val != val
                elif op == "contains":  match = val in raw_val
                elif op == "empty":     match = raw_val in self._COND_EMPTY
                elif op == "not_empty": match = raw_val not in self._COND_EMPTY
                if match:
                    hidden.add(w.key)
                    break
        return hidden

    def load_file(self, path, entry, raw_meta=None):
        """Populate all canvas panels from a file's attrs entry."""
        tags_set = set(entry.get("tags", []))
        _entry = dict(entry); _entry["path"] = path
        for w in self.widgets:
            w.load_soft(tags_set, _entry)

        # Apply visibility conditions — always evaluate (raw_meta may be absent)
        _rm = raw_meta if raw_meta is not None else {}
        # Normalize keys to lowercase so "Audio" matches condition source "audio"
        meta_with_tags = {k.lower(): v for k, v in _rm.items()}
        # Inject boolean tag presence so "audio equals true/false" conditions work
        for tag in tags_set:
            meta_with_tags.setdefault(tag.lower(), "true")
        # Inject dedicated entry fields (audio, speech, etc.) so conditions can check them
        for _dfk in _DEDICATED_FIELD_KEYS.values():
            _dfv = _entry.get(_dfk, "")
            if _dfv:
                meta_with_tags[_dfk] = _dfv
        # Boolean widgets not in tags_set = "false"
        for w in self.widgets:
            if w.style == "boolean" and w.key.lower() not in meta_with_tags:
                meta_with_tags[w.key.lower()] = "false"
        hidden_keys = self._eval_conditions(meta_with_tags)
        cur_mode = self._mode_cb.currentText().lower()
        for w in self.widgets:
            # CLIP_*/FACE debug tiles only appear via right-click → Show/Update —
            # condition + mode logic must not force-show them.
            if w.key.startswith("CLIP_") or w.key in ("CLIP", "FACE", "FACE_PW"):
                continue
            if w.key in hidden_keys:
                w.setVisible(False)
            else:
                # Restore visibility (respect hidden_for mode)
                if cur_mode == "all":
                    w.setVisible(True)
                else:
                    w.setVisible(cur_mode not in [m.lower() for m in w._hidden_for])

        # Recompute anchored positions after text boxes settle
        QTimer.singleShot(50, self.canvas.update)
        # Auto-set mode from file extension
        ext = os.path.splitext(path)[1].lower() if path else ""
        _IMG = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".avif"}
        _VID = {".mp4", ".mkv", ".mov", ".m4v", ".avi", ".webm", ".wmv", ".ts"}
        if ext in _IMG:
            self._mode_cb.blockSignals(True)
            self._mode_cb.setCurrentText("Image")
            self._mode_cb.blockSignals(False)
            self._apply_mode("Image")
        elif ext in _VID:
            self._mode_cb.blockSignals(True)
            self._mode_cb.setCurrentText("Video")
            self._mode_cb.blockSignals(False)
            self._apply_mode("Video")
        else:
            self._mode_cb.blockSignals(True)
            self._mode_cb.setCurrentText("All")
            self._mode_cb.blockSignals(False)
            self._apply_mode("All")

    def collect_soft_data(self):
        """Collect current canvas widget values.
        Returns (extra_tags, text_dict, coded_dict, matrix_dict, pathlist_dict).
        - extra_tags    : set of tag keys (taglist/boolean/radio)
        - text_dict     : {db_key: str} for text fields
        - coded_dict    : {field_key: val} for O/R/K-style combos
        - matrix_dict   : {widget_key: val} for matrix widgets — written to
                           entry[widget_key] so codes don't share the tags
                           namespace across matrix groups.
        - pathlist_dict : {db_key: [path, ...]} for pathlist widgets.
        """
        extra_tags    = set()
        text_dict     = {}
        coded_dict    = {}
        matrix_dict   = {}
        pathlist_dict = {}
        for w in self.widgets:
            result = w.collect_soft()
            if result is None:
                continue
            if result[0] == "tags":
                extra_tags |= result[1]
            elif result[0] == "tag" and result[1]:
                extra_tags.add(result[1])
            elif result[0] == "text":
                text_dict[result[1]] = result[2]
            elif result[0] == "coded" and result[2]:
                coded_dict[result[1]] = result[2]
            elif result[0] == "matrix_field":
                # Always include — empty value means "cleared selection"
                matrix_dict[result[1]] = result[2]
            elif result[0] == "pathlist":
                # Always include so empty list overwrites a previous value
                pathlist_dict[result[1]] = result[2]
        return extra_tags, text_dict, coded_dict, matrix_dict, pathlist_dict

    def refresh_language(self):
        """Re-populate all combo labels and tile titles after a language change."""
        for w in self.widgets:
            w.setTitle(_lang_label(w._label_raw))
            if getattr(w, "_cb", None):
                w._fill_combo(preserve=True)
            # Taglist/boolean/radio buttons keep raw label on btn._lbl_raw
            for _btn in getattr(w, "_btns", {}).values():
                _raw = getattr(_btn, "_lbl_raw", None)
                if _raw:
                    _btn.setText(_lang_label(_raw))
            for _ct in getattr(w, "_coded_combos", []):
                sub_key = _ct[0]
                coded_cb = _ct[1]
                cur = coded_cb.currentData()
                sub_opts = w._cfg.get(sub_key, []) if hasattr(w, "_cfg") else []
                coded_cb.blockSignals(True)
                for i in range(1, coded_cb.count()):
                    code = coded_cb.itemData(i)
                    raw = next((lbl for k, lbl in sub_opts if k == code), code)
                    coded_cb.setItemText(i, _lang_label(raw))
                if cur:
                    idx = coded_cb.findData(cur)
                    if idx >= 0:
                        coded_cb.setCurrentIndex(idx)
                coded_cb.blockSignals(False)

    def _gather_lost(self):
        """Move any off-screen or out-of-bounds boxes back into the visible canvas area."""
        self._push_undo()
        # Use minimum canvas dimensions as the reference for "off-screen" so that
        # widgets parked beyond the 1400×1000 safe area are always gathered,
        # regardless of how large the canvas widget has grown to accommodate them.
        CW = 1400
        CH = 1000
        MARGIN = 10
        # Always start gathered boxes at the top-left so they're easy to find.
        gx, gy = MARGIN, MARGIN
        row_h  = 0
        for w in self.widgets:
            in_view = (0 <= w.x() < CW and 0 <= w.y() < CH)
            if not in_view:
                w.move(gx, gy)
                save_position(self.conn, w.key, gx, gy)
                self._apply_connections_for(w.key)
                row_h = max(row_h, w.height())
                gx += w.width() + MARGIN
                if gx + w.width() > CW:
                    gx  = MARGIN
                    gy += row_h + MARGIN
                    row_h = 0
        self.canvas.update()

    def _auto_arrange(self):
        """Arrange all panels in a tidy grid left-to-right, top-to-bottom."""
        self._push_undo()
        COL_W = 240
        GAP   = 10
        x, y  = GAP, GAP
        row_h = 0
        for w in self.widgets:
            if w._collapsed:
                pass   # treat as small
            if x + w.width() > self.canvas.width() and x > GAP:
                x  = GAP
                y += row_h + GAP
                row_h = 0
            w.move(x, y)
            save_position(self.conn, w.key, x, y)
            row_h = max(row_h, w.height())
            x += COL_W + GAP


# ── Standalone window wrapper ─────────────────────────────────────────────────

class AttrViewer(QMainWindow):
    """Thin QMainWindow wrapper around AttrViewerWidget for standalone use."""
    def __init__(self, config_path=CONFIG_FILE):
        super().__init__()
        self.setWindowTitle("Attr Viewer")
        self.resize(1200, 850)
        self._w = AttrViewerWidget(config_path, parent=self)
        self.setCentralWidget(self._w)


if __name__ == "__main__":
    config = sys.argv[1] if len(sys.argv) > 1 else CONFIG_FILE
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = AttrViewer(config)
    win.show()
    sys.exit(app.exec())
