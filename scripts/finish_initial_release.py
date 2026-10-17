#!/usr/bin/env python3
"""Publish only the already-tested, checksum-pinned Luma 0.1.0 draft."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from urllib.request import Request, urlopen

REPO = "Biscuits-Lab/Luma"
REPO_ID = 1328395764
BRANCH = "refs/heads/release/finish-v0.1.0"
TAG = "v0.1.0"
BUILD_SHA = "99841e74c180282f742847c0e471eed457999067"
RUN_ID = 38835689713
JOB_ID = 116764226364
ARTIFACT_ID = 12019242016
ARTIFACT_DIGEST = "sha256:f5f7c214120735633a694b8ee8b3b487e227bae1ae4f9d1a5ff6e54eea6809ae"
RELEASE_ID = 414441027
ASSETS = {
    "Luma-0.1.0-windows-x64.zip": (49155563, "e5e2cb21f31de70ecc4aefa890a8fbdee00158bf0971653d1c24931d79e2acd0"),
    "Luma-0.1.0-dependency-sources.zip": (90827440, "7bbfbf69412242908cba54f21d441b35435c656eaecfc52901432d0769ef3ba7"),
    "SHA256SUMS": (195, "2ea3fc5cd76e591631e26cbc2e1060dfe467339c3843d04bf4476e7cb372b409"),
}
REQUIRED_STEPS = {
    "Release safety regression tests", "Native Windows tests and folder bundle",
    "Smoke packaged Windows executable and real decoders",
    "Collect exact dependency source material", "Create portable ZIP and checksums",
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def api(path):
    request = Request("https://api.github.com/" + path, headers={
        "Authorization": "Bearer " + os.environ["GH_TOKEN"],
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Luma-bounded-initial-release-recovery",
    })
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def validate_tag(item):
    require(item.get("ref") == "refs/tags/" + TAG, "Wrong release tag")
    require(item.get("object") == {
        "sha": BUILD_SHA, "type": "commit",
        "url": f"https://api.github.com/repos/{REPO}/git/commits/{BUILD_SHA}",
    }, "Release tag does not match the tested commit")


def validate_release(item):
    require(item.get("id") == RELEASE_ID and item.get("tag_name") == TAG, "Wrong release identity")
    require(item.get("target_commitish") == BUILD_SHA, "Wrong release source commit")
    require(item.get("prerelease") is False and isinstance(item.get("draft"), bool), "Unexpected release status")
    assets = item.get("assets", [])
    require(len(assets) == len(ASSETS) and {a.get("name") for a in assets} == set(ASSETS), "Wrong release asset set")
    for asset in assets:
        size, digest = ASSETS[asset["name"]]
        require(asset.get("size") == size and asset.get("digest") == "sha256:" + digest
                and asset.get("state") == "uploaded", "Release asset integrity mismatch: " + asset["name"])


def validate_build(run, job, artifact):
    require(run.get("id") == RUN_ID and run.get("head_sha") == BUILD_SHA
            and run.get("head_branch") == "main" and run.get("event") == "push"
            and run.get("path") == ".github/workflows/windows-release.yml", "Wrong source build")
    for key in ("repository", "head_repository"):
        require(run.get(key, {}).get("id") == REPO_ID
                and run.get(key, {}).get("full_name") == REPO, "Wrong build repository")
    require(job.get("id") == JOB_ID and job.get("run_id") == RUN_ID
            and job.get("name") == "windows" and job.get("conclusion") == "success", "Windows job did not succeed")
    succeeded = {s["name"] for s in job.get("steps", []) if s.get("conclusion") == "success"}
    require(REQUIRED_STEPS.issubset(succeeded), "Required Windows checks did not succeed")
    require(artifact.get("id") == ARTIFACT_ID and artifact.get("name") == "luma-windows-0.1.0"
            and artifact.get("expired") is False and artifact.get("digest") == ARTIFACT_DIGEST, "Wrong build artifact")
    owner = artifact.get("workflow_run", {})
    require(owner.get("id") == RUN_ID and owner.get("head_sha") == BUILD_SHA
            and owner.get("repository_id") == REPO_ID and owner.get("head_repository_id") == REPO_ID,
            "Artifact source does not match tested build")


def validate_files(directory):
    require({p.name for p in directory.iterdir()} == set(ASSETS), "Unexpected downloaded artifact files")
    for name, (size, expected) in ASSETS.items():
        path = directory / name
        require(path.is_file() and not path.is_symlink() and path.stat().st_size == size, "Artifact size/type mismatch: " + name)
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        require(digest.hexdigest() == expected, "Artifact SHA-256 mismatch: " + name)
    manifest = {}
    for line in (directory / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        require(name not in manifest, "Duplicate checksum entry")
        manifest[name] = digest
    require(manifest == {name: item[1] for name, item in ASSETS.items() if name != "SHA256SUMS"},
            "Checksum manifest does not match the pinned release assets")


def validate_public_downloads():
    # Intentionally anonymous: verify the same public links an end user receives.
    for name, (size, expected) in ASSETS.items():
        request = Request(f"https://github.com/{REPO}/releases/download/{TAG}/{name}",
                          headers={"User-Agent": "Luma-public-download-verification"})
        digest = hashlib.sha256()
        received = 0
        with urlopen(request, timeout=60) as response:
            for block in iter(lambda: response.read(1024 * 1024), b""):
                received += len(block)
                require(received <= size, "Public download exceeds expected size: " + name)
                digest.update(block)
        require(received == size and digest.hexdigest() == expected,
                "Public download size/SHA-256 mismatch: " + name)
        print(f"Anonymous download verified: {name}; {received} bytes; sha256:{expected}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, default=Path("artifacts"))
    args = parser.parse_args()
    require(os.environ.get("GITHUB_REPOSITORY") == REPO and os.environ.get("GITHUB_REF") == BRANCH,
            "Recovery is restricted to the reviewed repository and branch")
    repository = api(f"repos/{REPO}")
    require(repository.get("id") == REPO_ID and repository.get("private") is False, "Wrong public repository")
    run = api(f"repos/{REPO}/actions/runs/{RUN_ID}")
    job = api(f"repos/{REPO}/actions/jobs/{JOB_ID}")
    artifact = api(f"repos/{REPO}/actions/artifacts/{ARTIFACT_ID}")
    validate_build(run, job, artifact)
    # The download action's digest warning is not a hard gate. These checks are.
    validate_files(args.artifacts)
    validate_tag(api(f"repos/{REPO}/git/ref/tags/{TAG}"))
    release = api(f"repos/{REPO}/releases/{RELEASE_ID}")
    validate_release(release)
    if release["draft"]:
        subprocess.run(["gh", "api", f"repos/{REPO}/releases/{RELEASE_ID}", "--method", "PATCH",
                        "-F", "draft=false", "-f", "make_latest=true"], check=True, stdout=subprocess.DEVNULL)
    validate_tag(api(f"repos/{REPO}/git/ref/tags/{TAG}"))
    published = api(f"repos/{REPO}/releases/{RELEASE_ID}")
    validate_release(published)
    require(published["draft"] is False, "Release remains a draft")
    public = api(f"repos/{REPO}/releases/tags/{TAG}")
    validate_release(public)
    require(public["draft"] is False, "Tag lookup does not expose the published release")
    validate_public_downloads()
    print(f"Verified published release: https://github.com/{REPO}/releases/tag/{TAG}")
    for name, (size, digest) in ASSETS.items():
        print(f"{name}: {size} bytes; sha256:{digest}")


if __name__ == "__main__":
    main()
