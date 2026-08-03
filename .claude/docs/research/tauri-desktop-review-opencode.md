# OpenCode review — Tauri desktop (feature/tauri-desktop)

Date: 2026-07-27. Runner: Claude subagent (OpenCode review runner).

## Operational notes

- `openai/gpt-5.6-sol-pro` failed instantly on every attempt: **insufficient_quota**
  (confirmed in `~/.local/share/opencode/log/2026-07-27T131322.log`). All results
  below are from the fallback **`github-copilot/gpt-5.6-sol`**.
- The original monolithic Rust prompt stalled twice (0 bytes of output after 30+ min);
  it was split into two halves.
- Additional failure mode found: the copilot model repeatedly tried to read crate
  sources under `~/.cargo/registry` (permission auto-rejected by opencode) and its
  turn then ended with no findings. Adding "do NOT read outside the repo / do NOT run
  cargo" to the prompt fixed it. Recommend keeping that constraint in future runs.
- Python review: the first fallback attempt emitted progress but was cut off; the
  second attempt (same model) completed. Full output below.

---

## RUN 1 — Rust static review (model: github-copilot/gpt-5.6-sol, split into 2 parts)

### Part 1: process.rs / main.rs / webview.rs / Cargo.toml (raw output)

指定された4ファイルだけを読み、Windows向けAPIの型整合性、所有権・ハンドル寿命、Tauri/WebView2のAPI形状を静的に照合します。ビルドやリポジトリ外参照は行いません。
確認範囲は指定4ファイルのみです。外部ソース参照、Cargo系コマンド実行はしていません。

`CreateProcessW → Job設定 → AssignProcessToJobObject → ResumeThread` の順序自体は正しいです。Tauri側も、私の把握する2.x APIでは `on_navigation`、`navigate(Url)`、`RunEvent::ExitRequested`/`Exit` の形は概ね正しいです。ただし依存関係が固定されていないため、Tauri/Wryと直接依存する`webview2-com`の型一致には不確実性があります。

**Findings**
- **High / コンパイル失敗** `src-tauri/src/process.rs:281,283`: windows-rs 0.61の`CreatePipe`は第3引数が`Option<*const SECURITY_ATTRIBUTES>`です。`Some(&sa)`は`Option<&SECURITY_ATTRIBUTES>`から自動変換されません。`Some(&raw const sa)`などが必要です。
- **High / コンパイル失敗** `src-tauri/src/process.rs:285,287`: `SetHandleInformation`の`dwmask`は`HANDLE_FLAGS`ですが、`HANDLE_FLAG_INHERIT.0`は内部の整数です。`.0`を外して`HANDLE_FLAG_INHERIT`を渡す必要があります。
- **High / コンパイル失敗** `src-tauri/src/process.rs:327`: windows-rs 0.61の`CreateProcessW`の`bInheritHandles`は`BOOL`であり、Rustの`true`は直接渡せません。`true.into()`または`BOOL(1)`が必要です。
- **High / コンパイル失敗** `src-tauri/src/main.rs:406`: `GetDiskFreeSpaceExW`のnullable出力引数は`Option<*mut u64>`です。`Some(&mut free)`ではなく`Some(&raw mut free)`などが必要です。
- **High / コンパイル失敗** `src-tauri/src/webview.rs:150-154`: webview2-com 0.37のCOMプロパティgetterは戻り値形式で、概ね`let kind = args.PermissionKind()?;`、`let uri = take_pwstr(args.Uri()?);`です。現在のout-parameter形式は引数個数不一致になります。
- **High / ハンドルリーク** `src-tauri/src/process.rs:281-288`: stdoutパイプ作成後のstderr `CreatePipe`失敗、またはどちらかの`SetHandleInformation`失敗では、既に作成済みのパイプハンドルが`?`によってリークします。作成直後からRAII管理するか、各失敗時に全作成済みハンドルを閉じる必要があります。
- **High / ハンドルリーク** `src-tauri/src/process.rs:358-375`: Job作成後に`SetInformationJobObject`、`AssignProcessToJobObject`、`ResumeThread`が失敗すると、`kill_suspended`はプロセス、スレッド、パイプだけを閉じ、`job`を閉じません。
- **Medium / 継承範囲** `src-tauri/src/process.rs:327`: `bInheritHandles=TRUE`はパイプ書き込み端だけでなく、プロセス内の全inheritable handleを子へ継承します。現在のread端非継承化は正しいものの、厳密に2ハンドルだけ渡すには`STARTUPINFOEXW`と`PROC_THREAD_ATTRIBUTE_HANDLE_LIST`が必要です。
- **Medium / 依存解決依存のコンパイル失敗** `src-tauri/Cargo.toml:25,54-64`, `src-tauri/src/webview.rs:126-177`: `tauri = "2"`が解決するWryと、直接指定した`webview2-com = "0.37"`および`windows = "0.61"`が異なる版を使用すると、controller、handler、`EventRegistrationToken`、`PWSTR`が別crate型になり相互運用できません。0.37/0.61の組み合わせ自体は妥当に見えますが、ロックされていないTauri/Wry側との一致は保証できません。
- **Medium / 状態不整合** `src-tauri/src/main.rs:333-338`: backendを状態へ格納した後で`navigate_to_backend`が失敗すると、コマンドは`failed`を通知しますがbackendは稼働したままです。再試行可能な意図なら明示すべきで、失敗扱いなら格納前にnavigateするか停止処理が必要です。

