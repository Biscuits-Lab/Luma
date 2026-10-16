# Display-language localization

Luma follows the operating system's **display language** at launch. It ships English, Simplified Chinese (`zh-CN`), and Traditional Chinese (`zh-TW`). Unsupported primary display languages fall back to English. Restart Luma after changing the OS language; there is no manual language setting and no polling or registry change.

## Selection and data safety

- Windows: read the ordered `GetUserPreferredUILanguages(MUI_LANGUAGE_NAME)` list, taking its primary display language. If unavailable, use `GetUserDefaultUILanguage`. Do **not** use the Windows regional number/date locale. English Windows with Chinese regional formatting therefore remains English
- Other platforms: use `QLocale.system().uiLanguages()`, then message-language environment variables as a last resort
- `zh-Hans`, mainland China and Singapore map to Simplified Chinese; `zh-Hant`, Taiwan, Hong Kong and Macao map to Traditional Chinese. An explicit script takes precedence over region
- Language detection is cached per application session. Tests explicitly call `set_language('en')` by default and select their own target language for localization cases
- Catalog keys are exact, owned English templates. Runtime parameters are inserted **after** translation. No regex, substring replacement, or translation of arbitrary exception text is performed
- File paths/names, format identifiers, channel identifiers, numeric values, metadata keys and metadata values stay unchanged. Metadata is presented beneath a translated “Original metadata” heading
- Decoder/OS/network diagnostics are preserved verbatim beneath a translated action summary and “Technical details” label. Message boxes explicitly render plain text, so markup-like filenames cannot become HTML
- Translated tone/background labels use stable `QComboBox.itemData` identifiers for rendering; changing UI language does not alter decoder inputs

## Covered UI

Welcome screen; application menus; window controls and accessibility names; toolbar tooltips; image information; background/tone controls; load/scan/cancel/error states; keyboard shortcuts; file dialogs; Save As format selection, SDR warnings and destination errors; replacement/recycling confirmations; Settings; update checking, download, verification, install and launch statuses/errors; Explorer integration status and repair prompts.

Qt's matching `qtbase_zh_CN.qm` / `qtbase_zh_TW.qm` catalogs translate built-in file dialogs and standard buttons. Windows native dialogs also follow Windows' own UI language. The translator is retained by `QApplication`. `Luma.spec` requires both catalogs and bundles them under `_internal/luma/translations`. The explicit frozen `--smoke-test` route loads both **bundled** catalogs and exits with code 4 if either is missing or unreadable; it does not accept a development-machine fallback as release evidence. Headless thumbnail and installer routes remain separate from GUI imports.

## Layout regressions covered

- Header remains 34 logical pixels high; footer remains 36
- Footer vector glyphs are larger (24 logical-pixel icon boxes) without increasing the bar or hit-area height
- The metadata/info group reserves a stable, bounded left region. Metadata takes its natural text width; the info button sits 7 logical pixels after its label. Long text elides with a lossless tooltip, and only this left group compresses at 800-pixel window width
- Zoom text and a painter-drawn chevron have separate, fixed geometry. No font-dependent down-arrow glyph or padding spaces are used. Percentages remain relative to fit, with fit exactly 100%; actual-pixel zoom is separate
- Settings wraps text inside a scrollable content area, keeping Close reachable and preserving long diagnostics. Simplified/Traditional Chinese were checked at fractional and integer scaling
- Rotate/delete vectors follow the supplied outline references, including the rotation pivot ring and the bin's handle/tapered outline. Existing click targets are unchanged

## Settings cards

Settings groups Appearance and Version & updates into dark,
scrollable cards. The background selector controls the **current viewer
window** immediately; it does not imply saved preferences. Changes through the
main viewer also update this selector. Thumbnail-strip and image-information
visibility remain controlled by the main viewer toolbar rather than duplicate
Settings options. The update action sits beside its section heading; the idle
view omits explanatory status text until there is an actual result or progress.
Opening Settings does not check for updates, write registration, or schedule
image rendering or thumbnail work.

Routine explanations are available in tooltips and keyboard-operable details
panels. Windows integration and language selection remain automatic and have no
Settings section or footer label. Automatic-registration failures still produce
their existing nonmodal notice. Update
download/launch decisions still display SHA-256 verification, separate-copy
installation, and previous-version rollback information. There is no thumbnail
opt-in switch or automatic-update switch. An accessible top-right close icon
replaces the bottom Close button and wordmark; Escape and closing during an
update still cancel outstanding work. The close target remains outside the
scrolling content and stays reachable in small windows.
The initial window size is capped against the current screen's available logical
geometry, leaving room for the native window frame; high-DPI small screens use
vertical scrolling rather than opening an oversized dialog.

Settings explicitly prefers Microsoft YaHei on Windows, including semibold
headings. When that system font is unavailable, the font preference uses the
matching Simplified/Traditional Chinese family rather than a generic CJK
fallback. This does not change display-language selection or catalog strings.
No Microsoft font files are bundled or downloaded. Linux preview images use the
installed Noto Sans CJK SC/TC families and are not evidence of YaHei or native
Windows rendering.

## Menus and initial display

The ellipsis and image context menus use the same rounded, alpha-transparent
Qt popup surface and outline icons. Opening uses a short painted fade; Windows
UI-effects, menu-animation and accessibility-animation settings can disable it.
The context menu reuses the viewer's copy, rotate, confirmed recycle-bin delete
and Save As operations, and can copy the exact current source path. A changed
image or render request invalidates an already-open image menu. Standard menu
navigation, outside-click dismissal, Escape, Menu and Shift+F10 remain available.

Opening a supplied image or folder stages a neutral loading canvas before the
first window show. The welcome icon is reserved for genuine no-file or empty
states; decoding and folder scanning remain asynchronous. This addresses the
application's transient welcome-page paint, not every possible Windows
compositor transition.

## Reproduce checks and screenshots

```sh
python -m pytest tests/test_i18n.py tests/test_i18n_dialogs.py tests/test_footer_localization_layout.py tests/test_ui.py tests/test_chrome.py tests/test_zoom_stability.py tests/test_actions.py tests/test_updater.py tests/test_shell_settings.py tests/test_settings_redesign.py tests/test_menu_widgets.py tests/test_startup_display.py
QT_QPA_PLATFORM=offscreen QT_SCALE_FACTOR=1.5 python -m scripts.render_ui_preview --language zh-CN --output /tmp/luma-ui
```

`render_ui_preview` generates its own PNG fixture and captures the actual Qt viewer, header, footer and Settings. It does not load user photos. Tests cover `en`/`zh-CN`/`zh-TW`, unsupported locales, mocked Windows UI API calls and failures, catalog key/placeholder coverage, safe raw values, standard dialogs, long text, narrow windows, 125%/150%/200% DPI, vector-icon rasterization and repeated zoom/navigation.

Cloud offscreen Qt tests are **not** a native Windows visual or OS-integration certification. Release QA must still run the frozen application on Windows, including English Windows with Chinese regional formatting and both Chinese display-language variants.

## API references

- [Windows preferred UI language API](https://learn.microsoft.com/windows/win32/api/winnls/nf-winnls-getuserpreferreduilanguages)
- [Windows default UI language fallback](https://learn.microsoft.com/windows/win32/api/winnls/nf-winnls-getuserdefaultuilanguage)
- [Qt UI language and translation loading](https://doc.qt.io/qt-6/qtranslator.html)
