# Release Guide

This project follows SemVer (`MAJOR.MINOR.PATCH`) and releases from `main`.

## 1. Prepare

1. Ensure `main` is up to date and clean:
   - `git checkout main`
   - `git pull --ff-only origin main`
   - `git status`
2. Run tests locally:
   - `python3 -m unittest -q`

## 2. Bump Version

Update version fields consistently:

1. `scripts/recall.py`
   - `SKILL_VERSION = "x.y.z"`
2. `SKILL.md`
   - `metadata.version: "x.y.z"`
3. `CHANGELOG.md`
   - Move relevant notes from `Unreleased` into a new `## x.y.z` section

## 3. Release Commit

Commit version and changelog updates:

- `git add scripts/recall.py SKILL.md CHANGELOG.md`
- `git commit -m "release: vX.Y.Z"`

## 4. Tag and Push

Create and publish an annotated tag:

- `git tag -a vX.Y.Z -m "release vX.Y.Z"`
- `git push origin main`
- `git push origin vX.Y.Z`

## 5. Verify

After push:

1. Confirm GitHub Actions checks pass on `main`.
2. Verify runtime metadata:
   - `python3 scripts/recall.py --version`
3. Verify diagnostics:
   - `python3 scripts/recall.py --doctor`

## 6. Rollback (if needed)

If a bad release tag was pushed:

1. Revert commit on `main` via a new revert commit (do not rewrite history).
2. Create a new patch release tag (`vX.Y.(Z+1)`).
