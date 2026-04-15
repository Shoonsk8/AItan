import json, os, cv2, re, datetime, time as _time

_DIR      = os.path.dirname(os.path.abspath(__file__))
TAGS_FILE = os.path.join(_DIR, "attrs_tags.json")

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
        if   abs(ratio - 1.0) < 0.05:  result["o"] = "11"  # square
        elif ratio >= 1.7:              result["o"] = "09"  # 16:9 landscape
        elif ratio > 1.0:               result["o"] = "43"  # 4:3 landscape
        elif ratio <= 0.6:              result["o"] = "90"  # 9:16 portrait
        else:                           result["o"] = "34"  # 3:4 portrait

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
    """Return the attrs_tags JSON path for a project (or global default)."""
    if project and project != "default":
        p = os.path.join(_DIR, f"attrs_tags_{project}.json")
        if os.path.exists(p):
            return p
    return TAGS_FILE

def workspace_file_for_project(project=None):
    """Return the attribute_workspace JSON path for a project (or global default)."""
    if project and project != "default":
        return os.path.join(_DIR, f"attribute_workspace_{project}.json")
    return os.path.join(_DIR, "attribute_workspace.json")
FILENAME_RULES_FILE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "filename_rules.json")
RENAME_RULES_FILE    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "filename_rename_rules.json")
PERSON_REGISTRY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "person_registry.json")
PERSON_ALIASES_FILE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "person_aliases.json")
PERSON_RIGHT_GROUPS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "person_right_groups.json")

_DEFAULT_TAG_GROUPS = {
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
    # 2nd digit (left) = additional modifier
    "E_Additional": [
        ["0", "Naked"],              ["1", "Glasses"],
        ["2", "Neon"],               ["3", "Glasses + Neon"],
        ["b", "Bad"],                ["f", "Detail"],
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
    # 2nd digit = posture
    "PM_Posture": [
        ["0", "(none)"],      ["1", "Standing"],    ["2", "Sitting"],
        ["3", "Kneeling"],    ["4", "Lying"],       ["5", "Leaning"],
        ["6", "Crouching"],   ["7", "Handstand"],
    ],
    # 1st digit = motion
    "PM_Motion": [
        ["0", "Basic"],       ["1", "Still"],       ["2", "Walking"],
        ["3", "Running"],     ["4", "Dancing"],     ["5", "Looking at Camera"],
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
        ["0", "(none)"],       ["1", "Low Angle"],     ["2", "High Angle"],
        ["3", "Over-Shoulder"],["4", "Dutch Angle"],   ["5", "Bird's Eye"],
    ],
    # 1st digit = lighting
    "CS_Light": [
        ["0", "(none)"],       ["1", "Sunshine"],      ["2", "Sunset"],
        ["3", "Studio"],       ["4", "Cinematic"],     ["5", "Anime"],
        ["6", "Night"],
    ],

    # ── Background  BG[major][sub][specific]  ────────────────────────────────
    # 3rd digit = major category
    "BG_Major": [
        ["0", "Black BG"],     ["1", "White BG"],      ["2", "Green BG"],
        ["3", "Indoor"],       ["4", "Commercial Indoor"],
        ["5", "Outdoor"],      ["6", "Nature"],        ["8", "Space"],
    ],
    # 2nd digit = subcategory (context-dependent on major)
    "BG_Sub_Indoor": [
        ["0", "(none)"],   ["1", "Room"],       ["2", "Bathroom"],
        ["3", "Bedroom"],  ["4", "Living Room"],
    ],
    "BG_Sub_Outdoor": [
        ["0", "(none)"],        ["1", "Outside of House"], ["2", "Yard"],
        ["3", "Street"],        ["4", "Buildings"],
    ],
    "BG_Sub_Nature": [
        ["0", "Beach"],  ["1", "Ocean (no land)"], ["2", "Lake"],
        ["3", "Mountain"],
    ],
    "BG_Sub_Space": [
        ["0", "Stars only"],   ["1", "Moon surface"],
    ],

    # ── Orientation  O[width][height]  ───────────────────────────────────────
    "O_Preset": [
        ["f1", "15:1 (ultra-wide)"], ["09", "16:9 (landscape)"],
        ["90", "9:16 (portrait)"],   ["11", "Square"],
    ],

    # ── Resolution  R[w][h]  ─────────────────────────────────────────────────
    "R_Preset": [
        ["36", "360p"], ["48", "480p"],  ["72", "720p"],
        ["a8", "1080p"],["a4", "1440p"], ["04", "4K"],
        ["08", "8K"],
    ],

    # ── FrameRate  K[tens][units]  ───────────────────────────────────────────
    "K_Preset": [
        ["24", "24 fps"], ["30", "30 fps"], ["60", "60 fps"], ["b0", "120 fps"],
    ],

    # ── Misc tags (used by tag panel, not coded fields) ───────────────────────
    "Misc":     [["watermark", "Watermark"]],
    "Quality":  [["crap", "Crap"], ["ok", "OK"], ["good", "Good"]],
    "Audio":    [["no_sound", "No Sound"], ["sound", "Sound"], ["voice", "Voice"]],
    "Source":   [["comfyui", "ComfyUI"], ["a1111", "A1111"], ["aix", "AIX / MetadataReader"], ["other_src", "Other"]],
    "Variant":  [
        ["origin", "Origin"], ["base", "Base"], ["expression", "Expression"],
        ["clothing", "Clothing"], ["body_parts", "Body Parts"],
        ["background", "Background"], ["hair", "Hair"], ["style", "Style"],
        ["accessories", "Accessories"], ["mutant", "Mutant"], ["other", "Other"],
    ],
}

def _load_tag_groups(tags_file=None):
    merged = {grp: [tuple(pair) for pair in pairs]
              for grp, pairs in _DEFAULT_TAG_GROUPS.items()}
    path = tags_file or TAGS_FILE
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            for grp, pairs in raw.items():
                if grp.startswith("__"):
                    merged[grp] = pairs  # metadata keys: pass through unchanged
                elif isinstance(pairs, list):
                    merged[grp] = [tuple(pair) for pair in pairs]
                else:
                    merged[grp] = pairs
        except Exception:
            pass
    return merged

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
AUDIO_TAGS        = _tag_keys("Audio")
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
BG_MAJOR_TAGS     = _tag_keys("BG_Major")
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
    0x1: "Primary Emotions 基本の感情",
    0x2: "LOL Scale 笑いの段階",
    0x3: "Social & Subtle 社会的・微妙な合図",
    0x4: "Pain / Fear / Stress 痛み・恐怖・ストレス",
    0x5: "Embarrassment 当惑・恥じらい",
    0x6: "Intellectual 知的・思考",
    0x7: "Exhaustion / Boredom 疲労・退屈",
    0x8: "Sensory Pleasure 感覚的な快楽",
    0x9: "Intense Physiological 生理的な絶頂・激しい反応",
    0xa: "Oral & Tongue 口と舌の動き",
    0xb: "Reflexive / Habitual 無意識・習慣的な動作",
}

