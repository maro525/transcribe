# Current Project: ADHOC — v0.1.0-alpha.2 Windows desktop release

## Goal
- Converge the Windows CI via workflow dispatch, then attach the NSIS installer and backend runtime to the public alpha.2 pre-release.

## Key files
- `.github/workflows/desktop-windows.yml`
- `packaging/backend/`
- `packaging/ffmpeg/`
- `src-tauri/`
- `.claude/docs/decisions/task-ADHOC-desktop-alpha2-build.md`

## Architecture
- Dispatch runs separate bootstrap/compile iteration from immutable tag publication.
- The release gates on tests, backend relocation, FFmpeg licensing, and NSIS build; experimental hardware/runtime smoke remains non-blocking.
- Backend runtime assets and installer share one public release to satisfy the bundled manifest URL contract.

## Decisions
- Fix Windows PowerShell 5.1 parsing before lock generation.
- Treat Windows-generated pip-tools lock as authoritative; use Linux uv cross-resolution only for diagnosis.
- Add Windows `cargo check --locked`, align wry/windows/webview2 dependencies, and commit `Cargo.lock`.
- Draft pre-release first, tag only after dispatch success, verify assets before publishing, and never move the tag.
