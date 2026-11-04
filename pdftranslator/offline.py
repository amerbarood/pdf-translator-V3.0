"""Offline translation on this computer with open-source Argos Translate models (CTranslate2 + SentencePiece).

Language packs live in <app folder>/Offline/packs/<from>_<to>/. Pairs without a direct pack go through
English (e.g. sv → en → ar). Synonyms and per-meaning translations of words come from WordNet and the Open
Multilingual Wordnet in <app folder>/Offline/nltk_data. Install packs from the app (Translate › Offline
Language Packs…) or with:  python -m pdftranslator.offline_setup setup
"""
from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Optional

from . import text_tools
from .languages import base_code, name_for
from .models import Sense, TranslationResult
from .paths import offline_folder

# App language code → Open Multilingual Wordnet code.
OMW = {"en": "eng", "ar": "arb", "sv": "swe", "fr": "fra", "it": "ita", "nl": "nld", "da": "dan",
       "no": "nob", "fi": "fin", "pl": "pol", "pt": "por", "he": "heb", "ja": "jpn", "id": "ind",
       "ms": "zsm", "el": "ell", "zh": "cmn"}
POS = {"n": "noun", "v": "verb", "a": "adjective", "s": "adjective", "r": "adverb"}
ARABIC_MARKS = re.compile("[ً-ْٰـ]")


class OfflineError(Exception):
    pass


def _pair_names(pairs: list[str]) -> str:
    names = []
    for pair in pairs:
        parts = pair.split("_")
        names.append(f"{name_for(parts[0]) or parts[0]} → {name_for(parts[1]) or parts[1]}" if len(parts) == 2 else pair)
    return ", ".join(names) or "none"


def clean(lemma: str) -> str:
    """WordNet lemma → display word: underscores to spaces, no Arabic short-vowel marks."""
    return ARABIC_MARKS.sub("", lemma.replace("_", " ")).strip()


def unique(words, exclude=()) -> list[str]:
    seen = {w.lower() for w in exclude}
    out = []
    for w in words:
        if w and w.lower() not in seen:
            seen.add(w.lower())
            out.append(w)
    return out


