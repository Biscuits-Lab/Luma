"""Regression checks for selection, balancing and fail-closed worker results."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import run_tests as runner


def test_partition_preserves_module_order_and_prioritizes_measured_slow_modules():
    nodes = [f'tests/test_capture.py::test_capture[{i}]' for i in range(8)]
    nodes += [f'tests/test_core.py::test_core[{i}]' for i in range(40)]
    times = {node: 3.0 for node in nodes[:8]}
    groups = runner.partition(nodes, 4, times)
    assert sorted(node for group in groups for node in group) == sorted(nodes)
    assert groups == [nodes[:8], nodes[8:]]
    assert runner.partition([], 4, {}) == []
    assert runner.partition(nodes[:1], 4, {}) == [nodes[:1]]


def test_junit_counts_setup_errors_skips_and_failures_separately(tmp_path):
    xml = tmp_path / 'results.xml'
    xml.write_text('''<testsuites><testsuite>
      <testcase classname="tests.test_core" name="test_ok[1.5-中文]" time="1.2"/>
      <testcase classname="tests.test_core" name="test_bad"><failure/></testcase>
      <testcase classname="tests.test_core" name="test_setup"><error/></testcase>
      <testcase classname="tests.test_core" name="test_skip"><skipped/></testcase>
    </testsuite></testsuites>''', encoding='utf-8')
    counts, times = runner.read_results(xml)
    assert counts == dict(passed=1, failed=1, errors=1, skipped=1)
    assert times['tests/test_core.py::test_ok[1.5-中文]'] == 1.2


@pytest.mark.parametrize('outcome,expected', [
    ('pass', 0), ('fail', 1), ('crash', 1), ('missing_result', 1),
    ('incomplete', 1), ('timeout', 1),
])
def test_real_child_process_result_and_timeout_propagation(tmp_path, monkeypatch, outcome, expected):
    monkeypatch.setattr(runner, 'ROOT', tmp_path)
    node = 'tests/test_demo.py::test_demo'

    original_run = subprocess.run

    def collect(command, **kwargs):
        if command[0] == 'taskkill':
            return original_run(command, **kwargs)
        assert '--collect-only' in command
        assert kwargs['env']['QT_QPA_PLATFORM'] == 'offscreen'
        assert 'LUMA_SHELL_TEST_REAL_REGISTRY' not in kwargs['env']
        return subprocess.CompletedProcess(command, 0, node + '\n\n1 test collected\n', '')

    monkeypatch.setenv('LUMA_SHELL_TEST_REAL_REGISTRY', '1')
    monkeypatch.setattr(runner.subprocess, 'run', collect)
    original_popen = subprocess.Popen

    def worker(command, **kwargs):
        if command[0] == 'taskkill':
            return original_popen(command, **kwargs)
        argfile = Path(next(arg[1:] for arg in command if arg.startswith('@')))
        assert argfile.read_text(encoding='utf-8').splitlines() == [node]
        xml = next(arg.split('=', 1)[1] for arg in command if arg.startswith('--junitxml='))
        payload = ('<testsuites><testsuite><testcase classname="tests.test_demo" '
                   'name="test_demo" time="0.1">'
                   + ('<failure/>' if outcome == 'fail' else '')
                   + '</testcase></testsuite></testsuites>')
        if outcome == 'incomplete':
            payload = '<testsuites/>'
        script = 'import pathlib,sys,time\n'
        if outcome == 'timeout':
            script += 'time.sleep(30)\n'
        if outcome not in ('crash', 'missing_result', 'timeout'):
            script += f'pathlib.Path({xml!r}).write_text({payload!r})\n'
        script += f'raise SystemExit({1 if outcome in ("fail", "crash") else 0})\n'
        return original_popen([sys.executable, '-c', script], **kwargs)

    monkeypatch.setattr(runner.subprocess, 'Popen', worker)
    assert runner.main(['--jobs', '2', '--timeout', '0.5']) == expected
    report_path = next((tmp_path / 'build/test-runs').glob('run-*/report.json'))
    report = json.loads(report_path.read_text())
    assert report['success'] == (outcome == 'pass')
    assert report['collected'] == 1
    if outcome == 'timeout':
        assert report['workers'][0]['exit_code'] == 124


@pytest.mark.parametrize('code', [2, 5])
def test_failed_or_empty_collection_never_starts_workers(tmp_path, monkeypatch, code):
    monkeypatch.setattr(runner, 'ROOT', tmp_path)
    monkeypatch.setattr(runner.subprocess, 'run', lambda *a, **k:
                        subprocess.CompletedProcess(a[0], code, 'collection failed', ''))
    monkeypatch.setattr(runner.subprocess, 'Popen', lambda *a, **k: pytest.fail('must not start workers'))
    assert runner.main([]) == code


def test_list_only_does_not_execute_or_skip_over_collection_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'ROOT', tmp_path)
    monkeypatch.setattr(runner.subprocess, 'run', lambda *a, **k:
                        subprocess.CompletedProcess(a[0], 0, 'tests/test_a.py::test_a\n', ''))
    monkeypatch.setattr(runner.subprocess, 'Popen', lambda *a, **k: pytest.fail('list only'))
    assert runner.main(['--list']) == 0


def test_module_queue_limits_concurrency_and_never_reuses_a_cached_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'ROOT', tmp_path)
    nodes = [f'tests/test_module_{i}.py::test_case' for i in range(5)]
    monkeypatch.setattr(runner.subprocess, 'run', lambda *a, **k:
                        subprocess.CompletedProcess(a[0], 0, '\n'.join(nodes), ''))
    original_popen = subprocess.Popen
    processes = []
    selected = []

    def worker(command, **kwargs):
        assert sum(process.poll() is None for process in processes) < 2
        argfile = Path(next(arg[1:] for arg in command if arg.startswith('@')))
        group = argfile.read_text(encoding='utf-8').splitlines()
        assert len(group) == 1
        selected.extend(group)
        module = group[0].split('::')[0][:-3].replace('/', '.')
        xml = next(arg.split('=', 1)[1] for arg in command if arg.startswith('--junitxml='))
        payload = f'<testsuites><testsuite><testcase classname="{module}" name="test_case"/></testsuite></testsuites>'
        script = f'import pathlib; pathlib.Path({xml!r}).write_text({payload!r})'
        process = original_popen([sys.executable, '-c', script], **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(runner.subprocess, 'Popen', worker)
    for _ in range(2):
        selected.clear()
        assert runner.main(['--jobs', '2']) == 0
        assert sorted(selected) == nodes
