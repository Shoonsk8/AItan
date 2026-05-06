# AISearch — Session Changes Log

Most recent entries at the top. Each entry: file:line — what changed.

## 2026-05-06 — v2.4.1

Polish on top of v2.4.

### Right-click menu cleanup
- `aisearch_front_page.py:create_context_menu` — main-table right-click now: **File Manager · Rename · Move to… · Delete**. Dropped "📂 Open Folder" (replaced by File Manager) and "🏷 Attributes…" (preview-window panel covers it).
- `aisearch_app.py` — removed obsolete standalone Attributes window (`edit_attrs`, `_build_attr_window`, `_attr_win_load`), the `a`/`A` shortcut, the inline 📝 Title / Scene… button, and `open_folder`. Net ~165 lines deleted.
- Right-arrow on row 0 reverted to its pre-v2.4 behavior (enters browse mode in main page). FM is now reached through the right-click → 🗂 File Manager item.

### File Manager
- `aisearch_file_manager.py:_FMTreeList` — right-click toggle on coded-field sub-combos (HC_Color / CL_Top / etc.) now actually flips them between freq and alpha. Was: only the main combo had the hook; sub-combos were stuck on freq.
- `aisearch_file_manager.py:_FMItem` — folders sort first, files after, regardless of column or direction. ".." pinned to top.
- `aisearch_file_manager.py:_FMTreeList._collect_expanded` / `_restore_expanded` — folder expansion now persists through Ctrl+Wheel resize and other refreshes.
- `aisearch_file_manager.py:_kick_thumb_loader / populate_root / closeEvent` — wait for the thumbnail QThread to finish before dropping the reference. Was: `terminate called without an active exception` / Aborted on rapid navigation or close.
- `aisearch_file_manager.py:_remove_from_app_state` — FM-driven trash now also removes matching rows from the main-window table and persists `attrs.json` + `features.pt` so the deletion survives a relaunch.
- `aisearch_file_manager.py:FilePane._on_current_changed` — single-click on a tree row sends the file to the preview window (mirrors main-page table behaviour). Double-click still works.
- `aisearch_file_manager.py` — buttons relabeled `Back / Fwd / Up / Dual pane` (the Unicode arrows weren't rendering on the user's font).
- `aisearch_file_manager.py:status_lbl` — live status line per pane: `N selected — total size`.
- `aisearch_front_page.py:open_in_nemo` — for files, opens the parent folder; was launching the default image viewer instead of Nemo.

### Cross-platform
- `aisearch_file_manager.py:_open_default / _app_choices` — Open / Open with… now dispatches by `sys.platform`. Linux uses xdg-open + Pix/GIMP/VLC/etc; macOS uses `open -a` + Preview/QuickTime/IINA; Windows uses os.startfile + Paint/IrfanView/MPV. Only installed apps appear in the menu.

## 2026-05-06 — v2.4

### Version
- `VERSION = "2.4"` across `aisearch_app.py`, `aisearch_settings.py`, `aisearch_front_page.py`, `aisearch_preview.py`, `aisearch_file_manager.py`.
- `aisearch_attrs.py:1433` — `_AITAN_VERSION = "2.4"`.

