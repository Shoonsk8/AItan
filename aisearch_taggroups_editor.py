"""
aisearch_taggroups_editor.py
Standalone PyQt6 editor for AItan coded-field tag groups.

Usage:
    python aisearch_taggroups_editor.py

Saves:  attribute_workspace.json  (row-based working copy)
        attrs_tags.json           (TAG_GROUPS format loaded by aisearch_attrs.py)
"""
import sys, re
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QScrollArea,
    QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QComboBox, QPushButton,
    QFrame, QToolButton, QSizePolicy, QMessageBox,
)
from PyQt6.QtCore import Qt, QSize

from attribute_manager import AttributeManager, FIELD_DEFS, _STYLE_PAD

# ── Style display names used in the combo ──────────────────────────────────
_STYLE_LABELS = {
    "1dig":   "1-digit",
    "2dig":   "2-digit independent",
    "3dig":   "3-digit independent",
    "matrix": "16×16 matrix",
    "id":     "ID (structural)",
}

# Pre-defined AIsearch fields shown in the Add-Table combo
_AISEARCH_PRESETS = list(FIELD_DEFS.keys())


# ══════════════════════════════════════════════════════════════════════════════
class CollapsibleSection(QWidget):
    """A titled, toggle-collapsible container widget."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self._expanded = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 2, 0, 2)
        outer.setSpacing(0)

        # Header button
        self._btn = QToolButton()
        self._btn.setText(f"  {title}")
        self._btn.setArrowType(Qt.ArrowType.RightArrow)
        self._btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._btn.setCheckable(True)
        self._btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._btn.setStyleSheet(
            "QToolButton { background:#2a2a2a; color:#e0e0e0; "
            "font-weight:bold; font-size:10pt; border:none; "
            "padding:4px; text-align:left; }"
            "QToolButton:checked { background:#1e3a1e; }"
        )
        self._btn.toggled.connect(self._on_toggle)
        outer.addWidget(self._btn)

        # Content area
        self.content = QFrame()
        self.content.setVisible(False)
        self.content.setStyleSheet("QFrame { background:#1a1a1a; }")
        outer.addWidget(self.content)

    def _on_toggle(self, checked: bool):
        self.content.setVisible(checked)
        self._btn.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
        )

    def expand(self):
        self._btn.setChecked(True)


# ══════════════════════════════════════════════════════════════════════════════
class TagGroupsEditor(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("あいたん AItan — Tag Groups Editor")
        self.resize(920, 740)

        self._manager = AttributeManager()

        # Seed from existing TAG_GROUPS if workspace is empty
        if not self._manager.data:
            self._seed_from_defaults()

        # Track loaded prefixes and their section widgets
        self._loaded: dict[str, CollapsibleSection] = {}   # prefix → section
        self._entries: dict[str, any] = {}                 # json_key → widget(s)

        self._build_ui()
        self._load_existing()

    # ── Seeding ──────────────────────────────────────────────────────────────
    def _seed_from_defaults(self):
        try:
            import aisearch_attrs as _a
            self._manager.import_from_tag_groups(_a.TAG_GROUPS)
        except Exception:
            pass

    # ── UI construction ──────────────────────────────────────────────────────
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # ── Top bar ──────────────────────────────────────────────────────────
        top = QHBoxLayout()

        top.addWidget(QLabel("Prefix:"))
        self._key_edit = QLineEdit()
        self._key_edit.setFixedWidth(60)
        self._key_edit.setPlaceholderText("e.g. E")
        top.addWidget(self._key_edit)

        self._style_cb = QComboBox()
        self._style_cb.addItem("Select style…")
        for s, lbl in _STYLE_LABELS.items():
            self._style_cb.addItem(lbl, s)
        self._style_cb.setFixedWidth(200)
        top.addWidget(self._style_cb)

        add_btn = QPushButton("Add Table")
        add_btn.setStyleSheet("background:#2196F3; color:white; font-weight:bold; padding:4px 10px;")
        add_btn.clicked.connect(self._on_add_table)
        top.addWidget(add_btn)

        top.addSpacing(16)

        # Quick-load AIsearch preset
        top.addWidget(QLabel("Preset:"))
        self._preset_cb = QComboBox()
        self._preset_cb.addItem("— AItan field —")
        for p in _AISEARCH_PRESETS:
            style, cols = FIELD_DEFS[p]
            self._preset_cb.addItem(f"{p}  ({_STYLE_LABELS[style]})", p)
        self._preset_cb.setFixedWidth(220)
        top.addWidget(self._preset_cb)

        load_btn = QPushButton("Load Preset")
        load_btn.setStyleSheet("background:#555; color:white; padding:4px 10px;")
        load_btn.clicked.connect(self._on_load_preset)
        top.addWidget(load_btn)

        top.addStretch()
        root.addLayout(top)

        # ── Scroll area ──────────────────────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet("QScrollArea { background:#111; border:none; }")

        self._container = QWidget()
        self._container.setStyleSheet("background:#111;")
        self._vbox = QVBoxLayout(self._container)
        self._vbox.setContentsMargins(4, 4, 4, 4)
        self._vbox.setSpacing(2)
        self._vbox.addStretch()

        self._scroll.setWidget(self._container)
        root.addWidget(self._scroll, stretch=1)

        # ── Save button ──────────────────────────────────────────────────────
        save_btn = QPushButton("SAVE  (workspace + attrs_tags.json)")
        import aisearch_config as cfg
        save_btn.setStyleSheet(cfg.btn_ss("btn_write", None, "font-size:11pt; padding:8px;"))
        save_btn.clicked.connect(self._on_save)
        root.addWidget(save_btn)

    # ── Load existing workspace ───────────────────────────────────────────────
    def _load_existing(self):
        seen: set[str] = set()
        for key in self._manager.data:
            m = re.match(r'^([A-Za-z]{1,3})', key.upper())
            if not m:
                continue
            prefix = m.group(1)
            if prefix in seen or prefix not in FIELD_DEFS:
                continue
            seen.add(prefix)
            style, cols = FIELD_DEFS[prefix]
            self._add_table(prefix, style, cols, expand=False)

        # Load any remaining FIELD_DEFS prefixes that have data but weren't added
        # (handles first-run seeding)
        for prefix, (style, cols) in FIELD_DEFS.items():
            if prefix not in seen:
                # Only add if there is seeded data
                pad = _STYLE_PAD.get(style, 2)
                has_data = any(
                    k.startswith(prefix) and len(k) == len(prefix) + pad
                    for k in self._manager.data
                )
                if has_data:
                    self._add_table(prefix, style, cols, expand=False)

    # ── Add table ─────────────────────────────────────────────────────────────
    def _on_add_table(self):
        prefix = self._key_edit.text().strip().upper()
        if not prefix or len(prefix) > 3:
            QMessageBox.warning(self, "Input Error", "Enter 1–3 character prefix.")
            return
        style = self._style_cb.currentData()
        if not style:
            QMessageBox.warning(self, "Input Error", "Select a style.")
            return
        if prefix in self._loaded:
            QMessageBox.information(self, "Info", f"'{prefix}' already open.")
            return
        cols = FIELD_DEFS.get(prefix, (style, []))[1]
        self._add_table(prefix, style, cols, expand=True)
        self._key_edit.clear()

    def _on_load_preset(self):
        prefix = self._preset_cb.currentData()
        if not prefix:
            return
        if prefix in self._loaded:
            QMessageBox.information(self, "Info", f"'{prefix}' already open.")
            return
        style, cols = FIELD_DEFS[prefix]
        self._add_table(prefix, style, cols, expand=True)

    def _add_table(self, prefix: str, style: str, cols: list, *, expand: bool):
        if prefix in self._loaded:
            return
        style_lbl = _STYLE_LABELS.get(style, style)
        sec = CollapsibleSection(f"Key = {prefix}   │   {style_lbl}")
        # Insert before the trailing stretch
        self._vbox.insertWidget(self._vbox.count() - 1, sec)
        self._loaded[prefix] = sec

        # Build content
        content_layout = QVBoxLayout(sec.content)
        content_layout.setContentsMargins(8, 4, 8, 8)
        content_layout.setSpacing(0)

        if style == "id":
            lbl = QLabel(f"  3-digit ID marker — {prefix}  (no editable data)")
            lbl.setStyleSheet("color:#888; font-style:italic; padding:8px;")
            content_layout.addWidget(lbl)
            self._entries[prefix] = "ID_MARKER"
        elif style == "matrix":
            self._build_matrix(content_layout, prefix, cols)
        else:
            self._build_digit_table(content_layout, prefix, style, cols)

        if expand:
            sec.expand()

    # ── Digit table (1dig / 2dig / 3dig) ─────────────────────────────────────
    def _build_digit_table(self, layout, prefix: str, style: str, cols: list):
        pad = _STYLE_PAD.get(style, 2)
        n_cols = len(cols)

        grid = QWidget()
        gl = QGridLayout(grid)
        gl.setContentsMargins(2, 2, 2, 2)
        gl.setSpacing(2)

        # Header row
        hdr = QLabel("Hex")
        hdr.setStyleSheet("color:#aaa; font-weight:bold;")
        gl.addWidget(hdr, 0, 0)
        for ci, (col_label, json_field, tg_key) in enumerate(cols):
            h = QLabel(col_label)
            h.setStyleSheet("color:#f0c040; font-weight:bold; padding:0 4px;")
            gl.addWidget(h, 0, ci + 1)

        # Data rows (0x0 … 0xf)
        for i in range(16):
            h_str = hex(i)[2:]
            row_key = f"{prefix}{h_str.zfill(pad)}"
            stored = self._manager.data.get(row_key, {})

            row_lbl = QLabel(h_str)
            row_lbl.setStyleSheet("color:#6ea6f0; font-family:monospace; padding:0 6px;")
            gl.addWidget(row_lbl, i + 1, 0)

            row_widgets: dict[str, QLineEdit] = {}
            for ci, (col_label, json_field, tg_key) in enumerate(cols):
                le = QLineEdit()
                le.setFixedHeight(22)
                le.setMinimumWidth(140)
                le.setStyleSheet(
                    "QLineEdit { background:#252525; color:#e0e0e0; "
                    "border:1px solid #444; padding:1px 4px; }"
                    "QLineEdit:focus { border:1px solid #6ea6f0; }"
                )
                le.setText(stored.get(json_field, ""))
                gl.addWidget(le, i + 1, ci + 1)
                row_widgets[json_field] = le

            self._entries[row_key] = row_widgets

        layout.addWidget(grid)

    # ── 16×16 matrix ─────────────────────────────────────────────────────────
    def _build_matrix(self, layout, prefix: str, cols: list):
        grid = QWidget()
        gl = QGridLayout(grid)
        gl.setContentsMargins(2, 2, 2, 2)
        gl.setSpacing(1)

        # Column headers
        gl.addWidget(QLabel(""), 0, 0)
        for c in range(16):
            lbl = QLabel(hex(c)[2:])
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("color:#f0c040; font-weight:bold; font-family:monospace;")
            gl.addWidget(lbl, 0, c + 1)

        for r in range(16):
            r_h = hex(r)[2:]
            row_lbl = QLabel(r_h)
            row_lbl.setStyleSheet("color:#6ea6f0; font-family:monospace; padding:0 4px;")
            gl.addWidget(row_lbl, r + 1, 0)
            for c in range(16):
                c_h = hex(c)[2:]
                json_key = f"{prefix}{r_h}{c_h}"
                stored = self._manager.data.get(json_key, {})
                le = QLineEdit()
                le.setFixedSize(QSize(100, 22))
                le.setStyleSheet(
                    "QLineEdit { background:#252525; color:#e0e0e0; "
                    "border:1px solid #333; padding:1px 2px; font-size:8pt; }"
                    "QLineEdit:focus { border:1px solid #6ea6f0; }"
                )
                le.setText(stored.get("expression", ""))
                gl.addWidget(le, r + 1, c + 1)
                self._entries[json_key] = {"expression": le}  # key = prefix + r_h + c_h

        layout.addWidget(grid)

    # ── Save ──────────────────────────────────────────────────────────────────
    def _on_save(self):
        new_data: dict = {}

        for key, val in self._entries.items():
            if val == "ID_MARKER":
                new_data[key] = {"id": True}
            elif isinstance(val, dict):
                row: dict[str, str] = {}
                for field, widget in val.items():
                    row[field] = widget.text()
                new_data[key] = row

        try:
            self._manager.save_data(new_data)
            tag_groups = self._manager.export_tag_groups(new_data)
            # Write exported groups to global attrs_tags.json (merge over existing)
            from attribute_manager import TAG_GROUPS_FILE
            import json as _json
            _existing: dict = {}
            import os as _os
            if _os.path.exists(TAG_GROUPS_FILE):
                try:
                    with open(TAG_GROUPS_FILE, encoding="utf-8") as _f:
                        _existing = _json.load(_f)
                except Exception:
                    pass
            _existing.update(tag_groups)
            with open(TAG_GROUPS_FILE, "w", encoding="utf-8") as _f:
                _json.dump(_existing, _f, indent=2, ensure_ascii=False)
            QMessageBox.information(
                self, "Saved",
                f"Workspace → {self._manager.filename}\n"
                f"Tag groups → attrs_tags.json"
            )
        except Exception as ex:
            QMessageBox.critical(self, "Error", str(ex))


# ══════════════════════════════════════════════════════════════════════════════
def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # Dark palette
    from PyQt6.QtGui import QPalette, QColor
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window,          QColor(30, 30, 30))
    pal.setColor(QPalette.ColorRole.WindowText,      QColor(224, 224, 224))
    pal.setColor(QPalette.ColorRole.Base,            QColor(37, 37, 37))
    pal.setColor(QPalette.ColorRole.AlternateBase,   QColor(45, 45, 45))
    pal.setColor(QPalette.ColorRole.Text,            QColor(224, 224, 224))
    pal.setColor(QPalette.ColorRole.Button,          QColor(50, 50, 50))
    pal.setColor(QPalette.ColorRole.ButtonText,      QColor(224, 224, 224))
    pal.setColor(QPalette.ColorRole.Highlight,       QColor(42, 130, 218))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(0, 0, 0))
    app.setPalette(pal)

    win = TagGroupsEditor()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
