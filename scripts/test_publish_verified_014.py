import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import publish_verified_014 as p


def release(draft=True):
    return {'id': 42, 'tag_name': p.TAG, 'target_commitish': p.BUILD_SHA, 'name': 'Luma 0.1.4',
            'body': p.NOTES, 'prerelease': False, 'draft': draft,
            'assets': [{'name': name, 'size': size, 'digest': 'sha256:' + digest, 'state': 'uploaded'}
                       for name, (size, digest) in p.ASSETS.items()]}


def build():
    owner = {'id': p.REPO_ID, 'full_name': p.REPO}
    run = {'id': p.RUN_ID, 'head_sha': p.BUILD_SHA, 'head_branch': 'main',
           'event': 'push', 'status': 'completed', 'conclusion': 'success',
           'path': '.github/workflows/thumbnail-validation.yml', 'repository': owner, 'head_repository': owner}
    job = {'id': p.JOB_ID, 'run_id': p.RUN_ID, 'conclusion': 'success',
           'steps': [{'name': name, 'conclusion': 'success'} for name in p.REQUIRED_STEPS]}
    artifact = {'id': p.ARTIFACT_ID, 'name': 'luma-0.1.4-windows-candidate', 'expired': False,
                'size_in_bytes': p.ARTIFACT_SIZE, 'digest': p.ARTIFACT_DIGEST,
                'workflow_run': {'id': p.RUN_ID, 'head_sha': p.BUILD_SHA,
                                 'repository_id': p.REPO_ID, 'head_repository_id': p.REPO_ID}}
    return run, job, artifact


