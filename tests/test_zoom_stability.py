"""Fit-relative zoom and footer stability use synthetic images only."""
import math

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QImage

from luma.app import ViewerWindow
from luma.canvas import ImageCanvas


def image(width, height, dpr=1.0):
    result = QImage(width, height, QImage.Format.Format_RGBA8888)
    result.fill(Qt.GlobalColor.blue)
    result.setDevicePixelRatio(dpr)
    return result


@pytest.fixture
def canvas(qapp):
    result = ImageCanvas()
    result.resize(800, 600)
    result.show()
    qapp.processEvents()
    yield result
    result.close()


def test_fit_is_always_100_across_resolution_aspect_and_clear(canvas, qapp):
    seen = []
    canvas.zoomChanged.connect(seen.append)
    scales = []
    for width, height in [(16, 12), (640, 480), (1920, 1080), (80, 1600), (1600, 80)] * 3:
        canvas.clear_image()
        qapp.processEvents()
        canvas.set_image(image(width, height))
        qapp.processEvents()
        assert canvas.fitted
        assert canvas.zoom_percent == 100
        scales.append(canvas.transform().m11())
    assert len(set(scales)) == 5
    assert seen == [100.0]  # No pixel-scale values or repeated reset notifications.


def test_new_image_resets_zoom_once_without_transient_values(canvas, qapp):
    canvas.set_image(image(1600, 1200))
    canvas.set_zoom_percent(250)
    seen = []
    canvas.zoomChanged.connect(seen.append)
    canvas.clear_image()
    qapp.processEvents()
    assert seen == []
    canvas.set_image(image(120, 800))
    qapp.processEvents()
    assert seen == [100.0]
    assert canvas.fitted


def test_presets_and_multipliers_are_relative_to_fit(canvas):
    canvas.set_image(image(1600, 1200))
    baseline = canvas.transform().m11()
    canvas.set_zoom_percent(200)
    assert canvas.zoom_percent == 200
    assert canvas.transform().m11() == pytest.approx(baseline * 2)
    canvas.zoom(.8)
    assert canvas.zoom_percent == pytest.approx(160)
    assert canvas.transform().m11() == pytest.approx(baseline * 1.6)
    canvas.set_zoom_percent(100)
    assert canvas.fitted
    assert canvas.transform().m11() == pytest.approx(baseline)


def test_rerender_preserves_zoom_and_rotation(canvas):
    canvas.set_image(image(1600, 1200))
    canvas.rotate_clockwise()
    canvas.set_zoom_percent(200)
    before = canvas.transform()
    seen = []
    canvas.zoomChanged.connect(seen.append)
    canvas.set_image(image(1600, 1200), reset=False)
    assert canvas.item.rotation() == 90
    assert canvas.transform() == before
    assert canvas.zoom_percent == 200
    assert seen == []


def test_resize_keeps_relative_zoom_and_fit_baseline(canvas, qapp):
    canvas.set_image(image(1600, 1200))
    original = canvas.transform().m11()
    seen = []
    canvas.zoomChanged.connect(seen.append)
    canvas.resize(1000, 720)
    qapp.processEvents()
    assert canvas.zoom_percent == 100
    assert canvas.transform().m11() > original
    assert seen == []
    canvas.set_zoom_percent(200)
    canvas.resize(680, 500)
    qapp.processEvents()
    assert canvas.zoom_percent == 200
    zoomed = canvas.transform().m11()
    canvas.fit()
    assert zoomed == pytest.approx(canvas.transform().m11() * 2)
    assert seen == [200.0, 100.0]


@pytest.mark.parametrize('display_dpr', [1.0, 1.25, 1.5, 2.0])
@pytest.mark.parametrize('image_dpr', [1.0, 2.0])
def test_actual_pixels_is_separate_and_dpi_correct(canvas, qapp, monkeypatch, display_dpr, image_dpr):
    monkeypatch.setattr(canvas.viewport(), 'devicePixelRatioF', lambda: display_dpr)
    canvas.set_image(image(1600, 1200, image_dpr))
    baseline = canvas.transform().m11()
    assert canvas.zoom_percent == 100
    canvas.actual_size()
    assert not canvas.fitted
    assert canvas.transform().m11() * display_dpr / image_dpr == pytest.approx(1)
    assert canvas.zoom_percent == pytest.approx(image_dpr / display_dpr / baseline * 100)
    canvas.resize(900, 650)
    qapp.processEvents()
    assert canvas.transform().m11() * display_dpr / image_dpr == pytest.approx(1)
    canvas.fit()
    assert canvas.zoom_percent == 100


