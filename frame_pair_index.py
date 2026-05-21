"""Start/end frame embedding index for AItsugi.

Builds and searches a per-video (first_frame_embedding, last_frame_embedding)
index using AItan's CLIP encoder. The index lets AItsugi answer:

  Given video Q,
    find videos that end like Q starts        → candidates that come BEFORE Q
    find videos that start like Q ends        → candidates that come AFTER  Q

The corpus is the union of paths across all features_<project>.pt files under
AItan's data dir. Encoder is AItan's module-level CLIP-ViT-L-14 (768-d).

Index format (torch.save) at  AItsugi/data/frame_pairs.pt :
    {
      "paths":      [str, ...],
      "start_embs": Tensor[N, 768]   # L2-normalized
      "end_embs":   Tensor[N, 768]   # L2-normalized
      "mtimes":     [float, ...]     # source-file mtimes at index time
    }

CLI:
    python frame_pair_index.py build              # (re)build the index
    python frame_pair_index.py info               # show stats
    python frame_pair_index.py search VID.mp4 \
        --side end   --top 20                     # find successors of VID
    python frame_pair_index.py search VID.mp4 \
        --side start --top 20                     # find predecessors of VID
"""
from __future__ import annotations

import argparse
import os
import sys
import time

# ── AItan bridge ────────────────────────────────────────────────────────────
AITAN_DIR = "/mnt/1TBSSD/AItan"
if not os.path.isdir(AITAN_DIR):
    raise SystemExit(f"AItsugi index requires AItan at {AITAN_DIR} (not found).")
if AITAN_DIR not in sys.path:
    sys.path.insert(0, AITAN_DIR)

# Importing aisearch_logic triggers CLIP model load (~15-25 s on CPU,
# 2-5 s on GPU). Done once at module import.
import aisearch_logic as _lg                                        # noqa: E402
import cv2                                                           # noqa: E402
import torch                                                         # noqa: E402
from PIL import Image                                                # noqa: E402

EXT_VID = _lg.EXT_VID
EXT_IMG = _lg.EXT_IMG
EXT_ALL = EXT_VID + EXT_IMG
AITAN_DATA_DIR = os.path.join(AITAN_DIR, "data")
HERE = os.path.dirname(os.path.abspath(__file__))
INDEX_DIR = os.path.join(HERE, "data")
os.makedirs(INDEX_DIR, exist_ok=True)
INDEX_PATH = os.path.join(INDEX_DIR, "frame_pairs.pt")


# ── Frame extraction ────────────────────────────────────────────────────────
def _read_first_last_frames(path: str):
    """Return (first_pil, last_pil) or (None, None) on any failure.
    Held under AItan's NATIVE_VISION_LOCK so cv2/FFmpeg don't race with
    AItan's own scan threads when this module runs alongside it."""
    cap = None
    try:
        with _lg.NATIVE_VISION_LOCK:
            cap = cv2.VideoCapture(path, cv2.CAP_FFMPEG)
            ok1, frame_first = cap.read()
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            frame_last = None
            if total > 1:
                # Seek to last decodable frame. CAP_PROP_POS_FRAMES on
                # the very last index sometimes returns nothing on
                # certain codecs; back off one frame on miss.
                for off in (1, 2, 3):
                    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, total - off))
                    ok2, candidate = cap.read()
                    if ok2 and candidate is not None:
                        frame_last = candidate
                        break
        if not ok1 or frame_first is None:
            return None, None
        first_pil = Image.fromarray(cv2.cvtColor(frame_first, cv2.COLOR_BGR2RGB))
        last_pil = (Image.fromarray(cv2.cvtColor(frame_last, cv2.COLOR_BGR2RGB))
                    if frame_last is not None else first_pil)
        return first_pil, last_pil
    except Exception:
        return None, None
    finally:
        if cap is not None:
            cap.release()


def _encode_pil(img: Image.Image) -> torch.Tensor | None:
    """Encode a PIL image via AItan's CLIP model. L2-normalized."""
    if _lg.model is None:
        return None
    if max(img.width, img.height) > 512:
        img.thumbnail((512, 512), Image.LANCZOS)
    with torch.no_grad():
        emb = _lg.model.encode(img, convert_to_tensor=True)
    emb = emb.float().cpu()
    emb = emb / (emb.norm() + 1e-8)
    return emb


