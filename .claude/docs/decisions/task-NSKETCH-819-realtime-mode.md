# Task: NSKETCH-819 — リアルタイムモード（ライブ会議文字起こし）の追加

## Meta
- linear_id: NSKETCH-819
- linear_url: https://linear.app/nsketch/issue/NSKETCH-819/リアルタイムモードライブ会議文字起こしの追加
- tier: L
- created: 2026-07-04
- updated: 2026-07-04 (team-implement 完了)
- status: implemented (review 待ち)
- branch: feature/live-mode (feature/web-ui から分岐)

## Task Description
既存のファイルドロップ型バッチ処理モードとは別に、新モード「リアルタイムモード」を追加する。

- PC で会議中にアプリを起動すると、その場でライブ文字起こしを表示（リアルタイムに文字が出る）
- 会議中は重要キーワードのみをリストアップして表示
- 話者分離（pyannote）等の重い処理はリアルタイムでは行わず、会議終了後にバックグラウンドでまとめて実行

### 前提・既存構成
- 既存アプリ: Whisper + pyannote 話者分離。input/ にファイルを置くと worker スレッドが処理、FastAPI ダッシュボードで進捗表示
- feature/web-ui ブランチで SSE リアルタイム進捗表示（NSKETCH-732）実装済み（未検証・未マージ）
- リポジトリ: hidemaro-nsketch/transcribe (private)。GPU があれば自動使用

### 計画フェーズで詰める論点
1. ライブ音声の取り込み方法（システム音声/マイクのキャプチャ。OS 依存、特に Windows/WSL2 での実現性）
2. ストリーミング/逐次の文字起こし方式（Whisper チャンク分割 vs faster-whisper 等の追加依存。既存方針は「依存追加は強い理由がない限り避ける」）
3. キーワード抽出のロジック
4. 会議終了後のバックグラウンド話者分離ジョブへの引き継ぎ（録音保存 → 既存バッチパイプライン）
5. 既存モードとの共存・モード切替の UI/エンドポイント設計

## Brief
- **Current State**: ファイルドロップ型バッチ処理専用。`input/` 監視（30秒ポーリング）→ Whisper+pyannote → `output/*.txt` → `done/` 移動。SSE ダッシュボード（NSKETCH-732）は feature/web-ui 上に実装済み・未マージ。モデルは worker スレッド内のローカル変数に閉じている。サーバは WSL2 上で稼働、ブラウザは Windows 側。
- **Goal**: 既存モードと共存する「リアルタイムモード」を追加。会議中: ライブ文字起こし表示 + 重要キーワードのリストアップ。会議終了後: 録音 WAV を既存バッチパイプラインへ引き継ぎ、話者分離つき正式書き起こしをバックグラウンド生成。
- **Scope**: ライブ音声取り込み（ブラウザキャプチャ方式）、whisper.cpp + VAD による逐次文字起こし（partial/final 二段表示）、キーワード抽出、セッション終了 → バッチ引き継ぎ、`/live` UI とエンドポイント。**スコープ外**: リアルタイム話者分離、複数同時ライブセッション、モバイル対応、既存バッチパイプラインのエンジン変更。
- **Constraints**: 「依存追加ゼロ」前提は**ライブエンジンに限り緩和**（whisper.cpp バインディング / Silero VAD / large-v3-turbo モデル重みを新規依存として許可。Gate 1 決定）。既存バッチモード（openai-whisper + pyannote）の依存・動作は不変。WSL2 サーバはネイティブ音声デバイスに信頼性高くアクセスできない。GPU は 1 枚をバッチ/ライブで共有（ライブ中はバッチ worker 一時停止）。既存モードの動作を壊さない。
- **Success Criteria**:
  1. ブラウザから録音開始 → 発話後おおむね数秒〜10秒以内に文字が画面に出る
  2. キーワードリストが会議中に更新される
  3. 「会議終了」で WAV が `input/` に入り既存ジョブとしてダッシュボードに現れ、話者分離つき書き起こしが完成する
  4. ライブ中もバッチモードのジョブ表示が壊れない

