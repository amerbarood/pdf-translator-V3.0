"""Main window: toolbar, PDF on the left, translation panel on the right, and the app's menus."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

try:
    import pymupdf as fitz
except ImportError:
    import fitz

from PySide6.QtCore import QObject, QRunnable, QSettings, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel,
                               QListWidget, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QPushButton,
                               QSizePolicy, QSlider, QSplitter, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
                               QWidgetAction)

from . import APP_NAME, languages, text_tools
from .panel import TranslationPanel
from .pdf_view import PdfView
from .speaker import ORIGINAL, TRANSLATION, Speaker
from .translator import Translator

TEXT_SIZE_DEFAULT = 24
TEXT_SIZE_MIN, TEXT_SIZE_MAX = 14, 48


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(object)


class Job(QRunnable):
    """Runs a function on the thread pool and reports back on the main thread."""

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = _Signals()
        self.setAutoDelete(False)   # the window keeps a reference until the result arrives

    def run(self) -> None:
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as error:  # reported to the UI
            self.signals.failed.emit(error)
        else:
            self.signals.done.emit(result)


class MainWindow(QMainWindow):
    progress = Signal(str)

    def __init__(self, translator: Optional[Translator] = None):
        super().__init__()
        self.settings = QSettings("AamerBarood", "PDFTranslator")
        self.translator = translator or Translator()
        self.offline = self.translator.offline
        self.speaker = Speaker(self)
        self.speaker.changed.connect(self.refresh)
        self.pool = QThreadPool.globalInstance()
        self._jobs: set[Job] = set()

        self.document = None
        self.document_path: Optional[Path] = None
        self.source_text = ""
        self.source_label = ""
        self.result = None
        self.error: Optional[str] = None
        self.translating = False
        self.translation_id = 0
        self._pending_selection = ""
        self._swapping = False

        self.source_language = str(self.settings.value("sourceLanguage", "auto"))
        self.target_language = str(self.settings.value("targetLanguage", "ar"))
        stored_offline = self.settings.value("useOffline")
        self.use_offline = (str(stored_offline).lower() == "true") if stored_offline is not None else self.offline.is_installed()
        self.text_size = float(self.settings.value("textSize", TEXT_SIZE_DEFAULT))

        self._selection_timer = QTimer(self)
        self._selection_timer.setSingleShot(True)
        self._selection_timer.setInterval(250)
        self._selection_timer.timeout.connect(lambda: self._translate(self._pending_selection, "Selection"))

        self._build_ui()
        self._build_menus()
        self.setAcceptDrops(True)
        self.resize(1280, 820)
        self.setMinimumSize(900, 600)
        self.refresh()
        if self.offline.is_installed():
            self.offline.preload()

    # ---------------- UI ----------------

    def _build_ui(self) -> None:
        toolbar = self.addToolBar("Main")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)

        self.open_action = QAction("Open PDF…", self)
        self.open_action.setShortcut(QKeySequence.StandardKey.Open)
        self.open_action.setToolTip("Open a PDF (Ctrl/⌘ O)")
        self.open_action.triggered.connect(self.show_open_dialog)
        toolbar.addAction(self.open_action)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        engine = QWidget()
        engine_row = QHBoxLayout(engine)
        engine_row.setContentsMargins(0, 0, 0, 0)
        engine_row.setSpacing(0)
        self.offline_button = QToolButton(text="Offline", checkable=True)
        self.online_button = QToolButton(text="Online", checkable=True)
        self.offline_button.setToolTip("Offline: translate on this computer, no internet needed.")
        self.online_button.setToolTip("Online: Google Translate (better quality, shows word meanings).")
        for button, value in ((self.offline_button, True), (self.online_button, False)):
            button.clicked.connect(lambda _=False, v=value: self.set_offline(v))
            engine_row.addWidget(button)
        toolbar.addWidget(engine)
        toolbar.addSeparator()

        self.source_combo = QComboBox()
        for code, name in languages.SOURCES:
            self.source_combo.addItem(name, code)
        self.source_combo.setToolTip("Language of the PDF")
        self.source_combo.currentIndexChanged.connect(lambda: self._language_changed("source"))
        toolbar.addWidget(self.source_combo)

        self.swap_action = QAction("⇄", self)
        self.swap_action.setToolTip("Swap languages (Ctrl/⌘ Shift S)")
        self.swap_action.triggered.connect(self.swap_languages)
        toolbar.addAction(self.swap_action)

        self.target_combo = QComboBox()
        for code, name in languages.TARGETS:
            self.target_combo.addItem(name, code)
        self.target_combo.setToolTip("Translate into")
        self.target_combo.currentIndexChanged.connect(lambda: self._language_changed("target"))
        toolbar.addWidget(self.target_combo)
        self._select_combo(self.source_combo, self.source_language)
        self._select_combo(self.target_combo, self.target_language)

        toolbar.addSeparator()
        self.size_button = QToolButton(text="Text Size")
        self.size_button.setToolTip("Size of the translated text")
        self.size_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.size_button.setMenu(self._text_size_menu())
        toolbar.addWidget(self.size_button)

        self.page_action = QAction("Translate Page", self)
        self.page_action.setShortcut(QKeySequence("Ctrl+T"))
        self.page_action.setToolTip("Translate the whole current page (Ctrl/⌘ T)")
        self.page_action.triggered.connect(self.translate_current_page)
        toolbar.addAction(self.page_action)

        # Left: empty state or PDF. Right: translation panel.
        self.pdf_view = PdfView()
        self.pdf_view.selection_finished.connect(self.selection_changed)
        empty = QWidget()
        empty_layout = QVBoxLayout(empty)
        empty_layout.addStretch(1)
        icon = QLabel("📄")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("font-size: 52px;")
        hint = QLabel("Drop a PDF here")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("font-size: 20px;")
        open_button = QPushButton("Open PDF…")
        open_button.clicked.connect(self.show_open_dialog)
        open_row = QHBoxLayout()
        open_row.addStretch(1)
        open_row.addWidget(open_button)
        open_row.addStretch(1)
        empty_layout.addWidget(icon)
        empty_layout.addWidget(hint)
        empty_layout.addLayout(open_row)
        empty_layout.addStretch(1)
        self.left = QStackedWidget()
        self.left.addWidget(empty)
        self.left.addWidget(self.pdf_view)

        self.panel = TranslationPanel()
        self.panel.smaller.connect(lambda: self.change_text_size(-2))
        self.panel.bigger.connect(lambda: self.change_text_size(2))
        self.panel.speak_original.connect(self.speak_original)
        self.panel.copy.connect(self.copy_translation)
        self.panel.clear.connect(self.clear)
        self.panel.retry.connect(self.retry)
        self.panel.retry_online.connect(self.retry_online)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(8)   # wide, easy-to-grab divider
        splitter.addWidget(self.left)
        splitter.addWidget(self.panel)
        splitter.setChildrenCollapsible(False)
        self.left.setMinimumWidth(300)
        self.panel.setMinimumWidth(260)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([800, 460])
        self.setCentralWidget(splitter)

    def _text_size_menu(self) -> QMenu:
        menu = QMenu(self)
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(14, 10, 14, 10)
        self.size_label = QLabel()
        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setRange(TEXT_SIZE_MIN // 2, TEXT_SIZE_MAX // 2)
        self.size_slider.setMinimumWidth(220)
        self.size_slider.valueChanged.connect(lambda v: self.set_text_size(v * 2))
        self.size_sample = QLabel("مرحبا  Hello")
        self.size_sample.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.size_sample.setMinimumHeight(60)
        reset = QPushButton("Reset")
        reset.clicked.connect(lambda: self.set_text_size(TEXT_SIZE_DEFAULT))
        layout.addWidget(self.size_label)
        layout.addWidget(self.size_slider)
        layout.addWidget(self.size_sample)
        layout.addWidget(reset, 0, Qt.AlignmentFlag.AlignRight)
        action = QWidgetAction(menu)
        action.setDefaultWidget(box)
        menu.addAction(action)
        return menu

    def _build_menus(self) -> None:
        bar = self.menuBar()
        file_menu = bar.addMenu("&File")
        file_menu.addAction(self.open_action)
        file_menu.addSeparator()
        quit_action = QAction("Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        menu = bar.addMenu("&Translate")
        menu.addAction(self.page_action)
        self.swap_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        menu.addAction(self.swap_action)
        self.offline_action = QAction("Translate Offline", self, checkable=True)
        self.offline_action.setShortcut(QKeySequence("Ctrl+Shift+L"))
        self.offline_action.toggled.connect(self.set_offline)
        menu.addAction(self.offline_action)
        menu.addSeparator()
        self.read_original_action = QAction("Read Original Aloud", self)
        self.read_original_action.setShortcut(QKeySequence("Ctrl+R"))
        self.read_original_action.triggered.connect(self.speak_original)
        menu.addAction(self.read_original_action)
        self.read_translation_action = QAction("Read Translation Aloud", self)
        self.read_translation_action.setShortcut(QKeySequence("Ctrl+Shift+R"))
        self.read_translation_action.triggered.connect(self.speak_translation)
        menu.addAction(self.read_translation_action)
        self.stop_action = QAction("Stop Speaking", self)
        self.stop_action.setShortcut(QKeySequence("Ctrl+."))
        self.stop_action.triggered.connect(self.speaker.stop)
        menu.addAction(self.stop_action)
        menu.addSeparator()
        self.copy_action = QAction("Copy Translation", self)
        self.copy_action.setShortcut(QKeySequence("Ctrl+Shift+C"))
        self.copy_action.triggered.connect(self.copy_translation)
        menu.addAction(self.copy_action)
        clear_saved = QAction("Clear Saved Translations…", self)
        clear_saved.triggered.connect(self.confirm_clear_saved)
        menu.addAction(clear_saved)
        menu.addSeparator()
        packs = QAction("Offline Language Packs…", self)
        packs.triggered.connect(self.show_packs_dialog)
        menu.addAction(packs)

        view = bar.addMenu("&View")
        for title, keys, delta in (("Bigger Text", ["Ctrl++", "Ctrl+="], 2), ("Smaller Text", ["Ctrl+-"], -2)):
            action = QAction(title, self)
            action.setShortcuts([QKeySequence(k) for k in keys])
            action.triggered.connect(lambda _=False, d=delta: self.change_text_size(d))
            view.addAction(action)
        reset = QAction("Default Text Size", self)
        reset.setShortcut(QKeySequence("Ctrl+0"))
        reset.triggered.connect(lambda: self.set_text_size(TEXT_SIZE_DEFAULT))
        view.addAction(reset)

    @staticmethod
    def _select_combo(combo: QComboBox, code: str) -> None:
        index = combo.findData(code)
        combo.blockSignals(True)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    # ---------------- state → UI ----------------

    @property
    def original_language(self) -> str:
        if self.source_language == "auto":
            return (self.result.detected_language if self.result else None) or "en"
        return self.source_language

    def refresh(self) -> None:
        self.setWindowTitle(self.document_path.name if self.document_path else APP_NAME)
        self.offline_button.setChecked(self.use_offline)
        self.online_button.setChecked(not self.use_offline)
        self.offline_action.blockSignals(True)
        self.offline_action.setChecked(self.use_offline)
        self.offline_action.blockSignals(False)
        self.swap_action.setEnabled(self.source_language != "auto")
        self.page_action.setEnabled(self.document is not None)
        self.copy_action.setEnabled(self.result is not None)
        self.read_original_action.setEnabled(bool(self.source_text))
        self.read_translation_action.setEnabled(self.result is not None)
        self.stop_action.setEnabled(self.speaker.speaking is not None)
        self.read_original_action.setText("Stop Reading Original" if self.speaker.speaking == ORIGINAL else "Read Original Aloud")
        self.read_translation_action.setText("Stop Reading Translation" if self.speaker.speaking == TRANSLATION else "Read Translation Aloud")
        self.size_label.setText(f"Text Size: {int(self.text_size)} pt")
        self.size_slider.blockSignals(True)
        self.size_slider.setValue(int(self.text_size) // 2)
        self.size_slider.blockSignals(False)
        sample_font = self.size_sample.font()
        sample_font.setPixelSize(int(self.text_size))
        self.size_sample.setFont(sample_font)

        language = self.original_language
        can_speak = self.speaker.has_voice(language)
        if not self.speaker.available:
            tip = "Text to speech isn't available on this system."
        elif not can_speak:
            tip = f"No {languages.name_for(language) or language} voice is installed on this computer."
        else:
            tip = "Stop reading" if self.speaker.speaking == ORIGINAL else "Read the original text aloud (Ctrl/⌘ R)"
        self.panel.render(label=self.source_label, source_text=self.source_text, result=self.result, error=self.error,
                          translating=self.translating, text_size=self.text_size,
                          source_language=self.source_language, target_language=self.target_language,
                          original_language=language, use_offline=self.use_offline,
                          speaking_original=self.speaker.speaking == ORIGINAL, can_speak=can_speak, speak_tip=tip)

    # ---------------- opening ----------------

    def show_open_dialog(self) -> None:
        start = str(self.settings.value("lastFolder", str(Path.home())))
        path, _ = QFileDialog.getOpenFileName(self, "Open PDF", start, "PDF files (*.pdf)")
        if path:
            self.open(Path(path))

    def open(self, path: Path) -> None:
        try:
            doc = fitz.open(str(path))
            if doc.needs_pass:
                raise ValueError("password-protected")
            if not doc.is_pdf:
                raise ValueError("not a PDF")
        except Exception:
            self.clear()
            self.error = (f"Couldn't open “{path.name}”. It may not be a PDF, or it's damaged or "
                          "password-protected.")
            self.refresh()
            return
        self.document = doc
        self.document_path = path
        self.settings.setValue("lastFolder", str(path.parent))
        self.pdf_view.set_document(doc)
        self.left.setCurrentWidget(self.pdf_view)
        self.clear()

    def dragEnterEvent(self, event) -> None:
        if any(u.toLocalFile().lower().endswith(".pdf") for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            if url.toLocalFile().lower().endswith(".pdf"):
                self.open(Path(url.toLocalFile()))
                break

    # ---------------- translating ----------------

    def selection_changed(self, text: str) -> None:
        if text.strip():
            self._pending_selection = text
            self._selection_timer.start()

    def translate_current_page(self) -> None:
        if self.document is None:
            return
        index = self.pdf_view.current_page_index()
        text = self.pdf_view.page_text(index)
        label = f"Page {index + 1}"
        if not text.strip():
            self.speaker.stop()
            self.result = None
            self.source_text = ""
            self.source_label = label
            self.error = f"Page {index + 1} has no selectable text. It's probably a scanned image."
            self.refresh()
            return
        self._translate(text, label)

    def retry(self) -> None:
        if self.source_text:
            self._translate(self.source_text, self.source_label, reflow=False)

    def retry_online(self) -> None:
        """Translates the current text once with Google, without changing the Offline setting."""
        if self.source_text:
            self._translate(self.source_text, self.source_label, reflow=False, offline=False)

    def _translate(self, raw: str, label: str, reflow: bool = True, offline: Optional[bool] = None) -> None:
        text = text_tools.reflow(raw) if reflow else raw
        if not text.strip():
            return
        self.speaker.stop()
        self.translation_id += 1
        job_id = self.translation_id
        self.source_text, self.source_label = text, label
        self.error, self.result, self.translating = None, None, True
        self.refresh()

        job = Job(self.translator.translate, text, self.source_language, self.target_language,
                  self.use_offline if offline is None else offline)
        job.signals.done.connect(lambda result, j=job: self._finished(j, job_id, result, None))
        job.signals.failed.connect(lambda error, j=job: self._finished(j, job_id, None, error))
        self._jobs.add(job)
        self.pool.start(job)

    def _finished(self, job: Job, job_id: int, result, error) -> None:
        self._jobs.discard(job)
        if job_id != self.translation_id:   # a newer translation replaced this one
            return
        self.translating = False
        self.result = result
        self.error = str(error) if error is not None else None
        self.refresh()

    def _language_changed(self, which: str) -> None:
        if which == "source":
            self.source_language = self.source_combo.currentData()
            self.settings.setValue("sourceLanguage", self.source_language)
        else:
            self.target_language = self.target_combo.currentData()
            self.settings.setValue("targetLanguage", self.target_language)
        self.refresh()
        if not self._swapping and self.source_text:
            self.retry()

    def swap_languages(self) -> None:
        if self.source_language == "auto":
            return
        translated = self.result.text if self.result else None
        self._swapping = True
        source, target = self.source_language, self.target_language
        self._select_combo(self.source_combo, target)
        self._select_combo(self.target_combo, source)
        self.source_language, self.target_language = target, source
        self.settings.setValue("sourceLanguage", target)
        self.settings.setValue("targetLanguage", source)
        self._swapping = False
        if translated:
            self._translate(translated, self.source_label, reflow=False)
        else:
            self.refresh()

    def set_offline(self, value: bool) -> None:
        if value == self.use_offline:
            self.refresh()
            return
        self.use_offline = value
        self.settings.setValue("useOffline", value)
        self.refresh()
        if self.source_text:
            self.retry()

    # ---------------- other actions ----------------

    def clear(self) -> None:
        self._selection_timer.stop()
        self.translation_id += 1
        self.speaker.stop()
        self.source_text = self.source_label = ""
        self.result = self.error = None
        self.translating = False
        self.pdf_view.clear_selection()
        self.refresh()

    def copy_translation(self) -> None:
        if self.result is not None:
            QGuiApplication.clipboard().setText(self.result.text)

    def speak_original(self) -> None:
        if self.source_text:
            self.speaker.toggle(ORIGINAL, self.source_text, self.original_language)
            self.refresh()

    def speak_translation(self) -> None:
        if self.result is not None:
            self.speaker.toggle(TRANSLATION, self.result.text, self.target_language)
            self.refresh()

    def set_text_size(self, size: float) -> None:
        self.text_size = float(min(max(size, TEXT_SIZE_MIN), TEXT_SIZE_MAX))
        self.settings.setValue("textSize", self.text_size)
        self.refresh()

    def change_text_size(self, delta: float) -> None:
        self.set_text_size(self.text_size + delta)

    def confirm_clear_saved(self) -> None:
        count = self.translator.saved_count
        answer = QMessageBox.question(
            self, "Clear saved translations?",
            f"The app has {count} saved translation{'' if count == 1 else 's'}. "
            "Text you select again will be translated again.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Yes:
            self.translator.clear_saved()

    def show_packs_dialog(self) -> None:
        PacksDialog(self).exec()
        self.offline.reset()
        self.refresh()

    def run_job(self, fn, on_done, on_failed, *args) -> None:
        job = Job(fn, *args)
        job.signals.done.connect(lambda r, j=job: (self._jobs.discard(j), on_done(r)))
        job.signals.failed.connect(lambda e, j=job: (self._jobs.discard(j), on_failed(e)))
        self._jobs.add(job)
        self.pool.start(job)

    def closeEvent(self, event) -> None:
        self.speaker.stop()
        super().closeEvent(event)


class PacksDialog(QDialog):
    """Shows installed offline language packs and downloads new ones in the background."""
    message = Signal(str)

    def __init__(self, window: MainWindow):
        super().__init__(window)
        self.window_ = window
        self.engine = window.offline
        self.index = None
        self.setWindowTitle("Offline Language Packs")
        self.resize(520, 460)
        layout = QVBoxLayout(self)

        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addWidget(QLabel("Installed:"))
        self.installed = QListWidget()
        layout.addWidget(self.installed, 1)

        row = QHBoxLayout()
        self.languages = QComboBox()
        self.languages.addItem("Loading the list of languages…")
        self.languages.setEnabled(False)
        self.install_button = QPushButton("Install")
        self.install_button.setEnabled(False)
        self.install_button.clicked.connect(self.install)
        self.wordnet_button = QPushButton("Install WordNet")
        self.wordnet_button.setToolTip("Synonyms and meanings for words (about 130 MB)")
        self.wordnet_button.clicked.connect(self.install_wordnet)
        row.addWidget(self.languages, 1)
        row.addWidget(self.install_button)
        row.addWidget(self.wordnet_button)
        layout.addLayout(row)

        self.log = QPlainTextEdit(readOnly=True)
        self.log.setMaximumHeight(110)
        layout.addWidget(self.log)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.message.connect(self.log.appendPlainText)
        self.refresh()
        if self.engine.dependencies_installed():
            from . import offline_setup
            self.window_.run_job(offline_setup.fetch_index, self._index_loaded,
                                 lambda e: self._index_failed(e))

    def refresh(self) -> None:
        pairs = self.engine.pairs()
        self.installed.clear()
        for pair in pairs:
            a, b = pair.split("_")
            self.installed.addItem(f"{languages.name_for(a) or a} → {languages.name_for(b) or b}   ({pair})")
        if not self.engine.dependencies_installed():
            self.status.setText("The offline engine isn't installed. In a terminal, in the app's folder, run:\n"
                                "pip install -r requirements-offline.txt\nthen reopen the app.")
        else:
            self.status.setText(f"Packs are stored in {self.engine.packs}. Each language installs both "
                                "directions with English; other pairs go through English.")
        self.wordnet_button.setEnabled(self.engine.dependencies_installed())
        self.wordnet_button.setText("Reinstall WordNet" if self.engine.nltk_data.is_dir() else "Install WordNet")

    def _index_loaded(self, index) -> None:
        from . import offline_setup
        self.index = index
        self.languages.clear()
        for code, name in offline_setup.available_languages(index):
            self.languages.addItem(f"{name} ({code})", code)
        self.languages.setEnabled(True)
        self.install_button.setEnabled(True)

    def _index_failed(self, error) -> None:
        self.languages.clear()
        self.languages.addItem("Couldn't load the list of languages")
        self.log.appendPlainText(f"Couldn't load the list of languages: {error}")

    def install(self) -> None:
        from . import offline_setup
        code = self.languages.currentData()
        if not code:
            return
        self.install_button.setEnabled(False)
        self.window_.run_job(offline_setup.install_language, self._done, self._failed,
                             code, self.message.emit, self.index)

    def install_wordnet(self) -> None:
        from . import offline_setup
        self.wordnet_button.setEnabled(False)
        self.window_.run_job(offline_setup.install_wordnet, self._done, self._failed, self.message.emit)

    def _done(self, _result) -> None:
        self.engine.reset()
        self.install_button.setEnabled(self.index is not None)
        self.refresh()

    def _failed(self, error) -> None:
        self.log.appendPlainText(f"Failed: {error}")
        self.install_button.setEnabled(self.index is not None)
        self.refresh()
