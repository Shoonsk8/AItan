"""Layout tab — configure group order and field assignments for the preview panel."""
import json, os
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QListWidget, QListWidgetItem, QScrollArea,
    QFrame, QSizePolicy, QMessageBox, QAbstractItemView,
)
# QLabel kept for potential future status display
from PyQt6.QtCore import Qt
import aisearch_config as cfg


def _tags_file_for(app):
    """Return the tags JSON path for the current project."""
    import aisearch_attrs as _am
    return _am.tags_file_for_project(app.current_project)


def _load_layout(app):
    """Load group/field layout from the current project's tags file."""
    path = _tags_file_for(app)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    group_order    = data.get("__group_order__", [])
    section_groups = data.get("__section_groups__", {})
    section_order  = data.get("__section_order__", [])
    # Build ungrouped: fields in section_order but not in any group
    all_grouped = {k for members in section_groups.values() for k in members}
    ungrouped   = [k for k in section_order if k not in all_grouped and not k.startswith("__")]
    return group_order, section_groups, ungrouped, data


def _save_layout(app, group_order, section_groups, ungrouped):
    """Write updated layout back to the tags file, preserving all other data."""
    path = _tags_file_for(app)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}

    # Rebuild section_order: grouped fields in group_order sequence, then ungrouped
    new_order = []
    for grp in group_order:
        new_order.extend(section_groups.get(grp, []))
    for k in ungrouped:
        if k not in new_order:
            new_order.append(k)

    data["__group_order__"]    = group_order
    data["__section_groups__"] = section_groups
    data["__section_order__"]  = new_order

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


_CARD_SS  = ("QFrame { background:#2a2a2a; border:1px solid #444;"
             " border-radius:4px; margin:2px; }")
_TITLE_SS = "color:#ddd; font-weight:bold; font-size:9pt;"
_BTN_SS   = ("QPushButton { background:#383838; color:#ccc; border:1px solid #555;"
             " border-radius:3px; padding:1px 6px; font-size:8pt; }"
             "QPushButton:hover { background:#4a4a4a; }")
_LIST_SS  = ("QListWidget { background:#1e1e1e; color:#ccc; border:1px solid #555;"
             " font-size:8pt; } "
             "QListWidget::item:selected { background:#3a5a3a; color:#eee; }")
def _add_ss(app_config=None):
    color = cfg.btn_color("btn_add", app_config)
    return (f"QPushButton {{ background:{color}; color:white; font-weight:bold; border:none;"
            " border-radius:3px; padding:2px 10px; font-size:8pt; }"
            f"QPushButton:hover {{ background:{color}dd; }}")


