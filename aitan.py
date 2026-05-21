import os, sys, importlib.util

# faulthandler prints a Python stack trace if a fatal C-level error
# (segfault, abort, etc.) takes the process down. Without this, native
# crashes from mediapipe / face_recognition / cv2 just show "Killed"
# in the shell with no clue where it died. Output goes to stderr by
# default — your terminal log will capture it before the crash.
import faulthandler
faulthandler.enable()

# Silence specific noisy warnings that don't reflect any real problem:
# - PIL's palette-PNG nag fires once per loaded palette image (icons,
#   thumbnails) and floods stderr; the images decode correctly anyway.
# - face_recognition still uses the deprecated pkg_resources API; we can't
#   fix that from our side, so just hide the deprecation chatter.
import warnings
warnings.filterwarnings("ignore", message=".*Palette images with Transparency.*")
warnings.filterwarnings("ignore", message=".*pkg_resources is deprecated.*")

os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Suppress libavformat/libwebp warnings about malformed EXIF segments in
# user files ("invalid TIFF header in Exif data"). They flood stderr but
# are harmless — the rest of the file decodes fine without the bad EXIF.
# AV_LOG_LEVEL=panic (8) silences everything below FATAL in ffmpeg.
os.environ.setdefault("AV_LOG_LEVEL", "panic")
os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "8")
try:
    import av
    av.logging.set_level(av.logging.PANIC)
except Exception:
    pass

# Linux: cv2 ships its own Qt plugins that conflict with PyQt6 — point Qt to the right ones
if sys.platform == "linux":
    _spec = importlib.util.find_spec("PyQt6")
    if _spec:
        _plugins = os.path.join(os.path.dirname(_spec.origin), "Qt6", "plugins", "platforms")
        if os.path.exists(_plugins):
            os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = _plugins

from PyQt6.QtWidgets import QApplication, QComboBox
from PyQt6.QtGui import QIcon, QImageReader
from aisearch_app import AISearchApp

# Disable scroll-wheel on all combo boxes app-wide
QComboBox.wheelEvent = lambda self, e: e.ignore()

# Raise Qt's per-image allocation cap (default 256 MB is too low for large AI images)
QImageReader.setAllocationLimit(0)

DARK_STYLE = """
QMainWindow, QWidget, QDialog {
    background-color: #212529;
    color: #f8f9fa;
}
QFrame {
    background-color: #212529;
}
QTableWidget {
    background-color: #2b3035;
    color: #f8f9fa;
    gridline-color: #495057;
    alternate-background-color: #343a40;
    selection-background-color: #495057;
    border: none;
}
QHeaderView::section {
    background-color: #343a40;
    color: #f8f9fa;
    border: 1px solid #495057;
    padding: 4px;
}
QPushButton {
    background-color: #6c757d;
    color: white;
    border: none;
    padding: 6px 14px;
    border-radius: 3px;
}
QPushButton:hover    { background-color: #7c858d; }
QPushButton:disabled { background-color: #495057; color: #adb5bd; }
QLineEdit, QComboBox {
    background-color: #343a40;
    color: #f8f9fa;
    border: 1px solid #495057;
    padding: 4px;
    border-radius: 3px;
}
QComboBox::drop-down { border: none; }
QComboBox QAbstractItemView {
    background-color: #343a40;
    color: #f8f9fa;
    selection-background-color: #495057;
}
QListWidget {
    background-color: #343a40;
    color: #f8f9fa;
    border: none;
}
QListWidget::item:selected { background-color: #495057; }
QGroupBox {
    color: #f8f9fa;
    border: 1px solid #495057;
    border-radius: 4px;
    margin-top: 8px;
    padding-top: 4px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
}
QCheckBox, QRadioButton { color: #f8f9fa; spacing: 6px; }
QCheckBox::indicator, QRadioButton::indicator {
    background-color: #343a40;
    border: 1px solid #6c757d;
}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {
    background-color: #28a745;
}
QProgressBar {
    background-color: #343a40;
    border: 1px solid #495057;
    border-radius: 3px;
    text-align: center;
    color: white;
}
QProgressBar::chunk { background-color: #28a745; border-radius: 3px; }
QScrollBar:vertical {
    background-color: #343a40;
    width: 12px;
    border: none;
}
QScrollBar::handle:vertical {
    background-color: #6c757d;
    border-radius: 6px;
    min-height: 20px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QMenu {
    background-color: #343a40;
    color: #f8f9fa;
    border: 1px solid #495057;
}
QMenu::item:selected { background-color: #495057; }
QMenu::separator { background-color: #495057; height: 1px; margin: 2px 0; }
QToolTip {
    background-color: #343a40;
    color: #f8f9fa;
    border: 1px solid #495057;
}
"""

