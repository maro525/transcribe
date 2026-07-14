# Task: NSKETCH-885 — ASR-first batch transcription (whisperX-style word→speaker assignment)

## Meta
- linear_id: NSKETCH-885
- tier: M
- created: 2026-07-15
- status: completed

## Brief
- **Current State**: Batch pipeline is diarization-first: pyannote turns → per-turn audio crop → per-crop whisper decode (`src/transcriber.py`). Turn-boundary crops truncate words, lose cross-turn context, and decode N times. NSKETCH-883 already made the model tier GPU-aware (`BATCH_WHISPER_MODE`, strong=large-v3-turbo).
- **Goal**: ASR-first (whisperX-style): one whole-file openai-whisper pass with `word_timestamps=True, language=ja`, plus the existing whole-file pyannote diarization; assign each word to the max-overlap turn (nearest if none), group consecutive same-speaker words into `TranscriptSegment` joined without spaces.
- **Scope**: In — new pure `src/align.py`, rewrite of `diarize_and_transcribe` internals, `BATCH_CONDITION_ON_PREVIOUS_TEXT` env, unit tests, README update. Out — engine swap (openai-whisper stays), faster-whisper, live mode, worker/artifacts/formatter/discourse changes, accuracy benchmarking.
- **Constraints**: Public contract frozen (`list[TranscriptSegment]` + `on_segment` in time order; same function signature; `TranscriptSegment` importable from `src.transcriber`). Alignment must be one pure deterministic function testable without torch/GPU/audio/models. Respect NSKETCH-883 resolution (model chosen in worker, passed in).
- **Success Criteria**: (1) `worker.py`/`artifacts.py`/`formatter.py`/discourse untouched by diff; (2) new `tests/test_align.py` passes standalone via `python3`; (3) `py_compile` clean on touched files; (4) full suite no regression (145 existing + new); (5) all 6 design questions resolved and logged.

