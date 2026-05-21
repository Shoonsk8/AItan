from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback
import uuid

try:
    from PyQt6.QtCore import QObject, QRunnable, Qt, QThreadPool, QUrl, pyqtSignal as Signal, pyqtSlot as Slot
    from PyQt6.QtGui import QDesktopServices, QImage, QPixmap
    from PyQt6.QtWidgets import (
        QApplication,
        QDialog,
        QFileDialog,
        QFrame,
        QGridLayout,
        QHBoxLayout,
        QAbstractItemView,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMessageBox,
        QComboBox,
        QSizePolicy,
        QPushButton,
        QSpinBox,
        QProgressBar,
        QVBoxLayout,
        QWidget,
    )
except ModuleNotFoundError as exc:
    if exc.name != "PyQt6":
        raise
    print(
        "PyQt6 is not installed in this Python environment.\n"
        "Run AItan's environment, then start the joiner from /mnt/1TBSSD/AItan:\n\n"
        "  ./.venv/bin/python AItsugi.py\n"
        "  python AItsugi.py",
        file=sys.stderr,
    )
    sys.exit(1)

import cv2
import numpy as np

from aitsugi import JoinResult, get_video_info, join_videos


VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | IMAGE_EXTENSIONS
AITAN_DATA_DIR = "/mnt/1TBSSD/AItan/data"
AITAN_LAUNCHER = "/mnt/1TBSSD/AItan/aisearch_launch.sh"

# Lazy import — aitsugi_search_panel pulls in frame_pair_index → AItan's CLIP
# model on first use, NOT on AItsugi startup. Keep this here so the search
# feature stays optional: the joiner works even if AItan isn't installed.
try:
    from aitsugi_search_panel import SearchPanel as _SearchPanel
except Exception as _e:
    _SearchPanel = None
    _search_panel_import_err = str(_e)


def is_video_path(path: str) -> bool:
    return os.path.isfile(path) and os.path.splitext(path)[1].lower() in VIDEO_EXTENSIONS


def is_image_path(path: str) -> bool:
    return os.path.isfile(path) and os.path.splitext(path)[1].lower() in IMAGE_EXTENSIONS


def is_media_path(path: str) -> bool:
    return os.path.isfile(path) and os.path.splitext(path)[1].lower() in MEDIA_EXTENSIONS


def compact_path(path: str, max_len: int = 64) -> str:
    if len(path) <= max_len:
        return path
    head = path[:24]
    tail = path[-(max_len - 27) :]
    return f"{head}...{tail}"


def frame_to_pixmap(frame) -> QPixmap:
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    height, width = rgb.shape[:2]
    bytes_per_line = width * 3
    image = QImage(rgb.data, width, height, bytes_per_line, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(image.copy())


def read_video_edge_pixmaps(path: str) -> tuple[QPixmap | None, QPixmap | None]:
    # Images: same frame for both START and END.
    if is_image_path(path):
        frame = cv2.imread(path, cv2.IMREAD_COLOR)
        if frame is None:
            return None, None
        pm = frame_to_pixmap(frame)
        return pm, pm
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return None, None
    try:
        ok, first = cap.read()
        first_pixmap = frame_to_pixmap(first) if ok else None

        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        last_pixmap = None
        if frame_count > 1:
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame_count - 1))
            ok, last = cap.read()
            if ok:
                last_pixmap = frame_to_pixmap(last)
        if last_pixmap is None:
            last_pixmap = first_pixmap
        return first_pixmap, last_pixmap
    finally:
        cap.release()


def read_video_edge_frame(path: str, edge: str):
    # Images: same frame regardless of edge requested.
    if is_image_path(path):
        return cv2.imread(path, cv2.IMREAD_COLOR)
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


def frame_visual_distance(a, b) -> tuple[float, float]:
    if a is None or b is None or getattr(a, "size", 0) == 0 or getattr(b, "size", 0) == 0:
        return float("inf"), float("inf")
    gray_a = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
    gray_b = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
    small_a = cv2.resize(gray_a, (96, 160), interpolation=cv2.INTER_AREA).astype(np.float32)
    small_b = cv2.resize(gray_b, (96, 160), interpolation=cv2.INTER_AREA).astype(np.float32)
    raw_distance = float(np.mean(np.abs(small_a - small_b)))
    norm_a = (small_a - small_a.mean()) / (small_a.std() + 1e-6)
    norm_b = (small_b - small_b.mean()) / (small_b.std() + 1e-6)
    normalized_distance = float(np.mean(np.abs(norm_a - norm_b)))
    return normalized_distance, raw_distance


def frames_match_exact(score: float, raw_score: float) -> bool:
    return score <= 0.18 or raw_score <= 10.0


def frames_match_similar(score: float, raw_score: float) -> bool:
    return score <= 0.36 and raw_score <= 32.0


def frames_match(score: float, raw_score: float) -> bool:
    return frames_match_exact(score, raw_score) or frames_match_similar(score, raw_score)


def safe_output_stem(path: str) -> str:
    stem = os.path.splitext(os.path.basename(path))[0]
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in stem)
    return safe[:80] or "video"


def batch_output_path(folder: str, video_a: str, video_b: str) -> str:
    return os.path.join(folder, f"joined_{safe_output_stem(video_a)}__{safe_output_stem(video_b)}.mp4")