### File Manager window (new — `aisearch_file_manager.py`)
- New top-level window opened via right-arrow on row 0 (replaces the previous browse-into-main behavior for that key; the 📂 Browse button still does main-page browse). Closes when the main window closes.
- **Single / dual-pane toggle** — top-of-window button switches between one pane and two side-by-side panes via `QSplitter`. Each pane is independent: own Back / Fwd / Up + path bar + tree + history. Drag a file from one pane onto a folder row in the other = move.
- **Tree view** — `QTreeWidget` with 4 columns (Name · Size · Date · Type), expand triangles for folders, lazy-loaded children on first expand, sortable column headers, resizable column widths, custom `_FMItem` subclass that always groups folders before files regardless of column or sort direction. ".." pinned to the top.
- **Thumbnails** — 32 px file thumbnails by default, Ctrl+Wheel resizes 16 → 512 px. Async load via `_ThumbLoader` QThread; cache shared with the rest of the app and bounded at 500 entries. Green inner rim on video thumbnails.
- **Drag-drop** — manual QDrag with file URL MIME via viewport eventFilter (Qt's auto-detection didn't fire reliably in PyQt6 IconMode; Tree works with the same pattern). Drop on folder row = move; drop on file / empty = silent no-op. Multi-select drag preserved on plain click via deferred selection collapse.
- **Context menu** (right-click): New Folder · Rename (F2) · Move to Trash (Del, with confirm) · Open · Open with… · Open in Nemo. "Open with…" submenu lists installed apps per platform (Pix/GIMP/VLC/etc on Linux, Preview/QuickTime/etc on macOS, Paint/IrfanView/etc on Windows). Filtered at menu-build time via `shutil.which`.
- **Single-click → live preview** mirroring the main-page table — `currentItemChanged` triggers `preview_handler.show()`.
- **Right-arrow follows current row** when FM is open — clicking a different row in the main table and pressing → re-points FM at that row's directory instead of opening a fresh one.
- **Disk persistence** — file ops (move / rename / delete / folder rename) batch the on-disk store flush (`features.pt` + `attrs.json`) once at the end so a multi-file drop doesn't take 60+ s of disk I/O.
- **Folder expansion preserved across refresh** — Ctrl+Wheel resize and other repopulates snapshot expanded paths and BFS-restore them after.
- **Cross-platform** — Open / Open with… / Move to Trash / Open in Nemo all work on Linux + macOS + Windows (system trash on Linux, `~/.aisearch_trash` elsewhere; per-OS app candidate lists).

### Search ranking
- `aisearch_app.py` worker — score column already shows raw cos_sim; `_populate_search_results` caps display at `max_search_results` (default 500) but ranks every eligible file. (Carried over from v2.3 — included here as the visible UI now matches.)

### Dup-finder memory
- `aisearch_app.py:_find_duplicates` worker — chunked similarity pass replaces the dense (N, N) cos_sim. CHUNK = 512 rows × N peak ≈ 45 MiB instead of ~1.8 GiB. Added a `_SparseSim` lookup with on-demand fallback so the existing group-sort and display code still has `sim.get(i, j)` access without keeping the full matrix.

### Internationalization
- `aisearch_settings_appearance.py`, `aisearch_settings_attrs.py` — bilingual labels for Theme radio (Dark/ダーク, Light/ライト) and the Attributes tab's coded-field section headers (Clothing/服装, Eyes/目, etc.). Stored JSON keys remain English so data compat is unchanged.

### Other small fixes
- `aisearch_app.py:on_right_key_press` — bails when the query file's directory is gone (Nemo external move). Drops stale row 0, rebases search on row 1.
- `aisearch_front_page.py:open_in_nemo` — for files, opens parent folder (was launching default image viewer instead of Nemo).

## 2026-05-05 — v2.3

### Version
- `aisearch_app.py:25`, `aisearch_settings.py:15`, `aisearch_front_page.py:11`, `aisearch_preview.py:17` — `VERSION = "2.3"`.
- `aisearch_attrs.py:1433` — `_AITAN_VERSION = "2.3"`.

### Search ranking
- `aisearch_app.py` worker — topk now uses `n_allowed` (every eligible file is part of the ranking), so same-dir / feedback boosts can lift any match into the displayed results. Was: `topk(2000)` hard cap meant a same-dir file at raw rank 4000 could never surface even though +0.04 same-dir boost would have lifted it past many displayed rows.
- `aisearch_app.py:_populate_search_results` — applies `max_search_results` (default 500) as the display cap; files skipped (query itself, missing on disk) don't count toward it.
- `aisearch_app.py` worker — score column now shows raw cos_sim, not the boosted total. Boosts still drive ranking order, but the column reflects actual similarity. Was: feedback (+0.25) + proximity (+0.04) could push raw 0.7 over 1.0 where the 0.9999 cap clamped many rows to a fake near-duplicate score.

### Move / right-key
- `aisearch_app.py:on_right_key_press` — bails when the query file's directory has been removed externally (Nemo, etc.). Drops the stale row 0 and rebases the search on row 1 so the next right-press has a valid destination. Was: dirname of a stale `query_path` resolved to a now-absent location, sometimes the project root.

### Schedule Update DB
- `aisearch_settings_db.py` — new row in the Database tab: time picker (`HH:mm`) + ⏰ Schedule button. Picking a future time and clicking Schedule arms a one-shot timer that fires `execute_generate(reset=False)` at the chosen time. Click again while armed to cancel; if the time has already passed today the schedule rolls to tomorrow. App must stay open until firing.
- `aisearch_settings_db.py:_rescan_moved_files / execute_generate` — both gained an `auto_apply` flag. Scheduled fires pass `auto_apply=True`, so the "Fix Moved Files — Confirm" dialog is skipped and the remap proceeds as if the user clicked Apply. Manual Update click still surfaces the dialog.