# Full X field reference table: code → (english_name, japanese_name, description)
# First hex digit = category (1–b), second = index within category (0–f)
EXPRESSION_TABLE = {
    # ── 1x Primary Emotions ──────────────────────────────────────────────────
    "10": ("Smile",          "笑顔",         "Ranging from a slight upturn of the corners of the mouth to a broad, toothy grin."),
    "11": ("Frown",          "しかめっ面",   "Pulling the eyebrows together and down, often with a downward-curving mouth."),
    "12": ("Scowl",          "睨み",         "Heavy, angry frown, often involving narrowed eyes."),
    "13": ("Sneer",          "冷笑",         "Raising one side of the upper lip; usually expresses contempt or dislike."),
    "14": ("Grimace",        "苦悶の表情",   "A twisted expression often caused by pain, disgust, or disapproval."),
    "15": ("Gasp",           "息を呑む",     "Wide eyes and an open mouth, usually signaling sudden shock or realization."),
    "16": ("Disgust",        "嫌悪",         "Wrinkled nose, raised upper lip, and squinted eyes."),
    "17": ("Awe",            "畏怖",         "Widened eyes, dropped jaw, and completely relaxed brow."),
    "18": ("Worry",          "心配",         "Furrowed brow with the inner corners of the eyebrows pulled up."),
    # ── 2x LOL Scale ─────────────────────────────────────────────────────────
    "20": ("Duchenne Smile", "本物の笑顔",   "A full, genuine smile that reaches the eyes (crow's feet)."),
    "21": ("Beam",           "満面の笑み",   "A radiant, wide-eyed smile of immense pride or sudden success."),
    "22": ("Guffaw",         "大笑い",       "Boisterous, mouth-wide-open laughter with the head back and eyes squeezed shut."),
    "23": ("Chuckle",        "くすくす笑い", "Soft, closed-mouth laughter where the cheeks puff out and the shoulders bounce."),
    "24": ("Wheeze",         "ひーひー笑い", "Red-faced, breathless, silent laughter with watery eyes and a strained mouth."),
    "25": ("Snicker",        "忍び笑い",     "Suppressed laughter with tight lips and flared nostrils."),
    "26": ("Grin",           "にっこり",     "A broad, often mischievous or highly satisfied smile that prominently shows the teeth."),
    "27": ("Exultation",     "歓喜",         "Pure triumph; eyes wide and bright, mouth open in a shout or cheer of joy."),
    # ── 3x Social & Subtle ───────────────────────────────────────────────────
    "30": ("Smirk",          "にやにや",         "One-sided, smug, or 'know-it-all' smile that conveys conceit or irony."),
    "31": ("Wink",           "ウィンク",         "One eye closed; signals a secret, joke, or flirtation."),
    "32": ("Raised Eyebrow", "片眉を上げる",     "A single brow lifted high; signals skepticism or curiosity."),
    "33": ("Double Brow",    "両眉を上げる",     "Both brows lifted high; signals a friendly greeting or 'Wow'."),
    "34": ("Blank Stare",    "無表情",           "Total lack of muscle movement; the 'thousand-yard stare' or poker face."),
    "35": ("Deadpan",        "真顔",             "A neutral, wooden face used specifically while delivering humor."),
    "36": ("Side Eye",       "横目",             "Looking askance out of the corners of the eyes; signals suspicion or judgment."),
    "37": ("Pout",           "口を尖らせる",     "Lower lip pushed out; signals 'childish' annoyance or sulking."),
    # ── 4x Pain / Fear / Stress ──────────────────────────────────────────────
    "40": ("Wince",          "顔をしかめる",     "Involuntary facial shrinking reacting to a sudden sting."),
    "41": ("Horror",         "恐怖",             "Eyes wide with dilated pupils, brows pulled up, mouth pulled back into a scream."),
    "42": ("Cower",          "萎縮",             "Face pulled down and away, eyes squinting and brow furrowed."),
    "43": ("Jaw Clench",     "食いしばり",       "Teeth ground together making the jawline rigid; signals suppressed rage or stress."),
    "44": ("Brow Furrow",    "眉間のしわ",       "Eyebrows knit together tightly in the center; signals deep focus or worry."),
    "45": ("Lip Tremble",    "唇の震え",         "Lower lip shakes uncontrollably; the precursor to crying."),
    # ── 5x Embarrassment ─────────────────────────────────────────────────────
    "50": ("Blush",          "赤面",             "Visible reddening of the face and ears due to embarrassment."),
    "51": ("Averted Gaze",   "目線を逸らす",     "Looking down or to the side repeatedly; inability to hold eye contact."),
    "52": ("Lip Bite",       "唇を噛む",         "Sucking in the lower lip; signals nervousness or being 'caught'."),
    "53": ("Sheepish Grin",  "照れ笑い",         "A tight-lipped, shy smile often following a mistake."),
    "54": ("Face Palm",      "顔を覆う",         "Eyes closed tight, brow furrowed in frustration."),
    "55": ("Guilty Look",    "罪悪感",           "Shifting eyes, a lowered head, and compressed lips."),
    # ── 6x Intellectual ──────────────────────────────────────────────────────
    "60": ("Pensive",        "哀愁・考え込む",   "Head tilted slightly, eyes looking up and away; indicates deep thought."),
    "61": ("Determined",     "決意",             "Set jaw, forward-leaning head, and a fixed, unblinking gaze."),
    "62": ("Confused",       "困惑",             "Head tilt, one brow lower than the other, and the mouth slightly open."),
    "63": ("Concentrated",   "集中",             "Lips pursed, narrowed eyes, and a still face."),
    "64": ("Doubtful",       "疑念",             "The lower lip is pulled up over the top lip; signals 'I'm not so sure'."),
    # ── 7x Exhaustion / Boredom ──────────────────────────────────────────────
    "70": ("Yawn",           "あくび",           "Mouth wide open, eyes watering slightly, and nostrils flaring."),
    "71": ("Droopy Eyes",    "眠そうな目",       "Eyelids halfway closed and heavy; signals extreme tiredness."),
    "72": ("Eye Roll",       "目を回す",         "Eyes rotating upward and away; signals impatience."),
    "73": ("Slack Face",     "弛緩した顔",       "All facial muscles completely relaxed; signals total boredom."),
    "74": ("Heavy Sigh",     "ため息",           "Mouth slightly open to exhale, shoulders dropping, eyes looking downward."),
    # ── 8x Sensory Pleasure ──────────────────────────────────────────────────
    "80": ("Serene Bliss",   "至福",             "Eyes gently closed, a tiny, faint smile, and completely relaxed brow."),
    "81": ("Relief Exhale",  "安堵の吐息",       "Eyes closing halfway, a long slow exhale through slightly parted lips."),
    "82": ("Satisfaction",   "深い満足",         "A firm, closed-mouth smile with a slow, knowing nod of the head."),
    "83": ("Trance Gaze",    "恍惚",             "Soft, unfocused eyes and a completely slack, relaxed jaw."),
    "84": ("Zest",           "活気",             "An energetic, wide-eyed look with a broad grin; signals high spirits."),
    # ── 9x Intense Physiological ─────────────────────────────────────────────
    "90": ("Euphoria",       "圧倒的な多幸感",   "Eyes squeezed tightly shut, head thrown back, mouth wide open in a peak of joy."),
    "91": ("Adrenaline",     "アドレナリン",     "Pupils dilated, wide-eyed stare, rapid shallow breathing through slightly parted lips."),
    "92": ("Overload",       "感覚の過負荷",     "Eyes rolling back slightly or fluttering, jaw dropped open, overwhelmed by intense sensation."),
    "93": ("Ecstatic",       "恍惚の解放",       "Eyes closing slowly, head tilting back, deep exhale as tension leaves the body."),
    # ── ax Oral & Tongue ─────────────────────────────────────────────────────
    "a0": ("Tongue Poke",    "舌を出す",         "The tip of the tongue is poked out between the lips momentarily."),
    "a1": ("Lip Lick",       "唇をなめる",       "A slow, circular movement of the tongue tip along the upper or lower lip."),
    "a2": ("Tongue Press",   "舌を押し付ける",   "The tongue is pressed against the inside of the cheek, creating a visible bulge."),
    "a3": ("Blep",           "べー",             "The tongue is extended fully and held flat; a teasing gesture."),
    # ── bx Reflexive / Habitual ──────────────────────────────────────────────
    "b0": ("Picking Nose",   "鼻をほじる",       "A finger is partially inserted into the nostril; signals boredom or distraction."),
    "b1": ("Cover Face",     "手で顔を覆う",     "Both palms pressed against the face; signals shame, grief, or hiding a laugh."),
    "b2": ("Wipe Tears",     "涙を拭う",         "A finger brushes under the lower eyelid to clear moisture."),
    "b3": ("Rubbing Eyes",   "目をこする",       "Using knuckles or palms to apply pressure to closed eyes; signals fatigue."),
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

CODED_FIELDS = [
    # (letter, label, digits)
    # digits: 2 or 3 = hex digit count; 0 = boolean flag (letter only, no value)
    # Each digit position has independent meaning — see _DEFAULT_TAG_GROUPS for sub-tables
    # ── Person / Subject ─────────────────────────────────────────────────────
    ("A",   "Animal",        3),   # animal ID  (A000 = no animal)
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
    ("T",   "Tool",          2),   # 00=nothing  ff=custom
    # ── Technical ────────────────────────────────────────────────────────────
    ("CS",  "CameraShot",    3),   # [3rd=shot area][2nd=angle][1st=lighting]
    ("BG",  "Background",    3),   # [3rd=major][2nd=sub][1st=specific]
    ("O",   "Orientation",   2),   # f1=15:1  09=16:9  90=9:16  11=square
    ("R",   "Resolution",    2),   # 36=360p 48=480p 72=720p a8=1080p a4=1440p 04=4K 08=8K
    ("K",   "FrameRate",     2),   # 24=24fps 30=30fps 60=60fps b0=120fps
    ("W",   "Watermark",     0),   # flag — W present = watermarked
    ("ED",  "Editable",      0),   # flag — ED present = app may auto-rename this file
    ("J",   "Timestamp",     8),   # Timestamp: yymmddHHMMSS as 8 base-36 chars — always last
]

# Person token pattern: P + (human 3-hex OR animal A+3-hex)  [not followed by W]
_PERSON_PAT = r'P(?!W)(A[0-9a-f]{3}|[0-9a-f]{3})'
# PersonWith token pattern: PW + 3-hex (multi-token, like P)
_PW_PAT = r'PW([0-9a-f]{3})'

# Regex for the non-person coded fields (after all P tokens are stripped)
def _field_pat(letter, digits):
    if digits == 0:
        return rf'(?P<{letter.lower()}>{letter})?'      # flag: just the letter, no value
    return rf'(?:{letter}(?P<{letter.lower()}>[0-9a-f]{{{digits}}}))?'

_FIELD_RE = re.compile(
    r'^'
    + ''.join(_field_pat(letter, digits) for letter, _, digits in CODED_FIELDS)
    + r'$'
    # NOTE: no re.IGNORECASE — uppercase = field key, lowercase = value
)

def parse_coded_filename(stem):
    """Parse a coded filename stem into a dict.
    Supports two formats:
      Person-first: P001PW002E01HC001...   (AI search files)
      Date-first:   J3bmrvfkvP001E01...    (regular photos; P optional)
    Returns {'persons': [...], 'persons_with': [...], 'j': '...', ...}
    or None if stem has neither P tokens nor a leading J field."""
    # Normalize: strip legacy -hex fingerprint suffix (size-based, no longer used)
    stem = re.sub(r'-[0-9a-f]{3,6}$', '', stem)
    persons = re.findall(_PERSON_PAT, stem)
    persons_with = re.findall(_PW_PAT, stem)
    if not persons:
        # Accept date-first stems: must start with J + 8 base-36 chars
        if not re.match(r'^J[0-9a-z]{8}', stem, re.IGNORECASE):
            return None
    # Strip all P and PW tokens before matching coded fields
    remainder = re.sub(r'PW[0-9a-f]{3}', '', stem)
    remainder = re.sub(r'P(?!W)(?:A[0-9a-f]{3}|[0-9a-f]{3})', '', remainder)
    m = _FIELD_RE.match(remainder if remainder else '')
    if m is None:
        return None
    result = {'persons': persons, 'persons_with': persons_with}
    result.update({k: (v or "") for k, v in m.groupdict().items()})
    return result

def build_coded_filename(parts, date_first=False):
    """Build a coded filename stem from a dict of parts.
    Person-first (default): P001[P002...]PW003...{fields}   — AI search files
    Date-first (date_first=True): J{8chars}[P001...]...     — regular photos, sorts by date
    parts keys: persons (list), persons_with (list), plus lowercase coded field keys."""
    persons = parts.get("persons", [])
    if date_first:
        j_val = parts.get("j", "")
        if not j_val:
            return ""  # date-first requires a J value
        stem = f'J{j_val.lower().zfill(8)[:8]}'
        for p in persons:
            stem += f'P{p}'
        for pw in parts.get("persons_with", []):
            pw = str(pw).strip().lower().zfill(3)[:3]
            if pw and pw != "000":
                stem += f'PW{pw}'
        for letter, _, digits in CODED_FIELDS:
            if letter == 'J':
                continue  # already placed at front
            val = parts.get(letter.lower(), "")
            if not val:
                continue
            if digits == 0:
                stem += letter
            else:
                val = str(val).strip().lower().zfill(digits)[:digits]
                if val != "0" * digits:
                    stem += f"{letter}{val}"
        return stem
    # Person-first (original behaviour)
    if not persons:
        return ""
    stem = ''.join(f'P{p}' for p in persons)
    for pw in parts.get("persons_with", []):
        pw = str(pw).strip().lower().zfill(3)[:3]
        if pw and pw != "000":
            stem += f'PW{pw}'
    for letter, _, digits in CODED_FIELDS:
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
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), f"faces_{project}.json")

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

