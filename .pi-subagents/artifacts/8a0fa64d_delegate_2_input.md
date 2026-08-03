# Task for delegate

Security review of the Tauri desktop app implementation. Read and review these files for authentication gaps, input validation/sanitization, hardcoded secrets, injection vulnerabilities, and attack surface:

Python backend:
- /home/dev/src/transcribe/src/web/app.py (shutdown secret, feeder token, host allowlist, origin check, moonshine endpoint, traversal rejection)
- /home/dev/src/transcribe/src/live/session.py (TAURI_EVENT stdout emission)
- /home/dev/src/transcribe/src/live/moonshine_fetch.py (license gate)
- /home/dev/src/transcribe/src/ffmpeg_patch.py (subprocess patching)
- /home/dev/src/transcribe/main.py (dynamic port binding)

Rust shell:
- /home/dev/src/transcribe/src-tauri/src/process.rs (spawn, env injection, shutdown secret generation)
- /home/dev/src/transcribe/src-tauri/src/downloader.rs (zip extraction, path traversal, manifest validation)
- /home/dev/src/transcribe/src-tauri/src/webview.rs (navigation policy, permission handler)
- /home/dev/src/transcribe/src-tauri/src/capture.rs (WS feeder auth)
- /home/dev/src/transcribe/src-tauri/tauri.conf.json (CSP, capabilities)
- /home/dev/src/transcribe/src-tauri/capabilities/default.json

Focus on: shutdown secret comparison (timing-safe?), feeder token validation, zip-slip protection, DNS rebinding defense, CSP adequacy, WebView2 permission scope, env var injection, HF token storage. Do NOT run builds or tests. Static review only. Write your findings to /tmp/security-review.md with items tagged [critical/major/minor/info] and a summary at the top.

---
**Output:**
Write your findings to exactly this path: /tmp/security-review.md
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