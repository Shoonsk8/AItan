import os
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                              QLabel, QGroupBox, QCheckBox, QRadioButton,
                              QButtonGroup, QComboBox, QScrollArea, QSpinBox,
                              QColorDialog)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

import aisearch_config as cfg


class _AppearanceMixin:
    """Mixin: Settings tab, Thresholds/Colors tab, Appearance tab + related methods."""

    def _build_settings_tab(self, tabs):
        def _hsep():
            sep = QWidget(); sep.setFixedHeight(1)
            sep.setStyleSheet("background-color: #555;")
            return sep

        tab_settings = QWidget()
        sl = QVBoxLayout(tab_settings)
        sl.setContentsMargins(15, 10, 15, 10)
        sl.setSpacing(10)

        # Language — first item
        lang_row = QHBoxLayout()
        lang_row.addWidget(QLabel("Language:"))
        self._lang_cb = QComboBox()
        self._lang_cb.wheelEvent = lambda e: e.ignore()
        self._lang_cb.addItem("English", "en")
        self._lang_cb.addItem("日本語", "ja")
        from attr_viewer import _UI_LANG
        _cur_lang = self.app.config.get("ui_language", "en")
        _UI_LANG["val"] = _cur_lang
        _li = self._lang_cb.findData(_cur_lang)
        if _li >= 0:
            self._lang_cb.blockSignals(True)
            self._lang_cb.setCurrentIndex(_li)
            self._lang_cb.blockSignals(False)
        self._lang_cb.setFixedWidth(100)
        def _on_lang_changed(_idx):
            from attr_viewer import _UI_LANG
            lang = self._lang_cb.currentData()
            _UI_LANG["val"] = lang
            self.app.config["ui_language"] = lang
            cfg.save_config(self.app.config, getattr(self.app, "current_project", None))
            _ph = getattr(self.app, "preview_handler", None)
            _pw = getattr(_ph, "window", None)
            _sc = getattr(_pw, "_soft_canvas", None)
            _cur_path = getattr(_ph, "current_path", None)
            for _w in [getattr(self, "_canvas_widget", None), _sc]:
                if _w and hasattr(_w, "refresh_language"):
                    _w.refresh_language()
            if _sc and _cur_path:
                import aisearch_attrs as _am
                _entry = _am.get(self.app.attrs_data, _cur_path)
                _sc.load_file(_cur_path, _entry)
        self._lang_cb.currentIndexChanged.connect(_on_lang_changed)
        lang_row.addWidget(self._lang_cb)
        lang_row.addStretch()
        sl.addLayout(lang_row)
        sl.addWidget(_hsep())

        g3 = QGroupBox("⚙ Viewer Options")
        l3 = QVBoxLayout(g3)
        self.check_viewer = QCheckBox("Don't close viewer when opening new file")
        self.check_viewer.setChecked(self.app.keep_viewer_open)
        self.check_viewer.toggled.connect(self._save_viewer_option)
        l3.addWidget(self.check_viewer)
        self.check_always_on_top = QCheckBox("Preview window always on top")
        self.check_always_on_top.setChecked(self.app.config.get("preview_always_on_top", False))
        self.check_always_on_top.toggled.connect(self._save_always_on_top)
        l3.addWidget(self.check_always_on_top)
        self.check_dbl_spread = QCheckBox("Double-click spreads preview instead of opening external viewer")
        self.check_dbl_spread.setChecked(self.app.config.get("dbl_click_spread", False))
        self.check_dbl_spread.toggled.connect(self._save_dbl_spread)
        l3.addWidget(self.check_dbl_spread)
        sl.addWidget(g3)

        g4 = QGroupBox("🗑 Trash Options")
        l4 = QVBoxLayout(g4)
        self.check_delete_confirm = QCheckBox("Ask for confirmation before moving to Trash")
        self.check_delete_confirm.setChecked(self.app.config.get("delete_confirm", True))
        self.check_delete_confirm.toggled.connect(self._save_delete_confirm)
        l4.addWidget(self.check_delete_confirm)

        self.check_conflict_confirm = QCheckBox("Ask for confirmation on file conflict")
        self.check_conflict_confirm.setChecked(self.app.config.get("conflict_confirm", True))
        self.check_conflict_confirm.toggled.connect(self._save_conflict_confirm)
        l4.addWidget(self.check_conflict_confirm)
        sl.addWidget(g4)

        g_dup = QGroupBox("♊ Duplicate Tools")
        l_dup = QVBoxLayout(g_dup)
        self.check_czkawka_buttons = QCheckBox("Show czkawka import/export buttons")
        self.check_czkawka_buttons.setToolTip(
            "Adds Import/Export buttons to the Duplicates toolbar for\n"
            "round-tripping with czkawka's JSON format")
        self.check_czkawka_buttons.setChecked(self.app.config.get("show_czkawka_buttons", False))
        self.check_czkawka_buttons.toggled.connect(self._save_czkawka_buttons)
        l_dup.addWidget(self.check_czkawka_buttons)
        sl.addWidget(g_dup)

        g5 = QGroupBox("📁 File Conflict on Move")
        l5 = QVBoxLayout(g5)
        self._conflict_group = QButtonGroup(self)
        options = [
            ("size_check",       "Same size → overwrite silently, different size → rename with number (recommended)"),
            ("always_rename",    "Always rename with number (keep both files)"),
            ("always_overwrite", "Always overwrite without asking"),
            ("always_ask",       "Always ask (Overwrite / Rename / Cancel)"),
        ]
        current = self.app.config.get("move_conflict", "size_check")
        for val, label in options:
            rb = QRadioButton(label)
            rb.setProperty("conflict_value", val)
            if val == current: rb.setChecked(True)
            rb.toggled.connect(self._save_conflict_setting)
            self._conflict_group.addButton(rb)
            l5.addWidget(rb)
        sl.addWidget(g5)

        g6 = QGroupBox("🔍 Search Options")
        l6 = QHBoxLayout(g6)
        l6.addWidget(QLabel("Max search results:"))
        self._spin_max_results = QSpinBox()
        self._spin_max_results.setRange(10, 2000)
        self._spin_max_results.setSingleStep(50)
        self._spin_max_results.setValue(self.app.config.get("max_search_results", 300))
        self._spin_max_results.setFixedWidth(80)
        self._spin_max_results.valueChanged.connect(self._save_max_results)
        l6.addWidget(self._spin_max_results)
        l6.addStretch()
        sl.addWidget(g6)

        sl.addStretch()
        tabs.addTab(tab_settings, "⚙ Settings")

    def _build_colors_tab(self, tabs):
        tab_colors = QWidget()
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(tab_colors)
        col_layout = QVBoxLayout(tab_colors)
        col_layout.setContentsMargins(15, 10, 15, 10)
        col_layout.setSpacing(10)

        def color_row(label, get_hex, set_hex):
            row = QHBoxLayout()
            btn = QPushButton()
            btn.setFixedSize(48, 24)
            btn.setStyleSheet(f"background-color: {get_hex()}; border: 1px solid #666;")
            def _pick():
                c = QColorDialog.getColor(QColor(get_hex()), self)
                if c.isValid():
                    set_hex(c.name())
                    btn.setStyleSheet(f"background-color: {c.name()}; border: 1px solid #666;")
                    cfg.save_config(self.app.config, getattr(self.app, "current_project", None))
                    self.app.reload_colors()
            btn.clicked.connect(_pick)
            row.addWidget(QLabel(label)); row.addStretch(); row.addWidget(btn)
            return row

        colors = self.app.config.setdefault("colors", cfg.DEFAULT_COLORS)

        g_sel = QGroupBox("Row Selection")
        gs = QVBoxLayout(g_sel)
        gs.addLayout(color_row("Selected row",
            lambda: colors.get("selection", cfg.DEFAULT_COLORS["selection"]),
            lambda v: colors.update({"selection": v})))
        col_layout.addWidget(g_sel)

        g_dup = QGroupBox("Duplicate Groups")
        gd = QVBoxLayout(g_dup)
        dup_labels = ["Group A — near-exact (≥0.98)", "Group A — similar (≥0.90)",
                      "Group A — pale (≥0.80)",        "Group A — faint (<0.80)",
                      "Group B — near-exact (≥0.98)",  "Group B — similar (≥0.90)",
                      "Group B — pale (≥0.80)",         "Group B — faint (<0.80)"]
        for i, lbl in enumerate(dup_labels):
            family = "dup_a" if i < 4 else "dup_b"
            idx    = i % 4
            gd.addLayout(color_row(lbl,
                lambda f=family, x=idx: colors.get(f, cfg.DEFAULT_COLORS[f])[x],
                lambda v, f=family, x=idx: colors.get(f, cfg.DEFAULT_COLORS[f]).__setitem__(x, v)))
        col_layout.addWidget(g_dup)

        g_score = QGroupBox("Search Score Colors")
        gsc = QVBoxLayout(g_score)
        score_labels = ["Score ≥ 0.98", "Score ≥ 0.92", "Score ≥ 0.85", "Score ≥ 0.75"]
        for i, lbl in enumerate(score_labels):
            gsc.addLayout(color_row(lbl,
                lambda x=i: colors.get("score", cfg.DEFAULT_COLORS["score"])[x],
                lambda v, x=i: colors.get("score", cfg.DEFAULT_COLORS["score"]).__setitem__(x, v)))
        col_layout.addWidget(g_score)

        g_attrs = QGroupBox("Attributes")
        ga = QVBoxLayout(g_attrs)
        ga.addLayout(color_row("Unmarked file (dup view)",
            lambda: colors.get("unmarked", cfg.DEFAULT_COLORS["unmarked"]),
            lambda v: colors.update({"unmarked": v})))
        col_layout.addWidget(g_attrs)

        btn_reset = QPushButton("Reset All to Defaults")
        btn_reset.setStyleSheet(cfg.btn_ss("btn_special", self.app.config, "padding:4px 12px;"))
        btn_reset.clicked.connect(self._reset_colors)
        col_layout.addWidget(btn_reset)
        col_layout.addStretch()
        tabs.addTab(scroll, "🎨 Thresholds")

    def _build_appearance_tab(self, tabs):
        def _hsep():
            sep = QWidget(); sep.setFixedHeight(1)
            sep.setStyleSheet("background-color: #555;")
            return sep

        tab_fonts = QWidget()
        fl = QVBoxLayout(tab_fonts)
        fl.setContentsMargins(20, 15, 20, 15)
        fl.setSpacing(12)

        # Theme
        theme_lbl = QLabel("Theme:")
        fl.addWidget(theme_lbl)
        theme_row = QHBoxLayout()
        theme_group = QButtonGroup(tab_fonts)
        current_theme = self.app.config.get("theme", "Dark")
        for t_name in ("Dark", "Light"):
            rb = QRadioButton(t_name)
            rb.setChecked(current_theme == t_name)
            theme_group.addButton(rb)
            theme_row.addWidget(rb)
            def _theme_changed(checked, name=t_name):
                if checked:
                    self.app.config["theme"] = name
                    cfg.save_config(self.app.config, getattr(self.app, "current_project", None))
                    import aisearch_main as _main
                    _main.apply_theme(name)
                    self.app._apply_header_theme()
            rb.toggled.connect(_theme_changed)
        theme_row.addStretch()
        fl.addLayout(theme_row)

        fl.addWidget(_hsep())

        # Font sizes
        fl.addWidget(QLabel("Font sizes:"))

        def font_row(label, key, default):
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            row.addStretch()
            sp = QSpinBox()
            sp.setRange(6, 28)
            sp.setValue(self.app.config.get(key, default))
            sp.setFixedWidth(60)
            def _changed(val, k=key):
                self.app.config[k] = val
                cfg.save_config(self.app.config, getattr(self.app, "current_project", None))
                self.app.reload_fonts()
            sp.valueChanged.connect(_changed)
            row.addWidget(sp)
            return row

        fl.addLayout(font_row("List (table)",     "table_font_size",   10))
        fl.addLayout(font_row("Attributes panel", "attr_font_size",    10))
        fl.addLayout(font_row("Project name",     "project_font_size", 30))
        fl.addLayout(font_row("General",          "ui_font_size",      10))

        fl.addWidget(_hsep())
        fl.addWidget(QLabel("Face thumbnail size:"))

        def face_thumb_row():
            row = QHBoxLayout()
            row.addWidget(QLabel("Person ID thumbnail (px)"))
            row.addStretch()
            sp = QSpinBox()
            sp.setRange(32, 256)
            sp.setSingleStep(16)
            sp.setValue(self.app.config.get("face_thumb_size", 96))
            sp.setFixedWidth(70)
            def _changed(val):
                self.app.config["face_thumb_size"] = val
                cfg.save_config(self.app.config, getattr(self.app, "current_project", None))
            sp.valueChanged.connect(_changed)
            row.addWidget(sp)
            return row

        fl.addLayout(face_thumb_row())

        fl.addWidget(_hsep())
        fl.addWidget(QLabel("Button Colors:"))

        colors = self.app.config.setdefault("colors", cfg.DEFAULT_COLORS)

        def _btn_color_row(label, key):
            row = QHBoxLayout()
            btn = QPushButton()
            btn.setFixedSize(48, 24)
            btn.setStyleSheet(f"background-color:{colors.get(key, cfg.DEFAULT_COLORS[key])}; border:1px solid #666;")
            def _pick(k=key, b=btn):
                c = QColorDialog.getColor(QColor(colors.get(k, cfg.DEFAULT_COLORS[k])), self)
                if c.isValid():
                    colors[k] = c.name()
                    b.setStyleSheet(f"background-color:{c.name()}; border:1px solid #666;")
                    cfg.save_config(self.app.config, getattr(self.app, "current_project", None))
            btn.clicked.connect(_pick)
            row.addWidget(QLabel(label)); row.addStretch(); row.addWidget(btn)
            return row

        for key, label in [
            ("btn_add",     "Add"),
            ("btn_remove",  "Remove"),
            ("btn_write",   "Write / Save / Overwrite"),
            ("btn_stop",    "Stop"),
            ("btn_special", "Special  (Scan ALL, Reset defaults)"),
        ]:
            fl.addLayout(_btn_color_row(label, key))

        fl.addStretch()
        tabs.addTab(tab_fonts, "🖌 Appearance")

    # --- callbacks ---

    def _reset_colors(self):
        import copy
        self.app.config["colors"] = copy.deepcopy(cfg.DEFAULT_COLORS)
        cfg.save_config(self.app.config, getattr(self.app, "current_project", None))
        self.app.reload_colors()
        self.close()
        self.app._open_settings(tab=2)

    def _save_viewer_option(self, checked):
        self.app.keep_viewer_open = checked
        self.app.config["keep_viewer_open"] = checked
        cfg.save_config(self.app.config, getattr(self.app, "current_project", None))

    def _save_always_on_top(self, checked):
        self.app.config["preview_always_on_top"] = checked
        cfg.save_config(self.app.config, getattr(self.app, "current_project", None))
        # Apply immediately if preview is open
        if self.app.preview_handler.window and self.app.preview_handler.window.isVisible():
            self.app.preview_handler._toggle_always_on_top(checked)

    def _save_dbl_spread(self, checked):
        self.app.config["dbl_click_spread"] = checked
        cfg.save_config(self.app.config, getattr(self.app, "current_project", None))

    def _save_czkawka_buttons(self, checked):
        self.app.config["show_czkawka_buttons"] = checked
        cfg.save_config(self.app.config, getattr(self.app, "current_project", None))
        if hasattr(self.app, "_btn_dup_import"):
            self.app._btn_dup_import.setVisible(checked)
            self.app._btn_dup_export.setVisible(checked)

    def _save_delete_confirm(self, checked):
        self.app.config["delete_confirm"] = checked
        cfg.save_config(self.app.config, getattr(self.app, "current_project", None))

    def _save_conflict_confirm(self, checked):
        self.app.config["conflict_confirm"] = checked
        cfg.save_config(self.app.config, getattr(self.app, "current_project", None))

    def _save_conflict_setting(self):
        for btn in self._conflict_group.buttons():
            if btn.isChecked():
                self.app.config["move_conflict"] = btn.property("conflict_value")
                cfg.save_config(self.app.config, getattr(self.app, "current_project", None))
                break

    def _save_max_results(self, value):
        self.app.config["max_search_results"] = value
        cfg.save_config(self.app.config, getattr(self.app, "current_project", None))
