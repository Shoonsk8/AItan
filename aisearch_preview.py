import os, re, cv2, shutil, subprocess, io, torch, time, threading, json
from PIL import Image, PngImagePlugin

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QLabel, QMenu,
                              QApplication, QDialog, QHBoxLayout, QPushButton,
                              QCheckBox, QComboBox, QGridLayout, QLineEdit,
                              QTextEdit, QScrollArea, QSizePolicy,
                              QSplitter, QSplitterHandle, QToolButton)
import aisearch_attrs as attrs_mod
from PyQt6.QtCore import Qt, QTimer, QUrl, QMimeData, QPoint, QEvent, QSize
from PyQt6.QtGui import QPixmap, QIcon, QDrag, QCursor, QFont, QPainter, QColor, QImage

from aisearch_config import FolderPickerDialog
import aisearch_front_page as front_page

VERSION = "1.93"


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
        if ext.endswith(('.mp4', '.m4v')):
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
        hdr_lay.addWidget(lbl, stretch=1)

        # Arrange-mode drag handle (hidden until arrange mode is on)
        self._drag_handle = QLabel("⠿")
        self._drag_handle.setFixedSize(22, 18)
        self._drag_handle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._drag_handle.setStyleSheet("color:#888; font-size:12pt;")
        self._drag_handle.setCursor(Qt.CursorShape.SizeVerCursor)
        self._drag_handle.setVisible(False)
        hdr_lay.addWidget(self._drag_handle)

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
    def __init__(self, handler):
        super().__init__()
        self.handler = handler
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
        self._btn_orient_left = QPushButton("⇕")
        self._btn_orient_left.setFlat(True)
        self._btn_orient_left.setToolTip("Toggle attr pane side / below")
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
        self.btn_orient = QPushButton("⇔")
        self.btn_orient.setFlat(True)
        self.btn_orient.setToolTip("Toggle attr pane side / below")
        self.btn_orient.setStyleSheet(
            "color: #888; background-color: #1a1a1a; border: none; padding: 4px;")
        self.btn_orient.setFixedSize(26, 26)
        self.btn_orient.clicked.connect(handler._toggle_splitter_orientation)
        _tbar_layout.addWidget(self.btn_orient)
        _inner_vbox.addWidget(self._top_bar)

        # btn_toggle_attrs points to whichever strip is active (top in vertical mode)
        self.btn_toggle_attrs = self._btn_toggle_top

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
        # --- Section drag-handle events ---
        for sec in getattr(self, '_attr_sections', []):
            if obj is sec._drag_handle:
                t = event.type()
                if t == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                    self._drag_sec = sec
                    return True
                elif t == QEvent.Type.MouseMove and getattr(self, '_drag_sec', None) is sec:
                    self._do_drag_move(event.globalPosition().toPoint().y())
                    return True
                elif t == QEvent.Type.MouseButtonRelease:
                    if getattr(self, '_drag_sec', None) is sec:
                        self._drag_sec = None
                        self._save_section_order()
                    return True
                break

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

        # Top row: info label + Quality + Change File Name
        r1 = QHBoxLayout()
        self._info_label = QLabel("")
        self._info_label.setStyleSheet("color: #e0e0e0;")
        r1.addWidget(self._info_label, stretch=1)
        lq = QLabel("Quality:")
        lq.setStyleSheet("color: #aaa;")
        r1.addWidget(lq)
        self._quality_combo = QComboBox()
        self._quality_combo.addItem("—", "")
        for key, lbl in attrs_mod.TAG_GROUPS["Quality"]:
            self._quality_combo.addItem(lbl, key)
        self._quality_combo.setFixedWidth(105)
        self._quality_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._quality_combo.currentIndexChanged.connect(self._save_attrs)
        r1.addWidget(self._quality_combo)
        self._btn_auto_rename = QPushButton("Change File Name")
        self._btn_auto_rename.setStyleSheet(
            "QPushButton { background-color: #22aa66; color: white; border: none; padding: 3px 8px; font-weight: bold; }"
            "QPushButton:disabled { background-color: #555555; color: #888888; border: none; padding: 3px 8px; }"
        )
        self._btn_auto_rename.setToolTip(
            "Detect person ID + pose, then rename to canonical form")
        self._btn_auto_rename.clicked.connect(self._on_auto_rename)
        self._btn_auto_rename.setVisible(
            self.handler.app.config.get("auto_rename", False))
        r1.addWidget(self._btn_auto_rename)

        self._btn_arrange = QPushButton("≡ Arrange")
        self._btn_arrange.setCheckable(True)
        self._btn_arrange.setFixedWidth(78)
        self._btn_arrange.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_arrange.setStyleSheet(
            "QPushButton { background:#3a3a3a; color:#aaa; border:none; padding:3px 6px; }"
            "QPushButton:checked { background:#4a3a6e; color:#ddd; }")
        self._btn_arrange.toggled.connect(self._toggle_arrange_mode)
        r1.addWidget(self._btn_arrange)
        self._protected_check = QPushButton("🔓 Editable")
        self._protected_check.setCheckable(True)
        self._protected_check.setToolTip("🔓 Editable — app may auto-rename\n🔒 Locked — app will not rename")
        self._protected_check.setStyleSheet(
            "QPushButton { background: transparent; border: none; font-size: 18px; color: #66cc88; padding: 0 4px; }")
        self._protected_check.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._protected_check.toggled.connect(self._save_attrs)
        self._protected_check.toggled.connect(self._apply_protected_lock)
        r1.addWidget(self._protected_check)
        vbox.addLayout(r1)

        # Note row (at top for quick access) — hidden when project defines "note" as a text field
        self._note_row_widget = QWidget()
        r_title = QHBoxLayout(self._note_row_widget)
        r_title.setContentsMargins(0, 0, 0, 0)
        lp = QLabel("Note:")
        lp.setStyleSheet("color: #aaa;")
        r_title.addWidget(lp)
        self._project_edit = QLineEdit()
        self._project_edit.setPlaceholderText("note…")
        self._project_edit.setStyleSheet(
            "background-color: #3a3a3a; color: #e0e0e0; border: 1px solid #555;")
        self._project_edit.editingFinished.connect(self._save_attrs)
        self._project_edit.textChanged.connect(lambda _: self._update_bake_btn("pending"))
        r_title.addWidget(self._project_edit, stretch=1)
        vbox.addWidget(self._note_row_widget)

        self._attr_cbs = {}

        # ── Coded filename fields ─────────────────────────────────────────
        _field_ss = "background:#3a3a3a; color:#e0e0e0; border:1px solid #555; font-family:monospace;"
        _lbl_ss   = "color:#aaa;"

        # ── Person registration row ───────────────────────────────────────
        self._code_edits = {}
        self._p_edits    = []   # populated dynamically from 'id' fields
        self._pw_edits   = []   # populated dynamically from 'id' fields
        self._id_edits   = {}   # section_key → QLineEdit for id-style fields
        rA = QHBoxLayout(); rA.setSpacing(4)

        lp_id = QLabel("Person:"); lp_id.setStyleSheet("color:#aaa;")
        rA.addWidget(lp_id)

        # Editable combo — shows registered IDs, user can type a new one
        self._person_id_combo = QComboBox()
        self._person_id_combo.setEditable(True)
        self._person_id_combo.setMinimumWidth(140)
        self._person_id_combo.setStyleSheet(_field_ss)
        self._person_id_combo.setPlaceholderText("select or type ID…")
        self._person_id_combo.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._person_id_combo.setMaxVisibleItems(20)
        self._person_id_combo.currentIndexChanged.connect(self._on_person_combo_changed)
        rA.addWidget(self._person_id_combo, stretch=1)
        self._person_id_label = self._person_id_combo   # backward-compat alias

        self._person_name_edit = QLineEdit()
        self._person_name_edit.setPlaceholderText("name…")
        self._person_name_edit.setStyleSheet(_field_ss)
        self._person_name_edit.editingFinished.connect(self._on_person_name_changed)
        rA.addWidget(self._person_name_edit, stretch=1)

        self._btn_match_person = QPushButton("Match")
        self._btn_match_person.setVisible(False)  # disabled — not working
        self._btn_match_person.clicked.connect(self._on_match_person)

        self._btn_detect_person = QPushButton("Register")
        self._btn_detect_person.setVisible(False)  # disabled — not working
        self._btn_detect_person.clicked.connect(self._on_detect_person)

        vbox.addLayout(rA)
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
                "BG": [("BG_Major", 3, "Major")],
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
                self._on_name_edit_finished()
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
                fe2.setFixedWidth(75)    # wide enough for "250408"
                fe2.setReadOnly(True)
                fe2.setStyleSheet(
                    "background:#1a1a1a; color:#888888; border:1px solid #333;"
                    " font-family:monospace; padding:1px 3px;")
                fe2.setPlaceholderText("auto")
                fe2.setToolTip("Julian date — set automatically, not editable")
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
        # Styles that should be skipped (handled by taglist/text loop below)
        _SKIP_STYLES  = {"matrix", "boolean", "taglist", "text", ""}
        _STYLE_DIGITS = {"1dig": 1, "2dig": 2, "3dig": 3}

        def _add_id_field(sec_layout, key):
            """Small labeled text input for an id-style section."""
            box = QWidget(); vb = QVBoxLayout(box)
            vb.setContentsMargins(0, 0, 0, 0); vb.setSpacing(1)
            _display = _field_names.get(key, key)
            _lbl = QLabel(f"{_display}:"); _lbl.setStyleSheet(_lbl_ss)
            vb.addWidget(_lbl)
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
        _INTERNAL_GROUP = "Internal"

        # Ensure W/ED/Fix are always created (needed by _refresh_attrs)
        self._w_check  = QCheckBox("W");  self._w_check.setVisible(False)
        self._ed_check = QCheckBox("ED"); self._ed_check.setVisible(False)
        self._btn_normalize = QPushButton("Fix"); self._btn_normalize.setVisible(False)
        self._w_check.toggled.connect(self._on_name_edit_finished)
        self._ed_check.toggled.connect(self._on_name_edit_finished)
        self._btn_normalize.clicked.connect(self._on_normalize_filename)
        self._code_edits["w"]  = self._w_check
        self._code_edits["ed"] = self._ed_check

        self._field_to_section = {}   # letter.lower() -> _AttrSection
        self._attr_sections = []      # ordered list of all reorderable _AttrSection widgets
        self._drag_sec = None         # section currently being dragged in arrange mode

        # Container for coded sections — reordering operates inside here
        self._sections_container = QWidget()
        self._sections_container.setStyleSheet("background:transparent;")
        self._sections_vbox = QVBoxLayout(self._sections_container)
        self._sections_vbox.setContentsMargins(0, 0, 0, 0)
        self._sections_vbox.setSpacing(0)
        vbox.addWidget(self._sections_container)

        for _grp in _group_order_list:
            if _grp == _INTERNAL_GROUP:
                continue
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

            # W / ED / Fix controls appended to whichever group contains CS
            if _has_cs:
                _flags_box = QWidget(); _flags_vb = QVBoxLayout(_flags_box)
                _flags_vb.setContentsMargins(0, 0, 0, 0); _flags_vb.setSpacing(2)
                self._w_check.setVisible(True);  _flags_vb.addWidget(self._w_check)
                self._ed_check.setVisible(True); _flags_vb.addWidget(self._ed_check)
                self._btn_normalize.setVisible(True); _flags_vb.addWidget(self._btn_normalize)
                _sec_lay.addWidget(_flags_box)
                def _clear_flags():
                    for _c in (self._w_check, self._ed_check):
                        _c.blockSignals(True); _c.setChecked(False); _c.blockSignals(False)
                    self._on_name_edit_finished()
                _sec_w.register_clear(_clear_flags)

            _sec_lay.addStretch()

        # Decode label — shows human-readable breakdown of current coded values
        self._decode_lbl = QLabel("")
        self._decode_lbl.setStyleSheet("color:#888; font-size:8pt;")
        self._decode_lbl.setWordWrap(True)
        vbox.addWidget(self._decode_lbl)
        self._decode_field_fn = _decode_field   # store for use in _refresh_attrs

        # X category hint label (kept for X expression category display)
        self._x_hint = QLabel("")
        self._x_hint.setStyleSheet("color:#888; font-size:8pt;")
        vbox.addWidget(self._x_hint)

        # Keep _name_edit as hidden alias so existing code doesn't break
        self._name_edit = self._code_edits.get("b", QLineEdit())

        # Seed row (always shown)
        r5 = QHBoxLayout()
        ls2 = QLabel("Seed:")
        ls2.setStyleSheet("color: #aaa;")
        r5.addWidget(ls2)
        self._seed_edit = QLineEdit()
        self._seed_edit.setFixedWidth(140)
        self._seed_edit.setStyleSheet(
            "background-color: #3a3a3a; color: #e0e0e0; border: 1px solid #555;")
        self._seed_edit.editingFinished.connect(self._save_attrs)
        self._seed_edit.editingFinished.connect(lambda: self._update_bake_btn("pending"))
        r5.addWidget(self._seed_edit)
        r5.addStretch()
        vbox.addLayout(r5)

        # ── Dynamic sections driven by attrs_tags.json ──────────────────────
        self._text_save_timer = QTimer()
        self._text_save_timer.setSingleShot(True)
        self._text_save_timer.setInterval(800)
        self._text_save_timer.timeout.connect(self._save_attrs)
        self._text_save_timer.timeout.connect(lambda: self._update_bake_btn("pending"))

        self._text_edits = {}    # section_key → QTextEdit
        self._attr_select = {}   # section_key → {"combo": QComboBox, "data": [[k,l],...], "freq": bool}
        _proj_tg        = _proj_tg_early   # already loaded above
        _section_order  = _proj_tg.get("__section_order__", [])
        _section_styles = _proj_tg.get("__section_styles__", {})
        _tf_data        = _proj_tg.get("__text_fields__", {})
        if not isinstance(_tf_data, dict):
            _tf_data = {}
        _SKIP_SECS = {"Quality"}   # Quality is already a dropdown at the top

        # Separate sections by render style
        _taglist_secs = []
        _combo_secs   = []   # matrix style → frequency-sorted single-select QComboBox
        _text_secs    = []
        for _sec_key in _section_order:
            if _sec_key in _SKIP_SECS:
                continue
            _style = _section_styles.get(_sec_key, "")
            if _style in ("taglist", "boolean"):
                _grp_data = _proj_tg.get(_sec_key, [])
                if isinstance(_grp_data, list) and _grp_data:
                    _taglist_secs.append((_sec_key, _grp_data))
            elif _style == "matrix":
                _grp_data = _proj_tg.get(_sec_key, [])
                if isinstance(_grp_data, list) and _grp_data:
                    _combo_secs.append((_sec_key, _grp_data))
            elif _style == "text":
                _text_secs.append(_sec_key)

        # ── Tag group sections — frequency-sorted toggle buttons ─────────────
        _tag_usage = self._load_tag_usage()
        _BTN_SS_OFF = (
            "QPushButton { background:#333; color:#bbb; border:1px solid #555;"
            " padding:2px 6px; border-radius:3px; font-size:8pt; }"
            "QPushButton:hover { background:#444; }")
        _BTN_SS_ON  = (
            "QPushButton { background:#4a7a4e; color:#e8ffe8; border:1px solid #6aaa6e;"
            " padding:2px 6px; border-radius:3px; font-size:8pt; font-weight:bold; }"
            "QPushButton:hover { background:#5a8a5e; }")
        for _sec_key, _grp_data in _taglist_secs:
            _sec_name = _sec_key.replace("_", " ").title()
            _ts_sec = _AttrSection(_sec_name)
            self._sections_vbox.addWidget(_ts_sec)
            self._attr_sections.append(_ts_sec)
            _ts_grid = QGridLayout(_ts_sec.content)
            _ts_grid.setContentsMargins(4, 2, 4, 4)
            _ts_grid.setSpacing(3)
            # Sort by usage count descending, stable (preserves definition order for ties)
            _sorted_tags = sorted(_grp_data, key=lambda kv: -_tag_usage.get(kv[0], 0))
            _COLS = 6
            for _ti, (_key, _label) in enumerate(_sorted_tags):
                _tbtn = QPushButton(_label)
                _tbtn.setCheckable(True)
                _tbtn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                _tbtn.setStyleSheet(_BTN_SS_OFF)
                def _on_tag_toggle(checked, k=_key, b=_tbtn,
                                   on_ss=_BTN_SS_ON, off_ss=_BTN_SS_OFF):
                    b.setStyleSheet(on_ss if checked else off_ss)
                    if checked:
                        self._increment_tag_usage(k)
                    self._save_attrs()
                _tbtn.toggled.connect(_on_tag_toggle)
                self._attr_cbs[_key] = _tbtn
                _ts_grid.addWidget(_tbtn, _ti // _COLS, _ti % _COLS)

        # ── Matrix/select sections — frequency-sorted single-select QComboBox ──
        _CB_SS = ("QComboBox { background:#2e2e2e; color:#ddd; border:1px solid #555;"
                  " padding:2px 6px; border-radius:3px; font-size:8pt; }"
                  "QComboBox::drop-down { border:none; }"
                  "QComboBox QAbstractItemView { background:#2e2e2e; color:#ddd;"
                  " selection-background-color:#4a7a4e; }")
        for _sec_key, _grp_data in _combo_secs:
            _sec_name = _sec_key.replace("_", " ").title()
            _ms_sec = _AttrSection(_sec_name)
            self._sections_vbox.addWidget(_ms_sec)
            self._attr_sections.append(_ms_sec)
            _ms_lay = QHBoxLayout(_ms_sec.content)
            _ms_lay.setContentsMargins(4, 2, 4, 4); _ms_lay.setSpacing(6)

            _cb = QComboBox()
            _cb.setStyleSheet(_CB_SS)
            _cb.setMinimumWidth(160)
            _cb.addItem("—", "")   # blank / no selection
            _sorted_data = sorted(_grp_data, key=lambda kv: -_tag_usage.get(kv[0], 0))
            for _k, _lbl in _sorted_data:
                _cb.addItem(_lbl, _k)

            _freq_lbl = QLabel("freq")
            _freq_lbl.setStyleSheet("color:#666; font-size:7pt;")
            _ms_lay.addWidget(_cb)
            _ms_lay.addWidget(_freq_lbl)
            _ms_lay.addStretch()

            self._attr_select[_sec_key] = {"combo": _cb, "data": _grp_data, "freq": True}

            def _on_select(idx, sk=_sec_key, cb=_cb):
                key = cb.currentData()
                if key:
                    self._increment_tag_usage(key)
                    self._rebuild_select_combo(sk)
                self._save_attrs()
            _cb.currentIndexChanged.connect(_on_select)

            # Right-click on combo toggles freq ↔ alpha sort
            _cb.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            def _on_ctx(pos, sk=_sec_key, lbl=_freq_lbl):
                info = self._attr_select.get(sk)
                if not info: return
                info["freq"] = not info["freq"]
                lbl.setText("freq" if info["freq"] else "alpha")
                lbl.setStyleSheet(
                    "color:#666; font-size:7pt;" if info["freq"]
                    else "color:#8ab; font-size:7pt; font-style:italic;")
                self._rebuild_select_combo(sk)
            _cb.customContextMenuRequested.connect(_on_ctx)

            _ms_sec.register_clear(lambda cb=_cb: (cb.blockSignals(True),
                                                    cb.setCurrentIndex(0),
                                                    cb.blockSignals(False)))

        # ── Restore saved section order ───────────────────────────────────────
        _saved_order = self.handler.app.config.get("attr_section_order", [])
        if _saved_order:
            _by_title = {s._title: s for s in self._attr_sections}
            _reordered = [_by_title[t] for t in _saved_order if t in _by_title]
            _remaining = [s for s in self._attr_sections if s not in _reordered]
            _final = _reordered + _remaining
            if _final != self._attr_sections:
                self._attr_sections = _final
                sv = self._sections_vbox
                for s in _final:
                    sv.removeWidget(s)
                for s in _final:
                    sv.addWidget(s)

        # ── Text areas (one per section, with display name label) ────────────
        for _sec_key in _text_secs:
            _tf_info     = _tf_data.get(_sec_key, {})
            _label_str   = _tf_info.get("label", _sec_key.replace("_", " ").title())
            _placeholder = _tf_info.get("placeholder", "")
            _lbl2 = QLabel(f"{_label_str}:")
            _lbl2.setStyleSheet("color: #aaa;")
            vbox.addWidget(_lbl2)
            _te = QTextEdit()
            _te.setMinimumHeight(28)
            _te.setFixedHeight(28)
            _te.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            _te.textChanged.connect(lambda te=_te: _resize_textedit(te))






            _te.setPlaceholderText(_placeholder or f"{_label_str}…")
            _te.setStyleSheet(
                "background-color: #3a3a3a; color: #e0e0e0; border: 1px solid #555;")
            _te.textChanged.connect(self._text_save_timer.start)
            vbox.addWidget(_te)
            self._text_edits[_sec_key] = _te


# ── Raw Metadata Section ──────────────────────────────────────────






        self._raw_meta_sec = _AttrSection("Raw Info")
        vbox.addWidget(self._raw_meta_sec)
        _raw_lay = QVBoxLayout(self._raw_meta_sec.content)
        _raw_lay.setContentsMargins(6, 4, 6, 6)
        
        self._raw_meta_edit = QTextEdit()
        self._raw_meta_edit.setReadOnly(True)
        self._raw_meta_edit.setFixedHeight(120)
        self._raw_meta_edit.setStyleSheet(
            "background-color: #1a1a1a; color: #999; border: 1px solid #333; "
            "font-family: monospace; font-size: 8pt;"
        )
        self._raw_meta_edit.setPlaceholderText("No technical metadata available.")
        _raw_lay.addWidget(self._raw_meta_edit)






        # Bake row: read from file / bake to file
        r_bake = QHBoxLayout()
        self._btn_read_meta = QPushButton("Read from File")
        self._btn_read_meta.setToolTip(
            "Import prompt/seed/model embedded in the physical file into the database")
        self._btn_read_meta.setStyleSheet(
            "background:#3a4a3a; color:#e0e0e0; border:1px solid #556655; padding:2px 6px;")
        self._btn_read_meta.clicked.connect(self._read_file_meta)
        r_bake.addWidget(self._btn_read_meta)
        self._btn_bake_meta = QPushButton("Bake to File")
        self._btn_bake_meta.setToolTip(
            "Embed prompt/seed/model from database into the physical file")
        self._btn_bake_meta.setStyleSheet(
            "background:#3a3a4a; color:#e0e0e0; border:1px solid #556655; padding:2px 6px;")
        self._btn_bake_meta.clicked.connect(self._bake_to_file)
        r_bake.addWidget(self._btn_bake_meta)
        self._bake_btn_state = "idle"   # idle | pending | ok | error
        from PyQt6.QtWidgets import QCheckBox as _QCB
        self._chk_auto_bake = _QCB("Auto-bake")
        self._chk_auto_bake.setToolTip("Automatically bake to file when navigating to next image")
        self._chk_auto_bake.setChecked(self.handler.app.config.get("auto_bake", False))
        def _on_ab_toggle(v):
            self.handler.app.config["auto_bake"] = v
            import aisearch_config as _cfg
            _cfg.save_config(self.handler.app.config,
                             getattr(self.handler.app, "current_project", None))
        self._chk_auto_bake.toggled.connect(_on_ab_toggle)
        r_bake.addWidget(self._chk_auto_bake)
        self._chk_auto_rename = _QCB("Auto-rename")
        self._chk_auto_rename.setToolTip("Rename file to match person ID when baking")
        self._chk_auto_rename.setChecked(self.handler.app.config.get("auto_rename", False))
        def _on_ar_toggle(v):
            self.handler.app.config["auto_rename"] = v
            import aisearch_config as _cfg
            _cfg.save_config(self.handler.app.config,
                             getattr(self.handler.app, "current_project", None))
            # Sync settings dialog checkbox if open
            sv = getattr(self.handler.app, "_settings_win", None)
            if sv and hasattr(sv, "chk_rename_on_scan") and sv.chk_rename_on_scan.isChecked() != v:
                sv.chk_rename_on_scan.blockSignals(True)
                sv.chk_rename_on_scan.setChecked(v)
                sv.chk_rename_on_scan.blockSignals(False)
            # Sync btn_auto_rename visibility
            if hasattr(self, "_btn_auto_rename"):
                self._btn_auto_rename.setVisible(v)
        self._chk_auto_rename.toggled.connect(_on_ar_toggle)
        r_bake.addWidget(self._chk_auto_rename)
        self._bake_err_label = QLabel("")
        self._bake_err_label.setStyleSheet("color:#ff6666; font-size:10px;")
        self._bake_err_label.setWordWrap(True)
        r_bake.addWidget(self._bake_err_label, stretch=1)
        vbox.addLayout(r_bake)

        self._attr_path = None
        return panel

    def _attr_arrow(self, open_state):
        """Return the correct arrow for open/closed state based on splitter orientation."""
        horiz = (self._splitter.orientation() == Qt.Orientation.Horizontal)
        if horiz:
            return "►" if open_state else "◄"
        else:
            return "▼" if open_state else "▲"

    def _sync_toggle_strip(self):
        """Show the correct toggle strip and update btn_toggle_attrs reference."""
        horiz = (self._splitter.orientation() == Qt.Orientation.Horizontal)
        self._left_bar.setVisible(horiz)
        self._top_bar.setVisible(not horiz)
        is_open = self._attr_scroll.isVisible()
        if horiz:
            self.btn_toggle_attrs = self._btn_toggle_left
            self._btn_toggle_left.setText(self._attr_arrow(is_open))
        else:
            self.btn_toggle_attrs = self._btn_toggle_top
            self._btn_toggle_top.setText(self._attr_arrow(is_open))

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
            # Expanded → collapse
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

    def _refresh_attrs(self, path):
        if not self._attr_panel_built:
            self._attr_panel_pending_path = path
            return
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

    def _refresh_attrs_inner(self, path):
        app = self.handler.app
        # Auto-bake previous file when navigating to a different one
        if self._attr_path and self._attr_path != path:
            if getattr(self, '_chk_auto_bake', None) and self._chk_auto_bake.isChecked():
                self._bake_to_file(silent=True)
        self._attr_path = path
        self._update_bake_btn("idle")
        self._btn_detect_person.setText("Detect & Register")
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
        entry = attrs_mod.get(self.handler.app.attrs_data, path)
        tags  = set(entry.get("tags", []))

        for cb in self._attr_cbs.values():
            cb.blockSignals(True)
        self._quality_combo.blockSignals(True)
        self._protected_check.blockSignals(True)

        qual = next((k for k in attrs_mod.QUALITY_TAGS if k in tags), "")
        self._quality_combo.setCurrentIndex(max(0, self._quality_combo.findData(qual)))
        for key, cb in self._attr_cbs.items():
            cb.setChecked(key in tags)
        self._protected_check.setChecked(not bool(entry.get("editable", False)))

        self._quality_combo.blockSignals(False)
        for cb in self._attr_cbs.values():
            cb.blockSignals(False)
        self._protected_check.blockSignals(False)

        # Populate matrix-style select combos
        for _sk, _info in self._attr_select.items():
            _scb = _info["combo"]
            _sec_keys = {kv[0] for kv in _info["data"]}
            _selected = next((t for t in tags if t in _sec_keys), "")
            _scb.blockSignals(True)
            _scb.setCurrentIndex(max(0, _scb.findData(_selected)))
            _scb.blockSignals(False)

        # Sync tag-button styles (signals were blocked during setChecked)
        _BTN_ON  = ("QPushButton { background:#4a7a4e; color:#e8ffe8; border:1px solid #6aaa6e;"
                    " padding:2px 6px; border-radius:3px; font-size:8pt; font-weight:bold; }"
                    "QPushButton:hover { background:#5a8a5e; }")
        _BTN_OFF = ("QPushButton { background:#333; color:#bbb; border:1px solid #555;"
                    " padding:2px 6px; border-radius:3px; font-size:8pt; }"
                    "QPushButton:hover { background:#444; }")
        for cb in self._attr_cbs.values():
            if isinstance(cb, QPushButton):
                cb.setStyleSheet(_BTN_ON if cb.isChecked() else _BTN_OFF)

        # Lock/unlock all editable widgets based on editable state
        _locked = not bool(entry.get("editable", False))
        self._apply_protected_lock(_locked)
        self._btn_auto_rename.setEnabled(not _locked)







        # Person fields — try parsing coded filename first, fall back to saved person_id
        stem    = os.path.splitext(os.path.basename(path))[0]
        parsed  = attrs_mod.parse_coded_filename(stem)
        persons = parsed.get("persons", []) if parsed else []
        if not persons:
            saved = entry.get("person_id", "")
            persons = [saved] if saved else []
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

        # Populate coded fields from parsed filename — skip collapsed sections
        _fts = getattr(self, "_field_to_section", {})
        if parsed:
            for letter, _, digits in attrs_mod.CODED_FIELDS:
                _sec = _fts.get(letter.lower())
                if _sec and not _sec.is_expanded():
                    continue   # section is collapsed — will refresh when opened
                fe = self._code_edits.get(letter.lower())
                if fe is None:
                    continue
                val = parsed.get(letter.lower(), "")
                if digits == 0:
                    fe.blockSignals(True)
                    fe.setChecked(bool(val))
                    fe.blockSignals(False)
                else:
                    fe.setText(val)
                    # Sync combo boxes (if this field uses them)
                    self._set_field_combos(letter.lower(), val)
        # J field: decode base-36 → yymmdd date string
        fe_j = self._code_edits.get("j")
        if fe_j and parsed:
            j_val = parsed.get("j", "")
            if j_val:
                decoded = attrs_mod.julian_id_to_date(j_val)
                fe_j.setText(decoded)
                fe_j.setToolTip(f"Julian: {j_val} → {decoded[:2]}-{decoded[2:4]}-{decoded[4:]}")
            else:
                fe_j.setText("")

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
        rules = attrs_mod.load_filename_rules()
        base  = self._filename_base(stem, rules)
        self._name_edit.setText(base)
        self._name_edit.setCursorPosition(len(base))
        self._project_edit.setText(entry.get("note", ""))
        self._project_edit.blockSignals(False)
        self._seed_edit.setText(entry.get("seed", ""))
        self._seed_edit.blockSignals(False)
        _saved_pid = entry.get("person_id", "")
        self._person_id_combo.blockSignals(True)
        if _saved_pid:
            _idx = self._person_id_combo.findData(_saved_pid)
            if _idx >= 0:
                self._person_id_combo.setCurrentIndex(_idx)
            else:
                self._person_id_combo.setCurrentText(_saved_pid)
        self._person_id_combo.blockSignals(False)
        for _sec_key, _te in self._text_edits.items():
            _db_key = _TEXT_KEY_MAP.get(_sec_key, _sec_key)
            _te.blockSignals(True)
            _te.setPlainText(entry.get(_db_key, ""))
            _te.blockSignals(False)
            # Defer resize: document layout isn't computed until next event loop tick
            QTimer.singleShot(0, lambda te=_te: _resize_textedit(te))

        # Hide the hardcoded Note row when the project defines "note" as a text field
        _note_covered = any(
            _TEXT_KEY_MAP.get(k, k) == "note" for k in self._text_edits
        )
        self._note_row_widget.setVisible(not _note_covered)

        # Auto-read embedded file metadata if db has no prompt/seed/speech yet
        _needs_read = (not entry.get("prompt") and not entry.get("neg_prompt")
                       and not entry.get("speech") and not entry.get("seed"))
        if _needs_read:
            def _auto_read(p=path):
                data = _read_embedded_meta(p)
                if not data:
                    return
                def _apply(p=p, data=data):
                    if self._attr_path != p:
                        return
                    _rev_map = {v: k for k, v in _TEXT_KEY_MAP.items()}
                    _changed = False
                    for _db_key in ("prompt", "neg_prompt", "speech"):
                        if data.get(_db_key):
                            _te = self._text_edits.get(_rev_map.get(_db_key, _db_key))
                            if _te:
                                _te.blockSignals(True)
                                _te.setPlainText(data[_db_key])
                                _te.blockSignals(False)
                                QTimer.singleShot(0, lambda te=_te: _resize_textedit(te))
                                _changed = True
                    if data.get("seed"):
                        self._seed_edit.setText(str(data["seed"]))
                        _changed = True
                    if _changed:
# ────────── MODIFICATION START ──────────
                        # 1. Force the internal state to Editable (Unchecked)
                        # We block signals to prevent redundant recursive saves during setup
                        self._protected_check.blockSignals(True)
                        self._protected_check.setChecked(False)
                        self._protected_check.blockSignals(False)

                        # 2. FORCE visual update of the Lock/Unlock icons and colors
                        # This triggers the text change to "🔓 Editable" and green color
                        self._apply_protected_lock(False)

                        # 3. Commit to database so it stays unlocked
                        self._save_attrs()
                        # ─────────── MODIFICATION END ───────────

                        self._update_bake_btn("ok")   # already baked — file was the source
                QTimer.singleShot(0, _apply)
            threading.Thread(target=_auto_read, daemon=True).start()


        # Info box — use cached meta only; background thread fills it in if missing
        meta = entry.get("meta", {})
        if meta:




            parts = [v for k, v in meta.items()
                     if k in ("Dimensions", "Ratio", "File size", "FPS", "Duration", "Audio")]
            if meta.get("Seed"):
                parts.append(f"seed:{meta['Seed']}")
            self._info_label.setText("  ·  ".join(parts))
# --- NEW: Update Raw Metadata Text Box ---
            try:
                raw_text = json.dumps(meta, indent=4, ensure_ascii=False)
                self._raw_meta_edit.setPlainText(raw_text)
            except Exception:
                self._raw_meta_edit.setPlainText(str(meta))
            # -----------------------------------------



            # Fill seed/prompt fields from meta if top-level attrs are empty
            if not self._seed_edit.text() and meta.get("Seed"):
                self._seed_edit.blockSignals(True)
                self._seed_edit.setText(str(meta["Seed"]))
                self._seed_edit.blockSignals(False)
            _rev_map = {v: k for k, v in _TEXT_KEY_MAP.items()}
            for meta_key, db_key in (("Prompt", "prompt"), ("NegPrompt", "neg_prompt")):
                if meta.get(meta_key):
                    _sec_key = _rev_map.get(db_key)
                    _te = self._text_edits.get(_sec_key) if _sec_key else None
                    if _te and not _te.toPlainText():
                        _te.blockSignals(True)
                        _te.setPlainText(meta[meta_key])
                        _te.blockSignals(False)
        else:
            self._info_label.setText("")

        # Sync person_id: try one-way detection rules first, then coded filename parse
        if not entry.get("person_id"):
            stem_sync = os.path.splitext(os.path.basename(path))[0]
            fn_rules  = attrs_mod.load_filename_rules()
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
# ────────── MODIFICATION START ──────────
                                   speech=entry.get("speech", ""),
                                   # This was missing! Without it, set_file overwrites the state to Locked (False)
                                   editable=entry.get("editable", True) 
                                   # ─────────── MODIFICATION END ───────────   

                                   )
                attrs_mod.save(app.current_project, app.attrs_data)

        # Auto-detect tags (resolution, audio, AI source) in background if incomplete.
        # NOTE: MediaPipe (shot/pose) is intentionally skipped here — use the Scan buttons.
        needs_res    = not any(t in attrs_mod.RESOLUTION_TAGS for t in tags)
        needs_src    = not any(t in attrs_mod.SOURCE_TAGS for t in tags)
        needs_meta   = not entry.get("meta")
        needs_person = False  # face matching is manual-only (use Detect & Register button)
        if needs_res or needs_src or needs_meta or needs_person:
            def _detect(p=path, _needs_res=needs_res, _needs_src=needs_src,
                        _needs_meta=needs_meta, _needs_person=needs_person):
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
                    if new_prompt and not _entry.get("prompt"):
                        _entry["prompt"] = new_prompt; _changed = True
                    if new_seed and not _entry.get("seed"):
                        _entry["seed"] = new_seed; _changed = True
                if _needs_meta:
                    meta = attrs_mod.extract_metadata(p)
                    if meta:
                        _entry["meta"] = meta; _changed = True
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
                    _entry["tags"] = _tags
                    app.attrs_data[p] = _entry
                    attrs_mod.save(app.current_project, app.attrs_data)
                def _safe_refresh(p=p, pid=_matched_pid):
                    if self._attr_path != p:
                        return
                    _app = self.handler.app
                    _entry2 = attrs_mod.get(_app.attrs_data, p)
                    for _sec_key, _widget in self._text_edits.items():
                        _db_key = _TEXT_KEY_MAP.get(_sec_key, _sec_key)
                        typed = _widget.toPlainText()
                        if typed and not _entry2.get(_db_key):
                            _entry2[_db_key] = typed
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

    def _on_auto_rename(self):
        """Detect person ID + pose/shot, then rename file to canonical form."""
        path = self._attr_path
        if not path:
            return
        if not attrs_mod.is_editable(self.handler.app.attrs_data, path):
            return
        app = self.handler.app
        self._btn_auto_rename.setEnabled(False)
        self._btn_auto_rename.setText("…")

        new_base = ""  # original name discarded — final name is just P000/P001 etc.

        # Results stored here by background thread
        _result = [None, None, None]  # [pid, shot_tag, pose_tag]
        _done   = threading.Event()

        def _run():
            try:
                _result[0] = attrs_mod.detect_or_assign_person_id(path, app.current_project)
            except Exception:
                pass
            try:
                _result[1], _result[2] = attrs_mod.detect_shot_and_pose(path)
            except Exception:
                pass
            _done.set()

        def _apply():
            # Always restore button first
            self._btn_auto_rename.setEnabled(True)
            self._btn_auto_rename.setText("Change File Name")

            pid, shot_tag, pose_tag = _result
            pid = pid or "000"   # no face detected / video → P000

            # Update person ID field
            if hasattr(self, '_person_id_label'):
                self._person_id_label.setText(pid)

            # Build tags from current checkboxes + combos
            tags = [k for k, cb in self._attr_cbs.items() if cb.isChecked()]
            qual = self._quality_combo.currentData()
            if qual: tags.append(qual)
            for _attr in ('_hair_combo', '_camera_angle_combo', '_posture_combo',
                          '_action_combo', '_eye_color_combo', '_skin_type_combo'):
                _cb = getattr(self, _attr, None)
                if _cb:
                    _v = _cb.currentData()
                    if _v: tags.append(_v)
            if pose_tag and pose_tag not in tags: tags.append(pose_tag)
            if shot_tag and shot_tag not in tags: tags.append(shot_tag)

            # Build new filename
            pose_suf = f"-{pose_tag}" if pose_tag else ""
            wm_suf   = "-watermark" if "watermark" in tags else ""
            fp       = attrs_mod.file_fingerprint(path) or ""
            fp_suf   = f"-{fp}" if fp else ""
            pid_str  = pid   # always set ("000" or "001" etc.)
            j_code   = attrs_mod.julian_id_for_file(path)
            _, ext   = os.path.splitext(path)
            new_stem = f"P{pid_str}J{j_code}" + pose_suf + wm_suf + fp_suf
            new_path = attrs_mod.unique_path(
                os.path.join(os.path.dirname(path), new_stem + ext))

            if new_path != path:
                try:
                    os.rename(path, new_path)
                except Exception as e:
                    from PyQt6.QtWidgets import QMessageBox
                    QMessageBox.critical(self.handler.window, "Rename Error", str(e))
                    return
                attrs_mod.update_path_in_all_stores(path, new_path, app.current_project)
                if app.data and "paths" in app.data and path in app.data["paths"]:
                    idx2 = app.data["paths"].index(path)
                    app.data["paths"][idx2] = new_path
                    torch.save(app.data, f"features_{app.current_project}.pt")
                if path in app.attrs_data:
                    app.attrs_data[new_path] = app.attrs_data.pop(path)
                row = app._current_row()
                if row >= 0:
                    app.table.item(row, 2).setText(os.path.basename(new_path))
                    app.table.set_row_path(row, new_path)
                self._attr_path = new_path
                self.handler.current_path = new_path
                self.handler.window.setWindowTitle(
                    f"{app._mask_path(new_path)}/{os.path.basename(new_path)}")

            entry = attrs_mod.get(app.attrs_data, new_path)
            attrs_mod.set_file(app.attrs_data, new_path,
                               tags=tags,
                               note=entry.get("note", ""),
                               confirmed=entry.get("confirmed", False),
                               project=entry.get("project", ""),
                               scene=entry.get("scene", ""),
                               prompt=entry.get("prompt", ""),
                               seed=entry.get("seed", ""),
                               meta=entry.get("meta"),
                               custom=entry.get("custom", ""),
                               person_id=pid_str,
                               editable=entry.get("editable", True))
            attrs_mod.save(app.current_project, app.attrs_data)
            rules = attrs_mod.load_filename_rules()
            stem  = os.path.splitext(os.path.basename(new_path))[0]
            self._name_edit.setText(attrs_mod._extract_filename_base(stem, rules))

        def _poll():
            if _done.is_set():
                _apply()
            else:
                QTimer.singleShot(100, _poll)

        threading.Thread(target=_run, daemon=True).start()
        QTimer.singleShot(100, _poll)  # started from main thread — reliable

    def _refresh_person_id_combo(self, force=False):
        """Populate the person ID combo — text immediately, thumbnails async."""
        app = self.handler.app
        db = attrs_mod.load_faces_db(app.current_project)
        faces = db.get("faces", {})
        registry = attrs_mod.load_person_registry()

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

    # ── Arrange mode ─────────────────────────────────────────────────────────

    def _toggle_arrange_mode(self, on: bool):
        """Show/hide drag handles on all reorderable sections."""
        self._drag_sec = None
        for sec in getattr(self, '_attr_sections', []):
            sec._drag_handle.setVisible(on)
            if on:
                sec._drag_handle.installEventFilter(self)
            else:
                sec._drag_handle.removeEventFilter(self)

    def _do_drag_move(self, global_y: int):
        """Reorder sections live while the user drags a section header."""
        sec = getattr(self, '_drag_sec', None)
        if sec is None:
            return
        secs = self._attr_sections
        cur_idx = secs.index(sec)
        from PyQt6.QtCore import QPoint
        local_y = self._sections_container.mapFromGlobal(QPoint(0, global_y)).y()
        # Count how many other sections have their centre above local_y
        new_idx = sum(
            1 for s in secs
            if s is not sec and s.geometry().center().y() < local_y
        )
        if new_idx == cur_idx:
            return
        secs.pop(cur_idx)
        secs.insert(new_idx, sec)
        sv = self._sections_vbox
        for s in secs:
            sv.removeWidget(s)
        for s in secs:
            sv.addWidget(s)

    def _save_section_order(self):
        """Persist current section order to config."""
        self.handler.app.config["attr_section_order"] = [
            s._title for s in self._attr_sections
        ]
        import aisearch_config as _cfg
        _cfg.save_config(self.handler.app.config,
                         getattr(self.handler.app, "current_project", None))

    # ── Tag usage tracking ────────────────────────────────────────────────────

    def _tag_usage_path(self):
        proj = getattr(self.handler.app, "current_project", "default")
        return f"tag_usage_{proj}.json"

    def _load_tag_usage(self):
        p = self._tag_usage_path()
        try:
            mtime = os.path.getmtime(p)
            cache = getattr(self, "_tag_usage_cache", (None, None))
            if cache[0] == mtime:
                return cache[1]
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            self._tag_usage_cache = (mtime, data)
            return data
        except Exception:
            return {}

    def _increment_tag_usage(self, key: str):
        p = self._tag_usage_path()
        usage = self._load_tag_usage()
        usage[key] = usage.get(key, 0) + 1
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(usage, f, ensure_ascii=False)
        except Exception:
            pass

    def _rebuild_select_combo(self, sec_key):
        """Re-sort a matrix-style QComboBox by freq or alpha, preserving current selection."""
        info = self._attr_select.get(sec_key)
        if not info:
            return
        cb   = info["combo"]
        data = info["data"]
        freq = info["freq"]
        cur  = cb.currentData()
        usage = self._load_tag_usage()
        if freq:
            sorted_data = sorted(data, key=lambda kv: (-usage.get(kv[0], 0), kv[1]))
        else:
            sorted_data = sorted(data, key=lambda kv: kv[1])
        cb.blockSignals(True)
        cb.clear()
        cb.addItem("—", "")
        for k, lbl in sorted_data:
            cb.addItem(lbl, k)
        idx = cb.findData(cur)
        cb.setCurrentIndex(max(0, idx))
        cb.blockSignals(False)

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
        registry = attrs_mod.load_person_registry()
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
                    self._btn_detect_person.setText("Registered")
                    self._btn_detect_person.setStyleSheet(
                        "background:#2a6a2a; color:#aaffaa; border:1px solid #44aa44;"
                        " padding:3px 10px; font-weight:bold;")
                else:
                    self._btn_detect_person.setText("Detect & Register")
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
                                       person_id=pid)
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
        """Update the X hint label with expression name and description."""
        code = text.strip().lower()
        if len(code) == 2:
            en, jp = attrs_mod.expression_label(code)
            if en:
                cat = attrs_mod.expression_category(code)
                self._x_hint.setText(f"{en} {jp}  —  {cat}")
            else:
                self._x_hint.setText(attrs_mod.expression_category(code))
        elif len(code) == 1:
            try:
                cat = attrs_mod.EXPRESSION_CATEGORIES.get(int(code, 16), "")
                self._x_hint.setText(cat)
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
                           person_id=new_pid)
        attrs_mod.save(app.current_project, app.attrs_data)
        name_label = attrs_mod.get_person_id_label(app.current_project, new_pid)
        self._person_name_edit.setText(name_label if name_label != new_pid else "")

    def _on_pw_changed(self):
        """User manually edited a PW field — trigger filename normalize if auto-rename is on."""
        if not self._attr_path:
            return
        app = self.handler.app
        if app.config.get("auto_rename", False):
            self._on_normalize_filename()

    def _on_person_name_changed(self):
        pid = self._person_id_label.text()
        if not pid or pid == "—":
            return
        name = self._person_name_edit.text().strip()
        attrs_mod.set_person_name(self.handler.app.current_project, pid, name)

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
        for letter, _, digits in attrs_mod.CODED_FIELDS:
            fe = self._code_edits.get(letter.lower())
            if fe is None:
                continue
            if digits == 0:
                parts[letter.lower()] = "1" if fe.isChecked() else ""
            else:
                parts[letter.lower()] = fe.text().strip().lower()
        new_stem = attrs_mod.build_coded_filename(parts)
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
            QMessageBox.critical(self.handler.window, "Rename Error", str(e))
            return
        # Update all stores
        attrs_mod.update_path_in_all_stores(path, new_path, app.current_project)
        if app.data and "paths" in app.data and path in app.data["paths"]:
            idx = app.data["paths"].index(path)
            app.data["paths"][idx] = new_path
            torch.save(app.data, f"features_{app.current_project}.pt")
        if path in app.attrs_data:
            app.attrs_data[new_path] = app.attrs_data.pop(path)
        row = app._current_row()
        if row >= 0:
            app.table.item(row, 2).setText(os.path.basename(new_path))
            app.table.set_row_path(row, new_path)
        self._attr_path = new_path
        self.handler.current_path = new_path
        self.handler.window.setWindowTitle(
            f"{app._mask_path(new_path)}/{os.path.basename(new_path)}")
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
            QMessageBox.critical(self, "Rename Error", str(e))
            self._name_edit.setText(old_stem)
            return
        app = self.handler.app
        attrs_mod.update_path_in_all_stores(path, new_path, app.current_project)
        # Update .pt database
        if app.data and "paths" in app.data and path in app.data["paths"]:
            idx = app.data["paths"].index(path)
            app.data["paths"][idx] = new_path
            torch.save(app.data, f"features_{app.current_project}.pt")
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
        self.handler.current_path = new_path
        self.handler.window.setWindowTitle(
            f"{app._mask_path(new_path)}/{os.path.basename(new_path)}")

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
                torch.save(app.data, f"features_{app.current_project}.pt")
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
            self.handler.current_path = new_path
            self.handler.window.setWindowTitle(
                f"{app._mask_path(new_path)}/{os.path.basename(new_path)}")
        self._save_attrs()

    def _apply_protected_lock(self, locked):
        """Disable all attribute editing widgets when file is protected."""
        # Collect all editable widgets in the attr panel (except the lock checkbox itself)
        editables = []
        editables += list(self._text_edits.values())
        editables += list(self._attr_cbs.values())
        editables.append(self._quality_combo)
        editables.append(self._seed_edit)
        editables.append(self._project_edit)
        editables.append(self._name_edit)
        editables.append(self._person_id_combo)
        editables.append(self._person_name_edit)
        editables.append(self._btn_detect_person)
        editables.append(self._btn_bake_meta)
        editables.append(self._btn_read_meta)
        for w in editables:
            w.setEnabled(not locked)
        for cb_list in getattr(self, '_code_combos', {}).values():
            for _, _, cb in cb_list:
                cb.setEnabled(not locked)
        self._btn_auto_rename.setEnabled(not locked)




        if locked:
            self._protected_check.setText("🔒 Locked")
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
        self._update_bake_btn("pending")
        app = self.handler.app
        tags = [k for k, cb in self._attr_cbs.items() if cb.isChecked()]
        qual = self._quality_combo.currentData()
        if qual: tags.append(qual)
        for _info in self._attr_select.values():
            _v = _info["combo"].currentData()
            if _v: tags.append(_v)
        entry = attrs_mod.get(app.attrs_data, path)
        persons = [_norm_pid(pe.text().strip()) for pe in self._p_edits if pe.text().strip()]
        if not persons:
            _combo_fid = self._person_id_combo.currentData()
            if _combo_fid:
                persons = [_norm_pid(_combo_fid)]
        _text_vals = {_TEXT_KEY_MAP.get(k, k): te.toPlainText()
                      for k, te in self._text_edits.items()}
        attrs_mod.set_file(app.attrs_data, path,
                           tags=tags,
                           note=self._project_edit.text() if self._note_row_widget.isVisible() else entry.get("note", ""),
                           confirmed=entry.get("confirmed", False),
                           project=entry.get("project", ""),
                           scene=entry.get("scene", ""),
                           prompt=_text_vals.get("prompt", ""),
                           neg_prompt=_text_vals.get("neg_prompt", ""),
                           seed=self._seed_edit.text(),
                           meta=entry.get("meta"),
                           custom=entry.get("custom", ""),
                           person_id=persons[0] if persons else "",
                           speech=_text_vals.get("speech", ""),
                           editable=not self._protected_check.isChecked())
        attrs_mod.save(app.current_project, app.attrs_data)
        row = app._current_row()
        if row >= 0:
            app._refresh_attrs_indicator(row, path)
        app._highlight_unmarked_rows()
        if app.btn_hide_confirmed.isChecked():
            app._apply_confirmed_filter(True)
        # Sync main window inline panel if visible
        if hasattr(app, '_inline_attr_path') and app._inline_attr_path == path:
            app._refresh_inline_attrs(path)

    def _read_file_meta(self):
        """Read embedded metadata from the physical file and populate the attr panel."""
        path = self._attr_path
        if not path or not os.path.exists(path): return
        data = _read_embedded_meta(path)
        if not data:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Read Metadata", "No embedded metadata found in file.")
            return
        # Populate fields (don't overwrite if already filled unless user confirms)
        _rev_map = {v: k for k, v in _TEXT_KEY_MAP.items()}
        for _db_key in ("prompt", "neg_prompt", "speech"):
            if data.get(_db_key):
                _te = self._text_edits.get(_rev_map.get(_db_key, _db_key))
                if _te:
                    _te.setPlainText(data[_db_key])
        if data.get("seed"):
            self._seed_edit.setText(str(data["seed"]))
        if data.get("model"):
            # Store in custom field via attrs
            app = self.handler.app
            entry = attrs_mod.get(app.attrs_data, path)
            attrs_mod.set_file(app.attrs_data, path, custom=data["model"],
                               **{k: entry.get(k, "") for k in
                                  ("tags","note","confirmed","project","scene",
                                   "prompt","neg_prompt","seed","speech","person_id")})

