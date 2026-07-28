# Task for reviewer

Tauri desktop standalone app (NSKETCH-TBD) — review phase.

--tier=L
--task-file=.claude/docs/decisions/task-NSKETCH-TBD-tauri-desktop.md
--linear-id=NSKETCH-TBD

Scope to review: the two commits on branch feature/tauri-desktop vs main:
- b75ab3a feat(desktop): Tauri v2 standalone desktop app scaffold
- cc3f8b7 fix(desktop): address OpenCode review findings (Python + Rust pre-build)
Run `git diff main...HEAD` for the full change set.

Context you must read first:
1. The task file above (Brief / Decision Log / Design / Implementation Notes / 未検証事項 / リリースブロッカー).
2. .claude/docs/research/tauri-desktop-review-opencode.md — an INFORMAL OpenCode review already done. The cc3f8b7 commit addressed a subset of its findings; the rest are explicitly deferred to the first Windows build per the task file policy ("windows-rs API シグネチャは初回ビルドで機械的修正前提"). Adjudicate formally which deferred items are acceptable for v1 vs which are release-blocking.

IMPORTANT environment constraints (honor the task file's "憶測で動作主張しない" rule):
- This sandbox BLOCKS `python`, `pytest`, `uv venv`, `uv run pytest`, and any venv path under /tmp/claude-1000 via a permission check. I could NOT run pytest here. Do NOT claim tests pass. If you also cannot run them, record "テスト未実行（sandbox 制約）" honestly in the Review section — that is NOT a FAIL by itself, it is a documented verification gap.
- Phase B (Rust, src-tauri/) and Phase C (packaging/CI) are entirely 未検証 — cannot compile on WSL2. Review them STATICALLY only; do not assert they build or run.
- Linear MCP is not configured for this task (local task-file operation, per Decision Log). SKIP all Linear comment posts; record entries in the task file's Decision Log instead.

Run the 4 reviewers in parallel (Quality / Logic / Security / Simplify) per the skill, integrate as Lead, then write the Review section (判定 PASS/FAIL + 統合結果 + 動作検証結果 + 申し送り事項) into the task file and append a `[team-review]` Decision Log entry. Report the final verdict back to me.

## Acceptance Contract
Acceptance level: reviewed
Completion is not accepted from prose alone. End with a structured acceptance report.

Criteria:
- criterion-1: Implement the requested change without widening scope
- criterion-2: Return evidence sufficient for an independent acceptance review

Required evidence: changed-files, tests-added, commands-run, validation-output, residual-risks, no-staged-files

Review gate: required by reviewer.

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
    },
    {
      "id": "criterion-2",
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