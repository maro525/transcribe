# Task: NSKETCH-732 — SSE realtime planning

## Meta
- linear_id: NSKETCH-732
- tier: L
- created: 2026-05-26T03:08:36
- status: done

## Brief
### Current State
- FastAPI ベースの Transcribe Dashboard があり、`src/web/app.py` で `/`, `/jobs`, `/jobs/{filename}/transcript` を提供している。
- `src/web/templates/index.html` は htmx で `/jobs` を `load, every 2s` ポーリングし、`_jobs.html` のテーブル partial を差し替えている。
- `src/status.py` の `StatusStore` はプロセス内 in-memory state を `threading.Lock` で保護しており、永続キューやDBはない。
- `src/worker.py` は FastAPI lifespan から daemon thread として起動され、ファイル検知・処理開始・segment完了・done/error・system message を同期的に `store` へ反映している。
- テストディレクトリや既存プロジェクト設計文書は現時点で見当たらない。

### Goal
- Dashboard を 2秒 polling から Server-Sent Events (SSE) によるリアルタイム更新へ移行するための仕様と実装計画を策定する。
- synchronous worker thread + in-memory store の現状を活かし、依存追加なしで安全に段階導入できる設計にする。

### Scope
- SSE endpoint 契約、event model、payload shape、reconnect、heartbeat、fallback、frontend update、backend notification、testing、rollout/risks を定義する。
- 実装対象候補は主に `src/status.py`, `src/web/app.py`, `src/web/templates/index.html`, 必要に応じて `_jobs.html` と新規/既存テスト。
- `/jobs` partial endpoint と transcript 表示 endpoint は互換性・fallback・デバッグ用途で維持する。

### Constraints
- 計画フェーズのみ。コード実装は行わない。
- 追加依存は強い理由がない限り避ける。
- 現在の worker は同期 thread のまま維持する。
- state は引き続きプロセス内メモリで扱い、再接続時は差分再送より snapshot 復元を優先する。
- ブラウザ互換性が必要な場合は現行 htmx polling fallback を残す。

### Success Criteria
- 新規/更新 job と system message が通常時は polling なしで即時に画面へ反映される。
- SSE 接続直後・再接続後に dashboard が現在状態へ復元される。
- worker thread と SSE streaming の境界で deadlock や全体停止を起こさない。
- slow client / disconnected client が worker 更新や他クライアントに影響しない。
- SSE 非対応・連続接続失敗時に `/jobs` polling fallback で従来動作を維持できる。
- heartbeat により idle timeout による意図しない切断を軽減できる。

