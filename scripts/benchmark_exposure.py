#!/usr/bin/env python3
"""Same-file, fresh-process exposure latency probe (Qt offscreen, synthetic inputs).

Run once per repo/fixture/sample; source generation is outside timed regions.
Records input-to-canvas-install, not monitor scanout or Windows GPU latency.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import time

try:
    import resource
except ImportError:
    resource = None


def peak_rss_mib():
    if resource is None:
        return None
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 ** 2 if sys.platform == 'darwin' else 1024)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', type=Path)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--label', default='candidate')
    args = parser.parse_args()
    sys.path.insert(0, str(args.repo.resolve()))
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    import numpy as np
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from luma.app import ViewerWindow, STYLE
    from luma import workers
    from luma.decoders import render_rgba
    app = QApplication.instance() or QApplication([])
    app.setStyle('Fusion'); app.setStyleSheet(STYLE)
    events, stages, inputs, heartbeats = [], [], [], []
    final_rgba = []
    calls = {'decode': 0}
    original_decode = workers.decode_image
    def decode(*a, **kw):
        calls['decode'] += 1
        return original_decode(*a, **kw)
    workers.decode_image = decode
    original_render = workers.render_display_rgba
    def render(data, exposure, channel, tone, cancelled):
        start = time.perf_counter()
        outcome = 'completed'
        try:
            return original_render(data, exposure, channel, tone, cancelled)
        except Exception:
            outcome = 'cancelled' if cancelled() else 'failed'
            raise
        finally:
            stages.append(dict(exposure=exposure, pixels=data.width * data.height,
                render_ms=(time.perf_counter() - start)*1000, outcome=outcome))
    workers.render_display_rgba = render
    class Probe(ViewerWindow):
        def _loaded(self, token, result):
            valid = token == self._load_token and result is not None and result['path'] == self.path
            start = time.perf_counter()
            super()._loaded(token, result)
            end = time.perf_counter()
            if valid:
                if not result.get('preview', False) and self.exposure.value() == 14:
                    final_rgba[:] = [result['rgba']]
                events.append(dict(at=end, preview=result.get('preview', False),
                    ui_install_ms=(end-start)*1000, value=self.exposure.value(),
                    width=self.canvas.item.pixmap().width(), height=self.canvas.item.pixmap().height()))
    window = Probe(); window.show(); app.processEvents()
    heartbeat = QTimer(); heartbeat.setInterval(5)
    heartbeat.timeout.connect(lambda: heartbeats.append(time.perf_counter()))
    heartbeat.start()
    def wait(predicate, timeout=90):
        deadline = time.perf_counter()+timeout
        while not predicate():
            app.processEvents()
            if time.perf_counter()>deadline:
                raise TimeoutError(window.details.toPlainText())
            time.sleep(.001)
        app.processEvents()
    def settled():
        return not window._pending_render and not window.render_timer.isActive()
    path = args.path.resolve()
    start=time.perf_counter(); window.open_path(path)
    wait(lambda: window.displayed_path == path)
    opening_ms=(time.perf_counter()-start)*1000
    # The Details panel is visible in both arms, including histogram and glass.
    window.sidebar.show(); app.processEvents(); time.sleep(.08); app.processEvents()
    results=[]
    for value in (10, -10, 20):
        events.clear(); stages.clear(); heartbeats.clear()
        start=time.perf_counter(); window.exposure.setValue(value)
        wait(lambda: settled() and events)
        end=time.perf_counter()
        preview=next((e for e in events if e['preview']), None)
        full=next(e for e in reversed(events) if not e['preview'])
        results.append(dict(value=value, input_to_first_ms=(events[0]['at']-start)*1000,
            input_to_preview_ms=(preview['at']-start)*1000 if preview else None,
            input_to_exact_ms=(full['at']-start)*1000, ui_install_max_ms=max(e['ui_install_ms'] for e in events),
            ui_heartbeat_gap_max_ms=max(np.diff([start]+heartbeats+[end]))*1000,
            stages=list(stages)))
    events.clear(); stages.clear(); heartbeats.clear(); inputs.clear()
    drag_start=time.perf_counter()
    window.exposure.setSliderDown(True)
    def change(value):
        inputs.append((time.perf_counter(),value)); window.exposure.setValue(value)
    values=list(range(-15,15))
    for index,value in enumerate(values):
        QTimer.singleShot(index*33, lambda value=value: change(value))
    release=[]
    def finish():
        release.append(time.perf_counter()); window.exposure.setSliderDown(False)
    QTimer.singleShot(len(values)*33, finish)
    wait(lambda: release and settled() and events and events[-1]['value']==values[-1])
    drag_end=time.perf_counter()
    previews=[e for e in events if e['preview'] and e['at']<release[0]]
    latencies=[]
    for event in previews:
        matching=[at for at,value in inputs if value==event['value'] and at<=event['at']]
        if matching: latencies.append((event['at']-matching[-1])*1000)
    latest=events[-1]
    # Exact equality is checked against the unchanged public renderer, outside timing.
    timing_peak_rss=peak_rss_mib()
    data=window.service.current_image
    exact=render_rgba(data, values[-1]/10, window.channel.currentText(), window.tone.currentData())
    identical=bool(np.array_equal(exact,final_rgba[0]))
    with path.open('rb') as stream: digest=hashlib.file_digest(stream,'sha256').hexdigest()
    record=dict(label=args.label,fixture=path.name,fixture_sha256=digest,width=data.width,height=data.height,
        source_bytes=data.nbytes,opening_ms=opening_ms,changes=results,
        drag=dict(events=len(values),interval_ms=33,previews_during_drag=len(previews),
            accepted_frames_during_drag=sum(e['at']<release[0] for e in events),
            input_to_preview_median_ms=float(np.median(latencies)) if latencies else None,
            input_to_preview_max_ms=max(latencies) if latencies else None,
            release_to_exact_ms=(latest['at']-release[0])*1000,
            input_to_exact_ms=(latest['at']-inputs[-1][0])*1000,
            ui_install_max_ms=max(e['ui_install_ms'] for e in events),
            ui_heartbeat_gap_max_ms=max(np.diff([drag_start]+heartbeats+[drag_end]))*1000,
            cancelled_render_count=sum(s['outcome']=='cancelled' for s in stages),
            completed_render_count=sum(s['outcome']=='completed' for s in stages)),
        decode_calls=calls['decode'],exact_pixels_equal=identical,
        exact_rgba_sha256=hashlib.sha256(exact).hexdigest(),
        cache_bytes=window.service.cache.bytes,render_cache_bytes=window.service.render_cache.bytes,
        preview_source_bytes=getattr(window.service,'preview_image',None).nbytes if getattr(window.service,'preview_image',None) is not None else 0,
        peak_rss_mib=timing_peak_rss,
        validation_peak_rss_mib=peak_rss_mib(),
        python=platform.python_version(),numpy=np.__version__,platform=platform.platform(),
        conditions='Qt offscreen; synthetic input; OS cache not evicted; one fresh process per sample; input-to-canvas install, not scanout')
    window.close(); app.processEvents()
    print(json.dumps(record),flush=True)

if __name__=='__main__': main()
