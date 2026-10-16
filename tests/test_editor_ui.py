from dataclasses import replace
from pathlib import Path
import threading
import time

from PIL import Image
from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton
import pytest

from luma.editor import ImageEditorDialog
from luma.editor_core import EditState, Adjustments, Stroke, FULL_CROP
from luma.editor_worker import EditorRenderer


def wait_for(qapp, predicate, timeout=3):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        qapp.processEvents()
        if predicate(): return
        QTest.qWait(5)
    assert predicate(), 'Timed out waiting for editor state'


@pytest.fixture
def editor(qapp):
    image=QImage(800,500,QImage.Format.Format_RGBA8888)
    image.fill(0xff8a6a45)
    dialog=ImageEditorDialog(image,Path('/tmp/luma-editor-original.png'))
    dialog.show()
    wait_for(qapp,lambda:dialog._ready)
    yield dialog
    dialog._saved_state=dialog.state
    dialog.reject()
    dialog._renderer._thread.join(3)
    assert not dialog._renderer._thread.is_alive()


def test_exactly_four_tabs_and_native_reference_controls(editor):
    assert [button.text() for button in editor.tab_buttons]==['Crop','Adjust','Filters','Markup']
    assert not any(b.text() in ('Erase','Background') for b in editor.findChildren(QPushButton))
    assert len(editor.adjust_sliders)==10
    assert editor.canvas.mode=='Crop'
    assert not editor.panels.isVisible()
    assert editor.crop_tools.isVisible()


@pytest.mark.parametrize('name,index', [('Adjust',1),('Filters',2),('Markup',3),('Crop',0)])
def test_switch_tab_keeps_recipe_and_produces_latest_image(editor,qapp,name,index):
    editor._commit(replace(editor.state,adjustments=Adjustments(brightness=20)))
    QTest.mouseClick(editor.tab_buttons[index],Qt.MouseButton.LeftButton)
    wait_for(qapp,lambda:editor._ready)
    assert editor.canvas.mode==name
    assert editor.tab_buttons[index].isChecked()
    assert editor.state.adjustments.brightness==20
    assert editor.panels.isVisible()==(name in ('Adjust','Filters'))
    if name=='Filters': assert all(not b.icon().isNull() for b in editor.filter_buttons.values())


def test_adjust_drag_commits_one_history_item_then_undo_redo(editor,qapp):
    slider=editor.adjust_sliders['brightness'][0]
    slider.setSliderDown(True)
    for value in range(0,51,5): slider.setValue(value)
    assert not editor.history.undo_commands
    slider.setSliderDown(False)
    wait_for(qapp,lambda:editor._ready)
    assert len(editor.history.undo_commands)==1
    assert editor.state.adjustments.brightness==50
    editor._undo(); assert editor.state.adjustments.brightness==0
    editor._redo(); assert editor.state.adjustments.brightness==50


def test_crop_handle_drag_and_aspect_constraint(editor,qapp):
    rect=editor.canvas._crop_rect()
    QTest.mousePress(editor.canvas,Qt.MouseButton.LeftButton,pos=rect.bottomRight().toPoint())
    target=QPoint(round(rect.right()-rect.width()*.2),round(rect.bottom()-rect.height()*.2))
    QTest.mouseMove(editor.canvas,target)
    QTest.mouseRelease(editor.canvas,Qt.MouseButton.LeftButton,pos=target)
    assert editor.state.crop[2]==pytest.approx(.8,abs=.01)
    assert editor.state.crop[3]==pytest.approx(.8,abs=.01)
    editor.aspect.setCurrentIndex(editor.aspect.findText('1:1'))
    l,t,r,b=editor.state.crop
    assert (r-l)*800/((b-t)*500)==pytest.approx(1)
    editor._undo()
    assert editor.state.crop[2]==pytest.approx(.8,abs=.01)


def test_markup_add_erase_and_undo(editor,qapp):
    editor._tab_changed('Markup')
    wait_for(qapp,lambda:editor._ready)
    rect=editor.canvas.image_rect()
    start=QPoint(round(rect.left()+rect.width()*.2),round(rect.center().y()))
    end=QPoint(round(rect.left()+rect.width()*.8),round(rect.center().y()))
    editor.canvas.shape='arrow'
    QTest.mousePress(editor.canvas,Qt.MouseButton.LeftButton,pos=start)
    QTest.mouseMove(editor.canvas,end)
    QTest.mouseRelease(editor.canvas,Qt.MouseButton.LeftButton,pos=end)
    assert len(editor.state.strokes)==1
    assert editor.state.strokes[0].shape=='arrow'
    editor._erase_at(QPointF((start+end)/2))
    assert not editor.state.strokes
    editor._undo()
    assert len(editor.state.strokes)==1


