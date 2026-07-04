# Task: NSKETCH-820 — Moonshine による CPU 向けライブ文字起こしエンジンの追加

## Meta
- linear_id: NSKETCH-820
- linear_url: https://linear.app/nsketch/issue/NSKETCH-820/moonshine-による-cpu-向けライブ文字起こしエンジンの追加
- related: NSKETCH-819 (feature/live-mode, PR #1)
- tier: L
- created: 2026-07-05T00:17:37+09:00
- status: implemented (team-review 待ち)
- branch: feature/moonshine-live-engine (from feature/live-mode)
- base_branch: feature/live-mode

## Tier Rationale
- Hard Trigger: 新規コア依存の追加（transformers）→ 自動 L
- Files: 10+ 見込み(engine アダプタ、選択ロジック、scripts、依存定義、docs、テスト)
- Complexity: エンジンフォールバック鎖への新バックエンド追加 + ランタイム選定
- Risk: ライブモード中核経路に影響（ただし既存バッチ/GPU 経路は不変更）

## Brief

### Current State
- feature/live-mode（PR #1 未マージ）上に NSKETCH-819 のライブモードが実装済み。
- 転写エンジンは pywhispercpp（whisper.cpp large-v3-turbo）単一実装。`src/live/engine.py` の
  `get_engine()` シングルトンを `session.py` の `_default_engine_provider` が参照し、
  `TranscriptionWorker` は `Engine` Protocol（`transcribe_partial`/`transcribe_final`）にのみ依存。
- フォールバック（CUDA whisper.cpp → whisper-server → CPU q5_0）はコード上の自動切替ではなく
  運用手順（ビルド方法・量子化選択）として README/819 計画に記載されている。
- CPU 環境の現実装は q5_0 でも 10 秒発話の final に約 5〜10 秒かかり、partial 間隔 2.5 秒への
  緩和が必要（ライブ用途として体験が悪い）。

### Goal
- GPU 無し環境向けに UsefulSensors/moonshine-tiny-ja（27M、日本語特化、CER Fleurs 17.87）を
  CPU 用代替ライブエンジンとして追加し、CPU でも partial 間隔 1.0 秒を維持できるようにする。
- `LIVE_ENGINE` 環境変数（auto|whispercpp|moonshine）で選択、auto は CUDA 非検出かつ
  moonshine 重み配置済みのとき moonshine を既定とする。

### Scope
- In: `src/live/engine_moonshine.py`（新規アダプタ）、`src/live/engine.py` の get_engine() 選択化、
  `src/config.py` の LIVE_ENGINE 系設定、`scripts/fetch_moonshine_model.py`、requirements.txt への
  transformers 追加、README、選択ロジック/チャンク分割のユニットテスト。
- Out: 既存バッチモード（openai-whisper + pyannote）、GPU 用 whisper.cpp 経路（pywhispercpp
  実装・ggml 取得スクリプト）、Silero VAD・partial/final スケジューリング、WS プロトコル、UI。
- ストレッチ（言及のみ・実装しない）: バッチモードの CPU 軽量オプションとして MoonshineEngine を
  流用する将来案。

### Constraints
- `session.py` / `streaming.py` / `vad.py` は無変更（エンジン供給の差し替えのみで実現する）。
- 重みは git 管理外（models/ は .gitignore 済み）。
- ライセンス: Moonshine AI Community License（MIT ではない）。商用利用は年商 <$1M で無償だが
  登録必須。Gate 1 でユーザー承認を得ること。
- 検証環境に GPU / 依存パッケージ / モデル重みが無いため、numpy のみで動く検証と
  依存導入環境での未実施検証を分離して明記する。

### Success Criteria
- LIVE_ENGINE=moonshine で /live が動作し、CPU で partial 間隔 1.0 秒・final（〜30 秒発話）が
  数秒以内（依存環境での実測により確定）。
- auto 選択: GPU ホストでは従来どおり whispercpp、非 GPU + moonshine 重みありで moonshine、
  moonshine 重み無しなら従来の whispercpp CPU に自動フォールバック。GPU ホストの挙動は不変。
- 既存テスト 29 件 + 新規テスト（選択ロジック・チャンク分割）が numpy のみの環境で PASS。
- 30 秒発話の final がトークン上限 194 で切り詰められない（12 秒チャンク分割で担保）。

## Decision Log
- 2026-07-05 [orchestrate] PRE: tier=L 判定（Hard Trigger: 新規コア依存）。Linear 既存タスク無しのため
  NSKETCH-820 を新規作成し NSKETCH-819 に関連付け。
- 2026-07-05 [startproject] DECISION: ランタイムは transformers（>=4.52,<5）を採用し onnxruntime 経路は不採用。
  理由: (a) 公式配布が transformers 形式のみで、モデルカードの公式コード例をそのまま参照実装にできる
  （検証環境に依存/重みが無い本タスクでは実装正しさのリスク最小化が最優先）。(b) torch は既存依存で、
  transformers の追加増分は純 Python 系ホイール約 50〜60 MB のみ（CUDA 不要）。(c) 第三者 ONNX 再
  エクスポートは KV キャッシュ付き decoder が無く greedy ループ自前実装が必要で、テスト不能環境での
  初回導入にはリスク過大。(d) onnxruntime==1.20.1 は既に requirements にあるため、検証可能な export が
  得られた時点で同一アダプタ interface の背後で v2 として差し替え可能（int8 で重み約 28 MB・CPU 2〜4 倍速
  の余地を記録として残す）。
- 2026-07-05 [startproject] DECISION: エンジン選択は既存 `get_engine()` 内に薄く実装（新 module は作らない）。
  LIVE_ENGINE=auto|whispercpp|moonshine。auto の解決順: (1) torch.cuda.is_available() → whispercpp
  （GPU ホストの挙動不変を保証）(2) moonshine 重みディレクトリ存在 → moonshine (3) ggml 重み存在 →
  whispercpp CPU (4) いずれも無ければ両取得スクリプトを案内するエラー。選択結果は必ずログ出力。
  session.py の呼び出し契約（get_engine）は不変更。
- 2026-07-05 [startproject] DECISION: 長尺音声はアダプタ内で固定 12 秒チャンクに分割して逐次 generate し
  テキスト連結（decoder max_length=194・13 tokens/sec 制約による約 14.9 秒上限への安全マージン）。
  VAD 連動分割は責務境界が崩れるため不採用、LIVE_MAX_UTTERANCE_SECONDS の短縮も whispercpp 側の
  挙動に波及するため不採用。トークン上限はモデルカード推奨の max_length = 秒数 × 13 を各チャンクに適用
  （ハルシネーションループ防止）。
- 2026-07-05 [startproject] DECISION: モデル取得は scripts/fetch_moonshine_model.py（新規）で
  UsefulSensors/moonshine-tiny-ja の 6 ファイル（config/generation_config/preprocessor_config/
  tokenizer.json/model.safetensors 約 103 MB）を models/moonshine-tiny-ja/ へ取得（huggingface_hub は
  導入済み・gated ではないため HF_TOKEN 不要）。既存 fetch_live_model.py は不変更。スクリプトは
  Moonshine AI Community License の要点（商用は登録必須）を表示する。
- 2026-07-05 [startproject] DECISION: ライセンスは MIT ではなく Moonshine AI Community License
  （研究・非商用無償／商用は年商 <$1M 無償だが moonshine.ai/community-license で登録必須、
  社内業務利用も Commercial Purpose に含まれる）。採用可否は Gate 1 でユーザー判断を仰ぐ。
  代替候補（不採用時）: 現行 whisper.cpp の ggml-small/medium への差し替え（コード変更不要・MIT）、
  ReazonSpeech k2（Apache-2.0、sherpa-onnx 依存追加）、kotoba-whisper（Apache-2.0、756M で CPU 重い）。
- 2026-07-05 [startproject] DECISION: 精度・レイテンシ期待値 — CER Fleurs 17.87 / CV17 18.3
  （large-v3-turbo 比で明確に劣るがライブドラフト用途に許容、正式書き起こしは既存バッチが再生成）。
  27M fp32 の CPU 推論は 8 スレッド級で RTF 0.05〜0.15 想定 → partial（12 秒チャンク）1 秒未満、
  30 秒 final（3 チャンク）1〜3 秒の見込み。moonshine 使用時は LIVE_PARTIAL_INTERVAL_SECONDS=1.0
  を維持可能（whisper CPU の 2.5 秒推奨から改善）。数値は依存導入環境での実測により確定（未実施検証に登録）。
- 2026-07-05 [orchestrate] GATE1: ユーザー承認取得。判断1=A（Community License 受諾・登録の上で採用、
  登録はユーザー側手続き・README に明記）、判断2=transformers 採用（ONNX は v2）、判断3=opt-in 設計。
- 2026-07-05 [team-implement] POST: エンジン選択を get_engine()/_create_engine() に集約実装、
  MoonshineEngine アダプタ追加（遅延 import・12 秒チャンク・13 tokens/sec 上限・warmup）。
  循環 import は engine.py 側 MoonshineEngine 参照の遅延化で回避。_ggml_weights_present() ヘルパを
  追加してテストの stdlib monkeypatch を排除。numpy-only 検証 PASS（新規 19 + 既存 29 無回帰）。
  transformers API スモーク・pip 解決・実測レイテンシ・E2E は依存導入環境へ引き継ぎ（未実施検証 1-7）。
- 2026-07-05 [team-review] POST: 判定 PASS。4 レビュアー統合で critical/major ゼロ。OpenCode の major 3件
  （chunk_seconds 未検証 / auto フォールバック / set_num_threads global）は承認済み設計・fail-loud・
  デフォルト非到達を根拠に minor へ整流。うち即時修正可能な 3 件（chunk_seconds バリデーション追加・
  未使用 logger 削除・fetch の LICENSE 同梱）をレビュー後に適用し全 49 テスト再 PASS。
  Security は safetensors + local_files_only で RCE/ネットワーク露出なしを確認。
  申し送り: 依存環境での未実施検証 7 項目（特に transformers 実 API #3）を deploy へ引き継ぎ。
- 2026-07-05 [startproject] PRE: コードベース精読完了（engine.py の Engine Protocol / worker の warmup・
  in-flight 抑制 / session.py の engine_provider 注入 / .gitignore の models/ 除外 / 既存テスト 29 件
  numpy のみで PASS を本環境で確認）。HF API・モデルカード・LICENSE.txt を一次情報として取得し、
  tier=L 手順として Architect（OpenCode gpt-5.5）相談を実施、設計方針は一致
  （transformers 採用・12 秒チャンク・get_engine() 内選択・fail-fast/フォールバック区別）。
  Gemini CLI は本環境に未導入のため Researcher 調査は Claude Lead が HF API 直接取得で代替。

## Design

### 論点1: アダプタ設計 — src/live/engine_moonshine.py（新規）
`streaming.py` の `Engine` Protocol（transcribe_partial/transcribe_final: np.float32 16kHz mono → str）を
そのまま実装。VAD・partial/final スケジューリング・frozen prefix・warmup・in-flight 抑制は既存機構を
無変更で流用（転写バックエンドのみ差し替え）。

```python
class MoonshineEngine:
    def __init__(self, model_dir=config.LIVE_MOONSHINE_MODEL_DIR): ...
        # lazy import transformers（未導入でも module import 可能に — engine.py と同流儀）
        # バージョン検査: transformers>=4.52 でなければ LiveEngineError で明示
        # MoonshineForConditionalGeneration.from_pretrained(model_dir) + AutoProcessor（ローカルのみ）
        # threading.Lock（worker スレッド 1 本だが engine.py と同じ防御）
        # 初期化末尾に 0.5 秒無音のダミー generate（初回推論のセットアップコストを warmup 段に吸収）
    def transcribe_partial(self, audio): return self._transcribe(audio)
    def transcribe_final(self, audio):   return self._transcribe(audio)
    # partial/final ともチャンク分割つき greedy。moonshine は temperature 等の whisper 的
    # パラメータ分岐が不要なため共通実装（差は将来必要になったら分ける）
```

- チャンク分割: `LIVE_MOONSHINE_CHUNK_SECONDS`(12.0) 超の入力を固定長分割し逐次 generate、結果連結。
  分割境界計算は純関数 `chunk_spans(n_samples, chunk_samples)` としてモジュールレベルに置き、
  transformers 無しでユニットテスト可能にする。
- トークン上限: チャンク毎に `max_length = int(秒数 × 13)`（モデルカード推奨。反復ハルシネーション防止）。
- 短音声: whisper.cpp のような 1.1 秒パディングは不要（moonshine は可変長入力が設計前提）。
  空入力は "" を即返す。
- LIVE_LANGUAGE: tiny-ja は日本語単一言語モデルのため無視（docstring に明記）。
- スレッド数: 初期化時に torch.set_num_threads(config.LIVE_WHISPER_THREADS)（Silero VAD と同一
  torch ランタイムを共有。既定は CPU コア数で現状と同じ）。

### 論点2: ランタイム選定 — transformers 採用（vs onnxruntime）
| 観点 | transformers（採用） | onnxruntime（不採用・v2 候補） |
|---|---|---|
| 配布元 | 公式（UsefulSensors/moonshine-tiny-ja） | 第三者再エクスポート（onnx-community） |
| 実装 | モデルカード公式例をほぼ転記 | KV キャッシュ無し decoder で greedy ループ自前実装 |
| 依存増分 | transformers+tokenizers+safetensors 等 約 50〜60 MB（torch 再利用・CUDA 不要） | 0（ort 1.20.1 導入済み）+ tokenizers のみ |
| 重みサイズ | fp32 108 MB | fp32 約 109 MB / int8 約 28 MB |
| CPU 速度 | 27M なら十分（RTF 0.05〜0.15 想定） | 目安 2〜4 倍速（int8） |
| 検証リスク | 低（依存無し環境でも公式例が参照実装） | 高（本環境で動作確認不能な自前デコードループ）|

決定理由: 検証環境に依存・重みが無い制約下では「公式経路・公式サンプル準拠」が正しさリスク最小。
速度・サイズで ONNX が勝る点は認識しつつ、アダプタ interface の背後に隠蔽してあるため、
検証可能な export（merged decoder）を得た時点で無破壊に差し替え可能。依存衝突: transformers は
pyannote.audio 3.1.1 / openai-whisper と衝突する既知ピン無し（huggingface_hub は下限 >=0.30 で両立、
numpy 1.26.4 対応）。クリーン環境での pip 解決確認は未実施検証に登録。

### 論点3: エンジン選択 — 既存 get_engine() 内で解決
- config.py: `LIVE_ENGINE`（既定 "auto"）/ `LIVE_MOONSHINE_MODEL_DIR`（既定 models/moonshine-tiny-ja）/
  `LIVE_MOONSHINE_CHUNK_SECONDS`（既定 12.0）を追加。
- engine.py の `get_engine()` はシングルトン管理のまま `_create_engine(config.LIVE_ENGINE)` に分岐を委譲:
  - "whispercpp" → 既存 LiveEngine（コード・挙動とも不変更）
  - "moonshine"  → MoonshineEngine（重み/依存欠如は fail-fast で明確なエラー）
  - "auto"       → (1) CUDA あり→whispercpp (2) moonshine 重みあり→moonshine
                   (3) ggml 重みあり→whispercpp (4) 双方無し→両取得スクリプトを案内するエラー
- CUDA 検出は torch.cuda.is_available()（import 失敗は False 扱い）。GPU ホストは常に従来経路になり
  「GPU 用 whisper.cpp 経路を変更しない」制約を構造的に満たす。GPU があっても moonshine を使いたい
  場合は LIVE_ENGINE=moonshine で強制可能。選択結果（engine 名 + 理由）を必ず 1 行ログする。
- フォールバック鎖での位置づけ: CUDA whisper.cpp →（非 GPU）moonshine → whisper.cpp CPU q5_0。
  moonshine 重みを取得したホストだけが新既定に乗る opt-in 設計（既存 CPU 運用の暗黙変更なし）。

### 論点4: モデル取得 — scripts/fetch_moonshine_model.py（新規）
- hf_hub_download で 6 ファイルを models/moonshine-tiny-ja/ へ（gated=false、HF_TOKEN 不要、
  git 管理外は既存 .gitignore の models/ で担保）。--dest オプションは fetch_live_model.py と同形。
- 実行時に Moonshine AI Community License の要点と登録 URL を表示。

### 論点5: ライセンス・サイズ・レイテンシのトレードオフ（記録）
- ライセンス: Moonshine AI Community License（詳細は Decision Log）。**Gate 1 承認事項**。
- サイズ: 重み 108 MB + 依存 50〜60 MB ≒ 約 170 MB 増。whisper CPU 運用（q5_0 550 MB）より軽い。
- レイテンシ/精度: Decision Log の期待値参照。final の正式版は既存バッチが担保するため、
  ライブ表示の CER 悪化はドラフト用途として許容という設計判断。

### 論点6: CPU 推奨設定（README 記載値）
- LIVE_ENGINE=auto（moonshine 重み取得のみで有効化）、LIVE_PARTIAL_INTERVAL_SECONDS=1.0 維持、
  LIVE_PARTIAL_WINDOW_SECONDS=15 維持（アダプタ内 12 秒チャンクで安全）。実測後に必要なら調整。

### 論点7: ストレッチ（言及のみ）
- バッチモード CPU 軽量オプション: MoonshineEngine を src/transcriber.py の代替バックエンドとして
  流用する将来案。話者分離（pyannote）は別問題のため本タスクでは設計しない。

### テスト設計
- tests/test_engine_select.py（新規）: _create_engine/auto 解決を monkeypatch（CUDA 検出・ファイル存在・
  エンジンコンストラクタ）で全分岐検証。numpy のみで実行可能（既存 tests/_runner.py 流儀）。
- tests/test_moonshine_chunking.py（新規）: chunk_spans の境界（ちょうど 12 秒、+1 サンプル、空、30 秒）と
  トークン上限計算。transformers 無しで import 可能なことも検証（engine.py と同じ遅延 import 要件）。
- 既存 29 テストの無回帰。

### 検証計画（環境制約の明記）
本環境で実施可能（numpy のみ・依存/GPU/重み無し）:
1. py_compile 全変更ファイル 2. 新規ユニットテスト 3. 既存テスト 29 件の無回帰
4. LIVE_ENGINE 不正値・重み欠如時のエラーメッセージ確認（monkeypatch）

依存導入環境で必要な未実施検証（team-implement/レビューで引き継ぎ）:
1. クリーン venv での pip install -r requirements.txt 解決（pyannote/whisper との衝突無し）
2. fetch_moonshine_model.py の実ダウンロード
3. 日本語 10 秒 WAV での transcribe_final スモーク（文字化け・空出力が無いこと）
4. 対象 CPU での partial/final 実測 → 期待値（partial <1 秒等）の確定と README 推奨値の校正
5. 30 秒発話 final のチャンク分割で切り詰めが起きないこと
6. 非 GPU ホストで auto→moonshine、GPU ホストで auto→whispercpp の実機確認（GPU 側は無回帰）
7. /live E2E（ブラウザ→WS→partial/final 表示→バッチ引き継ぎ）

### 実装タスクリスト
1. `src/config.py` に `LIVE_ENGINE` / `LIVE_MOONSHINE_MODEL_DIR` / `LIVE_MOONSHINE_CHUNK_SECONDS` を追加
2. `src/live/engine_moonshine.py` 新規作成（MoonshineEngine、遅延 import、バージョン検査、lock、
   `chunk_spans` 純関数、13 tokens/sec 上限、ダミー warmup）
3. `src/live/engine.py` の `get_engine()` に選択ロジック追加（LiveEngine 本体は不変更、選択ログ出力）
4. `scripts/fetch_moonshine_model.py` 新規作成（ライセンス表示含む）
5. `requirements.txt` に `transformers>=4.52,<5` を live セクションへ追加（コメント付き）
6. `tests/test_engine_select.py` / `tests/test_moonshine_chunking.py` 新規作成
7. `README.md` 更新（LIVE_ENGINE 表追加、moonshine セットアップ、精度/レイテンシ/ライセンス注記、
   CPU 推奨設定の更新）
8. 本環境で実施可能な検証の実行 + 未実施検証リストを TASK_FILE に転記

## Implementation Notes

### 実装サマリー（2026-07-05, branch: feature/moonshine-live-engine）
- 追加モジュール: `src/live/engine_moonshine.py`（MoonshineEngine + 純関数 chunk_spans / token_budget）。
- 選択ロジックは `engine.py` の `get_engine()` 内 `_create_engine()` に集約（新 module は作らず、LiveEngine 本体は不変更）。
- Gate 1 承認事項の反映: ライセンス注記（README + fetch スクリプトの実行時表示）、transformers 採用、
  auto は opt-in 設計（CUDA 優先 → moonshine 重みあり → whisper.cpp CPU → 両取得スクリプト案内エラー）。
- 主要判断:
  - (a) transformers/torch は遅延 import とし、numpy-only 環境でも module import 可能を維持
    （engine.py / vad.py と同じ流儀。テストで import 安全性を検証）。
  - (b) engine_moonshine が LiveEngineError を import する循環を、engine.py 側の MoonshineEngine
    参照を `_create_engine` 内の遅延 import にして解消（monkeypatch 可能な形も維持）。
  - (c) 長尺は 12 秒固定チャンク + `max_new_tokens = ceil(秒数×13)` で 194 トークン切り詰めを回避。
  - (d) auto は CUDA 検出を最優先し GPU ホスト無回帰を構造的に保証。選択結果は必ず 1 行 INFO ログ。
  - (e) 設計時の指摘を反映し `_ggml_weights_present()` ヘルパを追加（stdlib Path.exists の
    monkeypatch を回避し、テストがクリーンに全分岐を差し替え可能）。
  - (f) fetch スクリプトは 6 ファイル固定列挙ではなく `snapshot_download(allow_patterns=["*.json","*.safetensors"])`
    を採用（special_tokens_map.json 等の取りこぼし防止）。

### 変更ファイル（7）
- `src/config.py` — LIVE_ENGINE / LIVE_MOONSHINE_MODEL_DIR / LIVE_MOONSHINE_CHUNK_SECONDS 追加
- `src/live/engine_moonshine.py` — 新規アダプタ（Engine Protocol 実装、警告 warmup、バージョン検査、lock）
- `src/live/engine.py` — _cuda_available / _moonshine_weights_present / _ggml_weights_present /
  _create_engine 追加、get_engine() 分岐化（戻り型は Engine Protocol に拡大）、選択ログ
- `scripts/fetch_moonshine_model.py` — 新規（snapshot_download + Moonshine AI Community License 表示）
- `requirements.txt` — `transformers>=4.52,<5` を live セクションへ（コメント付き）
- `tests/test_engine_select.py`（10件）/ `tests/test_moonshine_chunking.py`（9件） — 新規
- `README.md` — 構成図、LIVE_ENGINE 系 env 表、Moonshine セットアップ節（ライセンス登録必須の注意含む）、
  CPU 推奨設定の更新

### 本環境で実施済みの検証（numpy-only、torch/transformers/重み無し）
1. py_compile 全変更ファイル PASS
2. 新規テスト 19 件 PASS（chunk_spans 境界: 空/短尺/ちょうど12s/+1サンプル/30s、token_budget 上限・下限、
   選択ロジック全分岐 + 正規化 + シングルトン + エラーメッセージ内容）
3. 既存テスト 29 件 無回帰 PASS（keywords 6 / session 9 / streaming_worker 6 / vad_segmenter 8）
4. `import src.live.engine_moonshine, src.live.engine` が依存レス環境で成功（遅延 import 要件）

### 依存導入環境で必要な未実施検証（team-review / 引き継ぎ）
1. クリーン venv での `pip install -r requirements.txt` 解決（transformers × pyannote 3.1.1 /
   openai-whisper の衝突無し、numpy 1.26.4 維持）
2. `fetch_moonshine_model.py` の実ダウンロード（models/moonshine-tiny-ja/ に配置されること）
3. **Moonshine transformers API スモーク（最重要）**: 日本語 10 秒 WAV → transcribe_final が
   非空・非文字化けテキストを返すこと（AutoProcessor の入出力形・batch_decode の呼び出し形の確認）
4. 対象 CPU での partial/final 実測 → partial <1 秒等の期待値確定と README 推奨値の校正
5. 30 秒発話 final のチャンク分割で切り詰めが起きないこと
6. 非 GPU ホストで auto→moonshine、GPU ホストで auto→whispercpp の実機確認（GPU 側無回帰）
7. /live E2E（ブラウザ→WS→partial/final 表示→バッチ引き継ぎ）

## Review

### 判定: PASS（2026-07-05、4 レビュアー: Claude / OpenCode / Security / Simplify）

critical / major はゼロ。既存 29 + 新規テスト全通過。変更は opt-in 設計で GPU / バッチ経路に無変更。

#### 統合結果（Lead 整流後）
- [minor→修正済] `MoonshineEngine.__init__` が chunk_seconds 未検証（Claude / OpenCode 共通指摘）
  → レビュー後に `0 < s <= 194/13(≈14.9)` のバリデーションを追加（重み検査より前で実行、依存レスでテスト可能）。
- [minor→修正済] `engine_moonshine.py` の未使用 `logger` / `import logging`（Simplify）→ 削除。
- [minor→修正済] fetch スクリプトの `allow_patterns` が LICENSE を取りこぼす（OpenCode）
  → `LICENSE*` / `README*` を追加（ライセンス必須配布物の同梱）。
- [minor・設計承認済み] auto 分岐は `model.safetensors` の存在のみで判定し、moonshine 構築失敗時に
  whisper.cpp CPU へ graceful fallback しない → Decision Log の fail-fast 方針どおり。将来検討として申し送り。
- [minor・設計承認済み] `torch.set_num_threads` はプロセス global（Silero VAD と共有）→ 意図済み・文書化済み。
- [minor・引き継ぎ] transformers 実 API 経路（AutoProcessor / generate / batch_decode）は本環境で
  実行不能のため未検証 → 未実施検証 #3（最重要）として deploy / PR に明記。
- [Security・positive] safetensors 読み込み + `local_files_only=True` で pickle RCE / 推論時ネットワーク露出なし。
  機密ハードコード無し、重み git 管理外、SQL/XSS/認証サーフェス無し。
- [プロセス] `.claude/rules/security.md` がリポジトリに不在（一般原則で代替）→ ルール整備を別途起票推奨。

#### テスト実行結果
- 全 49 通過: 新規 test_engine_select 10/10、test_moonshine_chunking 10/10（レビュー修正後の
  バリデーションテスト含む）、既存 29 無回帰（keywords 6 / session 9 / streaming_worker 6 / vad_segmenter 8）。
- py_compile 全 PASS、numpy-only import 成功。ブラウザ確認は非該当（UI 変更なし）。

#### 残りの申し送り（次タスク候補）
- `"model.safetensors"` リテラルの共有定数化、auto の graceful fallback 検討、
  transformers 上限の検証済み minor 固定、.claude/rules/security.md 整備。

## Deploy
<!-- deploy が記入 -->
