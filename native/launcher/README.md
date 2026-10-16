# Stable installed Luma entry

The installed root contains a real native `Luma.exe` with Luma's existing icon,
version information, and an as-invoker manifest. It is a small static-runtime
Windows x64 launcher. The viewer and DLLs stay immutable under
`versions/<version>-<bundle-sha256-first-24>/`. Portable ZIPs keep their existing
folder executable and do not need this launcher.

## Protocol 1

All protocol files are bounded ASCII, without a BOM, using exactly LF line
endings and a final LF. The installer writes a temporary file in the same root,
flushes and atomically replaces the pointer. It never truncates the live pointer.

`active-payload.txt` has exactly four lines (at most 512 bytes):

1. `LUMA-ACTIVE-1`
2. `<version>-<bundle-sha256-first-24>`
3. `<full-lowercase-bundle-sha256>`
4. `<lowercase-payload-Luma.exe-sha256>`

`launcher-owner.txt` has exactly two lines:

1. `LUMA-LAUNCHER-1`
2. `<lowercase-root-Luma.exe-sha256>`

The existing payload `luma-install.json` must have exactly the five generated
fields: `managed_install: true`, `layout: "versions-v1"`, `version`, `payload_id`,
and `bundle_sha256`. The JSON reader permits whitespace and arbitrary key order,
but no duplicate/unknown fields, escapes, nested values, BOM or trailing data.
All generated values are plain ASCII. The payload ID and version must match the
pointer exactly. The three numeric version components have no leading zeros.

An owner hash validates installation consistency; it does not authenticate a
publisher against an attacker who can replace the entire user's installation.
The existing release download/checksum/installer authorization boundary remains
responsible for selecting the trusted application. The launcher hashes only the
root launcher and payload executable; it does not claim to verify every runtime
DLL on every launch. DLLs remain external and replaceable per the project policy.

## Startup and errors

The launcher reads its own module path, accepts only local DOS-drive paths, and
pins every ancestor with handles that deny rename/deletion. It opens the pointer,
owner, metadata and executable without following reparse points, rejects file
hardlinks, checks both executable SHA-256 hashes, and validates metadata. It reads
one complete pointer snapshot; concurrent activation may select the complete old
or complete new version, never an arbitrary sibling or a partial pointer.

Arguments are parsed with `CommandLineToArgvW` and escaped individually using the
Windows backslash/quote rules, including empty/Unicode arguments and hidden
startup-verification arguments. No argument can override the selected payload.
`CreateProcessW` receives a non-null absolute application path, a bounded mutable
command line, no inherited handles, and the exact payload directory as cwd. The
launcher does not call a shell, search PATH, execute commands stored in files,
or mutate the installation. It keeps its direct child process handle and waits
for the child, returning its exit status. This keeps the exact root process alive
while the updater verifies the child's nonce/version/PID, OS process image,
parent PID and creation time. The root launcher itself is never proof that the
viewer reached a working GUI.

Missing/invalid pointers, metadata, hashes, paths or executables produce a visible
error dialog and exit 120. There is no search for another payload and no silent
fallback to an older version. Existing payloads/DLLs are not changed or deleted.

## Installer responsibilities

The installer owns payload staging, journaled registration, pointer publication,
rollback, shortcuts, file associations and uninstall ownership. It validates the
existing launcher-owner file and bytes before preserving a compatible root entry.
An unrecognized root executable must fail safely without overwrite. New installs
stage the root launcher and exact owner file. Normal compatible viewer upgrades
preserve the protocol-1 launcher byte-for-byte, including while it is running.

Protocol-1 resources intentionally use launcher ABI `1.0.0.0`, independent of the
selected viewer version. A future launcher fix or protocol migration requires an
explicit safe replacement policy: verify ownership, require the old root image
to be unloaded, and transactionally replace it or report a close/retry blocker.
Normal app updates must not silently overwrite a mapped root executable.

## Verification

Run `scripts/build_launcher.ps1` on Windows x64 with MSVC and the project's build
Python environment. It builds the actual launcher and synthetic test-only payload,
then runs both CTest suites. `-SkipTests` is diagnostic only and is not evidence.

- `launcher_contract`: strict parsing, metadata consistency and quoting units
- `launcher_windows`: actual root launches, argv round trips, direct child OS
  image/parent/creation-time evidence, fixed cwd, same stable entry after activation,
  concurrent atomic pointer replacements, retained running old payload, visible
  failure dialogs, corrupted/missing pointers, changed executable/metadata/owner,
  hardlink rejection and directory-junction rejection

The Windows suite writes `build/launcher/launcher-native-report.json` even on
failure. Upload that report and CTest logs as candidate diagnostics. The fixture
helper must never be packaged with the application. Installer lifecycle and actual
frozen GUI update verification are separate required gates.

The portable contract can also be compiled with a C++17 compiler or CMake
`-DLUMA_CONTRACT_ONLY=ON`. A Linux parser pass is not native Windows evidence.

Primary API references:
- https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-createprocessw
- https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew
- https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-commandlinetoargvw