### Part 2: capture.rs / downloader.rs (raw output)

指定された3ファイルだけを読み、Windows x64での初回ビルド可否と実行時の並行処理・データ整合性を静的に確認します。Cargo系コマンドやリポジトリ外の参照は行いません。
指定3ファイルのみを静的レビューしました。実ビルドおよび依存ソース確認は未実施です。

**Findings**
- **High** `src-tauri/src/capture.rs:28-30,295-323` — 48 kHz→16 kHzで線形補間だけではアンチエイリアス処理になりません。比率3:1では実質的に3サンプルごとの間引きとなり、8 kHz以上の成分が音声帯域へ折り返します。ASR用途でも品質劣化要因になるため、ローパス付きpolyphase/sinc resamplerが必要です。
- **High** `src-tauri/src/capture.rs:53-54,276-279,338-365` — 64フレームは約8.2秒分です。切断中に古い音声を保持し、満杯後は新しい音声を捨てるため、再接続後に大幅に遅延した音声を送ります。リアルタイム用途では小さいキューにするか、最古フレームを捨てる設計が必要です。
- **High** `src-tauri/src/capture.rs:339-357` — WS接続を送信専用として扱い、incoming streamを一度もpollしていません。したがってpingを読まず、自動pongも生成されず、server closeにも応答しません。`frames_rx.recv()`と`ws.next()`を`tokio::select!`する必要があります。冒頭の「tungsteniteがpingへ応答する」という説明は、読み取りをpollする場合に限ります。
- **Medium** `src-tauri/src/capture.rs:104-116,338` — 5秒のtimeoutで`JoinHandle`を消費していますが、timeout時にタスクはabortされずdetachされます。特にtimeoutのない`connect_async`中は停止フラグを確認できず、shutdown後もタスクが残る可能性があります。timeout後に明示的にabortできるよう、handleを可変参照で待つ必要があります。
- **Medium** `src-tauri/src/downloader.rs:236-284` — async関数のネットワークstream loop内で`std::fs::File::write_all`と`flush`を直接実行しています。遅いディスクやウイルススキャンでTokio workerをブロックします。専用blocking writerか`tokio::fs::File`を使用すべきです。
- **Medium** `src-tauri/src/downloader.rs:237-258` — resume時にHTTP 206だけを確認し、`Content-Range`の開始位置が`downloaded`と一致するか検証していません。最終サイズとSHA-256により壊れた成果物のインストールは防げますが、不正なresume応答で不要な再ダウンロードや失敗が起きます。
- **Medium** `src-tauri/src/downloader.rs:171-173` — `current-runtime.json`を直接上書きしており、コメントにあるatomic switch/rollbackを満たしません。クラッシュや書き込み失敗で以前の有効なrecordを破損できます。一時ファイルを書いて同一volumeでrenameすべきです。
- **Medium** `src-tauri/src/downloader.rs:312-329` — `enclosed_name()`により通常のzip-slipは防止できていますが、展開後サイズ、entry数、圧縮率に上限がなく、zip bombによるディスク枯渇が可能です。署名済み・信頼済みmanifest/archiveだけを前提とするならリスクは下がります。
- **Low** `src-tauri/src/downloader.rs:336-349` — manifestの`python_exe`を安全な相対パスか検証していません。絶対パスや`..`を含む値が外部の`python.exe`を指せます。manifestを完全に信頼する設計でなければ`enclosed_name`相当の検証が必要です。
- **Info** `src-tauri/src/capture.rs:169-199` — 私のCPAL 0.15 WASAPI API知識では、output deviceに対する`build_input_stream`はloopback captureを要求する正しいCPALパターンで、output mix formatを使う点も妥当です。ただし依存ソースを確認しておらず、実機Windowsでの確認は必要です。
- **Info** `src-tauri/src/capture.rs:345` — tokio-tungstenite 0.26の`Message::Binary`は`Bytes`を受け取ります。`frame.into()`で`Vec<u8>`から変換しているため、この行は型として正しい見込みです。
- **Info** `src-tauri/src/downloader.rs:186-303` — 既存partialを再hashし、受信chunkをファイル書き込み成功後にhashへ加え、サイズと最終SHA-256を検証する流れは整合しています。途中の部分書き込み後に失敗しても、次回起動時に実ファイルを再hashするため復旧できます。
- **Info / Build risk** `src-tauri/Cargo.toml:32-46` — この3ファイルだけから確実なRustコンパイルエラーは特定できませんでした。ただし依存が広いsemver指定でlockfileも確認していないため、Rust 1.77を使う場合の依存MSRV、`windows`/`webview2-com`の型整合、`futures-util`のfeature unificationは初回ビルド上の未検証リスクです。

