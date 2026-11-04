"""Continuous PDF viewer with mouse text selection, built on PyMuPDF.

Pages are rendered to fit the window width when they scroll into view. Dragging selects words in reading
order (across pages too), double-click selects a word, Shift-click extends the selection.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Optional

try:
    import pymupdf as fitz
except ImportError:  # older PyMuPDF
    import fitz

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import QFrame, QScrollArea, QVBoxLayout, QWidget

MARGIN = 14
SPACING = 12
MAX_PIXMAPS = 16

Position = tuple  # (page index, word index)


class PageWidget(QWidget):
    def __init__(self, view: "PdfView", index: int, width_pt: float, height_pt: float):
        super().__init__()
        self.view = view
        self.index = index
        self.width_pt = width_pt
        self.height_pt = height_pt
        self.scale = 1.0
        self.pixmap: Optional[QPixmap] = None
        self.pixmap_scale = 0.0
        self.setCursor(Qt.CursorShape.IBeamCursor)

    def set_scale(self, scale: float) -> None:
        self.scale = scale
        self.setFixedSize(max(1, round(self.width_pt * scale)), max(1, round(self.height_pt * scale)))
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.white)
        pixmap = self.view.pixmap_for(self)
        if pixmap is not None:
            painter.drawPixmap(0, 0, pixmap)
        highlight = QColor(self.palette().highlight().color())
        highlight.setAlpha(90)
        s = self.scale
        for x0, y0, x1, y1 in self.view.selected_rects(self.index):
            painter.fillRect(QRectF(x0 * s, y0 * s, (x1 - x0) * s, (y1 - y0) * s), highlight)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.view.press(self, event.position(), event.modifiers())

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.view.drag(self, event.position())

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.view.release()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.view.double_click(self, event.position())


class PdfView(QScrollArea):
    selection_finished = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.doc = None
        self.pages: list[PageWidget] = []
        self._words: dict[int, list[tuple]] = {}
        self._rendered: OrderedDict[int, None] = OrderedDict()
        self.anchor: Optional[Position] = None
        self.head: Optional[Position] = None
        self._dragging = False
        self._moved = False

        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidgetResizable(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.container = QWidget()
        self.container.setAutoFillBackground(False)
        self.layout_ = QVBoxLayout(self.container)
        self.layout_.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        self.layout_.setSpacing(SPACING)
        self.layout_.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.setWidget(self.container)

        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.setInterval(80)
        self._relayout_timer.timeout.connect(self.relayout)

    # ---- document ----

    def set_document(self, doc) -> None:
        for page in self.pages:
            self.layout_.removeWidget(page)
            page.deleteLater()
        self.pages = []
        self._words.clear()
        self._rendered.clear()
        self.anchor = self.head = None
        self.doc = doc
        if doc is not None:
            for i in range(doc.page_count):
                rect = doc[i].rect
                page = PageWidget(self, i, rect.width, rect.height)
                self.pages.append(page)
                self.layout_.addWidget(page, 0, Qt.AlignmentFlag.AlignHCenter)
        self.relayout()
        self.verticalScrollBar().setValue(0)

    def relayout(self) -> None:
        if not self.pages:
            return
        available = max(100, self.viewport().width() - 2 * MARGIN)
        widest = max(p.width_pt for p in self.pages)
        scale = max(0.2, available / widest)
        for page in self.pages:
            page.set_scale(scale)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout_timer.start()

    def pixmap_for(self, page: PageWidget) -> Optional[QPixmap]:
        if self.doc is None:
            return None
        if page.pixmap is None or page.pixmap_scale != page.scale:
            ratio = page.devicePixelRatioF()
            zoom = page.scale * ratio
            pix = self.doc[page.index].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
            pixmap = QPixmap.fromImage(image)
            pixmap.setDevicePixelRatio(ratio)
            page.pixmap, page.pixmap_scale = pixmap, page.scale
        self._rendered[page.index] = None
        self._rendered.move_to_end(page.index)
        while len(self._rendered) > MAX_PIXMAPS:     # keep memory bounded on long PDFs
            old, _ = self._rendered.popitem(last=False)
            self.pages[old].pixmap = None
        return page.pixmap

    def current_page_index(self) -> int:
        """The page that takes up most of the visible area."""
        if not self.pages:
            return 0
        top = self.verticalScrollBar().value()
        bottom = top + self.viewport().height()
        best, best_visible = 0, -1
        for page in self.pages:
            geometry = page.geometry()
            visible = min(bottom, geometry.bottom()) - max(top, geometry.top())
            if visible > best_visible:
                best, best_visible = page.index, visible
        return best

    def page_text(self, index: int) -> str:
        return self.doc[index].get_text() if self.doc is not None else ""

    # ---- words and hit testing ----

    def words(self, index: int) -> list[tuple]:
        """Words of a page in reading order: (x0, y0, x1, y1, text, block, line), in page coordinates."""
        if index not in self._words:
            page = self.doc[index]
            matrix = page.rotation_matrix if page.rotation else None
            words = []
            for x0, y0, x1, y1, text, block, line, _ in page.get_text("words"):
                if matrix is not None:
                    r = fitz.Rect(x0, y0, x1, y1) * matrix
                    x0, y0, x1, y1 = r.x0, r.y0, r.x1, r.y1
                words.append((x0, y0, x1, y1, text, block, line))
            self._words[index] = words
        return self._words[index]

    def hit(self, index: int, pos: QPointF) -> Optional[int]:
        words = self.words(index)
        if not words:
            return None
        scale = self.pages[index].scale
        x, y = pos.x() / scale, pos.y() / scale
        best, best_score = None, float("inf")
        for i, (x0, y0, x1, y1, *_rest) in enumerate(words):
            dx = max(x0 - x, 0, x - x1)
            dy = max(y0 - y, 0, y - y1)
            if dx == 0 and dy == 0:
                return i
            score = dy * 4 + dx
            if score < best_score:
                best, best_score = i, score
        return best

    def _page_at(self, point: QPoint) -> Optional[PageWidget]:
        if not self.pages:
            return None
        for page in self.pages:
            g = page.geometry()
            if point.y() <= g.bottom() + SPACING // 2:
                return page
        return self.pages[-1]

    # ---- selection ----

    def _range(self):
        if self.anchor is None or self.head is None:
            return None
        return min(self.anchor, self.head), max(self.anchor, self.head)

    def _span(self, index: int, selection) -> Optional[tuple[int, int]]:
        (start_page, start_word), (end_page, end_word) = selection
        if not start_page <= index <= end_page:
            return None
        count = len(self.words(index))
        if count == 0:
            return None
        first = start_word if index == start_page else 0
        last = end_word if index == end_page else count - 1
        return first, last

    def selected_rects(self, index: int) -> list[tuple]:
        selection = self._range()
        if selection is None:
            return []
        span = self._span(index, selection)
        if span is None:
            return []
        rects, key, current = [], None, None
        for x0, y0, x1, y1, _text, block, line in self.words(index)[span[0]:span[1] + 1]:
            if (block, line) == key:
                current = [min(current[0], x0), min(current[1], y0), max(current[2], x1), max(current[3], y1)]
            else:
                if current:
                    rects.append(tuple(current))
                key, current = (block, line), [x0, y0, x1, y1]
        if current:
            rects.append(tuple(current))
        return rects

    def selected_text(self) -> str:
        selection = self._range()
        if selection is None:
            return ""
        pages = []
        for index in range(selection[0][0], selection[1][0] + 1):
            span = self._span(index, selection)
            if span is None:
                continue
            text, previous = "", None
            for *_box, word, block, line in self.words(index)[span[0]:span[1] + 1]:
                if previous is None:
                    text = word
                elif block != previous[0]:
                    text += "\n\n" + word
                elif line != previous[1]:
                    text += "\n" + word
                else:
                    text += " " + word
                previous = (block, line)
            pages.append(text)
        return "\n".join(pages)

    def clear_selection(self) -> None:
        if self.anchor is not None:
            self.anchor = self.head = None
            self.container.update()
            for page in self.pages:
                page.update()

    def _refresh(self) -> None:
        for page in self.pages:
            if not page.visibleRegion().isEmpty():
                page.update()

    def press(self, page: PageWidget, pos: QPointF, modifiers) -> None:
        self.setFocus()
        index = self.hit(page.index, pos)
        self._dragging = True
        if modifiers & Qt.KeyboardModifier.ShiftModifier and self.anchor is not None and index is not None:
            self.head = (page.index, index)
            self._moved = True
        else:
            self.anchor = self.head = (page.index, index) if index is not None else None
            self._moved = False
        self._refresh()

    def drag(self, page: PageWidget, pos: QPointF) -> None:
        if not self._dragging:
            return
        point = page.mapTo(self.container, pos.toPoint())
        target = self._page_at(point)
        if target is None:
            return
        index = self.hit(target.index, QPointF(target.mapFrom(self.container, point)))
        if index is None:
            return
        if self.anchor is None:
            self.anchor = (target.index, index)
        new_head = (target.index, index)
        if new_head != self.head or not self._moved:
            self.head = new_head
            self._moved = True
            self._refresh()
        self.ensureVisible(point.x(), point.y(), 0, 30)

    def release(self) -> None:
        if not self._dragging:
            return
        self._dragging = False
        if self._moved and self.anchor is not None:
            text = self.selected_text()
            if text.strip():
                self.selection_finished.emit(text)
        else:
            self.clear_selection()

    def double_click(self, page: PageWidget, pos: QPointF) -> None:
        index = self.hit(page.index, pos)
        self._dragging = False
        if index is None:
            return
        self.anchor = self.head = (page.index, index)
        self._refresh()
        word = self.words(page.index)[index][4].strip(".,;:!?()[]{}\"'“”‘’«»،؛؟")
        if word:
            self.selection_finished.emit(word)

    def keyPressEvent(self, event) -> None:
        if event.matches(QKeySequence.StandardKey.Copy) and self.anchor is not None:
            QGuiApplication.clipboard().setText(self.selected_text())
            return
        super().keyPressEvent(event)
