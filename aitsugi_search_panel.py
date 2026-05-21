"""SearchPanel for AItsugi.

Drop a video → click 前を探す (find predecessors) or 次を探す (find successors).
Predecessors match the dropped video's START on their END.
Successors  match the dropped video's END   on their START.

Click a result → loads it into the Video A or Video B slot (signal to host).

Self-contained: imports frame_pair_index lazily on background threads so CLIP
model load (15-25 s on CPU) doesn't block the GUI startup.
"""
from __future__ import annotations

import os
import traceback

from PyQt6.QtCore import (QObject, QRunnable, Qt, QThreadPool,
                          pyqtSignal as Signal, pyqtSlot as Slot, QSize)
from PyQt6.QtGui import QImage, QPixmap, QIcon
from PyQt6.QtWidgets import (QFrame, QGroupBox, QHBoxLayout, QLabel,
                             QListWidget, QListWidgetItem, QPushButton,
                             QVBoxLayout)


# ── PIL → QPixmap (no PIL.ImageQt dependency) ───────────────────────────────
def _pil_to_qpixmap(img) -> QPixmap:
    img = img.convert("RGB")
    data = img.tobytes("raw", "RGB")
    qimg = QImage(data, img.width, img.height, img.width * 3,
                  QImage.Format.Format_RGB888).copy()
    return QPixmap.fromImage(qimg)


# ── Worker plumbing ─────────────────────────────────────────────────────────
class _Signals(QObject):
    # build_progress(done, total, message)
    build_progress = Signal(int, int, str)
    build_done     = Signal(int)         # total videos in index after build
    build_failed   = Signal(str)         # error message
    search_done    = Signal(list)        # [(path, similarity), ...]
    search_failed  = Signal(str)
    thumb_ready    = Signal(int, QPixmap)   # (row_index, pixmap)
    thumbs_done    = Signal()


class _BuildRunnable(QRunnable):
    def __init__(self, signals: _Signals, full: bool):
        super().__init__()
        self.signals = signals
        self.full = full

    @Slot()
    def run(self) -> None:
        try:
            import frame_pair_index as fpi
            def _cb(done, total, msg=""):
                self.signals.build_progress.emit(done, total, msg)
            idx = fpi.build_index(progress_cb=_cb, full_rebuild=self.full)
            self.signals.build_done.emit(len(idx.get("paths", [])))
        except Exception as e:
            self.signals.build_failed.emit(f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


class _SearchRunnable(QRunnable):
    """Encodes the query video's start+end frames, then searches the index.
    side='end'   → look at INDEX_END column   (predecessors).
    side='start' → look at INDEX_START column (successors)."""
    def __init__(self, signals: _Signals, query_path: str, side: str, top_n: int):
        super().__init__()
        self.signals = signals
        self.query_path = query_path
        self.side = side
        self.top_n = top_n

    @Slot()
    def run(self) -> None:
        try:
            import frame_pair_index as fpi
            s_emb, e_emb = fpi.encode_video_edges(self.query_path)
            if s_emb is None or e_emb is None:
                self.signals.search_failed.emit("クエリ動画のフレームをエンコードできませんでした。")
                return
            # 'end' side searches INDEX_END (where candidate ENDS),
            # using the query's START as the probe — predecessors.
            query = s_emb if self.side == "end" else e_emb
            hits = fpi.search(query, side=self.side, top_n=self.top_n,
                              exclude=self.query_path)
            self.signals.search_done.emit(hits)
        except Exception as e:
            self.signals.search_failed.emit(
                f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


class _ThumbsRunnable(QRunnable):
    """Decode small thumbnails for each result on a background thread so
    the GUI doesn't stall while N video first-frames are read."""
    def __init__(self, signals: _Signals, paths_with_side: list[tuple[str, str]]):
        super().__init__()
        self.signals = signals
        # paths_with_side: list of (path, edge) — edge in {"start","end"}
        # We thumbnail the EDGE that the match is keyed on.
        self.paths_with_side = paths_with_side

    @Slot()
    def run(self) -> None:
        _IMG = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
        try:
            for i, (p, edge) in enumerate(self.paths_with_side):
                pm = None
                low = p.lower()
                try:
                    from PIL import Image as _Im
                    if low.endswith(_IMG):
                        # Image: same thumb regardless of edge.
                        img = _Im.open(p).convert("RGB")
                        img.thumbnail((96, 96), _Im.LANCZOS)
                        pm = _pil_to_qpixmap(img)
                    else:
                        import cv2
                        cap = cv2.VideoCapture(p, cv2.CAP_FFMPEG)
                        try:
                            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                            if edge == "end" and total > 1:
                                cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, total - 1))
                            ok, frame = cap.read()
                        finally:
                            cap.release()
                        if ok and frame is not None:
                            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                            img = _Im.fromarray(rgb)
                            img.thumbnail((96, 96), _Im.LANCZOS)
                            pm = _pil_to_qpixmap(img)
                except Exception:
                    pm = None
                if pm is not None:
                    self.signals.thumb_ready.emit(i, pm)
            self.signals.thumbs_done.emit()
        except Exception:
            self.signals.thumbs_done.emit()


