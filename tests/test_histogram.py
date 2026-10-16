"""Known displayed RGB/alpha distributions and bounded demand-only work."""
import time

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPixmap

from luma.histogram import (CACHE_ENTRIES, MAX_SAMPLES, SAMPLE_EDGE,
                            PreviewHistogram, bounded_preview, preview_histogram)


def rgba_image(values):
    data = np.asarray(values, dtype=np.uint8).reshape(1, -1, 4)
    return QImage(data.data, data.shape[1], 1, data.strides[0], QImage.Format.Format_RGBA8888).copy()


def test_known_rgb_and_alpha_counts():
    image = rgba_image([(255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255),
                        (40, 60, 80, 0), (20, 40, 60, 128)])
    result = preview_histogram(image)
    assert result.sampled_pixels == 5
    assert result.visible_pixels == 4
    assert result.counts.shape == (3, 256)
    assert result.counts.dtype == np.uint32
    assert list(result.counts.sum(axis=1)) == [4, 4, 4]
    for channel, partial in enumerate((20, 40, 60)):
        assert result.counts[channel, 0] == 2
        assert result.counts[channel, 255] == 1
        assert result.counts[channel, partial] == 1
    assert not result.counts.flags.writeable


def test_fully_transparent_and_null_images_have_honest_empty_data():
    result = preview_histogram(rgba_image([(255, 100, 70, 0)] * 20))
    assert result.sampled_pixels == 20
    assert result.visible_pixels == 0
    assert not result.counts.any()
    empty = preview_histogram(QImage())
    assert empty.sampled_pixels == empty.visible_pixels == 0


def test_premultiplied_preview_is_unpremultiplied_before_counting():
    source = rgba_image([(240, 120, 60, 85), (33, 66, 99, 0)])
    image = source.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    result = preview_histogram(image)
    assert result.visible_pixels == 1
    for channel, expected in enumerate((240, 120, 60)):
        assert result.counts[channel, expected] == 1


def test_opaque_rgb_stride_and_padding_are_supported():
    image = QImage(3, 2, QImage.Format.Format_RGB888)
    image.fill(QColor(10, 20, 30))
    result = preview_histogram(image)
    assert result.visible_pixels == 6
    assert [result.counts[channel, value] for channel, value in enumerate((10, 20, 30))] == [6, 6, 6]


@pytest.mark.parametrize('size', [(3840, 2160), (8192, 1024), (100000, 1), (1, 100000), (8, 3)])
def test_sampling_is_bounded_and_does_not_smooth_colors(size, qapp):
    image = QImage(*size, QImage.Format.Format_RGBA8888)
    image.fill(QColor(19, 73, 211, 255))
    pixmap = QPixmap.fromImage(image)
    sample = bounded_preview(pixmap)
    assert sample.width() <= SAMPLE_EDGE
    assert sample.height() <= SAMPLE_EDGE
    assert 0 < sample.width() * sample.height() <= MAX_SAMPLES
    result = preview_histogram(sample)
    for channel, value in enumerate((19, 73, 211)):
        assert np.count_nonzero(result.counts[channel]) == 1
        assert result.counts[channel, value] == result.visible_pixels
    assert sample.format() == QImage.Format.Format_RGBA8888


def test_hidden_panel_never_calls_source_and_coalesces_to_newest(qapp):
    calls = []
    panel = PreviewHistogram(lambda: calls.append(True) or rgba_image([(255, 0, 0, 255)]))
    try:
        for index in range(100):
            panel.set_preview(('hidden', index))
            qapp.processEvents()
        assert calls == []
        assert panel.sample_runs == 0
        panel.set_active(True)
        panel.set_preview('outdated')
        panel.set_preview('newest')
        qapp.processEvents()
        assert len(calls) == 1
        assert panel.sample_runs == 1
        assert list(panel._cache) == ['newest']
        assert panel.plot.data.counts[0, 255] == 1
        panel.set_preview('never-run')
        panel.set_active(False)
        qapp.processEvents()
        assert len(calls) == 1
    finally:
        panel.close()


def test_cache_is_bounded_by_render_state_and_contains_no_image(qapp):
    calls = []
    panel = PreviewHistogram(lambda: calls.append(True) or rgba_image([(0, 0, 255, 255)]))
    try:
        panel.set_active(True)
        for index in range(CACHE_ENTRIES + 5):
            panel.set_preview(('file-signature', index, 'RGBA', 'Reinhard'))
            qapp.processEvents()
        assert len(panel._cache) == CACHE_ENTRIES
        assert sum(value.counts.nbytes for value in panel._cache.values()) == CACHE_ENTRIES * 3 * 256 * 4
        before = len(calls)
        panel.set_preview(('file-signature', CACHE_ENTRIES + 4, 'RGBA', 'Reinhard'))
        qapp.processEvents()
        assert len(calls) == before
        assert panel.plot.data is not None
    finally:
        panel.close()


def test_reentrant_source_change_cannot_commit_stale_bins(qapp):
    panel = None
    def source():
        panel.clear('loading')
        return rgba_image([(255, 0, 0, 255)])
    panel = PreviewHistogram(source)
    try:
        panel.set_active(True)
        panel.set_preview('old')
        qapp.processEvents()
        assert panel.plot.data is None
        assert not panel._cache
        assert panel.plot.message == 'Waiting for preview…'
    finally:
        panel.close()


def test_loading_error_and_transparent_states_never_reuse_old_bins(qapp):
    images = [rgba_image([(255, 0, 0, 255)]), rgba_image([(255, 0, 0, 0)]), QImage()]
    panel = PreviewHistogram(lambda: images.pop(0))
    try:
        panel.set_active(True)
        panel.set_preview('opaque')
        qapp.processEvents()
        assert panel.plot.data.visible_pixels == 1
        panel.clear('loading')
        assert panel.plot.data is None
        assert panel.plot.message == 'Waiting for preview…'
        panel.set_preview('transparent')
        qapp.processEvents()
        assert panel.plot.data.visible_pixels == 0
        assert panel.plot.message == 'No visible pixels in sample'
        panel.set_preview('null')
        qapp.processEvents()
        assert panel.plot.data is None
        assert panel.plot.message == 'Histogram unavailable'
        assert 'null' not in panel._cache
    finally:
        panel.close()
