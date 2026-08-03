# OpenCode Research: Packaging the Python ML webapp as a Tauri v2 Windows desktop app

- Date: 2026-07-26
- Model: github-copilot/gpt-5.6-sol (fallback; openai/gpt-5.6-sol-pro hung with no output and was killed after ~25 min)
- Method: single monolithic prompt also stalled on the fallback model; the question was split into 3 sequential opencode runs and concatenated below.
- Scope: (1) sidecar packaging, (2) torch bundle size — (3) mic capture, (4) port/sidecar lifecycle — (5) ffmpeg, (6) model distribution, (7) project structure + CI.

---

# Part 1: Python sidecar packaging & torch bundle size

## 1. Python Sidecar Packaging

### Recommended: PyInstaller `onedir`

For this stack, `onedir` is usually the least painful deployment model.

- Use a recent PyInstaller 6.x release with explicit Python 3.12 support.
- Build on Windows x64, ideally on the oldest Windows version you support.
- Treat the generated directory as one Tauri resource.
- Launch the packaged `.exe` as a Tauri v2 sidecar.
- Sign the Python executable, native DLLs where practical, and the final installer.

Advantages over `onefile`:

- Native DLLs remain beside the executable.
- Faster startup because nothing is extracted to `%TEMP%`.
- Easier diagnosis of missing `torch`, ONNX Runtime, CTranslate2, OpenMP, or MSVC DLLs.
- Fewer antivirus heuristics than a self-extracting executable.
- Tauri updates can replace a deterministic directory rather than an ephemeral extraction payload.

Typical problems:

- PyInstaller analysis does not discover imports loaded through plugin registries or `importlib`.
- `torch`, `torchaudio`, Lightning, `pyannote`, `onnxruntime`, and `ctranslate2` may need `collect_submodules`, `collect_data_files`, or `collect_dynamic_libs`.
- Model YAML, package metadata, entry points, and configuration files may be omitted even when imports succeed.
- Import success during startup does not prove inference works. Test actual Torch, pyannote, CTranslate2, and ONNX inference in the frozen build.

Common collection pattern:

```python
from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

hiddenimports = (
    collect_submodules("pyannote")
    + collect_submodules("lightning")
)

datas = (
    collect_data_files("pyannote.audio")
    + collect_data_files("speechbrain")
)

binaries = (
    collect_dynamic_libs("torch")
    + collect_dynamic_libs("torchaudio")
    + collect_dynamic_libs("onnxruntime")
    + collect_dynamic_libs("ctranslate2")
)
```

Do not blindly collect every submodule in production. It can pull in optional training, notebook, CUDA, testing, and visualization packages. Start broad to establish a working build, then reduce it while running end-to-end inference tests.

### Torch/Pyannote Failure Modes

Frequent Windows errors include:

- `OSError: [WinError 126] The specified module could not be found`
- `DLL load failed while importing ...`
- Failure loading `torch_cpu.dll`, `c10.dll`, `fbgemm.dll`, `libiomp5md.dll`, ONNX Runtime providers, or CTranslate2 DLLs
- `WinError 1114: A dynamic link library initialization routine failed`
- Successful `import torch`, followed by a crash on the first model operation

Likely causes:

- A dependent DLL is missing, not necessarily the DLL named in the error.
- The Microsoft Visual C++ 2015-2022 x64 runtime is absent.
- PyInstaller omitted DLLs from `torch/lib` or another package directory.
- Incompatible `torch` and `torchaudio` versions.
- Multiple OpenMP runtimes are loaded by Torch, NumPy/SciPy, ONNX Runtime, or CTranslate2.
- A CUDA-enabled Torch package was installed on a machine without a compatible runtime.
- DLL search behavior changes after PyInstaller modifies the process environment.
- Antivirus quarantines an extracted or unsigned native library.

Use Dependencies.exe or Process Monitor to identify the actual missing transitive DLL. Do not assume copying the DLL named in the Python exception will solve it.

For `pyannote.audio 3.1`, pin the complete tested dependency set, especially:

```text
torch
torchaudio
pyannote.audio
pyannote.core
pyannote.database
pyannote.metrics
lightning
speechbrain
numpy
scipy
soundfile
```

