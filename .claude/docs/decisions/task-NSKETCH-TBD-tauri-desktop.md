# Task: NSKETCH-TBD — Tauri でスタンドアロンのデスクトップアプリ化

> **Linear ID: NSKETCH-TBD (未採番 / ローカル運用)**
> Linear MCP は使用しない。issue 作成・ステータス遷移・コメント投稿はすべて本ファイルへの記録で代替する。
> 実 ID が採番されたらファイル名を `task-{ID}-tauri-desktop.md` に rename すること。

## Meta
- linear_id: NSKETCH-TBD (ローカル運用)
- tier: L
- created: 2026-07-26T00:56+09:00
- status: awaiting-approval (Gate 1)

## Task Description
この webapp (FastAPI + Jinja2 テンプレート + WebSocket/SSE、Whisper/pyannote/Moonshine の Python ML スタック) を Tauri でスタンドアロンのデスクトップアプリ化する。
設計フェーズで OpenCode (openai/gpt-5.6-sol-pro、失敗時 github-copilot/gpt-5.6-sol) に必ず相談すること。

### 論点
1. Tauri v2 での Python サイドカー同梱方式 (PyInstaller/uv sidecar vs 外部インタープリタ)
2. 巨大な torch/CUDA 依存とバンドルサイズ
3. ライブモードのマイク入力 (現在: ブラウザ getUserMedia + AudioWorklet → WebSocket) を WebView 上で secure context をどう満たすか
4. ffmpeg 同梱
5. モデル (Moonshine/pyannote/whisper) の配布とダウンロード戦略
6. Windows x64 ターゲット

### 関連メモリ
- CPU-first 方針: デフォルトは軽量 CPU (Moonshine)、GPU はオプトインの heavy mode
- Windows ARM (Surface Laptop) では x86-64 Python 3.12 エミュレーション実行
- 依存 pin 済み: torch/torchaudio/fastapi。batch diarization は torchaudio>=2.2 vs pyannote 3.1.1 で破損中

## Brief

FastAPI + Jinja2（テンプレート内インライン JS/CSS、ビルドステップなし）+ SSE `/events` + WS `/live/ws`（生 16kHz mono s16 PCM）の
ローカル webapp を、Tauri v2 で Windows x64 スタンドアロンデスクトップアプリ化する。

### コードベースの確定事実（詳細: `.claude/docs/research/tauri-desktop-codebase.md`）
- 起動: `main.py` → uvicorn（app オブジェクト直渡し、CLI 引数なし、設定は 100% env var・import 時読取）
- バッチワーカーは同一プロセスの daemon thread。multiprocessing なし（freeze に有利）
- Python 3.12 必須（numpy 1.26.4 pin）。requirements.txt のみ。torch 2.2.2 CPU / pyannote 3.1.1（HF gated, HF_TOKEN 必須）/ openai-whisper（全ファイルで ffmpeg を shell out）/ Moonshine tiny-ja（CPU, ja は再配布制限あり）/ silero-vad
- 凍結後バックエンド見積: CPU-only で 1.2–1.8GB（モデル別途 0.1–3GB）

### パッケージング阻害要因（Blocking 7 件）
1. テンプレートdir が `__file__` 相対
2. `TRANSCRIBE_BASE_DIR` デフォルトが CWD（Program Files では書込不可）
3. ffmpeg を PATH の裸名で解決（whisper 内部呼び出し含め必須）
4. WS Origin チェックが `tauri.localhost` オリジンを 1008 で拒否
5. `getDisplayMedia`（システム音声）は WebView2 でほぼ動かない / マイク getUserMedia は secure context 的に OK だが PermissionRequested 対応要
6. Google Fonts CDN（オフライン破損 + CSP ブロック）
7. ポート 8000 固定（衝突時フォールバックなし）

### 一次情報の確定事実（詳細: `.claude/docs/research/tauri-desktop-sources.md`）
- NSIS/WiX は 2GB 超ペイロードでビルド失敗（tauri#7372）→ torch/モデルはインストーラ同梱不可
- サイドカーは自動 kill されない → `RunEvent::ExitRequested` で明示 kill + Job Object
- Windows の WebView オリジン `http://tauri.localhost` は secure context。getUserMedia 可、`ws://127.0.0.1` mixed-content 問題なし
- ffmpeg LGPL は BtbN `lgpl` win64 ビルド。Whisper 重みは MIT（再配布可）/ pyannote segmentation-3.0 は MIT だが HF gated / **Moonshine ja は非商用 Community License**
- 標準アーキテクチャ: 小さいインストーラ + 初回起動時ダウンロード（torch 環境 / ffmpeg / モデル → app-data）