### UI cleanup
- `aisearch_app.py` `__init__` — main-window status bar now auto-hides when empty (`messageChanged → setVisible(bool(text))`); used to eat ~20 px at the bottom even with no message.
- `aisearch_settings.py` — Settings dialog default size 800 → 1100 px wide.
- `aisearch_settings_db.py` worker — scan progress / face-error messages now show full file path instead of basename.

## 2026-05-04 — v2.2

### Version
- `aisearch_app.py:25`, `aisearch_settings.py:15`, `aisearch_front_page.py:11`, `aisearch_preview.py:17` — `VERSION = "2.2"`.
- `aisearch_attrs.py:1433` — `_AITAN_VERSION = "2.2"` (data-format stamp).

### Watch-dir handling during long Updates
- `aisearch_settings_db.py:execute_generate` — Update DB now also walks `watch_dirs` (non-recursive) on top of project `base_dirs`. Files dropped in Downloads before the scan starts get indexed by the same scan. Watch dirs stay out of `data["base_dirs"]` so search-result filtering is unchanged.
- `aisearch_app.py:_do_scan_new_files` — when the watcher detects an unknown file in `watch_dirs` while a settings scan is in progress, it now requests `_unified_stop` on the active scan. The scan saves a checkpoint before exiting, then auto-resumes once the new file is indexed.
- `aisearch_settings_db.py:_toggle_ui` — on unpause, immediately kicks `_scan_new_files` so the just-detected file gets indexed without waiting for the 30 s fallback tick. If the stop was watcher-triggered, also fires `execute_generate(reset=False)` to resume the Update from its checkpoint. Manual Stop still works as before.
- `aisearch_settings_db.py:_poll_queue` (stopped branch) — watcher-triggered stops are silent: no Stopped dialog, no Settings show/raise, no failed-files / face-errors popups. Button reads "Paused…" before auto-resume kicks in.

### Preview window stability
- `aisearch_app.py:set_project` — only resets the preview window when the project name actually changes. Was: any `set_project(name)` call (including the "reload data from disk" hooks at scan checkpoint) destroyed and recreated the preview, so the watcher-pause flow visibly closed and reopened the window.

### Header
- `aisearch_app.py:_set_base_dir_label` — drops the "Base:" prefix and the six-space continuation indent on multi-dir labels. Paths now align flush-left, one per line.

## 2026-05-04 — v2.1

### Version
- `aisearch_app.py:25`, `aisearch_settings.py:15`, `aisearch_front_page.py:11`, `aisearch_preview.py:17` — `VERSION = "2.1"`.
- `aisearch_attrs.py:1433` — `_AITAN_VERSION = "2.1"` (data-format stamp).

### Table selection (PyQt6)
- `aisearch_app.py:FileTable` — stopped overriding Qt's native click handling; rebuilt around it. Was: custom mousePressEvent was clearing/recreating selections and fighting Qt's own logic, producing a long tail of corner-case bugs (multi-select collapsing on plain click, Ctrl+click toggling away neighbors, rubberband appearing on drag-from-selected-row).
- `aisearch_app.py:FileTable.mousePressEvent` — plain click on a multi-selected row keeps the selection (used to start a drag); Ctrl+click toggles only that row; release-side collapse suppressed when multiple rows are selected.
- `aisearch_app.py:FileTable.mousePressEvent` — rubberband suppressed when drag begins on an already-selected row.
- `aisearch_app.py:FileTable` — modifier check now uses `bool(modifiers & Qt.KeyboardModifier.ControlModifier)`. PyQt6 raises on `int(KeyboardModifierCombination)`, which broke Ctrl+/Shift+click.
- `aisearch_app.py:FileTable` — explicit selection highlight color in stylesheet so selected rows are visibly highlighted regardless of the active palette.

### Robustness without a project / database
- `aisearch_app.py` — drop-to-preview path works when no project is loaded (no DB, no attrs file). Used to crash on missing project state.
- `aisearch_app.py` — features that require a DB now warn the user with a friendly dialog instead of failing silently or tracebacking.
- `aisearch_app.py` — `query_path` is updated when the query file is moved via drag-drop, so subsequent searches use the new location.