## Decision Log
1. `[startproject] DECISION` 音声取り込みはブラウザキャプチャ（getUserMedia/getDisplayMedia）+ WS 送信。WSL2 サーバ側キャプチャは不採用（システム音声不可・OS 依存のため）
2. `[startproject] DECISION` PCM は AudioWorklet で 16kHz/mono/Int16 に変換して送信。MediaRecorder/opus は不採用（サーバ側デコード複雑化）
3. ~~`[startproject] DECISION` 逐次転写は既存 openai-whisper のチャンク方式（無音区切り）。faster-whisper は v1 見送り、`LIVE_WHISPER_MODEL` で緩和~~ **→ #10 で破棄（Gate 1）**
4. `[startproject] DECISION` キーワード抽出は文字種正規表現 + 頻度スコア + ストップワード（依存ゼロ、プラガブル）
5. `[startproject] DECISION` 会議後引き継ぎは録音 WAV を `input/` に move して既存バッチパイプライン再利用（新ジョブ機構なし）
6. `[startproject] DECISION` ライブセッションは単一、セッション中はバッチ worker を一時停止して GPU を譲る
7. `[startproject] DECISION` モデルロードは共有レジストリへリファクタ（worker/live 双方から参照）**→ Gate 1 改訂によりライブは whisper.cpp を engine.py 内で独自常駐させるため、レジストリはバッチ用途（openai-whisper + pyannote）のみに縮小**
8. ~~`[startproject] DECISION` 新規 pip 依存ゼロ（WebSocket は uvicorn[standard] 同梱、WAV/無音検出は stdlib+numpy）~~ **→ #14 で緩和（Gate 1）**
9. `[Gate1] APPROVED` 音声取り込み = ブラウザキャプチャ（getUserMedia/getDisplayMedia）採用を承認（決定 #1, #2 確定）
10. `[Gate1] APPROVED` GPU 競合 = ライブセッション中はバッチ worker を一時停止、セッション終了後に再開を承認（決定 #6 確定）
11. `[Gate1] DECISION (supersedes #3)` 逐次転写エンジンは **whisper.cpp + large-v3-turbo**。Python からは pywhispercpp バインディングを第1候補、whisper.cpp server 常駐を第2候補とする。openai-whisper チャンク方式・faster-whisper はともに不採用
12. `[Gate1] DECISION` チャンク化は **VAD（Silero VAD）による発話区間検出**で utterance 単位。無音固定長分割は不採用
13. `[Gate1] DECISION` 表示は **partial/final 二段構え**。WS プロトコルに `partial` / `final` メッセージ種別を持たせ、発話中は暫定表示、発話終了で確定表示に置換
14. `[Gate1] DECISION (relaxes #8)` 依存追加ゼロ前提はライブエンジンに限り緩和: pywhispercpp、silero-vad、ggml large-v3-turbo モデル重みを新規依存として許可。既存バッチモード（openai-whisper + pyannote）の依存は不変
15. `[Gate1] DECISION` エンジン入力は **16kHz / mono** に統一（ブラウザ AudioWorklet で変換済み PCM を受信、サーバ側で float32 正規化して VAD/whisper.cpp へ）

## Design

### 論点1: ライブ音声取り込み — ブラウザキャプチャ + WebSocket
- WSL2 は WSLg/PulseAudio 経由のマイクが不安定でシステム音声（ループバック）は不可のため、サーバ側キャプチャは不採用。
- ブラウザ（Windows 側）で取得: マイク = `getUserMedia`、システム音声 = `getDisplayMedia({ audio: true })`（Chrome/Edge の「システム音声を共有」）。
- 転送: AudioWorklet で 16kHz mono Int16 PCM にダウンサンプルし WS バイナリフレーム送信（約 32 KB/s）。既存 `SAMPLE_RATE=16000` と一致。
- secure context: `localhost`/`127.0.0.1` は例外のため現行構成でそのまま動く。LAN 越しは将来課題として README 注記。

### 論点2: 逐次転写エンジン — whisper.cpp + large-v3-turbo + Silero VAD（Gate 1 改訂）

**パイプライン**（WS 受信 16kHz/mono/Int16 PCM → float32 [-1,1] 正規化後）:

1. **VAD 段**: Silero VAD に 512 サンプル（32ms）フレーム単位で通し、発話確率を得る
   - 発話開始: 確率 ≥ `LIVE_VAD_THRESHOLD`(0.5) が連続 → utterance バッファ開始（頭切れ防止に直前 300ms のプリロールを付与）
   - 発話終了: 無音が `LIVE_VAD_MIN_SILENCE_MS`(500) 継続 → utterance 確定
   - ガード: `LIVE_MAX_UTTERANCE_SECONDS`(30) 超過で強制確定（長広舌対策）、`LIVE_MIN_UTTERANCE_MS`(300) 未満は破棄（ノイズ・咳払い）
2. **partial（暫定）推論**: 発話継続中、`LIVE_PARTIAL_INTERVAL_SECONDS`(1.0) ごとに「発話開始〜現在」までのバッファを whisper.cpp で推論し `partial` として送信（前回 partial を全文置換）
   - 発話が長引くと再推論コストが増えるため、partial は直近 `LIVE_PARTIAL_WINDOW_SECONDS`(15) のみを対象とし、それ以前のテキストは前回結果を温存して連結する
   - partial はレイテンシ優先パラメータ（greedy / beam_size=1 / temperature=0 / no_context / single_segment）
3. **final（確定）推論**: utterance 確定時に発話全体を推論し `final` として送信。final テキストのみをドラフト保存・キーワード抽出の入力とする（partial は揮発）
4. 転写ワーカーは専用スレッド 1 本（VAD/バッファリングは WS 受信側、推論はキュー渡し）。partial 推論中に utterance が確定した場合は partial をスキップして final を優先

**エンジン実装**:
- 第1候補 **pywhispercpp**（whisper.cpp の Python バインディング）: モデルをプロセス内に常駐でき、numpy 配列を直接渡せる。`Model(LIVE_MODEL_PATH)` を `src/live/engine.py` に閉じ込める
- 第2候補（フォールバック）: whisper.cpp の **whisper-server**（HTTP 常駐）をサブプロセス起動し REST で推論。pywhispercpp の GPU ビルドが環境で通らない場合に採用
- 不採用: whisper-cli の発話毎バイナリ実行（起動 + モデルロードが毎回発生しレイテンシ要件を満たさない）
- 既存バッチの openai-whisper とはエンジン・モデルとも完全分離（`src/models.py` 共有レジストリはバッチ用途のまま。live エンジンは engine.py 内シングルトン）

### 依存追加・調達・ライセンス（Gate 1 改訂: ライブエンジン限定で緩和）

| 依存 | 形態 | ライセンス | サイズ目安 | 備考 |
|---|---|---|---|---|
| pywhispercpp | pip | MIT | 数 MB（whisper.cpp 同梱） | PyPI wheel は CPU ビルド。GPU 利用時はソースビルド要（下記） |
| silero-vad | pip | MIT | パッケージ + モデル 約 2 MB | 推論バックエンドは**既存の torch を再利用**（pyannote/openai-whisper で導入済みのため実質増分ほぼゼロ。onnxruntime は追加しない） |
| ggml-large-v3-turbo（モデル重み） | HF `ggerganov/whisper.cpp` から取得 | MIT（OpenAI Whisper 重み） | f16 約 1.6 GB / q8_0 約 870 MB / q5_0 約 550 MB | リポジトリには含めず `models/` に配置（.gitignore）。取得スクリプト同梱 |

- **調達**: `scripts/fetch_live_model.py`（huggingface_hub は pyannote 経由で導入済み）で `models/ggml-large-v3-turbo-*.bin` をダウンロード。`LIVE_MODEL_PATH` で参照。README にセットアップ手順を記載
- **バッチ側は不変**: openai-whisper + pyannote の依存・バージョンには一切触れない。ディスク上は openai-whisper のモデルキャッシュと ggml モデルが二重になる点を README に明記

**CPU/GPU 動作とトレードオフ**:

| 構成 | モデル | VRAM/RAM | 10 秒発話の final 所要 | partial 周期 |
|---|---|---|---|---|
| GPU（CUDA ビルド） | f16 or q8_0 | VRAM 約 2〜4 GB | 約 0.5〜1.5 秒 | 1.0 秒維持可 |
| CPU（8 スレッド級） | q5_0 | RAM 約 1 GB | 約 5〜10 秒 | 2〜3 秒に緩和推奨 |