## Decision Log
- 2026-07-26: Linear MCP 未認証のため、コーディネーター指示によりローカルタスクファイル運用に切替（全ステップで Linear 呼び出しスキップ）
- 2026-07-26: tier=L 判定（Hard Trigger: 新規コア依存 Tauri/Rust、アーキテクチャ変更、10+ files）
- 2026-07-26: OpenCode 設計相談は `openai/gpt-5.6-sol-pro` が Quota exceeded → 規定のフォールバック `github-copilot/gpt-5.6-sol` で実行
- 2026-07-26: OpenCode リサーチも sol-pro が無応答（~25分、kill）。モノリシック長文プロンプトはフォールバックでもストール → 3 分割実行で成功（運用知見: opencode への長文一括質問は避ける）
- 2026-07-26: **裁定 (firecrawl 優先)** `useHttpsScheme` 有効化案（OpenCode 知見）は不採用 — 一次情報では `http://tauri.localhost`/`127.0.0.1` は既に secure context で、useHttpsScheme は mixed content をブロックする副作用あり。そもそも WebView を `http://127.0.0.1:<port>` に直接遷移させる設計で無関係化
- 2026-07-26: **裁定** ffmpeg は BtbN LGPL ビルド（firecrawl 確定）。OpenCode 知見は lgpl-shared (DLL 群)、設計相談は lgpl static (単体 exe) を推奨 → **static 単体 exe を採用**（DLL 探索問題の回避を優先）。CI で `-buildconf` の GPL/nonfree 無効を検証
- 2026-07-26: **裁定** サイドカー方式: firecrawl は PyInstaller が Tauri docs 推奨と確認したが、2GB インストーラ上限により結局「初回ダウンロード」が必須。設計相談推奨の **python-build-standalone 再配置可能ランタイム（初回 DL）を採用**、PyInstaller onedir は hidden-import 保守負担が大きく次善策に格下げ
- 2026-07-26: **裁定** Moonshine tiny-ja は v1 から除外（ja は非商用 Community License、再配布不可 — firecrawl 確定）。デスクトップ版ライブ ASR は pywhispercpp + MIT の whisper ggml 重みに切替
- 2026-07-26: マイクは WebView2 getUserMedia + AudioWorklet を維持（全ソース一致: 127.0.0.1 は secure context）。システム音声 (getDisplayMedia) は v1 から除外
- 2026-07-26: status: planning → awaiting-approval (Gate 1)
- 2026-07-26: **Gate 1 承認（ユーザー判断 3 点で計画修正）**:
  - ① システム音声は v1 除外を却下 → **WASAPI ループバック (Rust cpal) で v1 実装**（getDisplayMedia の置換。会議用途の中核機能のため）
  - ② Moonshine 完全除外を却下 → **選択式で存続**。既定 `LIVE_ENGINE` を moonshine → whispercpp に変更。Moonshine ja 重みは同梱・自動 DL せず、明示選択時のみ非商用ライセンス同意フロー付きで DL。pywhispercpp を必須依存に格上げ（1.5.0 に win_amd64 ホイールあり・確認済み）
  - ③ ランタイム archive は **GitHub Releases (public 前提)** + **v1 は無署名**（SmartScreen/Defender 回避手順を README 記載）。コード署名は将来課題として保留
- 2026-07-26: システム音声 PCM の経路設計: **Rust が `/live/ws` への第 2 の WS クライアント（PCM 送信専用）となり、`feed_pcm` 契約 (`src/live/session.py:149`) は無変更**。開始/停止の伝達はフロントエンド→既存 WS 制御メッセージ→バックエンドが stdout に `TAURI_EVENT` 行を出力→Rust が drain 中の stdout から検知（remote ページへの Tauri IPC 解禁を回避）。リサンプリング（デバイス既定 48kHz/f32 → 16kHz/mono/s16le）は **Rust 側の責務**。却下案: フロントエンド IPC 経由（`dangerousRemoteDomainIpcAccess` が必要になり設計方針と矛盾）/ HTTP チャンク POST（レイテンシ・オーバーヘッド）
- 2026-07-26: status: awaiting-approval → implementing (Gate 1 passed)

## Design

参照: `.claude/docs/research/tauri-desktop-{codebase,sources,opencode,design-consult}.md`

