import json
import os
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                              QPushButton, QListWidget, QListWidgetItem,
                              QLabel, QLineEdit)
from PyQt6.QtCore import Qt

_DIR        = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR   = os.path.join(_DIR, "data")
CONFIG_FILE = os.path.join(_DATA_DIR, "aisearch_config.json")

def config_file_for_project(project=None):
    """Return config file path for a project, or global default."""
    if project and project != "default":
        return os.path.join(_DATA_DIR, f"aisearch_config_{project}.json")
    return CONFIG_FILE

DEFAULT_COLORS = {
    "selection":  "#f9f06b",
    "dup_a":      ["#781919", "#9b4141", "#b97373", "#d2a5a5"],
    "dup_b":      ["#192d78", "#375096", "#6482b9", "#91a5d2"],
    "score":      ["#005a5a", "#1e6e64", "#508c82", "#8cb9af"],
    "unmarked":   "#ff9944",
    "attr_label": "#f0c040",
    # Button category colors
    "btn_add":     "#1a5a9a",
    "btn_remove":  "#7a2020",
    "btn_write":   "#9a8a00",
    "btn_stop":    "#cc2020",
    "btn_special": "#c87000",
}


def btn_color(key, config=None):
    """Return just the hex color string for a button category."""
    colors = (config or {}).get("colors", {})
    return colors.get(key, DEFAULT_COLORS.get(key, "#555555"))


def btn_ss(key, config=None, extra="padding:3px 10px;"):
    """Return a QPushButton stylesheet for a button category.

    key: 'btn_add' | 'btn_remove' | 'btn_write' | 'btn_stop' | 'btn_special'
    config: app.config dict (or None to use defaults)
    extra: additional CSS appended after the base style
    """
    colors = (config or {}).get("colors", {})
    color = colors.get(key, DEFAULT_COLORS.get(key, "#555555"))
    base = f"background-color:{color}; color:white; font-weight:bold;"
    return f"{base} {extra}".strip() if extra else base

def load_config(project=None):
    """Load config for a project. Falls back to global default if no project config exists."""
    defaults = {
        "last_move_dir": os.path.expanduser("~"),
        "keep_viewer_open": True,
        "dbl_click_spread": False,
        "last_project": "",
        "move_conflict": "size_check",
        "delete_confirm": True,
        "colors": DEFAULT_COLORS,
        "table_font_size":   10,
        "attr_font_size":    10,
        "ui_font_size":      10,
        "project_font_size": 30,
        "theme": "Dark",
    }
    path = config_file_for_project(project)
    if not os.path.exists(path):
        path = CONFIG_FILE  # fall back to global default
    if os.path.exists(path):
        try:
            with open(path, 'r') as f:
                cfg = {**defaults, **json.load(f)}
            # One-time migration: before v1.971, clip_inspect_mode="always"
            # meant "detect but skip when fields already set". That behavior is
            # now "when_empty"; "always" now means force every time. Migrate
            # so users keep the behavior they previously chose.
            if (not cfg.get("_clip_mode_migrated_v1971")
                    and cfg.get("clip_inspect_mode") == "always"):
                cfg["clip_inspect_mode"] = "when_empty"
                cfg["_clip_mode_migrated_v1971"] = True
                try:
                    with open(path, "w") as fh:
                        json.dump(cfg, fh, indent=2, ensure_ascii=False)
                except Exception:
                    pass
            return cfg
        except:
            return defaults
    return defaults


# --- Project behavior settings (never inherited from global) ---
# Stored in project_settings_PROJECT.json, separate from UI config.

_PROJECT_SETTINGS_DEFAULTS = {
    "auto_rename": False,
}

def project_settings_file(project):
    if project and project != "default":
        return os.path.join(_DIR, f"project_settings_{project}.json")
    return os.path.join(_DIR, "project_settings.json")

def load_project_settings(project=None):
    """Load per-project behavior flags. Never falls back to global — always returns safe defaults."""
    path = project_settings_file(project)
    if os.path.exists(path):
        try:
            with open(path, 'r') as f:
                return {**_PROJECT_SETTINGS_DEFAULTS, **json.load(f)}
        except:
            pass
    return dict(_PROJECT_SETTINGS_DEFAULTS)

