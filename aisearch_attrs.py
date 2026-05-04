import json, os, sys, cv2, re, datetime, time as _time, threading as _threading

# dlib (via face_recognition) is not thread-safe — serialize all face detection calls.
_face_lock = _threading.Lock()

_DIR      = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.join(_DIR, "data")
DATA_DIR  = _DATA_DIR   # exported for use by other modules
TAGS_FILE = os.path.join(_DATA_DIR, "attrs_tags.json")

_B36 = "0123456789abcdefghijklmnopqrstuvwxyz"
def _to_b36(n, width=8):
    """Encode integer n as base-36 string, zero-padded to width."""
    result = []
    while n:
        result.append(_B36[n % 36])
        n //= 36
    return "".join(reversed(result)).zfill(width) if result else "0" * width

def _from_b36(s):
    """Decode base-36 string to integer."""
    return int(s, 36)

def julian_id_now():
    """Return 8-char base-36 Julian ID — yymmddHHMMSS encoded in base-36."""
    val = int(datetime.datetime.now().strftime("%y%m%d%H%M%S"))
    return _to_b36(val, width=8)

def julian_id_for_file(path):
    """Return 8-char base-36 Julian ID representing when the file was originally created.
    For image files: prefers EXIF DateTimeOriginal (when photo was taken on camera).
    Fallback: birthtime → ctime."""
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.jpg', '.jpeg', '.tiff', '.tif', '.heic', '.heif', '.png'):
        try:
            from PIL import Image
            img = Image.open(path)
            exif = img.getexif()
            dt_orig = exif.get(36867)  # 36867 = DateTimeOriginal
            if dt_orig:
                dt = datetime.datetime.strptime(dt_orig, "%Y:%m:%d %H:%M:%S")
                val = int(dt.strftime("%y%m%d%H%M%S"))
                return _to_b36(val, width=8)
        except Exception:
            pass
    try:
        st = os.stat(path)
        ts = getattr(st, "st_birthtime", None)
        if ts is None or ts == 0:
            ts = st.st_ctime
        dt = datetime.datetime.fromtimestamp(ts)
        val = int(dt.strftime("%y%m%d%H%M%S"))
        return _to_b36(val, width=8)
    except Exception:
        return julian_id_now()

def julian_id_to_date(jid):
    """Decode a base-36 Julian ID to datetime string (yy-mm-dd HH:MM:SS)."""
    try:
        s = f"{_from_b36(jid):012d}"
        return f"{s[0:2]}-{s[2:4]}-{s[4:6]} {s[6:8]}:{s[8:10]}:{s[10:12]}"
    except Exception:
        return jid

def date_str_to_julian_id(s):
    """Convert a date/time string from raw metadata to an 8-char base-36 Julian ID.
    Handles EXIF format '2024:03:15 10:30:45', ISO '2024-03-15 10:30:45',
    date-only '2024-03-15', and Unix timestamp strings."""
    s = str(s).strip()
    # Ordered from most-specific to least — strptime requires exact full match per fmt
    _fmts = [
        "%Y:%m:%d %H:%M:%S",   # EXIF
        "%Y-%m-%d %H:%M:%S",   # ISO
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",   # ISO 8601
        "%Y-%m-%d",
        "%Y:%m:%d",
        "%Y/%m/%d",
    ]
    # Also try on just the first 19 / 10 chars in case there's a trailing timezone suffix
    candidates = [s, s[:19], s[:10]]
    for candidate in dict.fromkeys(candidates):  # deduplicate while preserving order
        for fmt in _fmts:
            try:
                dt = datetime.datetime.strptime(candidate, fmt)
                val = int(dt.strftime("%y%m%d%H%M%S"))
                return _to_b36(val, width=8)
            except Exception:
                continue
    # Try Unix timestamp
    try:
        dt = datetime.datetime.fromtimestamp(float(s))
        val = int(dt.strftime("%y%m%d%H%M%S"))
        return _to_b36(val, width=8)
    except Exception:
        pass
    return ""

def detect_file_attrs(path):
    """Auto-detect O (orientation), R (resolution), K (framerate) from a file.
    Returns dict with lowercase keys e.g. {'o': '09', 'r': 'a8', 'k': '30'}."""
    result = {}
    try:
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            return result
        width  = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps    = cap.get(cv2.CAP_PROP_FPS)
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()

        if width <= 0 or height <= 0:
            return result

        # R — resolution based on longer side
        long_side = max(width, height)
        if   long_side >= 7680: result["r"] = "08"   # 8K
        elif long_side >= 3840: result["r"] = "04"   # 4K
        elif long_side >= 2560: result["r"] = "a4"   # 1440p
        elif long_side >= 1920: result["r"] = "a8"   # 1080p
        elif long_side >= 1280: result["r"] = "72"   # 720p
        elif long_side >= 854:  result["r"] = "48"   # 480p
        else:                   result["r"] = "36"   # 360p

        # O — orientation / aspect ratio
        ratio = width / height
        if   abs(ratio - 1.0) < 0.05:  result["o"] = "11"  # 1:1  square
        elif ratio >= 4.0:              result["o"] = "f1"  # 15:1 extreme ultra-wide
        elif ratio >= 2.2:              result["o"] = "73"  # 21:9 cinema wide
        elif ratio >= 1.7:              result["o"] = "09"  # 16:9 landscape
        elif ratio >= 1.4:              result["o"] = "32"  # 3:2  landscape (photo)
        elif ratio > 1.05:              result["o"] = "43"  # 4:3  landscape
        elif ratio > 0.72:              result["o"] = "34"  # 3:4  portrait
        elif ratio > 0.58:              result["o"] = "23"  # 2:3  portrait (photo)
        else:                           result["o"] = "90"  # 9:16 portrait

        # K — frame rate (video only; images have frame_count == 1)
        if frames > 1 and fps > 0:
            if   fps >= 100: result["k"] = "b0"  # 120fps
            elif fps >= 55:  result["k"] = "60"  # 60fps
            elif fps >= 27:  result["k"] = "30"  # 30fps
            else:            result["k"] = "24"  # 24fps

    except Exception:
        pass
    return result

def tags_file_for_project(project=None):
    """Return the attrs_tags JSON path for a project (falls back to global)."""
    if project and project != "default":
        p = os.path.join(_DATA_DIR, f"attrs_tags_{project}.json")
        if os.path.exists(p):
            return p
    return TAGS_FILE

def tags_save_path_for_project(project=None):
    """Return the path to WRITE attrs_tags for a project (always project-specific)."""
    if project and project != "default":
        return os.path.join(_DATA_DIR, f"attrs_tags_{project}.json")
    return TAGS_FILE

def workspace_file_for_project(project=None):
    """Return the attribute_workspace JSON path for a project (falls back to global)."""
    if project and project != "default":
        p = os.path.join(_DATA_DIR, f"attribute_workspace_{project}.json")
        if os.path.exists(p):
            return p
    return os.path.join(_DATA_DIR, "attribute_workspace.json")

def workspace_save_path_for_project(project=None):
    """Return the path to WRITE workspace for a project (always project-specific)."""
    if project and project != "default":
        return os.path.join(_DATA_DIR, f"attribute_workspace_{project}.json")
    return os.path.join(_DATA_DIR, "attribute_workspace.json")
FILENAME_RULES_FILE  = os.path.join(_DATA_DIR, "filename_rules.json")

def filename_rules_file_for_project(project=None):
    """Return the filename_rules JSON path for a project (or global default)."""
    if project and project != "default":
        p = os.path.join(_DATA_DIR, f"filename_rules_{project}.json")
        if os.path.exists(p):
            return p
    return FILENAME_RULES_FILE

def filename_rules_save_path_for_project(project=None):
    """Return the path to WRITE filename_rules for a project (always project-specific)."""
    if project and project != "default":
        return os.path.join(_DATA_DIR, f"filename_rules_{project}.json")
    return FILENAME_RULES_FILE
RENAME_RULES_FILE    = os.path.join(_DATA_DIR, "filename_rename_rules.json")
PERSON_REGISTRY_FILE = os.path.join(_DATA_DIR, "person_registry.json")
META_MAP_RULES_FILE  = os.path.join(_DATA_DIR, "metadata_mapping_rules.json")

def metadata_rules_file_for_project(project=None):
    """Return the metadata_mapping_rules JSON path for a project (or global default)."""
    if project and project != "default":
        p = os.path.join(_DATA_DIR, f"metadata_mapping_rules_{project}.json")
        if os.path.exists(p):
            return p
    return META_MAP_RULES_FILE

def metadata_rules_save_path_for_project(project=None):
    """Return the path to WRITE metadata_mapping_rules for a project."""
    if project and project != "default":
        return os.path.join(_DATA_DIR, f"metadata_mapping_rules_{project}.json")
    return META_MAP_RULES_FILE

def load_metadata_rules(project=None):
    """Load metadata mapping rules for a project.
    If a project file exists, return it as-is (complete override).
    Only fall back to defaults when no project file has been saved yet."""
    def _read(path):
        try:
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return None

    if not project or project == "default":
        return _read(META_MAP_RULES_FILE) or []

    proj_path = metadata_rules_save_path_for_project(project)
    proj_rules = _read(proj_path)
    if proj_rules is not None:
        return proj_rules  # project file exists — use it exactly as saved

    # No project file yet — seed from defaults
    return _read(META_MAP_RULES_FILE) or []

def save_metadata_rules(rules, project=None):
    """Save metadata mapping rules list to disk."""
    path = metadata_rules_save_path_for_project(project)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rules, f, indent=2)

def apply_metadata_rules(raw_meta, rules):
    """Apply mapping rules to a raw metadata dict.
    Returns {target_id: value_str} for all rules whose source key exists.
    target_id examples: 'prompt', 'neg_prompt', 'seed', 'note', 'speech',
                        'model', 'code:O', 'code:R', 'code:K', etc.
    If a rule has a 'value' key (preset hex code), that fixed value is used
    instead of copying the raw metadata string.
    For code:J targets, date strings are automatically converted to base-36 Julian IDs."""
    result = {}
    # Sentinel sources — no meta key needed, just signal which detection to run
    _SENTINEL_SOURCES = {"Shot", "Pose",               # MediaPipe
                         "HC", "FA", "SK", "E",         # CLIP face (uppercase)
                         "B", "WH", "PM",               # CLIP body (uppercase)
                         "CS", "BG",                    # CLIP scene (uppercase)
                         "hc", "fa", "sk", "e",         # CLIP face (lowercase)
                         "b", "wh", "pm",               # CLIP body (lowercase)
                         "cs", "bg",                    # CLIP scene (lowercase)
                         "Audio", "Resolution",           # file detection
                         "Ratio", "FPS"}                 # file coded-field detection
    for rule in rules:
        src = rule.get("source", "")
        tgt = rule.get("target", "")
        if not src or not tgt:
            continue
        # Sentinel sources (MediaPipe detection) — always pass through
        if src in _SENTINEL_SOURCES:
            result[tgt] = src
            continue
        if src not in raw_meta:
            continue
        # Use the saved preset value if present, otherwise copy the raw value
        val = rule.get("value", "") or str(raw_meta[src]).strip()
        if val:
            # Auto-convert date strings to Julian ID for the J coded field
            if tgt == "code:J" and not rule.get("value"):
                val = date_str_to_julian_id(val) or val
            result[tgt] = val
    return result

def person_registry_file_for_project(project=None):
    """Return the person_registry JSON path for a project (or global default)."""
    if project and project != "default":
        return os.path.join(_DATA_DIR, f"person_registry_{project}.json")
    return PERSON_REGISTRY_FILE
PERSON_ALIASES_FILE      = os.path.join(_DATA_DIR, "person_aliases.json")
PERSON_RIGHT_GROUPS_FILE = os.path.join(_DATA_DIR, "person_right_groups.json")

_DEFAULT_TAG_GROUPS = {
    # ── Animal  A[hi][lo]  — 2-digit matrix (16x16 = 256 codes) ─────────────
    # User-editable; expand via Settings → Attributes editor.
    "A_Table": [
        ["00", "(no animal)"],
        ["01", "Capybara"],
    ],
    # ── Eyes  E[additional][color]  ──────────────────────────────────────────
    # 1st digit (right) = color
    "E_Color": [
        ["0", "Closed / No eyes"],   ["1", "Brown"],    ["2", "Blue"],
        ["3", "Hazel"],              ["4", "Amber"],    ["5", "Gray"],
        ["6", "Green"],              ["7", "Violet"],   ["8", "Red"],
        ["9", "Silver"],             ["a", "Black"],
        ["b", "Heterochromia (Complete)"],
        ["c", "Heterochromia (Sectoral)"],
        ["d", "Heterochromia (Central)"],
        ["e", "Aniridia"],
    ],
    # ── Hair  HC[length][style][color]  ──────────────────────────────────────
    # 1st digit (right) = color
    "HC_Color": [
        ["0", "No hair"],            ["1", "Black"],          ["2", "Dark Brown"],
        ["3", "Light Brown"],        ["4", "Blonde"],         ["5", "Platinum Blonde"],
        ["6", "Red"],                ["7", "Pink"],           ["8", "Ginger"],
        ["9", "Gray"],               ["a", "White"],          ["b", "Blue"],
        ["c", "Yellow"],             ["d", "Green"],          ["e", "Rainbow"],
        ["f", "Neon"],
    ],
    # 2nd digit (middle) = style
    "HC_Style": [
        ["0", "(none)"],    ["1", "Straight"],  ["2", "Wavy"],
        ["3", "Curly"],     ["4", "Voluminous"],["5", "Bob"],
        ["6", "Ponytail"],  ["7", "Braid"],     ["8", "Tied"],
        ["9", "Buzz"],
    ],
    # 3rd digit (left) = length
    "HC_Length": [
        ["0", "(none)"],      ["1", "Very Short"],  ["2", "Short"],
        ["3", "Medium"],      ["4", "Long"],        ["5", "Very Long"],
        ["6", "Bald"],        ["7", "Partially Bald"],
    ],

    # ── Face Angle  FA[vertical][direction]  ─────────────────────────────────
    # 1st digit = direction
    "FA_Dir": [
        ["0", "Front"],        ["1", "Right"],       ["2", "Right 3/4"],
        ["3", "Left"],         ["4", "Left 3/4"],    ["5", "Back"],
    ],
    # 2nd digit = vertical tilt
    "FA_Vert": [
        ["0", "Horizontal"],   ["1", "Upward"],      ["2", "Downward"],
    ],

    # ── Skin  SK[reserved][type]  ────────────────────────────────────────────
    "SK_Type": [
        ["0", "Type I — Very Fair"],       ["1", "Type II — Fair"],
        ["2", "Type III — Medium"],        ["3", "Type IV — Olive"],
        ["4", "Type V — Dark Brown"],      ["5", "Type VI — Deeply Pigmented"],
    ],

    # ── Bust  B[size][shape]  ────────────────────────────────────────────────
    # 2nd digit = size
    "B_Size": [
        ["0", "(none)"],             ["1", "Flat / Male / Neutral"],
        ["2", "Athletic / Pectorals"],["3", "Petite / AAA-A"],
        ["4", "Small / B-C"],        ["5", "Medium / D-E"],
        ["6", "Large / F-G"],        ["7", "Extra Large / H+"],
        ["8", "Enhanced"],
    ],
    # 1st digit = shape  (TBD — reserve f0 range)
    "B_Shape": [
        ["0", "(undefined)"],   ["1", "Square (Pecs)"],  ["2", "Round"],
        ["3", "Teardrop"],      ["4", "Broad"],          ["5", "Side Set"],
        ["6", "Slender"],
    ],

    # ── WaistHip  WH[waist][hip]  ────────────────────────────────────────────
    # 1st digit = hip
    "WH_Hip": [
        ["0", "(none)"],          ["1", "Thin"],           ["2", "Athletic / Firm"],
        ["3", "Average"],         ["4", "Curvy / Full"],   ["5", "Large / Wide"],
        ["6", "Extra Large"],     ["7", "Sticks out"],
    ],
    # 2nd digit = waist
    "WH_Waist": [
        ["0", "(none)"],          ["1", "Flat / Thin"],    ["2", "Athletic / Firm"],
        ["3", "Average"],         ["4", "Curvy / Full"],   ["5", "Large / Wide"],
        ["6", "Extra Large"],     ["7", "Pregnant"],
    ],

    # ── Posture+Motion  PM[posture][motion]  ─────────────────────────────────
    # 2nd digit = posture. Code 0 is the "Standing" default (default_is_zero).
    "PM_Posture": [
        ["0", "Standing"],                ["1", "Standing in style"],
        ["2", "Sitting"],                 ["3", "Kneeling"],
        ["4", "Lying"],                   ["5", "Leaning"],
        ["6", "Crouching"],               ["7", "Handstand"],
    ],
    # 1st digit = motion. Code 0 is the "Still" default (default_is_zero).
    "PM_Motion": [
        ["0", "Still"],       ["2", "Walking"],     ["3", "Running"],
        ["4", "Dancing"],     ["5", "Looking at Camera"],
        ["6", "Talking"],     ["7", "Gesturing"],   ["8", "Fighting"],
    ],

    # ── Camera/Shot  CS[shot][angle][lighting]  ──────────────────────────────
    # 3rd digit = shot area
    "CS_Shot": [
        ["0", "(none)"],             ["1", "Extreme Close-Up"],   ["2", "Face Close-Up"],
        ["3", "Big Close-Up"],       ["4", "Close-Up"],           ["5", "Bust Shot"],
        ["6", "Medium Close-Up"],    ["7", "Medium Shot"],        ["8", "Cowboy Shot"],
        ["9", "Full Shot"],          ["a", "Wide Shot"],          ["b", "Extreme Wide"],
    ],
    # 2nd digit = angle
    "CS_Angle": [
        ["0", "Eye Level"],    ["1", "Low Angle"],     ["2", "High Angle"],
        ["3", "Over-Shoulder"],["4", "Dutch Angle"],   ["5", "Bird's Eye"],
    ],
    # 1st digit = lighting. Code 0 is the "Natural" default (default_is_zero).
    "CS_Light": [
        ["0", "Natural"],      ["1", "Sunshine"],      ["2", "Sunset"],
        ["3", "Studio"],       ["4", "Cinematic"],     ["5", "Anime"],
        ["6", "Night"],
    ],

    # ── Universal built-in taglists (blue) — standard, same for everyone ─────
    "O": [
        ["f1", "15:1"], ["73", "21:9"], ["09", "16:9"],
        ["32", "3:2"],  ["43", "4:3"],  ["11", "1:1"],
        ["34", "3:4"],  ["23", "2:3"],  ["90", "9:16"],
    ],
    "O_Preset": [
        ["f1", "15:1"], ["73", "21:9"], ["09", "16:9"],
        ["32", "3:2"],  ["43", "4:3"],  ["11", "1:1"],
        ["34", "3:4"],  ["23", "2:3"],  ["90", "9:16"],
    ],
    "R": [
        ["36", "360p"], ["48", "480p"],  ["72", "720p"],
        ["a8", "1080p"],["a4", "1440p"], ["04", "4K"],
        ["08", "8K"],
    ],
    "R_Preset": [
        ["36", "360p"], ["48", "480p"],  ["72", "720p"],
        ["a8", "1080p"],["a4", "1440p"], ["04", "4K"],
        ["08", "8K"],
    ],
    "K": [
        ["24", "24 fps"], ["30", "30 fps"], ["60", "60 fps"], ["b0", "120 fps"],
    ],
    "K_Preset": [
        ["24", "24 fps"], ["30", "30 fps"], ["60", "60 fps"], ["b0", "120 fps"],
    ],
    "audio": [
        ["none", "None"], ["aac", "AAC"], ["mp3", "MP3"],
        ["opus", "Opus"], ["vorbis", "Vorbis"], ["flac", "FLAC"],
        ["ac3", "AC3"], ["eac3", "E-AC3"], ["sound", "Sound"],
    ],
    "audio_Preset": [
        ["none", "None"], ["aac", "AAC"], ["mp3", "MP3"],
        ["opus", "Opus"], ["vorbis", "Vorbis"], ["flac", "FLAC"],
        ["ac3", "AC3"], ["eac3", "E-AC3"], ["sound", "Sound"],
    ],

    # Kept user-editable (yellow, no hardcode): Background, Variant, Watermark.
    # User tables live entirely in per-project attrs_tags_<project>.json.
}

# Display names for hardcoded sections (shown in Attributes tab title when no custom name saved)
_DEFAULT_FIELD_NAMES = {
    "audio":   "Audio Format",
    "Variant": "Variant",
    "Source":  "Source",
    "Misc":    "Misc",
}

def _load_tag_groups(tags_file=None):
    path = tags_file or TAGS_FILE
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            # If settings have been saved (file has __section_order__), the file is
            # fully authoritative — don't seed from _DEFAULT_TAG_GROUPS at all.
            # This prevents deleted groups (e.g. Quality) from resurrecting via defaults.
            if "__section_order__" in raw:
                merged = {}
            else:
                merged = {grp: [tuple(pair) for pair in pairs]
                          for grp, pairs in _DEFAULT_TAG_GROUPS.items()}
            for grp, pairs in raw.items():
                if grp.startswith("__"):
                    merged[grp] = pairs
                elif isinstance(pairs, list) and pairs:
                    merged[grp] = [tuple(pair) for pair in pairs]
                elif not isinstance(pairs, list):
                    merged[grp] = pairs
                # empty list → omit (explicit deletion or cleared group)
            return merged
        except Exception:
            pass
    # No file — use all defaults
    return {grp: [tuple(pair) for pair in pairs]
            for grp, pairs in _DEFAULT_TAG_GROUPS.items()}

TAG_GROUPS = _load_tag_groups()
TAGS = [item for key, group in TAG_GROUPS.items()
        if not key.startswith("__") and isinstance(group, list)
        for item in group]

def _tag_keys(group_name):
    """Return set of tag keys from a group, tolerating tuples of any length."""
    return {item[0] for item in TAG_GROUPS.get(group_name, [])
            if isinstance(item, (list, tuple)) and len(item) >= 1}

QUALITY_TAGS      = _tag_keys("Quality")
SOURCE_TAGS       = _tag_keys("Source") or {"comfyui", "a1111", "aix", "other_src"}
AUDIO_TAGS        = (_tag_keys("audio") or _tag_keys("Audio")
                     or {"none", "aac", "mp3", "opus", "vorbis", "flac", "ac3", "eac3", "sound"})
# Per-digit sub-table sets
E_COLOR_TAGS      = _tag_keys("E_Color")
E_ADDITIONAL_TAGS  = _tag_keys("E_Additional")
HC_COLOR_TAGS     = _tag_keys("HC_Color")
HC_STYLE_TAGS     = _tag_keys("HC_Style")
HC_LENGTH_TAGS    = _tag_keys("HC_Length")
FA_DIR_TAGS       = _tag_keys("FA_Dir")
FA_VERT_TAGS      = _tag_keys("FA_Vert")
SK_TYPE_TAGS      = _tag_keys("SK_Type")
B_SIZE_TAGS       = _tag_keys("B_Size")
B_SHAPE_TAGS      = _tag_keys("B_Shape")
WH_HIP_TAGS       = _tag_keys("WH_Hip")
WH_WAIST_TAGS     = _tag_keys("WH_Waist")
PM_POSTURE_TAGS   = _tag_keys("PM_Posture")
PM_MOTION_TAGS    = _tag_keys("PM_Motion")
CS_SHOT_TAGS      = _tag_keys("CS_Shot")
CS_ANGLE_TAGS     = _tag_keys("CS_Angle")
CS_LIGHT_TAGS     = _tag_keys("CS_Light")
# Legacy empty stubs — functionality moved to coded fields (CS, FA, PM, R)
RESOLUTION_TAGS   = set()
SHOT_TAGS         = set()
POSE_TAGS         = set()
CAMERA_ANGLE_TAGS = set()
POSTURE_TAGS      = set()
ACTION_TAGS       = set()
EYE_COLOR_TAGS    = set()
SKIN_TYPE_TAGS    = set()
HAIR_COLOR_TAGS   = set()


# ── Coded filename convention ────────────────────────────────────────────────
# Format: {SubjectID}-E{e}-H{hh}-X{xx}-M{mm}-T{tt}-P{pp}-B{bbb}-O{o}-S{s}-{fingerprint}
# SubjectID: 3 hex digits (single), M+2 hex (multiple mains), A+2 hex (animals)