# ── Query drop box ──────────────────────────────────────────────────────────
class _QueryDropBox(QFrame):
    """Drop area for the query video. Shows first + last thumbnails."""
    file_changed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAcceptDrops(True)
        self.setMinimumHeight(110)
        self.path: str = ""
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(8)
        self.thumb_first = QLabel("先頭")
        self.thumb_first.setFixedSize(120, 90)
        self.thumb_first.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_first.setStyleSheet("background:#222; color:#999; border:1px solid #444;")
        self.thumb_last = QLabel("末尾")
        self.thumb_last.setFixedSize(120, 90)
        self.thumb_last.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_last.setStyleSheet("background:#222; color:#999; border:1px solid #444;")
        self.name_lbl = QLabel("ここに検索したい動画をドロップ")
        self.name_lbl.setWordWrap(True)
        self.name_lbl.setStyleSheet("color:#bbb;")
        lay.addWidget(self.thumb_first)
        lay.addWidget(self.thumb_last)
        lay.addWidget(self.name_lbl, 1)

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dragMoveEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        urls = ev.mimeData().urls()
        if not urls:
            return
        p = urls[0].toLocalFile()
        if not p:
            return
        self.set_file(p)
        ev.acceptProposedAction()

    def set_file(self, path: str) -> None:
        self.path = path
        self.name_lbl.setText(os.path.basename(path))
        # Render thumbnails synchronously — small operation, single file.
        _IMG = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
        try:
            from PIL import Image as _Im
            if path.lower().endswith(_IMG):
                # Image: both START and END thumbnails show the same picture.
                try:
                    im = _Im.open(path).convert("RGB")
                except Exception:
                    im = None
                if im is not None:
                    im.thumbnail((120, 90), _Im.LANCZOS)
                    pm = _pil_to_qpixmap(im)
                    self.thumb_first.setPixmap(pm)
                    self.thumb_last.setPixmap(pm)
            else:
                import cv2
                cap = cv2.VideoCapture(path, cv2.CAP_FFMPEG)
                try:
                    ok1, frame1 = cap.read()
                    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                    ok2, frame2 = False, None
                    if total > 1:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, total - 1))
                        ok2, frame2 = cap.read()
                finally:
                    cap.release()
                if ok1 and frame1 is not None:
                    im = _Im.fromarray(cv2.cvtColor(frame1, cv2.COLOR_BGR2RGB))
                    im.thumbnail((120, 90), _Im.LANCZOS)
                    self.thumb_first.setPixmap(_pil_to_qpixmap(im))
                if ok2 and frame2 is not None:
                    im = _Im.fromarray(cv2.cvtColor(frame2, cv2.COLOR_BGR2RGB))
                    im.thumbnail((120, 90), _Im.LANCZOS)
                    self.thumb_last.setPixmap(_pil_to_qpixmap(im))
        except Exception:
            pass
        self.file_changed.emit(path)


