import os, queue, threading, torch
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                              QLabel, QLineEdit, QGroupBox, QCheckBox,
                              QProgressBar, QComboBox, QMessageBox,
                              QTableWidget, QTableWidgetItem, QHeaderView,
                              QListWidget, QApplication)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor

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
        g1 = QGroupBox("📂 Switch Project")
        l1 = QHBoxLayout(g1)
        self.db_projects = sorted([
            f.replace('features_', '').replace('.pt', '')
            for f in os.listdir('.') if f.startswith('features_') and f.endswith('.pt')
        ])
        self.proj_combo = QComboBox()
        self.proj_combo.addItems(self.db_projects)
        self.proj_combo.setCurrentText(self.app.current_project)
        self.proj_combo.currentTextChanged.connect(self._on_project_select)
        l1.addWidget(self.proj_combo, stretch=1)
        self.btn_load   = QPushButton("Load")
        self.btn_load.setToolTip("Load selected project and close settings")
        self.btn_load.clicked.connect(self.switch_project)
        self.btn_reset  = QPushButton("Reset")
        self.btn_reset.setToolTip("Clear embeddings, keep base dirs — re-run Scan ALL to rebuild")
        self.btn_reset.clicked.connect(self.reset_project)
        self.btn_reset.setStyleSheet("background-color: #5a3a00; color: white;")
        self.btn_delete = QPushButton("Delete")
        self.btn_delete.setToolTip("Delete project entirely")
        self.btn_delete.clicked.connect(self.delete_project)
        self.btn_delete.setStyleSheet("background-color: #7a2020; color: white;")
        for b in [self.btn_load, self.btn_reset, self.btn_delete]:
            l1.addWidget(b)
        tl.addWidget(g1)

        # ── Create / Update Database ─────────────────────────────────────────
        g2 = QGroupBox("🛠 Create / Update Database")
        l2 = QVBoxLayout(g2)
        proj_name_row = QHBoxLayout()
        proj_name_row.addWidget(QLabel("Project Name:"))
        self.new_proj_entry = QLineEdit()
        self.new_proj_entry.setText(self.app.current_project or "")
        self.new_proj_entry.setPlaceholderText("Enter name for new or existing project…")
        proj_name_row.addWidget(self.new_proj_entry, stretch=1)
        def _on_proj_name_changed(text):
            text = text.strip()
            existing = [f.replace('features_', '').replace('.pt', '')
                        for f in os.listdir('.') if f.startswith('features_') and f.endswith('.pt')]
            if text in existing:
                # Load that project's dirs
                self.dir_listbox.setRowCount(0)
                self._fill_dirs(text, False)
            else:
                # New project name — clear listbox so user adds fresh dirs
                self.dir_listbox.setRowCount(0)
                self._update_generate_btn()
        self.new_proj_entry.textChanged.connect(_on_proj_name_changed)
        btn_new_proj = QPushButton("Register")
        btn_new_proj.setToolTip("Register a new project with the current name and directories")
        btn_new_proj.setFixedWidth(90)
        btn_new_proj.clicked.connect(self._create_new_project)
        proj_name_row.addWidget(btn_new_proj)
        l2.addLayout(proj_name_row)

        self.dir_listbox = QTableWidget(0, 2)
        self.dir_listbox.setFixedHeight(150)
        self.dir_listbox.setHorizontalHeaderLabels(["Directory", "Recursive"])
        self.dir_listbox.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.dir_listbox.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.dir_listbox.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.dir_listbox.verticalHeader().setVisible(False)
        l2.addWidget(self.dir_listbox)

        bf = QHBoxLayout()
        self.btn_add_dir    = QPushButton("+ Add Dir"); self.btn_add_dir.clicked.connect(self.add_dir)
        self.btn_remove_dir = QPushButton("- Remove");  self.btn_remove_dir.clicked.connect(self.remove_selected_dirs)
        bf.addWidget(self.btn_add_dir); bf.addWidget(self.btn_remove_dir); bf.addStretch()
        l2.addLayout(bf)

        self.progress_label = QLabel("Status: Ready")
        l2.addWidget(self.progress_label)
        self.progress_bar = QProgressBar(); self.progress_bar.setRange(0, 100); self.progress_bar.setValue(0)
        l2.addWidget(self.progress_bar)

        # Status label (used by scan operations)
        self.lbl_scan_project = QLabel()
        self._update_scan_project_label()
        l2.addWidget(self.lbl_scan_project)
        self.lbl_scan = QLabel("")
        l2.addWidget(self.lbl_scan)

        # ── 3 action buttons ──────────────────────────────────────────────────
        action_row = QHBoxLayout()
        self.btn_generate = QPushButton("Scan ALL")
        self.btn_generate.setToolTip("Process every file: CLIP + face + metadata.\nWARNING: resets and rebuilds from scratch.")
        self.btn_generate.clicked.connect(lambda: self.execute_generate(reset=True))
        self.btn_generate.setStyleSheet(
            "background-color: #7a3a00; color: white; font-weight: bold; padding: 6px;")
        action_row.addWidget(self.btn_generate, stretch=1)

        self.btn_scan_new = QPushButton("Update")
        self.btn_scan_new.setToolTip("Process only new / unprocessed files.")
        self.btn_scan_new.clicked.connect(lambda: self.execute_generate(reset=False))
        self.btn_scan_new.setStyleSheet(
            "background-color: #1a5a1a; color: white; font-weight: bold; padding: 6px;")
        action_row.addWidget(self.btn_scan_new, stretch=1)

        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setEnabled(False)
        self.btn_stop.setStyleSheet("background-color: #7a2020; color: white; font-weight: bold;")
        self.btn_stop.clicked.connect(self._unified_stop)
        action_row.addWidget(self.btn_stop)
        l2.addLayout(action_row)
        self._update_generate_btn()

        # ── Utility buttons row ───────────────────────────────────────────────
        util_row = QHBoxLayout()
        btn_rename_util = QPushButton("✏ Rename Files")
        btn_rename_util.setToolTip(
            "Rename all project files to coded format (no CLIP/face scan).\n"
            "Update mode: rename only files not yet in coded format.")
        btn_rename_util.setStyleSheet(
            "background-color: #2a2a4a; color: #aaaaff; padding: 4px 8px;")
        btn_rename_util.clicked.connect(
            lambda: self._rename_only() if True else self._rename_new_files())
        # Use Scan ALL vs Update button state to decide
        btn_rename_util.clicked.disconnect()
        btn_rename_util.clicked.connect(self._rename_util_clicked)
        util_row.addWidget(btn_rename_util)

        btn_unlock_util = QPushButton("🔓 Unlock All")
        btn_unlock_util.setToolTip(
            "Run metadata scan on all files to set editable flag — no CLIP scan.")
        btn_unlock_util.setStyleSheet(
            "background-color: #2a3a2a; color: #aaffaa; padding: 4px 8px;")
        btn_unlock_util.clicked.connect(self._unlock_all_metadata)
        util_row.addWidget(btn_unlock_util)

        util_row.addStretch()
        l2.addLayout(util_row)

        # Compat stubs — older call sites that check _face_mode_group.checkedId() or
        # reference the radio buttons still work without errors.
        class _FMG:
            def checkedId(self): return 1   # always "Include face" = 1
        class _R:
            def setChecked(self, v): pass
            def isChecked(self): return False
            def setEnabled(self, v): pass
            def toggled(self): pass
        self._face_mode_group   = _FMG()
        self._radio_no_face     = _R()
        self._radio_incl_face   = _R()
        self._radio_face_only   = _R()
        self._radio_rename_only = _R()
        self._radio_unlock_only = _R()
        self._btn_rename_util   = btn_rename_util

        # ── Options row: Auto rename + Unlock all ────────────────────────────
        opt_row = QHBoxLayout()

        self.chk_rename_on_scan = _QCB("✏️ Auto rename")
        self.chk_rename_on_scan.setToolTip("Auto-rename files during scan using Filename Rules.")
        self.chk_rename_on_scan.setChecked(self.app.config.get("auto_rename", False))
        def _apply_rename_only_state(enabled):
            pass  # no longer needed — rename is a utility button, not a mode
        def _on_rename_toggled(v):
            self.app.config["auto_rename"] = v
            cfg.save_config(self.app.config, getattr(self.app, "current_project", None))
            pw = getattr(self.app, "preview_handler", None)
            pw = getattr(pw, "window", None)
            if pw:
                if hasattr(pw, "_btn_auto_rename"):
                    pw._btn_auto_rename.setVisible(v)
                if hasattr(pw, "_chk_auto_rename") and pw._chk_auto_rename.isChecked() != v:
                    pw._chk_auto_rename.blockSignals(True)
                    pw._chk_auto_rename.setChecked(v)
                    pw._chk_auto_rename.blockSignals(False)
            # Keep filename tab checkbox in sync
            fn_chk = getattr(self, "check_auto_rename", None)
            if fn_chk and fn_chk.isChecked() != v:
                fn_chk.blockSignals(True)
                fn_chk.setChecked(v)
                fn_chk.blockSignals(False)
        self.chk_rename_on_scan.toggled.connect(_on_rename_toggled)
        # Set initial state
        _apply_rename_only_state(self.app.config.get("auto_rename", False))
        opt_row.addWidget(self.chk_rename_on_scan)

        opt_row.addStretch()
        l2.addLayout(opt_row)

        self._stop_rename_only = False
        self._stop_scan_all = False
        # Aliases so older code paths keep working
        self.btn_stop_scan = self.btn_stop
        self.btn_stop_rename_only = self.btn_stop
        self.btn_rename_only = None   # kept as None so old refs don't crash
        self.chk_rename_only = self._radio_rename_only  # compat: checkedId()==3 is the real check

        tl.addWidget(g2)

        # ── Watch Folders ─────────────────────────────────────────────────────
        g_watch = QGroupBox("👁 Watch Folders (auto-add new files)")
        gw = QVBoxLayout(g_watch)
        gw.addWidget(QLabel("New files dropped here are added to the DB automatically:"))
        self._watch_dir_list = QListWidget()
        self._watch_dir_list.setFixedHeight(80)
        for d in self.app.config.get("watch_dirs", []):
            self._watch_dir_list.addItem(d)
        gw.addWidget(self._watch_dir_list)
        wr = QHBoxLayout()
        btn_add_w = QPushButton("+ Add"); btn_add_w.clicked.connect(self._add_watch_dir)
        btn_rem_w = QPushButton("- Remove"); btn_rem_w.clicked.connect(self._remove_watch_dir)
        wr.addWidget(btn_add_w); wr.addWidget(btn_rem_w); wr.addStretch()
        gw.addLayout(wr)
        tl.addWidget(g_watch)

        tl.addStretch()
        tabs.addTab(tab_data, "🗄 Database")

    # --- scanning ---

    def execute_generate(self, reset=True):
        if self._is_scanning:
            # Auto-recover: if no poll timer is active, the scan finished but _is_scanning
            # was never cleared (old bug / exception). Force-reset so the user can retry.
            timer_live = self._poll_timer is not None and self._poll_timer.isActive()
            if not timer_live and not getattr(self, '_is_metadata_scanning', False):
                self._is_scanning = False
                self._toggle_ui(False)
                self.btn_scan_new.setEnabled(True)
                self.btn_stop.setEnabled(False)
                self.btn_stop.setText("Stop")
            else:
                self.lbl_scan.setText("Scan already in progress — press Stop to cancel.")
                return
        name = self.new_proj_entry.text().strip()
        dirs_flags  = self._get_dirs_with_flags()
        dirs     = [d       for d, _      in dirs_flags]
        no_subs  = [no_sub  for _, no_sub in dirs_flags]
        if not name:
            self.lbl_scan.setText("Enter a project name first."); return
        if not dirs:
            self.lbl_scan.setText("No directories configured — use '+ Add Dir' first."); return

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
            pt = f"features_{name}.pt"
            if os.path.exists(pt):
                os.remove(pt)
            faces_pt = attrs_mod.faces_db_path(name)
            if os.path.exists(faces_pt):
                os.remove(faces_pt)
        self._is_scanning = True
        self._stop_scan = False
        self._active_scan_btn = self.btn_generate if reset else self.btn_scan_new
        self._toggle_ui(True)
        self._active_scan_btn.setText("Scanning…")
        self.btn_stop.setEnabled(True)
        self.btn_scan_new.setEnabled(False)
        self._scan_queue = queue.Queue()

        _auto_rename = self.app.config.get("auto_rename", False)
        self.lbl_scan.setText("Starting scan — CLIP + face + metadata…")

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

                def _save_checkpoint():
                    torch.save(data, f"features_{name}.pt")
                    attrs_mod.save(name, attrs_data)

                for i, p in enumerate(to_add):
                    if self._stop_scan:
                        _save_checkpoint()
                        if scan_renames:
                            attrs_mod.flush_path_renames_to_stores(scan_renames, name)
                        self._scan_queue.put(("stopped", (removed, added, failed, attrs_data, faces_found, face_errors))); return

                    fname = os.path.basename(p)
                    if os.path.getsize(p) == 0:
                        failed.append((p, "0 bytes")); continue

                    # ── Step 1: CLIP embed ────────────────────────────────────
                    self._scan_queue.put(("progress", (i + 1, len(to_add), fname, "CLIP")))
                    emb = logic.extract_feature(p)
                    if emb is None:
                        failed.append((p, "unreadable/corrupt")); continue

                    # ── Step 2: CLIP auto-detect attributes ───────────────────
                    self._scan_queue.put(("progress", (i + 1, len(to_add), fname, "attrs")))
                    try:
                        clip_updates = attrs_mod.auto_detect_clip_attrs(emb, attrs_data.get(p, {}))
                        if clip_updates:
                            attrs_data.setdefault(p, {}).update(clip_updates)
                    except Exception:
                        pass

                    # ── Step 3: Face ──────────────────────────────────────────
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

                    # ── Step 5: Rename ────────────────────────────────────────
                    if _auto_rename:
                        self._scan_queue.put(("progress", (i + 1, len(to_add), fname, "rename")))
                        try:
                            new_path = attrs_mod.rename_with_person_id(
                                attrs_data, p, pid or "000", flush_stores=False,
                                skip_uncoded=False)
                            if new_path != p:
                                scan_renames[p] = new_path
                                p = new_path
                        except Exception:
                            pass

                    # ── Commit to DB ──────────────────────────────────────────
                    data["paths"].append(p)
                    data["embeddings"] = torch.cat([data["embeddings"], emb.unsqueeze(0)])
                    added += 1

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

    def _poll_scan_queue(self, name):
        _step_labels = {"rename": "Rename", "CLIP": "CLIP", "meta": "Meta", "face": "Face", "attrs": "Attrs"}
        sb = self.app.statusBar()
        try:
            while True:
                msg, payload = self._scan_queue.get_nowait()
                if msg == "total":
                    self.progress_bar.setMaximum(payload)
                elif msg == "moved":
                    old_p, new_p = payload
                    self.lbl_scan.setText(f"Moved: {os.path.basename(old_p)} → {os.path.basename(new_p)}")
                elif msg == "progress":
                    i, total, fname, step = payload
                    step_lbl = _step_labels.get(step, step)
                    self.progress_label.setText(f"[{step_lbl}] ({i}/{total}): {fname}")
                    self.progress_bar.setValue(i)
                    sb.showMessage(f"[{step_lbl}] {name}: {i}/{total} — {fname}")
                elif msg == "checkpoint":
                    self.progress_label.setText(self.progress_label.text() + "  [saved]")
                    sb.showMessage(sb.currentMessage() + "  [saved]")
                elif msg == "uptodate":
                    self._active_scan_btn.setText("Up to date")
                    sb.clearMessage()
                    self.show(); self.raise_()
                    QMessageBox.information(self, "Done", "Already up to date.")
                    self._scan_done(); return
                elif msg == "face_warn":
                    # Face detection error for one file — show briefly, keep scanning
                    self.lbl_scan.setText(f"Face err: {payload[:80]}")
                elif msg == "stopped":
                    removed, added, failed, attrs_data, faces_found, face_errors = payload
                    self.app.attrs_data = attrs_data
                    self._refresh_list()
                    self.proj_combo.setCurrentText(name)
                    self.app.set_project(name)
                    self._active_scan_btn.setText("Stopped")
                    sb.clearMessage()
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
                    self.app.attrs_data = attrs_data
                    self._refresh_list()
                    self.proj_combo.setCurrentText(name)
                    self.app.set_project(name)
                    face_info = f"  |  Faces: {faces_found}" if faces_found or face_errors else ""
                    err_info  = f"  |  Face errors: {len(face_errors)}" if face_errors else ""
                    self._active_scan_btn.setText("Done")
                    self.lbl_scan.setText(f"Done — added {added}, removed {removed}{face_info}{err_info}")
                    QTimer.singleShot(8000, lambda: self.lbl_scan.setText(""))
                    sb.clearMessage()
                    if failed: self._show_failed_files(failed)
                    if face_errors: self._show_face_errors(face_errors)
                    self._scan_done(); return
                elif msg == "error":
                    self._active_scan_btn.setText("Error")
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
        self.btn_stop.setText("Stopping…")

    def _scan_done(self):
        if self._poll_timer:
            self._poll_timer.stop()
            self._poll_timer = None
        self._stop_scan = False
        # Restore each button to its original label
        self.btn_generate.setText("Scan ALL")
        self.btn_scan_new.setText("Update")
        self.btn_stop.setEnabled(False)
        self.btn_stop.setText("Stop")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_label.setText("Status: Ready")
        self._is_metadata_scanning = False
        self._is_scanning = False
        self._toggle_ui(False)
        self.btn_scan_new.setEnabled(True)
        self._update_generate_btn()

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
        for w in [self.btn_load, self.btn_reset, self.btn_delete,
                  self.btn_add_dir, self.btn_remove_dir, self.btn_generate]:
            w.setEnabled(not locked)
        self.proj_combo.setEnabled(not locked)
        # Pause/resume the watch-folder scanner so it doesn't fire mid-rename/scan
        self.app._watcher_paused = locked

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
        """Update: scan new files, or (if Rename only) rename uncoded files."""
        if self._face_mode_group.checkedId() == 3:
            self._rename_new_files()
            return
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
        fn_rules = attrs_mod.load_filename_rules()
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
        face_mode       = self._face_mode_group.checkedId()  # 0=no face, 1=include, 2=face only
        auto_rename     = self.app.config.get("auto_rename", False)
        QApplication.processEvents()
        updated = 0
        scan_renames = {}   # old_path -> new_path, flushed once after the loop
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
                            # Update feature store path if loaded
                            if (self.app.data and "paths" in self.app.data
                                    and path in self.app.data["paths"]):
                                idx2 = self.app.data["paths"].index(path)
                                self.app.data["paths"][idx2] = new_path
                            path = new_path   # continue with new path for unlock/after
            after = attrs_mod.get(self.app.attrs_data, path)
            if after != before:
                updated += 1
            if (i + 1) % 10 == 0 or i == 0:
                self.progress_bar.setValue(i + 1)
                self.progress_label.setText(f"Metadata ({i+1}/{len(paths)}): {os.path.basename(path)}")
                self.lbl_scan.setText(f"Metadata scan: {i+1}/{len(paths)}, updated {updated}")
                QApplication.processEvents()
        else:
            self.progress_bar.setValue(len(paths))
            self.lbl_scan.setText(f"Done — {updated} of {len(paths)} files updated.")
            QTimer.singleShot(4000, lambda: self.lbl_scan.setText(""))
        attrs_mod.save(self.app.current_project, self.app.attrs_data)
        if scan_renames:
            attrs_mod.flush_path_renames_to_stores(scan_renames, self.app.current_project)
            if self.app.data:
                torch.save(self.app.data, f"features_{self.app.current_project}.pt")
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
        self.btn_stop.setText("Stop")
        self.btn_stop_scan.setEnabled(False)

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
        self.btn_stop.setText("Stop")
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
                                torch.save(self.app.data, f"features_{project}.pt")
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
        self.app.config["watch_dirs"] = existing + [d]
        cfg.save_config(self.app.config, getattr(self.app, "current_project", None))
        self.app._apply_watch_dirs()

    def _remove_watch_dir(self):
        row = self._watch_dir_list.currentRow()
        if row < 0: return
        self._watch_dir_list.takeItem(row)
        dirs = [self._watch_dir_list.item(i).text()
                for i in range(self._watch_dir_list.count())]
        self.app.config["watch_dirs"] = dirs
        cfg.save_config(self.app.config, getattr(self.app, "current_project", None))
        self.app._apply_watch_dirs()

    # --- directory helpers ---

    def add_dir(self):
        import sys
        if sys.platform == "win32":
            initial = os.path.expanduser("~")
        elif sys.platform == "darwin":
            initial = os.path.expanduser("~/Pictures")
        else:
            initial = "/mnt/1TBSSD" if os.path.exists("/mnt/1TBSSD") else os.path.expanduser("~")
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
            for f in os.listdir('.') if f.startswith('features_') and f.endswith('.pt')
        ])
        self.proj_combo.blockSignals(True)
        self.proj_combo.clear()
        self.proj_combo.addItems(self.db_projects)
        self.proj_combo.blockSignals(False)

    def reset_project(self):
        t = self.proj_combo.currentText()
        if not t: return
        if QMessageBox.question(self, "Reset", f"Reset '{t}'?\nThis clears all paths and embeddings but keeps base directories.\nYou will need to re-run Generate.",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        pt_file = f"features_{t}.pt"
        data, _ = logic.load_db_logic(t)
        base_dirs   = data.get("base_dirs",       []) if data else []
        base_nosub  = data.get("base_dirs_nosub", []) if data else []
        import torch
        torch.save({"paths": [], "embeddings": None,
                    "base_dirs": base_dirs, "base_dirs_nosub": base_nosub}, pt_file)
        if self.app.current_project == t:
            self.app.load_db()
        self._on_project_select()
        QMessageBox.information(self, "Reset", f"'{t}' has been reset.\nBase dirs preserved: {base_dirs}")

    def delete_project(self):
        t = self.proj_combo.currentText()
        if not t: return
        if QMessageBox.question(self, "Delete", f"Delete {t}?",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            import aisearch_attrs as _am
            for path in (f"features_{t}.pt", _am.attrs_path(t)):
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
        pt_path = f"features_{name}.pt"
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
        self._refresh_list()
        self.proj_combo.blockSignals(True)
        self.proj_combo.setCurrentText(name)
        self.proj_combo.blockSignals(False)
        self.new_proj_entry.setText(name)
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
        """Update new_proj_entry + dir_listbox to reflect `name`, without signal cascade."""
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

    def _on_project_select(self, text=None):
        n = self.proj_combo.currentText()
        if not n:
            return
        # Sync the entire scan section to the selected project
        self._sync_scan_section(n)
        # Also update the person tab
        if hasattr(self, '_refresh_person_tab'):
            self._refresh_person_tab(n)

    def switch_project(self):
        t = self.proj_combo.currentText()
        if t:
            self.app.set_project(t)
            self._sync_scan_section(t)
            self._update_scan_project_label()
            self.hide()   # always close immediately