---

## RUN 2 — Python lifecycle review (model: github-copilot/gpt-5.6-sol, raw output)

対象ファイルは作業ツリーではクリーンなため、現在ブランチとベースブランチの差分としてレビューします。まず分岐点を特定し、差分と実装全体を照合します。
主要な競合経路を確認すると、shutdown は live finalize 完了まで `should_exit` を設定しない設計です。ここから、実際の Tauri 側の待機・強制終了条件、WAV テストの未網羅ケース、Whisper の呼び出し形態を突き合わせて重大度を確定します。

- **High — `src/web/app.py:115-122`, `src/live/session.py:206`**: shutdown waits for live finalization before setting `should_exit`, but finalization may wait 60 seconds while Tauri force-kills after 10 seconds. Slow inference therefore turns a "graceful" shutdown into a hard kill.

- **High — `main.py:33`, `src/web/app.py:107-122`**: the batch worker is a daemon thread with no stop/join path. Shutdown can terminate the process during transcription or artifact/file moves, leaving partially written output or duplicate reprocessing.

- **High — `src/web/app.py:299-324`, `src/live/session.py:273-284`**: the second PCM feeder WebSocket counts as a normal client. When the UI disconnects, `_clients` remains nonzero, so auto-finalize never starts; for system capture this is circular because the feeder stops only after the finalization-generated `TAURI_EVENT stop`.

- **High — `src/live/session.py:212-217`, `src/live/recovery.py:55`**: finalization removes `.part` before moving the file. A crash or move failure in this window leaves `tmp_audio/live_*.wav`, but recovery scans only `*.wav.part`, permanently stranding the recording.

- **Medium — `src/web/app.py:115-122`, `src/live/session.py:188-200`**: the `status() == recording` check and `stop()` call are not atomic. A disconnect timer, WebSocket stop, or duplicate shutdown can transition to `finalizing` between them; shutdown then catches the error and sets `should_exit` while another daemon thread is still finalizing.

- **Medium — `src/live/recovery.py:88-100,124-139`**: repair assumes every byte after the first `data` header is PCM and ignores the format tag, block alignment, declared byte rate, and trailing RIFF chunks. This is correct for the current canonical 16-bit `wave.Wave_write` output, but malformed or extended WAVs can be "repaired" into corrupted audio instead of rejected.

- **Medium — `src/ffmpeg_patch.py:78-106`**: this monkey-patches the private `whisper.audio.run` symbol while `openai-whisper` is unpinned. An upstream switch to `subprocess.run`, `Popen`, or another module binding silently disables the patch. Prefer controlling decoding at the application boundary; otherwise pin Whisper and add an integration test against the real installed package rather than a synthetic module.

- **Low — `src/live/moonshine_fetch.py:109-127`**: state changes to `downloading` before `Thread.start()`. If thread creation fails, the exception escapes and status remains stuck indefinitely. Start failure should restore `failed`; retaining and joining/cancelling the thread during shutdown would also avoid abrupt daemon termination.
