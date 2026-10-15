# Validation status

This is a dated development checkpoint, not a claim that a Windows release has already passed. The native Windows workflow/run for the released commit is the authoritative Windows build result.

## Cloud Linux checkpoint, 2026-10-15

- 216 application regression tests passed with Qt offscreen: 88 decoder, 88 updater, 29 image-action, 9 UI/worker, and 2 custom-window-chrome tests
- 12 release-tag safety tests passed separately, covering lightweight/annotated tag matching, mismatches, missing tags, invalid chains and idempotent published releases
- Full strict dependency-notice collection passed; upstream texts have exact-source provenance and SHA-256 integrity records
- PyInstaller folder bundle built successfully and passed static dependency/platform-plugin checks
- The frozen executable passed window startup plus PNG, TGA, DDS, Unicode PNG, Radiance HDR, flat EXR and real Nikon NEF decode/render checks; a Unicode NEF launch is also required by the final smoke script
- A dependency-source artifact was generated and inspected; photographic RAW fixtures and Git data are excluded
- Workflow YAML parsed successfully; repository access is read-only in build/test jobs and write permission exists only in the release job
- PowerShell execution and native Windows behavior were not tested by these Linux checks

The running UI screenshot uses a generated synthetic image. It demonstrates the application UI, not a camera photo or color-fidelity comparison.

## Real RAW evidence

Both files below were fully demosaiced using rawpy 0.27.1 / LibRaw 0.22.1, yielding float32 linear RGBA and an SDR render. Source bytes were unchanged.

| Official rawpy sample | Dimensions | SHA-256 |
|---|---:|---|
| `iss030e122639.NEF` | 4284 × 2844 | `5922721d13f11795557d97fdeb0a60b900086c402bc82a848ff280d15b99ffd4` |
| `iss042e297200.NEF` | 4940 × 3292 | `b21e9752aaf8e678fe4d8138986bc9819aa89b7b969a28d43c619af3bd6d58c1` |

Sources: [first rawpy test sample](https://github.com/letmaik/rawpy/blob/main/test/iss030e122639.NEF), [second rawpy test sample](https://github.com/letmaik/rawpy/blob/main/test/iss042e297200.NEF). The temporary local report is generated during development; it is not included as a camera-compatibility guarantee. These two samples do not certify all NEF variants or other camera manufacturers.

The release workflow downloads the first sample only as a temporary input, verifies Git blob `e03d193927a604de40fd0fd7a71e33fde878a0e1`, and checks the packaged Windows executable with both its original name and `相机 测试.NEF`. It does not put the camera file in the released ZIP.

## Native Windows gate

Still pending at this checkpoint. The authorized workflow must pass native Windows tests, full notice collection, folder packaging, static checks and packaged startup/render checks for every required fixture before creating release assets. It also requires matching dependency source material and checksums. Publication verifies that `v0.1.0` resolves to the exact tested commit; an older tag is never moved or silently reused for different binaries.

Manual Windows DPI, file-dialog, drag/drop, fullscreen and update-launch behavior remains a separate acceptance checklist in [BUILDING.md](BUILDING.md). Automated source tests, offscreen tests, frozen Linux tests and native Windows tests are distinct evidence.

- Final custom-window/UI subset also passed with QT_SCALE_FACTOR=2 (6 tests); physical Windows drag/resize, system recycle behavior and update launch still need native validation