## 2026-05-01 — v2.0

### Stability
- `aisearch_debug.py:dbg` — panel mirror is now GUI-thread-safe. Worker threads calling `dbg(...)` were poking `QPlainTextEdit.appendPlainText` directly, which is undefined behavior off the GUI thread and produced segfaults during CLIP inspect. Now lines from worker threads stay in stderr + the in-memory buffer; only GUI-thread calls touch the panel.

### Memory
- `aisearch_app.py:_reset_project_memory` — clears the dup result list (`_dup_display_data`, `_dup_result_summary`) and the table BEFORE running `gc.collect` + `malloc_trim`, so the trim has the freed pages to return to the OS. Was: dup data was cleared a few lines later in `load_db`, so trim ran before the big release and never had anything to give back.
- `aisearch_app.py:_reset_project_memory` — also clears the project-keyed module caches in `aisearch_attrs` (`_faces_db_cache`, `_corrections_cache`, `_fn_rules_cache`, `_person_registry_cache`). These survive switches because they're keyed by project name, so the old project's face DB / corrections embeddings stayed resident forever.
- `aisearch_app.py:_reset_project_memory` — calls `malloc_trim(0)` on a daemon thread so RSS actually drops in the system monitor after a switch. Off-thread so a slow trim on a big heap can't freeze the GUI.

### Dup mode performance
- `aisearch_app.py:handle_preview` — group lookup no longer does `os.path.exists` on every member of every group. Was O(total_files) stat calls per click; now O(1) group lookup + 8 stat calls (only the visible window).
- `aisearch_app.py:handle_preview` — filmstrip capped to 8 thumbnails (`_STRIP_MAX = 8`), centered around the selected path so the purple rim is always visible. Was: at low thresholds a group could have 100+ members, shrinking each thumb to unreadable size and decoding 100+ videos on first display.
- `aisearch_app.py:_update_filmstrip_cells` — per-cell thumbnail cache (`cell._thumb_path` / `_thumb_mtime`). Navigating rows within the same dup group no longer redecodes the same files; only the rim color updates.
- `aisearch_app.py:_cancel_paused / _cancel_clip` — Cancel during dup scan now clears the partial result list (`_dup_display_data`, `_dup_result_summary`) and table rows. Was: partial dup list stayed visible after Cancel.

### Dup mode UI
- `aisearch_app.py:_display_dup_groups + _display_dup_from_data` — apply `_contrast_fg(color)` so text is dark on pale backgrounds and light on dark ones (was: only `setBackground` was called, leaving white text unreadable on pale rows).
- `aisearch_app.py` — Row layout in dup controls reorganized: same/smaller/larger | deeper/shallower | older/newer/reverse | hide pics/hide videos | collapse/uncollapse/delete (separate bottom row).

### Version
- `aisearch_app.py:25` + `aisearch_attrs.py:1433` — `VERSION = "2.0"` / `_AITAN_VERSION = "2.0"`.

## 2026-04-30

### Apply Rules / rename pipeline
- `aisearch_app.py:646` — **🔧 Apply Rules button (main window)** now wired to `_apply_rules_step` (toggle: start/stop bulk rename).
- `aisearch_app.py:_apply_rules_step / _apply_rules_tick / _apply_rules_finish` — direct-call bulk walk: parse rules → write attrs → `rename_file_to_match_entry(defer_save=True)`. ~800 files/sec, batched 25 per tick, status bar shows progress, single save at end.
- `aisearch_attrs.py:rename_file_to_match_entry` — added `defer_save` parameter so bulk callers don't trigger N JSON writes.
- `aisearch_preview.py:_refresh_attrs` — auto-rename hook on file navigation (gated by per-project `auto_rename` flag). Also appends old basename to entry's note (rename history).
- `aisearch_preview.py:_btn_rename row` — added `🔄 Auto-rename` checkbox next to the 🪪 Rename button (mirrors Auto-bake placement).
- `aisearch_settings_filename.py:_FilenameMixin._reapply_fn_rules` — settings tab Re-apply Rules also runs Phase 1 detect + Phase 2 rename.
- `aisearch_settings_filename.py:_ValCombo` (lines 16–153) — new widget for filename rule values: single-combo for non-coded fields, N-combos (one per sub-table) for multi-digit coded fields like HC (3 digits: Color/Style/Length).

