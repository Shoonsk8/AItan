# AISearch — Session Changes Log

Most recent entries at the top. Each entry: file:line — what changed.

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
