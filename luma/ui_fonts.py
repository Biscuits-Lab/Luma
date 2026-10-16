"""One display-language font policy for every Luma-owned UI surface.

CJK faces share many Unicode characters but draw region-specific glyphs. A
Latin-only font followed by generic sans-serif can therefore render Chinese
copy with Japanese glyphs. Choose a face for the selected UI catalog, without
changing that catalog, the OS locale, font sizes, weights, or DPI scaling.
Only installed fonts are used; no proprietary system fonts are redistributed.
"""
from __future__ import annotations


def ui_font_families(language=None):
    from .i18n import get_language, normalize_language
    language = get_language() if language is None else normalize_language(language)
    if language == 'zh-CN':
        return ('Microsoft YaHei', 'Microsoft YaHei UI',
                'Noto Sans CJK SC', 'Noto Sans SC', 'Segoe UI', 'sans-serif')
    if language == 'zh-TW':
        return ('Microsoft JhengHei', 'Microsoft JhengHei UI',
                'Noto Sans CJK TC', 'Noto Sans TC', 'Segoe UI', 'sans-serif')
    return ('Segoe UI', 'Inter', 'sans-serif')


def ui_font_family_css(language=None):
    return ', '.join(f"'{family}'" for family in ui_font_families(language))


def install_ui_font(app=None, language=None):
    """Set the shared family while preserving point size and platform metrics.

    Stylesheets may still specify size/weight, but must not override this with
    a Latin-only family. Standalone popup styles use ui_font_family_css too.
    """
    from PySide6.QtWidgets import QApplication
    app = app or QApplication.instance()
    if not isinstance(app, QApplication):
        return False
    families = list(ui_font_families(language))
    font = app.font()
    if font.families() != families:
        font.setFamilies(families)
        app.setFont(font)
    return True