def detect_or_assign_person_id(path, project, threshold=0.55, raise_errors=False):
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


def match_person_id(path, project, threshold=0.55):
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
    registry = load_person_registry()
    old_name = registry.pop(old_id, "")
    if old_name and not registry.get(new_id):
        registry[new_id] = old_name
    save_person_registry(registry)

    return attrs_data


def get_person_id_label(project, hex_id):
    """Return the optional name label for a person ID, or the ID itself.
    Registry is the canonical source; falls back to faces DB name."""
    if not hex_id:
        return ""
    registry = load_person_registry()
    name = registry.get(hex_id, "")
    if not name:
        db = load_faces_db(project)
        name = db.get("faces", {}).get(hex_id, {}).get("name", "")
    return name or hex_id

def set_person_name(project, hex_id, name):
    """Attach a human-readable name to a person ID.
    Writes to both person_registry (canonical) and faces DB (legacy compat)."""
    # Registry — canonical, project-independent
    registry = load_person_registry()
    if name:
        registry[hex_id] = name
    elif hex_id in registry:
        del registry[hex_id]
    save_person_registry(registry)
    # Faces DB — keep in sync for any legacy readers
    db = load_faces_db(project)
    db.setdefault("faces", {}).setdefault(hex_id, {})["name"] = name
    save_faces_db(project, db)