# ────────── MODIFICATION START ──────────
        # Imitate a physical mouse click!
        # If the button is locked (Checked = True), unchecking it automatically
        # fires the 'toggled' signals, perfectly triggering the UI unlock and database save.

        self._protected_check.setChecked(False) 
  
            # If it was already unlocked, we still need to save the newly read text
        self._save_attrs()
        # ─────────── MODIFICATION END ───────────



        self._save_attrs()

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

    def _bake_to_file(self, silent=False):
        """Embed current prompt/seed/model/speech into the physical file."""
        path = self._attr_path
        if not path or not os.path.exists(path): return
        app = self.handler.app
        entry = attrs_mod.get(app.attrs_data, path)
        _rev_map = {v: k for k, v in _TEXT_KEY_MAP.items()}
        persons = [pe.text().strip() for pe in self._p_edits if pe.text().strip()]
        data = {
            "prompt":     (self._text_edits[_rev_map["prompt"]].toPlainText().strip()
                           if _rev_map.get("prompt") in self._text_edits else ""),
            "neg_prompt": (self._text_edits[_rev_map["neg_prompt"]].toPlainText().strip()
                           if _rev_map.get("neg_prompt") in self._text_edits else ""),
            "seed":       self._seed_edit.text().strip(),
            "model":      entry.get("custom", ""),
            "speech":     (self._text_edits[_rev_map["speech"]].toPlainText().strip()
                           if _rev_map.get("speech") in self._text_edits else ""),
            "person_id":  persons[0] if persons else "",
        }
        # Only bake if there's something worth writing
        if not any(data.get(k) for k in ("prompt", "neg_prompt", "seed", "speech", "person_id")):
            self._update_bake_btn("idle")
            return
        ok, err = _bake_embedded_meta(path, data)
        if ok:
            if not silent:
                self._save_attrs()   # persist person_id + other fields to JSON on manual bake
                # Auto-rename if enabled and person_id is set
                pid = data.get("person_id", "").strip()
                if pid and self._chk_auto_rename.isChecked():
                    new_path = attrs_mod.rename_with_person_id(
                        app.attrs_data, path, pid,
                        flush_stores=True,
                        project=app.current_project,
                        skip_uncoded=False)
                    if new_path != path:
                        # Update in-memory feature store
                        if (app.data and "paths" in app.data
                                and path in app.data["paths"]):
                            idx2 = app.data["paths"].index(path)
                            app.data["paths"][idx2] = new_path
                        # Update main table row
                        for row in range(app.table.rowCount()):
                            if app.table.get_row_path(row) == path:
                                app.table.set_row_path(row, new_path)
                                name_item = app.table.item(row, 2)
                                if name_item:
                                    name_item.setText(os.path.basename(new_path))
                                break
                        # Update preview state
                        self._attr_path = new_path
                        self.handler.current_path = new_path
            self._update_bake_btn("ok")
            self._bake_err_label.setText("")
            QTimer.singleShot(2000, lambda: self._update_bake_btn("idle"))
        else:
            if not silent:
                self._update_bake_btn("error")
                self._bake_err_label.setText(str(err))
                QMessageBox.critical(self, "Bake Failed", str(err))

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

        is_video = path.lower().endswith(('.mp4', '.mkv', '.mov', '.avi', '.webm'))
        rel_dir  = self.app._mask_path(path)
        name     = os.path.basename(path)
        self.window.setWindowTitle(f"{rel_dir}/{name}" if rel_dir and rel_dir != "." else name)

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
            QMessageBox.critical(self.window, "Copy Error", str(e))

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
            QMessageBox.critical(self.window, "Move Error", str(e)); return

        attrs_mod.update_path_in_all_stores(old_path, final_path, self.app.current_project)
        if self.app.data and "paths" in self.app.data:
            if overwrite: front_page._remove_from_data(self.app.data, dest_path)
            paths = self.app.data["paths"]
            if old_path in paths:
                paths[paths.index(old_path)] = final_path
                torch.save(self.app.data, f"features_{self.app.current_project}.pt")

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
            QMessageBox.critical(self.window, "Delete Error", str(e)); return

        if self.app.data and "paths" in self.app.data:
            norm = os.path.normpath(path)
            idx  = next((i for i, x in enumerate(self.app.data["paths"]) if os.path.normpath(x) == norm), None)
            if idx is not None:
                keep = [i for i in range(len(self.app.data["paths"])) if i != idx]
                self.app.data["paths"]      = [self.app.data["paths"][i] for i in keep]
                self.app.data["embeddings"] = self.app.data["embeddings"][keep]
                torch.save(self.app.data, f"features_{self.app.current_project}.pt")

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
        torch.save(self.app.data, f"features_{self.app.current_project}.pt")
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
        except Exception as e:
            if self.window:
                self.window.label.setText(f"Render Error: {e}")
        finally:
            if self.window:
                self.window.btn_toggle_attrs.setText(
                    self.window._attr_arrow(self.window._attr_scroll.isVisible()))
