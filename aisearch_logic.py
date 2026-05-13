import os, torch, shutil, cv2, threading
from PIL import Image
from sentence_transformers import SentenceTransformer, util

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Global lock for native-vision work (cv2 / FFmpeg / mediapipe / insightface).
# These libraries can corrupt the heap when called concurrently from multiple
# threads (e.g. inspect thread + watch-dir scan + extract_feature). Any code
# path that opens a video, runs face detection, or invokes a native vision
# pipeline must hold this lock for the duration.
NATIVE_VISION_LOCK = threading.RLock()

MODEL_NAME = 'clip-ViT-L-14'
EMBEDDING_DIM = 768   # clip-ViT-L-14 output dimension
EXT_IMG = ('.jpg', '.jpeg', '.png', '.bmp', '.webp')
EXT_VID = ('.mp4', '.mkv', '.mov', '.avi', '.webm')

device = "cuda" if torch.cuda.is_available() else "cpu"
# AISEARCH_SKIP_MODEL=1 — short-lived subprocesses (e.g. face_worker)
# that don't need CLIP can skip the 15-25s model load. `model` stays
# None; any code path that touches it will raise, which is the right
# signal that the caller leaked a non-face dependency into a face-only
# context.
if os.environ.get("AISEARCH_SKIP_MODEL") == "1":
    model = None
else:
    try:
        model = SentenceTransformer(MODEL_NAME, device=device)
    except torch.OutOfMemoryError:
        # SentenceTransformer's constructor pre-allocates on `device` —
        # passing device="cpu" here is essential. The previous fallback
        # called SentenceTransformer(MODEL_NAME) without a device arg,
        # so it auto-detected cuda and OOM'd before we could .to("cpu").
        print(f"[aisearch] CUDA OOM loading model — falling back to CPU")
        try:
            torch.cuda.empty_cache()
        except Exception:
            pass
        device = "cpu"
        model = SentenceTransformer(MODEL_NAME, device=device)


def load_db_logic(name):
    """DBが存在するか確認し、読み込む。
    Always loads via map_location="cpu" first — the features.pt may have
    been saved with CUDA tensors; if the current CUDA context is in a
    bad state (e.g. earlier assert, partial OOM), loading directly to
    device aborts the whole process with `cudaErrorAssert` SIGABRT
    instead of letting us fall back gracefully. After CPU load we try
    to move embeddings to device; on failure we keep the CPU copy."""
    db_path = os.path.join(_DATA_DIR, f"features_{name}.pt")
    if not os.path.exists(db_path):
        return (None, None)
    data = torch.load(db_path, map_location="cpu")
    if device != "cpu" and isinstance(data, dict) and "embeddings" in data:
        try:
            data["embeddings"] = data["embeddings"].to(device)
        except Exception:
            # CUDA unavailable or asserted — search still works on CPU,
            # just slower. Leave embeddings on CPU.
            pass
    return (data, db_path)

def get_sz_readable(p):
    """人間が読みやすいサイズ表記"""
    try:
        s = os.path.getsize(p)
        for u in ['B','KB','MB','GB']:
            if s < 1024: return f"{s:.1f} {u}"
            s /= 1024
    except: return "N/A"

_video_thumb_cache: dict = {}   # (path, mtime, "first"|"both") → numpy RGB array
_VIDEO_THUMB_CACHE_MAX  = 24    # 24 × ~700KB ≈ 17MB worst-case (after downscale)
_VIDEO_THUMB_MAX_DIM    = 512   # cap longest side; full-res 4K frames blow RAM


def _downscale_rgb(rgb, max_dim=_VIDEO_THUMB_MAX_DIM):
    """Downscale numpy RGB array so longest side ≤ max_dim. Saves ~16× RAM
    on a 4K source. cv2.INTER_AREA gives clean shrinking."""
    h, w = rgb.shape[:2]
    longest = max(h, w, 1)
    if longest <= max_dim:
        return rgb
    sc = max_dim / longest
    return cv2.resize(rgb, (max(1, int(w * sc)), max(1, int(h * sc))),
                      interpolation=cv2.INTER_AREA)


