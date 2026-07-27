# OpenCode Design Consultation: Tauri desktop packaging (NSKETCH-TBD)

> Model: github-copilot/gpt-5.6-sol (fallback; openai/gpt-5.6-sol-pro failed with Quota exceeded / hang)
> Date: 2026-07-26. The model read the codebase and both research files (tauri-desktop-codebase.md, tauri-desktop-sources.md) before answering.

まず既存の設計資料と実装上の制約を確認し、提示された事実を前提に、Windows 配布サイズ・起動経路・ライフサイクルを一つの構成へ落とし込みます。外部一次情報が必要な論点だけは公式資料と既知の Tauri 制約に限定して補強します。
## 推奨アーキテクチャ

### 1. サイドカーのパッケージ方式

**推奨: python-build-standalone ベースの実行環境を初回起動時にダウンロードする。PyInstaller onedir はインストーラに含めない。**

配布を次の3層に分けます。

| 配布物 | 配置 | 内容 |
|---|---|---|
| Tauriインストーラ | NSIS | Tauri本体、初期セットアップUI、Pythonアプリコードとtemplates、ffmpeg、ライセンス |
| CPUバックエンド環境 | 初回ダウンロード | Python 3.12 standalone、固定済みsite-packages、torch/torchaudio/pyannote/transformers等 |
| モデル | 必要時ダウンロード | Whisper、pyannote、ライブASRモデル |

インストーラは200MB前後を目標にします。1.2–1.8GBのPython環境とモデルは絶対に入れません。2GB制限だけでなく、差分更新がないTauri updaterで毎回巨大なインストーラを再取得する問題も避けられます。

通常のWindows venvをそのままzip化すると絶対パスを含むため危険です。python-build-standaloneのルート配下に`Lib/site-packages`を構築した、完全な再配置可能アプリケーションディレクトリとして生成してください。初回起動時にpipを実行する方式は、再現性、速度、プロキシ、AV誤検知の面から採用しません。

配置例:

```text
%LOCALAPPDATA%/Transcribe/
  data/
    input/
    output/
    done/
    tmp_audio/
    model_cache/
  runtimes/
    cpu-2026.07.1/
      python.exe
      Lib/site-packages/
  models/
  logs/
  current-runtime.json
```

Pythonコードとtemplatesは小さいためTauri resourceに置き、通常のTauri updaterで更新します。Rustから以下を設定してPythonを起動します。

```text
TRANSCRIBE_BASE_DIR=%LOCALAPPDATA%/Transcribe/data
HF_HOME=%LOCALAPPDATA%/Transcribe/data/model_cache
XDG_CACHE_HOME=%LOCALAPPDATA%/Transcribe/data/model_cache
PATH=<bundled ffmpeg dir>;<original PATH>
```

**代替案:** PyInstaller onedirを「別ダウンロード成果物」として使う構成は次善策です。しかし、MLライブラリのhidden import、torch DLL、sileroデータ、Whisper assets、transformers/pyannoteの動的ロード対応が継続的な保守負担になります。

**不採用:** PyInstaller onefile、システムPython依存、初回pip install、巨大なonedirの`bundle.resources`同梱。

---

### 2. WebViewのロード先

**推奨: 初期セットアップ中だけTauri assetを表示し、バックエンド起動後は`http://127.0.0.1:<port>/`へ遷移する。**

これにより次が自然に解決します。

- HTML、SSE、WebSocketが完全なsame-originになる
- 現在の`Origin == Host`検査を変更せず維持できる
- 相対URLをそのまま利用できる
- Tauri asset側のCSPにローカルWebSocket例外を大量追加しなくてよい
- `127.0.0.1`はWebView2/Chromiumでpotentially trustworthy originとして扱われ、`getUserMedia`とAudioWorkletを利用できる

ただし、**Google Fontsのオフライン問題は解決しません**。Tauri CSPの問題は消えますが、CDN依存は残るため、フォントを同梱するかシステムフォントへ置換します。

アプリページにTauri IPCは不要です。現在必要な機能はHTTP、SSE、WebSocketで完結しています。Rustが担当するのはプロセス、更新、ダウンロード、ウィンドウ、マイク権限だけです。

設定方針:

- `dangerousRemoteDomainIpcAccess`は設定しない
- remote originへTauri command権限を与えない
- 初期Tauri assetだけに最小限のIPC capabilityを付与
- WebView内の遷移は実行時に決定した正確な`127.0.0.1:<port>`のみ許可
- 外部リンクは既定ブラウザで開き、WebView内の外部遷移を拒否

将来「フォルダを開く」などが必要になっても、危険なremote IPCを広く許可せず、限定されたローカルAPIまたは別のTauri-owned windowを利用します。