- large-v3-turbo はデコーダ 4 層で large-v3 比 約 6〜8 倍速・精度はほぼ同等（日本語含む多言語でわずかに劣化、medium より高精度）
- GPU 利用には `GGML_CUDA=1` での pywhispercpp ソースビルド（CUDA toolkit 必須）が要る。ビルド不可なら第2候補（whisper-server の CUDA ビルドバイナリ）または CPU q5_0 へフォールバック
- GPU VRAM はライブ中バッチ worker が停止するため pyannote/openai-whisper と競合しない（Gate 1 決定 #10）
- CPU 環境ではモデル差し替え（`LIVE_MODEL_PATH` に ggml-medium/small を指定）でさらにレイテンシ調整可能

### 論点3: キーワード抽出 — 正規表現ベース + 頻度スコア（プラガブル）
- カタカナ連続（3+）、漢字連続（2+）、英数字語（2+）を用語候補として抽出。
- スコア = 出現頻度 × 長さボーナス。汎用語ストップワードで除外。上位 N=15。
- **final 確定のたび**に全 final テキストで再計算（partial は入力にしない。揺れる暫定文でキーワードが明滅するのを防ぐ。テキスト量的に十分軽い）。
- `src/live/keywords.py` の `extract_keywords(text, limit) -> list[Keyword]` に閉じ込め、janome/LLM への将来差し替えを容易に。

### 論点4: 会議後の話者分離引き継ぎ — WAV を `input/` に置くだけ
- セッション中、受信 PCM を `tmp_audio/live_<session_id>.wav` に逐次追記（stdlib `wave`、16kHz/mono/16bit）。
- 「会議終了」時: クローズ → `meeting_YYYYMMDD_HHMM.wav` にリネームして `input/` へ move → 既存 watcher が発見しフル処理。ダッシュボードに既存ジョブとして自動表示。
- ライブ中の暫定書き起こし（**final テキストのみ**、utterance 単位で追記）を `output/meeting_..._live_draft.txt` に保存。
- `.wav` のため `ensure_wav` の ffmpeg 変換はスキップされる。

### 論点5: 共存・モード切替 — ページ分離 + 単一ライブセッション + GPU 協調
- エンドポイント: `GET /live`（live.html）/ `WS /live/ws`（プロトコルは下記「WS メッセージ設計」）/ `GET /live/status`。
- SSE (`/events`) はバッチジョブ用に維持。ライブ更新は WS に載せ既存 SSE イベント設計を汚さない。
- UI: `index.html` ヘッダに「リアルタイムモード」リンク、`live.html` に録音ソース選択・開始/終了・ライブ書き起こしペイン・キーワードサイドバー・「バッチモードへ戻る」。
- 同時実行: ライブセッションは同時 1 本（2 本目の start は拒否）。バッチ側モデルロードは `src/models.py` の共有レジストリ（module-level シングルトン + `threading.Lock`）へリファクタ（ライブの whisper.cpp モデルは `src/live/engine.py` 内シングルトンで別管理）。ライブセッション中はバッチ worker が新規ファイル処理を一時停止（live フラグを watcher ループでチェック）。

### WS メッセージ設計（partial/final 二段表示、Gate 1 改訂）

**上り（client → server, `WS /live/ws`）**:
- バイナリフレーム: 16kHz / mono / Int16 PCM チャンク（AudioWorklet から約 128ms ごと）
- テキストフレーム（JSON）: `{"type":"start","source":"mic"|"system"}` / `{"type":"stop"}`

**下り（server → client, すべて JSON テキストフレーム）**:
```json
{"type":"status","state":"idle|recording|finalizing","session_id":"...","elapsed_sec":45.2}
{"type":"partial","utterance_id":17,"text":"では次のリリースにつ","t0":123.4}
{"type":"final","utterance_id":17,"text":"では次のリリースについて話します。","t0":123.4,"t1":131.2}
{"type":"keywords","items":[{"word":"リリース","score":12.5},{"word":"デプロイ","score":8.0}]}
{"type":"finalized","wav":"input/meeting_20260704_1500.wav","draft":"output/meeting_20260704_1500_live_draft.txt"}
{"type":"error","message":"..."}
```

