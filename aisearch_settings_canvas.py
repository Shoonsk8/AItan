"""Canvas tab — embeds AttrViewerWidget (the free-canvas attr panel editor)."""
import os, shutil
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QCheckBox, QPushButton
import aisearch_config as cfg


class _CanvasMixin:
    """Settings mixin: Canvas tab containing the AttrViewer panel canvas."""

    def _build_canvas_tab(self, tabs):
        from attr_viewer import AttrViewerWidget
        import aisearch_attrs as _am

        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(4, 4, 4, 0)
        lay.setSpacing(4)

        # ── Project bar ───────────────────────────────────────────────────────
        proj_bar = QHBoxLayout()
        proj_bar.setSpacing(6)
        proj_bar.addWidget(QLabel("Project:"))

        _projects = ["default"] + sorted([
            f.replace("features_", "").replace(".pt", "")
            for f in os.listdir(_am.DATA_DIR)
            if f.startswith("features_") and f.endswith(".pt")
        ])
        self._canvas_proj_cb = QComboBox()
        self._canvas_proj_cb.addItems(_projects)
        _cur_proj = getattr(self.app, "current_project", "") or ""
        _idx = self._canvas_proj_cb.findText(_cur_proj)
        self._canvas_proj_cb.blockSignals(True)
        if _idx >= 0:
            self._canvas_proj_cb.setCurrentIndex(_idx)
        self._canvas_proj_cb.blockSignals(False)
        self._canvas_proj_cb.setStyleSheet(
            "QComboBox{background:#2e2e2e;color:#ddd;border:1px solid #555;"
            "padding:2px 8px;border-radius:3px;font-size:9pt;}"
            "QComboBox::drop-down{border:none;}"
            "QComboBox QAbstractItemView{background:#2e2e2e;color:#ddd;"
            "selection-background-color:#4a7a4e;}")
        self._canvas_proj_cb.setFixedWidth(160)
        proj_bar.addWidget(self._canvas_proj_cb)

        btn_load = QPushButton("Load")
        btn_load.setStyleSheet("background:#1e6e1e; color:white; font-weight:bold; padding:3px 8px;")
        proj_bar.addWidget(btn_load)

        self._btn_canvas_save = btn_overwrite = QPushButton("💾 Overwrite")
        btn_overwrite.setStyleSheet(cfg.btn_ss("btn_write", self.app.config))
        proj_bar.addWidget(btn_overwrite)

        self._canvas_editing_lbl = QLabel(f"Editing: {_cur_proj}")
        self._canvas_editing_lbl.setStyleSheet("color:#aaa; font-style:italic;")
        proj_bar.addWidget(self._canvas_editing_lbl)

        proj_bar.addStretch()

        self._chk_show_raw_data = QCheckBox("Arrangement in preview")
        self._chk_show_raw_data.setStyleSheet("color:#ccc;")
        self._chk_show_raw_data.setChecked(self.app.config.get("show_raw_data", False))
        def _on_raw_data_toggle(v):
            self.app.config["show_raw_data"] = v
            cfg.save_config(self.app.config, getattr(self.app, "current_project", None))
            pw = getattr(getattr(self.app, "preview_handler", None), "window", None)
            if pw:
                sec = getattr(pw, "_raw_meta_sec", None)
                if sec: sec.setVisible(v)
                pc = getattr(pw, "_protected_check", None)
                if pc: pc.setVisible(v)
                sc = getattr(pw, "_soft_canvas", None)
                if sc:
                    snap_cb = getattr(sc, "_snap_cb", None)
                    drag_cb = getattr(sc, "_drag_cb", None)
                    if snap_cb:
                        snap_cb.setEnabled(v)
                        if not v: snap_cb.setChecked(False); sc._set_snap(False)
                    if drag_cb:
                        drag_cb.setEnabled(v)
                        if not v: drag_cb.setChecked(False)
        self._chk_show_raw_data.toggled.connect(_on_raw_data_toggle)
        proj_bar.addWidget(self._chk_show_raw_data)

        # ── CLIP inspect trigger ──────────────────────────────────────────────
        _insp_lbl = QLabel("CLIP Inspect:")
        _insp_lbl.setStyleSheet("color:#ccc; font-size:9pt;")
        proj_bar.addWidget(_insp_lbl)
        self._clip_inspect_mode_cb = QComboBox()
        self._clip_inspect_mode_cb.wheelEvent = lambda e: e.ignore()
        self._clip_inspect_mode_cb.addItem("No inspection",    "never")
        self._clip_inspect_mode_cb.addItem("All the time",     "always")
        self._clip_inspect_mode_cb.addItem("On watch receive", "watch")
        _cur_mode = self.app.config.get("clip_inspect_mode", "never")
        _mi = self._clip_inspect_mode_cb.findData(_cur_mode)
        if _mi >= 0:
            self._clip_inspect_mode_cb.setCurrentIndex(_mi)
        def _on_inspect_mode(idx):
            mode = self._clip_inspect_mode_cb.currentData()
            self.app.config["clip_inspect_mode"] = mode
            cfg.save_config(self.app.config, getattr(self.app, "current_project", None))
        self._clip_inspect_mode_cb.currentIndexChanged.connect(_on_inspect_mode)
        proj_bar.addWidget(self._clip_inspect_mode_cb)

        lay.addLayout(proj_bar)

        # ── Canvas widget ─────────────────────────────────────────────────────
        cfg_path = _am.tags_file_for_project(_cur_proj)
        self._canvas_widget = AttrViewerWidget(config_path=cfg_path, parent=tab)
        self._canvas_editing_proj = _cur_proj
        lay.addWidget(self._canvas_widget)

        def _preview_canvas():
            pw = getattr(getattr(self.app, "preview_handler", None), "window", None)
            return getattr(pw, "_soft_canvas", None) if pw else None

        def _sync_move_to_preview(key, x, y):
            sc = _preview_canvas()
            if not sc:
                return
            for w in sc.widgets:
                if w.key == key:
                    w.move(x, y)
                    sc._apply_connections_for(key)
                    break
            canvas = getattr(sc, "canvas", None)
            if canvas and sc.widgets:
                bottom = max(w.y() + w.height() for w in sc.widgets)
                canvas.setMinimumHeight(max(1000, bottom + 40))

        def _sync_resize_to_preview(key):
            sc = _preview_canvas()
            if not sc:
                return
            src_map = {w.key: w for w in self._canvas_widget.widgets}
            for w in sc.widgets:
                if w.key == key:
                    src = src_map.get(key)
                    if src:
                        w.resize(src.width(), src.height())
                    break

        def _wire_preview_sync():
            for w in self._canvas_widget.widgets:
                w.moved.connect(lambda k, x, y: _sync_move_to_preview(k, x, y))
                w.resized.connect(lambda k: _sync_resize_to_preview(k))

        _wire_preview_sync()

        # ── Button actions ────────────────────────────────────────────────────
        def _do_load():
            name = self._canvas_proj_cb.currentText()
            path = _am.tags_file_for_project(name)
            self._canvas_widget.reload(path)
            self._canvas_editing_proj = name
            self._canvas_editing_lbl.setText(f"Editing: {name}")
            _wire_preview_sync()

        def _do_overwrite():
            from PyQt6.QtWidgets import QMessageBox, QCheckBox as _QCB
            target = self._canvas_proj_cb.currentText()
            src    = _am.tags_file_for_project(self._canvas_editing_proj)
            dst    = _am.tags_file_for_project(target)
            if src == dst:
                return
            if not getattr(self, '_canvas_overwrite_skip_warn', False):
                _mb = QMessageBox(self)
                _mb.setIcon(QMessageBox.Icon.Warning)
                _mb.setWindowTitle("Overwrite")
                _mb.setText(f"This will overwrite the canvas layout for <b>'{target}'</b>.<br>Continue?")
                _mb.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                _cb = _QCB("Don't show this warning again")
                _mb.setCheckBox(_cb)
                if _mb.exec() != QMessageBox.StandardButton.Yes:
                    return
                if _cb.isChecked():
                    self._canvas_overwrite_skip_warn = True
            if os.path.exists(src):
                shutil.copy2(src, dst)
            # Also copy the per-project SQLite DB (positions, sizes, connections)
            from attr_viewer import _db_file_for_config
            src_db = _db_file_for_config(src)
            dst_db = _db_file_for_config(dst)
            if os.path.exists(src_db):
                shutil.copy2(src_db, dst_db)
            if hasattr(self, '_btn_canvas_save'):
                self._flash_saved_btn(self._btn_canvas_save)

        btn_load.clicked.connect(_do_load)
        btn_overwrite.clicked.connect(_do_overwrite)

        tabs.addTab(tab, "🖼 Canvas")

        # Auto-sync: whenever this tab becomes active, reload from current project
        def _on_tab_changed(idx):
            if tabs.widget(idx) is not tab:
                return
            cur = getattr(self.app, "current_project", "") or ""
            _i = self._canvas_proj_cb.findText(cur)
            if _i >= 0:
                self._canvas_proj_cb.blockSignals(True)
                self._canvas_proj_cb.setCurrentIndex(_i)
                self._canvas_proj_cb.blockSignals(False)
            _do_load()

        tabs.currentChanged.connect(_on_tab_changed)
