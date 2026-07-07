# Task: LOCAL-batch-detail-20260707 — Batch job detail page + word network + artifact files

## Meta
- linear_id: LOCAL-batch-detail-20260707 (local task — Linear not used)
- tier: M
- created: 2026-07-07T13:59:55+09:00
- status: planning (Gate 1 awaiting approval)
- base_branch: feature/live-word-network

## Brief

### Current State
- バッチモード: `src/worker.py` が `input/` を監視 → whisper+pyannote で `diarize_and_transcribe()` → `src/formatter.py::save_transcript()` が `output/{stem}.txt`（`[MM:SS.mmm - MM:SS.mmm] SPEAKER_00: text` 形式、1行=1話者セグメント）を保存 → 音声は `done/` へ移動。`bootstrap_history()`（worker.py:18）が起動時に `output/*.txt` + `done/` の対から DONE ジョブを復元。
- ダッシュボード: `src/web/app.py` の `create_app()`。`index.html` は SSE (`/events`) でジョブ表を再描画し、「表示」リンクは htmx で `GET /jobs/{filename}/transcript`（`_transcript.html` partial）をインライン `#viewer` に差し込む。リンクは `_jobs.html`（サーバ側、行24）と `index.html` の JS（行171-177、SSE 再描画時に動的生成）の2箇所で生成される。
- ライブモード資産（再利用対象、純Python・torch非依存）: `src/live/terms.py::extract_terms(text, limit) -> list[Term(word, score)]`（janome、無ければ keywords.py fallback）、`src/live/graph.py::CooccurrenceGraph`（`add_utterance(terms)` / `snapshot()` → `{type:"graph", seq, nodes:[{id,weight,last_seen}], edges:[{a,b,weight}]}`）。`live.html` 行406-594 に依存ゼロの力学 Canvas レンダラ（この snapshot 形式をそのまま消費、エネルギー閾値で静止する rAF ループ）。
- 開発環境制約: venv 無し・依存未インストール（fastapi/janome/torch すべて import 不可）。テストは `tests/_runner.py` によるスタンドアロン実行（pytest 互換）。janome 非依存のテストは injected tokenizer で書かれている（`tests/test_terms.py` 方式）。

### Goal
1. ジョブ一覧の「表示」をフルページ詳細 `GET /jobs/{filename}` へ変更（インライン `#viewer` 廃止）。
2. 詳細ページに文字起こし・キーワード・共起グラフ（静的 Canvas 描画、WS 不要）を表示。
3. worker がバッチ完了時に `output/{stem}.keywords.json` / `output/{stem}.graph.json` を保存し、詳細ページはファイルを読むだけ。

### Scope
- In: 新モジュール `src/artifacts.py`、worker への保存フック、`GET /jobs/{filename}` ルート＋`detail.html` テンプレート、`_jobs.html`/`index.html` のリンク変更、単体テスト。任意（P2）: `scripts/backfill_artifacts.py`。
- Out: live モードの挙動変更、`CooccurrenceGraph`/`terms.py` 本体の変更、Linear 連携、実音声での E2E 検証。

### Constraints
- GPU/依存/モデル重み無し環境 → 検証は「ロジック単体テスト（依存注入）・既存テスト無回帰・py_compile」のみ。FastAPI ルートの実行テスト・実 Canvas 描画・実音声処理は本環境では未実施（PR 本文にも明記）。
- `src/artifacts.py` は stdlib + `src/live/terms.py` + `src/live/graph.py` のみに依存させる（torch/fastapi を import しない）。

### Success Criteria
- 新規バッチ完了時に 2 つの JSON 成果物が保存される（単体テストで検証）。
- 詳細ページが transcript/keywords/graph を表示し、成果物が無い旧ジョブでは「なし」を表示（テンプレート・ローダの単体レベルで検証）。
- 既存テスト全 PASS、全変更ファイル py_compile PASS。live.html は無変更（回帰ゼロ保証）。

## Decision Log
- 2026-07-07: Linear integration explicitly skipped per user instruction (local TASK_FILE only).
- 2026-07-07: Base branch = feature/live-word-network (batch pipeline + terms.py/graph.py available).
- `[startproject] PRE` 2026-07-07: Phase1-2 完了。コードベース調査 + OpenCode 設計相談実施。
- `[startproject] DECISION`: 「表示」リンクをフルページ `GET /jobs/{filename}` に変更、インライン `#viewer` は削除。
- `[startproject] DECISION`: worker が完了時に `output/{stem}.keywords.json` / `{stem}.graph.json` を保存。詳細ページは読むだけ。生成失敗はジョブを ERROR にしない（ログのみ）。
- `[startproject] DECISION`: 後方互換は「なし」表示。遅延生成・write-back は不採用（読み取り専用ページ制約・janome 品質差・worker との書き込み競合回避）。任意 P2 で backfill スクリプト。
- `[startproject] DECISION`: `/jobs/{filename}/transcript` ルートは deprecated として残置、UI リンクのみ全廃。
- `[startproject] DECISION`: Canvas は live.html から detail.html へ縮約コピー（live.html 無変更＝回帰ゼロ）。static .js 共有化は後続課題。
- `[startproject] DECISION`: 発話単位 = 話者ラベル付き `TranscriptSegment` 1件。抽出パラメータは既存 `LIVE_KEYWORD_LIMIT` / `LIVE_GRAPH_MAX_NODES` / `LIVE_GRAPH_CANDIDATES_PER_FINAL` を再利用（新 env 変数を増やさない）。
- `[startproject] DECISION`: スキーマは version:1 envelope、graph は `snapshot()` 無変換格納。
- `[startproject] POST` 2026-07-07: 計画完了、Gate 1 承認待ち。

