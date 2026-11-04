"""Smoke tests for the window: open a PDF, select text with the mouse, translate a page."""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

try:
    import pymupdf as fitz
except ImportError:
    import fitz

from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QApplication

from pdftranslator.cache import TranslationCache
from pdftranslator.main_window import MainWindow
from pdftranslator.models import TranslationResult
from pdftranslator.offline import OfflineEngine
from pdftranslator.translator import Translator


class EchoOnline:
    def translate(self, text, source, target):
        return TranslationResult(f"«{target}» {text}", "en")


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setenv("PDFTRANSLATOR_HOME", str(tmp_path / "home"))
    app = QApplication.instance() or QApplication([])
    translator = Translator(TranslationCache(tmp_path / "c.json"), EchoOnline(), OfflineEngine(tmp_path / "none"))
    w = MainWindow(translator)
    w.settings.clear()
    w.use_offline = False
    w.source_language, w.target_language = "auto", "ar"
    w.resize(1200, 800)
    w.show()
    app.processEvents()
    yield w
    w.close()


def make_pdf(path):
    doc = fitz.open()
    for n in range(3):
        page = doc.new_page()
        page.insert_text((72, 90), f"Page {n + 1} heading", fontsize=16)
        page.insert_text((72, 130), "The quick brown fox jumps over the lazy dog.", fontsize=12)
        page.insert_text((72, 148), "A second line of text continues here.", fontsize=12)
    doc.save(str(path))


def wait_for(condition, timeout=5.0):
    app = QApplication.instance()
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_open_select_and_translate(window, tmp_path):
    pdf = tmp_path / "sample.pdf"
    make_pdf(pdf)
    window.open(pdf)
    assert window.windowTitle() == "sample.pdf"
    view = window.pdf_view
    assert len(view.pages) == 3
    page = view.pages[0]
    words = view.words(0)
    s = page.scale
    first = next(w for w in words if w[4] == "quick")
    last = next(w for w in words if w[4] == "line")
    view.press(page, QPointF(first[0] * s + 1, first[1] * s + 2), Qt.KeyboardModifier.NoModifier)
    view.drag(page, QPointF(last[2] * s - 1, last[3] * s - 2))
    view.release()
    assert wait_for(lambda: window.result is not None)
    assert window.source_label == "Selection"
    assert window.source_text == "quick brown fox jumps over the lazy dog. A second line"
    assert window.result.text.startswith("«ar» quick brown fox")
    assert "quick brown fox" in window.panel.browser.toPlainText()
    assert view.selected_rects(0)


def test_translate_page_and_swap(window, tmp_path):
    pdf = tmp_path / "sample.pdf"
    make_pdf(pdf)
    window.open(pdf)
    window.translate_current_page()
    assert wait_for(lambda: window.result is not None)
    assert window.source_label == "Page 1"
    assert "Page 1 heading" in window.source_text

    window._select_combo(window.source_combo, "en")
    window._language_changed("source")
    assert wait_for(lambda: window.result is not None and not window.translating)
    previous = window.result.text
    window.swap_languages()
    assert window.source_language == "ar" and window.target_language == "en"
    assert wait_for(lambda: window.result is not None and not window.translating)
    assert window.source_text == previous


def test_double_click_word_and_clear(window, tmp_path):
    pdf = tmp_path / "sample.pdf"
    make_pdf(pdf)
    window.open(pdf)
    view, page = window.pdf_view, window.pdf_view.pages[0]
    fox = next(w for w in view.words(0) if w[4] == "fox")
    view.double_click(page, QPointF((fox[0] + 2) * page.scale, (fox[1] + 2) * page.scale))
    assert wait_for(lambda: window.result is not None)
    assert window.source_text == "fox"
    window.clear()
    assert window.source_text == "" and window.result is None and view.anchor is None


def test_bad_file_shows_error(window, tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_text("not a pdf")
    window.open(bad)
    assert window.error and "Couldn't open" in window.error


def test_text_size_limits(window):
    window.set_text_size(100)
    assert window.text_size == 48
    window.change_text_size(-100)
    assert window.text_size == 14
