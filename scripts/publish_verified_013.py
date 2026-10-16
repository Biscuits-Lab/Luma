#!/usr/bin/env python3
"""Publish only the checksum-pinned, already-tested Luma 0.1.3 candidate."""
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
BRANCH = 'refs/heads/main'
TAG = 'v0.1.3'
BUILD_SHA = 'caa4215f4177b3082ec94af2f001bd93154a8d81'
PREVIOUS_012_SHA = 'f861e0574096d76ef6534571d1a6dd6c46d7a143'
OLD_SHA = '99841e74c180282f742847c0e471eed457999067'
PREVIOUS_SHA = 'ada8d4beceedb7ef336401c80c546f5f3e635f08'
RUN_ID = 38925369895
JOB_ID = 117058148711
ARTIFACT_ID = 12056962930
ARTIFACT_SIZE = 179364560
ARTIFACT_DIGEST = 'sha256:d94584481fd1a7b1c63e6c3b422f516caf211931edff321494fb67ee94f3e270'
ASSETS = {'Luma-0.1.3-windows-x64-setup.exe': (35761843, 'c6622670955db4e25c02ceaeb42d432e2b8add73bba1777bbba09188d0ea5f4f'), 'Luma-0.1.3-windows-x64.zip': (49532268, '00f7cd8eaec6b46783a3d5a1e7fa547d638910ceac217eb181751d1417d9034f'), 'Luma-0.1.3-dependency-sources.zip': (94067735, '311e7f14d824c2dcd72be3802d2edeeef04cedbf51275717a7b1ca28952a3277'), 'SHA256SUMS': (292, '5317034cdd0d1244e44db588da57fcc75357b68b39aac9ff82501477b19d80ae')}
AUXILIARY = {'WINDOWS-SMOKE-REPORT.json': (1688, '19eba9e02098a71a1d0fdff83d9951ed27e6abe401542c404d30ac4f4374c200')}
REQUIRED_STEPS = {
    'Preflight native installer compilation without setup output',
    'Build native bridge, test viewer, create folder bundle',
    'Verify installer and release-safety contracts',
    'Verify localized controls and relative zoom at 200 percent scaling',
    'Capture localized native Qt controls',
    'Verify loaded-image drop regression sensitivity',
    'Smoke real bundled UI and decoders',
    'Exercise real isolated Shell thumbnail surrogate',
    'Collect dependency sources', 'Package candidate ZIP and checksums',
    'Build current-user Windows installer',
    'Verify native installer layout and language controls',
    'Verify installer lifecycle on disposable runner',
}
NOTES = '## 下载\n- **安装版**：`Luma-0.1.3-windows-x64-setup.exe`，推荐日常使用。关闭 Luma 后运行；安装程序会准备当前用户的“打开方式”和资源管理器缩略图组件，不擅自更改默认应用\n- **免安装版**：解压 `Luma-0.1.3-windows-x64.zip`，保留整个 `Luma` 文件夹并运行 `Luma.exe`\n- `SHA256SUMS` 提供文件校验；依赖源代码与许可证材料单独附带\n\n## 更新\n- 加速平面 EXR 的选定通道读取和显示；完整查看支持 8192 × 8192，缩略图采用有界内存的小尺寸采样，修复大图被旧像素限制拒绝的问题\n- 界面跟随系统显示语言，支持简体中文、繁体中文和英文；重新设计精简设置页与安装界面\n- 带图标、圆角、柔和外阴影和短开启动画的菜单；图片右键可复制、旋转、删除、另存为和复制路径\n- 从文件启动时不再先闪现欢迎图标；调整底栏图标、信息按钮间距与缩放显示\n- 图片详情改为不移动画面的悬浮毛玻璃面板，加入显示预览 RGB 直方图，以及文件实际包含的相机、拍摄参数和 GPS 坐标\n\n## 性能与范围\n同一 Linux 云主机、相同合成文件的对照中，4K/8K 平面 EXR 完整窗口打开中位数约为 0.1.2 的 1.7–2.4 倍速度。另一个 66.4 MB、8192² 的合成 EXR 缩略图处理约 0.406 秒，未包含冻结程序启动或 Shell 通信。这些是有说明条件的开发测量，不代表用户电脑或未取得的原始 EXR；详见[测量方法与范围](https://github.com/Biscuits-Lab/Luma/blob/caa4215f4177b3082ec94af2f001bd93154a8d81/docs/EXR_PERFORMANCE.md)。\n\n直方图统计当前色调映射/通道显示的有限像素采样，并非原始线性 HDR 全图统计。元数据只读取图片内嵌内容，不联网查询地点。HDR 显示为 SDR 色调映射，不是显示器原生 HDR 输出。RAW 缩略图优先使用内嵌预览，查看器使用 RAW 解码。\n\n深度、多部件及通道子采样 EXR 仍不支持。EXR 缩略图保留 256 MiB 压缩文件、768 MiB 隔离进程和 10 秒执行上限；存储布局、压缩和图像内容可能使部分文件达到保护上限。8K 方形完整查看保留 float32 像素，需额外显示与解码内存，不能等同于小缩略图的内存需求。\n\n## 验证与注意\n本版本使用通过 Windows 自动化验证的同一份安装包/免安装包，不在发布时重新构建。验证包含 1392 项常规测试、329 项原生 200% 界面测试、62 次缩略图调用、10 个打包查看器用例、7 个安装界面用例和 8 组安装生命周期检查。最终测试与产物校验见[Windows 构建记录](https://github.com/Biscuits-Lab/Luma/actions/runs/38925369895)；版本标签对应在 main 上验证的源码提交 caa4215。\n\n安装程序、EXE 和 DLL 暂未数字签名，Windows 可能提示未知发布者。托管 Windows 测试覆盖原生 Qt、隔离 Shell 缩略图和安装/升级/卸载流程；尚未完成每种桌面环境的人工 Explorer、Win11 圆角、混合显示缩放与全相机型号检查。系统“打开方式”简短推荐列表和缩略图缓存也受 Windows 自身策略影响。\n\n已有安装版建议运行新 Setup 升级；应用内更新提供经过校验的便携包流程。源图不会因预览、旋转、调色或升级被覆盖；删除需要确认并使用回收站。\n'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def api(path, method='GET', payload=None, missing=False):
    require(path.startswith(f'repos/{REPO}'), 'Unexpected API destination')
    headers = {'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
               'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
               'User-Agent': 'Luma-verified-0.1.3-publisher'}
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
            and run.get('head_branch') == 'main'
            and run.get('event') == 'push' and run.get('status') == 'completed'
            and run.get('conclusion') == 'success'
            and run.get('path') == '.github/workflows/thumbnail-validation.yml', 'Wrong or unsuccessful source build')
    for key in ('repository', 'head_repository'):
        require(run.get(key, {}).get('id') == REPO_ID and run[key].get('full_name') == REPO, 'Wrong source repository')
    require(job.get('id') == JOB_ID and job.get('run_id') == RUN_ID
            and job.get('conclusion') == 'success', 'Required Windows job did not succeed')
    succeeded = {s['name'] for s in job.get('steps', []) if s.get('conclusion') == 'success'}
    require(REQUIRED_STEPS <= succeeded, 'Required Windows checks did not succeed')
    require(artifact.get('id') == ARTIFACT_ID and artifact.get('name') == 'luma-0.1.3-windows-candidate'
            and artifact.get('expired') is False and artifact.get('size_in_bytes') == ARTIFACT_SIZE
            and artifact.get('digest') == ARTIFACT_DIGEST, 'Wrong or expired candidate artifact')
    owner = artifact.get('workflow_run', {})
    require(owner.get('id') == RUN_ID and owner.get('head_sha') == BUILD_SHA
            and owner.get('repository_id') == REPO_ID and owner.get('head_repository_id') == REPO_ID,
            'Artifact provenance mismatch')