def encode_video_edges(path: str) -> tuple[torch.Tensor | None, torch.Tensor | None]:
    """Public helper used by the GUI: encode the start and end frame.
    Returns (start_emb, end_emb).

    For images: same embedding for both start and end (the image itself).
    For videos: first frame and last frame.
    Either may be None on decode/encode failure."""
    low = path.lower()
    if low.endswith(EXT_IMG):
        try:
            img = _lg.load_image_rgb(path, max_pixels=2048 * 2048)
        except Exception:
            return None, None
        if img is None:
            return None, None
        emb = _encode_pil(img)
        if emb is None:
            return None, None
        # Image acts as both START and END (zero-length sequence).
        return emb, emb.clone()
    # Videos: separate first/last frames.
    first, last = _read_first_last_frames(path)
    if first is None:
        return None, None
    return _encode_pil(first), _encode_pil(last)


# ── Corpus enumeration ──────────────────────────────────────────────────────
def _collect_video_paths_from_aitan() -> list[str]:
    """Union of video AND image paths across every features_*.pt in
    AItan/data. Name kept for backward compat; corpus is now (videos +
    images)."""
    seen: set[str] = set()
    out: list[str] = []
    if not os.path.isdir(AITAN_DATA_DIR):
        return out
    for fn in os.listdir(AITAN_DATA_DIR):
        if not (fn.startswith("features_") and fn.endswith(".pt")):
            continue
        if "." in fn[len("features_"):-len(".pt")]:
            # Skip backups like features_AIX.pt.bak-* (which have dots
            # before .pt).
            continue
        full = os.path.join(AITAN_DATA_DIR, fn)
        try:
            data = torch.load(full, map_location="cpu")
        except Exception as e:
            print(f"  [{fn}] load failed: {e}", file=sys.stderr)
            continue
        paths = (data or {}).get("paths") or []
        for p in paths:
            ap = os.path.abspath(p)
            if ap in seen:
                continue
            if not ap.lower().endswith(EXT_ALL):
                continue
            seen.add(ap)
            out.append(ap)
    return out


# ── Persistence ─────────────────────────────────────────────────────────────
def load_index() -> dict | None:
    if not os.path.exists(INDEX_PATH):
        return None
    try:
        return torch.load(INDEX_PATH, map_location="cpu", weights_only=False)
    except Exception as e:
        print(f"load_index failed: {e}", file=sys.stderr)
        return None


def _save_index(idx: dict) -> None:
    tmp = INDEX_PATH + ".tmp"
    torch.save(idx, tmp)
    os.replace(tmp, INDEX_PATH)


# ── Build / update ──────────────────────────────────────────────────────────
def build_index(progress_cb=None, full_rebuild: bool = False) -> dict:
    """Incrementally update the on-disk index.
    Skips videos whose mtime matches the cached entry. Rebuilds from scratch
    when full_rebuild=True or no cache exists."""
    if progress_cb is None:
        def progress_cb(done, total, msg=""):
            if done % 25 == 0 or done == total:
                print(f"  [{done}/{total}] {msg}")

    paths = _collect_video_paths_from_aitan()
    total = len(paths)
    progress_cb(0, total, f"corpus: {total} videos across AItan projects")

    # Existing index, keyed by abspath
    existing: dict[str, tuple[torch.Tensor, torch.Tensor, float]] = {}
    if not full_rebuild:
        prev = load_index()
        if prev and "paths" in prev:
            for i, p in enumerate(prev["paths"]):
                existing[p] = (prev["start_embs"][i],
                               prev["end_embs"][i],
                               prev["mtimes"][i])

    keep_paths: list[str] = []
    keep_start: list[torch.Tensor] = []
    keep_end: list[torch.Tensor] = []
    keep_mtime: list[float] = []

    encoded = 0
    skipped_cached = 0
    failed = 0
    for i, p in enumerate(paths, start=1):
        if not os.path.exists(p):
            continue
        try:
            mt = os.path.getmtime(p)
        except OSError:
            continue
        cached = existing.get(p)
        if cached is not None and abs(cached[2] - mt) < 1e-3:
            keep_paths.append(p)
            keep_start.append(cached[0])
            keep_end.append(cached[1])
            keep_mtime.append(cached[2])
            skipped_cached += 1
            if i % 50 == 0:
                progress_cb(i, total, f"cached {skipped_cached} new {encoded} fail {failed}")
            continue
        s_emb, e_emb = encode_video_edges(p)
        if s_emb is None or e_emb is None:
            failed += 1
            progress_cb(i, total, f"FAIL {os.path.basename(p)}")
            continue
        keep_paths.append(p)
        keep_start.append(s_emb)
        keep_end.append(e_emb)
        keep_mtime.append(mt)
        encoded += 1
        if i % 5 == 0:
            progress_cb(i, total, f"cached {skipped_cached} new {encoded} fail {failed}")

    if not keep_paths:
        progress_cb(total, total, "no videos encoded — index not written")
        return {"paths": [], "start_embs": torch.empty(0, 768),
                "end_embs": torch.empty(0, 768), "mtimes": []}

    idx = {
        "paths":      keep_paths,
        "start_embs": torch.stack(keep_start),
        "end_embs":   torch.stack(keep_end),
        "mtimes":     keep_mtime,
    }
    _save_index(idx)
    progress_cb(total, total,
                f"DONE indexed={len(keep_paths)} new={encoded} cached={skipped_cached} fail={failed}")
    return idx


