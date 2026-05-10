import json, os, sys, cv2, re, datetime, time as _time, threading as _threading

# dlib (via face_recognition) is not thread-safe — serialize all face detection calls.
_face_lock = _threading.Lock()


def _face_encodings_with_fallback(img):
    """face_recognition.face_encodings(img) but with an upsample-2
    retry when the default HOG-1 finds nothing. Many small / off-
    angle / AI-generated faces are missed by the default settings;
    upsampling once typically catches them. Caller still owns the
    _face_lock; this helper just adds the fallback."""
    import face_recognition as _fr
    encodings = _fr.face_encodings(img)
    if encodings:
        return encodings
    # No faces at default settings — retry with upsample=2.
    locations = _fr.face_locations(img, number_of_times_to_upsample=2)
    if not locations:
        return []
    return _fr.face_encodings(img, known_face_locations=locations)


def _load_image_or_video_frame(path):
    """Return an RGB numpy array suitable for face_recognition,
    auto-detecting whether the file is actually an image or a video
    regardless of its extension. The user reported renaming MP4 files
    with .jpg extensions; the previous extension-only check ran
    load_image_file on those, which raised silently and returned no
    faces. Returns None on hard failure."""
    import face_recognition as _fr
    _VID_EXT = (".mp4", ".mkv", ".mov", ".m4v", ".avi", ".webm", ".wmv")
    _ext = os.path.splitext(path)[1].lower()
    # Try the path that matches the extension first.
    # Note: cannot use `a or b` here — the decode helpers return
    # numpy arrays, and `bool(ndarray)` raises "The truth value of
    # an array with more than one element is ambiguous" for any
    # array that's not a scalar. Explicit None checks instead.
    if _ext in _VID_EXT:
        img = _decode_video_first_frame(path)
        if img is not None:
            return img
        return _try_image(path)
    # Image-extension first; if PIL can't read it, try as video.
    img = _try_image(path)
    if img is not None:
        return img
    return _decode_video_first_frame(path)


def _try_image(path):
    """Attempt PIL image decode. Returns RGB array or None."""
    try:
        from PIL import UnidentifiedImageError
        import face_recognition as _fr
        return _fr.load_image_file(path)
    except Exception:
        return None