## Decision Log
- 2026-05-26T03:08:36 [startproject] PRE: Began planning for NSKETCH-732. Loaded available project context; `.claude/rules/`, `.claude/docs/DESIGN.md`, `AGENTS.md`, and `CLAUDE.md` were not present in the repository, so planning is based on direct code inspection and the provided task context.
- 2026-05-26T03:08:36 [startproject] DECISION: Treat this as tier=L planning because it changes backend streaming contracts, frontend update architecture, reconnect/fallback behavior, and testing/rollout strategy.
- 2026-05-26T03:08:36 [startproject] DECISION: Use native browser `EventSource` rather than htmx SSE extension for the primary realtime path, because no dependency addition is needed and event-specific JSON handling/reconnect state is clearer.
- 2026-05-26T03:08:36 [startproject] DECISION: Keep htmx and existing `/jobs` endpoint for transcript loading, fallback, debug visibility, and incremental rollout rather than replacing all frontend behavior at once.
- 2026-05-26T03:08:36 [startproject] DECISION: Preserve the synchronous worker thread and in-memory `StatusStore`; add a thread-safe publish/subscribe notification layer around store mutations instead of introducing a broker, DB, or async worker rewrite.
- 2026-05-26T03:08:36 [startproject] DECISION: Reconnect semantics will favor sending a fresh `snapshot` on every connection over exact `Last-Event-ID` replay, because current state is small and in-memory history is not durable.
- 2026-05-26T03:08:36 [startproject] DECISION: Use heartbeat comments/events at a fixed interval to keep long-lived connections active through browsers/proxies while avoiding UI re-render work.
- 2026-05-26T03:19:57 [startproject] POST: Posted Linear planning-complete comment to NSKETCH-732 and requested Gate 1 review because this tier=L change has event-granularity, UI fallback, throttling, and deployment-assumption decisions to confirm before implementation.
- 2026-05-26T03:31:00 [team-implement] POST: Implemented the approved SSE v1 plan with in-memory StatusStore pub/sub, FastAPI `/events`, native EventSource rendering, `/jobs` polling fallback, and local syntax/smoke verification. Full FastAPI endpoint smoke test could not run in the current shell because FastAPI is not installed in the active Python environment.
- 2026-05-26T04:55:00 [team-review] POST: Completed tier=L review.判定は FAIL。bounded subscriber queue overflow 時の silent event loss が snapshot/reset recovery を伴わず UI を永続 stale にし得る点と、subscribe 後 snapshot 取得までの race で snapshot 後に古い queued event が流れる点を major として検出。py_compile / StatusStore smoke / diff whitespace は pass、FastAPI endpoint と browser QA は依存未導入のため未実行。
- 2026-05-26T05:05:00 [team-implement] POST: Review FAIL retry fixed major findings by adding explicit queue overflow `reset` recovery and atomic `subscribe_with_snapshot()` with snapshot event id filtering. Also addressed small fallback status duplication and fallback transcript URL encoding issues. Verification passed for py_compile, StatusStore smoke, overflow recovery smoke, SSE formatter smoke, and diff whitespace; FastAPI endpoint/browser checks remain blocked by missing FastAPI dependency in the active Python environment.
- 2026-05-26T05:16:00 [team-review] POST: Re-reviewed the retry fix for NSKETCH-732. 判定は PASS。prior major findings are resolved: queue overflow now emits explicit `reset` recovery, and initial snapshot is acquired atomically with subscriber registration plus stale queued event filtering. py_compile, diff whitespace, StatusStore pub/sub smoke, overflow recovery smoke, and snapshot ordering smoke passed; FastAPI endpoint/browser verification remains blocked by missing FastAPI in the active Python environment.
- 2026-05-26T14:10:29+09:00 [deploy] POST: Finalized deployment record for NSKETCH-732 after PASS review. Local verification re-ran py_compile, git diff --check, StatusStore pub/sub smoke, overflow recovery smoke, and format_sse smoke successfully. Push/PR creation was not attempted because no git remote is configured; FastAPI endpoint/browser verification remains blocked by missing FastAPI in the active Python environment.

## Design
### Recommended Architecture

Adopt a minimal SSE architecture:

1. `StatusStore` remains the single source of truth for jobs and system message.
2. Store mutations publish lightweight `StoreEvent` objects to per-client queues.
3. `GET /events` opens a `text/event-stream` response.
4. Each SSE connection receives an immediate `snapshot` followed by queued store events and periodic heartbeat messages.
5. The frontend uses native `EventSource` to render snapshots and targeted job updates.
6. Existing htmx `/jobs` polling remains available but only starts as fallback when SSE is unsupported or repeatedly fails.

### Native EventSource vs htmx SSE Extension

Decision: **native `EventSource` + small inline/dashboard JavaScript**.

Rationale:
- No new dependency is required; `EventSource` is a browser standard.
- Current realtime need is state/event driven, not just server-rendered HTML swaps.
- JSON payloads let the frontend update only system message or one job row instead of replacing the whole table for every event.
- Reconnect status, fallback threshold, and connection indicator are easier to control explicitly.
- htmx should remain for the transcript detail link (`hx-get="/jobs/{filename}/transcript"`) because that behavior already works and is orthogonal.

Do not use htmx SSE extension initially unless implementation strongly prefers HTML-fragment streaming over JSON events. If selected later, document the extra dependency/script and re-evaluate targeted updates.

### Endpoint Contract

#### `GET /events`

Response headers:

```http
Content-Type: text/event-stream; charset=utf-8
Cache-Control: no-cache
Connection: keep-alive
X-Accel-Buffering: no
```

Notes:
- `X-Accel-Buffering: no` is useful for nginx-like proxies; harmless otherwise.
- Endpoint is read-only and long-lived.
- Authentication is out of scope unless the app later gains auth; EventSource sends cookies automatically for same-origin requests.
- On connect, always emit a `retry: 2000` directive and a `snapshot` event.
- `Last-Event-ID` may be accepted/logged but is not required for replay in v1.

Example stream:

