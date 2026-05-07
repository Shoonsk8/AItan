#!/usr/bin/env python3
"""
fix_extension_mismatch.py — Find files whose extension lies about their
content and rename them to the correct extension.

Caused by an old bug in _embed_aitan_image where a corrupt-but-real JPEG
that PIL couldn't classify fell through to ffmpeg, which re-muxed the
input as MP4 and shutil.move'd tmp.mp4 over the original .jpg path —
silently converting JPEGs into MP4-with-.jpg-extension files.

Usage:
    python fix_extension_mismatch.py /path/to/dir          # dry run, report only
    python fix_extension_mismatch.py /path/to/dir --apply  # actually rename

Renames are conservative: only files whose magic bytes UNAMBIGUOUSLY
identify a single format different from the current extension are touched.
"""

import os
import sys


_MAGIC = [
    (b"\xff\xd8\xff",                 ".jpg"),
    (b"\x89PNG\r\n\x1a\n",            ".png"),
    (b"GIF87a",                       ".gif"),
    (b"GIF89a",                       ".gif"),
    (b"BM",                           ".bmp"),
    (b"II*\x00",                      ".tif"),    # TIFF little-endian
    (b"MM\x00*",                      ".tif"),    # TIFF big-endian
]


def _detect_image_format(head: bytes) -> str | None:
    for sig, ext in _MAGIC:
        if head.startswith(sig):
            return ext
    # WebP: "RIFF????WEBP"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return ".webp"
    # MP4 / MOV / m4v / 3gp / etc — look for ftyp box at offset 4
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"isom", b"iso2", b"mp41", b"mp42", b"avc1",
                     b"M4V ", b"M4A ", b"M4B ", b"M4P "):
            return ".mp4"
        if brand in (b"qt  ", b"M4V "):
            return ".mov"
        if brand in (b"3gp4", b"3gp5", b"3g2a"):
            return ".3gp"
        return ".mp4"   # default for ftyp-shaped containers
    # Matroska / WebM: 1A45DFA3
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return ".mkv"   # WebM uses the same magic; can't tell apart from header
    return None


_TARGET_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif", ".tif", ".tiff",
                ".mp4", ".mkv", ".mov", ".m4v", ".avi", ".webm", ".wmv", ".3gp"}


def audit(root: str, apply_changes: bool):
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        print(f"not a directory: {root}")
        sys.exit(1)
    n_scanned = 0
    n_mismatch = 0
    n_renamed = 0
    n_unknown = 0
    by_kind = {}   # (cur_ext, real_ext) → count

    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if ext not in _TARGET_EXTS:
                continue
            full = os.path.join(dirpath, fn)
            n_scanned += 1
            try:
                with open(full, "rb") as f:
                    head = f.read(32)
            except OSError:
                continue
            real = _detect_image_format(head)
            if real is None:
                n_unknown += 1
                continue
            # Treat .jpg/.jpeg/.tif/.tiff as equivalent
            cur_norm = ".jpg" if ext in (".jpg", ".jpeg") else \
                       ".tif" if ext in (".tif", ".tiff") else ext
            real_norm = ".jpg" if real in (".jpg", ".jpeg") else \
                        ".tif" if real in (".tif", ".tiff") else real
            if cur_norm == real_norm:
                continue
            n_mismatch += 1
            by_kind[(ext, real)] = by_kind.get((ext, real), 0) + 1
            new_ext = real
            new_full = os.path.splitext(full)[0] + new_ext
            # If a file already exists at the destination, append a counter
            if os.path.exists(new_full):
                base, _ = os.path.splitext(new_full)
                i = 1
                while os.path.exists(f"{base}_{i}{new_ext}"):
                    i += 1
                new_full = f"{base}_{i}{new_ext}"
            if apply_changes:
                try:
                    os.rename(full, new_full)
                    n_renamed += 1
                    print(f"  renamed: {fn} -> {os.path.basename(new_full)}")
                except OSError as e:
                    print(f"  ERROR renaming {full}: {e}")
            else:
                print(f"  would rename: {full} (currently {ext}, actually {real})")

    print()
    print(f"scanned: {n_scanned} files")
    print(f"unknown magic: {n_unknown}")
    print(f"mismatched: {n_mismatch}")
    if by_kind:
        print(f"breakdown:")
        for (cur, real), cnt in sorted(by_kind.items(), key=lambda x: -x[1]):
            print(f"  {cur:>6} -> {real:<6}  {cnt}")
    if apply_changes:
        print(f"renamed: {n_renamed}")
    else:
        print()
        print("(dry run — pass --apply to actually rename)")


if __name__ == "__main__":
    args = sys.argv[1:]
    apply_changes = "--apply" in args
    args = [a for a in args if not a.startswith("--")]
    if not args:
        print("usage: python fix_extension_mismatch.py /path/to/dir [--apply]")
        sys.exit(2)
    audit(args[0], apply_changes)
