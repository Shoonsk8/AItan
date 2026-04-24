import os, re, cv2, shutil, subprocess, io, torch, time, threading, json
from PIL import Image, PngImagePlugin

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QLabel, QMenu,
                              QApplication, QDialog, QHBoxLayout, QPushButton,
                              QCheckBox, QComboBox, QGridLayout, QLineEdit,
                              QTextEdit, QScrollArea, QSizePolicy,
                              QSplitter, QSplitterHandle, QToolButton, QMessageBox)
import aisearch_attrs as attrs_mod
from PyQt6.QtCore import Qt, QTimer, QUrl, QMimeData, QPoint, QEvent, QSize, pyqtSignal, pyqtSlot, Q_ARG, QMetaObject
from PyQt6.QtGui import QPixmap, QIcon, QDrag, QCursor, QFont, QPainter, QColor, QImage

from aisearch_config import FolderPickerDialog
import aisearch_front_page as front_page
from attr_viewer import _lang_label as _t

VERSION = "1.961"


def _read_embedded_meta(path):
    """Read AI generation metadata embedded in the physical file.
    Returns dict with keys: prompt, neg_prompt, seed, model, speech — or {} if none found."""
    ext = path.lower()
    data = {}
    try:
        if ext.endswith(('.mp4', '.mkv', '.mov', '.m4v')):
            from mutagen.mp4 import MP4
            video = MP4(path)
            comment = video.get("\xa9cmt", [""])[0]
            data["model"] = video.get("\xa9too", [""])[0]
            if "DATA: " in comment:
                try:
                    extra = json.loads(comment.split("DATA: ")[1])
                    data["prompt"]     = extra.get("prompt", "")
                    data["neg_prompt"] = extra.get("neg_prompt", "")
                    data["seed"]       = str(extra.get("seed", ""))
                    data["speech"]     = extra.get("speech", "")
                    data["model"]      = extra.get("custom", data["model"])
                except Exception:
                    pass
            elif comment.startswith("{"):
                # ComfyUI workflow JSON — unwrap and extract via attrs helper
                try:
                    import aisearch_attrs as _am
                    outer = json.loads(comment)
                    workflow_str = outer.get("prompt") or outer.get("workflow", "")
                    _tmp = {}
                    _am._extract_comfyui_meta({"prompt": workflow_str}, _tmp)
                    if _tmp.get("Prompt"):     data["prompt"]     = _tmp["Prompt"]
                    if _tmp.get("NegPrompt"):  data["neg_prompt"] = _tmp["NegPrompt"]
                    if _tmp.get("Seed"):       data["seed"]       = _tmp["Seed"]
                    if _tmp.get("Model"):      data["model"]      = _tmp["Model"]
                except Exception:
                    pass
        elif ext.endswith('.png'):
            with Image.open(path) as img:
                raw = img.info.get("Description") or img.info.get("prompt", "")
                if raw:
                    if raw.startswith('{'):
                        try:
                            d = json.loads(raw)
                            data = {k: str(d.get(k, "")) for k in
                                    ("prompt", "neg_prompt", "seed", "model", "speech")}
                        except Exception:
                            data["prompt"] = raw
                    else:
                        data["prompt"] = raw
                data["model"] = data.get("model") or img.info.get("Software", "")
        elif ext.endswith(('.jpg', '.jpeg', '.webp')):
            with Image.open(path) as img:
                desc = img.info.get("ImageDescription", "")
                if not desc:
                    exif = img.getexif() if hasattr(img, "getexif") else {}
                    desc = exif.get(0x010e, "") or exif.get(0x013b, "")
                if desc:
                    if desc.startswith('{'):
                        try:
                            d = json.loads(desc)
                            data = {k: str(d.get(k, "")) for k in
                                    ("prompt", "neg_prompt", "seed", "model", "speech")}
                        except Exception:
                            data["prompt"] = desc
                    else:
                        data["prompt"] = desc
    except Exception:
        pass
    # Discard AItan baked-attrs blocks — they are not AI generation prompts
    for _k in ("prompt", "neg_prompt", "speech"):
        if data.get(_k, "").startswith("AItan{"):
            data.pop(_k, None)
    return {k: v for k, v in data.items() if v}


def _norm_pid(pid):
    """Normalize a person ID: strip leading 'P'/'p' so '001' and 'P001' both become '001'."""
    if pid and pid[0].lower() == 'p' and len(pid) > 1 and pid[1:].isalnum():
        return pid[1:].lower()
    return pid.lower() if pid else pid


def _bake_embedded_meta(path, data):
    """Embed AI generation metadata into the physical file.
    data: dict with prompt, neg_prompt, seed, model, speech.
    Returns (True, None) on success, (False, error_str) on failure."""
    ext = path.lower()
    payload = json.dumps({
        "prompt":     data.get("prompt", ""),
        "neg_prompt": data.get("neg_prompt", ""),
        "seed":       data.get("seed", ""),
        "custom":     data.get("model", ""),
        "speech":     data.get("speech", ""),
        "person_id":  data.get("person_id", ""),
    })
    try:
        if ext.endswith(('.mp4', '.m4v', '.mov')):
            from mutagen.mp4 import MP4
            video = MP4(path)
            video["\xa9cmt"] = [f"PROMPT: {data.get('prompt','')}\nDATA: {payload}"]
            video["\xa9too"] = [data.get("model", "")]
            video.save()
        elif ext.endswith('.png'):
            img = Image.open(path)
            meta = PngImagePlugin.PngInfo()
            meta.add_text("Description", payload)
            if data.get("model"):
                meta.add_text("Software", data["model"])
            img.save(path, pnginfo=meta)
        elif ext.endswith(('.jpg', '.jpeg', '.webp')):
            import piexif
            img = Image.open(path)
            try:
                exif_dict = piexif.load(img.info.get("exif", b""))
            except Exception:
                exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}}
            exif_dict.setdefault("0th", {})
            exif_dict["0th"][piexif.ImageIFD.ImageDescription] = payload.encode("utf-8")
            exif_bytes = piexif.dump(exif_dict)
            fmt = "JPEG" if ext.endswith(('.jpg', '.jpeg')) else "WEBP"
            img.save(path, fmt, exif=exif_bytes)
        else:
            return False, f"Baking not supported for this file type ({os.path.splitext(path)[1]})"
        return True, None
    except Exception as e:
        return False, str(e)


class _GripSplitterHandle(QSplitterHandle):
    """Splitter handle with centered grip dots so it's visually obvious."""
    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx = self.width() // 2
        cy = self.height() // 2
        p.setBrush(QColor("#666"))
        p.setPen(Qt.PenStyle.NoPen)
        for dy in (-3, 3):
            for dx in (-8, -4, 0, 4, 8):
                p.drawEllipse(cx + dx - 2, cy + dy - 2, 4, 4)
        p.end()


class _GripSplitter(QSplitter):
    def createHandle(self):
        return _GripSplitterHandle(self.orientation(), self)


def _pil_to_pixmap(img):
    img = img.convert("RGB")
    w, h = img.size
    data = img.tobytes("raw", "RGB")
    qimg = QImage(data, w, h, w * 3, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qimg)


# Map from attrs_tags section key → db field name used in attrs_mod.set_file()
_TEXT_KEY_MAP = {
    "positive_prompt": "prompt",
    "negative_prompt": "neg_prompt",
    "speech":          "speech",
}


class PreviewLabel(QLabel):
    """Image display label — handles mouse events and drag-to-copy/move."""
    def __init__(self, handler):
        super().__init__()
        self.handler = handler
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(0, 0)   # allow splitter to shrink freely
        self.setStyleSheet("background-color: black;")
        self.setMinimumSize(1, 1)   # allow window to shrink freely
        self._press_pos = None
        self._press_global = None
        self._shift_at_press = False
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls:
                path = urls[0].toLocalFile()
                ext = os.path.splitext(path)[1].lower()
                from aisearch_logic import EXT_IMG, EXT_VID
                if ext in EXT_IMG + EXT_VID:
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if not urls: return
        path = urls[0].toLocalFile()
        if os.path.exists(path):
            self.handler.show(path)
            event.acceptProposedAction()

    def keyPressEvent(self, event):
        # Forward all key events to the parent PreviewWindow
        parent = self.parent()
        while parent and not isinstance(parent, PreviewWindow):
            parent = parent.parent()
        if parent:
            parent.keyPressEvent(event)
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus()
            self._press_pos = event.pos()
            self._press_global = event.globalPosition().toPoint()
            self._shift_at_press = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.MouseButton.LeftButton): return
        if self._press_pos is None: return

        # Pan when zoomed in
        if self.handler.zoom_factor > 1.0:
            curr = event.globalPosition().toPoint()
            delta = curr - self._press_global
            self.handler.pan_image(delta)
            self._press_global = curr
            return

        if (event.pos() - self._press_pos).manhattanLength() < QApplication.startDragDistance(): return

        path = self.handler.current_path
        if not path: return

        shift = self._shift_at_press
        drag = QDrag(self)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(os.path.abspath(path))])
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction | Qt.DropAction.MoveAction, Qt.DropAction.CopyAction)

        if shift:
            self.handler._on_shift_drag_done(path)

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        self.handler.zoom_factor *= (1.1 if delta > 0 else 0.9)
        self.handler.zoom_factor = max(0.1, min(self.handler.zoom_factor, 10.0))
        self.handler._render(self.handler.current_path,
                             self.handler.current_path.lower().endswith(
                                 ('.mp4', '.mkv', '.mov', '.avi', '.webm'))
                             if self.handler.current_path else False,
                             fast=True)
        self.handler._start_zoom_cleanup_timer()
        event.accept()

    def contextMenuEvent(self, event):
        self.handler._show_context_menu(event.globalPos())

    def mouseDoubleClickEvent(self, event):
        path = self.handler.current_path
        if not path: return
        is_video = path.lower().endswith(('.mp4', '.mkv', '.mov', '.avi', '.webm'))
        if self.handler.app.config.get("dbl_click_spread", False):
            self.handler._toggle_physical_geometry(path, is_video)
        else:
            # Not in spread mode — ensure state is clean so spread works next time
            self.handler.is_maximized = False
            self.handler._expanded_wid = None
            import aisearch_front_page as fp
            fp.open_external_viewer(path, keep_open=self.handler.app.keep_viewer_open)


def _resize_textedit(te):
    """Resize a QTextEdit to fit its content (deferred-safe)."""
    doc = te.document()
    doc.setTextWidth(te.viewport().width() or te.width() or 300)
    h = int(doc.size().height()) + 2 * te.frameWidth() + 6
    te.setFixedHeight(max(28, min(h, 400)))


class _AttrSection(QWidget):
    """Collapsible section widget for the attributes panel coded fields."""
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self._title = title
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 1, 0, 1)
        root.setSpacing(0)

        hdr = QWidget()
        hdr.setStyleSheet("background:#2d2d2d;")
        hdr_lay = QHBoxLayout(hdr)
        hdr_lay.setContentsMargins(2, 1, 2, 1)
        hdr_lay.setSpacing(4)

        self._arrow = QToolButton()
        self._arrow.setArrowType(Qt.ArrowType.RightArrow)
        self._arrow.setCheckable(True)
        self._arrow.setStyleSheet("QToolButton { border:none; background:transparent; }")
        self._arrow.toggled.connect(self._on_toggle)
        hdr_lay.addWidget(self._arrow)

        lbl = QLabel(title)
        lbl.setStyleSheet("color:#f0c040; font-weight:bold; font-size:9pt;")
        hdr_lay.addWidget(lbl)

        self._info_lbl = QLabel("")
        self._info_lbl.setStyleSheet("color:#888; font-size:8pt;")
        self._info_lbl.setVisible(False)
        hdr_lay.addWidget(self._info_lbl)

        self._hdr_lay = hdr_lay   # exposed so callers can append widgets

        root.addWidget(hdr)

        self.content = QWidget()
        self.content.setStyleSheet("background:#1e1e1e;")
        self.content.setVisible(False)
        root.addWidget(self.content)

        self._clear_cbs: list = []
        self._expand_cb = None

    def _on_toggle(self, checked: bool):
        self.content.setVisible(checked)
        self._arrow.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
        )
        if checked and self._expand_cb:
            self._expand_cb()
        from PyQt6.QtWidgets import QScrollArea
        p = self.parentWidget()
        while p is not None:
            if isinstance(p, QScrollArea):
                break
            p.adjustSize()
            p.updateGeometry()
            p = p.parentWidget()

    def set_info(self, text: str):
        self._info_lbl.setText(text)
        self._info_lbl.setVisible(bool(text))

    def set_expand_callback(self, cb):
        self._expand_cb = cb

    def set_expanded(self, v: bool):
        self._arrow.setChecked(v)

    def is_expanded(self):
        return self._arrow.isChecked()

    def register_clear(self, cb):
        self._clear_cbs.append(cb)

    def _on_delete(self):
        for cb in self._clear_cbs:
            cb()


# Module-level thumbnail cache: (path, mtime) → QPixmap (28×28)
_THUMB_CACHE: dict = {}