```text
retry: 2000

event: snapshot
id: 101
data: {"system_message":"ready (device: cuda, whisper: medium)","jobs":[...]}

event: job_updated
id: 102
data: {"job":{"filename":"meeting.wav","state":"processing","segments_completed":3,"current_text":"[SPEAKER_00] ...","started_at":"2026-05-26T03:10:00","finished_at":null,"output_path":null,"error":null}}

event: heartbeat
data: {"ts":"2026-05-26T03:10:15"}
```

### Event Model

| Event | When | Payload | UI behavior |
| --- | --- | --- | --- |
| `snapshot` | immediately on connect/reconnect | `{system_message, jobs}` | rebuild status bar and jobs table from current state |
| `job_registered` | `store.register(filename)` creates or confirms a queued job | `{job}` | insert/update one row; sort according to backend order or request snapshot |
| `job_updated` | `store.update(filename, **changes)` mutates a job | `{job}` | update that row; if `done`, show transcript link; if `error`, show error text |
| `system_message` | `store.set_system_message(message)` | `{system_message}` | update status bar only |
| `heartbeat` | no state events for heartbeat interval | `{ts}` or comment-only | no table update; optional connection freshness indicator |
| `reset` | future/manual recovery event | `{reason}` | request `/jobs` or wait for next `snapshot`; reserved for v2 |

Implementation simplification: `job_registered` may be folded into `job_updated` if the team wants fewer event types, but keeping it explicit makes testing and debugging easier.

### Payload Shape

Canonical job JSON should mirror `Job` dataclass fields and avoid exposing local file contents:

```json
{
  "filename": "meeting.wav",
  "state": "processing",
  "segments_completed": 3,
  "current_text": "[SPEAKER_00] hello",
  "started_at": "2026-05-26T03:10:00",
  "finished_at": null,
  "output_path": null,
  "error": null
}
```

Snapshot JSON:

```json
{
  "system_message": "ready (device: cuda, whisper: medium)",
  "jobs": [
    {
      "filename": "meeting.wav",
      "state": "processing",
      "segments_completed": 3,
      "current_text": "[SPEAKER_00] hello",
      "started_at": "2026-05-26T03:10:00",
      "finished_at": null,
      "output_path": null,
      "error": null
    }
  ]
}
```

Security/escaping:
- Treat `filename`, `current_text`, and `error` as untrusted display text in JavaScript.
- Use `textContent` for text nodes, not `innerHTML`, except for static template fragments generated by code.
- URL-encode filename when constructing `/jobs/{filename}/transcript` links.
- Do not expose transcript text through SSE; keep transcript fetch explicit via existing endpoint.

### Backend Notification Strategy

Add a small notification layer to `StatusStore`:

- Add `self._version: int` for monotonic event ids.
- Add `self._subscribers: set[queue.Queue[StoreEvent]]` or similar.
- Add `subscribe(maxsize=N) -> queue.Queue[StoreEvent]` and `unsubscribe(queue)`.
- Add `snapshot() -> dict` returning a serializable `{system_message, jobs}` copy.
- Add `_publish(event_type, payload)` that increments `_version` and attempts non-blocking enqueue for each subscriber.

Threading rules:
- Keep `threading.Lock` for state mutation.
- Copy the event payload while holding the lock, but avoid blocking queue operations under the lock if possible.
- Never let a full or broken subscriber queue block `worker.run` or store updates.
- For full queues, prefer dropping that subscriber's oldest queued item or enqueueing a `snapshot_required`/`reset` marker. Since reconnect always snapshots, small event loss is acceptable if followed by recovery.

SSE endpoint implementation approach:
- Use FastAPI `StreamingResponse` with an async generator.
- The generator emits initial `snapshot`, then waits for subscriber queue events with a timeout matching heartbeat interval.
- Because the producer is a sync thread, use either:
  - `asyncio.to_thread(queue.get, True, timeout)` in the generator, or
  - a sync generator backed by `queue.Queue` passed to `StreamingResponse`.
- Prefer the simpler pattern that tests cleanly in this codebase; avoid introducing `sse-starlette` unless native formatting becomes problematic.
- In generator `finally`, call `store.unsubscribe(subscriber)`.

SSE formatting helper:
- Centralize formatting in a helper such as `format_sse(event: str, data: dict, event_id: int | None = None, retry: int | None = None) -> str`.
- JSON encode with `ensure_ascii=False`.
- Split multi-line data defensively by prefixing each line with `data:`.

