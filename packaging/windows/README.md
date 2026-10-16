# Windows installer

Future Windows releases should ship all four artifacts together:

- `Luma-<version>-windows-x64-setup.exe`: current-user installer
- `Luma-<version>-windows-x64.zip`: portable folder bundle
- `Luma-<version>-dependency-sources.zip`: matching dependency source material
- `SHA256SUMS`: checksums for all three assets

The already-published `v0.1.0` is not modified by this change. The bounded initial-release workflow must not be repurposed to overwrite it. The feature validation workflow builds candidate artifacts without publishing a release.

## Build

Use Windows x64, the checked-in Python build environment and [official Inno Setup 6.3 or newer](https://jrsoftware.org/isdl.php). GitHub-hosted Windows runners normally include the compiler. The build script fails with an actionable message if it is absent; it does not download or install a compiler silently.

```powershell
./scripts/build_windows.ps1
./.venv-build/Scripts/python.exe scripts/prepare_source_material.py --version 0.1.2
./.venv-build/Scripts/python.exe scripts/package_release.py --version 0.1.2
./scripts/build_installer.ps1
# Optional explicit compiler location:
# ./scripts/build_installer.ps1 -IsccPath 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
```

`build_installer.ps1` reads matching versions from `pyproject.toml` and `luma/__init__.py`, rejects development-only/incomplete bundles, checks the payload, compiles the EXE and adds its checksum to `SHA256SUMS` without discarding portable/source ZIP checksums. It does not publish, install, or launch the application. The installer is currently **unsigned**; SHA-256 establishes download integrity, not publisher authentication. Windows SmartScreen may show an unfamiliar-app warning.

## Installation and updates

- Windows 10/11 x64 only. ARM64 Explorer cannot load this x64 shell extension
- Installs for the current Windows account under `%LOCALAPPDATA%\Programs\Luma` by default, with no administrator prompt
- Adds a Start menu entry and an optional, unchecked desktop shortcut
- Setup automatically registers Luma's current-user thumbnail support and Open with entries after installing the payload. There is no thumbnail checkbox. Choose Luma in Windows Default apps to use it as the image viewer; Setup never changes `UserChoice` or extension defaults
- Payloads live in `versions\<version>-<content-hash>`. Upgrading changes the shortcut to the new payload and retains older installed payloads, avoiding in-place replacement of DLLs already loaded by Explorer
- During an upgrade, unchanged registrations owned by a validated payload in this installation move to the new executable/DLL paths. Older payloads remain available while Explorer still holds their DLLs
- An unrelated portable copy's registration or a value changed by another application is never taken over automatically. Registration failure is shown on the final Setup page and in the log, with exit code `10` (also for silent Setup). The installed files are retained so the user can inspect registration in Settings and retry; automatic launch is disabled for that incomplete installation
- Reinstalling identical bytes checks existing payload hashes and only restores missing files. A changed existing file stops setup with instructions, rather than silently overwriting a user-replaced library or claiming it was repaired
- The portable updater and this installer are separate paths. A portable update does not silently take ownership of an installer or its shortcuts

Old version folders remain until uninstall. This deliberately trades disk space for safe shell-DLL upgrades; do not manually remove a folder while it still owns Explorer integration.

## Uninstall

Use Windows Installed apps or the install folder's uninstaller. Before deleting app files, it launches the newest installed executable's `--shell-unregister-if-owned` command. The command validates its installer marker and a bounded set of sibling payloads, then delegates to the registrar's exact-owner, compare-before-restore cleanup. It never runs an old binary or unregisters an unrelated portable copy. Invalid ownership state stops removal. Setup's matching `--shell-register-current-user` command uses the same bounded payload validation, with the registrar checking allowed previous owners under its mutex before recovery or writes.

If a native binary is still loaded, shell integration is disabled where owned, but uninstall stops before deleting application files. Close Luma and Explorer windows and retry; if Windows still holds the DLL, sign out and back in before retrying. The installer never kills/restarts Explorer or requests elevation to delete an in-use DLL. This is intentional for a per-user package: Inno Setup's reboot replacement requires administrative privileges.

Uninstall removes files recorded by the installer and its shortcuts/Installed apps entry. It does not recursively delete the installation folder, images saved there, application settings or portable copies. It does not reset Windows defaults. A later application change to a shell value is preserved by the registrar rather than overwritten with stale state.

## Verification

Portable ownership-boundary tests:

```powershell
python -m unittest discover -s scripts -p test_installer_contract.py
```

On an **ephemeral GitHub-hosted Windows runner only**:

```powershell
./scripts/test_installer.ps1 -Installer artifacts/Luma-0.1.2-windows-x64-setup.exe
```

The smoke script refuses developer desktops and self-hosted runners. It verifies per-user installation, Installed apps registration, launch, shortcut defaults, automatic HDR/Applications-route registration, foreign-provider conflict failure and retry, identical reinstall, rejection of modified payloads, automatic registration migration while the old real DLL is loaded, safe refusal of busy uninstall, cleanup of an old installed version's shell ownership, preservation of images/settings/defaults, and preservation of an unrelated portable registration during both reinstall and uninstall. It builds a second test-only package under the runner's temporary directory to exercise the versioned layout without altering source versions or public releases.

Logs and the pass/fail report are written to `build/installer-smoke/`. Upload that directory with CI diagnostics. A passing lifecycle smoke is not evidence of interactive wizard appearance, SmartScreen behavior, or every Windows/Explorer configuration; manually check a fresh-account install, Windows Default apps selection, real Explorer thumbnail display, upgrade, and uninstall before publishing.

Implementation references: [Inno Setup non-administrative install](https://jrsoftware.org/ishelp/topic_setup_privilegesrequired.htm), [file handling and restart replacement](https://jrsoftware.org/ishelp/topic_filessection.htm), [appending uninstall logs](https://jrsoftware.org/ishelp/topic_appendnotes.htm), [safe uninstall abort points](https://jrsoftware.org/ishelp/topic_isxfunc_abort.htm).
