#!/usr/bin/env python3
"""Bounded v0.1.0 release gate/publisher using the workflow's temporary token."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
from urllib.error import HTTPError
from urllib.request import Request, urlopen

TAG = "v0.1.0"
ASSETS = {"Luma-0.1.0-windows-x64.zip", "Luma-0.1.0-dependency-sources.zip", "SHA256SUMS"}


def api(path):
    request = Request(f"https://api.github.com/{path}", headers={
        "Authorization": "Bearer " + os.environ["GH_TOKEN"],
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
    })
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def release(repo):
    metadata = api(f"repos/{repo}")
    if not metadata or metadata.get("private") or metadata.get("full_name", "").lower() != "biscuits-lab/luma":
        raise RuntimeError("This bounded release is authorized only for the public Biscuits-Lab/Luma repository")
    return api(f"repos/{repo}/releases/tags/{TAG}")


def published_complete(item):
    if item and not item["draft"]:
        present = {asset["name"] for asset in item["assets"]}
        if not ASSETS.issubset(present):
            raise RuntimeError("Published release is incomplete; inspect it instead of overwriting it automatically")
        return True
    return False


def ensure_tag_matches(repo: str, expected_sha: str, *, create: bool = False) -> bool:
    """Never move an existing tag; peel annotated tags to the actual commit."""
    reference = api(f"repos/{repo}/git/ref/tags/{TAG}")
    if reference is None:
        if not create:
            return False
        subprocess.run(["gh", "api", f"repos/{repo}/git/refs", "--method", "POST",
                        "-f", f"ref=refs/tags/{TAG}", "-f", f"sha={expected_sha}"], check=True)
        reference = api(f"repos/{repo}/git/ref/tags/{TAG}")
        if reference is None:
            raise RuntimeError("Created release tag could not be verified")
    obj = reference["object"]
    seen = set()
    while obj["type"] == "tag":
        if obj["sha"] in seen or len(seen) >= 10:
            raise RuntimeError("Invalid annotated release tag chain")
        seen.add(obj["sha"])
        annotated = api(f"repos/{repo}/git/tags/{obj['sha']}")
        if annotated is None:
            raise RuntimeError("Annotated release tag could not be resolved")
        obj = annotated["object"]
    if obj["type"] != "commit" or obj["sha"] != expected_sha:
        raise RuntimeError(f"Existing {TAG} does not resolve to this tested commit; refusing to move it or publish mismatched binaries")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["check", "publish"])
    parser.add_argument("--artifacts", type=Path, default=Path("artifacts"))
    args = parser.parse_args()
    repo = os.environ["GITHUB_REPOSITORY"]
    item = release(repo)
    if args.mode == "check":
        should_build = not published_complete(item)
        if should_build:
            ensure_tag_matches(repo, os.environ["GITHUB_SHA"])
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as target:
            target.write(f"should_build={str(should_build).lower()}\n")
        print("Initial release needs a verified Windows build" if should_build else "Complete v0.1.0 release already exists; skipping")
        return 0
    if published_complete(item):
        print("Complete release already exists; no assets overwritten")
        return 0
    sha = os.environ["GITHUB_SHA"]
    paths = [args.artifacts / name for name in sorted(ASSETS)]
    if any(not p.is_file() for p in paths):
        raise RuntimeError("Required built release assets are missing")
    notes = Path("docs/RELEASE_NOTES_0.1.0.md")
    # An existing tag takes precedence over --target in GitHub release creation.
    # Explicitly pin a missing tag, or verify the existing peeled tag, first.
    ensure_tag_matches(repo, sha, create=True)
    if item:
        if item["target_commitish"] != sha:
            raise RuntimeError("Existing draft targets another commit; inspect it before publishing")
    else:
        subprocess.run(["gh", "release", "create", TAG, "--repo", repo, "--target", sha,
                        "--draft", "--title", "Luma 0.1.0", "--notes-file", str(notes)], check=True)
    if not ensure_tag_matches(repo, sha):
        raise RuntimeError("Release tag disappeared during publication")
    subprocess.run(["gh", "release", "upload", TAG, "--repo", repo, "--clobber", *map(str, paths)], check=True)
    draft = release(repo)
    if not draft or not draft["draft"] or not ASSETS.issubset({a["name"] for a in draft["assets"]}):
        raise RuntimeError("Draft release asset verification failed")
    if not ensure_tag_matches(repo, sha):
        raise RuntimeError("Release tag disappeared during publication")
    subprocess.run(["gh", "release", "edit", TAG, "--repo", repo, "--draft=false", "--latest"], check=True)
    if not ensure_tag_matches(repo, sha):
        raise RuntimeError("Release tag disappeared during publication")
    if not published_complete(release(repo)):
        raise RuntimeError("Release publication could not be verified")
    print(f"Published https://github.com/{repo}/releases/tag/{TAG}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
