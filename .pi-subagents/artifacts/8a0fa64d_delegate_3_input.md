# Task for delegate

Simplify review of the Tauri desktop app implementation. Read and review these files for over-complexity, unnecessary abstraction, dead code, and refactoring suggestions:

Rust shell:
- /home/dev/src/transcribe/src-tauri/src/main.rs
- /home/dev/src/transcribe/src-tauri/src/process.rs
- /home/dev/src/transcribe/src-tauri/src/capture.rs
- /home/dev/src/transcribe/src-tauri/src/downloader.rs
- /home/dev/src/transcribe/src-tauri/src/webview.rs
- /home/dev/src/transcribe/src-tauri/Cargo.toml

Python backend:
- /home/dev/src/transcribe/main.py
- /home/dev/src/transcribe/src/web/app.py
- /home/dev/src/transcribe/src/live/session.py
- /home/dev/src/transcribe/src/live/recovery.py

Also read packaging/CI files:
- /home/dev/src/transcribe/packaging/backend/build.ps1
- /home/dev/src/transcribe/packaging/ffmpeg/fetch.ps1
- /home/dev/src/transcribe/.github/workflows/desktop-windows.yml

Focus on: over-engineering, unnecessary abstractions, dead code, code that could be simplified, feature flags or config that add complexity without value, Cargo.toml dependency bloat. Do NOT run builds or tests. Static review only. Write your findings to /tmp/simplify-review.md with items tagged [critical/major/minor/info] and a summary at the top.

---
**Output:**
Write your findings to exactly this path: /tmp/simplify-review.md
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