def test_actual_pixels_tracks_display_dpr_changes(canvas, qapp, monkeypatch):
    canvas.set_image(image(1600, 1200))
    canvas.actual_size()
    monkeypatch.setattr(canvas.viewport(), 'devicePixelRatioF', lambda: 2.0)
    qapp.sendEvent(canvas, QEvent(QEvent.Type.DevicePixelRatioChange))
    assert canvas.transform().m11() == pytest.approx(.5)
    assert canvas.zoom_percent == pytest.approx(.5 / canvas._fit_scale * 100)


def test_zoom_limits_invalid_values_and_no_feedback(canvas):
    canvas.set_image(image(1600, 1200))
    for invalid in (0, -1, math.nan, math.inf):
        canvas.set_zoom_percent(invalid)
        canvas.zoom(invalid)
        assert canvas.zoom_percent == 100
    canvas.set_zoom_percent(.001)
    assert canvas.zoom_percent == .5
    canvas.set_zoom_percent(100000)
    assert canvas.zoom_percent == 6400


def test_footer_geometry_and_controls_stay_stable(qapp):
    window = ViewerWindow()
    window.show()
    window.stack.setCurrentWidget(window.canvas)
    qapp.processEvents()
    try:
        controls = [window.metadata_group, window.fit_button,
                    window.zoom_combo, window.zoom_slider]
        initial = [control.geometry() for control in controls]
        for dimensions, text in [((32, 24), 'PNG | 32 × 24 | 99 B'),
                                 ((1920, 1080), 'Loading ' + 'very long filename ' * 30),
                                 ((70, 1600), 'EXR | 70 × 1600 | 152.1 MB')] * 3:
            window._status(text)
            window.cancel_button.show()
            window.canvas.clear_image()
            qapp.processEvents()
            assert [control.geometry() for control in controls] == initial
            window.canvas.set_image(image(*dimensions))
            window.cancel_button.hide()
            qapp.processEvents()
            assert window.zoom_combo.text() == '100%'
            assert window.zoom_slider.value() == 0
            assert window.status_label.toolTip() == text
            assert 0 < window.info_button.x() - window.status_label.geometry().right() <= 8
            assert [control.geometry() for control in controls] == initial
        window.resize(800, 560)
        qapp.processEvents()
        assert window.width() == 800
        assert window.zoom_slider.geometry().right() < window.viewbar.width()
    finally:
        window.close()


def test_toolbar_slider_and_explicit_pixel_action(qapp):
    window = ViewerWindow()
    window.show()
    window.stack.setCurrentWidget(window.canvas)
    qapp.processEvents()
    try:
        window.canvas.set_image(image(1600, 1200))
        baseline = window.canvas.transform().m11()
        seen = []
        window.canvas.zoomChanged.connect(seen.append)
        window.zoom_slider.setValue(20)
        assert window.canvas.zoom_percent == 200
        assert window.canvas.transform().m11() == pytest.approx(baseline * 2)
        assert seen == [200]
        window._zoom_preset(125)
        assert window.canvas.zoom_percent == 125  # Slider quantization cannot feed back.
        assert window.zoom_combo.text() == '125%'
        pixel_action = next(action for action in window.zoom_combo.menu().actions()
                            if action.text().startswith('Actual pixels (1:1)'))
        pixel_action.trigger()
        assert window.canvas.transform().m11() * window.canvas.viewport().devicePixelRatioF() == pytest.approx(1)
        window._zoom_preset(100)
        assert window.canvas.fitted
        assert window.zoom_combo.text() == '100%'
    finally:
        window.close()


def test_rapid_file_navigation_commits_only_fit_relative_100(qapp, tmp_path):
    import time
    from PIL import Image

    paths = []
    for index, dimensions in enumerate([(32, 24), (480, 800), (1920, 1080), (800, 64)]):
        path = tmp_path / f'{index}.png'
        Image.new('RGB', dimensions, 'blue').save(path)
        paths.append(path)

    def wait_for(predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(.005)
        qapp.processEvents()
        assert predicate()

    window = ViewerWindow()
    window.show()
    seen = []
    window.canvas.zoomChanged.connect(seen.append)
    try:
        window.open_path(paths[0])
        wait_for(lambda: window.displayed_path == paths[0] and len(window.files) == len(paths))
        initial = [control.geometry() for control in (window.metadata_group, window.fit_button, window.zoom_combo)]
        for sequence in [(1, 2, 3), (2, 1, 0), (3, 0, 1)]:
            for index in sequence:
                window.select_index(index)
                assert window.fit_button.isEnabled()
                assert window.zoom_combo.isEnabled()
                assert window.zoom_slider.isEnabled()
                assert not window.delete_button.isEnabled()
            wait_for(lambda: window.displayed_path == paths[sequence[-1]])
            assert window.canvas.zoom_percent == 100
            assert window.zoom_combo.text() == '100%'
            assert window.zoom_slider.value() == 0
            assert [control.geometry() for control in (window.metadata_group, window.fit_button, window.zoom_combo)] == initial
        assert seen == [100.0]
    finally:
        window.close()