class PreviewWindow(QWidget):
    """Standalone preview window."""
    _raw_refresh_signal = pyqtSignal(str)   # emitted from background thread with file path

    def __init__(self, handler):
        super().__init__()
        self.handler = handler
        self._file_info_text = ""
        self.setWindowFlag(Qt.WindowType.Window)
        self.resize(700, 700)
        self.setStyleSheet("background-color: black;")
        # Debounce timer for resize events — prevents 40+ back-to-back renders
        # while the window lays out; fires 80ms after the last resize.
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(80)
        self._resize_timer.timeout.connect(self._on_resize_settled)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._raw_refresh_signal.connect(self._on_raw_refresh)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self._splitter = _GripSplitter(Qt.Orientation.Vertical)
        self._splitter.setHandleWidth(10)
        self._splitter.setStyleSheet(
            "QSplitter::handle { background-color: #2a2a2a; }")
        root_layout.addWidget(self._splitter)

        # Top pane: image in scroll area (enables panning when zoomed)
        self.label = PreviewLabel(handler)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidget(self.label)
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll_area.setStyleSheet("border: none; background: black;")
        # Install event filter on viewport to catch clicks and drops on empty areas
        self.scroll_area.viewport().installEventFilter(self)
        self.label.installEventFilter(self)
        self._nav_press_pos = None
        self.scroll_area.setAcceptDrops(True)
        self.scroll_area.viewport().setAcceptDrops(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setMinimumHeight(0)   # allow splitter to shrink the image pane freely
        self._splitter.addWidget(self.scroll_area)

        # Bottom pane: outer HBox holds a left-edge strip (horizontal mode) + inner VBox
        self._bottom_pane = QWidget()
        self._bottom_pane.setMinimumHeight(0)
        self._bottom_pane.setStyleSheet("background-color: #1a1a1a;")
        _outer_hbox = QHBoxLayout(self._bottom_pane)
        _outer_hbox.setContentsMargins(0, 0, 0, 0)
        _outer_hbox.setSpacing(0)

        # Left strip — shown only when splitter is Horizontal (attr on side)
        self._left_bar = QWidget()
        self._left_bar.setFixedWidth(26)
        self._left_bar.setStyleSheet("background-color: #1a1a1a;")
        _lbar_layout = QVBoxLayout(self._left_bar)
        _lbar_layout.setContentsMargins(0, 0, 0, 0)
        _lbar_layout.setSpacing(0)
        _lbar_layout.addStretch()
        self._btn_toggle_left = QPushButton("►")
        self._btn_toggle_left.setFlat(True)
        self._btn_toggle_left.setStyleSheet(
            "color: #aaa; background-color: #1a1a1a; border: none; padding: 4px;")
        self._btn_toggle_left.setFixedSize(26, 26)
        self._btn_toggle_left.clicked.connect(self._toggle_attrs)
        _lbar_layout.addWidget(self._btn_toggle_left)
        _lbar_layout.addStretch()
        self._btn_back_left = QPushButton("⏮")
        self._btn_back_left.setFlat(True)
        self._btn_back_left.setToolTip(_t("Go back to previously viewed file / 直前に表示したファイルに戻る"))
        self._btn_back_left.setStyleSheet(
            "color: #aaa; background-color: #1a1a1a; border: none; padding: 4px;")
        self._btn_back_left.setFixedSize(26, 26)
        self._btn_back_left.clicked.connect(handler._go_back_from_preview)
        _lbar_layout.addWidget(self._btn_back_left)
        self._btn_orient_left = QPushButton("⇕")
        self._btn_orient_left.setFlat(True)
        self._btn_orient_left.setToolTip(_t("Toggle attr pane side or below / 属性ペインを横/下に切替"))
        self._btn_orient_left.setStyleSheet(
            "color: #888; background-color: #1a1a1a; border: none; padding: 4px;")
        self._btn_orient_left.setFixedSize(26, 26)
        self._btn_orient_left.clicked.connect(handler._toggle_splitter_orientation)
        _lbar_layout.addWidget(self._btn_orient_left)
        self._left_bar.hide()   # hidden in vertical mode
        _outer_hbox.addWidget(self._left_bar)

        # Right/inner area — VBox with top strip + attr scroll
        _inner = QWidget()
        _inner_vbox = QVBoxLayout(_inner)
        _inner_vbox.setContentsMargins(0, 0, 0, 0)
        _inner_vbox.setSpacing(0)

        # Top strip — shown only when splitter is Vertical (attr below)
        self._top_bar = QWidget()
        self._top_bar.setFixedHeight(26)
        self._top_bar.setStyleSheet("background-color: #1a1a1a;")
        _tbar_layout = QHBoxLayout(self._top_bar)
        _tbar_layout.setContentsMargins(0, 0, 0, 0)
        _tbar_layout.setSpacing(0)
        self._btn_toggle_top = QPushButton("▲")
        self._btn_toggle_top.setFlat(True)
        self._btn_toggle_top.setStyleSheet(
            "color: #aaa; background-color: #1a1a1a; border: none; padding: 4px;")
        self._btn_toggle_top.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._btn_toggle_top.setFixedHeight(26)
        self._btn_toggle_top.clicked.connect(self._toggle_attrs)
        _tbar_layout.addWidget(self._btn_toggle_top)
        self._btn_back_top = QPushButton("⏮")
        self._btn_back_top.setFlat(True)
        self._btn_back_top.setToolTip(_t("Go back to previously viewed file / 直前に表示したファイルに戻る"))
        self._btn_back_top.setStyleSheet(
            "color: #aaa; background-color: #1a1a1a; border: none; padding: 4px;")
        self._btn_back_top.setFixedSize(26, 26)
        self._btn_back_top.clicked.connect(handler._go_back_from_preview)
        _tbar_layout.addWidget(self._btn_back_top)
        self.btn_orient = QPushButton("⇔")
        self.btn_orient.setFlat(True)
        self.btn_orient.setToolTip(_t("Toggle attr pane side or below / 属性ペインを横/下に切替"))
        self.btn_orient.setStyleSheet(
            "color: #888; background-color: #1a1a1a; border: none; padding: 4px;")
        self.btn_orient.setFixedSize(26, 26)
        self.btn_orient.clicked.connect(handler._toggle_splitter_orientation)
        _tbar_layout.addWidget(self.btn_orient)
        _inner_vbox.addWidget(self._top_bar)

        # btn_toggle_attrs points to whichever strip is active (top in vertical mode)
        self.btn_toggle_attrs = self._btn_toggle_top

        # Info bar — shown in horizontal mode above the attr scroll
        self._info_bar = QLabel("")
        self._info_bar.setFixedHeight(18)
        self._info_bar.setStyleSheet(
            "background-color: #1a1a1a; color: #888; font-size: 8pt; padding: 0 6px;")
        self._info_bar.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self._info_bar.setVisible(False)
        _inner_vbox.addWidget(self._info_bar)

        # Attr panel scroll area — panel built lazily on first expand/navigate
        _tags_f = attrs_mod.tags_file_for_project(
            getattr(handler.app, 'current_project', None))
        try:
            self._tags_file_mtime = os.path.getmtime(_tags_f)
        except OSError:
            self._tags_file_mtime = 0
        self.attr_widget = None
        self._attr_panel_built = False
        self._attr_panel_pending_path = None   # path waiting for panel to be ready
        self._attr_scroll = QScrollArea()
        self._attr_scroll.setWidgetResizable(True)
        self._attr_scroll.setWidget(QWidget())  # empty placeholder until built
        self._attr_scroll.setStyleSheet("QScrollArea { border: none; background: #2e2e2e; }")
        self._attr_scroll.setMinimumHeight(0)   # allow splitter to fully collapse this pane
        self._attr_scroll.hide()                # start collapsed; ▲ is correct initial state
        _inner_vbox.addWidget(self._attr_scroll)
        # NOTE: _deferred_build_attr_panel is NOT scheduled here.
        # show() schedules it AFTER _render so the image appears first.
        _outer_hbox.addWidget(_inner, stretch=1)

        self._splitter.addWidget(self._bottom_pane)
        self._splitter.setStretchFactor(0, 1)   # image takes all extra space
        self._splitter.setStretchFactor(1, 0)   # attr pane stays compact
        self._splitter.setCollapsible(0, False)
        self._splitter.setCollapsible(1, False)
        self._splitter.splitterMoved.connect(self._on_splitter_moved)
        # Separate saved sizes for vertical (height) and horizontal (width) modes
        self._attrs_split_size_v = handler.app.config.get("attrs_split_size", 300)
        self._attrs_split_size_h = handler.app.config.get("attrs_split_size_h", 350)
        self._attrs_split_size   = self._attrs_split_size_v   # compat alias
        # Restore saved splitter orientation
        _saved_orient = handler.app.config.get("attrs_splitter_orient", "vertical")
        if _saved_orient == "horizontal":
            self._splitter.setOrientation(Qt.Orientation.Horizontal)
        def _init_sizes():
            self._sync_toggle_strip()
            sp = self._splitter
            total = sp.width() if sp.orientation() == Qt.Orientation.Horizontal else sp.height()
            if total > 0:
                sp.setSizes([max(1, total - 26), 26])
        QTimer.singleShot(0, _init_sizes)

        # Loading indicator — reuses the toggle button text (no floating overlay needed)
        self.loading_label = QLabel("", self)   # kept for compat but stays hidden
        self.loading_label.hide()

        from PyQt6.QtGui import QShortcut, QKeySequence
        QShortcut(QKeySequence("Ctrl+S"), self, self._bake_to_file)

    def eventFilter(self, obj, event):
        """Catch mouse press/release and drops on the scroll area viewport and label."""
        # --- Scroll area / label events ---
        if obj is self.scroll_area.viewport() or obj is self.label:
            if event.type() == QEvent.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.LeftButton:
                    self._nav_press_pos = event.globalPosition().toPoint()
            elif event.type() == QEvent.Type.MouseButtonRelease:
                if event.button() == Qt.MouseButton.LeftButton and self._nav_press_pos is not None:
                    cur = event.globalPosition().toPoint()
                    if (cur - self._nav_press_pos).manhattanLength() < QApplication.startDragDistance():
                        self._check_nav_zone(cur)
                    self._nav_press_pos = None
            elif event.type() == QEvent.Type.DragEnter:
                if event.mimeData().hasUrls():
                    event.acceptProposedAction()
                    return True
            elif event.type() == QEvent.Type.Drop:
                urls = event.mimeData().urls()
                if urls:
                    path = urls[0].toLocalFile()
                    if os.path.exists(path):
                        self.handler.show(path)
                        event.acceptProposedAction()
                        return True
        return super().eventFilter(obj, event)

    def _check_nav_zone(self, global_pos):
        """Fire navigation action based on where in the image pane the click landed."""
        sa = self.scroll_area
        w, h = sa.width(), sa.height()
        if w == 0 or h == 0: return
        p  = sa.mapFromGlobal(global_pos)
        rx = p.x() / w
        ry = p.y() / h
        if ry < 0.15:
            self.handler._navigate(-1)
        elif ry > 0.85:
            self.handler._navigate(1)
        elif rx < 0.10:
            self.handler.app.on_left_key_press()
        elif rx > 0.90:
            self.handler.app.on_right_key_press()
        else:
            path = self.handler.current_path
            if path:
                QApplication.clipboard().setText(os.path.abspath(path))

    def keyPressEvent(self, event):
        key  = event.key()
        mods = event.modifiers()
        if key == Qt.Key.Key_Escape:
            self.handler._close()
        elif key == Qt.Key.Key_W and mods & Qt.KeyboardModifier.ControlModifier:
            self.handler._close()
        elif key == Qt.Key.Key_C and mods & Qt.KeyboardModifier.ControlModifier:
            self.handler._copy_path_to_clipboard()
        elif key == Qt.Key.Key_Delete:
            self.handler.app.delete_file()
        elif key == Qt.Key.Key_Backspace:
            self.handler._go_back_from_preview()
        elif key == Qt.Key.Key_Down:
            self.handler._navigate(1)
        elif key == Qt.Key.Key_Up:
            self.handler._navigate(-1)
        elif key == Qt.Key.Key_Right:
            self.handler.app.on_right_key_press()
        elif key == Qt.Key.Key_Left:
            self.handler.app.on_left_key_press()
        elif key == Qt.Key.Key_T:
            self.handler._toggle_always_on_top(
                not bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))
        elif key == Qt.Key.Key_A:
            self._toggle_attrs()
        elif key == Qt.Key.Key_Home:
            self.handler.app._go_to_first_row()
        elif key == Qt.Key.Key_End:
            self.handler.app._go_to_last_row()
        elif key == Qt.Key.Key_F2:
            self.handler.app.rename_file()
        elif key == Qt.Key.Key_S and not mods:
            self.handler.app._open_settings()
        else:
            super().keyPressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Debounce: reset the timer on every resize; render fires 80ms after last one
        self._resize_timer.start()

    def _on_resize_settled(self):
        if self.handler.current_path:
            is_vid = self.handler.current_path.lower().endswith(('.mp4', '.mkv', '.mov', '.avi', '.webm'))
            self.handler._render(self.handler.current_path, is_vid)

    def _build_attr_panel(self):
        panel = QWidget()
        _fs = self.handler.app.config.get("attr_font_size", 10)
        panel.setStyleSheet(f"background-color: #2e2e2e; font-size: {_fs}pt;")
        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(8, 4, 8, 6)
        vbox.setSpacing(3)

        # Load project tag groups once — used for section titles and dynamic sections
        _proj_tg_early = attrs_mod._load_tag_groups(
            attrs_mod.tags_file_for_project(self.handler.app.current_project))
        _sec_groups = _proj_tg_early.get("__section_groups__", {})
        # Build reverse map: section key → group name
        _key_to_group = {k: grp for grp, keys in _sec_groups.items() for k in keys}
        def _grp_name(key): return _key_to_group.get(key, key)

        # Keep _quality_combo as orphan widget for _save_attrs/_refresh_attrs compat
        self._quality_combo = QComboBox()
        self._quality_combo.wheelEvent = lambda e: e.ignore()
        self._quality_combo.addItem("—", "")
        for key, lbl in attrs_mod.TAG_GROUPS.get("Quality", []):
            self._quality_combo.addItem(_t(lbl), key)
        self._quality_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._quality_combo.currentIndexChanged.connect(self._save_attrs)

        # Note row (at top for quick access) — hidden when project defines "note" as a text field
        self._note_row_widget = QWidget()
        r_title = QHBoxLayout(self._note_row_widget)
        r_title.setContentsMargins(0, 0, 0, 0)
        lp = QLabel(_t("Note: / ノート："))
        lp.setStyleSheet("color: #aaa;")
        r_title.addWidget(lp)
        self._project_edit = QLineEdit()
        self._project_edit.setPlaceholderText(_t("note… / ノート…"))
        self._project_edit.setStyleSheet(
            "background-color: #3a3a3a; color: #e0e0e0; border: 1px solid #555;")
        self._project_edit.editingFinished.connect(self._save_attrs)
        r_title.addWidget(self._project_edit, stretch=1)
        # note row kept as orphan widget (referenced by _refresh_attrs/_save_attrs)

        # ── Coded filename fields ─────────────────────────────────────────
        _field_ss = "background:#3a3a3a; color:#e0e0e0; border:1px solid #555; font-family:monospace;"
        _lbl_ss   = "color:#aaa;"

        # ── Person registration row ───────────────────────────────────────
        self._code_edits = {}
        self._p_edits    = []   # populated dynamically from 'id' fields
        self._pw_edits   = []   # populated dynamically from 'id' fields
        self._id_edits   = {}   # section_key → QLineEdit for id-style fields
        rA = QHBoxLayout(); rA.setSpacing(4)

        lp_id = QLabel(_t("Person: / 人物：")); lp_id.setStyleSheet("color:#aaa;")
        rA.addWidget(lp_id)

        # Editable combo — shows registered IDs, user can type a new one
        self._person_id_combo = QComboBox()
        self._person_id_combo.wheelEvent = lambda e: e.ignore()
        self._person_id_combo.setEditable(True)
        self._person_id_combo.setMinimumWidth(140)
        self._person_id_combo.setStyleSheet(_field_ss)
        self._person_id_combo.setPlaceholderText(_t("select or type ID… / IDを選択または入力…"))
        self._person_id_combo.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._person_id_combo.setMaxVisibleItems(20)
        self._person_id_combo.currentIndexChanged.connect(self._on_person_combo_changed)
        rA.addWidget(self._person_id_combo, stretch=1)
        self._person_id_label = self._person_id_combo   # backward-compat alias

        self._person_name_edit = QLineEdit()
        self._person_name_edit.setPlaceholderText(_t("name… / 名前…"))
        self._person_name_edit.setStyleSheet(_field_ss)
        self._person_name_edit.editingFinished.connect(self._on_person_name_changed)
        rA.addWidget(self._person_name_edit, stretch=1)

        self._btn_match_person = QPushButton(_t("Match / 照合"))
        self._btn_match_person.setVisible(False)  # disabled — not working
        self._btn_match_person.clicked.connect(self._on_match_person)

        self._btn_detect_person = QPushButton(_t("Register / 登録"))
        self._btn_detect_person.setVisible(False)  # disabled — not working
        self._btn_detect_person.clicked.connect(self._on_detect_person)

        # person row kept as orphan (referenced by _refresh_attrs/_save_attrs)
        self._refresh_person_id_combo()   # populate from face DB

        # ── Coded fields — collapsible sections ─────────────────────────────
        self._code_combos = {}  # letter.lower() → [(sub_group, pos, QComboBox), ...]

        # Friendly display names for coded field letters (from CODED_FIELDS)
        _field_names  = {ltr: lbl for ltr, lbl, _ in attrs_mod.CODED_FIELDS}
        _field_digits = {ltr: dig for ltr, _, dig in attrs_mod.CODED_FIELDS if dig > 0}

        # Per-field combo specs: letter → [(sub_group, digit_pos, short_label), ...]
        # digit_pos: 1 = rightmost (1st), 2 = middle (2nd), 3 = leftmost (3rd)
        # Loaded from __field_combos__ in attrs_tags.json — fully config-driven.
        # Adding a new entry to __field_combos__ makes it auto-appear here.
        _fc_raw = _proj_tg_early.get("__field_combos__", {})
        _combo_specs = {k.upper(): [(s[0], s[1], s[2]) for s in specs]
                        for k, specs in _fc_raw.items()}
        if not _combo_specs:
            # Fallback for installations without __field_combos__ in config
            _combo_specs = {
                "E":  [("E_Color", 1, "Color"),    ("E_Additional", 2, "Add.")],
                "HC": [("HC_Color", 1, "Color"),   ("HC_Style", 2, "Style"),  ("HC_Length", 3, "Length")],
                "FA": [("FA_Dir", 1, "Dir"),        ("FA_Vert", 2, "Vert")],
                "SK": [("SK_Type", 1, "Type")],
                "B":  [("B_Shape", 1, "Shape"),    ("B_Size", 2, "Size")],
                "WH": [("WH_Hip", 1, "Hip"),        ("WH_Waist", 2, "Waist")],
                "PM": [("PM_Motion", 1, "Motion"),  ("PM_Posture", 2, "Posture")],
                "CS": [("CS_Light", 1, "Light"),    ("CS_Angle", 2, "Angle"), ("CS_Shot", 3, "Shot")],
                "BG": [("Background", 3, "Major")],
            }

        # Helper: decode a coded field value to human-readable string
        # _decode_sub_map derived from _combo_specs (must come after _combo_specs is set)
        _decode_sub_map = {k: [(sg, pos) for sg, pos, _ in specs]
                           for k, specs in _combo_specs.items()}
        # Add HC alias so decode works for both H (config key) and HC (CODED_FIELDS key)
        if "H" in _decode_sub_map and "HC" not in _decode_sub_map:
            _decode_sub_map["HC"] = _decode_sub_map["H"]

        def _decode_field(letter, val):
            if not val:
                return ""
            # digit position: 1 = rightmost, 2 = middle, 3 = leftmost
            def _dig(v, pos):
                return v[-pos] if pos <= len(v) else ""
            parts = []
            for grp, pos in _decode_sub_map.get(letter, []):
                d = _dig(val, pos)
                if not d:
                    continue
                lbl = next((lb for k, lb in attrs_mod.TAG_GROUPS.get(grp, [])
                            if k == d), "")
                if lbl and lbl not in ("(none)", "(undefined)"):
                    parts.append(lbl)
            return " · ".join(parts) if parts else val

        def _make_combo(sub_grp):
            cb = QComboBox()
            cb.wheelEvent = lambda e: e.ignore()
            cb.setMaximumWidth(148)
            cb.setStyleSheet("background:#3a3a3a; color:#e0e0e0; border:1px solid #555;")
            cb.addItem("—", "")
            for key, val_lbl in attrs_mod.TAG_GROUPS.get(sub_grp, []):
                cb.addItem(f"{key}: {val_lbl}", key)
            return cb

        def _add_combo_field(sec_layout, letter, hidden_edit):
            """Build a vertical field widget (label + combos); add to horizontal sec_layout."""
            specs = _combo_specs[letter]
            box = QWidget(); vb = QVBoxLayout(box)
            vb.setContentsMargins(0, 0, 0, 0); vb.setSpacing(1)
            _display = _field_names.get(letter, letter)
            fl = QLabel(f"{_display}:")
            fl.setStyleSheet("color:#aaa; font-weight:bold;")
            vb.addWidget(fl)
            combos = []
            for sub_grp, pos, col_lbl in specs:
                sub_row = QHBoxLayout(); sub_row.setSpacing(2)
                dlbl = QLabel(col_lbl)
                dlbl.setStyleSheet("color:#888; font-size:8pt;")
                sub_row.addWidget(dlbl)
                cb = _make_combo(sub_grp)
                combos.append((sub_grp, pos, cb))
                sub_row.addWidget(cb)
                vb.addLayout(sub_row)
            sec_layout.addWidget(box)
            self._code_combos[letter.lower()] = combos

            def _recompute():
                any_set = any(cb_w.currentData() for _, _, cb_w in combos)
                if not any_set:
                    code = ""
                else:
                    max_pos = max(p for _, p, _ in combos)
                    pos_map = {p: cb_w for _, p, cb_w in combos}
                    code = "".join(
                        (pos_map[p].currentData() or "0") if p in pos_map else "0"
                        for p in range(max_pos, 0, -1)
                    )
                hidden_edit.blockSignals(True)
                hidden_edit.setText(code)
                hidden_edit.blockSignals(False)
                self._save_attrs()
                # Write coded value into attrs_data AFTER set_file creates the entry
                _path = self._attr_path
                if _path and _path in self.handler.app.attrs_data:
                    self.handler.app.attrs_data[_path][letter.lower()] = code
                self._update_bake_btn("pending")
                if attrs_mod.load_filename_config(
                        getattr(self.handler.app, "current_project", None)).get("auto_rename"):
                    self._on_normalize_filename()
            for _, _, cb_w in combos:
                cb_w.currentIndexChanged.connect(_recompute)

            def _clear():
                for _, _, cb_w in combos:
                    cb_w.blockSignals(True); cb_w.setCurrentIndex(0); cb_w.blockSignals(False)
                hidden_edit.blockSignals(True); hidden_edit.setText(""); hidden_edit.blockSignals(False)
                self._on_name_edit_finished()
            return _clear

        def _add_plain_field(sec_layout, letter, digits):
            """Build a vertical field widget (label + input); add to horizontal sec_layout."""
            box = QWidget(); vb = QVBoxLayout(box)
            vb.setContentsMargins(0, 0, 0, 0); vb.setSpacing(1)
            _display = _field_names.get(letter, letter)
            fl = QLabel(f"{_display}:")
            fl.setStyleSheet(_lbl_ss)
            vb.addWidget(fl)

            # J (Julian) is auto-set by the program — show as read-only label
            if letter == "J":
                fe2 = QLineEdit()
                fe2.setFixedWidth(130)   # wide enough for "26-04-17 10:30:45"
                fe2.setReadOnly(True)
                fe2.setStyleSheet(
                    "background:#1a1a1a; color:#888888; border:1px solid #333;"
                    " font-family:monospace; padding:1px 3px;")
                fe2.setPlaceholderText(_t("auto / 自動"))
                fe2.setToolTip(_t("Julian date — set automatically, not editable / ユリウス日 — 自動設定（編集不可）"))
                self._code_edits[letter.lower()] = fe2
                vb.addWidget(fe2)
                sec_layout.addWidget(box)
                def _clear_j(f=fe2):
                    f.blockSignals(True); f.setText(""); f.blockSignals(False)
                return _clear_j

            fe2 = QLineEdit()
            fe2.setFixedWidth(14 + digits * 14)
            fe2.setStyleSheet(_field_ss)
            fe2.setMaxLength(digits)
            fe2.setPlaceholderText("0" * digits)
            if letter == "X":
                fe2.textChanged.connect(self._on_x_code_changed)
            fe2.editingFinished.connect(self._on_name_edit_finished)
            self._code_edits[letter.lower()] = fe2
            vb.addWidget(fe2)
            sec_layout.addWidget(box)
            def _clear():
                fe2.blockSignals(True); fe2.setText(""); fe2.blockSignals(False)
                self._on_name_edit_finished()
            return _clear

        # ── Coded sections — fully dynamic from __field_combos__ + __group_order__ ──
        # Any field in __field_combos__ renders as combos regardless of __section_styles__.
        # Adding a new field+sub-groups to config auto-appears here — no code changes needed.
        # _COMBO_ALIAS: section key in __section_groups__ → CODED_FIELDS key
        # (e.g. H in config → HC in CODED_FIELDS; used so _code_combos key matches parse output)
        _COMBO_ALIAS  = {"H": "HC"}
        # Coded styles → rendered in the group coded-section widget
        # Soft styles  → rendered in the soft sections area (taglist/combo/text)
        _CODED_STYLES = {"id", "1dig", "2dig", "3dig"}
        _SKIP_STYLES  = {"matrix", "boolean", "taglist", "text", ""}  # skip in coded widget
        _STYLE_DIGITS = {"1dig": 1, "2dig": 2, "3dig": 3}

        def _add_id_field(sec_layout, key):
            """Small labeled text input for an id-style section."""
            box = QWidget(); vb = QVBoxLayout(box)
            vb.setContentsMargins(0, 0, 0, 0); vb.setSpacing(1)
            _display = _field_names.get(key, key)
            _lbl = QLabel(f"{_display}:"); _lbl.setStyleSheet(_lbl_ss)
            vb.addWidget(_lbl)
            # J is a timestamp — show as decoded date, read-only
            if key == "J":
                fe = QLineEdit()
                fe.setFixedWidth(130)
                fe.setReadOnly(True)
                fe.setStyleSheet(
                    "background:#1a1a1a; color:#888888; border:1px solid #333;"
                    " font-family:monospace; padding:1px 3px;")
                fe.setPlaceholderText(_t("auto / 自動"))
                fe.setToolTip(_t("Julian date — set automatically / ユリウス日 — 自動設定"))
                self._code_edits["j"] = fe
                vb.addWidget(fe)
                sec_layout.addWidget(box)
                return fe
            fe = QLineEdit()
            fe.setFixedWidth(52); fe.setMaxLength(4)
            fe.setStyleSheet(_field_ss); fe.setPlaceholderText("000")
            fe.editingFinished.connect(self._on_person_id_override)
            fe.textChanged.connect(lambda _: self._update_bake_btn("pending"))
            self._id_edits[key] = fe
            vb.addWidget(fe)
            sec_layout.addWidget(box)
            return fe

        _group_order_list = _proj_tg_early.get("__group_order__", [])
        _section_styles_all = _proj_tg_early.get("__section_styles__", {})
        _INTERNAL_GROUP = "Internal"   # kept for _non_internal_keys compat — no longer skipped

        # Create boolean flag checkboxes dynamically from CODED_FIELDS (digits==0)
        self._bool_flag_checks = {}  # letter.lower() -> QCheckBox
        for _ltr, _lbl, _digs in attrs_mod.CODED_FIELDS:
            if _digs == 0:
                _lk = _ltr.lower()
                _cb = QCheckBox(_ltr)
                _cb.setVisible(False)
                _cb.toggled.connect(lambda _, _l=_ltr: self._on_bool_flag_toggled(_l))
                self._bool_flag_checks[_lk] = _cb
                self._code_edits[_lk] = _cb
        # Backward-compat refs (used by legacy code paths)
        self._w_check  = self._bool_flag_checks.get("wm",  QCheckBox("WM"))
        self._ed_check = self._bool_flag_checks.get("ed",  QCheckBox("ED"))
        self._btn_normalize = QPushButton("Fix"); self._btn_normalize.setVisible(False)
        self._btn_normalize.clicked.connect(self._on_normalize_filename)

        self._field_to_section = {}   # letter.lower() -> _AttrSection
        self._attr_sections = []      # ordered list of all reorderable _AttrSection widgets

        # Container for coded sections — reordering operates inside here
        self._sections_container = QWidget()
        self._sections_container.setStyleSheet("background:transparent;")
        self._sections_vbox = QVBoxLayout(self._sections_container)
        self._sections_vbox.setContentsMargins(0, 0, 0, 0)
        self._sections_vbox.setSpacing(0)
        # coded sections container kept as orphan (widgets created for compatibility)

        for _grp in _group_order_list:
            _grp_keys = _sec_groups.get(_grp, [])

            # Decide how to render each key in the group.
            # __field_combos__ takes priority — checked before __section_styles__.
            # This means BG/matrix and similar are rendered as combos if listed in __field_combos__.
            _renderable = []
            for _sk in _grp_keys:
                # Resolve alias (e.g. H → HC) then check __field_combos__
                _ck = _COMBO_ALIAS.get(_sk, _sk).upper()
                if _ck in _combo_specs:
                    _renderable.append((_sk, "combo", _ck))
                    continue
                _st = _section_styles_all.get(_sk, "")
                if _st in _SKIP_STYLES:
                    continue
                if _st == "id":
                    _renderable.append((_sk, "id", None))
                else:
                    _digs = _STYLE_DIGITS.get(_st) or _field_digits.get(_sk.upper(), 0)
                    if _digs:
                        _renderable.append((_sk, "plain", _digs))

            if not _renderable:
                continue

            _sec_w = _AttrSection(_grp)
            self._sections_vbox.addWidget(_sec_w)
            self._attr_sections.append(_sec_w)
            _sec_lay = QHBoxLayout(_sec_w.content)
            _sec_lay.setContentsMargins(6, 2, 6, 4); _sec_lay.setSpacing(6)

            # When expanded, refresh attrs so the newly-visible fields are populated
            def _make_expand_cb(sw=_sec_w):
                def _cb():
                    if self._attr_path:
                        self._refresh_attrs(self._attr_path)
                return _cb
            _sec_w.set_expand_callback(_make_expand_cb())

            _has_cs = False
            for _sk, _kind, _extra in _renderable:
                # Track which section owns each field key
                self._field_to_section[_sk.lower()] = _sec_w

                if _kind == "id":
                    _fe = _add_id_field(_sec_lay, _sk.upper())
                    # Wire into _p_edits / _pw_edits for backward compat
                    if _sk.upper() in ("PI",):
                        self._p_edits.append(_fe)
                        self._person_id_label = _fe
                    elif _sk.upper() in ("PW",):
                        self._pw_edits.append(_fe)
                        _fe.editingFinished.connect(self._on_pw_changed)
                elif _kind == "combo":
                    _ck = _extra   # the _combo_specs key
                    _hid = QLineEdit(panel); _hid.setVisible(False)
                    self._code_edits[_sk.lower()] = _hid
                    self._field_to_section[_ck.lower()] = _sec_w   # alias key too
                    if _sk.upper() != _ck:
                        self._code_edits[_ck.lower()] = _hid   # alias
                    _sec_w.register_clear(_add_combo_field(_sec_lay, _ck, _hid))
                    if _ck == "CS":
                        _has_cs = True
                else:
                    _sec_w.register_clear(_add_plain_field(_sec_lay, _sk.upper(), _extra))

            # Boolean flag checkboxes + Fix appended to whichever group contains CS
            if _has_cs:
                _flags_box = QWidget(); _flags_vb = QVBoxLayout(_flags_box)
                _flags_vb.setContentsMargins(0, 0, 0, 0); _flags_vb.setSpacing(2)
                for _lk, _cb in self._bool_flag_checks.items():
                    _cb.setVisible(True); _flags_vb.addWidget(_cb)
                self._btn_normalize.setVisible(True); _flags_vb.addWidget(self._btn_normalize)
                _sec_lay.addWidget(_flags_box)
                def _clear_flags():
                    for _cb in self._bool_flag_checks.values():
                        _cb.blockSignals(True); _cb.setChecked(False); _cb.blockSignals(False)
                    self._on_name_edit_finished()
                _sec_w.register_clear(_clear_flags)

            _sec_lay.addStretch()

        # ── Catch-all: any CODED_FIELDS entry not yet rendered ────────────────
        # Ensures new fields added to CODED_FIELDS appear automatically without
        # needing manual section configuration in attrs_tags.json.
        _unrendered = [
            (ltr, lbl, dig) for ltr, lbl, dig in attrs_mod.CODED_FIELDS
            if ltr.lower() not in self._code_edits and dig > 0
        ]
        if _unrendered:
            _extra_sec = _AttrSection("Other")
            self._sections_vbox.addWidget(_extra_sec)
            self._attr_sections.append(_extra_sec)
            _extra_lay = QHBoxLayout(_extra_sec.content)
            _extra_lay.setContentsMargins(6, 2, 6, 4); _extra_lay.setSpacing(6)

            def _make_expand_cb_extra(sw=_extra_sec):
                def _cb():
                    if self._attr_path:
                        self._refresh_attrs(self._attr_path)
                return _cb
            _extra_sec.set_expand_callback(_make_expand_cb_extra())

            for _ltr, _lbl, _dig in _unrendered:
                # Use combo if field has combo specs, otherwise plain edit
                if _ltr in _combo_specs:
                    _hid = QLineEdit(panel); _hid.setVisible(False)
                    self._code_edits[_ltr.lower()] = _hid
                    _extra_sec.register_clear(_add_combo_field(_extra_lay, _ltr, _hid))
                else:
                    _extra_sec.register_clear(_add_plain_field(_extra_lay, _ltr, _dig))
                self._field_to_section[_ltr.lower()] = _extra_sec
            _extra_lay.addStretch()

        # Decode label — shows human-readable breakdown of current coded values
        self._decode_lbl = QLabel("")
        self._decode_lbl.setStyleSheet("color:#888; font-size:8pt;")
        self._decode_lbl.setWordWrap(True)
        # decode_lbl and x_hint kept as orphans
        self._decode_field_fn = _decode_field   # store for use in _refresh_attrs

        self._x_hint = QLabel("")
        self._x_hint.setStyleSheet("color:#888; font-size:8pt;")

        # Keep _name_edit as hidden alias so existing code doesn't break
        self._name_edit = self._code_edits.get("b", QLineEdit())

        # Seed edit kept as orphan (referenced by _refresh_attrs/_save_attrs)
        self._seed_edit = QLineEdit()
        self._seed_edit.editingFinished.connect(self._save_attrs)
        self._seed_edit.editingFinished.connect(lambda: self._update_bake_btn("pending"))

        # ── Dynamic sections driven by attrs_tags.json ──────────────────────
        self._text_save_timer = QTimer()
        self._text_save_timer.setSingleShot(True)
        self._text_save_timer.setInterval(800)
        self._text_save_timer.timeout.connect(self._save_attrs)
        self._text_save_timer.timeout.connect(lambda: self._update_bake_btn("pending"))

        self._soft_sec_map = {}  # kept: referenced by legacy drag-reorder code

        # ── Soft canvas — free-canvas AttrViewerWidget (main attr UI) ───────────
        from attr_viewer import AttrViewerWidget as _AV, _UI_LANG as _av_lang
        _av_lang["val"] = self.handler.app.config.get("ui_language", "en")
        _cfg_path = attrs_mod.tags_file_for_project(self.handler.app.current_project)
        self._soft_canvas = _AV(config_path=_cfg_path, parent=None)
        self._soft_canvas.setMinimumHeight(300)
        vbox.addWidget(self._soft_canvas, stretch=1)
        self._soft_canvas.data_changed.connect(self._text_save_timer.start)
        self._soft_canvas.data_changed.connect(lambda: self._update_bake_btn("pending"))
        self._soft_canvas.action_triggered.connect(self._on_canvas_action)
        self._wire_canvas_bool_flags()
        # Make CLIP and FACE canvas tiles auto-expand to show full detection text
        self._setup_clip_face_autoheight()
        # Design and Drag Mode only available when Arrangement in preview is ON
        _show_raw = self.handler.app.config.get("show_raw_data", False)
        _snap_cb = getattr(self._soft_canvas, "_snap_cb", None)
        _drag_cb = getattr(self._soft_canvas, "_drag_cb", None)
        if not _show_raw:
            if _snap_cb:
                _snap_cb.setEnabled(False)
                _snap_cb.setChecked(False)
                self._soft_canvas._set_snap(False)
            if _drag_cb:
                _drag_cb.setEnabled(False)
                _drag_cb.setChecked(False)

        # Bake row: always visible
        r_bake = QHBoxLayout()
        self._btn_bake_meta = QPushButton(_t("Bake to File / ファイルに書込"))
        self._btn_bake_meta.setToolTip(_t(
            "Embed prompt/seed/model from database into the physical file / データベースのプロンプト/シード/モデルを物理ファイルに埋め込む"))
        self._btn_bake_meta.setStyleSheet(
            "background:#3a3a4a; color:#e0e0e0; border:1px solid #556655; padding:2px 6px;")
        self._btn_bake_meta.clicked.connect(self._bake_to_file)
        r_bake.addWidget(self._btn_bake_meta)
        self._bake_btn_state = "idle"   # idle | pending | ok | error
        from PyQt6.QtWidgets import QCheckBox as _QCB2
        self._chk_auto_bake = _QCB2(_t("Auto-bake / 自動書込"))
        self._chk_auto_bake.setToolTip(_t("Automatically bake to file when navigating to next image / 次の画像へ移動時に自動でファイルに書き込み"))
        self._chk_auto_bake.setChecked(self.handler.app.config.get("auto_bake", False))
        def _on_ab_toggle(v):
            self.handler.app.config["auto_bake"] = v
            import aisearch_config as _cfg
            _cfg.save_config(self.handler.app.config,
                             getattr(self.handler.app, "current_project", None))
        self._chk_auto_bake.toggled.connect(_on_ab_toggle)
        r_bake.addWidget(self._chk_auto_bake)
        self._btn_gather = QPushButton(_t("⚑ Gather / ⚑ 集約"))
        self._btn_gather.setToolTip(_t("Move any off-screen canvas tiles back into view / 画面外のキャンバスタイルを表示内に戻す"))
        self._btn_gather.setStyleSheet(
            "QPushButton { background:#2a2a2a; color:#ccaa66; border:1px solid #554433; padding:2px 6px; }"
            "QPushButton:hover { background:#3a3a2a; color:#eedd88; }")
        self._btn_gather.clicked.connect(
            lambda: getattr(self._soft_canvas, "_gather_lost", lambda: None)())
        r_bake.addWidget(self._btn_gather)
        self._btn_apply_clip = QPushButton(_t("🔄 Refresh CLIP / 🔄 CLIP再検出"))
        self._btn_apply_clip.setToolTip(_t("Clear all CLIP fields and re-detect from scratch / 全CLIPフィールドをクリアして最初から再検出"))
        self._btn_apply_clip.setStyleSheet(
            "QPushButton { background:#2a2a3a; color:#88aacc; border:1px solid #446; padding:2px 6px; }"
            "QPushButton:hover { background:#3a3a5a; color:#aaccee; }")
        self._btn_apply_clip.clicked.connect(lambda: self._on_inspect(overwrite=True))
        r_bake.addWidget(self._btn_apply_clip)
        from PyQt6.QtWidgets import QCheckBox as _QCB
        self._chk_auto_rename = _QCB(_t("Auto-rename / 自動改名"))
        self._chk_auto_rename.setToolTip(_t("Rename file to match person ID when baking / 書込時に人物IDに合わせてファイル名を変更"))
        self._chk_auto_rename.setChecked(
            attrs_mod.load_filename_config(getattr(self.handler.app, "current_project", None)).get("auto_rename", False))
        def _on_ar_toggle(v):
            proj = getattr(self.handler.app, "current_project", None)
            fn_cfg = attrs_mod.load_filename_config(proj)
            fn_cfg["auto_rename"] = v
            attrs_mod.save_filename_config(fn_cfg, proj)
            sv = getattr(self.handler.app, "_settings_win", None)
            if sv:
                if hasattr(sv, "chk_rename_on_scan") and sv.chk_rename_on_scan.isChecked() != v:
                    sv.chk_rename_on_scan.blockSignals(True)
                    sv.chk_rename_on_scan.setChecked(v)
                    sv.chk_rename_on_scan.blockSignals(False)
                if hasattr(sv, "check_auto_rename") and sv.check_auto_rename.isChecked() != v:
                    sv.check_auto_rename.blockSignals(True)
                    sv.check_auto_rename.setChecked(v)
                    sv.check_auto_rename.blockSignals(False)
        self._chk_auto_rename.toggled.connect(_on_ar_toggle)
        r_bake.addWidget(self._chk_auto_rename)
        self._protected_check = QPushButton(_t("🔓 Editable / 🔓 編集可"))
        self._protected_check.setCheckable(True)
        self._protected_check.setToolTip(_t("🔓 Editable — app may auto-rename\n🔒 Locked — app will not auto-rename / 🔓 編集可 — 自動改名される可能性あり\n🔒 ロック — 自動改名されません"))
        self._protected_check.setStyleSheet(
            "QPushButton { background: transparent; border: none; font-size: 18px; color: #66cc88; padding: 0 4px; }")
        self._protected_check.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._protected_check.toggled.connect(lambda _: self._save_attrs())
        self._protected_check.toggled.connect(self._apply_protected_lock)
        r_bake.addWidget(self._protected_check)
        self._bake_err_label = QLabel("")
        self._bake_err_label.setStyleSheet("color:#ff6666; font-size:10px;")
        self._bake_err_label.setWordWrap(True)
        r_bake.addWidget(self._bake_err_label, stretch=1)
        vbox.addLayout(r_bake)

        # ── Raw Data section (collapsed by default) ───────────────────────
        self._raw_meta_sec = _AttrSection(_t("Raw Data / 生データ"))
        vbox.addWidget(self._raw_meta_sec)
        # Place the soft canvas toolbar inline in the section header
        _toolbar = getattr(self._soft_canvas, "_toolbar_widget", None)
        if _toolbar:
            _toolbar.setParent(self._raw_meta_sec)
            _toolbar.setVisible(True)
            self._raw_meta_sec._hdr_lay.addWidget(_toolbar, stretch=1)
        self._btn_save_layout = QPushButton(_t("💾 Layout / 💾 レイアウト"))
        self._btn_save_layout.setToolTip(_t("Save current canvas tile positions / 現在のキャンバスタイル位置を保存"))
        self._btn_save_layout.setStyleSheet(
            "QPushButton { background:#2a3a2a; color:#88cc88; border:1px solid #446644; padding:2px 6px; }"
            "QPushButton:hover { background:#3a4a3a; color:#aaeebb; }")
        self._btn_save_layout.clicked.connect(self._save_canvas_layout)
        self._raw_meta_sec._hdr_lay.addWidget(self._btn_save_layout)
        _raw_lay = QVBoxLayout(self._raw_meta_sec.content)
        _raw_lay.setContentsMargins(6, 4, 6, 6)
        _raw_lay.setSpacing(4)
        self._raw_meta_edit = QTextEdit()
        self._raw_meta_edit.setReadOnly(True)
        self._raw_meta_edit.setFixedHeight(160)
        self._raw_meta_edit.setStyleSheet(
            "background-color: #1a1a1a; color: #ccc; border: 1px solid #333; "
            "font-family: monospace; font-size: 8pt;")
        self._raw_meta_edit.setPlaceholderText(_t("No data. / データなし。"))
        _raw_lay.addWidget(self._raw_meta_edit)

        # ── CLIP + Face side by side (CLIP 2/3, Face 1/3) ────────────────
        _inspect_row = QHBoxLayout()
        _inspect_row.setSpacing(4)

        _clip_col = QVBoxLayout()
        _clip_col.setSpacing(2)
        _clip_hdr = QHBoxLayout()
        _clip_hdr.addWidget(QLabel(_t("CLIP: / CLIPスコア：")))
        _clip_hdr.addStretch()
        _clip_col.addLayout(_clip_hdr)
        self._clip_inspect_edit = QTextEdit()
        self._clip_inspect_edit.setReadOnly(True)
        self._clip_inspect_edit.setStyleSheet(
            "background-color: #111122; color: #b0b0cc; border: 1px solid #334; "
            "font-family: monospace; font-size: 8pt;")
        self._clip_inspect_edit.setPlaceholderText(_t("CLIP scores will appear here. / CLIPスコアがここに表示されます。"))
        _clip_col.addWidget(self._clip_inspect_edit)
        _inspect_row.addLayout(_clip_col, stretch=2)

        _face_col = QVBoxLayout()
        _face_col.setSpacing(2)
        _face_hdr = QHBoxLayout()
        _face_hdr.addWidget(QLabel(_t("Face: / 顔：")))
        self._btn_apply_face = QPushButton(_t("Apply / 適用"))
        self._btn_apply_face.setEnabled(False)
        self._btn_apply_face.setFixedHeight(18)
        self._btn_apply_face.setStyleSheet(
            "background:#2a4a2a; color:#88cc88; border:1px solid #466; padding:0 6px; font-size:8pt;")
        self._btn_apply_face.setToolTip(_t("Apply detected person ID to this file / 検出された人物IDをこのファイルに適用"))
        self._btn_apply_face.clicked.connect(self._apply_detected_face)
        _face_hdr.addWidget(self._btn_apply_face)
        _face_hdr.addStretch()
        _face_col.addLayout(_face_hdr)
        self._face_inspect_edit = QTextEdit()
        self._face_inspect_edit.setReadOnly(True)
        self._face_inspect_edit.setFixedHeight(260)
        self._face_inspect_edit.setStyleSheet(
            "background-color: #111122; color: #b0b0cc; border: 1px solid #334; "
            "font-family: monospace; font-size: 8pt;")
        self._face_inspect_edit.setPlaceholderText(_t("Face scores will appear here. / 顔スコアがここに表示されます。"))
        _face_col.addWidget(self._face_inspect_edit)
        _inspect_row.addLayout(_face_col, stretch=1)

        _raw_lay.addLayout(_inspect_row)

        # Re-read from disk whenever the Raw Data section is expanded
        def _on_raw_expand():
            _p = getattr(self, '_attr_path', None)
            if _p:
                self._raw_meta_edit.setPlainText(_t("Loading... / 読み込み中..."))
                try:
                    _txt = attrs_mod.read_raw_embedded_text(_p)
                except Exception:
                    _txt = ""
                self._raw_meta_edit.setPlainText(_txt or _t("(no embedded text) / （埋め込みテキストなし）"))
                self._on_inspect()
        self._raw_meta_sec.set_expand_callback(_on_raw_expand)

        # Apply show_raw_data config (hidden by default unless enabled in Settings > Canvas)
        _show_dev = self.handler.app.config.get("show_raw_data", False)
        self._raw_meta_sec.setVisible(_show_dev)
        self._protected_check.setVisible(_show_dev)

        self._attr_path = None
        self._canvas_loaded_path = None
        return panel

    def _attr_arrow(self, open_state):
        """Return the correct arrow for open/closed state based on splitter orientation."""
        horiz = (self._splitter.orientation() == Qt.Orientation.Horizontal)
        if horiz:
            return "►" if open_state else "◄"
        else:
            arrow = "▼" if open_state else "▲"
            info = getattr(self, "_file_info_text", "")
            return f"{arrow}  {info}" if info else arrow

    def _sync_toggle_strip(self):
        """Show the correct toggle strip and update btn_toggle_attrs reference."""
        horiz = (self._splitter.orientation() == Qt.Orientation.Horizontal)
        self._left_bar.setVisible(horiz)
        self._top_bar.setVisible(not horiz)
        is_open = self._attr_scroll.isVisible()
        info = getattr(self, "_file_info_text", "")
        if horiz:
            self.btn_toggle_attrs = self._btn_toggle_left
            self._btn_toggle_left.setText(self._attr_arrow(is_open))
            self._info_bar.setText(info)
            self._info_bar.setVisible(bool(info))
        else:
            self.btn_toggle_attrs = self._btn_toggle_top
            self._btn_toggle_top.setText(self._attr_arrow(is_open))
            self._info_bar.setVisible(False)

    def _is_horiz(self):
        return self._splitter.orientation() == Qt.Orientation.Horizontal

    def _saved_size(self):
        return self._attrs_split_size_h if self._is_horiz() else self._attrs_split_size_v

    def _store_size(self, value):
        import aisearch_config as _cfg
        if self._is_horiz():
            self._attrs_split_size_h = value
            self.handler.app.config["attrs_split_size_h"] = value
        else:
            self._attrs_split_size_v = value
            self._attrs_split_size   = value
            self.handler.app.config["attrs_split_size"] = value
        _cfg.save_config(self.handler.app.config,
                         getattr(self.handler.app, "current_project", None))

    def _on_splitter_moved(self):
        # User is dragging — remove height cap so they can pull the boundary down freely
        self.scroll_area.setMaximumHeight(16777215)
        sizes = self._splitter.sizes()
        bottom = sizes[1] if len(sizes) > 1 else 0
        if bottom > 36:
            self._store_size(bottom)
            self._attr_scroll.show()
            self.btn_toggle_attrs.setText(self._attr_arrow(True))
        else:
            self._attr_scroll.hide()
            self.btn_toggle_attrs.setText(self._attr_arrow(False))
        # Use the same debounce timer — splitter drag fires many events per second
        self._resize_timer.start()

    def _toggle_attrs(self):
        total = sum(self._splitter.sizes())
        if self._attr_scroll.isVisible():
            # Expanded → collapse: remove the height cap so image pane fills window
            self.scroll_area.setMaximumHeight(16777215)
            bottom = self._splitter.sizes()[1] if len(self._splitter.sizes()) > 1 else 0
            if bottom > 36:
                self._store_size(bottom)
            self._attr_scroll.hide()
            self._splitter.setSizes([total - 26, 26])
            self.btn_toggle_attrs.setText(self._attr_arrow(False))
        else:
            # Collapsed → expand
            saved = max(self._saved_size(), 200)
            self._attr_scroll.show()
            self._splitter.setSizes([total - saved, saved])
            self.btn_toggle_attrs.setText(self._attr_arrow(True))
            # Fit image pane after layout settles
            QTimer.singleShot(120, self.handler._auto_fit_splitter)
        QTimer.singleShot(0, self.handler._rerender)

    def _maybe_rebuild_attr_panel(self):
        """Rebuild the attr panel if attrs_tags.json has changed since last build."""
        if not self._attr_panel_built:
            return
        tags_file = attrs_mod.tags_file_for_project(
            getattr(self.handler.app, 'current_project', None))
        try:
            mtime = os.path.getmtime(tags_file)
        except OSError:
            mtime = 0
        if mtime == self._tags_file_mtime:
            return
        self._tags_file_mtime = mtime
        # Reload TAG_GROUPS so combos pick up new values
        attrs_mod.TAG_GROUPS = attrs_mod._load_tag_groups(tags_file)
        # Rebuild and swap the panel; delete the old one to free memory
        old_panel = self._attr_scroll.takeWidget()
        self.attr_widget = self._build_attr_panel()
        self._attr_scroll.setWidget(self.attr_widget)
        if old_panel is not None:
            old_panel.deleteLater()

    def _deferred_build_attr_panel(self):
        self.attr_widget = self._build_attr_panel()
        self._attr_scroll.setWidget(self.attr_widget)
        self._attr_panel_built = True
        if self._attr_panel_pending_path:
            path = self._attr_panel_pending_path
            self._attr_panel_pending_path = None
            self._refresh_attrs(path)

    def _update_title_with_info(self, path):
        """Set window title to path + file info (dimensions, size, audio, etc.)."""
        app = self.handler.app
        rel_dir = app._mask_path(path)
        name = os.path.basename(path)
        base = f"{rel_dir}/{name}" if rel_dir and rel_dir != "." else name
        info = getattr(self, "_file_info_text", "")
        self.setWindowTitle(f"{base}    {info}" if info else base)

    def _refresh_attrs(self, path):
        if not self._attr_panel_built:
            self._attr_panel_pending_path = path
            return
        # Flush any pending auto-save when switching to a different file
        if self._text_save_timer.isActive() and path != self._attr_path:
            self._text_save_timer.stop()
            self._save_attrs()
        elif self._text_save_timer.isActive():
            self._text_save_timer.stop()
        # Rebuild attr panel if attrs_tags.json changed (new attributes added via Settings)
        self._maybe_rebuild_attr_panel()
        # Block editingFinished on note/seed so setEnabled(False) in _apply_protected_lock
        # doesn't fire _save_attrs with stale widget values for the new image's path.
        # Use try/finally to guarantee these are always unblocked even if something throws.
        self._project_edit.blockSignals(True)
        self._seed_edit.blockSignals(True)
        try:
            self._refresh_attrs_inner(path)
        finally:
            self._project_edit.blockSignals(False)
            self._seed_edit.blockSignals(False)
        # Auto-run CLIP + face inspect based on clip_inspect_mode setting
        _mode = self.handler.app.config.get("clip_inspect_mode", "never")
        if _mode == "always":
            self._on_inspect()

    _IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".tif", ".avif"}
    _VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".m4v", ".avi", ".webm", ".wmv", ".flv", ".ts"}

    @pyqtSlot(str)
    def _refresh_attrs_from_thread(self, path: str):
        """Slot so background threads can trigger attr refresh on the main thread.
        Calls _refresh_attrs_inner directly to avoid re-triggering _on_inspect."""
        if not self._attr_panel_built:
            return
        # _refresh_attrs_from_thread is always same-file (called after inspect/detect),
        # so never flush-save here — that would overwrite newly detected values.
        if self._text_save_timer.isActive():
            self._text_save_timer.stop()
        self._maybe_rebuild_attr_panel()
        self._project_edit.blockSignals(True)
        self._seed_edit.blockSignals(True)
        try:
            self._refresh_attrs_inner(path)
        finally:
            self._project_edit.blockSignals(False)
            self._seed_edit.blockSignals(False)

    @pyqtSlot(str)
    def _set_detect_status(self, msg: str):
        """Show/clear a temporary detection status in the info bar."""
        self._info_bar.setText(msg)
        self._info_bar.setVisible(bool(msg))

    @pyqtSlot()
    def _refresh_clip_canvas(self):
        """Called after background CLIP detection completes — re-loads canvas with updated entry."""
        path = getattr(self, "_attr_path", None)
        if not path:
            return
        _sc = getattr(self, "_soft_canvas", None)
        if _sc is None:
            return
        app = self.handler.app
        entry = attrs_mod.get(app.attrs_data, path)
        entry["_project"] = getattr(app, "current_project", None)
        # Re-inject live O/R/K detections (same as _refresh_attrs_inner) so combos stay populated
        try:
            _det = attrs_mod.detect_file_attrs(path)
            if _det:
                _ork_opts = {}
                for _w in _sc.widgets:
                    if getattr(_w, "key", None) in ("O", "R", "K"):
                        _ork_opts[_w.key] = {k for k, _ in (_w.options or [])}
                _cur_tags = set(entry.get("tags", []))
                _extra = [_fv for _fk, _fv in [("O", _det.get("o")), ("R", _det.get("r")), ("K", _det.get("k"))]
                          if _fv and _ork_opts.get(_fk) and not (_cur_tags & _ork_opts[_fk])]
                if _extra:
                    entry = dict(entry)
                    entry["tags"] = list(_cur_tags) + _extra
        except Exception:
            pass
        _sc.load_file(path, entry)
        # Resize CLIP/FACE/per-field tiles to fit their loaded text content
        _clip_keys = {"CLIP", "FACE", "CLIP_HC", "CLIP_FA", "CLIP_SK", "CLIP_PM", "CLIP_E", "CLIP_CS", "CLIP_BG", "CLIP_X"}
        for _cfw in _sc.widgets:
            if _cfw.key in _clip_keys and getattr(_cfw, "_te", None):
                QTimer.singleShot(50, lambda _w=_cfw: self._fit_clip_face_tile(_w))
        # Restore normal info bar (file info text in horiz mode, else hidden)
        info = getattr(self, "_file_info_text", "")
        if self._is_horiz():
            self._info_bar.setText(info)
            self._info_bar.setVisible(bool(info))
        else:
            self._info_bar.setVisible(False)

    def _setup_clip_face_autoheight(self):
        """Make CLIP and FACE canvas tiles resize to fit their full text content."""
        sc = getattr(self, "_soft_canvas", None)
        if not sc:
            return
        _clip_keys = {"CLIP", "FACE", "CLIP_HC", "CLIP_FA", "CLIP_SK", "CLIP_PM", "CLIP_E", "CLIP_CS", "CLIP_BG", "CLIP_X"}
        for w in getattr(sc, "widgets", []):
            if w.key in _clip_keys:
                te = getattr(w, "_te", None)
                if te:
                    te.setMaximumHeight(16777215)
                # Collapse empty tiles to title-only height on first load
                QTimer.singleShot(100, lambda _w=w: self._fit_clip_face_tile(_w))

    def _apply_file_visibility(self, path):
        """Show/hide soft canvas sections based on __hidden_for__ rules and file type."""
        ext = os.path.splitext(path)[1].lower() if path else ""
        if ext in self._IMAGE_EXTS:
            mode = "image"
        elif ext in self._VIDEO_EXTS:
            mode = "video"
        else:
            mode = "all"
        # Tell the embedded canvas to apply visibility
        cw = getattr(self, "_soft_canvas", None)
        if cw:
            cw._apply_mode(mode)
        # Sync Canvas tab mode if Settings dialog is open
        try:
            sv = getattr(self.handler.app, "_settings_win", None)
            if sv and sv.isVisible():
                tab_cw = getattr(sv, "_canvas_widget", None)
                if tab_cw:
                    tab_cw._mode_cb.setCurrentText(mode.title() if mode != "all" else "All")
        except Exception:
            pass

    def _refresh_attrs_inner(self, path):
        app = self.handler.app
        # Auto-bake previous file when navigating to a different one
        if self._attr_path and self._attr_path != path:
            if getattr(self, '_chk_auto_bake', None) and self._chk_auto_bake.isChecked():
                self._bake_to_file(silent=True)
        _same_file = (self._attr_path == path)
        self._attr_path = path
        # Only reset bake state when navigating to a new file — preserve state on same-file refresh
        if not _same_file:
            _entry_pre = attrs_mod.get(self.handler.app.attrs_data, path) if path else {}
            _has_bakeable = any(_entry_pre.get(k) for k in ("prompt", "neg_prompt", "seed", "speech", "person_id"))
            self._update_bake_btn("pending" if _has_bakeable else "idle")
        self._btn_detect_person.setText(_t("Detect & Register / 検出＆登録"))
        self._btn_detect_person.setStyleSheet(
            "background:#445566; color:#e0e0e0; border:1px solid #667788;"
            " padding:3px 10px; font-weight:bold;")
        if not path:
            self.attr_widget.setEnabled(False)
            self._attr_scroll.setEnabled(False)
            self._project_edit.blockSignals(False)
            self._seed_edit.blockSignals(False)
            return
        self.attr_widget.setEnabled(True)
        self._attr_scroll.setEnabled(True)
        # Clean up any AItan{} blocks accidentally written into text fields
        _ep_clean = app.attrs_data.get(path, {})
        _ep_dirty = False
        for _ep_k in ("prompt", "neg_prompt", "speech", "note"):
            if str(_ep_clean.get(_ep_k, "")).startswith("AItan{"):
                _ep_clean[_ep_k] = ""
                _ep_dirty = True
        if _ep_dirty:
            attrs_mod.save(app.current_project, app.attrs_data)

        # Sync embedded file metadata into attrs_data before anything else reads entry.
        # Sync embedded file metadata — skip if already scanned this session (avoids Image.open on every view).
        _emb_scanned = getattr(app, '_emb_meta_scanned', None)
        if _emb_scanned is None:
            app._emb_meta_scanned = set()
            _emb_scanned = app._emb_meta_scanned
        if path not in _emb_scanned:
            _emb_scanned.add(path)
            _emb_pre = _read_embedded_meta(path)
            if _emb_pre:
                if path not in app.attrs_data:
                    app.attrs_data[path] = {}
                _ep_stored = app.attrs_data[path]
                _ep_changed = False
                for _ep_key in ("prompt", "neg_prompt", "speech"):
                    _ep_val = _emb_pre.get(_ep_key, "")
                    if _ep_val and not _ep_stored.get(_ep_key):
                        _ep_stored[_ep_key] = _ep_val
                        _ep_changed = True
                _ep_seed = str(_emb_pre["seed"]) if _emb_pre.get("seed") else ""
                if _ep_seed and not _ep_stored.get("seed"):
                    _ep_stored["seed"] = _ep_seed
                    _ep_changed = True
                if _ep_changed:
                    attrs_mod.save(app.current_project, app.attrs_data)

        # Apply path-scoped rules before reading entry so widgets fill with correct values
        _app = self.handler.app
        _path_rules = _app.get_path_rules_cached()
        if _path_rules:
            _app.attrs_data, _ = attrs_mod.apply_path_rules(
                _app.attrs_data, path, _app.current_project, _path_rules=_path_rules)

        entry = attrs_mod.get(self.handler.app.attrs_data, path)
        tags  = set(entry.get("tags", []))

        self._quality_combo.blockSignals(True)
        self._protected_check.blockSignals(True)

        qual = next((k for k in attrs_mod.QUALITY_TAGS if k in tags), "")
        self._quality_combo.setCurrentIndex(max(0, self._quality_combo.findData(qual)))
        self._protected_check.setChecked(not bool(entry.get("editable", True)))

        self._quality_combo.blockSignals(False)
        self._protected_check.blockSignals(False)

        # Lock/unlock all editable widgets based on editable state
        _locked = not bool(entry.get("editable", True))
        self._apply_protected_lock(_locked)







        # Person fields — prefer saved person_id if set (non-000), then filename
        stem    = os.path.splitext(os.path.basename(path))[0]
        parsed  = attrs_mod.parse_coded_filename(stem)
        persons = parsed.get("persons", []) if parsed else []
        saved_pid = entry.get("person_id", "")
        if saved_pid and saved_pid != "000":
            persons = [saved_pid] + (persons[1:] if len(persons) > 1 else [])
        elif not persons:
            persons = [saved_pid] if saved_pid else []
        for i, pe in enumerate(self._p_edits):
            pe.blockSignals(True)
            pe.setText(persons[i] if i < len(persons) else "")
            pe.blockSignals(False)
        pid = persons[0] if persons else ""
        if pid:
            name_label = attrs_mod.get_person_id_label(app.current_project, pid)
            self._person_name_edit.setText(name_label if name_label != pid else "")
        else:
            self._person_name_edit.setText("")

        # PW fields
        pws = parsed.get("persons_with", []) if parsed else []
        for i, pwe in enumerate(self._pw_edits):
            pwe.setText(pws[i] if i < len(pws) else "")

        # Populate coded fields — prefer attrs_data value (manual input) over filename
        _fts = getattr(self, "_field_to_section", {})
        for letter, _, digits in attrs_mod.CODED_FIELDS:
            _sec = _fts.get(letter.lower())
            if _sec and not _sec.is_expanded():
                continue   # section is collapsed — will refresh when opened
            fe = self._code_edits.get(letter.lower())
            if fe is None:
                continue
            # manual value → cf_ auto-detected → parsed filename
            _db_val = attrs_mod.get_coded_field(entry, letter)
            val = _db_val if _db_val else (parsed.get(letter.lower(), "") if parsed else "")
            if digits == 0:
                fe.blockSignals(True)
                fe.setChecked(bool(val))
                fe.blockSignals(False)
            else:
                fe.setText(val)
                # Sync combo boxes (if this field uses them)
                self._set_field_combos(letter.lower(), val)
        # Auto-fill O/R/K from file metadata when not set in filename
        _auto_detect_keys = {"o", "r", "k"}
        _need_detect = False
        if parsed:
            _need_detect = any(not parsed.get(k) for k in _auto_detect_keys)
        else:
            _need_detect = True
        if _need_detect:
            _detected = attrs_mod.detect_file_attrs(path)
            for _dk, _dv in _detected.items():
                if _dk not in _auto_detect_keys:
                    continue
                _sec = _fts.get(_dk)
                if _sec and not _sec.is_expanded():
                    continue
                if parsed and parsed.get(_dk):
                    continue  # filename already has a value — keep it
                fe = self._code_edits.get(_dk)
                if fe is None:
                    continue
                fe.setText(_dv)
                self._set_field_combos(_dk, _dv)

        # J field: decode base-36 → date string; fall back to file date if not in filename
        fe_j = self._code_edits.get("j")
        if fe_j:
            j_val = parsed.get("j", "") if parsed else ""
            if not j_val:
                j_val = attrs_mod.julian_id_for_file(path)
            decoded = attrs_mod.julian_id_to_date(j_val)
            fe_j.setText(decoded)
            fe_j.setToolTip(f"Julian ID: {j_val}")

        # Update decode label
        _dec = getattr(self, "_decode_field_fn", None)
        if _dec:
            _decode_keys = [("E","Eyes"), ("HC","Hair"), ("FA","Face"),
                            ("SK","Skin"), ("B","Bust"), ("WH","W/H"),
                            ("PM","Post/Mot"), ("CS","Camera")]
            parts = []
            for letter, name in _decode_keys:
                fe = self._code_edits.get(letter.lower())
                val = fe.text() if fe and hasattr(fe, "text") else ""
                if val:
                    decoded = _dec(letter, val)
                    if decoded:
                        parts.append(f"{name}: {decoded}")
            self._decode_lbl.setText("  |  ".join(parts))
        rules = attrs_mod.load_filename_rules(getattr(self.handler.app, "current_project", None))
        base  = self._filename_base(stem, rules)
        self._name_edit.setText(base)
        self._name_edit.setCursorPosition(len(base))
        self._project_edit.setText(entry.get("note", ""))
        self._project_edit.blockSignals(False)
        self._seed_edit.setText(entry.get("seed", ""))
        self._seed_edit.blockSignals(False)
        _saved_pid = entry.get("person_id", "")
        if not _saved_pid:
            _stem_fb = os.path.splitext(os.path.basename(path))[0]
            _parsed_fb = attrs_mod.parse_coded_filename(_stem_fb)
            if _parsed_fb and _parsed_fb.get("persons"):
                _saved_pid = _parsed_fb["persons"][0]
        self._person_id_combo.blockSignals(True)
        if _saved_pid:
            _idx = self._person_id_combo.findData(_saved_pid)
            if _idx >= 0:
                self._person_id_combo.setCurrentIndex(_idx)
            else:
                self._person_id_combo.setCurrentText(_saved_pid)
        else:
            self._person_id_combo.setCurrentIndex(0)
        self._person_id_combo.blockSignals(False)
        # Load soft fields (taglist / matrix / text) into the canvas
        _sc = getattr(self, "_soft_canvas", None)
        if _sc:
            # Pass raw metadata so canvas conditions can evaluate (e.g. hide Speech when no audio)
            _raw_meta = {}
            try:
                _raw_meta = attrs_mod.extract_metadata(path) if path else {}
            except Exception:
                pass
            # Merge filename-rule tags (e.g. -watermark → watermark boolean) into
            # a display copy of the entry so the canvas reflects the filename in real time
            _fn_rules = attrs_mod.load_filename_rules(
                getattr(self.handler.app, "current_project", None))
            if _fn_rules and path:
                _fn_tags = attrs_mod.detect_tags_from_filename(path, _fn_rules)
                if _fn_tags:
                    entry = dict(entry)
                    _merged = list(entry.get("tags", []))
                    for _tag in _fn_tags:
                        if _tag not in _merged:
                            _merged.append(_tag)
                    entry["tags"] = _merged
            # Auto-detect O/R/K from file dimensions/fps; inject into display entry
            # if the entry has no tag matching any of those fields' options.
            try:
                _detected_ork = attrs_mod.detect_file_attrs(path)
                if _detected_ork:
                    _ork_option_keys = {}  # field_key → set of valid option tag-keys
                    for _w in _sc.widgets:
                        if getattr(_w, "key", None) in ("O", "R", "K"):
                            _ork_option_keys[_w.key] = {k for k, _ in (_w.options or [])}
                    _cur_tags = set(entry.get("tags", []))
                    _extra_ork = []
                    for _fk, _fv in [("O", _detected_ork.get("o")),
                                      ("R", _detected_ork.get("r")),
                                      ("K", _detected_ork.get("k"))]:
                        if not _fv:
                            continue
                        _opts = _ork_option_keys.get(_fk, set())
                        if _opts and not (_cur_tags & _opts):  # no existing tag for this field
                            _extra_ork.append(_fv)
                    if _extra_ork:
                        entry = dict(entry)
                        entry["tags"] = list(_cur_tags) + _extra_ork
            except Exception:
                pass
            entry["_project"] = getattr(app, "current_project", None)
            _sc.load_file(path, entry, raw_meta=_raw_meta)
            # Mark that the canvas now reflects THIS path. _save_attrs uses this
            # to refuse writes when the widgets haven't been reloaded yet.
            self._canvas_loaded_path = path

            # If key CLIP fields are absent, run detection in background and refresh canvas
            _clip_fields = {"hc", "fa", "sk", "e", "b", "wh", "pm", "cs", "bg"}
            if not any(entry.get(f) for f in _clip_fields):
                import threading as _thr
                QMetaObject.invokeMethod(
                    self, "_set_detect_status",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, _t("Detecting CLIP & face… / CLIPと顔を検出中…")))
                def _run_clip(_path=path, _entry=dict(entry)):
                    try:
                        import aisearch_logic as _lg
                        _data = getattr(app, "data", None)
                        _emb = None
                        if _data and "paths" in _data and _path in _data["paths"]:
                            _idx = _data["paths"].index(_path)
                            _emb = _data["embeddings"][_idx]
                        if _emb is None:
                            _emb = _lg.extract_feature(_path)
                        if _emb is None:
                            QMetaObject.invokeMethod(
                                self, "_set_detect_status",
                                Qt.ConnectionType.QueuedConnection,
                                Q_ARG(str, ""))
                            return
                        _changed = False
                        _updates = attrs_mod.auto_detect_clip_attrs(
                            _emb, _entry, allowed_fields=_clip_fields,
                            project=getattr(app, "current_project", None))
                        if _updates:
                            # Check live entry — user may have manually set a field
                            # while this thread was running; don't overwrite their input
                            _live = app.attrs_data.setdefault(_path, {})
                            for _k, _v in _updates.items():
                                if not _live.get(_k):
                                    _live[_k] = _v
                                    _changed = True
                        # Generate per-field CLIP text for canvas tiles
                        _CF_KEYS = ("HC", "FA", "SK", "PM", "E", "CS", "BG", "X")
                        try:
                            _specs = attrs_mod.inspect_clip_scores(_emb)
                            _live2 = app.attrs_data.setdefault(_path, {})
                            _field_txt = {}
                            for _sp in _specs:
                                _fk = _sp["field"].upper()
                                if _fk not in _CF_KEYS:
                                    continue
                                _winner = _sp.get("winner")
                                _lmap = {c: l for c, l, _ in _sp["options"]}
                                _wlbl = _lmap.get(_winner, "—") if _winner else "below threshold"
                                _flines = _field_txt.setdefault(_fk, [])
                                _flines.append(f"pos={_sp['pos']}  thr={_sp['threshold']:.2f}")
                                _flines.append(f"  -> {_winner or '—'}  {_wlbl}")
                                for _c, _l, _s in _sp["options"][:6]:
                                    _flines.append(f"  {'*' if _c == _winner else ' '} {_c}: {_s:.4f}  {_l[:52]}")
                                _flines.append("")
                            for _fk in _CF_KEYS:
                                if _fk in _field_txt:
                                    _live2[f"CLIP_{_fk}"] = "\n".join(_field_txt[_fk])
                                    _changed = True
                        except Exception:
                            pass
                        # Face detection — set person_id if not yet assigned
                        _stored = (app.attrs_data.get(_path) or {}).get("person_id", "")
                        if not _stored:
                            _pid = attrs_mod.detect_or_assign_person_id(_path, app.current_project)
                            if _pid is None:
                                _pid = "000"
                            app.attrs_data.setdefault(_path, {})["person_id"] = _pid
                            _changed = True
                        if _changed:
                            attrs_mod.save(app.current_project, app.attrs_data)
                        # Update per-field canvas tiles
                        for _fk in _CF_KEYS:
                            if _fk in _field_txt:
                                QMetaObject.invokeMethod(
                                    self, "_update_canvas_text_widget",
                                    Qt.ConnectionType.QueuedConnection,
                                    Q_ARG(str, f"CLIP_{_fk}"),
                                    Q_ARG(str, "\n".join(_field_txt[_fk])))
                        QMetaObject.invokeMethod(
                            self, "_refresh_clip_canvas",
                            Qt.ConnectionType.QueuedConnection)
                    except Exception:
                        QMetaObject.invokeMethod(
                            self, "_set_detect_status",
                            Qt.ConnectionType.QueuedConnection,
                            Q_ARG(str, ""))
                _thr.Thread(target=_run_clip, daemon=True).start()

        # Hide the hardcoded Note row when the canvas has a "note" text panel
        _canvas_has_note = _sc is not None and any(
            getattr(w, "key", None) in ("note", "positive_prompt")
            for w in getattr(_sc, "widgets", [])
        )
        self._note_row_widget.setVisible(not _canvas_has_note)

        # Apply __hidden_for__ visibility rules based on file type
        self._apply_file_visibility(path)



        # Info — append to window title bar
        meta = entry.get("meta", {})
        if meta:
            _key_order = ["Dimensions", "Ratio", "File size", "Duration", "FPS", "Audio"]
            parts = [meta[k] for k in _key_order if k in meta]
            self._file_info_text = "  ·  ".join(parts)
        else:
            self._file_info_text = ""
        self._update_title_with_info(path)

        # ── Raw Info box: actual embedded text from file ──────────────────────
        self._raw_meta_edit.setPlainText("Loading...")
        try:
            _embedded = attrs_mod.read_raw_embedded_text(path)
        except Exception:
            _embedded = ""
        self._raw_meta_edit.setPlainText(_embedded if _embedded else "(no embedded text)")

        # Fill seed/prompt fields from meta — always update from file
        if meta:
            if meta.get("Seed"):
                self._seed_edit.blockSignals(True)
                self._seed_edit.setText(str(meta["Seed"]))
                self._seed_edit.blockSignals(False)
        # Fill prompt/neg_prompt into canvas — always from meta
        _sc_meta = getattr(self, "_soft_canvas", None)
        if _sc_meta and meta:
            _entry_meta = attrs_mod.get(self.handler.app.attrs_data, path)
            _meta_updated = False
            for meta_key, db_key in (("Prompt", "prompt"), ("NegPrompt", "neg_prompt")):
                if meta.get(meta_key) and _entry_meta.get(db_key) != meta[meta_key]:
                    _entry_meta[db_key] = meta[meta_key]
                    _meta_updated = True
            if _meta_updated:
                _entry_meta["_project"] = getattr(app, "current_project", None)
                _sc_meta.load_file(path, _entry_meta)
        if not meta:
            self._file_info_text = ""
            self._sync_toggle_strip()

        # Sync person_id: try one-way detection rules first, then coded filename parse
        if not entry.get("person_id"):
            stem_sync = os.path.splitext(os.path.basename(path))[0]
            fn_rules  = attrs_mod.load_filename_rules(getattr(self.handler.app, "current_project", None))
            ow_rules  = [r for r in fn_rules if r.get("field") and r.get("one_way")]
            pid       = ""
            if ow_rules:
                od = attrs_mod.parse_filename_rules(stem_sync, ow_rules)
                pid = od.get("P", "")
            if not pid:
                parsed = attrs_mod.parse_coded_filename(stem_sync)
                if parsed and parsed.get("persons"):
                    pid = parsed["persons"][0]
            if pid:
                app.attrs_data = attrs_mod.get(app.attrs_data, path) and app.attrs_data
                attrs_mod.set_file(app.attrs_data, path,
                                   tags=list(tags),
                                   note=entry.get("note", ""),
                                   confirmed=entry.get("confirmed", False),
                                   project=entry.get("project", ""),
                                   scene=entry.get("scene", ""),
                                   prompt=entry.get("prompt", ""),
                                   neg_prompt=entry.get("neg_prompt", ""),
                                   seed=entry.get("seed", ""),
                                   meta=entry.get("meta"),
                                   custom=entry.get("custom", ""),
                                   person_id=pid,
                                   speech=entry.get("speech", ""),
                                   editable=entry.get("editable", True)
                                   )
                attrs_mod.save(app.current_project, app.attrs_data)

        # Auto-detect tags (resolution, audio, AI source) in background if incomplete.
        # NOTE: MediaPipe (shot/pose) is intentionally skipped here — use the Scan buttons.
        needs_res    = not any(t in attrs_mod.RESOLUTION_TAGS for t in tags)
        needs_src    = not any(t in attrs_mod.SOURCE_TAGS for t in tags)
        needs_meta   = True   # always re-read meta from file
        needs_person = False  # face matching is manual-only (use Detect & Register button)
        needs_fn_rules = True  # always apply filename rules — last matching rule wins
        if needs_res or needs_src or needs_meta or needs_person or needs_fn_rules:
            def _detect(p=path, _needs_res=needs_res, _needs_src=needs_src,
                        _needs_meta=needs_meta, _needs_person=needs_person,
                        _needs_fn=needs_fn_rules):
                _entry = attrs_mod.get(app.attrs_data, p)
                _tags  = list(_entry.get("tags", []))
                _changed = False
                if _needs_res:
                    tag = attrs_mod.detect_resolution_tag(p)
                    if tag:
                        _tags = [t for t in _tags if t not in attrs_mod.RESOLUTION_TAGS] + [tag]
                        _changed = True
                if _needs_src:
                    src, new_prompt, new_seed = attrs_mod.detect_ai_source(p)
                    if src and not any(t in attrs_mod.SOURCE_TAGS for t in _tags):
                        _tags = [t for t in _tags if t not in attrs_mod.SOURCE_TAGS] + [src]
                        _changed = True
                    if new_prompt and _entry.get("prompt") != new_prompt:
                        _entry["prompt"] = new_prompt; _changed = True
                    if new_seed and _entry.get("seed") != new_seed:
                        _entry["seed"] = new_seed; _changed = True
                if _needs_meta:
                    meta = attrs_mod.extract_metadata(p)
                    if meta:
                        _entry["meta"] = meta; _changed = True
                # Filename rule → person_id (one-way rules, e.g. image-*.png → Nastia)
                if _needs_fn:
                    try:
                        _fn_rules = attrs_mod.load_filename_rules(app.current_project)
                        _ow = [r for r in _fn_rules
                               if r.get("field") and (r.get("one_way") or r.get("extract"))]
                        if _ow:
                            _bn = os.path.basename(p)
                            _st = os.path.splitext(_bn)[0]
                            _od = attrs_mod.parse_filename_rules(_st, _ow, basename=_bn, fullpath=p)
                            if _od.get("P"):
                                _entry["person_id"] = _od["P"]
                                _changed = True
                    except Exception:
                        pass
                # Face match — only when faces DB has entries (never auto-assigns new IDs)
                _matched_pid = None
                if _needs_person:
                    _matched_pid = attrs_mod.match_person_id(p, app.current_project)
                    if _matched_pid:
                        _entry["person_id"] = _matched_pid
                        _changed = True
                # Mark file editable on first auto-detection (auto-adoption)
                if not _entry.get("editable", False):
                    _entry["editable"] = True
                    _changed = True
                if _changed:
                    # Merge into the LIVE entry so concurrent user edits (prompt,
                    # neg_prompt, note, seed, etc.) are not clobbered by this
                    # stale background snapshot.
                    _live = app.attrs_data.get(p)
                    if _live is None:
                        _live = {}
                        app.attrs_data[p] = _live
                    _live["tags"] = _tags
                    for _k in ("meta", "person_id", "editable", "prompt", "seed"):
                        if _k in _entry:
                            # Never overwrite user-typed text with our stale copy
                            if _k in ("prompt", "seed") and _live.get(_k):
                                continue
                            _live[_k] = _entry[_k]
                    attrs_mod.save(app.current_project, app.attrs_data)
                def _safe_refresh(p=p, pid=_matched_pid):
                    if self._attr_path != p:
                        return
                    _app = self.handler.app
                    self._refresh_attrs(p)
                    # Update person combo if a match was found
                    if pid and hasattr(self, '_person_id_combo'):
                        _idx = self._person_id_combo.findData(pid)
                        if _idx >= 0:
                            self._person_id_combo.blockSignals(True)
                            self._person_id_combo.setCurrentIndex(_idx)
                            self._person_id_combo.blockSignals(False)
                QTimer.singleShot(0, _safe_refresh)
            threading.Thread(target=_detect, daemon=True).start()

    def _filename_base(self, stem, rules):
        """Return just the clean base (all known suffixes stripped) for the Name
        field.  The user can keep it or retype a meaningful name; Fix will append
        the canonical suffixes from the current tags."""
        base = attrs_mod._extract_filename_base(stem, rules)
        return base

    def _refresh_person_id_combo(self, force=False):
        """Populate the person ID combo — text immediately, thumbnails async."""
        app = self.handler.app
        db = attrs_mod.load_faces_db(app.current_project)
        faces = db.get("faces", {})
        registry = attrs_mod.load_person_registry(app.current_project)

        # Build a lightweight signature; skip full rebuild if nothing changed
        _sig = (tuple(sorted(faces.keys())), tuple(sorted(registry.items())))
        if not force and getattr(self, "_person_combo_sig", None) == _sig:
            return
        self._person_combo_sig = _sig

        # Build pid → source_path lookup
        _pid_path = {}
        for _fkey, _fdata in faces.items():
            sp = _fdata.get("source_path", "")
            if sp and os.path.exists(sp) and _fkey not in _pid_path:
                _pid_path[_fkey] = sp

        THUMB = 28
        # ── Fast path: populate with text labels only (no icons) ──────────
        self._person_id_combo.blockSignals(True)
        current = self._person_id_combo.currentData()
        self._person_id_combo.clear()
        self._person_id_combo.addItem("", "")   # blank = auto-assign
        _ordered_fids = []
        for fid in sorted(faces.keys()):
            fid = _norm_pid(fid)
            name = registry.get(fid, "")
            item_text = f"P{fid}  {name}" if name else f"P{fid}"
            self._person_id_combo.addItem(item_text, fid)
            _ordered_fids.append(fid)
        idx = self._person_id_combo.findData(current)
        self._person_id_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._person_id_combo.blockSignals(False)

        # ── Slow path: load thumbnails in background, apply on main thread ─
        def _load_thumbs_bg(pid_path=dict(_pid_path), fids=list(_ordered_fids)):
            results = {}   # fid → ('px', QPixmap) or ('raw', bytes, w, h, cache_key)
            for fid in fids:
                src = pid_path.get(fid, "")
                if not src:
                    continue
                try:
                    mtime = os.path.getmtime(src)
                    cache_key = (src, mtime)
                    if cache_key in _THUMB_CACHE:
                        results[fid] = ('cached', cache_key)
                    else:
                        # PIL decode in background — thread-safe
                        img = Image.open(src)
                        img.draft('RGB', (THUMB * 2, THUMB * 2))
                        img.thumbnail((THUMB, THUMB), Image.BOX)
                        img = img.convert("RGB")
                        w, h = img.size
                        results[fid] = ('raw', img.tobytes("raw", "RGB"), w, h, cache_key)
                except Exception:
                    pass
            # Apply icons on main thread
            QTimer.singleShot(0, lambda r=results: _apply_thumbs(r))

        def _apply_thumbs(results):
            combo = self._person_id_combo
            combo.setIconSize(QSize(THUMB, THUMB))
            for i in range(combo.count()):
                fid = combo.itemData(i)
                if not fid or fid not in results:
                    continue
                r = results[fid]
                if r[0] == 'cached':
                    px = _THUMB_CACHE.get(r[1])
                else:
                    _, raw, w, h, ck = r
                    qimg = QImage(raw, w, h, w * 3, QImage.Format.Format_RGB888)
                    px = QPixmap.fromImage(qimg)
                    if not px.isNull():
                        _THUMB_CACHE[ck] = px
                if px and not px.isNull():
                    combo.setItemIcon(i, QIcon(px))

        import threading as _threading
        _threading.Thread(target=_load_thumbs_bg, daemon=True).start()

    # ── Person combo ─────────────────────────────────────────────────────────

    def _on_person_combo_changed(self, idx):
        """When user selects a person from the dropdown, fill the P001 and name fields."""
        if idx <= 0:
            return
        fid = self._person_id_combo.itemData(idx)
        if not fid:
            return
        # Push the ID into the P001 text field so Bake and _save_attrs pick it up
        if self._p_edits:
            self._p_edits[0].blockSignals(True)
            self._p_edits[0].setText(fid)
            self._p_edits[0].blockSignals(False)
        registry = attrs_mod.load_person_registry(self.handler.app.current_project)
        name = registry.get(fid, "")
        self._person_name_edit.setText(name)
        # Save immediately so person_id persists across restarts
        self._save_attrs()
        self._update_bake_btn("pending")

    def _on_match_person(self):
        """Match face against existing faces DB — never assigns a new ID."""
        path = self._attr_path
        if not path:
            return
        app = self.handler.app
        self._btn_match_person.setEnabled(False)
        self._btn_match_person.setText("…")

        def _run():
            pid = attrs_mod.match_person_id(path, app.current_project)
            def _apply():
                self._btn_match_person.setEnabled(True)
                self._btn_match_person.setText("Match")
                if pid:
                    idx = self._person_id_combo.findData(pid)
                    if idx >= 0:
                        self._person_id_combo.setCurrentIndex(idx)
                    else:
                        self._person_id_combo.setCurrentText(pid)
                    entry = attrs_mod.get(app.attrs_data, path)
                    entry["person_id"] = pid
                    app.attrs_data[path] = entry
                    attrs_mod.save(app.current_project, app.attrs_data)
                else:
                    self._btn_match_person.setText("No match")
                    QTimer.singleShot(2000, lambda: self._btn_match_person.setText("Match"))
            QTimer.singleShot(0, _apply)

        import threading
        threading.Thread(target=_run, daemon=True).start()

    def _on_detect_person(self):
        path = self._attr_path
        if not path:
            return
        app = self.handler.app
        # Read the specified ID from combo (strip any name suffix the user didn't type)
        raw = self._person_id_combo.currentText().strip().split()[0] if self._person_id_combo.currentText().strip() else ""
        forced_id = raw if raw else None

        self._btn_detect_person.setEnabled(False)
        self._btn_detect_person.setText("…")

        def _run():
            if forced_id:
                # Register face under the specified ID
                entry = attrs_mod.get(app.attrs_data, path)
                wrong = entry.get("person_id", "")
                attrs_mod.correct_person_id(path, app.current_project, forced_id,
                                            wrong_id=wrong if wrong != forced_id else None)
                pid = forced_id
            else:
                # Auto-detect and assign
                pid = attrs_mod.detect_or_assign_person_id(path, app.current_project)

            def _apply():
                self._btn_detect_person.setEnabled(True)
                if pid:
                    self._btn_detect_person.setText(_t("Registered / 登録済"))
                    self._btn_detect_person.setStyleSheet(
                        "background:#2a6a2a; color:#aaffaa; border:1px solid #44aa44;"
                        " padding:3px 10px; font-weight:bold;")
                else:
                    self._btn_detect_person.setText(_t("Detect & Register / 検出＆登録"))
                if pid:
                    self._person_id_combo.setCurrentText(pid)
                    entry = attrs_mod.get(app.attrs_data, path)
                    attrs_mod.set_file(app.attrs_data, path,
                                       tags=entry.get("tags", []),
                                       note=entry.get("note", ""),
                                       confirmed=entry.get("confirmed", False),
                                       project=entry.get("project", ""),
                                       scene=entry.get("scene", ""),
                                       prompt=entry.get("prompt", ""),
                                       seed=entry.get("seed", ""),
                                       meta=entry.get("meta"),
                                       custom=entry.get("custom", ""),
                                       person_id=pid,
                                       editable=entry.get("editable", True))
                    attrs_mod.save(app.current_project, app.attrs_data)
                    self._refresh_person_id_combo(force=True)
                    # Show name if registered
                    name = attrs_mod.get_person_id_label(app.current_project, pid)
                    self._person_name_edit.setText(name if name != pid else "")
                else:
                    self._person_id_combo.setCurrentText("—")
            QTimer.singleShot(0, _apply)
        threading.Thread(target=_run, daemon=True).start()

    def _set_field_combos(self, letter_lower: str, hex_val: str):
        """Set combo boxes for a coded field from its hex string value."""
        combos = getattr(self, "_code_combos", {}).get(letter_lower, [])
        for _sub_grp, pos, cb in combos:
            digit = hex_val[-pos] if hex_val and pos <= len(hex_val) else ""
            idx = cb.findData(digit) if digit else 0
            cb.blockSignals(True)
            cb.setCurrentIndex(max(0, idx))
            cb.blockSignals(False)

    def _on_x_code_changed(self, text):
        """Update the X hint label with expression name and description.
        EXPRESSION_TABLE stores (en, jp) tuples; EXPRESSION_CATEGORIES stores
        'English 日本語' space-joined. Pick the half matching the current UI language."""
        from attr_viewer import _UI_LANG
        _is_ja = _UI_LANG.get("val") == "ja"

        def _split_cat(cat):
            # "Neutral 無表情" → "Neutral" (en) / "無表情" (ja). Split at first CJK char.
            for i, c in enumerate(cat):
                if ('぀' <= c <= 'ゟ') or ('゠' <= c <= 'ヿ') or ('一' <= c <= '鿿'):
                    return (cat[:i].strip(), cat[i:].strip())
            return (cat, cat)

        code = text.strip().lower()
        if len(code) == 2:
            en, jp = attrs_mod.expression_label(code)
            if en:
                cat_full = attrs_mod.expression_category(code)
                cat_en, cat_ja = _split_cat(cat_full)
                _name = jp if _is_ja else en
                _cat  = cat_ja if _is_ja else cat_en
                self._x_hint.setText(f"{_name}  —  {_cat}")
            else:
                cat_full = attrs_mod.expression_category(code)
                cat_en, cat_ja = _split_cat(cat_full)
                self._x_hint.setText(cat_ja if _is_ja else cat_en)
        elif len(code) == 1:
            try:
                cat_full = attrs_mod.EXPRESSION_CATEGORIES.get(int(code, 16), "")
                cat_en, cat_ja = _split_cat(cat_full)
                self._x_hint.setText(cat_ja if _is_ja else cat_en)
            except Exception:
                self._x_hint.setText("")
        else:
            self._x_hint.setText("")

    def _on_person_id_override(self):
        """User manually typed a corrected hex ID.
        Re-registers the face under the correct ID and removes it from the
        wrong one so future detections learn from the correction."""
        path = self._attr_path
        if not path:
            return
        new_pid = _norm_pid(self._person_id_label.text().strip())
        if not new_pid:
            return
        app     = self.handler.app
        entry   = attrs_mod.get(app.attrs_data, path)
        old_pid = entry.get("person_id", "")
        wrong   = old_pid if old_pid and old_pid != new_pid else None

        def _fix():
            attrs_mod.correct_person_id(path, app.current_project, new_pid, wrong_id=wrong)
        threading.Thread(target=_fix, daemon=True).start()

        attrs_mod.set_file(app.attrs_data, path,
                           tags=entry.get("tags", []),
                           note=entry.get("note", ""),
                           confirmed=entry.get("confirmed", False),
                           project=entry.get("project", ""),
                           scene=entry.get("scene", ""),
                           prompt=entry.get("prompt", ""),
                           seed=entry.get("seed", ""),
                           meta=entry.get("meta"),
                           custom=entry.get("custom", ""),
                           person_id=new_pid,
                           editable=entry.get("editable", True))
        attrs_mod.save(app.current_project, app.attrs_data)
        name_label = attrs_mod.get_person_id_label(app.current_project, new_pid)
        self._person_name_edit.setText(name_label if name_label != new_pid else "")

    def _on_inspect(self, overwrite=False):
        """Run CLIP + face detection and write raw scores into the inspect text box.
        overwrite=True: clear all CLIP fields and re-detect from scratch (Refresh mode).
        overwrite=False: only fill empty fields, never touch manual input."""
        # Guard: skip if a previous inspect is still running (rapid navigation)
        if getattr(self, '_inspect_running', False):
            return
        path = self._attr_path
        if not path or not os.path.exists(path):
            return
        clip_out = getattr(self, "_clip_inspect_edit", None)
        face_out = getattr(self, "_face_inspect_edit", None)
        if clip_out is None and face_out is None:
            return
        app = self.handler.app
        if clip_out: clip_out.setPlainText("Computing…")
        if face_out: face_out.setPlainText("Computing…")
        self._inspect_running = True

        import threading
        def _run():
            from PyQt6.QtCore import QMetaObject, Qt, Q_ARG

            # ── CLIP ────────────────────────────────────────────────────────
            clip_txt = []
            clip_field_txt = {}   # field.upper() → list of lines
            _CLIP_CANVAS_FIELDS = ("HC", "FA", "SK", "PM", "E", "CS", "BG", "X")
            _clip_specs = []
            try:
                import aisearch_logic as _lg
                emb = None
                data = getattr(app, "data", None)
                if data and "paths" in data and path in data["paths"]:
                    idx = data["paths"].index(path)
                    emb = data["embeddings"][idx]
                if emb is None:
                    emb = _lg.extract_feature(path)
                if emb is not None:
                    _clip_specs = attrs_mod.inspect_clip_scores(emb)
                    for sp in _clip_specs:
                        winner = sp["winner"]
                        label_map = {code: lbl for code, lbl, _ in sp["options"]}
                        win_label = label_map.get(winner, "—") if winner else "below threshold"
                        clip_txt.append(f"{sp['field']} pos={sp['pos']}  thr={sp['threshold']:.2f}")
                        clip_txt.append(f"  -> {winner or '—'}  {win_label}")
                        for code, lbl, score in sp["options"][:6]:
                            mark = "*" if code == winner else " "
                            clip_txt.append(f"  {mark} {code}: {score:.4f}  {lbl[:52]}")
                        clip_txt.append("")
                        # Per-field accumulation
                        _fk = sp["field"].upper()
                        if _fk in _CLIP_CANVAS_FIELDS:
                            _flines = clip_field_txt.setdefault(_fk, [])
                            _flines.append(f"pos={sp['pos']}  thr={sp['threshold']:.2f}")
                            _flines.append(f"  -> {winner or '—'}  {win_label}")
                            for code, lbl, score in sp["options"][:6]:
                                mark = "*" if code == winner else " "
                                _flines.append(f"  {mark} {code}: {score:.4f}  {lbl[:52]}")
                            _flines.append("")
                else:
                    clip_txt.append("(could not extract embedding)")
            except Exception as e:
                clip_txt.append(f"ERROR: {e}")
            if clip_out:
                QMetaObject.invokeMethod(clip_out, "setPlainText",
                                         Qt.ConnectionType.QueuedConnection,
                                         Q_ARG(str, "\n".join(clip_txt)))
            # Apply CLIP results to attrs_data
            if _clip_specs and emb is not None:
                # Build multi-digit field values by combining all positions
                # e.g. HC pos1=5, pos2=none, pos3=none → "hc": "005" not "5"
                _field_digits = {cf[0].lower(): cf[2]
                                 for cf in attrs_mod.CODED_FIELDS if cf[2] > 0}
                _working = {}
                # Track which (field, digit_index) had an actual winner (incl. "0")
                _detected_indices = {}
                _entry_pre = attrs_mod.get(app.attrs_data, path)
                for sp in _clip_specs:
                    _f = sp["field"].lower()
                    _pos = sp.get("pos", 1)
                    _winner = sp.get("winner")
                    if _f not in _working:
                        _digits = _field_digits.get(_f, 1)
                        _cur = (_entry_pre.get(_f) or "").zfill(_digits)
                        _working[_f] = list(_cur)
                    if _winner:
                        _digits = len(_working[_f])
                        _idx = _digits - _pos  # pos=1 = rightmost
                        if 0 <= _idx < _digits:
                            _working[_f][_idx] = _winner
                            _detected_indices.setdefault(_f, set()).add(_idx)
                # Apply correction-based overrides (baked examples take priority)
                _corrections = attrs_mod.load_corrections(getattr(app, "current_project", None))
                if _corrections and emb is not None:
                    for sp in _clip_specs:
                        _f = sp["field"].lower()
                        _pos = sp.get("pos", 1)
                        if _f not in _working:
                            continue
                        _corr = attrs_mod.detect_from_corrections(emb, _corrections, _f, _pos)
                        if _corr is not None:
                            _digits = len(_working[_f])
                            _idx = _digits - _pos
                            if 0 <= _idx < _digits:
                                _working[_f][_idx] = _corr
                                _detected_indices.setdefault(_f, set()).add(_idx)
                # Include fields with non-zero digits OR real "0" detections (FA/SK/BG)
                _updates = {}
                for _f, _v in _working.items():
                    _vs = "".join(_v)
                    if any(c != "0" for c in _vs) or _f in _detected_indices:
                        _updates[_f] = _vs
                if _updates:
                    _entry = attrs_mod.get(app.attrs_data, path)
                    if overwrite:
                        # Refresh mode: overwrite all CLIP fields
                        _entry.update(_updates)
                    else:
                        # Normal mode: merge — keep existing non-zero digits, fill zeros
                        # Allow "0" winners through for zero_is_none=False fields (FA, SK, BG)
                        for _k, _v in _updates.items():
                            _digits = _field_digits.get(_k, 1)
                            _existing = (_entry.get(_k) or "").zfill(_digits)
                            _merged = list(_existing)
                            _det_idxs = _detected_indices.get(_k, set())
                            for _i, _c in enumerate(_v.zfill(_digits)):
                                # Fill empty position if new value is non-zero OR was detected
                                if _merged[_i] == "0" and (_c != "0" or _i in _det_idxs):
                                    _merged[_i] = _c
                            _result = "".join(_merged)
                            # Store if any non-zero digit, OR real detections were made
                            if any(c != "0" for c in _result) or _k in _detected_indices:
                                _entry[_k] = _result
                    # Store combined CLIP text and per-field texts for canvas tiles
                    _entry = attrs_mod.get(app.attrs_data, path)
                    _entry["CLIP"] = "\n".join(clip_txt)
                    for _cf in _CLIP_CANVAS_FIELDS:
                        if _cf in clip_field_txt:
                            _entry[f"CLIP_{_cf}"] = "\n".join(clip_field_txt[_cf])
                    attrs_mod.save(app.current_project, app.attrs_data)
                    QMetaObject.invokeMethod(self, "_refresh_attrs_from_thread",
                                             Qt.ConnectionType.QueuedConnection,
                                             Q_ARG(str, path))
                    # Update per-field canvas tiles
                    for _cf in _CLIP_CANVAS_FIELDS:
                        if _cf in clip_field_txt:
                            QMetaObject.invokeMethod(self, "_update_canvas_text_widget",
                                                     Qt.ConnectionType.QueuedConnection,
                                                     Q_ARG(str, f"CLIP_{_cf}"),
                                                     Q_ARG(str, "\n".join(clip_field_txt[_cf])))
            elif clip_txt:
                # No numeric updates but still have text — store in CLIP canvas tile
                _entry = attrs_mod.get(app.attrs_data, path)
                _entry["CLIP"] = "\n".join(clip_txt)
                for _cf in _CLIP_CANVAS_FIELDS:
                    if _cf in clip_field_txt:
                        _entry[f"CLIP_{_cf}"] = "\n".join(clip_field_txt[_cf])
                attrs_mod.save(app.current_project, app.attrs_data)
                QMetaObject.invokeMethod(self, "_refresh_attrs_from_thread",
                                         Qt.ConnectionType.QueuedConnection,
                                         Q_ARG(str, path))
                for _cf in _CLIP_CANVAS_FIELDS:
                    if _cf in clip_field_txt:
                        QMetaObject.invokeMethod(self, "_update_canvas_text_widget",
                                                 Qt.ConnectionType.QueuedConnection,
                                                 Q_ARG(str, f"CLIP_{_cf}"),
                                                 Q_ARG(str, "\n".join(clip_field_txt[_cf])))

            # ── Face ────────────────────────────────────────────────────────
            face_txt = []
            _detected_pid = None
            try:
                _stored_pid = (app.attrs_data.get(path) or {}).get("person_id", "")
                fi = attrs_mod.inspect_face_detection(path, app.current_project)
                if fi.get("error"):
                    face_txt.append(f"ERROR: {fi['error']}")
                else:
                    face_txt.append(_t(f"Faces found: {fi['num_faces']} / 検出顔数: {fi['num_faces']}"))
                    face_txt.append(f"Stored: {'P' + _stored_pid if _stored_pid else '—'}")
                    if fi["face_found"]:
                        registry = attrs_mod.load_person_registry(app.current_project)
                        if fi["matches"]:
                            face_txt.append("Top matches:")
                            for pid, sim in fi["matches"]:
                                name = registry.get(pid, "")
                                mark = "*" if pid == fi["assigned_id"] else " "
                                face_txt.append(f"  {mark} P{pid}  {sim:.3f}  {name}")
                        else:
                            face_txt.append("No persons in DB")
                        _detected_pid = fi["assigned_id"]
                        _det_str = ('P' + _detected_pid) if _detected_pid else 'no match (thr 0.35)'
                        _match = " ==" if _detected_pid == _stored_pid else " !="
                        face_txt.append(f"\n-> {_det_str}{_match} stored")
                    else:
                        face_txt.append("No face detected")
                        _detected_pid = "000"
            except Exception as e:
                face_txt.append(f"ERROR: {e}")
            # Store FACE text for the canvas FACE tile
            if face_txt:
                _entry = attrs_mod.get(app.attrs_data, path)
                _entry["FACE"] = "\n".join(face_txt)
                attrs_mod.save(app.current_project, app.attrs_data)
                QMetaObject.invokeMethod(self, "_update_canvas_text_widget",
                                         Qt.ConnectionType.QueuedConnection,
                                         Q_ARG(str, "FACE"),
                                         Q_ARG(str, "\n".join(face_txt)))
            if face_out:
                QMetaObject.invokeMethod(face_out, "setPlainText",
                                         Qt.ConnectionType.QueuedConnection,
                                         Q_ARG(str, "\n".join(face_txt)))
            # Auto-apply face result to person_id when a real person was detected
            _stored_pid_now = (app.attrs_data.get(path) or {}).get("person_id", "")
            if _detected_pid and _detected_pid != "000" and _detected_pid != _stored_pid_now:
                QMetaObject.invokeMethod(self, "_auto_apply_face",
                                         Qt.ConnectionType.QueuedConnection,
                                         Q_ARG(str, path),
                                         Q_ARG(str, _detected_pid))
            # Keep Apply button in Raw Data section in sync
            _apply_btn = getattr(self, "_btn_apply_face", None)
            if _apply_btn and _detected_pid is not None and _detected_pid != _stored_pid_now:
                QMetaObject.invokeMethod(_apply_btn, "setEnabled",
                                         Qt.ConnectionType.QueuedConnection,
                                         Q_ARG(bool, True))

        def _run_guarded():
            try:
                _run()
            finally:
                self._inspect_running = False

        threading.Thread(target=_run_guarded, daemon=True).start()

    def _apply_detected_face(self):
        """Apply the face-detection result to the person field and save."""
        path = self._attr_path
        if not path:
            return
        app = self.handler.app
        try:
            fi = attrs_mod.inspect_face_detection(path, app.current_project)
            pid = fi.get("assigned_id")
            if not pid:
                return
            entry = attrs_mod.get(app.attrs_data, path)
            old_pid = entry.get("person_id", "")
            attrs_mod.set_file(app.attrs_data, path,
                               tags=entry.get("tags", []),
                               note=entry.get("note", ""),
                               confirmed=entry.get("confirmed", False),
                               project=entry.get("project", ""),
                               scene=entry.get("scene", ""),
                               prompt=entry.get("prompt", ""),
                               neg_prompt=entry.get("neg_prompt", ""),
                               seed=entry.get("seed", ""),
                               meta=entry.get("meta"),
                               person_id=pid,
                               editable=entry.get("editable", True))
            attrs_mod.save(app.current_project, app.attrs_data)
            # Correct face DB if old ID was wrong
            if old_pid and old_pid != pid:
                import threading as _thr
                _thr.Thread(target=lambda: attrs_mod.correct_person_id(
                    path, app.current_project, pid, wrong_id=old_pid), daemon=True).start()
            # Update person field in UI
            if self._p_edits:
                self._p_edits[0].blockSignals(True)
                self._p_edits[0].setText(pid)
                self._p_edits[0].blockSignals(False)
            registry = attrs_mod.load_person_registry(app.current_project)
            self._person_name_edit.setText(registry.get(pid, ""))
            _btn = getattr(self, "_btn_apply_face", None)
            if _btn:
                _btn.setEnabled(False)
            self._update_bake_btn("pending")
        except Exception:
            pass

    def _save_canvas_layout(self):
        """Save every canvas tile's current position and size to the DB."""
        sc = getattr(self, "_soft_canvas", None)
        if not sc:
            return
        from attr_viewer import save_position as _sp, save_size as _ss
        for w in sc.widgets:
            _sp(sc.conn, w.key, w.x(), w.y())
            _ss(sc.conn, w.key, w.width(), w.height())
        btn = getattr(self, "_btn_save_layout", None)
        if btn:
            btn.setText("✓ Saved")
            QTimer.singleShot(1500, lambda: btn.setText("💾 Layout"))

    def _fit_clip_face_tile(self, w):
        """Resize a CLIP or FACE FieldWidget to fit its full text content.
        Only shifts tiles that are in the same column (same x zone) and directly below."""
        te = getattr(w, "_te", None)
        if not te:
            return
        sc = getattr(self, "_soft_canvas", None)
        if not sc:
            return
        tile_w = max(150, w.width() - 20)
        te.document().setTextWidth(tile_w)
        doc_h = int(te.document().size().height()) + te.frameWidth() * 2
        title_h = w.fontMetrics().height() + 24
        new_h = max(title_h, doc_h + title_h)
        old_h = w.height()
        if new_h == old_h:
            return
        w.resize(w.width(), new_h)
        clip_old_bottom = w.y() + old_h
        clip_new_bottom = w.y() + new_h
        # Only push tiles that are:
        #   (a) in the same column — left edge within CLIP's horizontal span
        #   (b) were positioned below the old CLIP bottom (would be overlapped)
        col_right = w.x() + w.width() + 20   # CLIP column right boundary + slack
        for other in sc.widgets:
            if other is w:
                continue
            if other.x() >= col_right:
                continue  # different column (attribute combos on the right) — don't touch
            if other.y() < clip_old_bottom:
                continue  # above old CLIP bottom — don't touch
            new_y = max(other.y(), clip_new_bottom + 6)
            if new_y != other.y():
                other.move(other.x(), new_y)
                sc._apply_connections_for(other.key)
                # Intentionally not saving here — user saves layout explicitly via 💾 Layout
        canvas = getattr(sc, "canvas", None)
        if canvas:
            bottom = max((cw.y() + cw.height() for cw in sc.widgets if cw.isVisible()), default=0)
            canvas.setMinimumHeight(max(1000, bottom + 40))

    @pyqtSlot(str, str)
    def _update_canvas_text_widget(self, key: str, text: str):
        """Set text on a canvas FieldWidget and resize the tile to show full content."""
        sc = getattr(self, "_soft_canvas", None)
        if not sc:
            return
        for w in getattr(sc, "widgets", []):
            if w.key == key:
                te = getattr(w, "_te", None)
                if te:
                    te.blockSignals(True)
                    te.setPlainText(text)
                    te.blockSignals(False)
                    # Resize tile and shift tiles below — deferred so widget is laid out first
                    QTimer.singleShot(50, lambda _w=w: self._fit_clip_face_tile(_w))

    @pyqtSlot(str, str)
    def _auto_apply_face(self, path: str, pid: str):
        """Slot called from _on_inspect thread to auto-apply detected person_id to P field."""
        if not path or not pid or pid == "000":
            return
        app = self.handler.app
        if self._attr_path != path:
            return  # user navigated away
        entry = attrs_mod.get(app.attrs_data, path)
        old_pid = entry.get("person_id", "")
        if old_pid == pid:
            return
        entry["person_id"] = pid
        attrs_mod.save(app.current_project, app.attrs_data)
        if old_pid and old_pid != pid:
            import threading as _thr
            _thr.Thread(target=lambda: attrs_mod.correct_person_id(
                path, app.current_project, pid, wrong_id=old_pid), daemon=True).start()
        self._refresh_attrs_inner(path)

    def _on_canvas_action(self, key: str, action: str):
        """Dispatch action button clicks from canvas FieldWidgets."""
        if key == "P" and action == "detect_face":
            self._detect_face_for_canvas()

    def _detect_face_for_canvas(self):
        """Run face detection in background and update the P box + person_id."""
        path = getattr(self, "_attr_path", None)
        if not path:
            return
        app = self.handler.app
        # Disable button while running
        _sc = getattr(self, "_soft_canvas", None)
        _p_widget = next((w for w in getattr(_sc, "widgets", []) if w.key == "P"), None)
        _det_btn = getattr(_p_widget, "_detect_btn", None) if _p_widget else None
        if _det_btn:
            _det_btn.setEnabled(False)
            _det_btn.setText("…")
        QMetaObject.invokeMethod(
            self, "_set_detect_status",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(str, _t("Detecting face… / 顔を検出中…")))

        def _run(_path=path):
            try:
                pid = attrs_mod.detect_or_assign_person_id(_path, app.current_project)
                if pid is None:
                    pid = "000"
                entry = attrs_mod.get(app.attrs_data, _path)
                old_pid = entry.get("person_id", "")
                app.attrs_data.setdefault(_path, {})["person_id"] = pid
                attrs_mod.save(app.current_project, app.attrs_data)
                if old_pid and old_pid != pid and old_pid != "000":
                    attrs_mod.correct_person_id(_path, app.current_project, pid, wrong_id=old_pid)
            except Exception:
                pass
            QMetaObject.invokeMethod(
                self, "_finish_detect_face_canvas",
                Qt.ConnectionType.QueuedConnection)

        import threading as _thr
        _thr.Thread(target=_run, daemon=True).start()

    @pyqtSlot()
    def _finish_detect_face_canvas(self):
        """Re-enable Detect button and refresh the P box after detection completes."""
        QMetaObject.invokeMethod(
            self, "_set_detect_status",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(str, ""))
        _sc = getattr(self, "_soft_canvas", None)
        _p_widget = next((w for w in getattr(_sc, "widgets", []) if w.key == "P"), None)
        _det_btn = getattr(_p_widget, "_detect_btn", None) if _p_widget else None
        if _det_btn:
            _det_btn.setEnabled(True)
            _det_btn.setText(_t("Detect / 検出"))
        # Reload P box value
        path = getattr(self, "_attr_path", None)
        if path and _p_widget:
            app = self.handler.app
            entry = attrs_mod.get(app.attrs_data, path)
            entry["_project"] = getattr(app, "current_project", None)
            entry["path"] = path
            _p_widget.load_soft(set(entry.get("tags", [])), entry)
        # Sync old-style person edit boxes if present
        if path:
            app = self.handler.app
            entry = attrs_mod.get(app.attrs_data, path)
            pid = entry.get("person_id", "")
            if self._p_edits:
                self._p_edits[0].blockSignals(True)
                self._p_edits[0].setText(pid)
                self._p_edits[0].blockSignals(False)
            registry = attrs_mod.load_person_registry(app.current_project)
            self._person_name_edit.setText(registry.get(pid, ""))

    def _on_pw_changed(self):
        """User manually edited a PW field — trigger filename normalize if auto-rename is on."""
        if not self._attr_path:
            return
        app = self.handler.app
        if attrs_mod.load_filename_config(getattr(app, "current_project", None)).get("auto_rename", False):
            self._on_normalize_filename()

    def _on_person_name_changed(self):
        pid = self._person_id_label.text()
        if not pid or pid == "—":
            return
        name = self._person_name_edit.text().strip()
        attrs_mod.set_person_name(self.handler.app.current_project, pid, name)

    def _wire_canvas_bool_flags(self):
        """Connect soft canvas coded-boolean buttons to _on_bool_flag_toggled.
        Handles both single-toggle (boolean) and True/False radio pairs.
        Called after canvas creation and after each reload."""
        _sc = getattr(self, '_soft_canvas', None)
        if not _sc:
            return
        # Map option_key (lowercase label) → uppercase letter for digits=0 fields
        _bool_opt_map = {lbl.lower(): letter
                         for letter, lbl, digits in attrs_mod.CODED_FIELDS if digits == 0}
        for w in _sc.widgets:
            if w.style not in ("radio", "boolean"):
                continue
            _btns = getattr(w, '_btns', {})
            # Find which coded boolean field this widget controls
            field_letter = None
            positive_key = None
            for opt_key in _btns:
                letter = _bool_opt_map.get(opt_key.lower())
                if letter:
                    field_letter = letter
                    positive_key = opt_key
                    break
            if not field_letter:
                continue
            lk = field_letter.lower()
            fe = self._code_edits.get(lk)
            if fe is None:
                continue
            for opt_key, btn in _btns.items():
                is_on = (opt_key == positive_key)   # True button → add flag; False btn → remove
                def _make_handler(_fe=fe, _letter=field_letter, _is_on=is_on):
                    def _handler(checked):
                        if not checked:
                            return   # act only when a button becomes active
                        _fe.blockSignals(True)
                        _fe.setChecked(_is_on)
                        _fe.blockSignals(False)
                        self._on_bool_flag_toggled(_letter)
                    return _handler
                btn.toggled.connect(_make_handler())

    def _on_bool_flag_toggled(self, letter):
        """Toggle a boolean coded flag (e.g. WM, ED) in the filename."""
        path = self._attr_path
        if not path:
            return
        if not attrs_mod.is_editable(self.handler.app.attrs_data, path):
            return
        stem, ext = os.path.splitext(os.path.basename(path))
        parts = attrs_mod.parse_coded_filename(stem)
        if parts is None:
            # Not yet a coded file — trigger full normalize which builds coded filename from scratch
            self._on_normalize_filename()
            return
        lk = letter.lower()
        fe = self._code_edits.get(lk)
        if fe is None:
            return
        parts[lk] = letter if fe.isChecked() else ""
        _date_first = bool(parts.get("j")) and not parts.get("persons")
        _fo = attrs_mod.get_sync_field_order(getattr(self.handler.app, "current_project", None))
        new_stem = attrs_mod.build_coded_filename(parts, date_first=_date_first, field_order=_fo)
        if not new_stem or new_stem == stem:
            return
        _, _ext = os.path.splitext(path)
        new_path = attrs_mod.unique_path(os.path.join(os.path.dirname(path), new_stem + _ext))
        if new_path == path:
            return
        try:
            os.rename(path, new_path)
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self.handler.window, _t("Rename Error / 改名エラー"), str(e))
            return
        app = self.handler.app
        attrs_mod.update_path_in_all_stores(path, new_path, app.current_project)
        if app.data and "paths" in app.data and path in app.data["paths"]:
            idx = app.data["paths"].index(path)
            app.data["paths"][idx] = new_path
            import torch as _torch
            _torch.save(app.data, os.path.join(attrs_mod.DATA_DIR,
                                                f"features_{app.current_project}.pt"))
        if path in app.attrs_data:
            app.attrs_data[new_path] = app.attrs_data.pop(path)
        row = app._current_row()
        if row >= 0:
            app.table.item(row, 2).setText(os.path.basename(new_path))
            app.table.set_row_path(row, new_path)
        self._attr_path = new_path
        if self._canvas_loaded_path is not None: self._canvas_loaded_path = new_path
        self.handler.current_path = new_path
        self.handler.window._update_title_with_info(new_path)

    def _on_normalize_filename(self):
        """Rebuild filename from coded fields: P001P002B0a1O02I001.ext"""
        path = self._attr_path
        if not path:
            return
        if not attrs_mod.is_editable(self.handler.app.attrs_data, path):
            return
        app     = self.handler.app
        persons = [pe.text().strip().lower() for pe in self._p_edits if pe.text().strip()]
        if not persons:
            return
        persons_with = [pwe.text().strip().lower() for pwe in self._pw_edits if pwe.text().strip()]
        parts = {"persons": persons, "persons_with": persons_with}
        # Preserve J from existing coded filename; fall back to file creation time
        _stem_now = os.path.splitext(os.path.basename(path))[0]
        _parsed_now = attrs_mod.parse_coded_filename(_stem_now)
        parts["j"] = (_parsed_now.get("j", "") if _parsed_now else "") or \
                     attrs_mod.julian_id_for_file(path)
        for letter, _, digits in attrs_mod.CODED_FIELDS:
            if letter == "J":
                continue   # J already set above — skip display-decoded text
            fe = self._code_edits.get(letter.lower())
            if fe is None:
                continue
            if digits == 0:
                parts[letter.lower()] = "1" if fe.isChecked() else ""
            else:
                parts[letter.lower()] = fe.text().strip().lower()
        _fo2 = attrs_mod.get_sync_field_order(getattr(app, "current_project", None))
        new_stem = attrs_mod.build_coded_filename(parts, field_order=_fo2)
        if not new_stem:
            return
        _, ext   = os.path.splitext(path)
        new_path = attrs_mod.unique_path(
            os.path.join(os.path.dirname(path), new_stem + ext))
        if new_path == path:
            return
        try:
            os.rename(path, new_path)
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self.handler.window, _t("Rename Error / 改名エラー"), str(e))
            return
        # Update all stores
        attrs_mod.update_path_in_all_stores(path, new_path, app.current_project)
        if app.data and "paths" in app.data and path in app.data["paths"]:
            idx = app.data["paths"].index(path)
            app.data["paths"][idx] = new_path
            torch.save(app.data, os.path.join(attrs_mod.DATA_DIR, f"features_{app.current_project}.pt"))
        if path in app.attrs_data:
            app.attrs_data[new_path] = app.attrs_data.pop(path)
        row = app._current_row()
        if row >= 0:
            app.table.item(row, 2).setText(os.path.basename(new_path))
            app.table.set_row_path(row, new_path)
        self._attr_path = new_path
        if self._canvas_loaded_path is not None: self._canvas_loaded_path = new_path
        self.handler.current_path = new_path
        self.handler.window._update_title_with_info(new_path)
        self._name_edit.setText(new_base)
        self._name_edit.setCursorPosition(len(new_base))

    def _on_name_edit_finished(self):
        path = self._attr_path
        if not path:
            return
        app = self.handler.app
        if not attrs_mod.is_editable(app.attrs_data, path):
            return
        new_stem = self._name_edit.text().strip()
        if not new_stem:
            # Restore original if user blanked it
            self._name_edit.setText(os.path.splitext(os.path.basename(path))[0])
            return
        old_stem, ext = os.path.splitext(os.path.basename(path))
        if new_stem == old_stem:
            return
        new_path = os.path.join(os.path.dirname(path), new_stem + ext)
        try:
            os.rename(path, new_path)
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self, _t("Rename Error / 改名エラー"), str(e))
            self._name_edit.setText(old_stem)
            return
        app = self.handler.app
        attrs_mod.update_path_in_all_stores(path, new_path, app.current_project)
        # Update .pt database
        if app.data and "paths" in app.data and path in app.data["paths"]:
            idx = app.data["paths"].index(path)
            app.data["paths"][idx] = new_path
            torch.save(app.data, os.path.join(attrs_mod.DATA_DIR, f"features_{app.current_project}.pt"))
        # Migrate attrs entry
        if path in app.attrs_data:
            app.attrs_data[new_path] = app.attrs_data.pop(path)
        # Update table row
        row = app._current_row()
        if row >= 0:
            app.table.item(row, 2).setText(os.path.basename(new_path))
            app.table.set_row_path(row, new_path)
        # Update handler state
        self._attr_path = new_path
        if self._canvas_loaded_path is not None: self._canvas_loaded_path = new_path
        self.handler.current_path = new_path
        self.handler.window._update_title_with_info(new_path)

    def _on_pose_changed(self):
        path = self._attr_path
        if not path:
            return
        if not attrs_mod.is_editable(self.handler.app.attrs_data, path):
            return
        fe_fa = self._code_edits.get("fa")
        pose_tag = fe_fa.text().strip() if fe_fa else ""
        new_path = attrs_mod.apply_pose_to_filename(path, pose_tag)
        if new_path and new_path != path:
            app = self.handler.app
            attrs_mod.update_path_in_all_stores(path, new_path, app.current_project)
            # Update .pt database
            if app.data and "paths" in app.data and path in app.data["paths"]:
                idx = app.data["paths"].index(path)
                app.data["paths"][idx] = new_path
                torch.save(app.data, os.path.join(attrs_mod.DATA_DIR, f"features_{app.current_project}.pt"))
            # Migrate attrs entry
            if path in app.attrs_data:
                app.attrs_data[new_path] = app.attrs_data.pop(path)
            # Update table row
            row = app._current_row()
            if row >= 0:
                app.table.item(row, 2).setText(os.path.basename(new_path))
                app.table.set_row_path(row, new_path)
            # Update handler state
            self._attr_path = new_path
            if self._canvas_loaded_path is not None: self._canvas_loaded_path = new_path
            self.handler.current_path = new_path
            self.handler.window._update_title_with_info(new_path)
        self._save_attrs()

    def _apply_protected_lock(self, locked):
        """Disable all attribute editing widgets when file is protected."""
        # Collect all editable widgets in the attr panel (except the lock checkbox itself)
        editables = []
        _sc = getattr(self, "_soft_canvas", None)
        if _sc:
            editables.append(_sc)
        editables.append(self._quality_combo)
        editables.append(self._seed_edit)
        editables.append(self._project_edit)
        editables.append(self._name_edit)
        editables.append(self._person_id_combo)
        editables.append(self._person_name_edit)
        editables.append(self._btn_detect_person)

        for w in editables:
            w.setEnabled(not locked)
        for cb_list in getattr(self, '_code_combos', {}).values():
            for _, _, cb in cb_list:
                cb.setEnabled(not locked)





        if locked:
            self._protected_check.setText("🔒 Locked")  # Design = unlocked
            self._protected_check.setStyleSheet(
                "QPushButton { background: transparent; border: none; font-size: 18px; color: #ff6644; padding: 0 4px; }")
        else:
            self._protected_check.setText("🔓 Editable")
            self._protected_check.setStyleSheet(
                "QPushButton { background: transparent; border: none; font-size: 18px; color: #66cc88; padding: 0 4px; }")

    def _save_attrs(self):
        path = self._attr_path
        if not path:
            return
        # Safety: refuse to save when the canvas hasn't yet loaded data for
        # the current _attr_path. Otherwise a background timer or signal can
        # fire between 'self._attr_path = new_path' and 'canvas.load_file(new_path)',
        # and we'd write the OLD file's widget text into the NEW file's entry.
        _loaded = getattr(self, "_canvas_loaded_path", None)
        if _loaded is not None and _loaded != path:
            return
        self._update_bake_btn("pending")
        app = self.handler.app
        tags = []
        qual = self._quality_combo.currentData()
        if qual: tags.append(qual)
        entry = attrs_mod.get(app.attrs_data, path)
        # Collect soft-field data from canvas (taglist toggles + text areas)
        _sc = getattr(self, "_soft_canvas", None)
        if _sc:
            _extra_tags, _text_vals, _coded_vals = _sc.collect_soft_data()
            # "Our" tags = every tag key the canvas knows about (project-specific config)
            # This is more accurate than attrs_mod.TAGS which uses the general config
            from attr_viewer import _DEDICATED_FIELD_KEYS as _DFK
            _our_tags = {w.key for w in _sc.widgets
                         if w.style in ("taglist", "boolean", "matrix", "radio", "combo")}
            _our_tags.update(attrs_mod.QUALITY_TAGS)
            # Also include individual button keys from taglist/boolean/radio widgets
            for w in _sc.widgets:
                if w.style in ("taglist", "boolean", "radio"):
                    _our_tags.update(getattr(w, "_btns", {}).keys())
            # Strip keys from dedicated-field widgets (audio etc.) — not stored in tags
            _dedicated_btn_keys = set()
            for w in _sc.widgets:
                if w.style == "radio" and w.key in _DFK:
                    _dedicated_btn_keys.update(getattr(w, "_btns", {}).keys())
            # Coded-boolean option keys (e.g. "watermark") — stored in filename, not tags.
            # Also include complement keys from any radio widget that has a positive coded-bool btn.
            # Build opt_key → letter map (e.g. "watermark" → "wm") from CODED_FIELDS labels.
            _cb_label_map = {lbl.lower(): letter.lower()
                             for letter, lbl, d in attrs_mod.CODED_FIELDS if d == 0}
            _coded_bool_opts = set(_cb_label_map)
            for _cw in _sc.widgets:
                if _cw.style == "radio":
                    _wb = getattr(_cw, '_btns', {})
                    if any(k.lower() in _cb_label_map for k in _wb):
                        _coded_bool_opts.update(k.lower() for k in _wb)
            # All option keys from combo widgets — these live in the filename, not tags
            _combo_opt_keys = set()
            for _cw in _sc.widgets:
                if _cw.style == "combo":
                    _combo_opt_keys.update(k for k, _ in (_cw.options or []))
            # Preserve foreign tags we don't own, then add canvas tags
            _preserved = [t for t in entry.get("tags", [])
                          if t not in _our_tags and t not in _dedicated_btn_keys
                          and t not in _coded_bool_opts and t not in _combo_opt_keys]
            tags.extend(_preserved)
            # Canvas tags — strip quality tags, dedicated-field values, coded booleans, combo keys
            tags.extend(t for t in _extra_tags
                        if t not in attrs_mod.QUALITY_TAGS
                        and t not in _dedicated_btn_keys
                        and t not in _coded_bool_opts
                        and t not in _combo_opt_keys)
            tags = list(dict.fromkeys(tags))  # remove duplicates, preserve order
        else:
            _text_vals = {}
            _coded_vals = {}
        # Canvas P tile has priority when present; fall back to classic p_edits then combo
        if "person_id" in _text_vals:
            _canvas_pid = _norm_pid(_text_vals["person_id"])
            persons = [_canvas_pid] if _canvas_pid else []
        else:
            persons = [_norm_pid(pe.text().strip()) for pe in self._p_edits if pe.text().strip()]
            if not persons:
                _combo_fid = self._person_id_combo.currentData()
                if _combo_fid:
                    persons = [_norm_pid(_combo_fid)]
        attrs_mod.set_file(app.attrs_data, path,
                           tags=tags,
                           note=self._project_edit.text() if self._note_row_widget.isVisible() else _text_vals.get("note", entry.get("note", "")),
                           confirmed=entry.get("confirmed", False),
                           project=entry.get("project", ""),
                           scene=entry.get("scene", ""),
                           prompt=_text_vals.get("prompt", entry.get("prompt", "")),
                           neg_prompt=_text_vals.get("neg_prompt", entry.get("neg_prompt", "")),
                           seed=self._seed_edit.text() or entry.get("seed", ""),
                           meta=entry.get("meta"),
                           custom=entry.get("custom", ""),
                           person_id=persons[0] if persons else "",
                           speech=_text_vals.get("speech", entry.get("speech", "")),
                           audio=_text_vals.get("audio", entry.get("audio", "")),
                           editable=not self._protected_check.isChecked())
        # Write canvas coded-field values (HC, E, FA, SK, etc.) back into attrs_data.
        # set_file preserves old values via merge; we overwrite with the current canvas state.
        if _coded_vals and path in app.attrs_data:
            for _ck, _cv in _coded_vals.items():
                app.attrs_data[path][_ck] = _cv
        attrs_mod.save(app.current_project, app.attrs_data)
        # One-way filename tag_group rules: apply detect rules to existing files
        # (e.g. "Gemini_Generated_Image_" → MDL_img_Table → "03")
        # Only tag_group rules — boolean coded-field rules go in the filename, not tags.
        _fn_cfg = attrs_mod.load_filename_config(getattr(app, "current_project", None))
        _fn_rules_all = attrs_mod.load_filename_rules(getattr(app, "current_project", None))
        if _fn_rules_all:
            _name_lc = os.path.basename(path).lower()
            _fn_tags = [r.get("value", "").strip()
                        for r in _fn_rules_all
                        if r.get("tag_group") and r.get("pattern", "").lower() in _name_lc
                        and r.get("value", "").strip()]
            if _fn_tags:
                _cur_entry2 = attrs_mod.get(app.attrs_data, path)
                _cur_tags2 = list(_cur_entry2.get("tags", []))
                _fn_changed = False
                for _ft in _fn_tags:
                    if _ft not in _cur_tags2:
                        _cur_tags2.append(_ft)
                        _fn_changed = True
                if _fn_changed:
                    _cur_entry2["tags"] = _cur_tags2
                    attrs_mod.save(app.current_project, app.attrs_data)
        # Tag ↔ filename sync: apply two-way tag_group rules when auto_rename is on
        if _fn_cfg.get("auto_rename", False):
            new_path = attrs_mod.apply_tag_sync_rules(app.attrs_data, path, app.current_project)
            if new_path != path:
                attrs_mod.update_path_in_all_stores(path, new_path, app.current_project)
                if app.data and "paths" in app.data and path in app.data["paths"]:
                    app.data["paths"][app.data["paths"].index(path)] = new_path
                self._attr_path = new_path
                if self._canvas_loaded_path is not None: self._canvas_loaded_path = new_path
                self.handler.current_path = new_path
                row = app._current_row()
                if row >= 0:
                    app.table.set_row_path(row, new_path)
                self.setWindowTitle(os.path.basename(new_path))
                path = new_path
        # Embed AItan{} block into the file itself (background, non-blocking)
        _saved_entry = attrs_mod.get(app.attrs_data, path)
        if os.path.exists(path):
            import threading
            def _embed_and_refresh(_p=path, _e=_saved_entry):
                attrs_mod.embed_aitan_meta(_p, _e)
                # Schedule UI refresh on main thread via signal
                self._raw_refresh_signal.emit(_p)
            threading.Thread(target=_embed_and_refresh, daemon=True).start()
        row = app._current_row()
        if row >= 0:
            app._refresh_attrs_indicator(row, path)
        app._highlight_unmarked_rows()
        if app.btn_hide_confirmed.isChecked():
            app._apply_confirmed_filter(True)
        # Sync main window inline panel if visible
        if hasattr(app, '_inline_attr_path') and app._inline_attr_path == path:
            app._refresh_inline_attrs(path)

    def _update_bake_btn(self, state):
        """state: 'idle' | 'pending' | 'ok' | 'error'"""
        self._bake_btn_state = state
        _styles = {
            "idle":    "background:#3a3a4a; color:#e0e0e0; border:1px solid #556655; padding:2px 6px;",
            "pending": "background:#7a5a10; color:#ffe080; border:1px solid #aa8820; padding:2px 6px;",
            "ok":      "background:#2a6a2a; color:#aaffaa; border:1px solid #44aa44; padding:2px 6px;",
            "error":   "background:#6a2020; color:#ffaaaa; border:1px solid #aa3333; padding:2px 6px;",
        }
        self._btn_bake_meta.setStyleSheet(_styles.get(state, _styles["idle"]))

    def _on_raw_refresh(self, path):
        """Called from main thread after background embed completes — refresh Raw Data display."""
        _ed = getattr(self, '_raw_meta_edit', None)
        if _ed is None:
            return
        # Only refresh if still viewing the same file
        if getattr(self, '_attr_path', None) == path:
            _ed.setPlainText(attrs_mod.read_raw_embedded_text(path) or "(no embedded text)")

    def _bake_to_file(self, silent=False):
        """Embed all attrs (tags, prompt, seed, etc.) into the physical file as AItan{} block."""
        path = self._attr_path
        if not path or not os.path.exists(path): return
        app = self.handler.app
        # Save current UI state to attrs_data first (picks up prompt, seed, person_id etc.)
        if not silent:
            self._save_attrs()
            path = self._attr_path  # refresh — _save_attrs may have renamed the file
            if not os.path.exists(path): return
            if self._chk_auto_rename.isChecked():
                pid = _norm_pid(attrs_mod.get(app.attrs_data, path).get("person_id", "") or "")
                if not pid:
                    persons = [pe.text().strip() for pe in self._p_edits if pe.text().strip()]
                    pid = persons[0] if persons else "000"
                orig_stem = os.path.splitext(os.path.basename(path))[0]

                def _apply_rename(old_p, new_p):
                    if (app.data and "paths" in app.data and old_p in app.data["paths"]):
                        app.data["paths"][app.data["paths"].index(old_p)] = new_p
                    for row in range(app.table.rowCount()):
                        if app.table.get_row_path(row) == old_p:
                            app.table.set_row_path(row, new_p)
                            name_item = app.table.item(row, 2)
                            if name_item:
                                name_item.setText(os.path.basename(new_p))
                            break
                    self._attr_path = new_p
                    if self._canvas_loaded_path is not None: self._canvas_loaded_path = new_p
                    self.handler.current_path = new_p

                # Coded combo values (O/R/K) — apply to filename on bake
                _sc2 = getattr(self, "_soft_canvas", None)
                if _sc2:
                    try:
                        _, _, _coded_vals2 = _sc2.collect_soft_data()
                        if _coded_vals2:
                            _ork_stem, _ork_ext = os.path.splitext(os.path.basename(path))
                            _ork_parts = attrs_mod.parse_coded_filename(_ork_stem)
                            if _ork_parts is None:
                                _ork_parts = {"persons": [], "persons_with": [],
                                              "j": attrs_mod.julian_id_for_file(path)}
                            _ork_chg = False
                            for _fk, _fv in _coded_vals2.items():
                                if _ork_parts.get(_fk, "") != _fv:
                                    _ork_parts[_fk] = _fv; _ork_chg = True
                            if _ork_chg:
                                _ork_df = not bool(_ork_parts.get("persons"))
                                _ork_fo = attrs_mod.get_sync_field_order(app.current_project)
                                _ork_ns = attrs_mod.build_coded_filename(_ork_parts, date_first=_ork_df, field_order=_ork_fo)
                                if _ork_ns and _ork_ns != _ork_stem:
                                    _ork_np = attrs_mod.unique_path(
                                        os.path.join(os.path.dirname(path), _ork_ns + _ork_ext))
                                    if _ork_np != path:
                                        os.rename(path, _ork_np)
                                        if path in app.attrs_data:
                                            app.attrs_data[_ork_np] = app.attrs_data.pop(path)
                                        attrs_mod.update_path_in_all_stores(
                                            path, _ork_np, app.current_project)
                                        _apply_rename(path, _ork_np)
                                        path = _ork_np
                                        attrs_mod.save(app.current_project, app.attrs_data)
                    except Exception:
                        pass
                if pid and pid != "000":
                    try:
                        new_path = attrs_mod.rename_with_person_id(
                            app.attrs_data, path, pid,
                            flush_stores=True,
                            project=app.current_project,
                            skip_uncoded=False)
                        if new_path != path:
                            _apply_rename(path, new_path)
                            path = new_path
                    except Exception:
                        pass
                try:
                    new_path = attrs_mod.apply_boolean_sync_rules(
                        app.attrs_data, path, app.current_project,
                        orig_stem=orig_stem)
                    if new_path != path:
                        _apply_rename(path, new_path)
                        path = new_path
                except Exception:
                    pass
                # Coded boolean flags from radio widgets (WM, ED) — apply to coded filename
                _sc = getattr(self, "_soft_canvas", None)
                if _sc:
                    try:
                        _cb_map = {lbl.lower(): letter.lower()
                                   for letter, lbl, d in attrs_mod.CODED_FIELDS if d == 0}
                        _bool_flags = {}
                        for _cw in _sc.widgets:
                            if _cw.style == "radio":
                                _wb = getattr(_cw, "_btns", {})
                                _pk = next((k for k in _wb if k.lower() in _cb_map), None)
                                if _pk:
                                    _bool_flags[_cb_map[_pk.lower()]] = bool(_wb[_pk].isChecked())
                        if _bool_flags:
                            _stem2, _ext2 = os.path.splitext(os.path.basename(path))
                            _parts2 = attrs_mod.parse_coded_filename(_stem2)
                            if _parts2 is not None:
                                _chg = False
                                for _lk, _on in _bool_flags.items():
                                    _cur = bool(_parts2.get(_lk, ""))
                                    if _on and not _cur:
                                        _parts2[_lk] = _lk.upper(); _chg = True
                                    elif not _on and _cur:
                                        _parts2[_lk] = "";           _chg = True
                                if _chg:
                                    _ns = attrs_mod.build_coded_filename(
                                        _parts2, date_first=not bool(_parts2.get("persons")),
                                        field_order=attrs_mod.get_sync_field_order(app.current_project))
                                    if _ns and _ns != _stem2:
                                        _np = attrs_mod.unique_path(
                                            os.path.join(os.path.dirname(path), _ns + _ext2))
                                        if _np != path:
                                            os.rename(path, _np)
                                            if path in app.attrs_data:
                                                app.attrs_data[_np] = app.attrs_data.pop(path)
                                            attrs_mod.update_path_in_all_stores(
                                                path, _np, app.current_project)
                                            _apply_rename(path, _np)
                                            path = _np
                                            attrs_mod.save(app.current_project, app.attrs_data)
                    except Exception:
                        pass
        # Embed synchronously so we can report success/error
        entry = attrs_mod.get(app.attrs_data, path)
        # Never bake an empty entry — it would write AItan{} and erase recovered filename data.
        # If entry has no real data, skip (silent) or show what's already on disk (manual bake).
        if not attrs_mod._has_real_data(entry):
            if silent:
                return
            # Manual bake with nothing to write — just refresh the display from disk
            self._update_bake_btn("ok")
            self._bake_err_label.setText("")
            QTimer.singleShot(2000, lambda: self._update_bake_btn("idle"))
            self._raw_meta_edit.setPlainText(
                attrs_mod.read_raw_embedded_text(path) or "(no embedded text)")
            return
        ok = attrs_mod.embed_aitan_meta(path, entry)
        if not ok:
            # Some containers cause ffmpeg to return non-zero even when metadata was written.
            # Also guards against rare race conditions — verify by reading the block back.
            ok = bool(attrs_mod._read_embedded_aitan_block(path))
        if ok:
            self._update_bake_btn("ok")
            self._bake_err_label.setText("")
            QTimer.singleShot(2000, lambda: self._update_bake_btn("idle"))
            _baked_path = getattr(self, '_attr_path', path)
            self._raw_meta_edit.setPlainText(
                attrs_mod.read_raw_embedded_text(_baked_path) or "(no embedded text)")
            # Record as correction example for future CLIP detection
            try:
                import torch as _torch
                _proj = getattr(app, "current_project", None)
                _cemb = None
                _data = getattr(app, "data", None)
                if _data and "paths" in _data and path in _data["paths"]:
                    _ci = _data["paths"].index(path)
                    _cemb = _data["embeddings"][_ci]
                if _cemb is None:
                    # Fallback: load embedding from features file on disk
                    _ft_path = os.path.join(
                        attrs_mod.DATA_DIR,
                        f"features_{_proj}.pt" if _proj else "features_default.pt")
                    if os.path.exists(_ft_path):
                        _ft = _torch.load(_ft_path, map_location="cpu", weights_only=False)
                        if "paths" in _ft and path in _ft["paths"]:
                            _fi = _ft["paths"].index(path)
                            _cemb = _ft["embeddings"][_fi]
                if _cemb is not None:
                    _centry = attrs_mod.get(app.attrs_data, path)
                    attrs_mod.add_correction(_proj, path, _cemb, _centry)
            except Exception:
                pass
        else:
            if not silent:
                self._update_bake_btn("error")
                self._bake_err_label.setText("Bake failed — file type not supported or write error.")
                QMessageBox.critical(self, _t("Bake Failed / 書込失敗"), _t("Could not embed AItan block into file. / AItanブロックをファイルに埋め込めませんでした。"))

    def refresh_language(self):
        """Re-translate all preview-window labels/buttons/tooltips after a language change."""
        # Nav buttons + tooltips
        if hasattr(self, '_btn_back_left'):
            self._btn_back_left.setToolTip(_t("Go back to previously viewed file / 直前に表示したファイルに戻る"))
        if hasattr(self, '_btn_orient_left'):
            self._btn_orient_left.setToolTip(_t("Toggle attr pane side or below / 属性ペインを横/下に切替"))
        if hasattr(self, '_btn_back_top'):
            self._btn_back_top.setToolTip(_t("Go back to previously viewed file / 直前に表示したファイルに戻る"))
        if hasattr(self, 'btn_orient'):
            self.btn_orient.setToolTip(_t("Toggle attr pane side or below / 属性ペインを横/下に切替"))
        # Inline inputs
        if hasattr(self, '_project_edit'):
            self._project_edit.setPlaceholderText(_t("note… / ノート…"))
        if hasattr(self, '_person_id_combo'):
            self._person_id_combo.setPlaceholderText(_t("select or type ID… / IDを選択または入力…"))
        if hasattr(self, '_person_name_edit'):
            self._person_name_edit.setPlaceholderText(_t("name… / 名前…"))
        # Person buttons
        if hasattr(self, '_btn_match_person'):
            self._btn_match_person.setText(_t("Match / 照合"))
        if hasattr(self, '_btn_detect_person'):
            _txt = self._btn_detect_person.text()
            # Preserve Registered / Detect state label
            if _txt in ("Registered", _t("Registered / 登録済")):
                self._btn_detect_person.setText(_t("Registered / 登録済"))
            elif _txt != "…":
                self._btn_detect_person.setText(_t("Detect & Register / 検出＆登録"))
        # Bake row
        if hasattr(self, '_btn_bake_meta'):
            self._btn_bake_meta.setText(_t("Bake to File / ファイルに書込"))
            self._btn_bake_meta.setToolTip(_t(
                "Embed prompt/seed/model from database into the physical file / データベースのプロンプト/シード/モデルを物理ファイルに埋め込む"))
        if hasattr(self, '_chk_auto_bake'):
            self._chk_auto_bake.setText(_t("Auto-bake / 自動書込"))
            self._chk_auto_bake.setToolTip(_t("Automatically bake to file when navigating to next image / 次の画像へ移動時に自動でファイルに書き込み"))
        if hasattr(self, '_btn_gather'):
            self._btn_gather.setText(_t("⚑ Gather / ⚑ 集約"))
            self._btn_gather.setToolTip(_t("Move any off-screen canvas tiles back into view / 画面外のキャンバスタイルを表示内に戻す"))
        if hasattr(self, '_btn_apply_clip'):
            self._btn_apply_clip.setText(_t("🔄 Refresh CLIP / 🔄 CLIP再検出"))
            self._btn_apply_clip.setToolTip(_t("Clear all CLIP fields and re-detect from scratch / 全CLIPフィールドをクリアして最初から再検出"))
        if hasattr(self, '_chk_auto_rename'):
            self._chk_auto_rename.setText(_t("Auto-rename / 自動改名"))
            self._chk_auto_rename.setToolTip(_t("Rename file to match person ID when baking / 書込時に人物IDに合わせてファイル名を変更"))
        if hasattr(self, '_protected_check'):
            self._protected_check.setText(_t("🔓 Editable / 🔓 編集可"))
            self._protected_check.setToolTip(_t("🔓 Editable — app may auto-rename\n🔒 Locked — app will not auto-rename / 🔓 編集可 — 自動改名される可能性あり\n🔒 ロック — 自動改名されません"))
        # Raw Data section + Save Layout
        if hasattr(self, '_raw_meta_sec'):
            _raw_lbl = getattr(self._raw_meta_sec, '_title_lbl', None) or getattr(self._raw_meta_sec, '_lbl', None)
            if _raw_lbl and hasattr(_raw_lbl, 'setText'):
                _raw_lbl.setText(_t("Raw Data / 生データ"))
        if hasattr(self, '_btn_save_layout'):
            self._btn_save_layout.setText(_t("💾 Layout / 💾 レイアウト"))
            self._btn_save_layout.setToolTip(_t("Save current canvas tile positions / 現在のキャンバスタイル位置を保存"))
        if hasattr(self, '_raw_meta_edit'):
            self._raw_meta_edit.setPlaceholderText(_t("No data. / データなし。"))
        if hasattr(self, '_clip_inspect_edit'):
            self._clip_inspect_edit.setPlaceholderText(_t("CLIP scores will appear here. / CLIPスコアがここに表示されます。"))
        if hasattr(self, '_face_inspect_edit'):
            self._face_inspect_edit.setPlaceholderText(_t("Face scores will appear here. / 顔スコアがここに表示されます。"))
        if hasattr(self, '_btn_apply_face'):
            self._btn_apply_face.setText(_t("Apply / 適用"))
            self._btn_apply_face.setToolTip(_t("Apply detected person ID to this file / 検出された人物IDをこのファイルに適用"))

    def set_mode_color(self, color: str):
        """Update the bar background color to reflect the active mode."""
        bar_ss = f"background-color: {color};"
        btn_ss = "color: #fff; background-color: transparent; border: none; padding: 4px;"
        self._top_bar.setStyleSheet(bar_ss)
        self._left_bar.setStyleSheet(bar_ss)
        for btn in (self._btn_toggle_top, self._btn_toggle_left,
                    self._btn_back_top, self._btn_back_left,
                    self.btn_orient, self._btn_orient_left):
            btn.setStyleSheet(btn_ss)

    def closeEvent(self, event):
        import aisearch_config as _cfg_mod
        g = self.geometry()
        self.handler.app.config["preview_geometry"] = [g.x(), g.y(), g.width(), g.height()]
        _cfg_mod.save_config(self.handler.app.config)
        self.handler._close()
        event.accept()


