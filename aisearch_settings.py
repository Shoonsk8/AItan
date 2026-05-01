import os
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QTabWidget, QWidget, QApplication)
from PyQt6.QtCore import Qt
import aisearch_config as cfg
from attr_viewer import _lang_label
from aisearch_settings_widgets import _WsSec, _WsGroup
from aisearch_settings_db import _DbMixin
from aisearch_settings_appearance import _AppearanceMixin
from aisearch_settings_attrs import _AttrsMixin
from aisearch_settings_filename import _FilenameMixin
from aisearch_settings_person import _PersonMixin
from aisearch_settings_metadata import _MetadataMixin
from aisearch_settings_canvas import _CanvasMixin

VERSION = "2.0"


class SettingsView(_DbMixin, _PersonMixin, _AppearanceMixin, _AttrsMixin, _FilenameMixin, _MetadataMixin, _CanvasMixin, QDialog):
    def __init__(self, parent, app_instance, initial_tab=0):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowCloseButtonHint |
            Qt.WindowType.WindowMinimizeButtonHint |
            Qt.WindowType.WindowMaximizeButtonHint)
        self.app = app_instance
        self.setWindowTitle(_lang_label(f"Settings & DB Maintenance - Ver {VERSION} / 設定 & DBメンテナンス - Ver {VERSION}"))
        self.resize(800, 850)

        self._is_scanning = False
        self._stop_scan = False
        self._poll_timer = None
        self._scan_queue = None

        self._setup_ui(initial_tab)
        self.show()

    def _setup_ui(self, initial_tab=0):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(0)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # Define tab builders in order; each is built on first selection (lazy)
        self._tab_builders = [
            self._build_db_tab,           # Tab 0: 🗄 Database
            self._build_person_tab,       # Tab 1: 👤 Persons
            self._build_settings_tab,     # Tab 2: ⚙ Settings
            self._build_colors_tab,       # Tab 3: 🎨 Thresholds
            self._build_appearance_tab,   # Tab 4: 🖌 Appearance
            self._build_attrs_tab,        # Tab 5: 🏷 Attributes
            self._build_filename_tab,     # Tab 6: 📁 Filename Rules
            self._build_metadata_tab,     # Tab 7: 🔗 Meta Map
            self._build_canvas_tab,       # Tab 8: 🖼 Canvas
        ]
        self._tabs_built = set()
        self._tabs_ready = set()   # set after actual widget build completes

        # Add placeholder widgets so tab labels are visible immediately
        self._tab_labels_raw = [
            "🗄 Database / 🗄 データベース",
            "👤 Persons / 👤 人物",
            "⚙ Settings / ⚙ 設定",
            "🎨 Thresholds / 🎨 閾値",
            "🖌 Appearance / 🖌 外観",
            "🏷 Attributes / 🏷 属性",
            "📁 Filename Rules / 📁 ファイル名規則",
            "🔗 Meta Map / 🔗 メタマップ",
            "🖼 Canvas / 🖼 キャンバス",
        ]
        for lbl in self._tab_labels_raw:
            self.tabs.addTab(QWidget(), _lang_label(lbl))

        # Always build tab 0 (DB tab) first — showEvent calls _sync_scan_section which
        # needs DB tab widgets regardless of which tab is shown initially.
        self._ensure_tab_built(0)
        if initial_tab != 0:
            self._ensure_tab_built(initial_tab)
        self.tabs.setCurrentIndex(initial_tab)
        self.tabs.currentChanged.connect(self._ensure_tab_built)

    def _ensure_tab_built(self, index):
        if index in self._tabs_built:
            return
        self._tabs_built.add(index)
        # Defer actual build so the tab switch is instant and the label shows first
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self._do_build_tab(index))

    def _do_build_tab(self, index):
        # Replace the placeholder with the real tab content
        placeholder = self.tabs.widget(index)
        self._tab_builders[index](self.tabs)
        # _build_*_tab calls tabs.addTab() which appends a new tab at the end.
        # Move it to the correct position and remove the placeholder.
        last = self.tabs.count() - 1
        lbl = self.tabs.tabText(last)
        widget = self.tabs.widget(last)
        # Block signals so currentChanged callbacks don't fire mid-swap,
        # then set the correct index before unblocking so the first repaint
        # already shows the right tab (avoids the MetaMap flash).
        self.tabs.blockSignals(True)
        self.tabs.removeTab(last)
        self.tabs.removeTab(index)
        self.tabs.insertTab(index, widget, lbl)
        if placeholder is not None:
            placeholder.deleteLater()
        self.tabs.setCurrentIndex(index)
        self.tabs.blockSignals(False)
        self.tabs.update()
        self._tabs_ready.add(index)
        # If tab 0 just finished building, run the sync that showEvent couldn't
        if index == 0:
            self._sync_scan_section(self.app.current_project)

    def rebuild_for_language(self):
        """Update tab labels and rebuild all tabs for new language."""
        for i, raw in enumerate(self._tab_labels_raw):
            self.tabs.setTabText(i, _lang_label(raw))
        self.setWindowTitle(_lang_label(
            f"Settings & DB Maintenance - Ver {VERSION} / 設定 & DBメンテナンス - Ver {VERSION}"))
        cur = self.tabs.currentIndex()
        built = set(self._tabs_built)
        self._tabs_built.clear()
        self._tabs_ready.clear()
        for i in built:
            self._do_build_tab(i)
        self.tabs.setCurrentIndex(cur)

    def showEvent(self, event):
        super().showEvent(event)
        # Re-sync the scan section once tab 0 widgets are actually ready
        if 0 in self._tabs_ready:
            self._sync_scan_section(self.app.current_project)

    def _flash_saved_btn(self, btn, ms=1800):
        """Briefly turn button green with '✓ Saved' text, then restore."""
        from PyQt6.QtCore import QTimer
        orig_text = btn.text()
        orig_ss   = btn.styleSheet()
        btn.setText("✓ Saved")
        btn.setStyleSheet(
            "QPushButton { background:#1a7a1a; color:#ccffcc; font-weight:bold;"
            " border:1px solid #44cc44; padding:3px 10px; }"
            "QPushButton:hover { background:#1a9a1a; }")
        QTimer.singleShot(ms, lambda: (btn.setText(orig_text), btn.setStyleSheet(orig_ss)))

    def closeEvent(self, event):
        # Always hide instead of destroy — avoids rebuilding all tabs on next open
        event.ignore()
        self.hide()


