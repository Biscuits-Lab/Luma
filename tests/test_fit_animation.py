"""Explicit Fit interpolates geometry only and yields immediately to newer input."""
import ctypes
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt, QEvent, QPointF
from PySide6.QtGui import QImage, QMouseEvent, QWheelEvent
from PySide6.QtTest import QTest

from luma import canvas as module
from luma.canvas import ImageCanvas
from luma.app import ViewerWindow


@pytest.fixture
def canvas(qapp, monkeypatch):
    monkeypatch.setattr(module, 'motion_allowed', lambda: True)
    value = ImageCanvas(); value.resize(800, 600); value.show(); qapp.processEvents()
    image = QImage(2400, 1600, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.blue); value.set_image(image)
    yield value
    value.close(); qapp.processEvents()


def test_explicit_fit_animates_center_and_scale_then_lands_exactly(canvas, qapp):
    exact_transform = canvas.transform()
    exact_center = canvas.mapToScene(canvas.viewport().rect().center())
    canvas.set_zoom_percent(320)
    canvas.horizontalScrollBar().setValue(canvas.horizontalScrollBar().value() + 190)
    canvas.verticalScrollBar().setValue(canvas.verticalScrollBar().value() - 80)
    start_transform = canvas.transform()
    key = canvas.item.pixmap().cacheKey()
    signals = []; canvas.fitAnimationChanged.connect(signals.append)
    canvas.animate_fit()
    assert canvas.fit_animation_running
    assert canvas.transform() == start_transform
    canvas._fit_animation.setCurrentTime(110)
    assert exact_transform.m11() < canvas.transform().m11() < start_transform.m11()
    assert 100 < canvas.zoom_percent < 320
    assert canvas.item.pixmap().cacheKey() == key
    canvas._fit_animation.setCurrentTime(220)
    assert not canvas.fit_animation_running and canvas.fitted
    assert canvas.zoom_percent == 100
    assert canvas.transform() == exact_transform
    assert canvas.mapToScene(canvas.viewport().rect().center()) == exact_center
    assert signals == [True, False]


def test_repeat_fit_retargets_current_geometry_without_jump(canvas):
    canvas.set_zoom_percent(400); canvas.animate_fit()
    canvas._fit_animation.setCurrentTime(80)
    before = canvas.transform()
    canvas.animate_fit()
    assert canvas.fit_animation_running and canvas.transform() == before
    canvas._fit_animation.setCurrentTime(220)
    assert canvas.zoom_percent == 100 and canvas.fitted


@pytest.mark.parametrize('interrupt', ['zoom', 'wheel', 'pan', 'new_image', 'clear', 'resize', 'actual', 'hide'])
def test_newer_actions_cancel_animation_and_old_completion_cannot_override(canvas, qapp, interrupt):
    canvas.set_zoom_percent(350); canvas.animate_fit(); canvas._fit_animation.setCurrentTime(70)
    if interrupt == 'zoom':
        canvas.set_zoom_percent(250)
    elif interrupt == 'wheel':
        from PySide6.QtCore import QPoint
        position = QPointF(canvas.viewport().rect().center())
        event = QWheelEvent(position, position, QPoint(), QPoint(0,120),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.NoScrollPhase, False)
        qapp.sendEvent(canvas.viewport(), event)
    elif interrupt == 'pan':
        QTest.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton)
        QTest.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton)
    elif interrupt == 'new_image':
        image = QImage(600, 1600, QImage.Format.Format_ARGB32_Premultiplied); image.fill(Qt.GlobalColor.red)
        canvas.set_image(image)
        assert canvas.zoom_percent == 100
    elif interrupt == 'clear':
        canvas.clear_image()
    elif interrupt == 'resize':
        canvas.resize(950, 700); qapp.processEvents()
    elif interrupt == 'actual':
        canvas.actual_size()
    elif interrupt == 'hide':
        canvas.hide()
    assert not canvas.fit_animation_running
    transform, percent = canvas.transform(), canvas.zoom_percent
    canvas._finish_animated_fit(); canvas._animate_fit_step(1.)
    assert canvas.transform() == transform and canvas.zoom_percent == percent


