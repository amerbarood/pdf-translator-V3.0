"""Text clean-up for PDF text: rebuilding paragraphs, splitting into sentences and request-sized chunks."""
from __future__ import annotations

import re

_ENDERS = set(".!?؟。！？")
_CLOSERS = set("\"'”’)]»")
_ABBREVIATIONS = {"e.g", "i.e", "etc", "vs", "dr", "mr", "mrs", "ms", "prof", "fig", "figs", "eq", "eqs",
                  "no", "al", "cf", "approx", "vol", "pp", "ch", "sec", "st", "jr", "sr", "inc", "ltd"}


def reflow(raw: str) -> str:
    """PDF text has a hard line break at the end of every printed line. Join lines back into paragraphs,
    remove end-of-line hyphenation, and keep real paragraph breaks."""
    text = (raw.replace("\r\n", "\n").replace("\r", "\n")
            .replace("­", "").replace(" ", "\n"))
    lines = [line.strip() for line in text.split("\n")]

    # A line clearly shorter than a typical full line, ending a sentence, ends its paragraph.
    lengths = sorted(len(line) for line in lines if line)
    typical = lengths[min(len(lengths) - 1, len(lengths) * 4 // 5)] if lengths else 0

    paragraphs: list[str] = []
    current = ""
    previous_length = 0
    for line in lines:
        if not line:
            if current:
                paragraphs.append(current)
                current = ""
            continue
        if not current:
            current = line
        elif current.endswith("-") and line[0].islower():
            current = current[:-1] + line
        elif current[-1] in ".!?:;\"”)" and previous_length < typical * 0.75:
            paragraphs.append(current)
            current = line
        else:
            current += " " + line
        previous_length = len(line)
    if current:
        paragraphs.append(current)

    return "\n\n".join(re.sub(r" {2,}", " ", p) for p in paragraphs)


def sentences(text: str) -> list[str]:
    """Splits text into sentences, without breaking at common abbreviations (e.g., Fig. 3, J. Smith)."""
    result: list[str] = []
    n = len(text)
    start = i = 0
    while i < n:
        if text[i] not in _ENDERS:
            i += 1
            continue
        end_char = text[i]
        j = i + 1
        while j < n and text[j] in _ENDERS:
            j += 1
        while j < n and text[j] in _CLOSERS:
            j += 1
        if j >= n:
            break
        if not text[j].isspace():
            i = j
            continue
        k = j
        while k < n and text[k].isspace():
            k += 1
        following = text[k] if k < n else ""
        words = text[start:i].split()
        last = words[-1].lower() if words else ""
        if end_char == "." and (last in _ABBREVIATIONS or (len(last) == 1 and last.isalpha())
                                or (following and (following.islower() or following.isdigit()))):
            i = j
            continue
        piece = text[start:j].strip()
        if piece:
            result.append(piece)
        start = i = k
    tail = text[start:].strip()
    if tail:
        result.append(tail)
    return result or [text]


def chunks(text: str, limit: int) -> list[tuple[str, str]]:
    """Splits text into pieces of at most `limit` characters, preferring paragraph, then sentence, then word
    boundaries. Each item is (piece, separator), where separator is what followed the piece in the text."""
    if len(text) <= limit:
        return [(text, "")]

    units: list[list[str]] = []
    for paragraph in text.split("\n\n"):
        if not paragraph:
            continue
        if len(paragraph) <= limit:
            units.append([paragraph, "\n\n"])
            continue
        for sentence in sentences(paragraph):
            if len(sentence) <= limit:
                units.append([sentence, " "])
                continue
            piece = ""
            for word in sentence.split(" "):
                if piece and len(piece) + len(word) + 1 > limit:
                    units.append([piece, " "])
                    piece = ""
                piece = word if not piece else piece + " " + word
            if piece:
                units.append([piece, " "])
        if units:
            units[-1][1] = "\n\n"

    result: list[tuple[str, str]] = []
    current = ""
    last_separator = ""
    for unit_text, separator in units:
        if current and len(current) + len(last_separator) + len(unit_text) > limit:
            result.append((current, last_separator))
            current = ""
        current = unit_text if not current else current + last_separator + unit_text
        last_separator = separator
    if current:
        result.append((current, ""))
    return result
