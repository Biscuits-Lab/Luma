#!/usr/bin/env python3
"""Publish only the checksum-pinned, already-tested Luma 0.1.4 candidate."""
from __future__ import annotations
import argparse
import base64
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
TAG = 'v0.1.4'
BUILD_SHA = '99b4349b51f139c963733aa4b1764cccbfe02df3'
PREVIOUS_013_SHA = 'caa4215f4177b3082ec94af2f001bd93154a8d81'
PREVIOUS_012_SHA = 'f861e0574096d76ef6534571d1a6dd6c46d7a143'
OLD_SHA = '99841e74c180282f742847c0e471eed457999067'
PREVIOUS_SHA = 'ada8d4beceedb7ef336401c80c546f5f3e635f08'
RUN_ID = 39005678218
JOB_ID = 117317550804
ARTIFACT_ID = 12091670751
ARTIFACT_SIZE = 181644564
ARTIFACT_DIGEST = 'sha256:5d8f90f488efb55ae7322bd1cc5c6037b3764e7cdce24b552ac45fe761c98453'
ASSETS = {'Luma-0.1.4-windows-x64-setup.exe': (36782629, '0d4026cfb16ad68c68639dae30dd91b4710127412ff5cbecdf054f05d357c3e5'), 'Luma-0.1.4-windows-x64.zip': (50791600, '41f8eab700583d350b68dee0b19e39c22c62dc522fbf7271c9dada105c1cfbfe'), 'Luma-0.1.4-dependency-sources.zip': (94067621, '2339d53b8c6c333379d94117314ff3ad87ec1a18fadff4b158f22f4771302bb3'), 'SHA256SUMS': (292, 'a4bfa521e9164516115fe75c25fc0a6cd8de1fadd6232740759ff367cfb466a6')}
AUXILIARY = {'WINDOWS-SMOKE-REPORT.json': (1688, '41d557d3857143f2be7ba50fe177236a4620df9af644bc76b7e8829e240d2999')}
REQUIRED_STEPS = {
    'Preflight native installer compilation without setup output',
    'Preflight actual stable launcher and process safety',
    'Verify actual managed update and frozen GUI relaunch',
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
NOTES = '## 下载与首次升级\n- 推荐安装版：`Luma-0.1.4-windows-x64-setup.exe`\n- 从 0.1.3 或更早的安装版升级，请关闭 Luma，手动运行本次 Setup，选择原安装目录。旧版本的应用内更新器不能被追溯修复；它可能下载独立便携副本，原快捷方式仍指向旧版\n- 免安装版：解压 `Luma-0.1.4-windows-x64.zip`，保留整个 Luma 文件夹，运行其中的 Luma.exe\n- `SHA256SUMS` 提供校验，依赖源代码与许可证材料单独附带\n\n## 更新修复\n- 安装目录第一层提供固定的 Luma.exe；开始菜单、桌面快捷方式及打开方式都指向这个入口，升级后选择新版本的独立文件目录\n- 新版安装模式的应用内更新使用经过校验的 Setup，保留原安装位置，并核实实际启动进程、版本和窗口确认后再关闭旧窗口。升级失败时保留原程序和明确错误\n- 保护现有文件和缩略图注册归属；交互式安装遇到另一个 Luma 的注册时，说明两个路径并默认拒绝迁移，不静默抢占\n- 修复安装界面的目录按钮、图标底色和进度显示；安装、升级、卸载及真实重启均有 Windows 自动化验证\n\n便携模式当前采用手动下载、解压和切换目录，暂不自动替换正在运行的便携程序。校验和用于检查下载完整性，并非代码签名。\n\n## 编辑与界面\n- 加入本地裁剪、校正角度、旋转/翻转、调整、滤镜及标记工具；没有擦除、背景移除或外部 AI 服务\n- 编辑基于当前显示的 8 位 SDR 图像，默认另存为。HDR/EXR/RAW 编辑导出不是原始高精度或 RAW 数据的无损保存，保存前会明确提示；原图不会被静默覆盖\n- HDR 曝光拖动先显示有界预览，停止后恢复完整质量；适应图片采用短过渡动画\n- 详情改为右侧圆角面板，保留预览直方图及图片实际包含的相机/GPS信息，移除背景设置和快捷键说明\n- 设置页去掉系统标题栏，精简外观与更新区域，直接显示对应版本的发布说明\n- 统一深灰圆角下拉菜单、工具提示和中文字体；“实际比例”完整显示，复制等消息改为画面上方短暂提示\n\n## 验证与范围\n本次直接发布[已通过的 Windows 构建](https://github.com/Biscuits-Lab/Luma/actions/runs/39005678218)产物，不在发布时重建。标签固定到 main 上验证的源码 `99b4349`。\n\n验证包括 1894 项应用测试、474 项 200% 界面测试、28 项真实根入口测试、62 次 Shell 缩略图调用、10 个打包查看器用例、7 个安装界面用例、11 组安装生命周期和 6 组更新测试。两个界面测试批次各有 4 个因缺少可选区域字体而跳过的字形测试。小桌面截图会移动测试窗口以露出目标控件，不表示完整窗口可容纳在 512×360 的逻辑桌面内。\n\n安装包、EXE 和 DLL 尚未数字签名，Windows 可能提示未知发布者。未完成所有桌面环境的人工 Explorer、混合显示器缩放、Win11 圆角与全相机型号检查。HDR 仍输出 SDR 色调映射；直方图统计显示预览的有界采样。RAW 缩略图优先取内嵌预览；深度、多部件及通道子采样 EXR 仍不支持。格式和内存保护范围见[说明文档](https://github.com/Biscuits-Lab/Luma/blob/99b4349b51f139c963733aa4b1764cccbfe02df3/README.md)。\n'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def api(path, method='GET', payload=None, missing=False):
    require(path.startswith(f'repos/{REPO}'), 'Unexpected API destination')
    headers = {'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
               'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
               'User-Agent': 'Luma-verified-0.1.4-publisher'}
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
    require(artifact.get('id') == ARTIFACT_ID and artifact.get('name') == 'luma-0.1.4-windows-candidate'
            and artifact.get('expired') is False and artifact.get('size_in_bytes') == ARTIFACT_SIZE
            and artifact.get('digest') == ARTIFACT_DIGEST, 'Wrong or expired candidate artifact')
    owner = artifact.get('workflow_run', {})
    require(owner.get('id') == RUN_ID and owner.get('head_sha') == BUILD_SHA
            and owner.get('repository_id') == REPO_ID and owner.get('head_repository_id') == REPO_ID,
            'Artifact provenance mismatch')


PUBLISHER_PATHS = {
    '.github/workflows/publish-verified-014.yml',
    'scripts/publish_verified_014.py', 'scripts/test_publish_verified_014.py',
    '.github/workflows/thumbnail-validation.yml', 'tests/test_validation_workflow.py',
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


PUBLICATION_EXCLUSIONS = """    paths-ignore:
      - '.github/workflows/publish-verified-014.yml'
      - 'scripts/publish_verified_014.py'
      - 'scripts/test_publish_verified_014.py'
"""


def validate_workflow_parity(base, current):
    marker = '    branches: [main]\n'
    require(base.count(marker) == 1 and 'paths-ignore:' not in base,
            'Unexpected tested validation workflow')
    require(current == base.replace(marker, marker + PUBLICATION_EXCLUSIONS, 1),
            'Validation jobs or gates changed after the tested source')


def validate_workflow_history(current_main):
    path = '.github/workflows/thumbnail-validation.yml'
    def read_at(ref):
        item = api(f'repos/{REPO}/contents/{path}?ref={ref}')
        require(item.get('encoding') == 'base64' and item.get('size', 999999) <= 128000,
                'Unexpected workflow content')
        return base64.b64decode(item['content']).decode('utf-8')
    validate_workflow_parity(read_at(BUILD_SHA), read_at(current_main))


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
            and item.get('name') == 'Luma 0.1.4' and item.get('body') == NOTES
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
               'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'Luma-verified-0.1.4-publisher'}
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
    validate_tag(api(f'repos/{REPO}/git/ref/tags/v0.1.3'), 'v0.1.3', PREVIOUS_013_SHA)
    original_main = api(f'repos/{REPO}/git/ref/heads/main')['object']['sha']
    require(original_main == os.environ.get('GITHUB_SHA'), 'Main advanced after this publisher started')
    validate_main_parity(api(f'repos/{REPO}/compare/{BUILD_SHA}...{original_main}'), original_main)
    validate_workflow_history(original_main)
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
            'tag_name': TAG, 'target_commitish': BUILD_SHA, 'name': 'Luma 0.1.4',
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
    validate_tag(api(f'repos/{REPO}/git/ref/tags/v0.1.3'), 'v0.1.3', PREVIOUS_013_SHA)
    require_unchanged_main(original_main)
    require(api(f'repos/{REPO}/releases/latest').get('id') == release_id, 'Release is not latest stable')
    verify_public_downloads()
    print(f'Verified public release: https://github.com/{REPO}/releases/tag/{TAG}', flush=True)


if __name__ == '__main__':
    main()
