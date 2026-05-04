# あいたん AItan — Claude Code Project Guide

## What This Is
PyQt6 desktop app for AI-powered image/video search, tagging, and organization.
Version 2.1. Entry point: `aisearch_main.py`. Launched via desktop icon using:
`/mnt/1TBSSD/AIsearch/venv/bin/python /mnt/1TBSSD/AIsearch/aisearch_main.py`

## File Map

| File | Role |
|------|------|
| `aisearch_main.py` | Entry point, theme setup, creates `AISearchApp` |
| `aisearch_app.py` | Main window (`AISearchApp`): search table, inline attr panel, undo, DB load, settings |
| `aisearch_preview.py` | Preview window (`PreviewWindow`): media display, full attr panel with collapsible sections + hex combos |
| `aisearch_settings.py` | Settings dialog (`SettingsView`): 9 tabs — coordinates mixins below |
| `aisearch_settings_db.py` | `_DbMixin`: Database tab (scan dirs, rebuild, person registry) |
| `aisearch_settings_person.py` | `_PersonMixin`: Persons tab (face cards, link/unlink, pending) |
| `aisearch_settings_appearance.py` | `_AppearanceMixin`: Settings, Thresholds, Appearance tabs |
| `aisearch_settings_attrs.py` | `_AttrsMixin`: Attributes tab (workspace editor) |
| `aisearch_settings_filename.py` | `_FilenameMixin`: Filename Rules tab |
| `aisearch_settings_metadata.py` | `_MetadataMixin`: Meta Map tab (raw metadata → attribute field rules) |
| `aisearch_settings_canvas.py` | `_CanvasMixin`: Canvas tab |
| `aisearch_settings_layout.py` | `_LayoutMixin`: group/section layout management |
| `aisearch_attrs.py` | All attribute logic: TAG_GROUPS, CODED_FIELDS, parse/build filename, face detection, file metadata |
| `aisearch_config.py` | Config load/save (`aisearch_config.json`) |
| `aisearch_front_page.py` | Front page / project selector |
| `aisearch_feedback.py` | Feedback/rating helpers |
| `aisearch_logic.py` | CLIP search logic |
| `attribute_manager.py` | `AttributeManager` class: manages `attribute_workspace.json`, exports to `attrs_tags.json` |
| `aisearch_taggroups_editor.py` | Standalone editor for tag groups (separate app) |
| `attr_viewer.py` | Standalone attribute viewer |
| `embed_aitan.py` | Embedding utility script |

## Data Files

| File | Contents |
|------|---------|
| `aisearch_config.json` | App config (colors, fonts, dirs, auto_rename, etc.) |
| `attrs_<PROJECT>.json` | Per-project attribute data: path → {tags, note, person_id, coded fields...} |
| `attrs_tags.json` | TAG_GROUPS overrides + taglist/text/boolean definitions (loaded over `_DEFAULT_TAG_GROUPS`) |
| `attribute_workspace.json` | Row-based workspace for the Attributes settings tab editor |
| `filename_rules.json` | Filename ↔ attribute rules (see Rule Formats below) |
| `filename_rename_rules.json` | Pattern-replacement rename rules |
| `person_registry.json` | Person ID (3-hex) → description |
| `faces_<PROJECT>.json` | Face embeddings per project |
| `metadata_mapping_rules.json` | Default raw metadata → attr field mapping rules |
| `metadata_mapping_rules_<PROJECT>.json` | Project-specific overrides; merged with default by `load_metadata_rules` |

## Key Data Structures

### CODED_FIELDS (aisearch_attrs.py:393)
```python
CODED_FIELDS = [
    ("letter", "Label", digits),  # digits=2/3 = hex value; digits=0 = boolean flag
    ...
    ("W",  "Watermark", 0),   # boolean: W present in filename = watermarked
    ("ED", "Editable",  0),   # boolean flag
]
```
Person field "P" is special (3-digit hex, looked up in person_registry).

### TAG_GROUPS (aisearch_attrs.py:202)
Dict loaded from `_DEFAULT_TAG_GROUPS` merged with `attrs_tags.json`.
- Each CODED_FIELDS entry has one or more sub-tables named `{PREFIX}_{Name}` (e.g. E → `E_Color`, `E_Additional`; HC → `HC_Color`, `HC_Style`, `HC_Length`; etc.)
- User tag groups: `Quality`, `Audio`, `Source`, `Variant`, `Misc`
- Custom matrix fields (defined in `attrs_tags.json` `__section_styles__`): e.g. `MDL` (ModelVideo), `MDL_img` (ModelImage) — data in `{PREFIX}_Table` keys
- `__text_fields__` key: dict of `field_name → {label, placeholder}`

### Filename Rule Formats (filename_rules.json)
```json
{"pattern": "-E0a", "field": "E",       "value": "0a"}           // two-way coded field sync
{"pattern": "-E0a", "field": "E",       "value": "0a", "one_way": true}  // detect only
{"pattern": "-q1",  "tag_group": "Quality", "value": "good"}    // tag group rule (detect only)
```