**プロトコル規約**:
- `utterance_id` はセッション内で単調増加。1 発話 = 1 id
- `partial` は同一 `utterance_id` 内で**全文置換**（差分送信ではない）。ネットワーク落ちしても次の partial で自己修復する
- `final` は同 id の partial を**確定テキストで置換**する。final 送信後、同 id の partial は二度と来ない（サーバ側で保証）
- `keywords` は final 契機でのみ再計算・送信。`finalized` は stop 処理完了（WAV の `input/` 引き継ぎ完了）を示す
- 再接続リカバリ: サーバは直近 100 utterance の final をリングバッファ保持し、WS 再接続時に `status` + final 履歴を再送（進行中 partial は再送しない）

### フロント描画方針（live.html、Gate 1 改訂）
- 素の JS + WebSocket（htmx・SSE 不使用。バッチダッシュボードの SSE とは独立）
- transcript ペイン = **確定行の append-only リスト + 末尾の暫定行 1 つ**:
  - `final` 受信: 確定行 `<div class="utt-final">` を append し、暫定行を空にする
  - `partial` 受信: 暫定行 `<div class="utt-partial">` の textContent を置換するのみ（DOM 1 ノード更新、再描画なし）
  - 暫定行はグレー + イタリック等で「未確定」を視覚的に区別。final への置換で通常スタイルに
- 自動スクロールは「ユーザーが最下部にいる時のみ」実施（読み返し中のジャンプ防止）
- keywords 受信でサイドバーを全置換。`finalized` 受信で完了バナー + バッチダッシュボードへのリンク表示
- WS 切断時は指数バックオフで自動再接続し、サーバからの final 履歴再送で表示を復元

### 新規モジュール構成
```
src/live/
├── __init__.py
├── session.py     # LiveSessionManager: 状態機械 (idle/recording/finalizing)、PCM受信、WAV追記、finalize→input/引き継ぎ、final履歴リングバッファ
├── vad.py         # Silero VAD ラッパ: フレーム判定 + utterance 状態機械（開始/継続/終了検出、プリロール）
├── engine.py      # whisper.cpp (pywhispercpp) ラッパ: モデル常駐、transcribe_partial()/transcribe_final()
├── streaming.py   # utterance チャンカー + 転写ワーカーループ（partial スケジューラ、final 優先制御）
└── keywords.py    # extract_keywords()
```
- `config.py` 追加: `LIVE_MODEL_PATH`（ggml-large-v3-turbo）, `LIVE_VAD_THRESHOLD=0.5`, `LIVE_VAD_MIN_SILENCE_MS=500`, `LIVE_PARTIAL_INTERVAL_SECONDS=1.0`, `LIVE_PARTIAL_WINDOW_SECONDS=15`, `LIVE_MAX_UTTERANCE_SECONDS=30`, `LIVE_MIN_UTTERANCE_MS=300`, `LIVE_WHISPER_THREADS`, `LIVE_KEYWORD_LIMIT=15`

### 実装ステップ（team-implement 向け・依存順、Gate 1 改訂）
1. 依存導入: pywhispercpp / silero-vad を uv add、`scripts/fetch_live_model.py`（ggml-large-v3-turbo 取得）、`models/` を .gitignore、README セットアップ手順
2. モデルレジストリのリファクタ（`src/models.py` + `src/worker.py`、既存バッチ回帰確認）※バッチ側のみが対象
3. config 追加（LIVE_* 群）
4. `src/live/engine.py` — whisper.cpp ラッパ（単体で WAV → テキストのスモークテスト）
5. `src/live/vad.py` — Silero VAD ラッパ + utterance 状態機械（録音済み WAV での単体テスト）
6. `src/live/streaming.py` — utterance チャンカー + 転写ワーカー（partial スケジューラ、final 優先）
7. `src/live/session.py` — LiveSessionManager（WAV 追記、finalize → `input/` 引き継ぎ、final 履歴）
8. `src/live/keywords.py` — キーワード抽出
9. worker/watcher の live 協調フック（ライブ中一時停止）
10. Web 層（`app.py` に `/live` ルート + WS、`live.html` 新規（partial/final 二段描画）、`index.html` 切替リンク）
11. 結合確認（GPU/CPU 両モード）+ README 更新