### Frontend Update Strategy

Target structure:
- Replace the polling container in `index.html` with stable nodes: `#status-bar`, `#jobs-root`, optional `#connection-status`, and existing `#viewer`.
- On page load:
  1. If `window.EventSource` exists, open `new EventSource('/events')`.
  2. Render `snapshot` when received.
  3. Apply targeted updates for job/system events.
  4. If SSE unsupported or repeatedly errors, start legacy polling with htmx or a small fetch loop against `/jobs`.

Rendering approach:
- Keep rendering logic small and explicit in `index.html` or move to a static JS file only if project gains static asset structure.
- `snapshot`: sort/rebuild the complete jobs table using server-defined order from payload.
- `job_registered` / `job_updated`: update local `Map<filename, job>`, then re-render the table. Given likely small job counts, full table re-render from client-side state is acceptable and less bug-prone than complex row movement.
- `system_message`: update only status text.
- Preserve transcript display link with htmx attributes. After client-side insertion, htmx v2 may need `htmx.process(element)` for newly created nodes; alternatively attach a plain click handler that fetches the transcript partial into `#viewer`.

Fallback decision:
- Keep `/jobs` polling fallback for v1.
- Prefer not to run polling and SSE simultaneously during normal operation.
- Suggested fallback triggers:
  - `EventSource` not available: immediately use htmx polling behavior.
  - `error` event fires 3 consecutive times before any successful `open`: switch to polling and close EventSource.
  - If a connection opened successfully, let EventSource auto-reconnect and show a reconnecting indicator rather than falling back immediately.

Connection status UX:
- Optional but recommended: show subtle text such as `live`, `reconnecting...`, or `polling fallback` near the status bar.
- Do not make connection status visually dominant; transcription state remains primary.

### Reconnect Behavior

- Use built-in EventSource reconnect.
- Emit `retry: 2000` to match current user expectation from 2s polling.
- Each event except heartbeat should include a monotonic `id`.
- On reconnect, server emits fresh `snapshot` regardless of `Last-Event-ID`.
- Exact replay is explicitly out of scope for v1 because the state store is in-memory, event history is not durable, and snapshot is enough for dashboard consistency.

### Heartbeat Strategy

- Send heartbeat every 15 seconds when there are no state events.
- Heartbeat may be either:
  - `event: heartbeat` with JSON timestamp, easier to test and debug; or
  - SSE comment `: heartbeat`, lower frontend overhead.
- Recommendation: use `event: heartbeat` in v1 for observability, but the frontend should not update the table on it.

### Testing Plan

Add tests before or alongside implementation. If no test framework exists, add `pytest` only if already acceptable for the project; otherwise document manual checks. Suggested automated tests:

1. `StatusStore` unit tests
   - `register` publishes `job_registered` only when appropriate or publishes idempotent current job if chosen.
   - `update` publishes `job_updated` with serialized job payload.
   - `set_system_message` publishes `system_message`.
   - `snapshot` returns sorted jobs and system message without exposing mutable internal objects.
   - Slow/full subscriber does not block updates.

2. SSE formatting tests
   - `format_sse` includes `event`, `id`, `retry`, and JSON `data` correctly.
   - Multi-line data is valid SSE format.
   - Non-ASCII Japanese text survives JSON encoding.

3. FastAPI endpoint tests
   - `/events` returns `text/event-stream`.
   - First emitted event is `snapshot` after optional `retry`.
   - Published store event appears in stream.
   - Disconnect path unsubscribes the subscriber.

4. Frontend/manual QA
   - New file appears without waiting for 2s polling.
   - Segment count/current text update during processing.
   - Done state displays transcript link and link loads transcript into viewer.
   - Error state displays error text safely.
   - Browser devtools offline/online or server restart shows reconnect/fallback behavior.
   - EventSource-disabled simulation uses `/jobs` fallback.

### Implementation Task List

