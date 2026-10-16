"""Qt catalog notice/source packaging regressions; no network or upstream code runs."""
from contextlib import ExitStack
from copy import deepcopy
from email.message import Message
import hashlib
from io import BytesIO
import json
from pathlib import Path
import shutil
import sys
import tarfile
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase, main
from unittest.mock import patch
import zipfile

import collect_licenses as licenses
import fetch_upstream_licenses as fetcher
import prepare_source_material as sources

ROOT = Path(__file__).resolve().parents[1]
TAG = 'v6.12.0'
REVISION = '2811457ceff8f0c24a1c4d14f947a283a17239d0'
DECLARATION = b'qt_internal_add_sbom(Translations\n TYPE QT_TRANSLATIONS\n QT_LICENSE_ID "QT_COMMERCIAL_OR_BSD3"\n)'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def source_archive(declaration=DECLARATION):
    files = {
        'LICENSES/BSD-3-Clause.txt': b'BSD license text fixture',
        'LICENSES/GPL-3.0-only.txt': b'GPL license text fixture',
        'LICENSES/Qt-GPL-exception-1.0.txt': b'Qt GPL exception fixture',
        'licenseRule.json': b'[]',
        'translations/CMakeLists.txt': declaration,
        'translations/README': b'Qt community translations',
        'translations/split-qt-ts.pl': b'# GPL-3.0-only WITH Qt-GPL-exception-1.0',
        'translations/qtbase_zh_CN.ts': b'<TS language="zh_CN"/>',
        'translations/qtbase_zh_TW.ts': b'<TS language="zh_TW"/>',
    }
    buffer = BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz', pax_headers={'comment': REVISION}) as archive:
        for path, data in files.items():
            info = tarfile.TarInfo('qttranslations-6.12.0/' + path)
            info.size = len(data)
            archive.addfile(info, BytesIO(data))
    return buffer.getvalue(), files


class TranslationNoticeTests(TestCase):
    def test_collector_preserves_actual_module_license_evidence_and_sources(self):
        archive, files = source_archive()
        with TemporaryDirectory() as folder:
            root = Path(folder)
            destination = root / 'notices'
            with patch.object(fetcher, 'ROOT', root), patch.object(fetcher, 'fetch', return_value=archive) as fetch:
                result = fetcher.collect_repo('qt/qttranslations', TAG, destination)
            fetch.assert_called_once_with('https://codeload.github.com/qt/qttranslations/tar.gz/refs/tags/' + TAG)
            self.assertEqual(result['revision'], REVISION)
            self.assertEqual(result['source_archive_sha256'], sha(archive))
            self.assertEqual(result['translations']['license_expression'], 'BSD-3-Clause')
            for path in ('LICENSES/BSD-3-Clause.txt', 'LICENSES/GPL-3.0-only.txt',
                         'LICENSES/Qt-GPL-exception-1.0.txt', *fetcher.QT_TRANSLATION_EVIDENCE):
                self.assertEqual((destination / path).read_bytes(), files[path])
                self.assertEqual(result['files'][path]['sha256'], sha(files[path]))
            for name in fetcher.QT_TRANSLATION_CATALOGS:
                source = 'translations/' + name + '.ts'
                self.assertEqual(result['translations']['catalogs'][name + '.qm'], {
                    'source': source, 'source_sha256': sha(files[source])})

    def test_changed_catalog_license_declaration_fails_closed(self):
        archive, _ = source_archive(DECLARATION.replace(b'QT_COMMERCIAL_OR_BSD3', b'QT_COMMERCIAL_OR_GPL3'))
        with TemporaryDirectory() as folder:
            with patch.object(fetcher, 'ROOT', Path(folder)), patch.object(fetcher, 'fetch', return_value=archive):
                with self.assertRaisesRegex(RuntimeError, 'license declaration changed'):
                    fetcher.collect_repo('qt/qttranslations', TAG, Path(folder) / 'notices')

    def test_checked_in_module_revision_and_notice_hashes(self):
        upstream = ROOT / 'packaging/licenses/upstream'
        manifest = json.loads((upstream / 'manifest.json').read_text())
        component = manifest['components']['qttranslations']
        self.assertEqual(component['tag'], manifest['components']['qtbase']['tag'])
        self.assertEqual(component['revision'], REVISION)
        self.assertEqual(component['source_archive_sha256'], '8be0626ad9c84b09a64ab521e5049bbd009f1335c2283c996954736c7e71484d')
        for path, record in component['files'].items():
            data = (upstream / 'qttranslations' / path).read_bytes()
            self.assertEqual(sha(data), record['sha256'], path)
            self.assertEqual(len(data), record['bytes'], path)
        self.assertEqual(component['translations']['license_expression'], 'BSD-3-Clause')


