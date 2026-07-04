from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from typing import Callable, Optional

import cv2
import numpy as np

ProgressCallback = Optional[Callable[[int, str], None]]
JoinMode = str
AspectMode = str


def _replace_output_file(src: str, dst: str) -> None:
    if os.path.abspath(src) == os.path.abspath(dst):
        return
    if os.path.exists(dst):
        os.remove(dst)
    shutil.move(src, dst)


@dataclass
class VideoInfo:
    path: str
    fps: float
    width: int
    height: int
    frame_count: int


@dataclass
class MotionSample:
    index: int
    x: float
    y: float


@dataclass
class SeamMatch:
    index_a: int
    index_b: int
    distance: float


@dataclass
class AudioSegment:
    path: str
    start: float
    duration: float


@dataclass
class JoinResult:
    output: str
    cut_a_frames: int = 0
    cut_b_frames: int = 0
    seam_a_frame: Optional[int] = None
    seam_b_frame: Optional[int] = None

    def __str__(self) -> str:
        return self.output


def get_video_info(path: str) -> VideoInfo:
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    if width <= 0 or height <= 0:
        raise RuntimeError(f"Invalid video dimensions: {path}")
    return VideoInfo(path, fps, width, height, frame_count)


def _has_audio_stream(path: str) -> bool:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=index",
                "-of",
                "csv=p=0",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


def _mux_audio_segments(
    rendered_video: str,
    output: str,
    segments: list[AudioSegment],
    progress: ProgressCallback = None,
) -> str:
    playable = [segment for segment in segments if segment.duration > 0.01]
    with_audio = [_has_audio_stream(segment.path) for segment in playable]
    if not any(with_audio):
        if rendered_video != output:
            _replace_output_file(rendered_video, output)
        return output

    if shutil.which("ffmpeg") is None:
        if rendered_video != output:
            _replace_output_file(rendered_video, output)
        return output

    if progress:
        progress(99, "Adding audio...")

    tmp_output = os.path.join(
        tempfile.gettempdir(),
        f"aitsugi_{uuid.uuid4().hex[:8]}.audio_tmp.mp4",
    )
    cmd = ["ffmpeg", "-y", "-i", rendered_video]
    input_index = []
    next_input = 1
    for segment, ok in zip(playable, with_audio):
        if ok:
            cmd.extend(["-i", segment.path])
            input_index.append(next_input)
            next_input += 1
        else:
            input_index.append(None)

    # EVERY segment keeps its lane in the concat: a segment whose source has
    # no audio stream becomes generated SILENCE of the same duration. The
    # old code dropped soundless segments entirely, which closed the gap and
    # shifted every later segment's audio earlier by the dropped length —
    # a soundless LEFT clip made the right clip's audio play over the left
    # clip's video. All lanes are normalized to one format so concat can
    # splice silence and real audio safely.
    norm = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    filters: list[str] = []
    labels: list[str] = []
    for index, (segment, in_idx) in enumerate(zip(playable, input_index)):
        label = f"a{index}"
        if in_idx is not None:
            filters.append(
                f"[{in_idx}:a:0]atrim=start={segment.start:.6f}:duration={segment.duration:.6f},"
                f"asetpts=PTS-STARTPTS,{norm}[{label}]"
            )
        else:
            filters.append(
                f"aevalsrc=0|0:s=48000:d={segment.duration:.6f},"
                f"asetpts=PTS-STARTPTS,{norm}[{label}]"
            )
        labels.append(f"[{label}]")

    if len(labels) == 1:
        filter_complex = filters[0]
        audio_map = labels[0]
    else:
        filter_complex = ";".join(filters) + ";" + "".join(labels) + f"concat=n={len(labels)}:v=0:a=1[aout]"
        audio_map = "[aout]"

    cmd.extend(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            "0:v:0",
            "-map",
            audio_map,
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            tmp_output,
        ]
    )

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=120)
        if result.returncode != 0:
            err_lines = result.stderr.decode(errors="replace").strip().splitlines()
            reason = err_lines[-1] if err_lines else "ffmpeg audio mux failed"
            raise RuntimeError(reason)
        if os.path.exists(tmp_output) and os.path.getsize(tmp_output) > 0:
            _replace_output_file(tmp_output, output)
            if rendered_video != output and os.path.exists(rendered_video):
                os.remove(rendered_video)
            return output
    finally:
        if os.path.exists(tmp_output):
            try:
                os.remove(tmp_output)
            except OSError:
                pass

    if rendered_video != output:
        _replace_output_file(rendered_video, output)
    return output


