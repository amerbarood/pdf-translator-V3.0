from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class Meaning:
    """Dictionary meanings from Google for a single word, grouped by part of speech."""
    part_of_speech: str
    terms: list[str]


@dataclass
class Sense:
    """One meaning of a looked-up word (WordNet): synonyms in the original language and the words for
    that meaning in the target language."""
    part_of_speech: str
    definition: str
    synonyms: list[str]
    translations: list[str]


@dataclass
class TranslationResult:
    text: str
    detected_language: Optional[str] = None
    meanings: list[Meaning] = field(default_factory=list)
    alternatives: Optional[list[str]] = None   # None = not looked up
    senses: Optional[list[Sense]] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "TranslationResult":
        senses = data.get("senses")
        return cls(
            text=data.get("text", ""),
            detected_language=data.get("detected_language"),
            meanings=[Meaning(**m) for m in data.get("meanings") or []],
            alternatives=data.get("alternatives"),
            senses=None if senses is None else [Sense(**s) for s in senses],
        )
