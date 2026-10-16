"""One-time recovery guards; no network, real release writes, or large files."""
from copy import deepcopy
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main
from unittest.mock import patch
import finish_initial_release as recovery


def release():
    return {"id": recovery.RELEASE_ID, "tag_name": recovery.TAG,
            "target_commitish": recovery.BUILD_SHA, "draft": True, "prerelease": False,
            "assets": [{"name": n, "size": s, "digest": "sha256:" + h, "state": "uploaded"}
                       for n, (s, h) in recovery.ASSETS.items()]}


def build():
    repo = {"id": recovery.REPO_ID, "full_name": recovery.REPO}
    run = {"id": recovery.RUN_ID, "head_sha": recovery.BUILD_SHA, "head_branch": "main",
           "event": "push", "path": ".github/workflows/windows-release.yml",
           "repository": repo, "head_repository": repo}
    job = {"id": recovery.JOB_ID, "run_id": recovery.RUN_ID, "name": "windows", "conclusion": "success",
           "steps": [{"name": n, "conclusion": "success"} for n in recovery.REQUIRED_STEPS]}
    artifact = {"id": recovery.ARTIFACT_ID, "name": "luma-windows-0.1.0",
                "expired": False, "digest": recovery.ARTIFACT_DIGEST,
                "workflow_run": {"id": recovery.RUN_ID, "head_sha": recovery.BUILD_SHA,
                                 "repository_id": recovery.REPO_ID, "head_repository_id": recovery.REPO_ID}}
    return run, job, artifact


class RecoveryTests(TestCase):
    def test_exact_draft_and_published_release_are_valid(self):
        item = release()
        recovery.validate_release(item)
        item["draft"] = False
        recovery.validate_release(item)

    def test_wrong_release_metadata_is_rejected(self):
        for field, value in [("id", 1), ("tag_name", "other"), ("target_commitish", "a" * 40),
                             ("prerelease", True), ("draft", None)]:
            with self.subTest(field=field):
                item = release()
                item[field] = value
                with self.assertRaises(RuntimeError):
                    recovery.validate_release(item)

    def test_asset_digest_size_state_and_duplicates_are_rejected(self):
        for field, value in [("digest", "sha256:" + "a" * 64), ("size", 1), ("state", "new")]:
            with self.subTest(field=field):
                item = release()
                item["assets"][0][field] = value
                with self.assertRaises(RuntimeError):
                    recovery.validate_release(item)
        item = release()
        item["assets"][1] = deepcopy(item["assets"][0])
        with self.assertRaises(RuntimeError):
            recovery.validate_release(item)

    def test_tag_cannot_move(self):
        item = {"ref": "refs/tags/" + recovery.TAG,
                "object": {"type": "commit", "sha": recovery.BUILD_SHA,
                           "url": f"https://api.github.com/repos/{recovery.REPO}/git/commits/{recovery.BUILD_SHA}"}}
        recovery.validate_tag(item)
        item["object"]["sha"] = "a" * 40
        with self.assertRaises(RuntimeError):
            recovery.validate_tag(item)

    def test_exact_build_provenance_is_valid(self):
        recovery.validate_build(*build())

    def test_wrong_run_job_or_artifact_is_rejected(self):
        for index, field, value in [(0, "head_sha", "a" * 40), (0, "event", "pull_request"),
                                    (1, "conclusion", "failure"), (1, "steps", []),
                                    (2, "expired", True), (2, "digest", "wrong")]:
            with self.subTest(index=index, field=field):
                values = build()
                values[index][field] = value
                with self.assertRaises(RuntimeError):
                    recovery.validate_build(*values)

    def test_file_hashes_are_a_hard_gate(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            payloads = {"a.zip": b"one", "b.zip": b"two"}
            hashes = {n: hashlib.sha256(b).hexdigest() for n, b in payloads.items()}
            manifest = "".join(f"{h}  {n}\n" for n, h in hashes.items()).encode()
            payloads["SHA256SUMS"] = manifest
            specs = {n: (len(b), hashlib.sha256(b).hexdigest()) for n, b in payloads.items()}
            for n, b in payloads.items():
                (directory / n).write_bytes(b)
            with patch.object(recovery, "ASSETS", specs):
                recovery.validate_files(directory)
                (directory / "a.zip").write_bytes(b"bad")
                with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                    recovery.validate_files(directory)

    def test_unexpected_files_are_rejected(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "unknown").write_bytes(b"")
            with self.assertRaises(RuntimeError):
                recovery.validate_files(directory)


if __name__ == "__main__":
    main()