EXPRESSION_CATEGORIES = {
    0x0: "Neutral 無表情",
    0x1: "Happy 幸せ",
    0x2: "Disgust / Contempt 嫌悪・軽蔑",
    0x3: "Sad 悲しみ",
    0x4: "Angry 怒り",
    0x5: "Surprised 驚き",
    0x6: "Fear 恐れ",
    0x7: "Tired / Soft 疲れ・穏やか",
    0x8: "Seductive 誘惑",
    0x9: "Shy / Embarrassed 恥じらい",
    0xa: "Mischievous いたずら",
    0xb: "Pain 痛み",
    0xc: "Intense / Dramatic 強烈・劇的",
}

# Full X field reference table: code → (english_name, japanese_name, description)
# First hex digit = category (1–b), second = index within category (0–f)
EXPRESSION_TABLE = {
    # ── 0x Neutral ← AI detects "00" ────────────────────────────────────────
    "00": ("Neutral",        "無表情",           "Completely relaxed face with no visible muscle tension; AI-detectable baseline."),
    "01": ("Deadpan",        "真顔",             "Neutral face used deliberately while delivering humor or sarcasm."),
    "02": ("Resting",        "休息顔",           "Default resting expression; may read as serious or unfriendly."),
    "03": ("Vacant",         "空虚顔",           "Empty unfocused gaze; total relaxation or 'thousand-yard stare'."),
    "04": ("Composed",       "落ち着いた顔",     "Calm, controlled face; actively projecting stillness and poise."),
    "05": ("Masked",         "感情を隠す顔",     "Deliberately concealing all feeling behind a smooth blank surface."),
    "06": ("Serene",         "穏やか顔",         "Peaceful and still; no tension anywhere, soft unfocused eyes."),
    "07": ("Impassive",      "無感動顔",         "Completely unreadable; neither tension nor relaxation gives anything away."),
    # ── 1x Happy ← AI detects "10" ───────────────────────────────────────────
    "10": ("Smile",          "笑顔",             "Slight upturn of the corners of the mouth; AI-detectable happy baseline."),
    "11": ("Grin",           "にっこり",         "Broad smile showing teeth; satisfied or mischievous."),
    "12": ("Laugh",          "大笑い",           "Open-mouth laughter; head may tilt back, eyes squeezed or bright."),
    "13": ("Giggle",         "くすくす笑い",     "Soft suppressed laughter; cheeks puff out, shoulders bounce lightly."),
    "14": ("Smirky Snicker", "忍び笑い",         "Tight-lipped suppressed laugh with flared nostrils; monkey snicker."),
    "15": ("Beaming",        "満面の笑み",       "Radiant wide-eyed smile of immense joy or sudden success."),
    "16": ("Playful Glee",   "はしゃぎ顔",       "Bright-eyed energetic expression of pure playful happiness."),
    "17": ("Wink",           "ウィンク",         "One eye closed; signals a secret, joke, or flirtation."),
    "18": ("Exultant",       "歓喜顔",           "Triumphant joy; eyes wide and bright, mouth open in a cheer."),
    "19": ("Tender Smile",   "優しい笑顔",       "Soft warm affectionate smile; fondness without excitement."),
    # ── 2x Disgust / Contempt ← AI detects "20" ─────────────────────────────
    "20": ("Sneer",          "冷笑",             "One side of the upper lip raised; contempt or dislike; AI-detectable baseline."),
    "21": ("Smug",           "自己満足顔",       "Self-satisfied closed smile; 'I know something you don't'."),
    "22": ("Disgusted",      "嫌悪顔",           "Wrinkled nose, raised upper lip, squinted eyes; strong aversion."),
    "23": ("Disdain",        "蔑み顔",           "Cold flat expression with slight downward curl; looking down on someone."),
    "24": ("Condescending",  "見下し顔",         "Raised chin, lidded eyes; patronising superiority."),
    "25": ("Eye Roll",       "目を回す",         "Eyes rotating upward and away; dismissive impatience or disbelief."),
    "26": ("Bitter",         "苦々しい顔",       "Tight lips, narrowed eyes; resentful contempt from past hurt."),
    "27": ("Withering",      "冷たい一瞥",       "A single cutting glance that diminishes without a word."),
    "28": ("Repulsed",       "嫌悪感顔",         "Full-body revulsion visible in the face; skin crawling disgust."),
    # ── 3x Sad ← AI detects "30" ─────────────────────────────────────────────
    "30": ("Cry",            "泣き顔",           "Visible tears, trembling lower lip; AI-detectable sad baseline."),
    "31": ("Pout",           "口を尖らせる",     "Lower lip pushed forward; childlike sulking or mild annoyance."),
    "32": ("Whimper",        "今にも泣きそうな顔","Lip trembling, eyes glistening; on the verge of tears."),
    "33": ("Wistful",        "哀愁顔",           "Soft distant gaze with a faint sad smile; bittersweet longing."),
    "34": ("Melancholy",     "憂鬱顔",           "Downcast eyes, slack mouth; heavy lingering sadness."),
    "35": ("Dejected",       "落胆顔",           "Bowed head, compressed lips; broken or deeply disappointed."),
    "36": ("Grief",          "悲嘆顔",           "Profound loss; face crumpled, eyes shut tight against the pain."),
    "37": ("Despairing",     "絶望顔",           "Hollow eyes, slack jaw; all hope extinguished."),
    "38": ("Forlorn",        "孤独な悲しみ顔",   "Lonely sad gaze; abandoned and adrift."),
    # ── 4x Angry ← AI detects "40" ───────────────────────────────────────────
    "40": ("Frown",          "しかめっ面",       "Brows pulled down and together, mouth down-curved; AI-detectable angry baseline."),
    "41": ("Stern",          "厳格な顔",         "Set jaw, flat mouth, controlled displeasure; authoritative."),
    "42": ("Scowl",          "睨み顔",           "Heavy frown with narrowed eyes; deep displeasure or warning."),
    "43": ("Glare",          "にらみつけ",       "Fixed piercing stare, brows low; sharp anger or challenge."),
    "44": ("Furious",        "激怒顔",           "Flared nostrils, bared teeth, reddened face; full rage."),
    "45": ("Brooding",       "陰鬱顔",           "Dark introspective look; suppressed anger or deep resentment."),
    "46": ("Seething",       "怒りを抑える顔",   "Controlled surface hiding boiling rage just beneath; jaw clenched tight."),
    "47": ("Outraged",       "憤慨顔",           "Indignant open-faced anger; a moral line has been crossed."),
    "48": ("Cold Fury",      "冷たい怒り顔",     "Ice-cold, utterly still anger; far more dangerous than hot rage."),
    # ── 5x Surprised ← AI detects "50" ──────────────────────────────────────
    "50": ("Surprised",      "驚き顔",           "Raised brows, slightly open mouth; AI-detectable surprise baseline."),
    "51": ("Shocked",        "衝撃顔",           "Wide eyes, dropped jaw; strong unexpected impact."),
    "52": ("Amazed",         "感嘆顔",           "Wide-eyed wonder with relaxed brow; awe without fear."),
    "53": ("Startled",       "びっくり顔",       "Flinch reaction; head pulled back, eyes snapped wide."),
    "54": ("Bewildered",     "困惑顔",           "Surprised and confused together; head tilt, brow knit."),
    "55": ("Astonished",     "仰天顔",           "Extreme positive surprise; mouth agape, eyes at maximum width."),
    "56": ("Dumbfounded",    "唖然顔",           "Speechless; mouth opens but nothing comes out."),
    "57": ("Aghast",         "ぞっとした顔",     "Horrified surprise; recoil and pallor with open mouth."),
    # ── 6x Fear ← AI detects "60" ────────────────────────────────────────────
    "60": ("Scared",         "恐怖顔",           "Eyes wide, brows raised, mouth pulled back; AI-detectable fear baseline."),
    "61": ("Nervous",        "緊張顔",           "Tight smile, stiff posture, darting glances; unease without panic."),
    "62": ("Anxious",        "不安顔",           "Furrowed brow, tense jaw, worried eyes; anticipatory dread."),
    "63": ("Horrified",      "戦慄顔",           "Blanched skin, pupils dilated, mouth open in silent scream."),
    "64": ("On-Guard",       "警戒顔",           "Narrowed eyes, tense brow; watchful wariness."),
    "65": ("Panicked",       "パニック顔",       "Frantic wide eyes, rapid shallow breathing; fight-or-flight triggered."),
    "66": ("Frozen",         "すくみ顔",         "Completely still; paralysed by fear, unable to move or speak."),
    "67": ("Cowering",       "萎縮顔",           "Face pulled down and away; shrinking from a looming threat."),
    # ── 7x Tired / Soft ← AI detects "70" ────────────────────────────────────
    "70": ("Sleepy",         "眠そうな顔",       "Eyelids drooping heavily; AI-detectable tiredness baseline."),
    "71": ("Drowsy",         "うとうと顔",       "Eyes half-closed, slow blinking; drifting in and out of awareness."),
    "72": ("Relaxed",        "リラックス顔",     "All muscles soft, unhurried; completely at ease."),
    "73": ("Dazed",          "ぼんやり顔",       "Unfocused gaze, slack jaw; mentally elsewhere."),
    "74": ("Content",        "満足顔",           "Quiet settled happiness; a gentle closed-mouth smile."),
    "75": ("Peaceful",       "平和顔",           "Eyes gently closed or soft, tiny faint smile; deep inner calm."),
    "76": ("Languid",        "倦怠顔",           "Slow heavy pleasurably tired; every movement takes effort."),
    "77": ("Bored",          "退屈顔",           "Flat affect, glazed eyes; nothing holds interest."),
    "78": ("Zoned Out",      "ぼうっとした顔",   "Mind clearly elsewhere; body present, person absent."),
    # ── 8x Seductive ← AI detects "80" ───────────────────────────────────────
    "80": ("Flirty",         "色目遣い",         "Coy playful look with a slight smile; AI-detectable seductive baseline."),
    "81": ("Sultry",         "妖艶顔",           "Heavy-lidded eyes, parted lips; smoldering attraction."),
    "82": ("Bedroom Eyes",   "蠱惑的な目",       "Half-closed lids, soft gaze; intimate invitation."),
    "83": ("Biting Lip",     "唇を噛む",         "Lower lip caught between teeth; desire or nervous anticipation."),
    "84": ("Inviting",       "誘い顔",           "Open welcoming expression; soft smile with sustained eye contact."),
    "85": ("Smoldering",     "くすぶる欲望顔",   "Intense restrained desire; heat behind still composed features."),
    "86": ("Yearning",       "切望顔",           "Longing with visible desire; eyes soft, slightly parted lips."),
    "87": ("Possessive",     "独占欲顔",         "Claiming intensity; gaze that says 'you are mine'."),
    "88": ("Alluring",       "魅惑顔",           "Effortless magnetism; draws the eye without apparent effort."),
    # ── 9x Shy / Embarrassed ← AI detects "90" ───────────────────────────────
    "90": ("Shy",            "照れ顔",           "Averted gaze, compressed lips; reserved and self-conscious; AI baseline."),
    "91": ("Embarrassed",    "恥ずかしそうな顔", "Wide eyes, flustered half-smile; caught off-guard."),
    "92": ("Blushing",       "赤面",             "Visible reddening of cheeks and ears; warmth from attention."),
    "93": ("Bashful",        "はにかみ顔",       "Head tilted down, eyes glancing up; charming shyness."),
    "94": ("Timid",          "おどおど顔",       "Small gestures, slightly hunched; fearful of making a mistake."),
    "95": ("Flustered",      "あたふた顔",       "Overwhelmed and scattered; too much happening at once."),
    "96": ("Self-Conscious", "自意識過剰顔",     "Acutely aware of being watched; stiff and hyper-aware."),
    "97": ("Meek",           "おとなしい顔",     "Quiet and submissive; accepting without protest."),
    # ── ax Mischievous ← AI detects "a0" ─────────────────────────────────────
    "a0": ("Mischievous",    "いたずらっ子顔",   "Impish smirk with bright scheming eyes; AI-detectable mischief baseline."),
    "a1": ("Teasing",        "からかい顔",       "Playful taunting look; tongue slightly visible or exaggerated pout."),
    "a2": ("Cheeky",         "ずうずうしい顔",   "Impudent grin; knows they're pushing limits and enjoying it."),
    "a3": ("Devious Grin",   "悪巧み顔",         "Wide knowing grin; plotting something just naughty enough."),
    "a4": ("Tongue Out",     "舌を出す",         "Tip of tongue poked out; teasing, playful, or cute."),
    "a5": ("Conspiratorial", "共謀顔",           "Secret-plotting look; eyebrows raised, voice dropped, eyes darting."),
    "a6": ("Sly",            "ずる賢い顔",       "Craftily clever; half-smile that hides more than it shows."),
    "a7": ("Impudent",       "生意気顔",         "Boldly rude and disrespectful with complete confidence."),
    # ── bx Pain ← AI detects "b0" ────────────────────────────────────────────
    "b0": ("Grimace",        "顔をしかめる",     "Involuntary facial shrinking from sharp pain; AI-detectable pain baseline."),
    "b1": ("Cringe",         "ひるみ顔",         "Full-body recoil reflected in the face; bracing for impact."),
    "b2": ("Anguish",        "苦悩顔",           "Eyes squeezed shut, brow knotted, mouth contorted; deep suffering."),
    "b3": ("Exhausted Pain", "消耗した痛み顔",   "All energy depleted; slack and hollow-eyed after prolonged ordeal."),
    "b4": ("Wince",          "ひるみ顔",         "Sharp brief reactive pain; eyes snap shut, teeth clench."),
    "b5": ("Agony",          "激痛顔",           "Extreme pain at its peak; every muscle contracted against it."),
    "b6": ("Suffering",      "苦しみ顔",         "Ongoing sustained pain; endurance written across the whole face."),
    "b7": ("Tearful Pain",   "痛み泣き顔",       "Crying directly from pain; wet eyes, helpless expression."),
    # ── cx Intense / Dramatic ← AI detects "c0" ──────────────────────────────
    "c0": ("Intense Stare",  "鋭い眼差し",       "Unblinking focused gaze; commanding presence; AI-detectable baseline."),
    "c1": ("Fierce",         "猛々しい顔",       "Bared teeth or set jaw, forward lean; battle-ready aggression."),
    "c2": ("Stoic",          "泰然顔",           "Utterly still face, no readable emotion; iron self-control."),
    "c3": ("Determined",     "決意の顔",         "Set jaw, forward-leaning head, fixed unblinking gaze; unwavering resolve."),
    "c4": ("Resolute",       "断固たる顔",       "Quiet unshakeable purpose; no drama, just absolute certainty."),
    "c5": ("Commanding",     "威厳顔",           "Authority and power projected outward; others instinctively defer."),
    "c6": ("Piercing",       "刺すような眼差し", "Gaze that cuts straight through; nothing is hidden from it."),
    "c7": ("Formidable",     "圧倒的存在感",     "Intimidating presence; the whole face radiates controlled power."),
}

def expression_category(x_code):
    """Return category name for a 2-digit hex X code e.g. '1a' → 'Primary Emotions'."""
    try:
        tens = int(x_code[0], 16)
        return EXPRESSION_CATEGORIES.get(tens, "")
    except Exception:
        return ""

def expression_label(x_code):
    """Return (english, japanese) name for a 2-digit hex X code, or ('', '') if unknown."""
    entry = EXPRESSION_TABLE.get(x_code.lower(), None)
    if entry:
        return entry[0], entry[1]
    return "", ""


# ── Face Angle reference table (F field) ─────────────────────────────────────
# F field is 2 hex digits: first digit = vertical tilt, second = horizontal rotation
# Horizontal: 0=full front … 8=profile … f=full back
# Vertical:   0=level … 4=chin up … c=chin down
FACE_ANGLE_TABLE = {
    # ── Straight-on (vertical 0) ──────────────────────────────────────────────
    "00": ("Front",           "正面",         "Facing directly at camera, level."),
    "01": ("Front Slight R",  "正面・右向き", "Very slight turn to subject's right."),
    "02": ("Front Quarter R", "正面右斜め",   "Roughly 1/8 turn right."),
    "03": ("3/4 Right",       "右斜め",       "Classic 3/4 view, subject turned right."),
    "04": ("Half Right",      "右半面",       "Half-profile, right side."),
    "05": ("Profile Right",   "右横顔",       "Full profile, right side."),
    "06": ("3/4 Back R",      "右後斜め",     "3/4 back view, right side."),
    "07": ("Back Slight R",   "後ろ右",       "Nearly full back, slight right."),
    "08": ("Back",            "後ろ",         "Full back of head."),
    "09": ("Back Slight L",   "後ろ左",       "Nearly full back, slight left."),
    "0a": ("3/4 Back L",      "左後斜め",     "3/4 back view, left side."),
    "0b": ("Profile Left",    "左横顔",       "Full profile, left side."),
    "0c": ("Half Left",       "左半面",       "Half-profile, left side."),
    "0d": ("3/4 Left",        "左斜め",       "Classic 3/4 view, subject turned left."),
    "0e": ("Front Quarter L", "正面左斜め",   "Roughly 1/8 turn left."),
    "0f": ("Front Slight L",  "正面・左向き", "Very slight turn to subject's left."),
    # ── Chin up (vertical 4) ─────────────────────────────────────────────────
    "40": ("Chin Up Front",   "あご上・正面", "Facing camera, head tilted back."),
    "43": ("Chin Up 3/4 R",   "あご上・右斜め","Head back, 3/4 right."),
    "45": ("Chin Up Profile", "あご上・横顔", "Head back, full profile."),
    "48": ("Chin Up Back",    "あご上・後ろ", "Head thrown back."),
    # ── Chin down (vertical c) ────────────────────────────────────────────────
    "c0": ("Chin Down Front", "うつむき・正面","Head bowed, facing camera."),
    "c3": ("Chin Down 3/4 R", "うつむき・右斜め","Head bowed, 3/4 right."),
    "c5": ("Chin Down Prof",  "うつむき・横顔","Head bowed, full profile."),
    "c8": ("Chin Down Back",  "うつむき・後ろ","Head bowed, back of head."),
}

def face_angle_label(f_code):
    """Return (english, japanese) for a 2-digit hex F code, or ('', '') if unknown."""
    entry = FACE_ANGLE_TABLE.get(f_code.lower(), None)
    if entry:
        return entry[0], entry[1]
    return "", ""

# Default coded fields — used as fallback when attrs_tags.json has no __coded_fields__ key.
# To change coded fields without editing Python: add "__coded_fields__" to data/attrs_tags.json.
_DEFAULT_CODED_FIELDS = [
    # (letter, label, digits)
    # digits: 2 or 3 = hex digit count; 0 = boolean flag (letter only, no value)
    # Each digit position has independent meaning — see _DEFAULT_TAG_GROUPS for sub-tables
    # ── Person / Subject ─────────────────────────────────────────────────────
    ("A",   "Animal",        2),   # animal type — 2-digit matrix (00 = no animal)
    ("PI",  "PersonInhrt",   3),   # origin/inherited person ID
    # PW is handled as multi-token (like P), not a single CODED_FIELD
    # ── Face ─────────────────────────────────────────────────────────────────
    ("E",   "Eyes",          2),   # [2nd=color][1st=modifier]
    ("HC",  "Hair",          3),   # [3rd=length][2nd=style][1st=color]
    ("FA",  "FaceAngle",     2),   # [2nd=vertical][1st=direction]
    ("X",   "Expression",    2),   # [2nd=category][1st=index]  see EXPRESSION_TABLE
    # ── Body ─────────────────────────────────────────────────────────────────
    ("SK",  "Skin",          2),   # [2nd=reserved][1st=type 0-5]
    ("B",   "Bust",          2),   # [2nd=size][1st=shape]
    ("WH",  "WaistHip",      2),   # [2nd=waist][1st=hip]
    ("PM",  "PostureMotion", 2),   # [2nd=posture][1st=motion]
    ("CL",  "Clothing",      4),   # [4=topColor][3=top][2=botColor][1=bot]; 0=unknown 1=none f=custom
    ("T",   "Tool",          2),   # 00=nothing  ff=custom
    # ── Technical ────────────────────────────────────────────────────────────
    ("CS",  "CameraShot",    3),   # [3rd=shot area][2nd=angle][1st=lighting]
    ("BG",  "Background",    2),   # 16×16 matrix — 2-digit hex (00-ff). Was 3
                                   # historically; reduced to match the actual
                                   # Background_Table (e.g. Ocean=42).
    ("O",   "Orientation",   2),   # f1=15:1  73=21:9  09=16:9  32=3:2  43=4:3  11=1:1  34=3:4  23=2:3  90=9:16
    ("R",   "Resolution",    2),   # 36=360p 48=480p 72=720p a8=1080p a4=1440p 04=4K 08=8K
    ("K",   "FrameRate",     2),   # 24=24fps 30=30fps 60=60fps b0=120fps
    ("J",   "Timestamp",     8),   # yymmddHHMMSS as 8 base-36 chars
    ("ED",  "Editable",      0),   # flag — ED present = app may auto-rename
    ("WM",  "Watermark",     0),   # flag — WM present = watermarked. Always last.
]

def _load_coded_fields():
    """Load CODED_FIELDS from data/attrs_tags.json __coded_fields__ key.
    Falls back to _DEFAULT_CODED_FIELDS if not present or on error."""
    try:
        if os.path.exists(TAGS_FILE):
            with open(TAGS_FILE, encoding="utf-8") as _f:
                _raw = json.load(_f)
            _cf = _raw.get("__coded_fields__")
            if _cf and isinstance(_cf, list):
                result = []
                for item in _cf:
                    if isinstance(item, (list, tuple)) and len(item) == 3:
                        result.append((str(item[0]), str(item[1]), int(item[2])))
                if result:
                    return result
    except Exception:
        pass
    return list(_DEFAULT_CODED_FIELDS)

CODED_FIELDS = _load_coded_fields()

# Person token pattern: P + (human 3-hex OR animal A+3-hex)  [not followed by W]
_PERSON_PAT = r'P(?!W)(A[0-9a-f]{3}|[0-9a-f]{3})'
# PersonWith token pattern: PW + 3-hex (multi-token, like P)
_PW_PAT = r'PW([0-9a-f]{3})'

# Regex for the non-person coded fields (after all P tokens are stripped)
def _field_pat(letter, digits):
    if digits == 0:
        return rf'(?P<{letter.lower()}>{letter})?'      # flag: just the letter, no value
    char_cls = "[0-9a-z]" if letter == "J" else "[0-9a-f]"   # J = base-36 timestamp
    return rf'(?:{letter}(?P<{letter.lower()}>{char_cls}{{{digits}}}))?'

_FIELD_RE = re.compile(
    r'^'
    + ''.join(_field_pat(letter, digits) for letter, _, digits in CODED_FIELDS)
    + r'$'
    # NOTE: no re.IGNORECASE — uppercase = field key, lowercase = value
)

_PCF_CACHE = {}