### アーキテクチャ（3 層配布）
| 層 | 配布方法 | 内容 | サイズ見積 |
|---|---|---|---|
| Tauri シェル | NSIS インストーラ (目標 ~150-250MB) | Tauri 本体 + セットアップ UI + Python アプリコード/templates + ffmpeg (BtbN LGPL static) + ライセンス | << 2GB 上限 |
| CPU バックエンド環境 | 初回起動時 DL (Rust downloader) | python-build-standalone 3.12 + 固定 site-packages (torch CPU/pyannote/transformers 等) | 展開 ~1.2-1.8GB / DL ~0.6-0.9GB |
| モデル | 機能初回利用時 DL (Python 側) | whisper medium ~1.4GB / ggml-large-v3-turbo q8_0 ~870MB / pyannote ~30MB (gated, 要 HF_TOKEN) | 0.9-3GB |

- 配置: `%LOCALAPPDATA%/Transcribe/` 配下に `data/`(=TRANSCRIBE_BASE_DIR), `runtimes/cpu-<ver>/`, `models/`, `logs/`, `current-runtime.json`
- Rust が env 注入: `TRANSCRIBE_BASE_DIR`, `HF_HOME`/`XDG_CACHE_HOME`→model_cache, `PATH`←ffmpeg dir, shutdown secret, `WEB_PORT` は使わず port 0

### 主要設計判断
1. **WebView は起動後 `http://127.0.0.1:<port>/` へ直接遷移**（セットアップ中のみ Tauri asset の bootstrap ページ）。same-origin 化により WS Origin チェック無変更・CSP 例外不要・getUserMedia は secure context で動作。Tauri IPC はアプリページでは不使用（`dangerousRemoteDomainIpcAccess` 設定しない）
2. **ポート/ライフサイクル**: Python が `127.0.0.1:0` を自分で bind → stdout に `TAURI_READY {"port":...,"token":...}` → Rust が `/healthz` 確認後に遷移。終了は `POST /internal/shutdown` (secret 認証) → タイムアウトで kill → **Job Object (KILL_ON_JOB_CLOSE, CREATE_SUSPENDED→assign→resume)** で子孫 (ffmpeg) ごと確実終了。stdout/stderr は Rust が常時 drain してログへ
3. **マイク**: WebView2 getUserMedia + AudioWorklet 維持。Rust で `PermissionRequested` を処理（exact origin + Microphone のみ許可）
3b. **システム音声 (v1 実装)**: getDisplayMedia を廃止し **Rust cpal の WASAPI ループバック**に置換。経路: フロントエンドがソース `system` で `{"type":"start","source":"system"}` を既存 WS 制御で送信 → バックエンドが stdout に `TAURI_EVENT {"capture":"start"}` 行を出力 → Rust（stdout drain 中）が検知して WASAPI loopback 開始 → **Rust が downmix + 48kHz→16kHz リサンプル + s16le 変換**して `/live/ws` への第 2 WS クライアント（PCM 送信専用）として送出。停止も同様に `TAURI_EVENT {"capture":"stop"}`。`feed_pcm` 契約は無変更。※ `live_ws` の複数接続時の挙動（第 2 接続の bytes が同一セッションに feed されるか）は Phase A で検証し、必要なら feeder 接続モードを追加
4. **ライブ ASR**: 既定を **whispercpp**（`LIVE_ENGINE` 既定値を moonshine → whispercpp に変更、pywhispercpp を必須依存に格上げ / win_amd64 ホイールあり）。**Moonshine は選択式で存続**: 重みは同梱・自動 DL せず、ユーザーが明示選択した時のみ非商用ライセンス同意フロー付きで DL
5. **堅牢化**: ライブ WAV は `.wav.part` + atomic rename、起動時に RIFF 修復 → `input/recovered_*`。ffmpeg 呼び出しに `CREATE_NO_WINDOW`（whisper 内部呼び出しも subprocess パッチで対応）。Google Fonts はローカル同梱（woff2 vendor + StaticFiles mount）
6. **更新**: Tauri updater（シェル）と backend manifest（runtime バージョン + protocol version + rollback）を分離