1. Add serializable snapshot/job helpers to `src/status.py`.
2. Add `StoreEvent`, versioning, subscribe/unsubscribe, and publish behavior to `StatusStore`.
3. Ensure store methods publish after `register`, `update`, and `set_system_message` without blocking the worker thread.
4. Add SSE formatting helper and `/events` endpoint in `src/web/app.py` using `StreamingResponse`.
5. Update `index.html` to remove default 2s polling in the primary path and initialize native `EventSource`.
6. Implement client-side rendering for snapshot, job events, system message, heartbeat/no-op, connection status, and fallback.
7. Keep `/jobs` and transcript partial routes unchanged for compatibility.
8. Add automated tests for store notification and SSE formatting/endpoint where feasible.
9. Run manual QA against live worker processing a sample file.
10. Document any chosen compromises in Implementation Notes during implementation phase.

### Rollout Plan

1. Phase A: backend store notifications and `/events` endpoint behind existing UI, with `/jobs` polling still active for local verification.
2. Phase B: switch frontend primary path to SSE, keeping `/jobs` fallback.
3. Phase C: test with multiple open browser tabs and long idle periods.
4. Phase D: leave `/jobs` endpoint permanently unless there is a strong reason to remove it; it remains useful for fallback and debugging.

### Risks and Mitigations

- **Thread/async boundary complexity**: keep queue interface thread-safe and small; avoid worker awaiting async primitives.
- **Subscriber leak**: unsubscribe in generator `finally`; test disconnect cleanup if possible.
- **Slow clients / queue backpressure**: bounded per-subscriber queues and non-blocking publish; recover via snapshot/reconnect.
- **High-frequency segment events**: current segment updates are likely moderate, but if UI churn occurs, coalesce updates to 250-500ms or publish only latest per job.
- **Proxy buffering/idle timeout**: use `text/event-stream`, `Cache-Control: no-cache`, `X-Accel-Buffering: no`, and 15s heartbeat.
- **XSS from transcript/job text**: render with `textContent`; encode filename in URLs.
- **Lost events on reconnect**: accepted in v1 because snapshot is authoritative and small.
- **Multi-process deployment**: in-memory store and subscriber list only work per process. If uvicorn workers >1 are introduced, SSE consistency requires sticky sessions or a shared store/broker. Current app appears single-process.

### Open Questions for Gate 1

1. Is `EventSource` unsupported-browser fallback required for actual target users, or is fallback mainly a safety net?
2. Should `job_registered` be a separate event, or should the implementation simplify to `job_updated` + `snapshot` only?
3. Is showing a small connection status (`live/reconnecting/polling fallback`) desired in the UI?
4. Should high-frequency segment updates be throttled from day one, or only after observing UI churn?
5. Are multi-process deployments planned? If yes, in-memory SSE pub/sub is insufficient and needs a shared backend.

## Implementation Notes
### 実装サマリー
- `StatusStore` に `StoreEvent`、単調増加 event id、thread-safe subscribe/unsubscribe、bounded queue への non-blocking publish、serializable `snapshot()` を追加。
- review retry で bounded queue overflow 時は古い queued events を破棄したうえで `reset` marker を enqueue し、frontend がリロードして reconnect snapshot で復元する明示的 recovery に変更。
- review retry で `subscribe_with_snapshot()` を追加し、subscriber 登録・snapshot 作成・snapshot version 取得を同一 lock 内で実行。`/events` は snapshot に event id を付与し、`id <= snapshot_version` の queued event を配信しない。
- `GET /events` を `StreamingResponse` で追加し、接続直後の `snapshot`、store mutation event、15秒 heartbeat、`retry: 2000`、SSE 向け headers を実装。
- `index.html` を native `EventSource` primary path に変更し、snapshot/job/system_message を client-side render。SSE 非対応または初回接続が3回連続失敗した場合は `/jobs` fetch polling fallback に切り替える。
- 既存 `/jobs` partial と transcript endpoint は維持。`_jobs.html` は fallback/debug partial として status bar 重複を避ける形に調整し、fallback transcript link の filename path segment を URL encode。

### 変更ファイル
- `src/status.py` — in-memory store の snapshot/job serialization、pub/sub notification、bounded subscriber queue 対応、overflow `reset` recovery、atomic subscribe+snapshot helper を追加。
- `src/web/app.py` — `format_sse()`、async event stream、`/events` endpoint、SSE headers/heartbeat、snapshot event id と stale queued event filtering を追加。
- `src/web/templates/index.html` — 2秒 htmx polling primary path を EventSource client に置換し、connection status、fallback polling、`reset` 受信時の reconnect snapshot recovery、fallback status bar 統合を実装。
- `src/web/templates/_jobs.html` — fallback/debug では status bar を表示し、トップページ include 時は重複しないよう `include_status` で制御。transcript link の filename を URL encode。

