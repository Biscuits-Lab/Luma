#!/usr/bin/env python3
"""Reproduce minification quality and timings with public/synthetic fixtures.

QT_QPA_PLATFORM=offscreen python scripts/benchmark_minification.py \
  --photo /path/to/matplotlib/mpl-data/sample_data/grace_hopper.jpg --include-16k

No downloads, private images, Windows certification, denoising claim, or source
modifications. Timing excludes fixture generation, reference calculation and PNG
encoding. Generated native-resolution noise is not an 8K camera photograph.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import statistics
import sys
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from PySide6.QtCore import Qt, qVersion
from PySide6.QtGui import QImage, QPainter, QPixmap, QColor
from PySide6.QtWidgets import QApplication, QGraphicsPixmapItem, QStyleOptionGraphicsItem
from luma.minification import MinificationCache
from luma.canvas import FilteredPixmapItem

SEED = 20261017
BACKGROUND = 32 / 255
PHOTO_URL = 'https://github.com/matplotlib/matplotlib/blob/main/lib/matplotlib/mpl-data/sample_data/grace_hopper.jpg'
PHOTO_ORIGIN = 'https://commons.wikimedia.org/wiki/File:Grace_Hopper.jpg'


def pixels(image):
    image = image.convertToFormat(QImage.Format.Format_RGBA8888_Premultiplied)
    return np.frombuffer(image.constBits(), np.uint8).reshape(image.height(), image.bytesPerLine())[:, :image.width()*4].reshape(image.height(), image.width(), 4).copy()


def qimage(array):
    h, w, _ = array.shape
    return QImage(array.data, w, h, array.strides[0], QImage.Format.Format_RGBA8888).convertToFormat(QImage.Format.Format_RGBA8888_Premultiplied)


def synthetic(width, height, kind):
    """Generate each source once, in strips to bound RNG temporaries."""
    out = np.empty((height, width, 4), np.uint8)
    out[..., 3] = 255
    rng = np.random.default_rng(SEED)
    x = np.arange(width)[None, :]
    for top in range(0, height, 128):
        bottom = min(height, top + 128)
        y = np.arange(top, bottom)[:, None]
        if kind == 'flat-noise':
            g = np.clip(np.rint(rng.normal(128, 24, (bottom-top, width))), 0, 255).astype(np.uint8)
        else:
            # Different bands prevent a lucky integer phase from hiding aliasing.
            period = np.array([1, 3, 5, 7])[np.minimum(y * 4 // height, 3)]
            g = (((x // period + y // period) % 2) * 255).astype(np.uint8)
        out[top:bottom, :, :3] = g[..., None]
    return qimage(out)


def srgb_to_linear(rgb):
    return np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4)


def linear_to_srgb(rgb):
    return np.where(rgb <= .0031308, rgb * 12.92, 1.055 * np.maximum(rgb, 0) ** (1/2.4) - .055)


def area_reference(source, width, height, linear=False):
    """Independent exact coverage box integral; bounded 16-output-row strips.

    In display mode, average premultiplied display-code RGBA, matching Qt's
    ordinary SDR convention. Linear mode independently averages linear-light
    premultiplied RGB and alpha. Neither uses Qt/Pillow resize as its oracle.
    """
    h, w, _ = source.shape
    fx, fy = w / width, h / height
    xl = np.arange(width, dtype=np.float64) * fx
    xr = (np.arange(width, dtype=np.float64)+1) * fx
    li, ri = np.floor(xl).astype(int), np.floor(xr).astype(int)
    lf, rf = xl-li, xr-ri
    out = np.empty((height, width, 4), np.float32)
    for top in range(0, height, 16):
        ys = np.arange(top, min(height, top+16))
        yl, yr = ys*fy, (ys+1)*fy
        first, last = int(np.floor(yl[0])), min(h, int(np.ceil(yr[-1])))
        block = source[first:last].astype(np.float64) / 255
        if linear:
            alpha = block[..., 3:4]
            rgb = np.divide(block[..., :3], alpha, out=np.zeros_like(block[..., :3]), where=alpha > 0)
            block[..., :3] = srgb_to_linear(np.clip(rgb, 0, 1)) * alpha
        prefix = np.concatenate((np.zeros((last-first, 1, 4)), np.cumsum(block, axis=1)), axis=1)
        left = prefix[:, li] + block[:, np.minimum(li, w-1)] * lf[None, :, None]
        right = prefix[:, ri] + block[:, np.minimum(ri, w-1)] * rf[None, :, None]
        horizontal = (right-left) / fx
        result = np.zeros((len(ys), width, 4), np.float64)
        base = np.floor(yl).astype(int)
        for step in range(int(math.ceil(fy))+1):
            rows = base+step
            weight = np.maximum(0, np.minimum(yr, rows+1) - np.maximum(yl, rows)) / fy
            result += horizontal[np.clip(rows-first, 0, last-first-1)] * weight[:, None, None]
        out[top:top+len(ys)] = result
    return out


def composite(display_rgba):
    return display_rgba[..., :3] + BACKGROUND*(1-display_rgba[..., 3:4])


def quality(rendered, display_reference, linear_reference, kind):
    a = rendered.astype(np.float32) / 255
    rgb = composite(a)
    drgb = composite(display_reference)
    linear = srgb_to_linear(np.clip(rgb, 0, 1))
    lref = linear_reference[..., :3] + srgb_to_linear(np.array(BACKGROUND))*(1-linear_reference[..., 3:4])
    # Exclude the outermost pixels from natural/noise summary to avoid boundary
    # convention dominating a tiny photograph. Full-image RMSE remains reported.
    inner = np.s_[2:-2, 2:-2] if min(rgb.shape[:2]) > 8 else np.s_[:, :]
    luma = rgb @ np.array([.2126, .7152, .0722])
    linear_y = linear @ np.array([.2126, .7152, .0722])
    result = {
        'display_area_rmse_8bit': float(np.sqrt(np.mean((rgb-drgb)**2))*255),
        'display_area_mean_drift_8bit': float(np.mean(rgb-drgb)*255),
        'linear_light_area_rmse': float(np.sqrt(np.mean((linear-lref)**2))),
        'linear_light_area_mean_drift': float(np.mean(linear-lref)),
        'mean_display_luma_8bit': float(luma[inner].mean()*255),
        'mean_linear_luminance': float(linear_y[inner].mean()),
    }
    if kind == 'flat-noise':
        result['flat_noise_std_display_8bit'] = float(luma[inner].std()*255)
        result['flat_noise_std_linear_luminance'] = float(linear_y[inner].std())
    if kind == 'checker':
        result['checker_alias_rmse_vs_display_area_8bit'] = result['display_area_rmse_8bit']
    return result


def render(item, source_size, scale, dpr, repeats=1, crop=None):
    w, h = source_size
    ow, oh = (max(1, round(w*scale)), max(1, round(h*scale))) if crop is None else crop[2:]
    target = QImage(ow, oh, QImage.Format.Format_RGBA8888_Premultiplied)
    target.setDevicePixelRatio(dpr)
    option = QStyleOptionGraphicsItem()
    times = []
    for _ in range(repeats):
        target.fill(Qt.GlobalColor.transparent)
        started = time.perf_counter()
        painter = QPainter(target)
        try:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.scale((round(w*scale)/w)/dpr, (round(h*scale)/h)/dpr)
            if crop:
                painter.translate(-crop[0], -crop[1])
            item.paint(painter, option, None)
        finally:
            painter.end()
        times.append((time.perf_counter()-started)*1000)
    return target, {'median_ms': statistics.median(times), 'samples_ms': times}


def pump(app, predicate, timeout=90):
    deadline = time.perf_counter()+timeout
    while not predicate():
        app.processEvents()
        if time.perf_counter() > deadline:
            raise RuntimeError('Cache did not complete before timeout')
        time.sleep(.001)
    app.processEvents()


def idle(cache):
    return cache._active is None


def comparison(output, name, scale, images):
    tile = 256
    labels = ['Original default Fast item', 'Smooth item only', 'Area cache + smooth item', 'Exact display-area reference']
    sheet = Image.new('RGB', (4*(tile+12)+12, tile+80), '#202020')
    draw = ImageDraw.Draw(sheet)
    draw.text((12, 8), f'{name} | physical scale {scale:g} | center crop at 1 display pixel per PNG pixel', fill='white')
    for column, (label, rgba) in enumerate(zip(labels, images)):
        rgb = np.uint8(np.clip(np.rint(composite(rgba.astype(np.float32)/255)*255), 0, 255))
        im = Image.fromarray(rgb)
        left, top = max(0, (im.width-tile)//2), max(0, (im.height-tile)//2)
        im = im.crop((left, top, min(im.width, left+tile), min(im.height, top+tile)))
        x = 12+column*(tile+12)
        sheet.paste(im, (x, 48))
        draw.text((x, 29), label, fill='white')
    sheet.save(output / f'{name}-scale-{scale:g}.png')


def measure_fixture(app, image, name, kind, output, scales, repeat):
    source = pixels(image)
    pm = QPixmap.fromImage(image)
    original = QGraphicsPixmapItem(pm)
    smooth = QGraphicsPixmapItem(pm)
    smooth.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
    report = {'name': name, 'kind': kind, 'source_size': [image.width(), image.height()], 'source_pixels_sha256': hashlib.sha256(source).hexdigest(), 'samples': []}
    for scale in scales:
        ow, oh = round(image.width()*scale), round(image.height()*scale)
        dr = area_reference(source, ow, oh)
        lr = area_reference(source, ow, oh, linear=True)
        cache = MinificationCache()
        candidate = FilteredPixmapItem(cache)
        candidate.setPixmap(pm)
        start = time.perf_counter()
        cache.request(scale)
        pump(app, lambda: idle(cache) and cache.choose(scale) is not None)
        cold = (time.perf_counter()-start)*1000
        start = time.perf_counter()
        for _ in range(1000):
            cache.request(scale)
            cache.choose(scale)
        lookup = (time.perf_counter()-start)*1000/1000
        try:
            for dpr in (1, 2):
                arrays = []
                entry = {'physical_scale': scale, 'logical_scale': scale/dpr, 'dpr': dpr, 'cold_cache_ready_ms': cold, 'warm_request_choose_ms': lookup, 'cache_bytes': cache.cached_bytes, 'cache_levels': list(cache.cached_levels), 'methods': {}}
                for method, item in [('original_fast', original), ('smooth_only', smooth), ('area_cache', candidate)]:
                    result, timing = render(item, (image.width(), image.height()), scale, dpr, repeat)
                    arr = pixels(result)
                    arrays.append(arr)
                    entry['methods'][method] = {'paint': timing, **quality(arr, dr, lr, kind)}
                entry['reference_flat_noise_std_8bit'] = float((composite(dr) @ np.array([.2126,.7152,.0722])).std()*255) if kind == 'flat-noise' else None
                report['samples'].append(entry)
                if dpr == 1:
                    comparison(output, name, scale, arrays+[np.uint8(np.clip(np.rint(dr*255), 0, 255))])
            # Physical 1:1 uses original detail even after cache population.
            crop = (image.width()//2-64, image.height()//2-64, 128, 128)
            expected = source[crop[1]:crop[1]+128, crop[0]:crop[0]+128]
            entry = []
            for dpr in (1, 2):
                actual, _ = render(candidate, (image.width(), image.height()), 1.0, dpr, crop=crop)
                delta = np.abs(pixels(actual).astype(int)-expected.astype(int))
                entry.append({'dpr':dpr, 'max_channel_error':int(delta.max()), 'cache_choose_is_none':cache.choose(1.0) is None})
            report['physical_1to1'] = entry
        finally:
            cache.close()
            app.processEvents()
        print(f'{name} scale={scale:g} cold={cold:.1f} ms cache={report["samples"][-1]["cache_bytes"]/2**20:.1f} MiB', flush=True)
    return report


def stress(app, output, include16k):
    """Bounded full-resolution allocation, rapid zoom, and latest-image checks."""
    results = []
    for w,h in ([(7680,4320), (16384,8192)] if include16k else [(7680,4320)]):
        start = time.perf_counter()
        image = synthetic(w,h,'flat-noise')
        generation = time.perf_counter()-start
        cache = MinificationCache()
        cache.set_source(image)
        changed = []
        cache.changed.connect(lambda: changed.append(time.perf_counter()))
        start = time.perf_counter()
        scales = [.5,.125,.37,.25,.125,.4,.125]*12
        for scale in scales:
            cache.request(scale)
        pump(app, lambda: cache.choose(.125) is not None)
        ready = (time.perf_counter()-start)*1000
        memory = cache.cached_bytes
        # Immediately switch while requests may still be running. A solid unique
        # color makes an obsolete prior-image result unambiguous.
        marker = QImage(1536,1024,QImage.Format.Format_RGBA8888_Premultiplied)
        marker.fill(QColor(19,173,83,255))
        cache.request(.25)
        cache.set_source(marker)
        cache.request(.125)
        pump(app, lambda: cache.choose(.125) is not None)
        deadline = time.perf_counter()+.2
        while time.perf_counter()<deadline:
            app.processEvents(); time.sleep(.001)
        result = pixels(cache.choose(.125))
        maxerr = int(np.max(np.abs(result.astype(np.int16)-np.array([19,173,83,255]))))
        item = {'source_size':[w,h], 'source_generation_seconds_excluded':generation, 'rapid_zoom_requests':len(scales), 'latest_zoom_ready_ms':ready, 'cache_bytes_before_switch':memory, 'cache_bytes_after_switch':cache.cached_bytes, 'latest_image_max_channel_error':maxerr, 'changed_signals':len(changed)}
        cache.close()
        results.append(item)
        print('stress', item, flush=True)
        del image,cache,result
        gc.collect()
    return results


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--photo', type=Path, required=True, help='Unmodified public Matplotlib grace_hopper.jpg (not a private user file)')
    parser.add_argument('--output', type=Path, default=Path('build/minification-evidence'))
    parser.add_argument('--sizes', nargs='+', default=['3840x2160','7680x4320'])
    parser.add_argument('--scales', nargs='+', type=float, default=[.5,.25,.125,.37])
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--include-16k', action='store_true')
    args=parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    app=QApplication.instance() or QApplication([])
    if app.platformName() != 'offscreen':
        raise RuntimeError('This benchmark must run with QT_QPA_PLATFORM=offscreen')
    photo=Image.open(args.photo)
    comment=photo.info.get('comment',b'').decode('latin1')
    if 'commons.wikimedia.org/wiki/File:Grace_Hopper.jpg' not in comment:
        raise ValueError('Expected public Matplotlib Grace Hopper fixture with original source comment')
    (args.output/'grace_hopper.jpg').write_bytes(args.photo.read_bytes())
    report={'platform':platform.platform(),'python':platform.python_version(),'qt':qVersion(),'qt_platform':os.environ['QT_QPA_PLATFORM'],'numpy':np.__version__,'seed':SEED,'conditions':['Qt offscreen CPU painting on recorded host; not interactive desktop validation','Original QGraphicsPixmapItem has default FastTransformation; painter SmoothPixmapTransform is on in all paths','Display-area reference integrates premultiplied SDR code values; linear-light reference is separately reported','Flat-noise STD is reported only for constant synthetic scene plus independent seeded noise; natural-image RMSE is not a noise metric','Sources generated once before timed work; QPixmap creation, references, I/O and output allocation/fill excluded from paint timing','Cold cache time includes request, worker completion and Qt delivery; warm time is request+choose only','DPR1/2 pairs have identical physical scale; logical scale is physical scale divided by DPR'], 'photo':{'file':args.photo.name,'size':list(photo.size),'sha256':hashlib.sha256(args.photo.read_bytes()).hexdigest(),'source_comment':comment,'fixture_url':PHOTO_URL,'original_url':PHOTO_ORIGIN,'credit':'James S. Davis / U.S. Navy, January 20 1984. U.S. federal government public-domain work. Matplotlib distributed 512x600 derivative; never upscaled for this test.'},'fixtures':[]}
    # Opaque and alpha fixtures exercise premultiplication independently of noise.
    report['fixtures'].append(measure_fixture(app,qimage(np.array(photo.convert('RGBA'))),'public-grace-hopper','natural-photo',args.output,args.scales,args.repeats))
    alpha=np.zeros((600,512,4),np.uint8)
    alpha[...,2]=255  # Hidden blue must not contaminate partially opaque red.
    alpha[::2,::2]=[255,0,0,128]
    report['fixtures'].append(measure_fixture(app,qimage(alpha),'premultiplied-alpha','alpha',args.output,args.scales,args.repeats))
    for size in args.sizes:
        w,h=map(int,size.split('x'))
        for kind in ('flat-noise','checker'):
            image=synthetic(w,h,kind)
            report['fixtures'].append(measure_fixture(app,image,f'{kind}-{size}',kind,args.output,args.scales,args.repeats))
            del image
            gc.collect()
            (args.output/'results.json').write_text(json.dumps(report,indent=2)+'\n')
    report['stress']=stress(app,args.output,args.include_16k)
    report['passed_1to1']=all(x['max_channel_error']==0 and x['cache_choose_is_none'] for f in report['fixtures'] for x in f['physical_1to1'])
    report['passed_latest_image']=all(s['latest_image_max_channel_error']==0 for s in report['stress'])
    report['passed_cache_budget']=all(s['cache_bytes_before_switch']<=64*2**20 for s in report['stress']) and all(s['cache_bytes']<=64*2**20 for f in report['fixtures'] for s in f['samples'])
    (args.output/'results.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k.startswith('passed_')},indent=2))
    return 0 if report['passed_1to1'] and report['passed_latest_image'] and report['passed_cache_budget'] else 1


if __name__=='__main__':
    raise SystemExit(main())