class OfflineEngine:
    def __init__(self, folder: Optional[Path] = None):
        self.folder = folder or offline_folder()
        self.packs = self.folder / "packs"
        self.nltk_data = self.folder / "nltk_data"
        self._lock = threading.RLock()
        self._loaded: dict[str, tuple] = {}
        self._wordnet = None
        self._wordnet_failed = False
        self._arabic_index: Optional[dict[str, list[str]]] = None

    # ---- status ----

    @staticmethod
    def dependencies_installed() -> bool:
        try:
            import ctranslate2  # noqa: F401
            import sentencepiece  # noqa: F401
            return True
        except ImportError:
            return False

    def pairs(self) -> list[str]:
        if not self.packs.is_dir():
            return []
        return sorted(p.name for p in self.packs.iterdir() if (p / "model").is_dir() and (p / "sentencepiece.model").exists())

    def is_installed(self) -> bool:
        return self.dependencies_installed() and bool(self.pairs())

    def reset(self) -> None:
        """Forget loaded models, e.g. after installing a new pack."""
        with self._lock:
            self._loaded.clear()

    def preload(self) -> None:
        """Loads WordNet in the background so the first word lookup is quick."""
        threading.Thread(target=self._wordnet_module, daemon=True).start()

    # ---- translation ----

    def translate(self, text: str, source: str, target: str) -> TranslationResult:
        if not self.dependencies_installed():
            raise OfflineError("The offline engine isn't installed. Install it with "
                               "“pip install -r requirements-offline.txt”, or switch to Online in the toolbar.")
        installed = self.pairs()
        if not installed:
            raise OfflineError("No offline language packs are installed yet. Add them in "
                               "Translate › Offline Language Packs…, or switch to Online in the toolbar.")
        src = self.detect(text, installed) if source == "auto" else base_code(source)
        dst = base_code(target)

        # Translate sentence by sentence (what the models are trained on), then rebuild the paragraphs.
        paragraphs = [text_tools.sentences(p) for p in text.split("\n\n")]
        flat = [s for p in paragraphs for s in p]
        if src == dst:
            translated = flat
        else:
            steps = self.route(src, dst, installed)
            if steps is None:
                raise OfflineError(
                    f"There's no offline language pack for {name_for(src) or src} → {name_for(dst) or dst}. "
                    f"Installed: {_pair_names(installed)}. Use Online (Google) for this, or install the pack.")
            translated = flat
            for step in steps:
                translated = self._translate_direct(translated, step)
        output = []
        position = 0
        for paragraph in paragraphs:
            output.append(" ".join(translated[position:position + len(paragraph)]))
            position += len(paragraph)
        return TranslationResult(text="\n\n".join(output), detected_language=src)

    def route(self, src: str, dst: str, available: Optional[list[str]] = None) -> Optional[list[str]]:
        available = set(available if available is not None else self.pairs())
        if f"{src}_{dst}" in available:
            return [f"{src}_{dst}"]
        if src != "en" and dst != "en" and f"{src}_en" in available and f"en_{dst}" in available:
            return [f"{src}_en", f"en_{dst}"]
        return None

    def _load(self, pair: str):
        with self._lock:
            if pair not in self._loaded:
                import ctranslate2
                import sentencepiece
                folder = self.packs / pair
                translator = ctranslate2.Translator(str(folder / "model"), device="cpu", compute_type="int8",
                                                    intra_threads=min(4, os.cpu_count() or 1))
                sp = sentencepiece.SentencePieceProcessor(model_file=str(folder / "sentencepiece.model"))
                self._loaded[pair] = (translator, sp)
            return self._loaded[pair]

    def _translate_direct(self, texts: list[str], pair: str) -> list[str]:
        translator, sp = self._load(pair)
        out = [""] * len(texts)
        todo = [(i, t) for i, t in enumerate(texts) if t.strip()]
        if not todo:
            return out
        tokens = [sp.encode(t, out_type=str) for _, t in todo]
        with self._lock:
            results = translator.translate_batch(tokens, beam_size=4, max_batch_size=16,
                                                 max_decoding_length=512, replace_unknowns=True)
        for (i, _), r in zip(todo, results):
            out[i] = sp.decode(r.hypotheses[0]).strip()
        return out

    def detect(self, text: str, pairs: list[str]) -> str:
        """Most likely language of `text` among those the installed packs can translate from."""
        sources = {p.split("_")[0] for p in pairs}
        sample = text[:2000]
        try:
            from langdetect import DetectorFactory, detect_langs
            DetectorFactory.seed = 0
            for guess in detect_langs(sample):
                code = base_code(guess.lang)
                if code in sources:
                    return code
        except Exception:
            pass
        letters = [c for c in sample if c.isalpha()]
        if letters and "ar" in sources and sum("؀" <= c <= "ۿ" for c in letters) > len(letters) / 3:
            return "ar"
        if "sv" in sources and re.search(r"[åäöÅÄÖ]", sample):
            return "sv"
        return "en" if "en" in sources or not sources else sorted(sources)[0]

    # ---- word details ----

    def lookup(self, word: str, source: str, target: str) -> tuple[list[str], list[Sense]]:
        """Synonyms grouped by meaning, plus alternative translations, for a word or short phrase."""
        word = word.strip()
        src, dst = base_code(source), base_code(target)
        senses: list[Sense] = []
        src_lang, dst_lang = OMW.get(src), OMW.get(dst)
        if src_lang and self._wordnet_module() is not None:
            try:
                synsets = self._synsets(word, src_lang)[:8]
            except LookupError:   # multilingual data missing; English-only details still work
                synsets = self._synsets(word, "eng")[:8] if src_lang == "eng" else []
            for s in synsets:
                synonyms = unique([clean(l) for l in self._lemmas(s, src_lang)], exclude=[clean(word)])
                translations = unique([clean(l) for l in self._lemmas(s, dst_lang)]) if dst_lang else []
                if not synonyms and not translations and len(senses) >= 3:
                    continue
                senses.append(Sense(part_of_speech=POS.get(s.pos(), "other"), definition=s.definition(),
                                    synonyms=synonyms[:8], translations=translations[:8]))
                if len(senses) == 6:
                    break

        alternatives: list[str] = []
        steps = self.route(src, dst) if self.dependencies_installed() else None
        if steps:
            text = word
            for step in steps[:-1]:
                text = self._translate_direct([text], step)[0]
            translator, sp = self._load(steps[-1])
            with self._lock:
                result = translator.translate_batch([sp.encode(text, out_type=str)], beam_size=8, num_hypotheses=8,
                                                    max_decoding_length=64, replace_unknowns=True,
                                                    return_scores=True)[0]
            best = result.scores[0] if result.scores else 0
            candidates = []
            for hyp, score in zip(result.hypotheses, result.scores):
                words = sp.decode(hyp).strip().strip(".!?:;،؟").split()
                # Skip low-confidence guesses and degenerate ones that repeat a word.
                if words and score >= best - 0.6 and len({w.lower() for w in words}) == len(words):
                    candidates.append(" ".join(words))
            alternatives = unique(candidates)[:6]
        return alternatives, senses

    def _wordnet_module(self):
        with self._lock:
            if self._wordnet is None and not self._wordnet_failed:
                try:
                    import nltk
                    if not self.nltk_data.is_dir():
                        raise LookupError("no WordNet data")
                    if str(self.nltk_data) not in nltk.data.path:
                        nltk.data.path.insert(0, str(self.nltk_data))
                    from nltk.corpus import wordnet as wn
                    wn.ensure_loaded()
                    self._wordnet = wn
                except Exception:
                    self._wordnet_failed = True
            return self._wordnet

    @staticmethod
    def _lemmas(synset, lang: str) -> list[str]:
        try:
            return synset.lemma_names(lang)
        except LookupError:
            return []

    def _synsets(self, word: str, lang: str):
        wn = self._wordnet
        if lang == "eng":
            return wn.synsets(word.lower().replace(" ", "_"))
        if lang == "arb":  # Arabic WordNet lemmas carry vowel marks; match without them.
            with self._lock:
                if self._arabic_index is None:
                    index: dict[str, list[str]] = {}
                    for lemma in wn.all_lemma_names(lang="arb"):
                        index.setdefault(clean(lemma), []).append(lemma)
                    self._arabic_index = index
            found = []
            for lemma in self._arabic_index.get(clean(word), []):
                found += [s for s in wn.synsets(lemma, lang="arb") if s not in found]
            return found
        try:
            return wn.synsets(word.lower().replace(" ", "_"), lang=lang)
        except Exception:
            return []