## Design

### D1. 後方互換 — 【決定: 「なし」表示。遅延生成しない】
- 理由: (a) 「詳細ページはファイルを読むだけ」という要件と整合、(b) janome 有無で結果品質が変わり遅延生成は不安定、(c) 長い transcript の request 時解析はレイテンシ不定、(d) write-back は worker と `output/` の書き込み競合になる。
- キーワード欄・グラフ欄に「なし（このジョブは成果物未生成です）」を表示。
- 任意の P2 として `scripts/backfill_artifacts.py`（`output/*.txt` を行 regex `^\[.+? - .+?\] (\S+): (.*)$` でパースし 1行=1発話として成果物生成）。Web 層からは生成しない。

### D2. 既存 `/jobs/{filename}/transcript` partial — 【決定: ルートは残置（deprecated）、UI からのリンクは全廃】
- `_jobs.html` の htmx リンクと `index.html` JS の動的リンクを通常の `<a href="/jobs/{encoded}">詳細</a>` に置換。`#viewer` div と `htmx.process()` 依存箇所は削除。
- ルート本体と `_transcript.html` は互換のため 1 リリース残す（削除は後続タスク）。

### D3. 成果物スキーマ — バージョン付き envelope
- `output/{stem}.keywords.json`:
  `{"version": 1, "source": "<audio filename>", "generated_at": "<iso8601>", "keywords": [{"word": str, "score": float}]}`
- `output/{stem}.graph.json`:
  `{"version": 1, "source": "<audio filename>", "generated_at": "<iso8601>", "graph": <CooccurrenceGraph.snapshot() そのまま>}`
- snapshot をそのまま入れることで live.html 由来レンダラが無変換で消費できる。読み込み時は version 不一致・JSON 破損・欠落をすべて `None`（=「なし」表示）に落とす。

### D4. 生成ロジックの置き場 — 新モジュール `src/artifacts.py`
- 主要関数:
  - `build_keywords(utterance_texts: list[str], *, limit) -> dict`（`extract_terms("\n".join(...))` — live の `_keywords_message_locked` と同方式）
  - `build_graph(utterance_texts: list[str], *, max_nodes, candidates_per_utterance) -> dict`（発話ごとに `extract_terms` → `add_utterance`、decay=1.0）
  - `save_artifacts(output_dir: Path, audio_filename: str, utterance_texts: list[str]) -> None`
  - `load_keywords(output_dir, stem) -> dict | None` / `load_graph(output_dir, stem) -> dict | None`
- worker 統合: `process_file()` の `save_transcript` 直後、`store.update(DONE)` の前に try/except で呼ぶ。成果物生成の失敗はジョブを ERROR にしない。

### D5. Canvas 再利用 — 【決定: detail.html に live.html から縮約コピー（live.html は無変更）】
- WS ハンドラ・reheat 分岐・collapse を除いた約 100 行（`applyGraph`/`simulateGraph`/`drawGraph`/`resizeGraphCanvas` 相当）を移植。出典コメントで live.html 行番号を参照。
- データ受け渡しは `<script type="application/json">{{ graph|tojson }}</script>`（`tojson` は `<` をエスケープするため script 注入安全）。初期ランダム配置 → エネルギー閾値で自然静止。resize 時のみ再加熱。

### D6. ファイル名エンコーディング / 安全性
- サーバ側 `{{ job.filename|urlencode|replace('/', '%2F') }}`、JS 側 `encodeURIComponent(job.filename)`。
- `GET /jobs/{filename}` は `store.get(filename)` 存在確認 + `Path(filename).name != filename` なら 404（トラバーサル防御、transcript ルートにも同ガード追加）。
- ルート宣言順: `/jobs` → `/jobs/{filename}/transcript` → `/jobs/{filename}`。

### D7. 検証計画
**実施可能（本環境）:**
- 新規 `tests/test_artifacts.py`: injected tokenizer + `reset_extractor_cache()` で build/save/load 往復、スキーマ version、欠落/破損 JSON → None、発話単位のグラフ形成（エッジ = 同一発話内共起）を検証。`_runner.py` 対応。
- 既存テスト無回帰: `tests/test_terms.py` `tests/test_live_graph.py` `tests/test_keywords.py` 等をスタンドアロン実行。
- `python3 -m py_compile` を全変更 .py に実施。テンプレートは目視レビューのみ（jinja2 未導入）。
**未実施（環境制約により不可、実機で要確認 — PR 本文に明記）:**
- FastAPI ルートの実行テスト（fastapi/TestClient 未インストール）。
- ブラウザでの Canvas 実描画・レイアウト確認。
- 実音声によるバッチ完了 → 成果物保存 → 詳細ページ表示の E2E。
- janome 実体での抽出品質確認。

### 実装タスクリスト
1. `src/artifacts.py` 新規作成 — build_keywords / build_graph / save_artifacts / load_keywords / load_graph。
2. `src/worker.py` — `process_file()` の `save_transcript` 後に `save_artifacts()` を try/except で呼ぶ。
3. `src/web/app.py` — `GET /jobs/{filename}` ルート追加＋filename サニタイズガード（transcript ルートにも）。
4. `src/web/templates/detail.html` 新規作成 — transcript / キーワード / Canvas グラフ / 戻るリンク。
5. `_jobs.html` / `index.html` — フルページリンク化、`#viewer` 削除。
6. `tests/test_artifacts.py` 新規作成。
7. （P2・任意）`scripts/backfill_artifacts.py`。
8. 検証: テスト実行 + py_compile。未実施項目を Review 欄に転記。

## Implementation Notes
<!-- team-implement が記入 -->

## Review
<!-- team-review が記入 -->

## Deploy
<!-- deploy が記入 -->
