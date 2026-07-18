# Task: ADHOC — Dashboard (index.html) redesign to "Aetheric Precision"

## Meta
- linear_id: none (explicitly no Linear ticket)
- tier: S
- created: 2026-07-18
- status: done

## Brief
Restyle src/web/templates/index.html to match detail.html's "Aetheric Precision"
design system (same :root tokens + Google Fonts link). Keep the current
single-column (max-width ~1040px) layout — do NOT add the sidebar/topnav that
appear in the Stitch screenshot (decoration only). Display-only change:
SSE/polling/reconnect behavior untouched.

## Decision Log
- Card look applied to `#jobs-root` container (not JS-generated wrapper) so both
  JS-rendered and server-fallback (_jobs.html) content get the card for free.
- State pills: Japanese labels (完了/処理中/待機中/エラー) via a JS label map and
  the same Jinja mapping detail.html uses; internal state keys, `state-{state}`
  classes and stateOrder sort untouched.
- Connection status: `setConnectionStatus` additionally toggles an `is-live`
  class (green dot via ::before) when text === "live"; wording unchanged.
- _jobs.html updated minimally (labels, 詳細 → link, error class) so polling
  fallback matches the new look; `.status-bar` class kept (JS querySelector).
- Removed dead index.html styles (pre.transcript, .viewer-title) left over from
  the deprecated inline transcript viewer.

## Design
Tokens copied verbatim from detail.html (:root + prefers-color-scheme: dark),
color-scheme: light dark kept. Fonts: Inter (body) + Plus Jakarta Sans
(headings) via the same preconnect+link. Table: no vertical rules, hairline row
dividers, roomy padding, faint hover, uppercase muted headers. Empty state
centered muted with input/ code chip.

## Implementation Notes
- Branch: feature/dashboard-aetheric-index
- Files: src/web/templates/index.html (full restyle), src/web/templates/_jobs.html
  (labels/classes only).
- index.html: tokens + dark override + fonts link copied verbatim from
  detail.html; card on #jobs-root (overflow-x auto, min-width 560px table on
  <=640px); pill .state styles identical to detail.html; JS display-only edits:
  stateLabels map, is-live class toggle in setConnectionStatus, td classes
  (file/num), "詳細 →" link text, .cell-error class replacing inline styles.
  Removed dead pre.transcript / .viewer-title CSS (deprecated inline viewer).
- SSE/polling/reconnect code untouched (verified via diff grep: zero handler
  lines changed).

## Review
- Self-review (tier S) PASS.
- Verified in real app (uvicorn + mock store, scratch venv):
  light/dark x desktop(1280)/mobile(390) x jobs/empty — 8 screenshots OK.
- Live SSE path confirmed (green dot + "live", renderJobs output on screen);
  /jobs fallback HTML confirmed to carry new classes + Japanese labels while
  keeping .status-bar (JS querySelector contract).
- No tests reference the templates; no Python changes.

## Deploy
- PR: https://github.com/maro525/transcribe/pull/16 (feature/dashboard-aetheric-index -> main)
- Commit: d374264 feat(web): restyle dashboard index to Aetheric Precision design system
- No Linear ticket (explicitly requested).