class _GroupCard(QFrame):
    """A single group card with name, field list, and reorder/delete controls."""

    def __init__(self, name, fields, parent_mixin):
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(_CARD_SS)
        self._mixin = parent_mixin
        self._name  = name

        vlay = QVBoxLayout(self)
        vlay.setContentsMargins(6, 4, 6, 6)
        vlay.setSpacing(4)

        # ── Header row ───────────────────────────────────────────────────────
        h = QHBoxLayout(); h.setSpacing(6)
        self._name_edit = QLineEdit(name)
        self._name_edit.setStyleSheet(
            "background:#333; color:#e0e0e0; border:1px solid #555;"
            " border-radius:2px; padding:1px 4px; font-weight:bold;")
        self._name_edit.setFixedWidth(120)
        h.addWidget(self._name_edit)
        h.addStretch()

        btn_up = QPushButton("▲"); btn_up.setFixedWidth(28); btn_up.setStyleSheet(_BTN_SS)
        btn_dn = QPushButton("▼"); btn_dn.setFixedWidth(28); btn_dn.setStyleSheet(_BTN_SS)
        btn_x  = QPushButton("✕"); btn_x.setFixedWidth(28)
        btn_x.setStyleSheet("QPushButton{background:transparent;color:#884444;"
                            "border:none;font-size:10pt;}"
                            "QPushButton:hover{color:#cc6666;}")
        btn_up.clicked.connect(self._move_up)
        btn_dn.clicked.connect(self._move_down)
        btn_x.clicked.connect(self._delete)
        h.addWidget(btn_up); h.addWidget(btn_dn); h.addWidget(btn_x)
        vlay.addLayout(h)

        # ── Field list ───────────────────────────────────────────────────────
        self._list = QListWidget()
        self._list.setStyleSheet(_LIST_SS)
        self._list.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self._list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._list.setAcceptDrops(True)
        self._list.setMaximumHeight(120)
        self._list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        for f in fields:
            self._list.addItem(QListWidgetItem(f))
        vlay.addWidget(self._list)

        # ── Add field row ─────────────────────────────────────────────────────
        ar = QHBoxLayout(); ar.setSpacing(4)
        self._field_edit = QLineEdit()
        self._field_edit.setPlaceholderText("field key…")
        self._field_edit.setStyleSheet(
            "background:#2a2a2a; color:#ccc; border:1px solid #555;"
            " border-radius:2px; padding:1px 4px; font-size:8pt;")
        self._field_edit.setFixedWidth(100)
        btn_add = QPushButton("+ Add field"); btn_add.setStyleSheet(_add_ss(getattr(getattr(self._mixin, 'app', None), 'config', None)))
        btn_add.clicked.connect(self._add_field)
        self._field_edit.returnPressed.connect(self._add_field)
        ar.addWidget(self._field_edit); ar.addWidget(btn_add); ar.addStretch()
        vlay.addLayout(ar)

    # ── Public API ────────────────────────────────────────────────────────────

    def group_name(self):
        return self._name_edit.text().strip() or self._name

    def fields(self):
        return [self._list.item(i).text() for i in range(self._list.count())]

    # ── Internals ─────────────────────────────────────────────────────────────

    def _add_field(self):
        key = self._field_edit.text().strip()
        if key:
            self._list.addItem(QListWidgetItem(key))
            self._field_edit.clear()

    def _move_up(self):
        self._mixin._move_card(self, -1)

    def _move_down(self):
        self._mixin._move_card(self, +1)

    def _delete(self):
        # Move fields to ungrouped before removing
        for f in self.fields():
            self._mixin._unassigned_list.addItem(QListWidgetItem(f))
        self._mixin._remove_card(self)