def numbered_output_path(path: str) -> str:
    folder = os.path.dirname(os.path.abspath(path)) or "."
    stem, ext = os.path.splitext(os.path.basename(path))
    candidate = os.path.join(folder, f"{stem}{ext}")
    if not os.path.exists(candidate):
        return candidate
    index = 1
    while True:
        candidate = os.path.join(folder, f"{stem}_{index}{ext}")
        if not os.path.exists(candidate):
            return candidate
        index += 1


def last_scene_jpg_path(video_path: str) -> str:
    folder = os.path.dirname(os.path.abspath(video_path)) or "."
    return numbered_output_path(os.path.join(folder, f"{safe_output_stem(video_path)}_last_scene.jpg"))


def extract_last_scene_jpg(video_path: str) -> str:
    frame = read_video_edge_frame(video_path, "end")
    if frame is None:
        raise RuntimeError(f"Could not read the last frame: {video_path}")
    output = last_scene_jpg_path(video_path)
    ok = cv2.imwrite(output, frame, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
    if not ok:
        raise RuntimeError(f"Could not write JPG: {output}")
    return output


def first_scene_jpg_path(video_path: str) -> str:
    folder = os.path.dirname(os.path.abspath(video_path)) or "."
    return numbered_output_path(os.path.join(folder, f"{safe_output_stem(video_path)}_first_scene.jpg"))


def extract_first_scene_jpg(video_path: str) -> str:
    frame = read_video_edge_frame(video_path, "start")
    if frame is None:
        raise RuntimeError(f"Could not read the first frame: {video_path}")
    output = first_scene_jpg_path(video_path)
    ok = cv2.imwrite(output, frame, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
    if not ok:
        raise RuntimeError(f"Could not write JPG: {output}")
    return output


def show_in_aitan_preview(path: str) -> bool:
    if not path or not os.path.exists(path) or not os.path.exists(AITAN_LAUNCHER):
        return False
    try:
        subprocess.Popen(
            [AITAN_LAUNCHER, path],
            cwd=os.path.dirname(AITAN_LAUNCHER),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except Exception:
        return False


def resolve_output_size_choice(choice: str, video_a: str, video_b: str) -> tuple[int, int] | None:
    choice = (choice or "auto").strip().lower().replace(" ", "")
    if choice == "auto":
        return None
    if choice == "a":
        info = get_video_info(video_a)
        return info.width, info.height
    if choice == "b":
        info = get_video_info(video_b)
        return info.width, info.height
    parts = choice.split("x")
    if len(parts) != 2:
        raise ValueError("Size must be Auto, A, B, or WIDTHxHEIGHT")
    width, height = int(parts[0]), int(parts[1])
    if width <= 0 or height <= 0:
        raise ValueError("Size dimensions must be positive")
    return width, height


def resolve_output_fps_choice(choice: str, video_a: str, video_b: str) -> float | None:
    choice = (choice or "auto").strip().lower().replace("fps", "").strip()
    if choice == "auto":
        return None
    if choice == "a":
        return get_video_info(video_a).fps
    if choice == "b":
        return get_video_info(video_b).fps
    fps = float(choice)
    if fps <= 0:
        raise ValueError("FPS must be positive")
    return fps


AITAN_PREFIX = "AItan"
AITAN_VERSION = "2.5.4"


def strip_aitan_block(text: str) -> str:
    if not text or f"{AITAN_PREFIX}{{" not in text:
        return text
    idx = text.find(f"{AITAN_PREFIX}{{")
    start = idx + len(AITAN_PREFIX)
    depth = 0
    for pos in range(start, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                return (text[:idx] + text[pos + 1 :]).strip()
    return text


def build_aitan_block(entry: dict) -> str:
    skip = {"meta", "ver", "confirmed", "audio_probed"}
    slim = {"ver": AITAN_VERSION}
    for key, value in entry.items():
        if key in skip or key.startswith("CLIP_") or key in {"CLIP", "FACE", "FACE_PW"}:
            continue
        if value in (None, "", [], {}):
            continue
        slim[key] = value
    return AITAN_PREFIX + json.dumps(slim, ensure_ascii=False, separators=(",", ":"))


def embed_aitan_video_metadata(path: str, entry: dict) -> bool:
    if not os.path.exists(path):
        return False

    existing_desc = ""
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
            capture_output=True,
            text=True,
            timeout=10,
        )
        existing_desc = (
            json.loads(probe.stdout).get("format", {}).get("tags", {}).get("description", "") or ""
        )
    except Exception:
        existing_desc = ""

    block = build_aitan_block(entry)
    stripped = strip_aitan_block(existing_desc)
    new_desc = f"{stripped.rstrip()}\n{block}" if stripped.strip() else block
    tmp = os.path.join(
        os.path.dirname(path),
        f".{os.path.basename(path)}.{uuid.uuid4().hex[:8]}.aitan_tmp{os.path.splitext(path)[1] or '.mp4'}",
    )
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", path, "-metadata", f"description={new_desc}", "-codec", "copy", tmp],
            capture_output=True,
            timeout=30,
        )
        if os.path.exists(tmp) and os.path.getsize(tmp) > 0 and os.path.exists(path):
            orig_size = os.path.getsize(path)
            tmp_size = os.path.getsize(tmp)
            if tmp_size >= orig_size * 0.5:
                os.replace(tmp, path)
                return True
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return False


def aitan_project_for_path(path: str) -> str | None:
    abs_path = os.path.abspath(path)
    parts = abs_path.split(os.sep)
    for project in ("AIX", "AI", "AIALL", "AI2", "TEST", "PIC"):
        if project in parts:
            return project
    return None


def update_aitan_join_metadata(output: str, video_a: str, video_b: str, join_result: JoinResult | None = None) -> None:
    project = aitan_project_for_path(output) or aitan_project_for_path(video_a) or aitan_project_for_path(video_b)
    if not project:
        return

    attrs_path = os.path.join(AITAN_DATA_DIR, f"attrs_{project}.json")
    if not os.path.exists(attrs_path):
        return

    output = os.path.abspath(output)
    video_a = os.path.abspath(video_a)
    video_b = os.path.abspath(video_b)
    with open(attrs_path, encoding="utf-8") as f:
        data = json.load(f)

    entry = dict(data.get(output, {}))
    entry.setdefault("tags", [])
    entry.setdefault("confirmed", False)
    entry.setdefault("project", "")
    entry.setdefault("scene", "")
    entry.setdefault("custom", "")
    entry.setdefault("person_id", "")
    entry.setdefault("audio", "")
    entry["editable"] = False
    entry.setdefault("prompt", "")
    entry.setdefault("neg_prompt", "")
    entry.setdefault("seed", "")
    entry.setdefault("speech", "")

    note_lines = [
        "AItsugi",
        f"START: {video_a}",
        f"END: {video_b}",
    ]
    if join_result is not None:
        note_lines.append(f"CUT A FRAMES: {join_result.cut_a_frames}")
        note_lines.append(f"CUT B FRAMES: {join_result.cut_b_frames}")
        note_lines.append(f"OUTPUT B START FRAME: {join_result.cut_b_frames}")
        if join_result.seam_a_frame is not None and join_result.seam_b_frame is not None:
            note_lines.append(f"SEAM: A frame {join_result.seam_a_frame} / B frame {join_result.seam_b_frame}")
    note_block = "\n".join(note_lines)
    current_note = (entry.get("note") or "").strip()
    if note_block not in current_note:
        entry["note"] = f"{current_note}\n{note_block}".strip()
    else:
        entry["note"] = current_note

    related = list(entry.get("related") or [])
    for path in (video_a, video_b):
        if path not in related:
            related.append(path)
    entry["related"] = related

    embed_aitan_video_metadata(output, entry)

    data[output] = entry
    tmp_path = f"{attrs_path}.aitsugi.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, attrs_path)


class VideoPreviewLabel(QLabel):
    double_clicked = Signal()

    def __init__(self, caption: str) -> None:
        super().__init__(caption)
        self._source_pixmap: QPixmap | None = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(64, 80)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            "background-color: black; color: #b8c0cc; border: 4px solid #00ff00;"
        )

    def set_source_pixmap(self, pixmap: QPixmap | None) -> None:
        self._source_pixmap = pixmap
        if pixmap is None:
            self.clear()
            return
        self._update_scaled_pixmap()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._update_scaled_pixmap()

    def _update_scaled_pixmap(self) -> None:
        if not self._source_pixmap or self.width() <= 8 or self.height() <= 8:
            return
        target = self.contentsRect().size()
        target.setWidth(max(1, target.width() - 8))
        target.setHeight(max(1, target.height() - 8))
        scaled = self._source_pixmap.scaled(
            target,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setPixmap(scaled)

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class DropBox(QFrame):
    file_changed = Signal(str)
    edge_clicked = Signal(object, str)

    def __init__(self, title: str) -> None:
        super().__init__()
        self.path = ""
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName("dropBox")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(220)

        self.title_label = QLabel(title)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setObjectName("dropTitle")
        self.path_label = QLabel("動画ファイルをここにドロップ\nクリックして選択もできます")
        self.path_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.path_label.setWordWrap(True)
        self.path_label.setObjectName("dropPath")
        self.info_label = QLabel("")
        self.info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.info_label.setObjectName("dropInfo")

        self.start_preview = VideoPreviewLabel("START")
        self.end_preview = VideoPreviewLabel("END")
        self.start_preview.double_clicked.connect(lambda: self.edge_clicked.emit(self, "start"))
        self.end_preview.double_clicked.connect(lambda: self.edge_clicked.emit(self, "end"))
        self.start_preview.setToolTip("Double-click to find videos whose END matches this START")
        self.end_preview.setToolTip("Double-click to find videos whose START matches this END")
        start_label = QLabel("START")
        start_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        start_label.setObjectName("edgeLabel")
        end_label = QLabel("END")
        end_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        end_label.setObjectName("edgeLabel")
        start_column = QVBoxLayout()
        start_column.setSpacing(4)
        start_column.addWidget(start_label)
        start_column.addWidget(self.start_preview, 1)
        end_column = QVBoxLayout()
        end_column.setSpacing(4)
        end_column.addWidget(end_label)
        end_column.addWidget(self.end_preview, 1)
        self.preview_row = QHBoxLayout()
        self.preview_row.setSpacing(10)
        self.preview_row.addLayout(start_column)
        self.preview_row.addLayout(end_column)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(self.title_label)
        layout.addLayout(self.preview_row, 1)
        layout.addWidget(self.path_label)
        layout.addWidget(self.info_label)

    def mousePressEvent(self, event):  # noqa: N802
        path, _ = QFileDialog.getOpenFileName(
            self,
            "動画を選択",
            "",
            "Video files (*.mp4 *.mov *.mkv *.avi *.webm);;All files (*.*)",
        )
        if path:
            self.set_file(path)
        super().mousePressEvent(event)

    def dragEnterEvent(self, event):  # noqa: N802
        if self._event_has_video(event):
            self.setProperty("dragging", True)
            self.style().unpolish(self)
            self.style().polish(self)
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):  # noqa: N802
        self.setProperty("dragging", False)
        self.style().unpolish(self)
        self.style().polish(self)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):  # noqa: N802
        self.setProperty("dragging", False)
        self.style().unpolish(self)
        self.style().polish(self)
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path:
                self.set_file(path)
                event.acceptProposedAction()

    def _event_has_video(self, event) -> bool:
        # Accepts both videos and images now — the joiner is still video-only,
        # but the edge-search panel uses A/B slots for query input on either.
        return any(is_media_path(url.toLocalFile()) for url in event.mimeData().urls())

    def set_file(self, path: str) -> None:
        if not is_media_path(path):
            QMessageBox.warning(self, "対応していないファイルです", f"動画または画像を選択してください:\n{path}")
            return
        self.path = path
        self.path_label.setText(os.path.basename(path))
        self.path_label.setToolTip(path)
        try:
            info = get_video_info(path)
            self.info_label.setText(
                f"{info.width}x{info.height} / {info.fps:.2f} fps / {info.frame_count} frames"
            )
        except Exception:
            self.info_label.setText(compact_path(path))
        first_pixmap, last_pixmap = read_video_edge_pixmaps(path)
        self.start_preview.set_source_pixmap(first_pixmap)
        self.end_preview.set_source_pixmap(last_pixmap)
        self.file_changed.emit(path)