---

### 3. ポートとライフサイクル

**推奨: Pythonが`127.0.0.1:0`をbindし、stdoutで実ポートを通知する。Rustのfree-port probeは使わない。**

Rustが空きポートを調べてからPythonがbindする方式にはTOCTOU競合があります。Python側でsocketを先にbindし、そのsocketを`uvicorn.Server.serve(sockets=[socket])`へ渡します。

stdoutプロトコル例:

```text
TAURI_READY {"port":49182,"protocol":1,"backend_version":"2026.07.1"}
```

Rustはstdout/stderrを常時drainし、ログファイルをローテーションします。`TAURI_READY`取得後も、`GET /healthz`が成功してからWebViewを遷移させます。

`/healthz`はHTTP readinessとモデル状態を分けます。

```json
{
  "status": "ok",
  "protocol": 1,
  "backend_version": "2026.07.1",
  "worker": "loading"
}
```

モデルロード中でもUIは表示し、`worker`を`loading`、`ready`、`failed`として提示します。

終了処理は以下の順序です。

1. Rustが認証済み`POST /internal/shutdown`を呼ぶ
2. 録音中ならライブセッションをfinalizeする
3. uvicorn停止を待つ
4. タイムアウト時にプロセスをkillする
5. Windows Job Objectを閉じ、ffmpegを含む子孫プロセスを確実に終了する

shutdown API用のランダムsecretを起動時envで渡し、Rust以外からの終了要求を拒否します。

Job Objectには`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`を設定します。可能ならPythonを`CREATE_SUSPENDED`で生成し、Jobへ割り当ててからresumeすると、割り当て前に孫プロセスが生成される競合も除去できます。

ライブWAVは`.wav.part`として書き、正常終了時だけatomic renameします。起動時には残存ファイルのRIFF長を修復し、`input/recovered_*`へ移す回復処理を入れます。ハードkill時の完全保証はできませんが、現状の放置より大幅に安全です。

---

### 4. マイクキャプチャ

**推奨: WebView2の`getUserMedia` + AudioWorkletを維持する。ネイティブ録音へ移植しない。**

既存のPCM変換、WSプロトコル、再接続、UI状態管理をそのまま利用でき、Rust/Python間に新しい音声IPCを作る必要がありません。

WebView2の`PermissionRequested`をRustから処理し、以下を満たす場合だけMicrophoneを許可します。

- originが現在の正確な`http://127.0.0.1:<port>`
- permission kindがMicrophone
- WebViewがアプリのメインウィンドウ

他originとcamera等は拒否します。実機WebView2で初回許可、再起動後、デバイス切替、マイクなし環境を必ず検証します。

cpalはデバイス管理、リサンプリング、Rust→Python転送を新規実装するため、現時点では利益がありません。sounddeviceもPortAudio配布とデバイス競合を増やします。

**システム音声の`getDisplayMedia`はv1から外すことを推奨します。** UIから選択肢を隠し、「マイクのみ対応」と明示します。システム音声は将来、WASAPI loopbackをcpalまたは専用Rustモジュールで実装する独立機能とします。

---

### 5. 重い依存とモデルのダウンロードUX

**推奨: Rustがバックエンド環境を取得し、Pythonがモデルを取得する二段構成。**

Rust downloaderの責務:

- CPUバックエンドarchiveの取得
- HTTP Rangeによるresume
- SHA-256と署名検証
- 一時ディレクトリへの展開
- version directoryへのatomic rename
- 旧versionへのrollback
- ダウンロード済みbytes、総bytes、速度、残り時間の表示

Python downloaderの責務:

- Hugging Faceモデル取得
- HF cache管理
- gated repositoryのエラー判別
- モデル単位の進捗をSSEで通知
- ダウンロード後のロード検証

最小v1のセットアップ画面は以下で十分です。

1. CPUバックエンド環境をダウンロード
2. ライブまたはバッチ機能ごとに必要なモデル容量を表示
3. pyannote利用時だけHF_TOKENを要求
4. pyannoteの2つのgate承認ページへのリンクを表示
5. tokenをWindows Credential Managerへ保存し、Python起動時にenv注入
6. ディスク空き容量を事前確認
7. 中断後のresumeと再試行を提供

**Moonshine tiny-jaはv1から除外します。** 「ライセンスに同意」チェックボックスだけでは再配布制限を解決できません。商用利用・再配布許諾が得られるまで配布も自動ダウンロード導線も提供せず、ライブASRにはMITのWhisper weightsを使う`pywhispercpp`を採用します。

---

### 6. ffmpeg

**推奨: BtbNの固定versionのwin64 LGPL static buildをTauri resourceとして同梱する。**