The exact Python 3.12-compatible combination should be validated before freezing. I am not fully certain that every `pyannote.audio 3.1.x` transitive dependency currently supports Python 3.12 equally well; some older SpeechBrain, Lightning, or audio-package versions may require newer patch releases.

Also test `soundfile` and its bundled `libsndfile` DLL. Your ffmpeg subprocess should be shipped separately and invoked through an absolute resource path rather than relying on `PATH`.

### Avoid PyInstaller `onefile`

`onefile` is a poor fit for this workload:

- It embeds hundreds of megabytes or gigabytes into one executable.
- Every launch extracts the payload.
- Startup can take many seconds.
- Extraction requires substantial temporary disk space.
- Locked-down environments may block execution from `%TEMP%`.
- Interrupted extraction and antivirus quarantine can produce intermittent DLL failures.
- Self-extracting executables receive more antivirus scrutiny and false positives.
- Diagnosing native-library failures is harder.

It does not meaningfully reduce installer download size compared with compressing an `onedir` build in the Tauri installer.

### `python-build-standalone` Plus Environment

This can be more predictable than PyInstaller when dynamic imports make freezing unreliable, but do not ship an ordinary venv and assume it is relocatable.

Python venvs contain path-sensitive configuration and scripts. A safer layout is:

```text
resources/python/python.exe
resources/python/python312.dll
resources/python/Lib/...
resources/python/Lib/site-packages/...
resources/backend/...
resources/bin/ffmpeg.exe
```

Use a small launcher that sets controlled values for:

- `PYTHONHOME`
- `PYTHONPATH`
- DLL search directories
- model/cache directories
- writable application-data directories

Benefits:

- Normal Python import semantics.
- Easier troubleshooting than a frozen import graph.
- Fewer PyInstaller hook surprises.
- Package metadata and dynamic plugin discovery generally behave normally.

Costs:

- More files.
- Usually larger than a carefully trimmed PyInstaller build.
- The environment must be assembled and tested as relocatable.
- Native wheels still require correct MSVC and DLL handling.
- Some packages may assume a conventional CPython installation.

I would choose:

1. PyInstaller `onedir` if repeatable inference tests pass.
2. A relocatable standalone CPython payload if PyInstaller requires fragile hook maintenance.
3. PyInstaller `onefile` only for small tools without Torch.

## 2. Keeping Torch Bundle Size Sane

### Pin CPU-Only Torch Explicitly

Install Torch and torchaudio from the CPU wheel index rather than relying on the default resolver:

```powershell
python -m pip install `
  --index-url https://download.pytorch.org/whl/cpu `
  torch==<tested-version> `
  torchaudio==<matching-version>
```

Then install the remaining packages from PyPI without allowing Torch to be replaced:

```powershell
python -m pip install --no-deps pyannote.audio==3.1.1
python -m pip install -r locked-transitive-requirements.txt
```

The exact Torch version should come from a tested lockfile. Do not independently select mismatched Torch and torchaudio releases.

Check the resulting environment:

```python
import torch

print(torch.__version__)
print(torch.version.cuda)       # Should be None for a CPU build
print(torch.cuda.is_available()) # Should be False
```

On Windows, CUDA-enabled Torch wheels can include a large set of CUDA-related DLLs. On other platforms, dependency resolution may also pull separate `nvidia-*` packages. Accidentally packaging these can add roughly 1-3 GB installed, depending on the Torch/CUDA release. The precise amount varies considerably by version.

Do not delete apparently CUDA-named files from a frozen build without inference testing. Some library layouts and manifests are version-dependent. Preventing CUDA packages from entering the build is safer than post-build deletion.

### Realistic Sizes

Approximate Windows x64 figures, varying by release and compression:

| Payload | Approximate installed size |
|---|---:|
| Standalone CPython 3.12 runtime | 40-100 MB |
| NumPy/SciPy and native libraries | 150-350 MB |
| CPU Torch | 300-700 MB |
| Torchaudio and audio dependencies | 20-150 MB |
| Pyannote, Lightning, sklearn, supporting stack | 300-900 MB |
| ONNX Runtime + CTranslate2 | 50-250 MB |
| ffmpeg | 70-200 MB |
| Backend before model weights | roughly 1-2.5 GB |

A compressed installer might be approximately 600 MB-1.5 GB. Model weights are additional:

- Whisper/CTranslate2 models range from under 100 MB to several GB.
- Pyannote pipeline components can add hundreds of MB.
- Moonshine ONNX assets depend on the selected model and quantization.

These are planning ranges, not guaranteed measurements. Torch packaging sizes change substantially between releases.

Useful controls:

- Exclude training-only packages if runtime inference does not import them.
- Exclude tests, notebooks, documentation, caches, and duplicate model files.
- Keep model weights outside the executable payload.
- Avoid bundling multiple Torch or ONNX backends for the same operation.
- Use quantized CTranslate2 and ONNX models where quality permits.
- Generate and inspect a build-size report instead of relying only on installer size.
- Verify that exclusion rules do not break lazy imports during actual inference.

### First-Launch Download

Downloading model weights on first launch is strongly recommended. Downloading the entire Python runtime and dependency environment is a product decision.

A sensible compromise:

- Bundle Tauri, the Python runtime, application code, ffmpeg, and minimum inference dependencies.
- Download selectable model weights on demand.
- Store models under `%LOCALAPPDATA%\<Vendor>\<App>\models`.
- Verify cryptographic hashes.
- Download to a staging file and atomically rename it.
- Support resume, cancellation, disk-space checks, proxies, and offline import.

For the smallest bootstrap installer, download a prebuilt, versioned Python payload archive at first launch. Do not run `pip install` against public indexes on the customer machine. Runtime installation is slow, non-atomic, proxy-sensitive, and can resolve different dependencies later.

A downloadable payload should be:

- Built and tested in CI
- Immutable and versioned
- Code-signed where applicable
- Hash-verified
- Extracted atomically into an application-managed directory
- Retained alongside the previous version for rollback
- Covered by the same update compatibility rules as the Tauri frontend

Tradeoffs:

- Bundling gives reliable offline installation and fewer first-run failures.
- Downloading gives a small installer and lets CPU/model variants be selected.
- First-run download adds CDN, integrity, proxy, rollback, and support complexity.
- Corporate users often prefer a large offline installer over a bootstrapper.

For a consumer application, I would bundle the runtime and dependencies but download models. For a centrally managed or frequently updated application, a small signed bootstrapper plus a signed, immutable backend payload can work well.

---

# Part 2: Microphone capture & port/sidecar lifecycle

## 1. マイク入力

### Tauri v2 + WebView2 でも基本的には動く

`getUserMedia()`、`AudioWorklet`、WebSocket による PCM 送信は WebView2 が対応しています。ただし、通常のブラウザより次の条件に左右されます。

- `getUserMedia()` と `AudioWorklet` は secure context が必要
- 開発時の `http://localhost` は secure context 扱い
- 本番の Tauri カスタムプロトコルは構成やバージョンによって扱いが異なる
- WebView2 のマイク許可、Windows のプライバシー設定、企業ポリシーのすべてを通過する必要がある
- AudioWorklet のモジュール URL が CSP とカスタムプロトコルで許可される必要がある

起動直後に最低限これを検査してください。

```js
console.log({
  secure: window.isSecureContext,
  mediaDevices: Boolean(navigator.mediaDevices),
  audioWorklet: Boolean(AudioWorkletNode),
});
```

### Secure context

Tauri v2 の Windows 設定では、可能ならウィンドウ設定の `useHttpsScheme` を有効にします。

```json
{
  "app": {
    "windows": [
      {
        "label": "main",
        "useHttpsScheme": true
      }
    ]
  }
}
```

ただし、このキーの正確な配置と利用可能条件は Tauri v2 のマイナーバージョンで確認が必要です。ここは私がバージョン横断で断言できない部分です。

以下の WebView2 引数は診断や自動テスト向けであり、製品での恒久的な回避策にはしない方がよいです。

```text
--unsafely-treat-insecure-origin-as-secure=http://tauri.localhost
--use-fake-ui-for-media-stream
--use-fake-device-for-media-stream
```

指定方法は Tauri の `additionalBrowserArgs`、または WebView2 の `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS` がありますが、Tauri v2 の正確な設定キーはリリースによって変化しているため要確認です。

### Permission prompt

WebView2 はマイク権限要求をサポートしますが、ホスト側が `PermissionRequested` をどう処理するかで挙動が変わります。

確認すべき失敗ケース:

- Windowsの「マイクへのアクセス」が無効
- 「デスクトップ アプリにマイクへのアクセスを許可する」が無効
- 企業ポリシーで WebView2 の音声入力が禁止
- WebView2 プロファイル変更や WebView データ削除で許可が消える
- Tauri/WebView2 ランタイムの組み合わせでプロンプトが表示されず `NotAllowedError`
- 開発ビルドでは動くが、インストール済み本番ビルドでは secure context 判定が異なる

Tauri の capability は `getUserMedia()` 権限を直接制御しません。これは WebView2/Windows の権限です。

### 選択肢の比較

| 方式 | 長所 | 短所 |
|---|---|---|
| WebView `getUserMedia` | 現行実装を再利用、波形表示が容易、Rust/Python変更が少ない | secure contextとWebView権限に依存、デバイス制御が限定的、WebView更新の影響 |
| Rust `cpal` | WebViewから独立、WASAPIを直接利用、デスクトップアプリとして最も制御しやすい | サンプル形式変換、リサンプリング、デバイス切替、IPCの実装が必要 |
| Python `sounddevice` | PCMを直接推論パイプラインへ渡せる、実装が比較的短い | PortAudio DLLの同梱、デバイス名/番号の不安定さ、callbackとGIL、Pythonプロセス停止時の扱い |

### 推奨

製品としてマイク録音が中核機能なら、最終的には **Rustの `cpal` で共有モードWASAPI入力を取得**するのが最も堅牢です。

推奨構成:

1. Rustでデバイス選択とキャプチャ
2. 入力フォーマットを `f32` または `i16` に正規化
3. 必要なら16 kHz monoへリサンプリング
4. ローカルWebSocket、named pipe、またはstdinでPythonへ送信
5. WebViewにはレベルメーターや状態だけをイベント送信

短期移行では既存の `getUserMedia + AudioWorklet` を残しても妥当です。ただし、実機のインストーラー版で権限プロンプト、再起動後の権限保持、複数デバイス、Bluetooth、RDPを必ず試験してください。

Python側で捕捉するなら `sounddevice` より `cpal` を推します。Pythonはtorch/ONNX推論だけでも重いため、リアルタイム入力のライフサイクルまで同じプロセスへ集約しない方が障害分離しやすいためです。

## 2. uvicorn sidecarのポート管理

### ポートはsidecar自身に選ばせる

Rust側で「空きポートを探してソケットを閉じ、その番号をPythonへ渡す」方式にはTOCTOU競合があります。

より堅牢なのはPythonが `127.0.0.1:0` にbindし、実際のポートをstdoutへ通知する方式です。

```python
import json
import socket
import uvicorn

sock = socket.socket()
sock.bind(("127.0.0.1", 0))
sock.listen()
port = sock.getsockname()[1]

print(json.dumps({
    "type": "ready",
    "port": port,
    "token": startup_token,
}), flush=True)

config = uvicorn.Config(app, log_config=None)
server = uvicorn.Server(config)
server.run(sockets=[sock])
```

実際には「ソケットbind完了」と「ASGI lifespan startup完了」は別です。より正確には、uvicornのstartup完了後にreadyを通知するか、Rust側が `/health` を成功するまで再試行します。

### 起動ハンドシェイク

推奨フロー:

1. Tauriがランダムな128-bit以上の起動トークンを生成
2. sidecarへ環境変数またはstdinで渡す
3. sidecarがloopbackのport 0にbind
4. stdoutへ1行JSONでポートを通知
5. Tauriが `GET /health` を指数バックオフ付きで確認
6. 応答のinstance ID/tokenを照合
7. 成功後にWebViewへURLと短命トークンを渡す

stdoutはログと混ぜない方が安全です。ready通知を固定プレフィックス付き1行JSONにするか、専用named pipeを使います。

失敗条件も明示してください。

- 10～30秒でreadyにならない
- sidecarがready前に終了
- JSONが壊れている
- `/health` が別プロセスから応答
- モデル初期化中にhealthが成功してしまう
- Windows Defenderによって初回起動が大幅に遅れる

WebSocket/SSE/HTTPをWebViewから直接呼ぶ場合、CSPにloopbackを許可します。