PUBLISHER_PATHS = {
    '.github/workflows/publish-verified-013.yml',
    'scripts/publish_verified_013.py', 'scripts/test_publish_verified_013.py',
}


def validate_main_parity(comparison, current_main):
    # The tag keeps the tested main source. Only publication machinery may have
    # been committed afterward; no application/build payload can silently differ.
    require(comparison.get('status') == 'ahead' and comparison.get('behind_by') == 0
            and 1 <= comparison.get('ahead_by', 0) <= 10
            and comparison.get('base_commit', {}).get('sha') == BUILD_SHA
            and comparison.get('merge_base_commit', {}).get('sha') == BUILD_SHA,
            'Main no longer descends directly from the verified source')
    commits = comparison.get('commits', [])
    require(len(commits) == comparison.get('ahead_by') and commits[-1].get('sha') == current_main,
            'Main comparison is incomplete')
    files = comparison.get('files', [])
    require(files and {f.get('filename') for f in files} <= PUBLISHER_PATHS
            and all(f.get('status') in {'added', 'modified'} for f in files),
            'Application or build source changed after the verified main candidate')


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
            and item.get('name') == 'Luma 0.1.3' and item.get('body') == NOTES
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
               'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'Luma-verified-0.1.3-publisher'}
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


def require_unchanged_main(expected):
    require(api(f'repos/{REPO}/git/ref/heads/main')['object']['sha'] == expected,
            'Main changed during publication; refusing to publish this draft')


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
    validate_tag(api(f'repos/{REPO}/git/ref/tags/v0.1.2'), 'v0.1.2', PREVIOUS_012_SHA)
    original_main = api(f'repos/{REPO}/git/ref/heads/main')['object']['sha']
    require(original_main == os.environ.get('GITHUB_SHA'), 'Main advanced after this publisher started')
    validate_main_parity(api(f'repos/{REPO}/compare/{BUILD_SHA}...{original_main}'), original_main)
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
            'tag_name': TAG, 'target_commitish': BUILD_SHA, 'name': 'Luma 0.1.3',
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
        require_unchanged_main(original_main)
        api(f'repos/{REPO}/releases/{release_id}', 'PATCH', {'draft': False, 'make_latest': 'true'})
    published = api(f'repos/{REPO}/releases/tags/{TAG}')
    validate_release(published)
    require(published['id'] == release_id and published['draft'] is False, 'Release is not public')
    validate_tag(api(f'repos/{REPO}/git/ref/tags/{TAG}'))
    validate_tag(api(f'repos/{REPO}/git/ref/tags/v0.1.0'), 'v0.1.0', OLD_SHA)
    validate_tag(api(f'repos/{REPO}/git/ref/tags/v0.1.1'), 'v0.1.1', PREVIOUS_SHA)
    validate_tag(api(f'repos/{REPO}/git/ref/tags/v0.1.2'), 'v0.1.2', PREVIOUS_012_SHA)
    require_unchanged_main(original_main)
    require(api(f'repos/{REPO}/releases/latest').get('id') == release_id, 'Release is not latest stable')
    verify_public_downloads()
    print(f'Verified public release: https://github.com/{REPO}/releases/tag/{TAG}', flush=True)


if __name__ == '__main__':
    main()
