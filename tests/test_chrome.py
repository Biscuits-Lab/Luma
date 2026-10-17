from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel
from luma.chrome import resize_edges
from luma.app import ViewerWindow
from luma.i18n import tr
from PySide6.QtGui import QImage


def test_resize_edges_use_logical_coordinates():
    assert resize_edges(QPoint(0, 0), 1280, 820) == Qt.Edge.TopEdge | Qt.Edge.LeftEdge
    assert resize_edges(QPoint(1279, 819), 1280, 820) == Qt.Edge.BottomEdge | Qt.Edge.RightEdge
    assert resize_edges(QPoint(400, 400), 1280, 820) == Qt.Edge(0)
    assert resize_edges(QPoint(5, 400), 1280, 820) == Qt.Edge.LeftEdge
    assert resize_edges(QPoint(6, 400), 1280, 820) == Qt.Edge(0)


def test_custom_titlebar_rotation_and_window_states(qapp):
    win = ViewerWindow()
    win.show()
    qapp.processEvents()
    assert win.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert win.titlebar.height() == 34
    assert win.viewbar.height() == 36
    assert win.stack.currentWidget() is win.welcome
    assert not win.viewbar.isVisible()
    assert not any(control.isVisible() for control in
                   (win.rotate_button, win.edit_button, win.delete_button, win.more_button))
    welcome_texts = [label.text() for label in win.welcome.findChildren(QLabel)]
    assert tr('Room for the image') not in welcome_texts
    assert not any('JPG' in text for text in welcome_texts)
    assert win.maximize_button.iconSize().width() == 17
    assert all(control.iconSize().width() == 20 for control in (win.rotate_button, win.delete_button))
    assert win.more_button.iconSize().width() == 17
    assert not win.filmstrip.isVisible()
    assert not win.sidebar.isVisible()
    assert not hasattr(win, 'brand_button')
    QTest.mouseDClick(win.titlebar, Qt.MouseButton.LeftButton, pos=QPoint(300, 20))
    assert win.isMaximized()
    win.toggle_maximized()
    assert not win.isMaximized()
    image = QImage(12, 8, QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.red)
    win.canvas.set_image(image)
    win._set_controls(True)
    qapp.processEvents()
    assert win.viewbar.isVisible()
    assert win.info_button.geometry().left() > win.status_label.geometry().right()
    assert win.info_button.geometry().left() - win.status_label.geometry().right() < 12
    assert win.info_button.geometry().right() < win.width() // 2
    fit_scale = win.canvas.transform().m11()
    win.zoom_combo.menu().actions()[3].trigger()
    assert abs(win.canvas.transform().m11() / fit_scale - 2) < .001
    assert win.canvas.zoom_percent == 200
    win.canvas.rotate_clockwise()
    assert win.canvas.displayed_image().size().width() == 8
    assert win.canvas.displayed_image().size().height() == 12
    assert win.canvas.sceneRect().width() == 8
    for _ in range(3):
        win.canvas.rotate_clockwise()
    assert win.canvas.displayed_image().size() == image.size()
    win.close()
