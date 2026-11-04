"""The translation panel on the right: header with actions, then translation, word details and original."""
from __future__ import annotations

import html
from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QTextBrowser, QToolButton,
                               QVBoxLayout, QWidget)

from . import languages
from .models import Sense, TranslationResult


def _mix(a: QColor, b: QColor, t: float) -> str:
    return QColor(int(a.red() * (1 - t) + b.red() * t), int(a.green() * (1 - t) + b.green() * t),
                  int(a.blue() * (1 - t) + b.blue() * t)).name()


def _paragraphs(text: str, attributes: str = "") -> str:
    """Text → <p> elements. Qt's rich text doesn't pass a <div>'s font size on to its paragraphs, so the
    attributes (dir, align, style) go on every paragraph."""
    return "".join(f"<p {attributes}>{html.escape(p).replace(chr(10), '<br>')}</p>"
                   for p in text.split("\n\n") if p.strip())


class TranslationPanel(QWidget):
    smaller = Signal()
    bigger = Signal()
    speak_original = Signal()
    copy = Signal()
    clear = Signal()
    retry = Signal()
    retry_online = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget()
        header.setFixedHeight(42)
        row = QHBoxLayout(header)
        row.setContentsMargins(14, 0, 8, 0)
        row.setSpacing(4)
        self.title = QLabel("Translation")
        font = self.title.font()
        font.setBold(True)
        if font.pointSizeF() > 0:
            font.setPointSizeF(font.pointSizeF() + 1)
        else:
            font.setPixelSize(font.pixelSize() + 1)
        self.title.setFont(font)
        self.status = QLabel("")
        self.status.setStyleSheet("color: gray;")
        row.addWidget(self.title)
        row.addSpacing(8)
        row.addWidget(self.status)
        row.addStretch(1)

        def tool(text: str, tip: str, signal) -> QToolButton:
            button = QToolButton()
            button.setText(text)
            button.setToolTip(tip)
            button.setAutoRaise(True)
            button.clicked.connect(signal.emit)
            row.addWidget(button)
            return button

        self.smaller_button = tool("A−", "Smaller text (Ctrl/⌘ −)", self.smaller)
        self.bigger_button = tool("A+", "Bigger text (Ctrl/⌘ +)", self.bigger)
        self.speak_button = tool("🔊", "Read the original text aloud (Ctrl/⌘ R)", self.speak_original)
        self.copy_button = tool("⧉", "Copy translation (Ctrl/⌘ Shift C)", self.copy)
        self.clear_button = tool("✕", "Clear", self.clear)
        layout.addWidget(header)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)

        self.error_bar = QWidget()
        error_row = QHBoxLayout(self.error_bar)
        error_row.setContentsMargins(18, 10, 18, 0)
        self.retry_button = QPushButton("Try Again")
        self.retry_button.clicked.connect(self.retry.emit)
        self.online_button = QPushButton("Translate Online Instead")
        self.online_button.clicked.connect(self.retry_online.emit)
        error_row.addWidget(self.retry_button)
        error_row.addWidget(self.online_button)
        error_row.addStretch(1)
        self.error_bar.hide()
        layout.addWidget(self.error_bar)

        self.browser = QTextBrowser()
        self.browser.setFrameShape(QFrame.Shape.NoFrame)
        self.browser.setOpenLinks(False)
        self.browser.document().setDocumentMargin(18)
        layout.addWidget(self.browser, 1)

    def render(self, *, label: str, source_text: str, result: Optional[TranslationResult], error: Optional[str],
               translating: bool, text_size: float, source_language: str, target_language: str,
               original_language: str, use_offline: bool, speaking_original: bool, can_speak: bool,
               speak_tip: str) -> None:
        self.title.setText(label or "Translation")
        self.status.setText("Translating…" if translating else "")
        self.speak_button.setText("⏹" if speaking_original else "🔊")
        self.speak_button.setEnabled(bool(source_text) and can_speak)
        self.speak_button.setToolTip(speak_tip)
        self.copy_button.setEnabled(result is not None)
        self.clear_button.setEnabled(bool(source_text) or error is not None)
        self.smaller_button.setEnabled(text_size > 14)
        self.bigger_button.setEnabled(text_size < 48)
        self.error_bar.setVisible(error is not None and bool(source_text))
        self.online_button.setVisible(use_offline)

        palette = self.browser.palette()
        text_color = palette.text().color()
        base_color = palette.base().color()
        secondary = _mix(text_color, base_color, 0.45)
        box = _mix(base_color, text_color, 0.07)
        target_rtl = languages.is_rtl(target_language)
        original_rtl = languages.is_rtl(original_language)
        t_dir = "rtl" if target_rtl else "ltr"
        t_align = "right" if target_rtl else "left"
        o_dir = "rtl" if original_rtl else "ltr"
        o_align = "right" if original_rtl else "left"
        px = lambda factor: f"{round(text_size * factor)}px"  # noqa: E731

        parts: list[str] = []
        if error:
            parts.append(f'<p style="color:#d9822b; font-size:{px(0.6)};">⚠ {html.escape(error)}</p>')

        if result is not None and source_text:
            parts.append(_paragraphs(result.text, f'dir="{t_dir}" align="{t_align}" '
                                                  f'style="font-size:{px(1)}; line-height:120%;"'))

            if result.alternatives:
                sep = "  ،  " if target_rtl else "  ·  "
                parts.append(f'<p style="font-size:{px(0.7)}; font-weight:600; margin-top:14px;">Other translations</p>'
                             f'<p dir="{t_dir}" align="{t_align}" style="font-size:{px(0.8)};">'
                             f'{html.escape(sep.join(result.alternatives))}</p>')

            if result.senses:
                parts.append(self._senses_html(result.senses, text_size, target_language, original_rtl, target_rtl,
                                               secondary, box))

            if result.meanings:
                parts.append(f'<p style="font-size:{px(0.7)}; font-weight:600; margin-top:14px;">All meanings</p>')
                sep = "، " if target_rtl else ", "
                for meaning in result.meanings:
                    parts.append(f'<p style="font-size:{px(0.6)}; color:{secondary}; font-weight:600; margin-bottom:0;">'
                                 f'{html.escape(meaning.part_of_speech.capitalize())}</p>'
                                 f'<p dir="{t_dir}" align="{t_align}" style="font-size:{px(0.8)}; margin-top:2px;">'
                                 f'{html.escape(sep.join(meaning.terms))}</p>')

            detected = ""
            if source_language == "auto" and languages.name_for(result.detected_language):
                detected = f' <span style="color:{secondary}; font-weight:400;">· detected {languages.name_for(result.detected_language)}</span>'
            parts.append(f'<hr><p style="font-size:{px(0.6)}; font-weight:600;">Original{detected}</p>'
                         + _paragraphs(source_text, f'dir="{o_dir}" align="{o_align}" '
                                                    f'style="color:{secondary}; font-size:{px(0.6)};"'))
        elif error is None and not translating:
            hint = f'<p style="color:{secondary}; font-size:{px(0.6)};">'
            parts.append(hint + "Select text in the PDF to translate it. Double-click translates a single word.</p>"
                         + hint + "Or click <b>Translate Page</b> (Ctrl/⌘ T) to translate the whole page you're viewing.</p>"
                         + hint + "Click 🔊 to hear the original text read aloud.</p>")

        self.browser.setHtml("".join(parts))

    @staticmethod
    def _senses_html(senses: list[Sense], text_size: float, target: str, original_rtl: bool, target_rtl: bool,
                     secondary: str, box: str) -> str:
        px = lambda factor: f"{round(text_size * factor)}px"  # noqa: E731
        target_name = languages.name_for(target) or "Translation"
        out = [f'<p style="font-size:{px(0.7)}; font-weight:600; margin-top:14px;">Synonyms &amp; meanings</p>']
        for sense in senses:
            rows = []
            for label, words, rtl in (("Synonyms", sense.synonyms, original_rtl), (target_name, sense.translations, target_rtl)):
                if words:
                    sep = "، " if rtl else ", "
                    rows.append(f'<tr><td width="{round(text_size * 4.6)}" style="font-size:{px(0.55)}; color:{secondary}; white-space:nowrap;">'
                                f'{html.escape(label)}</td><td dir="{"rtl" if rtl else "ltr"}" '
                                f'style="font-size:{px(0.75)};">{html.escape(sep.join(words))}</td></tr>')
            out.append(f'<table width="100%" cellpadding="8" cellspacing="0" style="background-color:{box}; margin-bottom:8px;">'
                       f'<tr><td colspan="2" style="font-size:{px(0.6)}; color:{secondary};">'
                       f'<b>{html.escape(sense.part_of_speech.capitalize())}</b> · {html.escape(sense.definition)}</td></tr>'
                       f'{"".join(rows)}</table>')
        return "".join(out)