### Coded Filename Format
`P001P002E0aHC012FA01SKxx...` — no separators, uppercase field keys, lowercase hex values.
Parse: `parse_coded_filename(stem)` → dict. Build: `build_coded_filename(parts)` → stem.

### FIELD_DEFS (attribute_manager.py)
Per-prefix style + column definitions for the workspace editor:
```python
FIELD_DEFS = {
    "E":  ("2dig", [("Colors 1st", "colors_1st", "E_Color"), ("Additional 2nd", "additional_2nd", "E_Additional")]),
    ...
}
```
Styles: `1dig`, `2dig`, `3dig`, `matrix`, `id`, `taglist`, `boolean`, `text`

## UI Architecture

### Preview Window (`aisearch_preview.py`)
- `_AttrSection` widget: collapsible section with arrow toggle + × delete + content
- `_code_combos`: `dict[letter.lower() → [(sub_group, pos, QComboBox)]]`
- `_code_edits`: `dict[letter.lower() → QLineEdit]` (hidden, backward-compatible with `_save_attrs`)
- Three sections: Face (E/HC/FA/X), Body (SK/B/WH/PM/T), Technical (CS/BG/O/R/K/I/W/ED)
- `_set_field_combos(letter_lower, hex_val)`: sets combo selections from a hex value
- `_refresh_attrs(path)`: loads all attrs from file into widgets

### Settings Attributes Tab (`aisearch_settings_attrs.py`)
- `_WsSec` widget: collapsible + drag-to-reorder + × delete
- `self._attr_ws_entries`: `json_key → {field: QLineEdit}` (for 1/2/3dig and matrix)
- `self._attr_tag_groups`: `grp → [(k_edit, l_edit, row_widget)]` (for taglist/boolean)
- `self._attr_text_fields`: `field_name → (label_edit, placeholder_edit)` (for text)
- `self._attr_sec_positions`: `prefix → vbox_index` (for location insert)
- Location dropdown: "Insert at Top" + "Insert after X" (no "Append at End" — selecting last = append)
- Pre-built sections: E, HC, FA, SK, B, WH, PM, CS, BG, X + Misc(boolean), Quality/Audio/Source/Variant(taglist) + prompt/neg_prompt/speech/seed/note(text)
- `_save_attr_groups()`: saves workspace + exports TAG_GROUPS to attrs_tags.json

### Settings Filename Rules Tab (`aisearch_settings_filename.py`)
- Unified rules table: Pattern | Attribute | Value | Mode | ✕
- `self._fn_rows`: list of `(pat_e, attr_cb, val_cb, mode_cb, row_w)`
- Attribute dropdown: grouped — Coded Fields (all CODED_FIELDS + P) then Tag Groups (flat user groups + matrix fields shown with friendly names from `__col_names__`)
- Mode: "→ Detect" (one_way=True) or "⇄ Sync" (two-way, coded fields only)
- Tag group attrs stored as `TAG:GroupName` in attr_cb data
- `_save_fn_rules()`: serializes all rows to `filename_rules.json`

### Settings Meta Map Tab (`aisearch_settings_metadata.py`)
- Maps raw file/AI metadata keys → attribute fields; rules stored in `metadata_mapping_rules.json`
- Source groups: ComfyUI, A1111, AIX, JPEG, Image, Video, File, CLIP Face, CLIP Body, CLIP Scene, Face Det.
- Sentinel sources (Shot, Pose, CLIP fields HC/FA/SK/E/B/WH/PM/CS/BG) always pass through in `apply_metadata_rules`
- CLIP detection is rule-driven: DB rebuild checks MetaMap rules for which fields to detect

## Common Pitfalls

- `TAG_GROUPS["Resolution"]` → **KeyError** — Resolution was removed, use `.get("Resolution", [])`
- Scroll area not updating after section expand: walk `parentWidget()` chain calling `adjustSize()` + `updateGeometry()`
- `_code_combos` digit position: pos=1 = rightmost digit, pos=2 = second from right (E: pos1=color, pos2=additional)
- Boolean flags (digits=0) should never get a value combo — check `_attr_is_boolean(key)` before enabling
- `_WsSec` and `_AttrSection` are at module level (not inside any method) — must stay there

## Person Alias System
`person_aliases.json` — list of groups, each group is a list of linked person ID strings.
E.g. `[["001","005","007"], ["002","003"]]` — embeddings from all IDs in a group are pooled during face matching.

- `link_persons(pid_a, pid_b)` — adds link, merges groups if needed
- `unlink_persons(pid_a, pid_b)` — splits two IDs out of a group
- `remove_person_from_aliases(pid)` — removes ID from all groups
- `get_alias_group(pid, aliases)` — returns set of all IDs linked to pid
- `detect_or_assign_person_id` / `match_person_id` both pool embeddings across the alias group

UI: Person Management groupbox in DB settings tab — Link [combo] ↔ [combo] + list of existing link groups with × delete.

## Pending / Next Tasks
- **Attribute Sets**: support multiple named attribute profiles (default + per-project), selectable dropdown at top of Attributes tab. Each set = separate `attribute_workspace.<name>.json`. Active set stored in config.
- Backend: apply `tag_group` rules from `filename_rules.json` in `auto_set_all` (currently only coded-field rules trigger file renames)
