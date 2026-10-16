"""Small exact-key UI catalogs and OS *display* language selection.

Never pass filenames, metadata, or exception messages through ``tr``. Only
Luma-owned literal templates belong in the catalogs; values are formatted after
lookup. Native/decoder diagnostics are retained verbatim after a translated
summary by ``error_text``. Language is resolved once per application session.
"""
from __future__ import annotations

import ctypes
import locale
import os
from pathlib import Path
import sys

from .i18n_viewer import TRANSLATIONS as VIEWER_TRANSLATIONS

_LANGUAGE: str | None = None


def normalize_language(tag: str | None) -> str:
    """Map a BCP-47/POSIX UI language to a shipped catalog (English fallback)."""
    parts = str(tag or '').replace('_', '-').split('.', 1)[0].split('@', 1)[0].lower().split('-')
    if parts[0] != 'zh':
        return 'en'
    # An explicit script overrides region (e.g. zh-Hans-HK is simplified).
    if 'hant' in parts or 'cht' in parts:
        return 'zh-TW'
    if 'hans' in parts or 'chs' in parts:
        return 'zh-CN'
    return 'zh-TW' if any(part in ('tw', 'hk', 'mo') for part in parts[1:]) else 'zh-CN'


def _windows_ui_languages() -> list[str]:
    """Read the current user's MUI preferences, never the regional locale.

    https://learn.microsoft.com/windows/win32/api/winnls/nf-winnls-getuserpreferreduilanguages
    https://learn.microsoft.com/windows/win32/api/winnls/nf-winnls-getuserdefaultuilanguage
    """
    try:
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    except (AttributeError, OSError):
        return []
    try:
        preferred = kernel.GetUserPreferredUILanguages
        preferred.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32),
                              ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
        preferred.restype = ctypes.c_int32
        count, size = ctypes.c_uint32(), ctypes.c_uint32()
        # MUI_LANGUAGE_NAME; size includes the MULTI_SZ final double NUL.
        if preferred(0x8, ctypes.byref(count), None, ctypes.byref(size)) and 1 < size.value <= 65536:
            buffer = ctypes.create_unicode_buffer(size.value)
            if preferred(0x8, ctypes.byref(count), buffer, ctypes.byref(size)):
                names = [name for name in buffer[:size.value].split('\0') if name]
                if names:
                    return names
    except (AttributeError, OSError, ValueError):
        pass
    try:
        default = kernel.GetUserDefaultUILanguage
        default.argtypes = []
        default.restype = ctypes.c_uint16
        language_id = default()
        name = locale.windows_locale.get(language_id)
        return [name] if name else []
    except (AttributeError, OSError, ValueError):
        return []


def _qt_ui_languages() -> list[str]:
    try:
        from PySide6.QtCore import QLocale
        return list(QLocale.system().uiLanguages())
    except (ImportError, RuntimeError):
        return []


def _posix_ui_languages() -> list[str]:
    # Last-resort fallback without touching process-global setlocale().
    for key in ('LANGUAGE', 'LC_ALL', 'LC_MESSAGES', 'LANG'):
        value = os.environ.get(key, '')
        if value:
            return [tag for tag in value.split(':') if tag]
    try:
        category = getattr(locale, 'LC_MESSAGES', None)
        name = locale.getlocale(category)[0] if category is not None else None
        return [name] if name else []
    except (ValueError, TypeError):
        return []


def detect_system_language() -> str:
    """Follow the primary display language, or English if it is unsupported.

    Windows deliberately never falls back to regional formatting preferences:
    English Windows with a Chinese number/date locale must stay English.
    Other platforms use Qt's OS UI preferences, then message-locale variables.
    """
    if sys.platform == 'win32':
        languages = _windows_ui_languages()
    else:
        languages = _qt_ui_languages() or _posix_ui_languages()
    return normalize_language(languages[0]) if languages else 'en'


def get_language() -> str:
    global _LANGUAGE
    if _LANGUAGE is None:
        _LANGUAGE = detect_system_language()
    return _LANGUAGE


