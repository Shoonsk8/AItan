import os, sys, importlib.util

os.chdir(os.path.dirname(os.path.abspath(__file__)))

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
    import atexit, signal
    atexit.register(_restore_terminal)
    signal.signal(signal.SIGINT, lambda *_: (_restore_terminal(), sys.exit(0)))
    signal.signal(signal.SIGTERM, lambda *_: (_restore_terminal(), sys.exit(0)))

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
    sys.exit(app.exec())
