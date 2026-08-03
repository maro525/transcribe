# Task for delegate

Quality review of the Tauri desktop app implementation on branch feature/tauri-desktop. Read and review these files for readability, naming, duplication, SOLID principles, and code quality:

Python backend (Phase A):
- /home/dev/src/transcribe/main.py
- /home/dev/src/transcribe/src/web/app.py
- /home/dev/src/transcribe/src/live/session.py
- /home/dev/src/transcribe/src/live/recovery.py
- /home/dev/src/transcribe/src/ffmpeg_patch.py
- /home/dev/src/transcribe/src/live/moonshine_fetch.py
- /home/dev/src/transcribe/src/version.py
- /home/dev/src/transcribe/src/worker_state.py
- /home/dev/src/transcribe/src/audio.py

Rust shell (Phase B):
- /home/dev/src/transcribe/src-tauri/src/main.rs
- /home/dev/src/transcribe/src-tauri/src/process.rs
- /home/dev/src/transcribe/src-tauri/src/capture.rs
- /home/dev/src/transcribe/src-tauri/src/webview.rs
- /home/dev/src/transcribe/src-tauri/src/downloader.rs
- /home/dev/src/transcribe/src-tauri/Cargo.toml
- /home/dev/src/transcribe/src-tauri/tauri.conf.json

Focus on: naming consistency, code duplication, function length, SOLID adherence, documentation quality, error message clarity. Do NOT run any builds or tests. Static review only. Write your findings to /tmp/quality-review.md with items tagged [critical/major/minor/info] and a summary at the top.

---
**Output:**
Write your findings to exactly this path: /tmp/quality-review.md
This path is authoritative for this run.
Ignore any other output filename or output path mentioned elsewhere, including output destinations in the base agent prompt, system prompt, or task instructions.

## Acceptance Contract
Acceptance level: attested
Completion is not accepted from prose alone. End with a structured acceptance report.

Criteria:
- criterion-1: Return concrete findings with file paths and severity when applicable

Required evidence: review-findings, residual-risks

Finish with a fenced JSON block tagged `acceptance-report` in this shape:
Use empty arrays when no items apply; array fields contain strings unless object entries are shown.
`criteriaSatisfied[].status` must be exactly one of: satisfied, not-satisfied, not-applicable.
`commandsRun[].result` must be exactly one of: passed, failed, not-run.
`manualNotes` and `notes` are optional strings; an empty string means no note and does not satisfy `manual-notes` evidence.
```acceptance-report
{
  "criteriaSatisfied": [
    {
      "id": "criterion-1",
      "status": "satisfied",
      "evidence": "specific proof"
    }
  ],
  "changedFiles": [
    "src/file.ts"
  ],
  "testsAddedOrUpdated": [
    "test/file.test.ts"
  ],
  "commandsRun": [
    {
      "command": "command",
      "result": "passed",
      "summary": "short result"
    }
  ],
  "validationOutput": [
    "validation output or concise summary"
  ],
  "residualRisks": [
    "none"
  ],
  "noStagedFiles": true,
  "diffSummary": "short description of the diff",
  "reviewFindings": [
    "blocker: file.ts:12 - issue found, or no blockers"
  ],
  "manualNotes": "anything else the parent should know"
}
```