### 実装タスク（v1）
**Phase A: Python バックエンド改修（既存ファイル）**
- A1 `main.py`: port-0 bind + `TAURI_READY` stdout ハンドシェイク + shutdown secret 受取
- A2 `src/web/app.py`: `/healthz`（worker: loading/ready/failed）+ `POST /internal/shutdown`
- A3 `src/audio.py` + subprocess パッチ: `CREATE_NO_WINDOW`、`FFMPEG_PATH` env 対応
- A4 `src/live/session.py`: `.wav.part` + 起動時回復処理
- A5 templates: フォント vendor 化（+ StaticFiles mount）、live.html: ソース選択 UI は維持しつつ `system` 選択時は getDisplayMedia を廃止（制御メッセージのみ送信、キャプチャはネイティブ側で実施される旨の状態表示）
- A6 `requirements-windows.lock` 生成（hash 固定）
- A7 `src/config.py`/`src/live/engine.py`: `LIVE_ENGINE` 既定を whispercpp へ変更、pywhispercpp を必須依存化（requirements.txt）
- A8 `src/web/app.py`+`src/live/session.py`: `system` ソースの start/stop 受信時に stdout へ `TAURI_EVENT {"capture":...}` 行を出力。`live_ws` 第 2 接続（PCM feeder）の挙動検証・必要なら対応
- A9 Moonshine 選択時のオンデマンド DL + ライセンス同意フロー（設定 UI/エンドポイント、同意記録は app-data に永続化）

**Phase B: Tauri シェル（新規 `src-tauri/`）**
- B1 Cargo プロジェクト + `tauri.conf.json`（NSIS, capabilities 最小）+ bootstrap ページ
- B2 `downloader.rs`: runtime archive DL（Range resume + SHA-256 + atomic rename + rollback + 進捗）
- B3 `process.rs`: spawn + Job Object + stdout drain + handshake + graceful shutdown
- B4 `webview.rs`: 127.0.0.1 遷移制御 + PermissionRequested + 外部リンクはブラウザへ
- B5 セットアップ UI: 空き容量確認 / HF_TOKEN 入力 (Credential Manager 保存) / pyannote gate 承認リンク / モデル DL 進捗
- B6 `capture.rs`: cpal WASAPI ループバックキャプチャ + downmix/リサンプル (48k f32 → 16k mono s16le) + `/live/ws` への WS クライアント送出（tokio-tungstenite 等）
- B7 `process.rs` 拡張: stdout drain に `TAURI_EVENT` 行パーサを追加し capture start/stop を B6 へ中継

**Phase C: パッケージング/CI（新規 `packaging/`, `.github/workflows/`）**
- C1 `packaging/backend/build.ps1`: python-build-standalone + lock 済み deps → 再配置可能 archive + manifest.json
- C2 `packaging/ffmpeg/`: BtbN LGPL static 固定版取得 + SHA-256 + `-buildconf` 検証 + ライセンス同梱
- C3 GitHub Actions (windows-2022): テスト → backend archive → relocation smoke test → `tauri build --bundles nsis` → 署名（Authenticode + updater 鍵は別物）→ E2E（インストール→初回DL→/healthz→孤児プロセス検査）

### v1 スコープ外（延期）
CUDA 環境 / Windows ARM64 native / backend 差分更新 / remote ページへの Tauri IPC / コード署名（Authenticode + updater 署名）

### 未解決リスク（上位）
1. Moonshine ja ライセンス: 同梱・自動 DL なし + 明示選択時のみ同意フロー付き DL で対応（再配布に該当しない構成）
2. 1GB 超初回 DL の失敗率（resume/checksum/rollback で緩和）— ホスティングは **GitHub Releases (public 前提)** で決定。private 化する場合は要再設計
3. python-build-standalone 再配置の実績不足（CI で relocation smoke test 必須。失敗時は PyInstaller onedir へフォールバック）
4. **無署名配布**（ユーザー決定）による SmartScreen/Defender 警告 → 回避手順を README に記載。署名は将来課題
5. WebView2 マイク権限のバージョン差（実機 smoke test 必須）
6. WASAPI ループバック（cpal）の実装リスク: 排他モードデバイス・無音時のパケット欠落・デバイス切替。実機検証必須
7. pyannote gated モデル: ユーザー各自の HF_TOKEN + 2 つの gate 承認が必要（初回 UX で明示）
8. 開発環境が WSL2 のため Windows 実機ビルド・検証不可 → **検証できない箇所は「未検証」と明示し、憶測で動作確認済みと記載しない**（コーディネーター指示）

## Implementation Notes

実施: 2026-07-26/27、branch `feature/tauri-desktop`。/team-implement fork は read-only だったため、execution-ready 計画を策定後、オーケストレーターが write 可能な 3 班（Phase A/B/C 並列、モジュール別オーナーシップ）で実装。凍結契約（TAURI_READY/TAURI_EVENT/healthz/shutdown/WS feeder/env）を全班に同一文面で配布し drift を防止。

