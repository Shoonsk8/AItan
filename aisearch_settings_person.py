import os
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                              QLabel, QLineEdit, QScrollArea, QFrame,
                              QToolButton, QApplication, QInputDialog, QMessageBox,
                              QMenu, QComboBox)
from PyQt6.QtCore import Qt, QTimer, QMimeData
from PyQt6.QtGui import QPixmap, QDrag

import aisearch_attrs as attrs_mod
import aisearch_config as cfg
from attr_viewer import _lang_label as _t

_CARD_MIME  = "PERSON_CARD"
_GROUP_MIME = "REORDER_PERSON_GROUP"
_THUMB = 60


# ─────────────────────────────────────────────────────────────────────────────

class _HoverThumb(QLabel):
    """Thumbnail label. Click handled by parent _PersonCard via event propagation."""

    def __init__(self, src_path: str, size: int = 48, parent=None):
        super().__init__(parent)
        self._src = src_path
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background: #1a1a1a; border-radius: 3px;")

    def hide_popup(self):
        pass  # no popup — kept for call-site compat


# ─────────────────────────────────────────────────────────────────────────────

class _PersonCard(QFrame):
    """Draggable person card: thumbnail + P001 label + optional × unlink.
    Drag moves the card to another group (links IDs).
    Click (press+release with no drag) opens the image in preview."""

    _SS_NORMAL = "QFrame { background:#2a2a2a; border-radius:5px; }"
    _SS_DROP   = "QFrame { background:#252535; border:1px solid #5566bb; border-radius:5px; }"

    def __init__(self, pid: str, src: str, click_cb=None,
                 unlink_cb=None, show_unlink=False, link_cb=None,
                 attrs_getter=None, parent=None):
        super().__init__(parent)
        self._pid = pid
        self._src = src
        self._click_cb = click_cb
        self._unlink_cb = unlink_cb
        self._link_cb = link_cb   # if set, card accepts drops to create a new group
        self._attrs_getter = attrs_getter  # callable(path) → attrs dict
        self._drag_start = None
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(self._SS_NORMAL)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        if link_cb:
            self.setAcceptDrops(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(2)

        # × unlink button (multi-member groups only)
        if show_unlink and unlink_cb:
            top = QHBoxLayout()
            top.setContentsMargins(0, 0, 0, 0)
            top.addStretch()
            btn_x = QPushButton("×")
            btn_x.setFixedSize(16, 16)
            btn_x.setStyleSheet(
                "QPushButton { background:#553333; color:#ffaaaa; border-radius:8px; "
                "font-size:10px; padding:0; }"
                "QPushButton:hover { background:#773333; }")
            btn_x.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_x.clicked.connect(lambda _pid=pid: unlink_cb(_pid))
            top.addWidget(btn_x)
            root.addLayout(top)

        # Thumbnail — no click_cb; click fires from mouseReleaseEvent below
        self._thumb = _HoverThumb(src, _THUMB)
        if src and os.path.exists(src):
            px = QPixmap(src)
            if not px.isNull():
                px = px.scaled(_THUMB, _THUMB,
                               Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
                self._thumb.setPixmap(px)
            else:
                self._thumb.setText("👤")
                self._thumb.setStyleSheet(
                    "background:#1a1a1a; border-radius:3px; font-size:22px; color:#666;")
        else:
            self._thumb.setText("👤")
            self._thumb.setStyleSheet(
                "background:#1a1a1a; border-radius:3px; font-size:22px; color:#666;")
        # Pass mouse events through to _PersonCard so clicks/drags register
        self._thumb.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        root.addWidget(self._thumb, alignment=Qt.AlignmentFlag.AlignCenter)

        # ID label
        lbl = QLabel(f"P{pid}")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet("color:#aaccff; font-size:10px; font-weight:bold;")
        lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        root.addWidget(lbl)

        self.setFixedWidth(_THUMB + 16)

    # ── Context menu ──────────────────────────────────────────────────────────

    def contextMenuEvent(self, event):
        path = self._src
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#2a2a2a;color:#ddd;border:1px solid #555;}"
            "QMenu::item:selected{background:#4a7a4e;}"
            "QMenu::separator{background:#555;height:1px;margin:2px 0;}")
        act_path     = menu.addAction(_t("📋 Copy Path / 📋 パスをコピー"))
        act_filename = menu.addAction(_t("📋 Copy Filename / 📋 ファイル名をコピー"))
        act_stem     = menu.addAction(_t("📋 Copy Stem (no ext) / 📋 ファイル名（拡張子なし）をコピー"))
        if self._attrs_getter and path:
            menu.addSeparator()
            act_attrs = menu.addAction(_t("📋 Copy Attributes / 📋 属性をコピー"))
        else:
            act_attrs = None
        chosen = menu.exec(event.globalPos())
        if not chosen:
            return
        cb = QApplication.clipboard()
        if chosen == act_path and path:
            cb.setText(os.path.abspath(path))
        elif chosen == act_filename and path:
            cb.setText(os.path.basename(path))
        elif chosen == act_stem and path:
            cb.setText(os.path.splitext(os.path.basename(path))[0])
        elif chosen == act_attrs and path and self._attrs_getter:
            entry = self._attrs_getter(path) or {}
            parts = []
            tags = entry.get("tags", [])
            if tags:
                parts.append("tags: " + ", ".join(tags))
            coded = entry.get("coded", {})
            if coded:
                parts.append("coded: " + str(coded))
            note = entry.get("note", "")
            if note:
                parts.append("note: " + note)
            cb.setText("\n".join(parts) if parts else "(no attributes)")

    # ── Mouse: drag + click ───────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.pos()

    def mouseMoveEvent(self, event):
        if not self._drag_start:
            return
        if (event.pos() - self._drag_start).manhattanLength() < QApplication.startDragDistance():
            return
        # Hide hover popup before drag starts
        self._thumb.hide_popup()

        drag = QDrag(self)
        mime = QMimeData()
        mime.setText(f"{_CARD_MIME}:{self._pid}")
        drag.setMimeData(mime)
        drag.setPixmap(self.grab())
        drag.setHotSpot(event.pos())
        drag.exec(Qt.DropAction.MoveAction)
        self._drag_start = None

    def mouseReleaseEvent(self, event):
        if (event.button() == Qt.MouseButton.LeftButton
                and self._drag_start is not None
                and (event.pos() - self._drag_start).manhattanLength() < QApplication.startDragDistance()):
            # It was a click, not a drag
            if self._click_cb:
                self._click_cb()
        self._drag_start = None

    def dragEnterEvent(self, event):
        txt = event.mimeData().text() if event.mimeData().hasText() else ""
        if txt.startswith(_CARD_MIME + ":"):
            pid = txt.split(":", 1)[1]
            if pid != self._pid:
                self.setStyleSheet(self._SS_DROP)
                event.acceptProposedAction()
                return
        event.ignore()

    def dragLeaveEvent(self, event):
        self.setStyleSheet(self._SS_NORMAL)

    def dropEvent(self, event):
        self.setStyleSheet(self._SS_NORMAL)
        txt = event.mimeData().text()
        if txt.startswith(_CARD_MIME + ":") and self._link_cb:
            pid = txt.split(":", 1)[1]
            if pid != self._pid:
                self._link_cb(pid)
                event.acceptProposedAction()


# ─────────────────────────────────────────────────────────────────────────────

class _PersonGroup(QWidget):
    """Collapsible group card with drag-to-reorder header.
    Drop a PERSON_CARD onto it to link that ID into this group."""

    _SS_NORMAL    = "QWidget#_pg_hdr { background:#2a2d2a; border-radius:4px; }"
    _SS_DROP_CARD = ("QWidget#_pg_hdr { background:#252535; border:1px solid #5566bb; "
                     "border-radius:4px; }")

    def __init__(self, pids: list, registry: dict, pid_to_path: dict,
                 save_name_cb, preview_cb, unlink_cb, changed_cb,
                 reorder_cb=None, show_name=True, is_right=False,
                 reassign_cb=None, parent=None):
        super().__init__(parent)
        self._pids = list(pids)
        self._is_right = is_right
        self._changed_cb = changed_cb
        self._reorder_cb = reorder_cb
        self._drag_start = None
        self.setAcceptDrops(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 1, 0, 1)
        root.setSpacing(0)

        # ── Header ────────────────────────────────────────────────────────────
        self.hdr = QWidget()
        self.hdr.setObjectName("_pg_hdr")
        self.hdr.setStyleSheet(self._SS_NORMAL)
        hdr_lay = QHBoxLayout(self.hdr)
        hdr_lay.setContentsMargins(4, 3, 6, 3)
        hdr_lay.setSpacing(4)

        handle = QLabel("⠿")
        handle.setStyleSheet("color:#557755; font-size:13pt; font-weight:bold;")
        handle.setCursor(Qt.CursorShape.OpenHandCursor)
        hdr_lay.addWidget(handle)

        self._arrow = QToolButton()
        self._arrow.setArrowType(Qt.ArrowType.DownArrow)
        self._arrow.setCheckable(True)
        self._arrow.setChecked(True)
        self._arrow.setStyleSheet("QToolButton { border:none; background:transparent; }")
        self._arrow.toggled.connect(self._on_toggle)
        hdr_lay.addWidget(self._arrow)

        sorted_pids = sorted(pids)
        self._primary_pid = sorted_pids[0]
        name = ""
        for p in sorted_pids:
            n = registry.get(p, "")
            if n:
                name = n
                self._primary_pid = p
                break
        # Default name: decimal number derived from the hex person ID
        if not name:
            try:
                name = str(int(self._primary_pid, 16))
            except ValueError:
                name = self._primary_pid

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText(_t("Person name… / 人物名…"))
        self._name_edit.setText(name if show_name else "")
        self._name_edit.setStyleSheet(
            "background:transparent; color:#cceecc; font-weight:bold; "
            "border:none; border-bottom:1px solid #446644;")
        self._name_edit.setAcceptDrops(False)

        # Save 800 ms after the last keystroke (covers all exit paths)
        self._name_save_timer = QTimer()
        self._name_save_timer.setSingleShot(True)
        self._name_save_timer.setInterval(800)
        self._name_save_timer.timeout.connect(
            lambda: save_name_cb(self._primary_pid, self._name_edit.text().strip()))
        self._name_edit.textEdited.connect(lambda: self._name_save_timer.start())
        # Also save immediately on Return / focus-loss
        self._name_edit.editingFinished.connect(
            lambda: save_name_cb(self._primary_pid, self._name_edit.text().strip()))

        # If no existing name was in the registry, persist the default number now
        if show_name and not any(registry.get(p, "") for p in sorted_pids):
            QTimer.singleShot(0, lambda: save_name_cb(self._primary_pid, self._name_edit.text().strip()))

        hdr_lay.addWidget(self._name_edit, stretch=1)

        for p in sorted_pids:
            chip = QLabel(f"P{p}")
            chip.setStyleSheet(
                "color:#aaccff; font-size:9px; background:#333; "
                "border-radius:3px; padding:1px 5px;")
            hdr_lay.addWidget(chip)

        # ── Reassign button ───────────────────────────────────────────────────
        if reassign_cb:
            self._reassign_cb = reassign_cb
            self._reassign_pids = list(sorted_pids)
            # Stash the full registry so the dialog can check for existing
            # owners of the target ID before applying the reassignment.
            self._reassign_registry = dict(registry or {})
            btn_reassign = QPushButton("→")
            btn_reassign.setFixedSize(22, 22)
            btn_reassign.setToolTip(_t("Reassign all files in this group to a different person ID / このグループの全ファイルを別の人物IDに再割り当て"))
            btn_reassign.setStyleSheet(
                "QPushButton { background:#2a2a3a; color:#aaaaff; border:1px solid #445566; "
                "border-radius:3px; font-weight:bold; padding:0; }"
                "QPushButton:hover { background:#3a3a5a; }")
            btn_reassign.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_reassign.clicked.connect(self._on_reassign_clicked)
            hdr_lay.addWidget(btn_reassign)

        root.addWidget(self.hdr)

        # ── Content (collapsible) ─────────────────────────────────────────────
        self.content = QWidget()
        self.content.setStyleSheet("background:#1e221e;")

        cards_lay = QHBoxLayout(self.content)
        cards_lay.setContentsMargins(8, 6, 8, 6)
        cards_lay.setSpacing(6)
        multi = len(pids) > 1

        for p in sorted_pids:
            src = pid_to_path.get(p, "")

            def _mk_click(_p=p, _s=src):
                return lambda: preview_cb(_p, _s)

            card = _PersonCard(
                pid=p, src=src,
                click_cb=_mk_click(),
                unlink_cb=unlink_cb,
                show_unlink=multi,
                attrs_getter=lambda _s=src: attrs_mod.get(
                    getattr(self._app, "attrs_data", {}), _s) if _s else {},
            )
            cards_lay.addWidget(card)

        cards_lay.addStretch()
        root.addWidget(self.content)
        self.content.setVisible(True)   # after parenting — avoids ghost top-level window

    # ── Collapse ──────────────────────────────────────────────────────────────

    def _on_toggle(self, checked: bool):
        self.content.setVisible(checked)
        self._arrow.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)
        from PyQt6.QtWidgets import QScrollArea
        p = self.parentWidget()
        while p is not None:
            p.updateGeometry()
            if isinstance(p, QScrollArea):
                p.widget().adjustSize()
                break
            p = p.parentWidget()

    # ── Drag to reorder ───────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.hdr.underMouse():
            self._drag_start = event.pos()

    def mouseMoveEvent(self, event):
        if not self._drag_start:
            return
        if (event.pos() - self._drag_start).manhattanLength() < QApplication.startDragDistance():
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setText(f"{_GROUP_MIME}:{self._primary_pid}")
        drag.setMimeData(mime)
        drag.setPixmap(self.hdr.grab())
        drag.setHotSpot(event.pos() - self.hdr.pos())
        drag.exec(Qt.DropAction.MoveAction)
        self._drag_start = None

    def mouseReleaseEvent(self, event):
        self._drag_start = None

    # ── Drop ──────────────────────────────────────────────────────────────────

    def dragEnterEvent(self, event):
        txt = event.mimeData().text() if event.mimeData().hasText() else ""
        if txt.startswith(_CARD_MIME + ":"):
            pid = txt.split(":", 1)[1]
            if pid not in self._pids:
                self.hdr.setStyleSheet(self._SS_DROP_CARD)
                event.acceptProposedAction()
                return
        elif txt.startswith(_GROUP_MIME + ":"):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragLeaveEvent(self, event):
        self.hdr.setStyleSheet(self._SS_NORMAL)

    def dropEvent(self, event):
        self.hdr.setStyleSheet(self._SS_NORMAL)
        txt = event.mimeData().text()

        if txt.startswith(_CARD_MIME + ":"):
            dropped_pid = txt.split(":", 1)[1]
            if dropped_pid not in self._pids:
                # Remove from whichever store the card was in
                attrs_mod.remove_person_from_aliases(dropped_pid)
                attrs_mod.remove_from_right_group(dropped_pid)
                if self._is_right:
                    attrs_mod.link_right_group(self._pids[0], dropped_pid)
                else:
                    attrs_mod.link_persons(self._pids[0], dropped_pid)
                event.acceptProposedAction()
                # Defer rebuild so drag.exec() fully returns before widgets are torn down
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, self._changed_cb)

        elif txt.startswith(_GROUP_MIME + ":"):
            source_pid = txt.split(":", 1)[1]
            # Search globally across both columns
            top = self
            while top.parentWidget():
                top = top.parentWidget()
            source = None
            for w in top.findChildren(_PersonGroup):
                if w._primary_pid == source_pid:
                    source = w
                    break
            if source and source is not self:
                # Remove from wherever it is
                if source.parentWidget() and source.parentWidget().layout():
                    source.parentWidget().layout().removeWidget(source)
                # Insert before self in self's column
                lay = self.parentWidget().layout() if self.parentWidget() else None
                if lay:
                    curr_idx = lay.indexOf(self)
                    lay.insertWidget(curr_idx, source)
                event.acceptProposedAction()
                if self._reorder_cb:
                    from PyQt6.QtCore import QTimer
                    QTimer.singleShot(0, self._reorder_cb)

    # ── Reassign ──────────────────────────────────────────────────────────────

    def _on_reassign_clicked(self):
        pids_str = ", ".join(f"P{p}" for p in self._reassign_pids)
        new_id, ok = QInputDialog.getText(
            self, _t("Reassign Person ID / 人物ID再割り当て"),
            _t(f"Reassign all files in this group ({pids_str})\n"
               f"to which person ID?\n\n"
               f"Enter 3-hex ID (e.g. 001): / "
               f"このグループ({pids_str})の全ファイルを\n"
               f"どの人物IDに再割り当てしますか？\n\n"
               f"3桁16進数IDを入力（例：001）："),
        )
        if not ok or not new_id.strip():
            return
        new_id = new_id.strip().lower().zfill(3)
        # Validate: must be 1–3 hex digits
        try:
            val = int(new_id, 16)
            if val < 0 or val > 0xfff:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, _t("Invalid ID / 無効なID"),
                                _t(f"'{new_id}' is not a valid 3-hex person ID (000–fff). / '{new_id}' は有効な3桁16進数IDではありません（000〜fff）。"))
            return
        # Reject if the target ID is already owned by a person OUTSIDE this
        # group. Reassigning into one of the group's own pids is fine (it
        # collapses the group onto that pid). The registry was stashed at
        # build time — fresh enough for an immediate decision.
        registry = getattr(self, "_reassign_registry", {}) or {}
        own = {p.lower() for p in self._reassign_pids}
        if new_id in registry and new_id not in own:
            owner_name = registry.get(new_id, "")
            owner_str = f"{new_id} ({owner_name})" if owner_name else new_id
            ans = QMessageBox.question(
                self, _t("ID already taken / ID は使用中"),
                _t(f"Person ID '{owner_str}' is already registered.\n\n"
                   f"Reassign anyway? Files in this group will be merged "
                   f"into the existing person. / "
                   f"人物ID '{owner_str}' は既に登録されています。\n\n"
                   f"このまま再割り当てしますか？このグループのファイルは "
                   f"既存の人物に統合されます。"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if ans != QMessageBox.StandardButton.Yes:
                return
        self._reassign_cb(self._reassign_pids, new_id)


# ─────────────────────────────────────────────────────────────────────────────

class _PendingGroup(QWidget):
    """Drop zone for creating a new group.
    Drop ONE card → immediately becomes a real single-member group.
    Drop more cards onto that group to link them."""

    _SS_NORMAL = "QWidget { background:#222228; border:1px dashed #4a4a6a; border-radius:6px; }"
    _SS_DROP   = "QWidget { background:#252535; border:1px solid #5566bb; border-radius:6px; }"

    def __init__(self, promote_cb, parent=None):
        """promote_cb(self, pid) — called on first drop; replaces this widget with a real group."""
        super().__init__(parent)
        self._promote_cb = promote_cb
        self.setAcceptDrops(True)
        self.setStyleSheet(self._SS_NORMAL)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)

        lbl = QLabel(_t("Drop a person here to create a new group / 人物をここにドロップして新グループを作成"))
        lbl.setStyleSheet("color:#666; font-size:10px;")
        lay.addWidget(lbl)
        lay.addStretch()

        btn_x = QPushButton("×")
        btn_x.setFixedSize(20, 20)
        btn_x.setStyleSheet(
            "QPushButton { background:#442222; color:#ffaaaa; border-radius:10px; }"
            "QPushButton:hover { background:#662222; }")
        btn_x.clicked.connect(self.deleteLater)
        lay.addWidget(btn_x)

    def dragEnterEvent(self, event):
        if event.mimeData().hasText() and event.mimeData().text().startswith(_CARD_MIME + ":"):
            self.setStyleSheet(self._SS_DROP)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.setStyleSheet(self._SS_NORMAL)

    def dropEvent(self, event):
        self.setStyleSheet(self._SS_NORMAL)
        txt = event.mimeData().text()
        if not txt.startswith(_CARD_MIME + ":"):
            return
        pid = txt.split(":", 1)[1]
        event.acceptProposedAction()
        self._promote_cb(self, pid)


# ─────────────────────────────────────────────────────────────────────────────

class _ColumnHeader(QLabel):
    """Column header bar that also acts as a drop target for PERSON_CARD drags."""

    _SS_NORMAL = ("color:#cccccc; font-size:10px; font-weight:bold; "
                  "background:#2d2d2d; padding:5px 8px;")
    _SS_DROP   = ("color:#cccccc; font-size:10px; font-weight:bold; "
                  "background:#252535; border:1px solid #5566bb; padding:5px 8px;")

    def __init__(self, text, card_drop_cb=None, parent=None):
        super().__init__(text, parent)
        self._card_drop_cb = card_drop_cb
        self.setFixedHeight(26)
        self.setStyleSheet(self._SS_NORMAL)
        if card_drop_cb:
            self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        txt = event.mimeData().text() if event.mimeData().hasText() else ""
        if txt.startswith(_CARD_MIME + ":"):
            self.setStyleSheet(self._SS_DROP)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.setStyleSheet(self._SS_NORMAL)

    def dropEvent(self, event):
        self.setStyleSheet(self._SS_NORMAL)
        txt = event.mimeData().text()
        if txt.startswith(_CARD_MIME + ":") and self._card_drop_cb:
            pid = txt.split(":", 1)[1]
            event.acceptProposedAction()
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, lambda p=pid: self._card_drop_cb(p))