### Table refresh on rename
- `aisearch_app.py:FileTable.set_row_path` — now updates the visible Name and Path columns, walks parent chain to find AISearchApp's `_mask_path`. Fixes "renamed file still shows old name in main page".

### Persons tab
- `aisearch_settings_person.py:_open_person_preview` — when source path missing AND no fallback file exists, prompt to delete the person from registry.
- `aisearch_settings_person.py:_cleanup_invalid_persons` + 🧹 Clean up button — bulk-removes persons with no surviving image. Confirmation dialog with preview list.
- `aisearch_settings_person.py:_PersonGroup._on_reassign_clicked` — warns when reassigning to a person ID already owned by someone outside the group.

### Related attribute (canvas)
- `attribute_manager.py:FIELD_DEFS` — added `"related": ("pathlist", [])`. Added `"related"` to `BLUE_PREFIXES`.
- `attribute_manager.py:is_blue_prefix` — `FACE_*` prefixes now resolve blue (was missing FACE_PW).
- `attr_viewer.py:FieldWidget.__init__` — new `pathlist` style branch: QListWidget + 📄 add file / 📂 add folder / × remove buttons. Double-click opens via `QDesktopServices`. File picker starts in current file's directory.
- `attr_viewer.py:FieldWidget.load_soft` — populates pathlist from `entry["related"]`. Stores current file path on widget for picker.
- `attr_viewer.py:FieldWidget.collect_soft` — returns `("pathlist", db_key, [paths])`.
- `attr_viewer.py:AttrViewerWidget.collect_soft_data` — now returns 5-tuple including `pathlist_dict`.
- `aisearch_preview.py:_save_attrs` — unpacks 5-tuple, writes pathlist values to `attrs_data[path][db_key]`.

### Dup mode (from earlier in session)
- `aisearch_app.py:_refresh_dup_delete_marks` — explicitly clear selection before applying rule marks (Qt6 ClearAndSelect with empty selection is a no-op).
- `aisearch_app.py:_delete_dups_by_rule` — uses `front_page.trash_file()` (XDG trash with `.trashinfo`) instead of `os.remove()` / `send2trash`. Also explicitly clears table selection after delete.
- `aisearch_preview.py:_on_shift_drag_done` — uses `trash_file()` instead of `os.remove()`.

---

*Format: I append to the top of this file when I make code changes. Skim this section after a session to verify what was touched.*

## 2026-04-30 (later)