### リスクと備え（Gate 1 改訂）
- **pywhispercpp の GPU ビルド失敗**（CUDA toolkit 相性）→ 第2候補: whisper-server 常駐（CUDA ビルド済みバイナリ）、第3候補: CPU q5_0 で動作継続。engine.py 抽象化で差し替え可能に
- **CPU 環境のレイテンシ** → q5_0 量子化 + partial 周期 2〜3 秒 + `LIVE_MODEL_PATH` で medium/small へ差し替え可。README に GPU 推奨明記
- **長い発話での partial 再推論コスト増** → `LIVE_PARTIAL_WINDOW_SECONDS` で直近ウィンドウのみ再推論
- **VAD 閾値がシステム音声（会議アプリ出力・BGM 混在）でずれる** → `LIVE_VAD_THRESHOLD` / `LIVE_VAD_MIN_SILENCE_MS` を設定可能に
- **モデル重み約 0.5〜1.6 GB のダウンロード** → 取得スクリプト + README。リポジトリには含めない。openai-whisper キャッシュとの二重保持によるディスク増も明記
- `getDisplayMedia` の音声共有はダイアログで「音声を共有」選択が必要 → UI に案内文
- WS 切断時: 切断後 60 秒で自動 finalize（録音は保全して引き継ぎ）
- feature/web-ui 未マージ → 本タスクは feature/web-ui から分岐（SSE 実装に依存）

## Implementation Notes

### 追加 Decision（team-implement）
16. `[team-implement] DECISION` 量子化デフォルトは **q8_0**（`LIVE_MODEL_QUANT` で切替）。理由: large-v3-turbo では f16 とほぼ同精度でサイズ/VRAM が約半分（約 870 MB vs 1.6 GB）。GPU はバッチモデルと 1 枚を共有するため VRAM に余裕を残す「無難な」既定。q5_0（約 550 MB）は精度が目に見えて落ちるため RAM 制約 CPU 環境向けのオプトインに留めた。`LIVE_MODEL_PATH` 明示指定が常に最優先。
17. `[team-implement] DECISION` 依存導入は設計書の `uv add` ではなく **requirements.txt 追記**（リポジトリは pip + requirements.txt 運用で pyproject/uv.lock が存在しないため）。
18. `[team-implement] DECISION` テストハーネスは新規導入。ただし実行環境に pip/pytest が無いため、**pytest 互換の素関数 + stdlib ランナー（tests/_runner.py）** とし、`python3 tests/test_*.py` で直接実行可能にした。pytest 導入後はそのまま `pytest tests/` で動く。
19. `[team-implement] DECISION` 短すぎる発話（`LIVE_MIN_UTTERANCE_MS` 未満）の破棄は、クライアントの partial 行を消すために **空テキストの `final`** を送って通知する（プロトコル追記: `final.text == ""` は破棄扱い、draft/キーワードには入れない）。
20. `[team-implement] DECISION` バッチ一時停止フラグは `src/live/state.py` の `threading.Event` に分離（watcher/worker が torch 等を import せずに参照できるように）。watcher は一時停止中も `on_discover` を呼び、新規ファイルは queued としてダッシュボードに出る。
21. `[team-implement] NOTE` ライブ中でも「処理中のバッチジョブ」は中断しない（次のファイルから停止）。ライブ開始時に GPU 競合の窓が残る点は v1 の既知制約。
22. `[team-implement] NOTE` バッチモデルは常駐のまま（unload しない）。VRAM が逼迫する環境では f16 でなく q8_0/q5_0 を使う運用で回避。レジストリ化（models.py）により将来 unload フックを足せる構造にした。
23. `[team-review] POST` 第1回判定 FAIL。major 1（ライブドラフトのセッション跨ぎ混線）+ minor 7。テスト 27/27 PASS。逐次転写/ブラウザ経路は依存不在で未検証 → deploy 前実機スモークを必須化。
24. `[team-review] POST` 第2回再レビュー: **PASS**。FAIL 全指摘（major 1 + minor 7）を 4a456cc で解消、リグレッションなし、29/29 PASS。OpenCode 指摘「bounded queue の無差別ドロップ (major)」は replay/draft 永続による回復可能性を根拠に minor へ降格。申し送り minor 4 件（overflow 時の final 温存方式、_origin_allowed のプロキシ境界、_filter_params の best-effort 性、worker 専有状態の過剰ロック注記）は次タスク扱い。