# ── Attrs storage ─────────────────────────────────────────────────────────────

def attrs_path(project):
    return f"attrs_{project}.json"

def load(project):
    p = attrs_path(project)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save(project, data):
    with open(attrs_path(project), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def get(attrs_data, path):
    return attrs_data.get(path, {})

def set_file(attrs_data, path, tags, note="", confirmed=False, project="", scene="",
             prompt="", neg_prompt="", seed="", meta=None, custom="", person_id="",
             speech="", editable=False):
    has_data = (tags or note or confirmed or project or scene or prompt or neg_prompt
                or seed or meta or custom or person_id or speech or editable)
    if not has_data:
        attrs_data.pop(path, None)
    else:
        entry = {
            "tags":       tags,
            "note":       note,
            "confirmed":  confirmed,
            "project":    project,
            "scene":      scene,
            "prompt":     prompt,
            "neg_prompt": neg_prompt,
            "seed":       seed,
            "custom":     custom,
            "person_id":  person_id,
            "speech":     speech,
            "editable":   editable,
        }
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
        # ComfyUI workflow in format comment tag
        comment = probe.get("format", {}).get("tags", {}).get("comment", "")
        if comment:
            try:
                outer = _json.loads(comment)
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

_fn_rules_cache = (None, None)  # (mtime, rules)

def load_filename_rules():
    global _fn_rules_cache
    if os.path.exists(FILENAME_RULES_FILE):
        try:
            mtime = os.path.getmtime(FILENAME_RULES_FILE)
            if _fn_rules_cache[0] == mtime:
                return _fn_rules_cache[1]
            with open(FILENAME_RULES_FILE, encoding="utf-8") as f:
                rules = json.load(f)
            _fn_rules_cache = (mtime, rules)
            return rules
        except Exception:
            pass
    return []

def save_filename_rules(rules):
    with open(FILENAME_RULES_FILE, "w", encoding="utf-8") as f:
        json.dump(rules, f, indent=2, ensure_ascii=False)

_person_registry_cache = (None, None)  # (mtime, data)

def load_person_registry():
    """Returns dict {id_str: description}, always includes 000."""
    global _person_registry_cache
    defaults = {"000": "No human/animal"}
    if os.path.exists(PERSON_REGISTRY_FILE):
        try:
            mtime = os.path.getmtime(PERSON_REGISTRY_FILE)
            if _person_registry_cache[0] == mtime:
                return _person_registry_cache[1]
            with open(PERSON_REGISTRY_FILE, encoding="utf-8") as f:
                data = json.load(f)
            result = {**defaults, **data}
            _person_registry_cache = (mtime, result)
            return result
        except Exception:
            pass
    return defaults

def save_person_registry(data):
    global _person_registry_cache
    _person_registry_cache = (None, None)  # invalidate cache on write
    with open(PERSON_REGISTRY_FILE, "w", encoding="utf-8") as f:
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


def detect_tags_from_filename(path, rules):
    """Return list of tag keys to add based on filename field rules.
    New format: {"pattern": "-0", "field": "P", "value": "001"}
    Old format (backward compat): {"pattern": "-front", "tags": ["front"]}
    Uses boundary matching so e.g. '-right' won't match '-right34'."""
    name = os.path.splitext(os.path.basename(path))[0].lower()
    tags = []
    for rule in rules:
        pattern = rule.get("pattern", "").lower()
        if not pattern:
            continue
        if not re.search(re.escape(pattern) + r'(?=[-.]|$)', name):
            continue
        if "tag_group" in rule:
            # Tag group rule: pattern in filename → add tag value to file's tags
            val = rule.get("value", "").strip()
            if val and val not in tags:
                tags.append(val)
        elif "field" in rule:
            # Coded-field format — handled by parse_filename_rules
            pass
        else:
            # Legacy tag format
            for t in rule.get("tags", []):
                if t and t not in tags:
                    tags.append(t)
    return tags

def parse_filename_rules(stem, rules):
    """Extract coded field values from a filename stem using rules.
    Returns dict of field→value, e.g. {"P": "001", "E": "0a"}.
    Supports:
      - Extract rule: {"field": "E", "extract": true, "digits": 2}
        → regex finds E followed by N hex digits in stem
      - Value rule:   {"pattern": "-0", "field": "P", "value": "001"}
        → exact pattern match sets fixed value"""
    name = stem.lower()
    result = {}
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
        else:
            pattern = rule.get("pattern", "").lower()
            if not pattern:
                continue
            if re.search(re.escape(pattern) + r'(?=[-.]|$)', name):
                result[rule["field"]] = rule.get("value", "")
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
    {"field": "hc", "pos": 2, "zero_is_none": True,  "threshold": 0.20, "options": [
        ("1", "a person with straight hair"),
        ("2", "a person with wavy hair"),
        ("3", "a person with curly hair"),
        ("4", "a person with voluminous puffy hair"),
        ("5", "a person with bob cut hair"),
        ("6", "a person with hair in a ponytail"),
        ("7", "a person with braided hair"),
        ("8", "a person with hair in a bun or tied up"),
        ("9", "a person with buzz cut or very short shaved head"),
    ]},
    # ── Hair length ───────────────────────────────────────────────────────────
    {"field": "hc", "pos": 3, "zero_is_none": True,  "threshold": 0.20, "options": [
        ("1", "a person with very short hair nearly shaved"),
        ("2", "a person with short hair above the ears"),
        ("3", "a person with medium length hair to the shoulders"),
        ("4", "a person with long hair below the shoulders"),
        ("5", "a person with very long hair down to the waist or lower"),
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
    {"field": "pm", "pos": 2, "zero_is_none": True,  "threshold": 0.20, "options": [
        ("1", "a person standing upright"),
        ("2", "a person sitting down on chair or floor"),
        ("3", "a person kneeling on one or both knees"),
        ("4", "a person lying down horizontally"),
        ("5", "a person leaning against a wall or surface"),
        ("6", "a person crouching or squatting down"),
        ("7", "a person doing a handstand upside down"),
    ]},
    # ── Motion ────────────────────────────────────────────────────────────────
    {"field": "pm", "pos": 1, "zero_is_none": True,  "threshold": 0.22, "options": [
        ("1", "a person posing still not moving"),
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
    {"field": "cs", "pos": 2, "zero_is_none": True,  "threshold": 0.22, "options": [
        ("1", "low angle shot looking upward at the subject"),
        ("2", "high angle shot looking downward at the subject"),
        ("3", "over-the-shoulder shot from behind a person"),
        ("4", "dutch angle or tilted camera creating strong diagonal"),
        ("5", "bird's eye view shot directly from overhead above"),
    ]},
    # ── Lighting ─────────────────────────────────────────────────────────────
    {"field": "cs", "pos": 1, "zero_is_none": True,  "threshold": 0.20, "options": [
        ("1", "bright sunny daylight outdoor lighting"),
        ("2", "warm golden sunset or sunrise lighting"),
        ("3", "clean professional studio lighting white background"),
        ("4", "dramatic cinematic lighting with strong shadows and contrast"),
        ("5", "flat colorful anime or illustration style"),
        ("6", "dark nighttime or very low-light scene"),
    ]},
    # ── Background major ─────────────────────────────────────────────────────
    {"field": "bg", "pos": 3, "zero_is_none": False, "threshold": 0.0,  "options": [
        ("0", "solid pure black background no details"),
        ("1", "solid pure white background no details"),
        ("2", "bright green screen or chromakey green background"),
        ("3", "indoor room or interior home setting"),
        ("4", "commercial indoor location restaurant office store cafe"),
        ("5", "outdoor urban street city buildings"),
        ("6", "natural outdoor setting trees grass forest field beach water"),
        ("8", "outer space stars cosmos planets"),
    ]},
    # ── Eye color ─────────────────────────────────────────────────────────────
    {"field": "e", "pos": 1, "zero_is_none": True,  "threshold": 0.22, "options": [
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
]

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


def auto_detect_clip_attrs(image_emb, existing_entry):
    """Use CLIP to auto-detect coded field values not already set.
    image_emb: 1-D tensor from logic.extract_feature().
    existing_entry: current attrs dict for the file (may be empty).
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

    def _get_working(field):
        if field not in working:
            digits = field_digits_map.get(field, 2)
            cur = existing_entry.get(field, "") or ""
            working[field] = cur.zfill(digits) if cur else "0" * digits
        return working[field]

    emb = image_emb
    if hasattr(emb, "dim") and emb.dim() == 1:
        emb = emb.unsqueeze(0)

    for i, spec in enumerate(CLIP_AUTO_DETECT):
        field       = spec["field"]
        pos         = spec["pos"]
        zero_is_none = spec.get("zero_is_none", True)
        threshold   = spec.get("threshold", 0.20)
        options     = spec["options"]

        current = _get_working(field)
        cur_digit = current[-pos] if pos <= len(current) else "0"

        if zero_is_none:
            if cur_digit != "0":
                continue   # already set by user — skip
        else:
            if field in existing_entry and existing_entry[field]:
                continue   # already has a value — skip

        # Score image against all option texts
        text_embs = cache[i]
        scores    = _stutil.cos_sim(emb, text_embs)[0]
        best_idx  = int(scores.argmax())
        best_score = float(scores[best_idx])

        if best_score < threshold:
            continue

        best_code = options[best_idx][0]
        if zero_is_none and best_code == "0":
            continue   # classified as "none" — leave unset

        # Write the detected digit into the working hex string
        val = list(current)
        val[-pos] = best_code
        working[field] = "".join(val)

    # Return only fields that actually changed from original
    result = {}
    for field, new_val in working.items():
        digits = field_digits_map.get(field, 2)
        orig = (existing_entry.get(field, "") or "").zfill(digits) or "0" * digits
        if new_val != orig and new_val != "0" * digits:
            result[field] = new_val
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
        _base = os.path.dirname(os.path.abspath(__file__))
        _pt_path = os.path.join(_base, f"features_{project}.pt")
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
                          skip_uncoded=True):
    """Update the person ID in the file's coded filename stem and rename it on disk.
    Preserves all other coded fields (O, R, J, etc.).
    flush_stores=True (default): immediately update faces/dups stores (single rename).
    flush_stores=False: skip store flush — caller must call flush_path_renames_to_stores
                        with the collected renames dict after the batch is done.
    skip_uncoded=True (default): do NOT rename files that are not already in coded
                        filename format — protects regular photos/videos from being
                        silently renamed when auto_rename is off.
                        Pass skip_uncoded=False only when auto_rename is explicitly on.
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
        if current_persons and current_persons[0] == pid:
            return path   # already correct — nothing to do
        parts["persons"] = [pid] + current_persons[1:]   # keep secondary persons
        new_stem = build_coded_filename(parts)
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
    new_stem = build_coded_filename(parts, date_first=True)
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
    """Return a tuple (sorted_persons, background) used for duplicate grouping.
    P001P002B0a1R04K30I001 and P001P002B0a1R02I002 → same group (same people, same bg).
    Falls back to stem for legacy filenames."""
    parsed = parse_coded_filename(stem)
    if parsed:
        persons = tuple(sorted(parsed.get("persons", [])))
        bg      = parsed.get("b", "")
        return (persons, bg)
    # Legacy format fallback: strip fingerprint and known suffixes
    s = _strip_fingerprint(stem)
    s = re.sub(r'^[0-9a-f]{3}-', '', s, flags=re.IGNORECASE)
    for pk in ["right34", "right", "left34", "left", "front", "back"]:
        s = re.sub(r'-' + re.escape(pk) + r'(?=[-.]|$)', '', s, flags=re.IGNORECASE)
    s = re.sub(r'-watermark(?=[-.]|$)', '', s, flags=re.IGNORECASE)
    return ((), s.strip('-'))

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
    rules = load_filename_rules()
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


def apply_pose_to_filename(path, pose_tag):
    """Rename file putting pose AFTER face suffix: {base}-{face}-{pose}-{watermark}.
    Returns new path, original path if unchanged, or None on error."""
    stem, ext = os.path.splitext(path)
    rules = load_filename_rules()
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
    """Return 'sound' or 'no_sound' for video files."""
    if not path.lower().endswith(('.mp4', '.mkv', '.mov', '.avi', '.webm')):
        return None
    try:
        import subprocess
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_name", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=5)
        return "sound" if result.stdout.strip() else "no_sound"
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

def auto_set_all(attrs_data, path, project):
    """Auto-detect and save: resolution, audio tag, AI source, prompt, seed, metadata."""
    entry        = get(attrs_data, path)
    was_editable = entry.get("editable", False)   # only rename files the app has previously touched
    current_tags = list(entry.get("tags", []))
    changed      = False

    # Resolution
    if not any(t in RESOLUTION_TAGS for t in current_tags):
        tag = detect_resolution_tag(path)
        if tag:
            current_tags = [t for t in current_tags if t not in RESOLUTION_TAGS] + [tag]
            changed = True

    # Audio tag for videos
    if not any(t in AUDIO_TAGS for t in current_tags):
        tag = detect_audio_tag(path)
        if tag:
            current_tags = [t for t in current_tags if t not in AUDIO_TAGS] + [tag]
            changed = True

    # AI source + prompt + seed
    prompt = entry.get("prompt", "")
    seed   = entry.get("seed", "")
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

    # Sync meta fields → top-level attrs (only fill if not already set)
    if not seed and meta.get("Seed"):
        seed = meta["Seed"]
        changed = True
    prompt     = entry.get("prompt", "")
    neg_prompt = entry.get("neg_prompt", "")
    if not prompt and meta.get("Prompt"):
        prompt = meta["Prompt"]
        changed = True
    if not neg_prompt and meta.get("NegPrompt"):
        neg_prompt = meta["NegPrompt"]
        changed = True

    # Shot type + pose direction (single MediaPipe pass, skip each if already set)
    needs_shot = not any(t in SHOT_TAGS for t in current_tags)
    needs_pose = not any(t in POSE_TAGS for t in current_tags)
    if needs_shot or needs_pose:
        shot_tag, pose_tag = detect_shot_and_pose(path)
        if needs_shot and shot_tag:
            current_tags = [t for t in current_tags if t not in SHOT_TAGS] + [shot_tag]
            changed = True
        if needs_pose and pose_tag:
            current_tags = [t for t in current_tags if t not in POSE_TAGS] + [pose_tag]
            changed = True

    # Filename-based tags + enforce rules
    fn_rules = load_filename_rules()
    if fn_rules:
        for t in detect_tags_from_filename(path, fn_rules):
            if t not in current_tags:
                current_tags.append(t)
                changed = True
        # Detect coded-field values from two-way rules (store only, no rename)
        two_way_rules = [r for r in fn_rules if r.get("field") and not r.get("one_way")]

    # One-way coded-field detection (e.g. -0 → P 001, reads only, no rename)
    one_way_rules = [r for r in fn_rules if r.get("field") and r.get("one_way")]
    if one_way_rules:
        stem_ow = os.path.splitext(os.path.basename(path))[0]
        od = parse_filename_rules(stem_ow, one_way_rules)
        if od:
            if "P" in od and od["P"] and not entry.get("person_id"):
                person_id = od["P"]
                changed = True
            # Store other detected coded fields as custom metadata if not already set
            for field, value in od.items():
                if field != "P" and value:
                    custom_key = f"cf_{field.lower()}"
                    if not entry.get(custom_key):
                        entry[custom_key] = value
                        changed = True

    # Sync person_id from coded filename (e.g. P001.jpg → person_id "001")
    person_id = entry.get("person_id", "")
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
                 editable=entry.get("editable", True))
        save(project, attrs_data)
    return attrs_data

# Keep old individual helpers pointing to auto_set_all for backwards compat
def auto_set_resolution(attrs_data, path, project):
    return auto_set_all(attrs_data, path, project)

def auto_set_ai_source(attrs_data, path, project):
    return auto_set_all(attrs_data, path, project)