```json
{
  "app": {
    "security": {
      "csp": "default-src 'self'; connect-src 'self' http://127.0.0.1:* ws://127.0.0.1:*"
    }
  }
}
```

SSEがHTTPなら `http://127.0.0.1:*`、WebSocketなら `ws://127.0.0.1:*` が必要です。通常のbrowser fetchにはTauri capabilityは不要です。

### Sidecar設定

sidecarバイナリは概ね次のように登録します。

```json
{
  "bundle": {
    "externalBin": [
      "binaries/transcribe-sidecar"
    ]
  }
}
```

Tauri shell pluginを使う場合はcapabilityでspawnを限定します。概念的には次の形です。

```json
{
  "permissions": [
    {
      "identifier": "shell:allow-spawn",
      "allow": [
        {
          "name": "binaries/transcribe-sidecar",
          "sidecar": true,
          "args": true
        }
      ]
    }
  ]
}
```

`allow` の正確なスキーマ、sidecar名がbasenameか設定名かは `tauri-plugin-shell` のバージョン依存なので確認が必要です。引数を完全自由にするより、許可する引数を限定してください。

### Windowsで確実に終了させる

`Child.kill()` だけでは不十分です。直接のPythonプロセスは停止しても、次が孤児化する可能性があります。

- `ffmpeg.exe`
- multiprocessingの子プロセス
- PyInstaller bootloaderが起動した実プロセス
- torch/ONNX関連で別途起動したプロセス

最も堅牢なのは **Windows Job Object** です。

1. `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` のJob Objectを作成
2. sidecarを可能なら`CREATE_SUSPENDED`で起動
3. Job Objectへ割り当て
4. プロセスをresume
5. Job Object handleをTauriのmanaged stateで保持
6. 終了時にgraceful shutdownを要求
7. タイムアウト後に`TerminateJobObject`
8. 最後にJob Object handleを閉じる

`CREATE_SUSPENDED`が重要なのは、Job Objectへ割り当てる前にsidecarが `ffmpeg` を起動する競合を避けるためです。

`tauri-plugin-shell` の抽象化ではWindows process handleやcreation flagsへ十分アクセスできない可能性があります。その場合はRust側で `std::process::Command` と `windows` crateを使った専用launcherを実装する方が確実です。

### 終了シーケンス

- Tauriの終了要求で新規録音を停止
- sidecarの管理APIへshutdown要求
- uvicornの既存SSE/WebSocket接続を閉じる
- 2～5秒待つ
- 終了しなければJob Object全体をterminate
- app stateが破棄される際にもJob handle closeを保証

注意点:

- `ExitRequested` と実際のprocess exitは異なる
- トレイへ隠す動作ではsidecarを終了させない
- Rust panicやTauri強制終了でもJob handle closeにより子を終了させる
- Task Managerの「タスクの終了」など、親ごと強制終了されてもJob Objectなら通常は後始末される
- 子がbreakaway flagでJob Objectを脱出すると終了保証が崩れる
- uvicornの`--reload`や複数workerは子プロセスを増やすのでsidecarでは使用しない

結論として、**port 0 + stdout ready通知 + health確認 +起動トークン + Windows Job Object** の組み合わせを推奨します。単純な空きポート探索と `Child.kill()` だけの構成は、配布版Windowsアプリでは競合と孤児プロセスを残しやすいです。

---

# Part 3: ffmpeg bundling, model distribution, project structure & CI

## 1. Windows向けffmpegの同梱

### 推奨ビルド

FFmpeg公式はWindowsバイナリを配布していません。実務上は次のどちらかです。

- **BtbN FFmpeg Builds** の `win64-lgpl-shared` 系
- 自前ビルドで `--disable-gpl --disable-nonfree` を指定したLGPL構成

LGPL遵守を明確にするなら、BtbNのGitHub Releasesからバージョン固定した`lgpl-shared`ビルドを取得し、SHA-256をCIで検証するのが扱いやすいです。

`shared`ビルドでは`ffmpeg.exe`だけでなく、同梱される`avcodec-*.dll`、`avformat-*.dll`なども必要です。`ffmpeg.exe`だけコピーすると、Windowsで次のように失敗します。

```text
The code execution cannot proceed because avcodec-XX.dll was not found
```

### 避けるべき構成

