import os
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                              QLabel, QLineEdit, QToolButton, QScrollArea,
                              QApplication, QStyle, QCheckBox)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QDrag, QPixmap
from PyQt6.QtCore import QMimeData


class _WsSec(QWidget):
    """Collapsible workspace section for the Attributes tab with Drag and Drop.

    `editable=True` (yellow rows) shows a 🔒 Protected checkbox in the
    header; when checked, _save_attr_groups preserves the section's
    existing tag-group entries instead of overwriting them with whatever
    the editor currently shows. State persists in attrs_tags __protected__.
    """
    def __init__(self, title: str, prefix: str = "", parent=None,
                 color: str = "#f0c040", editable: bool = True):
        super().__init__(parent)
        self.prefix = prefix
        self.setAcceptDrops(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 1, 0, 1)
        root.setSpacing(0)

        self.hdr = QWidget()
        self.hdr.setStyleSheet("background:#2d2d2d;")
        hdr_lay = QHBoxLayout(self.hdr)
        hdr_lay.setContentsMargins(2, 1, 2, 1)
        hdr_lay.setSpacing(4)

        # Visual Drag Handle (⠿)
        self.handle = QLabel("⠿")
        self.handle.setStyleSheet("color: #666; font-size: 14pt; font-weight: bold; margin-right: 5px;")
        self.handle.setCursor(Qt.CursorShape.OpenHandCursor)
        hdr_lay.addWidget(self.handle)

        self._arrow = QToolButton()
        self._arrow.setArrowType(Qt.ArrowType.RightArrow)
        self._arrow.setCheckable(True)
        self._arrow.setStyleSheet("QToolButton { border:none; background:transparent; }")
        self._arrow.toggled.connect(self._on_toggle)
        hdr_lay.addWidget(self._arrow)

        self._title_lbl = QLabel(title)
        self._title_lbl.setStyleSheet(f"QLabel {{ color:{color}; font-weight:bold; font-size:9pt; }}")
        hdr_lay.addWidget(self._title_lbl, stretch=1)

        # Protected checkbox — only on yellow / editable sections.
        # Checked: _save_attr_groups keeps the existing tag-group data
        # instead of overwriting with the editor's current rows.
        self._protected_cb = None
        if editable:
            self._protected_cb = QCheckBox("🔒")
            self._protected_cb.setToolTip(
                "Protected: keep existing entries on save / overwrite")
            self._protected_cb.setStyleSheet(
                "QCheckBox { color:#cce; font-size:9pt; }"
                "QCheckBox::indicator { width:12px; height:12px; }")
            hdr_lay.addWidget(self._protected_cb)

        self._del_btn = QPushButton()
        self._del_btn.setIcon(QApplication.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon))
        self._del_btn.setFixedSize(22, 20)
        self._del_btn.setStyleSheet(
            "QPushButton { background:#662222; border:1px solid #884444; border-radius:2px; }"
            "QPushButton:hover { background:#882222; }"
        )
        self._del_btn.setToolTip("Delete this section")
        hdr_lay.addWidget(self._del_btn)
        root.addWidget(self.hdr)

        self.content = QWidget()
        self.content.setStyleSheet("background:#1e1e1e;")
        self.content.setVisible(False)
        root.addWidget(self.content)

        self._drag_start_pos = None
        self._build_cb = None   # called once on first expand to lazily build content

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.hdr.underMouse():
            self._drag_start_pos = event.pos()

    def mouseMoveEvent(self, event):
        if not self._drag_start_pos: return
        if (event.pos() - self._drag_start_pos).manhattanLength() < QApplication.startDragDistance(): return

        drag = QDrag(self)
        mime = QMimeData()
        mime.setText(f"REORDER:{self.prefix}")
        drag.setMimeData(mime)

        pixmap = self.hdr.grab()
        drag.setPixmap(pixmap)
        drag.setHotSpot(event.pos() - self.hdr.pos())
        drag.exec(Qt.DropAction.MoveAction)
        self._drag_start_pos = None

    def dragEnterEvent(self, event):
        txt = event.mimeData().text() if event.mimeData().hasText() else ""
        if txt.startswith("REORDER:"):
            event.acceptProposedAction()

    def dropEvent(self, event):
        txt = event.mimeData().text()
        if not txt.startswith("REORDER:"):
            return
        source_pfx = txt.split(":", 1)[1]
        # Search globally — source may be in a different layout (e.g. inside a _WsGroup)
        top = self
        while top.parentWidget():
            top = top.parentWidget()
        source = None
        for w in top.findChildren(_WsSec):
            if w.prefix == source_pfx:
                source = w; break
        if not source or source is self:
            return
        # Remove from current parent layout
        if source.parentWidget() and source.parentWidget().layout():
            source.parentWidget().layout().removeWidget(source)
        # Insert before self in self's parent layout
        dst_lay = self.parentWidget().layout()
        curr_idx = dst_lay.indexOf(self)
        dst_lay.insertWidget(curr_idx, source)
        event.acceptProposedAction()

    def is_protected(self) -> bool:
        """Yellow rows: True if user checked the 🔒 box. Blue / readonly
        rows have no checkbox; treat as not protected."""
        cb = self._protected_cb
        return bool(cb is not None and cb.isChecked())

    def set_protected(self, on: bool):
        """Sync the 🔒 checkbox from saved __protected__ state.
        Blocks signals so the auto-save toggle handler doesn't fire
        during load (otherwise every section open writes the file)."""
        cb = self._protected_cb
        if cb is not None:
            cb.blockSignals(True)
            cb.setChecked(bool(on))
            cb.blockSignals(False)

    def _on_toggle(self, checked: bool):
        # Lazy-build content on first expand
        if checked and self._build_cb is not None:
            cb = self._build_cb
            self._build_cb = None   # clear before calling so re-entrant calls are safe
            cb()
        self.content.setVisible(checked)
        self._arrow.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
        )
        # Update geometry up to the QScrollArea only — not the dialog itself
        from PyQt6.QtWidgets import QScrollArea
        p = self.parentWidget()
        while p is not None:
            p.updateGeometry()
            if isinstance(p, QScrollArea):
                p.widget().adjustSize()
                break
            p = p.parentWidget()

    def set_expanded(self, v: bool):
        self._arrow.setChecked(v)