def _resize_to(frame: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    width, height = size
    if frame.shape[1] == width and frame.shape[0] == height:
        return frame
    interpolation = cv2.INTER_AREA
    if width > frame.shape[1] or height > frame.shape[0]:
        interpolation = cv2.INTER_CUBIC
    return cv2.resize(frame, (width, height), interpolation=interpolation)


def _choose_larger_output_size(info_a: VideoInfo, info_b: VideoInfo) -> tuple[int, int]:
    area_a = info_a.width * info_a.height
    area_b = info_b.width * info_b.height
    if area_b > area_a:
        return info_b.width, info_b.height
    return info_a.width, info_a.height


def _read_edge_frame(path: str, edge: str) -> Optional[np.ndarray]:
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return None
    try:
        if edge == "end":
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame_count - 1))
        ok, frame = cap.read()
        return frame if ok else None
    finally:
        cap.release()


def _choose_output_size(video_a: str, video_b: str, info_a: VideoInfo, info_b: VideoInfo) -> tuple[int, int]:
    """Choose a canvas that preserves the best seam framing.

    Similar aspect ratios still use the larger source canvas. When aspect ratios
    differ, try both source canvases at the A-end/B-start seam and keep the one
    with the lower visual distance. This avoids blindly prioritizing A when A is
    larger but B has the framing that actually lines up at the join.
    """
    larger = _choose_larger_output_size(info_a, info_b)
    ratio_a = info_a.width / info_a.height
    ratio_b = info_b.width / info_b.height
    ratio_delta = abs(ratio_a - ratio_b) / max(ratio_a, ratio_b)
    if ratio_delta <= 0.02:
        return larger

    frame_a = _read_edge_frame(video_a, "end")
    frame_b = _read_edge_frame(video_b, "start")
    if frame_a is None or frame_b is None:
        return larger

    candidates = [
        (info_a.width, info_a.height),
        (info_b.width, info_b.height),
    ]
    best_size = larger
    best_score = float("inf")
    for size in candidates:
        fitted_a = _fit_to_size(frame_a, size, "cover")
        fitted_b = _fit_to_size(frame_b, size, "cover")
        distance, raw_distance = _frame_visual_distance(fitted_a, fitted_b)
        score = distance + (raw_distance / 255.0)
        if score < best_score:
            best_score = score
            best_size = size
    return best_size


def _choose_output_fps(info_a: VideoInfo, info_b: VideoInfo) -> float:
    fps_a = info_a.fps if info_a.fps > 1 else 30.0
    fps_b = info_b.fps if info_b.fps > 1 else 30.0
    return max(fps_a, fps_b)


def _resolve_auto_aspect(info_a: VideoInfo, info_b: VideoInfo, size: tuple[int, int]) -> tuple[str, str]:
    # Auto never distorts. It fills the selected canvas by center-cropping.
    return "cover", "cover"


