"""Explicit, confirmed cross-copy Explorer registration repair for manual Setup.

Normal startup, silent Setup and managed updates must never call this route.
Qt is imported only when the interactive command is invoked. The backend never
executes an old copy, removes files, or changes Windows default-app choices.
"""
from __future__ import annotations

from pathlib import Path
import sys
from typing import Callable

from . import installer, shell_integration as shell


TITLE = "Use this Luma installation for Explorer?"
QUESTION = (
    "Explorer registration currently points to:\n{previous}\n\n"
    "Use this installed copy instead?\n{current}\n\n"
    "This updates Luma's Explorer entries to the installed copy. "
    "Your Windows default-app choices will stay unchanged. "
    "No application files will be deleted."
)
ERROR_TITLE = "Explorer registration was not moved"
ERROR_SUMMARY = (
    "The existing registration could not be safely moved. "
    "Your Windows default-app choices and application files are unchanged."
)

# Kept local because this dialog is exclusive to the interactive Setup helper.
TRANSLATIONS = {
    "zh-CN": {
        TITLE: "将此 Luma 安装用于资源管理器？",
        QUESTION: (
            "资源管理器注册当前指向：\n{previous}\n\n"
            "是否改用此安装版本？\n{current}\n\n"
            "此操作会将 Luma 的资源管理器注册更新为此安装版本。"
            "Windows 默认应用选择将保持不变，不会删除任何应用文件。"
        ),
        ERROR_TITLE: "未迁移资源管理器注册",
        ERROR_SUMMARY: "无法安全迁移现有注册。Windows 默认应用选择和应用文件保持不变。",
    },
    "zh-TW": {
        TITLE: "將此 Luma 安裝用於檔案總管？",
        QUESTION: (
            "檔案總管註冊目前指向：\n{previous}\n\n"
            "是否改用此安裝版本？\n{current}\n\n"
            "此操作會將 Luma 的檔案總管註冊更新為此安裝版本。"
            "Windows 預設應用程式選擇將保持不變，不會刪除任何應用程式檔案。"
        ),
        ERROR_TITLE: "未移轉檔案總管註冊",
        ERROR_SUMMARY: "無法安全移轉現有註冊。Windows 預設應用程式選擇及應用程式檔案保持不變。",
    },
}


def _tr(template: str, **values) -> str:
    from .i18n import get_language
    return TRANSLATIONS.get(get_language(), {}).get(template, template).format(**values)


def _validated_installation(executable: Path) -> tuple[str, tuple[str, ...]]:
    # Validate the original path before resolving: a junction must not be made
    # invisible by canonicalization. Only the current managed root is inspected.
    candidates = installer.installed_executables(executable)
    siblings = tuple(str(path.resolve(strict=True)) for path in candidates)
    current = shell._checked_executable(executable)
    if current.casefold() not in {path.casefold() for path in siblings}:
        raise installer.InstallerError("The current installer payload could not be verified.")
    return current, siblings


def repair_registration(executable: Path, confirm: Callable[[str, str], bool],
                        registry=None) -> shell.Result | None:
    """Confirm a single owner, then compare it atomically before migration.

    ``None`` means cancellation. Any invalid, changed or interrupted ownership
    raises without takeover. Payload validation runs again after the prompt.
    """
    shell._runtime_guard()
    current, siblings = _validated_installation(executable)
    registry = registry if registry is not None else shell.WinRegistry()
    previous = shell.registration_owner(registry)
    if previous.executable.casefold() in {path.casefold() for path in siblings}:
        raise shell.RegistrationConflict(
            "The registration already belongs to this installation; cross-copy repair is not needed.")
    if confirm(previous.executable, current) is not True:
        return None
    checked_current, checked_siblings = _validated_installation(executable)
    if checked_current != current:
        raise installer.InstallerError("The installer payload changed after confirmation.")
    return shell.install(
        Path(current), registry=registry,
        allowed_previous_executables=(previous.executable, *checked_siblings),
        expected_registration=previous,
    )


def confirmation_dialog(previous: str, current: str):
    """Plain, selectable paths; keyboard Enter/Escape both default to No."""
    from PySide6.QtCore import Qt
    from .dialogs import PlainMessageBox
    from .i18n import localize_buttons

    box = PlainMessageBox(
        PlainMessageBox.Icon.Question, _tr(TITLE),
        _tr(QUESTION, previous=previous, current=current),
        PlainMessageBox.StandardButton.Yes | PlainMessageBox.StandardButton.No,
    )
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
    box.setDefaultButton(PlainMessageBox.StandardButton.No)
    box.setEscapeButton(PlainMessageBox.StandardButton.No)
    box.setAccessibleName(_tr(TITLE))
    localize_buttons(box)
    return box


def _confirm(previous: str, current: str) -> bool:
    box = confirmation_dialog(previous, current)
    return box.exec() == box.StandardButton.Yes


def main() -> int:
    """Interactive Setup helper: 0 migrated, 1 declined, 2 refused/failed."""
    from PySide6.QtWidgets import QApplication
    from .dialogs import PlainMessageBox
    from .i18n import error_text, get_language, set_language
    from .settings_widgets import settings_font_families

    app = QApplication.instance() or QApplication(["Luma"])
    app.setApplicationName("Luma")
    set_language()
    if get_language() != "en":
        font = app.font()
        font.setFamilies(settings_font_families())
        app.setFont(font)
    try:
        result = repair_registration(Path(sys.executable), _confirm)
    except (installer.InstallerError, shell.ShellIntegrationError, OSError) as error:
        PlainMessageBox.warning(
            None, _tr(ERROR_TITLE), error_text(_tr(ERROR_SUMMARY), error))
        return 2
    return 1 if result is None else 0
