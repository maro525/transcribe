# Current Project: ADHOC — v0.1.0-alpha.3 Windows desktop hotfix release

## Goal
- Shipped: fix the alpha.2 first-run regression (missing HF token killed the batch worker) and publish v0.1.0-alpha.3 with the NSIS installer and backend runtime.

## Key files
- `.github/workflows/desktop-windows.yml`
- `packaging/backend/`
- `packaging/ffmpeg/`
- `src-tauri/`
- `src/auth.py` / `src/worker.py` / `src/transcriber.py` (optional-HF-token path)
- `.claude/docs/decisions/task-ADHOC-desktop-alpha2-build.md` (alpha.2 record)
- `.claude/docs/decisions/task-ADHOC-hf-token-optional-alpha3.md` (alpha.3 record)

## Architecture
- Dispatch runs separate bootstrap/compile iteration from immutable tag publication.
- The release gates on tests, backend relocation, FFmpeg licensing, and NSIS build; experimental hardware/runtime smoke remains non-blocking.
- Backend runtime assets and installer share one public release to satisfy the bundled manifest URL contract.
- HF token is optional end-to-end: without it the batch worker transcribes single-speaker (`SPEAKER_00`); diarization load failure with a token set stays fatal.

## Decisions
- Fix Windows PowerShell 5.1 parsing before lock generation.
- Treat Windows-generated pip-tools lock as authoritative; use Linux uv cross-resolution only for diagnosis.
- Add Windows `cargo check --locked`, align wry/windows/webview2 dependencies, and commit `Cargo.lock`.
- Draft pre-release first, tag only after dispatch success, verify assets before publishing, and never move the tag.
- The tag-only release-upload step pins `draft: true` so asset upload never auto-publishes (softprops/action-gh-release defaults `draft: false`); publish only after post-upload re-download verification.
