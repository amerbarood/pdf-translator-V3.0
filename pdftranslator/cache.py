"""Saved translations on disk, so selecting the same text again is instant and doesn't hit Google again."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Optional

from .models import TranslationResult
from .paths import data_folder


class TranslationCache:
    LIMIT = 3000

    def __init__(self, path: Optional[Path] = None):
        self.path = path or data_folder() / "translations.json"
        self._lock = threading.Lock()
        self._entries: dict[str, dict] = {}
        try:
            self._entries = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass

    @staticmethod
    def key(text: str, source: str, target: str) -> str:
        return f"{source}|{target}|{text}"

    @property
    def count(self) -> int:
        return len(self._entries)

    def get(self, key: str) -> Optional[TranslationResult]:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            entry["used"] = time.time()
            try:
                return TranslationResult.from_dict(entry["result"])
            except (KeyError, TypeError):
                del self._entries[key]
                return None

    def set(self, key: str, result: TranslationResult) -> None:
        with self._lock:
            self._entries[key] = {"result": result.to_dict(), "used": time.time()}
            if len(self._entries) > self.LIMIT:
                oldest = sorted(self._entries, key=lambda k: self._entries[k]["used"])
                for old in oldest[: len(self._entries) - self.LIMIT]:
                    del self._entries[old]
            self._save()

    def clear(self) -> None:
        with self._lock:
            self._entries = {}
            try:
                self.path.unlink()
            except OSError:
                pass

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(self._entries, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass
