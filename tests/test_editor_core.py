from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from luma.editor_core import (
    Adjustments, EditState, EditHistory, EditorCancelled, FILTERS, Stroke,
    bounded_size, crop_box, render, export_image, transform_point, change_geometry,
)


def fixture_image(width=48, height=32):
    y, x = np.mgrid[:height, :width]
    rgba = np.stack(((x*7)%256, (y*11)%256, ((x+y)*5)%256, (x*13+y*17)%256), axis=-1).astype(np.uint8)
    return Image.fromarray(rgba)


def test_neutral_is_exact_and_source_immutable():
    image = fixture_image()
    before = image.tobytes()
    result = render(image, EditState())
    assert result.tobytes() == before
    assert result is not image
    render(image, EditState(adjustments=Adjustments(exposure=1, saturation=40), turns=1))
    assert image.tobytes() == before


@pytest.mark.parametrize('turns', [0, 1, 2, 3, 4, -1])
def test_quarter_rotation_is_lossless(turns):
    image = fixture_image()
    result = render(image, EditState(turns=turns))
    expected = np.rot90(np.asarray(image), -turns)
    np.testing.assert_array_equal(result, expected)


@pytest.mark.parametrize('x,y', [(True,False), (False,True), (True,True)])
def test_flips_preserve_rgba(x,y):
    array = np.asarray(fixture_image())
    expected = array[:, ::-1] if x else array
    expected = expected[::-1] if y else expected
    np.testing.assert_array_equal(render(fixture_image(), EditState(flip_x=x,flip_y=y)),expected)


def test_crop_full_resolution_and_one_pixel_minimum():
    image = fixture_image(320,160)
    state = EditState(crop=(.25,.25,.75,.75))
    result = render(image,state)
    assert result.size == (160,80)
    np.testing.assert_array_equal(result,np.asarray(image)[40:120,80:240])
    assert render(image,EditState(crop=(.5,.5,.50000001,.50000001))).size == (1,1)


@pytest.mark.parametrize('name', list(FILTERS))
def test_filters_preserve_alpha_and_source(name):
    image=fixture_image()
    result=render(image,EditState(filter_name=name))
    np.testing.assert_array_equal(np.asarray(result)[...,3],np.asarray(image)[...,3])
    if name!='original': assert result.tobytes()!=image.tobytes()


@pytest.mark.parametrize('key', list(vars(Adjustments())))
@pytest.mark.parametrize('sign', [-1,1])
def test_adjustment_is_functional_and_alpha_preserving(key,sign):
    image=fixture_image()
    value=sign*(1 if key=='exposure' else 70)
    result=render(image,EditState(adjustments=replace(Adjustments(),**{key:value})))
    assert result.tobytes()!=image.tobytes()
    np.testing.assert_array_equal(np.asarray(result)[...,3],np.asarray(image)[...,3])


def test_zero_filter_strength_is_exact():
    image=fixture_image()
    assert render(image,EditState(filter_name='noir',filter_strength=0)).tobytes()==image.tobytes()


def test_auto_enhance_is_alpha_aware():
    image=Image.new('RGBA',(50,40),(80,100,120,0))
    assert render(image,EditState(auto_enhance=True)).tobytes()==image.tobytes()
    image=fixture_image()
    result=render(image,EditState(auto_enhance=True))
    assert result.tobytes()!=image.tobytes()
    np.testing.assert_array_equal(np.asarray(result)[...,3],np.asarray(image)[...,3])


def test_straighten_retains_dimensions_and_transparent_corners():
    image=Image.new('RGBA',(300,180),(80,100,120,255))
    result=render(image,EditState(straighten=20))
    assert result.size==image.size
    assert result.getpixel((0,0))[3]==0
    assert result.getpixel((150,90))[3]==255


@pytest.mark.parametrize('turns', [0,1,2,3])
@pytest.mark.parametrize('flip_x,flip_y', [(False,False),(True,False),(False,True),(True,True)])
def test_coordinate_mapping_round_trip(turns,flip_x,flip_y):
    state=EditState(turns=turns,flip_x=flip_x,flip_y=flip_y,straighten=21)
    for point in ((.1,.2),(.7,.8),(.5,.5)):
        mapped=transform_point(point,state,(400,200))
        assert transform_point(mapped,state,(400,200),inverse=True)==pytest.approx(point)


def test_geometry_keeps_crop_attached_and_annotation_on_source():
    state=EditState(crop=(.1,.2,.4,.8))
    rotated=change_geometry(state,(400,200),turns=1)
    assert rotated.crop==pytest.approx((.2,.1,.8,.4))
    image=Image.new('RGBA',(200,100),(0,0,0,255))
    stroke=Stroke(((.25,.2),),color=(255,0,0,255),width=.1)
    out=render(image,EditState(turns=1,strokes=(stroke,)))
    assert out.getpixel((80,50))[:3]==(255,0,0)


@pytest.mark.parametrize('tool', ['pen','pencil','highlighter'])
@pytest.mark.parametrize('shape', ['freehand','line','arrow','double_arrow'])
def test_annotation_tools_shapes_and_clear(tool,shape):
    image=Image.new('RGBA',(100,100),(255,255,255,255))
    stroke=Stroke(((.1,.1),(.9,.9)),color=(255,0,0,255),width=.04,tool=tool,shape=shape)
    state=EditState(strokes=(stroke,))
    out=render(image,state)
    assert out.getpixel((50,50))[1]<255
    if tool=='highlighter': assert out.getpixel((50,50))[1]>150
    assert render(image,replace(state,strokes=())).tobytes()==image.tobytes()


def test_annotation_can_add_alpha_on_transparent_image():
    image=Image.new('RGBA',(100,100),(0,0,0,0))
    out=render(image,EditState(strokes=(Stroke(((.1,.5),(.9,.5))),)))
    assert out.getpixel((50,50))[3]>0
    assert out.getpixel((0,0))[3]==0


def test_history_is_bounded_commands_no_images_and_redo_branch():
    history=EditHistory(limit=5)
    for i in range(20): history.commit(EditState(adjustments=Adjustments(brightness=i)))
    assert len(history.undo_commands)==5
    assert history.undo().adjustments.brightness==18
    assert history.redo().adjustments.brightness==19
    history.undo()
    history.commit(EditState(filter_name='vivid'))
    assert not history.redo_commands
    assert all(isinstance(c.before,EditState) for c in history.undo_commands)
    history.reset()
    assert history.state==EditState()
    assert history.undo().filter_name=='vivid'


def test_history_shares_vectors_and_limits_repeated_clear_points(monkeypatch):
    import luma.editor_core as core
    monkeypatch.setattr(core,'MAX_HISTORY_POINTS',20)
    history=EditHistory()
    for n in range(20):
        stroke=Stroke(tuple((i/10,n/20) for i in range(10)))
        history.commit(EditState(strokes=(stroke,)))
        assert history.state.strokes[0] is stroke
        history.reset()
    strokes={id(s):s for c in history.undo_commands for state in (c.before,c.after) for s in state.strokes}
    assert sum(len(s.points) for s in strokes.values())<=20


@pytest.mark.parametrize('format,suffix', [('PNG','.png'),('TIFF','.tiff'),('WEBP','.webp')])
def test_save_as_lossless_alpha_full_resolution_source_unchanged(tmp_path,format,suffix):
    source=tmp_path/'source.png'
    image=fixture_image(2100,1000)
    image.save(source)
    before=source.read_bytes()
    destination=tmp_path/('edited'+suffix)
    state=EditState(crop=(.25,0,.75,1),turns=2)
    preview=render(image,state,preview=True)
    export_image(image,state,destination,source,format)
    with Image.open(destination) as result:
        assert result.size==(1050,1000)
        assert result.size!=preview.size
        np.testing.assert_array_equal(result.convert('RGBA'),render(image,state))
    assert source.read_bytes()==before


def test_save_rejects_source_same_path_hardlink_symlink(tmp_path):
    source=tmp_path/'source.png'
    image=fixture_image();image.save(source)
    alias=tmp_path/'hard.png';alias.hardlink_to(source)
    link=tmp_path/'sym.png';link.symlink_to(source)
    for path in (source,alias,link):
        with pytest.raises(ValueError): export_image(image,EditState(),path,source,'PNG')


def test_save_refuses_unreviewed_existing_destination_and_bad_extension(tmp_path):
    image=fixture_image(); source=tmp_path/'source.png'; image.save(source)
    output=tmp_path/'other.png';output.write_bytes(b'do not touch')
    with pytest.raises(FileExistsError): export_image(image,EditState(),output,source,'PNG')
    assert output.read_bytes()==b'do not touch'
    with pytest.raises(ValueError): export_image(image,EditState(),tmp_path/'other.exr',source,'PNG')


def test_explicit_replace_and_jpeg_white_flatten(tmp_path):
    from luma.actions import _file_signature
    image=Image.new('RGBA',(30,20),(255,0,0,0))
    source=tmp_path/'source.png'; image.save(source)
    output=tmp_path/'other.jpg';output.write_bytes(b'old output')
    export_image(image,EditState(),output,source,'JPEG',expected=_file_signature(output))
    with Image.open(output) as result:
        assert result.mode=='RGB'
        assert min(result.getpixel((0,0)))>250


def test_cancelled_export_has_no_output(tmp_path):
    with pytest.raises(EditorCancelled):
        export_image(fixture_image(),EditState(),tmp_path/'out.png',tmp_path/'source.png','PNG',cancelled=lambda:True)
    assert not (tmp_path/'out.png').exists()
    assert not list(tmp_path.glob('.luma-edit-*'))


def test_preview_is_bounded():
    for size in ((16000,1000),(8192,8192),(1,8000),(100,100)):
        w,h=bounded_size(size)
        assert max(w,h)<=1600
        assert w*h<=1_601_000
    out=render(Image.new('RGBA',(3000,2000)),EditState(),preview=True)
    assert max(out.size)<=1600 and out.width*out.height<=1_601_000


@pytest.mark.parametrize('state', [EditState(crop=(1,0,0,1)),EditState(straighten=90),EditState(filter_name='ai'),EditState(adjustments=Adjustments(exposure=8))])
def test_invalid_recipe_rejected(state):
    with pytest.raises(ValueError): render(fixture_image(),state)


def test_subpixel_crop_on_last_pixel_never_extends_image():
    image=Image.new('RGBA',(100,80),(120,130,140,255))
    state=EditState(crop=(.99999999,.99999999,1,1))
    assert crop_box(image.size,state.crop)==(99,79,100,80)
    output=render(image,state)
    assert output.size==(1,1)
    assert output.getpixel((0,0))==(120,130,140,255)