# ── Search ──────────────────────────────────────────────────────────────────
def search(query_emb: torch.Tensor, side: str = "end",
           top_n: int = 20, exclude: str | None = None) -> list[tuple[str, float]]:
    """Cosine-similarity search.
    side='end'   → match against END column   (find videos whose END looks
                   like query_emb; these come BEFORE the query).
    side='start' → match against START column (find videos whose START looks
                   like query_emb; these come AFTER  the query).
    Returns [(abs_path, similarity), ...] sorted descending."""
    idx = load_index()
    if not idx or not idx["paths"]:
        return []
    column = idx["end_embs"] if side == "end" else idx["start_embs"]
    q = query_emb.float().cpu()
    q = q / (q.norm() + 1e-8)
    sims = (column @ q).tolist()
    pairs = list(zip(idx["paths"], sims))
    if exclude:
        ex = os.path.abspath(exclude)
        pairs = [(p, s) for p, s in pairs if p != ex]
    pairs.sort(key=lambda ps: ps[1], reverse=True)
    return pairs[:top_n]


# ── CLI ─────────────────────────────────────────────────────────────────────
def _cli() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build / update index from AItan corpus")
    b.add_argument("--full", action="store_true", help="ignore cached entries; re-encode everything")
    sub.add_parser("info", help="print index stats")
    s = sub.add_parser("search", help="search for matches to one video")
    s.add_argument("video", help="path to the query video")
    s.add_argument("--side", choices=["start", "end"], default="end",
                   help="'end' = find predecessors (their END matches query START); "
                        "'start' = find successors (their START matches query END)")
    s.add_argument("--top", type=int, default=20)
    args = p.parse_args()

    if args.cmd == "build":
        t0 = time.time()
        build_index(full_rebuild=args.full)
        print(f"build_index total {time.time()-t0:.1f}s")
        return 0
    if args.cmd == "info":
        idx = load_index()
        if not idx:
            print(f"no index at {INDEX_PATH}")
            return 1
        print(f"index: {INDEX_PATH}")
        print(f"  videos:    {len(idx['paths'])}")
        print(f"  start_emb: {tuple(idx['start_embs'].shape)} {idx['start_embs'].dtype}")
        print(f"  end_emb:   {tuple(idx['end_embs'].shape)} {idx['end_embs'].dtype}")
        return 0
    if args.cmd == "search":
        if not os.path.exists(args.video):
            print(f"video not found: {args.video}", file=sys.stderr)
            return 1
        # When searching by side='end', we use the query's START frame
        # (predecessors end where we start). And vice versa.
        s_emb, e_emb = encode_video_edges(args.video)
        if s_emb is None or e_emb is None:
            print("could not encode query frames", file=sys.stderr)
            return 1
        query = s_emb if args.side == "end" else e_emb
        hits = search(query, side=args.side, top_n=args.top,
                      exclude=args.video)
        print(f"{'similarity':>10}  path")
        for path, sim in hits:
            print(f"{sim:10.4f}  {path}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(_cli())