### 作成・変更ファイル
- 新規 `src/live/__init__.py, state.py, engine.py, vad.py, streaming.py, session.py, keywords.py`
- 新規 `src/web/templates/live.html`（素の JS + WebSocket + AudioWorklet インライン、partial/final 二段描画、指数バックオフ再接続、最下部時のみ自動スクロール）
- 新規 `scripts/fetch_live_model.py`（HF ggerganov/whisper.cpp から ggml-large-v3-turbo-{quant}.bin を models/ へ取得）
- 新規 `tests/`（_runner.py + test_keywords.py 6件 / test_vad_segmenter.py 8件 / test_streaming_worker.py 6件 / test_session.py 7件 = 27件）
- 変更 `src/config.py`（LIVE_* 設定群）、`src/models.py`（バッチ用共有レジストリ get_whisper_model/get_diarization_pipeline/get_audio_cropper、lock 付きシングルトン）、`src/worker.py`（get_* へ切替 + should_pause 接続）、`src/watcher.py`（should_pause 引数）、`src/web/app.py`（GET /live, GET /live/status, WS /live/ws、スレッド→イベントループは loop.call_soon_threadsafe + asyncio.Queue で橋渡し、worker スレッドから WS を直接触らない）、`src/web/templates/index.html`（/live リンク）、`requirements.txt`（pywhispercpp, silero-vad）、`.gitignore`（models/）、`README.md`（セットアップ・LIVE_* 表・量子化表・GPU ビルド注記・ディスク二重保持注記）

### 実行できた検証（この環境: fastapi / torch / pywhispercpp / GPU / モデル重みなし）
- `python3 -m py_compile`: main.py / src/**/*.py / scripts / tests 全て OK
- 単体テスト 27/27 PASS（依存レス部分のみ）:
  - keywords: 頻度×長さスコア / ストップワード / 文字種抽出 / 決定性
  - vad (UtteranceSegmenter): start/update/end、プリロール付与、min 未満 cancel、max 強制確定、flush、フレーム跨ぎバッファ（Silero 本体はモック。合成確率で状態機械のみ検証）
  - streaming (TranscriptionWorker): partial→final 順序、final 優先で stale partial スキップ、cancel→空 final、partial ウィンドウの凍結プレフィックス連結、interval レート制限、エンジン例外でも worker 生存
  - session (LiveSessionManager): フルセッション（WAV が input/ に meeting_*.wav で引き継がれ、フレーム数一致、draft 生成、final/keywords/finalized 配信）、二重 start 拒否、idle stop 拒否、再接続 replay（status+final 履歴+keywords）、切断→猶予後自動 finalize、再接続でタイマー解除、live_active フラグのライフサイクル
- テスト中に発見・修正したバグ 1 件: セグメンタのプリロールが発話開始フレームを preroll deque に先に積んでいたため 1 フレーム分欠落 → 修正済み（test_preroll_is_prepended_to_utterance_audio で回帰防止）

### 依存導入環境で必要な未実施検証
1. `pip install -r requirements.txt` + `python scripts/fetch_live_model.py` → `src/live/engine.py` の WAV→テキスト スモーク（pywhispercpp の transcribe パラメータ名 language/no_context/single_segment/temperature の実バインディングとの整合確認を含む）
2. Silero VAD 実モデルでの発話区切り精度（LIVE_VAD_THRESHOLD/MIN_SILENCE の実会議チューニング）
3. GPU: `GGML_CUDA=1` での pywhispercpp ソースビルド、ライブ中バッチ停止→再開の実機確認、VRAM 同時常駐（バッチ medium+pyannote + live q8_0）の実測
4. ブラウザ E2E: getUserMedia/getDisplayMedia キャプチャ、AudioWorklet ダウンサンプル音質、partial/final 表示、WS 再接続復元、finalized→ダッシュボード遷移
5. 会議終了→input/ 引き継ぎ→既存バッチ処理完走（success criteria 3）、ライブ中のバッチ表示非破壊（criteria 4）
6. CPU 実測レイテンシ（q8_0 / q5_0）と LIVE_PARTIAL_INTERVAL_SECONDS の推奨値検証