def parse_coded_filename(stem):
    """Parse a coded filename stem into a dict.
    Supports two formats:
      Person-first: P001PW002E01HC001...   (AI search files)
      Date-first:   J3bmrvfkvP001E01...    (regular photos; P optional)
    Returns {'persons': [...], 'persons_with': [...], 'j': '...', ...}
    or None if stem has neither P tokens nor a leading J field."""
    # Same stem is parsed many times per attr-panel refresh (once per widget);
    # cache by raw stem so we don't redo regex work in a hot loop.
    if stem in _PCF_CACHE:
        _v = _PCF_CACHE[stem]
        return None if _v is None else dict(_v)
    _orig_stem = stem
    # Normalize: strip legacy -hex fingerprint suffix (size-based, no longer used)
    stem = re.sub(r'-[0-9a-f]{3,6}$', '', stem)
    persons = re.findall(_PERSON_PAT, stem)
    persons_with = re.findall(_PW_PAT, stem)
    # No early-return on "no P and no leading J" — the tolerant fallback
    # below scans for fields anywhere in the stem and is the only thing that
    # can preserve J across renames for a stem like "E33HC333…J3bn0ryb2".
    # If neither strict nor lenient finds a single field, we'll still return
    # None at the end.
    # Strip all P and PW tokens before matching coded fields
    remainder = re.sub(r'PW[0-9a-f]{3}', '', stem)
    remainder = re.sub(r'P(?!W)(?:A[0-9a-f]{3}|[0-9a-f]{3})', '', remainder)
    m = _FIELD_RE.match(remainder if remainder else '')
    if m is None:
        # Strict in-order regex didn't match — fall back to a per-field
        # tolerant scan that pulls each field's value regardless of position.
        # This catches files renamed before the canonical field order was
        # finalized (e.g. "...J{j}CL...BG..." where J is mid-string instead
        # of at the end). Without this, every rename of such a file gets a
        # fresh J from ctime → endless filename churn.
        result = {'persons': persons, 'persons_with': persons_with}
        _ok = False
        for letter, _, digits in CODED_FIELDS:
            lk = letter.lower()
            if digits == 0:
                # Boolean flag — letter, not part of another key's name.
                # Lookbehind forbids uppercase only (lowercase hex is fine
                # because that's the previous field's value tail).
                _bm = re.search(rf'(?<![A-Z]){letter}(?![A-Za-z0-9])', remainder)
                result[lk] = letter if _bm else ""
                if _bm:
                    _ok = True
            else:
                _cls = "[0-9a-z]" if letter == "J" else "[0-9a-f]"
                _fm = re.search(rf'(?<![A-Z]){letter}({_cls}{{{digits}}})', remainder)
                result[lk] = _fm.group(1) if _fm else ""
                if _fm:
                    _ok = True
        if not _ok:
            _PCF_CACHE[_orig_stem] = None
            if len(_PCF_CACHE) > 4096:
                _PCF_CACHE.clear()
            return None
        _PCF_CACHE[_orig_stem] = dict(result)
        if len(_PCF_CACHE) > 4096:
            _PCF_CACHE.clear()
        return result
    result = {'persons': persons, 'persons_with': persons_with}
    result.update({k: (v or "") for k, v in m.groupdict().items()})
    _PCF_CACHE[_orig_stem] = dict(result)
    if len(_PCF_CACHE) > 4096:
        _PCF_CACHE.clear()
    return result

def build_coded_filename(parts, date_first=False, field_order=None):
    """Build a coded filename stem from a dict of parts.
    Format: P{pid}[P{pid2}…][PW{pid}…]{fields in CODED_FIELDS order}.
    parts keys: persons (list), persons_with (list), plus lowercase coded field keys.

    The legacy date-first mode (J{j} prepended) was removed — J now lives at
    its CODED_FIELDS position like every other field. Files with no person
    just start at the first non-empty field; no special leading marker.
    The date_first / field_order params are kept for signature compatibility
    but are ignored.
    """
    _fields = CODED_FIELDS
    persons = parts.get("persons", [])
    stem = ''.join(f'P{p}' for p in persons)
    for pw in parts.get("persons_with", []):
        pw = str(pw).strip().lower().zfill(3)[:3]
        if pw and pw != "000":
            stem += f'PW{pw}'
    for letter, _, digits in _fields:
        val = parts.get(letter.lower(), "")
        if not val:
            continue
        if digits == 0:
            stem += letter                          # boolean flag — just append the letter
        else:
            val = str(val).strip().lower().zfill(digits)[:digits]
            if val != "0" * digits:
                stem += f"{letter}{val}"
    return stem

# ── Face identity database ───────────────────────────────────────────────────

def faces_db_path(project):
    return os.path.join(_DATA_DIR, f"faces_{project}.json")

_faces_db_cache = {}  # project -> (mtime, db)

def load_faces_db(project):
    p = faces_db_path(project)
    if os.path.exists(p):
        try:
            mtime = os.path.getmtime(p)
            if _faces_db_cache.get(project, (None,))[0] == mtime:
                return _faces_db_cache[project][1]
            with open(p, encoding="utf-8") as f:
                db = json.load(f)
            _faces_db_cache[project] = (mtime, db)
            return db
        except Exception:
            pass
    return {"next_id": 1, "faces": {}}  # 000 = no human; humans start at 001

def save_faces_db(project, db):
    _faces_db_cache.pop(project, None)   # invalidate cache on write
    with open(faces_db_path(project), "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)

def detect_or_assign_person_id(path, project, threshold=0.65, raise_errors=False):
    """Extract face embedding, match against project face DB, return hex ID.
    Each person ID stores multiple embeddings; comparison uses closest match
    across all samples so accuracy improves as more images are confirmed.
    Assigns a new ID (001–fff) if no known person matches. 000 = no human.
    If raise_errors=True, exceptions propagate instead of returning None."""
    _VIDEO_EXTS = ('.mp4', '.mkv', '.mov', '.avi', '.webm')
    _is_video = path.lower().endswith(_VIDEO_EXTS)
    try:
        import face_recognition
        import numpy as np
        if _is_video:
            # Extract a frame from the middle of the video for face detection
            import cv2
            cap = cv2.VideoCapture(path)
            ret, frame = cap.read()
            cap.release()
            if not ret or frame is None:
                return None
            img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        else:
            from PIL import UnidentifiedImageError
            try:
                img = face_recognition.load_image_file(path)
            except (UnidentifiedImageError, OSError):
                # File has image extension but is not a valid image (e.g. MP4 with .jpg)
                return None
        # dlib is not thread-safe — serialize all face encoding calls
        with _face_lock:
            encodings = face_recognition.face_encodings(img)
        if not encodings:
            return None    # no face — background/object, no ID assigned
        enc = encodings[0]

        db    = load_faces_db(project)
        faces = db.setdefault("faces", {})
        aliases = load_person_aliases()

        def _group_samples(fid):
            """Return all embeddings for fid and all its alias-linked IDs."""
            group = get_alias_group(fid, aliases)
            embs = []
            for gid in group:
                fdata = faces.get(gid, {})
                s = fdata.get("embeddings", [])
                if not s and fdata.get("embedding"):
                    s = [fdata["embedding"]]
                embs.extend(s)
            return embs

        # Find closest known person across ALL their stored embeddings (+ aliases)
        best_id, best_dist = None, 1.0
        seen_groups = set()
        for fid in faces:
            # Only compare once per alias group (use lexicographically first ID as representative)
            group = get_alias_group(fid, aliases)
            rep = min(group)
            if rep in seen_groups:
                continue
            seen_groups.add(rep)
            samples = _group_samples(fid)
            if not samples:
                continue
            dist = min(face_recognition.face_distance(samples, enc))
            if dist < best_dist:
                best_dist, best_id = dist, fid

        if best_dist < threshold:
            # Known person — add this embedding as a new sample (up to 20 kept)
            _add_embedding(faces[best_id], enc.tolist())
            # Repair source_path if it no longer exists on disk
            if not os.path.exists(faces[best_id].get("source_path", "")):
                faces[best_id]["source_path"] = path
            save_faces_db(project, db)
            return best_id

        # New person — 000 is reserved for "no human", start from 001
        next_id = max(db.get("next_id", 1), 1)
        if next_id > 0xfff:
            return None
        new_id = format(next_id, "03x")
        faces[new_id] = {"embeddings": [enc.tolist()], "source_path": path}
        db["next_id"] = next_id + 1
        save_faces_db(project, db)
        return new_id
    except Exception as e:
        if raise_errors:
            raise
        return None


def match_person_id(path, project, threshold=0.65):
    """Match face in image against known people — never assigns a new ID.
    Returns the best matching person ID string, or None if no confident match."""
    _is_video = path.lower().endswith(('.mp4', '.mkv', '.mov', '.avi', '.webm'))
    try:
        import face_recognition
        if _is_video:
            import cv2
            cap = cv2.VideoCapture(path)
            ret, frame = cap.read()
            cap.release()
            if not ret or frame is None:
                return None
            img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        else:
            from PIL import UnidentifiedImageError
            try:
                img = face_recognition.load_image_file(path)
            except (UnidentifiedImageError, OSError):
                return None
        with _face_lock:
            encodings = face_recognition.face_encodings(img)
        if not encodings:
            return None
        enc   = encodings[0]
        db    = load_faces_db(project)
        faces = db.get("faces", {})
        aliases = load_person_aliases()
        best_id, best_dist = None, 1.0
        seen_groups = set()
        for fid in faces:
            group = get_alias_group(fid, aliases)
            rep = min(group)
            if rep in seen_groups:
                continue
            seen_groups.add(rep)
            # Pool all embeddings from alias group
            samples = []
            for gid in group:
                fd = faces.get(gid, {})
                s = fd.get("embeddings", [])
                if not s and fd.get("embedding"):
                    s = [fd["embedding"]]
                samples.extend(s)
            if not samples:
                continue
            dist = min(face_recognition.face_distance(samples, enc))
            if dist < best_dist:
                best_dist, best_id = dist, fid
        return best_id if best_dist < threshold else None
    except Exception:
        return None


def _add_embedding(fdata, enc, max_samples=20):
    """Add an embedding to a person's sample list, capped at max_samples."""
    samples = fdata.get("embeddings", [])
    if fdata.get("embedding") and not samples:
        samples = [fdata.pop("embedding")]   # migrate old single-embedding format
    samples.append(enc)
    if len(samples) > max_samples:
        samples = samples[-max_samples:]     # keep most recent
    fdata["embeddings"] = samples


def correct_person_id(path, project, correct_id, wrong_id=None):
    """Register face from path under correct_id.
    If wrong_id is given, removes this face's contribution from that ID's samples."""
    try:
        import face_recognition
        img  = face_recognition.load_image_file(path)
        with _face_lock:
            encs = face_recognition.face_encodings(img)
        if not encs:
            return
        enc = encs[0].tolist()
        db    = load_faces_db(project)
        faces = db.setdefault("faces", {})

        # Add to correct ID
        faces.setdefault(correct_id, {})
        _add_embedding(faces[correct_id], enc)
        # Ensure next_id is beyond this manually assigned ID
        try:
            val = int(correct_id, 16)
            if val >= db.get("next_id", 0):
                db["next_id"] = val + 1
        except ValueError:
            pass

        # Remove from wrong ID if specified
        if wrong_id and wrong_id in faces:
            samples = faces[wrong_id].get("embeddings", [])
            if samples:
                # Drop the sample most similar to enc
                distances = face_recognition.face_distance(samples, encs[0])
                worst_idx = int(distances.argmin())
                samples.pop(worst_idx)
                faces[wrong_id]["embeddings"] = samples

        save_faces_db(project, db)
    except Exception:
        pass


def reassign_person_id(old_id, new_id, project, attrs_data):
    """Permanently rename old_id → new_id everywhere.

    1. Face DB  — merge old_id embeddings into new_id pool, delete old_id key
    2. attrs    — change person_id old_id → new_id for every file entry
    3. Filenames — rename P{old_id}… → P{new_id}… on disk
    4. Aliases  — replace old_id with new_id in every alias group
    5. Right groups — same replacement
    6. Registry — move name to new_id if new_id has no name, delete old_id

    Returns the (mutated) attrs_data so the caller can save it."""
    if old_id == new_id:
        return attrs_data

    # ── 1. Face DB ─────────────────────────────────────────────────────────
    db    = load_faces_db(project)
    faces = db.setdefault("faces", {})
    if old_id in faces:
        old_data = faces.pop(old_id)
        old_embs = old_data.get("embeddings", [])
        if not old_embs and old_data.get("embedding"):
            old_embs = [old_data["embedding"]]
        if new_id not in faces:
            faces[new_id] = {
                "embeddings":  [],
                "source_path": old_data.get("source_path", ""),
            }
        new_data = faces[new_id]
        for enc in old_embs:
            _add_embedding(new_data, enc)
        if not new_data.get("source_path") and old_data.get("source_path"):
            new_data["source_path"] = old_data["source_path"]
        try:
            val = int(new_id, 16)
            if val >= db.get("next_id", 0):
                db["next_id"] = val + 1
        except ValueError:
            pass
    save_faces_db(project, db)

    # ── 2 & 3. attrs + file renames ────────────────────────────────────────
    renames = {}
    for path in list(attrs_data.keys()):
        entry = attrs_data[path]
        if entry.get("person_id") == old_id:
            entry["person_id"] = new_id
            new_path = rename_with_person_id(
                attrs_data, path, new_id, flush_stores=False, project=project)
            if new_path != path:
                renames[path] = new_path
    if renames:
        flush_path_renames_to_stores(renames, project)

    # ── 4. Aliases ─────────────────────────────────────────────────────────
    aliases = load_person_aliases()
    for grp in aliases:
        if old_id in grp:
            grp.remove(old_id)
            if new_id not in grp:
                grp.append(new_id)
    save_person_aliases([g for g in aliases if g])

    # ── 5. Right groups ────────────────────────────────────────────────────
    right_groups = load_right_groups()
    for grp in right_groups:
        if old_id in grp:
            grp.remove(old_id)
            if new_id not in grp:
                grp.append(new_id)
    save_right_groups([g for g in right_groups if g])

    # ── 6. Registry ────────────────────────────────────────────────────────
    registry = load_person_registry(project)
    old_name = registry.pop(old_id, "")
    if old_name and not registry.get(new_id):
        registry[new_id] = old_name
    save_person_registry(registry, project)

    return attrs_data


def get_person_id_label(project, hex_id):
    """Return the optional name label for a person ID, or the ID itself.
    Registry is the canonical source; falls back to faces DB name."""
    if not hex_id:
        return ""
    registry = load_person_registry(project)
    name = registry.get(hex_id, "")
    if not name:
        db = load_faces_db(project)
        name = db.get("faces", {}).get(hex_id, {}).get("name", "")
    return name or hex_id

def set_person_name(project, hex_id, name):
    """Attach a human-readable name to a person ID per project."""
    registry = load_person_registry(project)
    if name:
        registry[hex_id] = name
    elif hex_id in registry:
        del registry[hex_id]
    save_person_registry(registry, project)
    # Faces DB — keep in sync for any legacy readers
    db = load_faces_db(project)
    db.setdefault("faces", {}).setdefault(hex_id, {})["name"] = name
    save_faces_db(project, db)


# ── Attrs storage ─────────────────────────────────────────────────────────────

def attrs_path(project):
    return os.path.join(_DATA_DIR, f"attrs_{project}.json")

def load(project):
    p = attrs_path(project)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

# Keys that live only in memory (diagnostic/score-text caches and runtime markers);
# stripped at disk-write so attrs_*.json stays compact.
_TRANSIENT_ENTRY_KEYS = frozenset({
    "_project",  # only this is truly transient — runtime project marker
})

# Keys we DO persist to JSON but DON'T embed in the AItan{} block.
# CLIP/FACE detection results are now cached in JSON so re-opening a file
# doesn't re-run detection (which used to leak ~50MB per call). They stay
# out of AItan to keep the embedded block small and file-portable.
_AITAN_SKIP_KEYS = frozenset({
    "CLIP", "CLIP_HC", "CLIP_FA", "CLIP_SK", "CLIP_PM", "CLIP_E",
    "CLIP_CS", "CLIP_BG", "CLIP_X", "CLIP_CL", "FACE", "FACE_PW",
})

def _is_transient_key(k: str) -> bool:
    """Runtime markers that should never hit disk. CLIP/FACE blobs are no
    longer transient — they're cached in JSON so detection runs once per
    file. Use _is_aitan_skip_key for the embed-side filter."""
    return k in _TRANSIENT_ENTRY_KEYS

def _is_aitan_skip_key(k: str) -> bool:
    """Skip from the AItan{} block (still saved to JSON)."""
    return k in _AITAN_SKIP_KEYS or k.startswith("CLIP_")

# Guards against concurrent save() calls from multiple background threads
# (CLIP detect, face detect, auto-scan, etc.) racing with the user's
# _save_attrs. Two threads calling json.dump on the same file at the same
# time can produce a truncated/corrupt file.
_SAVE_LOCK = _threading.Lock()

def save(project, data):
    # Strip transient keys per-entry so disk JSON keeps only results, not
    # CLIP/FACE diagnostic dumps or runtime markers.
    with _SAVE_LOCK:
        # CLIP/FACE/CLIP_*/FACE_PW debug dumps were pre-cap saved at 25k+ chars
        # which triggered QTextCursor::setPosition out-of-range warnings on
        # every reload. Cap on save so existing data normalizes over time.
        _DEBUG_TEXT_CAP = 8192
        _DEBUG_KEYS = {"CLIP", "FACE", "FACE_PW"}
        def _cap_debug(k, v):
            if isinstance(v, str) and len(v) > _DEBUG_TEXT_CAP and (
                    k in _DEBUG_KEYS or k.startswith("CLIP_")):
                return v[:_DEBUG_TEXT_CAP] + "\n…(truncated)"
            return v
        cleaned = {}
        for path, entry in data.items():
            if isinstance(entry, dict):
                cleaned[path] = {k: _cap_debug(k, v) for k, v in entry.items()
                                 if not _is_transient_key(k)}
            else:
                cleaned[path] = entry
        # Atomic write: dump to temp file, then rename, so a crash or second
        # writer can never observe a partially-written JSON.
        p = attrs_path(project)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cleaned, f, indent=2, ensure_ascii=False)
        os.replace(tmp, p)

def get(attrs_data, path):
    return attrs_data.get(path, {})

def get_coded_field(entry, letter):
    """Read a coded field value from an entry dict.
    Checks manual key (letter.lower()) first, then auto-detected (cf_{letter.lower()}).
    Treats both storage locations as equivalent sources for the same field."""
    return entry.get(letter.lower(), "") or entry.get(f"cf_{letter.lower()}", "")

_UNSET = object()  # sentinel — distinguishes "not passed" from "explicit empty"


def set_file(attrs_data, path, tags, note=_UNSET, confirmed=False, project="", scene="",
             prompt=_UNSET, neg_prompt=_UNSET, seed=_UNSET, meta=None, custom="", person_id="",
             speech=_UNSET, audio=_UNSET, editable=False, preserve_text=False):
    # Trace any prompt change — set AISEARCH_TRACE_PROMPT=1 to enable.
    if os.environ.get("AISEARCH_TRACE_PROMPT"):
        try:
            _old_p = (attrs_data.get(path) or {}).get("prompt", "")
            _new_p = "<unset>" if prompt is _UNSET else prompt
            if _old_p != _new_p:
                import traceback
                _stk = traceback.extract_stack(limit=4)
                _caller = f"{_stk[-2].filename.split('/')[-1]}:{_stk[-2].lineno}"
                print(f"[PROMPT-TRACE] {os.path.basename(path)}  "
                      f"{_old_p!r:.40} -> {_new_p!r:.40}  via {_caller}")
        except Exception:
            pass
    """Write `path`'s attribute entry. Text fields (note/prompt/neg_prompt/seed/
    speech/audio) default to a sentinel: if a caller doesn't pass them, the
    existing value is preserved. Pass an explicit "" to clear a field.

    Previously defaults were "" which meant any caller that forgot a parameter
    silently wiped that field — caused user-typed prompts to be lost when
    other code paths (face apply etc.) called set_file with partial args."""
    # Resolve sentinels against existing entry so old values survive when
    # the caller didn't specify them.
    _existing = attrs_data.get(path, {})
    def _resolve(val, key, default=""):
        if val is _UNSET:
            return _existing.get(key, default)
        return val
    note       = _resolve(note,       "note")
    prompt     = _resolve(prompt,     "prompt")
    neg_prompt = _resolve(neg_prompt, "neg_prompt")
    seed       = _resolve(seed,       "seed")
    speech     = _resolve(speech,     "speech")
    audio      = _resolve(audio,      "audio")

    has_data = (tags or note or confirmed or project or scene or prompt or neg_prompt
                or seed or meta or custom or person_id or speech or audio or editable)
    if not has_data and not preserve_text:
        attrs_data.pop(path, None)
    else:
        # Merge into existing entry so unknown/extra fields are preserved
        entry = dict(attrs_data.get(path, {}))
        entry["tags"]       = list(dict.fromkeys(tags))  # deduplicate, preserve order
        entry["confirmed"]  = confirmed
        entry["project"]    = project
        entry["scene"]      = scene
        entry["custom"]     = custom
        entry["person_id"]  = person_id
        entry["audio"]      = audio
        entry["editable"]   = editable
        # preserve_text=True (auto-scan): never overwrite user text with empty string
        if preserve_text:
            if note:       entry["note"]       = note
            elif "note" not in entry: entry["note"] = ""
            if prompt:     entry["prompt"]     = prompt
            if neg_prompt: entry["neg_prompt"] = neg_prompt
            if seed:       entry["seed"]       = seed
            if speech:     entry["speech"]     = speech
        else:
            entry["note"]       = note
            entry["prompt"]     = prompt
            entry["neg_prompt"] = neg_prompt
            entry["seed"]       = seed
            entry["speech"]     = speech
        if meta:
            entry["meta"] = meta
        attrs_data[path] = entry

def is_editable(attrs_data, path):
    """Return True if the file is editable (app has touched it and user hasn't locked it).
    New files not yet in attrs default to True so users can still move/delete them."""
    entry = attrs_data.get(path, {})
    if "editable" not in entry:
        return True   # untouched file — user actions (move/delete) still allowed
    return bool(entry["editable"])

def is_confirmed(attrs_data, path):
    return attrs_data.get(path, {}).get("confirmed", False)

def tag_label(key):
    for item in TAGS:
        if isinstance(item, (list, tuple)) and len(item) >= 2 and item[0] == key:
            return item[1]
    return key


# ── Metadata extraction ──────────────────────────────────────────────────────

def file_fingerprint(path):
    """Return a short hex fingerprint from file size in KB.
    Larger value = higher resolution / upscaled version.
    Used as the I field: P001B0a1Iabc.jpg where 'abc' = hex(size_kb)."""
    try:
        size_kb = os.path.getsize(path) // 1024
        return format(size_kb, 'x')   # e.g. 2048KB → '800', 8192KB → '2000'
    except Exception:
        return None

_AITAN_PREFIX = "AItan"
_AITAN_VERSION = "2.2"  # stamped into every AItan{} block as "ver"

def _extract_aitan_block(text: str) -> dict | None:
    """Parse AItan{...} from a metadata string. Returns dict or None."""
    try:
        idx = text.find(_AITAN_PREFIX + "{")
        if idx == -1:
            return None
        start = idx + len(_AITAN_PREFIX)
        # Find matching closing brace
        depth, i = 0, start
        while i < len(text):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start : i + 1])
            i += 1
    except Exception:
        pass
    return None

def _strip_aitan_block(text: str) -> str:
    """Remove the AItan{...} block from a string, leaving the rest intact."""
    if not text or _AITAN_PREFIX + "{" not in text:
        return text
    idx = text.find(_AITAN_PREFIX + "{")
    end = idx + len(_AITAN_PREFIX)
    depth, i = 0, end
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return (text[:idx] + text[i + 1:]).strip()
        i += 1
    return text

def _build_aitan_block(entry: dict) -> str:
    """Serialize attrs entry as AItan{...} string (excludes heavy/internal fields).
    CLIP/FACE debug blobs and runtime markers are stripped so the embedded
    block stays small and file-portable — only results travel with the file.
    "ver" is stamped first so future readers can branch on writer version."""
    # Redundant flags that don't carry portable info — confirmed/editable are
    # session-scoped UI state, audio_probed is a "we already ffprobed this
    # file" cache marker, not file-level metadata.
    _SKIP_FIELDS = {"meta", "ver", "confirmed", "editable", "audio_probed"}
    slim = {"ver": _AITAN_VERSION}
    for k, v in entry.items():
        if (k in _SKIP_FIELDS
                or _is_transient_key(k)
                or _is_aitan_skip_key(k)        # CLIP_*/FACE stay out of file embed
                or v in (None, "", [], {})):
            continue
        slim[k] = v
    return _AITAN_PREFIX + json.dumps(slim, ensure_ascii=False, separators=(",", ":"))

