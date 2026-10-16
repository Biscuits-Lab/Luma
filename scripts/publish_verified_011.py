#!/usr/bin/env python3
"""Publish only the checksum-pinned, already-tested Luma 0.1.1 candidate."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

REPO = 'Biscuits-Lab/Luma'
REPO_ID = 1328395764
BRANCH = 'refs/heads/release/publish-v0.1.1'
TAG = 'v0.1.1'
BUILD_SHA = 'ada8d4beceedb7ef336401c80c546f5f3e635f08'
OLD_SHA = '99841e74c180282f742847c0e471eed457999067'
RUN_ID = 38850835580
JOB_ID = 116811456753
ARTIFACT_ID = 12025920146
ARTIFACT_DIGEST = 'sha256:f0624f7aec1974a9ac509f7f4934da23d0872e1e500654ad7f85880914945aa1'
ASSETS = {
    'Luma-0.1.1-windows-x64-setup.exe': (34530947, '096fe5c67ec327b5add3581770d5f0baf5d0f330722a4c4d1a8e1a46b09a79b3'),
    'Luma-0.1.1-windows-x64.zip': (49277737, 'c3f9e482bf0cf6f46b85f17372decc3df2d561a457f77961dcaac80581aed612'),
    'Luma-0.1.1-dependency-sources.zip': (90827176, 'f9ae5739d9986de92ea1522cc3a4740c4cc69f09c75d374211371b8cdcf618d4'),
    'SHA256SUMS': (292, 'd22416b9a8621f5ac3908fcad8ba0455b19ef67db1da99f957660f7882d6798e'),
}
AUXILIARY = {'WINDOWS-SMOKE-REPORT.json': (1539, 'a3b8b0c7ff922570b0952f78b8a60a4299ef75a38b1e788be5eafb251db9a5ce')}
REQUIRED_STEPS = {
    'Build native bridge, test viewer, create folder bundle',
    'Verify installer and release-safety contracts',
    'Verify loaded-image drop regression sensitivity',
    'Smoke real bundled UI and decoders',
    'Exercise real isolated Shell thumbnail surrogate',
    'Collect dependency sources', 'Package candidate ZIP and checksums',
    'Build current-user Windows installer',
    'Verify installer lifecycle on disposable runner',
}
NOTES = '''## Luma 0.1.1

Windows x64 image viewer with a current-user installer, optional Explorer thumbnails, and a fix for dropping a new image into an already-loaded window.

### Downloads and installation
- **Luma-0.1.1-windows-x64-setup.exe**: recommended installer. Installs for the current Windows account without administrator access. Start Luma from the Start menu. Optional desktop shortcut; clean uninstall preserves user images and settings.
- **Luma-0.1.1-windows-x64.zip**: portable. Extract the complete Luma folder and run Luma.exe; keep _internal beside it. No separate Python installation is required.
- **Luma-0.1.1-dependency-sources.zip**: matching redistributable dependency source material. License notices and replacement guidance are included in the application bundle.
- **SHA256SUMS**: SHA-256 checksums for the installer and both ZIPs.

### Enable Explorer thumbnails
1. Open Luma, click the top-left ellipsis, then Settings.
2. Enable **Explorer thumbnails** and confirm the current-user registration.
3. Click **Windows Default Apps** and select Luma for the file types you want.
4. Use a thumbnail-capable view in normal, non-elevated Explorer. Existing common-image providers are preserved; Windows manages cached thumbnails.

Choosing a default viewer alone does not register a thumbnail provider. Installation and opening files do not enable integration automatically. Turn it off in Settings to remove unchanged Luma thumbnail registrations while preserving default-viewer choices and other applications' later changes. For a portable copy, disable integration before moving or removing its folder. After updating or switching copies, explicitly choose **Register this copy / repair** if needed. Installer upgrades retain older version folders while their DLLs may still be loaded; a busy uninstall asks for a retry instead of terminating Explorer.

### Changes
- Isolated native Shell thumbnail bridge with bounded decoder processes for Radiance HDR, flat EXR and supported camera RAW variants, plus raster handling where appropriate.
- RAW thumbnails prefer embedded JPEG/bitmap previews, with bounded half-size LibRaw fallback; the main viewer still develops RAW image data.
- Correct 16-bit TIFF orientation and source-file preservation.
- Loaded-image drag/drop works through the actual image viewport, including fullscreen and repeated folder drops.
- Current-user installer, same-version integrity checks, side-by-side upgrades and guarded uninstall.

### Verification and limitations
The exact files below come from [successful Windows validation run 38850835580](https://github.com/Biscuits-Lab/Luma/actions/runs/38850835580), source commit ada8d4beceedb7ef336401c80c546f5f3e635f08. They are published without rebuilding. Validation passed 432 application tests, 24 packaging/installer contract tests, three native suites, original-failure/patched-pass drag/drop checks, nine packaged viewer cases, actual required-surrogate Shell extraction and all five installer lifecycle groups.

The installer, executable and Shell DLL are **unsigned**; Windows may display a reputation warning. Automated tests ran on a GitHub-hosted Windows runner. Fresh-account interactive installer/Explorer visual checks and a complete Windows 10/11 or DPI matrix have **not** been performed. The tests include one real Nikon NEF and Unicode paths; they do not certify every camera, compression variant or file. The optional Shell integration is new and can be disabled in Settings.

HDR/EXR previews and thumbnails are SDR tone-mapped, not native HDR-monitor output. Deep EXR, general AOV/compositing, animation and all pages/frames are outside this static viewer's scope. Thumbnail limits are tighter than interactive viewing (256 MiB input, 64 MP raster/sensor, 16 MP float/HDR/EXR, bounded memory and decoder time); failures fall back to the file icon. Existing Windows/third-party thumbnail providers are not replaced.

The built-in updater remains a portable-bundle update path. For an installed copy, use the new setup executable to preserve installer-owned shortcuts and uninstall lifecycle. Photographic RAW test samples are not redistributed. Luma v0.1.0 remains unchanged.
'''


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def api(path, method='GET', payload=None, missing=False):
    require(path.startswith(f'repos/{REPO}'), 'Unexpected API destination')
    headers = {'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
               'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
               'User-Agent': 'Luma-verified-0.1.1-publisher'}
    body = None if payload is None else json.dumps(payload).encode('utf-8')
    if body is not None:
        headers['Content-Type'] = 'application/json'
    try:
        with urlopen(Request('https://api.github.com/' + path, data=body, headers=headers, method=method), timeout=60) as response:
            return json.load(response)
    except HTTPError as error:
        if missing and error.code == 404 and method == 'GET':
            return None
        raise


def validate_tag(item, tag=TAG, sha=BUILD_SHA):
    require(item and item.get('ref') == 'refs/tags/' + tag, 'Wrong release tag')
    require(item.get('object', {}).get('sha') == sha and item['object'].get('type') == 'commit',
            'Release tag does not match the tested commit')


def validate_build(run, job, artifact):
    require(run.get('id') == RUN_ID and run.get('head_sha') == BUILD_SHA
            and run.get('head_branch') == 'feature/explorer-thumbnails'
            and run.get('event') == 'push' and run.get('status') == 'completed'
            and run.get('conclusion') == 'success'
            and run.get('path') == '.github/workflows/thumbnail-validation.yml', 'Wrong or unsuccessful source build')
    for key in ('repository', 'head_repository'):
        require(run.get(key, {}).get('id') == REPO_ID and run[key].get('full_name') == REPO, 'Wrong source repository')
    require(job.get('id') == JOB_ID and job.get('run_id') == RUN_ID
            and job.get('conclusion') == 'success', 'Required Windows job did not succeed')
    succeeded = {s['name'] for s in job.get('steps', []) if s.get('conclusion') == 'success'}
    require(REQUIRED_STEPS <= succeeded, 'Required Windows checks did not succeed')
    require(artifact.get('id') == ARTIFACT_ID and artifact.get('name') == 'luma-0.1.1-windows-candidate'
            and artifact.get('expired') is False and artifact.get('size_in_bytes') == 174638425
            and artifact.get('digest') == ARTIFACT_DIGEST, 'Wrong or expired candidate artifact')
    owner = artifact.get('workflow_run', {})
    require(owner.get('id') == RUN_ID and owner.get('head_sha') == BUILD_SHA
            and owner.get('repository_id') == REPO_ID and owner.get('head_repository_id') == REPO_ID,
            'Artifact provenance mismatch')


def file_digest(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def validate_files(directory):
    expected_files = ASSETS | AUXILIARY
    require({p.name for p in directory.iterdir()} == set(expected_files), 'Unexpected artifact file set')
    for name, (size, expected) in expected_files.items():
        path = directory / name
        require(path.is_file() and not path.is_symlink() and path.stat().st_size == size, 'Artifact size/type mismatch: ' + name)
        require(file_digest(path) == expected, 'Artifact SHA-256 mismatch: ' + name)
    lines = (directory / 'SHA256SUMS').read_text(encoding='utf-8').splitlines()
    manifest = {}
    for line in lines:
        digest, name = line.split('  ', 1)
        require(name not in manifest, 'Duplicate checksum entry')
        manifest[name] = digest
    require(manifest == {name: value[1] for name, value in ASSETS.items() if name != 'SHA256SUMS'}, 'Wrong checksum manifest')


def validate_release(item, complete=True):
    require(item.get('tag_name') == TAG and item.get('target_commitish') == BUILD_SHA
            and item.get('name') == 'Luma 0.1.1' and item.get('body') == NOTES
            and item.get('prerelease') is False and isinstance(item.get('draft'), bool), 'Release identity/content mismatch')
    assets = item.get('assets', [])
    names = [a.get('name') for a in assets]
    require(len(names) == len(set(names)) and set(names) <= set(ASSETS), 'Unexpected release assets')
    if complete:
        require(set(names) == set(ASSETS), 'Release assets are incomplete')
    for asset in assets:
        size, digest = ASSETS[asset['name']]
        require(asset.get('size') == size and asset.get('digest') == 'sha256:' + digest
                and asset.get('state') == 'uploaded', 'Release asset integrity mismatch: ' + asset['name'])


def upload_asset(release_id, path):
    require(path.name in ASSETS, 'Unexpected upload filename')
    url = f'https://uploads.github.com/repos/{REPO}/releases/{release_id}/assets?name={quote(path.name)}'
    headers = {'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
               'Accept': 'application/vnd.github+json', 'Content-Type': 'application/octet-stream',
               'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'Luma-verified-0.1.1-publisher'}
    with urlopen(Request(url, data=path.read_bytes(), headers=headers, method='POST'), timeout=180) as response:
        json.load(response)
    print('Uploaded exact candidate asset: ' + path.name, flush=True)


def verify_public_downloads():
    for name, (size, expected) in ASSETS.items():
        # No Authorization header: verify the URL a signed-out user receives.
        request = Request(f'https://github.com/{REPO}/releases/download/{TAG}/{name}',
                          headers={'User-Agent': 'Luma-anonymous-download-verification'})
        count, digest = 0, hashlib.sha256()
        with urlopen(request, timeout=90) as response:
            for block in iter(lambda: response.read(1024 * 1024), b''):
                count += len(block)
                require(count <= size, 'Public download exceeds pinned size')
                digest.update(block)
        require(count == size and digest.hexdigest() == expected, 'Public download checksum mismatch: ' + name)
        print(f'Anonymous download verified: {name}; {size} bytes; sha256:{expected}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts', type=Path, default=Path('artifacts'))
    args = parser.parse_args()
    require(os.environ.get('GITHUB_REPOSITORY') == REPO and os.environ.get('GITHUB_REF') == BRANCH,
            'Publisher is restricted to the reviewed repository and branch')
    repo = api(f'repos/{REPO}')
    require(repo.get('id') == REPO_ID and repo.get('private') is False, 'Wrong public repository')
    validate_build(api(f'repos/{REPO}/actions/runs/{RUN_ID}'), api(f'repos/{REPO}/actions/jobs/{JOB_ID}'),
                   api(f'repos/{REPO}/actions/artifacts/{ARTIFACT_ID}'))
    validate_files(args.artifacts)
    validate_tag(api(f'repos/{REPO}/git/ref/tags/v0.1.0'), 'v0.1.0', OLD_SHA)
    original_main = api(f'repos/{REPO}/git/ref/heads/main')['object']['sha']
    # Never move existing tags or replace/delete existing assets.
    tag = api(f'repos/{REPO}/git/ref/tags/{TAG}', missing=True)
    if tag is None:
        tag = api(f'repos/{REPO}/git/refs', 'POST', {'ref': 'refs/tags/' + TAG, 'sha': BUILD_SHA})
    validate_tag(tag)
    releases = api(f'repos/{REPO}/releases?per_page=100')
    matching = [r for r in releases if r.get('tag_name') == TAG]
    require(len(matching) <= 1, 'Multiple releases for the pinned tag')
    if matching:
        release = matching[0]
        validate_release(release, complete=not release['draft'])
    else:
        release = api(f'repos/{REPO}/releases', 'POST', {
            'tag_name': TAG, 'target_commitish': BUILD_SHA, 'name': 'Luma 0.1.1',
            'body': NOTES, 'draft': True, 'prerelease': False, 'generate_release_notes': False,
        })
        validate_release(release, complete=False)
    release_id = release['id']
    if release['draft']:
        names = {a['name'] for a in release['assets']}
        for name in ASSETS:
            if name not in names:
                upload_asset(release_id, args.artifacts / name)
        validate_release(api(f'repos/{REPO}/releases/{release_id}'))
        validate_tag(api(f'repos/{REPO}/git/ref/tags/{TAG}'))
        api(f'repos/{REPO}/releases/{release_id}', 'PATCH', {'draft': False, 'make_latest': 'true'})
    published = api(f'repos/{REPO}/releases/tags/{TAG}')
    validate_release(published)
    require(published['id'] == release_id and published['draft'] is False, 'Release is not public')
    validate_tag(api(f'repos/{REPO}/git/ref/tags/{TAG}'))
    validate_tag(api(f'repos/{REPO}/git/ref/tags/v0.1.0'), 'v0.1.0', OLD_SHA)
    require(api(f'repos/{REPO}/git/ref/heads/main')['object']['sha'] == original_main, 'Main changed during publication')
    verify_public_downloads()
    print(f'Verified public release: https://github.com/{REPO}/releases/tag/{TAG}', flush=True)


if __name__ == '__main__':
    main()
