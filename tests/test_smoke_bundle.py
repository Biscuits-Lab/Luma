from io import BytesIO, TextIOWrapper
import sys

import pytest

from scripts.smoke_bundle import configure_console_output


@pytest.mark.parametrize("encoding", ["ascii", "cp1252"])
def test_smoke_logs_support_unicode_on_legacy_consoles(monkeypatch, encoding):
    output, errors = BytesIO(), BytesIO()
    stdout = TextIOWrapper(output, encoding=encoding)
    stderr = TextIOWrapper(errors, encoding=encoding)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    configure_console_output()
    print("图像 测试.png: exit 0", flush=True)
    print("相机 测试.NEF", file=sys.stderr, flush=True)
    assert b"\\u56fe" in output.getvalue()
    assert b"\\u76f8" in errors.getvalue()


def test_large_exr_packaged_smoke_uses_bounded_writer(tmp_path, monkeypatch):
    from scripts import smoke_bundle, smoke_thumbnails
    calls = []
    monkeypatch.setattr(smoke_thumbnails, "write_exr_thumbnail_fixture",
                        lambda *args, **kwargs: calls.append((args, kwargs)))
    path = smoke_bundle.prepare_exr_viewer_fixture(tmp_path)
    assert path.name == "图像 8192-square.exr"
    assert calls == [((path, 8192, 8192), {
        "sample_type": "FLOAT", "compression": "ZIP", "channels": ("R", "G", "B", "A")})]
