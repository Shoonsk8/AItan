import os, json
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                              QLabel, QLineEdit, QGroupBox, QComboBox,
                              QMessageBox, QScrollArea, QGridLayout, QApplication)
from PyQt6.QtCore import Qt

import aisearch_attrs as _am_ref
from aisearch_settings_widgets import _WsSec, _WsGroup


class _AttrsMixin:
    """Mixin: Attributes tab builder + all attribute-related methods."""

    def _build_attrs_tab(self, tabs):
        def _hsep():
            sep = QWidget(); sep.setFixedHeight(1)
            sep.setStyleSheet("background-color: #555;")
            return sep

        from attribute_manager import AttributeManager, FIELD_DEFS, _STYLE_PAD as _WSPAD

        tab_attrs = QWidget()
        al = QVBoxLayout(tab_attrs)
        al.setContentsMargins(8, 6, 8, 6)
        al.setSpacing(4)

        # ── Project selector ──────────────────────────────────────────────────
        proj_bar = QHBoxLayout(); proj_bar.setSpacing(6)
        proj_bar.addWidget(QLabel("Attribute Set:"))
        self._attr_proj_cb = QComboBox()
        self._attr_proj_cb.setEditable(True)
        self._attr_proj_cb.setFixedWidth(140)
        self._attr_proj_cb.setPlaceholderText("default")
        # Populate from existing attrs_tags_*.json files
        import glob as _glob
        _existing_sets = ["default"] + sorted(
            os.path.basename(p).replace("attrs_tags_", "").replace(".json", "")
            for p in _glob.glob(os.path.join(os.path.dirname(_am_ref.TAGS_FILE),
                                              "attrs_tags_*.json"))
        )
        for _s in _existing_sets:
            self._attr_proj_cb.addItem(_s)
        # Default to current project if available
        _cur_proj = getattr(self.app, 'current_project', 'default') or 'default'
        _idx = self._attr_proj_cb.findText(_cur_proj)
        if _idx >= 0:
            self._attr_proj_cb.setCurrentIndex(_idx)
        else:
            self._attr_proj_cb.setCurrentText(_cur_proj)
        proj_bar.addWidget(self._attr_proj_cb)

        btn_proj_load = QPushButton("Load")
        btn_proj_load.setStyleSheet("background:#333; color:white; padding:3px 8px;")
        proj_bar.addWidget(btn_proj_load)

        proj_bar.addStretch()

        btn_proj_copy = QPushButton("Copy from Default")
        btn_proj_copy.setStyleSheet("background:#2a3a2a; color:white; padding:3px 8px;")
        proj_bar.addWidget(btn_proj_copy)

        btn_set_default = QPushButton("Set as Default")
        btn_set_default.setStyleSheet("background:#2a2a3a; color:white; padding:3px 8px;")
        proj_bar.addWidget(btn_set_default)
        al.addLayout(proj_bar)
        al.addWidget(_hsep())

        self._attr_current_project = self._attr_proj_cb.currentText().strip() or "default"

        def _tags_file_for_current():
            p = self._attr_proj_cb.currentText().strip() or "default"
            return _am_ref.tags_file_for_project(None if p == "default" else p)

        def _workspace_file_for_current():
            p = self._attr_proj_cb.currentText().strip() or "default"
            return _am_ref.workspace_file_for_project(None if p == "default" else p)

        def _reload_attr_sections():
            """Clear and reload all workspace sections for the selected project."""
            # Clear existing widgets
            while self._attr_aw_vbox.count():
                item = self._attr_aw_vbox.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self._attr_ws_entries.clear()
            self._attr_ws_loaded.clear()
            self._attr_tag_groups.clear()
            self._attr_text_fields.clear()
            self._attr_col_names.clear()
            self._attr_parent_names.clear()
            self._attr_section_styles.clear()
            self._attr_groups.clear()
            # Reload manager for this project
            self._attr_manager = AttributeManager(_workspace_file_for_current())
            if not self._attr_manager.data:
                tf = _tags_file_for_current()
                tg = _am_ref._load_tag_groups(tf)
                self._attr_manager.import_from_tag_groups(tg)
            # Reload sections (reuse logic below via _load_sections())
            _load_sections()

        btn_proj_load.clicked.connect(_reload_attr_sections)

        def _copy_from_default():
            import shutil as _shutil
            p = self._attr_proj_cb.currentText().strip()
            if not p or p == "default":
                return
            if not os.path.exists(_am_ref.TAGS_FILE):
                QMessageBox.warning(self, "Missing Default",
                    f"Default tags file not found:\n{_am_ref.TAGS_FILE}")
                return
            dst_tags = os.path.join(os.path.dirname(_am_ref.TAGS_FILE),
                                    f"attrs_tags_{p}.json")
            dst_ws   = os.path.join(os.path.dirname(_am_ref.TAGS_FILE),
                                    f"attribute_workspace_{p}.json")
            _shutil.copy2(_am_ref.TAGS_FILE, dst_tags)
            ws_src = os.path.join(os.path.dirname(_am_ref.TAGS_FILE),
                                  "attribute_workspace.json")
            if os.path.exists(ws_src):
                _shutil.copy2(ws_src, dst_ws)
            # Add to combo if new
            if self._attr_proj_cb.findText(p) < 0:
                self._attr_proj_cb.addItem(p)
            # Refresh module-level TAG_GROUPS from the newly copied file
            _am_ref.TAG_GROUPS = _am_ref._load_tag_groups(dst_tags)
            _reload_attr_sections()
        btn_proj_copy.clicked.connect(_copy_from_default)

        def _set_as_default():
            import shutil as _shutil
            p = self._attr_proj_cb.currentText().strip()
            if not p or p == "default":
                return
            ans = QMessageBox.question(self, "Set as Default",
                f"Overwrite default attributes with '{p}'?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ans != QMessageBox.StandardButton.Yes:
                return
            src_tags = os.path.join(os.path.dirname(_am_ref.TAGS_FILE),
                                    f"attrs_tags_{p}.json")
            src_ws   = os.path.join(os.path.dirname(_am_ref.TAGS_FILE),
                                    f"attribute_workspace_{p}.json")
            if os.path.exists(src_tags):
                _shutil.copy2(src_tags, _am_ref.TAGS_FILE)
            ws_dst = os.path.join(os.path.dirname(_am_ref.TAGS_FILE),
                                  "attribute_workspace.json")
            if os.path.exists(src_ws):
                _shutil.copy2(src_ws, ws_dst)
            QMessageBox.information(self, "Done", f"Default attributes updated from '{p}'.")
        btn_set_default.clicked.connect(_set_as_default)

        self._attr_manager = AttributeManager(_workspace_file_for_current())
        if not self._attr_manager.data:
            self._attr_manager.import_from_tag_groups(_am_ref.TAG_GROUPS)

        self._attr_ws_entries  = {}   # json_key → {field_name: QLineEdit}
        self._attr_ws_loaded   = set()  # prefixes already added
        self._attr_rows        = []   # kept for backward compat (unused in new UI)
        self._attr_tag_groups  = {}   # grp_name → [(key_edit, lbl_edit, row_w), ...]
        self._attr_text_fields = {}   # field_name → (label_edit, placeholder_edit)
        self._attr_col_names   = {}   # prefix → [QLineEdit, ...]  (per-digit attr names)
        self._attr_parent_names  = {}  # prefix → QLineEdit  (human parent name e.g. "hair")
        self._attr_section_styles = {}  # prefix → style string (for all sections incl. coded)
        self._attr_groups      = {}   # group_name → _WsGroup widget

        # ── Input bar ─────────────────────────────────────────────────────────
        inp_bar = QHBoxLayout(); inp_bar.setSpacing(6)
        inp_bar.addWidget(QLabel("Prefix/Key:"))
        self._attr_key_edit = QLineEdit()
        self._attr_key_edit.setFixedWidth(60)
        self._attr_key_edit.setPlaceholderText("e.g. E")
        inp_bar.addWidget(self._attr_key_edit)

        self._attr_style_cb = QComboBox()
        self._attr_style_cb.addItem("Select style…")
        for _s, _ln in [("1dig","1-digit"), ("2dig","2-digit independent"),
                        ("3dig","3-digit independent"), ("matrix","16×16 matrix"),
                        ("taglist","Tag List  (key · label)"),
                        ("boolean","Boolean  (single flag)"),
                        ("text","Text Field  (prompt / notes)"),
                        ("id","ID  (structural marker)")]:
            self._attr_style_cb.addItem(_ln, _s)
        self._attr_style_cb.setFixedWidth(195)
        inp_bar.addWidget(self._attr_style_cb)

        self._attr_grp_cb = QComboBox()
        self._attr_grp_cb.setEditable(True)
        self._attr_grp_cb.setFixedWidth(120)
        self._attr_grp_cb.setPlaceholderText("(none)")
        self._attr_grp_cb.addItem("")
        for _gname in ["Head", "Body", "BG", "Technical", "Tags", "Text"]:
            self._attr_grp_cb.addItem(_gname)

        btn_add_tbl = QPushButton("Add Table")
        btn_add_tbl.setStyleSheet(
            "background:#2196F3; color:white; font-weight:bold; padding:3px 10px;")
        inp_bar.addWidget(btn_add_tbl)

        inp_bar.addWidget(QLabel("Group:"))
        inp_bar.addWidget(self._attr_grp_cb)

        btn_add_grp = QPushButton("Add Group")
        btn_add_grp.setStyleSheet("background:#2a5a2a; color:white; padding:3px 8px;")
        inp_bar.addWidget(btn_add_grp)
        inp_bar.addStretch()
        al.addLayout(inp_bar)

        scroll_a = QScrollArea(); scroll_a.setWidgetResizable(True)
        scroll_inner = QWidget()
        self._attr_aw_vbox = QVBoxLayout(scroll_inner)
        self._attr_aw_vbox.setSpacing(2); self._attr_aw_vbox.setContentsMargins(0, 0, 0, 0)
        scroll_a.setWidget(scroll_inner)
        al.addWidget(scroll_a, stretch=1)
        aw_vbox = self._attr_aw_vbox  # local alias

        _style_names = {
            "1dig":    "1-digit",
            "2dig":    "2-digit independent",
            "3dig":    "3-digit independent",
            "matrix":  "16×16 matrix",
            "id":      "ID",
            "taglist": "Tag List",
            "boolean": "Boolean",
            "text":    "Text Field",
        }
        _hs  = "color:#f0c040; font-weight:bold; font-size:8pt; padding:0 4px;"
        _rxs = "color:#6ea6f0; font-family:monospace; padding:0 6px;"
        _les = "background:#252525; color:#e0e0e0; border:1px solid #444; padding:1px 4px;"

        # Generic cols for custom (non-FIELD_DEFS) prefixes
        _generic_cols = {
            "1dig":   [("1st digit",  "digit_1st",  None)],
            "2dig":   [("2nd digit",  "digit_2nd",  None), ("1st digit", "digit_1st", None)],
            "3dig":   [("3rd digit",  "digit_3rd",  None), ("2nd digit", "digit_2nd", None),
                       ("1st digit",  "digit_1st",  None)],
            "matrix": [],
        }

        def _build_ws_group(name, append=False):
            """Create a parent group widget and add it to the main vbox."""
            if name in self._attr_groups:
                return self._attr_groups[name]
            grp = _WsGroup(name)
            if append:
                self._attr_aw_vbox.addWidget(grp)
            else:
                self._attr_aw_vbox.insertWidget(0, grp)
            self._attr_groups[name] = grp
            # Update group combo
            if self._attr_grp_cb.findText(name) < 0:
                self._attr_grp_cb.addItem(name)

            def _on_del_group(g=grp, n=name):
                # Move children back to main vbox, then remove group
                for sec in list(g.sections()):
                    self._attr_aw_vbox.addWidget(sec)
                self._attr_groups.pop(n, None)
                g.setParent(None); g.deleteLater()
            grp._del_btn.clicked.connect(_on_del_group)
            return grp

        def _build_ws_section(prefix, force_style=None, group=None, text_label=None, text_placeholder=None):
            if prefix in self._attr_ws_loaded:
                return
            # Resolve style + cols
            if force_style:
                style = force_style
                if prefix in FIELD_DEFS and FIELD_DEFS[prefix][0] == style:
                    cols = FIELD_DEFS[prefix][1]
                else:
                    cols = _generic_cols.get(style, [])
            elif prefix in FIELD_DEFS:
                style, cols = FIELD_DEFS[prefix]
            else:
                return
            self._attr_ws_loaded.add(prefix)
            self._attr_section_styles[prefix] = style
            pad = _WSPAD.get(style, 2)

            def _add_to_target(widget):
                if group:
                    grp_w = _build_ws_group(group)
                    grp_w.add_section(widget)
                else:
                    self._attr_aw_vbox.addWidget(widget)

            if style == "id":
                sec = _WsSec(f"Key = {prefix}   │   ID  (structural marker)", prefix=prefix)
                _add_to_target(sec)
                if prefix == "J":
                    msg = "  Julian date ID — 4-char base-36, auto-stamped by scan  (not editable)"
                else:
                    msg = f"  Structural ID marker — {prefix}  (no editable data)"
                blank_lbl = QLabel(msg)
                blank_lbl.setStyleSheet("color:#666; font-style:italic; padding:8px;")
                bl = QVBoxLayout(sec.content); bl.addWidget(blank_lbl)
                def _on_del_id(checked=False, pfx=prefix, s=sec):
                    self._attr_ws_loaded.discard(pfx)
                    s.setParent(None); s.deleteLater()
                sec._del_btn.clicked.connect(_on_del_id)
                return

            sec = _WsSec(f"Key = {prefix}   │   {_style_names.get(style, style)}", prefix=prefix)
            _add_to_target(sec)

            # ── Delete button: works even if content was never expanded ───
            def _on_del_any(checked=False, pfx=prefix, s=sec):
                s._build_cb = None   # prevent deferred build after delete
                self._attr_ws_loaded.discard(pfx)
                for _key in [k for k in self._attr_ws_entries if k.startswith(pfx)]:
                    self._attr_ws_entries.pop(_key, None)
                self._attr_tag_groups.pop(pfx, None)
                self._attr_text_fields.pop(pfx, None)
                self._attr_col_names.pop(pfx, None)
                self._attr_parent_names.pop(pfx, None)
                s.setParent(None); s.deleteLater()
            sec._del_btn.clicked.connect(_on_del_any)

            # ── Defer content build to first expand via _build_cb ─────────
            def _build_content(s=sec, pfx=prefix, sty=style, c=cols, p=pad,
                                tl=text_label, tp=text_placeholder):
                if sty == "boolean":
                    # ── Boolean — single flag key + label ─────────────────
                    bl_lay = QVBoxLayout(s.content)
                    bl_lay.setContentsMargins(8, 4, 8, 6); bl_lay.setSpacing(2)
                    self._attr_tag_groups[pfx] = []
                    existing_pairs = _am_ref.TAG_GROUPS.get(pfx, [])
                    pair0 = existing_pairs[0] if existing_pairs else ["", ""]
                    rw = QWidget()
                    rl2 = QHBoxLayout(rw); rl2.setContentsMargins(0,0,0,0); rl2.setSpacing(6)
                    rl2.addWidget(QLabel("Key:", styleSheet="color:#888; font-size:8pt;"))
                    k_e = QLineEdit(pair0[0]); k_e.setFixedWidth(110); k_e.setStyleSheet(_les)
                    k_e.setPlaceholderText("tag_key")
                    rl2.addWidget(k_e)
                    rl2.addWidget(QLabel("Label:", styleSheet="color:#888; font-size:8pt;"))
                    l_e = QLineEdit(pair0[1]); l_e.setMinimumWidth(120); l_e.setStyleSheet(_les)
                    l_e.setPlaceholderText("Display Name")
                    rl2.addWidget(l_e, stretch=1)
                    flag_note = QLabel("  ☑ boolean flag")
                    flag_note.setStyleSheet("color:#6ea6f0; font-size:8pt;")
                    rl2.addWidget(flag_note)
                    bl_lay.addWidget(rw)
                    self._attr_tag_groups[pfx].append((k_e, l_e, rw))

                elif sty == "text":
                    # ── Text field (prompt / notes / seed …) ──────────────
                    tx_lay = QVBoxLayout(s.content)
                    tx_lay.setContentsMargins(8, 4, 8, 6); tx_lay.setSpacing(4)
                    key_row = QHBoxLayout(); key_row.setSpacing(6)
                    key_row.addWidget(QLabel("Key:", styleSheet="color:#888; font-size:8pt;"))
                    key_e = QLineEdit(pfx)
                    key_e.setStyleSheet(_les); key_e.setMinimumWidth(160)
                    key_e.setPlaceholderText("JSON field key (e.g. positive_prompt)")
                    key_e.setAcceptDrops(False)
                    key_row.addWidget(key_e, stretch=1)
                    tx_lay.addLayout(key_row)
                    key_e.textChanged.connect(
                        lambda txt, _s=s: _s._title_lbl.setText(
                            f"Key = {txt or _s.prefix}   │   Text Field"))
                    lbl_row = QHBoxLayout(); lbl_row.setSpacing(6)
                    lbl_row.addWidget(QLabel("Label:", styleSheet="color:#888; font-size:8pt;"))
                    lbl_e = QLineEdit()
                    lbl_e.setStyleSheet(_les); lbl_e.setMinimumWidth(160)
                    lbl_e.setPlaceholderText("Display name (e.g. Prompt)")
                    lbl_e.setAcceptDrops(False)
                    _known_labels = {
                        "prompt": "Positive Prompt", "neg_prompt": "Negative Prompt",
                        "speech": "Speech / Description", "seed": "Seed",
                        "note": "Note", "project": "Title", "scene": "Scene",
                    }
                    lbl_e.setText(tl or _known_labels.get(pfx.lower(), pfx))
                    lbl_row.addWidget(lbl_e, stretch=1)
                    tx_lay.addLayout(lbl_row)
                    ph_row = QHBoxLayout(); ph_row.setSpacing(6)
                    ph_row.addWidget(QLabel("Hint:", styleSheet="color:#888; font-size:8pt;"))
                    ph_e = QLineEdit()
                    ph_e.setStyleSheet(_les); ph_e.setMinimumWidth(160)
                    ph_e.setPlaceholderText("Placeholder hint shown in input box")
                    ph_e.setAcceptDrops(False)
                    if tp:
                        ph_e.setText(tp)
                    ph_row.addWidget(ph_e, stretch=1)
                    tx_lay.addLayout(ph_row)
                    type_note = QLabel("  Ⓣ multi-line text input")
                    type_note.setStyleSheet("color:#88cc88; font-size:8pt;")
                    tx_lay.addWidget(type_note)
                    self._attr_text_fields[pfx] = (lbl_e, ph_e, key_e)

                elif sty == "taglist":
                    # ── Tag list — arbitrary key=label rows ───────────────
                    row_lay = QVBoxLayout(s.content)
                    row_lay.setContentsMargins(6, 3, 6, 5); row_lay.setSpacing(2)
                    self._attr_tag_groups[pfx] = []

                    def _make_tag_row(grp=pfx, rl=row_lay, k_val="", l_val=""):
                        rw = QWidget()
                        rl2 = QHBoxLayout(rw); rl2.setContentsMargins(0, 0, 0, 0); rl2.setSpacing(4)
                        rl2.addWidget(QLabel("Key:", styleSheet="color:#888; font-size:8pt;"))
                        k_e = QLineEdit(k_val); k_e.setFixedWidth(90); k_e.setStyleSheet(_les)
                        k_e.setPlaceholderText("tag_key")
                        rl2.addWidget(k_e)
                        rl2.addWidget(QLabel("Label:", styleSheet="color:#888; font-size:8pt;"))
                        l_e = QLineEdit(l_val); l_e.setMinimumWidth(120); l_e.setStyleSheet(_les)
                        l_e.setPlaceholderText("Display Name")
                        rl2.addWidget(l_e, stretch=1)
                        btn_x = QPushButton("✕"); btn_x.setFixedWidth(26)
                        btn_x.setStyleSheet("background:transparent; color:#884444; border:none; font-size:10pt;")
                        rl2.addWidget(btn_x)
                        rl.addWidget(rw)
                        entry = (k_e, l_e, rw)
                        self._attr_tag_groups[grp].append(entry)
                        def _del_row(checked=False, e=entry, g=grp):
                            self._attr_tag_groups[g].remove(e)
                            e[2].setParent(None); e[2].deleteLater()
                        btn_x.clicked.connect(_del_row)
                        return entry

                    existing_pairs = _am_ref.TAG_GROUPS.get(pfx, [])
                    for _k, _l in existing_pairs:
                        _make_tag_row(k_val=_k, l_val=_l)
                    if not existing_pairs:
                        _make_tag_row()
                    btn_add_row = QPushButton("+ Add")
                    btn_add_row.setFixedWidth(60)
                    btn_add_row.setStyleSheet("background:#2a4a2a; color:#e0e0e0; border:none; border-radius:2px;")
                    btn_add_row.clicked.connect(lambda checked=False, g=pfx: _make_tag_row(grp=g))
                    add_h = QHBoxLayout(); add_h.addWidget(btn_add_row); add_h.addStretch()
                    row_lay.addLayout(add_h)

                else:
                    # ── Hex grid / Matrix ─────────────────────────────────
                    cl = QVBoxLayout(s.content)
                    cl.setContentsMargins(4, 4, 4, 4); cl.setSpacing(4)
                    if sty == "matrix":
                        _mx_row = QHBoxLayout(); _mx_row.setSpacing(6)
                        _mx_row.addWidget(QLabel("Name:", styleSheet="color:#888; font-size:8pt;"))
                        _mx_name_e = QLineEdit()
                        _mx_name_e.setFixedWidth(180); _mx_name_e.setStyleSheet(_les)
                        _mx_name_e.setPlaceholderText("e.g. Expression")
                        _saved_mx = _am_ref.TAG_GROUPS.get("__col_names__", {}).get(pfx, [""])
                        _mx_name_e.setText(_saved_mx[0] if _saved_mx else pfx.lower())
                        _mx_row.addWidget(_mx_name_e); _mx_row.addStretch()
                        cl.addLayout(_mx_row)
                        self._attr_col_names[pfx] = [_mx_name_e]
                    grid_w = QWidget()
                    gl = QGridLayout(grid_w)
                    gl.setContentsMargins(0, 0, 0, 0); gl.setSpacing(2)
                    cl.addWidget(grid_w)
                    if sty == "matrix":
                        gl.addWidget(QLabel(""), 0, 0)
                        for _c in range(16):
                            h_lbl = QLabel(hex(_c)[2:])
                            h_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                            h_lbl.setStyleSheet(_hs)
                            gl.addWidget(h_lbl, 0, _c + 1)
                        for _r in range(16):
                            r_h = hex(_r)[2:]
                            _rl = QLabel(r_h); _rl.setStyleSheet(_rxs)
                            gl.addWidget(_rl, _r + 1, 0)
                            for _c in range(16):
                                c_h = hex(_c)[2:]
                                json_key = f"{pfx}{c_h}{r_h}"
                                stored = self._attr_manager.data.get(json_key, {})
                                le = QLineEdit()
                                le.setFixedSize(64, 20)
                                le.setStyleSheet(_les + " font-size:8pt;")
                                le.setText(stored.get("expression", ""))
                                gl.addWidget(le, _r + 1, _c + 1)
                                self._attr_ws_entries[json_key] = {"expression": le}
                    else:
                        _saved_parent = _am_ref.TAG_GROUPS.get(
                            "__parent_names__", {}).get(pfx, pfx.lower())
                        _pr_row = QHBoxLayout(); _pr_row.setSpacing(6)
                        _pr_row.addWidget(QLabel("Parent:", styleSheet="color:#888; font-size:8pt;"))
                        _parent_e = QLineEdit(_saved_parent)
                        _parent_e.setFixedWidth(140); _parent_e.setStyleSheet(_les)
                        _parent_e.setPlaceholderText("e.g. hair")
                        _pr_row.addWidget(_parent_e); _pr_row.addStretch()
                        cl.addLayout(_pr_row)
                        self._attr_parent_names[pfx] = _parent_e
                        _hdr_ss = ("background:#1a2a1a; color:#f0c040; font-weight:bold; "
                                   "border:1px solid #446644; padding:1px 4px; font-size:8pt;")
                        col_name_edits = []
                        _saved_cols = _am_ref.TAG_GROUPS.get("__col_names__", {}).get(pfx, [])
                        _col_bases = []
                        for ci, (col_lbl, _jf, _tg) in enumerate(c):
                            _base = col_lbl.split()[0].rstrip("s").lower()
                            _col_bases.append(_base)
                            saved_name = _saved_cols[ci] if ci < len(_saved_cols) else ""
                            default_name = f"{_saved_parent}_{_base}"
                            col_e = QLineEdit(saved_name or default_name)
                            col_e.setFixedHeight(22); col_e.setMinimumWidth(110)
                            col_e.setStyleSheet(_hdr_ss)
                            col_e.setPlaceholderText("Attr name")
                            gl.addWidget(col_e, 0, ci + 1)
                            col_name_edits.append(col_e)
                        self._attr_col_names[pfx] = col_name_edits
                        def _on_parent_change(text, edits=col_name_edits, bases=_col_bases, _pfx=pfx):
                            _p = text.strip() or _pfx.lower()
                            for col_e, base in zip(edits, bases):
                                col_e.setText(f"{_p}_{base}")
                        _parent_e.textChanged.connect(_on_parent_change)
                        _B36 = "0123456789abcdefghijklmnopqrstuvwxyz"
                        for i, ch in enumerate(_B36):
                            row_key = f"{pfx}{'0' * (p - 1)}{ch}"
                            stored = self._attr_manager.data.get(row_key, {})
                            _rl = QLabel(ch); _rl.setStyleSheet(_rxs)
                            gl.addWidget(_rl, i + 1, 0)
                            row_wids = {}
                            for ci, (col_lbl, json_field, _tg) in enumerate(c):
                                le = QLineEdit()
                                le.setMinimumWidth(130); le.setFixedHeight(22)
                                le.setStyleSheet(_les)
                                le.setText(stored.get(json_field, ""))
                                gl.addWidget(le, i + 1, ci + 1)
                                row_wids[json_field] = le
                            self._attr_ws_entries[row_key] = row_wids

            sec._build_cb = _build_content

        def _load_sections():
            """Load all workspace sections from the current project's tags file."""
            _tags_raw = {}
            try:
                with open(_tags_file_for_current(), "r", encoding="utf-8") as _f:
                    _tags_raw = json.load(_f)
            except Exception:
                pass

            _section_order  = _tags_raw.get("__section_order__", [])
            _section_styles = _tags_raw.get("__section_styles__", {})
            # group_map: prefix → group_name (or None)
            _group_map = {}
            _saved_groups = _tags_raw.get("__section_groups__", {})
            for _gname, _members in _saved_groups.items():
                for _m in _members:
                    _group_map[_m] = _gname
            # Pre-create all saved groups in saved order (including empty ones)
            _group_order = _tags_raw.get("__group_order__", list(_saved_groups.keys()))
            for _gname in _group_order:
                _build_ws_group(_gname, append=True)

            if not _section_order:
                _sub_prefixes = {
                    p for p in _tags_raw
                    if p.startswith("__") or any(
                        p.startswith(f"{fd}_") for fd in FIELD_DEFS
                    )
                }
                _section_order = list(FIELD_DEFS.keys())
                for _k in _tags_raw:
                    if _k not in _sub_prefixes and _k not in _section_order:
                        _section_order.append(_k)

            _tf_data_map = _tags_raw.get("__text_fields__", {})
            for _pfx in _section_order:
                if _pfx.startswith("__"):
                    continue
                _fstyle = _section_styles.get(_pfx)
                if not _fstyle:
                    if _pfx in FIELD_DEFS:
                        _fstyle = None
                    elif isinstance(_tags_raw.get(_pfx), list):
                        _pairs = _tags_raw[_pfx]
                        _fstyle = "boolean" if len(_pairs) <= 1 else "taglist"
                    else:
                        continue
                _tf_meta = _tf_data_map.get(_pfx, {})
                _saved_lbl = _tf_meta.get("label", "") if isinstance(_tf_meta, dict) else ""
                _saved_ph  = _tf_meta.get("placeholder", "") if isinstance(_tf_meta, dict) else ""
                _build_ws_section(_pfx, force_style=_fstyle,
                                  group=_group_map.get(_pfx),
                                  text_label=_saved_lbl, text_placeholder=_saved_ph)

            tf_data = _tags_raw.get("__text_fields__", {})
            if isinstance(tf_data, dict):
                for _tf, _tf_meta in tf_data.items():
                    _saved_lbl = _tf_meta.get("label", "") if isinstance(_tf_meta, dict) else ""
                    _saved_ph  = _tf_meta.get("placeholder", "") if isinstance(_tf_meta, dict) else ""
                    _build_ws_section(_tf, force_style="text",
                                      group=_group_map.get(_tf),
                                      text_label=_saved_lbl, text_placeholder=_saved_ph)

        _load_sections()

        def _on_add_group():
            name = self._attr_grp_cb.currentText().strip()
            if not name:
                QMessageBox.warning(self, "Input Error", "Enter a group name.")
                return
            _build_ws_group(name)

        btn_add_grp.clicked.connect(_on_add_group)

        def _on_add_table():
            style  = self._attr_style_cb.currentData()
            raw    = self._attr_key_edit.text().strip()
            prefix = raw if style == "text" else raw.upper()
            if not prefix or (style != "text" and len(prefix) > 3):
                QMessageBox.warning(self, "Input Error", "Enter 1–3 character prefix.")
                return
            if not style:
                QMessageBox.warning(self, "Input Error", "Select a style.")
                return
            if prefix in self._attr_ws_loaded:
                QMessageBox.information(self, "Info", f"'{prefix}' already loaded.")
                return
            _build_ws_section(prefix, force_style=style, group=None)
            self._attr_key_edit.clear()

        btn_add_tbl.clicked.connect(_on_add_table)
        aw_vbox.addStretch()

        btn_row = QHBoxLayout()
        btn_save = QPushButton("💾 Save & Apply Sequence")
        btn_save.setStyleSheet("background-color: #1e6e64; color: white; font-weight: bold; padding: 4px 12px;")
        btn_save.clicked.connect(self._save_attr_groups)
        btn_row.addStretch()
        btn_row.addWidget(btn_save)
        al.addLayout(btn_row)

        tabs.addTab(tab_attrs, "🏷 Attributes")

    # --- callbacks ---

    def _remove_attr_row(self, entry):
        name_e, tags_e, row_w = entry
        self._attr_rows.remove(entry)
        row_w.setParent(None)
        row_w.deleteLater()

    def _reset_attr_defaults(self):
        import aisearch_attrs as attrs_mod
        # Re-seed hex workspace entries from defaults
        self._attr_manager.import_from_tag_groups(attrs_mod._DEFAULT_TAG_GROUPS)
        for key, wids in self._attr_ws_entries.items():
            stored = self._attr_manager.data.get(key, {})
            for field, le in wids.items():
                le.setText(stored.get(field, ""))
        # Re-seed taglist/boolean groups from defaults
        for grp, rows in self._attr_tag_groups.items():
            defaults = attrs_mod._DEFAULT_TAG_GROUPS.get(grp, [])
            # Clear then repopulate existing widgets (up to available rows)
            for i, (k_e, l_e, _) in enumerate(rows):
                if i < len(defaults):
                    k_e.setText(defaults[i][0])
                    l_e.setText(defaults[i][1])
                else:
                    k_e.setText(""); l_e.setText("")

    def _save_attr_groups(self):
        import aisearch_attrs as attrs_mod
        from aisearch_settings_widgets import _WsSec as _WsSecCls

        # 0. Force-build any sections that were never expanded, so their data
        #    lands in _attr_ws_entries / _attr_tag_groups / _attr_text_fields
        #    before we read those dicts below.
        _scroll_inner = self._attr_aw_vbox.parentWidget()
        if _scroll_inner:
            for _sec in _scroll_inner.findChildren(_WsSecCls):
                if _sec._build_cb is not None:
                    _cb = _sec._build_cb
                    _sec._build_cb = None
                    _cb()

        # 1. Build prefix → save_key map (text fields may have been renamed)
        def _save_key(pfx):
            if pfx in self._attr_text_fields:
                tf_tuple = self._attr_text_fields[pfx]
                key_e = tf_tuple[2] if len(tf_tuple) > 2 else None
                new_key = key_e.text().strip() if key_e and key_e.text().strip() else pfx
                return new_key
            return pfx

        # Capture visual order — walk main vbox + group children
        # Keep original prefixes here; apply _save_key only when writing JSON keys
        ordered_prefixes = []
        section_groups   = {}   # group_name → [save_key, ...]
        for i in range(self._attr_aw_vbox.count()):
            item = self._attr_aw_vbox.itemAt(i)
            if not item or not item.widget(): continue
            w = item.widget()
            if isinstance(w, _WsSec):
                ordered_prefixes.append(w.prefix)
            elif isinstance(w, _WsGroup):
                gname = w.title()
                members = [_save_key(s.prefix) for s in w.sections()]
                section_groups[gname] = members
                ordered_prefixes.extend([s.prefix for s in w.sections()])

        # 2. Determine project early so all saves go to the right file
        _proj = getattr(self, '_attr_proj_cb', None)
        _proj_name = (_proj.currentText().strip() or "default") if _proj else "default"
        _save_tags_file = attrs_mod.tags_file_for_project(
            None if _proj_name == "default" else _proj_name)
        _save_ws_file   = attrs_mod.workspace_file_for_project(
            None if _proj_name == "default" else _proj_name)

        # 3. Save workspace (coded fields) via AttributeManager in sequence
        new_ws = {}
        for pfx in ordered_prefixes:
            pfx_keys = [k for k in self._attr_ws_entries.keys() if k.startswith(pfx)]
            for key in sorted(pfx_keys):
                new_ws[key] = {field: le.text() for field, le in self._attr_ws_entries[key].items()}
        try:
            self._attr_manager.filename = _save_ws_file
            self._attr_manager.save_data(new_ws)
            self._attr_manager.export_tag_groups(new_ws)
        except Exception as e:
            QMessageBox.critical(self, "Save Error", str(e))
            return

        # 3. Collect taglist/boolean/text groups into attrs_tags.json in order
        result = {}
        for pfx in ordered_prefixes:
            sk = _save_key(pfx)
            if pfx in self._attr_tag_groups:
                pairs = [[k_e.text().strip(), l_e.text().strip()]
                         for k_e, l_e, _ in self._attr_tag_groups[pfx]
                         if k_e.text().strip()]
                result[sk] = pairs
            elif pfx in self._attr_text_fields:
                if "__text_fields__" not in result:
                    result["__text_fields__"] = {}
                tf_tuple = self._attr_text_fields[pfx]
                lbl_e, ph_e = tf_tuple[0], tf_tuple[1]
                result["__text_fields__"][sk] = {
                    "label": lbl_e.text().strip(),
                    "placeholder": ph_e.text().strip(),
                }

        # 4. Save section order and styles so loading is JSON-driven
        section_styles = {}
        for pfx in ordered_prefixes:
            sk = _save_key(pfx)
            if pfx in self._attr_tag_groups:
                pairs = self._attr_tag_groups[pfx]
                section_styles[sk] = "boolean" if len(pairs) <= 1 else "taglist"
            elif pfx in self._attr_text_fields:
                section_styles[sk] = "text"
            elif pfx in self._attr_section_styles:
                section_styles[pfx] = self._attr_section_styles[pfx]
        result["__section_order__"]  = [_save_key(p) for p in ordered_prefixes]
        result["__section_styles__"] = section_styles
        if section_groups:
            result["__section_groups__"] = section_groups
            result["__group_order__"] = list(section_groups.keys())

        # 5. Collect per-digit column names and parent names for coded fields
        col_names = {}
        for pfx, col_edits in self._attr_col_names.items():
            names = [e.text().strip() for e in col_edits]
            if any(names):
                col_names[pfx] = names
        if col_names:
            result["__col_names__"] = col_names

        parent_names = {}
        for pfx, pe in self._attr_parent_names.items():
            v = pe.text().strip()
            if v:
                parent_names[pfx] = v
        if parent_names:
            result["__parent_names__"] = parent_names

        try:
            existing = {}
            if os.path.exists(_save_tags_file):
                with open(_save_tags_file, encoding="utf-8") as f:
                    existing = json.load(f)

            final_json = {}
            for k, v in result.items():
                final_json[k] = v
            _internal = {"__text_fields__", "__field_names__", "__col_names__",
                         "__section_order__", "__section_styles__", "__section_groups__",
                         "__group_order__", "__parent_names__"}
            for k, v in existing.items():
                if k not in final_json and k not in _internal:
                    final_json[k] = v

            with open(_save_tags_file, "w", encoding="utf-8") as f:
                json.dump(final_json, f, indent=2, ensure_ascii=False)
        except Exception as e:
            QMessageBox.critical(self, "Save Error", str(e))
            return

        if hasattr(self.app, 'reload_tag_groups'):
            self.app.reload_tag_groups(_proj_name if _proj_name != "default" else None)
        QMessageBox.information(self, "Saved",
            f"Attribute set '{_proj_name}' saved and applied.")
