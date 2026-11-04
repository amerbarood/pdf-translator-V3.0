
import pytest
import requests

from pdftranslator import text_tools
from pdftranslator.cache import TranslationCache
from pdftranslator.models import Meaning, Sense, TranslationResult
from pdftranslator.offline import OfflineEngine
from pdftranslator.online import OnlineTranslator, TranslationError, parse_chrome, parse_dictionary
from pdftranslator.translator import Translator


# ---- text tools ----

def test_reflow_joins_lines_and_keeps_paragraphs():
    raw = ("This is a long line of text that goes all the way to the\n"
           "margin of the page and keeps on going until the end of the\n"
           "sentence here.\n\nNew paragraph with a hyphen-\nated word.")
    out = text_tools.reflow(raw)
    assert out.split("\n\n")[0].startswith("This is a long line of text that goes all the way to the margin")
    assert "hyphenated word." in out
    assert out.count("\n\n") == 1


def test_reflow_short_line_ending_sentence_starts_new_paragraph():
    long = "x" * 60
    raw = f"{long}\n{long}\nShort end.\nNext paragraph {long}"
    assert text_tools.reflow(raw).split("\n\n")[1].startswith("Next paragraph")


def test_sentences_respect_abbreviations():
    text = "See Fig. 3 for details. Dr. Smith agreed, e.g. in 2020. J. Doe left! Next one?"
    assert text_tools.sentences(text) == [
        "See Fig. 3 for details.", "Dr. Smith agreed, e.g. in 2020.", "J. Doe left!", "Next one?"]


def test_sentences_arabic_question_mark():
    assert text_tools.sentences("ما هذا؟ هذا كتاب.") == ["ما هذا؟", "هذا كتاب."]


def test_chunks_respect_limit_and_rebuild():
    text = "\n\n".join(" ".join(f"Sentence {p}-{i} has words." for i in range(30)) for p in range(5))
    pieces = text_tools.chunks(text, 400)
    assert all(len(p) <= 400 for p, _ in pieces)
    rebuilt = "".join(p + s for p, s in pieces)
    assert rebuilt == text


def test_chunks_short_text_is_one_piece():
    assert text_tools.chunks("hello", 4000) == [("hello", "")]


# ---- cache ----

def test_cache_round_trip_and_limit(tmp_path):
    path = tmp_path / "t.json"
    cache = TranslationCache(path)
    result = TranslationResult("مرحبا", "en", [Meaning("noun", ["a"])], ["b"], [Sense("noun", "d", ["s"], ["t"])])
    cache.set("k", result)
    assert TranslationCache(path).get("k") == result
    cache.LIMIT = 3
    for i in range(5):
        cache.set(f"k{i}", TranslationResult(str(i)))
    assert cache.count == 3
    cache.clear()
    assert cache.count == 0 and not path.exists()


# ---- online ----

def test_parse_dictionary():
    data = {"sentences": [{"trans": "مرحبا "}, {"trans": "بالعالم"}], "src": "en",
            "dict": [{"pos": "noun", "terms": ["a", "b"]}, {"pos": "", "terms": []}]}
    r = parse_dictionary(data)
    assert r.text == "مرحبا بالعالم" and r.detected_language == "en"
    assert r.meanings == [Meaning("noun", ["a", "b"])]


def test_parse_chrome_both_shapes():
    assert parse_chrome([["Hej", "en"], [" värld", "en"]]).text == "Hej värld"
    assert parse_chrome([["Hej", "en"]]).detected_language == "en"
    assert parse_chrome(["Hej"]).text == "Hej"
    assert parse_chrome({"x": 1}) is None


class FakeResponse:
    def __init__(self, status, data=None):
        self.status_code = status
        self._data = data

    def json(self):
        if self._data is None:
            raise ValueError
        return self._data


class FakeSession:
    def __init__(self, replies):
        self.replies = replies    # host keyword → list of responses/exceptions
        self.calls = []
        self.headers = {}

    def post(self, url, params=None, data=None, timeout=None):
        host = "googleapis" if "googleapis" in url else "clients5"
        self.calls.append(host)
        reply = self.replies[host].pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def test_rate_limited_service_falls_back_and_is_parked():
    session = FakeSession({"clients5": [FakeResponse(429)],
                           "googleapis": [FakeResponse(200, {"sentences": [{"trans": "Hej"}], "src": "en"}),
                                          FakeResponse(200, {"sentences": [{"trans": "Igen"}], "src": "en"})]})
    t = OnlineTranslator(session=session, sleep=lambda s: None)
    assert t.translate("Hello there friend and more", "auto", "sv").text == "Hej"
    assert session.calls == ["clients5", "googleapis"]
    t.translate("Again with more words here", "auto", "sv")
    assert session.calls[-1] == "googleapis" and session.calls.count("clients5") == 1


