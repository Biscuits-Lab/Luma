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