def get_video_thumbnail_rgb(path, first_only: bool = False):
    """Return a numpy RGB array of a video thumbnail (downscaled to fit
    ≤512px longest side so cache RAM stays bounded).
    first_only=False (default): combined first + last frame with green divider.
    first_only=True:           just the first frame — no last-frame seek.
    Cached by (path, mtime, mode). Holds NATIVE_VISION_LOCK during decode."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    mode = "first" if first_only else "both"
    key = (path, mtime, mode)
    cached = _video_thumb_cache.get(key)
    if cached is not None:
        return cached

    # Cross-key reuse: if caller wants "first" but we already decoded "both",
    # the work is wasted — the source frame still has to be decoded fresh
    # (we don't store frame1 separately). Mitigate by also caching the first
    # frame as a "first" entry whenever we decode "both" (see end of function).
    import numpy as np
    with NATIVE_VISION_LOCK:
        cap = cv2.VideoCapture(path)
        try:
            ret1, frame1 = cap.read()
            ret2, frame2 = False, None
            if not first_only:
                total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                if total > 1:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, total - 1)
                    ret2, frame2 = cap.read()
        finally:
            cap.release()
    if not ret1 or frame1 is None:
        return None
    # Downscale source frames before any concat — keeps RAM under control
    frame1 = _downscale_rgb(frame1)
    if ret2 and frame2 is not None:
        frame2 = _downscale_rgb(frame2)
        h, w = frame1.shape[:2]
        h2, w2 = frame2.shape[:2]
        # Match dimensions for concat (downscale may differ slightly)
        if w > h:
            if w2 != w:
                frame2 = cv2.resize(frame2, (w, max(1, int(h2 * w / max(w2, 1)))),
                                    interpolation=cv2.INTER_AREA)
            div_h = max(8, h // 48)
            div = np.zeros((div_h, w, 3), dtype=np.uint8); div[:, :] = [0, 200, 0]
            combined = np.concatenate([frame1, div, frame2], axis=0)
        else:
            if h2 != h:
                frame2 = cv2.resize(frame2, (max(1, int(w2 * h / max(h2, 1))), h),
                                    interpolation=cv2.INTER_AREA)
            div_w = max(8, w // 48)
            div = np.zeros((h, div_w, 3), dtype=np.uint8); div[:, :] = [0, 200, 0]
            combined = np.concatenate([frame1, div, frame2], axis=1)
    else:
        combined = frame1
    rgb = cv2.cvtColor(combined, cv2.COLOR_BGR2RGB)
    _video_thumb_cache[key] = rgb
    # Also populate the "first" entry whenever we decoded "both", so CLIP
    # later can hit the cache without re-decoding the same video file.
    if not first_only:
        first_rgb = cv2.cvtColor(frame1, cv2.COLOR_BGR2RGB)
        _video_thumb_cache[(path, mtime, "first")] = first_rgb
    if len(_video_thumb_cache) > _VIDEO_THUMB_CACHE_MAX:
        _video_thumb_cache.pop(next(iter(_video_thumb_cache)))
    # Drop large transient frame refs immediately. cv2's swscaler allocates
    # extra buffers on weird inputs (interlaced source, exotic colorspaces);
    # an explicit gc nudges Python to release them now rather than later.
    try:
        del frame1
        del frame2
        del combined
    except NameError:
        pass
    import gc as _gc; _gc.collect()
    return rgb


def _video_first_frame_pil(path):
    """Return a PIL.Image of the first usable frame, or None.
    Tries cv2 across several frames, then falls back to ffmpeg via subprocess
    so codecs cv2 can't handle (HEVC/AV1 in some builds) still work.
    MUST be called only from inside NATIVE_VISION_LOCK."""
    cap = cv2.VideoCapture(path)
    try:
        for _ in range(10):
            ret, frame = cap.read()
            if ret and frame is not None:
                return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    finally:
        cap.release()
    try:
        import subprocess, io
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", "0", "-i", path,
             "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
            capture_output=True, timeout=15)
        if proc.returncode == 0 and proc.stdout:
            return Image.open(io.BytesIO(proc.stdout)).convert("RGB")
    except Exception:
        pass
    return None


def extract_feature(path):
    """ファイルから特徴量を抽出.
    Returns the CLIP embedding tensor on success, or None on any failure.
    On failure, prints a one-line reason to stderr so the user can tell
    whether the file was *actually* unreadable vs CLIP-encode-failed
    (often the latter: a single CUDA assert poisons every subsequent
    encode, so the scan flags hundreds of fine files as 'unreadable')."""
    import sys as _sys
    img = None
    if path.lower().endswith(EXT_VID):
        # Reuse the shared video thumbnail cache (first-frame only) so
        # CLIP and the preview thumbnail share one cv2 decode per video.
        # Without this, a single video gets decoded twice through cv2 —
        # doubling memory pressure on swscaler-leaky files.
        try:
            rgb = get_video_thumbnail_rgb(path, first_only=True)
        except Exception as e:
            print(f"[extract_feature] video decode raised: {e}  path={path}",
                  file=_sys.stderr, flush=True)
            return None
        if rgb is None:
            print(f"[extract_feature] video decode returned None  path={path}",
                  file=_sys.stderr, flush=True)
            return None
        img = Image.fromarray(rgb)
    else:
        try:
            Image.MAX_IMAGE_PIXELS = None
            img = Image.open(path).convert('RGB')
            if img.width * img.height > 4000 * 4000:
                img.thumbnail((2048, 2048), Image.LANCZOS)
        except Exception as e_img:
            # Image open failed — could be a misnamed video. Try the
            # video path as a fallback (cv2/ffmpeg, first frame).
            try:
                rgb = get_video_thumbnail_rgb(path, first_only=True)
            except Exception as e_vid:
                print(f"[extract_feature] PIL failed ({e_img}) AND "
                      f"video fallback raised ({e_vid})  path={path}",
                      file=_sys.stderr, flush=True)
                return None
            if rgb is None:
                print(f"[extract_feature] PIL failed ({e_img}) AND "
                      f"video fallback returned None  path={path}",
                      file=_sys.stderr, flush=True)
                return None
            img = Image.fromarray(rgb)
    if img is None:
        print(f"[extract_feature] no image after open  path={path}",
              file=_sys.stderr, flush=True)
        return None
    # CLIP-ViT-L-14 expects 224×224. Anything larger is wasted memory.
    # Downscale before encoding so a 4K video frame doesn't allocate
    # gigabytes during model.encode.
    if max(img.width, img.height) > 512:
        img.thumbnail((512, 512), Image.LANCZOS)
    # torch.no_grad() prevents autograd graph buildup — without this each
    # encode keeps activations alive in memory, leaks ~hundreds of MB.
    import torch as _torch
    try:
        with _torch.no_grad():
            emb = model.encode(img, convert_to_tensor=True).to(device)
        return emb
    except Exception as e_gpu:
        # GPU encode failed. Most common cause: CUDA in asserted state
        # after an earlier OOM or kernel fault, which silently poisons
        # every subsequent encode. Don't write the file off as
        # "unreadable/corrupt" — it opened cleanly above. Retry on CPU
        # so the scan keeps producing real embeddings.
        print(f"[extract_feature] CLIP encode failed on device={device} "
              f"({type(e_gpu).__name__}: {e_gpu}), retrying on CPU  path={path}",
              file=_sys.stderr, flush=True)
        try:
            with _torch.no_grad():
                # device="cpu" param overrides the model's loaded device for
                # this call. Slower per-file than GPU but reliable when CUDA
                # is broken. Returns a CPU tensor; caller (extract_feature's
                # consumers) only need it for similarity math and don't
                # require GPU residency.
                emb = model.encode(img, convert_to_tensor=True, device="cpu")
            return emb
        except Exception as e_cpu:
            print(f"[extract_feature] CPU retry also failed: "
                  f"{type(e_cpu).__name__}: {e_cpu}  path={path}",
                  file=_sys.stderr, flush=True)
            return None
