import os
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                              QLabel, QLineEdit, QComboBox, QScrollArea,
                              QDoubleSpinBox, QSplitter, QFrame)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
import aisearch_config as cfg
from attr_viewer import _lang_label as _t


# ── Color per source group category ──────────────────────────────────────────
# Categories: AI (ComfyUI/A1111/AIX), File (camera/file), Database (CLIP),
#             Face (landmark detection), Det. (file-property detection)
_GROUP_COLORS = {
    "ComfyUI":    "#1a3a5a",   # AI → blue
    "A1111":      "#1a3a5a",
    "AIX":        "#1a3a5a",
    "JPEG":       "#3a2a10",   # File → amber
    "Image":      "#3a2a10",
    "Video":      "#3a2a10",
    "File":       "#3a2a10",
}
_GROUP_TEXT_COLORS = {
    "ComfyUI":    "#88ccff",
    "A1111":      "#88ccff",
    "AIX":        "#88ccff",
    "JPEG":       "#ffcc88",
    "Image":      "#ffcc88",
    "Video":      "#ffcc88",
    "File":       "#ffcc88",
}
# Category labels shown as separators in the quick-add area
_GROUP_CATEGORIES = [
    ("AI",       ["ComfyUI", "A1111", "AIX"],                "#88ccff", "#1a3a5a"),
    ("File",     ["JPEG", "Image", "Video", "File"],         "#ffcc88", "#3a2a10"),
]

# ── Color per target category ─────────────────────────────────────────────────
_TARGET_SECTION_COLORS = {
    "── Text / Seed ──":   ("#88ccff", "#1a3a5a"),   # text fg, header bg
    "── Tags ──":          ("#ffcc88", "#4a3a1a"),
}
# Map each target label → its section fg color
_TARGET_ITEM_COLOR = {}
_cur_section_fg = "#f0f0f0"
for _lbl, _data in [
    ("── Text / Seed ──",   None), ("Positive Prompt", "prompt"),
    ("Negative Prompt", "neg_prompt"), ("Seed", "seed"), ("Note", "note"),
    ("Speech", "speech"), ("Model / Custom", "model"),
    ("── Tags ──", None),
]:
    if _data is None:
        _cur_section_fg = _TARGET_SECTION_COLORS.get(_lbl, ("#f0f0f0", "#333"))[0]
    else:
        _TARGET_ITEM_COLOR[_lbl] = _cur_section_fg

# ── Known source key groups, organised by the system that produces each key ──
# Each entry is (group_name, [(source_key, default_target), ...])
# default_target is pre-selected in the target combo when the button is clicked.
_KNOWN_KEY_GROUPS = [
    # ── Embedded AI-generation metadata ─────────────────────────────────────
    ("ComfyUI",    [("Prompt",    "prompt"),   ("NegPrompt", "neg_prompt"),
                    ("Seed",      "seed"),      ("Model",     "model"),
                    ("Sampler",   "note"),      ("Steps",     "note"),
                    ("CFG",       "note"),      ("LoRAs",     "note")]),
    ("A1111",      [("Prompt",    "prompt"),   ("NegPrompt", "neg_prompt"),
                    ("Seed",      "seed"),      ("Model",     "model"),
                    ("Sampler",   "note"),      ("Steps",     "note"),
                    ("CFG",       "note")]),
    ("AIX",        [("Prompt",    "prompt"),   ("Seed",      "seed")]),
    # ── Camera / file metadata ───────────────────────────────────────────────
    ("JPEG",       [("Make",         "note"),  ("Model",        "model"),
                    ("DateTime",     "note"),   ("Software",     "note"),
                    ("ISO",          "note"),   ("Aperture",     "note"),
                    ("Shutter",      "note"),   ("Focal length", "note")]),
    ("Image",      [("Dimensions",   "note"),  ("Format",       "note"),
                    ("Mode",         "note")]),
    ("Video",      [("Dimensions",   "note"),  ("Duration",     "note"),
                    ("Audio",        "note")]),
    ("File",       [("File size",    "note"),  ("Fingerprint",  "note")]),
]

