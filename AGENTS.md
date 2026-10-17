# Repository workflow

- Work directly on `main` for normal changes: edit, validate locally, commit and
  push to `origin/main`. Do not create another branch or worktree unless the user
  explicitly requests one.
- Prefer the local test entry point (`测试.bat` or `scripts/run_tests.py`) for
  source regression checks. Run checks appropriate to the change before pushing;
  use the documented native Windows and release checks when applicable.
- Before deleting an existing branch, merge any commits missing from `main` and
  verify that the pushed `main` contains its tip. Preserve uncommitted work and
  existing release tags.