### テスト
- `python3 -m py_compile main.py src/status.py src/web/app.py src/worker.py src/watcher.py src/transcriber.py src/config.py src/formatter.py src/models.py src/audio.py src/auth.py src/__init__.py src/web/__init__.py` — pass。
- `StatusStore` の subscribe/register/update/system_message/snapshot/snapshot_with_version smoke check — pass。
- queue overflow recovery smoke（`maxsize=1` で overflow 時に `reset` marker が enqueue されること）— pass。
- `format_sse()` の retry/event/id/non-ASCII formatting smoke check — pass（FastAPI 未導入の active Python でも実行できるよう AST 経由で helper のみ検証）。
- `git diff --check` — pass。
- FastAPI `TestClient` による `/events` endpoint smoke check は active Python environment に `fastapi` が無く `ModuleNotFoundError` で未実行。

### 残課題・注意点
- 実行環境に `requirements.txt` の依存を入れた状態で `/events` の streaming endpoint とブラウザ上の reconnect/fallback を手動確認してください。
- v1 は計画どおり event replay なしで reconnect 時 snapshot 復元です。multi-process deployment では shared store/broker または sticky session が必要です。
- overflow recovery は最小実装として `reset` 受信後に page reload で EventSource reconnect snapshot を得る方式です。UX を滑らかにする場合は将来タスクで reset 時に snapshot fetch/stream-level reconnect へ改善してください。

## Review
### 判定: PASS

### コードレビュー統合結果

#### Quality Reviewer
- [minor] `src/status.py:138-165` queue overflow recovery は `_enqueue_reset_for_overflow()` に分離され、silent drop ではなく `reset` marker を送る形になった。挙動は読みやすくなったが、今後 unit test 化して regression を防ぐのが望ましい。
- [minor] `src/web/templates/index.html:205-223` fallback polling は `/jobs` partial から `.status-bar` を取り出して上部 `#system-message` へ反映し、二重表示を避けるよう修正済み。DOM parser 代わりに `innerHTML` を使う範囲は same-origin endpoint の HTML に限定されており許容。

#### Logic Reviewer
- [resolved] Prior major: `src/status.py:138-165` は queue full 時に queued events を明示的に破棄し、同じ event id の `reset` marker を enqueue する。frontend は `reset` 受信時に `window.location.reload()` して reconnect snapshot を取得する（`src/web/templates/index.html:264-270`）ため、silent event loss による永続 stale は解消されている。
- [resolved] Prior major: `src/status.py:111-121` で subscriber 登録・snapshot 作成・snapshot version 取得を同一 lock 内で行い、`src/web/app.py:40-59` が snapshot event id を付けたうえで `event.id > snapshot_version` のみ配信する。snapshot 後に古い queued event が流れて初期 snapshot を巻き戻す race は解消されている。
- [minor] `src/web/app.py:40-61` の streaming endpoint と disconnect unsubscribe は FastAPI 依存がない active environment では endpoint-level smoke が未実行。依存導入環境で `TestClient` または実ブラウザで確認する必要がある。

#### Security Reviewer
- [minor] `.claude/rules/security.md` はリポジトリ内に存在しなかったため、一般的な security 観点で確認。`src/web/templates/index.html:120-183` は `textContent` と `encodeURIComponent()` を使っており、SSE payload 由来の `filename/current_text/error` の XSS リスクは抑制されている。
- [minor] Prior minor: fallback template の transcript URL は `src/web/templates/_jobs.html:24` で `urlencode|replace('/', '%2F')` に修正済み。filename path segment の routing mismatch リスクは低減された。

#### Simplify Reviewer
- [minor] `src/web/templates/index.html:96-272` に rendering、SSE lifecycle、fallback polling、reset recovery がまとまっている。今回の規模では許容だが、今後 retry/fallback 条件を増やす場合は static JS へ分離し、pure function（render job row、apply event、start fallback）単位でテスト可能にするのが望ましい。

#### 統合サマリー
- Prior major 2件はいずれも解消。queue overflow は `reset` recovery により reconnect snapshot へ進むため silent stale にならず、snapshot ordering は atomic subscribe/snapshot + version filter により古い queued event を配信しない。
- 残る指摘は endpoint/browser verification と frontend JS 分離・regression test 化の minor のみ。critical / major はゼロのため PASS。