class _LayoutMixin:
    """Settings mixin: Layout tab for configuring group order and field assignments."""

    def _build_layout_tab(self, tabs):
        # Debounce timer — auto-applies layout to preview 800ms after any change
        self._layout_apply_timer = QTimer()
        self._layout_apply_timer.setSingleShot(True)
        self._layout_apply_timer.setInterval(800)
        self._layout_apply_timer.timeout.connect(self._layout_save)

        tab = QWidget()
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # ── Top toolbar ───────────────────────────────────────────────────────
        tb = QHBoxLayout(); tb.setSpacing(6)
        lbl = QLabel("Layout — groups & field order")
        lbl.setStyleSheet("color:#aaa; font-size:9pt;")
        tb.addWidget(lbl); tb.addStretch()
        self._layout_grp_edit = QLineEdit()
        self._layout_grp_edit.setPlaceholderText("New group name…")
        self._layout_grp_edit.setFixedWidth(140)
        self._layout_grp_edit.setStyleSheet(
            "background:#2a2a2a; color:#e0e0e0; border:1px solid #555;"
            " border-radius:2px; padding:2px 6px;")
        btn_add_grp = QPushButton("+ Add Group"); btn_add_grp.setStyleSheet(_add_ss(self.app.config))
        btn_add_grp.clicked.connect(self._layout_add_group)
        self._layout_grp_edit.returnPressed.connect(self._layout_add_group)
        tb.addWidget(self._layout_grp_edit); tb.addWidget(btn_add_grp)
        btn_save = QPushButton("💾 Save Layout")
        btn_save.setStyleSheet(cfg.btn_ss("btn_write", self.app.config, "border:none; border-radius:3px; padding:3px 12px; font-size:9pt;"))
        btn_save.clicked.connect(self._layout_save)
        tb.addWidget(btn_save)
        outer.addLayout(tb)

        # ── Scrollable group cards area ───────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none; background:#1a1a1a;}")
        self._layout_cards_widget = QWidget()
        self._layout_cards_widget.setStyleSheet("background:#1a1a1a;")
        self._layout_cards_vbox = QVBoxLayout(self._layout_cards_widget)
        self._layout_cards_vbox.setContentsMargins(4, 4, 4, 4)
        self._layout_cards_vbox.setSpacing(4)
        self._layout_cards_vbox.addStretch()
        scroll.setWidget(self._layout_cards_widget)
        outer.addWidget(scroll, stretch=3)

        # ── Unassigned fields pool ────────────────────────────────────────────
        ua_lbl = QLabel("Unassigned fields (drag into a group above):")
        ua_lbl.setStyleSheet("color:#888; font-size:8pt;")
        outer.addWidget(ua_lbl)
        self._unassigned_list = QListWidget()
        self._unassigned_list.setStyleSheet(_LIST_SS)
        self._unassigned_list.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self._unassigned_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._unassigned_list.setMaximumHeight(80)
        outer.addWidget(self._unassigned_list, stretch=0)

        tabs.addTab(tab, "📐 Layout")

        # ── Populate from current project data ────────────────────────────────
        self._layout_cards = []   # ordered list of _GroupCard widgets
        self._layout_populate()

    def _layout_populate(self):
        """Load current layout from tags file and populate cards."""
        # Clear existing cards
        for card in list(self._layout_cards):
            self._layout_cards_vbox.removeWidget(card)
            card.deleteLater()
        self._layout_cards.clear()
        self._unassigned_list.clear()

        group_order, section_groups, ungrouped, _ = _load_layout(self.app)

        for grp in group_order:
            self._layout_add_card(grp, section_groups.get(grp, []))

        for f in ungrouped:
            self._unassigned_list.addItem(QListWidgetItem(f))

    def _layout_add_group(self):
        name = self._layout_grp_edit.text().strip()
        if not name:
            return
        self._layout_add_card(name, [])
        self._layout_grp_edit.clear()

    def _layout_add_card(self, name, fields):
        card = _GroupCard(name, fields, self)
        stretch_idx = self._layout_cards_vbox.count() - 1
        self._layout_cards_vbox.insertWidget(stretch_idx, card)
        self._layout_cards.append(card)
        # Auto-apply when fields are dropped into/out of this card's list
        card._list.model().rowsInserted.connect(self._layout_changed)
        card._list.model().rowsRemoved.connect(self._layout_changed)
        card._list.model().rowsMoved.connect(self._layout_changed)
        return card

    def _layout_changed(self):
        """Called on any structural change — debounced auto-apply to preview."""
        self._layout_apply_timer.start()

    def _move_card(self, card, direction):
        idx = self._layout_cards.index(card)
        new_idx = idx + direction
        if new_idx < 0 or new_idx >= len(self._layout_cards):
            return
        self._layout_cards[idx], self._layout_cards[new_idx] = (
            self._layout_cards[new_idx], self._layout_cards[idx])
        for c in self._layout_cards:
            self._layout_cards_vbox.removeWidget(c)
        for i, c in enumerate(self._layout_cards):
            self._layout_cards_vbox.insertWidget(i, c)
        self._layout_changed()

    def _remove_card(self, card):
        self._layout_cards_vbox.removeWidget(card)
        self._layout_cards.remove(card)
        card.deleteLater()
        self._layout_changed()

    def _layout_save(self):
        group_order    = [c.group_name() for c in self._layout_cards]
        section_groups = {c.group_name(): c.fields() for c in self._layout_cards}
        ungrouped      = [self._unassigned_list.item(i).text()
                          for i in range(self._unassigned_list.count())]
        try:
            _save_layout(self.app, group_order, section_groups, ungrouped)
        except Exception as e:
            QMessageBox.warning(self, "Save Error", str(e))
            return
        # Rebuild preview immediately — no popup, just apply
        try:
            self.app.reload_tag_groups()
        except Exception:
            pass