class PublicationGuards(unittest.TestCase):
    def test_exact_metadata(self):
        p.validate_build(*build())
        p.validate_release(release())
        p.validate_tag({'ref': 'refs/tags/' + p.TAG, 'object': {'sha': p.BUILD_SHA, 'type': 'commit'}})

    def test_wrong_commit_or_failed_build_rejected(self):
        for field, value in [('head_sha', p.OLD_SHA), ('conclusion', 'failure'), ('status', 'in_progress'),
                             ('head_branch', 'feature/exr-performance'), ('event', 'pull_request')]:
            with self.subTest(field=field):
                run, job, artifact = build()
                run[field] = value
                with self.assertRaises(RuntimeError): p.validate_build(run, job, artifact)

    def test_shell_or_installer_failure_rejected_even_if_job_success(self):
        for name in p.REQUIRED_STEPS:
            with self.subTest(step=name):
                run, job, artifact = build()
                next(s for s in job['steps'] if s['name'] == name)['conclusion'] = 'failure'
                with self.assertRaises(RuntimeError): p.validate_build(run, job, artifact)

    def test_wrong_artifact_rejected(self):
        for field, value in [('id', 1), ('expired', True), ('digest', 'sha256:wrong'), ('size_in_bytes', 1)]:
            run, job, artifact = build()
            artifact[field] = value
            with self.assertRaises(RuntimeError): p.validate_build(run, job, artifact)
        run, job, artifact = build()
        artifact['workflow_run']['head_sha'] = p.OLD_SHA
        with self.assertRaises(RuntimeError): p.validate_build(run, job, artifact)

    def test_tag_cannot_target_publisher_or_old_version(self):
        for sha, kind, tag in [(p.OLD_SHA, 'commit', p.TAG), (p.BUILD_SHA, 'tag', p.TAG),
                               (p.BUILD_SHA, 'commit', 'v0.1.0')]:
            with self.assertRaises(RuntimeError):
                p.validate_tag({'ref': 'refs/tags/' + tag, 'object': {'sha': sha, 'type': kind}})

    def test_release_identity_and_bytes_fail_closed(self):
        for field, value in [('target_commitish', p.OLD_SHA), ('tag_name', 'v0.1.0'), ('prerelease', True),
                             ('body', 'Changed notes'), ('name', 'Other release')]:
            item = release(); item[field] = value
            with self.assertRaises(RuntimeError): p.validate_release(item)
        for field, value in [('digest', 'sha256:wrong'), ('size', 1), ('state', 'starter')]:
            item = release(); item['assets'][0][field] = value
            with self.assertRaises(RuntimeError): p.validate_release(item)

    def test_only_missing_draft_assets_can_resume(self):
        item = release(); item['assets'].pop()
        p.validate_release(item, complete=False)
        with self.assertRaises(RuntimeError): p.validate_release(item)
        item = release(); item['assets'].append(copy.deepcopy(item['assets'][0]))
        with self.assertRaises(RuntimeError): p.validate_release(item, complete=False)
        item = release(); item['assets'][0]['name'] = 'unexpected.exe'
        with self.assertRaises(RuntimeError): p.validate_release(item, complete=False)

    def test_local_bytes_and_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            data = b'exact tested binary'
            digest = hashlib.sha256(data).hexdigest()
            manifest = (digest + '  app.zip\n').encode()
            expected = {'app.zip': (len(data), digest), 'SHA256SUMS': (len(manifest), hashlib.sha256(manifest).hexdigest())}
            (directory / 'app.zip').write_bytes(data)
            (directory / 'SHA256SUMS').write_bytes(manifest)
            with patch.object(p, 'ASSETS', expected), patch.object(p, 'AUXILIARY', {}):
                p.validate_files(directory)
                (directory / 'app.zip').write_bytes(b'X' * len(data))
                with self.assertRaises(RuntimeError): p.validate_files(directory)
                (directory / 'app.zip').write_bytes(data)
                (directory / 'unexpected').write_text('x')
                with self.assertRaises(RuntimeError): p.validate_files(directory)

    def test_auxiliary_report_is_pinned_but_not_published(self):
        self.assertEqual(set(p.AUXILIARY), {'WINDOWS-SMOKE-REPORT.json'})
        self.assertFalse(set(p.AUXILIARY) & set(p.ASSETS))
        with self.assertRaises(RuntimeError):
            p.upload_asset(42, Path('WINDOWS-SMOKE-REPORT.json'))

    def test_wrong_repository_or_branch_stops_before_network(self):
        with patch.dict(p.os.environ, {'GITHUB_REPOSITORY': p.REPO, 'GITHUB_REF': 'refs/heads/unapproved'}), \
             patch.object(p, 'api') as api, patch('sys.argv', ['publisher']):
            with self.assertRaises(RuntimeError): p.main()
            api.assert_not_called()

    def test_main_contains_only_publication_changes_since_tested_source(self):
        item = {'status': 'ahead', 'behind_by': 0, 'ahead_by': 1,
                'base_commit': {'sha': p.BUILD_SHA}, 'merge_base_commit': {'sha': p.BUILD_SHA},
                'commits': [{'sha': 'publisher-head'}],
                'files': [{'filename': name, 'status': 'added'} for name in p.PUBLISHER_PATHS]}
        p.validate_main_parity(item, 'publisher-head')
        for change in [dict(status='diverged'), dict(behind_by=1), dict(ahead_by=11),
                       dict(commits=[]), dict(files=[]),
                       dict(files=[{'filename': 'luma/app.py', 'status': 'modified'}]),
                       dict(files=[{'filename': next(iter(p.PUBLISHER_PATHS)), 'status': 'removed'}])]:
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                p.validate_main_parity(item | change, 'publisher-head')
        with self.assertRaises(RuntimeError): p.validate_main_parity(item, 'different-head')

    def test_main_is_checked_again_immediately_before_publication(self):
        with patch.object(p, 'api', return_value={'object': {'sha': 'expected'}}):
            p.require_unchanged_main('expected')
        with patch.object(p, 'api', return_value={'object': {'sha': 'advanced'}}):
            with self.assertRaises(RuntimeError): p.require_unchanged_main('expected')
        source = Path(p.__file__).read_text()
        self.assertIn("require_unchanged_main(original_main)\n        api(f'repos/{REPO}/releases/{release_id}', 'PATCH'", source)

    def test_main_advancing_during_uploads_keeps_release_draft(self):
        run, job, artifact = build()
        state = {'main': 'publisher-head'}
        comparison = {'status': 'ahead', 'behind_by': 0, 'ahead_by': 1,
                      'base_commit': {'sha': p.BUILD_SHA}, 'merge_base_commit': {'sha': p.BUILD_SHA},
                      'commits': [{'sha': 'publisher-head'}],
                      'files': [{'filename': name, 'status': 'added'} for name in p.PUBLISHER_PATHS]}
        draft = release()
        draft['assets'] = []
        old_tags = {'v0.1.0': p.OLD_SHA, 'v0.1.1': p.PREVIOUS_SHA,
                    'v0.1.2': p.PREVIOUS_012_SHA, 'v0.1.3': p.PREVIOUS_013_SHA, p.TAG: p.BUILD_SHA}

        def fake_api(path, method='GET', payload=None, missing=False):
            # Every request is mocked, and any mutation or unexpected read fails.
            self.assertEqual(method, 'GET')
            suffix = path.removeprefix(f'repos/{p.REPO}')
            if suffix == '': return {'id': p.REPO_ID, 'private': False}
            if suffix == f'/actions/runs/{p.RUN_ID}': return run
            if suffix == f'/actions/jobs/{p.JOB_ID}': return job
            if suffix == f'/actions/artifacts/{p.ARTIFACT_ID}': return artifact
            if suffix == '/git/ref/heads/main': return {'object': {'sha': state['main']}}
            if suffix == f'/compare/{p.BUILD_SHA}...publisher-head': return comparison
            if suffix.startswith('/git/ref/tags/'):
                tag = suffix.rsplit('/', 1)[1]
                return {'ref': 'refs/tags/' + tag, 'object': {'sha': old_tags[tag], 'type': 'commit'}}
            if suffix == '/releases?per_page=100': return [draft]
            if suffix == '/releases/42': return release()
            self.fail('Unexpected API request: ' + path)

        def upload_after_main_advances(release_id, path):
            self.assertEqual(release_id, 42)
            self.assertIn(path.name, p.ASSETS)
            state['main'] = 'new-unreviewed-main'

        with patch.dict(p.os.environ, {'GITHUB_REPOSITORY': p.REPO, 'GITHUB_REF': p.BRANCH,
                                       'GITHUB_SHA': 'publisher-head'}), \
             patch.object(p, 'api', side_effect=fake_api) as api, \
             patch.object(p, 'validate_files'), \
             patch.object(p, 'validate_workflow_history'), \
             patch.object(p, 'upload_asset', side_effect=upload_after_main_advances) as upload, \
             patch.object(p, 'verify_public_downloads') as public_downloads, \
             patch('sys.argv', ['publisher']):
            with self.assertRaisesRegex(RuntimeError, 'Main changed during publication'):
                p.main()
            self.assertEqual(upload.call_count, len(p.ASSETS))
            self.assertEqual(api.call_args.args, (f'repos/{p.REPO}/git/ref/heads/main',))
            public_downloads.assert_not_called()

    def test_only_exact_publication_paths_are_excluded_from_application_build(self):
        base = 'on:\n  push:\n    branches: [main]\n  workflow_dispatch:\njobs:\n  full_test: required\n'
        current = base.replace('    branches: [main]\n', '    branches: [main]\n' + p.PUBLICATION_EXCLUSIONS)
        p.validate_workflow_parity(base, current)
        for changed in (current.replace('full_test: required', 'full_test: skipped'),
                        current.replace('scripts/publish_verified_014.py', 'luma/**'), base,
                        current + '# other change\n'):
            with self.subTest(changed=changed), self.assertRaises(RuntimeError):
                p.validate_workflow_parity(base, changed)

    def test_previous_013_tag_remains_pinned(self):
        self.assertEqual(p.PREVIOUS_013_SHA, 'caa4215f4177b3082ec94af2f001bd93154a8d81')
        source = Path(p.__file__).read_text()
        self.assertEqual(source.count("validate_tag(api(f'repos/{REPO}/git/ref/tags/v0.1.3'), 'v0.1.3', PREVIOUS_013_SHA)"), 2)

    def test_no_rebuild_or_force_or_deletion(self):
        source = Path(p.__file__).read_text()
        self.assertNotIn("'DELETE'", source)
        self.assertNotIn('force=true', source)
        self.assertNotIn('PyInstaller', source)
        self.assertNotIn('build_windows.ps1', source)
        self.assertIn("'draft': True", source)
        self.assertIn('verify_public_downloads()', source)


if __name__ == '__main__':
    unittest.main()
