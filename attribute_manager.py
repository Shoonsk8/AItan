"""
attribute_manager.py
Manages attribute workspace JSON (row-based) and exports to TAG_GROUPS format
for attrs_tags.json (used by aisearch_attrs.py).
"""
import json, os

_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.join(_DIR, "data")
WORKSPACE_FILE  = os.path.join(_DATA_DIR, "attribute_workspace.json")
TAG_GROUPS_FILE = os.path.join(_DATA_DIR, "attrs_tags.json")   # same as TAGS_FILE in attrs

# ---------------------------------------------------------------------------
# Per-prefix field definitions
#   style: "2dig" | "3dig" | "1dig" | "matrix" | "id"
#   cols: list of (header_label, json_field_key, tag_group_key_or_None)
#         columns ordered right-to-left (1st digit first, then 2nd, then 3rd)
# ---------------------------------------------------------------------------
FIELD_DEFS = {
    "E":  ("2dig",  [("Colors 1st",    "colors_1st",    "E_Color"),
                     ("Additional 2nd","additional_2nd", "E_Additional")]),
    "HC": ("3dig",  [("Colors 1st",    "colors_1st",    "HC_Color"),
                     ("Style 2nd",     "style_2nd",      "HC_Style"),
                     ("Length 3rd",    "length_3rd",     "HC_Length")]),
    "FA": ("2dig",  [("Direction 1st", "direction_1st",  "FA_Dir"),
                     ("Vertical 2nd",  "vertical_2nd",   "FA_Vert")]),
    "SK": ("1dig",  [("Skin Type",     "type_1st",       "SK_Type")]),
    "PM": ("2dig",  [("Motion 1st",    "motion_1st",     "PM_Motion"),
                     ("Posture 2nd",   "posture_2nd",     "PM_Posture")]),
    "CS": ("3dig",  [("Lighting 1st",  "lighting_1st",   "CS_Light"),
                     ("Angle 2nd",     "angle_2nd",       "CS_Angle"),
                     ("Shot 3rd",      "shot_3rd",        "CS_Shot")]),
    "X":  ("matrix",[("Expression",   "expression",      None)]),
    "Watermark":       ("radio",   []),
    "P":               ("id",      []),
    "J":               ("id",      []),
    "PI":              ("id",      []),
    "PW":              ("id",      []),
    "A":               ("id",      []),
    "O":               ("taglist",  []),
    "R":               ("taglist",  []),
    "K":               ("taglist",  []),
    "note":            ("text",    []),
    "positive_prompt": ("text",    []),
    "negative_prompt": ("text",    []),
    "speech":          ("text",    []),
    "audio":           ("taglist", []),
    "Quality":         ("taglist", []),
}

# Style → zero-padding width
_STYLE_PAD = {"1dig": 1, "2dig": 2, "3dig": 3, "matrix": 2, "id": 3}


class AttributeManager:
    def __init__(self, filename=None):
        self.filename = filename
        self.data: dict = {}
        if filename and os.path.exists(filename):
            try:
                with open(filename, encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception:
                self.data = {}

    # ------------------------------------------------------------------
    # Raw workspace save/load
    # ------------------------------------------------------------------
    def save_data(self, data: dict):
        self.data = data
        with open(self.filename, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Export to TAG_GROUPS format → attrs_tags.json
    # ------------------------------------------------------------------
    def export_tag_groups(self, data: dict | None = None) -> dict:
        """
        Convert workspace row-data to TAG_GROUPS format and write to
        attrs_tags.json.  Returns the exported dict.
        """
        if data is None:
            data = self.data
        tag_groups: dict[str, list] = {}

        for prefix, (style, cols) in FIELD_DEFS.items():
            if style == "id" or not cols:
                continue
            pad = _STYLE_PAD.get(style, 2)

            if style == "matrix":
                # 16×16 flat expression table
                entries: list[list] = []
                for r in range(16):
                    for c in range(16):
                        r_h, c_h = hex(r)[2:], hex(c)[2:]
                        row_key = f"{prefix}{r_h}{c_h}"   # r_h = 1st digit (row/vertical)
                        val = data.get(row_key, {}).get("expression", "").strip()
                        if val:
                            entries.append([f"{r_h}{c_h}", val])
                tag_groups[f"{prefix}_Table"] = entries
                continue

            _B36 = "0123456789abcdefghijklmnopqrstuvwxyz"
            for col_label, json_field, tg_key in cols:
                if tg_key is None:
                    continue
                entries = []
                for ch in _B36:
                    row_key = f"{prefix}{'0' * (pad - 1)}{ch}"
                    val = data.get(row_key, {}).get(json_field, "").strip()
                    if val:
                        entries.append([ch, val])
                if entries:
                    tag_groups[tg_key] = entries

        # Write to attrs_tags.json (merge over existing)
        existing: dict = {}
        if os.path.exists(TAG_GROUPS_FILE):
            try:
                with open(TAG_GROUPS_FILE, encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception:
                pass
        existing.update(tag_groups)
        with open(TAG_GROUPS_FILE, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)
        return tag_groups

    # ------------------------------------------------------------------
    # Import from TAG_GROUPS → workspace row-data (first-run seeding)
    # ------------------------------------------------------------------
    def import_from_tag_groups(self, tag_groups: dict):
        """
        Populate self.data from a TAG_GROUPS dict so the editor shows
        existing values on first launch.
        """
        for prefix, (style, cols) in FIELD_DEFS.items():
            if style == "id" or not cols:
                continue
            pad = _STYLE_PAD.get(style, 2)

            if style == "matrix":
                tg_key = f"{prefix}_Table"
                for pair in tag_groups.get(tg_key, []):
                    k, v = pair[0], pair[1]
                    if len(k) >= 2:
                        row_key = f"{prefix}{k}"
                        self.data.setdefault(row_key, {})["expression"] = v
                continue

            for col_label, json_field, tg_key in cols:
                if tg_key is None:
                    continue
                for pair in tag_groups.get(tg_key, []):
                    ch, v = pair[0], pair[1]
                    if len(ch) != 1 or ch not in "0123456789abcdefghijklmnopqrstuvwxyz":
                        continue
                    row_key = f"{prefix}{'0' * (pad - 1)}{ch}"
                    self.data.setdefault(row_key, {})[json_field] = v

        # Import custom dig sections defined via __col_defs__ (not in FIELD_DEFS)
        _custom_col_defs = tag_groups.get("__col_defs__", {})
        if not isinstance(_custom_col_defs, dict):
            return
        _custom_styles = tag_groups.get("__section_styles__", {})
        _B36 = set("0123456789abcdefghijklmnopqrstuvwxyz")
        for prefix, col_defs in _custom_col_defs.items():
            if prefix in FIELD_DEFS:
                continue
            style = _custom_styles.get(prefix, "2dig")
            if style not in ("1dig", "2dig", "3dig"):
                continue
            pad = _STYLE_PAD.get(style, 2)
            for col_def in col_defs:
                if len(col_def) < 3 or not col_def[2]:
                    continue
                json_field, tg_key = col_def[1], col_def[2]
                for pair in tag_groups.get(tg_key, []):
                    ch, v = pair[0], pair[1]
                    if len(ch) != 1 or ch not in _B36:
                        continue
                    row_key = f"{prefix}{'0' * (pad - 1)}{ch}"
                    self.data.setdefault(row_key, {})[json_field] = v