def read_raw_embedded_text(path: str) -> str:
    """Return all raw embedded metadata text from a file as a formatted string."""
    ext = os.path.splitext(path)[1].lower()
    parts = []
    try:
        if ext in (".mp4", ".mkv", ".mov", ".m4v", ".avi", ".webm", ".wmv"):
            import subprocess, json as _json
            result = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json",
                 "-show_format", "-show_streams", path],
                capture_output=True, text=True, timeout=10)
            probe = _json.loads(result.stdout)
            tags = probe.get("format", {}).get("tags", {})
            for k, v in tags.items():
                parts.append(f"[{k}]\n{v}")
            for s in probe.get("streams", []):
                s_tags = s.get("tags", {})
                if s_tags:
                    idx = s.get("index", "?")
                    for k, v in s_tags.items():
                        parts.append(f"[stream{idx}/{k}]\n{v}")
        else:
            from PIL import Image, ExifTags
            with Image.open(path) as img:
                info = img.info
                # Text chunks / info dict
                _skip_binary = ("exif", "icc_profile", "dpi", "jfif", "jfif_version",
                                "jfif_density", "jfif_unit", "adobe", "photoshop")
                for k, v in info.items():
                    if k.lower() in _skip_binary or isinstance(v, (bytes, bytearray)):
                        continue
                    parts.append(f"[{k}]\n{v}")
                # EXIF
                exif_raw = img._getexif() if hasattr(img, "_getexif") else None
                if not exif_raw and hasattr(img, "getexif"):
                    exif_raw = dict(img.getexif())
                if exif_raw:
                    for tag_id, v in exif_raw.items():
                        tag_name = ExifTags.TAGS.get(tag_id, str(tag_id))
                        if isinstance(v, (bytes, bytearray)):
                            try:
                                import piexif.helper
                                if tag_id == 0x9286:  # UserComment
                                    v = piexif.helper.UserComment.load(v)
                                else:
                                    v = v.decode("utf-8", errors="replace")
                            except Exception:
                                v = repr(v)
                        parts.append(f"[EXIF:{tag_name}]\n{v}")
    except Exception as e:
        parts.append(f"(error reading metadata: {e})")
    return "\n\n".join(parts)

def _has_real_data(entry: dict) -> bool:
    """Return True if an attrs entry (or parsed AItan block) has meaningful data beyond defaults."""
    if not entry:
        return False
    for k, v in entry.items():
        if v in (None, "", [], {}):
            continue
        if k == "confirmed" and v is False:
            continue
        if k == "editable" and v is True:
            continue
        return True
    return False


def _read_embedded_aitan_block(path: str) -> dict | None:
    """Return parsed AItan dict from file's embedded metadata, or None if absent/empty."""
    _PREFIX = "AItan"
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in (".jpg", ".jpeg"):
            import piexif, piexif.helper
            from PIL import Image
            with Image.open(path) as img:
                raw_exif = img.info.get("exif", b"")
            if not raw_exif:
                return None
            exif = piexif.load(raw_exif)
            uc_raw = exif.get("Exif", {}).get(piexif.ExifIFD.UserComment, b"")
            try:
                uc = piexif.helper.UserComment.load(uc_raw)
            except Exception:
                uc = uc_raw.decode("utf-8", errors="replace") if uc_raw else ""
            if uc.startswith(_PREFIX):
                try:
                    return json.loads(uc[len(_PREFIX):])
                except Exception:
                    return None
        elif ext in (".png", ".webp"):
            from PIL import Image
            with Image.open(path) as img:
                # Primary: dedicated "AItan" chunk (current write location).
                # Legacy: "Description"/"description" (older files).
                info = img.info
                aitan_text = info.get("AItan", "") or info.get("Description", "") or info.get("description", "")
                # WebP EXIF UserComment fallback (some old files put AItan there)
                if not aitan_text or not str(aitan_text).startswith(_PREFIX):
                    raw_exif = info.get("exif", b"")
                    if raw_exif:
                        try:
                            import piexif, piexif.helper
                            exif = piexif.load(raw_exif)
                            uc_raw = exif.get("Exif", {}).get(piexif.ExifIFD.UserComment, b"")
                            if uc_raw:
                                aitan_text = piexif.helper.UserComment.load(uc_raw)
                        except Exception:
                            pass
            desc = str(aitan_text or "")
            if desc.startswith(_PREFIX):
                try:
                    return json.loads(desc[len(_PREFIX):])
                except Exception:
                    return None
        else:
            raw = read_raw_embedded_text(path)
            for line in raw.split("\n"):
                line = line.strip()
                if line.startswith(_PREFIX):
                    try:
                        return json.loads(line[len(_PREFIX):])
                    except Exception:
                        pass
    except Exception:
        pass
    return None


def migrate_aitan_video(path: str) -> bool:
    """Migrate AItan{} block from old location ("comment" tag) to new
    location ("description" tag). Preserves anything else in either tag.
    Returns True if migration was performed, False if nothing to do.

    Old versions of this app wrote AItan to "comment", which clobbered the
    ComfyUI workflow stored there. For files written before that fix, the
    workflow is already lost, but we can still relocate the AItan block so
    new bakes don't keep stomping on "comment" going forward.
    """
    import subprocess, tempfile, shutil, json as _json, uuid as _uuid
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
            capture_output=True, text=True, timeout=10)
        fmt_tags = _json.loads(probe.stdout).get("format", {}).get("tags", {}) or {}
    except Exception:
        return False
    comment = fmt_tags.get("comment", "") or ""
    description = fmt_tags.get("description", "") or ""
    aitan_in_comment = _extract_aitan_block(comment)
    if aitan_in_comment is None:
        return False  # No AItan in comment — nothing to migrate
    # Build new field values
    block = _AITAN_PREFIX + json.dumps(aitan_in_comment, ensure_ascii=False, separators=(",", ":"))
    new_comment = _strip_aitan_block(comment).rstrip()
    desc_stripped = _strip_aitan_block(description) if description else ""
    new_description = (desc_stripped.rstrip() + "\n" + block) if desc_stripped.strip() else block
    # Detect container ext
    try:
        _fmt = _json.loads(probe.stdout).get("format", {}).get("format_name", "")
        _ext = ".mp4" if "mp4" in _fmt or "mov" in _fmt else \
               ".mkv" if "matroska" in _fmt else \
               ".webm" if "webm" in _fmt else \
               os.path.splitext(path)[1]
    except Exception:
        _ext = os.path.splitext(path)[1]
    tmp = os.path.join(os.path.dirname(path),
                       "." + os.path.basename(path) + "." + _uuid.uuid4().hex[:8] + ".aitan_tmp" + _ext)
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", path,
             "-metadata", f"comment={new_comment}",
             "-metadata", f"description={new_description}",
             "-codec", "copy", tmp],
            capture_output=True, timeout=30)
        orig_size = os.path.getsize(path) if os.path.exists(path) else 0
        tmp_size  = os.path.getsize(tmp)  if os.path.exists(tmp)  else 0
        if tmp_size > 0 and (orig_size == 0 or tmp_size >= orig_size * 0.5):
            shutil.move(tmp, path)
            return True
        return False
    finally:
        if os.path.exists(tmp):
            try: os.remove(tmp)
            except Exception: pass


def migrate_aitan_image(path: str) -> bool:
    """For images, normalize where the AItan{} block lives without touching
    any other metadata. PNG: ensure AItan goes in its own "AItan" text chunk
    and strip the legacy copy from "Description". JPEG/WebP: rewrite the
    existing AItan block in UserComment with strip+append so anything else
    in UserComment (e.g. ComfyUI workflow) is preserved.
    Returns True if the file was actually rewritten, False otherwise.
    """
    import shutil as _shutil, uuid as _uuid
    ext = os.path.splitext(path)[1].lower()
    if ext not in {".png", ".jpg", ".jpeg", ".webp"}:
        return False
    try:
        from PIL import Image, PngImagePlugin
    except Exception:
        return False
    try:
        img = Image.open(path)
        img.load()
    except Exception:
        return False

    tmp = os.path.join(os.path.dirname(path),
                       "." + os.path.basename(path) + "." + _uuid.uuid4().hex[:8] + ".aitan_tmp" + ext)
    try:
        if ext == ".png":
            info = img.info
            # Find AItan in any text chunk
            aitan_block = None
            for _k in ("AItan", "Description", "UserComment", "Comment"):
                _v = info.get(_k, "")
                if _v and _AITAN_PREFIX in str(_v):
                    _parsed = _extract_aitan_block(str(_v))
                    if _parsed is not None:
                        aitan_block = _AITAN_PREFIX + json.dumps(_parsed, ensure_ascii=False, separators=(",", ":"))
                        break
            if aitan_block is None:
                return False  # no AItan anywhere — nothing to migrate
            # Build new chunks: preserve everything, AItan goes in dedicated key,
            # legacy "Description" gets the AItan block stripped out.
            meta = PngImagePlugin.PngInfo()
            for _k, _v in info.items():
                if _k == "AItan" or not isinstance(_v, str):
                    continue
                if _k == "Description":
                    _stripped = _strip_aitan_block(_v)
                    if _stripped.strip():
                        try: meta.add_text(_k, _stripped)
                        except Exception: pass
                    continue
                try: meta.add_text(_k, _v)
                except Exception: pass
            meta.add_text("AItan", aitan_block)
            img.save(tmp, pnginfo=meta)
        else:
            # JPEG / WebP — pull AItan from any legacy slot (UserComment OR
            # XPComment) and write it back to UserComment with strip+append,
            # then remove the XPComment copy to avoid duplication.
            import piexif, piexif.helper
            exif_data = img.info.get("exif", b"")
            try:
                exif_dict = piexif.load(exif_data) if exif_data else {}
            except Exception:
                exif_dict = {}
            exif_dict.setdefault("Exif", {})
            exif_dict.setdefault("0th", {})
            # Read XPComment (UTF-16LE)
            xp_raw = exif_dict["0th"].get(piexif.ImageIFD.XPComment, b"")
            existing_xp = ""
            if xp_raw:
                try:
                    existing_xp = bytes(xp_raw).rstrip(b"\x00").decode("utf-16le", errors="replace")
                except Exception:
                    existing_xp = ""
            # Read UserComment
            uc_raw = exif_dict["Exif"].get(piexif.ExifIFD.UserComment, b"")
            existing_uc = ""
            try:
                existing_uc = piexif.helper.UserComment.load(uc_raw) if uc_raw else ""
            except Exception:
                existing_uc = ""
            # Find AItan in either slot — XPComment takes priority since that's
            # the legacy location we need to clean up
            block = None
            for src in (existing_xp, existing_uc):
                if src and _AITAN_PREFIX + "{" in src:
                    parsed = _extract_aitan_block(src)
                    if parsed is not None:
                        block = _AITAN_PREFIX + json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
                        break
            if block is None:
                return False
            # Build new UserComment: existing UC with AItan stripped + new block
            uc_stripped = _strip_aitan_block(existing_uc).rstrip() if existing_uc else ""
            new_uc = (uc_stripped + "\n" + block) if uc_stripped else block
            # Skip rewrite only if XPComment was clean AND UC already correct
            if not existing_xp and new_uc == existing_uc:
                return False
            exif_dict["Exif"][piexif.ExifIFD.UserComment] = (
                piexif.helper.UserComment.dump(new_uc, encoding="unicode"))
            # Remove XPComment so the AItan block isn't stored twice
            if piexif.ImageIFD.XPComment in exif_dict["0th"]:
                del exif_dict["0th"][piexif.ImageIFD.XPComment]
            try:
                exif_bytes = piexif.dump(exif_dict)
            except Exception:
                return False
            if ext in (".jpg", ".jpeg"):
                _shutil.copy2(path, tmp)
                try:
                    piexif.insert(exif_bytes, tmp)
                except Exception:
                    return False
            else:  # .webp
                lossless = img.info.get("lossless", False)
                img.save(tmp, format="WEBP", exif=exif_bytes, lossless=lossless, quality=90)
        if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
            _shutil.move(tmp, path)
            return True
        return False
    except Exception:
        return False
    finally:
        try: img.close()
        except Exception: pass
        if os.path.exists(tmp):
            try: os.remove(tmp)
            except Exception: pass


def embed_aitan_meta(path: str, entry: dict, _raise: bool = False) -> bool:
    """Write AItan{...} block into the file's comment/description metadata.
    Video: uses ffmpeg comment tag (copy-only, no re-encode).
    Image: writes PNG description or JPEG EXIF UserComment.
    Returns True on success. If _raise=True, raises on failure instead."""
    block = _build_aitan_block(entry)
    ext = os.path.splitext(path)[1].lower()
    _VID = {".mp4", ".mkv", ".mov", ".m4v", ".avi", ".webm", ".wmv"}
    _IMG = {".jpg", ".jpeg", ".png", ".webp"}
    if ext in _VID:
        return _embed_aitan_video(path, block, _raise=_raise)
    elif ext in _IMG:
        return _embed_aitan_image(path, block, _raise=_raise)
    return False

def _embed_aitan_video(path: str, block: str, _raise: bool = False) -> bool:
    import subprocess, tempfile, shutil, json as _json
    # Detect actual container format (file may have wrong extension e.g. MP4 as .jpg)
    try:
        _probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
            capture_output=True, text=True, timeout=10)
        _fmt = _json.loads(_probe.stdout).get("format", {}).get("format_name", "")
        _ext = ".mp4" if "mp4" in _fmt or "mov" in _fmt else \
               ".mkv" if "matroska" in _fmt else \
               ".webm" if "webm" in _fmt else \
               os.path.splitext(path)[1]
    except Exception:
        _ext = os.path.splitext(path)[1]
    # Use a unique tmp name so concurrent bake calls on the same file don't collide
    import uuid as _uuid
    tmp = os.path.join(os.path.dirname(path),
                       "." + os.path.basename(path) + "." + _uuid.uuid4().hex[:8] + ".aitan_tmp" + _ext)
    # Preserve any existing "description" content. Strip the prior AItan{}
    # block (so re-bakes don't duplicate it) and append the new one, leaving
    # everything else verbatim. ComfyUI's "comment" tag stays untouched.
    _existing_desc = ""
    try:
        _probe2 = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
            capture_output=True, text=True, timeout=10)
        _existing_desc = _json.loads(_probe2.stdout).get("format", {}).get("tags", {}).get("description", "") or ""
    except Exception:
        pass
    _stripped = _strip_aitan_block(_existing_desc) if _existing_desc else ""
    _new_desc = (_stripped.rstrip() + "\n" + block) if _stripped.strip() else block
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", path,
             "-metadata", f"description={_new_desc}",
             "-codec", "copy", tmp],
            capture_output=True, timeout=30)
        # Accept if tmp was written with reasonable size (>= 50% of original),
        # even if ffmpeg returned non-zero (some containers produce warnings/non-fatal errors)
        orig_size = os.path.getsize(path) if os.path.exists(path) else 0
        tmp_size  = os.path.getsize(tmp)  if os.path.exists(tmp)  else 0
        if tmp_size > 0 and (orig_size == 0 or tmp_size >= orig_size * 0.5):
            shutil.move(tmp, path)
            return True
        err = result.stderr.decode(errors="replace").strip().splitlines()
        reason = err[-1] if err else "ffmpeg failed"
        if _raise:
            raise RuntimeError(reason)
    except RuntimeError:
        raise
    except Exception as e:
        if _raise: raise
    finally:
        if os.path.exists(tmp):
            try: os.remove(tmp)
            except Exception: pass
    return False

def _embed_aitan_image(path: str, block: str, _raise: bool = False) -> bool:
    import shutil as _shutil, uuid as _uuid
    ext = os.path.splitext(path)[1].lower()
    tmp = os.path.join(os.path.dirname(path),
                       "." + os.path.basename(path) + "." + _uuid.uuid4().hex[:8] + ".aitan_tmp" + ext)
    try:
        from PIL import Image, PngImagePlugin
        try:
            img_test = Image.open(path)
            _img_fmt = img_test.format  # read format before verify() closes the object
            img_test.verify()
        except Exception:
            # verify() failed — check if PIL can still open it as an image
            try:
                _img_fmt = Image.open(path).format
                _is_img = _img_fmt in ("JPEG", "PNG", "WEBP", "BMP", "GIF", "TIFF")
            except Exception:
                _is_img = False
            if not _is_img:
                # Not an image at all (e.g. MP4 with .jpg extension) — try video path
                return _embed_aitan_video(path, block, _raise=_raise)
        with Image.open(path) as img:
            if ext == ".png":
                # Preserve every existing text chunk verbatim; AItan goes in
                # its own dedicated "AItan" key so nothing else is touched.
                meta = PngImagePlugin.PngInfo()
                # Preserve every text chunk (ComfyUI workflow lives here)
                for _k, _v in img.info.items():
                    if _k == "AItan" or not isinstance(_v, str):
                        continue
                    try:
                        meta.add_text(_k, _v)
                    except Exception:
                        pass
                meta.add_text("AItan", block)
                # Pass through PNG ancillary chunks that PIL exposes as
                # non-string values in img.info — gamma, DPI, color profile,
                # transparency, sRGB rendering intent. Without these passed
                # explicitly img.save() drops them, destroying user metadata.
                _save_kwargs = {"pnginfo": meta}
                for _ckey in ("dpi", "gamma", "transparency",
                              "icc_profile", "srgb", "chromaticity"):
                    if _ckey in img.info:
                        _save_kwargs[_ckey] = img.info[_ckey]
                img.save(tmp, **_save_kwargs)
            elif ext in (".jpg", ".jpeg"):
                import piexif, piexif.helper
                exif_dict = {}
                try:
                    if "exif" in img.info:
                        exif_dict = piexif.load(img.info["exif"])
                except Exception:
                    pass
                # Read existing UserComment, strip any prior AItan block, then
                # append the new one. Preserves ComfyUI/A1111 workflow if it
                # was stored there.
                exif_dict.setdefault("Exif", {})
                exif_dict.setdefault("0th", {})
                _uc_raw = exif_dict["Exif"].get(piexif.ExifIFD.UserComment, b"")
                _existing_uc = ""
                try:
                    _existing_uc = piexif.helper.UserComment.load(_uc_raw) if _uc_raw else ""
                except Exception:
                    _existing_uc = ""
                _uc_stripped = _strip_aitan_block(_existing_uc) if _existing_uc else ""
                _new_uc = (_uc_stripped.rstrip() + "\n" + block) if _uc_stripped.strip() else block
                exif_dict["Exif"][piexif.ExifIFD.UserComment] = (
                    piexif.helper.UserComment.dump(_new_uc, encoding="unicode"))
                # Drop any stale XPComment containing AItan from a prior write.
                _xp_raw = exif_dict["0th"].get(piexif.ImageIFD.XPComment, b"")
                if _xp_raw:
                    try:
                        _xp_str = bytes(_xp_raw).rstrip(b"\x00").decode("utf-16le", errors="replace")
                        if _AITAN_PREFIX + "{" in _xp_str:
                            del exif_dict["0th"][piexif.ImageIFD.XPComment]
                    except Exception:
                        pass
                try:
                    exif_bytes = piexif.dump(exif_dict)
                except Exception:
                    # piexif.dump can fail on quirky EXIF (e.g. malformed
                    # GPS subdir). DO NOT fall back to a minimal dict — that
                    # would wipe legitimate user EXIF (camera, GPS, datetime,
                    # XMP refs). Skip the embed instead and bail out cleanly.
                    if _raise:
                        raise
                    return False
                # Write to temp file then use piexif.insert to inject EXIF in-place
                _shutil.copy2(path, tmp)
                try:
                    piexif.insert(exif_bytes, tmp)
                except Exception:
                    # Fallback: file has .jpeg extension but may be PNG inside, or RGBA mode
                    _fmt = img.format or ""
                    if _fmt == "PNG" or img.mode in ("RGBA", "P", "LA"):
                        _save_img = img if img.mode not in ("RGBA", "P", "LA") else img.convert("RGBA") if img.mode == "P" else img
                        _pngmeta = PngImagePlugin.PngInfo()
                        _pngmeta.add_text("Description", block)
                        _save_img.save(tmp, format="PNG", pnginfo=_pngmeta)
                    else:
                        _save_img = img.convert("RGB") if img.mode != "RGB" else img
                        _save_img.save(tmp, exif=exif_bytes, quality="keep")
            elif ext == ".webp":
                import piexif, piexif.helper
                exif_data = img.info.get("exif", b"")
                try:
                    exif_dict = piexif.load(exif_data) if exif_data else {}
                except Exception:
                    exif_dict = {}
                # Read existing UserComment, strip prior AItan, append new
                # — preserves ComfyUI/A1111 workflow if stored there.
                exif_dict.setdefault("Exif", {})
                exif_dict.setdefault("0th", {})
                _uc_raw = exif_dict["Exif"].get(piexif.ExifIFD.UserComment, b"")
                _existing_uc = ""
                try:
                    _existing_uc = piexif.helper.UserComment.load(_uc_raw) if _uc_raw else ""
                except Exception:
                    _existing_uc = ""
                _uc_stripped = _strip_aitan_block(_existing_uc) if _existing_uc else ""
                _new_uc = (_uc_stripped.rstrip() + "\n" + block) if _uc_stripped.strip() else block
                exif_dict["Exif"][piexif.ExifIFD.UserComment] = (
                    piexif.helper.UserComment.dump(_new_uc, encoding="unicode"))
                # Drop any stale XPComment containing AItan from a prior write.
                _xp_raw = exif_dict["0th"].get(piexif.ImageIFD.XPComment, b"")
                if _xp_raw:
                    try:
                        _xp_str = bytes(_xp_raw).rstrip(b"\x00").decode("utf-16le", errors="replace")
                        if _AITAN_PREFIX + "{" in _xp_str:
                            del exif_dict["0th"][piexif.ImageIFD.XPComment]
                    except Exception:
                        pass
                exif_bytes = piexif.dump(exif_dict)
                lossless = img.info.get("lossless", False)
                img.save(tmp, format="WEBP", exif=exif_bytes, lossless=lossless, quality=90)
            else:
                return False
        # Atomically replace original only if temp file was written successfully
        if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
            _shutil.move(tmp, path)
            return True
    except Exception as e:
        if _raise: raise
    finally:
        if os.path.exists(tmp):
            try: os.remove(tmp)
            except Exception: pass
    return False

def extract_metadata(path):
    """Return a dict of all extractable metadata for display."""
    meta = {}
    try:
        stat = os.stat(path)
        meta["File size"]  = _fmt_size(stat.st_size)
        meta["Fingerprint"] = file_fingerprint(path)
    except Exception:
        pass

    ext = path.lower()
    if ext.endswith(('.mp4', '.mkv', '.mov', '.avi', '.webm')):
        _extract_video_meta(path, meta)
    else:
        _extract_image_meta(path, meta)
    return meta

def _ratio_str(w, h):
    """Return a simplified aspect ratio string like '16:9' or '4:3'."""
    if w <= 0 or h <= 0:
        return ""
    from math import gcd
    g = gcd(w, h)
    rw, rh = w // g, h // g
    # Simplify very large ratios by approximating common ones
    known = [(16,9),(4,3),(3,2),(1,1),(9,16),(2,3),(3,4),(21,9),(9,21)]
    ratio = w / h
    for a, b in known:
        if abs(ratio - a/b) < 0.02:
            return f"{a}:{b}"
    return f"{rw}:{rh}"

def _fmt_size(b):
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} GB"

def _extract_video_meta(path, meta):
    try:
        cap = cv2.VideoCapture(path)
        w   = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h   = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        fc  = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        cap.release()
        meta["Dimensions"] = f"{w} × {h}"
        r = _ratio_str(w, h)
        if r:
            meta["Ratio"] = r
        if fps > 0:
            meta["FPS"] = f"{fps:.0f} fps"
        if fps > 0 and fc > 0:
            secs = fc / fps
            meta["Duration"] = f"[{datetime.timedelta(seconds=int(secs))}]"
    except Exception:
        pass
    # ffprobe: audio codec + ComfyUI workflow in comment tag
    try:
        import subprocess, json as _json
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", "-show_format", path],
            capture_output=True, text=True, timeout=10)
        probe = _json.loads(result.stdout)
        # Audio codec
        for s in probe.get("streams", []):
            if s.get("codec_type") == "audio":
                meta["Audio"] = s.get("codec_name", "")
                break
        # AItan lives in the dedicated "AItan" tag; ComfyUI workflow in
        # "comment". Legacy locations (description, comment) still scanned for
        # backwards compat with files baked before the split.
        fmt_tags = probe.get("format", {}).get("tags", {})
        for _field in ("AItan", "description", "comment"):
            _val = fmt_tags.get(_field, "")
            if not _val:
                continue
            _aitan = _extract_aitan_block(_val)
            if _aitan is not None and "_aitan" not in meta:
                meta["_aitan"] = _aitan
            try:
                outer = _json.loads(_val)
                workflow_str = outer.get("prompt") or outer.get("workflow")
                if workflow_str:
                    workflow = _json.loads(workflow_str) if isinstance(workflow_str, str) else workflow_str
                    _extract_comfyui_meta({"prompt": _json.dumps(workflow)}, meta)
            except Exception:
                pass
    except Exception:
        pass