class RuntimeNoticeTests(TestCase):
    def fixture(self, root):
        upstream = root / 'packaging/licenses/upstream'
        upstream.mkdir(parents=True)
        original = json.loads((ROOT / 'packaging/licenses/upstream/manifest.json').read_text())
        qttranslations = deepcopy(original['components']['qttranslations'])
        shutil.copytree(ROOT / 'packaging/licenses/upstream/qttranslations', upstream / 'qttranslations')
        components = {'qttranslations': qttranslations}
        for key in ('qtbase', 'pyside'):
            (upstream / key).mkdir()
            (upstream / key / 'LICENSE').write_bytes(b'fixture license')
            components[key] = {'tag': TAG, 'files': {'LICENSE': {'sha256': sha(b'fixture license')}}}
        (upstream / 'Python-LICENSE.txt').write_bytes(b'python license')
        components['python'] = {'version': '3.12.14', 'file': 'Python-LICENSE.txt', 'sha256': sha(b'python license')}
        manifest = {'components': components}
        (root / 'THIRD_PARTY_NOTICES.md').write_text('notice fixture')
        qm_dir = root / 'translations'
        qm_dir.mkdir()
        for language in ('CN', 'TW'):
            (qm_dir / f'qtbase_zh_{language}.qm').write_bytes(('compiled ' + language).encode())
        return upstream, manifest, qm_dir

    def collect(self, root, manifest, qm_dir, incomplete=False):
        upstream = root / 'packaging/licenses/upstream'
        (upstream / 'manifest.json').write_text(json.dumps(manifest))
        package = SimpleNamespace(version='6.12.0', files=[], read_text=lambda _: None, metadata=Message())
        fake_qt = SimpleNamespace(qVersion=lambda: '6.12.0', QLibraryInfo=SimpleNamespace(
            path=lambda _: str(qm_dir), LibraryPath=SimpleNamespace(TranslationsPath=0)))
        fake_rawpy = SimpleNamespace(libraw_version=(0, 22, 1), flags={})
        with ExitStack() as stack:
            stack.enter_context(patch.object(licenses, 'ROOT', root))
            stack.enter_context(patch.object(licenses, 'PACKAGES', ('PySide6_Essentials', 'shiboken6')))
            stack.enter_context(patch.object(licenses, 'distribution', return_value=package))
            stack.enter_context(patch.object(licenses.platform, 'python_version', return_value='3.12.14'))
            stack.enter_context(patch.object(licenses, 'sys', SimpleNamespace(base_prefix=str(root / 'empty'),
                                version_info=sys.version_info, stderr=sys.stderr)))
            stack.enter_context(patch.dict(sys.modules, {'rawpy': fake_rawpy, 'PySide6.QtCore': fake_qt}))
            return licenses.collect(root / 'output', allow_incomplete=incomplete)

    def test_matching_notices_inventory_both_compiled_catalogs(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            _, manifest, qm_dir = self.fixture(root)
            result = self.collect(root, manifest, qm_dir)
            self.assertTrue(result['license_texts_complete'])
            self.assertIn('qttranslations', result['packages']['PySide6_Essentials']['upstream_notices'])
            self.assertEqual(set(result['translation_catalogs']), {'qtbase_zh_CN.qm', 'qtbase_zh_TW.qm'})
            self.assertEqual(result['translation_catalogs']['qtbase_zh_CN.qm']['sha256'], sha(b'compiled CN'))
            self.assertEqual(result['translation_catalogs']['qtbase_zh_TW.qm']['license_expression'], 'BSD-3-Clause')

    def test_public_collection_rejects_missing_changed_or_mismatched_evidence(self):
        for issue in ('missing_component', 'wrong_version', 'missing_exception', 'corrupt_notice',
                      'missing_source_hash', 'changed_license', 'missing_binary', 'missing_revision'):
            with self.subTest(issue=issue), TemporaryDirectory() as folder:
                root = Path(folder)
                upstream, manifest, qm_dir = self.fixture(root)
                component = manifest['components']['qttranslations']
                if issue == 'missing_component':
                    del manifest['components']['qttranslations']
                elif issue == 'wrong_version':
                    component['tag'] = 'v6.11.0'
                elif issue == 'missing_exception':
                    del component['files']['LICENSES/Qt-GPL-exception-1.0.txt']
                elif issue == 'corrupt_notice':
                    (upstream / 'qttranslations/LICENSES/BSD-3-Clause.txt').write_text('damaged')
                elif issue == 'missing_source_hash':
                    component['translations']['catalogs']['qtbase_zh_CN.qm']['source_sha256'] = ''
                elif issue == 'changed_license':
                    component['translations']['license_expression'] = 'GPL-3.0-only'
                elif issue == 'missing_binary':
                    (qm_dir / 'qtbase_zh_CN.qm').unlink()
                elif issue == 'missing_revision':
                    component['revision'] = ''
                with self.assertRaisesRegex(RuntimeError, 'Missing license material'):
                    self.collect(root, manifest, qm_dir)

    def test_incomplete_override_remains_development_only(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            _, manifest, qm_dir = self.fixture(root)
            del manifest['components']['qttranslations']
            result = self.collect(root, manifest, qm_dir, incomplete=True)
            self.assertFalse(result['license_texts_complete'])
            self.assertTrue((root / 'output/INCOMPLETE-DEVELOPMENT-ONLY.txt').is_file())


class TranslationSourceBundleTests(TestCase):
    def fixture(self, root):
        cache = root / 'build/source-cache'
        cache.mkdir(parents=True)
        components = {}
        for name in ('qtbase', 'pyside', 'qttranslations'):
            data = (name + ' source fixture').encode()
            (cache / f'{name}-{TAG}.tar.gz').write_bytes(data)
            components[name] = {'tag': TAG, 'revision': REVISION, 'repository': f'https://github.com/qt/{name}',
                'source_archive': f'https://codeload.github.com/qt/{name}/tar.gz/refs/tags/{TAG}',
                'source_archive_sha256': sha(data)}
        components['qttranslations']['translations'] = {'license_expression': 'BSD-3-Clause'}
        upstream = root / 'packaging/licenses/upstream'
        upstream.mkdir(parents=True)
        (upstream / 'manifest.json').write_text(json.dumps({'components': components}))
        (root / 'docs').mkdir()
        (root / 'docs/REDISTRIBUTION.md').write_text('replacement instructions')
        (root / 'THIRD_PARTY_NOTICES.md').write_text('notices')
        (cache / 'rawpy-source').mkdir()
        (cache / 'rawpy-source/setup.py').write_text('# fixture; never executed')
        return cache, components

    def git(self, *args, cwd=None):
        if args == ('rev-parse', 'HEAD'):
            return sources.RAWPY_COMMIT if cwd.name == 'rawpy-source' else 'a' * 40
        if args[:3] == ('config', '-f', '.gitmodules'):
            return 'https://github.com/LibRaw/LibRaw.git' if 'LibRaw.url' in args[3] else 'https://github.com/LibRaw/LibRaw-cmake'
        raise AssertionError(args)

    def test_bundle_contains_matching_qttranslations_archive_and_provenance(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            cache, _ = self.fixture(root)
            with patch.object(sources, 'ROOT', root), patch.object(sources, 'git', side_effect=self.git), \
                    patch.object(sources.subprocess, 'run'), patch.object(sources, 'urlopen') as network:
                result = sources.prepare(root / 'artifacts', '0.1.3')
            network.assert_not_called()
            with zipfile.ZipFile(result) as bundle:
                prefix = 'Luma-dependency-sources/'
                name = f'qttranslations-{TAG}.tar.gz'
                self.assertEqual(bundle.read(prefix + name), (cache / name).read_bytes())
                records = json.loads(bundle.read(prefix + 'source-manifest.json'))
                translation = next(item for item in records if item['component'] == 'qttranslations')
                self.assertEqual(translation['revision'], REVISION)
                self.assertEqual(translation['sha256'], sha((cache / name).read_bytes()))
                self.assertIn(b'BSD-3-Clause', bundle.read(prefix + 'README.txt'))

    def test_mismatched_or_unreviewed_translation_source_rejected_before_download(self):
        for issue in ('missing', 'version', 'license', 'hash'):
            with self.subTest(issue=issue), TemporaryDirectory() as folder:
                root = Path(folder)
                _, components = self.fixture(root)
                if issue == 'missing':
                    del components['qttranslations']
                elif issue == 'version':
                    components['qttranslations']['tag'] = 'v6.11.0'
                elif issue == 'license':
                    components['qttranslations']['translations']['license_expression'] = 'unknown'
                elif issue == 'hash':
                    components['qttranslations']['source_archive_sha256'] = 'f' * 64
                (root / 'packaging/licenses/upstream/manifest.json').write_text(json.dumps({'components': components}))
                with patch.object(sources, 'ROOT', root), patch.object(sources, 'urlopen') as network, \
                        patch.object(sources.subprocess, 'run') as commands:
                    with self.assertRaises(RuntimeError):
                        sources.prepare(root / 'artifacts', '0.1.3')
                network.assert_not_called()
                commands.assert_not_called()


if __name__ == '__main__':
    main()