def save_project_settings(project, data):
    """Save per-project behavior flags."""
    path = project_settings_file(project)
    try:
        with open(path, 'w') as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"Failed to save project settings: {e}")

def save_config(data, project=None):
    """Save config for a project (or global default)."""
    path = config_file_for_project(project)
    try:
        with open(path, 'w') as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"Failed to save config: {e}")

def reset_to_default(project):
    """Copy global default config into the project config."""
    import shutil
    if project and project != "default":
        dst = config_file_for_project(project)
        if os.path.exists(CONFIG_FILE):
            shutil.copy2(CONFIG_FILE, dst)
        return load_config(project)
    return load_config()


class FolderPickerDialog(QDialog):
    """Single-click folder selector. Single-click selects, double-click navigates."""
    def __init__(self, parent=None, initialdir=None, title="Select Folder"):
        super().__init__(parent)
        self.result = None
        self._current = os.path.abspath(initialdir) if initialdir and os.path.isdir(initialdir) else os.path.expanduser("~")
        self.setWindowTitle(title)
        self.resize(600, 520)
        self._build_ui()
        self._populate(self._current)
        self.exec()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        top = QHBoxLayout()
        top.addWidget(QLabel("Path:"))
        self.path_entry = QLineEdit()
        self.path_entry.returnPressed.connect(self._go_to_path)
        top.addWidget(self.path_entry)
        go_btn = QPushButton("Go")
        go_btn.setFixedWidth(50)
        go_btn.clicked.connect(self._go_to_path)
        top.addWidget(go_btn)
        layout.addLayout(top)

        self.list_widget = QListWidget()
        self.list_widget.itemClicked.connect(self._on_select)
        self.list_widget.itemDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self.list_widget)

        bf = QHBoxLayout()
        new_btn = QPushButton("New Folder")
        new_btn.clicked.connect(self._on_new_folder)
        new_btn.setStyleSheet("background-color: #444; color: #e0e0e0;")
        bf.addWidget(new_btn)
        bf.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setMinimumWidth(80)
        cancel_btn.setStyleSheet("background-color: #7a2020; color: white; font-weight: bold;")
        cancel_btn.clicked.connect(self.reject)
        ok_btn = QPushButton("OK")
        ok_btn.setMinimumWidth(80)
        ok_btn.setStyleSheet("background-color: #1a6e1a; color: white; font-weight: bold;")
        ok_btn.clicked.connect(self._on_ok)
        bf.addWidget(cancel_btn)
        bf.addWidget(ok_btn)
        layout.addLayout(bf)

    def _populate(self, path):
        self.list_widget.clear()
        self._current = path
        self.path_entry.setText(path)
        parent_path = os.path.dirname(path)
        if parent_path != path:
            item = QListWidgetItem("  .. (up)")
            item.setData(Qt.ItemDataRole.UserRole, "__up__")
            self.list_widget.addItem(item)
        try:
            entries = sorted(
                [e for e in os.scandir(path) if e.is_dir() and not e.name.startswith('.')],
                key=lambda e: e.name.lower()
            )
            for e in entries:
                item = QListWidgetItem(f"  {e.name}")
                item.setData(Qt.ItemDataRole.UserRole, e.path)
                self.list_widget.addItem(item)
        except PermissionError:
            pass

    def _on_select(self, item):
        iid = item.data(Qt.ItemDataRole.UserRole)
        self.path_entry.setText(os.path.dirname(self._current) if iid == "__up__" else iid)

    def _on_double_click(self, item):
        iid = item.data(Qt.ItemDataRole.UserRole)
        self._populate(os.path.dirname(self._current) if iid == "__up__" else iid)

    def _go_to_path(self):
        p = self.path_entry.text().strip()
        if os.path.isdir(p):
            self._populate(p)

    def _on_new_folder(self):
        from PyQt6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "New Folder", "Folder name:")
        if not ok or not name.strip(): return
        new_path = os.path.join(self._current, name.strip())
        try:
            os.makedirs(new_path, exist_ok=True)
            self._populate(new_path)
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Error", str(e))

    def _on_ok(self):
        p = self.path_entry.text().strip()
        if os.path.isdir(p):
            self.result = p
            self.accept()
