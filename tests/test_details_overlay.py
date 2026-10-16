"""Inset Details reserves width, refits fitted images and preserves manual views."""
from pathlib import Path
import time

import numpy as np
import pytest
from PIL import Image
from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtGui import QImage, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from luma.app import ViewerWindow
from luma.i18n import set_language, tr


def wait_for(qapp, predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(.005)
    qapp.processEvents()
    assert predicate()


@pytest.fixture
def window(qapp):
    window = ViewerWindow()
    window.show()
    window.stack.setCurrentWidget(window.canvas)
    image = QImage(1600, 900, QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.blue)
    window.canvas.set_image(image)
    window.histogram.set_preview('initial-render')
    qapp.processEvents()
    yield window
    window.close()
    qapp.processEvents()


def snapshot(window):
    canvas = window.canvas
    return (canvas.geometry(), canvas.viewport().size(), canvas.transform(),
            canvas.mapToScene(canvas.viewport().rect().center()), canvas.zoom_percent,
            canvas._fit_scale, canvas.fitted, canvas.item.rotation())


@pytest.mark.parametrize('mode', ['fit', 'zoom', 'pixels', 'rotated'])
def test_repeated_toggles_refit_or_preserve_manual_view_without_drift(window, qapp, mode):
    canvas = window.canvas
    if mode == 'zoom':
        canvas.set_zoom_percent(250)
        canvas.centerOn(600, 400)
    elif mode == 'pixels':
        canvas.actual_size()
        canvas.centerOn(700, 300)
    elif mode == 'rotated':
        canvas.rotate_clockwise()
        canvas.set_zoom_percent(200)
    before = snapshot(window)
    for _ in range(5):
        window.info_button.click()
        qapp.processEvents()
        assert window.sidebar.isVisible()
        assert window.sidebar.parentWidget() is window.centralWidget()
        assert window.canvas.width() == before[0].width() - window.sidebar.width()
        assert window.sidebar.x() == window.stack.geometry().right() + 1
        assert window.sidebar.height() == window.stack.height()
        if mode == 'fit':
            assert canvas.fitted and canvas.zoom_percent == 100
            assert canvas.transform().m11() < before[2].m11()
            assert canvas.transform().m11() == pytest.approx(canvas._fitted_scale())
        else:
            assert canvas.transform() == before[2]
            center = canvas.mapToScene(canvas.viewport().rect().center())
            assert abs(center.x() - before[3].x()) < 1
            assert abs(center.y() - before[3].y()) < 1
            assert canvas.zoom_percent == pytest.approx(canvas.transform().m11() / canvas._fit_scale * 100)
            assert canvas.item.rotation() == before[7]
        window.info_button.click()
        qapp.processEvents()
        restored = snapshot(window)
        assert restored[0:3] == before[0:3]
        assert restored[4:] == before[4:]
        assert abs(restored[3].x() - before[3].x()) < 1
        assert abs(restored[3].y() - before[3].y()) < 1
        assert not window.sidebar.isVisible()


def test_card_is_inset_and_does_not_overlap_image_input(window, qapp):
    window.info_button.click()
    qapp.processEvents()
    sidebar = window.sidebar
    assert sidebar.glass is None
    assert sidebar.card.geometry().left() == sidebar.CARD_MARGIN
    assert sidebar.card.geometry().top() == sidebar.CARD_MARGIN
    assert sidebar.card.geometry().right() == sidebar.width() - sidebar.CARD_MARGIN - 1
    assert sidebar.card.geometry().bottom() == sidebar.height() - sidebar.CARD_MARGIN - 1
    for x in (20, window.stack.width() - 6):
        point = window.stack.mapTo(window, QPoint(x, 100))
        assert window.childAt(point) is window.canvas.viewport()
    point = sidebar.mapTo(window, QPoint(20, 100))
    hit = window.childAt(point)
    assert hit is sidebar or sidebar.isAncestorOf(hit)


def test_small_window_scrolls_within_header_footer(window, qapp):
    window.resize(800, 560)
    window.photo_metadata.set_metadata({
        'camera_model': 'Camera ' + 'with a long model name ' * 10,
        'lens_model': 'Long lens name ' * 12,
        'gps_latitude': '37.000000', 'gps_longitude': '-122.000000'})
    window.details.setPlainText('Original metadata:\n\n' + '\n'.join(f'Key {index}\nValue {index}' for index in range(100)))
    window.info_button.click()
    qapp.processEvents()
    panel = window.sidebar
    assert panel.mapTo(window, QPoint(0, 0)).y() >= window.titlebar.geometry().bottom()
    assert panel.mapTo(window, QPoint(0, panel.height())).y() <= window.viewbar.y()
    assert panel.verticalScrollBar().maximum() > 0
    assert panel.horizontalScrollBar().maximum() == 0
    before = snapshot(window)
    panel.verticalScrollBar().setValue(panel.verticalScrollBar().maximum())
    qapp.processEvents()
    assert snapshot(window) == before
    assert window.histogram.sample_runs == 1


def test_resize_and_filmstrip_keep_card_beside_canvas(window, qapp):
    window.info_button.click()
    for size, strip in [((800, 560), True), ((1400, 900), False), ((1000, 650), True)]:
        window.resize(*size)
        window.strip_button.setChecked(strip)
        window._toggle_strip(strip)
        qapp.processEvents()
        assert window.canvas.width() == window.stack.width()
        assert window.sidebar.height() == window.stack.height()
        assert window.sidebar.x() + window.sidebar.width() == window.centralWidget().width()
        assert window.sidebar.x() == window.stack.geometry().right() + 1
        assert window.sidebar.card.height() == window.stack.height() - 2 * window.sidebar.CARD_MARGIN
        assert window.sidebar.glass is None


def test_card_close_button_and_keyboard_shortcut_share_visibility_state(window, qapp):
    window.info_button.click()
    qapp.processEvents()
    assert window.sidebar.close_button.accessibleName()
    window.sidebar.close_button.click()
    qapp.processEvents()
    assert not window.sidebar.isVisible() and not window.info_button.isChecked()
    assert window.canvas.hasFocus()
    QTest.keyClick(window, Qt.Key.Key_I)
    qapp.processEvents()
    assert window.sidebar.isVisible() and window.info_button.isChecked()


def test_hidden_navigation_does_not_sample_decode_or_retain_rgba(qapp, tmp_path, monkeypatch):
    paths = []
    for index, color in enumerate(('red', 'blue')):
        path = tmp_path / f'{index}.png'
        Image.new('RGB', (800, 600), color).save(path)
        paths.append(path)
    window = ViewerWindow()
    window.show()
    try:
        for path in paths:
            window.open_path(path)
            wait_for(qapp, lambda: window.displayed_path == path)
        assert window.histogram.sample_runs == 0
        assert not window.histogram._cache
        # Opening Details must reuse already-rendered pixels, not call services.
        monkeypatch.setattr(window.service, '_load', lambda *a, **kw: pytest.fail('Unexpected source load'))
        window.prefetch_timer.stop()
        window.info_button.click()
        wait_for(qapp, lambda: window.histogram.plot.data is not None)
        result = window.histogram.plot.data
        assert result.counts[2, 255] == result.visible_pixels
        assert result.counts[0, 0] == result.visible_pixels
        window.info_button.click()
        window.info_button.click()
        qapp.processEvents()
        assert window.histogram.sample_runs == 1
    finally:
        window.close()


def test_navigation_failure_and_cancel_clear_previous_photo_and_histogram(window, qapp, tmp_path):
    window.info_button.click()
    qapp.processEvents()
    window.photo_metadata.set_metadata({'camera_model': 'Old camera'})
    window._open_image(tmp_path / 'missing.png')
    assert not window.photo_metadata.values
    assert window.histogram.plot.data is None
    assert window.histogram.plot.message == 'Waiting for preview…'
    window._load_failed(window._load_token, 'example decode failure')
    assert window.histogram.plot.message == 'Histogram unavailable'
    assert not window.photo_metadata.values
    window.cancel_load()
    assert window.histogram.plot.data is None


@pytest.mark.parametrize('language', ['en', 'zh-CN', 'zh-TW'])
def test_photo_metadata_plain_text_optional_and_localized(qapp, language):
    set_language(language)
    window = ViewerWindow()
    try:
        window.photo_metadata.set_metadata({'camera_model': '<b>Literal camera</b>',
                                           'gps_latitude': '37.100000', 'gps_longitude': '-122.200000'})
        labels = window.photo_metadata.findChildren(QLabel)
        assert any(label.text() == tr('GPS coordinates') for label in labels)
        raw = next(label for label in labels if label.text() == '<b>Literal camera</b>')
        assert raw.textFormat() == Qt.TextFormat.PlainText
        assert not any(label.text() == tr('Lens') for label in labels)
        window.photo_metadata.set_metadata({})
        assert window.photo_metadata.isHidden()
    finally:
        window.close()


def test_histogram_tracks_actual_tone_exposure_and_channels_without_redecode(qapp, tmp_path, monkeypatch):
    from luma.decoders import ImageData, render_rgba
    from luma.histogram import preview_histogram
    import luma.workers as workers

    path = tmp_path / 'rendered.exr'
    path.write_bytes(b'synthetic source identity')
    pixels = np.empty((8, 8, 4), dtype=np.float32)
    pixels[...] = (.5, 2., 8., 1.)
    pixels[0, 0] = (10., 20., 30., 0.)
    data = ImageData(path, pixels, True)
    decoded = []
    monkeypatch.setattr(workers, 'decode_image', lambda *a, **kw: decoded.append(path) or data)
    window = ViewerWindow()
    window.show()
    try:
        window.open_path(path)
        wait_for(qapp, lambda: window.displayed_path == path)
        assert window.histogram.sample_runs == 0
        window.info_button.click()
        wait_for(qapp, lambda: window.histogram.plot.data is not None)
        for exposure, channel, tone in [(0., 'RGBA', 'Reinhard'), (2., 'RGBA', 'Clip'),
                                        (-1., 'R', 'Reinhard'), (0., 'A', 'Reinhard')]:
            window.exposure.setValue(round(exposure * 10))
            window.channel.setCurrentText(channel)
            window.tone.setCurrentIndex(window.tone.findData(tone))
            window.render_timer.stop()
            window._request_render()
            key = (window.service._key(path), exposure, channel, tone.casefold())
            wait_for(qapp, lambda: window.histogram._key == key and window.histogram.plot.data is not None)
            rendered = render_rgba(data, exposure, channel, tone)
            image = QImage(rendered.data, 8, 8, rendered.strides[0], QImage.Format.Format_RGBA8888)
            expected = preview_histogram(image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied))
            assert np.array_equal(window.histogram.plot.data.counts, expected.counts)
            assert window.histogram.plot.data.visible_pixels == expected.visible_pixels
        assert decoded == [path]
    finally:
        window.close()


@pytest.mark.parametrize('language', ['en', 'zh-CN', 'zh-TW'])
def test_details_omits_background_and_shortcuts_but_preserves_state(qapp, language):
    set_language(language)
    window = ViewerWindow()
    window.show()
    try:
        window.info_button.click()
        qapp.processEvents()
        assert not window.background.isVisible()
        assert not window.sidebar.isAncestorOf(window.background)
        assert tr('BACKGROUND') not in [label.text() for label in window.sidebar.findChildren(QLabel)]
        assert tr('Keyboard shortcuts') not in [button.text() for button in window.sidebar.findChildren(QPushButton)]
        window.background.setCurrentIndex(2)
        assert window.canvas.background == 'Black'
        assert window.histogram.parent() is not None
        assert window.photo_metadata.parent() is not None
        assert window.shortcuts
    finally:
        window.close()
        set_language('en')