def _fit_to_size(frame: np.ndarray, size: tuple[int, int], mode: str = "cover") -> np.ndarray:
    """Resize without distorting aspect ratio.

    cover fills the target size and center-crops overflow.
    contain letterboxes/pillarboxes inside the target size.
    pad keeps smaller frames at original size and pads around them.
    """
    target_w, target_h = size
    src_h, src_w = frame.shape[:2]
    if src_w == target_w and src_h == target_h:
        return frame
    if mode == "stretch":
        return _resize_to(frame, size)

    if mode == "pad":
        scale = min(1.0, target_w / src_w, target_h / src_h)
    elif mode == "contain":
        scale = min(target_w / src_w, target_h / src_h)
    else:
        scale = max(target_w / src_w, target_h / src_h)

    new_w = max(1, int(round(src_w * scale)))
    new_h = max(1, int(round(src_h * scale)))

    if mode == "contain":
        if target_w - new_w in (1, 2):
            new_w = target_w
        if target_h - new_h in (1, 2):
            new_h = target_h

    interpolation = cv2.INTER_AREA if scale <= 1.0 else cv2.INTER_CUBIC
    resized = cv2.resize(frame, (new_w, new_h), interpolation=interpolation)

    if mode in {"contain", "pad"}:
        output = np.zeros((target_h, target_w, 3), dtype=np.uint8)
        x = (target_w - new_w) // 2
        y = (target_h - new_h) // 2
        output[y : y + new_h, x : x + new_w] = resized
        return output

    x = max(0, (new_w - target_w) // 2)
    y = max(0, (new_h - target_h) // 2)
    return resized[y : y + target_h, x : x + target_w]


def make_numbered_output_path(path: str) -> str:
    """Return path, or path with _1, _2, ... before extension if it exists."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    filename = os.path.basename(path)
    stem, ext = os.path.splitext(filename)
    if not ext:
        ext = ".mp4"
    candidate = os.path.join(directory, f"{stem}{ext}")
    if not os.path.exists(candidate):
        return candidate

    index = 1
    while True:
        candidate = os.path.join(directory, f"{stem}_{index}{ext}")
        if not os.path.exists(candidate):
            return candidate
        index += 1


def _color_match(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Lightweight mean/std color matching: make source look closer to target."""
    src = source.astype(np.float32)
    tgt = target.astype(np.float32)
    out = src.copy()
    for c in range(3):
        src_mean, src_std = src[:, :, c].mean(), src[:, :, c].std() + 1e-6
        tgt_mean, tgt_std = tgt[:, :, c].mean(), tgt[:, :, c].std() + 1e-6
        out[:, :, c] = (src[:, :, c] - src_mean) * (tgt_std / src_std) + tgt_mean
    return np.clip(out, 0, 255).astype(np.uint8)


def _color_match_amount(source: np.ndarray, target: np.ndarray, amount: float) -> np.ndarray:
    """Apply color matching partially so transitions can return to source color."""
    amount = max(0.0, min(1.0, amount))
    if amount <= 0.0:
        return source
    matched = _color_match(source, target)
    if amount >= 1.0:
        return matched
    return cv2.addWeighted(source, 1.0 - amount, matched, amount, 0)


def _flow(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    gray_a = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
    gray_b = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
    return cv2.calcOpticalFlowFarneback(
        gray_a,
        gray_b,
        None,
        pyr_scale=0.5,
        levels=4,
        winsize=35,
        iterations=4,
        poly_n=7,
        poly_sigma=1.5,
        flags=0,
    )


def _warp_with_flow(frame: np.ndarray, flow: np.ndarray, amount: float) -> np.ndarray:
    h, w = frame.shape[:2]
    grid_x, grid_y = np.meshgrid(np.arange(w), np.arange(h))
    map_x = (grid_x - amount * flow[:, :, 0]).astype(np.float32)
    map_y = (grid_y - amount * flow[:, :, 1]).astype(np.float32)
    return cv2.remap(frame, map_x, map_y, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def _estimate_translation(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    gray_a = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gray_b = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY).astype(np.float32)
    shift, response = cv2.phaseCorrelate(gray_b, gray_a)
    if response < 0.04:
        return 0.0, 0.0
    dx, dy = shift
    h, w = gray_a.shape
    if abs(dx) > w * 0.6 or abs(dy) > h * 0.6:
        return 0.0, 0.0
    return float(dx), float(dy)


def _median_flow_translation(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    flow = _flow(a, b)
    magnitude = np.hypot(flow[:, :, 0], flow[:, :, 1])
    moving = magnitude > np.percentile(magnitude, 70)
    if not np.any(moving):
        return 0.0, 0.0
    return float(np.median(flow[:, :, 0][moving])), float(np.median(flow[:, :, 1][moving]))


def _motion_centroid(prev_frame: np.ndarray, frame: np.ndarray) -> Optional[tuple[float, float]]:
    diff = cv2.absdiff(prev_frame, frame)
    gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
    threshold = max(12, int(gray.mean() + gray.std()))
    mask = gray > threshold
    if int(mask.sum()) < 20:
        return None

    ys, xs = np.where(mask)
    h, w = gray.shape
    if len(xs) > w * h * 0.35:
        return None
    return float(xs.mean()), float(ys.mean())


def _collect_motion_samples(path: str, size: tuple[int, int]) -> list[MotionSample]:
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {path}")

    samples: list[MotionSample] = []
    try:
        ok, prev = cap.read()
        if not ok:
            return samples
        prev = _resize_to(prev, size)
        index = 1
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frame = _resize_to(frame, size)
            center = _motion_centroid(prev, frame)
            if center:
                samples.append(MotionSample(index, center[0], center[1]))
            prev = frame
            index += 1
    finally:
        cap.release()
    return samples


def _read_normalized_frames(
    path: str,
    size: tuple[int, int],
    target_fps: float,
    source_fps: Optional[float] = None,
    aspect_mode: str = "cover",
) -> list[np.ndarray]:
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {path}")

    frames: list[np.ndarray] = []
    try:
        native_fps = source_fps or cap.get(cv2.CAP_PROP_FPS) or target_fps
        native_fps = native_fps if native_fps > 1 else target_fps
        target_fps = target_fps if target_fps > 1 else native_fps
        frame_index = 0
        next_output_time = 0.0
        last_frame: Optional[np.ndarray] = None

        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frame = _fit_to_size(frame, size, aspect_mode)
            last_frame = frame

            frame_start = frame_index / native_fps
            frame_end = (frame_index + 1) / native_fps
            while next_output_time < frame_end - 1e-9:
                if next_output_time >= frame_start - 1e-9:
                    frames.append(frame.copy())
                next_output_time += 1.0 / target_fps
            frame_index += 1

        if not frames and last_frame is not None:
            frames.append(last_frame)
    finally:
        cap.release()

    return frames


def _motion_samples_from_frames(frames: list[np.ndarray]) -> list[MotionSample]:
    samples: list[MotionSample] = []
    for index in range(1, len(frames)):
        center = _motion_centroid(frames[index - 1], frames[index])
        if center:
            samples.append(MotionSample(index, center[0], center[1]))
    return samples


def _find_matching_motion_seam_from_frames(
    frames_a: list[np.ndarray],
    frames_b: list[np.ndarray],
    window: int = 60,
) -> Optional[SeamMatch]:
    samples_a = _motion_samples_from_frames(frames_a)
    samples_b = _motion_samples_from_frames(frames_b)
    if not samples_a or not samples_b:
        return None

    # A join seam must be near the actual boundary: the tail of A and the
    # head of B. Searching all of A can find a coincidental motion match early
    # in a long base clip, then cut the output shorter than the base video.
    start_a = max(0, len(frames_a) - window)
    end_b = min(len(frames_b), window)
    samples_a = [sample for sample in samples_a if sample.index >= start_a]
    samples_b = [sample for sample in samples_b if sample.index < end_b]
    if not samples_a or not samples_b:
        return None

    centers_b = np.array([(sample.x, sample.y) for sample in samples_b], dtype=np.float32)
    best: Optional[SeamMatch] = None
    best_keep = -1

    for sample_a in samples_a:
        delta = centers_b - np.array((sample_a.x, sample_a.y), dtype=np.float32)
        distances = np.hypot(delta[:, 0], delta[:, 1])
        b_pos = int(np.argmin(distances))
        sample_b = samples_b[b_pos]
        distance = float(distances[b_pos])

        # When several frames match equally well, prefer a splice that keeps a
        # useful amount from both sides instead of cutting at the very end.
        keep_balance = min(sample_a.index + 1, max(0, len(frames_b) - sample_b.index - 1))
        if best is None or distance < best.distance - 0.25 or (
            abs(distance - best.distance) <= 0.25 and keep_balance > best_keep
        ):
            best = SeamMatch(sample_a.index, sample_b.index, distance)
            best_keep = keep_balance

    if not best:
        return None

    h, w = frames_a[0].shape[:2]
    max_reasonable_distance = max(6.0, min(w, h) * 0.06)
    if best.distance > max_reasonable_distance:
        return None
    return best


def _frame_visual_distance(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    gray_a = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
    gray_b = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
    small_a = cv2.resize(gray_a, (96, 160), interpolation=cv2.INTER_AREA).astype(np.float32)
    small_b = cv2.resize(gray_b, (96, 160), interpolation=cv2.INTER_AREA).astype(np.float32)

    raw_distance = float(np.mean(np.abs(small_a - small_b)))
    norm_a = (small_a - small_a.mean()) / (small_a.std() + 1e-6)
    norm_b = (small_b - small_b.mean()) / (small_b.std() + 1e-6)
    normalized_distance = float(np.mean(np.abs(norm_a - norm_b)))
    return normalized_distance, raw_distance


def _visual_edge_match(distance: float, raw_distance: float) -> bool:
    exact_match = distance <= 0.18 or raw_distance <= 10.0
    home_position_match = distance <= 0.36 and raw_distance <= 32.0
    return exact_match or home_position_match


def _find_matching_visual_seam_from_frames(
    frames_a: list[np.ndarray],
    frames_b: list[np.ndarray],
    window: int = 60,
) -> Optional[SeamMatch]:
    if not frames_a or not frames_b:
        return None

    edge_distance, edge_raw = _frame_visual_distance(frames_a[-1], frames_b[0])
    if _visual_edge_match(edge_distance, edge_raw):
        return SeamMatch(len(frames_a) - 1, 0, edge_distance)

    start_a = max(0, len(frames_a) - window)
    end_b = min(len(frames_b), window)
    best: Optional[SeamMatch] = None
    best_raw = float("inf")

    for index_a in range(start_a, len(frames_a)):
        for index_b in range(end_b):
            distance, raw_distance = _frame_visual_distance(frames_a[index_a], frames_b[index_b])
            if best is None or distance < best.distance:
                best = SeamMatch(index_a, index_b, distance)
                best_raw = raw_distance

    if not best:
        return None

    if best.distance <= 0.18 or best_raw <= 10.0:
        return best
    return None


def _shift_frame(frame: np.ndarray, dx: float, dy: float) -> np.ndarray:
    if abs(dx) < 0.01 and abs(dy) < 0.01:
        return frame
    matrix = np.float32([[1, 0, dx], [0, 1, dy]])
    h, w = frame.shape[:2]
    return cv2.warpAffine(frame, matrix, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def _align_head_to_tail(tail_a: list[np.ndarray], head_b: list[np.ndarray]) -> tuple[list[np.ndarray], tuple[float, float]]:
    if len(tail_a) < 2 or not head_b:
        return head_b, (0.0, 0.0)

    last_a = tail_a[-1]
    first_b = head_b[0]

    dx = 0.0
    dy = 0.0
    used_motion_alignment = False
    if len(head_b) >= 2:
        center_a = _motion_centroid(tail_a[-2], last_a)
        center_b = _motion_centroid(head_b[0], head_b[1])
        prev_center_a = _motion_centroid(tail_a[-3], tail_a[-2]) if len(tail_a) >= 3 else None
        if center_a and center_b:
            vx = center_a[0] - prev_center_a[0] if prev_center_a else 0.0
            vy = center_a[1] - prev_center_a[1] if prev_center_a else 0.0
            dx = center_a[0] + vx - center_b[0]
            dy = center_a[1] + vy - center_b[1]
            used_motion_alignment = True

    if not used_motion_alignment:
        dx, dy = _estimate_translation(last_a, first_b)
        try:
            vx, vy = _median_flow_translation(tail_a[-2], last_a)
            dx += vx
            dy += vy
        except cv2.error:
            pass

    h, w = last_a.shape[:2]
    if abs(dx) < 1.0 and abs(dy) < 1.0:
        return head_b, (0.0, 0.0)
    if abs(dx) > w * 0.75 or abs(dy) > h * 0.75:
        return head_b, (0.0, 0.0)

    return [_shift_frame(frame, dx, dy) for frame in head_b], (dx, dy)


def make_transition_frames(
    frame_a: np.ndarray,
    frame_b: np.ndarray,
    steps: int = 12,
    color_match: bool = True,
) -> list[np.ndarray]:
    """Create optical-flow interpolated frames between frame_a and frame_b.

    Returned frames exclude frame_a and frame_b themselves.
    """
    if steps <= 0:
        return []

    if frame_a.shape != frame_b.shape:
        frame_b = _resize_to(frame_b, (frame_a.shape[1], frame_a.shape[0]))

    frames: list[np.ndarray] = []
    for i in range(1, steps + 1):
        t = i / (steps + 1)
        # Strongly match B near A, then fade back to B's original color before
        # the final B frame. Otherwise the second side of the join can jump.
        b_for_transition = _color_match_amount(frame_b, frame_a, 1.0 - t) if color_match else frame_b
        flow_ab = _flow(frame_a, b_for_transition)
        flow_ba = _flow(b_for_transition, frame_a)
        warped_a = _warp_with_flow(frame_a, flow_ab, t)
        warped_b = _warp_with_flow(b_for_transition, flow_ba, 1.0 - t)
        blended = cv2.addWeighted(warped_a, 1.0 - t, warped_b, t, 0)
        frames.append(blended)
    return frames


def _smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)


def make_overlap_transition(
    tail_a: list[np.ndarray],
    head_b: list[np.ndarray],
    color_match: bool = True,
) -> list[np.ndarray]:
    """Blend A's tail and B's head as an overlap transition.

    This replaces the seam region instead of inserting a morph between two
    still frames, so motion across the join stays closer to normal video.
    """
    count = min(len(tail_a), len(head_b))
    if count <= 0:
        return []

    frames: list[np.ndarray] = []
    for i in range(count):
        a = tail_a[i]
        b = head_b[i]
        if a.shape != b.shape:
            b = _resize_to(b, (a.shape[1], a.shape[0]))

        t = _smoothstep((i + 1) / (count + 1))
        # Fade color matching out as the overlap approaches normal B frames.
        # This keeps the A-side connection smooth without creating a B-side pop.
        b_for_transition = _color_match_amount(b, a, 1.0 - t) if color_match else b

        try:
            flow_ab = _flow(a, b_for_transition)
            flow_ba = _flow(b_for_transition, a)
            warped_a = _warp_with_flow(a, flow_ab, min(t, 0.85))
            warped_b = _warp_with_flow(b_for_transition, flow_ba, min(1.0 - t, 0.85))
        except cv2.error:
            warped_a = a
            warped_b = b_for_transition

        frames.append(cv2.addWeighted(warped_a, 1.0 - t, warped_b, t, 0))
    return frames


def join_videos(
    video_a: str,
    video_b: str,
    output: str,
    transition_frames: int = 12,
    progress: ProgressCallback = None,
    mode: JoinMode = "auto",
    aspect: AspectMode = "contain",
    keep_audio: bool = True,
    return_info: bool = False,
    output_size: Optional[tuple[int, int]] = None,
    output_fps: Optional[float] = None,
) -> str:
    if not os.path.exists(video_a):
        raise FileNotFoundError(video_a)
    if not os.path.exists(video_b):
        raise FileNotFoundError(video_b)
    if mode not in {"auto", "cut", "add"}:
        raise ValueError("mode must be one of: auto, cut, add")
    if aspect not in {"auto", "contain", "pad", "cover", "stretch"}:
        raise ValueError("aspect must be one of: auto, contain, pad, cover, stretch")

    auto_transition = transition_frames < 0
    if auto_transition:
        transition_frames = 16

    info_a = get_video_info(video_a)
    info_b = get_video_info(video_b)
    size = output_size or _choose_output_size(video_a, video_b, info_a, info_b)
    fps = output_fps or _choose_output_fps(info_a, info_b)
    if size[0] <= 0 or size[1] <= 0:
        raise ValueError("output_size must be positive")
    if fps <= 0:
        raise ValueError("output_fps must be positive")

    output = make_numbered_output_path(output)
    os.makedirs(os.path.dirname(os.path.abspath(output)) or ".", exist_ok=True)
    render_output = output
    if keep_audio:
        render_output = os.path.join(
            tempfile.gettempdir(),
            f"aitsugi_{uuid.uuid4().hex[:8]}.video_tmp.mp4",
        )

    if progress:
        progress(0, "Normalizing aspect ratio and frame rate...")
    if aspect == "auto":
        aspect_a, aspect_b = _resolve_auto_aspect(info_a, info_b, size)
    else:
        aspect_a = aspect
        aspect_b = aspect
    frames_a = _read_normalized_frames(video_a, size, fps, info_a.fps, aspect_a)
    frames_b = _read_normalized_frames(video_b, size, fps, info_b.fps, aspect_b)
    if not frames_a:
        raise RuntimeError("First video has no frames.")
    if not frames_b:
        raise RuntimeError("Second video has no frames.")

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(render_output, fourcc, fps, size)
    if not writer.isOpened():
        raise RuntimeError(f"Could not create output video: {render_output}")
    writer_released = False

    transition_frames = max(0, transition_frames)
    total_est = len(frames_a) + len(frames_b)
    written = 0

    def report(msg: str) -> None:
        nonlocal written
        if progress:
            pct = int(min(99, written / max(total_est, 1) * 100))
            progress(pct, msg)

    def finish(
        audio_segments: list[AudioSegment],
        cut_a_frames: int = 0,
        cut_b_frames: int = 0,
        seam_a_frame: Optional[int] = None,
        seam_b_frame: Optional[int] = None,
    ) -> str | JoinResult:
        nonlocal writer_released
        if not writer_released:
            writer.release()
            writer_released = True
        result = _mux_audio_segments(render_output, output, audio_segments, progress) if keep_audio else render_output
        if not keep_audio and result != output:
            os.replace(result, output)
            result = output
        if progress:
            progress(100, f"Done: {result}")
        if return_info:
            return JoinResult(result, cut_a_frames, cut_b_frames, seam_a_frame, seam_b_frame)
        return result

    try:
        seam_match = None
        if (auto_transition or transition_frames > 0) and mode in {"auto", "cut"}:
            report("Searching for matching visual seam...")
            seam_match = _find_matching_visual_seam_from_frames(frames_a, frames_b)
            if not seam_match:
                report("Searching for matching motion seam...")
                seam_match = _find_matching_motion_seam_from_frames(frames_a, frames_b)
            if mode == "cut" and not seam_match:
                raise RuntimeError("Could not find a close matching seam for cut mode.")

        if seam_match:
            report(
                f"Matched seam: A frame {seam_match.index_a}, "
                f"B frame {seam_match.index_b}, score {seam_match.distance:.3f}"
            )

            for frame in frames_a[: seam_match.index_a + 1]:
                writer.write(frame)
                written += 1
                if written % 20 == 0:
                    report("Writing first video to matched seam...")

            report("Writing second video after matched seam...")
            second_part = frames_b[seam_match.index_b + 1 :]
            if not second_part:
                raise RuntimeError("Matched seam is too close to the end of the second video.")
            for frame in second_part:
                writer.write(frame)
                written += 1
                if written % 20 == 0:
                    report("Writing second video after matched seam...")

            audio_segments = [
                AudioSegment(video_a, 0.0, (seam_match.index_a + 1) / fps),
                AudioSegment(video_b, (seam_match.index_b + 1) / fps, len(second_part) / fps),
            ]
            return finish(
                audio_segments,
                cut_a_frames=max(0, len(frames_a) - seam_match.index_a - 1),
                cut_b_frames=max(0, seam_match.index_b + 1),
                seam_a_frame=seam_match.index_a,
                seam_b_frame=seam_match.index_b,
            )

        if transition_frames == 0:
            report("Writing first video...")
            for frame in frames_a:
                writer.write(frame)
                written += 1
                if written % 20 == 0:
                    report("Writing first video...")

            report("Writing second video...")
            for frame in frames_b:
                writer.write(frame)
                written += 1
                if written % 20 == 0:
                    report("Writing second video...")

            audio_segments = [
                AudioSegment(video_a, 0.0, len(frames_a) / fps),
                AudioSegment(video_b, 0.0, len(frames_b) / fps),
            ]
            return finish(audio_segments)

        report("Writing first video and buffering overlap...")
        overlap_count = min(transition_frames, len(frames_a), len(frames_b))
        prefix_a = frames_a[: len(frames_a) - overlap_count]
        tail_a = frames_a[len(frames_a) - overlap_count :]
        head_b = frames_b[:overlap_count]
        suffix_b = frames_b[overlap_count:]

        for frame in prefix_a:
            writer.write(frame)
            written += 1
            if written % 20 == 0:
                report("Writing first video...")

        report("Reading second video overlap...")
        head_b, b_shift = _align_head_to_tail(tail_a, head_b)
        transition = make_overlap_transition(tail_a[-overlap_count:], head_b[:overlap_count])

        # If B was shorter than the requested overlap, keep A's extra buffered
        # frames before the blended section so temporal order is preserved.
        for f in tail_a[: max(0, len(tail_a) - overlap_count)]:
            writer.write(f)
            written += 1

        report("Writing blended transition...")
        for f in transition:
            writer.write(f)
            written += 1

        # If A was shorter than the requested overlap, keep B's extra buffered
        # frames after the blended section.
        for f in head_b[overlap_count:]:
            writer.write(f)
            written += 1

        report("Writing second video...")
        for frame in suffix_b:
            frame = _shift_frame(frame, b_shift[0], b_shift[1])
            writer.write(frame)
            written += 1
            if written % 20 == 0:
                report("Writing second video...")

        audio_segments = [
            AudioSegment(video_a, 0.0, len(prefix_a) / fps),
            AudioSegment(video_b, 0.0, (len(head_b[:overlap_count]) + len(suffix_b)) / fps),
        ]
        return finish(
            audio_segments,
            cut_a_frames=overlap_count,
            cut_b_frames=overlap_count,
        )
    finally:
        if not writer_released:
            writer.release()


def main() -> None:
    parser = argparse.ArgumentParser(description="Join two videos with optical-flow transition frames.")
    parser.add_argument("video_a")
    parser.add_argument("video_b")
    parser.add_argument("output")
    parser.add_argument("--transition", type=int, default=12, help="Number of generated transition frames")
    parser.add_argument(
        "--mode",
        choices=("auto", "cut", "add"),
        default="auto",
        help="auto: cut on a close match, otherwise add/overlap; cut: trim at matching motion; add: keep clips and blend overlap",
    )
    parser.add_argument(
        "--aspect",
        choices=("auto", "contain", "pad", "cover", "stretch"),
        default="auto",
        help=(
            "auto: choose a no-border fit based on the two clips; "
            "contain: preserve aspect ratio and fit with black bars; "
            "pad: do not upscale smaller videos, center on black; "
            "cover: preserve aspect ratio and center-crop; "
            "stretch: force resize and may distort"
        ),
    )
    parser.add_argument("--size", help="Output size as WIDTHxHEIGHT, for example 1280x720")
    parser.add_argument("--fps", type=float, help="Output frame rate, for example 24 or 30")
    parser.add_argument("--no-audio", action="store_true", help="Write video only without copying source audio")
    args = parser.parse_args()

    if args.transition < -1:
        parser.error("--transition must be -1 for auto or 0 or greater")
    output_size = None
    if args.size:
        parts = args.size.lower().split("x")
        if len(parts) != 2:
            parser.error("--size must use WIDTHxHEIGHT, for example 1280x720")
        try:
            output_size = (int(parts[0]), int(parts[1]))
        except ValueError:
            parser.error("--size must use numeric WIDTHxHEIGHT")
        if output_size[0] <= 0 or output_size[1] <= 0:
            parser.error("--size dimensions must be positive")
    if args.fps is not None and args.fps <= 0:
        parser.error("--fps must be positive")
    for label, path in (("video_a", args.video_a), ("video_b", args.video_b)):
        if not os.path.isfile(path):
            parser.error(f"{label} does not exist or is not a file: {path}")

    def print_progress(pct: int, msg: str) -> None:
        print(f"[{pct:3d}%] {msg}")

    join_videos(
        args.video_a,
        args.video_b,
        args.output,
        args.transition,
        print_progress,
        args.mode,
        args.aspect,
        keep_audio=not args.no_audio,
        output_size=output_size,
        output_fps=args.fps,
    )


if __name__ == "__main__":
    main()