def _extract_image_meta(path, meta):
    try:
        from PIL import Image, ExifTags
        with Image.open(path) as img:
            meta["Dimensions"] = f"{img.width} × {img.height}"
            r = _ratio_str(img.width, img.height)
            if r:
                meta["Ratio"] = r
            meta["Format"]     = img.format or os.path.splitext(path)[1].upper().lstrip(".")
            meta["Mode"]       = img.mode
            info = img.info

            # AItan embedded block — primary slot is PNG "AItan" chunk or JPEG/WebP
            # XPComment EXIF tag (0x9C9C). Legacy locations (Description,
            # UserComment, Comment) still scanned for backwards compat.
            for _field in ("AItan", "Description", "UserComment", "Comment"):
                _raw = info.get(_field, "")
                if _raw and _AITAN_PREFIX in str(_raw):
                    _aitan = _extract_aitan_block(str(_raw))
                    if _aitan is not None:
                        meta["_aitan"] = _aitan
                    break
            # JPEG/WebP XPComment lives in EXIF, not img.info — read separately
            if "_aitan" not in meta:
                try:
                    _xp = None
                    if "exif" in info:
                        import piexif as _piexif
                        _ex = _piexif.load(info["exif"])
                        _xp_raw = _ex.get("0th", {}).get(_piexif.ImageIFD.XPComment)
                        if _xp_raw:
                            _xp = _xp_raw.rstrip(b"\x00").decode("utf-16le", errors="replace")
                    if _xp and _AITAN_PREFIX in _xp:
                        _aitan = _extract_aitan_block(_xp)
                        if _aitan is not None:
                            meta["_aitan"] = _aitan
                except Exception:
                    pass

            # ComfyUI / A1111 generation params
            if "workflow" in info or "prompt" in info:
                _extract_comfyui_meta(info, meta)
            elif "parameters" in info:
                _extract_a1111_meta(info["parameters"], meta)

            # EXIF
            exif_raw = img._getexif() if hasattr(img, "_getexif") else None
            if exif_raw:
                tag_map = {v: k for k, v in ExifTags.TAGS.items()}
                exif = {ExifTags.TAGS.get(k, k): v for k, v in exif_raw.items()}
                for field in ("Make", "Model", "DateTime", "Software"):
                    if field in exif:
                        meta[field] = str(exif[field])
                for field, label in (
                    ("ISOSpeedRatings", "ISO"),
                    ("FNumber",         "Aperture"),
                    ("ExposureTime",    "Shutter"),
                    ("FocalLength",     "Focal length"),
                ):
                    if field in exif:
                        v = exif[field]
                        if hasattr(v, "numerator"):
                            v = f"{v.numerator}/{v.denominator}"
                        meta[label] = str(v)
    except Exception:
        pass

def _extract_comfyui_meta(info, meta):
    try:
        raw  = info.get("prompt") or info.get("workflow")
        data = json.loads(raw) if isinstance(raw, str) else raw
        models, samplers, loras, steps_list, cfgs, seeds = [], [], [], [], [], []
        prompts, neg_prompts = [], []
        _model_exts = (".safetensors", ".ckpt", ".pt", ".pth")
        _prompt_keys     = ("positive_prompt", "text", "text_g", "text_l", "prompt")
        _neg_prompt_keys = ("negative_prompt", "text_n", "negative")
        for node in data.values():
            if not isinstance(node, dict): continue
            cls    = node.get("class_type", "")
            inputs = node.get("inputs", {})
            # Seed — any node with a numeric seed/noise_seed field
            for key in ("seed", "noise_seed"):
                v = inputs.get(key)
                if isinstance(v, (int, float)) and int(v) > 0:
                    seeds.append(str(int(v)))
            # Steps + CFG
            if isinstance(inputs.get("steps"), (int, float)):
                steps_list.append(str(int(inputs["steps"])))
            if isinstance(inputs.get("cfg"), (int, float)):
                cfgs.append(f"{inputs['cfg']:.1f}")
            # Sampler name
            if isinstance(inputs.get("sampler_name"), str):
                samplers.append(inputs["sampler_name"])
            if isinstance(inputs.get("scheduler"), str) and inputs["scheduler"] not in ("normal", "karras", "simple"):
                samplers.append(inputs["scheduler"])
            # Model files
            for v in inputs.values():
                if isinstance(v, str) and v.lower().endswith(_model_exts):
                    models.append(v)
            # LoRA
            for key in ("lora_name", "lora"):
                v = inputs.get(key)
                if isinstance(v, str) and v.lower().endswith(_model_exts):
                    loras.append(v)
            # Prompts — only from nodes that have a text/prompt string input
            for key in _prompt_keys:
                v = inputs.get(key)
                if isinstance(v, str) and len(v) > 3:
                    prompts.append(v)
                    break
            for key in _neg_prompt_keys:
                v = inputs.get(key)
                if isinstance(v, str) and len(v) > 3:
                    neg_prompts.append(v)
                    break
        if models:      meta["Model"]      = ", ".join(dict.fromkeys(models))
        if samplers:    meta["Sampler"]    = ", ".join(dict.fromkeys(samplers))
        if steps_list:  meta["Steps"]      = steps_list[0]
        if cfgs:        meta["CFG"]        = cfgs[0]
        if seeds:       meta["Seed"]       = seeds[0]
        if loras:       meta["LoRAs"]      = ", ".join(dict.fromkeys(loras))
        if prompts:     meta["Prompt"]     = prompts[0]
        if neg_prompts: meta["NegPrompt"]  = neg_prompts[0]
    except Exception:
        pass

def _extract_a1111_meta(params, meta):
    try:
        lines = params.strip().split("\n")
        for line in lines:
            if line.startswith("Negative prompt:"): continue
            m = re.search(r"Steps:\s*(\d+)", line)
            if m: meta["Steps"] = m.group(1)
            m = re.search(r"Sampler:\s*([^,]+)", line)
            if m: meta["Sampler"] = m.group(1).strip()
            m = re.search(r"CFG scale:\s*([\d.]+)", line)
            if m: meta["CFG"] = m.group(1)
            m = re.search(r"Seed:\s*(\d+)", line)
            if m: meta["Seed"] = m.group(1)
            m = re.search(r"Model:\s*([^,]+)", line)
            if m: meta["Model"] = m.group(1).strip()
            m = re.search(r"Size:\s*(\d+x\d+)", line)
            if m: meta["Dimensions"] = m.group(1).replace("x", " × ")
    except Exception:
        pass


# ── Filename rules ───────────────────────────────────────────────────────────

_fn_rules_cache = {}  # project_key -> (mtime, config_dict)

def _load_fn_raw(project):
    """Load raw filename config dict from disk. Returns {} on missing/error."""
    path = filename_rules_file_for_project(project)
    key  = project or ""
    if os.path.exists(path):
        try:
            mtime = os.path.getmtime(path)
            cached = _fn_rules_cache.get(key)
            if cached and cached[0] == mtime:
                return cached[1]
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            # Support old format (bare array) and new format (object with "rules" key)
            if isinstance(raw, list):
                cfg = {"auto_rename": False, "rules": raw}
            else:
                cfg = raw
            _fn_rules_cache[key] = (mtime, cfg)
            return cfg
        except Exception:
            pass
    return {"auto_rename": False, "rules": []}

def load_filename_rules(project=None):
    """Return the rules list for a project."""
    return _load_fn_raw(project).get("rules", [])

def load_filename_config(project=None):
    """Return full filename config: {"auto_rename": bool, "rules": [...]}"""
    return dict(_load_fn_raw(project))

def get_sync_field_order(project=None):
    """Return CODED_FIELDS in canonical order.
    Historical: the rule-iteration order from filename_rules.json could
    override field order. That caused J to land mid-stem and CL/BG to
    drift to the end whenever the user's saved rules had a non-canonical
    sequence. CODED_FIELDS is the authoritative order and always wins now;
    the project parameter is kept for signature compatibility."""
    return CODED_FIELDS

def save_filename_config(config, project=None):
    """Save full filename config (auto_rename + rules) for a project."""
    path = filename_rules_save_path_for_project(project)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    _fn_rules_cache.pop(project or "", None)

def save_filename_rules(rules, project=None):
    """Save only the rules list, preserving auto_rename."""
    cfg = load_filename_config(project)
    cfg["rules"] = rules
    save_filename_config(cfg, project)

_person_registry_cache = {}  # project_key → (mtime, data)

def load_person_registry(project=None):
    """Returns dict {id_str: description} for a project (or global default)."""
    path = person_registry_file_for_project(project)
    key  = project or ""
    defaults = {"000": "No human/animal"}
    if os.path.exists(path):
        try:
            mtime = os.path.getmtime(path)
            cached = _person_registry_cache.get(key)
            if cached and cached[0] == mtime:
                return cached[1]
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            result = {**defaults, **data}
            _person_registry_cache[key] = (mtime, result)
            return result
        except Exception:
            pass
    return defaults

def save_person_registry(data, project=None):
    _person_registry_cache.pop(project or "", None)
    path = person_registry_file_for_project(project)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True, ensure_ascii=False)


def load_person_aliases():
    """Returns list of groups, each group is a list of linked person ID strings.
    E.g. [["001","005","007"], ["002","003"]] means 001=005=007 and 002=003."""
    if os.path.exists(PERSON_ALIASES_FILE):
        try:
            with open(PERSON_ALIASES_FILE, encoding="utf-8") as f:
                data = json.load(f)
            # validate: list of lists of strings
            if isinstance(data, list):
                return [grp for grp in data if isinstance(grp, list) and len(grp) >= 1]
        except Exception:
            pass
    return []


def save_person_aliases(groups):
    """Save alias groups to disk. Removes empty groups (single-member groups are kept)."""
    cleaned = [sorted(set(g)) for g in groups if len(set(g)) >= 1]
    with open(PERSON_ALIASES_FILE, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, indent=2, ensure_ascii=False)


def get_alias_group(pid, aliases=None):
    """Return set of all person IDs linked to pid (including pid itself)."""
    if aliases is None:
        aliases = load_person_aliases()
    for grp in aliases:
        if pid in grp:
            return set(grp)
    return {pid}


def link_persons(pid_a, pid_b):
    """Link two person IDs as aliases. Merges groups if either already has links."""
    aliases = load_person_aliases()
    group_a = next((grp for grp in aliases if pid_a in grp), None)
    group_b = next((grp for grp in aliases if pid_b in grp), None)
    if group_a is group_b and group_a is not None:
        return  # already in same group
    if group_a is None and group_b is None:
        aliases.append([pid_a, pid_b])
    elif group_a is None:
        group_b.append(pid_a)
    elif group_b is None:
        group_a.append(pid_b)
    else:
        # merge two groups
        merged = list(set(group_a + group_b))
        aliases = [g for g in aliases if g is not group_a and g is not group_b]
        aliases.append(merged)
    save_person_aliases(aliases)


def unlink_persons(pid_a, pid_b):
    """Remove the link between pid_a and pid_b. Other members of the group are kept linked."""
    aliases = load_person_aliases()
    for grp in aliases:
        if pid_a in grp and pid_b in grp:
            grp.remove(pid_b if pid_b in grp else pid_a)
            # also remove pid_a side
            if pid_a in grp:
                grp.remove(pid_a)
            # put pid_a back as singleton group if needed (just drop it — singletons are filtered)
            break
    save_person_aliases(aliases)


def remove_person_from_aliases(pid):
    """Remove a person ID from all alias groups."""
    aliases = load_person_aliases()
    for grp in aliases:
        if pid in grp:
            grp.remove(pid)
    save_person_aliases(aliases)


# ── Right-column groups (unsorted groupings, not used for face matching) ──────

