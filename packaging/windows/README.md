# Windows installer

Future Windows releases should ship all four artifacts together:

- `Luma-<version>-windows-x64-setup.exe`: current-user installer
- `Luma-<version>-windows-x64.zip`: portable folder bundle
- `Luma-<version>-dependency-sources.zip`: matching dependency source material
- `SHA256SUMS`: checksums for all three assets

The already-published `v0.1.0` is not modified by this change. The bounded initial-release workflow must not be repurposed to overwrite it. The main validation workflow builds candidate artifacts without publishing a release.

## Build

Use Windows x64, the checked-in Python build environment and [official Inno Setup 6.7 or newer](https://jrsoftware.org/isdl.php). GitHub-hosted Windows runners normally include the compiler. The build script fails with an actionable message if it is absent; it does not download or install a compiler silently.

```powershell
./scripts/build_windows.ps1
./.venv-build/Scripts/python.exe scripts/prepare_source_material.py --version 0.1.4
./.venv-build/Scripts/python.exe scripts/package_release.py --version 0.1.4
./scripts/build_installer.ps1
# Optional explicit compiler location:
# ./scripts/build_installer.ps1 -IsccPath 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
```

`build_installer.ps1` reads matching versions from `pyproject.toml` and `luma/__init__.py`, rejects development-only/incomplete bundles, checks the payload, builds/tests the native stable launcher, compiles the EXE and adds its checksum to `SHA256SUMS` without discarding portable/source ZIP checksums. It does not publish, install, or launch the application. The installer is currently **unsigned**; SHA-256 establishes download integrity, not publisher authentication. Windows SmartScreen may show an unfamiliar-app warning.

## Installation and updates

- Windows 10/11 x64 only. ARM64 Explorer cannot load this x64 shell extension
- Installs for the current Windows account under `%LOCALAPPDATA%\Programs\Luma` by default, with no administrator prompt
- Adds a Start menu entry and an optional, unchecked desktop shortcut
- Setup automatically registers Luma's current-user thumbnail support and Open with entries after installing the payload. There is no thumbnail checkbox. Choose Luma in Windows Default apps to use it as the image viewer; Setup never changes `UserChoice` or extension defaults
- The first installation level contains a real native `Luma.exe`. Start menu/desktop shortcuts, Open with commands and managed-update restarts use this stable entry. Payloads live in `versions\<version>-<content-hash>`; an atomically published, validated active pointer selects the current payload. Older payloads remain untouched, avoiding in-place replacement of DLLs already loaded by Explorer
- During an upgrade, unchanged registrations owned by a validated payload in this installation move to the new DLL paths while the viewer command stays at the root entry. The compatible protocol-v1 launcher is retained byte-for-byte, including while running. Its child remains a separate, version-verified viewer process; a zero launcher exit is not proof of a successful update
- An unrelated portable copy's registration or a value changed by another application is never taken over automatically. If strict registration fails during interactive Setup, the packaged helper can offer an explicit default-No migration confirmation showing the exact previous Luma owner path. Only a fully validated, intact single-owner journal is eligible; declining or unsafe/changed ownership remains a failure. Silent Setup and in-app managed updates never show this prompt or implicitly authorize migration. Registration failure is shown on the final Setup page and in the log, with exit code `10` (also for silent Setup). The installed files are retained so the user can close Luma and rerun Setup for this installation. Persistent failures should be checked in the Setup log; retrying does not override another copy's ownership or changed registrations. Automatic launch is disabled for that incomplete installation
- Reinstalling identical bytes checks existing payload hashes and only restores missing files. A changed existing file stops setup with instructions, rather than silently overwriting a user-replaced library or claiming it was repaired
- Managed updates apply the verified Setup to the same validated root, then verify the actual frozen child version/path before closing the old viewer. Portable copies keep the ZIP update path and never silently take over managed shortcuts
- Unknown or modified root launchers are rejected before payload extraction. The exact launcher-owner hash and bounded pointer/metadata formats are documented in [native/launcher](../../native/launcher/README.md). Invalid state fails visibly instead of silently launching an older version

Old version folders remain until uninstall. This deliberately trades disk space for safe shell-DLL upgrades; do not manually remove a folder while it still owns Explorer integration.

## Uninstall

Use Windows Installed apps or the install folder's uninstaller. Before deleting app files, it launches the newest installed executable's `--shell-unregister-if-owned` command. The command validates its installer marker and a bounded set of sibling payloads, then delegates to the registrar's exact-owner, compare-before-restore cleanup. It never runs an old binary or unregisters an unrelated portable copy. Invalid ownership state stops removal. Setup's matching `--shell-register-current-user` command uses the same bounded payload validation, with the registrar checking allowed previous owners under its mutex before recovery or writes.

If the root launcher or a payload native binary is still loaded, shell integration is disabled where owned, but uninstall stops before deleting application files. Exclusive native probes log the path and actual Win32 failure; only sharing/lock violations (32/33) get one total bounded retry budget across the entire scan, accommodating transient post-helper contention. Access denial and persistent locks still stop removal. Close Luma and Explorer windows and retry; if Windows still holds the DLL, sign out and back in before retrying. The installer never kills/restarts Explorer or requests elevation to delete an in-use DLL. This is intentional for a per-user package: Inno Setup's reboot replacement requires administrative privileges.

The active pointer is preserved on a busy abort. After the first root/payload busy scan passes, `--finalize-managed-uninstall` validates ownership and writes a bounded ready receipt without deleting the pointer. The uninstaller then performs the retained-handle commit below. A modified or ambiguous state aborts deletion.

Uninstall first validates the managed receipt and unregisters only owned shell values. After the final helper exits, it holds the same root mutex and retains read/write binary handles that allow deletion only. Both scans share one bounded ten-second retry budget, only for native sharing/lock errors; other errors fail immediately. This blocks new root/payload launches during the final scan and file removal. The hash-bound ready receipt, pending receipt, and active pointer are removed in that order; the usable pointer is last. Busy checks before that commit preserve it. Failed earlier protocol deletion remains reinstall-repairable. The guards are released on abort or uninstaller exit; empty directories may remain, and are never recursively purged.

Uninstall removes files recorded by the installer and its shortcuts/Installed apps entry. It does not recursively delete the installation folder, images saved there, application settings or portable copies. It does not reset Windows defaults. A later application change to a shell value is preserved by the registrar rather than overwritten with stale state.

## Verification

Portable ownership-boundary tests:

```powershell
python -m unittest discover -s scripts -p test_installer_contract.py
```

On an **ephemeral GitHub-hosted Windows runner only**:

```powershell
./scripts/test_installer.ps1 -Installer artifacts/Luma-0.1.4-windows-x64-setup.exe
```

The smoke script refuses developer desktops and self-hosted runners. It verifies rejection of an unowned root executable, per-user installation, Installed apps registration, stable-root launch, active-pointer selection, compatible launcher retention, shortcut defaults, automatic HDR/Applications-route registration, foreign-provider conflict failure and retry, identical reinstall, rejection of modified payloads, automatic registration migration while the old real DLL is loaded, safe refusal of running-root or loaded-DLL uninstall with pointer preservation, cleanup of an old installed version's shell ownership, preservation of images/settings/defaults, and preservation of an unrelated portable registration during both reinstall and uninstall. It builds a second test-only package under the runner's temporary directory to exercise the versioned layout without altering source versions or public releases.

Logs and the pass/fail report are written to `build/installer-smoke/`. Upload that directory with CI diagnostics. A passing lifecycle smoke is not evidence of interactive wizard appearance, SmartScreen behavior, or every Windows/Explorer configuration; manually check a fresh-account install, Windows Default apps selection, real Explorer thumbnail display, upgrade, and uninstall before publishing.

For explicitly requested local visual validation, `scripts/smoke_installer_ui.py
--local-preview --installer <setup.exe>` opens only the configuration page,
checks the real controls and directory chooser, then cancels. A private job owns
every installer process, actions never click Install/Next, and destinations and
reports stay under `build/installer-ui-local/`. This opt-in preview does not run
installation, uninstall, registration migration or the lifecycle smoke. Without
the flag, the disposable hosted-runner guard remains in force.

Implementation references: [Inno Setup non-administrative install](https://jrsoftware.org/ishelp/topic_setup_privilegesrequired.htm), [file handling and restart replacement](https://jrsoftware.org/ishelp/topic_filessection.htm), [appending uninstall logs](https://jrsoftware.org/ishelp/topic_appendnotes.htm), [safe uninstall abort points](https://jrsoftware.org/ishelp/topic_isxfunc_abort.htm).

## System display language

Setup chooses Simplified Chinese, Traditional Chinese or English from the current Windows **UI/display language**, not the regional date/number locale. English is the fallback. There is no additional language-selection dialog, and `UsePreviousLanguage=no` prevents an earlier English installation from overriding a newly selected Chinese Windows UI language. Chinese sublanguages are matched by Inno's documented Chinese-specific primary/sublanguage rules. The pinned [native matching implementation](https://github.com/jrsoftware/issrc/blob/cfdf48923178df4b4f040e038b423aa555a61ffc/Projects/Src/SetupLdrAndSetup.InstFunc.pas#L330-L339) groups Singapore (`$1004`) with Simplified Chinese (`$0804`, code page 936), and Hong Kong/Macao (`$0C04`/`$1404`) with Traditional Chinese (`$0404`, code page 950); it excludes the opposite group during fallback. No duplicate language entries are required. Automation may explicitly use `/LANG=en`, `/LANG=zhCN` or `/LANG=zhTW`.

The original Chinese catalogs are community translations distributed in the official Inno source tree, pinned to the signed `is-6_7_1` tag (commit `cfdf48923178df4b4f040e038b423aa555a61ffc`). Their source URLs, exact byte hashes and upstream license are under `languages/`. The Simplified catalog declares Inno 6.5.0+ support, and the custom background plus hidebevels modifiers require Inno 6.7.0+, so the compiler floor is 6.7.0, not an exact-version lock; later compilers remain allowed. Windows CI currently uses 6.7.1. Recheck native message coverage when upgrading that tested compiler version.

`ChineseTraditionalOverrides.isl` is a separately marked Luma addition for 23 native messages missing from the older upstream Traditional catalog and two legacy placeholder corrections. Original upstream files and their notices remain byte-for-byte unchanged. `Reference-Default.isl` is a test-only reference, not used by the installer compiler. Both Chinese catalogs cover all 281 native message keys of the pinned Inno reference. `LumaMessages.iss` translates all custom ownership, registration, recovery and busy-uninstall warnings; paths and identifiers are not translated. Technical helper-result logging remains stable for diagnostics.

Inno's uninstaller retains the language selected by the installation that generated it. A subsequent Setup run re-evaluates the current Windows UI language. The application independently follows its current Windows display language at launch.

## Native visual design

The reviewed compact design is implemented with Inno's built-in dark style and real native controls. The body is `#222222`, the footer `#191919`; the footer uses 60 font-scaled design units with 12 units above and below its 36-unit buttons. Primary width remains 112 units, secondary width 82. `ScaleX`/`ScaleY` honor Inno's language/font/DPI metrics; they are not fixed physical pixels. The Luma PNG uses native transparent painting. The custom layout replaces the native caption with a draggable top strip and accessible minimize/close buttons. Native controls retain focus, keyboard navigation, the directory chooser and cancellation dialogs. High Contrast and `/NOSTYLE` retain the stock wizard chrome. There is no external style DLL or replacement installation engine.

The first page combines the native directory edit with an accessible, keyboard-focusable folder-icon button and a desktop-shortcut checkbox. The native task engine still owns task persistence and silent `/TASKS` behavior; only an explicit checkbox change overrides its result. Progress uses the real native progress/status controls. Its six-unit gray track has rounded outer corners; the native moving fill and accessible percentage remain engine-owned. No simulated timer or overlay is used. Per-control theme/color/region changes are skipped in High Contrast and `/NOSTYLE`; cosmetic failure retains a usable native fallback. Completion keeps the engine's optional, unchecked Launch action. Registration failures retain the full error dialog and a scrollable final-page error, rather than clipping error text to the compact success layout. High Contrast and `/NOSTYLE` retain Inno's stock accessible layout. The footer contains no current-user or unsigned-package explanatory labels; installation permissions, actual signing state and Windows security prompts are unchanged.

Source contracts check catalog hashes, native-message coverage, placeholders, layout dimensions and lifecycle invariants. On an ephemeral GitHub-hosted Windows runner, `scripts/smoke_installer_ui.py --installer artifacts/Luma-0.1.4-windows-x64-setup.exe` opens the actual wizard in all three languages, checks actual parent bounds, translated accessible names and transparent logo corners, opens and cancels the native directory chooser, captures screenshots and cancels before installation. Reports are stored in `build/installer-ui/`. An optional separate build with `build_installer.ps1 -UiFontScaleTest -OutputDir build/installer-font-test` raises the dialog font to 18 points for a native font-metric scaling stress check; pass that EXE as `--scaled-installer`. It is not a claim of changing or testing the monitor's real 200% DPI setting, and that build must never replace a release asset. Interactive fresh-account, High Contrast and monitor-DPI checks remain valuable beyond the automated gate.

The lifecycle smoke separately observes its first real successful `/SILENT` installation, captures an intermediate progress frame, queries the owned native progress range/value, and verifies the MSAA ProgressBar role and accessible value. Evidence is `build/installer-smoke/native-progress.json` and `.png`. This observation reuses the lifecycle’s isolated destination and cleanup; it never fabricates or writes progress values.

## Historical updater reproduction

`python scripts/smoke_update_legacy.py` is a one-off, ephemeral Windows diagnostic of the unmodified public 0.1.2 GUI checking, downloading and launching exact public 0.1.3. It uses built-in Windows UI Automation, records visible embedded version plus actual process/runtime-module paths, and relaunches the target resolved from the retained Start-menu shortcut. No substitute updater module or fake acknowledgement is injected. It deliberately refuses if latest is no longer release 415136172 / v0.1.3. Reports go to `build/update-legacy-012-013/`; this latest-dependent historical probe is separate from the permanent managed-update candidate gate.