def test_transient_errors_are_retried():
    session = FakeSession({"clients5": [FakeResponse(503), requests.Timeout(), FakeResponse(200, ["ok"])],
                           "googleapis": []})
    t = OnlineTranslator(session=session, sleep=lambda s: None)
    assert t.translate("one two three four", "en", "sv").text == "ok"


def test_both_rate_limited_reports_429():
    session = FakeSession({"clients5": [FakeResponse(429)], "googleapis": [FakeResponse(429)]})
    with pytest.raises(TranslationError) as info:
        OnlineTranslator(session=session, sleep=lambda s: None).translate("a b c d", "en", "ar")
    assert "429" in str(info.value)


def test_short_selection_prefers_dictionary_service():
    session = FakeSession({"googleapis": [FakeResponse(200, {"sentences": [{"trans": "x"}],
                                                              "dict": [{"pos": "verb", "terms": ["y"]}]})],
                           "clients5": []})
    r = OnlineTranslator(session=session, sleep=lambda s: None).translate("run", "en", "ar")
    assert session.calls == ["googleapis"] and r.meanings[0].part_of_speech == "verb"


# ---- translator front door ----

class FakeOnline:
    def __init__(self):
        self.calls = 0

    def translate(self, text, source, target):
        self.calls += 1
        return TranslationResult(f"Translated {text}.", "en")


class FakeOffline:
    def __init__(self, installed=True):
        self.installed = installed

    def is_installed(self):
        return self.installed

    def translate(self, text, source, target):
        return TranslationResult(f"offline {text}", "en")

    def lookup(self, word, source, target):
        return ["translated word", "كلمة"], [Sense("noun", "a unit of language", ["term"], ["كلمة"])]


def test_translator_caches_and_adds_word_details(tmp_path):
    online = FakeOnline()
    t = Translator(TranslationCache(tmp_path / "c.json"), online, FakeOffline())
    first = t.translate("word", "auto", "ar")
    assert first.alternatives == ["كلمة"]   # the main translation itself is filtered out
    assert first.senses[0].synonyms == ["term"]
    t.translate("word", "auto", "ar")
    assert online.calls == 1
    assert t.translate("word", "auto", "ar", offline=True).text == "offline word"
    assert t.saved_count == 2


def test_long_text_gets_no_word_details(tmp_path):
    t = Translator(TranslationCache(tmp_path / "c.json"), FakeOnline(), FakeOffline())
    assert t.translate("a much longer selection here", "en", "ar").senses is None


# ---- offline engine (no packs needed) ----

def test_offline_routes_through_english(tmp_path):
    engine = OfflineEngine(tmp_path)
    pairs = ["ar_en", "en_ar", "en_sv", "sv_en"]
    assert engine.route("en", "ar", pairs) == ["en_ar"]
    assert engine.route("sv", "ar", pairs) == ["sv_en", "en_ar"]
    assert engine.route("fr", "ar", pairs) is None


def test_offline_detect_among_installed():
    engine = OfflineEngine()
    pairs = ["ar_en", "en_ar", "sv_en", "en_sv"]
    assert engine.detect("هذا نص عربي طويل بما فيه الكفاية للكشف", pairs) == "ar"
    assert engine.detect("Det här är en svensk mening om vädret i Växjö.", pairs) == "sv"
    assert engine.detect("This is an English sentence about the weather.", pairs) == "en"


def test_offline_translates_sentences_and_rebuilds_paragraphs(tmp_path, monkeypatch):
    engine = OfflineEngine(tmp_path)
    monkeypatch.setattr(engine, "dependencies_installed", lambda: True)
    monkeypatch.setattr(engine, "pairs", lambda: ["en_ar", "sv_en"])
    calls = []

    def fake_direct(texts, pair):
        calls.append((pair, list(texts)))
        return [f"{pair}:{t}" for t in texts]

    monkeypatch.setattr(engine, "_translate_direct", fake_direct)
    result = engine.translate("One. Two.\n\nThree.", "sv", "ar")
    assert [c[0] for c in calls] == ["sv_en", "en_ar"]          # pivots through English
    assert calls[0][1] == ["One.", "Two.", "Three."]              # sentence by sentence
    assert result.text == "en_ar:sv_en:One. en_ar:sv_en:Two.\n\nen_ar:sv_en:Three."
    assert result.detected_language == "sv"
    from pdftranslator.offline import OfflineError
    with pytest.raises(OfflineError) as info:
        engine.translate("Bonjour.", "fr", "ar")
    assert "French → Arabic" in str(info.value) and "English → Arabic" in str(info.value)


def test_offline_without_packs_explains(tmp_path):
    from pdftranslator.offline import OfflineError
    with pytest.raises(OfflineError) as info:
        OfflineEngine(tmp_path).translate("Hello.", "en", "ar")
    assert "language packs" in str(info.value) or "isn't installed" in str(info.value)
