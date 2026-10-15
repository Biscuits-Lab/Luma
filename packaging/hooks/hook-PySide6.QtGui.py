"""Collect only the raster Widgets platform plugins before dependency scanning."""
from pathlib import Path
import sys
from PyInstaller.utils.hooks.qt import add_qt6_dependencies

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)


def needed_plugin(item):
    source, destination = item
    path = (destination.replace("\\", "/") + "/" + Path(source).name).lower()
    if "/plugins/" not in path:
        return not path.endswith("opengl32sw.dll")
    category, _, filename = path.split("/plugins/", 1)[1].partition("/")
    if category == "platforms":
        allowed = {"qwindows.dll", "qoffscreen.dll"} if sys.platform == "win32" else {"libqxcb.so", "libqoffscreen.so"}
        return filename in allowed
    return category in {"styles", "platformthemes", "xcbglintegrations"}


binaries = [item for item in binaries if needed_plugin(item)]
