"""Independent diagnostics may continue, but no failed gate yields a candidate."""
from itertools import product
from pathlib import Path
import re


WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/thumbnail-validation.yml'
REQUIRED = ('thumbnails', 'scaled_ui', 'ui_capture', 'installer_ui')


def test_all_continued_diagnostics_are_covered_by_fail_closed_gate():
    source = WORKFLOW.read_text(encoding='utf-8')
    blocks = source.split('      - ')
    continued = [block for block in blocks if '\n        continue-on-error: true\n' in block]
    ids = {re.search(r'^        id: (\w+)$', block, re.MULTILINE).group(1)
           for block in continued}
    assert ids == set(REQUIRED)
    gate = next(block for block in blocks if block.startswith(
        'name: Require successful Shell and UI validation before accepting candidate\n'))
    condition = next(line.strip()[4:] for line in gate.splitlines() if line.strip().startswith('if: '))
    assert condition == ' || '.join(f"steps.{key}.outcome != 'success'" for key in REQUIRED)
    assert "run: throw 'Required Shell or UI validation failed; candidate artifacts are withheld.'" in gate
    # GitHub's outcome retains the pre-continue-on-error result. A failure,
    # cancellation or skipped required check must all close the final gate.
    for outcomes in product(('success', 'failure', 'cancelled', 'skipped'), repeat=len(REQUIRED)):
        blocks_candidate = any(value != 'success' for value in outcomes)
        assert blocks_candidate == (outcomes != ('success',) * len(REQUIRED))
    gate_index = blocks.index(gate)
    candidate = next(block for block in blocks if 'name: luma-0.1.3-windows-candidate\n' in block)
    assert blocks.index(candidate) > gate_index
    assert 'if:' not in candidate  # default success() cannot upload after a failed gate


def test_scaled_ui_gate_explicitly_uses_native_windows_plugin():
    source = WORKFLOW.read_text(encoding='utf-8')
    block = next(block for block in source.split('      - ') if '\n        id: scaled_ui\n' in block)
    assert '$env:QT_QPA_PLATFORM = "windows"' in block
    assert '$env:QT_SCALE_FACTOR = "2"' in block
    assert 'if ($LASTEXITCODE -ne 0) { throw' in block