def _decode_video_first_frame(path):
    """ffmpeg/cv2 first-frame decode. Returns RGB array or None."""
    try:
        cap = cv2.VideoCapture(path)
        ok, frame = cap.read()
        cap.release()
        if not ok or frame is None:
            return None
        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    except Exception:
        return None

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
        if   long_side >= 7680: result["resolution"] = "08"   # 8K
        elif long_side >= 3840: result["resolution"] = "04"   # 4K
        elif long_side >= 2560: result["resolution"] = "a4"   # 1440p
        elif long_side >= 1920: result["resolution"] = "a8"   # 1080p
        elif long_side >= 1280: result["resolution"] = "72"   # 720p
        elif long_side >= 854:  result["resolution"] = "48"   # 480p
        else:                   result["resolution"] = "36"   # 360p

        # O — orientation / aspect ratio
        ratio = width / height
        if   abs(ratio - 1.0) < 0.05:  result["orientation"] = "11"  # 1:1  square
        elif ratio >= 4.0:              result["orientation"] = "f1"  # 15:1 extreme ultra-wide
        elif ratio >= 2.2:              result["orientation"] = "73"  # 21:9 cinema wide
        elif ratio >= 1.7:              result["orientation"] = "09"  # 16:9 landscape
        elif ratio >= 1.4:              result["orientation"] = "32"  # 3:2  landscape (photo)
        elif ratio > 1.05:              result["orientation"] = "43"  # 4:3  landscape
        elif ratio > 0.72:              result["orientation"] = "34"  # 3:4  portrait
        elif ratio > 0.58:              result["orientation"] = "23"  # 2:3  portrait (photo)
        else:                           result["orientation"] = "90"  # 9:16 portrait

        # K — frame rate (video only; images have frame_count == 1)
        if frames > 1 and fps > 0:
            if   fps >= 100: result["frame_rate"] = "b0"  # 120fps
            elif fps >= 55:  result["frame_rate"] = "60"  # 60fps
            elif fps >= 27:  result["frame_rate"] = "30"  # 30fps
            else:            result["frame_rate"] = "24"  # 24fps

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
    "eye_color": [
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
    # HC stored value reads LEFT→RIGHT as Color, Style, Length.
    # Position numbering (used by CLIP_AUTO_DETECT and canvas _SUBPOS):
    # pos 1 = rightmost = Length, pos 2 = middle = Style, pos 3 = leftmost = Color.
    "hair_color": [
        ["0", "No hair"],            ["1", "Black"],          ["2", "Dark Brown"],
        ["3", "Light Brown"],        ["4", "Blonde"],         ["5", "Platinum Blonde"],
        ["6", "Red"],                ["7", "Pink"],           ["8", "Ginger"],
        ["9", "Gray"],               ["a", "White"],          ["b", "Blue"],
        ["c", "Yellow"],             ["d", "Green"],          ["e", "Rainbow"],
        ["f", "Neon"],
    ],
    # middle digit = style (pos 2)
    "hair_style": [
        ["0", "(none)"],         ["1", "Straight"],       ["2", "Wavy"],
        ["3", "Curly"],          ["4", "Voluminous"],     ["5", "Bob"],
        ["6", "Ponytail"],       ["7", "Braid"],          ["8", "Tied"],
        ["9", "Buzz"],           ["a", "Twintail"],       ["b", "High Bun"],
        ["c", "Low Bun"],        ["d", "Half-up"],        ["e", "Pixie"],
        ["f", "Hime Cut"],       ["g", "Side Ponytail"],  ["h", "Side Bun"],
        ["i", "Updo"],           ["j", "Mohawk"],         ["k", "Side Swept"],
        ["l", "Curtain Bangs"],  ["m", "Blunt Bangs"],    ["n", "Layered"],
        ["o", "Mullet"],         ["p", "Afro"],           ["q", "Cornrows"],
        ["r", "Dreadlocks"],     ["s", "Top Knot"],       ["t", "Space Buns"],
        ["u", "Crown Braid"],    ["v", "French Braid"],   ["w", "Fishtail"],
        ["x", "Crew Cut"],       ["y", "Undercut"],       ["z", "Spiky"],
    ],
    # rightmost digit = length (pos 1)
    "hair_length": [
        ["0", "(none)"],      ["1", "Very Short"],  ["2", "Short"],
        ["3", "Medium"],      ["4", "Long"],        ["5", "Very Long"],
        ["6", "Bald"],        ["7", "Partially Bald"],
    ],

    # ── Face Angle  FA[vertical][direction]  ─────────────────────────────────
    # 1st digit = direction
    "face_direction": [
        ["0", "Front"],        ["1", "Right"],       ["2", "Right 3/4"],
        ["3", "Left"],         ["4", "Left 3/4"],    ["5", "Back"],
    ],
    # 2nd digit = vertical tilt
    "face_vertical": [
        ["0", "Horizontal"],   ["1", "Upward"],      ["2", "Downward"],
    ],

    # ── Skin  SK[reserved][type]  ────────────────────────────────────────────
    "Skin_Type": [
        ["0", "Type I — Very Fair"],       ["1", "Type II — Fair"],
        ["2", "Type III — Medium"],        ["3", "Type IV — Olive"],
        ["4", "Type V — Dark Brown"],      ["5", "Type VI — Deeply Pigmented"],
    ],

    # ── Bust  B[size][shape]  ────────────────────────────────────────────────
    # 2nd digit = size
    "bust_size": [
        ["0", "(none)"],             ["1", "Flat / Male / Neutral"],
        ["2", "Athletic / Pectorals"],["3", "Petite / AAA-A"],
        ["4", "Small / B-C"],        ["5", "Medium / D-E"],
        ["6", "Large / F-G"],        ["7", "Extra Large / H+"],
        ["8", "Enhanced"],
    ],
    # 1st digit = shape  (TBD — reserve f0 range)
    "bust_shape": [
        ["0", "(undefined)"],   ["1", "Square (Pecs)"],  ["2", "Round"],
        ["3", "Teardrop"],      ["4", "Broad"],          ["5", "Side Set"],
        ["6", "Slender"],
    ],

    # ── WaistHip  WH[waist][hip]  ────────────────────────────────────────────
    # 1st digit = hip
    "hip_size": [
        ["0", "(none)"],          ["1", "Thin"],           ["2", "Athletic / Firm"],
        ["3", "Average"],         ["4", "Curvy / Full"],   ["5", "Large / Wide"],
        ["6", "Extra Large"],     ["7", "Sticks out"],
    ],
    # 2nd digit = waist
    "waist_size": [
        ["0", "(none)"],          ["1", "Flat / Thin"],    ["2", "Athletic / Firm"],
        ["3", "Average"],         ["4", "Curvy / Full"],   ["5", "Large / Wide"],
        ["6", "Extra Large"],     ["7", "Pregnant"],
    ],

    # ── Posture+Motion  PM[posture][motion]  ─────────────────────────────────
    # 2nd digit = posture. Code 0 is the "Standing" default (default_is_zero).
    "posture": [
        ["0", "Standing"],                ["1", "Standing in style"],
        ["2", "Sitting"],                 ["3", "Kneeling"],
        ["4", "Lying"],                   ["5", "Leaning"],
        ["6", "Crouching"],               ["7", "Handstand"],
    ],
    # 1st digit = motion. Code 0 is the "Still" default (default_is_zero).
    "motion": [
        ["0", "Still"],       ["2", "Walking"],     ["3", "Running"],
        ["4", "Dancing"],     ["5", "Looking at Camera"],
        ["6", "Talking"],     ["7", "Gesturing"],   ["8", "Fighting"],
    ],

    # ── Camera/Shot  CS[shot][angle][lighting]  ──────────────────────────────
    # 3rd digit = shot area
    "camera_shot": [
        ["0", "(none)"],             ["1", "Extreme Close-Up"],   ["2", "Face Close-Up"],
        ["3", "Big Close-Up"],       ["4", "Close-Up"],           ["5", "Bust Shot"],
        ["6", "Medium Close-Up"],    ["7", "Medium Shot"],        ["8", "Cowboy Shot"],
        ["9", "Full Shot"],          ["a", "Wide Shot"],          ["b", "Extreme Wide"],
    ],
    # 2nd digit = angle
    "camera_angle": [
        ["0", "Eye Level"],    ["1", "Low Angle"],     ["2", "High Angle"],
        ["3", "Over-Shoulder"],["4", "Dutch Angle"],   ["5", "Bird's Eye"],
    ],
    # 1st digit = lighting. Code 0 is the "Natural" default (default_is_zero).
    "camera_light": [
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
    # Person fields aren't in CODED_FIELDS (multi-token tags), so they
    # have no auto-derived label. Without these, the canvas displayed
    # the literal key (P / PW). Filename codes stay short.
    "P":       "Person",
    "PW":      "PersonsWith",
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
E_COLOR_TAGS      = _tag_keys("eye_color")
E_ADDITIONAL_TAGS  = _tag_keys("eye_additional")
HC_COLOR_TAGS     = _tag_keys("hair_color")
HC_STYLE_TAGS     = _tag_keys("hair_style")
HC_LENGTH_TAGS    = _tag_keys("hair_length")
FA_DIR_TAGS       = _tag_keys("face_direction")
FA_VERT_TAGS      = _tag_keys("face_vertical")
SK_TYPE_TAGS      = _tag_keys("Skin_Type")
B_SIZE_TAGS       = _tag_keys("bust_size")
B_SHAPE_TAGS      = _tag_keys("bust_shape")
WH_HIP_TAGS       = _tag_keys("hip_size")
WH_WAIST_TAGS     = _tag_keys("waist_size")
PM_POSTURE_TAGS   = _tag_keys("posture")
PM_MOTION_TAGS    = _tag_keys("motion")
CS_SHOT_TAGS      = _tag_keys("camera_shot")
CS_ANGLE_TAGS     = _tag_keys("camera_angle")
CS_LIGHT_TAGS     = _tag_keys("camera_light")
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
    # (letter, label, digits, storage_key)
    # letter:       filename code (e.g. "HC" — used in coded filenames)
    # label:        human-readable display label
    # digits:       2 or 3 = hex digit count; 0 = boolean flag
    # storage_key:  long human-readable key used in attrs.json AND in memory
    # Each digit position has independent meaning — see _DEFAULT_TAG_GROUPS
    # ── Person / Subject ─────────────────────────────────────────────────────
    ("A",   "Animal",        2, "animal"),
    ("PI",  "PersonInhrt",   3, "person_inhrt"),
    # PW is handled as multi-token (like P), not a single CODED_FIELD
    # ── Face ─────────────────────────────────────────────────────────────────
    ("E",   "Eyes",          2, "eyes"),
    ("HC",  "Hair",          3, "hair"),
    ("FA",  "FaceAngle",     2, "face_angle"),
    ("X",   "Expression",    2, "expression"),
    # ── Body ─────────────────────────────────────────────────────────────────
    ("SK",  "Skin",          2, "skin"),
    ("B",   "Bust",          2, "bust"),
    ("WH",  "WaistHip",      2, "waist_hip"),
    ("PM",  "PostureMotion", 2, "posture_motion"),
    ("CL",  "Clothing / 服装", 4, "clothing"),
    ("T",   "Tool",          2, "tool"),
    # ── Technical ────────────────────────────────────────────────────────────
    ("CS",  "CameraShot",    3, "camera_shot"),
    ("BG",  "Background",    2, "background"),
    ("O",   "Orientation",   2, "orientation"),
    ("R",   "Resolution",    2, "resolution"),
    ("K",   "FrameRate",     2, "frame_rate"),
    ("J",   "Timestamp",     8, "timestamp"),
    ("ED",  "Editable",      0, "editable_flag"),
    ("WM",  "Watermark",     0, "watermark_flag"),
]

def _load_coded_fields():
    """Load CODED_FIELDS from data/attrs_tags.json __coded_fields__ key.
    Falls back to _DEFAULT_CODED_FIELDS if not present or on error.

    Each entry is a 4-tuple: (letter, label, digits, storage_key).
    Older saves had 3-tuples — fall back to the default's storage_key
    (looked up by letter) so old project files keep loading cleanly."""
    try:
        if os.path.exists(TAGS_FILE):
            with open(TAGS_FILE, encoding="utf-8") as _f:
                _raw = json.load(_f)
            _cf = _raw.get("__coded_fields__")
            if _cf and isinstance(_cf, list):
                # Build a letter→storage_key map from the defaults so we
                # can fill in the 4th element when loading old 3-tuples.
                _default_storage = {l: sk
                                    for l, _, _, sk in _DEFAULT_CODED_FIELDS}
                result = []
                for item in _cf:
                    if isinstance(item, (list, tuple)) and len(item) >= 3:
                        letter = str(item[0])
                        label  = str(item[1])
                        digits = int(item[2])
                        if len(item) >= 4 and item[3]:
                            storage = str(item[3])
                        else:
                            storage = _default_storage.get(letter, letter.lower())
                        result.append((letter, label, digits, storage))
                if result:
                    return result
    except Exception:
        pass
    return list(_DEFAULT_CODED_FIELDS)

CODED_FIELDS = _load_coded_fields()


# ── Storage-key naming ───────────────────────────────────────────────────────
# Storage key = the lowercase human-readable name used in attrs.json AND
# in memory (e.g. "hair"). It's the 4th element of each CODED_FIELDS tuple.
# Filenames keep the short uppercase letter ("HC") — the storage key only
# governs JSON / Python-dict access.
#
# Two derived maps:
#   _STORAGE_KEY_MAP:     letter.lower() / legacy short code → storage key
#   _STORAGE_KEY_REVERSE: storage key                       → letter.lower()
# Both are useful for migration / audit but aren't load-bearing — code
# should call field_storage_key() instead of consulting them directly.
def _build_storage_key_map():
    """Map every legacy short alias of a coded field (the lowercase
    letter) to its current storage key. Includes letter aliases on
    aliased fields like ED→editable_flag."""
    out = {}
    for letter, _label, _digits, storage in CODED_FIELDS:
        out[letter.lower()] = storage
    return out

_STORAGE_KEY_MAP = _build_storage_key_map()
_STORAGE_KEY_REVERSE = {v: k for k, v in _STORAGE_KEY_MAP.items()}


def field_storage_key(field):
    """Return the JSON storage key for a coded field.
    Accepts either the uppercase filename letter ("HC"), the lowercase
    legacy short code ("hc"), or the long storage key itself ("hair")
    — any of these resolves to the canonical storage form."""
    if not field:
        return field
    s = field.lower()
    if s in _STORAGE_KEY_MAP:
        return _STORAGE_KEY_MAP[s]
    # Already a long storage key — return unchanged.
    return s


def field_storage_get(entry, field, default=None):
    """Read a coded-field value from `entry` honoring both new long-form
    keys and legacy short keys. Used during the migration window so a
    half-migrated attrs.json still loads cleanly."""
    if not isinstance(entry, dict):
        return default
    long_key  = field_storage_key(field)
    short_key = field.lower() if field else field
    if long_key in entry:
        return entry[long_key]
    if short_key in entry:
        return entry[short_key]
    return default


# ── Disk-key translation ─────────────────────────────────────────────────────
# In-memory attrs entries use the short coded-field keys (e.g. "hc", "bg") —
# all the existing code paths read/write those names. On disk we want the
# human-readable form ("hair", "background"). Translation happens ONLY at
# the save/load boundaries so internal call sites don't need to change.

def _entry_to_disk(entry):
    """Memory and disk now share the same long-form keys. Pass-through.
    Kept as a hook in case future asymmetries get added without
    needing to revisit save()."""
    return entry


def _entry_from_disk(entry):
    """Translate LEGACY short keys (pre-2026-05) to the canonical long
    form on read. Idempotent for current long-keyed files. Without
    this, a project last saved before the rename loses its coded-field
    values on first read."""
    if not isinstance(entry, dict):
        return entry
    out = {}
    for k, v in entry.items():
        out[_STORAGE_KEY_MAP.get(k, k)] = v
    return out


# ── Workspace prefix translation ─────────────────────────────────────────────
# attribute_workspace.json uses composite keys: <PREFIX><HEX_VALUE>, e.g.
# "PM00", "HC123". Same goal as the entry-level translation: long names on
# disk, short uppercase prefixes in memory.
_WS_PREFIX_TO_LONG = {
    "A":   "animal",
    "PI":  "person_inhrt",
    "E":   "eyes",
    "HC":  "hair",
    "FA":  "face_angle",
    "X":   "expression",
    "SK":  "skin",
    "B":   "bust",
    "WH":  "waist_hip",
    "PM":  "posture_motion",
    "CL":  "clothing",
    "T":   "tool",
    "CS":  "camera_shot",
    "BG":  "background",
    "O":   "orientation",
    "R":   "resolution",
    "K":   "frame_rate",
}
_WS_LONG_TO_PREFIX = {v: k for k, v in _WS_PREFIX_TO_LONG.items()}
# Sort longest-first so "BG" wins over "B" when matching at start of a key.
_WS_LONGS_BY_LEN = sorted(_WS_LONG_TO_PREFIX.keys(), key=len, reverse=True)
_WS_PREFIXES_BY_LEN = sorted(_WS_PREFIX_TO_LONG.keys(), key=len, reverse=True)


def workspace_key_to_disk(key):
    """`PM00` → `posture_motion00`. Idempotent: already-long keys
    pass through unchanged."""
    if not isinstance(key, str):
        return key
    for short in _WS_PREFIXES_BY_LEN:
        if key.startswith(short) and len(key) > len(short):
            return _WS_PREFIX_TO_LONG[short] + key[len(short):]
    return key


def workspace_key_from_disk(key):
    """`posture_motion00` → `PM00`. Idempotent."""
    if not isinstance(key, str):
        return key
    for long in _WS_LONGS_BY_LEN:
        if key.startswith(long) and len(key) > len(long):
            return _WS_LONG_TO_PREFIX[long] + key[len(long):]
    return key


def workspace_data_to_disk(data):
    if not isinstance(data, dict):
        return data
    return {workspace_key_to_disk(k): v for k, v in data.items()}


def workspace_data_from_disk(data):
    if not isinstance(data, dict):
        return data
    return {workspace_key_from_disk(k): v for k, v in data.items()}

# Person token pattern: P + (human 3-hex OR animal A+3-hex)  [not followed by W]
_PERSON_PAT = r'P(?!W)(A[0-9a-f]{3}|[0-9a-f]{3})'
# PersonWith token pattern: PW + 3-hex (multi-token, like P)
_PW_PAT = r'PW([0-9a-f]{3})'

# Regex for the non-person coded fields (after all P tokens are stripped).
# Values are lowercase a-z + 0-9 (base-36), so per-position tag tables can
# hold up to 36 entries. Field KEYS in the filename stay uppercase letters
# (HC, BG); regex group names use the LONG storage key ("hair", "background")
# so the parsed dict speaks the same namespace as in-memory entries.
# (Old hex-only data remains valid since [0-9a-z] is a superset of [0-9a-f].)
def _field_pat(letter, digits, storage):
    if digits == 0:
        return rf'(?P<{storage}>{letter})?'      # flag: just the letter, no value
    char_cls = "[0-9a-z]"
    return rf'(?:{letter}(?P<{storage}>{char_cls}{{{digits}}}))?'

def _storage_key_for(cf_entry):
    """Return the long storage key for a CODED_FIELDS row (4th element);
    falls back to lowercase letter for older 3-tuple definitions."""
    return cf_entry[3] if len(cf_entry) >= 4 else cf_entry[0].lower()

_FIELD_RE = re.compile(
    r'^'
    + ''.join(_field_pat(cf[0], cf[2], _storage_key_for(cf)) for cf in CODED_FIELDS)
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
        for cf in CODED_FIELDS:
            letter, _, digits = cf[0], cf[1], cf[2]
            lk = _storage_key_for(cf)   # long storage key
            if digits == 0:
                # Boolean flag — letter, not part of another key's name.
                # Lookbehind forbids uppercase only (lowercase hex is fine
                # because that's the previous field's value tail).
                _bm = re.search(rf'(?<![A-Z]){letter}(?![A-Za-z0-9])', remainder)
                result[lk] = letter if _bm else ""
                if _bm:
                    _ok = True
            else:
                _cls = "[0-9a-z]"   # base-36 (lowercase + digits) for all coded fields
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
    parts keys: persons (list), persons_with (list), plus long storage keys
    (e.g. "hair", "background") matching what parse_coded_filename returns
    and what entries store. For backwards compat, short letter keys ("hc")
    are also accepted.

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
    for cf in _fields:
        letter, _, digits = cf[0], cf[1], cf[2]
        storage = _storage_key_for(cf)
        # Try long storage key first; fall back to legacy short letter key.
        val = parts.get(storage, "") or parts.get(letter.lower(), "")
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
    p = faces_db_path(project)
    # Safeguard: if we're about to write a much-smaller faces dict on
    # top of a non-trivial existing one, snapshot the old file as a
    # .wiped-<unix> backup first. Real shrinkage (user deleted N pids
    # legitimately) still goes through — we just keep a recoverable
    # copy. This wouldn't have prevented the original Clean up wipe
    # from happening, but it would have left a backup the user could
    # restore from instead of having to dig through versioned dirs.
    try:
        new_n = len((db or {}).get("faces", {}) or {})
        if os.path.exists(p):
            with open(p, encoding="utf-8") as _f:
                _existing = json.load(_f)
            old_n = len((_existing or {}).get("faces", {}) or {})
            # Snapshot when wiping to (near-)empty from non-trivial,
            # OR when losing more than half the persons in one save.
            if (old_n >= 5 and new_n < max(2, old_n // 2)):
                import time as _t
                bak = f"{p}.wiped-{int(_t.time())}"
                if not os.path.exists(bak):
                    with open(bak, "w", encoding="utf-8") as _bf:
                        json.dump(_existing, _bf, indent=2, ensure_ascii=False)
                    print(f"[aisearch] faces DB shrank {old_n}->{new_n}; "
                          f"snapshot saved to {bak}")
    except Exception:
        pass
    with open(p, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)

def detect_or_assign_person_id(path, project, threshold=0.65, raise_errors=False):
    """Extract face embedding, match against project face DB, return hex ID.
    Each person ID stores multiple embeddings; comparison uses closest match
    across all samples so accuracy improves as more images are confirmed.
    Assigns a new ID (001–fff) if no known person matches. 000 = no human.
    If raise_errors=True, exceptions propagate instead of returning None."""
    try:
        import face_recognition
        import numpy as np
        # Auto-detect image vs video — handles .jpg files that are
        # actually MP4 (or vice versa) instead of failing silently.
        img = _load_image_or_video_frame(path)
        if img is None:
            return None
        # dlib is not thread-safe — serialize all face encoding calls
        with _face_lock:
            encodings = _face_encodings_with_fallback(img)
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
            # Known person — DO NOT auto-add this embedding to the
            # winning pid's pool. Auto-add was creating a "rich get
            # richer" loop: the wrong pid would fill its 20-sample
            # cap with auto-added matches, so even after the user
            # corrected, the next auto-detect would re-add the same
            # face back to the wrong pid. The user reported "I keep
            # fixing P013→P001 every time, does it really learn?"
            # — it didn't, because each correction's single-sample
            # removal was undone by the next scan's auto-add.
            #
            # Now the pool only grows via explicit user actions:
            #   - correct_person_id (manual P-field change)
            #   - _add_face_sample (right-click "Add this face")
            #   - _set_base_face / _assign_new_person
            # The detector reads from the pool but never writes to it.
            #
            # Repair source_path if it no longer exists on disk —
            # cheap, no learning effect.
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
    try:
        import face_recognition
        # Same content-vs-extension auto-detect as detect_or_assign.
        img = _load_image_or_video_frame(path)
        if img is None:
            return None
        with _face_lock:
            encodings = _face_encodings_with_fallback(img)
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


def dismantle_face_assignment(path, project, pid):
    """Strip every trace this file contributed to person `pid` in the
    faces DB. Used when the user finds a wrong assignment and wants
    the matcher to forget this file ever existed under that pid:

      - Extract the face encoding from `path`.
      - In faces[pid].embeddings, drop the sample most similar to it
        (face_distance argmin). That's the one this file most likely
        added during a prior detect/correct.
      - If faces[pid].source_path == path, clear it (caller can pick
        a new rep pic).
      - If faces[pid] now has zero embeddings, delete the pid entirely.

    Caller is responsible for clearing entry["person_id"] in attrs and
    for any filename renames — this function only touches the faces DB.
    Returns a dict describing what changed: {samples_removed, pid_deleted,
    source_path_cleared} or None on failure."""
    try:
        import face_recognition
        import numpy as np
        if not project or not pid or not path or not os.path.exists(path):
            return None
        # Decode image (or first video frame)
        if path.lower().endswith(('.mp4', '.mkv', '.mov', '.avi', '.webm')):
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
            encs = _face_encodings_with_fallback(img)
        if not encs:
            return None
        enc = encs[0]

        db = load_faces_db(project)
        faces = db.get("faces", {})
        if pid not in faces:
            return {"samples_removed": 0, "pid_deleted": False, "source_path_cleared": False}
        fdata = faces[pid]
        samples = list(fdata.get("embeddings", []))
        # Migrate legacy single-embedding entries
        if not samples and fdata.get("embedding"):
            samples = [fdata["embedding"]]
        samples_removed = 0
        if samples:
            dists = face_recognition.face_distance(np.array(samples), enc)
            worst_idx = int(np.argmin(dists))
            samples.pop(worst_idx)
            samples_removed = 1
        fdata["embeddings"] = samples
        # Clear legacy single-emb field if it pointed at this same enc
        fdata.pop("embedding", None)

        source_path_cleared = False
        if os.path.normpath(fdata.get("source_path", "")) == os.path.normpath(path):
            fdata["source_path"] = ""
            source_path_cleared = True

        pid_deleted = False
        if not samples:
            # No samples left → the pid is empty. Delete it so it
            # doesn't haunt the matcher and the registry.
            faces.pop(pid, None)
            pid_deleted = True

        save_faces_db(project, db)
        return {"samples_removed": samples_removed,
                "pid_deleted": pid_deleted,
                "source_path_cleared": source_path_cleared}
    except Exception:
        return None


def correct_person_id(path, project, correct_id, wrong_id=None):
    """Register face from path under correct_id.

    If wrong_id is given, fully dismantles this file's contribution to
    wrong_id (drop closest sample, clear source_path if it pointed
    here, delete the pid entirely if no samples remain) — same effect
    as dismantle_face_assignment so a manual P change behaves like the
    explicit "Dismantle" action followed by a register. User asked
    for this so that a manual ID correction doesn't leave the rejected
    face polluting the wrong pid's pool.
    """
    try:
        import face_recognition
        import numpy as _np
        img  = face_recognition.load_image_file(path)
        with _face_lock:
            encs = _face_encodings_with_fallback(img)
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

        # Full dismantle of wrong_id's tie to this file:
        # 1. Drop the sample most similar to this face from the pool.
        # 2. Clear source_path if it pointed at THIS file.
        # 3. Delete the pid entirely if the pool ends up empty.
        # 4. Print the similarity scores of the removed sample so the
        #    user can see what was removed — they asked for visibility
        #    into deletions: "if it shows which one is deleted ... maybe ok".
        result = {"correct_id": correct_id, "wrong_id": wrong_id,
                  "samples_removed": 0, "source_path_cleared": False,
                  "pid_deleted": False,
                  "wrong_id_size_before": 0, "wrong_id_size_after": 0,
                  "removed_similarity": None}
        if wrong_id and wrong_id in faces:
            fdata = faces[wrong_id]
            samples = list(fdata.get("embeddings", []))
            # Migrate legacy single-embedding entries into the pool
            if not samples and fdata.get("embedding"):
                samples = [fdata["embedding"]]
            result["wrong_id_size_before"] = len(samples)
            if samples:
                distances = face_recognition.face_distance(_np.array(samples), encs[0])
                worst_idx = int(_np.argmin(distances))
                worst_dist = float(distances[worst_idx])
                worst_sim = round(1.0 - worst_dist, 3)
                samples.pop(worst_idx)
                result["samples_removed"] = 1
                result["removed_similarity"] = worst_sim
            fdata["embeddings"] = samples
            fdata.pop("embedding", None)
            result["wrong_id_size_after"] = len(samples)
            if os.path.normpath(fdata.get("source_path", "")) == os.path.normpath(path):
                fdata["source_path"] = ""
                result["source_path_cleared"] = True
            if not samples:
                faces.pop(wrong_id, None)
                result["pid_deleted"] = True
            print(f"[correct-pid] {os.path.basename(path)}: "
                  f"P{wrong_id} pool {result['wrong_id_size_before']}→"
                  f"{result['wrong_id_size_after']} "
                  f"(removed sample @ similarity={result['removed_similarity']})"
                  + (f", pid deleted" if result['pid_deleted'] else "")
                  + (f", rep pic cleared" if result['source_path_cleared'] else ""))
        # Report add to correct_id too
        try:
            new_count = len(faces.get(correct_id, {}).get("embeddings", []))
            print(f"[correct-pid] {os.path.basename(path)}: "
                  f"P{correct_id} pool now {new_count} samples (added 1)")
        except Exception:
            pass

        save_faces_db(project, db)
        return result
    except Exception:
        return None


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
                raw = json.load(f)
        except Exception:
            return {}
        # Translate long-form disk keys ("hair", "background") back to
        # the short in-memory aliases ("hair", "bg") that all the existing
        # code expects. Idempotent for legacy short-keyed data.
        return {p: _entry_from_disk(e) for p, e in raw.items()}
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
                stripped = {k: _cap_debug(k, v) for k, v in entry.items()
                            if not _is_transient_key(k)}
                # Translate short coded-field keys (hc, bg, …) into the
                # human-readable form (hair, background, …) on disk.
                cleaned[path] = _entry_to_disk(stripped)
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
    Checks the storage key (long form — "hair", "background", …) first,
    then the auto-detected variant (cf_{storage_key}). Backward-compat:
    legacy short keys (letter.lower()) are also checked so a stale
    in-memory entry from before the migration still resolves.
    """
    sk = field_storage_key(letter)
    short = letter.lower() if letter else ""
    return (entry.get(sk, "")
            or entry.get(f"cf_{sk}", "")
            # legacy fallbacks — for entries that haven't been re-saved
            # since the rename or that came from an older import path.
            or entry.get(short, "")
            or entry.get(f"cf_{short}", ""))

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
_AITAN_VERSION = "2.5.2"  # stamped into every AItan{} block as "ver"

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
    """Return all raw embedded metadata text from a file as a formatted string.

    Tolerant of transient FileNotFound during a concurrent rename / bake
    swap (`embed_aitan_meta` writes to a `.aitan_tmp` and `shutil.move`s
    over the target — the target briefly disappears on cross-filesystem
    moves). We re-check after a short wait before reporting "missing"."""
    if not os.path.exists(path):
        # Race against concurrent bake/rename — wait briefly and re-check.
        import time as _time
        for _i in range(3):
            _time.sleep(0.08)
            if os.path.exists(path):
                break
        if not os.path.exists(path):
            # Surface the attempted path + its basename so it's clear
            # WHICH file the reader couldn't find. The earlier wording
            # "may have just been renamed" alarmed the user when the
            # actual file existed elsewhere on disk; showing the path
            # makes it diagnosable instead of mysterious.
            return ("(file not found at this path)\n"
                    f"path tried: {path}\n"
                    f"basename:   {os.path.basename(path)}\n"
                    "(if the basename matches a file on disk in a "
                    "different folder, the in-memory path index is "
                    "stale — try Update DB)")
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
                # Text chunks / info dict.
                # Skip:
                #   - Binary blobs PIL exposes as bytes (exif, icc, etc.)
                #   - JPEG/PNG/WEBP encoding-internal flags that don't
                #     carry meaningful metadata. progressive/progression
                #     in particular is just "is this baseline or
                #     progressive JPEG?" — leaks through to the user
                #     and looks like data when it isn't.
                _skip_binary = ("exif", "icc_profile", "dpi", "jfif", "jfif_version",
                                "jfif_density", "jfif_unit", "adobe", "photoshop",
                                "progressive", "progression",
                                "interlace", "compression", "filter",
                                "gamma", "transparency", "srgb",
                                "background", "duration", "loop",
                                "version", "ihdr", "chromaticity")
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
    except FileNotFoundError:
        # File vanished mid-read — concurrent rename or bake swap.
        return ("(file not found at this path)\n"
                f"path tried: {path}")
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
                # The original code fell through to _embed_aitan_video here.
                # That was a bug: when called on a real-but-corrupt JPEG
                # (or any file PIL couldn't classify), ffmpeg WOULD read
                # the input and re-mux it as MP4, then shutil.move'd
                # tmp.mp4 over the original .jpg path — silently
                # converting the user's JPEG into MP4-with-.jpg-extension.
                # Audit found 92 such files in the user's project.
                #
                # New rule: video bake is ONLY for files with video
                # extensions, never as an "image fallback". A truly
                # mismatched file (MP4 saved as .jpg) is left alone —
                # the audit tool can rename it to the right extension.
                if _raise:
                    raise RuntimeError(
                        "PIL could not classify this file as an image; "
                        "refusing to fall back to video bake (would have "
                        "overwritten the file with MP4 content)")
                print(f"[embed] skipped — file at {path} is not a "
                      f"recognizable image; refusing video fallback")
                return False
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
            for _l, _lb, _d, *_ in CODED_FIELDS:
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
    # Prompts are deliberately discriminative on luminance and saturation:
    # plain "black hair" vs "blonde hair" loses to mid-tones in shadowed
    # regions of the image. Calling out "very dark" / "bright light" /
    # "saturated" gives CLIP a sharper axis to score along.
    # pos 3 = leftmost digit (canvas convention: Color, Style, Length).
    {"field": "hair", "pos": 3, "zero_is_none": True,  "threshold": 0.20, "options": [
        ("1", "a person with very dark jet black hair, no light tones, no highlights"),
        ("2", "a person with dark brown brunette hair, deep cocoa or chocolate tone"),
        ("3", "a person with light brown caramel hair, mid-tone warm brown"),
        ("4", "a person with bright blonde hair, golden yellow light-colored hair"),
        ("5", "a person with platinum white-blonde hair, very pale near-white hair"),
        ("6", "a person with vivid red hair, deep crimson or burgundy hair"),
        ("7", "a person with pink hair, saturated pink dye"),
        ("8", "a person with ginger orange copper hair, fiery red-orange"),
        ("9", "a person with gray or silver-gray hair, no color saturation"),
        ("a", "a person with pure white hair, snow white not blonde"),
        ("b", "a person with vivid blue hair, saturated blue dye"),
        ("c", "a person with vivid pure yellow hair, saturated yellow not blonde"),
        ("d", "a person with vivid green hair, saturated green dye"),
        ("e", "a person with rainbow multi-colored hair, several distinct colors at once"),
        ("f", "a person with neon glowing fluorescent hair color"),
    ]},
    # ── Hair style ────────────────────────────────────────────────────────────
    {"field": "hair", "pos": 2, "zero_is_none": True,  "threshold": 0.16, "options": [
        ("1", "a person with flat straight hair with no curl or wave"),
        ("2", "a person with gently wavy or slightly curled hair"),
        ("3", "a person with clearly curly or spiral ringlet hair texture"),
        ("4", "a person with voluminous puffy hair"),
        ("5", "a person with bob cut chin-length hair"),
        ("6", "a person with hair tied back in a single ponytail at the back of the head"),
        ("7", "a person with braided hair, classic three-strand braid"),
        ("8", "a person with hair tied up in a generic bun or knot, position unspecified"),
        ("9", "a person with a buzzcut or head that is shaved bald"),
        ("a", "a person with hair in two symmetric pigtails or twintails on each side of the head"),
        ("b", "a person with hair tied in a high bun on top of the head"),
        ("c", "a person with hair tied in a low bun at the nape of the neck"),
        ("d", "a person with half-up half-down hairstyle, top tied while bottom flows free"),
        ("e", "a person with a pixie cut, very short layered haircut"),
        ("f", "a person with a hime cut, long straight hair with blunt straight bangs"),
        ("g", "a person with hair in a side ponytail draped over one shoulder"),
        ("h", "a person with hair tied in a side bun off to one side of the head"),
        ("i", "a person with hair in an elaborate updo or formal styled hair"),
        ("j", "a person with a mohawk, sides shaved with a strip of hair on top"),
        ("k", "a person with hair side-swept across the forehead"),
        ("l", "a person with curtain bangs framing the face"),
        ("m", "a person with thick blunt straight-cut bangs across the forehead"),
        ("n", "a person with a layered haircut showing visible cascading layers"),
        ("o", "a person with a mullet hairstyle, short on top short on sides long in back"),
        ("p", "a person with a large afro hairstyle"),
        ("q", "a person with hair in cornrows, tight braids close to the scalp"),
        ("r", "a person with dreadlocks, thick rope-like locked strands"),
        ("s", "a person with a top knot, hair gathered in a knot high on top of the head"),
        ("t", "a person with space buns, two small buns on either side high on the head"),
        ("u", "a person with a crown braid encircling the top of the head"),
        ("v", "a person with a French braid, single braid down the back of the head"),
        ("w", "a person with a fishtail braid, intricately woven herringbone braid"),
        ("x", "a person with a crew cut, very short military haircut"),
        ("y", "a person with an undercut, sides closely shaved beneath longer top hair"),
        ("z", "a person with spiky hair styled to stand up in spikes"),
    ]},
    # ── Hair length ───────────────────────────────────────────────────────────
    # pos 1 = rightmost digit.
    {"field": "hair", "pos": 1, "zero_is_none": True,  "threshold": 0.16, "options": [
        ("1", "a person with a buzzcut shaved head with almost no hair visible"),
        ("2", "a person with very short hair above the ears not reaching the jaw"),
        ("3", "a person with hair ending at or just touching the shoulders"),
        ("4", "a person with long hair clearly past the shoulders reaching mid-back"),
        ("5", "a person with very long hair reaching the waist hips or lower"),
        ("6", "a person who is fully bald with a completely shaved or smooth head and no visible hair"),
        ("7", "a person with partially bald receding hairline or thinning hair on top of the head"),
    ]},
    # ── Face direction ────────────────────────────────────────────────────────
    {"field": "face_angle", "pos": 1, "zero_is_none": False, "threshold": 0.0,  "options": [
        ("0", "a person facing directly forward toward camera"),
        ("1", "a person facing right full profile side view"),
        ("2", "a person facing slightly right three-quarter view"),
        ("3", "a person facing left full profile side view"),
        ("4", "a person facing slightly left three-quarter view"),
        ("5", "a person facing away from camera showing back of head"),
    ]},
    # ── Face vertical tilt ────────────────────────────────────────────────────
    {"field": "face_angle", "pos": 2, "zero_is_none": False, "threshold": 0.0,  "options": [
        ("0", "a person with head at normal horizontal level"),
        ("1", "a person with head tilted upward chin raised"),
        ("2", "a person with head bowed or tilted downward"),
    ]},
    # ── Skin type ─────────────────────────────────────────────────────────────
    {"field": "skin", "pos": 1, "zero_is_none": False, "threshold": 0.0,  "options": [
        ("0", "a person with very fair or pale white skin"),
        ("1", "a person with fair light skin tone"),
        ("2", "a person with medium beige or light tan skin"),
        ("3", "a person with olive or medium brown skin"),
        ("4", "a person with dark brown skin"),
        ("5", "a person with very dark deeply pigmented skin"),
    ]},
    # ── Posture ───────────────────────────────────────────────────────────────
    {"field": "posture_motion", "pos": 2, "zero_is_none": True, "default_is_zero": True, "threshold": 0.20, "options": [
        ("0", "a person standing upright on both feet"),
        ("2", "a person sitting down on a chair or floor"),
        ("3", "a person kneeling on one or both knees"),
        ("4", "a person lying down horizontally"),
        ("5", "a person leaning against a wall or surface"),
        ("6", "a person crouching or squatting down"),
        ("7", "a person doing a handstand upside down"),
    ]},
    # ── Motion ────────────────────────────────────────────────────────────────
    {"field": "posture_motion", "pos": 1, "zero_is_none": True, "default_is_zero": True, "threshold": 0.22, "options": [
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
    {"field": "camera_shot", "pos": 3, "zero_is_none": True,  "threshold": 0.18, "options": [
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
    {"field": "camera_shot", "pos": 2, "zero_is_none": True, "default_is_zero": True, "threshold": 0.22, "options": [
        ("0", "straight eye-level shot with camera at subject's eye height facing forward"),
        ("1", "low angle shot looking upward at the subject"),
        ("2", "high angle shot looking downward at the subject"),
        ("3", "over-the-shoulder shot from behind a person"),
        ("4", "dutch angle or tilted camera creating strong diagonal"),
        ("5", "bird's eye view shot directly from overhead above"),
    ]},
    # ── Lighting ─────────────────────────────────────────────────────────────
    {"field": "camera_shot", "pos": 1, "zero_is_none": True, "default_is_zero": True, "threshold": 0.20, "options": [
        ("0", "natural ambient light no artificial setup"),
        ("1", "bright sunny daylight outdoor lighting"),
        ("2", "warm golden sunset or sunrise lighting"),
        ("3", "clean professional studio lighting white background"),
        ("4", "dramatic cinematic lighting with strong shadows and contrast"),
        ("5", "flat colorful anime or illustration style"),
        ("6", "dark nighttime or very low-light scene"),
    ]},
    # ── Background major ─────────────────────────────────────────────────────
    # BG is a 2-digit field. pos=2 = leftmost digit (major scene
    # category). Codes MUST match the user's Background_Table
    # row-major layout — otherwise detecting "indoor" wrote 3
    # (the user's "Commercial" row), detecting "nature" wrote 6
    # (Space row), etc. The previous spec used CLIP-internal
    # codes 0-8 that drifted from the user's category numbering,
    # which is why "BG always wrong" — confirmed bug.
    #
    # Codes here mirror the user's row prefixes:
    #   0 = Solid   (00 Solid, 01 Black, 02 White, 03 Green, 04 Red)
    #   1 = Indoor  (10 Indoor, 11 Bedroom, 12 Living Room, …)
    #   2 = Outdoor (20 Outdoor, 21 Outside of house, 22 Pool)
    #   3 = Commercial (30 Commercial, 31 Store, 32 Restaurant, …)
    #   4 = Nature  (40 Nature, 41 Beach, 42 Ocean, …)
    #   5 = City    (50 City, 51 Street, 52 Park)
    #   6 = Space   (60 Space, 61 Stars, 62 Moon)
    #   7 = Castle
    # zero_is_none=True: "0" (Solid pure color) wins by argmax on every
    # bokeh / shallow-depth-of-field portrait because all the other prompts
    # score similarly low on a blurred background. Treating "0" as "didn't
    # confidently detect" keeps the field blank in those cases instead of
    # lying about it being a solid color background.
    # Prompts use "behind a person" framing because the images are portraits.
    # Without that cue, CLIP scores the whole image (mostly the person)
    # and every scene prompt scores similarly low — whichever prompt has
    # the most distinctive vocabulary then wins by accident. The "outer
    # space" prompt was particularly susceptible because it has seven
    # celestial nouns and any bokeh / stage-light dot pattern triggered
    # it. Keep the space prompt narrow to actual deep-space imagery.
    {"field": "background", "pos": 2, "zero_is_none": True, "threshold": 0.20, "options": [
        ("0", "a person photographed against a flat solid color background like a white green or black studio backdrop chromakey"),
        ("1", "a person photographed inside a private home interior bedroom living room kitchen with personal furniture visible behind"),
        ("2", "a person photographed in a residential outdoor area like a house yard backyard garden patio or swimming pool"),
        ("3", "a person photographed inside a commercial indoor business like a store cafe restaurant office gym studio classroom"),
        ("4", "a person photographed in a wilderness nature scene with trees grass forest mountains beach or open natural landscape"),
        ("5", "a person photographed on a city street with buildings cars sidewalks crowds or urban architecture visible behind"),
        ("6", "an astronaut floating in deep outer space with a planet or spacecraft visible in the vacuum of space"),
        ("7", "a person inside a stone castle with medieval walls towers fortress architecture or historic stonework visible behind"),
    ]},
    # ── Animal group (major row only — sub-types within a group come from
    # manual selection or per-field correction learning). zero_is_none=True
    # means "no animal" (code 0) is suppressed so portraits without animals
    # don't get a false animal tag.
    # Row codes match attrs_tags_<PROJECT>.json A_Table:
    #   1=Dog 2=Cat 3=Bird 4=Farm/Livestock(horse,cow,pig,sheep)
    #   5=Reptile 6=Aquatic mammal(dolphin,whale) 7=Fish/Marine(shark,octopus)
    #   8=Wild predator(lion,tiger,bear,wolf) 9=Wild prey(deer,elephant,zebra)
    #   a=Small mammal/Rodent  b=Insect  f=Mythical/Fantasy
    {"field": "animal", "pos": 2, "zero_is_none": True, "threshold": 0.20, "options": [
        ("0", "a person photographed alone with no animal companion or pet visible in frame"),
        ("1", "a person photographed with a domestic dog or puppy beside them as pet or companion"),
        ("2", "a person photographed with a domestic cat or kitten beside them as pet or companion"),
        ("3", "a person photographed with a bird parrot eagle owl peacock or songbird visible"),
        ("4", "a person photographed with farm livestock horse pony cow pig sheep goat or chicken in a barn pasture or stable"),
        ("5", "a person photographed with a reptile snake lizard turtle or crocodile"),
        ("6", "a person photographed in water with an aquatic mammal dolphin whale seal or sea lion"),
        ("7", "a person photographed underwater with fish or marine life tropical fish shark octopus or jellyfish"),
        ("8", "a person photographed with a wild predator carnivore lion tiger bear wolf or fox"),
        ("9", "a person photographed with a wild prey herbivore deer elephant giraffe zebra rhino or hippo"),
        ("a", "a person photographed with a small mammal rodent rabbit hamster capybara ferret or squirrel"),
        ("b", "a person photographed with an insect or arachnid butterfly bee or spider visible"),
        ("f", "a person photographed with a mythical fantasy creature dragon unicorn phoenix or mermaid"),
    ]},
    # ── Expression family (first digit — AI detects x0 baseline of each family) ─
    {"field": "expression", "pos": 2, "zero_is_none": True,  "threshold": 0.18, "options": [
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
    {"field": "eyes", "pos": 1, "zero_is_none": True,  "threshold": 0.18, "options": [
        ("1", "a person with chocolate brown eyes warm dark iris"),
        ("2", "a person with vivid blue eyes bright sky blue or ocean blue iris"),
        ("3", "a person with hazel eyes green-brown mixed iris"),
        ("4", "a person with amber or golden yellow eyes warm honey iris"),
        ("5", "a person with neutral gray ash eyes desaturated iris no blue or green tint"),
        ("6", "a person with vivid green eyes emerald or jade iris"),
        ("7", "a person with purple or violet eyes lavender iris"),
        ("8", "a person with red or pink eyes albino iris"),
        ("9", "a person with silver metallic eyes shiny chrome iris"),
        ("a", "a person with very dark almost black eyes deep dark iris"),
    ]},
    # ── Clothing — Top type (pos 3) ───────────────────────────────────────────
    {"field": "clothing", "pos": 3, "zero_is_none": True, "threshold": 0.15, "options": [
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
    {"field": "clothing", "pos": 4, "zero_is_none": True, "threshold": 0.14, "options": [
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
    {"field": "clothing", "pos": 1, "zero_is_none": True, "threshold": 0.15, "options": [
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
    {"field": "clothing", "pos": 2, "zero_is_none": True, "threshold": 0.14, "options": [
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
    code (e.g. CL) appear automatically without forcing a delete-and-resave.

    On-disk format keeps SHORT field codes ("hc", "bg") for backward
    compatibility with user backups; in-memory uses LONG storage keys
    ("hair", "background"). Translate at the boundary."""
    try:
        if os.path.exists(CLIP_LABELS_FILE):
            with open(CLIP_LABELS_FILE, encoding="utf-8") as f:
                data = json.load(f)
            for spec in data:
                spec["options"] = [tuple(o) for o in spec["options"]]
                # Short → long. Idempotent for already-long files.
                _f = spec.get("field", "")
                spec["field"] = _STORAGE_KEY_MAP.get(_f, _f)
            seen = {(s["field"], s["pos"]) for s in data}
            for default_spec in _CLIP_AUTO_DETECT_DEFAULTS:
                key = (default_spec["field"], default_spec["pos"])
                if key not in seen:
                    data.append(dict(default_spec))
            return data
    except Exception:
        pass
    return list(_CLIP_AUTO_DETECT_DEFAULTS)

def save_clip_labels(specs):
    """Save CLIP label specs to clip_labels.json and invalidate the cache.
    Writes with SHORT field codes for backward-compat with user backups."""
    global CLIP_AUTO_DETECT, _clip_label_cache
    out = []
    for spec in specs:
        s = dict(spec)
        # JSON doesn't support tuples — serialise option pairs as lists.
        s["options"] = [list(o) for o in spec["options"]]
        # Long → short on disk so older tooling can still read the file.
        _f = s.get("field", "")
        s["field"] = _STORAGE_KEY_REVERSE.get(_f, _f)
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
    """Record coded field values from a baked entry as labeled examples
    for future detection. Stores the FULL value per field (e.g. BG="36"
    as one example, not "3" and "6" separately) — for matrix-style
    fields like BG, the digits aren't independent (col 6 of row 3 means
    Commercial Gym, but col 6 of row 0 means nothing). Per-field
    learning preserves that semantic unit and matches how the user
    thinks about the matrix.

    Re-baking the same path replaces its previous corrections."""
    try:
        import torch
        corrections = load_corrections(project)
        corrections = [c for c in corrections if c.get("path") != path_key]
        emb = image_emb
        if hasattr(emb, "dim") and emb.dim() > 1:
            emb = emb.squeeze(0)
        emb = emb.cpu()
        # Iterate every coded field with digits > 0. Read by long storage
        # key (post-migration entries store at "hair", "background", …).
        for cf in CODED_FIELDS:
            letter, _label, digits = cf[0], cf[1], cf[2]
            if digits <= 0:
                continue
            sk = cf[3] if len(cf) >= 4 else letter.lower()
            val = (coded_entry.get(sk) or coded_entry.get(letter.lower())
                   or "").strip().lower()
            if not val:
                continue
            # Skip pure-zero values — those mean "didn't detect" / default.
            # A real "0X" value (e.g. BG=05 = Red BG) still has a non-zero
            # digit somewhere and is kept.
            if all(c == "0" for c in val):
                continue
            corrections.append({"path": path_key, "field": sk,
                                "value": val, "emb": emb})
        _save_corrections(project, corrections)
    except Exception:
        pass


def detect_from_corrections(image_emb, corrections, field, pos=None, threshold=0.92):
    """Return correction-based detection for `field` or None.

    Per-field corrections store the FULL value (e.g. BG="36"). When `pos`
    is None the full value is returned. When `pos` is given the digit at
    that position is extracted (pos=1 = rightmost). Legacy per-pos
    correction records (saved before the per-field migration) are still
    honored — if a record has its own `pos` field it must match, and
    its `value` is returned as-is.

    Only fires when a stored example is very similar (cosine ≥ threshold)."""
    relevant = []
    for c in corrections:
        if c.get("field") != field:
            continue
        # Legacy per-pos record (has explicit pos): only relevant when
        # caller asks for the same pos.
        if "pos" in c:
            if pos is None or c["pos"] != pos:
                continue
        relevant.append(c)
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
        if float(sims[best_idx]) < threshold:
            return None
        rec = relevant[best_idx]
        val = rec["value"]
        if "pos" in rec:
            return val   # legacy single-digit record; already a digit
        if pos is None:
            return val   # per-field whole-value lookup
        # Per-field record but caller wants a specific position digit.
        return val[-pos] if pos <= len(val) else None
    except Exception:
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

    # Build field → total_digits map keyed by long storage key, since
    # spec["field"] in CLIP_AUTO_DETECT is the long storage name.
    field_digits_map = {(cf[3] if len(cf) >= 4 else cf[0].lower()): cf[2]
                        for cf in CODED_FIELDS if cf[2] > 0}

    working = {}  # field → hex string being assembled
    detected_fields = set()  # fields where at least one digit was detected

    # Per-field placeholder for "not yet filled" digits. Default is
    # "0" (zero), but expression uses literal "x" so unscored sub-digits
    # read as "1x"/"2x" — clearly "family detected, detail not auto-
    # filled" — instead of "10"/"20" which would lie about the detail
    # being a real default category.
    _field_fill = {"expression": "x"}

    def _get_working(field):
        if field not in working:
            digits = field_digits_map.get(field, 2)
            cur = existing_entry.get(field, "") or ""
            fill = _field_fill.get(field, "0")
            if cur:
                # Pad right with fill char so position 1 (rightmost)
                # gets the placeholder if not already specified.
                working[field] = cur.ljust(digits, fill)[:digits]
            else:
                working[field] = fill * digits
        return working[field]

    emb = image_emb
    if hasattr(emb, "dim") and emb.dim() == 1:
        emb = emb.unsqueeze(0)

    corrections = load_corrections(project) if project else []

    # Per-field whole-value correction pass — runs BEFORE per-pos CLIP
    # scoring. If a near-duplicate image with this field already labeled
    # exists in corrections, write the WHOLE stored value (e.g. BG="36"
    # both digits) and mark the field fully detected so the per-pos loop
    # below skips it. Without this, only digits with a CLIP_AUTO_DETECT
    # spec would get filled — pos 1 of BG would always default to "0".
    if corrections:
        _emb_1d = emb.squeeze(0) if hasattr(emb, "dim") and emb.dim() > 1 else emb
        _corrected_fields = set()
        # Unique long storage keys present in corrections.
        for _field in {c.get("field") for c in corrections if c.get("field")}:
            if allowed_fields is not None and _field not in allowed_fields:
                continue
            _full = detect_from_corrections(_emb_1d, corrections, _field, pos=None)
            if _full is None:
                continue
            # Skip if every position already user-set (don't overwrite).
            _existing = (existing_entry.get(_field, "") or "").strip().lower()
            if _existing and _existing == _full:
                continue
            working[_field] = _full
            detected_fields.add(_field)
            _corrected_fields.add(_field)
        # Spec iteration below will check this set and skip per-pos work
        # on fields whose whole value already came from a correction.
    else:
        _corrected_fields = set()

    for i, spec in enumerate(CLIP_AUTO_DETECT):
        field       = spec["field"]
        if allowed_fields is not None and field not in allowed_fields:
            continue
        if field in _corrected_fields:
            continue   # whole value already set from correction
        pos         = spec["pos"]
        zero_is_none    = spec.get("zero_is_none", True)
        default_is_zero = spec.get("default_is_zero", False)
        threshold       = spec.get("threshold", 0.20)
        options         = spec["options"]

        current = _get_working(field)
        _placeholder = _field_fill.get(field, "0")
        cur_digit = current[-pos] if pos <= len(current) else _placeholder

        # Skip if already set by user — don't overwrite manual corrections.
        # Compare against the field's placeholder (e.g. "x" for X) so a
        # half-filled field with placeholder digits in remaining positions
        # still gets those positions re-detected.
        if zero_is_none or default_is_zero:
            if cur_digit != "0" and cur_digit != _placeholder:
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

        # Score image against all option texts.
        text_embs = cache[i]
        scores    = _stutil.cos_sim(emb, text_embs)[0]
        best_idx  = int(scores.argmax())
        best_score = float(scores[best_idx])
        best_code = options[best_idx][0]

        # Threshold removed: AI-generated images produce flatter
        # CLIP score distributions (the image is canonically a person
        # AND a forest AND a tank top all at once, so all 15 hair-
        # color prompts score moderately and the best peaks at
        # ~0.19 instead of ~0.28). The old `if best_score < 0.20:
        # continue` left those at 0 — i.e. "no hair" — which gave
        # nonsense like "very long hair AND no hair simultaneously".
        # We now always take the argmax and only skip when:
        #   - best is the explicit "0/none" option AND zero_is_none
        #     (genuine "I couldn't detect" answer), OR
        #   - none of the above for default_is_zero fields, where
        #     "0" is itself a meaningful answer (e.g. PM=0 standing).
        if zero_is_none and not default_is_zero and best_code == "0":
            continue   # genuinely classified as "none" — leave unset

        # Write the detected digit into the working hex string
        val = list(current)
        val[-pos] = best_code
        working[field] = "".join(val)
        detected_fields.add(field)

    # ── Cross-field consistency pass ──────────────────────────────────────
    # The independent per-position scoring produces nonsense like
    # "very long hair AND no hair" because each digit's threshold is
    # checked in isolation. If one HC digit firmly classified the image
    # as having hair (length is anything but 0/bald), force the other
    # two HC digits to pick their best NON-ZERO option even when the
    # raw scores didn't clear the threshold. Same for CL top/bottom:
    # if the bottom type was detected, the top isn't really "absent",
    # CLIP just didn't peak above the threshold.
    def _force_subdigits(field, leader_pos, dependent_specs):
        """Force any zero sub-digits of `field` (long storage key, e.g.
        "hair") to a non-zero argmax pick when the `leader_pos` digit is
        non-zero. Spec field, working dict and CODED_FIELDS digits are
        all keyed by the same long storage key, no translation needed."""
        cur = working.get(field) or _get_working(field)
        if not cur:
            return
        digits = field_digits_map.get(field, len(cur))
        idx_in_str = -leader_pos
        if abs(idx_in_str) > len(cur):
            return
        leader_digit = cur[idx_in_str]
        if leader_digit == "0":
            return  # no leader detected, nothing to force
        for dep_pos, dep_spec_idx, bad_codes in dependent_specs:
            cur2 = working.get(field) or _get_working(field)
            if abs(-dep_pos) > len(cur2):
                continue
            dep_digit = cur2[-dep_pos]
            if dep_digit != "0":
                continue   # already detected, leave alone
            spec = CLIP_AUTO_DETECT[dep_spec_idx]
            if spec.get("field") != field or spec.get("pos") != dep_pos:
                continue
            text_embs = cache[dep_spec_idx]
            scores = _stutil.cos_sim(emb, text_embs)[0]
            # Pick best non-zero, non-"bad" (e.g. "topless") option
            best_idx = -1
            best_sc = -1.0
            for j, (code, _txt) in enumerate(spec["options"]):
                if code == "0" or code in bad_codes:
                    continue
                sc = float(scores[j])
                if sc > best_sc:
                    best_sc = sc
                    best_idx = j
            if best_idx < 0:
                continue
            forced_code = spec["options"][best_idx][0]
            val = list(cur2)
            val[-dep_pos] = forced_code
            working[field] = "".join(val)
            detected_fields.add(field)

    # Index CLIP_AUTO_DETECT specs by (field, pos) — both long-keyed.
    _spec_idx_by_fp = {(s["field"], s["pos"]): i for i, s in enumerate(CLIP_AUTO_DETECT)}

    # HC: if length (pos 3) detected non-bald, force color (pos 1)
    # and style (pos 2) to a non-zero pick.
    if (allowed_fields is None or "hair" in allowed_fields):
        # bald-like length codes that would NOT imply visible hair: 1
        # (buzzcut, almost no hair) and 6 (fully bald).
        cur_hc = working.get("hair")
        if cur_hc and len(cur_hc) >= 3 and cur_hc[-3] not in ("0", "1", "6"):
            deps = []
            if ("hair", 1) in _spec_idx_by_fp:
                deps.append((1, _spec_idx_by_fp[("hair", 1)], set()))
            if ("hair", 2) in _spec_idx_by_fp:
                # Style: don't force "bald" sub-style (9 = buzzcut/shaved)
                deps.append((2, _spec_idx_by_fp[("hair", 2)], {"9"}))
            _force_subdigits("hair", 3, deps)

    # CL: if bottom type (pos 1) detected non-zero AND not "no bottom"
    # (code 1), force top type (pos 3) to a non-topless pick. Same logic
    # in reverse: if top type detected (not topless), force bottom.
    if (allowed_fields is None or "clothing" in allowed_fields):
        cur_cl = working.get("clothing")
        if cur_cl and len(cur_cl) >= 3:
            bot_type = cur_cl[-1]   # pos 1 = bottom type
            top_type = cur_cl[-3]   # pos 3 = top type
            if bot_type not in ("0", "1") and top_type == "0":
                # Bottom present → top probably present too
                if ("clothing", 3) in _spec_idx_by_fp:
                    _force_subdigits("clothing", 1,
                        [(3, _spec_idx_by_fp[("clothing", 3)], {"1"})])
            if top_type not in ("0", "1") and bot_type == "0":
                if ("clothing", 1) in _spec_idx_by_fp:
                    _force_subdigits("clothing", 3,
                        [(1, _spec_idx_by_fp[("clothing", 1)], {"1"})])

    # CS shot type → CL visibility: if the camera shot is close-up
    # / portrait / waist-up, the bottom (pants/skirt) isn't visible
    # so set it to the N/A code "0".
    #
    # CS shot type codes (pos 3):
    #   1 = extreme close-up (eyes/lips) — neither top nor bot visible
    #   2 = close-up of face only        — neither visible
    #   3 = face + tiny shoulders        — bot not visible
    #   4 = face + shoulders             — bot not visible
    #   5 = bust shot                    — bot not visible
    #   6 = head to upper chest          — bot not visible
    #   7 = waist up                     — bot not visible
    #   8 = mid-thigh up                 — both partially visible
    #   9+ = full body / wide            — both visible
    # "Out of frame / N/A / not detected" all collapse to code "0"
    # per user: '"0 — CLIP didn't detect (default fill)" this can be
    # N/A'. 0 carries the "no value" semantic universally; 1 stays
    # as the explicit "real zero category" (topless, no bottom, bare
    # skin) where defined.
    _NA = "0"
    if (allowed_fields is None or "clothing" in allowed_fields):
        cs_val = working.get("camera_shot") or _get_working("camera_shot")
        cl_val = working.get("clothing") or _get_working("clothing")
        if cs_val and len(cs_val) >= 3 and cl_val:
            cs_shot = cs_val[-3]
            cl_chars = list(cl_val)
            cl_changed = False
            # Bot not visible for shots 1-7
            if cs_shot in ("1", "2", "3", "4", "5", "6", "7"):
                for _p in (1, 2):
                    idx = -_p
                    if abs(idx) <= len(cl_chars) and cl_chars[idx] != _NA:
                        cl_chars[idx] = _NA
                        cl_changed = True
            # Top also not visible for shots 1-2
            if cs_shot in ("1", "2"):
                for _p in (3, 4):
                    idx = -_p
                    if abs(idx) <= len(cl_chars) and cl_chars[idx] != _NA:
                        cl_chars[idx] = _NA
                        cl_changed = True
            if cl_changed:
                working["clothing"] = "".join(cl_chars)
                detected_fields.add("clothing")

    # PM (Posture/Motion) cross-rule: only force N/A for very tight
    # close-ups (CS 1-4) where neither shoulders nor torso are visible.
    # At CS 5+ (bust shot and wider) the upper torso / arms ARE in
    # frame, so CLIP can usefully read motion (still vs walking) and
    # often posture (standing vs sitting/leaning) from shoulder line
    # and arm angle. Forcing N/A there hid PM on every bust-shot image.
    if (allowed_fields is None or "posture_motion" in allowed_fields):
        cs_val = working.get("camera_shot") or _get_working("camera_shot")
        pm_val = working.get("posture_motion") or _get_working("posture_motion")
        if cs_val and len(cs_val) >= 3 and pm_val:
            cs_shot = cs_val[-3]
            if cs_shot in ("1", "2", "3", "4"):
                pm_chars = list(pm_val)
                pm_changed = False
                for _p in range(1, len(pm_chars) + 1):
                    idx = -_p
                    if pm_chars[idx] != _NA:
                        pm_chars[idx] = _NA
                        pm_changed = True
                if pm_changed:
                    working["posture_motion"] = "".join(pm_chars)
                    detected_fields.add("posture_motion")

    # And for B (Bust) and WH (Waist/Hip): N/A for shots that don't
    # show that body region. Bust visible from CS 5 (bust shot) up;
    # waist/hip visible from CS 7 (waist up) up.
    if (allowed_fields is None or "bust" in allowed_fields):
        cs_val = working.get("camera_shot") or _get_working("camera_shot")
        b_val = working.get("bust") or _get_working("bust")
        if cs_val and len(cs_val) >= 3 and b_val:
            cs_shot = cs_val[-3]
            if cs_shot in ("1", "2", "3", "4"):
                # Bust not visible
                b_chars = list(b_val)
                b_changed = False
                for _p in range(1, len(b_chars) + 1):
                    idx = -_p
                    if b_chars[idx] != _NA:
                        b_chars[idx] = _NA
                        b_changed = True
                if b_changed:
                    working["bust"] = "".join(b_chars)
                    detected_fields.add("bust")
    if (allowed_fields is None or "waist_hip" in allowed_fields):
        cs_val = working.get("camera_shot") or _get_working("camera_shot")
        wh_val = working.get("waist_hip") or _get_working("waist_hip")
        if cs_val and len(cs_val) >= 3 and wh_val:
            cs_shot = cs_val[-3]
            if cs_shot in ("1", "2", "3", "4", "5", "6"):
                # Waist/hip not visible
                wh_chars = list(wh_val)
                wh_changed = False
                for _p in range(1, len(wh_chars) + 1):
                    idx = -_p
                    if wh_chars[idx] != _NA:
                        wh_chars[idx] = _NA
                        wh_changed = True
                if wh_changed:
                    working["waist_hip"] = "".join(wh_chars)
                    detected_fields.add("waist_hip")

    # Return only fields that actually changed from original. The
    # all-placeholder check (e.g. "00") is conditional: for fields
    # where every position has a meaningful zero — either zero_is_none
    # is False (FA "00" = front+level, SK "0" = a valid skin code)
    # OR default_is_zero is True (PM "00" = Standing+Still) — "all
    # placeholder" IS a real detection result and should be written.
    # For HC/X/etc. where "0" means "none / not detected" on at least
    # one position, all-placeholder is rejected as before.
    field_specs_by_field = {}
    for _s in CLIP_AUTO_DETECT:
        field_specs_by_field.setdefault(_s.get("field"), []).append(_s)
    def _all_zero_is_meaningful(field):
        specs = field_specs_by_field.get(field, [])
        if not specs:
            return False
        return all(
            (s.get("default_is_zero") or not s.get("zero_is_none", True))
            for s in specs
        )
    result = {}
    for field, new_val in working.items():
        digits = field_digits_map.get(field, 2)
        fill = _field_fill.get(field, "0")
        orig_raw = existing_entry.get(field, "") or ""
        orig = orig_raw.ljust(digits, fill)[:digits] if orig_raw else fill * digits
        all_placeholder = fill * digits
        zero_ok = _all_zero_is_meaningful(field)
        if new_val != orig and (new_val != all_placeholder or zero_ok):
            result[field] = new_val
        elif field in detected_fields and not existing_entry.get(field):
            if new_val != all_placeholder or zero_ok:
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
        threshold = spec.get("threshold", 0.20)
        _top_code = opts_sorted[0][0] if opts_sorted else None
        # Threshold gate REMOVED to match auto_detect_clip_attrs. AI-
        # generated images produce flat CLIP score distributions where
        # the best peak sits 0.15-0.18, well under the historical 0.20
        # threshold. Without this change the inspector / Update button
        # said "below threshold" and wrote nothing, while the bake path
        # silently wrote argmax — the two diverged on every refresh.
        # We still suppress an explicit "0/none" winner for fields where
        # zero means "couldn't detect" (HC, X), so the bake skip-rule
        # stays consistent.
        winner = _top_code
        if (spec.get("zero_is_none", True)
                and not spec.get("default_is_zero", False)
                and winner == "0"):
            winner = None
        # `field` here is the FILENAME LETTER ("HC", "BG", "CL") used by
        # the debug-tile system (CLIP_HC, CLIP_BG, …). spec["field"] is
        # the long storage key — translate via _STORAGE_KEY_REVERSE.
        # Also expose the long key so consumers can write to entry[long]
        # without having to re-translate.
        _short = _STORAGE_KEY_REVERSE.get(spec["field"], spec["field"])
        results.append({
            "field": _short.upper(),
            "storage_key": spec["field"],
            "pos": spec["pos"],
            "threshold": threshold,
            "zero_is_none": spec.get("zero_is_none", True),
            "default_is_zero": spec.get("default_is_zero", False),
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


def inspect_face_detection_subprocess(path, project, timeout=120):
    """Run inspect_face_detection in a worker subprocess.
    Isolates dlib/face_recognition leaks — each call gets a fresh process
    whose memory the OS fully reclaims on exit. The leak that was crashing
    the main app at high RSS no longer compounds across calls.

    The 120s budget accounts for `import aisearch_attrs` inside the
    worker — that transitively loads the CLIP model (15-25s on CPU
    fallback) before any face work begins. Without the headroom the
    user saw "worker timeout (30s)" on routine inspections.

    AISEARCH_INPROC_FACE=1 forces the in-process call (debugging).
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
        # Use the auto-detect loader so files with mismatched
        # extensions (e.g. an MP4 renamed with a .jpg suffix) still
        # decode. Without this, the user reported "face clearly
        # exists" but Faces found: 0.
        img = _load_image_or_video_frame(path)
        if img is None:
            result["error"] = "could not decode file as image or video"
            return result
        # First pass: HOG model with default upsample (fast). Most faces
        # land here. If nothing's found, retry with upsample=2 — that's
        # where small / off-angle / AI-generated faces typically show up.
        # User reported "face clearly exists" but num_faces=0; the
        # default HOG setting misses small or unusual faces.
        with _face_lock:
            locations = face_recognition.face_locations(img)
            if not locations:
                # Upsample retry — doubles the search cost but finds
                # faces below ~80 px that HOG-1 misses.
                locations = face_recognition.face_locations(
                    img, number_of_times_to_upsample=2)
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
        if current_persons and current_persons[0] == pid and parts.get("timestamp"):
            return path   # already correct — nothing to do
        if not parts.get("timestamp"):
            parts["timestamp"] = julian_id_for_file(path)  # stamp creation date if not already present
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


def _entry_value_for_letter(entry, letter, label, storage_key=None):
    """Return the entry's stored value for a CODED_FIELDS letter, checking
    every key the codebase has used over time. Different storage shapes:
      - matrix sections (A/X) — uppercase letter is the section/widget key
      - matrix sections w/ label name (T→Tool, BG→Background) — label key
      - canonical long storage key (post-2026-05): "hair", "background", …
      - dig fields (E/HC/FA/SK/B/WH/PM/CL/CS/O/R/K) — lowercase letter
      - cf_<letter> / cf_<storage_key> (auto-detected from CLIP / metadata)
    The LONG storage key is checked first so the post-rename canonical
    form wins. Matrix-style keys come next so the user's current widget
    pick beats a stale lowercase value left over from filename parsing.
    Without this, the rename function couldn't see entry["hair"] / ["background"]
    and built a stem missing every CLIP-detected field — exactly the
    "filename too short for the EXIF data" bug."""
    lk = letter.lower()
    sk = (storage_key or "").strip().lower()
    keys = []
    if sk: keys.append(sk)
    keys += [letter, label, lk]
    if sk: keys.append(f"cf_{sk}")
    keys.append(f"cf_{lk}")
    for _key in keys:
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
    for cf in CODED_FIELDS:
        letter, label, digits = cf[0], cf[1], cf[2]
        if letter == "J" or digits == 0:
            continue
        sk = _storage_key_for(cf)
        v = _entry_value_for_letter(entry, letter, label, storage_key=sk)
        # Compare against the parts entry the parser produces (long key);
        # also keep the short letter key in sync so legacy callers see it.
        if v and parts.get(sk, "") != v:
            parts[sk] = v
            parts[letter.lower()] = v
            _changed_field = True

    if not _changed_field and not pid and not pws:
        return False
    if not parts.get("timestamp"):
        parts["timestamp"] = julian_id_for_file(path)
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

    # Every coded field — entry's canonical long storage key is the source.
    # Backward-compat fallbacks (matrix uppercase, label, short letter,
    # cf_) handled by _entry_value_for_letter for entries that haven't
    # been migrated yet.
    for cf in CODED_FIELDS:
        letter, label, digits = cf[0], cf[1], cf[2]
        if letter == "J":
            continue
        sk = _storage_key_for(cf)
        v = _entry_value_for_letter(entry, letter, label, storage_key=sk)
        if digits == 0:
            # Boolean flag: parts[sk] = letter when on, "" when off.
            parts[sk] = letter if _bool_flag_on(v) else ""
            parts[letter.lower()] = parts[sk]   # legacy fallback
        else:
            if v:
                parts[sk] = v
                parts[letter.lower()] = v   # legacy fallback

    if not parts.get("timestamp"):
        parts["timestamp"] = julian_id_for_file(path)
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
        if not parts.get("timestamp"):
            parts["timestamp"] = j_code
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
                # Matrix groups can be referenced by either the bare
                # section name ("ModelImage") or the table-suffixed
                # form used in filename rules ("ModelImage_Table").
                # __section_styles__ is keyed on the bare name, so
                # strip "_Table" before looking up the style.
                _section_key = grp[:-6] if grp.endswith("_Table") else grp
                # Matrix groups: store the (single) wanted value in entry[section_key].
                if _styles.get(_section_key) == "matrix":
                    pick = next(iter(wanted), "")
                    if pick and entry.get(_section_key) != pick:
                        entry[_section_key] = pick
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
                    entry["face_angle"] = fa_dir   # single digit: Dir set, Vert defaults to none
                    changed = True
            if _needs_cs and shot_tag:
                cs_shot = _SHOT_TO_CS_SHOT.get(shot_tag)
                if cs_shot:
                    entry["camera_shot"] = cs_shot + "00"   # [Shot][Angle=0][Light=0]
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
    if _fa.get("orientation") and not entry.get("cf_o"):
        entry["cf_o"] = _fa["orientation"]
        changed = True
    if _fa.get("resolution") and not entry.get("cf_r"):
        entry["cf_r"] = _fa["resolution"]
        changed = True
    if _fa.get("frame_rate") and not entry.get("cf_k"):
        entry["cf_k"] = _fa["frame_rate"]
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
