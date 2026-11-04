"""Downloads offline language packs (Argos Translate) and WordNet for the offline engine.

    python -m pdftranslator.offline_setup setup      English ↔ Arabic, English ↔ Swedish and WordNet
    python -m pdftranslator.offline_setup install fr French ↔ English (and so French ↔ Arabic via English)
    python -m pdftranslator.offline_setup list       languages that have packs
    python -m pdftranslator.offline_setup wordnet    only WordNet (synonyms and meanings)
"""
from __future__ import annotations

import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Callable, Optional

import requests

from .paths import offline_folder

INDEX_URL = "https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json"
NLTK_DATA_URL = "https://raw.githubusercontent.com/nltk/nltk_data/gh-pages/packages/corpora"
Progress = Callable[[str], None]


def _say(message: str) -> None:
    print(message, flush=True)


def fetch_index() -> list[dict]:
    response = requests.get(INDEX_URL, timeout=30)
    response.raise_for_status()
    return response.json()


def available_languages(index: Optional[list[dict]] = None) -> list[tuple[str, str]]:
    """(code, name) of languages that have packs to and from English."""
    index = index if index is not None else fetch_index()
    to_en = {p["from_code"]: p["from_name"] for p in index if p.get("to_code") == "en"}
    from_en = {p["to_code"] for p in index if p.get("from_code") == "en"}
    return sorted(((c, n) for c, n in to_en.items() if c in from_en), key=lambda x: x[1])


def install_language(code: str, progress: Progress = _say, index: Optional[list[dict]] = None,
                     folder: Optional[Path] = None) -> list[str]:
    """Installs the code→en and en→code packs. Returns the pairs installed."""
    index = index if index is not None else fetch_index()
    packs = (folder or offline_folder()) / "packs"
    packs.mkdir(parents=True, exist_ok=True)
    installed = []
    for pair in (f"{code}_en", f"en_{code}"):
        src, dst = pair.split("_")
        match = [p for p in index if p.get("from_code") == src and p.get("to_code") == dst]
        if not match:
            progress(f"No pack for {pair}")
            continue
        url = match[0]["links"][0]
        progress(f"Downloading {pair}…")
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "pack.zip"
            with requests.get(url, stream=True, timeout=60) as response:
                response.raise_for_status()
                total = int(response.headers.get("content-length") or 0)
                done = 0
                last_shown = -1
                with open(archive, "wb") as f:
                    for block in response.iter_content(1 << 20):
                        f.write(block)
                        done += len(block)
                        if total:
                            percent = done * 100 // total
                            if percent // 10 != last_shown:
                                last_shown = percent // 10
                                progress(f"Downloading {pair}… {percent}%")
            extracted = Path(tmp) / "x"
            with zipfile.ZipFile(archive) as z:
                z.extractall(extracted)
            models = list(extracted.rglob("sentencepiece.model"))
            if not models:
                progress(f"The {pair} pack has an unexpected layout; skipped.")
                continue
            target = packs / pair
            shutil.rmtree(target, ignore_errors=True)
            shutil.move(str(models[0].parent), str(target))
            shutil.rmtree(target / "stanza", ignore_errors=True)
        installed.append(pair)
        progress(f"Installed {pair}")
    return installed


def _omw_package() -> str:
    """The Open Multilingual Wordnet version the installed NLTK loads (omw-1.4 before NLTK 3.10, omw-2.0 after)."""
    try:
        import nltk.corpus
        source = Path(nltk.corpus.__file__).read_text(encoding="utf-8")
        match = re.search(r'LazyCorpusLoader\(\s*"wordnet",.*?"(omw-[\d.]+)"', source, re.S)
        if match:
            return match.group(1)
    except Exception:
        pass
    return "omw-1.4"


def install_wordnet(progress: Progress = _say, folder: Optional[Path] = None) -> None:
    """WordNet + Open Multilingual Wordnet, from NLTK's data repository (fetched directly, which also works
    behind proxies where nltk.download() refuses)."""
    corpora = (folder or offline_folder()) / "nltk_data" / "corpora"
    corpora.mkdir(parents=True, exist_ok=True)
    for package in ("wordnet", _omw_package()):
        progress(f"Downloading {package}…")
        response = requests.get(f"{NLTK_DATA_URL}/{package}.zip", timeout=120)
        response.raise_for_status()
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / f"{package}.zip"
            archive.write_bytes(response.content)
            shutil.rmtree(corpora / package, ignore_errors=True)
            with zipfile.ZipFile(archive) as z:   # unzipped corpora load faster
                z.extractall(corpora)
    progress("Installed WordNet")


def main(argv: list[str]) -> int:
    command = argv[0] if argv else "help"
    if command == "list":
        for code, name in available_languages():
            print(f"{code:6} {name}")
    elif command == "install" and len(argv) > 1:
        index = fetch_index()
        for code in argv[1:]:
            install_language(code, index=index)
    elif command == "wordnet":
        install_wordnet()
    elif command == "setup":
        index = fetch_index()
        install_language("ar", index=index)
        install_language("sv", index=index)
        try:
            install_wordnet()
        except Exception as error:  # word meanings are optional
            print(f"WordNet wasn't installed ({error}). Translation still works.")
        print(f"Offline engine ready in: {offline_folder()}")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