## Decision Log
- `[startproject] PRE` — 2026-07-15 startproject Phase 1–3 executed (tier=M; OpenCode gpt-5.5 consulted — first run hung >17min and was killed (exit 143), direct-answer retry succeeded ~1min). DONT-ASK MODE: Gate 1 auto-approved.
- `[startproject] DECISION` — Word-level assignment (whisperX-style), not whisper-segment-level; grouping absorbs Japanese word-timestamp jitter. (OpenCode concurs)
- `[startproject] DECISION` — Tie-breaking: max overlap → min gap distance → earliest turn; zero-duration words are point intervals under the same rule. Fully deterministic.
- `[startproject] DECISION` — `condition_on_previous_text=False` default (repetition-loop risk on long ja meetings); new env knob `BATCH_CONDITION_ON_PREVIOUS_TEXT` (default off), parsed like `DISCOURSE_ENABLED`.
- `[startproject] DECISION` — `min_segment_seconds` becomes `min_turn_seconds` inside the pure function: short diarization turns are dropped before assignment so their words re-attach to neighboring turns (no text loss); empty-text output groups are still dropped.
- `[startproject] DECISION` — `on_segment` fires after decode+diarization+assignment, once per segment, strictly in time order. No incremental emission (speaker only final post-alignment); worker.py unchanged, contract preserved.
- `[startproject] DECISION` — Pure function lives in new stdlib-only `src/align.py`; `TranscriptSegment` moves there and is re-exported from `src/transcriber.py` so `formatter.py` and all consumers are unchanged.
- `[startproject] DECISION` — `diarize_and_transcribe` keeps its exact signature; `audio_cropper` accepted-but-unused (documented) to keep worker.py literally unchanged.
- `[startproject] DECISION` — Deviation from OpenCode: NaN/inf/reversed word timestamps are deterministically sanitized (drop non-finite, clamp reversed), NOT `ValueError` — a hallucinated whisper word must not fail an entire meeting job (worker marks the whole job ERROR on exceptions). Sanitization rules are themselves unit-tested.
- `[startproject] DECISION` — Empty diarization (no turns) → single-speaker fallback label `SPEAKER_00` instead of today's empty transcript.
- `[startproject] DECISION` — Verification without GPU/audio/models: `py_compile` on all touched files + new pure-logic test module + full suite no-regression (currently 145 passing). Accuracy validation explicitly out of scope (no GPU/audio in dev).
- `[startproject] POST` — Plan complete; Linear comment posted to NSKETCH-885.
- `[team-implement] POST` — 2026-07-15 実装完了。align.py / transcriber.py / config.py / tests/test_align.py / README を変更。worker・artifacts・formatter・discourse 不変。py_compile OK、test_align 29/29、全体スイート 174/174（既存 145 回帰なし）。ブランチ feature/nsketch-885-asr-first-batch。Linear コメント投稿済み。
- `[team-review] POST` — 2026-07-15 判定 PASS。4観点レビュー（Claude / Security手動 / Simplify手動；OpenCodeは過去ハング実績によりスキップ）。align.py 純粋アルゴリズムを Design ケースマトリクスに照合し正当性確認。契約保全確認。test_align 29/29、全体 174/174 回帰なし、py_compile OK。critical/major ゼロ。minor 申し送りは Review 節参照。精度検証はユーザーフォローアップ。
- `[deploy] POST` — 2026-07-15 デプロイ完了。コミット 7cb3d86 + a672a62 (Merge PR #13)。Linear NSKETCH-885 を In Review に更新、デプロイコメント投稿。タスクファイル Deploy 節記入・status=completed。

## Design

### Module layout
1. **New `src/align.py`** — stdlib-only (dataclasses/math/typing; no torch/whisper/pyannote):
   - `@dataclass(frozen=True) Word(text: str, start: float, end: float)`
   - `@dataclass(frozen=True) Turn(speaker: str, start: float, end: float)`
   - `@dataclass TranscriptSegment(speaker, start, end, text)` — moved here from `transcriber.py`.
   - `words_from_whisper_result(result: dict) -> list[Word]` — pure normalizer over the raw whisper transcribe dict: iterates `result["segments"]`, takes `seg["words"]` (`{"word","start","end"}`); if a segment lacks `words` (defensive), falls back to one pseudo-word from the segment's own `text/start/end`.
   - `assign_words_to_turns(words, turns, *, min_turn_seconds: float = 0.0, fallback_speaker: str = "SPEAKER_00") -> list[TranscriptSegment]` — the single pure deterministic function.
2. **`src/transcriber.py`** — rewritten body, identical public surface. Re-export `TranscriptSegment`. Flow: `ensure_wav` → whole-file `whisper_model.transcribe(str(wav_path), language="ja", word_timestamps=True, fp16=torch.cuda.is_available(), condition_on_previous_text=config.BATCH_CONDITION_ON_PREVIOUS_TEXT)` → `words_from_whisper_result` → `diarization_pipeline(str(wav_path), num_speakers=...)` → turns → `assign_words_to_turns(words, turns, min_turn_seconds=min_segment_seconds)` → per-segment print + `on_segment` in time order → return. Temp-wav `finally` cleanup unchanged. `audio_cropper` accepted-but-unused.
3. **`src/config.py`** — add `BATCH_CONDITION_ON_PREVIOUS_TEXT` bool env (default False, parsed like `DISCOURSE_ENABLED`).
4. **`src/worker.py`, `src/artifacts.py`, `src/formatter.py`, discourse modules — untouched.**

### Pure-function algorithm (`assign_words_to_turns`)
1. Sanitize words: drop empty-after-strip text; drop non-finite timestamps; clamp `end = max(start, end)`; stable sort by `(start, end)`.
2. Filter turns: keep `end - start >= min_turn_seconds` and finite; sort by `(start, end, speaker)`.
3. Empty inputs: no words → `[]`; no surviving turns → one segment per consecutive run with `fallback_speaker`.
4. Assign each word: `overlap = max(0.0, min(w.end, t.end) - max(w.start, t.start))`; max overlap wins; if all 0, min gap `distance = max(0.0, t.start - w.end, w.start - t.end)`; ties → earliest turn in sort order. Selection key: `max over turns of (overlap, -distance, -index)`.
5. Group consecutive same-speaker words: `start` = first word start, `end` = last word end, `text = "".join(w.text.strip() ...)` (no-space join for Japanese). Drop empty-text groups.
6. Output naturally in time order. Complexity O(W×T) accepted (W≈10⁴, T≈10²).

### Risks / notes
- Dashboard progress granularity degrades: `segments_completed` jumps 0→N at end of decode. Accepted trade-off (progress channel would need worker.py changes — out of scope).
- `whisper_model.transcribe(str(wav_path), ...)` takes the path (ffmpeg internally); torch stays imported in transcriber only for `fp16`.
- `models.get_audio_cropper` remains loaded by worker; do not remove.
- Keep `align.py` import-free of `src.config`; thresholds are parameters.

### Implementation plan
1. Create `src/align.py` per algorithm above.
2. Rewrite `src/transcriber.py` (signature unchanged; re-export `TranscriptSegment`).
3. Add `BATCH_CONDITION_ON_PREVIOUS_TEXT` to `src/config.py`; wire into transcribe call.
4. Write `tests/test_align.py` (self-running, imports `src.align` only): grouping/no-space join; speaker-change split; overlap boundary cases; equal-overlap tie → earlier turn; zero-overlap → nearest (before/after/between); zero-duration words; empty words; empty turns → SPEAKER_00; min_turn_seconds re-attach; non-finite dropped / reversed clamped; empty-text filtering; time-order invariant; segment bounds; `words_from_whisper_result` normal/fallback/empty.
5. Update README: pipeline description + env table row.
6. Verify: py_compile + `python3 tests/test_align.py` + full suite loop — no regression vs 145.

## Implementation Notes

### 実装サマリー
- src/align.py 新規（stdlib のみ / torch 非依存）: Word・Turn・TranscriptSegment、
  words_from_whisper_result 正規化、assign_words_to_turns 純粋決定的関数
  （max-overlap → min-gap → earliest-turn、無スペース連結、サニタイズ）。
- src/transcriber.py を ASR-first に書き換え。公開シグネチャは不変、
  TranscriptSegment は align から再エクスポート。audio_cropper は受理のみ・未使用。
- src/config.py に BATCH_CONDITION_ON_PREVIOUS_TEXT（既定 False、DISCOURSE_ENABLED 方式）。
- tests/test_align.py 新規（自走・29 テスト）。README にパイプライン記述・ディレクトリ構成・env 行を追加。
- 主要判断: 幻覚ワードの非有限/逆転タイムスタンプは ValueError ではなく決定的サニタイズ
  （ジョブ全体を落とさない）。話者ターン 0 件は SPEAKER_00 フォールバック。

### 変更ファイル
- src/align.py — 新規: 純粋な単語→話者割り当てロジックとデータクラス
- src/transcriber.py — ASR-first 全体デコード＋全体話者分離に書き換え、TranscriptSegment 再エクスポート
- src/config.py — BATCH_CONDITION_ON_PREVIOUS_TEXT 追加
- tests/test_align.py — 新規: Design のケースマトリクス網羅（29 テスト）
- README.md — 処理フロー・ディレクトリ構成に align.py、env 表に新変数

### テスト
- tests/test_align.py（自走: python3 tests/test_align.py）29/29 PASS
- 全体スイート: 174/174（既存 145 + 新規 29、回帰なし）。torch/pytest/GPU 不要。
- py_compile: src/align.py src/transcriber.py src/config.py tests/test_align.py すべて OK。
- 契約確認: diarize_and_transcribe のシグネチャ不変（AST 検証）、formatter.py の
  `from .transcriber import TranscriptSegment` は再エクスポートで維持、
  git diff は worker/artifacts/formatter/discourse に触れていない。

### 残課題・注意点
- ダッシュボード進捗の粒度が低下（segments_completed が復号完了時に 0→N）。worker.py 不変維持のため許容。
- 精度検証は開発環境に GPU/音声が無いためスコープ外（PR に明記）。

## Review

### 判定: PASS（2026-07-15, tier=M）

4観点レビュー（Claude / Security 手動 / Simplify 手動；OpenCode は過去ハング実績によりスキップ）。

- **アルゴリズム正当性**: `_best_turn` の選択キー `(overlap, -gap, -index)` が max-overlap → min-gap → earliest-turn を正しく符号化（オーバーラップ存在時は gap=0 に収束しオーバーラップが厳密に優越）。サニタイズ・グルーピング・時系列順も Design ケースマトリクスに照合し確認。
- **契約保全**: `diarize_and_transcribe` シグネチャ不変、`TranscriptSegment` 再エクスポートで `formatter.py` 維持、worker/artifacts/formatter/discourse 不変（git status 検証）。
- **テスト**: test_align 29/29、全体スイート 174/174（既存 145 回帰なし）、py_compile クリーン。
- **セキュリティ**: 手動確認（rules/security.md 不在）。認証・ネットワーク・SQL・HTML 面の追加なし、bool env パースと in-process dict 正規化のみ。新規攻撃面なし。PASS。

### Minor 申し送り（follow-up、FAIL 条件ではない）
- `align.py` `flush()` 内の `if text:` ガードは実質デッドコード（サニタイズ済みのため常に真）。無害な防御的冗長。
- 逆転ターン（end<start）は暗黙にドロップ、逆転ワードはクランプ — 非対称だが pyannote は逆転ターンを出さないため実害なし。
- 0.3 秒未満ターンの単語再付着は誤帰属の可能性（旧実装はテキスト消失）— 設計上の明示的トレードオフ。PR に明記。
- ダッシュボード進捗粒度低下（0→N ジャンプ）— 既知・許容。
- 精度・タイムスタンプ品質の実音声検証はユーザーフォローアップ（GPU/音声なし環境）。

## Deploy

### デプロイ結果: SUCCESS

### 実行内容
- デプロイ日時: 2026-07-15 16:00 JST
- コミット: a672a62 (Merge pull request #13 from hidemaro-nsketch/feature/nsketch-885-asr-first-batch)
- 実装コミット: 7cb3d86 feat(transcriber): ASR-first batch transcription with word-to-speaker alignment
- PR: https://github.com/hidemaro-nsketch/transcribe/pull/13 (マージ済み)

### 変更ファイル確認
- src/align.py — 新規: Word, Turn, TranscriptSegment, words_from_whisper_result, assign_words_to_turns
- src/transcriber.py — ASR-first に書き換え、TranscriptSegment 再エクスポート
- src/config.py — BATCH_CONDITION_ON_PREVIOUS_TEXT 追加
- tests/test_align.py — 新規: 29 ユニットテスト
- README.md — パイプライン説明・ディレクトリ構成・env 表を更新
- 不変確認: worker.py, artifacts.py, formatter.py, discourse (git diff で確認)

### デプロイ後検証結果

#### テスト
- 実行: 全体スイート 174/174 PASS
  - 既存 145 テスト: 回帰なし
  - 新規 29 テスト (test_align.py): すべてクリーン
- py_compile: src/align.py, src/transcriber.py, src/config.py, tests/test_align.py すべて OK

#### 契約保全確認
- diarize_and_transcribe シグネチャ不変 ✅
- TranscriptSegment 再エクスポート で formatter.py 無変更 ✅
- worker.py, artifacts.py, formatter.py, discourse に触れていない ✅

### Linear 更新
- NSKETCH-885 ステータス: In Progress → **In Review** ✅
- デプロイコメント投稿 ✅

### 申し送り事項

#### 重要な注記
**実音声 / GPU での精度・タイムスタンプ品質検証はユーザーの必須フォローアップ**

開発環境に GPU/音声/モデルなし。純粋ロジック + ユニットテスト + 全体スイート無退行で検証済み。

#### Minor follow-up items
1. **0.3 秒未満ターンの単語再付着による話者誤帰属の可能性**
   - 旧実装（短ターン削除）はテキスト消失
   - 新実装（隣接ターンに再付着）はテキスト保全だが誤帰属リスク
   - 設計上の明示的トレードオフ (PR に記載)

2. **ダッシュボード進捗の粒度低下**
   - segments_completed が復号完了時に 0→N にジャンプ
   - worker.py 不変維持のため許容 (PR に記載)