class PreviewHandler:
    def __init__(self, master, app_instance):
        self.master = master        # main QMainWindow
        self.app = app_instance
        self.window = None
        self.last_geom = None
        self._expanded_on_right = False
        self._expanded_wid = None
        self.is_maximized = False
        self.current_path = None
        self._context_menu = None
        self.zoom_factor = 1.0
        self._cached_pixmap = None
        self._cached_pixmap_path = None
        self._zoom_timer = QTimer()
        self._zoom_timer.setSingleShot(True)
        self._zoom_timer.timeout.connect(
            lambda: self._render(self.current_path,
                                 self.current_path.lower().endswith(
                                     ('.mp4', '.mkv', '.mov', '.avi', '.webm'))
                                 if self.current_path else False))

    def pan_image(self, delta):
        if not self.window: return
        sa = self.window.scroll_area
        sa.horizontalScrollBar().setValue(sa.horizontalScrollBar().value() - delta.x())
        sa.verticalScrollBar().setValue(sa.verticalScrollBar().value() - delta.y())

    def _start_zoom_cleanup_timer(self):
        self._zoom_timer.start(150)

    def show(self, path):
        if not path or not os.path.exists(path): return
        if path != self.current_path:
            self.zoom_factor = 1.0
            # Only clear cache if it doesn't already match the incoming path
            # (run_search may have pre-populated it)
            if self._cached_pixmap_path != path:
                self._cached_pixmap = None
                self._cached_pixmap_path = None

        _new_window = (self.window is None)
        if self.window is None:
            self.window = PreviewWindow(self)
            icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aisearch_icon.png")
            if os.path.exists(icon_path):
                self.window.setWindowIcon(QIcon(icon_path))
            # Apply "always on top" setting from config
            if self.app.config.get("preview_always_on_top", False):
                self.window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            saved = self.app.config.get("preview_geometry")
            if saved and len(saved) == 4:
                from PyQt6.QtGui import QGuiApplication
                sx, sy, sw, sh = saved
                screens = QGuiApplication.screens()
                on_screen = any(
                    s.geometry().contains(sx + sw // 2, sy + sh // 2)
                    for s in screens
                )
                if on_screen:
                    self.window.setGeometry(sx, sy, sw, sh)
        if not self.window.isVisible():
            self.window.show()
        else:
            self.window.raise_()  # bring to front without stealing focus from main window

        # Apply current mode color to the bar
        _mode_colors = {"search": "#2a8ad4", "dup": "#9b6dff", "browse": "#3a8a3a"}
        _cur_mode = self.app.config.get("last_mode", "search")
        self.window.set_mode_color(_mode_colors.get(_cur_mode, "#1a1a1a"))

        is_video = path.lower().endswith(('.mp4', '.mkv', '.mov', '.avi', '.webm'))
        # Set initial title (info will be appended once _refresh_attrs completes)
        self.window._file_info_text = ""
        self.window._update_title_with_info(path)

        if is_video:
            self.window.label.setStyleSheet("background-color: black; border: 10px solid #00ff00;")
        else:
            self.window.label.setStyleSheet("background-color: black;")

        self.current_path = path
        # Defer render FIRST — image appears before the slow attr panel build
        QTimer.singleShot(0, lambda: self._render(path, is_video))
        QTimer.singleShot(0, lambda: self.window._refresh_attrs(path))
        # Build attr panel only once (on first window creation), AFTER render
        if _new_window:
            QTimer.singleShot(0, self.window._deferred_build_attr_panel)
        # Note: _render() already calls setMaximumHeight(nh) synchronously,
        # so no deferred _auto_fit_splitter needed here.

    def _go_back_from_preview(self):
        """Delegate back navigation to the main app."""
        self.app._go_back()

    def _navigate(self, direction):
        row    = self.app._current_row()
        if row < 0: return
        target = row + direction
        if target < 0 or target >= self.app.table.rowCount(): return
        self.app._select_row(target)
        path = self.app.table.get_row_path(target)
        if path:
            # Show loading overlay before potentially slow render
            self.show(path)

    def _close(self):
        if self.window:
            self.window.hide()
        self.is_maximized = False

    def _copy_path_to_clipboard(self):
        if self.current_path:
            QApplication.clipboard().setText(os.path.abspath(self.current_path))

    def _copy_file(self):
        row = self.app._current_row()
        if row < 0: return
        src = self.app.table.get_row_path(row)
        target_dir = FolderPickerDialog(self.window, initialdir=self.app.last_move_dir, title="Copy to...").result
        if not target_dir: return
        try:
            shutil.copy2(src, os.path.join(target_dir, os.path.basename(src)))
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self.window, _t("Copy Error / コピーエラー"), str(e))

    def _move_file(self):
        import aisearch_config as cfg_mod
        row = self.app._current_row()
        if row < 0: return
        old_path   = self.app.table.get_row_path(row)
        target_dir = FolderPickerDialog(self.window, initialdir=self.app.last_move_dir, title="Move to...").result
        if not target_dir: return
        dest_path  = os.path.join(target_dir, os.path.basename(old_path))
        mode       = self.app.config.get("move_conflict", "size_check")
        final_path, overwrite = front_page._resolve_with_size(dest_path, old_path, mode, self.window)
        if final_path is None: return
        try:
            shutil.move(old_path, final_path)
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self.window, _t("Move Error / 移動エラー"), str(e)); return

        attrs_mod.update_path_in_all_stores(old_path, final_path, self.app.current_project)
        if self.app.data and "paths" in self.app.data:
            if overwrite: front_page._remove_from_data(self.app.data, dest_path)
            paths = self.app.data["paths"]
            if old_path in paths:
                paths[paths.index(old_path)] = final_path
                torch.save(self.app.data, os.path.join(attrs_mod.DATA_DIR, f"features_{self.app.current_project}.pt"))

        self.app._update_row(row, old_path, final_path, overwrite, dest_path)
        self.app.last_move_dir = target_dir
        self.app.config["last_move_dir"] = target_dir
        cfg_mod.save_config(self.app.config)
        self.current_path = final_path

    def _reveal_in_nemo(self):
        row = self.app._current_row()
        if row < 0: return
        front_page.open_in_nemo(self.app.table.get_row_path(row))

    def _delete_file(self):
        self.app.delete_file()

    def _toggle_always_on_top(self, checked):
        self.window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, checked)
        self.window.show()
        self.window.activateWindow()
        # Persist to config
        self.app.config["preview_always_on_top"] = checked
        import aisearch_config as cfg_mod
        cfg_mod.save_config(self.app.config)

    def _show_context_menu(self, global_pos):
        if not self._context_menu:
            m = QMenu(self.window)
            m.addAction("📋 Copy path (Ctrl+C)", self._copy_path_to_clipboard)
            m.addAction("📂 Reveal in Nemo",     self._reveal_in_nemo)
            m.addSeparator()
            m.addAction("📄 Copy to...",          self._copy_file)
            m.addAction("📦 Move to...",          self._move_file)
            m.addSeparator()
            m.addAction("🗑️ Delete (Del)",        self._delete_file)
            m.addSeparator()
            self._aot_action = m.addAction("📌 Always on Top (T)")
            self._aot_action.setCheckable(True)
            self._aot_action.toggled.connect(self._toggle_always_on_top)
            self._context_menu = m
        # Sync checked state with actual window flag
        is_top = bool(self.window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        self._aot_action.setChecked(is_top)
        self._context_menu.exec(global_pos)

    def _on_shift_drag_done(self, path):
        """After shift-drag: delete original and update DB/tree."""
        item_row = None
        for r in range(self.app.table.rowCount()):
            if os.path.normpath(self.app.table.get_row_path(r) or "") == os.path.normpath(path):
                item_row = r; break
        try:
            if os.path.exists(path): os.remove(path)
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self.window, _t("Delete Error / 削除エラー"), str(e)); return

        if self.app.data and "paths" in self.app.data:
            norm = os.path.normpath(path)
            idx  = next((i for i, x in enumerate(self.app.data["paths"]) if os.path.normpath(x) == norm), None)
            if idx is not None:
                keep = [i for i in range(len(self.app.data["paths"])) if i != idx]
                self.app.data["paths"]      = [self.app.data["paths"][i] for i in keep]
                self.app.data["embeddings"] = self.app.data["embeddings"][keep]
                torch.save(self.app.data, os.path.join(attrs_mod.DATA_DIR, f"features_{self.app.current_project}.pt"))

        if item_row is not None:
            self.app.table.removeRow(item_row)

        filename = os.path.basename(path)
        QTimer.singleShot(1500, lambda: self._find_and_insert_new(filename, path))

    def _find_and_insert_new(self, filename, original_path):
        import aisearch_logic as logic
        if not self.app.data: return
        now = time.time()
        seen_dirs = set()
        for db_path in self.app.data["paths"]:
            d = os.path.dirname(os.path.abspath(db_path))
            while d and d != os.path.dirname(d):
                seen_dirs.add(d); d = os.path.dirname(d)
        candidate = None
        for d in seen_dirs:
            test = os.path.join(d, filename)
            if (os.path.exists(test) and
                    os.path.abspath(test) != os.path.abspath(original_path) and
                    now - os.path.getmtime(test) < 30):
                candidate = os.path.abspath(test); break
        if not candidate: return
        emb = logic.extract_feature(candidate)
        if emb is None: return
        self.app.data["paths"].append(candidate)
        self.app.data["embeddings"] = torch.cat([self.app.data["embeddings"], emb.unsqueeze(0)])
        torch.save(self.app.data, os.path.join(attrs_mod.DATA_DIR, f"features_{self.app.current_project}.pt"))
        self.app.table.setSortingEnabled(False)
        self.app._append_row("-", logic.get_sz_readable(candidate),
                              os.path.basename(candidate),
                              self.app._mask_path(candidate), candidate)
        self.app.table.setSortingEnabled(True)

    def _screen_for_window(self, win):
        """Return the QScreen that contains the centre of win."""
        from PyQt6.QtGui import QGuiApplication
        centre = win.geometry().center()
        for s in QGuiApplication.screens():
            if s.geometry().contains(centre):
                return s
        return QGuiApplication.primaryScreen()

    def _target_screen(self):
        """Choose the best screen to expand the preview onto.
        Prefers the screen that does NOT contain the main app window.
        Falls back to the screen the preview is already on."""
        from PyQt6.QtGui import QGuiApplication
        screens = QGuiApplication.screens()
        if len(screens) == 1:
            return screens[0]
        # Find the screen the main app window is on
        main_win = getattr(self.app, 'window', None) or getattr(self.app, 'centralWidget', lambda: None)()
        main_screen = None
        if hasattr(self.app, 'geometry'):
            centre = self.app.geometry().center()
            for s in screens:
                if s.geometry().contains(centre):
                    main_screen = s
                    break
        if main_screen is None:
            main_screen = QGuiApplication.primaryScreen()
        # Pick the other screen (first one that isn't the main screen)
        for s in screens:
            if s is not main_screen:
                return s
        return main_screen

    def _toggle_physical_geometry(self, path, is_video):
        if is_video:
            front_page.open_external_viewer(path, keep_open=self.app.keep_viewer_open)
            return

        if not self.is_maximized:
            # Save current geometry for restore
            self.last_geom = self.window.geometry()
            # Find the best screen to expand onto
            target = self._target_screen()
            g = target.geometry()
            rim = 2
            self.window.showNormal()
            self.window.setGeometry(g.x() + rim, g.y() + rim,
                                    g.width() - rim * 2, g.height() - rim * 2)
            self.is_maximized = True
        else:
            self.window.showNormal()
            if self.last_geom:
                # Clamp saved geometry to its screen so it doesn't land off-screen
                from PyQt6.QtGui import QGuiApplication
                rx, ry = self.last_geom.x(), self.last_geom.y()
                rw, rh = self.last_geom.width(), self.last_geom.height()
                scr = None
                for s in QGuiApplication.screens():
                    if s.geometry().contains(rx + rw // 2, ry + rh // 2):
                        scr = s
                        break
                if scr is None:
                    scr = self._screen_for_window(self.window)
                sg = scr.geometry()
                rx = max(sg.x(), min(rx, sg.x() + sg.width()  - rw))
                ry = max(sg.y(), min(ry, sg.y() + sg.height() - rh))
                self.window.setGeometry(rx, ry, rw, rh)
            self.is_maximized = False

        QTimer.singleShot(100, lambda: self._render(path, is_video))

    def _rerender(self):
        if self.current_path:
            is_vid = self.current_path.lower().endswith(('.mp4', '.mkv', '.mov', '.avi', '.webm'))
            self._render(self.current_path, is_vid, fast=False)

    def _auto_fit_splitter(self):
        """Re-apply the image pane height cap after layout settles (e.g. attr panel toggle)."""
        if not self.window:
            return
        sp = self.window._splitter
        if (sp.orientation() != Qt.Orientation.Vertical
                or not self.window._attr_scroll.isVisible()):
            return
        nh = self.window.label.height()
        if nh <= 0:
            return
        self.window.scroll_area.setMaximumHeight(nh)

    def _update_splitter_orientation(self):
        pass  # orientation is set manually via the ⇔ button

    def _toggle_splitter_orientation(self):
        """Manually toggle attr pane between side (Horizontal) and below (Vertical)."""
        if not self.window:
            return
        sp = self.window._splitter
        sizes = sp.sizes()
        is_open = (sizes[1] > 36) if len(sizes) > 1 else False
        want = (Qt.Orientation.Vertical if sp.orientation() == Qt.Orientation.Horizontal
                else Qt.Orientation.Horizontal)
        sp.setOrientation(want)
        import aisearch_config as _cfg
        self.window.handler.app.config["attrs_splitter_orient"] = (
            "horizontal" if want == Qt.Orientation.Horizontal else "vertical")
        _cfg.save_config(self.window.handler.app.config,
                         getattr(self.window.handler.app, "current_project", None))
        def _apply(retries=3):
            self.window._sync_toggle_strip()
            total = sp.width() if want == Qt.Orientation.Horizontal else sp.height()
            if total <= 0:
                if retries > 0:
                    QTimer.singleShot(20, lambda: _apply(retries - 1))
                return
            if is_open:
                saved = max(self.window._saved_size(), 200)
                sp.setSizes([max(1, total - saved), saved])
            else:
                sp.setSizes([max(1, total - 26), 26])
            # Remove height cap — rerender will re-apply appropriate cap
            self.window.scroll_area.setMaximumHeight(16777215)
            QTimer.singleShot(0, self._rerender)
        QTimer.singleShot(0, lambda: _apply())

    def _render(self, path, is_video, fast=False):
        if not self.window: return
        if not path: return
        # Show loading indicator and flush paint events before slow image load
        _needs_load = (self._cached_pixmap is None or self._cached_pixmap_path != path)
        if _needs_load:
            self.window.btn_toggle_attrs.setText("⏳")
            self.window.btn_toggle_attrs.repaint()
        try:
            vw = self.window.scroll_area.viewport().width()
            vh = self.window.scroll_area.viewport().height()
            if vw < 10 or vh < 10: vw, vh = 700, 700

            # Load and cache the source pixmap — invalidate if path changed
            if _needs_load:
                if is_video:
                    import numpy as np
                    cap = cv2.VideoCapture(path)
                    ret1, frame1 = cap.read()
                    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                    if total > 1:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, total - 1)
                        ret2, frame2 = cap.read()
                    else:
                        ret2, frame2 = False, None
                    cap.release()
                    if not ret1:
                        self.window.label.setText("⚠ Could not read video frame")
                        return
                    if ret2 and frame2 is not None:
                        h, w = frame1.shape[:2]
                        if w > h:  # landscape → stack top/bottom
                            div_h = max(8, h // 48)
                            div = np.zeros((div_h, w, 3), dtype=np.uint8)
                            div[:, :] = [0, 200, 0]  # BGR green
                            combined = np.concatenate([frame1, div, frame2], axis=0)
                        else:  # portrait / square → side by side
                            div_w = max(8, w // 48)
                            div = np.zeros((h, div_w, 3), dtype=np.uint8)
                            div[:, :] = [0, 200, 0]  # BGR green
                            combined = np.concatenate([frame1, div, frame2], axis=1)
                    else:
                        combined = frame1
                    img = Image.fromarray(cv2.cvtColor(combined, cv2.COLOR_BGR2RGB))
                    self._cached_pixmap = _pil_to_pixmap(img)
                else:
                    # Load via PIL thumbnail — decodes only at display resolution.
                    # draft() uses JPEG DCT scaling; BOX filter is fast for downscaling.
                    try:
                        img = Image.open(path)
                        max_dim = int(max(vw, vh) * max(self.zoom_factor, 1.0))
                        img.draft('RGB', (max_dim * 2, max_dim * 2))
                        img.thumbnail((max_dim, max_dim), Image.BOX)
                        self._cached_pixmap = _pil_to_pixmap(img)
                    except Exception:
                        px = QPixmap(path)
                        self._cached_pixmap = px if not px.isNull() else None
                        if self._cached_pixmap is None:
                            self.window.label.setText("⚠ Could not load image")
                            return
                self._cached_pixmap_path = path
                self._update_splitter_orientation()

            ratio = min(vw / self._cached_pixmap.width(),
                        vh / self._cached_pixmap.height()) * self.zoom_factor
            nw = max(1, int(self._cached_pixmap.width()  * ratio))
            nh = max(1, int(self._cached_pixmap.height() * ratio))
            mode = (Qt.TransformationMode.FastTransformation if fast
                    else Qt.TransformationMode.SmoothTransformation)
            scaled = self._cached_pixmap.scaled(
                nw, nh, Qt.AspectRatioMode.KeepAspectRatio, mode)
            self.window.label.setPixmap(scaled)
            self.window.label.setFixedSize(scaled.size())
            # Constrain the image pane so it can't be taller than the rendered image.
            # setMaximumHeight is enforced immediately by Qt's layout engine, so the
            # attr panel moves up without any 120ms flicker.
            sp = self.window._splitter
            if (sp.orientation() == Qt.Orientation.Vertical
                    and self.window._attr_scroll.isVisible()):
                self.window.scroll_area.setMaximumHeight(nh)
            else:
                self.window.scroll_area.setMaximumHeight(16777215)  # QWIDGETSIZE_MAX
        except Exception as e:
            if self.window:
                self.window.label.setText(f"Render Error: {e}")
        finally:
            if self.window:
                self.window.btn_toggle_attrs.setText(
                    self.window._attr_arrow(self.window._attr_scroll.isVisible()))