def test_annotations_follow_rotation_and_crop(editor,qapp):
    stroke=Stroke(((.2,.3),(.8,.6)))
    editor._commit(replace(editor.state,strokes=(stroke,)))
    editor._geometry(turns=1)
    assert editor.state.strokes[0] is stroke
    assert editor.canvas.display_size()==(500,800)
    editor._reset()
    assert editor.state==EditState()
    editor._undo()
    assert editor.state.strokes==(stroke,)


def test_cancel_and_keep_edit_confirmation(editor,monkeypatch):
    from luma.editor import QMessageBox
    editor._commit(replace(editor.state,filter_name='golden'))
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:QMessageBox.StandardButton.Cancel)
    editor.reject()
    assert not editor._closed and editor.isVisible()
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:QMessageBox.StandardButton.Discard)
    editor.reject()
    assert editor._closed


def test_hdr_warning_and_reject_null_image(qapp):
    with pytest.raises(ValueError): ImageEditorDialog(QImage(),Path('/tmp/original.png'))
    image=QImage(10,10,QImage.Format.Format_RGBA8888);image.fill(0)
    editor=ImageEditorDialog(image,Path('/tmp/original.exr'))
    assert editor.display_referred
    assert 'tone-mapped' in editor.hdr_note.text()
    editor._shutdown();editor._renderer._thread.join(3)


def test_latest_wins_bounds_pending_work_and_reuses_preview(qapp,monkeypatch):
    import luma.editor_worker as worker
    original=worker.render
    started=threading.Event(); release=threading.Event()
    calls=[]; source_ids=[]
    def slow(source,state,**kwargs):
        calls.append(state.adjustments.brightness);source_ids.append(id(source))
        if len(calls)==1:
            started.set();release.wait(2)
        return original(source,state,**kwargs)
    monkeypatch.setattr(worker,'render',slow)
    renderer=EditorRenderer(Image.new('RGBA',(2400,1600),(100,100,100,255)))
    results=[]
    renderer.finished.connect(lambda token,result,error:results.append((token,result,error)))
    renderer.request(EditState())
    assert started.wait(2)
    for value in range(1,31): renderer.request(EditState(adjustments=Adjustments(brightness=value)))
    latest=renderer.token
    assert renderer._pending.token==latest
    release.set()
    wait_for(qapp,lambda:bool(results))
    assert [token for token,_,_ in results]==[latest]
    assert calls==[0,30]
    assert len(set(source_ids))==1
    assert max(results[0][1].size)<=1600
    renderer.close();renderer._thread.join(3)


def test_worker_close_suppresses_late_result(qapp,monkeypatch):
    import luma.editor_worker as worker
    started=threading.Event();release=threading.Event()
    def slow(*a,**k):
        started.set();release.wait(2)
        return Image.new('RGBA',(20,20))
    monkeypatch.setattr(worker,'render',slow)
    renderer=EditorRenderer(Image.new('RGBA',(20,20)))
    results=[];renderer.finished.connect(lambda *args:results.append(args))
    renderer.request(EditState());assert started.wait(2)
    renderer.close();release.set();renderer._thread.join(3)
    qapp.processEvents()
    assert not results and not renderer._thread.is_alive()


def test_save_snapshot_uses_explicit_output_and_async_full_resolution(editor,qapp,monkeypatch,tmp_path):
    import luma.actions as actions
    original=tmp_path/'original.png';original.write_bytes(b'original bytes')
    editor.source_path=original
    destination=tmp_path/'edited.png'
    class FakeDialog:
        def __init__(self,*args):
            self.choice=actions.ExportChoice(destination,'PNG')
            self.filename=type('Field',(),{'setText':lambda *args:None})()
        def exec(self): return ImageEditorDialog.DialogCode.Accepted
    monkeypatch.setattr(actions,'ExportDialog',FakeDialog)
    editor._commit(replace(editor.state,crop=(.25,0,.75,1)))
    exported=[];editor.exported.connect(exported.append)
    editor._save_as()
    assert editor._saving
    wait_for(qapp,lambda:not editor._saving)
    assert exported==[str(destination)]
    assert editor._saved_state==editor.state
    assert original.read_bytes()==b'original bytes'
    with Image.open(destination) as result: assert result.size==(400,500)


