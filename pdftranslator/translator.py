"""Front door for translating: saved translations first, then Google (online) or the offline engine,
plus word details (other translations, synonyms) for short selections."""
from __future__ import annotations

import unicodedata
from typing import Optional

from .cache import TranslationCache
from .models import TranslationResult
from .offline import OfflineEngine
from .online import OnlineTranslator


def _bare(text: str) -> str:
    """Lowercased, without surrounding whitespace or punctuation in any script (e.g. "Word." → "word")."""
    chars = list(text.strip())
    while chars and (chars[0].isspace() or unicodedata.category(chars[0]).startswith("P")):
        chars.pop(0)
    while chars and (chars[-1].isspace() or unicodedata.category(chars[-1]).startswith("P")):
        chars.pop()
    return "".join(chars).lower()


class Translator:
    def __init__(self, cache: Optional[TranslationCache] = None, online: Optional[OnlineTranslator] = None,
                 offline: Optional[OfflineEngine] = None):
        self.cache = cache or TranslationCache()
        self.online = online or OnlineTranslator()
        self.offline = offline or OfflineEngine()

    @property
    def saved_count(self) -> int:
        return self.cache.count

    def clear_saved(self) -> None:
        self.cache.clear()

    def translate(self, text: str, source: str, target: str, offline: bool = False) -> TranslationResult:
        key = ("offline|" if offline else "") + TranslationCache.key(text, source, target)
        saved = self.cache.get(key)
        if saved is not None:
            if saved.senses is None and self._add_word_details(saved, text, source, target):
                self.cache.set(key, saved)
            return saved
        if offline:
            result = self.offline.translate(text, source, target)
        else:
            result = self.online.translate(text, source, target)
        self._add_word_details(result, text, source, target)
        self.cache.set(key, result)
        return result

    def _add_word_details(self, result: TranslationResult, text: str, source: str, target: str) -> bool:
        """For a word or short phrase, adds synonyms and other translations from the offline engine
        (works in both modes). Returns False if nothing could be looked up."""
        if len(text.split()) > 3 or not self.offline.is_installed():
            return False
        src = (result.detected_language or "en") if source == "auto" else source
        try:
            alternatives, senses = self.offline.lookup(text, src, target)
        except Exception:
            return False
        main = _bare(result.text)
        result.alternatives = [a for a in alternatives if _bare(a) != main]
        result.senses = senses
        return True
