#!/usr/bin/env python3
"""Fresh-process HDR thumbnail timing; no Windows registration or cache eviction.

Generate synthetic 4K/8K/16K fixtures and compare source workers:
  python scripts/benchmark_thumbnails.py --fixtures /tmp/hdr --generate --output report.json
  python scripts/benchmark_thumbnails.py --fixtures /tmp/hdr --baseline-dir /tmp/released

The optional baseline directory must contain decoders.py and thumbnail_worker.py
from the release under comparison. Use git show to extract those exact files.
These are source-worker measurements, not packaged Windows Job-memory evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _worker(path: Path, edge: int, baseline: Path | None) -> dict:
    import luma
    if baseline:
        for name in ("decoders", "thumbnail_worker"):
            source = baseline / (name + ".py")
            spec = importlib.util.spec_from_file_location("luma." + name, source)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            setattr(luma, name, module)
            spec.loader.exec_module(module)
    from luma.thumbnail_worker import generate_thumbnail
    started = time.perf_counter()
    try:
        image = generate_thumbnail(path, "hdr", edge)
        result = {"ok": True, "dimensions": list(image.size),
                  "rgba_sha256": hashlib.sha256(image.tobytes()).hexdigest()}
    except Exception as error:
        result = {"ok": False, "error": type(error).__name__ + ": " + str(error)}
    result["generate_s"] = time.perf_counter() - started
    try:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        result["process_peak_rss_mib"] = peak / (1024 * 1024 if sys.platform == "darwin" else 1024)
    except ImportError:
        result["process_peak_rss_mib"] = None
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path)
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--baseline-dir", type=Path)
    parser.add_argument("--extra-fixture", type=Path, action="append", default=[])
    parser.add_argument("--edge", type=int, default=256)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 1 <= args.edge <= 1024 or not 1 <= args.repeat <= 100 or args.timeout <= 0:
        parser.error("Use edge 1..1024, repeat 1..100 and a positive timeout")
    if args.baseline_dir:
        args.baseline_dir = args.baseline_dir.resolve(strict=True)
        if not all((args.baseline_dir / name).is_file() for name in ("decoders.py", "thumbnail_worker.py")):
            parser.error("The baseline directory needs decoders.py and thumbnail_worker.py")
    if args.worker:
        print(json.dumps(_worker(args.worker.resolve(strict=True), args.edge, args.baseline_dir)))
        return 0
    if args.fixtures is None:
        parser.error("--fixtures is required")
    directory = args.fixtures.resolve()
    if args.generate:
        from scripts.smoke_bundle import prepare_hdr_thumbnail_fixtures
        prepare_hdr_thumbnail_fixtures(directory)
    files = [directory / f"thumbnail-{width}x{height}.hdr"
             for width, height in ((4096, 2048), (8192, 4096), (16384, 8192))]
    files += [path.resolve(strict=True) for path in args.extra_fixture]
    records = []
    variants = [("candidate", None)]
    if args.baseline_dir:
        variants.insert(0, ("baseline", args.baseline_dir))
    for path in files:
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        for variant, baseline in variants:
            for sample in range(1, args.repeat + 1):
                command = [sys.executable, str(Path(__file__).resolve()), "--worker", str(path),
                           "--edge", str(args.edge)]
                if baseline:
                    command += ["--baseline-dir", str(baseline)]
                started = time.perf_counter()
                child = subprocess.run(command, capture_output=True, text=True,
                                       check=True, timeout=args.timeout)
                elapsed = time.perf_counter() - started
                record = json.loads(child.stdout)
                record.update(variant=variant, fixture=path.name, edge=args.edge, sample=sample,
                              input_bytes=path.stat().st_size, source_sha256=digest, process_wall_s=elapsed)
                records.append(record)
                print(json.dumps(record), flush=True)
    report = {"platform": sys.platform, "python": sys.version,
              "cache": "Fresh interpreter per sample; OS file cache not evicted; input hashes read before timing",
              "note": "Source-worker timings/RSS, not Windows packaged Job-memory validation",
              "results": records}
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
