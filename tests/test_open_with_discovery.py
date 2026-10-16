"""Portable discovery contracts; the real Shell API runs in installer CI."""
import ctypes
from types import SimpleNamespace

import pytest

from luma import decoders
from luma import shell_integration as shell
from scripts import probe_open_with as probe

EXE = r"C:\Portable Apps\Luma 日本語\Luma.exe"


def handler(executable=EXE, display_name="Luma", recommended=True):
    return dict(executable=executable, display_name=display_name, recommended=recommended)


def test_open_with_registration_covers_exactly_supported_decoder_extensions():
    assert set(shell.EXTENSIONS) == decoders.SUPPORTED_EXTENSIONS
    assert len(shell.EXTENSIONS) == len(set(shell.EXTENSIONS))
    plan = {(write.key, write.name): write.value for write in shell.registration_plan(EXE)}
    application = shell.CLASSES + r"\Applications\Luma.exe"
    assert plan[application, "FriendlyAppName"] == shell.Value(shell.REG_SZ, "Luma")
    for extension in decoders.SUPPORTED_EXTENSIONS:
        progid = "Luma.Image." + extension[1:]
        assert plan[shell.CLASSES + "\\" + extension + r"\OpenWithProgIDs", progid] == shell.Value(shell.REG_SZ, "")
        assert plan[application + r"\SupportedTypes", extension] == shell.Value(shell.REG_SZ, "")
        assert plan[shell.CAPABILITIES + r"\FileAssociations", extension] == shell.Value(shell.REG_SZ, progid)
        assert plan[shell.CLASSES + "\\" + progid + r"\shell\open\command", ""] == shell.Value(shell.REG_SZ, f'"{EXE}" "%1"')
    assert not any(name in ("NoOpenWith", "NoStaticDefaultVerb") for _, name in plan)


def test_discovery_checks_every_extension_and_records_recommendation_without_requiring_it():
    queried = []

    def enumerate_handlers(extension):
        queried.append(extension)
        return [handler(recommended=False)]

    result = probe.discovery_report(EXE, shell.EXTENSIONS, enumerate_handlers)
    assert result["passed"]
    assert queried == list(shell.EXTENSIONS)
    assert all(row["luma_handlers"][0]["recommended"] is False for row in result["extensions"])


@pytest.mark.parametrize("candidate", [
    handler(executable=r"D:\Old Copy\Luma.exe"),
    handler(executable=None), handler(display_name=""), handler(display_name="Other"),
])
def test_discovery_does_not_accept_a_stale_copy_or_missing_display_name(candidate):
    result = probe.discovery_report(EXE, [".png"], lambda extension: [candidate])
    assert not result["passed"]


def test_discovery_matches_case_insensitive_windows_path():
    result = probe.discovery_report(EXE, [".hdr"], lambda extension: [handler(EXE.upper())])
    assert result["passed"]


def test_missing_one_extension_fails_the_complete_probe():
    result = probe.discovery_report(EXE, [".png", ".hdr"],
                                    lambda extension: [handler()] if extension == ".png" else [])
    assert not result["passed"]
    assert [row["found"] for row in result["extensions"]] == [True, False]


def test_native_error_is_retained_and_other_extensions_are_still_checked():
    def enumerate_handlers(extension):
        if extension == ".png":
            raise OSError("Native query failed")
        return [handler()]

    result = probe.discovery_report(EXE, [".png", ".hdr"], enumerate_handlers)
    assert not result["passed"]
    assert result["extensions"][0]["error"] == "Native query failed"
    assert result["extensions"][1]["found"]


def test_empty_probe_is_not_a_success():
    assert not probe.discovery_report(EXE, [], lambda extension: [handler()])["passed"]


def test_native_probe_refuses_non_windows(monkeypatch):
    monkeypatch.setattr(probe.sys, "platform", "linux")
    with pytest.raises(OSError, match="requires Windows"):
        probe.ShellHandlers()


def test_hresult_contract():
    probe._check_hr(0, "success")
    probe._check_hr(1, "success without value")
    with pytest.raises(OSError, match="0x80004005"):
        probe._check_hr(-2147467259, "native failure")


def fake_native_shell(monkeypatch, *, string_error=False, next_error=False):
    """Exercise real pointer/output handling without calling Windows or COM."""
    strings = []
    freed, released, calls = [], [], []
    state = {"next": 0}

    def assign(output, kind, value):
        ctypes.cast(output, ctypes.POINTER(kind)).contents.value = value

    def enumerate_handlers(extension, filter_value, output):
        assert extension == ".png" and filter_value == 0
        assign(output, probe.POINTER, 100)
        return 0

    def method(interface, slot, result_type, *argument_types):
        address = interface.value
        calls.append((address, slot))
        if slot == 2:
            return lambda pointer: released.append(pointer.value) or 0
        if address == 100 and slot == 3:
            def next_handler(pointer, count, handler, fetched):
                assert count == 1
                if state["next"]:
                    return 1
                state["next"] += 1
                assign(handler, probe.POINTER, 200)
                assign(fetched, probe.ULONG, 1)
                return -2147467259 if next_error else 0
            return next_handler
        assert address == 200
        if slot == 6:
            return lambda pointer: 1
        assert slot in (3, 4), "A non-read-only IAssocHandler method was requested"

        def get_string(pointer, output):
            value = ctypes.create_unicode_buffer(EXE if slot == 3 else "Luma")
            strings.append(value)
            assign(output, probe.POINTER, ctypes.addressof(value))
            return -2147467259 if string_error and slot == 4 else 0
        return get_string

    monkeypatch.setattr(probe, "_method", method)
    native = probe.ShellHandlers.__new__(probe.ShellHandlers)
    native.shell = SimpleNamespace(SHAssocEnumHandlers=enumerate_handlers)
    native.ole = SimpleNamespace(CoTaskMemFree=lambda pointer: freed.append(pointer.value))
    return native, strings, freed, released, calls


@pytest.mark.parametrize("string_error", [False, True])
def test_native_reader_frees_strings_and_releases_handlers_on_string_errors(monkeypatch, string_error):
    native, strings, freed, released, calls = fake_native_shell(monkeypatch, string_error=string_error)
    result, = native.enumerate(".png")
    assert result["executable"] == EXE
    assert result["display_name"] == (None if string_error else "Luma")
    assert result["recommended"] is False
    assert freed == [ctypes.addressof(value) for value in strings]
    assert released == [200, 100]
    assert set(calls) == {(100, 3), (100, 2), (200, 2), (200, 3), (200, 4), (200, 6)}


def test_native_reader_releases_partial_handler_and_enumerator_on_next_failure(monkeypatch):
    native, _, _, released, _ = fake_native_shell(monkeypatch, next_error=True)
    with pytest.raises(OSError, match="IEnumAssocHandlers.Next"):
        native.enumerate(".png")
    assert released == [200, 100]


def test_native_reader_bounds_enumeration_and_releases_resources(monkeypatch):
    native, strings, freed, released, _ = fake_native_shell(monkeypatch)
    monkeypatch.setattr(probe, "MAX_HANDLERS", 1)
    with pytest.raises(OSError, match="exceeded the 1-handler bound"):
        native.enumerate(".png")
    assert freed == [ctypes.addressof(value) for value in strings]
    assert released == [200, 100]
