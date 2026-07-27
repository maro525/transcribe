# Transcribe デスクトップ版 (Windows x64) ガイド

> **注意: 本ドキュメントはデスクトップ版 v1 の設計に基づいて事前作成されたもので、
> 記載内容は現時点で 未検証 です（開発環境が WSL2 のため Windows 実機での動作確認が行われていません）。
> 実リリース前に実機検証のうえ、画面表示・サイズ・手順を実測値で更新してください。**

対象: Windows 10/11 x64。インストーラは NSIS 形式（`Transcribe_x.y.z_x64-setup.exe`）で、
[GitHub Releases](../../releases) から配布されます。

---

## 1. インストール（エンドユーザー向け）

### 1-1. ダウンロードと SmartScreen の回避

v1 のインストーラは **コード署名されていません**。そのため Windows が警告を表示しますが、
以下の手順で実行できます。

1. GitHub Releases から `Transcribe_*_x64-setup.exe` をダウンロード
2. ブラウザが「一般的にダウンロードされていません」と警告した場合:
   Edge では「…」メニュー →「保存」→「詳細表示」→「保持する」
3. 実行時に **Microsoft Defender SmartScreen** の青い画面
   「Windows によって PC が保護されました」が出た場合:
   - **「詳細情報」(More info) をクリック**
   - 表示される **「実行」(Run anyway) をクリック**
4. インストーラは管理者権限不要（現在のユーザーのみ、
   `%LOCALAPPDATA%\Programs` 配下にインストール）

### 1-2. Microsoft Defender について

- 署名がないため、まれに Defender がインストーラや初回ダウンロードした Python ランタイムを
  スキャンして起動が遅くなる、または隔離することがあります。
- 隔離された場合: 「Windows セキュリティ」→「ウイルスと脅威の防止」→「保護の履歴」から
  該当項目を「許可」してください。
- 恒常的に遅い場合のみ、任意で除外設定
  （「ウイルスと脅威の防止の設定」→「除外」→ フォルダー `%LOCALAPPDATA%\Transcribe`）を
  検討してください。**除外はセキュリティ上のトレードオフです。必須ではありません。**
- 将来のバージョンで Authenticode 署名を導入予定です（導入後はこれらの警告は軽減されます）。

### 1-3. 初回起動時の大容量ダウンロード

インストーラ本体は小さく（目標 150–250MB）、重い実行環境は **初回起動時にダウンロード** されます。

| 項目 | ダウンロード量(目安) | 展開後(目安) | タイミング |
|---|---|---|---|
| CPU バックエンド環境 (Python + torch 等) | 約 0.6–0.9GB | 約 1.2–1.8GB | 初回起動時（必須） |
| ライブ文字起こしモデル (whisper.cpp ggml) | 〜約 0.9GB (選択による) | 同程度 | ライブ機能の初回利用時 |
| バッチ文字起こしモデル (Whisper medium 等) | 約 1.4GB | 同程度 | バッチ機能の初回利用時 |
| 話者分離モデル (pyannote) | 約 30MB | 同程度 | 話者分離の初回利用時 (要 HF トークン) |

