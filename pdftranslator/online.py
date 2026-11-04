"""Online translation through Google's free web endpoints.

There are two endpoints. If Google rate-limits one for this connection (HTTP 429), it is parked for
10 minutes and the other one is used automatically. Timeouts and 5xx errors are retried.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

import requests

from .models import Meaning, TranslationResult
from . import text_tools

DICTIONARY = "dictionary"   # translate.googleapis.com, client=gtx: also returns dictionary meanings
CHROME = "chrome"           # clients5.google.com, the Chrome extension's endpoint

USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
              "(KHTML, like Gecko) Version/17.5 Safari/605.1.15")


class TranslationError(Exception):
    MESSAGES = {
        "rate_limited": "Google is limiting requests on both translation services right now (HTTP 429). "
                        "Wait a few minutes and try again.",
        "timed_out": "Google didn't answer in time. Try again.",
        "unreachable": "Can't reach Google's translation servers. Check your internet connection, VPN or "
                       "firewall, then try again.",
        "bad_response": "Google sent a reply the app couldn't read. Try again.",
    }

    def __init__(self, kind: str, status: int = 0):
        self.kind = kind
        self.status = status
        if kind == "http":
            message = f"Google returned an error (HTTP {status}). Try again in a moment."
        else:
            message = self.MESSAGES.get(kind, kind)
        super().__init__(message)

    @property
    def is_transient(self) -> bool:
        return self.kind == "timed_out" or (self.kind == "http" and self.status >= 500)


class OnlineTranslator:
    CHUNK_LIMIT = 4000
    BLOCK_SECONDS = 600

    def __init__(self, session: Optional[requests.Session] = None, sleep: Callable[[float], None] = time.sleep):
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self._sleep = sleep
        self._lock = threading.Lock()
        self._blocked_until: dict[str, float] = {}

    def translate(self, text: str, source: str, target: str) -> TranslationResult:
        pieces = text_tools.chunks(text, self.CHUNK_LIMIT)
        wants_meanings = len(pieces) == 1 and len(text.split()) <= 3
        result = TranslationResult(text="")
        for index, (piece, separator) in enumerate(pieces):
            part = self._translate_piece(piece, source, target, wants_meanings)
            result.text += part.text + (separator if index < len(pieces) - 1 else "")
            result.detected_language = result.detected_language or part.detected_language
            if not result.meanings:
                result.meanings = part.meanings
        return result

    def _translate_piece(self, text: str, source: str, target: str, wants_meanings: bool) -> TranslationResult:
        order = [DICTIONARY, CHROME] if wants_meanings else [CHROME, DICTIONARY]
        now = time.time()
        with self._lock:
            open_services = [s for s in order if self._blocked_until.get(s, 0) < now]
        if open_services:
            order = open_services

        last_error = TranslationError("bad_response")
        for service in order:
            try:
                return self._with_retries(service, text, source, target)
            except TranslationError as error:
                if error.kind == "rate_limited":
                    with self._lock:
                        self._blocked_until[service] = time.time() + self.BLOCK_SECONDS
                last_error = error
        raise last_error

    def _with_retries(self, service: str, text: str, source: str, target: str) -> TranslationResult:
        attempt = 0
        while True:
            try:
                return self._request(service, text, source, target)
            except TranslationError as error:
                if not error.is_transient or attempt >= 2:
                    raise
                attempt += 1
                self._sleep(0.8 * attempt)

    def _request(self, service: str, text: str, source: str, target: str) -> TranslationResult:
        if service == DICTIONARY:
            url = "https://translate.googleapis.com/translate_a/single"
            params = [("client", "gtx"), ("sl", source), ("tl", target), ("dj", "1"), ("dt", "t"), ("dt", "bd")]
        else:
            url = "https://clients5.google.com/translate_a/t"
            params = [("client", "dict-chrome-ex"), ("sl", source), ("tl", target)]
        try:
            response = self.session.post(url, params=params, data={"q": text}, timeout=20)
        except requests.Timeout:
            raise TranslationError("timed_out") from None
        except requests.RequestException:
            raise TranslationError("unreachable") from None

        if response.status_code == 429:
            raise TranslationError("rate_limited")
        if not 200 <= response.status_code < 300:
            raise TranslationError("http", response.status_code)
        try:
            data = response.json()
        except ValueError:
            raise TranslationError("bad_response") from None
        result = parse_dictionary(data) if service == DICTIONARY else parse_chrome(data)
        if result is None or not result.text:
            raise TranslationError("bad_response")
        return result


def parse_dictionary(data) -> Optional[TranslationResult]:
    """{"sentences":[{"trans":"…"}],"src":"en","dict":[{"pos":"noun","terms":["…"]}]}"""
    if not isinstance(data, dict) or not isinstance(data.get("sentences"), list):
        return None
    text = "".join(s.get("trans", "") for s in data["sentences"] if isinstance(s, dict) and isinstance(s.get("trans"), str))
    meanings = []
    for entry in data.get("dict") or []:
        terms = entry.get("terms") if isinstance(entry, dict) else None
        if isinstance(terms, list) and terms:
            meanings.append(Meaning(part_of_speech=entry.get("pos") or "other", terms=[str(t) for t in terms[:8]]))
    src = data.get("src")
    return TranslationResult(text=text, detected_language=src if isinstance(src, str) else None, meanings=meanings)


def parse_chrome(data) -> Optional[TranslationResult]:
    """[["…","en"]] when the source language is auto-detected, ["…"] when it's given."""
    if not isinstance(data, list):
        return None
    text = ""
    detected = None
    for item in data:
        if isinstance(item, str):
            text += item
        elif isinstance(item, list) and item and isinstance(item[0], str):
            text += item[0]
            if detected is None and len(item) > 1 and isinstance(item[1], str):
                detected = item[1]
    return TranslationResult(text=text, detected_language=detected)