class WorkerSignals(QObject):
    progress = Signal(int, str)
    finished = Signal(str)
    error = Signal(str)


class JoinWorker(QRunnable):
    def __init__(
        self,
        a: str,
        b: str,
        output: str,
        transition: int,
        mode: str,
        aspect: str,
        size_choice: str,
        fps_choice: str,
    ) -> None:
        super().__init__()
        self.a = a
        self.b = b
        self.output = output
        self.transition = transition
        self.mode = mode
        self.aspect = aspect
        self.size_choice = size_choice
        self.fps_choice = fps_choice
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            output = join_videos(
                self.a,
                self.b,
                self.output,
                self.transition,
                progress=lambda pct, msg: self.signals.progress.emit(pct, msg),
                mode=self.mode,
                aspect=self.aspect,
                return_info=True,
                output_size=resolve_output_size_choice(self.size_choice, self.a, self.b),
                output_fps=resolve_output_fps_choice(self.fps_choice, self.a, self.b),
            )
            update_aitan_join_metadata(output.output, self.a, self.b, output)
            self.signals.finished.emit(output.output)
        except Exception:
            self.signals.error.emit(traceback.format_exc())


class BatchJoinWorker(QRunnable):
    def __init__(
        self,
        jobs: list[tuple[str, str, str]],
        transition: int,
        mode: str,
        aspect: str,
        size_choice: str,
        fps_choice: str,
    ) -> None:
        super().__init__()
        self.jobs = jobs
        self.transition = transition
        self.mode = mode
        self.aspect = aspect
        self.size_choice = size_choice
        self.fps_choice = fps_choice
        self.signals = WorkerSignals()
        self.cancel_requested = False

    def cancel(self) -> None:
        self.cancel_requested = True

    @Slot()
    def run(self) -> None:
        outputs: list[str] = []
        try:
            total = max(1, len(self.jobs))
            for index, (video_a, video_b, output) in enumerate(self.jobs, start=1):
                if self.cancel_requested:
                    break
                base_pct = int((index - 1) / total * 100)
                span = max(1, int(100 / total))

                def report(pct: int, msg: str, index=index) -> None:
                    overall = min(99, base_pct + int(pct * span / 100))
                    self.signals.progress.emit(overall, f"[{index}/{total}] {msg}")

                result = join_videos(
                    video_a,
                    video_b,
                    output,
                    self.transition,
                    progress=report,
                    mode=self.mode,
                    aspect=self.aspect,
                    return_info=True,
                    output_size=resolve_output_size_choice(self.size_choice, video_a, video_b),
                    output_fps=resolve_output_fps_choice(self.fps_choice, video_a, video_b),
                )
                update_aitan_join_metadata(result.output, video_a, video_b, result)
                outputs.append(result.output)
                if self.cancel_requested:
                    break
            self.signals.finished.emit("\n".join(outputs))
        except Exception:
            self.signals.error.emit(traceback.format_exc())


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("AItsugi")
        self.resize(420, 900)
        self.setAcceptDrops(True)
        self.pool = QThreadPool.globalInstance()
        self.last_output = ""
        self.batch_outputs: list[str] = []
        self.batch_worker: BatchJoinWorker | None = None
        self.search_root_manual = False
        self.updating_search_root = False

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        title = QLabel("AItsugi")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        video_layout = QVBoxLayout()
        video_layout.setSpacing(10)
        self.drop_a = DropBox("動画 A / 前半")
        self.drop_b = DropBox("動画 B / 後半")
        self.drop_a.file_changed.connect(self.on_input_changed)
        self.drop_b.file_changed.connect(self.on_input_changed)
        self.drop_a.edge_clicked.connect(self.on_edge_clicked)
        self.drop_b.edge_clicked.connect(self.on_edge_clicked)
        video_layout.addWidget(self.drop_a)
        video_layout.addWidget(self.drop_b)
        layout.addLayout(video_layout)

        output_row = QHBoxLayout()
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("出力先 output.mp4")
        browse_output = QPushButton("保存先")
        browse_output.clicked.connect(self.choose_output)
        output_row.addWidget(QLabel("出力:"))
        output_row.addWidget(self.output_edit)
        output_row.addWidget(browse_output)
        layout.addLayout(output_row)

        search_row = QHBoxLayout()
        self.search_root_edit = QLineEdit()
        self.search_root_edit.setPlaceholderText("未指定ならクリックした動画のフォルダ以下を検索")
        self.search_root_edit.textEdited.connect(self.on_search_root_edited)
        browse_search = QPushButton("検索範囲")
        browse_search.clicked.connect(self.choose_search_root)
        clear_search = QPushButton("自動")
        clear_search.clicked.connect(self.clear_search_root)
        search_row.addWidget(QLabel("検索:"))
        search_row.addWidget(self.search_root_edit, 1)
        search_row.addWidget(browse_search)
        search_row.addWidget(clear_search)
        layout.addLayout(search_row)

        settings_row = QHBoxLayout()
        self.transition_spin = QSpinBox()
        self.transition_spin.setRange(-1, 120)
        self.transition_spin.setValue(-1)
        self.transition_spin.setSpecialValueText("Auto")
        self.transition_spin.setSuffix(" frames")
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Auto", "auto")
        self.mode_combo.addItem("Cut & Connect", "cut")
        self.mode_combo.addItem("Add & Connect", "add")
        self.aspect_combo = QComboBox()
        self.aspect_combo.addItem("Scale Crop No Distort", "cover")
        self.aspect_combo.addItem("Scale Fit Bars No Distort", "contain")
        self.aspect_combo.addItem("Pad No Upscale No Distort", "pad")
        self.aspect_combo.addItem("Auto", "auto")
        self.aspect_combo.addItem("Force Stretch Distorts", "stretch")
        self.size_combo = QComboBox()
        self.size_combo.setEditable(True)
        self.size_combo.addItem("1280x720", "1280x720")
        self.size_combo.addItem("1920x1080", "1920x1080")
        self.size_combo.addItem("1080x1920", "1080x1920")
        self.size_combo.addItem("720x1280", "720x1280")
        self.size_combo.addItem("Video A", "a")
        self.size_combo.addItem("Video B", "b")
        self.size_combo.addItem("Auto", "auto")
        self.fps_combo = QComboBox()
        self.fps_combo.setEditable(True)
        self.fps_combo.addItem("Auto", "auto")
        self.fps_combo.addItem("24", "24")
        self.fps_combo.addItem("30", "30")
        self.fps_combo.addItem("60", "60")
        self.fps_combo.addItem("Video A", "a")
        self.fps_combo.addItem("Video B", "b")
        settings_row.addWidget(QLabel("接ぎ目:"))
        settings_row.addWidget(self.transition_spin)
        settings_row.addWidget(QLabel("モード:"))
        settings_row.addWidget(self.mode_combo, 1)
        settings_row.addWidget(QLabel("FPS:"))
        settings_row.addWidget(self.fps_combo)
        layout.addLayout(settings_row)

        output_settings_row = QHBoxLayout()
        output_settings_row.addWidget(QLabel("サイズ:"))
        output_settings_row.addWidget(self.size_combo)
        output_settings_row.addWidget(QLabel("合わせ:"))
        output_settings_row.addWidget(self.aspect_combo, 1)
        layout.addLayout(output_settings_row)

        self.progress = QProgressBar()
        self.status = QLabel("準備OK")
        self.status.setWordWrap(True)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)

        button_row = QHBoxLayout()
        self.run_button = QPushButton("結合")
        self.run_button.setObjectName("primaryButton")
        self.run_button.clicked.connect(self.run_join)
        self.run_button.setEnabled(False)
        self.open_button = QPushButton("出力を開く")
        self.open_button.clicked.connect(self.open_output)
        self.open_button.setEnabled(False)
        self.extract_jpg_button = QPushButton("最後をJPG保存")
        self.extract_jpg_button.clicked.connect(self.extract_current_last_scene_jpg)
        self.extract_jpg_button.setEnabled(False)
        self.stop_batch_button = QPushButton("停止")
        self.stop_batch_button.clicked.connect(self.stop_batch_join)
        self.stop_batch_button.setEnabled(False)
        button_row.addStretch(1)
        button_row.addWidget(self.extract_jpg_button)
        button_row.addWidget(self.stop_batch_button)
        button_row.addWidget(self.run_button)
        button_row.addWidget(self.open_button)
        layout.addLayout(button_row)

        # CLIP-based start/end-frame search panel. Wired only when the
        # backend module imported cleanly — otherwise the joiner UI works
        # standalone.
        if _SearchPanel is not None:
            self.search_panel = _SearchPanel()
            self.search_panel.load_to_a.connect(self.drop_a.set_file)
            self.search_panel.load_to_b.connect(self.drop_b.set_file)
            self.search_panel.status.connect(self.status.setText)
            layout.addWidget(self.search_panel)
        else:
            note = QLabel(f"CLIP検索パネル: {_search_panel_import_err}")
            note.setStyleSheet("color:#a55; font-size:11px;")
            note.setWordWrap(True)
            layout.addWidget(note)

        self.setStyleSheet(
            """
            QWidget { font-size: 14px; }
            QLabel#title { font-size: 26px; font-weight: 700; margin: 6px; }
            QLabel#dropTitle { font-size: 18px; font-weight: 700; }
            QLabel#dropPath { color: #202124; }
            QLabel#dropInfo { color: #5f6368; font-size: 12px; }
            QLabel#edgeLabel { color: #2f7d32; font-size: 11px; font-weight: 700; }
            QFrame#dropBox {
                border: 2px dashed #7c8794;
                border-radius: 8px;
                background: #f7f8fa;
            }
            QFrame#dropBox:hover, QFrame#dropBox[dragging="true"] {
                border-color: #1664d9;
                background: #eef5ff;
            }
            QPushButton { padding: 10px 18px; border-radius: 6px; }
            QPushButton#primaryButton {
                background: #1664d9;
                color: white;
                font-weight: 700;
                min-width: 88px;
            }
            QPushButton#primaryButton:disabled { background: #9aa7b8; }
            QLineEdit { padding: 8px; }
            """
        )

    def combo_choice(self, combo: QComboBox) -> str:
        index = combo.currentIndex()
        text = combo.currentText().strip()
        if index >= 0 and text == combo.itemText(index):
            data = combo.currentData()
            return str(data) if data is not None else text
        return text

    def choose_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "出力MP4を保存",
            "joined_output.mp4",
            "MP4 video (*.mp4);;All files (*.*)",
        )
        if path:
            if not path.lower().endswith(".mp4"):
                path += ".mp4"
            self.output_edit.setText(path)

    def set_search_root_text(self, path: str) -> None:
        self.updating_search_root = True
        try:
            self.search_root_edit.setText(path)
        finally:
            self.updating_search_root = False

    def on_search_root_edited(self, text: str) -> None:
        if self.updating_search_root:
            return
        self.search_root_manual = bool(text.strip())

    def choose_search_root(self) -> None:
        start_dir = self.search_root_edit.text().strip()
        if not start_dir or not os.path.isdir(start_dir):
            start_dir = os.path.dirname(self.drop_a.path or self.drop_b.path) or os.getcwd()
        path = QFileDialog.getExistingDirectory(self, "検索範囲を選択", start_dir)
        if path:
            self.search_root_manual = True
            self.set_search_root_text(path)

    def clear_search_root(self) -> None:
        self.search_root_manual = False
        self.set_search_root_text("")
        self.status.setText("検索範囲を自動に戻しました。")

    def run_join(self) -> None:
        a = self.drop_a.path
        b = self.drop_b.path
        output = self.output_edit.text().strip()
        if not a or not b:
            QMessageBox.warning(self, "動画が足りません", "動画Aと動画Bを両方入れてください。")
            return
        if not output:
            output = os.path.abspath("joined_output.mp4")
            self.output_edit.setText(output)
        elif not os.path.splitext(output)[1]:
            output += ".mp4"
            self.output_edit.setText(output)

        self.run_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self.progress.setValue(0)
        self.status.setText("処理開始...")

        worker = JoinWorker(
            a,
            b,
            output,
            self.transition_spin.value(),
            self.mode_combo.currentData(),
            self.aspect_combo.currentData(),
            self.combo_choice(self.size_combo),
            self.combo_choice(self.fps_combo),
        )
        worker.signals.progress.connect(self.on_progress)
        worker.signals.finished.connect(self.on_finished)
        worker.signals.error.connect(self.on_error)
        self.pool.start(worker)

    def on_progress(self, pct: int, msg: str) -> None:
        self.progress.setValue(pct)
        self.status.setText(msg)

    def on_finished(self, output: str) -> None:
        self.last_output = output
        self.output_edit.setText(output)
        self.run_button.setEnabled(True)
        self.open_button.setEnabled(True)
        self.progress.setValue(100)
        opened_aitan = show_in_aitan_preview(output)
        if opened_aitan:
            self.status.setText(f"完了してAItanプレビューに送りました: {output}")
            QMessageBox.information(self, "完了", f"書き出してAItanプレビューに送りました:\n{output}")
        else:
            self.status.setText(f"完了: {output}")
            QMessageBox.information(self, "完了", f"書き出しました:\n{output}")

    def on_error(self, err: str) -> None:
        self.batch_worker = None
        self.run_button.setEnabled(True)
        self.stop_batch_button.setEnabled(False)
        self.status.setText("エラー")
        QMessageBox.critical(self, "エラー", err)

    def extract_current_last_scene_jpg(self, path: str | None = None) -> None:
        source = path or self.drop_a.path or self.drop_b.path
        if not source:
            QMessageBox.warning(self, "動画がありません", "JPGを書き出す動画を読み込んでください。")
            return
        try:
            output = extract_last_scene_jpg(source)
        except Exception as exc:
            self.status.setText("JPG書き出しエラー")
            QMessageBox.critical(self, "JPG書き出しエラー", str(exc))
            return
        self.last_output = output
        self.open_button.setEnabled(True)
        opened_aitan = show_in_aitan_preview(output)
        if opened_aitan:
            self.status.setText(f"JPGを書き出してAItanプレビューに送りました: {output}")
            QMessageBox.information(self, "JPG書き出し完了", f"書き出してAItanプレビューに送りました:\n{output}")
        else:
            self.status.setText(f"JPGを書き出しました: {output}")
            QMessageBox.information(self, "JPG書き出し完了", f"書き出しました:\n{output}")

    def offer_extract_last_scene_jpg(self, source_path: str, message: str) -> None:
        reply = QMessageBox.question(
            self,
            "一致なし",
            f"{message}\n\n最後のフレームをJPGで保存しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.extract_current_last_scene_jpg(source_path)

    def on_input_changed(self, _path: str = "") -> None:
        self.run_button.setEnabled(bool(self.drop_a.path and self.drop_b.path))
        self.extract_jpg_button.setEnabled(bool(self.drop_a.path or self.drop_b.path))
        if _path and not self.search_root_manual:
            self.set_search_root_text(os.path.dirname(_path) or os.getcwd())
        if self.drop_a.path and self.drop_b.path and not self.output_edit.text().strip():
            folder = os.path.dirname(self.drop_a.path) or os.getcwd()
            self.output_edit.setText(os.path.join(folder, "joined_output.mp4"))
        if self.drop_a.path and self.drop_b.path:
            self.status.setText("2本の動画を読み込みました。結合できます。")
        else:
            self.status.setText("動画Aと動画Bをドロップしてください。")

    def on_edge_clicked(self, source_box: DropBox, edge: str) -> None:
        if not source_box.path:
            return

        if source_box is self.drop_a and edge == "start":
            self.drop_b.set_file(source_box.path)
            source_box = self.drop_b
        elif source_box is self.drop_b and edge == "end":
            self.drop_a.set_file(source_box.path)
            source_box = self.drop_a

        search_edge = "start" if edge == "end" else "end"
        source_frame = read_video_edge_frame(source_box.path, edge)
        if source_frame is None:
            QMessageBox.warning(self, "読み込み失敗", "クリックした動画フレームを読めませんでした。")
            return

        if not self.search_root_manual:
            self.set_search_root_text(os.path.dirname(source_box.path) or os.getcwd())
        folder = self.search_root_edit.text().strip()
        if folder and not os.path.isdir(folder):
            QMessageBox.warning(self, "検索範囲エラー", f"フォルダが見つかりません:\n{folder}")
            return
        if not folder:
            folder = os.path.dirname(source_box.path) or os.getcwd()
        candidates: list[str] = []
        for root, _, filenames in os.walk(folder):
            for name in filenames:
                path = os.path.join(root, name)
                if is_media_path(path) and path != source_box.path:
                    candidates.append(path)
        candidates.sort()
        if not candidates:
            self.offer_extract_last_scene_jpg(source_box.path, "同じフォルダに比較できるファイルがありません。")
            return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self.status.setText(
            f"{os.path.basename(source_box.path)} の {edge.upper()} と候補の {search_edge.upper()} を比較中..."
        )
        QApplication.processEvents()
        exact_matches: list[tuple[float, float, str]] = []
        similar_matches: list[tuple[float, float, str]] = []
        try:
            for path in candidates:
                frame = read_video_edge_frame(path, search_edge)
                if frame is None:
                    continue
                score, raw_score = frame_visual_distance(source_frame, frame)
                if frames_match_exact(score, raw_score):
                    exact_matches.append((score, raw_score, path))
                elif frames_match_similar(score, raw_score):
                    similar_matches.append((score, raw_score, path))
        finally:
            QApplication.restoreOverrideCursor()

        exact_matches.sort(key=lambda item: (item[0], item[1]))
        similar_matches.sort(key=lambda item: (item[0], item[1]))
        if not exact_matches and not similar_matches:
            self.status.setText("一致する開始/終了フレームは見つかりませんでした。")
            self.offer_extract_last_scene_jpg(
                source_box.path,
                f"{edge.upper()} に合う {search_edge.upper()} の動画は見つかりませんでした。\n検索範囲:\n{folder}",
            )
            return

        include_similar = True
        while True:
            visible_matches = list(exact_matches)
            if include_similar:
                visible_matches.extend(similar_matches)
                visible_matches.sort(key=lambda item: (item[0], item[1]))

            labels: list[str] = []
            label_to_path: dict[str, str] = {}
            for score, raw_score, path in visible_matches[:30]:
                rel_path = os.path.relpath(path, folder)
                rel_dir = os.path.dirname(rel_path) or "."
                filename = os.path.basename(path)
                kind = "EXACT" if frames_match_exact(score, raw_score) else "SIMILAR"
                label = f"{kind}  {score:.3f} / {raw_score:.1f}  [{rel_dir}]  {filename}"
                labels.append(label)
                label_to_path[label] = path

            dialog = QDialog(self)
            dialog.setWindowTitle("一致する動画")
            dialog.resize(520, 640)
            dialog.setSizeGripEnabled(True)
            dialog_layout = QVBoxLayout(dialog)
            title_label = QLabel(
                f"{edge.upper()} に合う {search_edge.upper()} の動画: "
                f"完全一致 {len(exact_matches)} 件 / 類似 {len(similar_matches)} 件"
            )
            title_label.setWordWrap(True)
            if include_similar:
                info_label = QLabel("完全一致と類似候補を表示中です。複数選択して結合できます。")
            else:
                info_label = QLabel("完全一致だけを表示しています。必要なら類似候補を追加表示できます。")
            info_label.setWordWrap(True)
            dialog_layout.addWidget(title_label)
            dialog_layout.addWidget(info_label)

            list_widget = QListWidget(dialog)
            list_widget.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
            list_widget.setMinimumSize(360, 260)
            for label in labels:
                item = QListWidgetItem(label)
                item.setToolTip(label_to_path[label])
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
                list_widget.addItem(item)
            list_widget.itemDoubleClicked.connect(
                lambda item, paths=label_to_path: QDesktopServices.openUrl(QUrl.fromLocalFile(paths[item.text()]))
            )
            if list_widget.count() > 0:
                list_widget.item(0).setCheckState(Qt.CheckState.Checked)
            dialog_layout.addWidget(list_widget, 1)

            button_row = QHBoxLayout()
            join_selected_button = QPushButton("選択を結合")
            join_all_button = QPushButton("表示中を全件結合")
            load_button = QPushButton("最初を読み込み")
            view_button = QPushButton("選択を表示")
            clicked_button = {"button": None}

            def choose_button(button):
                clicked_button["button"] = button
                dialog.accept()

            join_selected_button.clicked.connect(lambda: choose_button(join_selected_button))
            join_all_button.clicked.connect(lambda: choose_button(join_all_button))
            load_button.clicked.connect(lambda: choose_button(load_button))
            view_button.clicked.connect(lambda: choose_button(view_button))
            button_row.addWidget(join_selected_button)
            button_row.addWidget(join_all_button)
            button_row.addWidget(load_button)
            button_row.addWidget(view_button)
            show_similar_button = None
            if similar_matches and not include_similar:
                show_similar_button = QPushButton("類似も表示")
                show_similar_button.clicked.connect(lambda: choose_button(show_similar_button))
                button_row.addWidget(show_similar_button)
            cancel_button = QPushButton("Cancel")
            cancel_button.clicked.connect(dialog.reject)
            button_row.addStretch(1)
            button_row.addWidget(cancel_button)
            dialog_layout.addLayout(button_row)

            accepted = dialog.exec() == QDialog.DialogCode.Accepted
            button = clicked_button["button"]
            if accepted and button is show_similar_button:
                include_similar = True
                continue
            if accepted and button is view_button:
                checked_items = [
                    list_widget.item(i)
                    for i in range(list_widget.count())
                    if list_widget.item(i).checkState() == Qt.CheckState.Checked
                ]
                current_item = checked_items[0] if checked_items else list_widget.currentItem()
                if current_item:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(label_to_path[current_item.text()]))
                continue
            if not accepted or button is None:
                self.status.setText(
                    f"完全一致 {len(exact_matches)} 件 / 類似 {len(similar_matches)} 件見つかりました。"
                )
                return

            selected_labels = [
                list_widget.item(i).text()
                for i in range(list_widget.count())
                if list_widget.item(i).checkState() == Qt.CheckState.Checked
            ]
            if not selected_labels:
                selected_labels = [labels[0]]

            if button is join_all_button:
                selected_paths = [label_to_path[label] for label in labels]
            else:
                selected_paths = [label_to_path[label] for label in selected_labels]
            break

        chosen_path = selected_paths[0]
        target_box = self.drop_b if edge == "end" else self.drop_a
        target_box.set_file(chosen_path)
        self.mode_combo.setCurrentIndex(self.mode_combo.findData("cut"))
        self.transition_spin.setValue(-1)

        if button is load_button:
            self.status.setText(
                f"完全一致 {len(exact_matches)} 件 / 類似 {len(similar_matches)} 件から読み込み: "
                f"{os.path.basename(chosen_path)}"
            )
            return

        self.start_batch_join(source_box.path, edge, selected_paths)

    def start_batch_join(self, source_path: str, edge: str, match_paths: list[str]) -> None:
        if not match_paths:
            return
        output_folder = os.path.dirname(source_path) or os.getcwd()
        jobs: list[tuple[str, str, str]] = []
        for match_path in match_paths:
            if edge == "end":
                video_a, video_b = source_path, match_path
            else:
                video_a, video_b = match_path, source_path
            jobs.append((video_a, video_b, batch_output_path(output_folder, video_a, video_b)))

        self.run_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self.stop_batch_button.setEnabled(True)
        self.progress.setValue(0)
        self.status.setText(
            f"{len(jobs)} 件の結合を開始..."
        )

        worker = BatchJoinWorker(
            jobs,
            self.transition_spin.value(),
            self.mode_combo.currentData(),
            self.aspect_combo.currentData(),
            self.combo_choice(self.size_combo),
            self.combo_choice(self.fps_combo),
        )
        self.batch_worker = worker
        worker.signals.progress.connect(self.on_progress)
        worker.signals.finished.connect(self.on_batch_finished)
        worker.signals.error.connect(self.on_error)
        self.pool.start(worker)

    def stop_batch_join(self) -> None:
        if self.batch_worker is None:
            return
        self.batch_worker.cancel()
        self.stop_batch_button.setEnabled(False)
        self.status.setText("停止要求を受け付けました。現在の動画が終わったら停止します。")

    def on_batch_finished(self, outputs_text: str) -> None:
        self.batch_outputs = [line for line in outputs_text.splitlines() if line.strip()]
        self.last_output = self.batch_outputs[0] if self.batch_outputs else ""
        if self.last_output:
            self.output_edit.setText(self.last_output)
        self.batch_worker = None
        self.run_button.setEnabled(bool(self.drop_a.path and self.drop_b.path))
        self.open_button.setEnabled(bool(self.last_output))
        self.stop_batch_button.setEnabled(False)
        self.progress.setValue(100)
        if self.last_output:
            show_in_aitan_preview(self.last_output)
        self.status.setText(f"バッチ完了: {len(self.batch_outputs)} 件")
        QMessageBox.information(
            self,
            "バッチ完了",
            "\n".join(self.batch_outputs[:20]) if self.batch_outputs else "出力はありません。",
        )

    def dragEnterEvent(self, event):  # noqa: N802
        if any(is_video_path(url.toLocalFile()) for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):  # noqa: N802
        paths = [url.toLocalFile() for url in event.mimeData().urls()]
        videos = [path for path in paths if is_video_path(path)]
        if not videos:
            return
        if len(videos) >= 2:
            self.drop_a.set_file(videos[0])
            self.drop_b.set_file(videos[1])
        elif not self.drop_a.path:
            self.drop_a.set_file(videos[0])
        else:
            self.drop_b.set_file(videos[0])
        event.acceptProposedAction()

    def open_output(self) -> None:
        if self.last_output and os.path.exists(self.last_output):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.last_output))


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