# ── Public panel ────────────────────────────────────────────────────────────
class SearchPanel(QGroupBox):
    """Drop a video → find predecessors/successors → click result to load
    into a joiner slot.

    Signals:
      load_to_a(path) — emit when user picks a predecessor (start match)
      load_to_b(path) — emit when user picks a successor   (end match)
      status(text)    — status / progress messages for the host status bar
    """
    load_to_a = Signal(str)
    load_to_b = Signal(str)
    status    = Signal(str)

    def __init__(self) -> None:
        super().__init__("動画接続候補を検索 (CLIP)")
        self.pool = QThreadPool.globalInstance()
        self.signals = _Signals()
        self.signals.build_progress.connect(self._on_build_progress)
        self.signals.build_done.connect(self._on_build_done)
        self.signals.build_failed.connect(self._on_build_failed)
        self.signals.search_done.connect(self._on_search_done)
        self.signals.search_failed.connect(self._on_search_failed)
        self.signals.thumb_ready.connect(self._on_thumb_ready)
        self._last_side = "end"

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 12, 8, 8)
        outer.setSpacing(6)

        self.query = _QueryDropBox()
        outer.addWidget(self.query)

        btn_row = QHBoxLayout()
        self.btn_pred = QPushButton("前を探す (start ← end)")
        self.btn_pred.setToolTip("候補の末尾フレームがこの動画の先頭と似ている動画 (=この動画より前)")
        self.btn_succ = QPushButton("次を探す (end → start)")
        self.btn_succ.setToolTip("候補の先頭フレームがこの動画の末尾と似ている動画 (=この動画より後)")
        self.btn_pred.clicked.connect(lambda: self._search(side="end"))
        self.btn_succ.clicked.connect(lambda: self._search(side="start"))
        btn_row.addWidget(self.btn_pred)
        btn_row.addWidget(self.btn_succ)
        outer.addLayout(btn_row)

        self.results = QListWidget()
        self.results.setIconSize(QSize(96, 72))
        self.results.setMinimumHeight(180)
        self.results.itemActivated.connect(self._on_item_activated)
        self.results.itemDoubleClicked.connect(self._on_item_activated)
        outer.addWidget(self.results, 1)

        bottom_row = QHBoxLayout()
        self.lbl_status = QLabel("インデックス: 未確認")
        self.lbl_status.setStyleSheet("color:#aaa;")
        self.btn_build = QPushButton("インデックス更新")
        self.btn_build.clicked.connect(self._build_index)
        bottom_row.addWidget(self.lbl_status, 1)
        bottom_row.addWidget(self.btn_build)
        outer.addLayout(bottom_row)

        self._refresh_index_status()

    # ── Index status ────────────────────────────────────────────────────────
    def _refresh_index_status(self) -> None:
        index_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "frame_pairs.pt")
        if not os.path.exists(index_path):
            self.lbl_status.setText("インデックス: 未作成")
            return
        try:
            import torch
            idx = torch.load(index_path, map_location="cpu")
            n = len(idx["paths"]) if idx else 0
            self.lbl_status.setText(f"インデックス: {n} 本")
        except Exception as e:
            self.lbl_status.setText(f"インデックス読み込み失敗: {e}")

    def _build_index(self) -> None:
        self.btn_build.setEnabled(False)
        self.btn_pred.setEnabled(False)
        self.btn_succ.setEnabled(False)
        self.lbl_status.setText("インデックス構築中… (初回はCLIPロードに数十秒)")
        self.status.emit("インデックス構築開始")
        self.pool.start(_BuildRunnable(self.signals, full=False))

    def _on_build_progress(self, done: int, total: int, msg: str) -> None:
        self.lbl_status.setText(f"構築 {done}/{total} {msg}")

    def _on_build_done(self, n: int) -> None:
        self.lbl_status.setText(f"インデックス: {n} 本 (構築完了)")
        self.status.emit(f"インデックス構築完了: {n} 本")
        self.btn_build.setEnabled(True)
        self.btn_pred.setEnabled(True)
        self.btn_succ.setEnabled(True)

    def _on_build_failed(self, msg: str) -> None:
        self.lbl_status.setText("インデックス構築に失敗")
        self.status.emit("インデックス構築失敗 — ターミナル参照")
        print(msg)
        self.btn_build.setEnabled(True)
        self.btn_pred.setEnabled(True)
        self.btn_succ.setEnabled(True)

    # ── Search ──────────────────────────────────────────────────────────────
    def _search(self, side: str) -> None:
        if not self.query.path:
            self.status.emit("クエリ動画が未指定です。")
            return
        self._last_side = side
        self.results.clear()
        self.btn_pred.setEnabled(False)
        self.btn_succ.setEnabled(False)
        self.lbl_status.setText("検索中…")
        self.pool.start(_SearchRunnable(self.signals, self.query.path, side, top_n=30))

    def _on_search_done(self, hits: list) -> None:
        self.btn_pred.setEnabled(True)
        self.btn_succ.setEnabled(True)
        if not hits:
            self.lbl_status.setText("一致候補なし (インデックスを構築済みですか?)")
            return
        self.lbl_status.setText(f"{len(hits)} 件 — クリックで {'A' if self._last_side=='end' else 'B'} スロットへ")
        # The column we matched on is the row's "matched edge" thumbnail.
        edge = self._last_side  # 'end' → predecessor's END frame visible
        thumb_jobs: list[tuple[str, str]] = []
        for i, (path, sim) in enumerate(hits):
            it = QListWidgetItem(f"{sim:.3f}   {os.path.basename(path)}")
            it.setData(Qt.ItemDataRole.UserRole, path)
            self.results.addItem(it)
            thumb_jobs.append((path, edge))
        self.pool.start(_ThumbsRunnable(self.signals, thumb_jobs))

    def _on_search_failed(self, msg: str) -> None:
        self.btn_pred.setEnabled(True)
        self.btn_succ.setEnabled(True)
        self.lbl_status.setText("検索失敗 — ターミナル参照")
        print(msg)

    def _on_thumb_ready(self, idx: int, pm: QPixmap) -> None:
        if 0 <= idx < self.results.count():
            self.results.item(idx).setIcon(QIcon(pm))

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        # _last_side='end' → predecessor → goes into A (plays first)
        # _last_side='start' → successor   → goes into B (plays second)
        if self._last_side == "end":
            self.load_to_a.emit(path)
            self.status.emit(f"A スロットへ: {os.path.basename(path)}")
        else:
            self.load_to_b.emit(path)
            self.status.emit(f"B スロットへ: {os.path.basename(path)}")
