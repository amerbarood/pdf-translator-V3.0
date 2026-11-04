"""The one folder everything the app keeps lives in: offline engine, language packs and saved translations.

Default: ~/PDFTranslator. Set the PDFTRANSLATOR_HOME environment variable to use another folder.
"""
from __future__ import annotations

import os
from pathlib import Path


def app_folder() -> Path:
    custom = os.environ.get("PDFTRANSLATOR_HOME")
    folder = Path(custom).expanduser() if custom else Path.home() / "PDFTranslator"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def data_folder() -> Path:
    folder = app_folder() / "Data"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def offline_folder() -> Path:
    return app_folder() / "Offline"


def resources_folder() -> Path:
    return Path(__file__).resolve().parent / "resources"