- `full`、`gpl`、`nonfree`と書かれたビルドを、LGPL前提で無確認のまま同梱する
- LGPL staticビルドを、再リンク手段を提供せずに単一実行ファイルとして扱う
- `ffmpeg.exe`をPATH依存で呼び出す
- ダウンロードURLを`latest`にして再現不能なビルドにする

### ライセンス対応

最低限、インストーラーに以下を含めます。

```text
licenses/
  FFmpeg-LICENSE.txt
  FFmpeg-COPYING.LGPLv2.1.txt
  FFmpeg-COPYING.LGPLv3.txt
  THIRD-PARTY-NOTICES.txt
```

`THIRD-PARTY-NOTICES.txt`には次を記載します。

- FFmpegのバージョン
- 使用した配布元と正確なURL
- ビルド設定
- 対応するソースコードの取得先
- FFmpegがLGPLであること
- ユーザーがDLLを置換できる配置であること

LGPL 2.1か3のどちらが適用されるかは、使用ビルドの`LICENSE`と`configure`出力で確定してください。ここは配布元ごとに確認が必要です。

### Tauriへの配置

Python sidecarは`externalBin`、FFmpeg一式は`resources`にするのが無難です。

```text
src-tauri/
  binaries/
    transcribe-sidecar-x86_64-pc-windows-msvc.exe
  resources/
    ffmpeg/
      bin/
        ffmpeg.exe
        ffprobe.exe
        avcodec-*.dll
        avformat-*.dll
        avutil-*.dll
        ...
    licenses/
      ...
```

`src-tauri/tauri.conf.json`の例です。

```json
{
  "$schema": "https://schema.tauri.app/config/2",
  "productName": "Transcribe",
  "version": "1.0.0",
  "identifier": "com.example.transcribe",
  "build": {
    "frontendDist": "../desktop-ui/dist"
  },
  "bundle": {
    "active": true,
    "targets": ["nsis"],
    "externalBin": [
      "binaries/transcribe-sidecar"
    ],
    "resources": [
      "resources/ffmpeg",
      "resources/licenses"
    ]
  }
}
```

`externalBin`では設定値にターゲットトリプルを含めず、実ファイルに以下の名前を付けます。

```text
transcribe-sidecar-x86_64-pc-windows-msvc.exe
```

これはTauri v2 bundlerのsidecar命名規則です。

FFmpegはRust側でresource directoryを解決し、その絶対パスをsidecarへ渡します。

```rust
let ffmpeg = app
    .path()
    .resolve(
        "resources/ffmpeg/bin/ffmpeg.exe",
        tauri::path::BaseDirectory::Resource,
    )?;
```

実際のresourceパスに`resources/`が残るかは、Tauriのコピー規則と指定方法によって変わり得ます。パッケージ後のNSIS/MSIを必ず検査してください。

sidecar起動時に環境変数で渡す方法が単純です。

```text
TRANSCRIBE_FFMPEG_PATH=C:\...\ffmpeg\bin\ffmpeg.exe
TRANSCRIBE_FFPROBE_PATH=C:\...\ffmpeg\bin\ffprobe.exe
```

PythonではPATH検索にフォールバックさせず、明示パスを使用します。

```python
ffmpeg_path = os.environ["TRANSCRIBE_FFMPEG_PATH"]

subprocess.run(
    [ffmpeg_path, "-nostdin", "-hide_banner", ...],
    check=True,
    creationflags=subprocess.CREATE_NO_WINDOW,
)
```

`CREATE_NO_WINDOW`を付けないと、処理ごとにコンソールが点滅する場合があります。

## 2. モデル配布とキャッシュ

### Whisper

OpenAI Whisperの公式コードと公式weightsは一般にMITとして配布されています。したがって、正確なチェックポイントのライセンスを確認し、MIT noticeを同梱すれば、インストーラーへの収録は通常可能です。

ただし、`faster-whisper`でよく使うHugging Face上の変換済みモデルは、OpenAI公式ファイルそのものではありません。次をモデル単位で確認してください。

- Hugging Face repositoryの`license`
- README記載の追加条件
- 変換元モデル
- tokenizerなど付属ファイルのライセンス

数GBのモデルをインストーラーに含めると、GitHub Actions artifact、NSIS、コード署名、更新配布が重くなるため、法的に可能でも初回ダウンロードを推奨します。