class _ColumnInner(QWidget):
    """Scroll area inner widget for a person column.
    Accepts REORDER_PERSON_GROUP drops onto empty space (appends group to bottom).
    Optionally accepts PERSON_CARD drops via card_drop_cb(pid)."""

    _SS_NORMAL = ""
    _SS_DROP   = "QWidget { background:#1e1e2a; border:1px dashed #5566bb; border-radius:4px; }"

    def __init__(self, card_drop_cb=None, reorder_cb=None, parent=None):
        super().__init__(parent)
        self._card_drop_cb = card_drop_cb
        self._reorder_cb = reorder_cb
        self.setAcceptDrops(True)
        self._vbox = QVBoxLayout(self)
        self._vbox.setContentsMargins(4, 4, 4, 4)
        self._vbox.setSpacing(6)
        self._vbox.addStretch()

    def vbox(self):
        return self._vbox

    def dragEnterEvent(self, event):
        txt = event.mimeData().text() if event.mimeData().hasText() else ""
        if txt.startswith(_GROUP_MIME + ":"):
            self.setStyleSheet(self._SS_DROP)
            event.acceptProposedAction()
        elif self._card_drop_cb and txt.startswith(_CARD_MIME + ":"):
            self.setStyleSheet(self._SS_DROP)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.setStyleSheet(self._SS_NORMAL)

    def dropEvent(self, event):
        self.setStyleSheet(self._SS_NORMAL)
        txt = event.mimeData().text()
        if txt.startswith(_GROUP_MIME + ":"):
            source_pid = txt.split(":", 1)[1]
            top = self
            while top.parentWidget():
                top = top.parentWidget()
            for w in top.findChildren(_PersonGroup):
                if w._primary_pid == source_pid:
                    if w.parentWidget() and w.parentWidget().layout():
                        w.parentWidget().layout().removeWidget(w)
                    self._vbox.insertWidget(self._vbox.count() - 1, w)
                    event.acceptProposedAction()
                    if self._reorder_cb:
                        from PyQt6.QtCore import QTimer
                        QTimer.singleShot(0, self._reorder_cb)
                    return
        elif self._card_drop_cb and txt.startswith(_CARD_MIME + ":"):
            pid = txt.split(":", 1)[1]
            event.acceptProposedAction()
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, lambda p=pid: self._card_drop_cb(p))