## Review

### 第1回 team-review（tier=L: Claude / OpenCode / Security / Simplify）: FAIL
- 単体テスト 27/27 PASS、py_compile OK。逐次転写経路（pywhispercpp/Silero/GPU/ブラウザ）はレビュー環境に依存が無く実行不能。
- **major 1件**: ライブドラフトのセッション跨ぎ混線 — `session_id` が分粒度のため、同一分内の 2 セッションで `output/meeting_..._live_draft.txt` に追記混線（WAV は `_unique_target` で無事）。
- minor: worker 共有状態ロックなし / outbound Queue 無制限 / feed_pcm の PCM 長未検証 / WS Origin 未検証 / start() 例外時 cleanup 欠如 / SileroVad.reset デッドコード / stop タイムアウト時の遅延 emit のセッション帰属未検証。
- OpenCode 指摘「stop が in-flight 音声を破棄（major）」は WS 受信ループの await 直列化により非該当と査定し棄却。
- Security: XSS なし（全て textContent 描画）、機密ハードコードなし、パストラバーサルなし。

### FAIL 対応（DONT-ASK リトライ 1 回目で全指摘を修正）
- **major 修正**: `_unique_session_id()` を追加 — draft(output/)・WAV(input/・done/)・temp の全アーティファクト位置に対して分粒度 ID を一意化（`_2`, `_3` サフィックス）。回帰テスト `test_two_sessions_same_minute_get_distinct_artifacts` 追加。
- minor 修正（全件）:
  - streaming.py: `_state_lock` で feed 側/worker 側の共有状態（finalized_ids/partial_inflight/frozen 等）を保護。推論呼び出しはロック外。
  - session.py: `_make_emit(session_id)` で worker 出力をセッションに紐付け、セッション終了後の遅延 emit を破棄。奇数長 PCM フレームの防御（末尾 1 byte 切捨て）。start() 途中失敗時に WAV close + 削除。
  - app.py: WS Origin 検証（cross-site WebSocket hijack 防御、Origin 無しの非ブラウザクライアントは許可）、outbound キュー maxsize=1000 + 溢れ時ドロップ（partial は自己修復、final は再接続 replay で回復）、feed_pcm 例外を error メッセージ化して接続維持。
  - engine.py: pywhispercpp の `PARAMS_SCHEMA` に対して transcribe kwargs をフィルタ（バインディング版差異で例外でなく品質劣化に留める防御）。
  - vad.py: 未使用の `SileroVad.reset()` を削除。
- 修正後テスト: **29/29 PASS**（回帰 2 件追加）。

### 第2回 team-review（修正コミット 4a456cc の検証）: PASS
- 前回全指摘（major 1 + minor 7）の解消をコードトレース + 回帰テストで確認。critical/major 0。29/29 PASS、py_compile OK。
- `_unique_session_id` は draft/input/done/temp の 4 箇所を網羅、`start()` が lock 保持のため TOCTOU なし。`_state_lock` は非再入でもデッドロック経路なし。`_make_emit` は stop() ドレイン中の正規 final を通過させ、60s タイムアウト後の遅延 emit のみ破棄することを確認。
- Security: WS Origin 検証は CSWSH への正の防御。bounded queue は slow-client DoS を緩和。認証は既存 localhost trust-all を踏襲（劣化なし）。
- 申し送り（minor、次タスク可）: overflow 時に partial 優先破棄で final/error を温存する方式、`_origin_allowed` のプロキシ/`X-Forwarded-Host` 境界、`_filter_params` の best-effort 性、worker 専有状態の過剰ロック注記。
- **deploy ゲート（残置)**: 依存導入環境での実機スモーク必須 — pywhispercpp transcribe パラメータ実整合、Silero VAD 実挙動、ブラウザ E2E、input/ 引き継ぎ→バッチ完走、GPU ビルド。状態文字列の Enum 化・LiveSessionManager の責務分離は次タスク推奨。

## Deploy
<!-- deploy が記入 -->
