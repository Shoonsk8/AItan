import os
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                              QLabel, QLineEdit, QCheckBox, QComboBox,
                              QMessageBox, QScrollArea,
                              QDialog, QFormLayout, QDialogButtonBox,
                              QApplication, QStyle)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QDrag
from PyQt6.QtCore import QMimeData
from attr_viewer import _lang_label as _t

import aisearch_config as cfg


class _ValCombo(QWidget):
    """Value cell for a filename rule row.

    Has two modes:
      * "single" — one editable QComboBox (boolean fields, P, tag groups,
        single-digit coded fields, taglist values, etc.).
      * "multi"  — N small QComboBoxes side by side, one per sub-table of a
        multi-digit coded field (HC = 3 combos for Color / Style / Length).
        currentData/currentText return the digits concatenated in the
        digit-position order so a 3-digit HC value like "012" round-trips.

    Exposes the subset of QComboBox API the rule-row code uses, so the
    caller doesn't have to know which mode is active.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(2)
        self._mode = None
        self._combos = []
        # _digit_positions[i] = the 1-based digit position the i-th combo
        # controls. Position 1 is the rightmost digit. Used to assemble the
        # concatenated value for currentData/currentText.
        self._digit_positions = []
        self.set_single()

    # ── Layout switches ─────────────────────────────────────────────────
    def _clear_combos(self):
        while self._lay.count():
            it = self._lay.takeAt(0)
            w = it.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        self._combos = []
        self._digit_positions = []

    def _new_combo(self, editable):
        cb = QComboBox()
        cb.wheelEvent = lambda e: e.ignore()
        cb.setEditable(editable)
        if editable:
            cb.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        cb.setStyleSheet(
            "background:#252525; color:#e0e0e0; border:1px solid #444; padding:1px 3px;")
        return cb

    def set_single(self):
        if self._mode == "single" and len(self._combos) == 1:
            return
        self._clear_combos()
        cb = self._new_combo(editable=True)
        self._lay.addWidget(cb, stretch=1)
        self._combos = [cb]
        self._digit_positions = []
        self._mode = "single"

    def set_multi(self, sub_specs):
        """sub_specs = [(label, options, position), ...] in display order
        (left → right). options = [(display, key), ...]. position = 1-based
        digit position (1 = rightmost). The combos are rendered in
        descending position so the user reads them left-to-right matching
        the digit order in the stored value."""
        self._clear_combos()
        # Sort by descending position — leftmost combo = highest digit.
        ordered = sorted(sub_specs, key=lambda s: -s[2])
        for lbl, opts, pos in ordered:
            cb = self._new_combo(editable=False)
            cb.setMinimumWidth(110)
            cb.setToolTip(lbl)
            cb.addItem(f"— {lbl} —", "")
            for disp, k in opts:
                cb.addItem(disp, k)
            self._lay.addWidget(cb, stretch=1)
            self._combos.append(cb)
            self._digit_positions.append(pos)
        self._mode = "multi"

    # ── QComboBox-mimic API used by the row builder ────────────────────
    def blockSignals(self, b):
        for c in self._combos:
            c.blockSignals(b)
        return super().blockSignals(b)

    def setEnabled(self, b):
        for c in self._combos:
            c.setEnabled(b)
        super().setEnabled(b)

    @property
    def currentIndexChanged(self):
        # Forward only the first combo's signal — rule-row code uses this
        # to enable/hide the +Person button, only relevant in single mode.
        return self._combos[0].currentIndexChanged

    def clear(self):
        if self._mode == "single":
            self._combos[0].clear()

    def addItem(self, disp, key):
        if self._mode == "single":
            self._combos[0].addItem(disp, key)

    def findData(self, d):
        if self._mode == "single":
            return self._combos[0].findData(d)
        return -1

    def setCurrentIndex(self, i):
        if self._mode == "single":
            self._combos[0].setCurrentIndex(i)

    def setEditText(self, s):
        if self._mode == "single":
            self._combos[0].setEditText(s or "")
            return
        # Distribute the string across digit-position combos. Pad on the
        # left with zeros so a shorter saved value still selects sensibly.
        s = (s or "").strip()
        if not self._digit_positions:
            return
        max_pos = max(self._digit_positions)
        padded = s.zfill(max_pos)[-max_pos:]
        for i, cb in enumerate(self._combos):
            pos = self._digit_positions[i]
            ch = padded[max_pos - pos] if len(padded) >= pos else ""
            idx = cb.findData(ch)
            cb.blockSignals(True)
            cb.setCurrentIndex(idx if idx >= 0 else 0)
            cb.blockSignals(False)

    def currentData(self):
        if self._mode == "single":
            return self._combos[0].currentData()
        # Build value: digit at position p comes from the combo whose
        # _digit_positions entry == p. Use "0" when the combo is on the
        # placeholder.
        if not self._digit_positions:
            return ""
        max_pos = max(self._digit_positions)
        digits = ["0"] * max_pos
        for i, cb in enumerate(self._combos):
            pos = self._digit_positions[i]
            digits[max_pos - pos] = (cb.currentData() or "0")
        joined = "".join(digits)
        # All-zero means "nothing selected" — return empty so save skips it.
        return "" if all(d == "0" for d in joined) else joined

    def currentText(self):
        if self._mode == "single":
            return self._combos[0].currentText()
        return self.currentData()


class _FilenameMixin:
    """Mixin: Filename Rules tab builder + related methods."""

    def _build_filename_tab(self, tabs):
        import aisearch_attrs as _am
        from attribute_manager import FIELD_DEFS as _FIELD_DEFS

        def _hsep():
            sep = QWidget(); sep.setFixedHeight(1)
            sep.setStyleSheet("background-color: #555;")
            return sep

        tab_fn = QWidget()
        fn_l = QVBoxLayout(tab_fn)
        fn_l.setContentsMargins(15, 10, 15, 10)
        fn_l.setSpacing(8)

        # ── Project selector ──────────────────────────────────────────────────
        proj_bar = QHBoxLayout(); proj_bar.setSpacing(6)
        proj_bar.addWidget(QLabel(_t("Rules Set: / ルールセット：")))
        self._fn_proj_cb = QComboBox()
        self._fn_proj_cb.wheelEvent = lambda e: e.ignore()
        self._fn_proj_cb.setFixedWidth(140)
        _fn_sets = ["default"] + sorted(
            f.replace("features_", "").replace(".pt", "")
            for f in os.listdir(_am.DATA_DIR)
            if f.startswith("features_") and f.endswith(".pt")
        )
        for _s in _fn_sets:
            self._fn_proj_cb.addItem(_s)
        _cur_proj = getattr(self.app, 'current_project', 'default') or 'default'
        _idx = self._fn_proj_cb.findText(_cur_proj)
        if _idx >= 0:
            self._fn_proj_cb.setCurrentIndex(_idx)
        proj_bar.addWidget(self._fn_proj_cb)

        btn_fn_proj_load = QPushButton(_t("Load / 読み込み"))
        btn_fn_proj_load.setStyleSheet("background:#1e6e1e; color:white; font-weight:bold; padding:3px 8px;")
        proj_bar.addWidget(btn_fn_proj_load)

        self._btn_fn_save = btn_fn_save_over = QPushButton(_t("💾 Overwrite / 💾 上書き"))
        btn_fn_save_over.setStyleSheet(cfg.btn_ss("btn_write", self.app.config))
        btn_fn_save_over.clicked.connect(self._save_fn_rules)
        proj_bar.addWidget(btn_fn_save_over)
        self._fn_editing_lbl = QLabel(_t(f"Editing: {_cur_proj} / 編集中: {_cur_proj}"))
        self._fn_editing_lbl.setStyleSheet("color:#aaa; font-style:italic;")
        proj_bar.addWidget(self._fn_editing_lbl)
        proj_bar.addStretch()
        fn_l.addLayout(proj_bar)
        fn_l.addWidget(_hsep())

        # The user-facing auto-rename toggle lives on the preview window
        # (next to the 🪪 Rename button). Keep a hidden stub here so the
        # existing handlers / sync code that touches check_auto_rename
        # still works without crashing.
        _cur_fn_proj = self._fn_proj_cb.currentText().strip() or None
        self.check_auto_rename = QCheckBox()
        self.check_auto_rename.setVisible(False)
        # Banner stub for compat with toggle handlers; not shown.
        self._fn_sync_warning = QLabel("")
        self._fn_sync_warning.setVisible(False)

        # Container for rule segments — always editable. The auto_rename flag
        # only controls whether files get RENAMED on attribute change; the
        # rules still drive filename → tag detection and path-rule matching
        # even with auto-rename off, so users must be able to edit them.
        _rules_container = QWidget()
        _rc_l = QVBoxLayout(_rules_container)
        _rc_l.setContentsMargins(0, 0, 0, 0)
        _rc_l.setSpacing(8)
        fn_l.addWidget(_rules_container, stretch=1)

        def _fn_selected_proj():
            p = self._fn_proj_cb.currentText().strip()
            return None if (not p or p == "default") else p

        def _on_auto_rename_toggled(v):
            # Don't gate rule editing on this flag — see comment above.
            if hasattr(self, "_fn_sync_warning"):
                self._fn_sync_warning.setVisible(not v)
            proj = _fn_selected_proj()
            fn_cfg = _am.load_filename_config(proj)
            fn_cfg["auto_rename"] = v
            _am.save_filename_config(fn_cfg, proj)
            pw = self.app.preview_handler.window
            if pw:
                if hasattr(pw, '_btn_auto_rename'):
                    pw._btn_auto_rename.setVisible(v)
                if hasattr(pw, '_chk_auto_rename') and pw._chk_auto_rename.isChecked() != v:
                    pw._chk_auto_rename.blockSignals(True)
                    pw._chk_auto_rename.setChecked(v)
                    pw._chk_auto_rename.blockSignals(False)
            # Keep DB tab checkbox in sync
            db_chk = getattr(self, "chk_rename_on_scan", None)
            if db_chk and db_chk.isChecked() != v:
                db_chk.blockSignals(True)
                db_chk.setChecked(v)
                db_chk.blockSignals(False)
        self.check_auto_rename.toggled.connect(_on_auto_rename_toggled)

        # ── Attribute helpers ─────────────────────────────────────────────────
        _coded_prefixes = set(_FIELD_DEFS.keys()) | {l for l, _, _ in _am.CODED_FIELDS}

        # Load project-specific tag groups so MDL, Audio, etc. appear with correct options
        _proj      = getattr(self.app, 'current_project', None)
        _tags_file = _am.tags_file_for_project(_proj)
        _proj_tg   = _am._load_tag_groups(_tags_file)
        _proj_sec_styles = {}
        _col_names = {}
        try:
            import json as _json
            with open(_tags_file, encoding="utf-8") as _f:
                _proj_raw = _json.load(_f)
            _proj_sec_styles = _proj_raw.get("__section_styles__", {})
            _col_names = _proj_raw.get("__col_names__", {})
        except Exception:
            pass

        def _grp_display(grp):
            """Return a user-friendly display name for a tag group key."""
            prefix = grp[:-6] if grp.endswith("_Table") else grp
            col = _col_names.get(prefix)
            if col:
                return col[0]  # e.g. 'ModelImage' from ['ModelImage']
            return prefix

        # Tag groups that are user-facing (not coded-field sub-tables or internal keys)
        # Include both global and project-specific groups; allow matrix style too
        _tag_like_styles = {"taglist", "boolean", "matrix", "radio"}
        _tag_groups_raw = set(
            grp for grp in list(_am.TAG_GROUPS) + list(_proj_tg)
            if not grp.startswith("__")
            and not any(grp == p or grp.startswith(f"{p}_") for p in _coded_prefixes)
            and (grp in _proj_sec_styles and _proj_sec_styles[grp] in _tag_like_styles
                 or _am.TAG_GROUPS.get(grp) is not None
                 or _proj_tg.get(grp) is not None)
        )
        # Dedupe: when both `Foo` (default) and `Foo_Table` (project)
        # exist, they render with the same display name via _grp_display
        # and produce two identical-looking rows in the dropdown. Prefer
        # the bare form when present and drop the `_Table` sibling.
        _tag_groups_flat = sorted(
            grp for grp in _tag_groups_raw
            if not (grp.endswith("_Table") and grp[:-6] in _tag_groups_raw)
        )

        _ALL_FIELDS = [("P", "Person", 3)] + list(_am.CODED_FIELDS)

        _FIELD_TAG_GROUP = {
            "E": "E_Color", "HC": "HC_Color", "FA": "FA_Dir",
            "SK": "SK_Type", "B": "B_Size", "WH": "WH_Hip",
            "PM": "PM_Motion", "CS": "CS_Shot", "BG": "Background",
            "O": "O_Preset", "R": "R_Preset", "K": "K_Preset",
        }

        def _attr_is_boolean(key):
            if key.startswith("TAG:"): return False
            for l, _, d in _am.CODED_FIELDS:
                if l == key: return d == 0
            return False

        def _tag_options_for(key):
            if key.startswith("TAG:"):
                grp = key[4:]
                opts = (_am.TAG_GROUPS.get(grp) or _proj_tg.get(grp)
                        or _am.TAG_GROUPS.get(grp + "_Table") or _proj_tg.get(grp + "_Table")
                        or [])
                return [(f"{lbl}  ({k})", k) for k, lbl in opts]
            if key == "P":
                _proj = getattr(self.app, 'current_project', None)
                reg = _am.load_person_registry(_proj)
                return [(f"{desc}  ({pid})", pid) for pid, desc in sorted(reg.items())]
            grp = _FIELD_TAG_GROUP.get(key, "")
            if grp:
                opts = _am.TAG_GROUPS.get(grp) or _proj_tg.get(grp) or []
                return [(f"{lbl}  ({k})", k) for k, lbl in opts]
            return []

        def _build_rule_row(pattern, attr_key, value, one_way, extract=False):
            """Build one unified rule row. Entry = (pat_e, attr_cb, val_cb, mode_cb, row_w)."""
            row_w = QWidget()
            row_w.setAcceptDrops(True)
            row_l = QHBoxLayout(row_w)
            row_l.setContentsMargins(0, 0, 0, 0); row_l.setSpacing(4)

            # Drag handle
            handle = QLabel("⠿")
            handle.setStyleSheet("color:#555; font-size:14pt; padding:0 2px;")
            handle.setCursor(Qt.CursorShape.OpenHandCursor)
            _ds = [None]
            def _hp(e, rw=row_w):
                if e.button() == Qt.MouseButton.LeftButton: _ds[0] = e.pos()
            def _hm(e, rw=row_w):
                if not _ds[0]: return
                if (e.pos() - _ds[0]).manhattanLength() < QApplication.startDragDistance(): return
                drag = QDrag(rw); mime = QMimeData()
                mime.setText(f"FNRULE:{id(rw)}"); drag.setMimeData(mime)
                drag.exec(Qt.DropAction.MoveAction); _ds[0] = None
            handle.mousePressEvent = _hp
            handle.mouseMoveEvent  = _hm
            row_l.addWidget(handle)

            def _de(e):
                if e.mimeData().hasText() and e.mimeData().text().startswith("FNRULE:"):
                    e.acceptProposedAction()
            def _drop(e, rw=row_w):
                txt = e.mimeData().text()
                if not txt.startswith("FNRULE:"): return
                src_id = int(txt.split(":", 1)[1])
                src = next((w for _, _, _, _, w in self._fn_rows if id(w) == src_id), None)
                if not src or src is rw: return
                self._fn_grid.removeWidget(src)
                self._fn_grid.insertWidget(self._fn_grid.indexOf(rw), src)
                e.acceptProposedAction()
            row_w.dragEnterEvent = _de
            row_w.dropEvent      = _drop

            pat_e = QLineEdit(pattern)
            pat_e.setPlaceholderText("e.g. nastia/image-########-####.png")
            pat_e.setFixedWidth(200)
            pat_e.setStyleSheet(
                "background:#252525; color:#e0e0e0; border:1px solid #444; padding:1px 4px;")

            attr_cb = QComboBox()
            attr_cb.wheelEvent = lambda e: e.ignore()
            attr_cb.setFixedWidth(180)
            attr_cb.setStyleSheet(
                "background:#2a2a3a; color:#88aaee; border:1px solid #445; padding:1px 3px;")

            # ── Coded Fields group ──
            attr_cb.addItem("── Coded Fields ──", "__hdr__")
            attr_cb.model().item(attr_cb.count() - 1).setEnabled(False)
            for l, lb, d in _ALL_FIELDS:
                disp = lb if d == 0 else f"{l}  {lb}"
                attr_cb.addItem(disp, l)
            # ── Tag Groups ──
            if _tag_groups_flat:
                attr_cb.addItem("── Tag Groups ──", "__hdr__")
                attr_cb.model().item(attr_cb.count() - 1).setEnabled(False)
                for grp in _tag_groups_flat:
                    attr_cb.addItem(f"⊕ {_grp_display(grp)}", f"TAG:{grp}")

            idx = attr_cb.findData(attr_key)
            if idx >= 0: attr_cb.setCurrentIndex(idx)

            val_cb = _ValCombo()

            def _refresh_val(key, cur_val=None):
                # Multi-digit coded fields (HC, E, FA, CS, etc.) need one
                # combo per sub-table. Detect by checking how many sub-tables
                # exist in TAG_GROUPS for this prefix; if more than one, use
                # multi mode so each digit position gets its own picker.
                _coded_digits = 0
                for _l, _, _d in _am.CODED_FIELDS:
                    if _l == key:
                        _coded_digits = _d
                        break
                _sub_specs = []
                if _coded_digits >= 2 and not _attr_is_boolean(key) and not extract:
                    _SUBPOS = {
                        "HC": {"Length": 1, "Style": 2, "Color": 3},
                        "FA": {"Direction": 1, "Vert": 2, "Vertical": 2},
                        "PM": {"Motion": 1, "Posture": 2},
                        "CS": {"Light": 1, "Lighting": 1, "Angle": 2, "Shot": 3},
                        "E":  {"Color": 1, "Additional": 2, "Modifier": 2},
                        "B":  {"Shape": 1, "Size": 2},
                        "WH": {"Hip": 1, "Waist": 2},
                        "SK": {"Type": 1},
                    }
                    _pos_map = _SUBPOS.get(key, {})
                    for _grp_key, _opts in _am.TAG_GROUPS.items():
                        if not _grp_key.startswith(key + "_"):
                            continue
                        if not isinstance(_opts, list) or not _opts:
                            continue
                        _suffix = _grp_key[len(key)+1:]
                        _pos = _pos_map.get(_suffix)
                        if not _pos:
                            continue
                        _opt_pairs = [(f"{lbl}  ({k})", k) for k, lbl in _opts]
                        _sub_specs.append((_suffix, _opt_pairs, _pos))
                if _sub_specs:
                    val_cb.set_multi(_sub_specs)
                    val_cb.setEnabled(True)
                    val_cb.setEditText(cur_val or "")
                    return
                # Single-combo path
                val_cb.set_single()
                val_cb.blockSignals(True)
                val_cb.clear()
                if extract:
                    val_cb.setEnabled(False)
                    val_cb.setEditText(value)
                    val_cb.blockSignals(False)
                    return
                if _attr_is_boolean(key):
                    val_cb.setEnabled(True)
                    val_cb.addItem("False", "false")
                    val_cb.addItem("True",  "true")
                    i = val_cb.findData((cur_val or "false").lower())
                    if i >= 0: val_cb.setCurrentIndex(i)
                    val_cb.blockSignals(False)
                    return
                val_cb.setEnabled(True)
                if key == "P":
                    reg = _am.load_person_registry(getattr(self.app, 'current_project', None))
                    val_cb.addItem("", "")
                    for pid, desc in sorted(reg.items()):
                        val_cb.addItem(f"{pid}  {desc}", pid)
                    i = val_cb.findData(cur_val or "")
                    if i >= 0: val_cb.setCurrentIndex(i)
                    else: val_cb.setEditText(cur_val or "")
                else:
                    opts = _tag_options_for(key)
                    if opts:
                        val_cb.addItem("", "")
                        for disp, k in opts:
                            val_cb.addItem(disp, k)
                        i = val_cb.findData(cur_val or "")
                        if i >= 0: val_cb.setCurrentIndex(i)
                        else: val_cb.setEditText(cur_val or "")
                    else:
                        val_cb.setEditText(cur_val or "")
                val_cb.blockSignals(False)

            _refresh_val(attr_key, value)
            attr_cb.currentIndexChanged.connect(
                lambda _: _refresh_val(attr_cb.currentData() or "", ""))

            mode_cb = QComboBox()
            mode_cb.wheelEvent = lambda e: e.ignore()
            mode_cb.setFixedWidth(115)
            mode_cb.setStyleSheet(
                "background:#1a2a1a; color:#88dd88; border:1px solid #446644; padding:1px 3px;")
            mode_cb.addItem(_t("→ Detect / → 検出"), "detect")
            mode_cb.addItem(_t("⇄ Sync / ⇄ 同期"), "sync")
            mode_cb.setCurrentIndex(0 if one_way else 1)

            mode_cb.currentIndexChanged.connect(lambda _: None)
            attr_cb.currentIndexChanged.connect(lambda _: None)

            btn_plus = QPushButton("+"); btn_plus.setFixedSize(22, 22)
            btn_plus.setToolTip("Add / update Person entry")
            btn_plus.setStyleSheet("color:#88cc88; font-weight:bold; padding:0;")
            btn_plus.setVisible(not extract and attr_cb.currentData() == "P")
            attr_cb.currentIndexChanged.connect(
                lambda _: btn_plus.setVisible(not extract and attr_cb.currentData() == "P"))

            def _on_plus(vcb=val_cb, acb=attr_cb):
                if acb.currentData() != "P": return
                raw = vcb.currentData() or vcb.currentText().strip()
                pid = raw.zfill(3)[:3] if raw else "000"
                _proj = getattr(self.app, 'current_project', None)
                reg = _am.load_person_registry(_proj)
                dlg = QDialog(self); dlg.setWindowTitle("Add / Update Person")
                fl = QFormLayout(dlg); fl.setSpacing(8)
                id_e  = QLineEdit(pid); id_e.setMaxLength(3)
                desc_e = QLineEdit(reg.get(pid, "")); desc_e.setMinimumWidth(200)
                fl.addRow("ID:", id_e); fl.addRow("Description:", desc_e)
                btns = QDialogButtonBox(
                    QDialogButtonBox.StandardButton.Ok |
                    QDialogButtonBox.StandardButton.Cancel)
                btns.accepted.connect(dlg.accept); btns.rejected.connect(dlg.reject)
                fl.addRow(btns)
                if not dlg.exec(): return
                np_, nd = id_e.text().strip().zfill(3)[:3], desc_e.text().strip()
                if not np_.isdigit() or not nd: return
                reg[np_] = nd; _am.save_person_registry(reg, _proj)
                _refresh_val("P", np_)
                # Re-populate all P dropdowns in other rows
                for pe, ac, vc, mc, _ in self._fn_rows:
                    if ac.currentData() == "P" and vc is not val_cb:
                        cur = vc.currentData() or vc.currentText().strip()
                        vc.blockSignals(True); vc.clear()
                        vc.addItem("", "")
                        for pid2, desc2 in sorted(reg.items()):
                            vc.addItem(f"{pid2}  {desc2}", pid2)
                        i2 = vc.findData(cur)
                        if i2 >= 0: vc.setCurrentIndex(i2)
                        else: vc.setEditText(cur)
                        vc.blockSignals(False)
            btn_plus.clicked.connect(_on_plus)

            btn_del = QPushButton()
            btn_del.setIcon(QApplication.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon))
            btn_del.setFixedSize(26, 22)
            _rc = cfg.btn_color("btn_remove", self.app.config)
            btn_del.setStyleSheet(
                f"QPushButton {{ background:{_rc}; border:1px solid #9a4040; border-radius:2px; }}"
                "QPushButton:hover { background:#9a2020; }")

            row_l.addWidget(pat_e)
            row_l.addWidget(mode_cb)
            row_l.addWidget(attr_cb)
            row_l.addWidget(val_cb, stretch=1)
            row_l.addWidget(btn_plus)
            row_l.addWidget(btn_del)

            entry = (pat_e, attr_cb, val_cb, mode_cb, row_w)
            self._fn_rows.append(entry)
            self._fn_grid.addWidget(row_w)

            # Scroll to bottom so dropdowns have room to open downward
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self._fn_scroll.verticalScrollBar().setValue(
                self._fn_scroll.verticalScrollBar().maximum()))

            def _del(w=row_w):
                self._fn_rows[:] = [r for r in self._fn_rows if r[4] is not w]
                w.setParent(None); w.deleteLater()
            btn_del.clicked.connect(lambda _=False, w=row_w: _del(w))
            return entry

        # ── Rules table ───────────────────────────────────────────────────────

        # Column headers
        hdr_row = QHBoxLayout(); hdr_row.setSpacing(4); hdr_row.setContentsMargins(0,0,0,0)
        hdr_row.addSpacing(22)  # drag handle column
        for txt, w in [(_t("Pattern / パターン"), 100), (_t("Mode / モード"), 115), (_t("Attribute / 属性"), 180), (_t("Value / 値"), 0)]:
            lbl = QLabel(txt)
            lbl.setStyleSheet("color:#666; font-size:8pt;")
            if w: lbl.setFixedWidth(w)
            hdr_row.addWidget(lbl, 0 if w else 1)
        hdr_row.addSpacing(48)  # space for + and ✕ buttons
        _rc_l.addLayout(hdr_row)

        self._fn_rows = []
        self._fn_scroll = QScrollArea(); self._fn_scroll.setWidgetResizable(True)
        scroll_fn_inner = QWidget()
        self._fn_grid = QVBoxLayout(scroll_fn_inner)
        self._fn_grid.setSpacing(2); self._fn_grid.setContentsMargins(0, 0, 0, 0)
        self._fn_grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._fn_scroll.setWidget(scroll_fn_inner)
        self._fn_scroll.setMinimumHeight(200)
        _rc_l.addWidget(self._fn_scroll, stretch=1)

        # Load existing rules
        def _pattern_for(field, digits):
            ph = "z" if field == "J" else "f"
            return f"{field}{ph * digits}"

        for rule in _am.load_filename_rules(getattr(self.app, "current_project", None)):
            if rule.get("extract"):
                digits = rule.get("digits", 2)
                pat = _pattern_for(rule["field"], digits)
                ph  = "z" if rule["field"] == "J" else "f"
                _build_rule_row(pat, rule["field"], ph * digits, False, extract=True)
            elif "field" in rule:
                _build_rule_row(
                    rule.get("pattern", ""), rule.get("field", ""),
                    rule.get("value", ""), rule.get("one_way", False))
            elif "tag_group" in rule:
                # Honor the saved one_way flag — was hardcoded to True, which
                # made every tag_group rule display as "Detect" in the Mode
                # dropdown even when the JSON had no one_way (sync).
                _build_rule_row(
                    rule.get("pattern", ""), f"TAG:{rule['tag_group']}",
                    rule.get("value", ""), rule.get("one_way", False))

        fn_add_row = QHBoxLayout()
        btn_fn_add = QPushButton(_t("+ Add Rule / + ルール追加"))
        btn_fn_add.setStyleSheet(cfg.btn_ss("btn_add", self.app.config))
        def _on_add_rule():
            _build_rule_row("", "E", "", True)
        btn_fn_add.clicked.connect(_on_add_rule)
        fn_add_row.addWidget(btn_fn_add)

        btn_auto = QPushButton(_t("⚡ Auto-Assign / ⚡ 自動割り当て"))
        btn_auto.setStyleSheet("background:#2a4a2a; color:#aaffaa; padding:3px 10px; font-weight:bold;")
        btn_auto.setToolTip("Pick field letters and auto-generate all value rules")
        fn_add_row.addWidget(btn_auto)

        btn_reapply = QPushButton(_t("↺ Re-apply Rules / ↺ ルール再適用"))
        btn_reapply.setStyleSheet("background:#2a2a4a; color:#aaaaff; padding:3px 10px;")
        btn_reapply.setToolTip(
            "Re-run filename rules on all existing DB files.\n"
            "Only updates fields the rules explicitly match — all other attrs are untouched.")
        btn_reapply.clicked.connect(self._reapply_fn_rules)
        fn_add_row.addWidget(btn_reapply)

        fn_add_row.addStretch()
        fn_l.addLayout(fn_add_row)  # outside _rules_container so always clickable


        def _auto_assign():
            available = [(l, lb, d) for l, lb, d in _ALL_FIELDS if d > 0]
            existing_pats = {pat_e.text() for pat_e, *_ in self._fn_rows}

            dlg = QDialog(self)
            dlg.setWindowTitle("Auto-Assign Field Mappings")
            dlg.resize(320, 340)
            vl = QVBoxLayout(dlg)

            checks = {}
            scroll = QScrollArea(); scroll.setWidgetResizable(True)
            inner = QWidget(); il = QVBoxLayout(inner); il.setSpacing(4)
            for l, lb, digits in available:
                pat = _pattern_for(l, digits)
                already = pat in existing_pats
                cb = QCheckBox(f"{pat}  ↔  {l} {lb}")
                cb.setChecked(not already)
                cb.setEnabled(not already)
                if already:
                    cb.setText(cb.text() + "  ✓")
                il.addWidget(cb)
                checks[l] = (cb, digits)
            il.addStretch()
            scroll.setWidget(inner)
            vl.addWidget(scroll, stretch=1)

            btns = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            btns.accepted.connect(dlg.accept)
            btns.rejected.connect(dlg.reject)
            vl.addWidget(btns)

            if dlg.exec() != QDialog.DialogCode.Accepted:
                return

            added = 0
            for l, (cb, digits) in checks.items():
                if cb.isChecked() and cb.isEnabled():
                    pat = _pattern_for(l, digits)
                    ph  = "z" if l == "J" else "f"
                    _build_rule_row(pat, l, ph * digits, False, extract=True)
                    added += 1

            if added:
                QMessageBox.information(self, "Auto-Assign",
                    f"Added {added} field mapping(s).\nClick 'Save & Apply' to save.")

        btn_auto.clicked.connect(_auto_assign)

        def _reload_fn_rules():
            """Clear and reload rule rows for the selected project."""
            _loaded = self._fn_proj_cb.currentText().strip() or "default"
            self._fn_editing_lbl.setText(f"Editing: {_loaded}")
            # Re-read the project's attrs_tags_<PROJECT>.json so newly-added
            # values (e.g. a new ModelImage entry) appear in the rule's
            # value dropdown. Was: _proj_tg / _proj_sec_styles / _col_names
            # were captured once at tab init; updates from the Attributes
            # tab were invisible until the app restarted.
            _tags_file_now = _am.tags_file_for_project(_fn_selected_proj())
            print(f"[fn-reload] reading {_tags_file_now}")
            try:
                _new_tg = _am._load_tag_groups(_tags_file_now)
                _proj_tg.clear()
                _proj_tg.update(_new_tg)
                _mi = _proj_tg.get("ModelImage_Table") or _proj_tg.get("ModelImage")
                print(f"[fn-reload] _proj_tg ModelImage entries: "
                      f"{len(_mi) if _mi else 0}")
                import json as _json
                with open(_tags_file_now, encoding="utf-8") as _f:
                    _new_raw = _json.load(_f)
                _proj_sec_styles.clear()
                _proj_sec_styles.update(_new_raw.get("__section_styles__", {}))
                _col_names.clear()
                _col_names.update(_new_raw.get("__col_names__", {}))
            except Exception as _e:
                print(f"[fn-reload] error: {_e}")
            # Clear existing rows
            for _, _, _, _, rw in list(self._fn_rows):
                rw.setParent(None); rw.deleteLater()
            self._fn_rows.clear()
            proj = _fn_selected_proj()
            # Reload auto_rename checkbox
            fn_cfg = _am.load_filename_config(proj)
            ar = fn_cfg.get("auto_rename", False)
            self.check_auto_rename.blockSignals(True)
            self.check_auto_rename.setChecked(ar)
            self.check_auto_rename.blockSignals(False)
            if hasattr(self, "_fn_sync_warning"):
                self._fn_sync_warning.setVisible(not ar)
            # Rules table stays editable regardless of auto_rename flag.
            # Reload rules
            for rule in fn_cfg.get("rules", []):
                if rule.get("extract"):
                    digits = rule.get("digits", 2)
                    pat = _pattern_for(rule["field"], digits)
                    ph  = "z" if rule["field"] == "J" else "f"
                    _build_rule_row(pat, rule["field"], ph * digits, False, extract=True)
                elif "field" in rule:
                    _build_rule_row(
                        rule.get("pattern", ""), rule.get("field", ""),
                        rule.get("value", ""), rule.get("one_way", False))
                elif "tag_group" in rule:
                    _build_rule_row(
                        rule.get("pattern", ""), f"TAG:{rule['tag_group']}",
                        rule.get("value", ""), rule.get("one_way", False))
        self._reload_fn_rules = _reload_fn_rules  # expose so set_project() can call it
        btn_fn_proj_load.clicked.connect(_reload_fn_rules)




        tabs.addTab(tab_fn, _t("📁 Filename Rules / 📁 ファイル名規則"))

    # --- callbacks ---

    def _save_fn_rules(self):
        import aisearch_attrs as _am
        import re as _re
        from PyQt6.QtWidgets import QCheckBox as _QCB

        # Warning dialog (suppressed once user checks "don't show again")
        if not getattr(self, '_fn_overwrite_skip_warn', False):
            _p = getattr(self, '_fn_proj_cb', None)
            _tgt = (_p.currentText().strip() or "default") if _p else "default"
            _mb = QMessageBox(self)
            _mb.setIcon(QMessageBox.Icon.Warning)
            _mb.setWindowTitle("Overwrite")
            _mb.setText(f"This will overwrite the filename rules for <b>'{_tgt}'</b>.<br>Continue?")
            _mb.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            _cb = _QCB("Don't show this warning again")
            _mb.setCheckBox(_cb)
            if _mb.exec() != QMessageBox.StandardButton.Yes:
                return
            if _cb.isChecked():
                self._fn_overwrite_skip_warn = True

        # Build a map so we can iterate in visual (layout) order, not insertion order
        _row_map = {id(rw): (pe, ac, vc, mc)
                    for pe, ac, vc, mc, rw in self._fn_rows}
        _ordered = []
        for i in range(self._fn_grid.count()):
            item = self._fn_grid.itemAt(i)
            w = item.widget() if item else None
            if w and id(w) in _row_map:
                _ordered.append(_row_map[id(w)])

        rules = []
        for pat_e, attr_cb, val_cb, mode_cb in _ordered:
            pat      = pat_e.text().strip()
            attr_key = attr_cb.currentData() or ""
            if not pat or not attr_key or attr_key == "__hdr__":
                continue
            value   = val_cb.currentData() or val_cb.currentText().strip()
            one_way = mode_cb.currentData() == "detect"
            # Detect extract rule: pattern = FIELD + all-f or all-z digits, no value
            _extract_m = _re.fullmatch(
                rf'{_re.escape(attr_key)}([fz]+)', pat, _re.IGNORECASE)
            if _extract_m:
                rules.append({"field": attr_key, "digits": len(_extract_m.group(1)),
                               "extract": True})
            elif attr_key.startswith("TAG:"):
                rule = {"pattern": pat, "tag_group": attr_key[4:], "value": value}
                if one_way:
                    rule["one_way"] = True
                rules.append(rule)
            else:
                rule = {"pattern": pat, "field": attr_key, "value": value}
                if one_way:
                    rule["one_way"] = True
                rules.append(rule)
        # Save to whichever project the combo currently shows
        p = getattr(self, '_fn_proj_cb', None)
        _proj_name = (p.currentText().strip() or "default") if p else "default"
        proj = None if _proj_name == "default" else _proj_name
        _am.save_filename_rules(rules, proj)
        if hasattr(self, '_btn_fn_save'):
            self._flash_saved_btn(self._btn_fn_save)

    def _do_stop_rename(self):
        self._stop_rename = True

    def _batch_rename(self):
        import aisearch_attrs as _am
        import torch, queue, threading, os as _os
        import aisearch_logic as _logic
        from PyQt6.QtCore import QTimer

        # Use current project's directories only
        project = getattr(getattr(self, 'app', None), 'current_project', None) or ""
        feat_file = _os.path.join(_am.DATA_DIR, f"features_{project}.pt") if project else ""
        dirs = []
        if feat_file and _os.path.exists(feat_file):
            try:
                d = torch.load(feat_file, map_location="cpu", weights_only=False)
                dirs += [(bd, False) for bd in d.get("base_dirs", [])]
                dirs += [(bd, True)  for bd in d.get("base_dirs_nosub", [])]
            except Exception:
                pass

        if not dirs:
            QMessageBox.warning(self, "No Directories",
                f"No source directories found for project '{project}'.\n"
                "Select a project in the Database tab first.")
            return

        valid_exts = tuple(ext.lower() for ext in (_logic.EXT_IMG + _logic.EXT_VID))
        rules = _am.load_filename_rules(getattr(self.app, "current_project", None))
        self._stop_rename = False
        self.btn_stop_rename.setEnabled(True)
        self.lbl_rename.setText(f"Collecting files for project '{project}'…")

        _rq = queue.Queue()

        def _worker():
            try:
                # Gather all files
                all_files = []
                for base, nosub in dirs:
                    if not _os.path.isdir(base):
                        continue
                    if nosub:
                        for f in _os.listdir(base):
                            if f.lower().endswith(valid_exts):
                                all_files.append(_os.path.join(base, f))
                    else:
                        for root, _, fs in _os.walk(base):
                            for f in fs:
                                if f.lower().endswith(valid_exts):
                                    all_files.append(_os.path.join(root, f))

                total = len(all_files)
                total_renamed = 0
                for i, p in enumerate(all_files):
                    if self._stop_rename:
                        break
                    _rq.put(("progress", f"{i+1}/{total}: {_os.path.basename(p)}"))
                    try:
                        folder = _os.path.dirname(p)
                        stem   = _os.path.splitext(_os.path.basename(p))[0]
                        ext    = _os.path.splitext(p)[1]

                        extracted = _am.parse_filename_rules(stem, rules)
                        parts = {"persons": [extracted.get("P", "000").zfill(3)]}
                        fname_lower = _os.path.basename(p).lower()
                        for letter, _, digits in _am.CODED_FIELDS:
                            if letter in ("P", "J"):
                                continue
                            if digits == 0:
                                continue  # boolean flags handled below
                            val = extracted.get(letter, "")
                            if val:
                                parts[letter.lower()] = val
                        # Boolean sync rules — check pattern in original filename
                        for rule in rules:
                            if not rule.get("field") or rule.get("one_way") or rule.get("extract"):
                                continue
                            if rule.get("value", "").strip():
                                continue  # value-based rule, not boolean
                            pat = rule.get("pattern", "").lower()
                            fld = rule["field"].upper()
                            if pat and pat in fname_lower:
                                parts[fld.lower()] = fld  # truthy → flag included
                        # Auto-detect O, R, K from file
                        for dk, dv in _am.detect_file_attrs(p).items():
                            if dk not in parts:
                                parts[dk] = dv
                        parts["j"] = _am.julian_id_for_file(p)

                        _fo = _am.get_sync_field_order(getattr(self.app, "current_project", None))
                        new_stem = _am.build_coded_filename(parts, field_order=_fo)
                        new_p = _os.path.join(folder, f"{new_stem}{ext}")
                        if new_p != p:
                            base_new = _os.path.join(folder, new_stem)
                            counter = 1
                            while _os.path.exists(new_p):
                                new_p = f"{base_new}-{counter}{ext}"
                                counter += 1
                            _os.rename(p, new_p)
                            total_renamed += 1
                    except Exception:
                        pass  # skip file on error

                _rq.put(("done", total_renamed))
            except Exception as e:
                _rq.put(("error", str(e)))

        threading.Thread(target=_worker, daemon=True).start()

        self._rename_timer = QTimer(self)
        def _poll():
            while not _rq.empty():
                msg, val = _rq.get_nowait()
                if msg == "progress":
                    self.lbl_rename.setText(val)
                elif msg == "done":
                    self.lbl_rename.setText(f"Done — {val} file(s) renamed.")
                    self.btn_stop_rename.setEnabled(False)
                    self._rename_timer.stop()
                elif msg == "error":
                    self.lbl_rename.setText(f"Error: {val}")
                    self.btn_stop_rename.setEnabled(False)
                    self._rename_timer.stop()
        self._rename_timer.timeout.connect(_poll)
        self._rename_timer.start(150)

    def _reapply_fn_rules(self):
        """Re-run filename rules on all existing DB files.

        Two phases:
          1. Detect — read each filename, apply detect/extract/path-scoped
             rules, write the matched fields into attrs.
          2. Sync (rename) — for files whose entry now disagrees with the
             current filename (because of step 1 OR prior attr edits),
             rename the file on disk via rename_file_to_match_entry.

        Phase 2 is gated by a confirmation that lists how many files would
        be renamed so a stray rule doesn't silently rewrite the library.
        """
        import aisearch_attrs as _am
        app = self.app
        if not app.data or not app.attrs_data:
            QMessageBox.information(self, "Re-apply Rules", "No database loaded.")
            return
        paths = list(app.data.get("paths", []))
        if not paths:
            QMessageBox.information(self, "Re-apply Rules", "No files in database.")
            return
        # Use the project from the rules editor combo when present —
        # otherwise rules saved for project "AIX" but viewed under project
        # "AI" produce zero matches because both rules and paths must come
        # from the same project.
        _editor_proj = ""
        if hasattr(self, "_fn_proj_cb"):
            _editor_proj = (self._fn_proj_cb.currentText() or "").strip()
        _app_proj = getattr(app, "current_project", "") or ""
        if _editor_proj and _editor_proj != "default" and _editor_proj != _app_proj:
            ans = QMessageBox.question(
                self, "Project mismatch",
                f"Rules editor is set to '{_editor_proj}' but the currently "
                f"loaded project is '{_app_proj or '(none)'}'.\n\n"
                f"Rules and files must come from the same project. "
                f"Switch the loaded project to '{_editor_proj}' first?\n\n"
                f"Yes → cancel; switch project, then click Re-apply again.\n"
                f"No → run anyway using rules for '{_app_proj or '(none)'}'.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ans == QMessageBox.StandardButton.Yes:
                return
        proj = _app_proj or None
        # Persist any unsaved rule edits to disk first. Without this, a user
        # who just added a folder rule and clicked Re-apply would see no
        # effect — load_filename_rules reads from JSON, not from the live
        # rule rows. _save_fn_rules pops a confirmation dialog; suppress it
        # by setting the skip flag for this run only.
        try:
            _prev_skip = getattr(self, "_fn_overwrite_skip_warn", False)
            self._fn_overwrite_skip_warn = True
            self._save_fn_rules()
            self._fn_overwrite_skip_warn = _prev_skip
        except Exception:
            pass
        fn_rules = _am.load_filename_rules(proj)
        one_way = [
            r for r in fn_rules
            if r.get("field") and (
                r.get("one_way") or r.get("extract") or '/' in r.get("pattern", "")
            )
        ]
        # ── Re-apply Rules = one Down-arrow press ─────────────────────
        # The user said: clicking this button should do exactly what the
        # Down arrow key does. Apply detect rules to the currently-selected
        # file's entry, then send a real Down-arrow key event to the table.
        # Single click = single step = one row down = auto-rename fires on
        # the file we just left.
        tbl = getattr(app, "table", None)
        if tbl is None or tbl.rowCount() == 0:
            return
        # Apply detect rules to the current row's file BEFORE leaving it.
        sel = tbl.selectionModel().selectedRows()
        cur_row = max((idx.row() for idx in sel), default=tbl.currentRow())
        if cur_row < 0:
            cur_row = 0
        cur_path = tbl.get_row_path(cur_row)
        if cur_path and one_way:
            bn = os.path.basename(cur_path)
            stem = os.path.splitext(bn)[0]
            od = _am.parse_filename_rules(stem, one_way, basename=bn, fullpath=cur_path)
            if od:
                entry = app.attrs_data.setdefault(cur_path, {})
                if "P" in od and od["P"] and entry.get("person_id") != od["P"]:
                    entry["person_id"] = od["P"]
                for field, value in od.items():
                    if field == "P" or not value:
                        continue
                    if entry.get(field.lower()) != value:
                        entry[field.lower()] = value
        # Send the real Down-arrow key event.
        from PyQt6.QtCore import QEvent, Qt
        from PyQt6.QtGui import QKeyEvent
        from PyQt6.QtWidgets import QApplication
        tbl.setFocus()
        QApplication.sendEvent(tbl, QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Down,
            Qt.KeyboardModifier.NoModifier))
        QApplication.sendEvent(tbl, QKeyEvent(
            QEvent.Type.KeyRelease, Qt.Key.Key_Down,
            Qt.KeyboardModifier.NoModifier))

