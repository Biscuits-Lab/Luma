"""EXR status reads effective Shell selection without changing registration."""
from types import SimpleNamespace
import pytest
from luma import shell_integration as shell


@pytest.mark.parametrize("provider", [shell.PROVIDERS["exr"], shell.PROVIDERS["raster"]])
def test_exr_status_accepts_dedicated_and_executable_handlers(monkeypatch, provider):
    seen = []
    registry = SimpleNamespace(effective_provider_for=lambda ext: seen.append(ext) or provider)
    monkeypatch.setattr(shell, "_association_string", lambda *args: pytest.fail("No redundant query"))
    assert "selects Luma's thumbnail handler for .exr" in shell._thumbnail_association_detail(registry, ".exr")
    assert seen == [".exr"]


@pytest.mark.parametrize("provider", [None, shell.PROVIDERS["hdr"], "{11111111-1111-1111-1111-111111111111}"])
def test_exr_status_does_not_confuse_registration_with_effective_selection(monkeypatch, provider):
    registry = SimpleNamespace(effective_provider_for=lambda ext: provider)
    monkeypatch.setattr(shell, "_association_string", lambda ext, kind: r"Applications\Luma.exe")
    detail = shell._thumbnail_association_detail(registry, ".exr")
    assert "selects Luma's" not in detail
    assert "Open With" in detail and "Choose Luma for .exr" in detail
    assert ".hdr" not in detail


def test_exr_status_handles_unavailable_shell_and_rejects_other_extension():
    def failed(ext):
        raise OSError("Shell unavailable")
    registry = SimpleNamespace(effective_provider_for=failed)
    assert "could not be checked" in shell._thumbnail_association_detail(registry, ".exr")
    with pytest.raises(ValueError):
        shell._thumbnail_association_detail(registry, ".unknown")