def set_language(language: str | None = None) -> str:
    """Select at startup; explicit tags make tests independent of their host.

    Existing Luma widgets are not rebuilt; restart the app after changing the
    OS display language. Qt translators are retained by QApplication.
    """
    global _LANGUAGE
    _LANGUAGE = detect_system_language() if language is None else normalize_language(language)
    from .ui_fonts import install_ui_font
    install_ui_font(language=_LANGUAGE)
    install_qt_translations()
    return _LANGUAGE


def catalogs() -> tuple[dict, ...]:
    from .i18n_dialogs import TRANSLATIONS as DIALOG_TRANSLATIONS
    from .i18n_details import TRANSLATIONS as DETAILS_TRANSLATIONS
    return VIEWER_TRANSLATIONS, DIALOG_TRANSLATIONS, DETAILS_TRANSLATIONS


def tr(template: str, **values) -> str:
    """Translate an owned template exactly, then insert untouched values."""
    language = get_language()
    translated = template
    if language != 'en':
        for catalog in catalogs():
            if template in catalog.get(language, {}):
                translated = catalog[language][template]
                break
    return translated.format(**values) if values else translated


def error_text(summary: str, error, **values) -> str:
    return tr(summary, **values) + '\n\n' + tr('Technical details:') + '\n' + str(error)


def install_qt_translations(app=None) -> bool:
    """Install the shipped Qt catalog for built-in dialogs and standard buttons.

    Native OS file dialogs use the OS language; Qt fallback dialogs use this
    translator. Holding a Python reference on the app is essential for Qt.
    """
    from PySide6.QtCore import QCoreApplication, QLibraryInfo, QTranslator
    app = app or QCoreApplication.instance()
    if app is None:
        return False
    language = get_language()
    if getattr(app, '_luma_qt_language', None) == language:
        return language == 'en' or getattr(app, '_luma_qt_translator', None) is not None
    old = getattr(app, '_luma_qt_translator', None)
    if old is not None:
        app.removeTranslator(old)
    app._luma_qt_translator = None
    app._luma_qt_language = language
    if language == 'en':
        return True
    translator = QTranslator(app)
    directory = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    filename = 'qtbase_' + language.replace('-', '_')
    # Frozen builds keep the two requested Qt catalogs at a stable app path.
    packaged = Path(__file__).resolve().parent / 'translations'
    if (translator.load(filename, str(packaged)) or
            translator.load(filename, directory)):
        app.installTranslator(translator)
        app._luma_qt_translator = translator
        return True
    return False


def localize_buttons(box) -> None:
    """Translate standard buttons explicitly when constructing a custom dialog."""
    names = ('Ok', 'Save', 'SaveAll', 'Open', 'Yes', 'YesToAll', 'No', 'NoToAll',
             'Abort', 'Retry', 'Ignore', 'Close', 'Cancel', 'Discard', 'Help',
             'Apply', 'Reset', 'RestoreDefaults')
    labels = ('OK', 'Save', 'Save all', 'Open', 'Yes', 'Yes to all', 'No', 'No to all',
              'Abort', 'Retry', 'Ignore', 'Close', 'Cancel', 'Discard', 'Help',
              'Apply', 'Reset', 'Restore defaults')
    for name, label in zip(names, labels):
        standard = getattr(box.StandardButton, name)
        button = box.button(standard)
        if button is not None:
            button.setText(tr(label))


def verify_packaged_qt_catalogs(directory=None) -> bool:
    """Smoke-test the actual frozen data files, not a developer Qt fallback.

    This is called only by the explicit GUI smoke route, never by thumbnail or
    installer CLI helpers. Missing/corrupt translations must fail release QA.
    """
    from PySide6.QtCore import QTranslator
    directory = Path(directory) if directory is not None else Path(__file__).resolve().parent / 'translations'
    for language in ('zh_CN', 'zh_TW'):
        path = directory / f'qtbase_{language}.qm'
        if not path.is_file() or not QTranslator().load(str(path)):
            return False
    return True
