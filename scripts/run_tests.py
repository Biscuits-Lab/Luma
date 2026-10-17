"""Run all selected source tests in isolated, parallel offscreen Qt processes.

No result is reused: timing history only balances the work. Native Windows,
installer and packaged-release acceptance stays in the existing build workflow.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]


def plan_modules(nodeids, timings):
    """Keep file order/fixtures intact and give every module a fresh Qt app.

    Splitting individual cases mixes global font/theme state and repeatedly
    collects the growing QObject graph. Module isolation also bounds that cost.
    """
    modules = {}
    for node in nodeids:
        modules.setdefault(node.split('::', 1)[0], []).append(node)
    return sorted(modules.values(),
                  key=lambda group: -sum(timings.get(node, 0.1) for node in group))


def read_results(path):
    counts = dict(passed=0, failed=0, errors=0, skipped=0)
    timings = {}
    for case in ET.parse(path).iter('testcase'):
        status = next((name for tag, name in [('error', 'errors'), ('failure', 'failed'),
                                             ('skipped', 'skipped')]
                       if case.find(tag) is not None), 'passed')
        counts[status] += 1
        # pytest's classname uses dotted module names; parameter values stay
        # untouched in name (including dots, spaces and Unicode).
        module = case.get('classname', '').replace('.', '/') + '.py'
        timings[module + '::' + case.get('name', '')] = float(case.get('time', '0'))
    return counts, timings


def stop_process(process):
    if process.poll() is None and sys.platform == 'win32':
        # venv's python.exe is a redirector; killing it alone can leave the
        # actual pytest process and its screenshot subprocesses running.
        try:
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def stop_workers(workers):
    # Only processes created by this invocation, never an existing viewer.
    for worker in workers:
        stop_process(worker['process'])
        worker['stream'].close()


def collect_tests(command, *, env, timeout):
    process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, encoding='utf-8',
                               errors='replace')
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except BaseException:
        stop_process(process)
        process.communicate()
        raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('targets', nargs='*', help='Test files, directories or node IDs (default: tests)')
    parser.add_argument('-j', '--jobs', type=int, default=min(4, os.cpu_count() or 1))
    parser.add_argument('-k', default=None, help='pytest name expression')
    parser.add_argument('--contracts', action='store_true', help='Also run scripts/test_*.py contracts')
    parser.add_argument('--timeout', type=float, default=300, help='Seconds allowed per worker/collection')
    parser.add_argument('--list', action='store_true', help='Collect and show shard sizes without running tests')
    args = parser.parse_args(argv)
    if args.jobs < 1 or args.timeout <= 0:
        parser.error('jobs and timeout must be positive')

    started = time.monotonic()
    output = ROOT / 'build' / 'test-runs'
    output.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix='run-', dir=output))
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', PYTHONUTF8='1', PYTHONUNBUFFERED='1',
               PYTHONIOENCODING='utf-8')
    # Real registry acceptance belongs to the serial disposable Windows runner.
    env.pop('LUMA_SHELL_TEST_REAL_REGISTRY', None)
    targets = args.targets or ['tests']
    if args.contracts:
        targets += [str(path.relative_to(ROOT)) for path in sorted((ROOT / 'scripts').glob('test_*.py'))]
    selection = ['-k', args.k] if args.k else []
    print(f'Offscreen source tests; {args.jobs} workers; logs: {run}', flush=True)
    try:
        collected = collect_tests(
            [sys.executable, '-m', 'pytest', '--collect-only', '-q', '--color=no',
             f'--basetemp={run / "collect-tmp"}', *selection, *targets],
            env=env, timeout=args.timeout)
    except subprocess.TimeoutExpired:
        print(f'Collection exceeded {args.timeout:g}s', flush=True)
        return 1
    (run / 'collection.log').write_text(collected.stdout + collected.stderr, encoding='utf-8')
    if collected.returncode:
        print(collected.stdout + collected.stderr)
        return collected.returncode
    nodes = list(dict.fromkeys(line.strip() for line in collected.stdout.splitlines()
                               if '.py::' in line and not line.startswith((' ', '<'))))
    if not nodes:
        print('No test IDs collected; see collection.log')
        return 5
    history = output / 'timings.json'
    try:
        saved = json.loads(history.read_text(encoding='utf-8'))
        timings = {key: value for key, value in saved.items()
                   if isinstance(value, (int, float)) and 0 <= value < float('inf')}
    except (OSError, ValueError, AttributeError):
        timings = {}
    groups = plan_modules(nodes, timings)
    print(f'{len(nodes)} tests in {len(groups)} isolated modules; up to {args.jobs} at once', flush=True)
    if args.list:
        return 0

    workers = []
    report = {'platform': 'offscreen', 'collected': len(nodes), 'jobs': min(args.jobs, len(groups)),
              'counts': dict(passed=0, failed=0, errors=0, skipped=0), 'workers': []}
    interrupted = False
    try:
        def start_worker(index, group):
            prefix = run / f'worker-{index}'
            arguments = prefix.with_suffix('.args')
            arguments.write_text('\n'.join(group) + '\n', encoding='utf-8')
            log = prefix.with_suffix('.log')
            stream = log.open('w', encoding='utf-8')
            scratch = Path(f'{prefix}-scratch')
            scratch.mkdir()
            worker_env = dict(env, TEMP=str(scratch), TMP=str(scratch), TMPDIR=str(scratch))
            command = [sys.executable, '-m', 'pytest', '-q', '--color=no', '--durations=10',
                       '-o', 'faulthandler_timeout=60',
                       '-o', f'cache_dir={prefix}-cache',
                       f'--basetemp={prefix}-tmp', f'--junitxml={prefix}.xml', '@' + str(arguments)]
            try:
                process = subprocess.Popen(command, cwd=ROOT, env=worker_env, stdout=stream,
                                           stderr=subprocess.STDOUT)
            except BaseException:
                stream.close()
                raise
            workers.append(dict(process=process, stream=stream, log=log,
                                xml=prefix.with_suffix('.xml'), started=time.monotonic(),
                                selected=len(group), done=False))
        queued = iter(enumerate(groups, 1))
        for _ in range(min(args.jobs, len(groups))):
            start_worker(*next(queued))
        progress_at = time.monotonic() + 15
        while len(report['workers']) < len(groups):
            for index, worker in enumerate(list(workers), 1):
                if worker['done']:
                    continue
                elapsed = time.monotonic() - worker['started']
                code = worker['process'].poll()
                if code is None and elapsed <= args.timeout:
                    continue
                if code is None:
                    stop_process(worker['process'])
                    code = 124
                worker['done'] = True
                worker['stream'].close()
                try:
                    counts, measured = read_results(worker['xml'])
                    timings.update(measured)
                    if sum(counts.values()) != worker['selected'] and code == 0:
                        code = 1
                    for key, value in counts.items():
                        report['counts'][key] += value
                except (OSError, ET.ParseError, ValueError):
                    code = code or 1
                report['workers'].append(dict(index=index, exit_code=code,
                                               seconds=round(elapsed, 3), log=str(worker['log'])))
                print(f'Worker {index}: exit {code}, {elapsed:.1f}s', flush=True)
                if code:
                    print(worker['log'].read_text(encoding='utf-8', errors='replace')[-12000:], flush=True)
                next_group = next(queued, None)
                if next_group is not None:
                    start_worker(*next_group)
            if time.monotonic() >= progress_at:
                active = [i for i, worker in enumerate(workers, 1) if not worker['done']]
                if active:
                    print(f'{time.monotonic() - started:.0f}s elapsed; running workers: {active}', flush=True)
                progress_at = time.monotonic() + 15
            time.sleep(0.1)
    except KeyboardInterrupt:
        interrupted = True
        print('Interrupted; stopping this run\'s workers.', flush=True)
    finally:
        stop_workers(workers)
    report['seconds'] = round(time.monotonic() - started, 3)
    report['success'] = (not interrupted and len(report['workers']) == len(groups)
                         and all(worker['exit_code'] == 0 for worker in report['workers']))
    (run / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    # Atomic timing updates; this cache never bypasses test execution.
    pending = run / 'timings.json'
    pending.write_text(json.dumps(timings, indent=2), encoding='utf-8')
    pending.replace(history)
    print(f'{report["counts"]}; {report["seconds"]:.1f}s total; report: {run / "report.json"}', flush=True)
    return 130 if interrupted else (0 if report['success'] else 1)


if __name__ == '__main__':
    raise SystemExit(main())
