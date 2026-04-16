import os
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                              QLabel, QLineEdit, QCheckBox, QComboBox,
                              QMessageBox, QScrollArea, QListWidget, QListWidgetItem,
                              QDialog, QFormLayout, QDialogButtonBox, QSplitter,
                              QApplication, QStyle)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QDrag, QPixmap, QIcon
from PyQt6.QtCore import QMimeData

import aisearch_config as cfg


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
        import glob as _glob
        proj_bar = QHBoxLayout(); proj_bar.setSpacing(6)
        proj_bar.addWidget(QLabel("Rules Set:"))
        self._fn_proj_cb = QComboBox()
        self._fn_proj_cb.setEditable(True)
        self._fn_proj_cb.setFixedWidth(140)
        self._fn_proj_cb.setPlaceholderText("default")
        _fn_sets = ["default"] + sorted(
            os.path.basename(p).replace("filename_rules_", "").replace(".json", "")
            for p in _glob.glob(os.path.join(os.path.dirname(_am.FILENAME_RULES_FILE),
                                              "filename_rules_*.json"))
        )
        for _s in _fn_sets:
            self._fn_proj_cb.addItem(_s)
        _cur_proj = getattr(self.app, 'current_project', 'default') or 'default'
        _idx = self._fn_proj_cb.findText(_cur_proj)
        if _idx >= 0:
            self._fn_proj_cb.setCurrentIndex(_idx)
        else:
            self._fn_proj_cb.setCurrentText(_cur_proj)
        proj_bar.addWidget(self._fn_proj_cb)

        btn_fn_proj_load = QPushButton("Load")
        btn_fn_proj_load.setStyleSheet("background:#333; color:white; padding:3px 8px;")
        proj_bar.addWidget(btn_fn_proj_load)
        proj_bar.addStretch()

        btn_fn_copy = QPushButton("Copy from Default")
        btn_fn_copy.setStyleSheet("background:#2a3a2a; color:white; padding:3px 8px;")
        proj_bar.addWidget(btn_fn_copy)

        btn_fn_set_default = QPushButton("Set as Default")
        btn_fn_set_default.setStyleSheet("background:#2a2a3a; color:white; padding:3px 8px;")
        proj_bar.addWidget(btn_fn_set_default)
        fn_l.addLayout(proj_bar)
        fn_l.addWidget(_hsep())

        # Auto-rename switch
        _cur_fn_proj = self._fn_proj_cb.currentText().strip() or None
        auto_rename_on = _am.load_filename_config(_cur_fn_proj).get("auto_rename", False)
        self.check_auto_rename = QCheckBox("Auto-rename when attributes change")
        self.check_auto_rename.setChecked(auto_rename_on)
        fn_l.addWidget(self.check_auto_rename)
        fn_l.addWidget(_hsep())

        # Splitter: rules (top) | person registry (bottom)
        _fn_splitter = QSplitter(Qt.Orientation.Vertical)
        _fn_splitter.setChildrenCollapsible(False)
        fn_l.addWidget(_fn_splitter, stretch=1)

        # Container for rule segments — disabled when auto-rename is off
        _rules_container = QWidget()
        _rules_container.setEnabled(auto_rename_on)
        _rc_l = QVBoxLayout(_rules_container)
        _rc_l.setContentsMargins(0, 0, 0, 0)
        _rc_l.setSpacing(8)
        _fn_splitter.addWidget(_rules_container)

        def _fn_selected_proj():
            p = self._fn_proj_cb.currentText().strip()
            return None if (not p or p == "default") else p

        def _on_auto_rename_toggled(v):
            _rules_container.setEnabled(v)
            proj = _fn_selected_proj()
            fn_cfg = _am.load_filename_config(proj)
            fn_cfg["auto_rename"] = v
            _am.save_filename_config(fn_cfg, proj)
            pw = self.app.preview_handler.window
            if pw and hasattr(pw, '_btn_auto_rename'):
                pw._btn_auto_rename.setVisible(v)
            # Keep DB tab checkbox in sync
            db_chk = getattr(self, "chk_rename_on_scan", None)
            if db_chk and db_chk.isChecked() != v:
                db_chk.blockSignals(True)
                db_chk.setChecked(v)
                db_chk.blockSignals(False)
        self.check_auto_rename.toggled.connect(_on_auto_rename_toggled)

        # ── Person Registry ───────────────────────────────────────────────────
        _pr_pane = QWidget()
        _pr_pane_l = QVBoxLayout(_pr_pane)
        _pr_pane_l.setContentsMargins(0, 4, 0, 0)
        _pr_pane_l.setSpacing(4)
        _fn_splitter.addWidget(_pr_pane)
        _fn_splitter.setSizes([400, 150])

        lbl_pr = QLabel("Person Registry  (P field IDs)")
        lbl_pr.setStyleSheet("font-weight: bold;")
        _pr_pane_l.addWidget(lbl_pr)

        pr_container = QWidget()
        pr_l = QVBoxLayout(pr_container)
        pr_l.setContentsMargins(0, 0, 0, 0)
        pr_l.setSpacing(4)
        _pr_pane_l.addWidget(pr_container, stretch=1)

        # List widget showing existing entries with thumbnails
        self._pr_list = QListWidget()
        self._pr_list.setStyleSheet(
            "background:#1e1e1e; color:#e0e0e0; border:1px solid #444;")
        _tsz = getattr(self, 'app', None) and self.app.config.get("face_thumb_size", 96) or 96
        self._pr_list.setIconSize(QSize(_tsz, _tsz))
        self._pr_list.setSpacing(2)
        pr_l.addWidget(self._pr_list)

        def _pr_thumb(path):
            """Return a 64×64 QIcon from image path, or blank icon."""
            try:
                if path and os.path.exists(path):
                    px = QPixmap(path).scaled(
                        _tsz, _tsz,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)
                    return QIcon(px)
            except Exception:
                pass
            return QIcon()

        def _pr_populate():
            self._pr_list.clear()
            project = getattr(self, 'current_project', None)
            db    = _am.load_faces_db(project) if project else {}
            faces = db.get('faces', {})
            reg   = _am.load_person_registry()
            # Show all known IDs from registry + any in faces DB not yet named
            all_ids = sorted(set(reg.keys()) | set(faces.keys()))
            for pid in all_ids:
                desc        = reg.get(pid, "— unnamed —")
                source_path = faces.get(pid, {}).get('source_path', '')
                label       = f"P{pid}  —  {desc}"
                if source_path:
                    label += f"\n{os.path.basename(source_path)}"
                item = QListWidgetItem(_pr_thumb(source_path), label)
                item.setData(Qt.ItemDataRole.UserRole, pid)
                self._pr_list.addItem(item)
        self._pr_populate = _pr_populate

        _pr_populate()

        # Click row → fill edit fields
        def _pr_on_select(item):
            pid = item.data(Qt.ItemDataRole.UserRole)
            reg = _am.load_person_registry()
            self._pr_id_edit.setText(pid)
            self._pr_desc_edit.setText(reg.get(pid, ""))
        self._pr_list.itemClicked.connect(_pr_on_select)

        # Form row: ID + Description + Add/Update + Delete
        pr_form = QHBoxLayout()
        pr_form.setSpacing(6)

        pr_form.addWidget(QLabel("ID:"))
        self._pr_id_edit = QLineEdit()
        self._pr_id_edit.setPlaceholderText("000")
        self._pr_id_edit.setFixedWidth(50)
        self._pr_id_edit.setMaxLength(3)
        pr_form.addWidget(self._pr_id_edit)

        pr_form.addWidget(QLabel("Description:"))
        self._pr_desc_edit = QLineEdit()
        self._pr_desc_edit.setPlaceholderText("e.g. No human/animal")
        pr_form.addWidget(self._pr_desc_edit, stretch=1)

        btn_pr_save = QPushButton("Add / Update")
        btn_pr_save.setStyleSheet(
            "background-color: #1e4a1e; color: white; font-weight: bold; padding: 3px 8px;")
        pr_form.addWidget(btn_pr_save)

        btn_pr_del = QPushButton("Delete")
        btn_pr_del.setStyleSheet(
            "background-color: #4a1e1e; color: white; padding: 3px 8px;")
        pr_form.addWidget(btn_pr_del)

        pr_l.addLayout(pr_form)

        def _pr_add_update():
            pid  = self._pr_id_edit.text().strip().zfill(3)[:3]
            desc = self._pr_desc_edit.text().strip()
            if not pid.isdigit() or not desc:
                return
            reg = _am.load_person_registry()
            reg[pid] = desc
            _am.save_person_registry(reg)
            _pr_populate()
            self._pr_id_edit.clear()
            self._pr_desc_edit.clear()
        btn_pr_save.clicked.connect(_pr_add_update)
        self._pr_id_edit.returnPressed.connect(_pr_add_update)
        self._pr_desc_edit.returnPressed.connect(_pr_add_update)

        def _pr_delete():
            pid = self._pr_id_edit.text().strip().zfill(3)[:3]
            if pid == "000":
                return   # reserved
            reg = _am.load_person_registry()
            reg.pop(pid, None)
            _am.save_person_registry(reg)
            _pr_populate()
            self._pr_id_edit.clear()
            self._pr_desc_edit.clear()
        btn_pr_del.clicked.connect(_pr_delete)

        # ── Attribute helpers ─────────────────────────────────────────────────
        _coded_prefixes = set(_FIELD_DEFS.keys())

        # Tag groups that are user-facing (not coded-field sub-tables or internal keys)
        _tag_groups_flat = [
            grp for grp in _am.TAG_GROUPS
            if not grp.startswith("__")
            and not any(grp == p or grp.startswith(f"{p}_") for p in _coded_prefixes)
            and "_" not in grp
        ]

        _ALL_FIELDS = [("P", "Person", 3)] + list(_am.CODED_FIELDS)

        _FIELD_TAG_GROUP = {
            "E": "E_Color", "HC": "HC_Color", "FA": "FA_Dir",
            "SK": "SK_Type", "B": "B_Size", "WH": "WH_Hip",
            "PM": "PM_Motion", "CS": "CS_Shot", "BG": "BG_Major",
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
                return [(f"{lbl}  ({k})", k) for k, lbl in _am.TAG_GROUPS.get(grp, [])]
            if key == "P":
                reg = _am.load_person_registry()
                return [(f"{desc}  ({pid})", pid) for pid, desc in sorted(reg.items())]
            grp = _FIELD_TAG_GROUP.get(key, "")
            if grp:
                return [(f"{lbl}  ({k})", k) for k, lbl in _am.TAG_GROUPS.get(grp, [])]
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
            pat_e.setPlaceholderText("e.g. -E0a")
            pat_e.setFixedWidth(100)
            pat_e.setStyleSheet(
                "background:#252525; color:#e0e0e0; border:1px solid #444; padding:1px 4px;")

            attr_cb = QComboBox()
            attr_cb.setFixedWidth(180)
            attr_cb.setStyleSheet(
                "background:#2a2a3a; color:#88aaee; border:1px solid #445; padding:1px 3px;")

            # ── Coded Fields group ──
            attr_cb.addItem("── Coded Fields ──", "__hdr__")
            attr_cb.model().item(attr_cb.count() - 1).setEnabled(False)
            for l, lb, _ in _ALL_FIELDS:
                attr_cb.addItem(f"{l}  {lb}", l)
            # ── Tag Groups ──
            if _tag_groups_flat:
                attr_cb.addItem("── Tag Groups ──", "__hdr__")
                attr_cb.model().item(attr_cb.count() - 1).setEnabled(False)
                for grp in _tag_groups_flat:
                    attr_cb.addItem(f"⊕ {grp}", f"TAG:{grp}")

            idx = attr_cb.findData(attr_key)
            if idx >= 0: attr_cb.setCurrentIndex(idx)

            val_cb = QComboBox()
            val_cb.setEditable(True)
            val_cb.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            val_cb.setStyleSheet(
                "background:#252525; color:#e0e0e0; border:1px solid #444; padding:1px 3px;")

            def _refresh_val(key, cur_val=None):
                val_cb.blockSignals(True)
                val_cb.clear()
                if extract:
                    val_cb.setEnabled(False)
                    val_cb.setEditText(value)
                    val_cb.blockSignals(False)
                    return
                if _attr_is_boolean(key):
                    val_cb.setEnabled(False)
                    val_cb.blockSignals(False)
                    return
                val_cb.setEnabled(True)
                if key == "P":
                    reg = _am.load_person_registry()
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
            mode_cb.setFixedWidth(115)
            mode_cb.setStyleSheet(
                "background:#1a2a1a; color:#88dd88; border:1px solid #446644; padding:1px 3px;")
            mode_cb.addItem("→ Detect", "detect")
            mode_cb.addItem("⇄ Sync", "sync")
            mode_cb.setCurrentIndex(0 if one_way else 1)

            def _guard_sync(acb=attr_cb, mcb=mode_cb):
                key = acb.currentData() or ""
                if mcb.currentData() == "sync" and (
                        key.startswith("TAG:") or _attr_is_boolean(key)):
                    mcb.blockSignals(True)
                    mcb.setCurrentIndex(0)
                    mcb.blockSignals(False)
            mode_cb.currentIndexChanged.connect(lambda _: _guard_sync())
            attr_cb.currentIndexChanged.connect(lambda _: _guard_sync())

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
                reg = _am.load_person_registry()
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
                reg[np_] = nd; _am.save_person_registry(reg)
                _refresh_val("P", np_)
                if hasattr(self, '_pr_populate'): self._pr_populate()
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
            btn_del.setStyleSheet(
                "QPushButton { background:#662222; border:1px solid #884444; border-radius:2px; }"
                "QPushButton:hover { background:#882222; }")

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
        for txt, w in [("Pattern", 100), ("Mode", 115), ("Attribute", 180), ("Value", 0)]:
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
                _build_rule_row(
                    rule.get("pattern", ""), f"TAG:{rule['tag_group']}",
                    rule.get("value", ""), True)

        fn_add_row = QHBoxLayout()
        btn_fn_add = QPushButton("+ Add Rule")
        btn_fn_add.setStyleSheet("background:#333; color:white; padding:3px 10px;")
        btn_fn_add.clicked.connect(lambda: _build_rule_row("", "E", "", True))
        fn_add_row.addWidget(btn_fn_add)

        btn_auto = QPushButton("⚡ Auto-Assign")
        btn_auto.setStyleSheet("background:#2a4a2a; color:#aaffaa; padding:3px 10px; font-weight:bold;")
        btn_auto.setToolTip("Pick field letters and auto-generate all value rules")
        fn_add_row.addWidget(btn_auto)
        fn_add_row.addStretch()
        _rc_l.addLayout(fn_add_row)


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

        fn_btn_row = QHBoxLayout()
        btn_fn_save = QPushButton("💾 Save & Apply")
        btn_fn_save.setStyleSheet(
            "background-color: #1e6e64; color: white; font-weight: bold; padding: 4px 12px;")
        btn_fn_save.clicked.connect(self._save_fn_rules)
        fn_btn_row.addStretch()
        fn_btn_row.addWidget(btn_fn_save)
        _rc_l.addLayout(fn_btn_row)

        def _reload_fn_rules():
            """Clear and reload rule rows for the selected project."""
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
            _rules_container.setEnabled(ar)
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
                        rule.get("value", ""), True)
            # Add to combo if new
            p_text = self._fn_proj_cb.currentText().strip()
            if p_text and p_text != "default" and self._fn_proj_cb.findText(p_text) < 0:
                self._fn_proj_cb.addItem(p_text)

        self._reload_fn_rules = _reload_fn_rules  # expose so set_project() can call it
        btn_fn_proj_load.clicked.connect(_reload_fn_rules)

        def _fn_copy_from_default():
            import shutil as _shutil
            p = self._fn_proj_cb.currentText().strip()
            if not p or p == "default": return
            src = _am.FILENAME_RULES_FILE
            dst = os.path.join(os.path.dirname(_am.FILENAME_RULES_FILE),
                               f"filename_rules_{p}.json")
            if os.path.exists(src):
                _shutil.copy2(src, dst)
            if self._fn_proj_cb.findText(p) < 0:
                self._fn_proj_cb.addItem(p)
            _reload_fn_rules()
        btn_fn_copy.clicked.connect(_fn_copy_from_default)

        def _fn_set_as_default():
            import shutil as _shutil
            p = self._fn_proj_cb.currentText().strip()
            if not p or p == "default": return
            ans = QMessageBox.question(self, "Set as Default",
                f"Overwrite default filename rules with '{p}'?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ans != QMessageBox.StandardButton.Yes: return
            src = os.path.join(os.path.dirname(_am.FILENAME_RULES_FILE),
                               f"filename_rules_{p}.json")
            if os.path.exists(src):
                _shutil.copy2(src, _am.FILENAME_RULES_FILE)
            QMessageBox.information(self, "Done", f"Default filename rules updated from '{p}'.")
        btn_fn_set_default.clicked.connect(_fn_set_as_default)

        tabs.addTab(tab_fn, "📁 Filename Rules")

    # --- callbacks ---

    def _save_fn_rules(self):
        import aisearch_attrs as _am
        import re as _re
        rules = []
        for pat_e, attr_cb, val_cb, mode_cb, _ in self._fn_rows:
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
                rules.append({"pattern": pat, "tag_group": attr_key[4:], "value": value})
            else:
                rule = {"pattern": pat, "field": attr_key, "value": value}
                if one_way:
                    rule["one_way"] = True
                rules.append(rule)
        p = getattr(self, '_fn_proj_cb', None)
        proj = (None if (not p or not p.currentText().strip() or p.currentText().strip() == "default")
                else p.currentText().strip())
        _am.save_filename_rules(rules, proj)
        QMessageBox.information(self, "Saved", f"{len(rules)} rule(s) saved.")

    def _do_stop_rename(self):
        self._stop_rename = True

    def _batch_rename(self):
        import aisearch_attrs as _am
        import torch, queue, threading, os as _os
        import aisearch_logic as _logic
        from PyQt6.QtCore import QTimer

        # Use current project's directories only
        project = getattr(getattr(self, 'app', None), 'current_project', None) or ""
        feat_file = f"features_{project}.pt" if project else ""
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
                        for letter, _, digits in _am.CODED_FIELDS:
                            if letter in ("P", "J", "W"):
                                continue
                            val = extracted.get(letter, "")
                            if val:
                                parts[letter.lower()] = val
                        # Auto-detect O, R, K from file
                        for dk, dv in _am.detect_file_attrs(p).items():
                            if dk not in parts:
                                parts[dk] = dv
                        parts["j"] = _am.julian_id_for_file(p)

                        new_stem = _am.build_coded_filename(parts)
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