- ダウンロードは中断しても **再開 (resume)** されます。SHA-256 検証済みのものだけが使われます。
- 保存先はすべて `%LOCALAPPDATA%\Transcribe\` 配下です。アンインストール後に完全に削除したい場合は
  このフォルダーを手動で削除してください。
- 事前にディスク空き容量 **5GB 以上** を推奨します。

### 1-4. 話者分離 (pyannote) を使う場合: Hugging Face トークン設定

話者分離モデルは MIT ライセンスですが、Hugging Face 上で **gated（利用条件への同意が必要）** です。
各ユーザー自身のアカウントとトークンが必要です。

1. https://huggingface.co/ でアカウントを作成（無料）
2. 以下 **2 つ** のモデルページを開き、それぞれ利用条件に同意（"Agree and access repository"）:
   - https://huggingface.co/pyannote/segmentation-3.0
   - https://huggingface.co/pyannote/speaker-diarization-3.1
3. https://huggingface.co/settings/tokens で **Read** 権限のアクセストークンを作成
4. アプリのセットアップ画面でトークンを入力（Windows 資格情報マネージャーに保存されます。
   平文ファイルには保存されません）

トークン未設定・同意未実施の場合、話者分離のみ利用できません（文字起こし自体は動作します）。

### 1-5. Moonshine エンジン（オプトイン）のライセンス注意

ライブ文字起こしの既定エンジンは whisper.cpp（MIT ライセンスの Whisper 重み）です。
代替エンジンとして **Moonshine (日本語モデル)** を選択できますが、この日本語モデルは
**Moonshine Community License（非商用ライセンス）** です。

- アプリには **同梱されておらず、自動ダウンロードもされません**。
- 設定画面で明示的に選択した場合のみ、**非商用ライセンスへの同意フロー** を経てダウンロードされます。
- **商用利用はできません。** 業務利用の場合は既定の whisper.cpp エンジンを使用してください。

### 1-6. マイクとシステム音声

- マイク: 初回にアプリ内で許可ダイアログが表示されます（WebView2 の権限確認）。
- システム音声（会議音声など）の取り込みはアプリ内蔵のネイティブキャプチャ (WASAPI loopback) を
  使用します。画面共有ダイアログは表示されません。

### 1-7. アンインストール

「設定」→「アプリ」から Transcribe をアンインストール。ダウンロード済みランタイム・モデル・
出力データを消す場合は `%LOCALAPPDATA%\Transcribe\` を手動削除してください。

---

## 2. 開発者向けビルドガイド (Windows)

> ここも全体が 未検証 です。CI (`.github/workflows/desktop-windows.yml`) と同じ手順を
> ローカル実行する想定です。

### 2-1. 前提ツール

| ツール | 要件 |
|---|---|
| Windows | 10/11 x64（ARM 上の x64 エミュレーションも可の想定・未検証） |
| Rust | stable + `x86_64-pc-windows-msvc` ターゲット（Visual Studio Build Tools の C++ ワークロード） |
| Python | CPython 3.12 x64（lock 生成・CI ジョブ用。配布ランタイムは python-build-standalone を別途取得） |
| tauri-cli | `cargo install tauri-cli --version "^2" --locked` |
| tar | Windows 10 1803+ 標準の `tar.exe` |
| NSIS | 不要（`cargo tauri build` が自動取得） |
| Node/npm | **不要**（フロントエンドは Python 側の Jinja2 テンプレート） |

### 2-2. ビルド手順

```powershell
# 0) 事前に packaging/backend/python-pin.json と packaging/ffmpeg/pin.json の
#    プレースホルダ (REPLACE_ME...) を実 pin に置換しておくこと

# 1) 依存 lock の生成（requirements.txt 変更時のみ / 要 Windows + Python 3.12）
powershell -ExecutionPolicy Bypass -File packaging\backend\make-lock.ps1

# 2) 再配置可能バックエンドランタイムのビルド（zip + backend-manifest.json）
powershell -ExecutionPolicy Bypass -File packaging\backend\build.ps1 `
  -BackendVersion cpu-0.0.0-dev `
  -ReleaseBaseUrl https://example.invalid/dev

# 3) ffmpeg (BtbN win64 lgpl static) の取得・検証・ステージ
powershell -ExecutionPolicy Bypass -File packaging\ffmpeg\fetch.ps1

# 4) Tauri リソースの配置（CI の "Stage bundle resources" ステップと同じ配置:
#    src-tauri\resources\app\{main.py,src,scripts,requirements.txt},
#    src-tauri\resources\ffmpeg\, src-tauri\resources\backend-manifest.json）

# 5) NSIS インストーラのビルド（無署名）
cd src-tauri
cargo tauri build --bundles nsis
```

成果物: `src-tauri\target\release\bundle\nsis\*-setup.exe`

### 2-3. リリース手順（CI）

1. `packaging/backend/requirements-windows.lock` が最新であること（CI が `make-lock.ps1 -Check` で検証）
2. pin ファイル 2 つが実値であること（プレースホルダのままだと該当ジョブが失敗する設計）
3. タグ `vX.Y.Z` を push → `desktop-windows` ワークフローが backend zip / manifest / インストーラを
   同じ GitHub Release に添付
4. **重要:** backend-manifest.json の URL はその Release の公開 URL を指すため、
   Release は **public** である必要があります（初回起動ダウンロードが依存）

### 2-4. ライセンス同梱物

インストーラには ffmpeg の `LICENSE.txt` / `SOURCE.txt`（正確なビルド URL と対応ソース入手先）が
同梱されます。詳細と LGPL 上の義務は `packaging/README.md` を参照。
本アプリは FFmpeg (https://ffmpeg.org) を外部プロセスとして利用しています。

### 2-5. 既知の制約 (v1)

- コード署名なし（SmartScreen 警告あり。§1-1 参照）
- 自動更新 (Tauri updater) は未構成（署名鍵の導入とセット。CI の TODO コメント参照）
- CUDA / GPU ビルド、Windows ARM64 ネイティブは対象外
- E2E スモークテスト (CI job 4) は experimental / continue-on-error