class _WsGroup(QWidget):
    """Collapsible parent group that contains _WsSec children."""
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._drag_start_pos = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 2, 0, 2)
        root.setSpacing(0)

        self._hdr = QWidget()
        self._hdr.setStyleSheet("background:#1e3a1e;")
        hdr_lay = QHBoxLayout(self._hdr)
        hdr_lay.setContentsMargins(4, 2, 4, 2)
        hdr_lay.setSpacing(4)

        # Drag handle
        _handle = QLabel("⠿")
        _handle.setStyleSheet("color:#446644; font-size:14pt; font-weight:bold; margin-right:4px;")
        _handle.setCursor(Qt.CursorShape.OpenHandCursor)
        hdr_lay.addWidget(_handle)

        self._arrow = QToolButton()
        self._arrow.setArrowType(Qt.ArrowType.DownArrow)
        self._arrow.setCheckable(True)
        self._arrow.setChecked(True)
        self._arrow.setStyleSheet("QToolButton { border:none; background:transparent; }")
        self._arrow.toggled.connect(self._on_toggle)
        hdr_lay.addWidget(self._arrow)

        self._title_e = QLineEdit(title)
        self._title_e.setStyleSheet(
            "background:transparent; color:#88ee88; font-weight:bold; "
            "font-size:10pt; border:none; border-bottom:1px solid #446644;")
        self._title_e.setPlaceholderText("Group name")
        self._title_e.setAcceptDrops(False)
        hdr_lay.addWidget(self._title_e, stretch=1)

        self._del_btn = QPushButton()
        self._del_btn.setIcon(QApplication.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon))
        self._del_btn.setFixedSize(22, 20)
        self._del_btn.setStyleSheet(
            "QPushButton { background:#223322; border:1px solid #446644; border-radius:2px; }"
            "QPushButton:hover { background:#224422; }")
        self._del_btn.setToolTip("Remove group (sections move to ungrouped)")
        self._del_btn.clicked.connect(self._on_delete)
        hdr_lay.addWidget(self._del_btn)
        root.addWidget(self._hdr)

        self.content = QWidget()
        self.content.setStyleSheet("background:#161e16;")
        self._inner = QVBoxLayout(self.content)
        self._inner.setContentsMargins(12, 2, 2, 4)
        self._inner.setSpacing(2)
        root.addWidget(self.content)

    def title(self):
        return self._title_e.text().strip()

    def add_section(self, sec: _WsSec):
        self._inner.addWidget(sec)

    def sections(self):
        result = []
        for i in range(self._inner.count()):
            w = self._inner.itemAt(i).widget()
            if isinstance(w, _WsSec):
                result.append(w)
        return result

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._hdr.underMouse():
            self._drag_start_pos = event.pos()

    def mouseMoveEvent(self, event):
        if not self._drag_start_pos: return
        if (event.pos() - self._drag_start_pos).manhattanLength() < QApplication.startDragDistance(): return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setText(f"REORDER_GROUP:{self.title()}")
        drag.setMimeData(mime)
        pixmap = self._hdr.grab()
        drag.setPixmap(pixmap)
        drag.setHotSpot(event.pos() - self._hdr.pos())
        drag.exec(Qt.DropAction.MoveAction)
        self._drag_start_pos = None

    def _on_toggle(self, checked):
        self.content.setVisible(checked)
        self._arrow.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)
        from PyQt6.QtWidgets import QScrollArea
        p = self.parentWidget()
        while p:
            p.updateGeometry()
            if isinstance(p, QScrollArea):
                p.widget().adjustSize()
                break
            p = p.parentWidget()

    def _on_delete(self):
        parent_lay = self.parentWidget().layout() if self.parentWidget() else None
        if parent_lay is None:
            return
        # Move all contained sections to the parent layout before self
        my_idx = parent_lay.indexOf(self)
        for sec in list(self.sections()):
            self._inner.removeWidget(sec)
            parent_lay.insertWidget(my_idx, sec)
            my_idx += 1
        parent_lay.removeWidget(self)
        self.deleteLater()

    def dragEnterEvent(self, event):
        txt = event.mimeData().text() if event.mimeData().hasText() else ""
        if txt.startswith("REORDER:") or txt.startswith("REORDER_GROUP:"):
            event.acceptProposedAction()

    def dropEvent(self, event):
        txt = event.mimeData().text()
        if txt.startswith("REORDER_GROUP:"):
            # Reorder groups within the parent layout
            source_title = txt.split(":", 1)[1]
            parent_lay = self.parentWidget().layout()
            source = None
            for i in range(parent_lay.count()):
                w = parent_lay.itemAt(i).widget()
                if isinstance(w, _WsGroup) and w.title() == source_title:
                    source = w; break
            if source and source is not self:
                curr_idx = parent_lay.indexOf(self)
                parent_lay.removeWidget(source)
                parent_lay.insertWidget(curr_idx, source)
                event.acceptProposedAction()
            return
        # REORDER: — drop a _WsSec into this group
        source_pfx = txt.split(":", 1)[1]
        top = self
        while top.parentWidget():
            top = top.parentWidget()
        source = None
        for w in top.findChildren(_WsSec):
            if w.prefix == source_pfx:
                source = w; break
        if source:
            self._inner.addWidget(source)
            event.acceptProposedAction()