def test_actual_size_matches_physical_pixels_at_current_dpi(editor,qapp):
    editor.canvas.actual_size()
    assert editor.canvas.actual_zoom()==pytest.approx(100)
    assert editor.canvas.image_rect().width()*editor.canvas.devicePixelRatioF()==pytest.approx(800)
    editor.canvas.fit()
    assert editor.canvas.zoom==1


def test_repeated_close_releases_full_image(editor):
    editor._shutdown()
    editor._shutdown()
    assert editor._source is None
    assert editor.canvas.image.isNull()


def test_compact_window_controls_do_not_overlap(editor,qapp):
    editor.resize(900,620)
    qapp.processEvents()
    buttons=editor.tab_buttons+[editor.save_button]
    rects=[button.geometry() for button in buttons]
    assert all(not a.intersects(b) for i,a in enumerate(rects) for b in rects[i+1:])
    assert editor.save_button.geometry().right()<editor.width()


def test_interactive_preview_is_coarse_then_refines(qapp):
    renderer=EditorRenderer(Image.new('RGBA',(2400,1600),(80,90,100,255)))
    results=[];renderer.finished.connect(lambda token,result,error:results.append(result))
    renderer.request(EditState(),interactive=True)
    wait_for(qapp,lambda:len(results)==1)
    assert max(results[-1].size)<=800
    renderer.request(EditState(),interactive=False)
    wait_for(qapp,lambda:len(results)==2)
    assert max(results[-1].size)>800
    renderer.close();renderer._thread.join(3)


def test_drag_preview_timer_is_throttled_not_restarted(editor):
    slider=editor.adjust_sliders['brightness'][0]
    slider.setSliderDown(True)
    slider.setValue(10)
    timer_id=editor._preview_timer.timerId()
    slider.setValue(20)
    assert editor._preview_timer.timerId()==timer_id
    slider.setSliderDown(False)


@pytest.mark.parametrize('language,labels', [
    ('zh-CN',['裁剪','调整','滤镜','标记']),
    ('zh-TW',['裁剪','調整','濾鏡','標記']),
    ('en',['Crop','Adjust','Filters','Markup']),
])
def test_editor_localization_and_actual_glyph_fallback(qapp,language,labels):
    from luma.i18n import set_language
    from luma.ui_fonts import ui_font_families
    from PySide6.QtGui import QFontDatabase, QTextLayout
    set_language(language)
    image=QImage(30,20,QImage.Format.Format_RGBA8888);image.fill(0)
    dialog=ImageEditorDialog(image,Path('/tmp/font-check.png'))
    try:
        dialog.show();qapp.processEvents()
        assert [b.text() for b in dialog.tab_buttons]==labels
        available=set(QFontDatabase.families())
        preferred=[family for family in ui_font_families(language) if family in available]
        for button in dialog.tab_buttons:
            layout=QTextLayout(button.text(),button.font())
            layout.beginLayout();line=layout.createLine();line.setLineWidth(300);layout.endLayout()
            runs=layout.glyphRuns()
            assert runs and all(all(index != 0 for index in run.glyphIndexes()) for run in runs)
            if language.startswith('zh') and preferred:
                assert any(run.rawFont().familyName() in preferred for run in runs)
                assert not any('CJK JP' in run.rawFont().familyName() for run in runs)
    finally:
        dialog._shutdown();dialog._renderer._thread.join(3);dialog.hide();set_language('en')


def test_editor_dropdowns_share_trigger_width_and_interaction(editor,qapp):
    from luma.dropdown_widgets import DropdownComboBox
    for mode,combo in [('Crop',editor.aspect),('Markup',editor.shape_combo)]:
        editor._tab_changed(mode)
        qapp.processEvents()
        assert isinstance(combo,DropdownComboBox)
        combo.showPopup();qapp.processEvents()
        assert combo.popup_panel_rect().width()==combo.width()
        QTest.keyClick(combo.view(),Qt.Key.Key_Down)
        QTest.keyClick(combo.view(),Qt.Key.Key_Enter)
        qapp.processEvents()
        assert not combo.view().window().isVisible()
        assert combo.currentIndex()==1