# =============================================================================
# Standalone Runner
# =============================================================================
if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication, QMainWindow, QStatusBar

    class DummyPreviewHandler:
        def __init__(self):
            self.window = None

    class MockMainApp(QMainWindow):
        """
        A lightweight mock of AISearchApp that provides all the variables
        and methods the SettingsView expects, preventing crashes when run standalone.
        """
        def __init__(self):
            super().__init__()
            # Load real config if possible, otherwise use a blank dict
            self.config = cfg.load_config() if hasattr(cfg, 'load_config') else {}

            # Find an existing project to test with, or default to "Standalone_Test"
            db_files = [f.replace('features_', '').replace('.pt', '')
                        for f in os.listdir('.') if f.startswith('features_') and f.endswith('.pt')]
            self.current_project = db_files[0] if db_files else "Standalone_Test"

            self.keep_viewer_open = self.config.get("keep_viewer_open", True)
            self.data = None
            self.attrs_data = {}
            self.preview_handler = DummyPreviewHandler()

            # Mock the status bar used during database generation
            self.setStatusBar(QStatusBar())

        # Dummy methods that do nothing in standalone mode, but prevent AttributeErrors
        def reload_colors(self): print("[Mock] Colors reloaded")
        def _apply_header_theme(self): print("[Mock] Header theme applied")
        def reload_fonts(self): print("[Mock] Fonts reloaded")
        def set_project(self, name):
            self.current_project = name
            print(f"[Mock] Project switched to: {name}")
        def load_db(self): print("[Mock] Database loaded")
        def _apply_auto_update_db(self, checked): print(f"[Mock] Auto-update DB: {checked}")
        def reload_tag_groups(self): print("[Mock] Tag groups reloaded")

    # Launch the app
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    from PyQt6.QtGui import QPalette, QColor
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window,          QColor(30, 30, 30))
    pal.setColor(QPalette.ColorRole.WindowText,      QColor(224, 224, 224))
    pal.setColor(QPalette.ColorRole.Base,            QColor(37, 37, 37))
    pal.setColor(QPalette.ColorRole.AlternateBase,   QColor(45, 45, 45))
    pal.setColor(QPalette.ColorRole.Text,            QColor(224, 224, 224))
    pal.setColor(QPalette.ColorRole.BrightText,      QColor(255, 255, 255))
    pal.setColor(QPalette.ColorRole.Button,          QColor(50, 50, 50))
    pal.setColor(QPalette.ColorRole.ButtonText,      QColor(224, 224, 224))
    pal.setColor(QPalette.ColorRole.Highlight,       QColor(42, 130, 218))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(0, 0, 0))
    pal.setColor(QPalette.ColorRole.Link,            QColor(100, 180, 255))
    pal.setColor(QPalette.ColorRole.ToolTipBase,     QColor(50, 50, 50))
    pal.setColor(QPalette.ColorRole.ToolTipText,     QColor(224, 224, 224))
    app.setPalette(pal)

    mock_app = MockMainApp()
    window = SettingsView(None, mock_app)

    sys.exit(app.exec())