LIGHT_STYLE = """
QMainWindow, QWidget, QDialog {
    background-color: #f0f0f0;
    color: #1a1a1a;
}
QFrame {
    background-color: #f0f0f0;
}
QTableWidget {
    background-color: #ffffff;
    color: #1a1a1a;
    gridline-color: #cccccc;
    alternate-background-color: #e8e8e8;
    selection-background-color: #b0c8e8;
    border: none;
}
QHeaderView::section {
    background-color: #dcdcdc;
    color: #1a1a1a;
    border: 1px solid #cccccc;
    padding: 4px;
}
QPushButton {
    background-color: #c0c0c0;
    color: #1a1a1a;
    border: none;
    padding: 6px 14px;
    border-radius: 3px;
}
QPushButton:hover    { background-color: #a8a8a8; }
QPushButton:disabled { background-color: #d8d8d8; color: #888888; }
QLineEdit, QComboBox {
    background-color: #ffffff;
    color: #1a1a1a;
    border: 1px solid #aaaaaa;
    padding: 4px;
    border-radius: 3px;
}
QComboBox::drop-down { border: none; }
QComboBox QAbstractItemView {
    background-color: #ffffff;
    color: #1a1a1a;
    selection-background-color: #b0c8e8;
}
QListWidget {
    background-color: #ffffff;
    color: #1a1a1a;
    border: none;
}
QListWidget::item:selected { background-color: #b0c8e8; }
QGroupBox {
    color: #1a1a1a;
    border: 1px solid #aaaaaa;
    border-radius: 4px;
    margin-top: 8px;
    padding-top: 4px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
}
QCheckBox, QRadioButton { color: #1a1a1a; spacing: 6px; }
QCheckBox::indicator, QRadioButton::indicator {
    background-color: #ffffff;
    border: 1px solid #aaaaaa;
}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {
    background-color: #28a745;
}
QProgressBar {
    background-color: #dcdcdc;
    border: 1px solid #aaaaaa;
    border-radius: 3px;
    text-align: center;
    color: #1a1a1a;
}
QProgressBar::chunk { background-color: #28a745; border-radius: 3px; }
QScrollBar:vertical {
    background-color: #dcdcdc;
    width: 12px;
    border: none;
}
QScrollBar::handle:vertical {
    background-color: #aaaaaa;
    border-radius: 6px;
    min-height: 20px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QMenu {
    background-color: #ffffff;
    color: #1a1a1a;
    border: 1px solid #aaaaaa;
}
QMenu::item:selected { background-color: #b0c8e8; }
QMenu::separator { background-color: #cccccc; height: 1px; margin: 2px 0; }
QToolTip {
    background-color: #ffffff;
    color: #1a1a1a;
    border: 1px solid #aaaaaa;
}
"""

THEMES = {"Dark": DARK_STYLE, "Light": LIGHT_STYLE}


def apply_theme(name):
    QApplication.instance().setStyleSheet(THEMES.get(name, DARK_STYLE))


def _restore_terminal():
    try:
        os.system("stty sane")
    except Exception:
        pass


if __name__ == "__main__":
    import atexit, signal, fcntl, tempfile
    atexit.register(_restore_terminal)
    signal.signal(signal.SIGINT, lambda *_: (_restore_terminal(), sys.exit(0)))
    signal.signal(signal.SIGTERM, lambda *_: (_restore_terminal(), sys.exit(0)))

    # ── Single-instance via lockfile + drop file ────────────────────────────
    # New launch with file args: append paths to a drop file and exit, IF
    # another instance is already running (detected via fcntl lock on a pid
    # file). The running instance polls the drop file and consumes paths.
    _LOCK_FILE = os.path.join(tempfile.gettempdir(), f"aisearch-{os.getuid()}.lock")
    _DROP_FILE = os.path.join(tempfile.gettempdir(), f"aisearch-{os.getuid()}.drop")
    _file_args = [a for a in sys.argv[1:] if os.path.exists(a)]

    # Try to grab an exclusive lock — succeeds only if no other instance holds it
    _lock_fd = open(_LOCK_FILE, "w")
    try:
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _is_first_instance = True
    except (BlockingIOError, OSError):
        _is_first_instance = False

    if not _is_first_instance:
        # Another instance is running — append paths to the drop file and exit
        if _file_args:
            try:
                with open(_DROP_FILE, "a", encoding="utf-8") as f:
                    fcntl.flock(f, fcntl.LOCK_EX)
                    f.write("\n".join(_file_args) + "\n")
                    fcntl.flock(f, fcntl.LOCK_UN)
            except Exception:
                pass
        sys.exit(0)

    # First instance — keep the lockfile open for the duration of the process.
    # On exit (clean or crash), the OS releases the flock automatically.
    _lock_fd.write(str(os.getpid())); _lock_fd.flush()

    import aisearch_config as _cfg
    app = QApplication(sys.argv)
    _config = _cfg.load_config()
    app.setStyleSheet(THEMES.get(_config.get("theme", "Dark"), DARK_STYLE))
    from attr_viewer import _UI_LANG as _ui_lang_init
    _ui_lang_init["val"] = _config.get("ui_language", "en")

    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aisearch_icon.png")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    window = AISearchApp()
    window.show()

    # Poll the drop file every 500ms — when paths show up, hand them to the
    # window and clear the file. Same handler as drag-and-drop.
    from PyQt6.QtCore import QTimer as _QT
    def _poll_drops():
        if not os.path.exists(_DROP_FILE):
            return
        try:
            with open(_DROP_FILE, "r+", encoding="utf-8") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                content = f.read()
                f.seek(0); f.truncate()
                fcntl.flock(f, fcntl.LOCK_UN)
        except Exception:
            return
        paths = [p for p in content.splitlines() if p and os.path.exists(p)]
        if paths and hasattr(window, "handle_external_paths"):
            window.handle_external_paths(paths)
    _drop_timer = _QT()
    _drop_timer.timeout.connect(_poll_drops)
    _drop_timer.start(100)

    # Initial files from this launch's CLI args
    if _file_args and hasattr(window, "handle_external_paths"):
        _QT.singleShot(0, lambda: window.handle_external_paths(_file_args))

    sys.exit(app.exec())
