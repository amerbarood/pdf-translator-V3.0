"""Reads text aloud with the system's voices (works offline): macOS voices, Windows voices, or
speech-dispatcher on Linux, through Qt Text to Speech."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QLocale, QObject, Signal

try:
    from PySide6.QtTextToSpeech import QTextToSpeech
except ImportError:  # Qt built without the speech module
    QTextToSpeech = None

ORIGINAL = "original"
TRANSLATION = "translation"


def _locale_for(code: str) -> QLocale:
    code = {"he": "he", "no": "nb", "ckb": "ckb", "zh-CN": "zh_CN", "zh-TW": "zh_TW"}.get(code, code)
    return QLocale(code.replace("-", "_"))


class Speaker(QObject):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.speaking: Optional[str] = None
        self._started = False
        self._tts = None
        if QTextToSpeech is not None:
            try:
                self._tts = QTextToSpeech(self)
                self._tts.stateChanged.connect(self._state_changed)
            except Exception:
                self._tts = None

    @property
    def available(self) -> bool:
        return self._tts is not None

    def _voice_locale(self, code: str) -> Optional[QLocale]:
        if self._tts is None:
            return None
        wanted = _locale_for(code)
        try:
            locales = self._tts.availableLocales()
        except Exception:
            return None
        exact = [l for l in locales if l.name().lower() == wanted.name().lower()]
        if exact:
            return exact[0]
        same_language = [l for l in locales if l.language() == wanted.language()]
        return same_language[0] if same_language else None

    def has_voice(self, code: str) -> bool:
        return self._voice_locale(code) is not None

    def toggle(self, part: str, text: str, language: str) -> None:
        """Starts reading `text`, or stops if that part is already being read."""
        if self.speaking == part:
            self.stop()
            return
        self.stop()
        locale = self._voice_locale(language)
        if not text or locale is None:
            return
        self._tts.setLocale(locale)
        self._tts.setRate(-0.05)
        self._started = False
        self.speaking = part
        self.changed.emit()
        self._tts.say(text)

    def stop(self) -> None:
        was_speaking = self.speaking is not None
        self.speaking = None
        if self._tts is not None:
            self._tts.stop()
        if was_speaking:
            self.changed.emit()

    def _state_changed(self, state) -> None:
        if QTextToSpeech is None or self.speaking is None:
            return
        if state == QTextToSpeech.State.Speaking:
            self._started = True
        # Ready also arrives right after the stop() that precedes a new say(); only a Ready that follows
        # actual speaking means the text has finished.
        elif state == QTextToSpeech.State.Error or (state == QTextToSpeech.State.Ready and self._started):
            self.speaking = None
            self._started = False
            self.changed.emit()