# =============================================================================

class _PersonMixin:
    """Mixin: Person management tab — collapsible group cards with drag-and-drop."""

    def _build_person_tab(self, tabs):
        from PyQt6.QtWidgets import QSplitter
        tab_w = QWidget()
        outer = QVBoxLayout(tab_w)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # ── Header ───────────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        hdr.addWidget(QLabel(_t("Project: / プロジェクト：")))
        self._person_proj_cb = QComboBox()
        self._person_proj_cb.wheelEvent = lambda e: e.ignore()
        _person_projs = ["default"] + sorted(
            f.replace("features_", "").replace(".pt", "")
            for f in os.listdir(attrs_mod.DATA_DIR)
            if f.startswith("features_") and f.endswith(".pt")
        )
        self._person_proj_cb.addItems(_person_projs)
        _cur_proj = getattr(self.app, "current_project", "default") or "default"
        _idx = self._person_proj_cb.findText(_cur_proj)
        self._person_proj_cb.blockSignals(True)
        if _idx >= 0:
            self._person_proj_cb.setCurrentIndex(_idx)
        self._person_proj_cb.blockSignals(False)
        hdr.addWidget(self._person_proj_cb)

        btn_person_load = QPushButton(_t("Load / 読込"))
        btn_person_load.setStyleSheet("background:#1e6e1e; color:white; font-weight:bold; padding:3px 8px;")
        hdr.addWidget(btn_person_load)

        self._btn_person_over = btn_person_over = QPushButton(_t("💾 Overwrite / 💾 上書き保存"))
        btn_person_over.setStyleSheet(cfg.btn_ss("btn_write", self.app.config))
        hdr.addWidget(btn_person_over)

        self._person_undo_stack = []  # list of (proj, registry_dict)
        self._btn_person_undo = QPushButton(_t("↩ Undo / ↩ 元に戻す"))
        self._btn_person_undo.setStyleSheet(cfg.btn_ss("btn_special", self.app.config, "padding:3px 8px;"))
        self._btn_person_undo.setEnabled(False)
        self._btn_person_undo.setToolTip(_t("Undo last Overwrite or Append / 最後の上書き・追加を元に戻す"))
        hdr.addWidget(self._btn_person_undo)

        self._btn_person_append = btn_person_append = QPushButton(_t("💾 Append / 💾 追記"))
        btn_person_append.setStyleSheet(cfg.btn_ss("btn_write", self.app.config))
        hdr.addWidget(btn_person_append)

        self._person_editing_lbl = QLabel()
        self._person_editing_lbl.setStyleSheet("color:#aaa; font-style:italic;")
        hdr.addWidget(self._person_editing_lbl)

        hdr.addStretch()

        btn_new_group = QPushButton(_t("+ New Group / ＋新グループ"))
        btn_new_group.setMinimumWidth(130)
        btn_new_group.setStyleSheet(cfg.btn_ss("btn_add", self.app.config, "border:none; border-radius:3px;"))
        btn_new_group.clicked.connect(self._add_pending_group)
        hdr.addWidget(btn_new_group)
        btn_refresh = QPushButton(_t("↺ Refresh / ↺ 更新"))
        btn_refresh.setMinimumWidth(110)
        btn_refresh.clicked.connect(lambda: self._refresh_person_tab(self._person_proj_cb.currentText() or None))
        hdr.addWidget(btn_refresh)
        btn_cleanup = QPushButton(_t("🧹 Clean up / 🧹 整理"))
        btn_cleanup.setMinimumWidth(130)
        btn_cleanup.setStyleSheet(cfg.btn_ss("btn_remove", self.app.config, "border:none; border-radius:3px;"))
        btn_cleanup.setToolTip(_t(
            "Remove every person whose source image is missing AND has no "
            "file in attrs_data tagged with that ID. / "
            "ソース画像が見つからず、attrs_data にも該当ファイルがない "
            "パーソンをすべて削除します。"))
        btn_cleanup.clicked.connect(self._cleanup_invalid_persons)
        hdr.addWidget(btn_cleanup)

        def _do_person_load():
            name = self._person_proj_cb.currentText() or None
            self._refresh_person_tab(name)
        def _push_undo(proj):
            """Save current state of proj's registry to undo stack."""
            snapshot = dict(attrs_mod.load_person_registry(proj))
            self._person_undo_stack.append((proj, snapshot))
            self._btn_person_undo.setEnabled(True)

        def _do_undo():
            if not self._person_undo_stack:
                return
            proj, snapshot = self._person_undo_stack.pop()
            attrs_mod.save_person_registry(snapshot, proj)
            if not self._person_undo_stack:
                self._btn_person_undo.setEnabled(False)
            # Refresh view if we undid the currently loaded project
            if proj == getattr(self, '_person_tab_project', None):
                self._rebuild_person_groups()

        def _do_person_overwrite():
            import shutil
            from PyQt6.QtWidgets import QMessageBox, QCheckBox as _QCB
            target = self._person_proj_cb.currentText() or None
            src_proj = self._person_tab_project
            src_f = attrs_mod.person_registry_file_for_project(src_proj)
            dst_f = attrs_mod.person_registry_file_for_project(target)
            if src_f == dst_f:
                return
            if not getattr(self, '_person_overwrite_skip_warn', False):
                _mb = QMessageBox(self)
                _mb.setIcon(QMessageBox.Icon.Warning)
                _mb.setWindowTitle(_t("Overwrite / 上書き確認"))
                _mb.setText(_t(f"This will overwrite the person registry for <b>'{target or 'default'}'</b>.<br>Continue? / <b>'{target or 'default'}'</b> の人物レジストリを上書きします。<br>続けますか？"))
                _mb.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                _cb = _QCB(_t("Don't show this warning again / 次回から表示しない"))
                _mb.setCheckBox(_cb)
                if _mb.exec() != QMessageBox.StandardButton.Yes:
                    return
                if _cb.isChecked():
                    self._person_overwrite_skip_warn = True
            _push_undo(target)
            if os.path.exists(src_f):
                shutil.copy2(src_f, dst_f)
            if hasattr(self, '_btn_person_over'):
                self._flash_saved_btn(self._btn_person_over)

        def _do_person_append():
            from PyQt6.QtWidgets import QMessageBox
            target = self._person_proj_cb.currentText() or None
            src_proj = self._person_tab_project
            if attrs_mod.person_registry_file_for_project(src_proj) == \
               attrs_mod.person_registry_file_for_project(target):
                return
            src_reg = attrs_mod.load_person_registry(src_proj)
            dst_reg = attrs_mod.load_person_registry(target)
            added = 0
            for pid, desc in src_reg.items():
                if pid not in dst_reg:
                    dst_reg[pid] = desc
                    added += 1
            _push_undo(target)
            attrs_mod.save_person_registry(dst_reg, target)
            if hasattr(self, '_btn_person_append'):
                self._flash_saved_btn(self._btn_person_append)

        from PyQt6.QtGui import QKeySequence
        from PyQt6.QtGui import QShortcut
        _undo_sc = QShortcut(QKeySequence("Ctrl+Z"), tab_w)
        _undo_sc.activated.connect(_do_undo)

        btn_person_load.clicked.connect(_do_person_load)
        btn_person_over.clicked.connect(_do_person_overwrite)
        btn_person_append.clicked.connect(_do_person_append)
        self._btn_person_undo.clicked.connect(_do_undo)
        outer.addLayout(hdr)

        # ── Origin assignment bar ─────────────────────────────────────────────
        # Shown when the user clicked the canvas 👤 button. Lets them assign
        # a clicked-card's pid to the origin file's P / PI / PW field without
        # leaving Settings.
        _origin_bar = QHBoxLayout()
        self._persons_origin_lbl = QLabel("")
        self._persons_origin_lbl.setStyleSheet(
            "color:#cfd8e0; font-family:monospace; font-size:9pt;"
            " padding:4px 8px; background:#1e2630; border-radius:4px;")
        self._persons_origin_lbl.setWordWrap(True)
        self._persons_origin_lbl.hide()
        _origin_bar.addWidget(self._persons_origin_lbl, stretch=1)
        def _mk_assign_btn(text, field):
            b = QPushButton(text)
            b.setEnabled(False)
            b.setMinimumWidth(60)
            b.setStyleSheet(
                "QPushButton{background:#2e4a2e;color:#cfe8cf;border:1px solid #4a6a4a;"
                "border-radius:3px;font-weight:bold;padding:4px 10px;}"
                "QPushButton:hover{background:#3a5e3a;}"
                "QPushButton:disabled{color:#555;border-color:#333;background:#222;}")
            b.clicked.connect(lambda _=None, _f=field: self._assign_pid_to_origin(_f))
            return b
        self._persons_btn_p  = _mk_assign_btn("→ P",  "person_id")
        self._persons_btn_pi = _mk_assign_btn("→ PI", "pi")
        self._persons_btn_pw = _mk_assign_btn("→ PW", "persons_with")
        _origin_bar.addWidget(self._persons_btn_p)
        _origin_bar.addWidget(self._persons_btn_pi)
        _origin_bar.addWidget(self._persons_btn_pw)
        outer.addLayout(_origin_bar)
        self._persons_origin_path = ""
        self._persons_selected_card = None
        self._persons_selected_pid = ""

        # ── Two-column splitter ───────────────────────────────────────────────
        from PyQt6.QtWidgets import QSplitter
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(6)
        splitter.setStyleSheet(
            "QSplitter::handle { background:#3a3a3a; border-radius:3px; }"
            "QSplitter::handle:hover { background:#5566bb; }")

        def _make_column(label_text, card_drop_cb=None):
            col_w = QWidget()
            col_lay = QVBoxLayout(col_w)
            col_lay.setContentsMargins(0, 0, 0, 0)
            col_lay.setSpacing(0)
            lbl = _ColumnHeader(label_text, card_drop_cb=card_drop_cb)
            col_lay.addWidget(lbl)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            inner = _ColumnInner(card_drop_cb=card_drop_cb,
                                 reorder_cb=self._save_column_order)
            scroll.setWidget(inner)
            col_lay.addWidget(scroll)
            splitter.addWidget(col_w)
            return inner.vbox()

        self._person_groups_vbox = _make_column(_t("Groups / グループ"),   card_drop_cb=self._add_group_from_card_drop)
        self._unsorted_vbox      = _make_column(_t("Unsorted / 未分類"), card_drop_cb=self._unlink_person)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        outer.addWidget(splitter)
        self._person_splitter = splitter

        # Loading placeholder — shown until first _rebuild_person_groups completes
        self._person_loading_lbl = QLabel(_t("Loading… / 読み込み中…"))
        self._person_loading_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._person_loading_lbl.setStyleSheet("color:#888; font-size:16px; padding:40px;")
        outer.addWidget(self._person_loading_lbl)
        splitter.setVisible(False)

        tabs.addTab(tab_w, _t("👤 Persons / 👤 人物"))
        QTimer.singleShot(0, lambda: self._refresh_person_tab(self._person_proj_cb.currentText() or None))

    # ─────────────────────────────────────────────────────────────────────────

    def _refresh_person_tab(self, proj=None):
        if proj is None:
            proj = getattr(self.app, "current_project", "")
        self._person_tab_project = proj
        if not hasattr(self, '_person_proj_cb'):
            return  # tab not built yet
        cb = self._person_proj_cb
        _idx = cb.findText(proj or "default")
        if _idx >= 0:
            cb.blockSignals(True)
            cb.setCurrentIndex(_idx)
            cb.blockSignals(False)
        if hasattr(self, '_person_editing_lbl'):
            self._person_editing_lbl.setText(_t(f"Editing: {proj or 'default'} / 編集中: {proj or 'default'}"))
        self._rebuild_person_groups()

    def _rebuild_person_groups(self):
        # Clear left (groups) — keep only the stretch at end
        left_lay = self._person_groups_vbox
        while left_lay.count() > 1:
            item = left_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Clear right (unsorted) — keep only the stretch at end
        right_lay = self._unsorted_vbox
        while right_lay.count() > 1:
            item = right_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        proj       = getattr(self, "_person_tab_project", None) or getattr(self.app, "current_project", "")
        registry   = attrs_mod.load_person_registry(proj)
        db         = attrs_mod.load_faces_db(proj)
        faces      = db.get("faces", {})
        aliases      = attrs_mod.load_person_aliases()
        right_groups = attrs_mod.load_right_groups()
        all_pids   = sorted(p for p in faces.keys() if p != "000")

        pid_to_path = {}
        # Primary: face DB source_path
        for fkey, fdata in faces.items():
            sp = fdata.get("source_path", "")
            if sp and fkey not in pid_to_path and os.path.exists(sp):
                pid_to_path[fkey] = sp
        # Fallback: attrs_data — covers files renamed after face detection
        for fpath, entry in getattr(self.app, "attrs_data", {}).items():
            pid = entry.get("person_id", "")
            if pid and pid not in pid_to_path and os.path.exists(fpath):
                pid_to_path[pid] = fpath

        def _make_group(grp_pids, is_right=False):
            return _PersonGroup(
                pids=grp_pids,
                registry=registry,
                pid_to_path=pid_to_path,
                save_name_cb=self._save_person_name,
                preview_cb=self._open_person_preview,
                unlink_cb=self._unlink_person,
                changed_cb=self._rebuild_person_groups,
                reorder_cb=self._save_column_order,
                is_right=is_right,
                reassign_cb=self._reassign_person_group,
            )

        # ── Left: alias groups ────────────────────────────────────────────────
        left_grouped = set()
        named_groups = []
        for grp in aliases:
            members = sorted(p for p in grp if p in all_pids)
            if members:
                named_groups.append(members)
                left_grouped.update(members)

        for i, grp_pids in enumerate(named_groups):
            left_lay.insertWidget(i, _make_group(grp_pids))

        if not named_groups:
            lbl = QLabel(_t("No groups yet.\nUse + New Group or drag\ncards together. / グループはまだありません。\n＋新グループを使うか、\nカードをドラッグして作成してください。"))
            lbl.setStyleSheet("color:#555; font-size:10px;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            left_lay.insertWidget(0, lbl)

        # ── Right: ordered entries from right_groups, then any untracked pids ──
        right_placed = set()
        right_entries = []  # list of pid-lists in saved display order
        for grp in right_groups:
            members = sorted(p for p in grp if p in all_pids and p not in left_grouped)
            if members:
                right_entries.append(members)
                right_placed.update(members)

        # Pids not yet in right_groups: append sorted at the end
        for p in sorted(p for p in all_pids if p not in left_grouped and p not in right_placed):
            right_entries.append([p])

        for idx, entry in enumerate(right_entries):
            right_lay.insertWidget(idx, _make_group(entry, is_right=True))

        if not right_entries:
            lbl = QLabel(_t("All persons grouped. / 全人物がグループ済みです。"))
            lbl.setStyleSheet("color:#555; font-size:10px;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            right_lay.insertWidget(0, lbl)

        # Hide loading label, show content
        _loading = getattr(self, '_person_loading_lbl', None)
        if _loading and _loading.isVisible():
            _loading.setVisible(False)
        _splitter = getattr(self, '_person_splitter', None)
        if _splitter and not _splitter.isVisible():
            _splitter.setVisible(True)

    def _open_person_preview(self, pid, src):
        # When the user opened Settings via the canvas 👤 button, an origin
        # path is recorded — additionally highlight the card so the
        # selection-and-assign workflow still works. Preview ALWAYS runs
        # too: the user wants to see who they're picking.
        if getattr(self, "_persons_origin_path", ""):
            for c in self.findChildren(_PersonCard):
                if (getattr(c, "_pid", "") or "").strip().lower() == (pid or "").strip().lower():
                    self._select_person_card(c)
                    break
        ph = getattr(getattr(self, "app", None), "preview_handler", None)
        # Try source_path first
        if src and os.path.exists(src):
            if ph:
                ph.show(src)
            return
        # Fallback: any file in attrs_data tagged with this person_id
        for fpath, entry in getattr(self.app, "attrs_data", {}).items():
            if entry.get("person_id") == pid and os.path.exists(fpath):
                if ph:
                    ph.show(fpath)
                return
        # No image found anywhere — the person has no surviving source file
        # AND no file in attrs_data is tagged with this pid. The card points
        # at a path that no longer exists, so remove the person from the
        # registry/faces DB/aliases and rebuild the list. Confirm first so
        # a missed mount or transient I/O issue doesn't auto-purge data.
        from PyQt6.QtWidgets import QMessageBox
        bad = src if src else _t("(no source)")
        ans = QMessageBox.question(
            self, _t("Invalid path / 無効なパス"),
            _t(f"No image found for {pid}.\nSource: {bad}\n\n"
               f"Delete this person from the list? / "
               f"{pid} の画像が見つかりません。\nソース: {bad}\n\n"
               f"このパーソンをリストから削除しますか？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ans == QMessageBox.StandardButton.Yes:
            self._delete_person(pid)

    def _cleanup_invalid_persons(self):
        """Bulk-remove every person ID whose source image path is missing
        AND has no file in attrs_data tagged with that pid. Confirms with
        a preview list of pids before deleting so a missed mount doesn't
        wipe real data."""
        proj = getattr(self, "_person_tab_project", None) or getattr(self.app, "current_project", "")
        registry = attrs_mod.load_person_registry(proj)
        if not registry:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(self, _t("Clean up / 整理"),
                                    _t("No persons registered. / パーソンが登録されていません。"))
            return
        # Build pid → set of paths from attrs_data once
        attrs_data = getattr(self.app, "attrs_data", {}) or {}
        pid_has_attrs_file = {}
        for fpath, entry in attrs_data.items():
            _pid = (entry.get("person_id") or "").strip()
            if not _pid:
                continue
            if pid_has_attrs_file.get(_pid):
                continue
            if os.path.exists(fpath):
                pid_has_attrs_file[_pid] = True
        # Pid → source image path (face_card sources are stored in faces DB)
        faces_db = attrs_mod.load_faces_db(proj)
        pid_src = {}
        for _pid, _entries in (faces_db.get("faces", {}) or {}).items():
            # entries is a list of {emb, source_path} dicts
            if isinstance(_entries, list):
                for e in _entries:
                    sp = e.get("source_path") if isinstance(e, dict) else None
                    if sp:
                        pid_src.setdefault(_pid, sp)
                        break
        # Find invalid pids
        invalid = []
        for _pid in registry.keys():
            src = pid_src.get(_pid, "")
            src_ok = bool(src) and os.path.exists(src)
            attrs_ok = bool(pid_has_attrs_file.get(_pid))
            if not src_ok and not attrs_ok:
                invalid.append(_pid)
        if not invalid:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(
                self, _t("Clean up / 整理"),
                _t(f"Nothing to clean — all {len(registry)} persons have a valid image. / "
                   f"整理対象なし — {len(registry)} 名すべての画像が有効です。"))
            return
        from PyQt6.QtWidgets import QMessageBox
        preview = "\n".join(f"  • {p}" for p in invalid[:30])
        more = f"\n  …and {len(invalid)-30} more" if len(invalid) > 30 else ""
        ans = QMessageBox.question(
            self, _t("Clean up invalid persons / 無効パーソン整理"),
            _t(f"Delete {len(invalid)} person(s) with no surviving image?\n\n"
               f"{preview}{more}\n\n"
               f"This removes them from the registry, faces DB, and aliases. / "
               f"画像のないパーソン {len(invalid)} 名を削除しますか？\n\n"
               f"{preview}{more}\n\n"
               f"レジストリ、顔 DB、エイリアスから削除されます。"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ans != QMessageBox.StandardButton.Yes:
            return
        for _pid in invalid:
            self._delete_person(_pid)
        QMessageBox.information(
            self, _t("Clean up / 整理"),
            _t(f"Removed {len(invalid)} person(s). / {len(invalid)} 名を削除しました。"))

    def _delete_person(self, pid):
        """Remove a person ID from registry, faces DB, and aliases."""
        proj = getattr(self, "_person_tab_project", None) or getattr(self.app, "current_project", "")
        # Registry
        registry = attrs_mod.load_person_registry(proj)
        registry.pop(pid, None)
        attrs_mod.save_person_registry(registry, proj)
        # Faces DB
        db = attrs_mod.load_faces_db(proj)
        db.get("faces", {}).pop(pid, None)
        attrs_mod.save_faces_db(proj, db)
        # Aliases
        attrs_mod.remove_person_from_aliases(pid)
        self._rebuild_person_groups()

    def _unlink_person(self, pid):
        attrs_mod.remove_person_from_aliases(pid)
        attrs_mod.remove_from_right_group(pid)
        self._rebuild_person_groups()

    def _save_person_name(self, pid, name):
        proj = getattr(self.app, "current_project", "")
        # Save name to all members of the alias group (group name, not per-ID name)
        aliases = attrs_mod.load_person_aliases()
        group = {pid}
        for grp in aliases:
            if pid in grp:
                group = set(grp)
                break
        for member in group:
            attrs_mod.set_person_name(proj, member, name)
        pw = getattr(getattr(self.app, "preview_handler", None), "window", None)
        if pw and hasattr(pw, "_refresh_person_id_combo"):
            pw._refresh_person_id_combo()

    def _add_group_from_card_drop(self, pid):
        """Card dropped onto the left column empty area → new single-member alias group."""
        attrs_mod.remove_person_from_aliases(pid)
        attrs_mod.remove_from_right_group(pid)
        aliases = attrs_mod.load_person_aliases()
        aliases.append([pid])
        attrs_mod.save_person_aliases(aliases)
        self._rebuild_person_groups()

    def _add_pending_group(self):
        pending = _PendingGroup(promote_cb=self._promote_pending_group)
        lay = self._person_groups_vbox
        lay.insertWidget(lay.count() - 1, pending)  # before the trailing stretch

    def _promote_pending_group(self, pending_widget, pid):
        """Replace the pending drop zone with a real single-member group."""
        proj       = getattr(self, "_person_tab_project", None) or getattr(self.app, "current_project", "")
        registry   = attrs_mod.load_person_registry(proj)
        db         = attrs_mod.load_faces_db(proj)
        faces      = db.get("faces", {})
        pid_to_path = {}
        for fkey, fdata in faces.items():
            sp = fdata.get("source_path", "")
            if sp and fkey not in pid_to_path and os.path.exists(sp):
                pid_to_path[fkey] = sp
        for fpath, entry in getattr(self.app, "attrs_data", {}).items():
            epid = entry.get("person_id", "")
            if epid and epid not in pid_to_path and os.path.exists(fpath):
                pid_to_path[epid] = fpath

        # Persist as a single-member alias group so it survives refresh
        aliases = attrs_mod.load_person_aliases()
        if not any(pid in grp for grp in aliases):
            aliases.append([pid])
            attrs_mod.save_person_aliases(aliases)

        grp = _PersonGroup(
            pids=[pid],
            registry=registry,
            pid_to_path=pid_to_path,
            save_name_cb=self._save_person_name,
            preview_cb=self._open_person_preview,
            unlink_cb=self._unlink_person,
            changed_cb=self._rebuild_person_groups,
        )
        lay = self._person_groups_vbox
        idx = lay.indexOf(pending_widget)
        lay.removeWidget(pending_widget)
        pending_widget.deleteLater()
        lay.insertWidget(idx, grp)

    # Style applied to the currently-selected person card (orange border).
    # Persists until the user picks a different card or closes the dialog —
    # not time-limited, since it represents the active selection target for
    # the P/PI/PW assignment buttons.
    _PERSON_SEL_SS = (
        "QFrame { background:#3a2a4a; border:3px solid #ffaa00; border-radius:5px; }")

    def _focus_person(self, pid: str, origin_path: str | None = None):
        """Switch to Persons tab, highlight the card matching `pid`, and (if
        provided) store `origin_path` so the in-tab P/PI/PW buttons can write
        the user-chosen card's pid back to that file's entry."""
        try:
            self.tabs.setCurrentIndex(1)
        except Exception:
            pass
        # Store origin even if pid is empty — user may pick a card to assign
        if origin_path is not None:
            self._persons_origin_path = origin_path
            self._refresh_persons_origin_bar()
        pid = (pid or "").strip().lower()
        from PyQt6.QtCore import QTimer
        def _try(retry=0):
            target = None
            for c in self.findChildren(_PersonCard):
                if (getattr(c, "_pid", "") or "").strip().lower() == pid:
                    target = c
                    break
            if target is None:
                if retry < 8:
                    QTimer.singleShot(120, lambda: _try(retry + 1))
                return
            from PyQt6.QtWidgets import QScrollArea
            sa = target.parent()
            while sa is not None and not isinstance(sa, QScrollArea):
                sa = sa.parent()
            if isinstance(sa, QScrollArea):
                sa.ensureWidgetVisible(target, 50, 50)
            self._select_person_card(target)
        if pid:
            _try()

    def _select_person_card(self, card):
        """Mark `card` as the active selection — restores any prior card's
        original style and applies the orange highlight to this one. The
        selected pid is what P/PI/PW assignment buttons write back to the
        origin file."""
        prev = getattr(self, "_persons_selected_card", None)
        if prev is not None and prev is not card:
            try:
                prev.setStyleSheet(getattr(prev, "_orig_ss_for_select", _PersonCard._SS_NORMAL))
            except Exception:
                pass
        if card is None:
            self._persons_selected_card = None
            self._persons_selected_pid = ""
            return
        # Stash the card's pre-selection stylesheet on first selection so we
        # can restore it cleanly when selection moves elsewhere.
        if not hasattr(card, "_orig_ss_for_select"):
            card._orig_ss_for_select = card.styleSheet()
        card.setStyleSheet(self._PERSON_SEL_SS)
        self._persons_selected_card = card
        self._persons_selected_pid = (getattr(card, "_pid", "") or "").strip().lower()
        self._refresh_persons_origin_bar()

    def _refresh_persons_origin_bar(self):
        """Update the origin-info label and enable/disable assignment buttons
        based on whether an origin file and a selected card both exist."""
        lbl = getattr(self, "_persons_origin_lbl", None)
        if lbl is None:
            return
        origin = getattr(self, "_persons_origin_path", "") or ""
        sel    = getattr(self, "_persons_selected_pid", "") or ""
        if origin:
            import os
            _bn = os.path.basename(origin)
            _dir = os.path.dirname(origin)
            txt = f"📁 {_bn}\n{_dir}"
            if sel:
                txt += f"\n→ assign P{sel} to:"
            else:
                txt += "\n(click a card to select an ID)"
            lbl.setText(txt)
            lbl.show()
        else:
            lbl.setText("")
            lbl.hide()
        for _b in (getattr(self, "_persons_btn_p", None),
                   getattr(self, "_persons_btn_pi", None),
                   getattr(self, "_persons_btn_pw", None)):
            if _b is not None:
                _b.setEnabled(bool(origin and sel))

    def _assign_pid_to_origin(self, field: str):
        """Write the currently-selected card's pid into the origin file's
        attrs entry under `field` (one of 'person_id', 'pi', 'persons_with')
        and refresh the preview canvas if open."""
        origin = getattr(self, "_persons_origin_path", "") or ""
        pid    = getattr(self, "_persons_selected_pid", "") or ""
        if not origin or not pid:
            return
        proj = getattr(self.app, "current_project", None)
        attrs_data = getattr(self.app, "attrs_data", {})
        entry = attrs_data.setdefault(origin, {})
        if field == "persons_with":
            existing = [p for p in (entry.get("persons_with") or []) if p]
            if pid not in existing:
                existing.append(pid)
            entry["persons_with"] = existing
        else:
            entry[field] = pid
        attrs_mod.save(proj, attrs_data)
        # Refresh preview canvas if it's showing this same file
        try:
            ph = getattr(self.app, "preview_handler", None)
            pw = getattr(ph, "window", None) if ph else None
            if pw is not None and getattr(pw, "_attr_path", None) == origin:
                pw._refresh_attrs_inner(origin)
        except Exception:
            pass

    def _save_column_order(self):
        """Persist the current widget order of both columns to disk."""
        # Left: save alias groups in current layout order
        new_aliases = []
        for i in range(self._person_groups_vbox.count()):
            w = self._person_groups_vbox.itemAt(i).widget()
            if isinstance(w, _PersonGroup):
                new_aliases.append(w._pids)
        attrs_mod.save_person_aliases(new_aliases)

        # Right: save all right-column entries (multi + single) in current layout order
        new_right = []
        for i in range(self._unsorted_vbox.count()):
            w = self._unsorted_vbox.itemAt(i).widget()
            if isinstance(w, _PersonGroup):
                new_right.append(w._pids)
        attrs_mod.save_right_groups(new_right)

    def _reassign_person_group(self, old_pids, new_id):
        """Reassign all IDs in old_pids to new_id, one by one."""
        proj = getattr(self, "_person_tab_project", None) or getattr(self.app, "current_project", "")
        attrs_data = getattr(self.app, "attrs_data", {})
        for old_id in old_pids:
            if old_id == new_id:
                continue
            attrs_data = attrs_mod.reassign_person_id(old_id, new_id, proj, attrs_data)
        attrs_mod.save(proj, attrs_data)
        self.app.attrs_data = attrs_data
        self._rebuild_person_groups()
        # Refresh preview combo if open
        pw = getattr(getattr(self.app, "preview_handler", None), "window", None)
        if pw and hasattr(pw, "_refresh_person_id_combo"):
            pw._refresh_person_id_combo(force=True)

    def _refresh_person_combos(self):
        if hasattr(self, "_person_groups_vbox"):
            self._rebuild_person_groups()

    def _refresh_alias_list(self):
        pass
