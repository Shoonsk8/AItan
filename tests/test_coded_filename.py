"""Round-trip parsing of coded filenames.

These tests pin the format that pre-existing files on disk depend on. A
regression here means renamed files lose their attributes on next launch.
"""
import pytest

from aisearch_attrs import parse_coded_filename, CODED_FIELDS


def test_short_stem_returns_none():
    """No P token, no leading J — must return None so callers can skip."""
    assert parse_coded_filename("just_some_name") is None


def test_person_only():
    parsed = parse_coded_filename("P001")
    assert parsed is not None
    assert parsed["persons"] == ["001"]


def test_three_digit_field_extracted():
    """HC is 3 digits — pos 1=Length (rightmost), 2=Style, 3=Color (leftmost).
    The string is read LEFT→RIGHT as Color, Style, Length.

    parse_coded_filename returns LONG storage keys post-2026-05 rename;
    HC's storage key is "hair".
    """
    parsed = parse_coded_filename("P001HC0k5")
    assert parsed["hair"] == "0k5"
    # rightmost digit is "5" — that's pos 1 = Length under the canvas
    # convention codified in attr_viewer.py:_SUBPOS["HC"].
    assert parsed["hair"][-1] == "5"


def test_a_to_z_values_accepted():
    """Coded fields accept any base-36 char (0-9 + a-z), not just hex.
    Pre-2026-05 the regex was [0-9a-f] which silently dropped values
    above f — added when HC_Style was extended past 'a' (Twintail).
    """
    parsed = parse_coded_filename("P001HCxyz")
    assert parsed["hair"] == "xyz"


def test_uppercase_field_keys_strict():
    """Field keys are uppercase — a lowercase letter inside a value
    must NOT be parsed as another field's key. This is what lets us
    extend values to a-z without ambiguity."""
    # "k" inside HC value is a value digit, not a key.
    parsed = parse_coded_filename("P001HCk00FA10")
    assert parsed["hair"]       == "k00"
    assert parsed["face_angle"] == "10"


def test_full_chain_matches_user_screenshot():
    """The exact stem from the user's HC pos-mismatch bug report.
    Each field must land in the slot that built the filename, or
    canvas display + filename will silently disagree.

    Keys are LONG storage names ("hair", "background", …) — the same
    names entries store at and CLIP_AUTO_DETECT specs use.
    """
    stem = "P00dE03HC0k5FA13X80CLd119CS302O90R72J3bmsffwc"
    parsed = parse_coded_filename(stem)
    assert parsed is not None
    assert parsed["persons"]     == ["00d"]
    assert parsed["eyes"]        == "03"
    assert parsed["hair"]        == "0k5"
    assert parsed["face_angle"]  == "13"
    assert parsed["expression"]  == "80"
    assert parsed["clothing"]    == "d119"
    assert parsed["camera_shot"] == "302"
    assert parsed["orientation"] == "90"
    assert parsed["resolution"]  == "72"
    assert parsed["timestamp"]   == "3bmsffwc"


def test_pw_token_extracted():
    parsed = parse_coded_filename("P001PW002PW003E03")
    assert parsed["persons"] == ["001"]
    assert parsed["persons_with"] == ["002", "003"]


def test_boolean_flag_present():
    """Boolean / digits=0 fields appear as the bare letter, no value.
    Find a digits=0 field in CODED_FIELDS to keep this future-proof."""
    flag_fields = [(cf[0], (cf[3] if len(cf) >= 4 else cf[0].lower()))
                   for cf in CODED_FIELDS if cf[2] == 0]
    if not flag_fields:
        pytest.skip("no digits=0 fields in CODED_FIELDS")
    letter, storage = flag_fields[0]
    parsed = parse_coded_filename(f"P001{letter}")
    assert parsed[storage] == letter


def test_parser_idempotent_via_cache():
    """Same stem parsed twice returns equal results — guards against
    cache poisoning."""
    stem = "P001E03HC0k5"
    a = parse_coded_filename(stem)
    b = parse_coded_filename(stem)
    assert a == b


def test_build_uses_long_storage_keys():
    """build_coded_filename must accept long storage keys ("hair",
    "background") and emit the filename letters (HC, BG). After the
    2026-05 migration entries store at "hair", so a build that only
    read the short legacy key "hc" produced filenames missing every
    CLIP-detected field — the "filename too short" bug the user saw."""
    from aisearch_attrs import build_coded_filename
    parts = {
        "persons": ["013"], "persons_with": [],
        "hair":        "a15",
        "face_angle":  "00",
        "skin":        "0",
        "eyes":        "0a",
        "expression":  "34",
        "background":  "36",
        "camera_shot": "913",
        "clothing":    "3131",
        "orientation": "34",
        "resolution":  "72",
        "timestamp":   "3bocctxb",
    }
    stem = build_coded_filename(parts)
    # Every NON-DEFAULT field must show up. All-zero values like FA="00"
    # and SK="0" are the field's default and intentionally omitted from
    # the filename to keep stems short — that's existing behavior, not
    # part of the bug being pinned.
    for code, value in [("HC", "a15"), ("E", "0a"),
                        ("X", "34"),  ("BG", "36"), ("CS", "913"),
                        ("CL", "3131"), ("O", "34"), ("R", "72"),
                        ("J", "3bocctxb")]:
        assert f"{code}{value}" in stem, (
            f"build_coded_filename dropped {code}{value} — parts dict has "
            f"long storage keys but build was reading short keys only.\n"
            f"  got: {stem}")


def test_rename_function_reads_long_storage_keys():
    """rename_file_to_match_entry must read entry's long storage keys
    so the rebuilt filename includes every CLIP field. Pre-fix, the
    rename used _entry_value_for_letter which only checked uppercase
    letter / capital label / lowercase short key — never the long
    storage key — so post-migration entries produced truncated stems
    like P013E91J3bocctxb missing HC/FA/X/BG/CS/CL/O/R."""
    from aisearch_attrs import _entry_value_for_letter, CODED_FIELDS
    # Pretend entry is post-migration: long storage keys only.
    entry = {
        "person_id": "013",
        "hair":        "a15",
        "face_angle":  "00",
        "background":  "36",
        "expression":  "34",
        "clothing":    "3131",
    }
    # Find each CODED_FIELDS row by storage key and look it up.
    by_storage = {(cf[3] if len(cf) >= 4 else cf[0].lower()): cf for cf in CODED_FIELDS}
    for sk in ("hair", "face_angle", "background", "expression", "clothing"):
        cf = by_storage.get(sk)
        assert cf, f"CODED_FIELDS missing storage_key {sk!r}"
        letter, label, _, *_rest = cf
        v = _entry_value_for_letter(entry, letter, label, storage_key=sk)
        assert v == entry[sk], (
            f"_entry_value_for_letter({letter!r}, storage={sk!r}) returned "
            f"{v!r}, expected {entry[sk]!r}. Without the storage_key arg "
            f"the rename function silently drops every long-keyed field.")