def test_initial_load_regular_fit_and_reduced_motion_are_immediate(canvas, monkeypatch):
    seen = []; canvas.fitAnimationChanged.connect(seen.append)
    canvas.set_zoom_percent(250)
    image = QImage(1400, 2200, QImage.Format.Format_ARGB32_Premultiplied); image.fill(Qt.GlobalColor.red)
    canvas.set_image(image)
    assert canvas.fitted and not canvas.fit_animation_running
    canvas.set_zoom_percent(200); canvas.fit()
    assert canvas.zoom_percent == 100 and not seen
    monkeypatch.setattr(module, 'motion_allowed', lambda: False)
    canvas.set_zoom_percent(300); canvas.animate_fit()
    assert canvas.zoom_percent == 100 and not seen


def test_preview_refinement_with_same_source_geometry_does_not_abort_fit(canvas):
    canvas.set_zoom_percent(300); canvas.animate_fit(); canvas._fit_animation.setCurrentTime(60)
    sample = QImage(600, 400, QImage.Format.Format_ARGB32_Premultiplied); sample.fill(Qt.GlobalColor.blue)
    before = canvas.transform()
    canvas.set_image(sample, reset=False, source_size=(2400,1600))
    assert canvas.fit_animation_running and canvas.transform() == before
    canvas._fit_animation.setCurrentTime(220)
    assert canvas.zoom_percent == 100


def test_windows_motion_setting_and_failure_are_honored(qapp, monkeypatch):
    monkeypatch.setattr(module.sys, 'platform', 'win32')
    monkeypatch.setattr(module.QApplication, 'isEffectEnabled', lambda effect: True)
    calls=[]
    class Query:
        enabled=0
        success=1
        def __call__(self, flag, unused, result, flags):
            calls.append(flag)
            ctypes.cast(result, ctypes.POINTER(ctypes.c_int32)).contents.value=self.enabled
            return self.success
    query=Query()
    monkeypatch.setattr(module.ctypes, 'WinDLL', lambda *a, **kw: SimpleNamespace(SystemParametersInfoW=query), raising=False)
    assert not module.motion_allowed()
    query.enabled=1
    assert module.motion_allowed()
    query.success=0
    assert not module.motion_allowed()
    assert calls == [0x1042] * 3


def test_button_animation_is_pixel_free_and_suspends_extras(qapp, monkeypatch):
    monkeypatch.setattr(module, 'motion_allowed', lambda: True)
    window=ViewerWindow(); window.show(); window.stack.setCurrentWidget(window.canvas)
    image=QImage(2400,1600,QImage.Format.Format_ARGB32_Premultiplied); image.fill(Qt.GlobalColor.blue)
    window.canvas.set_image(image); window._set_controls(True)
    window.sidebar.show(); qapp.processEvents(); QTest.qWait(80)
    window.canvas.set_zoom_percent(300)
    assert window.sidebar.glass is None
    calls=[]
    monkeypatch.setattr(window.loader,'submit',lambda *a: calls.append('decode'))
    monkeypatch.setattr(window.canvas,'set_image',lambda *a,**k: calls.append('image'))
    monkeypatch.setattr(window.canvas,'displayed_image',lambda *a: calls.append('copy'))
    try:
        window.fit_button.click()
        assert window.canvas.fit_animation_running
        assert not window.histogram._active
        for time in (30,60,100,150,200):
            window.canvas._fit_animation.setCurrentTime(time)
            qapp.processEvents()
        assert window.sidebar.glass is None and not calls
        window.canvas._fit_animation.setCurrentTime(220)
        assert window.histogram._active
        assert window.canvas.zoom_percent == 100 and not calls
    finally:
        window.close()