### Phase A: Python バックエンド（検証済み — pytest 237 passed / baseline 174 → +63）
- 新規: `src/version.py` (BACKEND_VERSION/PROTOCOL_VERSION), `src/worker_state.py`, `src/ffmpeg_patch.py`, `src/live/recovery.py` (.wav.part RIFF 修復→`input/recovered_*.wav`), `src/live/moonshine_fetch.py` (同意記録+DL), `src/web/static/fonts/` (woff2 x4 vendor 済・DL 成功)
- 変更: `main.py` (TRANSCRIBE_DYNAMIC_PORT=1 で port-0 bind→TAURI_READY→Server.serve(sockets); env 無しは従来動作), `src/web/app.py` (/healthz, /internal/shutdown [hmac.compare_digest, secret 未設定=404], /static mount, /internal/models/moonshine GET/POST), `src/worker.py`, `src/audio.py` (FFMPEG_PATH+CREATE_NO_WINDOW), `src/live/session.py` (.wav.part + TAURI_EVENT), `src/config.py`+`src/live/engine.py` (**LIVE_ENGINE 既定 whispercpp**), `requirements.txt` (pywhispercpp==1.5.0 必須化), templates (フォントローカル化 / live.html system=制御のみ)
- テスト新規 7 ファイル + 更新 2（実 uvicorn E2E での handshake/shutdown 含む）
- **所見**: feeder 接続もクライアント数にカウント → ブラウザ閉でも feeder 接続中は 60s auto-finalize 不発。単一ウィンドウ構成のためアプリ終了時は shutdown 経路で finalize されるが、「フロント不帰還 + feeder 生存」でキャプチャ継続の edge case あり（レビュー観点）

### Phase B: Tauri シェル `src-tauri/`（**全体未検証** — WSL2 のため cargo check すら不可）
- B1-B7 全ファイル author 済（Cargo.toml / tauri.conf.json / capabilities / bootstrap/index.html / main.rs / downloader.rs / process.rs / webview.rs / capture.rs / README.md）
- 特記: CreateProcessW (CREATE_SUSPENDED→Job assign→resume, KILL_ON_JOB_CLOSE) / shutdown は std TcpStream の手書き HTTP (async runtime 回避) / capture は cpal loopback→mono→linear resample→s16le→tokio-tungstenite feeder (drop-on-backpressure) / HF token は keyring→Credential Manager
- 既知の要修正候補（README にも明記）: windows/webview2-com のバージョンは wry と要整合、windows-rs API シグネチャは初回ビルドで機械的修正前提、cpal loopback API 形状、reqwest の Range × GitHub リダイレクト

### Phase C: packaging/CI（**全体未検証**）
- `packaging/backend/` build.ps1 (CUDA ガード + relocation smoke test + 決定的 zip + manifest), make-lock.ps1, lock プレースホルダ（捏造ハッシュなし・マーカー検出で fail）, python-pin.json
- `packaging/ffmpeg/` fetch.ps1 (SHA-256 + `-L`/`-buildconf` の GPL/nonfree 検査), pin.json (REPLACE_ME), SOURCE.txt.template, README (LGPL 義務)
- `.github/workflows/desktop-windows.yml` (python-tests→backend-archive→tauri-build→e2e-smoke[experimental])、`docs/DESKTOP.md`

### オーケストレーターによる統合修正
- CI ステージング手順を `tauri.conf.json` の実参照（`../main.py`, `../src`, `../packaging/ffmpeg/*`, `../packaging/backend/manifest.json`）に整合、`__pycache__` prune を repo 側 src/ に適用
- `createUpdaterArtifacts: true → false`（無署名 v1 では署名鍵必須になりビルド不能のため。Gate 1 決定と整合、README 更新）
- 統合後テスト再実行: 237 passed

### リリースブロッカー（実 pin 待ち）
1. `packaging/backend/python-pin.json` sha256、2. `packaging/ffmpeg/pin.json` 全項目、3. `requirements-windows.lock`（Windows で make-lock.ps1 実行）、4. Windows 実機/CI での Phase B/C 初回ビルド

### 未検証事項（憶測で動作主張しない）
Phase B/C 全体、CREATE_NO_WINDOW 実効果、実 openai-whisper への patch 適用、pywhispercpp win_amd64 動作、WebView2 でのフォント描画・マイク許可・feeder 実接続、WASAPI loopback 実機動作

## Review
<!-- team-review が記入 -->

## Deploy
<!-- deploy が記入 -->