static LGPL版なら通常は`ffmpeg.exe`単体で済み、DLL探索問題を避けられます。CIで以下を検証します。

```text
ffmpeg -version
ffmpeg -buildconf
```

GPL/nonfreeが有効になっていないことを確認してください。

同梱物:

- `ffmpeg.exe`
- LGPLライセンス
- FFmpeg copyright notice
- 使用したBtbN buildの正確なURL、commit/version
- 対応するsource入手先

RustがPython起動時にffmpegディレクトリをPATH先頭へ追加します。

コンソールウィンドウのflashはPATHでは防げません。Pythonのffmpeg起動にWindowsの`CREATE_NO_WINDOW`を付ける必要があります。自前の`src/audio.py`だけでなく、openai-whisper内部のffmpeg呼び出しにも適用するため、共通ランナーへの置換または小さなvendor patchが必要です。

---

### 7. リポジトリとWindows CI

推奨構造:

```text
transcribe/
  main.py
  requirements.txt
  requirements-windows.lock
  src/
  tests/
  packaging/
    backend/
      build.ps1
      manifest.json
    ffmpeg/
      LICENSE.txt
      SOURCE.txt
  src-tauri/
    Cargo.toml
    tauri.conf.json
    capabilities/
      default.json
    src/
      main.rs
      downloader.rs
      process.rs
      webview.rs
    bootstrap/
      index.html
```

`requirements-windows.lock`には全transitive dependencyとhashを固定します。現在の部分的にunpinnedな`requirements.txt`だけでは再現可能な製品ビルドになりません。

GitHub Actionsは`windows-2022`で次の順序にします。

1. Pythonテストと依存lock検証
2. python-build-standalone取得
3. 固定依存をruntimeへインストール
4. 別パスへ移動してrelocation smoke test
5. archive生成、SHA-256、署名、release asset upload
6. 固定BtbN ffmpeg取得とbuildconf検証
7. `cargo tauri build --bundles nsis`
8. Authenticode署名
9. Tauri updater artifactと`.sig`生成
10. インストール、初回download、backend起動、`/healthz`、終了後の孤児プロセス検査

Tauri updater署名とAuthenticode署名は別物です。両方必要です。秘密鍵と証明書はGitHub Environment secretsまたは外部signing serviceで管理します。

質問にあるPyInstaller工程を残す場合は、`pyinstaller --onedir`成果物を初回ダウンロード用archiveとして生成します。**推奨構成ではPyInstaller工程自体をpython-build-standalone環境生成へ置き換え、PyInstaller成果物をTauri bundleには入れません。**

## フェーズ計画

**v1**

- Windows x64、CPUのみ
- 小型NSIS installer
- 初回バックエンド環境download、resume、署名検証
- 動的port、`/healthz`、stdout handshake
- backend originへ直接遷移
- mic + AudioWorkletのみ
- pywhispercpp/WhisperベースのライブASR
- HF_TOKEN設定とpyannote gated download
- LGPL ffmpeg同梱
- Job Object、graceful shutdown、WAV回復
- Tauri shell updaterと独立したbackend manifest updater

**延期**

- Moonshine tiny-ja
- システム音声/WASAPI loopback
- CUDA環境
- Windows ARM64 native
- backend差分更新
- 複数バックエンドprofile
- remote pageへのTauri IPC
- 自動モデル選択と高度なキャッシュ削除UI

## Top 10リスク

1. **Moonshine jaのライセンス違反:** v1から除外し、明示的な商用許諾取得まで提供しない。
2. **依存環境の再現性不足:** Windows用lockとhashを導入し、ビルド時pip解決を禁止する。
3. **1GB超の初回download失敗:** resume、checksum、atomic install、rollback、空き容量検査を実装する。
4. **Python環境の再配置失敗:** 通常venvを避け、異なるパスへのrelocation CI testを必須にする。
5. **torch/pyannote等のAV・SmartScreen誤検知:** Authenticode、署名済みmanifest、安定した配布domainを使用する。
6. **録音中の強制終了によるWAV破損:** graceful finalize、`.part`、起動時修復、Job Objectを組み合わせる。
7. **HF gate/tokenによるセットアップ失敗:** gate承認リンク、403の専用説明、Credential Manager保存を提供する。
8. **WebView2マイク権限のversion差:** exact-origin限定のPermissionRequested処理と実機smoke testを行う。
9. **ローカルHTTPサーバーの攻撃面:** loopback限定bind、same-origin WS、shutdown secret、外部navigation拒否を徹底する。
10. **shell/backendのversion不整合:** protocol versionとminimum runtime versionをmanifestに持たせ、atomic switchとrollbackを実装する。