### 動作検証結果

#### ブラウザ表示確認（該当時）
- 未実施。active Python environment に `fastapi` がなくアプリ起動/`/events` endpoint smoke ができなかったため、ブラウザでの EventSource reconnect/fallback 確認も未実施。

#### テスト実行結果（該当時）
- `python3 -m py_compile main.py src/status.py src/web/app.py src/worker.py src/watcher.py src/transcriber.py src/config.py src/formatter.py src/models.py src/audio.py src/auth.py src/__init__.py src/web/__init__.py` — pass。
- `git diff --check` — pass。
- `StatusStore` pub/sub smoke（subscribe_with_snapshot/register/update/set_system_message/snapshot_with_version/unsubscribe）— pass。
- queue overflow recovery smoke — pass（`maxsize=1` で overflow 時に `reset` marker が enqueue され、snapshot で最新 state を復元できることを確認）。
- snapshot ordering smoke — pass（atomic snapshot version 取得後の queued events が `id > snapshot_version` になり、stream 側 stale filter の前提を満たすことを確認）。
- `opencode run -m github-copilot/gpt-5.5 ...` による Logic reviewer 別セッション — timeout で完走せず。ただし途中出力では「主対象の race/overflow 修正は、単一 producer 前提では意図どおり stale UI を snapshot/reload で回復する方向」と確認。
- FastAPI dependency check — `ModuleNotFoundError: No module named 'fastapi'`。そのため `/events` StreamingResponse と `TestClient` 検証は未実行。

### 申し送り事項（minor）
- 依存を入れた環境で `/events` の初回 snapshot、mutation event、heartbeat、disconnect unsubscribe、ブラウザ reconnect/fallback、`reset` 受信時の reload recovery を確認する。
- queue overflow recovery と snapshot ordering は regression test 化する。
- multi-process deployment では今回の in-memory store/subscriber 前提が破綻するため、単一 process 前提を運用文書に明記するか shared backend を検討する。

## Deploy
### デプロイ結果: SUCCESS（local finalization）

### 実行内容
- デプロイ日時: 2026-05-26T14:10:29+09:00
- feature ブランチ: `feature/web-ui`
- PR: 未作成（`origin` remote が未設定で、push/PR workflow を安全に実行できないため）
- remote 確認: `git remote -v` / `git config --get remote.origin.url` ともに remote URL なし

### デプロイ後検証結果

#### ブラウザ確認（該当時）
- 未実施。
- 理由: active Python environment に `fastapi` がなく、アプリ起動・`/events` endpoint smoke・ブラウザ EventSource/reconnect/fallback 確認を安全に実行できなかった。
- 問題点: 依存導入済み環境での実ブラウザ確認が次ステップとして必要。

#### スモークテスト（該当時）
- `python3 -m py_compile main.py src/status.py src/web/app.py src/worker.py src/watcher.py src/transcriber.py src/config.py src/formatter.py src/models.py src/audio.py src/auth.py src/__init__.py src/web/__init__.py && git diff --check` — pass。
- `StatusStore` pub/sub smoke（`subscribe_with_snapshot` / `register` / `update` / `set_system_message` / `snapshot_with_version` / `unsubscribe`）— pass。
- queue overflow recovery smoke（`maxsize=1` で `reset` marker と最新 snapshot 復元を確認）— pass。
- `format_sse()` smoke（AST 経由で FastAPI import を回避し、retry/event/id/non-ASCII formatting を確認）— pass。
- FastAPI dependency check — `ModuleNotFoundError: No module named 'fastapi'`。そのため `/events` `StreamingResponse` endpoint と browser QA は未実行。

### 申し送り事項
- push/PR は未実施。remote を設定後、`feature/web-ui` を push して PR を作成する。
- 依存導入済み環境で `/events` の初回 snapshot、mutation event、heartbeat、disconnect unsubscribe、ブラウザ reconnect/fallback、`reset` 受信時の reload recovery を確認する。
- queue overflow recovery と snapshot ordering は regression test 化する。
- multi-process deployment では in-memory store/subscriber 前提が破綻するため、単一 process 前提の運用明記または shared backend を検討する。
