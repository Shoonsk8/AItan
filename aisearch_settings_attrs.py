import os, json
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                              QLabel, QLineEdit, QGroupBox, QComboBox,
                              QMessageBox, QScrollArea, QGridLayout, QApplication)
from PyQt6.QtCore import Qt

import aisearch_attrs as _am_ref
import aisearch_config as cfg
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
        self._attr_proj_cb.wheelEvent = lambda e: e.ignore()
        self._attr_proj_cb.setFixedWidth(140)
        # Populate from existing projects (features_*.pt), same source as DB tab
        _existing_projs = ["default"] + sorted(
            f.replace("features_", "").replace(".pt", "")
            for f in os.listdir(_am_ref.DATA_DIR)
            if f.startswith("features_") and f.endswith(".pt")
        )
        for _s in _existing_projs:
            self._attr_proj_cb.addItem(_s)
        # Default to current project if available
        _cur_proj = getattr(self.app, 'current_project', 'default') or 'default'
        _idx = self._attr_proj_cb.findText(_cur_proj)
        if _idx >= 0:
            self._attr_proj_cb.setCurrentIndex(_idx)
        proj_bar.addWidget(self._attr_proj_cb)

        btn_proj_load = QPushButton("Load")
        btn_proj_load.setStyleSheet("background:#1e6e1e; color:white; font-weight:bold; padding:3px 8px;")
        proj_bar.addWidget(btn_proj_load)

        self._btn_attr_save = btn_save_over = QPushButton("💾 Overwrite")
        btn_save_over.setStyleSheet(cfg.btn_ss("btn_write", self.app.config))
        btn_save_over.clicked.connect(self._save_attr_groups)
        proj_bar.addWidget(btn_save_over)
        self._attr_editing_lbl = QLabel(f"Editing: {_cur_proj}")
        self._attr_editing_lbl.setStyleSheet("color:#aaa; font-style:italic;")
        proj_bar.addWidget(self._attr_editing_lbl)

        proj_bar.addStretch()
        al.addLayout(proj_bar)
        al.addWidget(_hsep())

        self._attr_current_project = self._attr_proj_cb.currentText().strip() or "default"

        def _tags_file_for_current():
            p = self._attr_proj_cb.currentText().strip() or "default"
            return _am_ref.tags_file_for_project(None if p == "default" else p)

        def _workspace_file_for_current():
            p = self._attr_proj_cb.currentText().strip() or "default"
            return _am_ref.workspace_file_for_project(None if p == "default" else p)

        def _seed_custom_matrix(mgr):
            """Seed workspace entries for non-FIELD_DEFS matrix sections from the project tags file.
            Called whenever the workspace has no data for a custom matrix prefix, so the
            16×16 grid editor shows existing pairs instead of all-empty cells."""
            try:
                with open(_tags_file_for_current(), "r", encoding="utf-8") as _f:
                    _raw = json.load(_f)
            except Exception:
                return
            _styles = _raw.get("__section_styles__", {})
            for _pfx, _sty in _styles.items():
                if _sty != "matrix" or _pfx in FIELD_DEFS:
                    continue
                # Only seed if workspace is completely empty for this prefix
                _has = any(
                    mgr.data.get(f"{_pfx}{hex(_r)[2:]}{hex(_c)[2:]}", {}).get("expression")
                    for _r in range(16) for _c in range(16)
                )
                if _has:
                    continue
                # Read from {pfx}_Table first, fall back to {pfx} (old pair format)
                _pairs = list(_raw.get(f"{_pfx}_Table") or _raw.get(_pfx) or [])
                # Also check TAG_GROUPS for default values
                if not _pairs:
                    import aisearch_attrs as _aa
                    _pairs = list(_aa._DEFAULT_TAG_GROUPS.get(f"{_pfx}_Major") or [])
                for _pair in _pairs:
                    if len(_pair) >= 2:
                        _k = str(_pair[0])
                        # Pad single-digit keys to 2 digits (place in row 0)
                        _ws_key = f"{_pfx}{_k.zfill(2)}"
                        mgr.data.setdefault(_ws_key, {})["expression"] = _pair[1]

        def _reload_attr_sections():
            """Clear and reload all workspace sections for the selected project."""
            _loaded = self._attr_proj_cb.currentText().strip() or "default"
            self._attr_editing_lbl.setText(f"Editing: {_loaded}")
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
            # Clear group combo back to its static defaults
            self._attr_grp_cb.clear()
            self._attr_grp_cb.addItem("")
            for _gname in ["Head", "Body", "BG", "Technical", "Tags", "Text"]:
                self._attr_grp_cb.addItem(_gname)
            # Reload manager for this project — always from JSON, no workspace file
            self._attr_manager = AttributeManager()
            tf = _tags_file_for_current()
            tg = _am_ref._load_tag_groups(tf)
            self._attr_manager.import_from_tag_groups(tg)
            _seed_custom_matrix(self._attr_manager)
            # Reload sections (reuse logic below via _load_sections())
            _load_sections()
            # Re-add the stretch that was cleared above
            self._attr_aw_vbox.addStretch()

        btn_proj_load.clicked.connect(_reload_attr_sections)
        self._reload_attr_sections = _reload_attr_sections  # expose so set_project() can call it




        self._attr_manager = AttributeManager()  # in-memory only, no workspace file
        _tg = _am_ref._load_tag_groups(_tags_file_for_current())
        self._attr_manager.import_from_tag_groups(_tg)
        _seed_custom_matrix(self._attr_manager)

        self._attr_ws_entries  = {}   # json_key → {field_name: QLineEdit}
        self._attr_ws_loaded   = set()  # prefixes already added
        self._attr_rows        = []   # kept for backward compat (unused in new UI)
        self._attr_tag_groups  = {}   # grp_name → [(key_edit, lbl_edit, row_w), ...]
        self._attr_text_fields = {}   # field_name → (label_edit, placeholder_edit)
        self._attr_col_names   = {}   # prefix → [QLineEdit, ...]  (per-digit attr names)
        self._attr_parent_names  = {}  # prefix → QLineEdit  (human parent name e.g. "hair")
        self._attr_section_styles = {}  # prefix → style string (for all sections incl. coded)
        self._attr_deleted_sections = set()  # sections explicitly deleted by the user
        self._attr_groups      = {}   # group_name → _WsGroup widget

        # ── Input bar ─────────────────────────────────────────────────────────
        inp_bar = QHBoxLayout(); inp_bar.setSpacing(6)
        inp_bar.addWidget(QLabel("Prefix/Key:"))
        self._attr_key_edit = QLineEdit()
        self._attr_key_edit.setFixedWidth(120)
        self._attr_key_edit.setPlaceholderText("e.g. MDL_img")
        inp_bar.addWidget(self._attr_key_edit)

        self._attr_style_cb = QComboBox()
        self._attr_style_cb.wheelEvent = lambda e: e.ignore()
        self._attr_style_cb.addItem("Select style…")
        for _s, _ln in [("1dig","1-digit"), ("2dig","2-digit independent"),
                        ("3dig","3-digit independent"), ("matrix","16×16 matrix"),
                        ("taglist","Tag List  (key · label)"),
                        ("radio","Radio  (single-select tag list)"),
                        ("text","Text Field  (prompt / notes)"),
                        ("id","ID  (structural marker)"),
                        ]:
            self._attr_style_cb.addItem(_ln, _s)
        self._attr_style_cb.setFixedWidth(195)
        inp_bar.addWidget(self._attr_style_cb)

        self._attr_grp_cb = QComboBox()
        self._attr_grp_cb.wheelEvent = lambda e: e.ignore()
        self._attr_grp_cb.setEditable(True)
        self._attr_grp_cb.setFixedWidth(120)
        self._attr_grp_cb.setPlaceholderText("(none)")
        self._attr_grp_cb.addItem("")
        for _gname in ["Head", "Body", "BG", "Technical", "Tags", "Text"]:
            self._attr_grp_cb.addItem(_gname)

        btn_add_tbl = QPushButton("Add Table")
        btn_add_tbl.setStyleSheet(cfg.btn_ss("btn_add", self.app.config))
        inp_bar.addWidget(btn_add_tbl)

        inp_bar.addWidget(QLabel("Group:"))
        inp_bar.addWidget(self._attr_grp_cb)

        btn_add_grp = QPushButton("Add Group")
        btn_add_grp.setStyleSheet(cfg.btn_ss("btn_add", self.app.config))
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
            "radio":   "Radio",
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
            "matrix": [("Expression", "expression", None)],
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

        _BASIC_STYLES = {"1dig", "2dig", "3dig", "matrix", "id"}
        # Map coded-field prefix → human label (from CODED_FIELDS)
        _CODED_LABELS = {letter: label
                         for letter, label, _ in _am_ref._DEFAULT_CODED_FIELDS}
        _CODED_LABELS["P"] = "Person ID"

        def _build_ws_section(prefix, force_style=None, group=None, text_label=None, text_placeholder=None, initial_pairs=None, col_defs=None, saved_parent_name=None, saved_col_names=None, readonly=False, saved_field_name=None, _traw=None):
            if prefix in self._attr_ws_loaded:
                return
            # Resolve style + cols
            # Custom col defs stored in JSON (for dig-style sections not in FIELD_DEFS)
            _json_col_defs = col_defs or _am_ref.TAG_GROUPS.get("__col_defs__", {}).get(prefix, [])
            if force_style:
                style = force_style
                if prefix in FIELD_DEFS and FIELD_DEFS[prefix][0] == style:
                    cols = FIELD_DEFS[prefix][1]
                elif _json_col_defs:
                    cols = [tuple(c) for c in _json_col_defs]
                else:
                    cols = _generic_cols.get(style, [])
            elif prefix in FIELD_DEFS:
                style, cols = FIELD_DEFS[prefix]
            elif _json_col_defs:
                # Custom dig section defined in JSON __col_defs__
                style = _am_ref.TAG_GROUPS.get("__section_styles__", {}).get(prefix, "2dig")
                cols = [tuple(c) for c in _json_col_defs]
            else:
                return
            self._attr_ws_loaded.add(prefix)
            self._attr_section_styles[prefix] = style
            pad = _WSPAD.get(style, 2)
            # Basic (coded field) sections → blue; custom (tag/text/boolean) → yellow
            # Exception: if user has saved their own data for any column's tag group → yellow
            _is_coded_dig = (prefix in FIELD_DEFS and style in ("1dig", "2dig", "3dig", "id"))
            if _is_coded_dig and _traw:
                _fd_cols = FIELD_DEFS.get(prefix, (None, []))[1]
                _has_user_col_data = any(_traw.get(tg) for _, _, tg in _fd_cols if tg)
                if _has_user_col_data:
                    _is_coded_dig = False
            _sec_color = "#6ea6f0" if (_is_coded_dig or readonly) else "#f0c040"

            def _add_to_target(widget):
                if group:
                    grp_w = _build_ws_group(group)
                    grp_w.add_section(widget)
                else:
                    self._attr_aw_vbox.addWidget(widget)

            _lbl = _CODED_LABELS.get(prefix, "")
            if _lbl and prefix in FIELD_DEFS:
                _sec_title_prefix = f"{_lbl}  [{prefix}]"
            elif _lbl:
                _sec_title_prefix = _lbl
            elif saved_field_name:
                _sec_title_prefix = saved_field_name
            elif prefix in _am_ref._DEFAULT_FIELD_NAMES:
                _sec_title_prefix = _am_ref._DEFAULT_FIELD_NAMES[prefix]
            else:
                _sec_title_prefix = prefix

            if style == "id":
                sec = _WsSec(f"{_sec_title_prefix}   │   ID", prefix=prefix, color=_sec_color)
                _add_to_target(sec)
                if prefix == "J":
                    msg = "  Timestamp ID — auto-stamped by scan  (not editable)"
                elif prefix == "P":
                    msg = "  Person ID — 3-digit hex, managed via DB → Person Management"
                else:
                    msg = f"  Structural ID marker  (no editable data)"
                blank_lbl = QLabel(msg)
                blank_lbl.setStyleSheet("color:#666; font-style:italic; padding:8px;")
                bl = QVBoxLayout(sec.content); bl.addWidget(blank_lbl)
                def _on_del_id(checked=False, pfx=prefix, s=sec):
                    self._attr_ws_loaded.discard(pfx)
                    s.setParent(None); s.deleteLater()
                sec._del_btn.clicked.connect(_on_del_id)
                return

            if style == "matrix":
                _saved_name = (saved_col_names or _am_ref.TAG_GROUPS.get("__col_names__", {}).get(prefix) or [None])[0]
                _disp_title = (_saved_name or _sec_title_prefix)
                _sec_title = f"{_disp_title}   │   16×16 matrix"
            else:
                _sec_title = f"{_sec_title_prefix}   │   {_style_names.get(style, style)}"
            sec = _WsSec(_sec_title, prefix=prefix, color=_sec_color)
            sec._section_style = style   # authoritative style stored on the widget
            _add_to_target(sec)

            # ── Delete button: works even if content was never expanded ───
            def _on_del_any(checked=False, pfx=prefix, s=sec):
                s._build_cb = None   # prevent deferred build after delete
                self._attr_ws_loaded.discard(pfx)
                self._attr_deleted_sections.add(pfx)
                self._attr_section_styles.pop(pfx, None)  # clear stale style
                for _key in [k for k in self._attr_ws_entries if k.startswith(pfx)]:
                    self._attr_ws_entries.pop(_key, None)
                self._attr_tag_groups.pop(pfx, None)
                self._attr_text_fields.pop(pfx, None)
                self._attr_col_names.pop(pfx, None)
                self._attr_parent_names.pop(pfx, None)
                s.setParent(None); s.deleteLater()
            if readonly:
                sec._del_btn.setVisible(False)
            else:
                sec._del_btn.clicked.connect(_on_del_any)

            # ── Defer content build to first expand via _build_cb ─────────
            def _build_content(s=sec, pfx=prefix, sty=style, c=cols, p=pad,
                                tl=text_label, tp=text_placeholder, ip=initial_pairs,
                                _tr=_traw):
                if sty == "boolean":
                    # ── Boolean — section key IS the flag; just show Label ──
                    bl_lay = QVBoxLayout(s.content)
                    bl_lay.setContentsMargins(8, 4, 8, 6); bl_lay.setSpacing(2)
                    self._attr_tag_groups[pfx] = []
                    # Use initial_pairs key/label if available; fall back to TAG_GROUPS then pfx
                    if ip is not None and ip:
                        existing_key   = ip[0][0]
                        existing_label = ip[0][1]
                    else:
                        _fallback = _am_ref.TAG_GROUPS.get(pfx, [])
                        existing_key   = _fallback[0][0] if _fallback else pfx.lower()
                        existing_label = _fallback[0][1] if _fallback else pfx
                    rw = QWidget()
                    rl2 = QHBoxLayout(rw); rl2.setContentsMargins(0,0,0,0); rl2.setSpacing(6)
                    flag_note = QLabel("☑", styleSheet="color:#6ea6f0; font-size:10pt;")
                    rl2.addWidget(flag_note)
                    rl2.addWidget(QLabel("Label:", styleSheet="color:#888; font-size:8pt;"))
                    l_e = QLineEdit(existing_label); l_e.setMinimumWidth(160); l_e.setStyleSheet(_les)
                    l_e.setPlaceholderText("Display Name")
                    rl2.addWidget(l_e, stretch=1)
                    rl2.addWidget(QLabel(f"  key = {existing_key}", styleSheet="color:#555; font-size:8pt; font-style:italic;"))
                    bl_lay.addWidget(rw)
                    # Store existing_key so save produces the correct lowercase key
                    k_e = QLineEdit(existing_key)
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

                elif sty in ("taglist", "radio", "combo"):
                    # ── Tag list / Radio / Combo — arbitrary key=label rows ──
                    row_lay = QVBoxLayout(s.content)
                    row_lay.setContentsMargins(6, 3, 6, 5); row_lay.setSpacing(2)
                    self._attr_tag_groups[pfx] = []


                    # Style selector — lets user change taglist ↔ radio in-place (hidden for readonly)
                    if sty in ("taglist", "radio") and not readonly:
                        _sty_row = QHBoxLayout(); _sty_row.setSpacing(4)
                        _sty_row.addWidget(QLabel("Style:", styleSheet="color:#888; font-size:8pt;"))
                        _sty_cb = QComboBox()
                        _sty_cb.wheelEvent = lambda e: e.ignore()
                        _sty_cb.addItem("Tag List  (multi-select)", "taglist")
                        _sty_cb.addItem("Radio  (single-select)",   "radio")
                        _sty_cb.setFixedWidth(185)
                        _sty_cb.setCurrentIndex(0 if sty == "taglist" else 1)
                        def _on_sty_change(idx, cb=_sty_cb, _s=s, _pfx=pfx):
                            new_sty = cb.currentData()
                            _s._section_style = new_sty
                            self._attr_section_styles[_pfx] = new_sty
                            _s._title_lbl.setText(
                                f"Key = {_pfx}   │   {_style_names.get(new_sty, new_sty)}")
                        _sty_cb.currentIndexChanged.connect(_on_sty_change)
                        _sty_row.addWidget(_sty_cb)
                        _sty_row.addStretch()
                        row_lay.addLayout(_sty_row)

                    _ro = readonly
                    def _make_tag_row(grp=pfx, rl=row_lay, k_val="", l_val="", _readonly=_ro):
                        rw = QWidget()
                        rl2 = QHBoxLayout(rw); rl2.setContentsMargins(0, 0, 0, 0); rl2.setSpacing(4)
                        _lbl_ro_ss = ("color:#4a9eff; background:#1a1a2e; border:1px solid #333; "
                                      "padding:2px 4px;")
                        if _readonly:
                            k_e = QLabel(k_val); k_e.setFixedWidth(90)
                            k_e.setStyleSheet(_lbl_ro_ss)
                            rl2.addWidget(k_e)
                            rl2.addWidget(QLabel("Label:", styleSheet="color:#888; font-size:8pt;"))
                            l_e = QLabel(l_val); l_e.setMinimumWidth(120)
                            l_e.setStyleSheet(_lbl_ro_ss)
                        else:
                            rl2.addWidget(QLabel("Key:", styleSheet="color:#888; font-size:8pt;"))
                            k_e = QLineEdit(k_val); k_e.setFixedWidth(90)
                            k_e.setStyleSheet(_les)
                            k_e.setPlaceholderText("tag_key")
                            rl2.addWidget(k_e)
                            rl2.addWidget(QLabel("Label:", styleSheet="color:#888; font-size:8pt;"))
                            l_e = QLineEdit(l_val); l_e.setMinimumWidth(120)
                            l_e.setStyleSheet(_les)
                            l_e.setPlaceholderText("Display Name")
                        rl2.addWidget(l_e, stretch=1)
                        if not _readonly:
                            btn_x = QPushButton("✕"); btn_x.setFixedWidth(26)
                            btn_x.setStyleSheet("background:transparent; color:#884444; border:none; font-size:10pt;")
                            rl2.addWidget(btn_x)
                        rl.addWidget(rw)
                        entry = (k_e, l_e, rw)
                        self._attr_tag_groups[grp].append(entry)
                        if not _readonly:
                            def _del_row(checked=False, e=entry, g=grp):
                                self._attr_tag_groups[g].remove(e)
                                e[2].setParent(None); e[2].deleteLater()
                            btn_x.clicked.connect(_del_row)
                        return entry

                    # Priority: initial_pairs from project file > TAG_GROUPS > {pfx}_Preset
                    if ip is not None:
                        existing_pairs = list(ip)
                    else:
                        existing_pairs = list(_am_ref.TAG_GROUPS.get(pfx, []))
                        if not existing_pairs and pfx in FIELD_DEFS:
                            existing_pairs = list(_am_ref._DEFAULT_TAG_GROUPS.get(f"{pfx}_Preset", []))
                    for _k, _l in existing_pairs:
                        _make_tag_row(k_val=_k, l_val=_l)
                    if not existing_pairs and not readonly:
                        _make_tag_row()
                    if not readonly:
                        btn_add_row = QPushButton("+ Add")
                        btn_add_row.setFixedWidth(60)
                        btn_add_row.setStyleSheet(cfg.btn_ss("btn_add", self.app.config, "border:none; border-radius:2px;"))
                        btn_add_row.clicked.connect(lambda checked=False, g=pfx: _make_tag_row(grp=g))
                        add_h = QHBoxLayout(); add_h.addWidget(btn_add_row)
                        add_h.addStretch()
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
                        _init_name = (_saved_mx[0] if _saved_mx else "") or pfx
                        _mx_name_e.setText(_init_name)
                        s._title_lbl.setText(f"{_init_name or pfx}   │   16×16 matrix")
                        _mx_name_e.textChanged.connect(
                            lambda txt, _s=s, _pfx=pfx: _s._title_lbl.setText(
                                f"{txt or _pfx}   │   16×16 matrix"))
                        _mx_row.addWidget(_mx_name_e); _mx_row.addStretch()
                        cl.addLayout(_mx_row)
                        self._attr_col_names[pfx] = [_mx_name_e]
                    grid_w = QWidget()
                    from PyQt6.QtWidgets import QSizePolicy as _QSP
                    grid_w.setSizePolicy(_QSP.Policy.Maximum, _QSP.Policy.Preferred)
                    gl = QGridLayout(grid_w)
                    gl.setContentsMargins(0, 0, 0, 0); gl.setSpacing(2)
                    cl.addWidget(grid_w)
                    if sty == "matrix":
                        gl.setColumnMinimumWidth(0, 18)
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
                                json_key = f"{pfx}{r_h}{c_h}"
                                stored = self._attr_manager.data.get(json_key, {})
                                le = QLineEdit()
                                le.setFixedSize(100, 20)
                                le.setStyleSheet(_les + " font-size:8pt;")
                                le.setText(stored.get("expression", ""))
                                gl.addWidget(le, _r + 1, _c + 1)
                                self._attr_ws_entries[json_key] = {"expression": le}
                    else:
                        _saved_parent = (saved_parent_name
                            or _am_ref.TAG_GROUPS.get("__parent_names__", {}).get(pfx)
                            or pfx.lower())
                        _pr_row = QHBoxLayout(); _pr_row.setSpacing(6)
                        _pr_row.addWidget(QLabel("Parent:", styleSheet="color:#888; font-size:8pt;"))
                        _is_coded_field = pfx in FIELD_DEFS
                        _parent_e = QLineEdit(_CODED_LABELS.get(pfx, _saved_parent) if _is_coded_field else _saved_parent)
                        _parent_e.setFixedWidth(140)
                        _parent_e.setPlaceholderText("e.g. hair")
                        if _is_coded_field:
                            _parent_e.setEnabled(False)
                        else:
                            _parent_e.setStyleSheet(_les)
                        _pr_row.addWidget(_parent_e); _pr_row.addStretch()
                        cl.addLayout(_pr_row)
                        self._attr_parent_names[pfx] = _parent_e
                        _hdr_ss = ("background:#1a2a1a; color:#f0c040; font-weight:bold; "
                                   "border:1px solid #446644; padding:1px 4px; font-size:8pt;")
                        col_name_edits = []
                        _saved_cols = (saved_col_names or _am_ref.TAG_GROUPS.get("__col_names__", {}).get(pfx, []))
                        _col_bases = []
                        _n_cols = len(c)
                        # Columns are ordered right-to-left in FIELD_DEFS (ci=0 = rightmost digit)
                        # Display right-to-left so ci=0 (1st/rightmost digit) appears on the RIGHT
                        for ci, (col_lbl, _jf, _tg) in enumerate(c):
                            _base = col_lbl.split()[0].rstrip("s").lower()
                            _col_bases.append(_base)
                            saved_name = _saved_cols[ci] if ci < len(_saved_cols) else ""
                            _clean_parent = _CODED_LABELS.get(pfx, _saved_parent) if _is_coded_field else _saved_parent
                            default_name = f"{_clean_parent}_{_base}"
                            col_e = QLineEdit(saved_name or default_name)
                            col_e.setFixedHeight(22); col_e.setMinimumWidth(110)
                            col_e.setStyleSheet(_hdr_ss)
                            col_e.setPlaceholderText("Attr name")
                            if _is_coded_field:
                                col_e.setEnabled(False)
                            gl.addWidget(col_e, 0, _n_cols - ci)  # ci=0 → rightmost col
                            col_name_edits.append(col_e)
                        self._attr_col_names[pfx] = col_name_edits
                        def _on_parent_change(text, edits=col_name_edits, bases=_col_bases, _pfx=pfx):
                            _p = text.strip() or _pfx.lower()
                            for col_e, base in zip(edits, bases):
                                col_e.setText(f"{_p}_{base}")
                        if not _is_coded_field:
                            _parent_e.textChanged.connect(_on_parent_change)

                        # Build default-value lookup: json_field → {hex_code → label}
                        _default_lookup = {}
                        for ci, (col_lbl, json_field, _tg) in enumerate(c):
                            _def_pairs = _am_ref._DEFAULT_TAG_GROUPS.get(_tg, [])
                            _default_lookup[json_field] = {
                                str(pair[0]): str(pair[1])
                                for pair in _def_pairs
                                if isinstance(pair, (list, tuple)) and len(pair) >= 2
                            }
                        _ro_cell_ss = ("QLineEdit { color:#4a9eff; background:#1a1a2e; "
                                       "border:1px solid #333; font-size:8pt; }"
                                       "QLineEdit:disabled { color:#4a9eff; background:#1a1a2e; "
                                       "border:1px solid #333; font-size:8pt; }")
                        _B36 = "0123456789abcdefghijklmnopqrstuvwxyz"
                        for i, ch in enumerate(_B36):
                            row_key = f"{pfx}{'0' * (p - 1)}{ch}"
                            stored = self._attr_manager.data.get(row_key, {})
                            _rl = QLabel(ch); _rl.setStyleSheet(_rxs)
                            gl.addWidget(_rl, i + 1, 0)
                            row_wids = {}
                            for ci, (col_lbl, json_field, _tg) in enumerate(c):
                                # Sub-tables are indexed by single hex digit (ch)
                                _def_val = _default_lookup.get(json_field, {}).get(ch, "")
                                # Show as blue label only if there is a Python default AND
                                # the user has NOT saved their own JSON data for this column.
                                _col_has_json = bool(_tr and _tg and _tr.get(_tg))
                                if _def_val and not _col_has_json:
                                    # Default value — show as label, no input box
                                    lbl = QLabel(_def_val)
                                    lbl.setMinimumWidth(130); lbl.setFixedHeight(22)
                                    lbl.setStyleSheet("color:#4a9eff; background:#1a1a2e; "
                                                      "border:1px solid #333; font-size:8pt; padding:2px 4px;")
                                    gl.addWidget(lbl, i + 1, _n_cols - ci)
                                    # Store a disabled dummy so save loop can skip it
                                    _dummy = QLineEdit(_def_val); _dummy.setEnabled(False)
                                    _dummy.setVisible(False)
                                    row_wids[json_field] = _dummy
                                else:
                                    le = QLineEdit()
                                    le.setMinimumWidth(130); le.setFixedHeight(22)
                                    le.setStyleSheet(_les)
                                    le.setText(stored.get(json_field, "") or _def_val)
                                    gl.addWidget(le, i + 1, _n_cols - ci)
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
            _deleted_set = set(_tags_raw.get("__deleted_sections__", []))
            self._attr_deleted_sections = _deleted_set
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
            else:
                # Append any FIELD_DEFS keys missing from saved order (e.g. newly added P)
                # Skip keys the user has explicitly deleted (_deleted_set).
                _loaded_set = set(_section_order)
                for _fd_key in FIELD_DEFS:
                    if _fd_key not in _loaded_set and _fd_key not in _deleted_set:
                        _section_order.append(_fd_key)
                # Append any TAG_GROUPS keys (taglist/boolean) not yet in order
                # Sub-table keys: end in _Table, or start with any existing section prefix + "_"
                _loaded_prefixes = set(_section_order)
                def _is_subtable(k):
                    if k.endswith("_Table"): return True
                    for _p in _loaded_prefixes:
                        if k.startswith(_p + "_") and len(k) > len(_p) + 1:
                            return True
                    return False
                _LEGACY_REPLACED = {"Audio"}  # uppercase Audio replaced by lowercase audio radio field
                _all_tg = {**_am_ref._DEFAULT_TAG_GROUPS, **_am_ref.TAG_GROUPS}
                for _tg_key in _all_tg:
                    if (_tg_key not in _loaded_set
                            and not _tg_key.startswith("__")
                            and _tg_key not in _LEGACY_REPLACED
                            and _tg_key not in _deleted_set
                            and not _is_subtable(_tg_key)):
                        _section_order.append(_tg_key)
                        _loaded_set.add(_tg_key)
                        _loaded_prefixes.add(_tg_key)
                # Append any text fields not yet in order
                for _tf_key in _tags_raw.get("__text_fields__", {}):
                    if _tf_key not in _loaded_set:
                        _section_order.append(_tf_key)
                        _loaded_set.add(_tf_key)

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
                    elif _pfx in _am_ref.TAG_GROUPS:
                        _pairs = _am_ref.TAG_GROUPS[_pfx]
                        _fstyle = "boolean" if len(_pairs) <= 1 else "taglist"
                    elif _pfx in _am_ref._DEFAULT_TAG_GROUPS:
                        _pairs = _am_ref._DEFAULT_TAG_GROUPS[_pfx]
                        _fstyle = "boolean" if len(_pairs) <= 1 else "taglist"
                    elif _pfx in _tf_data_map:
                        _fstyle = "text"
                    else:
                        continue
                _tf_meta = _tf_data_map.get(_pfx, {})
                _saved_lbl = _tf_meta.get("label", "") if isinstance(_tf_meta, dict) else ""
                _saved_ph  = _tf_meta.get("placeholder", "") if isinstance(_tf_meta, dict) else ""
                # Use JSON pairs if available, else fall back to TAG_GROUPS / defaults
                _raw_pairs = _tags_raw.get(_pfx)
                if _raw_pairs is None:
                    _raw_pairs = _am_ref.TAG_GROUPS.get(_pfx) or _am_ref._DEFAULT_TAG_GROUPS.get(_pfx)
                if _raw_pairs is None and _pfx in FIELD_DEFS:
                    # Coded fields (O/R/K) store presets under {prefix}_Preset
                    _raw_pairs = (_am_ref.TAG_GROUPS.get(f"{_pfx}_Preset")
                                  or _am_ref._DEFAULT_TAG_GROUPS.get(f"{_pfx}_Preset"))
                _init_pairs = _raw_pairs if isinstance(_raw_pairs, list) else None
                # readonly if values come purely from _DEFAULT_TAG_GROUPS (not overridden in project JSON)
                _eff_style = _fstyle or (FIELD_DEFS[_pfx][0] if _pfx in FIELD_DEFS else None)
                _is_readonly = (
                    (
                        _tags_raw.get(_pfx) is None
                        and _pfx in _am_ref._DEFAULT_TAG_GROUPS
                        and _eff_style in ("taglist", "radio", "boolean", "combo")
                    )
                    or (_pfx in FIELD_DEFS and _eff_style == "text")
                )
                _build_ws_section(_pfx, force_style=_fstyle,
                                  group=_group_map.get(_pfx),
                                  text_label=_saved_lbl, text_placeholder=_saved_ph,
                                  initial_pairs=_init_pairs,
                                  col_defs=_tags_raw.get("__col_defs__", {}).get(_pfx, []),
                                  saved_parent_name=_tags_raw.get("__parent_names__", {}).get(_pfx),
                                  saved_col_names=_tags_raw.get("__col_names__", {}).get(_pfx),
                                  readonly=_is_readonly,
                                  saved_field_name=_tags_raw.get("__field_names__", {}).get(_pfx),
                                  _traw=_tags_raw)

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
            prefix = raw
            if not prefix:
                QMessageBox.warning(self, "Input Error", "Enter a prefix/key.")
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
        from PyQt6.QtWidgets import QCheckBox as _QCB
        from attribute_manager import FIELD_DEFS

        # Warning dialog (suppressed once user checks "don't show again")
        if not getattr(self, '_attr_overwrite_skip_warn', False):
            _proj = getattr(self, '_attr_proj_cb', None)
            _tgt = (_proj.currentText().strip() or "default") if _proj else "default"
            _mb = QMessageBox(self)
            _mb.setIcon(QMessageBox.Icon.Warning)
            _mb.setWindowTitle("Overwrite")
            _mb.setText(f"This will overwrite the attribute set <b>'{_tgt}'</b>.<br>Continue?")
            _mb.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            _cb = _QCB("Don't show this warning again")
            _mb.setCheckBox(_cb)
            if _mb.exec() != QMessageBox.StandardButton.Yes:
                return
            if _cb.isChecked():
                self._attr_overwrite_skip_warn = True

        # 0. Force-build any sections that were never expanded, so their data
        #    lands in _attr_ws_entries / _attr_tag_groups / _attr_text_fields
        #    before we read those dicts below.
        _scroll_inner = self._attr_aw_vbox.parentWidget()
        if _scroll_inner:
            for _sec in _scroll_inner.findChildren(_WsSecCls):
                if _sec._build_cb is not None:
                    _cb = _sec._build_cb
                    _sec._build_cb = None
                    try:
                        _cb()
                    except Exception as _e:
                        import traceback; traceback.print_exc()

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
        _pfx_sec_map     = {}   # prefix → _WsSec widget (style source of truth)
        for i in range(self._attr_aw_vbox.count()):
            item = self._attr_aw_vbox.itemAt(i)
            if not item or not item.widget(): continue
            w = item.widget()
            if isinstance(w, _WsSec):
                ordered_prefixes.append(w.prefix)
                _pfx_sec_map[w.prefix] = w
            elif isinstance(w, _WsGroup):
                gname = w.title()
                members = [_save_key(s.prefix) for s in w.sections()]
                section_groups[gname] = members
                for s in w.sections():
                    ordered_prefixes.append(s.prefix)
                    _pfx_sec_map[s.prefix] = s

        # 2. Determine project — save to whichever project the combo currently shows
        _proj = getattr(self, '_attr_proj_cb', None)
        _proj_name = (_proj.currentText().strip() or "default") if _proj else "default"
        _proj_arg = None if _proj_name == "default" else _proj_name
        _save_tags_file = attrs_mod.tags_save_path_for_project(_proj_arg)
        _save_ws_file   = attrs_mod.workspace_save_path_for_project(_proj_arg)

        # 3. Build in-memory workspace dict from QLineEdits and export coded sub-tables
        new_ws = {}
        for pfx in ordered_prefixes:
            pfx_keys = [k for k in self._attr_ws_entries.keys() if k.startswith(pfx)]
            for key in sorted(pfx_keys):
                row_data = {field: le.text()
                            for field, le in self._attr_ws_entries[key].items()
                            if le.isEnabled()}  # skip hardcoded default cells
                if any(v.strip() for v in row_data.values()):
                    new_ws[key] = row_data
        try:
            _exported_sub_tables = self._attr_manager.export_tag_groups(new_ws)
        except Exception as e:
            QMessageBox.critical(self, "Save Error", str(e))
            return

        # 4. Collect all data into project JSON
        # Start with coded sub-tables (E_Color, PM_Motion, etc.) from export_tag_groups
        result = dict(_exported_sub_tables)
        # 4-custom. Export custom dig sections (not in FIELD_DEFS but have __col_defs__)
        #   e.g. E → E_Color, E_Additional
        from attribute_manager import _STYLE_PAD as _WSPAD
        _existing_raw_for_col_defs: dict = {}
        if os.path.exists(_save_tags_file):
            try:
                with open(_save_tags_file, encoding="utf-8") as _f:
                    _existing_raw_for_col_defs = json.load(_f)
            except Exception:
                pass
        _custom_col_defs = _existing_raw_for_col_defs.get("__col_defs__", {})
        _B36_custom = "0123456789abcdefghijklmnopqrstuvwxyz"
        for _cpfx in ordered_prefixes:
            if _cpfx in FIELD_DEFS:
                continue
            _csty = self._attr_section_styles.get(_cpfx, "")
            if _csty not in ("1dig", "2dig", "3dig"):
                continue
            _col_defs_pfx = _custom_col_defs.get(_cpfx, [])
            if not _col_defs_pfx:
                continue
            _cpad = _WSPAD.get(_csty, 2)
            for _col_def in _col_defs_pfx:
                if len(_col_def) < 3 or not _col_def[2]:
                    continue
                _c_jf, _c_tg = _col_def[1], _col_def[2]
                _cent = []
                for _ch in _B36_custom:
                    _rk = f"{_cpfx}{'0' * (_cpad - 1)}{_ch}"
                    _v = new_ws.get(_rk, {}).get(_c_jf, "").strip()
                    if _v:
                        _cent.append([_ch, _v])
                if _cent:
                    result[_c_tg] = _cent
        # 4a. Export ALL matrix 16×16 grid data as {prefix}_Table sub-keys
        #     This covers both FIELD_DEFS matrix (X) and custom non-FIELD_DEFS matrix (MDL, CL…)
        #     so the project-specific JSON always has up-to-date sub-tables.
        _all_matrix_pfx = (
            [p for p, (s, _) in FIELD_DEFS.items() if s == "matrix"] +
            [p for p in ordered_prefixes
             if p not in FIELD_DEFS and
             (self._attr_section_styles.get(p) or
              getattr(_pfx_sec_map.get(p), '_section_style', None)) == "matrix"]
        )
        for pfx in _all_matrix_pfx:
            sk = _save_key(pfx)
            entries = []
            for _r in range(16):
                for _c in range(16):
                    _r_h, _c_h = hex(_r)[2:], hex(_c)[2:]
                    _ws_key = f"{pfx}{_r_h}{_c_h}"   # r_h = 1st digit (row/vertical)
                    _val = new_ws.get(_ws_key, {}).get("expression", "").strip()
                    if _val:
                        entries.append([f"{_r_h}{_c_h}", _val])
            result[f"{sk}_Table"] = entries
        # 4b. Taglist/boolean/text sections
        for pfx in ordered_prefixes:
            sk = _save_key(pfx)
            # Skip hardcoded defaults — their values live in _DEFAULT_TAG_GROUPS, not in JSON
            if pfx in _am_ref._DEFAULT_TAG_GROUPS and pfx not in _existing_raw_for_col_defs:
                continue
            if pfx in self._attr_tag_groups:
                pairs = [[k_e.text().strip(), l_e.text().strip()]
                         for k_e, l_e, _ in self._attr_tag_groups[pfx]
                         if k_e.text().strip()]
                result[sk] = pairs
            elif pfx in self._attr_text_fields:
                if pfx in FIELD_DEFS:
                    continue  # hardcoded text field — never save to JSON
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
            # Skip hardcoded defaults from styles too
            if pfx in _am_ref._DEFAULT_TAG_GROUPS and pfx not in _existing_raw_for_col_defs:
                continue
            # Read style from widget (most reliable) then fall back to dict
            _sec_w = _pfx_sec_map.get(pfx)
            _sty_from_widget = getattr(_sec_w, '_section_style', None)
            if pfx in self._attr_tag_groups:
                orig_sty = _sty_from_widget or self._attr_section_styles.get(pfx, "taglist")
                if orig_sty in ("taglist", "radio", "boolean", "combo"):
                    section_styles[sk] = orig_sty
                else:
                    pairs = self._attr_tag_groups[pfx]
                    section_styles[sk] = "boolean" if len(pairs) <= 1 else "taglist"
            elif pfx in self._attr_text_fields:
                section_styles[sk] = "text"
            elif _sty_from_widget or pfx in self._attr_section_styles:
                section_styles[pfx] = _sty_from_widget or self._attr_section_styles[pfx]
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

        # 5b. Preserve __col_defs__ for custom dig sections (not in FIELD_DEFS)
        _existing_col_defs = {}
        if os.path.exists(_save_tags_file):
            try:
                with open(_save_tags_file, encoding="utf-8") as _f:
                    _existing_col_defs = json.load(_f).get("__col_defs__", {})
            except Exception:
                pass
        # Update col_defs for any ordered prefix that has col info in _attr_ws_entries
        for pfx in ordered_prefixes:
            if pfx in FIELD_DEFS:
                continue
            sty = self._attr_section_styles.get(pfx, "")
            if sty not in ("1dig", "2dig", "3dig"):
                continue
            # Read col defs from the existing JSON (cols aren't editable via UI yet)
            if pfx in _existing_col_defs:
                _existing_col_defs[pfx] = _existing_col_defs[pfx]  # preserve as-is
        if _existing_col_defs:
            result["__col_defs__"] = _existing_col_defs

        parent_names = {}
        for pfx, pe in self._attr_parent_names.items():
            v = pe.text().strip()
            if v:
                parent_names[pfx] = v
        if parent_names:
            result["__parent_names__"] = parent_names

        # Write workspace (row-data) to per-project workspace file
        try:
            with open(_save_ws_file, "w", encoding="utf-8") as f:
                json.dump(new_ws, f, indent=2, ensure_ascii=False)
        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"Workspace write failed: {e}")
            return

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
                         "__group_order__", "__parent_names__", "__col_defs__",
                         "__deleted_sections__"}
            # Persist deleted sections — merge with any previously saved ones
            _prev_deleted = set(existing.get("__deleted_sections__", []))
            _all_deleted  = _prev_deleted | self._attr_deleted_sections
            if _all_deleted:
                final_json["__deleted_sections__"] = sorted(_all_deleted)
            # Any key that has ever appeared in __section_order__ (old OR new) is
            # "owned by the UI" — never merge from existing (the UI state is authoritative).
            # Only orphan keys such as coded sub-tables (E_Color, HC_Color, …) are preserved.
            _new_order = set(result.get("__section_order__", []))
            _old_order = set(existing.get("__section_order__", []))
            _ui_owned  = _old_order | _new_order
            # Write explicit empty-list deletion markers for tag sections removed by the user
            for k in _old_order - _new_order:
                if k not in _internal and isinstance(existing.get(k), list):
                    final_json[k] = []  # signals _load_tag_groups to remove from defaults
            for k, v in existing.items():
                if k not in final_json and k not in _internal and k not in _ui_owned:
                    final_json[k] = v

            with open(_save_tags_file, "w", encoding="utf-8") as f:
                json.dump(final_json, f, indent=2, ensure_ascii=False)
        except Exception as e:
            QMessageBox.critical(self, "Save Error", str(e))
            return

        if hasattr(self.app, 'reload_tag_groups'):
            self.app.reload_tag_groups(_proj_name)
        if hasattr(self, '_btn_attr_save'):
            self._flash_saved_btn(self._btn_attr_save)