### Moonshine

Moonshineのコードや公開checkpointはMITとして示されているものがありますが、**利用する具体的なONNX checkpointごとに確認が必要**です。私は全Moonshine派生モデルの再配布条件が一律MITであるとは保証できません。

再配布する場合は以下を固定してください。

- モデルrepositoryとrevision/commit SHA
- モデルカードのlicense
- ONNXファイルのSHA-256
- tokenizer、vocabulary、configのライセンス

小さいMoonshineモデルはinstaller同梱のメリットが比較的大きいですが、更新頻度が高いなら初回ダウンロードの方が安全です。

### pyannote.audio 3.1

`pyannote.audio`ライブラリ自体と、Hugging Face上のモデル利用条件は別です。

代表的な3.1構成では以下がgated modelです。

```text
pyannote/speaker-diarization-3.1
pyannote/segmentation-3.0
```

通常、ユーザーはそれぞれのHugging Faceページで条件に同意し、アクセストークンを使って取得します。

そのため、これらをインストーラーへ再配布する設計は避けるべきです。

- 開発者のHF tokenでCIダウンロードして同梱しない
- tokenをアプリや設定ファイルに埋め込まない
- 一度取得したcacheを他ユーザー向けinstallerへコピーしない
- pipelineだけでなく依存するgated modelにも同意が必要なことを案内する

`pyannote.audio 3.1`では概ね次の形です。

```python
Pipeline.from_pretrained(
    "pyannote/speaker-diarization-3.1",
    use_auth_token=user_token,
)
```

新しい`huggingface_hub`や別バージョンでは`token=`へ変更されている場合があります。実際に固定する`pyannote.audio`、`transformers`、`huggingface_hub`の組み合わせで確認してください。

### Windowsキャッシュ配置

推奨例です。

```text
%LOCALAPPDATA%\YourCompany\Transcribe\
  models\
    huggingface\
      hub\
      assets\
    torch\
    moonshine\
    whisper\
  logs\
  tmp\
  config\
```

sidecarがMLライブラリをimportする前に設定します。

```text
HF_HOME=%LOCALAPPDATA%\YourCompany\Transcribe\models\huggingface
HF_HUB_CACHE=%LOCALAPPDATA%\YourCompany\Transcribe\models\huggingface\hub
TORCH_HOME=%LOCALAPPDATA%\YourCompany\Transcribe\models\torch
```

必要なら以下も設定します。

```text
XDG_CACHE_HOME=%LOCALAPPDATA%\YourCompany\Transcribe\models
```

`TRANSFORMERS_CACHE`は新しいTransformersでは非推奨方向なので、基本は`HF_HOME`と`HF_HUB_CACHE`を使います。

### 初回ダウンロードUX

初回起動時に全モデルを無条件取得しない方がよいです。

1. Whisper/Moonshineのみで開始可能にする
2. モデル名、サイズ、必要空き容量、ライセンスリンクを表示する
3. ダウンロード進捗と現在のファイル名を表示する
4. 中断後にresumeできるようHugging Face Hubのcache機構を使う
5. 完了後に必要ファイルとrevisionを検証する
6. diarization有効化時だけHF tokenを要求する
7. pyannoteの2つの同意ページを明示する
8. tokenはWindows Credential Managerへ保存し、ログには出さない
9. proxy、TLS inspection、401、403、disk fullを個別に案内する

典型的な失敗原因は以下です。

- `401`: tokenがない、無効、期限切れ
- `403`: gated modelの条件に未同意
- pipelineは取得できたが`segmentation-3.0`への同意がない
- cache途中破損
- `%LOCALAPPDATA%`の空き容量不足
- 企業proxyや証明書差し替え
- Windows Defenderによる巨大ファイルのスキャン遅延

## 3. 構成とWindows CI

### 推奨構成

既存Python構成を維持し、Tauriを薄いdesktop hostとして追加します。

```text
repo/
  pyproject.toml
  src/
    transcribe/
      ...
    web/
      app.py
      templates/
      static/
  desktop-ui/
    package.json
    src/
    dist/
  src-tauri/
    Cargo.toml
    build.rs
    tauri.conf.json
    capabilities/
      default.json
    src/
      main.rs
      lib.rs
    binaries/
    resources/
  packaging/
    pyinstaller/
      transcribe-sidecar.spec
    licenses/
  scripts/
    prepare-ffmpeg.ps1
    prepare-models.ps1
  tests/
```

