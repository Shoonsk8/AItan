import os, queue, threading, time, torch, shutil
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                              QLabel, QLineEdit, QGroupBox, QCheckBox,
                              QProgressBar, QComboBox, QMessageBox,
                              QTableWidget, QTableWidgetItem, QHeaderView,
                              QListWidget, QApplication, QDateTimeEdit,
                              QSpinBox)
from PyQt6.QtCore import Qt, QTimer, QDateTime
from PyQt6.QtGui import QColor
from attr_viewer import _lang_label as _t

import aisearch_logic as logic
import aisearch_config as cfg
from aisearch_config import FolderPickerDialog
import aisearch_attrs as attrs_mod


class _DbMixin:
    """Mixin: Database tab builder + all DB/scanning related methods."""

    def _build_db_tab(self, tabs):
        from PyQt6.QtWidgets import QScrollArea, QRadioButton, QButtonGroup, QCheckBox as _QCB
        tab_data = QWidget()
        tab_outer = QVBoxLayout(tab_data)
        tab_outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        inner = QWidget()
        tl = QVBoxLayout(inner)
        tl.setContentsMargins(15, 10, 15, 10)
        tl.setSpacing(10)
        scroll.setWidget(inner)
        tab_outer.addWidget(scroll)

        # ── Switch Project ────────────────────────────────────────────────────
        g1 = QGroupBox(_t("📂 Switch Project / 📂 プロジェクト切替"))
        l1 = QHBoxLayout(g1)
        self.db_projects = sorted([
            f.replace('features_', '').replace('.pt', '')
            for f in os.listdir(attrs_mod.DATA_DIR) if f.startswith('features_') and f.endswith('.pt')
        ])
        self.proj_combo = QComboBox()
        self.proj_combo.wheelEvent = lambda e: e.ignore()
        self.proj_combo.addItems(self.db_projects)
        self.proj_combo.setCurrentText(self.app.current_project)
        self.proj_combo.currentTextChanged.connect(self._on_project_select)
        l1.addWidget(self.proj_combo, stretch=1)
        # Color swatch — click to pick background color for the SELECTED
        # project in the combo (not necessarily the active one). Shows the
        # current color; click to open color picker.
        self.btn_proj_color = QPushButton()
        self.btn_proj_color.setFixedSize(28, 28)
        self.btn_proj_color.setToolTip(_t(
            "Click to set this project's thumbnail/preview background color "
            "/ クリックでプロジェクトの背景色を設定"))
        self.btn_proj_color.clicked.connect(self._pick_project_color)
        self._refresh_proj_color_swatch()
        l1.addWidget(self.btn_proj_color)
        self.btn_load   = QPushButton(_t("Load / 読み込み"))
        self.btn_load.setToolTip(_t("Load selected project / 選択したプロジェクトを読み込む"))
        self.btn_load.clicked.connect(self.switch_project)
        self.btn_load.setStyleSheet("background-color: #2a7a2a; color: white; font-weight: bold;")
        self.chk_close_on_load = QCheckBox(_t("Close / 閉じる"))
        self.chk_close_on_load.setToolTip(_t("Close settings window when Load is pressed / 読み込み時に設定を閉じる"))
        self.chk_close_on_load.setChecked(self.app.config.get("close_on_load", True))
        self.chk_close_on_load.toggled.connect(
            lambda v: self.app.config.update({"close_on_load": v}))
        self.btn_delete = QPushButton(_t("Delete / 削除"))
        self.btn_delete.setToolTip(_t("Delete project entirely / プロジェクトを完全に削除"))
        self.btn_delete.clicked.connect(self.delete_project)
        self.btn_delete.setStyleSheet(cfg.btn_ss("btn_remove", self.app.config))
        for b in [self.btn_load, self.chk_close_on_load, self.btn_delete]:
            l1.addWidget(b)
        tl.addWidget(g1)

        # ── Create / Update Database ─────────────────────────────────────────
        g2 = QGroupBox(_t("🛠 Create/Update Database / 🛠 DB作成/更新"))
        l2 = QVBoxLayout(g2)
        proj_name_row = QHBoxLayout()
        proj_name_row.addWidget(QLabel(_t("Project Name: / プロジェクト名：")))
        self.new_proj_entry = QLineEdit()
        self.new_proj_entry.setText(self.app.current_project or "")
        self.new_proj_entry.setPlaceholderText(_t(
            "Enter name for new or existing project… / "
            "新規・既存プロジェクト名を入力…"))
        self.new_proj_entry.setMinimumWidth(220)
        proj_name_row.addWidget(self.new_proj_entry, stretch=1)
        def _on_proj_name_changed(text):
            text = text.strip()
            existing = [f.replace('features_', '').replace('.pt', '')
                        for f in os.listdir(attrs_mod.DATA_DIR) if f.startswith('features_') and f.endswith('.pt')]
            if text in existing:
                # Load that project's dirs
                self.dir_listbox.setRowCount(0)
                self._fill_dirs(text, False)
            else:
                # New project name — preserve any directories the user has
                # already added. The previous behavior cleared the listbox on
                # every keystroke, wiping the user's setup as they typed.
                self._update_generate_btn()
        self.new_proj_entry.textChanged.connect(_on_proj_name_changed)
        btn_new_proj = QPushButton(_t("Register / 登録"))
        btn_new_proj.setToolTip(_t("Register a new project with the current name and directories / 現在の名前とディレクトリで新しいプロジェクトを登録"))
        btn_new_proj.setMinimumWidth(120)
        btn_new_proj.setStyleSheet(cfg.btn_ss("btn_write", self.app.config))
        btn_new_proj.clicked.connect(self._create_new_project)
        proj_name_row.addWidget(btn_new_proj)
        l2.addLayout(proj_name_row)

        self.dir_listbox = QTableWidget(0, 2)
        self.dir_listbox.setFixedHeight(150)
        self.dir_listbox.setHorizontalHeaderLabels([_t("Directory / ディレクトリ"), _t("Recursive / 再帰")])
        self.dir_listbox.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.dir_listbox.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.dir_listbox.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.dir_listbox.verticalHeader().setVisible(False)
        l2.addWidget(self.dir_listbox)

        bf = QHBoxLayout()
        self.btn_add_dir    = QPushButton(_t("+ Add / + 追加")); self.btn_add_dir.clicked.connect(self.add_dir)
        self.btn_add_dir.setStyleSheet(cfg.btn_ss("btn_add", self.app.config))
        self.btn_remove_dir = QPushButton(_t("Remove / 削除"));  self.btn_remove_dir.clicked.connect(self.remove_selected_dirs)
        self.btn_remove_dir.setStyleSheet(cfg.btn_ss("btn_remove", self.app.config))
        bf.addWidget(self.btn_add_dir); bf.addWidget(self.btn_remove_dir); bf.addStretch()
        l2.addLayout(bf)

        self.progress_label = QLabel(_t("Status: Ready / 状態：準備完了"))
        l2.addWidget(self.progress_label)
        self.progress_bar = QProgressBar(); self.progress_bar.setRange(0, 100); self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")
        l2.addWidget(self.progress_bar)

        # Status label (used by scan operations)
        self.lbl_scan_project = QLabel()
        self._update_scan_project_label()
        l2.addWidget(self.lbl_scan_project)
        self.lbl_scan = QLabel("")
        l2.addWidget(self.lbl_scan)

        # ── 3 action buttons ──────────────────────────────────────────────────
        action_row = QHBoxLayout()
        self.btn_generate = QPushButton(_t("Scan ALL / 全スキャン"))
        self.btn_generate.setToolTip(_t("Process every file: CLIP+face+metadata. WARNING: resets from scratch. / 全ファイル処理：CLIP+顔+メタデータ。警告：最初からリビルド。"))
        self.btn_generate.clicked.connect(lambda: self.execute_generate(reset=True))
        self.btn_generate.setStyleSheet(cfg.btn_ss("btn_special", self.app.config, "padding:6px;"))
        action_row.addWidget(self.btn_generate, stretch=1)

        self.btn_scan_new = QPushButton(_t("Update / 更新"))
        self.btn_scan_new.setToolTip(_t("Process only new / unprocessed files. / 新規・未処理のファイルのみ処理。"))
        self.btn_scan_new.clicked.connect(lambda: self.execute_generate(reset=False))
        self.btn_scan_new.setStyleSheet(
            "background-color: #1a5a1a; color: white; font-weight: bold; padding: 6px;")
        action_row.addWidget(self.btn_scan_new, stretch=1)

        self.btn_stop = QPushButton(_t("Stop / 停止"))
        self.btn_stop.setEnabled(False)
        self.btn_stop.setStyleSheet(cfg.btn_ss("btn_stop", self.app.config))
        self.btn_stop.clicked.connect(self._unified_stop)
        action_row.addWidget(self.btn_stop)
        l2.addLayout(action_row)
        self._update_generate_btn()

        # ── Schedule Update ───────────────────────────────────────────────────
        sched_row = QHBoxLayout()
        sched_row.addWidget(QLabel(_t("Run Update at: / 更新の実行時刻：")))
        self.dt_schedule = QDateTimeEdit()
        self.dt_schedule.setDisplayFormat("HH:mm")
        self.dt_schedule.setDateTime(QDateTime.currentDateTime().addSecs(3600))
        sched_row.addWidget(self.dt_schedule)
        self.btn_schedule = QPushButton(_t("⏰ Schedule / ⏰ 予約"))
        self.btn_schedule.setStyleSheet(
            "background-color: #2a4a6a; color: white; padding: 4px 10px;")
        self.btn_schedule.clicked.connect(self._toggle_schedule_update)
        sched_row.addWidget(self.btn_schedule)
        # Recurring update — same row, right of the one-shot. Mutually
        # exclusive with the one-shot; arming either cancels the other.
        sched_row.addSpacing(12)
        sched_row.addWidget(QLabel(_t("Every / 毎")))
        self.sp_recurring = QSpinBox()
        self.sp_recurring.setRange(1, 1440)
        self.sp_recurring.setValue(int(self.app.config.get("update_every_min", 30) or 30))
        self.sp_recurring.setSuffix(_t(" min / 分"))
        sched_row.addWidget(self.sp_recurring)
        self.btn_recurring = QPushButton(_t("🔁 Cycle / 🔁 周期"))
        self.btn_recurring.setStyleSheet(
            "background-color: #2a4a6a; color: white; padding: 4px 10px;")
        self.btn_recurring.clicked.connect(self._toggle_recurring_update)
        sched_row.addWidget(self.btn_recurring)
        self.lbl_schedule_status = QLabel("")
        sched_row.addWidget(self.lbl_schedule_status, stretch=1)
        l2.addLayout(sched_row)

        # ── Utility buttons row ───────────────────────────────────────────────
        util_row = QHBoxLayout()
        btn_rename_util = QPushButton(_t("✏ Rename Files / ✏ ファイルリネーム"))
        btn_rename_util.setToolTip(_t(
            "Rename all project files to coded format (no CLIP/face scan).\n"
            "Update mode: rename only files not yet in coded format. / "
            "全プロジェクトファイルをコード形式にリネーム（CLIP・顔スキャンなし）。\n"
            "Updateモード：未コード化のファイルのみリネーム。"))
        btn_rename_util.setStyleSheet(
            "background-color: #2a2a4a; color: #aaaaff; padding: 4px 8px;")
        btn_rename_util.clicked.connect(
            lambda: self._rename_only() if True else self._rename_new_files())
        # Use Scan ALL vs Update button state to decide
        btn_rename_util.clicked.disconnect()
        btn_rename_util.clicked.connect(self._rename_util_clicked)
        util_row.addWidget(btn_rename_util)

        btn_redetect = QPushButton(_t("🔄 Re-detect All / 🔄 再検出"))
        btn_redetect.setToolTip(_t(
            "Re-run metadata + tag detection on all DB files.\n"
            "Picks up new filename rules (e.g. -watermark) on existing files.\n"
            "No CLIP re-encoding — fast. / "
            "全DBファイルでメタデータ+タグ検出を再実行。\n"
            "既存ファイルの新しいファイル名ルール（-watermarkなど）を反映。\n"
            "CLIP再エンコードなし — 高速。"))
        btn_redetect.setStyleSheet(
            "background-color: #2a3a2a; color: #aaffaa; padding: 4px 8px;")
        btn_redetect.clicked.connect(self._auto_detect_all)
        util_row.addWidget(btn_redetect)

        btn_unlock_util = QPushButton(_t("🔓 Unlock All / 🔓 全解除"))
        btn_unlock_util.setToolTip(_t(
            "Run metadata scan on all files to set editable flag — no CLIP scan. / "
            "全ファイルでメタデータスキャンを実行しeditableフラグを設定 — CLIPスキャンなし。"))
        btn_unlock_util.setStyleSheet(
            "background-color: #2a3a2a; color: #aaffaa; padding: 4px 8px;")
        btn_unlock_util.clicked.connect(self._unlock_all_metadata)
        util_row.addWidget(btn_unlock_util)

        btn_fix_moved = QPushButton(_t("🔍 Fix Moved Files / 🔍 移動ファイル修正"))
        btn_fix_moved.setToolTip(_t(
            "Scan configured directories for files matching missing DB entries.\n"
            "Remaps moved/renamed paths without re-scanning. / "
            "設定済みディレクトリを走査し、見つからないDBエントリに合致するファイルを検索。\n"
            "再スキャンせずに移動・改名されたパスを再マップ。"))
        btn_fix_moved.setStyleSheet(
            "background-color: #2a2a4a; color: #aaaaff; padding: 4px 8px;")
        btn_fix_moved.clicked.connect(self._rescan_moved_files)
        util_row.addWidget(btn_fix_moved)

        btn_embed_aitan = QPushButton(_t("📎 Embed AItan{} / 📎 AItan{}埋め込み"))
        btn_embed_aitan.setToolTip(_t(
            "Write AItan{} metadata block into every file's embedded comment/description.\n"
            "Backfills files added before auto-embedding was enabled. / "
            "全ファイルの埋め込みcomment/descriptionにAItan{}メタデータブロックを書き込む。\n"
            "自動埋め込み有効化前のファイルにも追記。"))
        btn_embed_aitan.setStyleSheet(
            "background-color: #2a3a4a; color: #aaccff; padding: 4px 8px;")
        btn_embed_aitan.clicked.connect(self._embed_aitan_all)
        util_row.addWidget(btn_embed_aitan)

        util_row.addStretch()
        l2.addLayout(util_row)

        self._btn_rename_util = btn_rename_util

        # auto_rename UI removed — rename is explicit via the 🪪 Rename button
        # on the preview window. Stub the attr so legacy code doesn't crash.
        self.chk_rename_on_scan = _QCB()
        self.chk_rename_on_scan.setVisible(False)

        self._stop_rename_only = False
        self._stop_scan_all = False
        self.btn_stop_scan = self.btn_stop

        tl.addWidget(g2)

        # ── Watch Folders ─────────────────────────────────────────────────────
        g_watch = QGroupBox(_t("👁 Watch Folders (auto-add new files) / 👁 監視フォルダ（自動追加）"))
        gw = QVBoxLayout(g_watch)
        gw.addWidget(QLabel(_t("New files dropped here are added to the DB automatically: / ここに追加されたファイルは自動的にDBに登録されます：")))
        self._watch_dir_list = QListWidget()
        self._watch_dir_list.setFixedHeight(80)
        # watch_dirs is global — always read from global config
        _g_watch = cfg.load_config().get("watch_dirs", [])
        for d in _g_watch:
            self._watch_dir_list.addItem(d)
        gw.addWidget(self._watch_dir_list)
        wr = QHBoxLayout()
        btn_add_w = QPushButton(_t("+ Add / + 追加")); btn_add_w.clicked.connect(self._add_watch_dir)
        btn_add_w.setStyleSheet(cfg.btn_ss("btn_add", self.app.config))
        btn_rem_w = QPushButton(_t("Remove / 削除")); btn_rem_w.clicked.connect(self._remove_watch_dir)
        btn_rem_w.setStyleSheet(cfg.btn_ss("btn_remove", self.app.config))
        wr.addWidget(btn_add_w); wr.addWidget(btn_rem_w); wr.addStretch()
        gw.addLayout(wr)
        tl.addWidget(g_watch)

        tl.addStretch()
        tabs.addTab(tab_data, _t("🗄 Database / 🗄 データベース"))

        # Restore the Cycle if it was armed at last shutdown. Deferred so
        # the rest of the tab finishes painting first, and we don't
        # accidentally re-arm when toggling on after settings is open.
        if (self.app.config.get("update_cycle_armed", False)
                and not getattr(self, '_recurring_armed', False)):
            QTimer.singleShot(0, lambda: (
                None if getattr(self, '_recurring_armed', False)
                else self._toggle_recurring_update()))

    # --- scanning ---

    def execute_generate(self, reset=True, auto_apply_moves=False):
        if self._is_scanning:
            # Auto-recover: if no poll timer is active, the scan finished but _is_scanning
            # was never cleared (old bug / exception). Force-reset so the user can retry.
            timer_live = self._poll_timer is not None and self._poll_timer.isActive()
            if not timer_live and not getattr(self, '_is_metadata_scanning', False):
                self._is_scanning = False
                self._toggle_ui(False)
                self.btn_scan_new.setEnabled(True)
                self.btn_stop.setEnabled(False)
                self.btn_stop.setText(_t("Stop / 停止"))
            else:
                self.lbl_scan.setText(_t(
                    "Scan already in progress — press Stop to cancel. / "
                    "スキャンが既に進行中 — Stopで中止。"))
                return
        name = self.new_proj_entry.text().strip()
        dirs_flags  = self._get_dirs_with_flags()
        # Update scans ONLY the project's own configured directories.
        # The previous behavior ALSO walked every global watch_dir, which
        # silently pulled unrelated files into whichever project was
        # being updated (see d7257f9). Removed per user instruction —
        # watch_dirs are for live drop-detection, not Update DB.
        dirs     = [d       for d, _      in dirs_flags]
        no_subs  = [no_sub  for _, no_sub in dirs_flags]
        if not name:
            self.lbl_scan.setText(_t(
                "Enter a project name first. / プロジェクト名を入力してください。")); return
        if not dirs:
            self.lbl_scan.setText(_t(
                "No directories configured — use '+ Add' first. / "
                "ディレクトリ未設定 — まず「+ 追加」してください。")); return

        dir_list = "\n".join(f"  • {d}" for d in dirs)
        if reset:
            reply = QMessageBox.question(
                self, "Scan ALL — Confirmation",
                f"⚠️  This will RESET and rebuild the entire database for:\n"
                f"  Project: {name}\n"
                f"  Directories:\n{dir_list}\n\n"
                f"All existing embeddings (CLIP + face) will be deleted. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return
            pt = os.path.join(attrs_mod.DATA_DIR, f"features_{name}.pt")
            if os.path.exists(pt):
                os.remove(pt)
            faces_pt = attrs_mod.faces_db_path(name)
            if os.path.exists(faces_pt):
                os.remove(faces_pt)
        # For Update (not full reset), run Fix Moved Files first so the CLIP
        # scan operates on an up-to-date path list.
        if not reset:
            self._rescan_moved_files(silent=True, auto_apply=auto_apply_moves)

        self._is_scanning = True
        self._stop_scan = False
        self._active_scan_btn = self.btn_generate if reset else self.btn_scan_new
        self._toggle_ui(True)
        self._active_scan_btn.setText(_t("Scanning… / スキャン中…"))
        self.btn_stop.setEnabled(True)
        self.btn_scan_new.setEnabled(False)
        self._scan_queue = queue.Queue()
        # Cycle status: while the scan runs, the recurring label would
        # otherwise still read "Next update in N min" from the previous
        # arm, which misleads the user into thinking we're idle. Flip to
        # "Updating now…" — _scan_done resets it to the next idle gap.
        if (getattr(self, '_recurring_armed', False)
                and hasattr(self, 'lbl_schedule_status')):
            self.lbl_schedule_status.setText(_t(
                f"Updating now… / 更新中…"))

        # Logo ON when scan starts: the scan IS doing AI work, so the logo
        # should reflect that. Without this, a previously-tripped ceiling
        # could leave the logo stuck OFF while the scan ran AI for hours
        # — visually disconnected from what's actually happening. Restore
        # from the stored _prev modes if present, otherwise set sensible
        # defaults (when_empty = run AI only on files missing the data).
        try:
            _changed_logo = False
            if self.app.config.get("face_inspect_mode", "when_empty") == "never":
                _prev = self.app.config.pop("_face_inspect_prev", "when_empty")
                self.app.config["face_inspect_mode"] = _prev
                _changed_logo = True
            if self.app.config.get("clip_inspect_mode", "never") == "never":
                _prev = self.app.config.pop("_clip_inspect_prev", "when_empty")
                self.app.config["clip_inspect_mode"] = _prev
                _changed_logo = True
            if _changed_logo:
                cfg.save_config(self.app.config,
                                getattr(self.app, "current_project", None))
                if hasattr(self.app, "_refresh_logo_pixmap"):
                    self.app._refresh_logo_pixmap()
        except Exception:
            pass

        _auto_rename = attrs_mod.load_filename_config(getattr(self.app, "current_project", None)).get("auto_rename", False)
        self.lbl_scan.setText(_t(
            "Starting scan — CLIP + face + metadata… / "
            "スキャン開始 — CLIP+顔+メタデータ…"))

        def _worker():
            try:
                dim = logic.EMBEDDING_DIM
                if reset:
                    data = {"paths": [], "embeddings": torch.empty((0, dim)).to(logic.device)}
                else:
                    data, _ = logic.load_db_logic(name)
                    if not data or data["embeddings"].shape[1] != dim:
                        data = {"paths": [], "embeddings": torch.empty((0, dim)).to(logic.device)}

                valid_exts = tuple(ext.lower() for ext in (logic.EXT_IMG + logic.EXT_VID))
                v_disk = set()
                for d, no_sub in dirs_flags:
                    if not os.path.exists(d): continue
                    if no_sub:
                        for f in os.listdir(d):
                            if f.lower().endswith(valid_exts):
                                v_disk.add(os.path.abspath(os.path.join(d, f)))
                    else:
                        for r, subdirs, fs in os.walk(d):
                            subdirs[:] = [s for s in subdirs if s != '_unreadable']
                            if os.path.basename(r) == '_unreadable': continue
                            for f in fs:
                                if f.lower().endswith(valid_exts):
                                    v_disk.add(os.path.abspath(os.path.join(r, f)))

                data["base_dirs"]       = list(dirs)
                data["base_dirs_nosub"] = list(no_subs)

                # Backup attrs before scan so data loss is always recoverable
                _attrs_path = os.path.join(attrs_mod.DATA_DIR, f"attrs_{name}.json")
                if os.path.exists(_attrs_path):
                    shutil.copy2(_attrs_path, _attrs_path + ".bak")

                # Load attrs once — worker owns a local copy
                attrs_data   = dict(attrs_mod.load(name))
                scan_renames = {}
                failed       = []
                face_errors  = []
                faces_found  = 0

                # ── Full scan: CLIP + face + metadata + rename ───────────────
                if reset:
                    removed = 0
                    to_add = list(v_disk)
                else:
                    old_paths = data["paths"]
                    old_embs  = data["embeddings"]
                    keep_idx  = [i for i, p in enumerate(old_paths)
                                 if os.path.exists(p) and os.path.abspath(p) in v_disk]
                    if keep_idx:
                        data["paths"]      = [old_paths[i] for i in keep_idx]
                        data["embeddings"] = old_embs[keep_idx]
                    else:
                        data["paths"]      = []
                        data["embeddings"] = torch.empty((0, dim)).to(logic.device)
                    removed = len(old_paths) - len(data["paths"])
                    # Also prune attrs entries that are no longer in v_disk —
                    # otherwise files dropped from features.pt linger in
                    # attrs_<project>.json forever (root cause of "still see
                    # orphans after Update DB").
                    _kept_set = {os.path.abspath(p) for p in data["paths"]}
                    _attrs_orphans = [_p for _p in attrs_data
                                      if os.path.abspath(_p) not in _kept_set]
                    for _op in _attrs_orphans:
                        attrs_data.pop(_op, None)
                    current_set = {os.path.abspath(p) for p in data["paths"]}
                    to_add = [p for p in v_disk if p not in current_set]

                    # ── Reconcile moved files ─────────────────────────────────
                    # Missing = was in DB but no longer exists on disk.
                    # If a "new" file has the same stem as a missing file,
                    # it was moved — update the path in-place (no re-encoding).
                    # missing: {(stem, size): (db_index, old_path)}
                    # size disambiguates files with the same plain name (e.g. photo.jpg)
                    missing_by_key = {}
                    for i, p in enumerate(data["paths"]):
                        if not os.path.exists(p):
                            stem = os.path.splitext(os.path.basename(p))[0]
                            try:
                                sz = os.path.getsize(p) if os.path.exists(p) else -1
                            except OSError:
                                sz = -1
                            missing_by_key[(stem, sz)] = (i, p)
                    if missing_by_key:
                        still_to_add = []
                        moved_count  = 0
                        moved_renames = {}   # old_path → new_path for store flush
                        for new_p in to_add:
                            stem = os.path.splitext(os.path.basename(new_p))[0]
                            try:
                                sz = os.path.getsize(new_p)
                            except OSError:
                                sz = -1
                            key = (stem, sz)
                            # also try stem-only match as fallback (size was unknown at old path)
                            match = missing_by_key.get(key) or missing_by_key.get((stem, -1))
                            if match:
                                idx, old_p = match
                                missing_by_key = {k: v for k, v in missing_by_key.items() if v[0] != idx}
                                data["paths"][idx] = new_p
                                if old_p in attrs_data:
                                    attrs_data[new_p] = attrs_data.pop(old_p)
                                moved_renames[old_p] = new_p
                                moved_count += 1
                                self._scan_queue.put(("moved", (old_p, new_p)))
                            else:
                                still_to_add.append(new_p)
                        if moved_count:
                            removed -= moved_count
                            to_add = still_to_add
                            # Update faces source_path and dups entries for moved files
                            attrs_mod.flush_path_renames_to_stores(moved_renames, name)

                if not to_add and removed == 0:
                    self._scan_queue.put(("uptodate", None)); return

                self._scan_queue.put(("total", len(to_add)))
                added = 0
                # Batch-flush buffers for the new entries. Per-file
                # `torch.cat([data["embeddings"], emb.unsqueeze(0)])`
                # used to reallocate the full embeddings tensor every
                # iteration; for 10K+ files that's a lot of churn. Now
                # we accumulate into Python lists and flush at every
                # checkpoint (~100 files) — one big stack+cat per batch.
                _pending_paths = []
                _pending_embs  = []

                def _flush_pending():
                    nonlocal _pending_paths, _pending_embs
                    if _pending_embs:
                        data["paths"].extend(_pending_paths)
                        data["embeddings"] = torch.cat(
                            [data["embeddings"], torch.stack(_pending_embs)])
                        _pending_paths = []
                        _pending_embs = []

                def _save_checkpoint():
                    _flush_pending()
                    torch.save(data, os.path.join(attrs_mod.DATA_DIR, f"features_{name}.pt"))
                    attrs_mod.save(name, attrs_data)

                # Per-file RSS spike tracker. If a single file pushes the
                # main process RSS up by more than this threshold, dump
                # the file path + before/after to stderr so the user can
                # find the offender. Quiet for normal-size files.
                _SPIKE_THRESHOLD_MB = 500
                try:
                    import psutil as _psutil_log
                    _last_rss = _psutil_log.Process().memory_info().rss / (1024 * 1024)
                except Exception:
                    _psutil_log = None
                    _last_rss = 0

                for i, p in enumerate(to_add):
                    if self._stop_scan:
                        _save_checkpoint()
                        if scan_renames:
                            attrs_mod.flush_path_renames_to_stores(scan_renames, name)
                        self._scan_queue.put(("stopped", (removed, added, failed, attrs_data, faces_found, face_errors))); return

                    fname = p   # show full path in progress / error messages
                    if not os.path.exists(p):
                        failed.append((p, "not found / moved")); continue
                    if os.path.getsize(p) == 0:
                        failed.append((p, "0 bytes")); continue

                    # ── Step 1: CLIP embed ────────────────────────────────────
                    self._scan_queue.put(("progress", (i + 1, len(to_add), fname, "CLIP")))
                    emb = logic.extract_feature(p)
                    if emb is None:
                        failed.append((p, "unreadable/corrupt")); continue

                    # Lock guard: if the user has marked this file as
                    # not-editable (confirmed face/CLIP), skip the auto
                    # re-detection steps. The CLIP embedding above is
                    # still needed for the search index. Manual UI
                    # actions (Update Face button, P field pick,
                    # dismantle, set BASE, etc.) bypass this lock —
                    # only the scanner auto-path is gated.
                    _locked = not attrs_mod.is_editable(attrs_data, p)
                    if _locked:
                        continue

                    # ── Step 2: CLIP auto-detect attributes ───────────────────
                    # Names match `spec["field"]` in CLIP_AUTO_DETECT (long
                    # storage keys). The previous short-letter set
                    # ({"hc","fa","sk",…}) silently mismatched every spec
                    # entry because auto_detect_clip_attrs filters by long
                    # name — so Update never actually wrote CLIP fields,
                    # they only got values from preview's _on_inspect.
                    #
                    # `animal` is special: force re-detected each scan and
                    # CLEARED from stored attrs when detection doesn't
                    # qualify (winner failed margin_over_zero=0.10). Without
                    # the clear, the text box keeps showing a stale a2
                    # Hamster / ff Kraken from a past detection while
                    # CLIP_A debug shows a different live winner — that's
                    # the data-connection disconnect the user reported.
                    # Now stored animal always reflects the most recent
                    # detection's outcome (winner or empty), in sync with
                    # what CLIP currently thinks.
                    # AI OFF respect: when the user has both inspect modes
                    # set to "never", Update SHOULD NOT run CLIP-attr or
                    # face detection. Only Step 1 (CLIP embed for search
                    # index) and Step 4 (metadata) + Step 5 (rename) run.
                    # User: "it is off from beginning and i just drop a
                    # file ... then updated" — meaning Update was still
                    # detecting against their AI-off wish.
                    _cm_upd = self.app.config.get("clip_inspect_mode", "never")
                    _fm_upd = self.app.config.get("face_inspect_mode", "never")
                    _ai_off_upd = (_cm_upd == "never" and _fm_upd == "never")
                    self._scan_queue.put(("progress", (i + 1, len(to_add), fname, "attrs")))
                    _step2_animal_decided = False
                    _step2_animal_value   = None
                    clip_updates          = None
                    # Gate Step 2 on clip_inspect_mode specifically — not
                    # on the combined _ai_off_upd. "Face only" means
                    # clip=never, face=on; Step 2 (CLIP attr-detect)
                    # must skip even though face is on.
                    if _cm_upd != "never":
                        try:
                            _clip_fields = {"hair", "face_angle", "skin", "eyes",
                                            "bust", "waist_hip", "posture_motion",
                                            "camera_shot", "background", "clothing",
                                            "animal", "expression"}
                            _entry_for_detect = dict(attrs_data.get(p, {}))
                            _curr_animal = _entry_for_detect.get("animal", "")
                            _animal_force_none = bool(
                                self.app.config.get("animal_force_none", False))
                            if _animal_force_none and not _curr_animal:
                                attrs_data.setdefault(p, {})["animal"] = "00"
                                _entry_for_detect["animal"] = "00"
                                _curr_animal = "00"
                            # All CLIP fields preserve any non-empty stored
                            # value — CLIP only fills fields that are empty.
                            _clip_fields_this_file = set()
                            for _f in _clip_fields:
                                if not _entry_for_detect.get(_f, ""):
                                    _clip_fields_this_file.add(_f)
                            _animal_user_locked = bool(_curr_animal)
                            # Settings → Clothing → "Skip CLIP for Clothing".
                            if self.app.config.get("clothing_skip_clip", False):
                                _clip_fields_this_file.discard("clothing")
                            clip_updates = attrs_mod.auto_detect_clip_attrs(
                                emb, _entry_for_detect, allowed_fields=_clip_fields_this_file)
                            if clip_updates:
                                attrs_data.setdefault(p, {}).update(clip_updates)
                            # Animal authority — re-apply after Step 4.
                            _step2_animal_decided = True
                            if _animal_user_locked:
                                _step2_animal_value = _curr_animal
                            else:
                                _step2_animal_value = (clip_updates or {}).get("animal")
                                if _step2_animal_value is None:
                                    _existing_entry = attrs_data.get(p)
                                    if isinstance(_existing_entry, dict):
                                        _existing_entry.pop("animal", None)
                        except Exception:
                            _step2_animal_decided = False
                            _step2_animal_value   = None

                    # ── Step 3: Face ──────────────────────────────────────────
                    # Filename-encoded P-codes are the user's explicit
                    # label and MUST win over the face detector's guess.
                    # User reported "Sophie/0Base/face/00-20 was P001
                    # everywhere after FanView mass-rename; Update changed
                    # them all to P031" — face_recognition misidentified
                    # the person and step 5's rename then rewrote the
                    # filename to match. Skip auto-assign when the
                    # filename already encodes a person.
                    _stem_for_pid = os.path.splitext(os.path.basename(p))[0]
                    _parsed_pid = attrs_mod.parse_coded_filename(_stem_for_pid)
                    _filename_persons = (_parsed_pid.get("persons", [])
                                         if _parsed_pid else [])
                    if _filename_persons:
                        # User-labeled file. Sync attrs to the filename so
                        # the rename step is a no-op. Detection is skipped
                        # entirely — running it on user-labeled files
                        # would also pollute faces DB enrichment with a
                        # wrong-person association.
                        attrs_data.setdefault(p, {})["person_id"] = _filename_persons[0]
                    elif _fm_upd == "never":
                        # face_inspect_mode is "never" — skip face
                        # detection. Independent of clip_inspect_mode so
                        # "CLIP only" mode runs CLIP attrs but not face.
                        pass
                    else:
                        self._scan_queue.put(("progress", (i + 1, len(to_add), fname, "face")))
                        pid = None
                        try:
                            pid = attrs_mod.detect_or_assign_person_id(p, name, raise_errors=True)
                        except Exception as fe:
                            face_errors.append(f"{fname}: {fe}")
                            self._scan_queue.put(("face_warn", f"{fname}: {fe}"))
                        if pid:
                            faces_found += 1
                            attrs_data.setdefault(p, {})["person_id"] = pid

                    # ── Step 4: Metadata ──────────────────────────────────────
                    self._scan_queue.put(("progress", (i + 1, len(to_add), fname, "meta")))
                    attrs_data = attrs_mod.auto_set_all(attrs_data, p, name)

                    # CLIP decision wins over filename-extract rules for
                    # animal. auto_set_all just ran user-defined filename
                    # rules (e.g. extract A-code from stem) which would
                    # overwrite Step 2's decision from the OLD filename.
                    # Restore Step 2's intended value (either the new CLIP
                    # winner, or "no animal" if margin failed) so the
                    # auto-rename below builds the filename to match.
                    if _step2_animal_decided:
                        _e = attrs_data.get(p)
                        if isinstance(_e, dict):
                            if _step2_animal_value is None:
                                _e.pop("animal", None)
                            else:
                                _e["animal"] = _step2_animal_value

                    # ── Step 5: Rename ────────────────────────────────────────
                    if _auto_rename:
                        self._scan_queue.put(("progress", (i + 1, len(to_add), fname, "rename")))
                        # Save original stem for boolean pattern matching BEFORE rename strips it
                        orig_stem = os.path.splitext(os.path.basename(p))[0]
                        try:
                            new_path = attrs_mod.rename_with_person_id(
                                attrs_data, p, pid or "000", flush_stores=False,
                                skip_uncoded=False)
                            if new_path != p:
                                scan_renames[p] = new_path
                                p = new_path
                        except Exception:
                            pass
                        # Apply boolean sync rules (e.g. -watermark → WM in coded filename)
                        # Pass orig_stem so pattern matching uses the pre-rename filename
                        try:
                            new_path = attrs_mod.apply_boolean_sync_rules(
                                attrs_data, p, name, orig_stem=orig_stem)
                            if new_path != p:
                                scan_renames[p] = new_path
                                p = new_path
                        except Exception:
                            pass

                    # ── Commit to DB (batched — flushed at checkpoint) ────────
                    _pending_paths.append(p)
                    _pending_embs.append(emb)
                    added += 1

                    # RSS spike tracker — log the file path if this one's
                    # work pushed RSS up by > _SPIKE_THRESHOLD_MB. Helps
                    # identify pathological files (huge videos, weird
                    # codecs, faces that confuse dlib, etc.).
                    if _psutil_log is not None:
                        try:
                            _now_rss = _psutil_log.Process().memory_info().rss / (1024 * 1024)
                            _delta = _now_rss - _last_rss
                            if _delta > _SPIKE_THRESHOLD_MB:
                                import sys as _sys
                                print(f"[RSS SPIKE] +{_delta:.0f} MB "
                                      f"({_last_rss:.0f} -> {_now_rss:.0f}) "
                                      f"on {p}",
                                      file=_sys.stderr, flush=True)
                            _last_rss = _now_rss
                        except Exception:
                            pass

                    # Bound memory growth from CLIP/MediaPipe/cv2/dlib
                    # accumulators every 50 files. gc.collect() trims Python
                    # cycles; empty_cache() returns VRAM to the driver so
                    # the next forward pass doesn't grow the cache further.
                    # Gate on `i` so locked/failed files (which still run
                    # CLIP) also get cleaned up.
                    if (i + 1) % 50 == 0:
                        import gc as _gc
                        _gc.collect()
                        try:
                            torch.cuda.empty_cache()
                        except Exception:
                            pass

                    # Pause-on-ceiling: same RSS cap the AI inspect logo
                    # uses (clip_inspect_rss_limit_mb, default 1500). When
                    # RSS exceeds it, flush + save, signal the main thread
                    # to flip the AI logo OFF, then sleep-poll until RSS
                    # drops to 80% of the ceiling. Stop button breaks out.
                    # Gated on `i` (loop index) not `added` — locked/failed
                    # files don't increment `added` but still run CLIP and
                    # grow RSS, so they must not starve the ceiling check.
                    if (i + 1) % 10 == 0:
                        try:
                            import psutil as _psutil
                            _env = os.environ.get("AISEARCH_INSPECT_RSS_LIMIT_MB")
                            _ceil = float(_env) if _env else float(
                                self.app.config.get("clip_inspect_rss_limit_mb", 1500))
                            _rss = _psutil.Process().memory_info().rss / (1024 * 1024)
                            if _rss > _ceil:
                                _flush_pending()
                                _save_checkpoint()
                                self._scan_queue.put(("pause_for_memory",
                                                      (_rss, _ceil)))
                                _resume = _ceil * 0.8
                                import gc as _gc2, time as _time
                                while not self._stop_scan:
                                    _gc2.collect()
                                    try:
                                        torch.cuda.empty_cache()
                                    except Exception:
                                        pass
                                    _rss = _psutil.Process().memory_info().rss / (1024 * 1024)
                                    if _rss <= _resume:
                                        break
                                    _time.sleep(3)
                                self._scan_queue.put(("resume_from_memory",
                                                      (_rss, _ceil)))
                        except Exception:
                            pass

                    if added % 100 == 0:
                        _save_checkpoint()
                        self._scan_queue.put(("checkpoint", added))

                _save_checkpoint()
                if scan_renames:
                    attrs_mod.flush_path_renames_to_stores(scan_renames, name)
                self._scan_queue.put(("done", (removed, added, failed, attrs_data, faces_found, face_errors)))
            except Exception as e:
                import traceback
                self._scan_queue.put(("error", f"{e}\n{traceback.format_exc()}"))

        threading.Thread(target=_worker, daemon=True).start()
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(lambda: self._poll_scan_queue(name))
        self._poll_timer.start(100)

    @staticmethod
    def _fmt_eta(seconds):
        """Format seconds into a compact human-readable string."""
        s = int(seconds)
        if s < 60:
            return f"{s}s"
        m, s = divmod(s, 60)
        if m < 60:
            return f"{m}m {s:02d}s"
        h, m = divmod(m, 60)
        return f"{h}h {m:02d}m"

    def _poll_scan_queue(self, name):
        _step_labels = {"rename": "Rename", "CLIP": "CLIP", "meta": "Meta", "face": "Face", "attrs": "Attrs"}
        sb = self.app.statusBar()
        try:
            while True:
                msg, payload = self._scan_queue.get_nowait()
                if msg == "total":
                    self.progress_bar.setMaximum(payload)
                    self._scan_start_time = time.monotonic()
                    self._last_eta_str = ""
                elif msg == "moved":
                    old_p, new_p = payload
                    self.lbl_scan.setText(f"Moved: {os.path.basename(old_p)} → {os.path.basename(new_p)}")
                elif msg == "progress":
                    i, total, fname, step = payload
                    step_lbl = _step_labels.get(step, step)
                    # Recompute ETA whenever a file finishes its CLIP step (most reliable sample)
                    # and persist it so face/meta steps still show the estimate.
                    t0 = getattr(self, '_scan_start_time', None)
                    if step == "CLIP" and i > 1 and t0:
                        elapsed = time.monotonic() - t0
                        files_done = i - 1   # fully completed files (all steps)
                        per_file = elapsed / files_done
                        remaining = total - files_done
                        self._last_eta_str = f"  ~{self._fmt_eta(per_file * remaining)} left"
                    eta_str = getattr(self, '_last_eta_str', "")
                    self.progress_label.setText(f"[{step_lbl}] ({i}/{total}): {fname}")
                    self.progress_bar.setValue(i)
                    # Show remaining time inside the progress bar
                    _eta_short = eta_str.strip().lstrip("~").replace(" left", "") if eta_str else ""
                    self.progress_bar.setFormat(f"%p%  {_eta_short}" if _eta_short else "%p%")
                    sb.showMessage(f"[{step_lbl}] {name}: {i}/{total}{eta_str} — {fname}")
                elif msg == "checkpoint":
                    self.progress_label.setText(self.progress_label.text() + "  [saved]")
                    sb.showMessage(sb.currentMessage() + "  [saved]")
                elif msg == "uptodate":
                    self._active_scan_btn.setText(_t("Up to date / 最新"))
                    sb.clearMessage()
                    self.show(); self.raise_()
                    QMessageBox.information(self, "Done", "Already up to date.")
                    self._scan_done(); return
                elif msg == "face_warn":
                    # Face detection error for one file — show briefly, keep scanning
                    self.lbl_scan.setText(f"Face err: {payload[:80]}")
                elif msg == "pause_for_memory":
                    # RSS crossed the AI-inspect ceiling. Mirror the preview's
                    # behavior: flip both inspect modes to "never" so the AI
                    # logo flips OFF — same one consistent signal whether the
                    # ceiling was tripped by preview or scan. Stored prev so
                    # the user can click the logo to restore later.
                    _rss_mb, _ceil_mb = payload
                    try:
                        _changed = False
                        if self.app.config.get("face_inspect_mode", "when_empty") != "never":
                            self.app.config["_face_inspect_prev"] = self.app.config.get(
                                "face_inspect_mode", "when_empty")
                            self.app.config["face_inspect_mode"] = "never"
                            _changed = True
                        if self.app.config.get("clip_inspect_mode", "never") != "never":
                            self.app.config["_clip_inspect_prev"] = self.app.config.get(
                                "clip_inspect_mode")
                            self.app.config["clip_inspect_mode"] = "never"
                            _changed = True
                        if _changed:
                            cfg.save_config(self.app.config,
                                            getattr(self.app, "current_project", None))
                            if hasattr(self.app, "_refresh_logo_pixmap"):
                                self.app._refresh_logo_pixmap()
                    except Exception:
                        pass
                    self.lbl_scan.setText(
                        f"⚠ Paused — RSS {int(_rss_mb)} MB > "
                        f"ceiling {int(_ceil_mb)} MB. Waiting for memory…")
                    sb.showMessage(self.lbl_scan.text())
                elif msg == "resume_from_memory":
                    _rss_mb, _ceil_mb = payload
                    self.lbl_scan.setText(
                        f"✓ Resumed — RSS dropped to {int(_rss_mb)} MB.")
                    sb.showMessage(self.lbl_scan.text())
                    # Logo back ON — scan is doing AI work again, so the
                    # OFF state would lie. Restores the modes that were
                    # active before the pause flipped them OFF.
                    try:
                        _changed_logo = False
                        if self.app.config.get("face_inspect_mode", "when_empty") == "never":
                            _prev = self.app.config.pop("_face_inspect_prev", "when_empty")
                            self.app.config["face_inspect_mode"] = _prev
                            _changed_logo = True
                        if self.app.config.get("clip_inspect_mode", "never") == "never":
                            _prev = self.app.config.pop("_clip_inspect_prev", "when_empty")
                            self.app.config["clip_inspect_mode"] = _prev
                            _changed_logo = True
                        if _changed_logo:
                            cfg.save_config(self.app.config,
                                            getattr(self.app, "current_project", None))
                            if hasattr(self.app, "_refresh_logo_pixmap"):
                                self.app._refresh_logo_pixmap()
                    except Exception:
                        pass
                elif msg == "stopped":
                    removed, added, failed, attrs_data, faces_found, face_errors = payload
                    self.app.attrs_data = attrs_mod.load(name)
                    self._refresh_list()
                    self.proj_combo.setCurrentText(name)
                    self.app.set_project(name)
                    # Watcher-triggered stop is silent — it'll auto-resume,
                    # so no Settings popup, no Stopped dialog, no failed-files
                    # / face-errors lists. Manual Stop click still surfaces
                    # everything as before.
                    _silent = getattr(self.app, '_scan_paused_by_watcher', False)
                    if _silent:
                        self._active_scan_btn.setText(_t("Paused… / 一時停止中…"))
                    else:
                        self._active_scan_btn.setText(_t("Stopped / 停止済"))
                    sb.clearMessage()
                    if not _silent:
                        self.show(); self.raise_()
                        face_info = f"\nFaces detected: {faces_found}" if faces_found or face_errors else ""
                        err_info  = f"\nFace errors: {len(face_errors)}" if face_errors else ""
                        QMessageBox.information(self, "Stopped",
                            f"Scan stopped and saved.\nAdded so far: {added}{face_info}{err_info}")
                        if failed: self._show_failed_files(failed)
                        if face_errors: self._show_face_errors(face_errors)
                    self._scan_done(); return
                elif msg == "done":
                    removed, added, failed, attrs_data, faces_found, face_errors = payload
                    self.app.attrs_data = attrs_mod.load(name)
                    self._refresh_list()
                    self.proj_combo.setCurrentText(name)
                    self.app.set_project(name)
                    face_info = f"  |  Faces: {faces_found}" if faces_found or face_errors else ""
                    err_info  = f"  |  Face errors: {len(face_errors)}" if face_errors else ""
                    # Surface CLIP/face inspect mode right after Update so
                    # the user can see whether AI is currently on or off
                    # without hunting through Settings. Both modes "never"
                    # = AI is fully off and Update didn't touch CLIP-detect
                    # values; anything else = AI was active.
                    _cm = self.app.config.get("clip_inspect_mode", "never")
                    _fm = self.app.config.get("face_inspect_mode", "never")
                    if _cm == "never" and _fm == "never":
                        _ai_state = "AI INSPECT: OFF"
                    elif _cm == "never":
                        _ai_state = "AI INSPECT: face only"
                    elif _fm == "never":
                        _ai_state = "AI INSPECT: clip only"
                    else:
                        _ai_state = "AI INSPECT: ON"
                    self._active_scan_btn.setText(_t("Done / 完了"))
                    self.lbl_scan.setText(
                        f"Done — added {added}, removed {removed}{face_info}{err_info}  |  {_ai_state}")
                    QTimer.singleShot(8000, lambda: self.lbl_scan.setText(""))
                    sb.clearMessage()
                    sb.showMessage(_ai_state, 6000)
                    if failed: self._show_failed_files(failed)
                    if face_errors: self._show_face_errors(face_errors)
                    self._scan_done(); return
                elif msg == "error":
                    self._active_scan_btn.setText(_t("Error / エラー"))
                    sb.clearMessage()
                    self.show(); self.raise_()
                    QMessageBox.critical(self, "Scan Error", payload)
                    self._scan_done(); return
        except queue.Empty:
            pass

    def _request_stop(self):
        self._unified_stop()

    def _unified_stop(self):
        """Stop whichever operation is currently running."""
        self._stop_scan = True
        self._stop_scan_all = True
        self._stop_rename_only = True
        self.btn_stop.setEnabled(False)
        self.btn_stop.setText(_t("Stopping… / 停止中…"))
        # Fallback: if the worker thread doesn't respond within 10 s, reset UI anyway
        self._stop_fallback_timer = QTimer(self)
        self._stop_fallback_timer.setSingleShot(True)
        self._stop_fallback_timer.timeout.connect(self._stop_fallback)
        self._stop_fallback_timer.start(10000)

    def _stop_fallback(self):
        """Called if the worker never sends a terminal message after Stop was pressed."""
        if self.btn_stop.text() == "Stopping…":
            self.lbl_scan.setText("Stopped (thread did not respond — UI reset).")
            self._scan_done()

    def _toggle_schedule_update(self):
        """Arm a one-shot timer to fire Update at the chosen time. Click
        again while armed to cancel. App must stay open until firing —
        the timer is in-process, no daemon."""
        existing = getattr(self, '_schedule_timer', None)
        if existing is not None and existing.isActive():
            existing.stop()
            self._schedule_timer = None
            self.btn_schedule.setText(_t("⏰ Schedule / ⏰ 予約"))
            self.lbl_schedule_status.setText("")
            return
        # Cancel any recurring schedule — one schedule active at a time.
        recurring = getattr(self, '_recurring_timer', None)
        if recurring is not None and recurring.isActive():
            recurring.stop()
            self._recurring_timer = None
            self.btn_recurring.setText(_t("🔁 Cycle / 🔁 周期"))
        # Time-only picker — combine the chosen time with today's date,
        # roll over to tomorrow if the time already passed.
        now = QDateTime.currentDateTime()
        picked_time = self.dt_schedule.time()
        target = QDateTime(now.date(), picked_time)
        if target <= now:
            target = target.addDays(1)
        delay_ms = now.msecsTo(target)
        if delay_ms <= 0:
            self.lbl_schedule_status.setText(_t(
                "Pick a future time. / 未来の時刻を指定してください。"))
            return
        # QTimer caps at INT32_MAX ms (~24.8 days). Beyond that, fall back
        # to a periodic check.
        _MAX = 2_000_000_000
        t = QTimer(self)
        t.setSingleShot(True)
        if delay_ms <= _MAX:
            t.timeout.connect(self._fire_scheduled_update)
            t.start(delay_ms)
        else:
            t.timeout.connect(self._toggle_schedule_update)  # re-arm
            t.start(_MAX)
        self._schedule_timer = t
        self.btn_schedule.setText(_t("✕ Cancel / ✕ キャンセル"))
        # Show date too in the status — it's not in the picker, but the
        # user benefits from confirmation when the time rolled to tomorrow.
        self.lbl_schedule_status.setText(_t(
            f"Scheduled for {target.toString('HH:mm (yyyy-MM-dd)')} / "
            f"予約時刻：{target.toString('HH:mm (yyyy-MM-dd)')}"))

    def _fire_scheduled_update(self):
        self._schedule_timer = None
        self.btn_schedule.setText(_t("⏰ Schedule / ⏰ 予約"))
        self.lbl_schedule_status.setText("")
        # Unattended — auto-apply any moved-file remaps so the scan
        # doesn't block on the confirmation dialog.
        self.execute_generate(reset=False, auto_apply_moves=True)

    def _toggle_recurring_update(self):
        """Arm/disarm a recurring Update. The N-minute gap is measured
        AFTER the previous scan finishes, not wall-clock — so a long
        scan doesn't compress the next idle window. Mutually exclusive
        with the one-shot _toggle_schedule_update; arming one cancels
        the other. Interval persists in config (update_every_min)."""
        if getattr(self, '_recurring_armed', False):
            # Currently armed → user clicked Stop
            existing = getattr(self, '_recurring_timer', None)
            if existing is not None and existing.isActive():
                existing.stop()
            self._recurring_timer = None
            self._recurring_armed = False
            try:
                self.app.config["update_cycle_armed"] = False
                cfg.save_config(self.app.config,
                                getattr(self.app, "current_project", None))
            except Exception:
                pass
            self.btn_recurring.setText(_t("🔁 Cycle / 🔁 周期"))
            self.lbl_schedule_status.setText("")
            return
        # Cancel any one-shot schedule — only one active at a time.
        one_shot = getattr(self, '_schedule_timer', None)
        if one_shot is not None and one_shot.isActive():
            one_shot.stop()
            self._schedule_timer = None
            self.btn_schedule.setText(_t("⏰ Schedule / ⏰ 予約"))
        mins = int(self.sp_recurring.value())
        try:
            self.app.config["update_every_min"] = mins
            self.app.config["update_cycle_armed"] = True
            cfg.save_config(self.app.config,
                            getattr(self.app, "current_project", None))
        except Exception:
            pass
        self._recurring_armed = True
        self._arm_recurring_timer(mins)
        self.btn_recurring.setText(_t("⏹ Stop Cycle / ⏹ 周期停止"))
        _target = QDateTime.currentDateTime().addSecs(mins * 60)
        _ts = _target.toString("HH:mm")
        self.lbl_schedule_status.setText(_t(
            f"Next update in {mins} min (at {_ts}) / 次回まで {mins}分（{_ts}）"))

    def _arm_recurring_timer(self, mins):
        """Start (or restart) the single-shot timer that will fire the
        next recurring Update. Replaces any previously-armed timer so
        _scan_done can call this on every finish without stacking."""
        existing = getattr(self, '_recurring_timer', None)
        if existing is not None and existing.isActive():
            existing.stop()
        t = QTimer(self)
        t.setSingleShot(True)
        t.timeout.connect(self._fire_recurring_update)
        t.start(int(mins) * 60 * 1000)
        self._recurring_timer = t

    def _fire_recurring_update(self):
        """Recurring tick. If a scan is somehow already running (e.g. a
        manual scan started during the idle window), wait — _scan_done
        will re-arm us when that scan finishes. Otherwise kick off
        execute_generate; _scan_done re-arms after it completes."""
        if not getattr(self, '_recurring_armed', False):
            return
        mins = int(self.sp_recurring.value()) if hasattr(self, "sp_recurring") else 0
        if (getattr(self, "_is_scanning", False)
                or getattr(self, "_is_metadata_scanning", False)):
            self.lbl_schedule_status.setText(_t(
                f"Waiting — scan in progress / 待機中 — 実行中"))
            return
        # Reflect the actual state — "Next update in 30 min" while the
        # update itself was running was misleading. _scan_done flips it
        # back to "Next update in N min" once the scan finishes.
        self.lbl_schedule_status.setText(_t(
            f"Updating now… / 更新中…"))
        # Unattended — same auto_apply_moves as the one-shot fire.
        # _scan_done will arm the next tick once this scan finishes.
        self.execute_generate(reset=False, auto_apply_moves=True)

    def _scan_done(self):
        _fb = getattr(self, '_stop_fallback_timer', None)
        if _fb and _fb.isActive():
            _fb.stop()
        if self._poll_timer:
            self._poll_timer.stop()
            self._poll_timer = None
        self._stop_scan = False
        # Restore each button to its original label
        self.btn_generate.setText(_t("Scan ALL / 全スキャン"))
        self.btn_scan_new.setText(_t("Update / 更新"))
        self.btn_stop.setEnabled(False)
        self.btn_stop.setText(_t("Stop / 停止"))
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")
        self._last_eta_str = ""
        self.progress_label.setText(_t("Status: Ready / 状態：準備完了"))
        self._is_metadata_scanning = False
        self._is_scanning = False
        self._toggle_ui(False)
        self.btn_scan_new.setEnabled(True)
        self._update_generate_btn()
        # Pick up any files that arrived in watch dirs during the scan
        QTimer.singleShot(500, lambda: getattr(self.app, '_scan_new_files', lambda: None)())
        # Re-arm the recurring Update timer for the next idle gap — measured
        # from now (scan finish), not from the previous tick. This is what
        # gives "every 30 min" its "30 min of idle between scans" meaning
        # instead of "ticks at fixed wall-clock intervals".
        if getattr(self, '_recurring_armed', False) and hasattr(self, "sp_recurring"):
            mins = int(self.sp_recurring.value())
            self._arm_recurring_timer(mins)
            _target = QDateTime.currentDateTime().addSecs(mins * 60)
            _ts = _target.toString("HH:mm")
            self.lbl_schedule_status.setText(_t(
                f"Next update in {mins} min (at {_ts}) / 次回まで {mins}分（{_ts}）"))

    def _update_generate_btn(self):
        has_dirs = self.dir_listbox.rowCount() > 0
        self.btn_generate.setEnabled(has_dirs)
        self.btn_scan_new.setEnabled(has_dirs)
        if has_dirs:
            self.btn_generate.setStyleSheet(
                "background-color: #7a3a00; color: white; font-weight: bold; padding: 6px;")
            self.btn_scan_new.setStyleSheet(
                "background-color: #1a5a1a; color: white; font-weight: bold; padding: 6px;")
        else:
            self.btn_generate.setStyleSheet(
                "background-color: #555; color: #999; font-weight: bold; padding: 6px;")
            self.btn_scan_new.setStyleSheet(
                "background-color: #555; color: #999; font-weight: bold; padding: 6px;")

    def _toggle_ui(self, locked):
        for w in [self.btn_load, self.btn_delete,
                  self.btn_add_dir, self.btn_remove_dir, self.btn_generate]:
            w.setEnabled(not locked)
        self.proj_combo.setEnabled(not locked)
        # Pause/resume the watch-folder scanner so it doesn't fire mid-rename/scan
        self.app._watcher_paused = locked
        # On unpause, kick a watch scan immediately. Otherwise files dropped
        # in watch_dirs during the Update sit unindexed until the next 30 s
        # fallback tick — and if the watcher already requested a stop, those
        # files are exactly what the user wants picked up first.
        if not locked:
            QTimer.singleShot(0, self.app._scan_new_files)
            # If the watcher caused this stop, auto-resume the Update so
            # the user perceives a pause, not a stop. _scan_new_files runs
            # synchronously, so by the time this fires the new file is
            # indexed and the worker can pick up where it left off.
            if getattr(self.app, '_scan_paused_by_watcher', False):
                self.app._scan_paused_by_watcher = False
                QTimer.singleShot(50, lambda: self.execute_generate(reset=False))

    def _show_failed_files(self, failed):
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QPushButton, QHBoxLayout, QLabel
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Skipped Files ({len(failed)})")
        dlg.resize(700, 400)
        vl = QVBoxLayout(dlg)
        vl.addWidget(QLabel(f"{len(failed)} file(s) could not be indexed:"))
        txt = QTextEdit()
        txt.setReadOnly(True)
        txt.setFontFamily("monospace")
        txt.setPlainText("\n".join(f"{reason}\t{path}" for path, reason in failed))
        vl.addWidget(txt)
        hl = QHBoxLayout()
        btn_copy = QPushButton("Copy All")
        btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(txt.toPlainText()))
        btn_move = QPushButton("Move All to _unreadable")
        btn_move.setStyleSheet("background-color: #5a3a00; color: white; font-weight: bold;")
        def _move_all():
            moved, errors = 0, []
            for path, reason in failed:
                try:
                    if not os.path.exists(path):
                        continue
                    dest_dir = os.path.join(os.path.dirname(path), "unreadable")
                    os.makedirs(dest_dir, exist_ok=True)
                    dest = os.path.join(dest_dir, os.path.basename(path))
                    if os.path.exists(dest):
                        base, ext = os.path.splitext(os.path.basename(path))
                        dest = os.path.join(dest_dir, f"{base}_dup{ext}")
                    os.rename(path, dest)
                    moved += 1
                except Exception as e:
                    errors.append(f"{path}: {e}")
            btn_move.setEnabled(False)
            btn_move.setText(f"Moved {moved} files")
            txt.setPlainText(f"Moved {moved} file(s) to _unreadable folders." +
                             ("\n\nErrors:\n" + "\n".join(errors) if errors else ""))
        btn_move.clicked.connect(_move_all)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(dlg.accept)
        hl.addWidget(btn_copy); hl.addWidget(btn_move); hl.addStretch(); hl.addWidget(btn_close)
        vl.addLayout(hl)
        dlg.exec()

    def _show_face_errors(self, face_errors):
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QPushButton, QHBoxLayout, QLabel
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Face Detection Errors ({len(face_errors)})")
        dlg.resize(700, 350)
        vl = QVBoxLayout(dlg)
        vl.addWidget(QLabel(f"{len(face_errors)} file(s) had face detection errors:"))
        txt = QTextEdit()
        txt.setReadOnly(True)
        txt.setFontFamily("monospace")
        txt.setPlainText("\n".join(face_errors))
        vl.addWidget(txt)
        hl = QHBoxLayout()
        btn_copy = QPushButton("Copy")
        btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(txt.toPlainText()))
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(dlg.accept)
        hl.addWidget(btn_copy); hl.addStretch(); hl.addWidget(btn_close)
        vl.addLayout(hl)
        dlg.exec()

    def _stop_auto_detect(self):
        self._stop_scan_all = True
        self.btn_stop_scan.setEnabled(False)

    def _auto_detect_new(self):
        """Update: scan new files that haven't been processed yet."""
        all_paths = self.app.data.get("paths", []) if self.app.data else []
        if not all_paths:
            self.lbl_scan.setText("No database loaded."); return
        attrs_data = self.app.attrs_data or {}
        paths = [p for p in all_paths if p not in attrs_data and os.path.exists(p)]
        if not paths:
            self.lbl_scan.setText("No unscanned files found."); return
        self._run_scan(paths)

    def _auto_detect_all(self):
        """Scan every file in the DB."""
        paths = self.app.data.get("paths", []) if self.app.data else []
        if not paths:
            self.lbl_scan.setText("No database loaded."); return
        import aisearch_attrs as attrs_mod
        fn_rules = attrs_mod.load_filename_rules(getattr(self.app, "current_project", None))
        two_way  = [r for r in fn_rules if r.get("field") and not r.get("one_way")]
        rename_warning = (
            f"\n\n⚠️  {len(two_way)} two-way filename rule(s) active — files will be RENAMED."
            if two_way else "")
        reply = QMessageBox.question(
            self, "Scan All Files",
            f"Scan {len(paths):,} files?{rename_warning}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes: return
        self._run_scan(paths)

    def _run_scan(self, paths):
        """Shared scan loop used by both Scan All and Scan New."""
        import aisearch_attrs as attrs_mod
        self._stop_scan_all = False
        self.btn_stop_scan.setEnabled(True)
        self.lbl_scan.setText(f"Scanning {len(paths)} files…")
        self.progress_bar.setRange(0, len(paths))
        self.progress_bar.setValue(0)
        face_mode       = 1  # always include face
        # Always rename during scan — Update DB is an explicit batch op, the
        # user already clicked it. The auto_rename UI was removed so this
        # no longer needs to be conditional on a checkbox.
        auto_rename     = True
        QApplication.processEvents()
        updated = 0
        scan_renames = {}   # old_path -> new_path, flushed once after the loop
        _scan_start = time.monotonic()
        for i, path in enumerate(paths):
            if self._stop_scan_all:
                self.lbl_scan.setText(f"Stopped — {updated} of {i} files updated."); break
            if not os.path.exists(path):
                continue
            before = attrs_mod.get(self.app.attrs_data, path).copy()
            if face_mode != 2:
                self.app.attrs_data = attrs_mod.auto_set_all(
                    self.app.attrs_data, path, self.app.current_project)
            if face_mode >= 1:
                pid = attrs_mod.detect_or_assign_person_id(path, self.app.current_project)
                if pid:
                    self.app.attrs_data.setdefault(path, {})["person_id"] = pid
                    if auto_rename:
                        new_path = attrs_mod.rename_with_person_id(
                            self.app.attrs_data, path, pid,
                            flush_stores=False, skip_uncoded=False)
                        if new_path != path:
                            scan_renames[path] = new_path
                            if (self.app.data and "paths" in self.app.data
                                    and path in self.app.data["paths"]):
                                idx2 = self.app.data["paths"].index(path)
                                self.app.data["paths"][idx2] = new_path
                            path = new_path
                elif auto_rename:
                    # No face → fall back to date-first coded name (J{j}…) so
                    # the file still gets a structured filename instead of
                    # staying as an arbitrary download stem.
                    new_path = attrs_mod.rename_to_date_first(
                        self.app.attrs_data, path, self.app.current_project)
                    if new_path != path:
                        scan_renames[path] = new_path
                        if (self.app.data and "paths" in self.app.data
                                and path in self.app.data["paths"]):
                            idx2 = self.app.data["paths"].index(path)
                            self.app.data["paths"][idx2] = new_path
                        path = new_path
            after = attrs_mod.get(self.app.attrs_data, path)
            if after != before:
                updated += 1
            if (i + 1) % 10 == 0 or i == 0:
                self.progress_bar.setValue(i + 1)
                eta_str = ""
                if i > 0:
                    elapsed = time.monotonic() - _scan_start
                    eta_str = f"  ~{self._fmt_eta(elapsed / (i + 1) * (len(paths) - i - 1))} left"
                self.progress_label.setText(f"Metadata ({i+1}/{len(paths)}){eta_str}: {os.path.basename(path)}")
                self.lbl_scan.setText(f"Update: {i+1}/{len(paths)}, updated {updated}{eta_str}")
                QApplication.processEvents()
        else:
            self.progress_bar.setValue(len(paths))
            self.lbl_scan.setText(f"Done — {updated} of {len(paths)} files updated.")
            QTimer.singleShot(4000, lambda: self.lbl_scan.setText(""))
        attrs_mod.save(self.app.current_project, self.app.attrs_data)
        if scan_renames:
            attrs_mod.flush_path_renames_to_stores(scan_renames, self.app.current_project)
            if self.app.data:
                torch.save(self.app.data, os.path.join(attrs_mod.DATA_DIR, f"features_{self.app.current_project}.pt"))
        self.btn_stop_scan.setEnabled(False)

    def _update_scan_project_label(self):
        proj = getattr(self.app, "current_project", None) or "(none)"
        self.lbl_scan_project.setText(
            f"Active project: <b style='color:#66ccff'>{proj}</b>"
            f" — scan / rename targets this project. Press Load to switch.")
        self.lbl_scan_project.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_scan_project.setStyleSheet("color: #aaa; font-size: 11px;")

    def _unlock_all_metadata(self):
        """Unconditionally set editable=True on every file in the scan dirs — no metadata scan."""
        import aisearch_attrs as attrs_mod
        dirs_flags = self._get_dirs_with_flags()
        if not dirs_flags:
            self.lbl_scan.setText("No directories configured."); return
        valid_exts = tuple(ext.lower() for ext in (logic.EXT_IMG + logic.EXT_VID))
        all_paths = []
        for d, no_sub in dirs_flags:
            if not os.path.exists(d): continue
            if no_sub:
                for f in os.listdir(d):
                    if f.lower().endswith(valid_exts):
                        all_paths.append(os.path.abspath(os.path.join(d, f)))
            else:
                for r, subdirs, fs in os.walk(d):
                    subdirs[:] = [s for s in subdirs if s != '_unreadable']
                    if os.path.basename(r) == '_unreadable': continue
                    for f in fs:
                        if f.lower().endswith(valid_exts):
                            all_paths.append(os.path.abspath(os.path.join(r, f)))
        if not all_paths:
            self.lbl_scan.setText("No files found in configured directories."); return
        # Use in-memory attrs (already loaded); fall back to disk only if empty
        live_attrs = self.app.attrs_data if self.app.attrs_data else attrs_mod.load(self.app.current_project)
        self._stop_scan_all = False
        self.btn_stop_scan.setEnabled(True)
        self.progress_bar.setRange(0, len(all_paths))
        self.lbl_scan.setText(f"Unlocking {len(all_paths)} files…")
        unlocked = 0
        for i, path in enumerate(all_paths):
            if self._stop_scan_all:
                self.lbl_scan.setText(f"Stopped — {unlocked} files unlocked."); break
            live_attrs.setdefault(path, {})["editable"] = True
            unlocked += 1
            if (i + 1) % 10 == 0 or i == 0:
                self.progress_bar.setValue(i + 1)
                self.progress_label.setText(f"Unlock ({i+1}/{len(all_paths)}): {os.path.basename(path)}")
                self.lbl_scan.setText(f"Unlocking: {i+1}/{len(all_paths)}, done {unlocked}")
                QApplication.processEvents()
        else:
            self.progress_bar.setValue(len(all_paths))
            self.lbl_scan.setText(f"Done — {unlocked} files unlocked.")
            QTimer.singleShot(4000, lambda: self.lbl_scan.setText(""))
        attrs_mod.save(self.app.current_project, live_attrs)
        self.app.attrs_data = live_attrs
        self.btn_stop.setText(_t("Stop / 停止"))
        self.btn_stop_scan.setEnabled(False)

    def _embed_aitan_all(self):
        """Embed AItan{} metadata block into every file in the current project."""
        import aisearch_attrs as attrs_mod
        project = self.app.current_project
        if not project or not self.app.attrs_data:
            self.lbl_scan.setText("No project/data loaded."); return

        _SUPPORTED = {".mp4", ".mkv", ".mov", ".m4v", ".avi", ".webm", ".wmv",
                      ".jpg", ".jpeg", ".png", ".webp"}
        all_entries = [(p, e) for p, e in self.app.attrs_data.items()
                       if os.path.exists(p)
                       and os.path.splitext(p)[1].lower() in _SUPPORTED]
        if not all_entries:
            self.lbl_scan.setText("No eligible files found."); return

        self._is_scanning = True
        self._stop_rename_only = False
        self._active_scan_btn = self.btn_scan_new
        self._toggle_ui(True)
        self.btn_stop.setEnabled(True)
        self.btn_stop.setText(_t("Stop / 停止"))
        self._active_scan_btn.setText("Embedding…")
        self.btn_scan_new.setEnabled(False)
        self.lbl_scan.setText(f"Embedding AItan{{}} in {len(all_entries)} files…")
        self.progress_bar.setRange(0, len(all_entries))
        self.progress_bar.setValue(0)

        _q = queue.Queue()

        def _worker():
            ok_n = 0; fail_n = 0
            for i, (path, entry) in enumerate(all_entries):
                if self._stop_rename_only:
                    _q.put(("stopped", (ok_n, fail_n, i))); return
                if attrs_mod.embed_aitan_meta(path, entry):
                    ok_n += 1
                else:
                    fail_n += 1
                if (i + 1) % 20 == 0:
                    _q.put(("progress", (i + 1, len(all_entries), ok_n, fail_n)))
            _q.put(("done", (ok_n, fail_n, len(all_entries))))

        def _poll_embed():
            try:
                while True:
                    msg, payload = _q.get_nowait()
                    if msg == "progress":
                        done_i, total, ok_n, fail_n = payload
                        self.progress_bar.setValue(done_i)
                        self.progress_label.setText(
                            f"Embed AItan ({done_i}/{total}) — OK:{ok_n} FAIL:{fail_n}")
                    elif msg in ("done", "stopped"):
                        ok_n, fail_n, total = payload
                        word = "Done" if msg == "done" else "Stopped"
                        self.lbl_scan.setText(f"{word} — {ok_n} embedded, {fail_n} failed.")
                        QTimer.singleShot(5000, lambda: self.lbl_scan.setText(""))
                        if self._poll_timer:
                            self._poll_timer.stop(); self._poll_timer = None
                        self._scan_done()
                        return
            except queue.Empty:
                pass

        threading.Thread(target=_worker, daemon=True).start()
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(_poll_embed)
        self._poll_timer.start(200)

    def _rescan_moved_files(self, silent=False, auto_apply=False):
        """Scan all configured + watch dirs for files matching missing DB entries by filename.
        silent=True suppresses error pop-ups (used when called from Update)."""
        import aisearch_attrs as _am
        project = getattr(self.app, "current_project", None)
        if not project or not self.app.data:
            if not silent:
                QMessageBox.warning(self, "No Project", "No project loaded.")
            return

        # Collect all paths from the DB + attrs_data
        db_paths   = list(self.app.data.get("paths", []))
        attr_paths = list((self.app.attrs_data or {}).keys())
        all_known  = list(dict.fromkeys(db_paths + attr_paths))  # preserve order, dedupe

        missing = [p for p in all_known if not os.path.exists(p)]
        if not missing:
            return

        # Build set of scan directories: project dirs + watch dirs
        dirs_flags = self._get_dirs_with_flags()
        scan_dirs  = [d for d, _ in dirs_flags if os.path.isdir(d)]
        watch_dirs = [d for d in self.app.config.get("watch_dirs", []) if os.path.isdir(d)]
        all_dirs   = list(dict.fromkeys(scan_dirs + watch_dirs))
        if not all_dirs:
            if not silent:
                QMessageBox.warning(self, "No Directories",
                    "No directories configured to scan.")
            return

        self.lbl_scan.setText(f"Scanning for {len(missing)} missing file(s)…")
        QApplication.processEvents()

        valid_exts = tuple(ext.lower() for ext in (logic.EXT_IMG + logic.EXT_VID))

        # Walk all dirs recursively and index files by basename
        disk_by_name: dict[str, list[str]] = {}
        for d in all_dirs:
            for root, subdirs, files in os.walk(d):
                subdirs[:] = [s for s in subdirs if s != '_unreadable']
                if os.path.basename(root) == '_unreadable':
                    continue
                for f in files:
                    if f.lower().endswith(valid_exts):
                        fp = os.path.normpath(os.path.join(root, f))
                        disk_by_name.setdefault(f, []).append(fp)

        # Match missing → unique candidate on disk
        matches: list[tuple[str, str]] = []   # (old_path, new_path)
        ambiguous: list[tuple[str, list[str]]] = []
        unmatched: list[str] = []

        for old_p in missing:
            basename = os.path.basename(old_p)
            candidates = disk_by_name.get(basename, [])
            if len(candidates) == 1:
                matches.append((old_p, candidates[0]))
            elif len(candidates) > 1:
                ambiguous.append((old_p, candidates))
            else:
                unmatched.append(old_p)

        # ── Shared thumbnail helper (used by both dialogs below) ─────────────────
        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                                     QScrollArea, QWidget, QComboBox, QPushButton,
                                     QSizePolicy, QFrame)
        from PyQt6.QtGui import QPixmap, QImageReader
        from PyQt6.QtCore import QSize

        THUMB_W, THUMB_H = 180, 135

        def _load_thumb(path):
            """Return a scaled QPixmap for path, or a grey placeholder."""
            if not path or not os.path.exists(path):
                px = QPixmap(THUMB_W, THUMB_H)
                px.fill(Qt.GlobalColor.darkGray)
                return px
            ext = os.path.splitext(path)[1].lower()
            reader = QImageReader(path)
            reader.setAutoTransform(True)
            orig = reader.size()
            if orig.isValid():
                sc = min(THUMB_W / max(orig.width(), 1), THUMB_H / max(orig.height(), 1))
                if sc < 1.0:
                    reader.setScaledSize(QSize(max(1, int(orig.width() * sc)),
                                               max(1, int(orig.height() * sc))))
            img = reader.read()
            if not img.isNull():
                return QPixmap.fromImage(img).scaled(
                    THUMB_W, THUMB_H,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
            if ext in ('.mp4', '.mkv', '.mov', '.avi', '.webm'):
                try:
                    import cv2
                    from PyQt6.QtGui import QImage
                    cap = cv2.VideoCapture(path)
                    ret, frame = cap.read(); cap.release()
                    if ret:
                        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        h, w, ch = rgb.shape
                        qimg = QImage(rgb.data, w, h, w * ch, QImage.Format.Format_RGB888)
                        return QPixmap.fromImage(qimg).scaled(
                            THUMB_W, THUMB_H,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)
                except Exception:
                    pass
            px = QPixmap(THUMB_W, THUMB_H); px.fill(Qt.GlobalColor.darkGray)
            return px

        # Auto-pick the first candidate for ambiguous files (files are renamed by the program)
        for old_p, cands in ambiguous:
            if cands:
                matches.append((old_p, cands[0]))
        ambiguous = []

        if not matches and not unmatched:
            self.lbl_scan.setText(""); return

        # Build visual confirmation dialog
        dlg = QDialog(self)
        dlg.setWindowTitle("Fix Moved Files — Confirm")
        dlg.resize(900, 600)
        vl = QVBoxLayout(dlg)
        vl.addWidget(QLabel(
            f"Found <b>{len(matches)}</b> remappable file(s) out of "
            f"<b>{len(missing)}</b> missing. Review and confirm:"))

        scroll_c = QScrollArea(); scroll_c.setWidgetResizable(True)
        inner_c  = QWidget(); inner_vl_c = QVBoxLayout(inner_c); inner_vl_c.setSpacing(6)

        # ── Matches — show thumbnail of new location ──────────────────────────
        if matches:
            hdr = QLabel(f"<b>Will remap ({len(matches)}):</b>")
            hdr.setStyleSheet("color:#8f8; padding:2px 4px;")
            inner_vl_c.addWidget(hdr)
            for old_p, new_p in matches:
                frame = QFrame(); frame.setFrameShape(QFrame.Shape.StyledPanel)
                row_hl = QHBoxLayout(frame)
                row_hl.setContentsMargins(6, 4, 6, 4); row_hl.setSpacing(10)

                thumb = QLabel()
                thumb.setFixedSize(THUMB_W, THUMB_H)
                thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
                thumb.setStyleSheet("background:#1a1a1a; border:1px solid #444;")
                thumb.setPixmap(_load_thumb(new_p))
                row_hl.addWidget(thumb)

                info = QWidget()
                info_vl = QVBoxLayout(info); info_vl.setContentsMargins(0,0,0,0); info_vl.setSpacing(2)
                info_vl.addWidget(QLabel(f"<b>{os.path.basename(old_p)}</b>"))
                old_lbl = QLabel(f"<span style='color:#888'>{old_p}</span>")
                old_lbl.setWordWrap(True)
                new_lbl = QLabel(f"<span style='color:#6af'>→ {new_p}</span>")
                new_lbl.setWordWrap(True)
                info_vl.addWidget(old_lbl); info_vl.addWidget(new_lbl); info_vl.addStretch()
                row_hl.addWidget(info, 1)
                inner_vl_c.addWidget(frame)

        # ── Ambiguous still-skipped ───────────────────────────────────────────
        if ambiguous:
            hdr2 = QLabel(f"<b>Ambiguous — skipped ({len(ambiguous)}):</b>")
            hdr2.setStyleSheet("color:#fa8; padding:2px 4px; margin-top:6px;")
            inner_vl_c.addWidget(hdr2)
            for old_p, cands in ambiguous:
                lbl = QLabel(f"  {os.path.basename(old_p)}: {len(cands)} candidates")
                lbl.setStyleSheet("color:#888;")
                inner_vl_c.addWidget(lbl)

        # ── Unmatched — will be removed ───────────────────────────────────────
        if unmatched:
            hdr3 = QLabel(f"<b>Not found — will be removed ({len(unmatched)}):</b>")
            hdr3.setStyleSheet("color:#f88; padding:2px 4px; margin-top:6px;")
            inner_vl_c.addWidget(hdr3)
            for p in unmatched:
                lbl = QLabel(f"  {p}")
                lbl.setStyleSheet("color:#888;"); lbl.setWordWrap(True)
                inner_vl_c.addWidget(lbl)

        inner_vl_c.addStretch()
        scroll_c.setWidget(inner_c)
        vl.addWidget(scroll_c)

        hl = QHBoxLayout()
        _btn_label = (f"✔ Apply {len(matches)} Remap(s) + Remove {len(unmatched)}"
                      if matches and unmatched else
                      f"✔ Apply {len(matches)} Remap(s)" if matches else
                      f"✔ Remove {len(unmatched)} Missing")
        btn_apply  = QPushButton(_btn_label)
        btn_apply.setStyleSheet(
            "background-color: #1a5a1a; color: white; font-weight: bold;")
        btn_cancel = QPushButton("Cancel")
        hl.addWidget(btn_apply); hl.addStretch(); hl.addWidget(btn_cancel)
        vl.addLayout(hl)

        btn_cancel.clicked.connect(dlg.reject)
        applied = [False]

        def _apply():
            applied[0] = True
            dlg.accept()

        btn_apply.clicked.connect(_apply)
        if auto_apply:
            # Scheduled / unattended Update — skip the confirmation
            # dialog and proceed as if the user clicked Apply.
            applied[0] = True
        else:
            dlg.exec()

        if not applied[0]:
            self.lbl_scan.setText(""); return

        # Apply remaps
        renames = dict(matches)   # old → new
        db_paths_list = self.app.data["paths"]
        for i, p in enumerate(db_paths_list):
            np_ = renames.get(p)
            if np_:
                db_paths_list[i] = np_

        attrs_data = self.app.attrs_data or {}
        for old_p, new_p in matches:
            if old_p in attrs_data:
                attrs_data[new_p] = attrs_data.pop(old_p)

        # Remove truly unmatched entries from both DB and attrs
        if unmatched:
            remove_set = set(unmatched)
            keep_idx = [i for i, p in enumerate(self.app.data["paths"])
                        if p not in remove_set]
            self.app.data["paths"]      = [self.app.data["paths"][i] for i in keep_idx]
            self.app.data["embeddings"] = self.app.data["embeddings"][keep_idx]
            for p in unmatched:
                attrs_data.pop(p, None)

        # Flush to disk
        _am.save(project, attrs_data)
        self.app.attrs_data = attrs_data
        torch.save(self.app.data,
                   os.path.join(_am.DATA_DIR, f"features_{project}.pt"))
        _am.flush_path_renames_to_stores(renames, project)

        # Rebuild path index
        self.app._path_idx = {
            os.path.realpath(p): i
            for i, p in enumerate(self.app.data["paths"])
        }

        proj = getattr(self.app, "current_project", None)
        if proj and not silent:
            QTimer.singleShot(0, lambda: self.app.set_project(proj))
        msg = (f"Remapped {len(matches)} file(s)."
               + (f"  Removed {len(unmatched)} unmatched." if unmatched else ""))
        self.lbl_scan.setText(msg)
        QTimer.singleShot(6000, lambda: self.lbl_scan.setText(""))

    def _rename_util_clicked(self):
        """Rename utility button — renames all files (or new-only) without a CLIP scan."""
        if self._is_scanning:
            self.lbl_scan.setText("Scan in progress — wait for it to finish first.")
            return
        self._rename_only()

    def _rename_only(self):
        """Rename all files in the current project to match stored attrs (person_id + coded fields)."""
        self._do_rename_batch(new_only=False)

    def _rename_new_files(self):
        """Rename only files that have a person_id but don't yet have a coded filename stem."""
        self._do_rename_batch(new_only=True)

    def _do_rename_batch(self, new_only=False):
        """Shared rename loop — runs in background thread to avoid blocking the UI."""
        if self._is_scanning:
            self.lbl_scan.setText("Scan already in progress."); return
        project = self.app.current_project
        if not project:
            QMessageBox.warning(self, "No Project", "No project loaded.")
            return
        all_paths = list(self.app.attrs_data.keys())
        all_paths = [p for p in all_paths if os.path.exists(p)]
        if new_only:
            paths = [p for p in all_paths
                     if attrs_mod.parse_coded_filename(
                         os.path.splitext(os.path.basename(p))[0]) is None]
        else:
            paths = all_paths
        if not paths:
            self.lbl_scan.setText("Nothing to rename." if new_only else "No files found.")
            QTimer.singleShot(3000, lambda: self.lbl_scan.setText(""))
            return

        self._is_scanning = True
        self._stop_rename_only = False
        self._active_scan_btn = self.btn_scan_new if new_only else self.btn_generate
        self._toggle_ui(True)
        self.btn_stop.setEnabled(True)
        self.btn_stop.setText(_t("Stop / 停止"))
        self._active_scan_btn.setText("Renaming…")
        self.btn_scan_new.setEnabled(False)
        label = "new files" if new_only else "files"
        self.lbl_scan.setText(f"Renaming {len(paths)} {label}…")
        self.progress_bar.setRange(0, len(paths))
        self.progress_bar.setValue(0)

        # Snapshot attrs for the worker thread (no CLIP needed — just path renames)
        _attrs = dict(self.app.attrs_data)
        _q = queue.Queue()

        def _worker():
            renames = {}
            renamed = 0
            for i, path in enumerate(paths):
                if self._stop_rename_only:
                    _q.put(("stopped", (renamed, renames, _attrs))); return
                entry = _attrs.get(path, {})
                pid = entry.get("person_id", "")
                if pid:
                    new_path = attrs_mod.rename_with_person_id(
                        _attrs, path, pid, flush_stores=False, project=project,
                        skip_uncoded=False)
                    if new_path != path:
                        renames[path] = new_path
                        renamed += 1
                if (i + 1) % 10 == 0:
                    _q.put(("progress", (i + 1, len(paths), renamed)))
            _q.put(("done", (renamed, renames, _attrs)))

        def _poll_rename():
            try:
                while True:
                    msg, payload = _q.get_nowait()
                    if msg == "progress":
                        done_i, total, renamed = payload
                        self.progress_bar.setValue(done_i)
                        self.progress_label.setText(f"Renaming ({done_i}/{total}), renamed {renamed}")
                    elif msg in ("done", "stopped"):
                        renamed, renames, new_attrs = payload
                        # Apply results back to main thread
                        self.app.attrs_data = new_attrs
                        attrs_mod.save(project, new_attrs)
                        if renames:
                            attrs_mod.flush_path_renames_to_stores(renames, project)
                            # Update CLIP DB paths in-place (in-memory copy)
                            if self.app.data and "paths" in self.app.data:
                                path_map = {os.path.normpath(k): v for k, v in renames.items()}
                                for idx2, p in enumerate(self.app.data["paths"]):
                                    np_ = path_map.get(os.path.normpath(p))
                                    if np_:
                                        self.app.data["paths"][idx2] = np_
                                torch.save(self.app.data, os.path.join(attrs_mod.DATA_DIR, f"features_{project}.pt"))
                                # Rebuild path index
                                self.app._path_idx = {
                                    os.path.realpath(p): i
                                    for i, p in enumerate(self.app.data["paths"])
                                }
                        word = "Done" if msg == "done" else "Stopped"
                        self.lbl_scan.setText(f"{word} — {renamed} file(s) renamed.")
                        QTimer.singleShot(5000, lambda: self.lbl_scan.setText(""))
                        if self._poll_timer:
                            self._poll_timer.stop(); self._poll_timer = None
                        self._scan_done()
                        return
            except queue.Empty:
                pass

        threading.Thread(target=_worker, daemon=True).start()
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(_poll_rename)
        self._poll_timer.start(100)

    def _add_watch_dir(self):
        picker = FolderPickerDialog(self, title="Select folder to watch")
        d = picker.result
        if not d: return
        existing = [self._watch_dir_list.item(i).text()
                    for i in range(self._watch_dir_list.count())]
        if d in existing: return
        self._watch_dir_list.addItem(d)
        dirs = existing + [d]
        # watch_dirs is global — save to global config so all projects share it
        _g = cfg.load_config()
        _g["watch_dirs"] = dirs
        cfg.save_config(_g)
        self.app.config["watch_dirs"] = dirs
        self.app._apply_watch_dirs()

    def _remove_watch_dir(self):
        row = self._watch_dir_list.currentRow()
        if row < 0: return
        self._watch_dir_list.takeItem(row)
        dirs = [self._watch_dir_list.item(i).text()
                for i in range(self._watch_dir_list.count())]
        # watch_dirs is global — save to global config so all projects share it
        _g = cfg.load_config()
        _g["watch_dirs"] = dirs
        cfg.save_config(_g)
        self.app.config["watch_dirs"] = dirs
        self.app._apply_watch_dirs()

    # --- directory helpers ---

    def add_dir(self):
        import sys
        if sys.platform == "win32":
            initial = os.path.expanduser("~")
        elif sys.platform == "darwin":
            initial = os.path.expanduser("~/Pictures")
        else:
            initial = os.path.expanduser("~")
        picker = FolderPickerDialog(self, initialdir=initial, title="Select Folder")
        if picker.result:
            existing = self._get_dir_paths()
            if picker.result not in existing:
                self._add_dir_row(picker.result, True)

    def _add_dir_row(self, path, recursive=True):
        from PyQt6.QtWidgets import QCheckBox
        row = self.dir_listbox.rowCount()
        self.dir_listbox.insertRow(row)
        item = QTableWidgetItem(path)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.dir_listbox.setItem(row, 0, item)
        cell_widget = QWidget()
        cell_layout = QHBoxLayout(cell_widget)
        cell_layout.setContentsMargins(0, 0, 0, 0)
        cell_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chk = QCheckBox()
        chk.setChecked(recursive)
        cell_layout.addWidget(chk)
        self.dir_listbox.setCellWidget(row, 1, cell_widget)
        self._update_generate_btn()

    def _get_dir_paths(self):
        return [self.dir_listbox.item(i, 0).text()
                for i in range(self.dir_listbox.rowCount())]

    def _get_dirs_with_flags(self):
        """Returns list of (path, no_sub) tuples."""
        from PyQt6.QtWidgets import QCheckBox
        result = []
        for i in range(self.dir_listbox.rowCount()):
            path = self.dir_listbox.item(i, 0).text()
            w1   = self.dir_listbox.cellWidget(i, 1)
            chk1 = w1.findChild(QCheckBox) if w1 else None
            recursive = chk1.isChecked() if chk1 else True
            result.append((path, not recursive))
        return result

    def remove_selected_dirs(self):
        rows = sorted({idx.row() for idx in self.dir_listbox.selectedIndexes()}, reverse=True)
        for row in rows:
            self.dir_listbox.removeRow(row)
        self._update_generate_btn()

    # --- project management ---

    def _refresh_list(self):
        self.db_projects = sorted([
            f.replace('features_', '').replace('.pt', '')
            for f in os.listdir(attrs_mod.DATA_DIR) if f.startswith('features_') and f.endswith('.pt')
        ])
        self.proj_combo.blockSignals(True)
        self.proj_combo.clear()
        self.proj_combo.addItems(self.db_projects)
        self.proj_combo.blockSignals(False)

    def delete_project(self):
        t = self.proj_combo.currentText()
        if not t: return
        if QMessageBox.question(self, "Delete", f"Delete {t}?",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            import aisearch_attrs as _am
            for path in (os.path.join(_am.DATA_DIR, f"features_{t}.pt"), _am.attrs_path(t)):
                if os.path.exists(path):
                    os.remove(path)
            self._refresh_list()
            if self.app.current_project == t:
                self.app.set_project("")
            QMessageBox.information(self, "Done", "Deleted.")

    def _create_new_project(self):
        """Register project: create new or update dirs of existing project."""
        name = self.new_proj_entry.text().strip()
        if not name:
            QMessageBox.warning(self, "No Name", "Enter a project name first."); return
        dirs_flags = self._get_dirs_with_flags()
        if not dirs_flags:
            QMessageBox.warning(self, "No Dirs", "Add at least one directory before registering."); return
        base_dirs  = [d       for d, no_sub in dirs_flags]
        base_nosub = [no_sub  for _, no_sub in dirs_flags]
        pt_path = os.path.join(attrs_mod.DATA_DIR, f"features_{name}.pt")
        if os.path.exists(pt_path):
            # Update dirs on existing project without touching embeddings
            existing, _ = logic.load_db_logic(name)
            if existing is None:
                existing = {"paths": [], "embeddings": torch.empty((0, logic.EMBEDDING_DIM)).to(logic.device)}
            existing["base_dirs"]       = base_dirs
            existing["base_dirs_nosub"] = base_nosub
            torch.save(existing, pt_path)
        else:
            data = {"paths": [], "embeddings": torch.empty((0, logic.EMBEDDING_DIM)).to(logic.device),
                    "base_dirs": base_dirs, "base_dirs_nosub": base_nosub}
            torch.save(data, pt_path)
            # Create blank person registry and faces file for new project
            reg_path = attrs_mod.person_registry_file_for_project(name)
            if not os.path.exists(reg_path):
                import json
                with open(reg_path, "w") as _f:
                    json.dump({}, _f)
            faces_path = os.path.join(attrs_mod.DATA_DIR, f"faces_{name}.json")
            if not os.path.exists(faces_path):
                with open(faces_path, "w") as _f:
                    json.dump({}, _f)
            # Bootstrap project-specific config files from global defaults
            import shutil as _shutil
            _copies = [
                (attrs_mod.tags_file_for_project(None),
                 attrs_mod.tags_save_path_for_project(name)),
                (attrs_mod.workspace_file_for_project(None),
                 attrs_mod.workspace_save_path_for_project(name)),
                (attrs_mod.metadata_rules_file_for_project(None),
                 attrs_mod.metadata_rules_save_path_for_project(name)),
            ]
            for _src, _dst in _copies:
                if _src and os.path.exists(_src) and not os.path.exists(_dst):
                    _shutil.copy2(_src, _dst)
            # Bootstrap canvas DB (attr_viewer_*.db) from global
            try:
                from attr_viewer import _db_file_for_config
                _src_db = _db_file_for_config(attrs_mod.tags_file_for_project(None))
                _dst_db = _db_file_for_config(attrs_mod.tags_save_path_for_project(name))
                if os.path.exists(_src_db) and not os.path.exists(_dst_db):
                    _shutil.copy2(_src_db, _dst_db)
            except Exception:
                pass
        self._refresh_list()
        self.proj_combo.blockSignals(True)
        self.proj_combo.setCurrentText(name)
        self.proj_combo.blockSignals(False)
        self.new_proj_entry.setText(name)
        # Refresh the per-tab project dropdowns (Attributes / Filename Rules /
        # Meta Map) so the new project appears immediately. Without this they
        # only show projects that existed when Settings was opened, so a
        # freshly registered project would show only "default" until reopen.
        for _cb_name in ("_attr_proj_cb", "_fn_proj_cb", "_meta_proj_cb"):
            _cb = getattr(self, _cb_name, None)
            if _cb is not None and _cb.findText(name) < 0:
                _cb.addItem(name)
        self.app.set_project(name)
        self._update_scan_project_label()
        self.lbl_scan.setText(f"Registered '{name}' with {len(base_dirs)} dir(s).")

    def append_project_info(self):  # kept for backward compat
        t = self.proj_combo.currentText()
        if t: self._fill_dirs(t, True)

    def _fill_dirs(self, name, highlight=False):
        # Use already-loaded app data when available — avoids re-reading the .pt file
        if (name == getattr(self.app, 'current_project', None)
                and getattr(self.app, 'data', None)):
            data = self.app.data
        else:
            data, _ = logic.load_db_logic(name)
        if not data:
            return
        existing = set(self._get_dir_paths())
        # Prefer saved base_dirs; fall back to deriving from paths
        saved_base = [d for d in data.get("base_dirs", []) if d and os.path.exists(d)]
        if saved_base:
            saved_nosub = data.get("base_dirs_nosub", [False] * len(saved_base))
            mini = list(zip(saved_base, saved_nosub))
        elif data.get("paths"):
            raw = []
            for p in data["paths"]:
                ap = os.path.abspath(p); pts = ap.split(os.sep)
                raw.append(os.sep.join(pts[:pts.index(name)+1]) if name in pts else os.path.dirname(ap))
            dirs_dedup = []
            for d in sorted(set(raw)):
                if not any(d.startswith(e + os.sep) for e, _ in dirs_dedup):
                    dirs_dedup = [(m, ns) for m, ns in dirs_dedup if not m.startswith(d + os.sep)]
                    dirs_dedup.append((d, False))
            mini = dirs_dedup
        else:
            return
        for d, no_sub in sorted(mini):
            if d not in existing and os.path.exists(d):
                row = self.dir_listbox.rowCount()
                self._add_dir_row(d, not no_sub)
                if highlight:
                    for col in range(self.dir_listbox.columnCount()):
                        item = self.dir_listbox.item(row, col)
                        if item:
                            item.setBackground(QColor('#1a3a3a'))
                            item.setForeground(QColor('#00ffcc'))

    def _sync_scan_section(self, name):
        """Update new_proj_entry + dir_listbox + auto_rename to reflect `name`."""
        if not name:
            return
        self.new_proj_entry.blockSignals(True)
        self.new_proj_entry.setText(name)
        self.new_proj_entry.blockSignals(False)
        self.dir_listbox.setRowCount(0)
        self._fill_dirs(name, False)
        # Keep proj_combo in sync too
        if self.proj_combo.currentText() != name:
            self.proj_combo.blockSignals(True)
            self.proj_combo.setCurrentText(name)
            self.proj_combo.blockSignals(False)
        # Update auto_rename checkbox for the selected project
        import aisearch_attrs as _ams
        _ar = _ams.load_filename_config(name).get("auto_rename", False)
        if hasattr(self, 'chk_rename_on_scan') and self.chk_rename_on_scan.isChecked() != _ar:
            self.chk_rename_on_scan.blockSignals(True)
            self.chk_rename_on_scan.setChecked(_ar)
            self.chk_rename_on_scan.blockSignals(False)

    def _on_project_select(self, text=None):
        n = self.proj_combo.currentText()
        if not n:
            return
        # Sync the entire scan section to the selected project
        self._sync_scan_section(n)
        # Refresh the color swatch to match selected project
        self._refresh_proj_color_swatch()
        # Also update the person tab
        if hasattr(self, '_refresh_person_tab'):
            self._refresh_person_tab(n)

    def _selected_project_bg_color(self):
        """Return the saved bg color for the selected project (in combo).
        Reads project_settings_<PROJECT>.json directly so we don't need to
        switch app state to view another project's color."""
        proj = self.proj_combo.currentText()
        if not proj:
            return ""
        # Active project: read from live config (in case unsaved)
        if proj == getattr(self.app, "current_project", None):
            return self.app.config.get("project_bg_color", "") or ""
        # Other projects: load their settings file
        try:
            return cfg.load_config(proj).get("project_bg_color", "") or ""
        except Exception:
            return ""

    def _refresh_proj_color_swatch(self):
        """Update the color-swatch button to show the selected project's color."""
        if not hasattr(self, "btn_proj_color"):
            return
        col = self._selected_project_bg_color() or "#888"
        # Solid color background, neutral border. Empty string falls back to gray.
        self.btn_proj_color.setStyleSheet(
            f"background-color: {col}; border: 1px solid #555;")

    def _pick_project_color(self):
        from PyQt6.QtWidgets import QColorDialog, QMenu
        proj = self.proj_combo.currentText()
        if not proj:
            return
        _menu = QMenu(self)
        _act_pick  = _menu.addAction(_t("Pick color… / 色を選択…"))
        _act_clear = _menu.addAction(_t("Clear (use theme default) / クリア（デフォルト）"))
        _act = _menu.exec(self.btn_proj_color.mapToGlobal(
            self.btn_proj_color.rect().bottomLeft()))
        if _act == _act_pick:
            cur = self._selected_project_bg_color()
            initial = QColor(cur) if cur else QColor("#495057")
            color = QColorDialog.getColor(initial, self,
                                          _t(f"Background color for {proj}"))
            if color.isValid():
                self._save_project_color(proj, color.name())
        elif _act == _act_clear:
            self._save_project_color(proj, "")
        self._refresh_proj_color_swatch()

    def _save_project_color(self, proj, color_hex):
        """Persist the bg color into the project's settings file. If the
        project is the active one, also update live config and refresh UI."""
        if proj == getattr(self.app, "current_project", None):
            if color_hex:
                self.app.config["project_bg_color"] = color_hex
            else:
                self.app.config.pop("project_bg_color", None)
            cfg.save_config(self.app.config, proj)
            self.app._apply_header_theme()
            _pw = getattr(self.app, "preview_handler", None)
            if _pw and getattr(_pw, "window", None):
                _pw.window._apply_project_bg()
        else:
            # Edit the project's settings file directly
            try:
                _data = cfg.load_config(proj)
                if color_hex:
                    _data["project_bg_color"] = color_hex
                else:
                    _data.pop("project_bg_color", None)
                cfg.save_config(_data, proj)
            except Exception:
                pass

    def switch_project(self):
        t = self.proj_combo.currentText()
        if t:
            self.app.set_project(t)
            self._sync_scan_section(t)
            self._update_scan_project_label()
            # Sync Canvas tab project selector and reload its config
            cb = getattr(self, "_canvas_proj_cb", None)
            if cb:
                cb.blockSignals(True)
                cb.setCurrentText(t)
                cb.blockSignals(False)
            cw = getattr(self, "_canvas_widget", None)
            if cw:
                import aisearch_attrs as _am
                cw.reload(_am.tags_file_for_project(t))
            if self.chk_close_on_load.isChecked():
                self.hide()