def load_right_groups():
    """Returns ordered list of pid-lists for the right column (singles allowed for ordering)."""
    if os.path.exists(PERSON_RIGHT_GROUPS_FILE):
        try:
            with open(PERSON_RIGHT_GROUPS_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return [g for g in data if isinstance(g, list) and len(g) >= 1]
        except Exception:
            pass
    return []

def save_right_groups(groups):
    """Save right-column groups in display order. Singles allowed (track position)."""
    cleaned = [sorted(set(g)) for g in groups if len(set(g)) >= 1]
    with open(PERSON_RIGHT_GROUPS_FILE, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, indent=2, ensure_ascii=False)

def link_right_group(pid_a, pid_b):
    """Group two pids together in the right column (no face-match pooling)."""
    groups = load_right_groups()
    ga = next((g for g in groups if pid_a in g), None)
    gb = next((g for g in groups if pid_b in g), None)
    if ga is gb and ga is not None:
        return
    if ga is None and gb is None:
        groups.append([pid_a, pid_b])
    elif ga is None:
        gb.append(pid_a)
    elif gb is None:
        ga.append(pid_b)
    else:
        merged = list(set(ga + gb))
        groups = [g for g in groups if g is not ga and g is not gb]
        groups.append(merged)
    save_right_groups(groups)

def remove_from_right_group(pid):
    """Remove a pid from any right-column group."""
    groups = load_right_groups()
    for g in groups:
        if pid in g:
            g.remove(pid)
    save_right_groups(groups)


def detect_tags_from_filename(path, rules, existing_tags=None):
    """Return list of tag keys to add based on filename field rules.
    existing_tags: optional set/list of tags already on the file. Used so
                   tag_group rules don't pile on a second value when the user
                   has already picked one from the same group (e.g. user picks
                   MDL_img_Table=09 and rule says =80; without this check
                   both end up in tags and the matrix shows the wrong one).
                   PATH rules ('/' in pattern) still override — they're
                   explicit user intent and applied via apply_path_rules.
    """
    import fnmatch as _fnmatch
    name      = os.path.basename(path).lower()
    norm_path = path.replace("\\", "/").lower()
    _parent_lc = os.path.basename(os.path.dirname(norm_path))
    _existing = set(existing_tags or [])
    tags = []
    for rule in rules:
        pattern = rule.get("pattern", "").lower()
        if not pattern:
            continue
        pattern_fnmatch = pattern.replace('#', '?')
        target = norm_path if '/' in pattern_fnmatch else name
        if '*' in pattern_fnmatch or '?' in pattern_fnmatch:
            matched = _fnmatch.fnmatch(target,
                f"*{pattern_fnmatch}" if '/' in pattern_fnmatch and not pattern_fnmatch.startswith('*')
                else pattern_fnmatch)
        elif pattern_fnmatch.endswith('/') and pattern_fnmatch.count('/') == 1:
            # "Folder/" → file's immediate parent ends with that name.
            # Suffix match (not equality) so "ob/" still matches parent
            # "apob"; rejects nested subfolders ("apob/" never matches
            # parent "sub" inside apob/sub/<file>).
            _stem = pattern_fnmatch.rstrip('/')
            matched = bool(_stem) and _parent_lc.endswith(_stem)
        else:
            matched = pattern_fnmatch in target
        if not matched:
            continue
        if "tag_group" in rule:
            # Tag group rule: pattern in filename → add tag value to file's tags.
            # Skip if user has already chosen a value from this same group
            # (path rules — '/' patterns — bypass this check; they're handled
            # by apply_path_rules with explicit override semantics).
            grp = rule.get("tag_group", "")
            val = rule.get("value", "").strip()
            if val and val not in tags:
                grp_vals = {v[0] for v in TAG_GROUPS.get(grp, [])
                            if isinstance(v, (list, tuple)) and v}
                is_path_rule = '/' in pattern_fnmatch
                if not is_path_rule and grp_vals and (_existing & grp_vals):
                    # User already has a value from this group — leave it alone
                    continue
                tags.append(val)
        elif "field" in rule:
            # Boolean coded field (digits=0) — add tag matching the field label
            field = rule.get("field", "").upper()
            for _l, _lb, _d in CODED_FIELDS:
                if _l == field and _d == 0:
                    tag_key = _lb.lower()   # e.g. "Watermark" → "watermark"
                    if tag_key and tag_key not in tags:
                        tags.append(tag_key)
                    break
            # Non-boolean coded fields are handled by parse_filename_rules
        else:
            # Legacy tag format
            for t in rule.get("tags", []):
                if t and t not in tags:
                    tags.append(t)
    return tags

def parse_filename_rules(stem, rules, basename=None, fullpath=None, _return_path_flags=False):
    """Extract coded field values from a filename stem using rules.
    Returns dict of field→value, e.g. {"P": "001", "E": "0a"}.
    If _return_path_flags=True, returns (result, path_fields) where path_fields is
    the set of field keys whose winning match came from a path rule (contains '/').
    Supports:
      - Extract rule: {"field": "E", "extract": true, "digits": 2}
        → regex finds E followed by N hex digits in stem
      - Value rule:   {"pattern": "-0.", "field": "P", "value": "001"}
        → substring/glob match against basename; if pattern contains '/' match
          against full path (allows e.g. "nastia/image-*.png" to scope by folder)
    basename: full filename including extension (used for pattern matching).
              Falls back to stem if not provided.
    fullpath: full absolute path; used when pattern contains '/'."""
    import fnmatch as _fnmatch
    name = (basename or stem).lower()   # filename-only target
    norm_path = fullpath.replace("\\", "/").lower() if fullpath else name
    result = {}
    # Track whether the current best match for each field came from a path rule (higher priority)
    _result_is_path = {}  # field -> bool
    for rule in rules:
        if "field" not in rule:
            continue
        if rule.get("extract"):
            field  = rule["field"].upper()
            digits = rule.get("digits", 2)
            m = re.search(rf'(?<![A-Z]){re.escape(field)}([0-9a-f]{{{digits}}})',
                          stem, re.IGNORECASE)
            if m:
                result[rule["field"]] = m.group(1).lower()
                _result_is_path[rule["field"]] = False
        else:
            pattern = rule.get("pattern", "").lower()
            if not pattern:
                continue
            # '#' is an alias for '?' (single-char wildcard) — more intuitive for UUIDs
            pattern_fnmatch = pattern.replace('#', '?')
            is_path_rule = '/' in pattern_fnmatch
            # Path-aware: if pattern contains '/', match against full path
            target = norm_path if is_path_rule else name
            if '*' in pattern_fnmatch or '?' in pattern_fnmatch:
                matched = _fnmatch.fnmatch(target, f"*{pattern_fnmatch}" if is_path_rule and not pattern_fnmatch.startswith('*') else pattern_fnmatch)
            else:
                matched = pattern_fnmatch in target
            if matched:
                field = rule["field"]
                # Path rules always win over filename-only rules for the same field.
                # Within the same priority level, last-match-wins (more specific rules last).
                if field not in result or is_path_rule or not _result_is_path.get(field):
                    result[field] = rule.get("value", "")
                    _result_is_path[field] = is_path_rule
    if _return_path_flags:
        return result, {f for f, is_path in _result_is_path.items() if is_path}
    return result


def load_rename_rules():
    """Load pattern-rename rules from filename_rename_rules.json."""
    if os.path.exists(RENAME_RULES_FILE):
        try:
            with open(RENAME_RULES_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_rename_rules(rules):
    with open(RENAME_RULES_FILE, "w", encoding="utf-8") as f:
        json.dump(rules, f, indent=2, ensure_ascii=False)

def apply_rename_rules(path, rules):
    """Apply pattern-rename rules to a filename.
    Each rule: {"pattern": "-0", "replacement": "001", "position": "front|end|inline"}
    - front:  strip pattern from stem, prepend replacement- at start
    - end:    strip pattern from stem, append -replacement at end
    - inline: replace pattern text with replacement text in stem
    Returns new path (same dir), or original path if no rule matched."""
    if not rules:
        return path
    dir_, base = os.path.split(path)
    stem, ext = os.path.splitext(base)
    new_stem = stem
    for rule in rules:
        pattern     = rule.get("pattern", "")
        replacement = rule.get("replacement", "")
        position    = rule.get("position", "inline")
        if not pattern:
            continue
        regex = re.compile(re.escape(pattern) + r'(?=[-.]|$)', re.IGNORECASE)
        if not regex.search(new_stem):
            continue
        stripped = regex.sub('', new_stem).strip('-')
        if position == "front":
            new_stem = replacement + ('-' + stripped if stripped else '')
        elif position == "end":
            new_stem = (stripped + '-' if stripped else '') + replacement
        else:  # inline
            new_stem = re.sub(re.escape(pattern), replacement, new_stem, flags=re.IGNORECASE)
    if new_stem == stem:
        return path
    return os.path.join(dir_, new_stem + ext)


def unique_path(path):
    """If path already exists, append -001, -002, … until a free name is found."""
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    for i in range(1, 1000):
        candidate = f"{stem}-{i:03d}{ext}"
        if not os.path.exists(candidate):
            return candidate
    return path   # fallback — caller will get an os.rename error


# ── CLIP Auto-Detect ─────────────────────────────────────────────────────────
# Each entry: field key (lowercase), digit position (1=rightmost), zero_is_none
# (True → "0" means unset, skip it; False → "0" is a real value like "Front"),
# confidence threshold, and (code, CLIP description) pairs.
# Only auto-sets a digit if it is currently "0" (zero_is_none=True) or the field
# is completely absent (zero_is_none=False).

CLIP_AUTO_DETECT = [
    # ── Hair color ────────────────────────────────────────────────────────────
    {"field": "hc", "pos": 1, "zero_is_none": True,  "threshold": 0.20, "options": [
        ("1", "a person with black hair"),
        ("2", "a person with dark brown hair"),
        ("3", "a person with light brown hair"),
        ("4", "a person with blonde or golden hair"),
        ("5", "a person with platinum blonde very light hair"),
        ("6", "a person with red hair"),
        ("7", "a person with pink hair"),
        ("8", "a person with ginger or orange hair"),
        ("9", "a person with gray or silver-gray hair"),
        ("a", "a person with white hair"),
        ("b", "a person with blue hair"),
        ("c", "a person with yellow hair"),
        ("d", "a person with green hair"),
        ("e", "a person with rainbow or multi-colored hair"),
        ("f", "a person with neon colored hair"),
    ]},
    # ── Hair style ────────────────────────────────────────────────────────────
    {"field": "hc", "pos": 2, "zero_is_none": True,  "threshold": 0.16, "options": [
        ("1", "a person with flat straight hair with no curl or wave"),
        ("2", "a person with gently wavy or slightly curled hair"),
        ("3", "a person with clearly curly or spiral ringlet hair texture"),
        ("4", "a person with voluminous puffy or afro-style hair"),
        ("5", "a person with bob cut chin-length hair"),
        ("6", "a person with hair tied back in a ponytail"),
        ("7", "a person with braided or dreadlocked hair"),
        ("8", "a person with hair tied up in a bun or topknot"),
        ("9", "a person with a buzzcut or head that is shaved bald"),
    ]},
    # ── Hair length ───────────────────────────────────────────────────────────
    {"field": "hc", "pos": 3, "zero_is_none": True,  "threshold": 0.16, "options": [
        ("1", "a person with a buzzcut shaved head with almost no hair visible"),
        ("2", "a person with very short hair above the ears not reaching the jaw"),
        ("3", "a person with hair ending at or just touching the shoulders"),
        ("4", "a person with long hair clearly past the shoulders reaching mid-back"),
        ("5", "a person with very long hair reaching the waist hips or lower"),
        ("6", "a person who is fully bald with a completely shaved or smooth head and no visible hair"),
        ("7", "a person with partially bald receding hairline or thinning hair on top of the head"),
    ]},
    # ── Face direction ────────────────────────────────────────────────────────
    {"field": "fa", "pos": 1, "zero_is_none": False, "threshold": 0.0,  "options": [
        ("0", "a person facing directly forward toward camera"),
        ("1", "a person facing right full profile side view"),
        ("2", "a person facing slightly right three-quarter view"),
        ("3", "a person facing left full profile side view"),
        ("4", "a person facing slightly left three-quarter view"),
        ("5", "a person facing away from camera showing back of head"),
    ]},
    # ── Face vertical tilt ────────────────────────────────────────────────────
    {"field": "fa", "pos": 2, "zero_is_none": False, "threshold": 0.0,  "options": [
        ("0", "a person with head at normal horizontal level"),
        ("1", "a person with head tilted upward chin raised"),
        ("2", "a person with head bowed or tilted downward"),
    ]},
    # ── Skin type ─────────────────────────────────────────────────────────────
    {"field": "sk", "pos": 1, "zero_is_none": False, "threshold": 0.0,  "options": [
        ("0", "a person with very fair or pale white skin"),
        ("1", "a person with fair light skin tone"),
        ("2", "a person with medium beige or light tan skin"),
        ("3", "a person with olive or medium brown skin"),
        ("4", "a person with dark brown skin"),
        ("5", "a person with very dark deeply pigmented skin"),
    ]},
    # ── Posture ───────────────────────────────────────────────────────────────
    {"field": "pm", "pos": 2, "zero_is_none": True, "default_is_zero": True, "threshold": 0.20, "options": [
        ("0", "a person standing upright on both feet"),
        ("2", "a person sitting down on a chair or floor"),
        ("3", "a person kneeling on one or both knees"),
        ("4", "a person lying down horizontally"),
        ("5", "a person leaning against a wall or surface"),
        ("6", "a person crouching or squatting down"),
        ("7", "a person doing a handstand upside down"),
    ]},
    # ── Motion ────────────────────────────────────────────────────────────────
    {"field": "pm", "pos": 1, "zero_is_none": True, "default_is_zero": True, "threshold": 0.22, "options": [
        ("0", "a person posing still not moving"),
        ("2", "a person walking"),
        ("3", "a person running"),
        ("4", "a person dancing"),
        ("5", "a person looking directly at the camera"),
        ("6", "a person talking or speaking with mouth open"),
        ("7", "a person gesturing with their hands"),
        ("8", "a person fighting or in combat action pose"),
    ]},
    # ── Shot type ─────────────────────────────────────────────────────────────
    {"field": "cs", "pos": 3, "zero_is_none": True,  "threshold": 0.18, "options": [
        ("1", "extreme close-up shot of eyes lips or small facial detail only"),
        ("2", "close-up shot showing only the face tightly framed"),
        ("3", "big close-up showing face and very top of shoulders"),
        ("4", "close-up portrait showing face and shoulders"),
        ("5", "bust shot showing head and chest"),
        ("6", "medium close-up showing head to upper chest"),
        ("7", "medium shot showing person from waist up"),
        ("8", "cowboy shot showing person from mid-thigh up"),
        ("9", "full body shot showing entire person head to toe"),
        ("a", "wide shot showing person and surrounding environment"),
        ("b", "extreme wide shot with small distant figure in large environment"),
    ]},
    # ── Camera angle ─────────────────────────────────────────────────────────
    {"field": "cs", "pos": 2, "zero_is_none": True, "default_is_zero": True, "threshold": 0.22, "options": [
        ("0", "straight eye-level shot with camera at subject's eye height facing forward"),
        ("1", "low angle shot looking upward at the subject"),
        ("2", "high angle shot looking downward at the subject"),
        ("3", "over-the-shoulder shot from behind a person"),
        ("4", "dutch angle or tilted camera creating strong diagonal"),
        ("5", "bird's eye view shot directly from overhead above"),
    ]},
    # ── Lighting ─────────────────────────────────────────────────────────────
    {"field": "cs", "pos": 1, "zero_is_none": True, "default_is_zero": True, "threshold": 0.20, "options": [
        ("0", "natural ambient light no artificial setup"),
        ("1", "bright sunny daylight outdoor lighting"),
        ("2", "warm golden sunset or sunrise lighting"),
        ("3", "clean professional studio lighting white background"),
        ("4", "dramatic cinematic lighting with strong shadows and contrast"),
        ("5", "flat colorful anime or illustration style"),
        ("6", "dark nighttime or very low-light scene"),
    ]},
    # ── Background major ─────────────────────────────────────────────────────
    {"field": "bg", "pos": 3, "zero_is_none": False, "threshold": 0.20, "options": [
        ("0", "solid pure black background no details"),
        ("1", "solid pure white background no details"),
        ("2", "bright green screen or chromakey green background"),
        ("3", "indoor room or interior home setting"),
        ("4", "commercial indoor location restaurant office store cafe"),
        ("5", "outdoor urban street city buildings"),
        ("6", "natural outdoor setting trees grass forest field beach water"),
        ("8", "outer space stars cosmos planets"),
    ]},
    # ── Expression family (first digit — AI detects x0 baseline of each family) ─
    {"field": "x", "pos": 2, "zero_is_none": True,  "threshold": 0.18, "options": [
        ("0", "a person with a neutral blank expressionless face"),
        ("1", "a person smiling or laughing with a happy expression"),
        ("2", "a person sneering or showing contempt disgust"),
        ("3", "a person crying or looking sad with tears"),
        ("4", "a person frowning or looking angry displeased"),
        ("5", "a person looking surprised or shocked with wide eyes"),
        ("6", "a person looking scared or fearful frightened"),
        ("7", "a person looking sleepy tired or drowsy with heavy eyelids"),
        ("8", "a person with a flirty seductive coy expression"),
        ("9", "a person looking shy bashful or embarrassed"),
        ("a", "a person with a mischievous impish smirk"),
        ("b", "a person grimacing wincing in pain"),
        ("c", "a person with an intense fierce dramatic stare"),
    ]},
    # ── Eye color ─────────────────────────────────────────────────────────────
    {"field": "e", "pos": 1, "zero_is_none": True,  "threshold": 0.18, "options": [
        ("1", "a person with brown eyes"),
        ("2", "a person with blue eyes"),
        ("3", "a person with hazel green-brown eyes"),
        ("4", "a person with amber or golden yellow eyes"),
        ("5", "a person with gray eyes"),
        ("6", "a person with green eyes"),
        ("7", "a person with purple or violet eyes"),
        ("8", "a person with red or pink eyes"),
        ("9", "a person with silver metallic eyes"),
        ("a", "a person with very dark black eyes"),
    ]},
    # ── Clothing — Top type (pos 3) ───────────────────────────────────────────
    {"field": "cl", "pos": 3, "zero_is_none": True, "threshold": 0.15, "options": [
        ("1", "a person who is topless without any top garment"),
        ("2", "a person wearing a t-shirt"),
        ("3", "a person wearing a blouse or button-up shirt"),
        ("4", "a person wearing a sweater or knit pullover"),
        ("5", "a person wearing a tank top or crop top"),
        ("6", "a person wearing a hoodie"),
        ("7", "a person wearing a jacket"),
        ("8", "a person wearing a coat or outerwear"),
        ("9", "a person wearing the upper part of a dress"),
        ("a", "a person wearing lingerie or a bra"),
        ("b", "a person wearing a swimsuit top or bikini top"),
        ("c", "a person wearing a kimono or yukata top"),
        ("d", "a person wearing a school uniform top"),
        ("e", "a person wearing a costume top"),
    ]},
    # ── Clothing — Top color (pos 4) ──────────────────────────────────────────
    {"field": "cl", "pos": 4, "zero_is_none": True, "threshold": 0.14, "options": [
        ("1", "a person whose top is bare skin or no fabric color"),
        ("2", "a person wearing a black colored top"),
        ("3", "a person wearing a white colored top"),
        ("4", "a person wearing a red colored top"),
        ("5", "a person wearing a blue colored top"),
        ("6", "a person wearing a green colored top"),
        ("7", "a person wearing a yellow colored top"),
        ("8", "a person wearing a pink colored top"),
        ("9", "a person wearing a purple colored top"),
        ("a", "a person wearing an orange colored top"),
        ("b", "a person wearing a brown colored top"),
        ("c", "a person wearing a gray colored top"),
        ("d", "a person wearing a beige tan colored top"),
        ("e", "a person wearing a multi-colored or patterned top"),
    ]},
    # ── Clothing — Bottom type (pos 1) ────────────────────────────────────────
    {"field": "cl", "pos": 1, "zero_is_none": True, "threshold": 0.15, "options": [
        ("1", "a person with no bottom garment bare lower body"),
        ("2", "a person wearing jeans denim pants"),
        ("3", "a person wearing trousers or slacks"),
        ("4", "a person wearing shorts"),
        ("5", "a person wearing a mini skirt"),
        ("6", "a person wearing a long skirt"),
        ("7", "a person wearing leggings or yoga pants"),
        ("8", "a person wearing sweatpants or joggers"),
        ("9", "a person wearing a full length dress"),
        ("a", "a person wearing panties or underwear bottom"),
        ("b", "a person wearing a bikini bottom or swim trunks"),
        ("c", "a person wearing hakama or kimono bottom"),
        ("d", "a person wearing a school skirt or school pants"),
        ("e", "a person wearing stockings or tights"),
    ]},
    # ── Clothing — Bottom color (pos 2) ───────────────────────────────────────
    {"field": "cl", "pos": 2, "zero_is_none": True, "threshold": 0.14, "options": [
        ("1", "a person whose bottom is bare skin or no fabric color"),
        ("2", "a person wearing black colored bottoms"),
        ("3", "a person wearing white colored bottoms"),
        ("4", "a person wearing red colored bottoms"),
        ("5", "a person wearing blue colored bottoms"),
        ("6", "a person wearing green colored bottoms"),
        ("7", "a person wearing yellow colored bottoms"),
        ("8", "a person wearing pink colored bottoms"),
        ("9", "a person wearing purple colored bottoms"),
        ("a", "a person wearing orange colored bottoms"),
        ("b", "a person wearing brown colored bottoms"),
        ("c", "a person wearing gray colored bottoms"),
        ("d", "a person wearing beige tan colored bottoms"),
        ("e", "a person wearing multi-colored or patterned bottoms"),
    ]},
]

CLIP_LABELS_FILE = os.path.join(_DATA_DIR, "clip_labels.json")

_CLIP_AUTO_DETECT_DEFAULTS = CLIP_AUTO_DETECT  # keep defaults reference

def load_clip_labels():
    """Load CLIP label overrides from clip_labels.json.
    Merges file contents over defaults so new (field, pos) entries added in
    code (e.g. CL) appear automatically without forcing a delete-and-resave."""
    try:
        if os.path.exists(CLIP_LABELS_FILE):
            with open(CLIP_LABELS_FILE, encoding="utf-8") as f:
                data = json.load(f)
            for spec in data:
                spec["options"] = [tuple(o) for o in spec["options"]]
            seen = {(s["field"].lower(), s["pos"]) for s in data}
            for default_spec in _CLIP_AUTO_DETECT_DEFAULTS:
                key = (default_spec["field"].lower(), default_spec["pos"])
                if key not in seen:
                    data.append(dict(default_spec))
            return data
    except Exception:
        pass
    return list(_CLIP_AUTO_DETECT_DEFAULTS)

def save_clip_labels(specs):
    """Save CLIP label specs to clip_labels.json and invalidate the cache."""
    global CLIP_AUTO_DETECT, _clip_label_cache
    # Serialise options as lists (JSON doesn't support tuples)
    out = []
    for spec in specs:
        s = dict(spec)
        s["options"] = [list(o) for o in spec["options"]]
        out.append(s)
    with open(CLIP_LABELS_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    CLIP_AUTO_DETECT = specs
    _clip_label_cache = None  # force cache rebuild on next detection

# Apply saved labels on module load
CLIP_AUTO_DETECT = load_clip_labels()

_clip_label_cache = None   # cached text embeddings, built on first use


def _get_clip_label_cache():
    """Build and cache text embeddings for all CLIP_AUTO_DETECT specs."""
    global _clip_label_cache
    if _clip_label_cache is not None:
        return _clip_label_cache
    try:
        import aisearch_logic as _logic
        cache = []
        for spec in CLIP_AUTO_DETECT:
            texts = [opt[1] for opt in spec["options"]]
            embs = _logic.model.encode(texts, convert_to_tensor=True).to(_logic.device)
            cache.append(embs)
        _clip_label_cache = cache
        return cache
    except Exception:
        return None


# ── Correction-based detection ────────────────────────────────────────────────
# Stores embeddings from manually baked images as labeled examples.
# On detection, nearest-neighbor lookup overrides text-similarity when a very
# similar image (cosine ≥ threshold) has a confirmed label.

_corrections_cache = {}  # project_key → list[dict]


def _corrections_file(project):
    key = project if (project and project != "default") else "default"
    name = f"corrections_{key}.pt" if key != "default" else "corrections.pt"
    return os.path.join(_DATA_DIR, name)


def load_corrections(project):
    key = project or "default"
    if key in _corrections_cache:
        return _corrections_cache[key]
    fpath = _corrections_file(project)
    if not os.path.exists(fpath):
        _corrections_cache[key] = []
        return []
    try:
        import torch
        data = torch.load(fpath, weights_only=False)
        result = data if isinstance(data, list) else []
        _corrections_cache[key] = result
        return result
    except Exception:
        _corrections_cache[key] = []
        return []


def _save_corrections(project, corrections):
    try:
        import torch
        torch.save(corrections, _corrections_file(project))
        _corrections_cache[project or "default"] = corrections
    except Exception:
        pass


def add_correction(project, path_key, image_emb, coded_entry):
    """Record coded field values from a baked entry as labeled examples for future detection.
    Called after a successful bake. Re-baking the same path updates its examples."""
    try:
        import torch
        corrections = load_corrections(project)
        corrections = [c for c in corrections if c.get("path") != path_key]
        emb = image_emb
        if hasattr(emb, "dim") and emb.dim() > 1:
            emb = emb.squeeze(0)
        emb = emb.cpu()
        field_digits = {cf[0].lower(): cf[2] for cf in CODED_FIELDS if cf[2] > 0}
        for spec in CLIP_AUTO_DETECT:
            field = spec["field"]
            pos   = spec["pos"]
            zero_is_none = spec.get("zero_is_none", True)
            digits = field_digits.get(field, 1)
            val = coded_entry.get(field, "")
            if not val:
                continue
            val_padded = val.zfill(digits)
            digit = val_padded[-pos] if pos <= len(val_padded) else "0"
            if zero_is_none and digit == "0":
                continue
            corrections.append({"path": path_key, "field": field, "pos": pos,
                                 "value": digit, "emb": emb})
        _save_corrections(project, corrections)
    except Exception:
        pass


def detect_from_corrections(image_emb, corrections, field, pos, threshold=0.92):
    """Return correction-based detection value or None.
    Only fires when a stored example is very similar (cosine ≥ threshold)."""
    relevant = [c for c in corrections if c["field"] == field and c["pos"] == pos]
    if not relevant:
        return None
    try:
        import torch
        emb = image_emb
        if hasattr(emb, "dim") and emb.dim() > 1:
            emb = emb.squeeze(0)
        embs = torch.stack([c["emb"] for c in relevant])
        sims = torch.nn.functional.cosine_similarity(emb.unsqueeze(0), embs)
        best_idx = int(sims.argmax())
        if float(sims[best_idx]) >= threshold:
            return relevant[best_idx]["value"]
    except Exception:
        pass
    return None


def auto_detect_clip_attrs(image_emb, existing_entry, allowed_fields=None, project=None):
    """Use CLIP to auto-detect coded field values not already set.
    image_emb: 1-D tensor from logic.extract_feature().
    existing_entry: current attrs dict for the file (may be empty).
    allowed_fields: optional set of lowercase field names to detect (e.g. {"hc","fa","sk"}).
                    If None, all CLIP_AUTO_DETECT fields are run (legacy behaviour).
    project: project name for loading correction examples.
    Returns {field_lower: new_hex} for any fields that were updated."""
    try:
        from sentence_transformers import util as _stutil
    except ImportError:
        return {}

    cache = _get_clip_label_cache()
    if cache is None:
        return {}

    # Build field → total_digits map from CODED_FIELDS
    field_digits_map = {cf[0].lower(): cf[2] for cf in CODED_FIELDS if cf[2] > 0}

    working = {}  # field → hex string being assembled
    detected_fields = set()  # fields where at least one digit was detected

    def _get_working(field):
        if field not in working:
            digits = field_digits_map.get(field, 2)
            cur = existing_entry.get(field, "") or ""
            working[field] = cur.zfill(digits) if cur else "0" * digits
        return working[field]

    emb = image_emb
    if hasattr(emb, "dim") and emb.dim() == 1:
        emb = emb.unsqueeze(0)

    corrections = load_corrections(project) if project else []

    for i, spec in enumerate(CLIP_AUTO_DETECT):
        field       = spec["field"]
        if allowed_fields is not None and field not in allowed_fields:
            continue
        pos         = spec["pos"]
        zero_is_none    = spec.get("zero_is_none", True)
        default_is_zero = spec.get("default_is_zero", False)
        threshold       = spec.get("threshold", 0.20)
        options         = spec["options"]

        current = _get_working(field)
        cur_digit = current[-pos] if pos <= len(current) else "0"

        # Skip if already set by user — don't overwrite manual corrections
        if zero_is_none or default_is_zero:
            if cur_digit != "0":
                continue
        else:
            if field in existing_entry and existing_entry[field]:
                continue

        # Correction-based detection: use nearest baked example if very similar
        if corrections:
            corr_val = detect_from_corrections(emb.squeeze(0), corrections, field, pos)
            if corr_val is not None:
                val = list(_get_working(field))
                val[-pos] = corr_val
                working[field] = "".join(val)
                detected_fields.add(field)
                continue

        # Score image against all option texts
        text_embs = cache[i]
        scores    = _stutil.cos_sim(emb, text_embs)[0]
        best_idx  = int(scores.argmax())
        best_score = float(scores[best_idx])

        best_code = options[best_idx][0]
        if best_score < threshold:
            # For default_is_zero fields (e.g. PM posture standing), store "0" even below threshold
            if not (default_is_zero and best_code == "0"):
                continue

        if zero_is_none and not default_is_zero and best_code == "0":
            continue   # classified as "none" — leave unset

        # Write the detected digit into the working hex string
        val = list(current)
        val[-pos] = best_code
        working[field] = "".join(val)
        detected_fields.add(field)

    # Return only fields that actually changed from original
    # For zero_is_none=False fields (FA/SK/BG), "0" is a valid detection — include even if all zeros
    result = {}
    for field, new_val in working.items():
        digits = field_digits_map.get(field, 2)
        orig = (existing_entry.get(field, "") or "").zfill(digits) or "0" * digits
        if new_val != orig and new_val != "0" * digits:
            result[field] = new_val
        elif field in detected_fields and not existing_entry.get(field):
            # First-time detection produced all-zero result (e.g. FA "00" = facing forward)
            result[field] = new_val
    return result


def inspect_clip_scores(image_emb):
    """Return raw CLIP scores for all CLIP_AUTO_DETECT specs.
    Returns list of dicts per spec: {field, pos, threshold, options: [(code, label, score)]}"""
    try:
        from sentence_transformers import util as _stutil
        import torch as _torch
    except ImportError:
        return []
    cache = _get_clip_label_cache()
    if cache is None:
        return []
    emb = image_emb
    if hasattr(emb, "dim") and emb.dim() == 1:
        emb = emb.unsqueeze(0)
    # torch.no_grad — same reason as extract_feature: prevent autograd buildup
    _ng = _torch.no_grad()
    _ng.__enter__()
    results = []
    for i, spec in enumerate(CLIP_AUTO_DETECT):
        text_embs = cache[i]
        scores = _stutil.cos_sim(emb, text_embs)[0]
        opts = [(code, label, float(scores[j]))
                for j, (code, label) in enumerate(spec["options"])]
        opts_sorted = sorted(opts, key=lambda x: x[2], reverse=True)
        best_score = opts_sorted[0][2] if opts_sorted else 0.0
        threshold = spec.get("threshold", 0.20)
        _top_code = opts_sorted[0][0] if opts_sorted else None
        if best_score >= threshold:
            winner = _top_code
        elif spec.get("default_is_zero", False) and _top_code == "0":
            winner = "0"  # store default even below threshold
        else:
            winner = None
        if spec.get("zero_is_none", True) and not spec.get("default_is_zero", False) and winner == "0":
            winner = None
        results.append({
            "field": spec["field"].upper(),
            "pos": spec["pos"],
            "threshold": threshold,
            "zero_is_none": spec.get("zero_is_none", True),
            "options": opts_sorted,
            "winner": winner,
        })
    _ng.__exit__(None, None, None)
    return results


_clip_pool_lock = _threading.Lock()
_clip_pool_state = {"proc": None, "calls": 0, "max_calls": 30,
                    "startup_timeout": 30.0, "call_timeout": 30.0}

def _clip_pool_spawn():
    """Start a persistent clip worker subprocess and wait for its READY line."""
    import subprocess as _sp
    _worker = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "clip_worker_persistent.py")
    if not os.path.exists(_worker):
        return None
    proc = _sp.Popen(
        [sys.executable, "-u", _worker],
        stdin=_sp.PIPE, stdout=_sp.PIPE, stderr=_sp.DEVNULL,
        text=True, bufsize=1)
    # Wait for "ready" line (model loaded). Bounded by startup_timeout.
    import select as _sel
    _deadline = _time.time() + _clip_pool_state["startup_timeout"]
    line = ""
    while _time.time() < _deadline:
        r, _, _ = _sel.select([proc.stdout], [], [], _deadline - _time.time())
        if r:
            line = proc.stdout.readline()
            break
    if not line:
        try: proc.kill()
        except Exception: pass
        return None
    try:
        msg = json.loads(line.strip())
        if not msg.get("ready"):
            try: proc.kill()
            except Exception: pass
            return None
    except Exception:
        try: proc.kill()
        except Exception: pass
        return None
    return proc

def _clip_pool_kill():
    """Terminate the current worker (called on recycle / shutdown)."""
    proc = _clip_pool_state.get("proc")
    if proc is None:
        return
    try:
        proc.stdin.write(json.dumps({"cmd": "exit"}) + "\n")
        proc.stdin.flush()
    except Exception:
        pass
    try:
        proc.wait(timeout=2)
    except Exception:
        try: proc.kill()
        except Exception: pass
    _clip_pool_state["proc"] = None
    _clip_pool_state["calls"] = 0

def inspect_clip_scores_subprocess(path, timeout=None):
    """Run CLIP scoring via a persistent worker subprocess. The worker
    keeps the CLIP model loaded so per-call latency is ~50-100ms instead
    of the ~8s cold-start that one-shot subprocesses cost. Worker is
    auto-recycled every N calls to bound any torch/CUDA state leaks.

    AISEARCH_INPROC_CLIP=1 forces in-process for debugging.
    """
    if os.environ.get("AISEARCH_INPROC_CLIP"):
        try:
            import aisearch_logic as _lg
            emb = _lg.extract_feature(path)
            return inspect_clip_scores(emb) if emb is not None else []
        except Exception:
            return []
    _to = timeout if timeout is not None else _clip_pool_state["call_timeout"]
    with _clip_pool_lock:
        proc = _clip_pool_state.get("proc")
        # Lazy spawn or recycle when call count crosses the threshold
        if proc is None or proc.poll() is not None:
            if proc is not None:
                _clip_pool_state["proc"] = None
                _clip_pool_state["calls"] = 0
            proc = _clip_pool_spawn()
            if proc is None:
                # Spawn failed — fall back to in-process so search/inspect
                # still works even if the worker is broken.
                try:
                    import aisearch_logic as _lg
                    emb = _lg.extract_feature(path)
                    return inspect_clip_scores(emb) if emb is not None else []
                except Exception:
                    return []
            _clip_pool_state["proc"] = proc
            _clip_pool_state["calls"] = 0
        if _clip_pool_state["calls"] >= _clip_pool_state["max_calls"]:
            _clip_pool_kill()
            proc = _clip_pool_spawn()
            if proc is None:
                return []
            _clip_pool_state["proc"] = proc
        # Send request
        try:
            proc.stdin.write(json.dumps({"path": path}) + "\n")
            proc.stdin.flush()
        except Exception:
            _clip_pool_kill()
            return []
        # Read response with timeout
        import select as _sel
        _deadline = _time.time() + _to
        line = ""
        while _time.time() < _deadline:
            r, _, _ = _sel.select([proc.stdout], [], [], _deadline - _time.time())
            if r:
                line = proc.stdout.readline()
                break
        if not line:
            # Timeout — kill the worker so it can be respawned next call
            _clip_pool_kill()
            return []
        _clip_pool_state["calls"] += 1
        try:
            out = json.loads(line.strip())
        except Exception:
            return []
        return out.get("specs") or []


def inspect_face_detection_subprocess(path, project, timeout=30):
    """Run inspect_face_detection in a worker subprocess.
    Isolates dlib/face_recognition leaks — each call gets a fresh process
    whose memory the OS fully reclaims on exit. The leak that was crashing
    the main app at high RSS no longer compounds across calls.

    Slower than the in-process version (~300ms process spawn + import) but
    bounded — fine for the auto-inspect debounced path which only fires
    when the user pauses on a file. AISEARCH_INPROC_FACE=1 forces the
    in-process call (fallback for debugging).
    """
    if os.environ.get("AISEARCH_INPROC_FACE"):
        return inspect_face_detection(path, project)
    import subprocess as _sp
    _worker = os.path.join(os.path.dirname(os.path.abspath(__file__)), "face_worker.py")
    if not os.path.exists(_worker):
        return inspect_face_detection(path, project)
    try:
        _py = sys.executable
        r = _sp.run(
            [_py, _worker, "--path", path, "--project", str(project)],
            capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            return {"face_found": False, "num_faces": 0, "matches": [],
                    "assigned_id": None,
                    "error": f"worker rc={r.returncode}: {r.stderr.strip()[:200]}"}
        return json.loads(r.stdout)
    except _sp.TimeoutExpired:
        return {"face_found": False, "num_faces": 0, "matches": [],
                "assigned_id": None, "error": f"worker timeout ({timeout}s)"}
    except Exception as e:
        return {"face_found": False, "num_faces": 0, "matches": [],
                "assigned_id": None, "error": f"worker spawn failed: {e}"}


def inspect_face_detection(path, project):
    """Return raw face detection info for a file.
    Returns dict: {face_found, num_faces, matches: [(pid, similarity)],
                   assigned_id, secondaries: [pid, ...], error}
    The biggest face (by bounding-box area) is treated as the primary and its
    DB matches go in `matches` / `assigned_id`. Smaller faces that confidently
    match a known person become `secondaries` (used for PW / persons_with).
    Unmatched secondary faces are NOT auto-registered — that would inflate
    the registry with strangers and extras."""
    result = {"face_found": False, "num_faces": 0, "matches": [],
              "assigned_id": None, "secondaries": []}
    try:
        import face_recognition
        import numpy as np
        _ext = os.path.splitext(path)[1].lower()
        _VID = (".mp4", ".mkv", ".mov", ".m4v", ".avi", ".webm", ".wmv")
        if _ext in _VID:
            cap = cv2.VideoCapture(path)
            _ok, _frame = cap.read()
            cap.release()
            if not _ok or _frame is None:
                result["error"] = "could not decode first frame of video"
                return result
            img = cv2.cvtColor(_frame, cv2.COLOR_BGR2RGB)
        else:
            img = face_recognition.load_image_file(path)
        with _face_lock:
            locations = face_recognition.face_locations(img)
            encodings = face_recognition.face_encodings(img, known_face_locations=locations)
        result["num_faces"] = len(encodings)
        if not encodings:
            return result
        result["face_found"] = True

        # Sort faces by bounding-box area (biggest first). face_locations gives
        # (top, right, bottom, left); encodings list is in the same order.
        def _area(loc):
            t, r, b, l = loc
            return max(0, b - t) * max(0, r - l)
        order = sorted(range(len(encodings)), key=lambda i: _area(locations[i]) if i < len(locations) else 0,
                       reverse=True)
        encs_sorted = [encodings[i] for i in order]

        db = load_faces_db(project)
        faces = db.get("faces", {})
        aliases = load_person_aliases()

        def _match_one(enc):
            """Return list of (pid, similarity) for a single encoding, sorted desc."""
            ms = []
            seen_groups = set()
            for fid, fdata in faces.items():
                group = frozenset(get_alias_group(fid, aliases))
                if group in seen_groups:
                    continue
                seen_groups.add(group)
                embs = fdata.get("embeddings", [])
                if not embs and fdata.get("embedding"):
                    embs = [fdata["embedding"]]
                for gid in group:
                    if gid != fid:
                        gdata = faces.get(gid, {})
                        ge = gdata.get("embeddings", [])
                        if not ge and gdata.get("embedding"):
                            ge = [gdata["embedding"]]
                        embs.extend(ge)
                if not embs:
                    continue
                np_embs = np.array(embs)
                dists = face_recognition.face_distance(np_embs, enc)
                min_dist = float(np.min(dists))
                ms.append((fid, round(1.0 - min_dist, 3)))
            ms.sort(key=lambda x: x[1], reverse=True)
            return ms

        # Primary face — biggest box. Existing callers consume `matches` and
        # `assigned_id` from this one.
        primary_matches = _match_one(encs_sorted[0])
        result["matches"] = primary_matches[:10]
        if primary_matches and primary_matches[0][1] >= 0.35:
            result["assigned_id"] = primary_matches[0][0]

        # Secondaries — only emit IDs that match KNOWN persons confidently.
        # Don't include the primary's assigned ID (would duplicate P into PW).
        primary_pid = result["assigned_id"]
        secondaries = []
        for enc in encs_sorted[1:]:
            ms = _match_one(enc)
            if ms and ms[0][1] >= 0.35:
                pid = ms[0][0]
                if pid != primary_pid and pid not in secondaries:
                    secondaries.append(pid)
        result["secondaries"] = secondaries
    except Exception as e:
        result["error"] = str(e)
    return result


def update_path_in_all_stores(old_path, new_path, project):
    """Update old_path → new_path in all on-disk data stores for a project:
    - faces_<project>.json  (source_path field)
    - dups_<project>_*.json (path field inside each group entry)
    Safe to call even if files don't exist or project is None.
    For bulk renames during a scan use flush_path_renames_to_stores() instead."""
    if not project or old_path == new_path:
        return
    flush_path_renames_to_stores({old_path: new_path}, project)


def flush_path_renames_to_stores(renames, project, update_clip_pt=True):
    """Apply a batch of path renames to all on-disk stores in one pass.
    renames: dict of {old_path: new_path}
    Dups JSON is intentionally NOT patched — dups are a point-in-time snapshot
    and go stale the moment any file moves; the dup view handles missing paths."""
    if not project or not renames:
        return

    # faces DB — one load/save
    db = load_faces_db(project)
    _changed = False
    for _fdata in db.get("faces", {}).values():
        sp = _fdata.get("source_path")
        if sp in renames:
            _fdata["source_path"] = renames[sp]
            _changed = True
    if _changed:
        save_faces_db(project, db)

    # CLIP .pt — patch paths list in-place (fast: just string replacements, tensor unchanged)
    if update_clip_pt:
        _pt_path = os.path.join(_DATA_DIR, f"features_{project}.pt")
        if os.path.exists(_pt_path):
            try:
                import torch as _torch
                _pt = _torch.load(_pt_path, map_location="cpu", weights_only=False)
                _paths = _pt.get("paths", [])
                _changed_pt = False
                for _i, _p in enumerate(_paths):
                    if _p in renames:
                        _paths[_i] = renames[_p]
                        _changed_pt = True
                if _changed_pt:
                    _pt["paths"] = _paths
                    _torch.save(_pt, _pt_path)
            except Exception:
                pass


def rename_with_person_id(attrs_data, path, pid, flush_stores=True, project=None,
                          skip_uncoded=False):
    """Update the person ID in the file's coded filename stem and rename it on disk.
    Preserves all other coded fields (O, R, J, etc.).
    flush_stores=True (default): immediately update faces/dups stores (single rename).
    flush_stores=False: skip store flush — caller must call flush_path_renames_to_stores
                        with the collected renames dict after the batch is done.
    skip_uncoded=False (default): rename even files whose current name isn't in
                        coded format — they'll be given a minimal coded stem like
                        'P{pid}J{julian}'. Pass True only if a specific caller
                        wants to leave uncoded files untouched.
    Returns the new path (same as path if no rename was needed or rename failed)."""
    stem, ext = os.path.splitext(os.path.basename(path))
    parts = parse_coded_filename(stem)
    if parts is None:
        if skip_uncoded:
            return path   # regular filename — never rename unless auto_rename is on
        # Not a coded filename — build a minimal one: P{pid}J{j_code}
        j_code = julian_id_for_file(path)
        new_stem = f"P{pid}J{j_code}"
    else:
        current_persons = parts.get("persons", [])
        if current_persons and current_persons[0] == pid and parts.get("j"):
            return path   # already correct — nothing to do
        if not parts.get("j"):
            parts["j"] = julian_id_for_file(path)  # stamp creation date if not already present
        parts["persons"] = [pid] + current_persons[1:]   # keep secondary persons
        new_stem = build_coded_filename(parts, field_order=get_sync_field_order(project))
        if not new_stem:
            return path

    new_path = unique_path(os.path.join(os.path.dirname(path), new_stem + ext))
    if new_path == path:
        return path
    try:
        os.rename(path, new_path)
    except Exception:
        return path
    # Move attrs entry to new key
    if path in attrs_data:
        attrs_data[new_path] = attrs_data.pop(path)
    if flush_stores and project:
        update_path_in_all_stores(path, new_path, project)
    return new_path


def _entry_value_for_letter(entry, letter, label):
    """Return the entry's stored value for a CODED_FIELDS letter, checking
    every key the codebase has used over time. Different storage shapes:
      - matrix sections (A/X) — uppercase letter is the section/widget key
      - matrix sections w/ label name (T→Tool, BG→Background) — label key
      - dig fields (E/HC/FA/SK/B/WH/PM/CL/CS/O/R/K) — lowercase letter
      - cf_<letter> (auto-detected from CLIP / metadata)
    Matrix-style keys are checked FIRST so the user's current widget pick
    wins over a stale lowercase value left over from filename parsing.
    Without this, picking Ocean (Background=42) was getting overridden by
    the old filename-parsed bg='200' and writing BG042 instead of BG42."""
    lk = letter.lower()
    for _key in (letter, label, lk, f"cf_{lk}"):
        if _key:
            _v = (entry.get(_key) or "")
            if isinstance(_v, str) and _v.strip():
                return _v.strip().lower()
    return ""


def would_rename(attrs_data, path, project=None):
    """Return True if sync_filename_from_entry would rename the file.
    Used by preview UI to color the Rename button: yellow when pending."""
    entry = get(attrs_data, path)
    if not entry:
        return False
    stem, _ext = os.path.splitext(os.path.basename(path))
    parts = parse_coded_filename(stem)
    if parts is None:
        parts = {"persons": [], "persons_with": [],
                 "j": julian_id_for_file(path)}

    pid = (entry.get("person_id") or "").strip().lower()
    if pid and pid != "000":
        parts["persons"] = [pid] + parts.get("persons", [])[1:]
    pws = [p for p in (entry.get("persons_with") or []) if p]
    if pws:
        parts["persons_with"] = pws

    _changed_field = False
    for letter, label, digits in CODED_FIELDS:
        if letter == "J" or digits == 0:
            continue
        lk = letter.lower()
        v = _entry_value_for_letter(entry, letter, label)
        if v and parts.get(lk, "") != v:
            parts[lk] = v
            _changed_field = True

    if not _changed_field and not pid and not pws:
        return False
    if not parts.get("j"):
        parts["j"] = julian_id_for_file(path)
    date_first = not bool(parts.get("persons"))
    new_stem = build_coded_filename(parts, date_first=date_first,
                                    field_order=get_sync_field_order(project))
    return bool(new_stem) and new_stem != stem


_FLAG_OFF_VALUES = frozenset(("", "0", "false", "no", "off", "none"))


def _bool_flag_on(entry_val):
    """Interpret an entry's boolean-flag value: treat 'WM'/'true'/'1' as on,
    ''/'false'/'0' as off."""
    if not isinstance(entry_val, str):
        return bool(entry_val)
    return entry_val.strip().lower() not in _FLAG_OFF_VALUES


def rename_file_to_match_entry(attrs_data, path, project=None, defer_save=False):
    """The single rename function. Reads entry's canonical lowercase keys
    for every CODED_FIELDS letter (plus person_id, persons_with), rebuilds
    the stem via build_coded_filename, and renames the file on disk.

    Replaces sync_filename_from_entry, apply_tag_sync_rules,
    apply_boolean_sync_rules, rename_with_person_id, rename_to_date_first.
    Boolean flags and per-field values flow through the same path here.

    defer_save: when True, do NOT write attrs_data to disk after the rename.
      Batch callers (Re-apply Rules) pass this and call save() once at the
      end so a 1000-file run isn't 1000 round-trips through json.dump.

    Returns new path (same as path if unchanged or rename failed)."""
    entry = get(attrs_data, path)
    if not entry:
        return path
    stem, ext = os.path.splitext(os.path.basename(path))
    parts = parse_coded_filename(stem)
    if parts is None:
        parts = {"persons": [], "persons_with": [],
                 "j": julian_id_for_file(path)}

    # Persons / persons_with — entry wins.
    pid = (entry.get("person_id") or "").strip().lower()
    if pid and pid != "000":
        parts["persons"] = [pid] + parts.get("persons", [])[1:]
    pws = [p for p in (entry.get("persons_with") or []) if p]
    if pws:
        parts["persons_with"] = pws

    # Every coded field — entry's canonical lowercase key is the source.
    # Backward-compat fallbacks (matrix uppercase, label, cf_) handled by
    # _entry_value_for_letter for entries that haven't been migrated yet.
    for letter, label, digits in CODED_FIELDS:
        if letter == "J":
            continue
        lk = letter.lower()
        if digits == 0:
            # Boolean flag: parts[lk] = letter when on, "" when off.
            v = _entry_value_for_letter(entry, letter, label)
            parts[lk] = letter if _bool_flag_on(v) else ""
        else:
            v = _entry_value_for_letter(entry, letter, label)
            if v:
                parts[lk] = v

    if not parts.get("j"):
        parts["j"] = julian_id_for_file(path)
    date_first = not bool(parts.get("persons"))
    new_stem = build_coded_filename(parts, date_first=date_first)
    if not new_stem or new_stem == stem:
        return path
    new_path = unique_path(os.path.join(os.path.dirname(path), new_stem + ext))
    if new_path == path:
        return path
    try:
        os.rename(path, new_path)
    except Exception:
        return path
    if path in attrs_data:
        attrs_data[new_path] = attrs_data.pop(path)
    if project:
        update_path_in_all_stores(path, new_path, project)
    # Persist immediately — without this, a concurrent watch-dir scan or any
    # stale-path consumer can see the old key in attrs_data and re-introduce
    # it (the "phantom file" reappearing after rename). Skipped during
    # batch runs (Re-apply Rules) where the caller saves once at the end.
    if not defer_save:
        try:
            save(project, attrs_data)
        except Exception:
            pass
    return new_path


# Back-compat alias — preview/UI still imports the old name. To be removed
# once all callers point at rename_file_to_match_entry.
sync_filename_from_entry = rename_file_to_match_entry


def rename_to_date_first(attrs_data, path, project=None):
    """Rename a regular (non-AI) photo to J-first coded filename format.
    Format: J{8chars}[P{pid}]{other_coded_fields}
    EXIF DateTimeOriginal is used for J (falls back to ctime).
    Preserves existing coded fields if the file is already in coded format.
    Returns new path (same as path if unchanged or on error)."""
    stem, ext = os.path.splitext(os.path.basename(path))
    parts = parse_coded_filename(stem)
    j_code = julian_id_for_file(path)
    if parts is None:
        # Not yet coded — build minimal date-first stem
        parts = {"persons": [], "persons_with": [], "j": j_code}
    else:
        # Already coded (person-first or date-first) — keep all fields, update J if absent
        if not parts.get("j"):
            parts["j"] = j_code
    new_stem = build_coded_filename(parts, date_first=True,
                                    field_order=get_sync_field_order(project))
    if not new_stem:
        return path
    new_path = unique_path(os.path.join(os.path.dirname(path), new_stem + ext))
    if new_path == path:
        return path
    try:
        os.rename(path, new_path)
    except Exception:
        return path
    if path in attrs_data:
        attrs_data[new_path] = attrs_data.pop(path)
    if project:
        update_path_in_all_stores(path, new_path, project)
    return new_path


def _strip_fingerprint(stem):
    """Remove trailing all-digit fingerprint suffix e.g. '-590020482048'."""
    return re.sub(r'-\d{6,}$', '', stem)

def filename_group_key(stem):
    """Return a grouping key used for duplicate prefix-matching.
    P001P002B0a1R04K30I001 and P001P002B0a1R02I002 → same group (same people, same bg).
    Returns None for non-coded (text) filenames — those are excluded from prefix grouping
    and left to CLIP similarity alone."""
    parsed = parse_coded_filename(stem)
    if parsed:
        persons = tuple(sorted(parsed.get("persons", [])))
        bg      = parsed.get("b", "")
        return (persons, bg)
    return None

def _extract_filename_base(stem, rules):
    """Strip fingerprint then all known rule-pattern suffixes.
    Uses the last face-number as the split point when present."""
    stem = _strip_fingerprint(stem)
    name = stem.lower()
    face_matches = list(re.finditer(r'-[0-5](?=[-.]|$)', name))
    if face_matches:
        anchor   = face_matches[-1].start()
        pre_face = stem[:anchor]
        changed  = True
        while changed:
            changed   = False
            pre_lower = pre_face.lower()
            for rule in rules:
                pattern = rule.get("pattern", "").lower()
                if not pattern or re.fullmatch(r'-[0-5]', pattern):
                    continue
                if re.search(re.escape(pattern) + r'$', pre_lower):
                    pre_face = pre_face[:len(pre_face) - len(pattern)]
                    changed  = True
                    break
        return pre_face
    earliest = len(stem)
    for rule in rules:
        pattern = rule.get("pattern", "").lower()
        if not pattern:
            continue
        m = re.search(re.escape(pattern) + r'(?=[-.]|$)', name)
        if m and m.start() < earliest:
            earliest = m.start()
    return stem[:earliest] if earliest < len(stem) else stem


def normalize_filename(path, current_tags, new_base=None, person_id=None, project=None):
    """Rebuild filename: {person_id}-{base}-{pose}-{watermark}.
    new_base overrides the extracted base (user-typed name).
    person_id overrides the cached person ID.
    project: if provided, updates faces/dups stores on rename.
    Returns new path, original if unchanged, or None on error."""
    stem, ext = os.path.splitext(path)
    rules = load_filename_rules(project)
    base  = new_base if new_base else _extract_filename_base(stem, rules)

    pid_prefix = f"{person_id}-" if person_id else ""

    pose_suf = ""
    for pk in ["front", "right34", "right", "left34", "left", "back"]:
        if pk in current_tags:
            pose_suf = f"-{pk}"; break

    wm_suf = "-watermark" if "watermark" in current_tags else ""

    new_stem = pid_prefix + base + pose_suf + wm_suf
    new_path = unique_path(os.path.join(os.path.dirname(path), new_stem + ext))
    if new_path == path:
        return path
    try:
        os.rename(path, new_path)
    except Exception:
        return None
    if project:
        update_path_in_all_stores(path, new_path, project)
    return new_path


def apply_pose_to_filename(path, pose_tag, project=None):
    """Rename file putting pose AFTER face suffix: {base}-{face}-{pose}-{watermark}.
    Returns new path, original path if unchanged, or None on error."""
    stem, ext = os.path.splitext(path)
    rules = load_filename_rules(project)
    base  = _extract_filename_base(stem, rules)

    # Detect face suffix from current filename
    name  = stem.lower()
    fm    = re.search(r'-([0-5])(?=[-.]|$)', name)
    face_suf = f"-{fm.group(1)}" if fm else ""

    # Detect watermark
    wm_suf = "-watermark" if re.search(r'-watermark(?=[-.]|$)', name) else ""

    new_stem = base + face_suf + (f"-{pose_tag}" if pose_tag else "") + wm_suf
    new_path = new_stem + ext
    if new_path == path:
        return path
    try:
        os.rename(path, new_path)
        return new_path
    except Exception:
        return None


# ── Auto-set helpers ─────────────────────────────────────────────────────────

def detect_resolution_tag(path):
    try:
        if path.lower().endswith(('.mp4', '.mkv', '.mov', '.avi', '.webm')):
            cap = cv2.VideoCapture(path)
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            cap.release()
        else:
            from PIL import Image
            with Image.open(path) as img:
                w, h = img.size
        longest = max(w, h)
        if longest >= 3840: return "4k"
        if longest >= 1920: return "2k"
        if longest >= 1280: return "1k"
        return "sd"
    except Exception:
        return None

def detect_audio_tag(path):
    """Return codec name (e.g. 'aac', 'mp3', 'opus') if file has audio, 'no_sound' if not, None on error."""
    _VID = ('.mp4', '.mkv', '.mov', '.m4v', '.avi', '.webm', '.wmv')
    if not path.lower().endswith(_VID):
        return None
    try:
        import subprocess, json as _json
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_name", "-of", "json", path],
            capture_output=True, text=True, timeout=5)
        streams = _json.loads(result.stdout).get("streams", [])
        if streams:
            return (streams[0].get("codec_name") or "sound").lower()
        return "no_sound"
    except Exception:
        return None

def detect_shot_and_pose(path):
    """Run MediaPipe Face Mesh + Pose in a single pass and return (shot_tag, pose_tag).
    Either value may be None if it cannot be determined.
    Requires: pip install mediapipe"""
    if path.lower().endswith(('.mp4', '.mkv', '.mov', '.avi', '.webm')):
        return None, None
    try:
        import mediapipe as mp
        img = cv2.imread(path)
        if img is None:
            return None, None
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

        # ── Face Mesh ────────────────────────────────────────────────────────
        face_lm = None
        with mp.solutions.face_mesh.FaceMesh(
                static_image_mode=True, max_num_faces=1,
                refine_landmarks=False,
                min_detection_confidence=0.5) as fm:
            fr = fm.process(rgb)
        if fr.multi_face_landmarks:
            face_lm = fr.multi_face_landmarks[0].landmark

        # ── Body Pose ────────────────────────────────────────────────────────
        body_lm = None
        with mp.solutions.pose.Pose(
                static_image_mode=True,
                model_complexity=1,
                min_detection_confidence=0.5) as ps:
            pr = ps.process(rgb)
        if pr.pose_landmarks:
            body_lm = pr.pose_landmarks.landmark

        # ── Pose direction ───────────────────────────────────────────────────
        pose_tag = None
        if face_lm:
            # Primary: nose offset relative to outer eye midpoint
            nose_x  = face_lm[1].x
            l_eye_x = face_lm[33].x
            r_eye_x = face_lm[263].x
            face_w  = abs(r_eye_x - l_eye_x)
            if face_w >= 0.01:
                offset = (nose_x - (l_eye_x + r_eye_x) / 2) / face_w
                if   offset >  0.30: pose_tag = "FO2"   # Right Profile
                elif offset >  0.10: pose_tag = "FO1"   # Slightly Right
                elif offset > -0.10: pose_tag = "FO0"   # Front
                elif offset > -0.30: pose_tag = "FO7"   # Slightly Left
                else:                pose_tag = "FO6"   # Left Profile
        elif body_lm:
            ls, rs = body_lm[11], body_lm[12]
            if ls.visibility > 0.5 and rs.visibility > 0.5:
                pose_tag = "FO4"   # Back

        # ── Shot type ────────────────────────────────────────────────────────
        shot_tag = None

        def vis(idx):
            if not body_lm: return False
            l = body_lm[idx]
            return l.visibility > 0.5 and 0.0 <= l.y <= 1.0

        if face_lm:
            ys      = [l.y for l in face_lm]
            face_h  = max(ys) - min(ys)   # fraction of image height
            if   face_h > 0.65: shot_tag = "SAecu"
            elif face_h > 0.45: shot_tag = "SAfcu"
            elif face_h > 0.30: shot_tag = "SAcu"
            elif face_h > 0.20: shot_tag = "SAbcu"
            else:
                ankles  = vis(27) or vis(28)
                knees   = vis(25) or vis(26)
                hips    = vis(23) or vis(24)
                sh_l    = vis(11); sh_r = vis(12)
                if ankles:
                    lowest = max(body_lm[i].y for i in [27,28] if vis(i))
                    shot_tag = "SAfs" if lowest > 0.85 else "SAmfs"
                elif knees:
                    shot_tag = "SAmfs"
                elif hips:
                    shot_tag = "SAms"
                elif sh_l or sh_r:
                    sh_y = max((body_lm[i].y for i in [11,12] if vis(i)), default=0)
                    shot_tag = "SAmcu" if sh_y > 0.45 else "SAbust"
                else:
                    shot_tag = "SAcu"
        elif body_lm:
            nose_vis = body_lm[0].visibility > 0.3
            ankles   = vis(27) or vis(28)
            knees    = vis(25) or vis(26)
            if ankles:
                person_h = max(body_lm[i].y for i in [27,28] if vis(i)) - body_lm[0].y
                if   person_h < 0.25: shot_tag = "SAews"
                elif person_h < 0.55: shot_tag = "SAws"
                else:                 shot_tag = "SAfs"
            elif knees:
                shot_tag = "SAmfs"

        return shot_tag, pose_tag
    except Exception:
        return None, None


def detect_pose_tag(path):
    """Convenience wrapper — returns pose tag only."""
    _, pose = detect_shot_and_pose(path)
    return pose


def detect_shot_tag(path):
    """Convenience wrapper — returns shot tag only."""
    shot, _ = detect_shot_and_pose(path)
    return shot

def detect_ai_source(path):
    """Returns (source_tag, prompt, seed) or (None, None, None)."""
    ext = path.lower()
    try:
        # ── PNG: ComfyUI / A1111 / MetadataReader JSON ────────────────────────
        if ext.endswith(".png"):
            from PIL import Image
            with Image.open(path) as img:
                info = img.info
            if "workflow" in info or "prompt" in info:
                prompt, seed = _extract_comfyui_prompt_seed(info)
                return "comfyui", prompt, seed
            if "parameters" in info:
                prompt, seed = _extract_a1111_prompt_seed(info["parameters"])
                return "a1111", prompt, seed
            # MetadataReader JSON baked into Description
            desc = info.get("Description", "")
            if desc.startswith("{"):
                d = json.loads(desc)
                return "aix", d.get("prompt", ""), str(d.get("seed", ""))

        # ── MP4 / MOV / M4V: mutagen atoms ────────────────────────────────────
        elif ext.endswith(('.mp4', '.mov', '.m4v')):
            from mutagen.mp4 import MP4
            video   = MP4(path)
            comment = video.get("\xa9cmt", [""])[0]
            prompt, seed = "", ""
            if "DATA: " in comment:
                d      = json.loads(comment.split("DATA: ")[1])
                prompt = d.get("prompt", "")
                seed   = str(d.get("seed", ""))
            elif comment.startswith("PROMPT:"):
                prompt = comment.split("\n")[0].replace("PROMPT:", "").strip()
            if prompt:
                return "aix", prompt, seed

        # ── JPEG / WebP: EXIF ImageDescription ───────────────────────────────
        elif ext.endswith(('.jpg', '.jpeg', '.webp')):
            from PIL import Image
            with Image.open(path) as img:
                exif = img.getexif() if hasattr(img, "getexif") else {}
                desc = exif.get(0x010e, "") or img.info.get("ImageDescription", "")
            if desc:
                if desc.startswith("{"):
                    d = json.loads(desc)
                    return "aix", d.get("prompt", ""), str(d.get("seed", ""))
                return "aix", desc, ""

    except Exception:
        pass
    return None, None, None

def _extract_comfyui_prompt_seed(info):
    prompt, seed = "", ""
    try:
        raw  = info.get("prompt") or info.get("workflow")
        data = json.loads(raw)
        texts, seeds = [], []
        for node in data.values():
            if not isinstance(node, dict): continue
            cls    = node.get("class_type", "")
            inputs = node.get("inputs", {})
            if cls in ("CLIPTextEncode", "CLIPTextEncodeSDXL") and "text" in inputs:
                t = inputs["text"]
                if isinstance(t, str) and len(t) > 10:
                    texts.append(t)
            if cls in ("KSampler", "KSamplerAdvanced"):
                for k in ("seed", "noise_seed"):
                    if k in inputs: seeds.append(str(inputs[k]))
        prompt = max(texts, key=len) if texts else ""
        seed   = seeds[0] if seeds else ""
    except Exception:
        pass
    return prompt, seed

def _extract_a1111_prompt_seed(params):
    prompt, seed = "", ""
    try:
        lines = params.split("\n")
        pos_lines = []
        for line in lines:
            if line.startswith("Negative prompt:") or re.match(r"^Steps:", line):
                break
            pos_lines.append(line)
        prompt = " ".join(pos_lines).strip()
        m = re.search(r"Seed:\s*(\d+)", params)
        if m: seed = m.group(1)
    except Exception:
        pass
    return prompt, seed

def apply_boolean_sync_rules(attrs_data, path, project, orig_stem=None):
    """For each non-one_way filename rule with a field and no value,
    check if the pattern is in the filename and add/remove the flag letter from the
    coded filename accordingly.  Returns the (possibly renamed) path.
    orig_stem: original filename stem before any rename (used for pattern matching so
               patterns are checked against the pre-rename name, not the coded result).
    The rule itself is authoritative — no CODED_FIELDS lookup needed."""
    rules = load_filename_rules(project)
    if not rules:
        return path

    # Boolean sync rule: field set, no one_way, no extract, value empty or "true"/"false"
    bool_sync = []
    for r in rules:
        if not r.get("field") or r.get("one_way") or r.get("extract"):
            continue
        val = r.get("value", "").strip().lower()
        if val and val not in ("true", "false"):
            continue  # non-boolean value → coded field rule, not boolean sync
        field = r["field"].upper()
        bool_sync.append((r, field))

    if not bool_sync:
        return path

    stem, ext = os.path.splitext(os.path.basename(path))
    parts = parse_coded_filename(stem)
    if parts is None:
        return path  # not a coded file — skip

    # Use original stem for pattern matching if provided (pre-rename filename may contain
    # patterns that get stripped during coded rename, e.g. '-watermark')
    check_name = (orig_stem or stem).lower()
    changed = False
    for rule, letter in bool_sync:
        pattern = rule.get("pattern", "").lower()
        if not pattern:
            continue
        lk = letter.lower()
        currently_on = bool(parts.get(lk, ""))
        pattern_present = pattern in check_name
        val = rule.get("value", "").strip().lower()
        if val == "true":
            # Pattern present → turn flag ON; absence does not turn it off
            if pattern_present and not currently_on:
                parts[lk] = letter
                changed = True
        elif val == "false":
            # Pattern present → turn flag OFF; absence does not turn it on
            if pattern_present and currently_on:
                parts[lk] = ""
                changed = True
        else:
            # No value (legacy): pattern present → ON, absent → OFF
            if pattern_present and not currently_on:
                parts[lk] = letter
                changed = True
            elif not pattern_present and currently_on:
                parts[lk] = ""
                changed = True

    if not changed:
        return path

    new_stem = build_coded_filename(parts, field_order=get_sync_field_order(project))
    if not new_stem or new_stem == stem:
        return path

    new_path = unique_path(os.path.join(os.path.dirname(path), new_stem + ext))
    if new_path == path:
        return path
    try:
        os.rename(path, new_path)
    except Exception:
        return path
    if path in attrs_data:
        attrs_data[new_path] = attrs_data.pop(path)
    return new_path


def apply_tag_sync_rules(attrs_data, path, project):
    """Two-way sync: tag ↔ filename pattern for tag_group rules without one_way.
    Rules sharing the same tag_group+value are treated as a group (alias patterns):
      - tag set:   if ANY alias pattern is in filename → keep it, add none.
                   if NO  alias pattern is in filename → append the first pattern.
      - tag unset: remove ALL alias patterns from filename.
    Returns the (possibly renamed) path."""
    import re as _re
    rules = load_filename_rules(project)
    sync_rules = [r for r in rules
                  if r.get("tag_group") and not r.get("one_way")
                  and r.get("pattern") and r.get("value")]
    if not sync_rules:
        return path
    stem, ext = os.path.splitext(os.path.basename(path))
    entry = get(attrs_data, path)
    entry_tags = set(entry.get("tags", []))
    # Matrix groups (Background, ModelImage, ModelVideo, X, Animal, ...) store
    # their value at entry[section_name], not in entry["tags"]. The rule's
    # tag_group key may use the legacy "_Table" suffix (e.g. Background_Table)
    # while the matrix section name is just "Background" — strip the suffix
    # and check both.
    try:
        _tags_cfg = _load_tag_groups(tags_file_for_project(project))
    except Exception:
        _tags_cfg = {}
    _styles = (_tags_cfg or {}).get("__section_styles__", {}) or {}
    new_stem = stem
    changed = False

    # Group rules by (tag_group, value) so alias patterns are handled together
    from collections import defaultdict as _dd
    groups = _dd(list)
    for rule in sync_rules:
        groups[(rule["tag_group"], rule["value"].strip())].append(rule["pattern"])

    for (tag_group, tag_val), patterns in groups.items():
        if not tag_val:
            continue
        tag_is_set = tag_val in entry_tags
        # Matrix per-field check: entry["Background"] == "ff", etc.
        if not tag_is_set:
            section = tag_group[:-len("_Table")] if tag_group.endswith("_Table") else tag_group
            if _styles.get(section) == "matrix" and entry.get(section, "") == tag_val:
                tag_is_set = True
        if tag_is_set:
            # Check if any alias pattern already present — if so, leave filename alone
            any_present = any(p.lower() in new_stem.lower() for p in patterns)
            if not any_present:
                # Append the first (canonical) pattern
                new_stem = new_stem + patterns[0]
                changed = True
        else:
            # Remove all alias patterns that are present
            for pattern in patterns:
                if pattern.lower() in new_stem.lower():
                    new_stem = _re.sub(_re.escape(pattern), "", new_stem, flags=_re.IGNORECASE)
                    changed = True

    if not changed or new_stem == stem:
        return path
    new_path = unique_path(os.path.join(os.path.dirname(path), new_stem + ext))
    if new_path == path:
        return path
    try:
        os.rename(path, new_path)
    except Exception:
        return path
    if path in attrs_data:
        attrs_data[new_path] = attrs_data.pop(path)
    return new_path


def apply_path_rules(attrs_data, path, project, _path_rules=None):
    """Apply path-scoped filename rules (pattern contains '/') to a single file.
    Called on file open — fast, no metadata/audio/CLIP detection.
    Path rules always override existing values, both for `field` rules
    (coded/text fields) and `tag_group` rules (replaces any existing tags
    belonging to the same tag_group with the rule's value).
    _path_rules: pre-filtered list of path-scoped rules (caller cache); if None, loads from disk.
    Returns (attrs_data, changed)."""
    if _path_rules is None:
        import fnmatch as _fnmatch
        fn_rules = load_filename_rules(project)
        _path_rules = [r for r in fn_rules
                       if (r.get("field") or r.get("tag_group"))
                       and '/' in r.get("pattern", "")]
    path_rules = _path_rules
    if not path_rules:
        return attrs_data, False

    _bn  = os.path.basename(path)
    stem = os.path.splitext(_bn)[0]

    entry   = dict(get(attrs_data, path))
    changed = False

    # ── Field path rules (coded fields, text fields, person ID) ──────────────
    field_rules = [r for r in path_rules if r.get("field")]
    if field_rules:
        od, _ = parse_filename_rules(stem, field_rules, basename=_bn, fullpath=path,
                                     _return_path_flags=True)
        if "P" in od and od["P"] and entry.get("person_id") != od["P"]:
            entry["person_id"] = od["P"]
            changed = True
        _TEXT_TARGETS = {"prompt", "neg_prompt", "seed", "note", "speech", "model"}
        for field, value in od.items():
            if field == "P" or not value:
                continue
            flc = field.lower()
            # Path rules override existing user input — write to the primary
            # key (cl, hc, etc.) so widgets see the rule value. Without this
            # the rule wrote to cf_<key>, but get_coded_field prefers the
            # primary key, so manual entries silently won out.
            primary_key = flc
            if primary_key not in _TEXT_TARGETS and entry.get(primary_key) != value:
                entry[primary_key] = value
                changed = True
                # Keep cf_ in sync so other readers see the same value
                entry[f"cf_{primary_key}"] = value
            elif primary_key in _TEXT_TARGETS and entry.get(primary_key) != value:
                entry[primary_key] = value
                changed = True

    # ── Tag-group path rules (override any existing tags in same group) ──────
    tag_group_rules = [r for r in path_rules if r.get("tag_group")]
    if tag_group_rules:
        import fnmatch as _fnmatch
        norm_path = path.replace("\\", "/").lower()
        # tag_group → set of wanted values from matched path rules
        wanted_by_group = {}
        # Path of the file's immediate parent dir, lowercased — used for
        # trailing-slash patterns (which mean "this folder is the immediate
        # parent", not "anywhere under this folder").
        _parent_lc = os.path.basename(os.path.dirname(norm_path))
        for rule in tag_group_rules:
            pattern = (rule.get("pattern") or "").lower()
            if not pattern:
                continue
            pattern_fn = pattern.replace('#', '?')
            if '*' in pattern_fn or '?' in pattern_fn:
                tgt = pattern_fn if pattern_fn.startswith('*') else f"*{pattern_fn}"
                if not _fnmatch.fnmatch(norm_path, tgt):
                    continue
            elif pattern_fn.endswith('/') and pattern_fn.count('/') == 1:
                # "Folder/" → file's immediate parent ends with that name.
                # Equality would block valid partial patterns: rule "ob/"
                # should match parent "apob" (since the path locally reads
                # ".../apob/<file>"). Suffix match handles both the exact
                # case ("apob/" → parent "apob") and the partial case
                # ("ob/" → parent "apob") while still rejecting nested
                # subfolders ("apob/" rule against parent "sub" inside
                # apob/sub/<file>).
                _stem = pattern_fn.rstrip('/')
                if not _stem or not _parent_lc.endswith(_stem):
                    continue
            else:
                if pattern_fn not in norm_path:
                    continue
            grp = rule["tag_group"]
            val = (rule.get("value") or "").strip()
            if not val:
                continue
            wanted_by_group.setdefault(grp, set()).add(val)

        if wanted_by_group:
            # Look up section styles so matrix groups (ModelImage, ModelVideo,
            # X, Tool, Background, etc.) write to entry[grp] (per-field
            # storage) instead of entry["tags"]. Tag widgets bound to matrix
            # groups read from entry[grp]; writing only to tags would leave
            # the matrix combo blank even though the rule "fired".
            try:
                _tags_cfg = _load_tag_groups(tags_file_for_project(project))
            except Exception:
                _tags_cfg = {}
            _styles = (_tags_cfg or {}).get("__section_styles__", {}) or {}
            cur_tags = list(entry.get("tags", []))
            new_tags = list(cur_tags)
            for grp, wanted in wanted_by_group.items():
                grp_def = TAG_GROUPS.get(grp, [])
                grp_vals = {v[0] for v in grp_def if isinstance(v, (list, tuple)) and v}
                # Matrix groups: store the (single) wanted value in entry[grp].
                if _styles.get(grp) == "matrix":
                    pick = next(iter(wanted), "")
                    if pick and entry.get(grp) != pick:
                        entry[grp] = pick
                        changed = True
                    # Also clean any stale matrix value out of the legacy
                    # tags list so the two storage locations don't disagree.
                    _stale = [t for t in new_tags if t in grp_vals]
                    if _stale:
                        new_tags = [t for t in new_tags if t not in grp_vals]
                    continue
                # Non-matrix tag groups: legacy behavior — write to tags list.
                cur_in_grp = {t for t in new_tags if t in grp_vals}
                if cur_in_grp == wanted:
                    continue
                new_tags = [t for t in new_tags if t not in grp_vals]
                for w in wanted:
                    if w not in new_tags:
                        new_tags.append(w)
            if new_tags != cur_tags:
                entry["tags"] = new_tags
                changed = True

    if changed:
        attrs_data[path] = entry
        # Persist immediately so re-opening the app preserves the rule's effect.
        try:
            save(project, attrs_data)
        except Exception:
            pass

    return attrs_data, changed


def auto_set_all(attrs_data, path, project, skip_heavy=False):
    """Auto-detect and save: resolution, audio tag, AI source, prompt, seed, metadata."""
    entry        = get(attrs_data, path)
    was_editable = entry.get("editable", False)   # only rename files the app has previously touched
    current_tags = list(entry.get("tags", []))
    changed      = False

    # AI source + prompt + seed
    prompt     = entry.get("prompt", "")
    neg_prompt = entry.get("neg_prompt", "")
    seed       = entry.get("seed", "")
    if not any(t in SOURCE_TAGS for t in current_tags) or not prompt:
        src, new_prompt, new_seed = detect_ai_source(path)
        if src:
            if not any(t in SOURCE_TAGS for t in current_tags):
                current_tags = [t for t in current_tags if t not in SOURCE_TAGS] + [src]
            if not prompt: prompt = new_prompt
            if not seed:   seed   = new_seed
            changed = True

    # Full metadata extraction — add new keys only; existing DB values always win
    existing_meta = entry.get("meta") or {}
    fresh_meta    = extract_metadata(path)
    meta = {**fresh_meta, **existing_meta}  # existing wins on conflict
    if meta != existing_meta:
        changed = True

    # Apply metadata mapping rules — custom text/tag/person_id mappings only
    _TEXT_TARGETS = {"prompt", "neg_prompt", "seed", "note", "speech", "model"}
    meta_rules = load_metadata_rules(project)
    if meta_rules:
        for tgt, val in apply_metadata_rules(meta, meta_rules).items():
            if tgt.startswith("tag:") and val:
                tag_key = tgt[4:]
                if tag_key and tag_key not in current_tags:
                    current_tags.append(tag_key)
                    changed = True
            elif tgt == "person_id":
                if not entry.get("person_id"):
                    entry["person_id"] = val; changed = True
            elif tgt in _TEXT_TARGETS:
                if tgt == "prompt"       and not prompt:     prompt     = val; changed = True
                elif tgt == "neg_prompt" and not neg_prompt: neg_prompt = val; changed = True
                elif tgt == "seed"       and not seed:       seed       = val; changed = True
                elif tgt not in ("prompt", "neg_prompt", "seed"):
                    if not entry.get(tgt):
                        entry[tgt] = val; changed = True

    # Shot type + pose direction via MediaPipe → FA coded field (dir) + CS coded field (shot)
    # FA is 2 digits [Vert][Dir]; only Dir (pos 1) is set here — Vert defaults to 0.
    # CS is 3 digits [Shot][Angle][Light]; only Shot (pos 3, leftmost) is set here.
    _POSE_TO_FA_DIR = {
        "FO0": "0",   # Front
        "FO1": "1",   # Right
        "FO2": "2",   # Right 3/4
        "FO4": "5",   # Back
        "FO6": "4",   # Left 3/4
        "FO7": "3",   # Left
    }
    _SHOT_TO_CS_SHOT = {
        "SAecu":  "1",   # Extreme Close-Up
        "SAfcu":  "2",   # Face Close-Up
        "SAbcu":  "3",   # Big Close-Up
        "SAcu":   "4",   # Close-Up
        "SAbust": "5",   # Bust Shot
        "SAmcu":  "6",   # Medium Close-Up
        "SAms":   "7",   # Medium Shot
        "SAmfs":  "8",   # Cowboy Shot (closest to Medium Full)
        "SAfs":   "9",   # Full Shot
        "SAws":   "a",   # Wide Shot
        "SAews":  "b",   # Extreme Wide Shot
    }
    # MediaPipe pose/shot detection — slow; skipped when called from watch
    # scan (skip_heavy=True) so the preview pops up fast and the user sees
    # the detection happen live via _on_inspect.
    if not skip_heavy:
        _needs_fa = not get_coded_field(entry, "fa")
        _needs_cs = not get_coded_field(entry, "cs")
        if _needs_fa or _needs_cs:
            shot_tag, pose_tag = detect_shot_and_pose(path)
            if _needs_fa and pose_tag:
                fa_dir = _POSE_TO_FA_DIR.get(pose_tag)
                if fa_dir:
                    entry["fa"] = fa_dir   # single digit: Dir set, Vert defaults to none
                    changed = True
            if _needs_cs and shot_tag:
                cs_shot = _SHOT_TO_CS_SHOT.get(shot_tag)
                if cs_shot:
                    entry["cs"] = cs_shot + "00"   # [Shot][Angle=0][Light=0]
                    changed = True

    # Audio detection — probe at most once per file. The audio value itself
    # is the "we already checked" marker: empty/missing = not probed, any
    # other value = probed. "sound" is treated as a re-probe trigger because
    # an earlier bug left some entries with that fallback value.
    _existing_audio = entry.get("audio")
    if not _existing_audio or _existing_audio == "sound":
        audio_tag = detect_audio_tag(path)
        if audio_tag is None or audio_tag == "no_sound":
            audio_tag = "none"
        elif audio_tag not in AUDIO_TAGS:
            audio_tag = "sound"
        entry["audio"] = audio_tag
        changed = True
    # Drop the now-redundant audio_probed flag if a prior version set it.
    if "audio_probed" in entry:
        entry.pop("audio_probed", None)
        changed = True

    # Ratio (O) / Resolution (R) / FPS (K) — always-on
    _fa = detect_file_attrs(path)
    if _fa.get("o") and not entry.get("cf_o"):
        entry["cf_o"] = _fa["o"]
        changed = True
    if _fa.get("r") and not entry.get("cf_r"):
        entry["cf_r"] = _fa["r"]
        changed = True
    if _fa.get("k") and not entry.get("cf_k"):
        entry["cf_k"] = _fa["k"]
        changed = True

    # Filename-based tags + enforce rules
    fn_rules = load_filename_rules(project)
    if fn_rules:
        for t in detect_tags_from_filename(path, fn_rules,
                                            existing_tags=current_tags):
            if t not in current_tags:
                current_tags.append(t)
                changed = True
    # Start with whatever is already stored
    person_id = entry.get("person_id", "")

    # One-way coded-field detection: detect rules, extract rules, + any sync rule with a path pattern
    one_way_rules = [
        r for r in fn_rules
        if r.get("field") and (
            r.get("one_way") or r.get("extract") or '/' in r.get("pattern", "")
        )
    ]
    if one_way_rules:
        _bn     = os.path.basename(path)
        stem_ow = os.path.splitext(_bn)[0]
        od, _path_flds = parse_filename_rules(stem_ow, one_way_rules, basename=_bn, fullpath=path, _return_path_flags=True)
        if od:
            if "P" in od and od["P"]:
                if "P" in _path_flds or not entry.get("person_id"):
                    person_id = od["P"]
                    changed = True
            # Path rules override existing values; non-path rules only fill empty fields.
            # Text fields (model, prompt, …) write to entry[field] directly;
            # coded fields write to entry["cf_<field>"].
            for field, value in od.items():
                if field == "P" or not value:
                    continue
                flc = field.lower()
                is_text = flc in _TEXT_TARGETS
                target_key = flc if is_text else f"cf_{flc}"
                if field in _path_flds or not entry.get(target_key):
                    if is_text:
                        # Text targets are stored at the entry level; map common
                        # ones to the named local var so set_file picks them up.
                        if flc == "prompt":     prompt = value
                        elif flc == "neg_prompt": neg_prompt = value
                        elif flc == "seed":     seed = value
                        else:                   entry[target_key] = value
                    else:
                        entry[target_key] = value
                    changed = True

    # Sync person_id from coded filename (e.g. P001.jpg → person_id "001")
    if not person_id:
        stem   = os.path.splitext(os.path.basename(path))[0]
        parsed = parse_coded_filename(stem)
        if parsed and parsed.get("persons"):
            person_id = parsed["persons"][0]
            changed = True

    # Always mark as editable after the app processes a file (first touch)
    if not was_editable:
        changed = True

    if changed:
        set_file(attrs_data, path,
                 tags=current_tags,
                 note=entry.get("note", ""),
                 confirmed=entry.get("confirmed", False),
                 project=entry.get("project", ""),
                 scene=entry.get("scene", ""),
                 prompt=prompt,
                 neg_prompt=neg_prompt,
                 seed=seed,
                 meta=meta,
                 person_id=person_id,
                 speech=entry.get("speech", ""),
                 editable=entry.get("editable", True),
                 preserve_text=True)
        save(project, attrs_data)
    return attrs_data

# Keep old individual helpers pointing to auto_set_all for backwards compat
def auto_set_resolution(attrs_data, path, project):
    return auto_set_all(attrs_data, path, project)

def auto_set_ai_source(attrs_data, path, project):
    return auto_set_all(attrs_data, path, project)