起動フローは次を推奨します。

1. Tauriが空きlocalhost portを決定
2. Python sidecarを`127.0.0.1`限定で起動
3. portとランダムなsession secretを引数または環境変数で渡す
4. `/health`がreadyになるまでTauri側で待つ
5. Tauri WebViewをFastAPI URLへ遷移
6. 終了時にsidecarを明示終了

`0.0.0.0`へbindするとLANからアクセスされるため避けてください。固定portも競合しやすいため非推奨です。

### PyInstaller

torch、ONNX Runtime、pyannoteを含むため、まずは固定バージョンの`.spec`を管理します。

```powershell
pyinstaller `
  --noconfirm `
  packaging/pyinstaller/transcribe-sidecar.spec
```

`onefile`は配布しやすい一方、次の問題があります。

- 起動ごとに一時展開
- torchを含むと起動が遅い
- Defenderに誤検知されやすい
- 展開領域不足
- subprocess起動時の挙動が分かりにくい

実務では`onedir`が安定しますが、Tauriの`externalBin`は主exe中心なので、PyInstallerの`_internal`一式を`resources`へ含め、実行時の隣接配置を検証する必要があります。単純さを優先する初期版なら`onefile`も合理的です。

### GitHub Actions概要

```yaml
name: windows-desktop

on:
  push:
    tags:
      - "v*"

jobs:
  build:
    runs-on: windows-2022

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip

      - uses: actions/setup-node@v4
        with:
          node-version: "22"
          cache: npm
          cache-dependency-path: desktop-ui/package-lock.json

      - uses: dtolnay/rust-toolchain@stable
        with:
          targets: x86_64-pc-windows-msvc

      - name: Install Python dependencies
        run: |
          python -m pip install --upgrade pip
          pip install . pyinstaller

      - name: Build Python sidecar
        run: |
          pyinstaller --noconfirm packaging/pyinstaller/transcribe-sidecar.spec
          Copy-Item dist/transcribe-sidecar.exe src-tauri/binaries/transcribe-sidecar-x86_64-pc-windows-msvc.exe

      - name: Prepare FFmpeg
        shell: pwsh
        run: ./scripts/prepare-ffmpeg.ps1

      - name: Install frontend
        working-directory: desktop-ui
        run: npm ci

      - name: Build frontend
        working-directory: desktop-ui
        run: npm run build

      - uses: tauri-apps/tauri-action@v0
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        with:
          projectPath: src-tauri
          args: --target x86_64-pc-windows-msvc
```

実際にはtorch系wheelが大きいため、pip cacheだけでは十分でないことがあります。バージョンとCPU wheel indexを固定してください。またPython 3.12、`pyannote.audio 3.1`、固定torch版、PyInstallerの組み合わせは事前検証が必要です。古い依存関係ではPython 3.12非対応があり得ます。

### コード署名

Windowsでは以下を区別してください。

- **Authenticode署名**: EXE、DLL、NSIS/MSIに対するWindows署名
- **Tauri updater署名**: Tauri updater artifact検証用の秘密鍵

`TAURI_SIGNING_PRIVATE_KEY`は通常Tauri updater用であり、Authenticode証明書ではありません。

TauriのWindows bundle設定には、バージョンに応じて次のキーがあります。

```json
{
  "bundle": {
    "windows": {
      "certificateThumbprint": "...",
      "digestAlgorithm": "sha256",
      "timestampUrl": "http://timestamp.digicert.com"
    }
  }
}
```

ただし、Tauri v2の細かな署名キーと証明書ストア要件は使用するCLIバージョンで再確認してください。

CIでは次のいずれかを使います。

- GitHub Actions runnerへPFXを一時importして`signtool.exe`
- Azure Trusted Signing
- EV証明書対応のクラウド署名サービス

Python sidecarも署名してください。installerだけ署名して内部の巨大な未署名sidecarを残すと、DefenderやSmartScreenで警告・隔離されやすくなります。FFmpeg DLLは第三者バイナリなので、自社署名するかどうかは証明書ポリシーと配布元ライセンスを確認してください。