# ── Target field list: (display label, target_id) ──
# target_id = plain key for text/seed fields, "tag:X" for tag groups, "code:X" for coded fields
_TARGET_FIELDS = [
    ("── Text / Seed ──",   None),
    ("Positive Prompt",     "prompt"),
    ("Negative Prompt",     "neg_prompt"),
    ("Seed",                "seed"),
    ("Note",                "note"),
    ("Speech",              "speech"),
    ("Model / Custom",      "model"),
    ("── Tags ──",          None),
]


class _MetadataMixin:
    """Mixin: Metadata Mapping tab — bridges raw file/AI metadata (Other) → Attribute fields."""

    # ── Tab builder ────────────────────────────────────────────────────────────

    def _build_metadata_tab(self, tabs):
        import aisearch_attrs as _am

        tab = QWidget()
        vbox = QVBoxLayout(tab)
        vbox.setContentsMargins(15, 10, 15, 10)
        vbox.setSpacing(8)

        def _hsep():
            sep = QWidget(); sep.setFixedHeight(1)
            sep.setStyleSheet("background-color: #555;")
            return sep

        # ── Project / rules-set bar ───────────────────────────────────────────
        proj_bar = QHBoxLayout(); proj_bar.setSpacing(6)
        _lbl_rs = QLabel(_t("Rules Set: / ルールセット："))
        _lbl_rs.setStyleSheet("color:#ddd; font-size:9pt;")
        proj_bar.addWidget(_lbl_rs)
        self._meta_proj_cb = QComboBox()
        self._meta_proj_cb.wheelEvent = lambda e: e.ignore()
        self._meta_proj_cb.setFixedWidth(140)
        _sets = ["default"] + sorted(
            f.replace("features_", "").replace(".pt", "")
            for f in os.listdir(_am.DATA_DIR)
            if f.startswith("features_") and f.endswith(".pt")
        )
        for _s in _sets:
            self._meta_proj_cb.addItem(_s)
        _cur = getattr(self.app, "current_project", "default") or "default"
        _idx = self._meta_proj_cb.findText(_cur)
        if _idx >= 0:
            self._meta_proj_cb.setCurrentIndex(_idx)
        proj_bar.addWidget(self._meta_proj_cb)

        btn_load = QPushButton(_t("Load / 読込"))
        btn_load.setStyleSheet(
            "background:#1e6e1e; color:white; font-weight:bold; padding:3px 8px;")
        proj_bar.addWidget(btn_load)

        self._btn_meta_save = btn_save = QPushButton(_t("💾 Overwrite / 💾 上書き保存"))
        btn_save.setStyleSheet(cfg.btn_ss("btn_write", self.app.config))
        btn_save.clicked.connect(self._save_meta_rules)
        proj_bar.addWidget(btn_save)

        self._meta_editing_lbl = QLabel(_t(f"Editing: {_cur} / 編集中: {_cur}"))
        self._meta_editing_lbl.setStyleSheet("color:#ccc; font-style:italic; font-size:9pt;")
        proj_bar.addWidget(self._meta_editing_lbl)
        proj_bar.addStretch()
        vbox.addLayout(proj_bar)
        vbox.addWidget(_hsep())

        # ── Description ───────────────────────────────────────────────────────
        desc = QLabel(_t(
            "Other  →  Attribute mapping: when 'Read from File' detects a raw metadata key, "
            "write its value to the chosen attribute field. / "
            "その他→属性マッピング：「ファイルから読込」で生メタデータキーを検出したとき、その値を指定した属性フィールドに書き込みます。"
        ))
        desc.setStyleSheet("color:#ccc; font-size:9pt;")
        desc.setWordWrap(True)
        vbox.addWidget(desc)
        vbox.addWidget(_hsep())

        # ── Auto-connected (read-only reference) ──────────────────────────────
        _auto_hdr = QLabel(_t("  Auto-connected (always active, not editable) / 自動接続（常時有効・編集不可）"))
        _auto_hdr.setStyleSheet(
            "color:#aaaaaa; background:#1e1e1e; font-size:8pt; font-weight:bold;"
            " padding:2px 4px; border-radius:2px;")
        vbox.addWidget(_auto_hdr)

        # Targets are the storage keys (the on-disk JSON keys) — same
        # naming used everywhere else after the rename.
        _AUTO_ROWS = [
            # (category_label, category_fg, category_bg, [(source, arrow, target, target_fg), ...])
            ("CLIP",      "#cc99ff", "#2a1a4a", [
                ("E",  "→", "eyes",            "#cc99ff"),
                ("HC", "→", "hair",            "#cc99ff"),
                ("FA", "→", "face_angle",      "#cc99ff"),
                ("SK", "→", "skin",            "#cc99ff"),
                ("PM", "→", "posture_motion",  "#cc99ff"),
                ("CS", "→", "camera_shot",     "#cc99ff"),
                ("BG", "→", "background",      "#cc99ff"),
            ]),
            ("MediaPipe", "#88ffcc", "#1a3a2a", [
                ("Pose", "→", "face_angle",   "#88ffcc"),
                ("Shot", "→", "camera_shot",  "#88ffcc"),
            ]),
            ("File Det.", "#ffcc88", "#3a2a10", [
                ("Audio", "→", "audio",       "#ffcc88"),
                ("Ratio", "→", "orientation", "#ffcc88"),
                ("FPS",   "→", "frame_rate",  "#ffcc88"),
            ]),
        ]

        for cat_lbl, cat_fg, cat_bg, pairs in _AUTO_ROWS:
            row_l = QHBoxLayout(); row_l.setSpacing(4); row_l.setContentsMargins(0, 0, 0, 0)
            _clbl = QLabel(f" {cat_lbl} ")
            _clbl.setStyleSheet(
                f"color:{cat_fg}; background:{cat_bg}; font-size:8pt;"
                " font-weight:bold; padding:1px 4px; border-radius:2px;")
            _clbl.setFixedWidth(68)
            row_l.addWidget(_clbl)
            for src, arr, tgt, tgt_fg in pairs:
                _chip = QLabel(f"{src} {arr} {tgt}")
                _chip.setStyleSheet(
                    f"color:{tgt_fg}; background:#1a1a1a; font-size:8pt;"
                    " padding:1px 6px; border-radius:2px; border:1px solid #333;")
                row_l.addWidget(_chip)
            row_l.addStretch()
            vbox.addLayout(row_l)

        vbox.addWidget(_hsep())

        # ── Quick-add known key buttons — grouped by Database / File / Face ─────
        _grp_lookup = {g: (fg, bg) for _, names, fg, bg in _GROUP_CATEGORIES for g in names}
        _last_cat = None
        for group_name, key_pairs in _KNOWN_KEY_GROUPS:
            # Category separator
            for cat_name, cat_members, cat_fg, cat_bg in _GROUP_CATEGORIES:
                if group_name in cat_members and cat_name != _last_cat:
                    _last_cat = cat_name
                    cat_sep = QLabel(f"  {cat_name}")
                    cat_sep.setStyleSheet(
                        f"color:{cat_fg}; background:{cat_bg}; font-size:8pt;"
                        " font-weight:bold; padding:1px 4px; border-radius:2px;")
                    vbox.addWidget(cat_sep)
                    break

            fg, bg = _grp_lookup.get(group_name, ("#cceeff", "#2a3a4a"))
            hover  = QColor(bg).lighter(130).name()
            row_l = QHBoxLayout(); row_l.setSpacing(4)
            grp_lbl = QLabel(f"{group_name}:")
            grp_lbl.setStyleSheet(f"color:{fg}; font-size:9pt; font-weight:bold;")
            grp_lbl.setFixedWidth(68)
            row_l.addWidget(grp_lbl)
            for src_key, def_tgt in key_pairs:
                btn_k = QPushButton(src_key)
                btn_k.setStyleSheet(
                    f"QPushButton {{ background:{bg}; color:{fg}; border:1px solid {hover};"
                    " padding:3px 7px; font-size:9pt; border-radius:3px; }"
                    f"QPushButton:hover {{ background:{hover}; }}"
                )
                btn_k.clicked.connect(
                    lambda _, k=src_key, t=def_tgt: self._add_meta_row(source=k, target=t))
                row_l.addWidget(btn_k)
            row_l.addStretch()
            vbox.addLayout(row_l)

        vbox.addWidget(_hsep())

        # ── Column headers ─────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        lbl_src = QLabel(_t("Source Key  (from Raw Info / Read from File) / ソースキー（生情報/ファイル読込から）"))
        lbl_src.setStyleSheet("color:#bbb; font-size:9pt; font-weight:bold;")
        hdr.addWidget(lbl_src, stretch=4)
        hdr.addSpacing(20)
        lbl_tgt = QLabel(_t("Attribute Field / 属性フィールド"))
        lbl_tgt.setStyleSheet("color:#bbb; font-size:9pt; font-weight:bold;")
        hdr.addWidget(lbl_tgt, stretch=4)
        hdr.addSpacing(26)   # room for ✕ button
        vbox.addLayout(hdr)

        # ── Scrollable rule rows ───────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        self._meta_rows_widget = QWidget()
        self._meta_rows_layout = QVBoxLayout(self._meta_rows_widget)
        self._meta_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._meta_rows_layout.setSpacing(4)
        self._meta_rows_layout.addStretch()
        scroll.setWidget(self._meta_rows_widget)
        vbox.addWidget(scroll, stretch=1)
        self._meta_scroll = scroll

        # ── Add row button ─────────────────────────────────────────────────────
        btn_add = QPushButton(_t("+ Add Rule / ＋ルール追加"))
        btn_add.setStyleSheet(cfg.btn_ss("btn_add", self.app.config, "padding:5px 12px; font-size:10pt;"))
        btn_add.clicked.connect(lambda: self._add_meta_row())
        vbox.addWidget(btn_add)

        self._meta_rows = []   # list of (src_edit, tgt_cb, row_widget)

        # ── Wire load button ──────────────────────────────────────────────────
        def _do_load():
            proj = self._meta_proj_cb.currentText().strip() or None
            self._meta_editing_lbl.setText(_t(f"Editing: {proj or 'default'} / 編集中: {proj or 'default'}"))
            rules = _am.load_metadata_rules(proj)
            for *_, rw in self._meta_rows:
                rw.deleteLater()
            self._meta_rows.clear()
            for rule in rules:
                self._add_meta_row(rule.get("source", ""), rule.get("target", "prompt"))

        btn_load.clicked.connect(_do_load)

        # Load current project's rules on tab open
        _do_load()

        self._reload_meta_rules = _do_load  # expose so set_project() can call it

        # ── CLIP Label Tuning ─────────────────────────────────────────────────
        _sep2 = QWidget(); _sep2.setFixedHeight(1)
        _sep2.setStyleSheet("background:#555;")
        vbox.addWidget(_sep2)

        _clip_hdr_row = QHBoxLayout()
        _clip_hdr_lbl = QLabel(_t("CLIP Label Tuning / CLIPラベル調整"))
        _clip_hdr_lbl.setStyleSheet("color:#ccaaff; font-weight:bold; font-size:10pt;")
        _clip_hdr_row.addWidget(_clip_hdr_lbl)
        _clip_hdr_row.addStretch()
        btn_clip_save = QPushButton(_t("💾 Save Labels / 💾 ラベル保存"))
        btn_clip_save.setStyleSheet(cfg.btn_ss("btn_write", self.app.config))
        btn_clip_save.clicked.connect(self._save_clip_labels)
        _clip_hdr_row.addWidget(btn_clip_save)
        vbox.addLayout(_clip_hdr_row)

        _clip_scroll = QScrollArea()
        _clip_scroll.setWidgetResizable(True)
        _clip_scroll.setFixedHeight(220)
        _clip_scroll.setStyleSheet("QScrollArea { border: none; }")
        _clip_inner = QWidget()
        self._clip_labels_layout = QVBoxLayout(_clip_inner)
        self._clip_labels_layout.setContentsMargins(0, 0, 0, 0)
        self._clip_labels_layout.setSpacing(6)
        _clip_scroll.setWidget(_clip_inner)
        vbox.addWidget(_clip_scroll)

        self._clip_label_rows = []  # list of (spec_idx, opt_idx, label_edit, thr_spin)
        self._build_clip_label_editor()

        tabs.addTab(tab, _t("🔗 Meta Map / 🔗 メタマップ"))

    # ── Row builder ────────────────────────────────────────────────────────────

    _ROW_COLORS = {
        "prompt": "#1a3a5a", "neg_prompt": "#1a3a5a", "seed": "#1a3a5a",
        "note":   "#1a3a5a", "speech":     "#1a3a5a", "model": "#1a3a5a",
        "person_id": "#2a1a3a",
        "tag:Quality": "#3a2a10", "tag:Source": "#3a2a10", "tag:Variant": "#3a2a10",
    }

    def _row_bg(self, tgt_id):
        if not tgt_id:
            return "#242424"
        if tgt_id in self._ROW_COLORS:
            return self._ROW_COLORS[tgt_id]
        if tgt_id.startswith("tag:"):
            return "#3a2a10"
        return "#242424"

    def _build_target_fields(self):
        """Build target field list from project attrs_tags config + TAG_GROUPS."""
        import aisearch_attrs as _am, json

        proj = getattr(self, '_meta_proj_cb', None)
        proj_name = (proj.currentText() if proj else None) or None
        tags_file = _am.tags_file_for_project(proj_name)
        try:
            with open(tags_file, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            raw = {}

        sec_order       = raw.get("__section_order__", [])
        text_fields_raw = raw.get("__text_fields__", {})

        # Coded field prefixes — combine all available sources
        _coded_prefixes = {cf[0] for cf in raw.get("__coded_fields__", [])}
        try:
            from attribute_manager import FIELD_DEFS as _FD
            _coded_prefixes |= set(_FD.keys())
        except Exception:
            pass
        # Also check global attrs_tags.json coded fields as fallback
        try:
            import aisearch_attrs as _am2
            _global_file = _am2.tags_file_for_project(None)
            if _global_file != tags_file:
                _g = json.load(open(_global_file))
                _coded_prefixes |= {cf[0] for cf in _g.get("__coded_fields__", [])}
        except Exception:
            pass
        # Style-based fallback: 1dig/2dig/3dig/id styles are always coded
        _coded_styles = {"1dig", "2dig", "3dig", "id"}
        sec_styles = raw.get("__section_styles__", {})

        _builtin_text = {"note", "positive_prompt", "negative_prompt",
                         "speech", "prompt", "model", "seed"}

        def _is_coded(key):
            if key in _coded_prefixes:
                return True
            if sec_styles.get(key, "") in _coded_styles:
                return True
            return False

        def _is_text(key):
            return key in _builtin_text or key in text_fields_raw

        fields = list(_TARGET_FIELDS)
        existing_lower = {d.lower() for _, d in fields if d}

        # ── Custom text fields from __text_fields__ ──────────────────────────
        txt_insert = next((i for i, (_, d) in enumerate(fields) if d == "model"),
                          len(fields)) + 1
        for fk, fmeta in text_fields_raw.items():
            if fk in _builtin_text or fk.lower() in existing_lower:
                continue
            fields.insert(txt_insert, (fmeta.get("label") or fk, fk))
            existing_lower.add(fk.lower())
            txt_insert += 1

        # ── Coded fields — let users target hair, background, etc. directly ──
        # Each entry uses the storage key as the data (what gets written to
        # entry[storage_key]) and "<storage>  (<Label>)" as the display, the
        # same format the Filename Rules dropdown uses.
        tags_hdr = next((i for i, (lbl, d) in enumerate(fields)
                         if d is None and "Tags" in lbl), len(fields))
        if "person_id" not in existing_lower:
            fields.insert(tags_hdr, ("person_id  (Person)", "person_id"))
            existing_lower.add("person_id")
            tags_hdr += 1
        # Insert every CODED_FIELDS entry that isn't already in the list,
        # immediately before the "── Tags ──" header.
        for letter, label, digits, *_rest in _am.CODED_FIELDS:
            storage = _rest[0] if _rest else letter.lower()
            if storage in existing_lower or digits == 0:
                continue
            fields.insert(tags_hdr, (f"{storage}  ({label})", storage))
            existing_lower.add(storage)
            tags_hdr += 1

        # ── All non-coded, non-text sections from project section_order ──────
        insert_after = next((i for i, (_, d) in enumerate(fields) if d == "tag:Resolution"),
                            len(fields)) + 1
        for key in sec_order:
            if key.startswith("__") or _is_coded(key) or _is_text(key):
                continue
            data = f"tag:{key}"
            if data.lower() not in existing_lower:
                fields.insert(insert_after, (key, data))
                existing_lower.add(data.lower())
                insert_after += 1

        # ── TAG_GROUPS entries not already covered ────────────────────────────
        for grp, val in _am.TAG_GROUPS.items():
            if grp.startswith("__") or _is_coded(grp) or _is_text(grp):
                continue
            if not isinstance(val, list):
                continue
            data = f"tag:{grp}"
            if data.lower() not in existing_lower:
                fields.insert(insert_after, (grp, data))
                existing_lower.add(data.lower())
                insert_after += 1

        return fields

    def _add_meta_row(self, source="", target="prompt"):
        # If called with a source (from quick-add button) and there's a trailing empty row, fill it
        if source and self._meta_rows:
            last_src, last_tgt, last_rw = self._meta_rows[-1]
            if not last_src.text().strip():
                last_src.setText(source)
                idx = last_tgt.findData(target)
                if idx >= 0:
                    last_tgt.setCurrentIndex(idx)
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(50, lambda: self._meta_scroll.ensureWidgetVisible(last_rw))
                return

        row_w = QWidget()
        row_w.setObjectName("meta_row")
        row_l = QHBoxLayout(row_w)
        row_l.setContentsMargins(4, 2, 4, 2)
        row_l.setSpacing(6)

        src_e = QLineEdit(source)
        src_e.setPlaceholderText(_t("Raw metadata key… / 生メタデータキー…"))
        src_e.setStyleSheet(
            "background:#2a2a2a; color:#f0f0f0; border:1px solid #666;"
            " padding:3px 6px; font-size:10pt;")
        row_l.addWidget(src_e, stretch=4)

        arrow = QLabel("→")
        arrow.setStyleSheet("color:#aaa; font-size:12pt;")
        row_l.addWidget(arrow)

        tgt_cb = QComboBox()
        tgt_cb.wheelEvent = lambda e: e.ignore()
        tgt_cb.setStyleSheet(
            "QComboBox { background:#2a2a2a; color:#f0f0f0; border:1px solid #666; font-size:10pt; }"
            "QComboBox QAbstractItemView { background:#2a2a2a; color:#f0f0f0;"
            "  selection-background-color:#3a5a3a; }")
        _tgt_section_fg  = "#f0f0f0"
        _tgt_section_bg  = "#2a2a2a"
        _dynamic_target_fields = self._build_target_fields()
        for label, data in _dynamic_target_fields:
            if data is None:
                tgt_cb.addItem(label)
                item = tgt_cb.model().item(tgt_cb.count() - 1)
                item.setEnabled(False)
                _sc = _TARGET_SECTION_COLORS.get(label, ("#888", "#333"))
                _tgt_section_fg, _tgt_section_bg = _sc
                item.setForeground(QColor(_tgt_section_fg))
                item.setBackground(QColor(_tgt_section_bg))
                from PyQt6.QtGui import QFont
                _f = item.font(); _f.setBold(True); item.setFont(_f)
            else:
                tgt_cb.addItem(label, data)
                item = tgt_cb.model().item(tgt_cb.count() - 1)
                item.setForeground(QColor(_TARGET_ITEM_COLOR.get(label, _tgt_section_fg)))

        def _update_row_color():
            bg = self._row_bg(tgt_cb.currentData())
            row_w.setStyleSheet(f"QWidget#meta_row {{ background:{bg}; border-radius:3px; }}")

        _orig_show_tgt = tgt_cb.showPopup
        def _tgt_show_popup():
            _orig_show_tgt()
            from PyQt6.QtCore import QItemSelectionModel
            view = tgt_cb.view()
            midx = tgt_cb.model().index(tgt_cb.currentIndex(), 0)
            view.setCurrentIndex(midx)
            view.selectionModel().select(midx, QItemSelectionModel.SelectionFlag.ClearAndSelect)
            view.scrollTo(midx)
        tgt_cb.showPopup = _tgt_show_popup
        tgt_cb.currentIndexChanged.connect(lambda _: _update_row_color())

        idx = tgt_cb.findData(target)
        if idx >= 0:
            tgt_cb.setCurrentIndex(idx)
        _update_row_color()
        row_l.addWidget(tgt_cb, stretch=4)

        btn_del = QPushButton("✕")
        btn_del.setFixedSize(22, 22)
        _rc = cfg.btn_color("btn_remove", self.app.config)
        btn_del.setStyleSheet(
            f"QPushButton {{ background:{_rc}; color:#ff8888; border:none; border-radius:3px; }}"
            "QPushButton:hover { background:#9a2020; }")
        btn_del.clicked.connect(lambda _, rw=row_w: self._remove_meta_row(rw))
        row_l.addWidget(btn_del)

        insert_pos = self._meta_rows_layout.count() - 1  # before the stretch
        self._meta_rows_layout.insertWidget(insert_pos, row_w)
        self._meta_rows.append((src_e, tgt_cb, row_w))

        # Scroll to show the new row after layout settles
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(50, lambda: self._meta_scroll.ensureWidgetVisible(row_w))

    def _remove_meta_row(self, row_w):
        self._meta_rows = [(s, t, r) for s, t, r in self._meta_rows if r is not row_w]
        row_w.deleteLater()

    # ── CLIP label editor ──────────────────────────────────────────────────────

    def _build_clip_label_editor(self):
        import aisearch_attrs as _am
        from PyQt6.QtCore import Qt as _Qt

        # Clear existing
        while self._clip_labels_layout.count():
            w = self._clip_labels_layout.takeAt(0)
            if w.widget(): w.widget().deleteLater()
        self._clip_label_rows.clear()

        # Keys are (long_storage_key, pos). spec["field"] in
        # CLIP_AUTO_DETECT is still the short code ("hc"), so translate
        # via _STORAGE_KEY_MAP at lookup. The dict itself stays in the
        # canonical long form everything else uses.
        _SECTION_NAMES = {
            ("hair", 1): "HC  Hair length", ("hair", 2): "HC  Hair style",
            ("hair", 3): "HC  Hair color",
            ("face_angle", 1): "FA  Face direction",
            ("face_angle", 2): "FA  Face tilt",
            ("skin", 1): "SK  Skin type",
            ("posture_motion", 1): "PM  Motion",
            ("posture_motion", 2): "PM  Posture",
            ("camera_shot", 1): "CS  Lighting",
            ("camera_shot", 2): "CS  Camera angle",
            ("camera_shot", 3): "CS  Shot type",
            ("background", 2): "BG  Background",
            ("expression", 2): "X   Expression",
            ("eyes", 1): "E   Eye color",
            ("clothing", 1): "CL  Bottom type",
            ("clothing", 2): "CL  Bottom color",
            ("clothing", 3): "CL  Top type",
            ("clothing", 4): "CL  Top color",
        }

        for si, spec in enumerate(_am.CLIP_AUTO_DETECT):
            _long = _am._STORAGE_KEY_MAP.get(spec["field"], spec["field"])
            key = (_long, spec["pos"])
            sec_name = _SECTION_NAMES.get(key, f"{_long} pos={spec['pos']}")

            # Section header with threshold spinbox
            hdr = QHBoxLayout(); hdr.setSpacing(6)
            lbl = QLabel(sec_name)
            lbl.setStyleSheet("color:#ccaaff; font-size:8pt; font-weight:bold; min-width:160px;")
            hdr.addWidget(lbl)
            hdr.addWidget(QLabel(_t("thr: / 閾値：")))
            thr_spin = QDoubleSpinBox()
            thr_spin.setRange(0.0, 1.0); thr_spin.setSingleStep(0.01)
            thr_spin.setDecimals(2); thr_spin.setValue(spec.get("threshold", 0.20))
            thr_spin.setFixedWidth(60)
            thr_spin.setStyleSheet(
                "background:#252525; color:#e0e0e0; border:1px solid #555; font-size:8pt;")
            hdr.addWidget(thr_spin)
            hdr.addStretch()
            hdr_w = QWidget(); hdr_w.setLayout(hdr)
            self._clip_labels_layout.addWidget(hdr_w)

            # Option rows
            for oi, (code, label) in enumerate(spec["options"]):
                row = QHBoxLayout(); row.setSpacing(4)
                code_lbl = QLabel(code)
                code_lbl.setStyleSheet("color:#888; font-family:monospace; font-size:8pt;")
                code_lbl.setFixedWidth(18)
                row.addWidget(code_lbl)
                lbl_edit = QLineEdit(label)
                lbl_edit.setStyleSheet(
                    "background:#1e1e2e; color:#ddd; border:1px solid #444;"
                    " font-size:8pt; padding:1px 4px;")
                row.addWidget(lbl_edit)
                row_w = QWidget(); row_w.setLayout(row)
                self._clip_labels_layout.addWidget(row_w)
                self._clip_label_rows.append((si, oi, lbl_edit, thr_spin))

            # Separator
            sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
            sep.setStyleSheet("color:#333;")
            self._clip_labels_layout.addWidget(sep)

        self._clip_labels_layout.addStretch()

    def _save_clip_labels(self):
        import aisearch_attrs as _am
        import copy

        specs = copy.deepcopy(_am.CLIP_AUTO_DETECT)
        # Apply threshold changes (one thr_spin per section header)
        seen_thr = {}  # (si) -> thr_spin already applied
        for si, oi, lbl_edit, thr_spin in self._clip_label_rows:
            if si not in seen_thr:
                specs[si]["threshold"] = round(thr_spin.value(), 4)
                seen_thr[si] = True
            opts = list(specs[si]["options"])
            code = opts[oi][0]
            opts[oi] = (code, lbl_edit.text().strip() or opts[oi][1])
            specs[si]["options"] = opts

        _am.save_clip_labels(specs)
        # Rebuild editor to reflect saved state
        self._build_clip_label_editor()
        if hasattr(self, '_btn_fn_save'):
            self._flash_saved_btn(self._btn_meta_save)

    # ── Save ───────────────────────────────────────────────────────────────────

    def _save_meta_rules(self):
        import aisearch_attrs as _am
        from PyQt6.QtWidgets import QMessageBox, QCheckBox as _QCB
        proj = self._meta_proj_cb.currentText().strip() or None
        if not getattr(self, '_meta_overwrite_skip_warn', False):
            _mb = QMessageBox(self)
            _mb.setIcon(QMessageBox.Icon.Warning)
            _mb.setWindowTitle(_t("Overwrite / 上書き確認"))
            _mb.setText(_t(f"This will overwrite the metadata rules for <b>'{proj or 'default'}'</b>.<br>Continue? / <b>'{proj or 'default'}'</b> のメタデータルールを上書きします。<br>続けますか？"))
            _mb.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            _cb = _QCB(_t("Don't show this warning again / 次回から表示しない"))
            _mb.setCheckBox(_cb)
            if _mb.exec() != QMessageBox.StandardButton.Yes:
                return
            if _cb.isChecked():
                self._meta_overwrite_skip_warn = True
        rules = []
        for src_e, tgt_cb, _ in self._meta_rows:
            src = src_e.text().strip()
            tgt = tgt_cb.currentData()
            if src and tgt:
                rules.append({"source": src, "target": tgt})
        _am.save_metadata_rules(rules, proj)
        self._meta_editing_lbl.setText(_t(f"Editing: {proj or 'default'} ✓ / 編集中: {proj or 'default'} ✓"))
        if hasattr(self, '_btn_meta_save'):
            self._flash_saved_btn(self._btn_meta_save)
