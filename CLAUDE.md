# Current Project: ADHOC — v0.1.0-alpha.2 Windows desktop release

## Goal
- Make the Windows desktop workflow pass, then publish the unsigned NSIS installer and backend runtime assets on the public `v0.1.0-alpha.2` pre-release.

## Key files
- `.github/workflows/desktop-windows.yml`
- `packaging/backend/{python-pin.json,requirements-windows.lock,make-lock.ps1,build.ps1}`
- `packaging/ffmpeg/{pin.json,fetch.ps1}`
- `src-tauri/{Cargo.toml,Cargo.lock,tauri.conf.json,src/*.rs}`
- `.claude/docs/decisions/task-ADHOC-desktop-alpha2-build.md`

## Architecture
- Use dispatch for lock/bootstrap and compile/build iteration; reserve the immutable version tag for the final release transaction.
- Gate release on Python tests, relocatable backend archive, LGPL FFmpeg validation, and NSIS build; keep hardware-dependent E2E experimental.
- Publish backend zip/checksum/manifest and NSIS together because the bundled manifest downloads from that public release.

## Decisions
- Generate the authoritative dependency lock on Windows; uv cross-resolution is diagnostic only.
- Add a fast Windows `cargo check --locked` layer before expensive packaging and commit the verified `Cargo.lock`.
- Create a draft pre-release before tagging, upload assets on the tag run, verify them, then publish; never move a release tag.
- v1 remains unsigned and updater artifacts remain disabled.
