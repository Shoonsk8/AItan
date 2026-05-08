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
    """
    parsed = parse_coded_filename("P001HC0k5")
    assert parsed["hc"] == "0k5"
    # rightmost digit is "5" — that's pos 1 = Length under the canvas
    # convention codified in attr_viewer.py:_SUBPOS["HC"].
    assert parsed["hc"][-1] == "5"


def test_a_to_z_values_accepted():
    """Coded fields accept any base-36 char (0-9 + a-z), not just hex.
    Pre-2026-05 the regex was [0-9a-f] which silently dropped values
    above f — added when HC_Style was extended past 'a' (Twintail).
    """
    parsed = parse_coded_filename("P001HCxyz")
    assert parsed["hc"] == "xyz"


def test_uppercase_field_keys_strict():
    """Field keys are uppercase — a lowercase letter inside a value
    must NOT be parsed as another field's key. This is what lets us
    extend values to a-z without ambiguity."""
    # "k" inside HC value is a value digit, not a key.
    parsed = parse_coded_filename("P001HCk00FA10")
    assert parsed["hc"] == "k00"
    assert parsed["fa"] == "10"


def test_full_chain_matches_user_screenshot():
    """The exact stem from the user's HC pos-mismatch bug report.
    Each field must land in the slot that built the filename, or
    canvas display + filename will silently disagree."""
    stem = "P00dE03HC0k5FA13X80CLd119CS302O90R72J3bmsffwc"
    parsed = parse_coded_filename(stem)
    assert parsed is not None
    assert parsed["persons"] == ["00d"]
    assert parsed["e"]  == "03"
    assert parsed["hc"] == "0k5"
    assert parsed["fa"] == "13"
    assert parsed["x"]  == "80"
    assert parsed["cl"] == "d119"
    assert parsed["cs"] == "302"
    assert parsed["o"]  == "90"
    assert parsed["r"]  == "72"
    assert parsed["j"]  == "3bmsffwc"


def test_pw_token_extracted():
    parsed = parse_coded_filename("P001PW002PW003E03")
    assert parsed["persons"] == ["001"]
    assert parsed["persons_with"] == ["002", "003"]


def test_boolean_flag_present():
    """Boolean / digits=0 fields appear as the bare letter, no value.
    Find a digits=0 field in CODED_FIELDS to keep this future-proof."""
    flag_fields = [letter for letter, _, digits in CODED_FIELDS if digits == 0]
    if not flag_fields:
        pytest.skip("no digits=0 fields in CODED_FIELDS")
    letter = flag_fields[0]
    parsed = parse_coded_filename(f"P001{letter}")
    assert parsed[letter.lower()] == letter


def test_parser_idempotent_via_cache():
    """Same stem parsed twice returns equal results — guards against
    cache poisoning."""
    stem = "P001E03HC0k5"
    a = parse_coded_filename(stem)
    b = parse_coded_filename(stem)
    assert a == b
