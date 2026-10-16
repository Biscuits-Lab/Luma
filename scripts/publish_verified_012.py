#!/usr/bin/env python3
"""Publish only the checksum-pinned, already-tested Luma 0.1.2 candidate."""
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
BRANCH = 'refs/heads/release/publish-v0.1.2'
TAG = 'v0.1.2'
BUILD_SHA = 'f861e0574096d76ef6534571d1a6dd6c46d7a143'
OLD_SHA = '99841e74c180282f742847c0e471eed457999067'
PREVIOUS_SHA = 'ada8d4beceedb7ef336401c80c546f5f3e635f08'
RUN_ID = 38865585679
JOB_ID = 116857541219
ARTIFACT_ID = 12031232250
ARTIFACT_DIGEST = 'sha256:b7043513e599ac2a6b4b79c94c3699daeeddb125367608d7702a20e5560ee2a1'
ASSETS = {
    'Luma-0.1.2-windows-x64-setup.exe': (34552184, 'ce6902745fbc914663e6000f586798ee031f4e46b29133ffc2fe95e90cf547d6'),
    'Luma-0.1.2-windows-x64.zip': (49298795, '4a8b4b32e71a51bcd0ed2af7defe71bf5b98c869484b251cf89dbe78d585f665'),
    'Luma-0.1.2-dependency-sources.zip': (90827309, '7133d52a9956600c890cc55c27e69a3e523fd9d24d02d1f017e425faea7c9406'),
    'SHA256SUMS': (292, 'b33738007f6b79c26f2beec84a8b64c1203469041f23d9cbccd0e7f13c509a37'),
}
AUXILIARY = {'WINDOWS-SMOKE-REPORT.json': (1539, 'e3e160f6f32ceedc6421c3c69a55a06b19bed27f74fe1be3737aecfee58397a0')}
REQUIRED_STEPS = {
    'Build native bridge, test viewer, create folder bundle',
    'Verify installer and release-safety contracts',
    'Verify relative zoom at 200 percent scaling',
    'Verify loaded-image drop regression sensitivity',
    'Smoke real bundled UI and decoders',
    'Exercise real isolated Shell thumbnail surrogate',
    'Collect dependency sources', 'Package candidate ZIP and checksums',
    'Build current-user Windows installer',
    'Verify installer lifecycle on disposable runner',
}
NOTES = '## Luma 0.1.2\n\n### 更新内容\n- 加速 HDR 解码、图像切换和缓存；隐藏缩略图栏时不再执行多余布局工作，预加载和内存有明确上限。\n- 缩放以“适应窗口”为 100%，每张新图片默认适应；保留独立的“Actual pixels (1:1)”操作。稳定底部工具栏和缩放显示。\n- 安装时自动准备资源管理器缩略图及“打开方式”注册，不再需要额外勾选。支持有界的 8K/16K HDR 缩略图，修复应用程序关联路径及旧安装路径迁移。\n- 添加 Windows 11 原生圆角请求；最大化/全屏遵循系统行为。\n\n### 下载与使用\n- **Luma-0.1.2-windows-x64-setup.exe**：推荐安装包，当前用户安装，无需管理员权限。已安装的用户也请运行此安装包升级，以保留快捷方式和卸载管理。\n- **Luma-0.1.2-windows-x64.zip**：免安装版，完整解压 Luma 文件夹后运行 Luma.exe，保留 _internal。首次普通启动会安全准备当前用户注册；与其他副本冲突时会提示，不会强行接管。\n- **Luma-0.1.2-dependency-sources.zip**：对应的第三方依赖源代码材料；应用包内包含许可证与替换说明。\n- **SHA256SUMS**：安装包和两个 ZIP 的 SHA-256 校验值。\n\n安装或首次运行后，在 Windows“打开方式”或“默认应用”中自行选择 Luma。缩略图不需要另开一个选项；Windows 的缩略图视图、缓存、文件变体及资源限制仍会影响显示。移动免安装目录或更换副本后，可在 Settings 中检查状态或显式修复。不会修改 UserChoice、强制默认程序、清空缩略图缓存或替换其他应用的注册。\n\n### 已验证\n这些文件直接来自 [Windows 验证运行 38865585679](https://github.com/Biscuits-Lab/Luma/actions/runs/38865585679)，源提交 f861e0574096d76ef6534571d1a6dd6c46d7a143，发布时没有重新构建。\n\n854 项 Windows 测试、28 项打包检查、31 项高 DPI 检查、3 组原生测试、9 个打包程序用例、41 个 Shell 命令及 8 组安装/升级/卸载场景通过。Windows 原生 API 在安装和升级后均能为全部 47 种注册格式找到正确的 Luma 程序；缩略图测试包含 4K/8K/16K HDR、EXR、真实 Nikon NEF 和中文路径，保持进程隔离。Windows 个性化的简短右键菜单仍由系统决定，完整程序列表的发现能力不等于每台电脑的菜单外观保证。\n\n同一 Linux 云端机器、相同合成文件的对比：8K flat HDR 解码加显示转换 18.16 秒→1.46 秒，输出校验值一致；4K JPG 切换 332 毫秒→35 毫秒，24 MP JPG 613 毫秒→77 毫秒。JPG 测试每次操作间留 400 毫秒供预加载，文件系统缓存未清空。这些不是用户电脑或 Windows 性能承诺；[方法与完整结果](https://github.com/Biscuits-Lab/Luma/blob/f861e0574096d76ef6534571d1a6dd6c46d7a143/docs/PERFORMANCE.md)。\n\n### 限制\n安装包、程序和 Shell DLL **尚未代码签名**。自动测试运行于 GitHub Windows runner；新用户账户下的交互式安装、Explorer 外观、Win11 圆角/吸附视觉效果和完整 Windows 10/11 DPI 矩阵尚未人工验证。原生圆角受系统策略影响。测试不代表所有相机、编码或损坏文件均受支持。\n\nHDR/EXR 为 SDR 色调映射预览，不是原生 HDR 显示输出。RAW 缩略图优先使用内嵌预览，主查看器仍解码 RAW。缩略图保持 256 MiB 输入、768 MiB 子进程、10 秒解码及 1024 像素输出限制；有界 Radiance 缩略图最多 256 MP/单边 32768，EXR 浮点路径仍限 16 MP。超过输入上限的 flat 16K HDR 不受支持；16K 缩略图支持不意味着主查看器可全分辨率打开所有 16K 文件。\n\n内置更新器仍使用免安装包更新路径；安装版升级请使用 setup.exe。旧版本 v0.1.0 和 v0.1.1 保持不变。\n'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def api(path, method='GET', payload=None, missing=False):
    require(path.startswith(f'repos/{REPO}'), 'Unexpected API destination')
    headers = {'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
               'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
               'User-Agent': 'Luma-verified-0.1.2-publisher'}
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
            and run.get('head_branch') == 'feature/performance'
            and run.get('event') == 'push' and run.get('status') == 'completed'
            and run.get('conclusion') == 'success'
            and run.get('path') == '.github/workflows/thumbnail-validation.yml', 'Wrong or unsuccessful source build')
    for key in ('repository', 'head_repository'):
        require(run.get(key, {}).get('id') == REPO_ID and run[key].get('full_name') == REPO, 'Wrong source repository')
    require(job.get('id') == JOB_ID and job.get('run_id') == RUN_ID
            and job.get('conclusion') == 'success', 'Required Windows job did not succeed')
    succeeded = {s['name'] for s in job.get('steps', []) if s.get('conclusion') == 'success'}
    require(REQUIRED_STEPS <= succeeded, 'Required Windows checks did not succeed')
    require(artifact.get('id') == ARTIFACT_ID and artifact.get('name') == 'luma-0.1.2-windows-candidate'
            and artifact.get('expired') is False and artifact.get('size_in_bytes') == 174680853
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
            and item.get('name') == 'Luma 0.1.2' and item.get('body') == NOTES
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
               'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'Luma-verified-0.1.2-publisher'}
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
    validate_tag(api(f'repos/{REPO}/git/ref/tags/v0.1.1'), 'v0.1.1', PREVIOUS_SHA)
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
            'tag_name': TAG, 'target_commitish': BUILD_SHA, 'name': 'Luma 0.1.2',
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
    validate_tag(api(f'repos/{REPO}/git/ref/tags/v0.1.1'), 'v0.1.1', PREVIOUS_SHA)
    require(api(f'repos/{REPO}/git/ref/heads/main')['object']['sha'] == original_main, 'Main changed during publication')
    require(api(f'repos/{REPO}/releases/latest').get('id') == release_id, 'Release is not latest stable')
    verify_public_downloads()
    print(f'Verified public release: https://github.com/{REPO}/releases/tag/{TAG}', flush=True)


if __name__ == '__main__':
    main()