- `aisearch_preview.py:eventFilter` — wheel events on scroll_area viewport / label now forwarded to `PreviewLabel.wheelEvent` and consumed. Was: when zoomed-in image showed scrollbars, QScrollArea ate the wheel event for content scrolling, so wheel-up zoom stopped working after first step ("shrink works, expand doesn't").
- `aisearch_settings_filename.py:300-313` — dedupe ModelImage/ModelVideo dupes in rule-attribute dropdown. Was: `ModelImage` (default) + `ModelImage_Table` (project) both shown, both rendering as "ModelImage". Now `_Table` variants are filtered out when the bare key exists.
- `aisearch_app.py:_sync_dup_delete_btn` — count only VISIBLE selected rows for the Delete button. Was: hidden rows (collapsed groups, Hide pictures/videos filter) inflated the count, so user saw 'Delete 320' when only ~50 visible.
- `aisearch_app.py:_delete_dups_by_rule` — same filter applied to the actual delete. Hidden rows are now protected from rule-based delete, not just hidden from view.
- `aisearch_app.py:_find_duplicates` — recall logic now looks for ANY saved `dups_<PROJECT>_*.json` (most recent mtime wins), not the one matching the current spinner. Was: scan saved at 99%, spinner left at 70% → recall failed → empty dup view. Now spinner syncs to the loaded cache's actual threshold.
- `aisearch_app.py:_load_dup_results` — honors `_dup_cache_path_override` so the caller can pick which cache file to load.
- `aisearch_app.py:_save_dup_results` + `_rebuild_dup_display_data` — guard against running outside dup mode. Was: when in search/browse mode, `handle_preview → _remove_missing_file → _rebuild_dup_display_data → _save_dup_results` ran on a non-dup table. UserRole+1 (sim) and UserRole+2 (label) are unset on non-dup rows, so all rows defaulted to sim=1.0 + same label, merging into one giant fake group that overwrote the real cache. Now both functions early-return unless `config["last_mode"] == "dup"` AND the column-0 header reads "Group". Drops rows lacking the metadata instead of inventing defaults.
- `aisearch_app.py:_replace_dup_display_path` — new helper that swaps `old_path → new_path` in `_dup_display_data` groups. Called from auto-rename navigation hook (preview) and Apply Rules tick. Was: after a rename in dup mode, `_dup_display_data` still held the old path while the table had the new one, so `if path in g_paths` (filmstrip thumb selector) failed → fallback showed only the top thumbnail. Now the in-memory dup data stays consistent with table + filesystem.
- `aisearch_app.py:3206-3214` — hash-mode dup scan progress now shows count of groups discovered so far: `Hashing… 5230/21550 · found 47 groups`. Was: only `Hashing… 5230/21550` with no live "what was actually found" feedback.
- `aisearch_app.py:3206-3220` — hash dup scan now emits a `partial` snapshot every 500 files. The existing `partial` handler in `_poll_dup_queue` populates the table, so duplicate groups appear as they're discovered instead of all at once at the end. Status bar still reports `Hashing… N/total · found M groups` every 50 files.
- `aisearch_app.py:904-915` — RAM-ceiling spinner range now scales with actual system RAM. Was: hardcoded 500-16000 MB, capped users with 32+ GB at 16 GB. Now max = 90% of total RAM (floor 16 GB), default = 50% of total RAM (capped at 8 GB) for new installs. Existing config values preserved.
- `aisearch_app.py:_display_dup_from_data` + `_delete_dups_by_rule` — group labels (G1, G2, …) now stable across delete-driven rebuilds via parallel `_dup_group_labels` list. Was: when an earlier group became a singleton (everyone in it deleted but one), it dropped from display and ALL subsequent groups slid up by one number, so a row that was G7 suddenly became G6 mid-deletion. Now surviving groups keep their original labels; you'll see gaps (G1, G2, G4, G5) instead of a confusing reshuffle.
- `aisearch_app.py:_find_duplicates / _worker / _stop_scan` — dup hash scan now remembers the stop position. Iteration starts from the saved offset, wraps around at end of list, finishes when it returns to the starting index. Position persists across app restarts via `data/dups_<PROJECT>_progress.json`. Cleared on full-pass completion. Means: a stopped scan resumes from where the user paused, not from row 0 — useful when the early files are deleted/uninteresting and live results only appear later in the iteration.
- `aisearch_preview.py:_on_inspect` — added a fire-time RSS check. Was: `_schedule_inspect` checked the RSS ceiling at SCHEDULE time, but RSS can creep up during the debounce delay. Inspections kept firing past the ceiling (you saw rss=1722 MB while a 1700 MB cap was set, with face detection still running every 9s and eventually OOM-killing the process). Now `_on_inspect` re-checks immediately before launching the worker thread; over-ceiling fires bail out and flip face/CLIP modes to "never" so subsequent navigations don't keep eating RAM.
- `aisearch_main.py:6-12` — enable Python `faulthandler` at startup so native-code crashes (segfault, abort) print a Python stack trace before the process dies. Helps diagnose `Killed` outputs that have no journalctl entry — those are typically C-extension faults, not OOM kills.
- `aisearch_app.py:_find_duplicates` — exclude `dups_<PROJECT>_progress.json` from the cache-file glob. Bug: my resume-position sidecar shared the `dups_<PROJECT>_*.json` prefix and had the most-recent mtime, so the "load most recent cache" logic loaded that file (containing `{"index": N}`, no `groups` key) and displayed an empty dup view. Now only real result caches are considered.
- `aisearch_app.py:_load_dup_results` — defensive guard against missing `groups` key in cache file. Was: `data["groups"]` raised KeyError if a malformed/sidecar JSON ended up in the load path, killing the whole `_find_duplicates` call. Now bails cleanly with an empty dup view + status message.
- `aisearch_app.py:_stop_scan` + `_poll_dup_queue["partial"]` — partial dup-scan results now saved to disk every 500 files (live partial emit) and on Stop. Was: partial groups only existed in memory; if the app crashed / was killed / closed mid-scan, all the work was lost. Now the JSON cache is updated alongside each partial display so even a `Killed` mid-scan preserves what was found.
