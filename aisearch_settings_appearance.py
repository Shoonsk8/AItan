import os
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                              QLabel, QGroupBox, QCheckBox, QRadioButton,
                              QButtonGroup, QComboBox, QScrollArea, QSpinBox,
                              QColorDialog)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

import aisearch_config as cfg
from attr_viewer import _lang_label as _t


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
        lang_row.addWidget(QLabel(_t("Language: / 言語：")))
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
            if hasattr(self, "rebuild_for_language"):
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, self.rebuild_for_language)
            if _sc and _cur_path:
                import aisearch_attrs as _am
                _entry = _am.get(self.app.attrs_data, _cur_path)
                _sc.load_file(_cur_path, _entry)
        self._lang_cb.currentIndexChanged.connect(_on_lang_changed)
        lang_row.addWidget(self._lang_cb)
        lang_row.addStretch()
        sl.addLayout(lang_row)
        sl.addWidget(_hsep())

        g3 = QGroupBox(_t("⚙ Viewer Options / ⚙ 表示オプション"))
        l3 = QVBoxLayout(g3)
        self.check_viewer = QCheckBox(_t("Don't close viewer when opening new file / 新しいファイルを開くときビューアを閉じない"))
        self.check_viewer.setChecked(self.app.keep_viewer_open)
        self.check_viewer.toggled.connect(self._save_viewer_option)
        l3.addWidget(self.check_viewer)
        self.check_always_on_top = QCheckBox(_t("Preview window always on top / プレビューを常に前面に表示"))
        self.check_always_on_top.setChecked(self.app.config.get("preview_always_on_top", False))
        self.check_always_on_top.toggled.connect(self._save_always_on_top)
        l3.addWidget(self.check_always_on_top)
        self.check_dbl_spread = QCheckBox(_t("Double-click spreads preview / ダブルクリックでプレビュー拡大"))
        self.check_dbl_spread.setChecked(self.app.config.get("dbl_click_spread", False))
        self.check_dbl_spread.toggled.connect(self._save_dbl_spread)
        l3.addWidget(self.check_dbl_spread)
        sl.addWidget(g3)

        g4 = QGroupBox(_t("🗑 Trash Options / 🗑 ゴミ箱オプション"))
        l4 = QVBoxLayout(g4)
        self.check_delete_confirm = QCheckBox(_t("Ask confirmation before moving to Trash / ゴミ箱に移動する前に確認"))
        self.check_delete_confirm.setChecked(self.app.config.get("delete_confirm", True))
        self.check_delete_confirm.toggled.connect(self._save_delete_confirm)
        l4.addWidget(self.check_delete_confirm)

        self.check_conflict_confirm = QCheckBox(_t("Ask confirmation on file conflict / ファイル競合時に確認"))
        self.check_conflict_confirm.setChecked(self.app.config.get("conflict_confirm", True))
        self.check_conflict_confirm.toggled.connect(self._save_conflict_confirm)
        l4.addWidget(self.check_conflict_confirm)
        sl.addWidget(g4)

        g_dup = QGroupBox(_t("♊ Duplicate Tools / ♊ 重複ツール"))
        l_dup = QVBoxLayout(g_dup)
        self.check_czkawka_buttons = QCheckBox(_t("Show czkawka import/export buttons / czkawkaボタンを表示"))
        self.check_czkawka_buttons.setChecked(self.app.config.get("show_czkawka_buttons", False))
        self.check_czkawka_buttons.toggled.connect(self._save_czkawka_buttons)
        l_dup.addWidget(self.check_czkawka_buttons)
        sl.addWidget(g_dup)

        g5 = QGroupBox(_t("📁 File Conflict on Move / 📁 移動時のファイル競合"))
        l5 = QVBoxLayout(g5)
        self._conflict_group = QButtonGroup(self)
        options = [
            ("size_check",       _t("Same size→overwrite, different size→rename (recommended) / 同サイズ→上書き、違うサイズ→番号付きリネーム（推奨）")),
            ("always_rename",    _t("Always rename with number (keep both) / 常に番号付きリネーム（両方保持）")),
            ("always_overwrite", _t("Always overwrite without asking / 確認なしに常に上書き")),
            ("always_ask",       _t("Always ask (Overwrite/Rename/Cancel) / 常に確認（上書き/リネーム/キャンセル）")),
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

        g6 = QGroupBox(_t("🔍 Search Options / 🔍 検索オプション"))
        l6 = QHBoxLayout(g6)
        l6.addWidget(QLabel(_t("Max search results: / 最大検索件数：")))
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
        tabs.addTab(tab_settings, _t("⚙ Settings / ⚙ 設定"))

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

        g_sel = QGroupBox(_t("Row Selection / 行選択"))
        gs = QVBoxLayout(g_sel)
        gs.addLayout(color_row(_t("Selected row / 選択行"),
            lambda: colors.get("selection", cfg.DEFAULT_COLORS["selection"]),
            lambda v: colors.update({"selection": v})))
        col_layout.addWidget(g_sel)

        g_dup = QGroupBox(_t("Duplicate Groups / 重複グループ"))
        gd = QVBoxLayout(g_dup)
        dup_labels = [
            _t("Group A — near-exact (≥0.98) / グループA — ほぼ同一 (≥0.98)"),
            _t("Group A — similar (≥0.90) / グループA — 類似 (≥0.90)"),
            _t("Group A — pale (≥0.80) / グループA — 薄い (≥0.80)"),
            _t("Group A — faint (<0.80) / グループA — 微 (<0.80)"),
            _t("Group B — near-exact (≥0.98) / グループB — ほぼ同一 (≥0.98)"),
            _t("Group B — similar (≥0.90) / グループB — 類似 (≥0.90)"),
            _t("Group B — pale (≥0.80) / グループB — 薄い (≥0.80)"),
            _t("Group B — faint (<0.80) / グループB — 微 (<0.80)"),
        ]
        for i, lbl in enumerate(dup_labels):
            family = "dup_a" if i < 4 else "dup_b"
            idx    = i % 4
            gd.addLayout(color_row(lbl,
                lambda f=family, x=idx: colors.get(f, cfg.DEFAULT_COLORS[f])[x],
                lambda v, f=family, x=idx: colors.get(f, cfg.DEFAULT_COLORS[f]).__setitem__(x, v)))
        col_layout.addWidget(g_dup)

        g_score = QGroupBox(_t("Search Score Colors / 検索スコア色"))
        gsc = QVBoxLayout(g_score)
        score_labels = [_t("Score ≥ 0.98 / スコア ≥ 0.98"), _t("Score ≥ 0.92 / スコア ≥ 0.92"),
                        _t("Score ≥ 0.85 / スコア ≥ 0.85"), _t("Score ≥ 0.75 / スコア ≥ 0.75")]
        for i, lbl in enumerate(score_labels):
            gsc.addLayout(color_row(lbl,
                lambda x=i: colors.get("score", cfg.DEFAULT_COLORS["score"])[x],
                lambda v, x=i: colors.get("score", cfg.DEFAULT_COLORS["score"]).__setitem__(x, v)))
        col_layout.addWidget(g_score)

        g_attrs = QGroupBox(_t("Attributes / 属性"))
        ga = QVBoxLayout(g_attrs)
        ga.addLayout(color_row(_t("Unmarked file (dup view) / 未マークファイル（重複表示）"),
            lambda: colors.get("unmarked", cfg.DEFAULT_COLORS["unmarked"]),
            lambda v: colors.update({"unmarked": v})))
        col_layout.addWidget(g_attrs)

        btn_reset = QPushButton(_t("Reset All to Defaults / すべてデフォルトに戻す"))
        btn_reset.setStyleSheet(cfg.btn_ss("btn_special", self.app.config, "padding:4px 12px;"))
        btn_reset.clicked.connect(self._reset_colors)
        col_layout.addWidget(btn_reset)
        col_layout.addStretch()
        tabs.addTab(scroll, _t("🎨 Thresholds / 🎨 閾値"))

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
        theme_lbl = QLabel(_t("Theme: / テーマ："))
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
        fl.addWidget(QLabel(_t("Font sizes: / フォントサイズ：")))

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

        fl.addLayout(font_row(_t("List (table) / リスト（テーブル）"),     "table_font_size",   10))
        fl.addLayout(font_row(_t("Attributes panel / 属性パネル"),        "attr_font_size",    10))
        fl.addLayout(font_row(_t("Project name / プロジェクト名"),         "project_font_size", 30))
        fl.addLayout(font_row(_t("General / 全般"),                       "ui_font_size",      10))

        fl.addWidget(_hsep())
        fl.addWidget(QLabel(_t("Face thumbnail size: / 顔サムネイルサイズ：")))

        def face_thumb_row():
            row = QHBoxLayout()
            row.addWidget(QLabel(_t("Person ID thumbnail (px) / 人物IDサムネイル (px)")))
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
        fl.addWidget(QLabel(_t("Button Colors: / ボタンカラー：")))

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
            ("btn_add",     _t("Add / 追加")),
            ("btn_remove",  _t("Remove / 削除")),
            ("btn_write",   _t("Write/Save/Overwrite / 書き込み/保存/上書き")),
            ("btn_stop",    _t("Stop / 停止")),
            ("btn_special", _t("Special (Scan ALL, Reset) / 特殊（全スキャン、リセット）")),
        ]:
            fl.addLayout(_btn_color_row(label, key))

        fl.addStretch()
        tabs.addTab(tab_fonts, _t("🖌 Appearance / 🖌 外観"))

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
