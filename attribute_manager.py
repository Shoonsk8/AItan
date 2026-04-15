"""
attribute_manager.py
Manages attribute workspace JSON (row-based) and exports to TAG_GROUPS format
for attrs_tags.json (used by aisearch_attrs.py).
"""
import json, os

_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_FILE  = os.path.join(_DIR, "attribute_workspace.json")
TAG_GROUPS_FILE = os.path.join(_DIR, "attrs_tags.json")   # same as TAGS_FILE in attrs

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
    "B":  ("2dig",  [("Shape 1st",     "shape_1st",      "B_Shape"),
                     ("Size 2nd",      "size_2nd",        "B_Size")]),
    "WH": ("2dig",  [("Hip 1st",       "hip_1st",        "WH_Hip"),
                     ("Waist 2nd",     "waist_2nd",       "WH_Waist")]),
    "PM": ("2dig",  [("Motion 1st",    "motion_1st",     "PM_Motion"),
                     ("Posture 2nd",   "posture_2nd",     "PM_Posture")]),
    "CS": ("3dig",  [("Lighting 1st",  "lighting_1st",   "CS_Light"),
                     ("Angle 2nd",     "angle_2nd",       "CS_Angle"),
                     ("Shot 3rd",      "shot_3rd",        "CS_Shot")]),
    "BG": ("3dig",  [("Specific 1st",  "specific_1st",   None),
                     ("Sub 2nd",       "sub_2nd",         None),
                     ("Major 3rd",     "major_3rd",       "BG_Major")]),
    "X":  ("matrix",[("Expression",   "expression",      None)]),
    "J":  ("id",    []),
    "PI": ("id",    []),
    "PW": ("id",    []),
    "A":  ("id",    []),
}

# Style → zero-padding width
_STYLE_PAD = {"1dig": 1, "2dig": 2, "3dig": 3, "matrix": 2, "id": 3}


class AttributeManager:
    def __init__(self, filename=WORKSPACE_FILE):
        self.filename = filename
        self.data: dict = {}
        if os.path.exists(filename):
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
                        row_key = f"{prefix}{c_h}{r_h}"
                        val = data.get(row_key, {}).get("expression", "").strip()
                        if val:
                            entries.append([f"{c_h}{r_h}", val])
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
