from __future__ import annotations

from typing import Optional

LANGUAGES: list[tuple[str, str]] = [
    ("ar", "Arabic"), ("en", "English"), ("sv", "Swedish"), ("fr", "French"), ("de", "German"),
    ("es", "Spanish"), ("it", "Italian"), ("pt", "Portuguese"), ("nl", "Dutch"), ("da", "Danish"),
    ("no", "Norwegian"), ("fi", "Finnish"), ("pl", "Polish"), ("ru", "Russian"), ("uk", "Ukrainian"),
    ("tr", "Turkish"), ("fa", "Persian"), ("ur", "Urdu"), ("ku", "Kurdish (Kurmanji)"),
    ("ckb", "Kurdish (Sorani)"), ("he", "Hebrew"), ("hi", "Hindi"), ("bn", "Bengali"),
    ("zh-CN", "Chinese (Simplified)"), ("zh-TW", "Chinese (Traditional)"), ("ja", "Japanese"),
    ("ko", "Korean"), ("id", "Indonesian"), ("ms", "Malay"), ("so", "Somali"), ("am", "Amharic"),
    ("el", "Greek"),
]

SOURCES: list[tuple[str, str]] = [("auto", "Detect language")] + LANGUAGES
TARGETS: list[tuple[str, str]] = LANGUAGES

_RTL = {"ar", "fa", "ur", "he", "iw", "ckb", "ps", "sd", "ug", "yi"}


def base_code(code: str) -> str:
    return code.split("-")[0].lower() if code else code


def name_for(code: Optional[str]) -> Optional[str]:
    if not code:
        return None
    lower = code.lower()
    for c, name in LANGUAGES:
        if c.lower() == lower:
            return name
    for c, name in LANGUAGES:
        if c.lower() == lower.split("-")[0]:
            return name
    return None


def is_rtl(code: Optional[str]) -> bool:
    return bool(code) and base_code(code) in _RTL
