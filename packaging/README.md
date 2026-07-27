# packaging/ — Windows デスクトップ版のパッケージングツール群

> **状態: authored-but-未検証。** このディレクトリの全スクリプトは WSL2 Linux 上で作成されており、
> **一度も実行されていない**（PowerShell/Windows が必要）。Windows CI（windows-2022）での初回実行時に
> 修正が必要になる前提で扱うこと。「動作確認済み」と読める記述はどこにも存在しない。

タスク: NSKETCH-TBD Phase C（`.claude/docs/decisions/task-NSKETCH-TBD-tauri-desktop.md` 参照）。
3 層配布アーキテクチャのうち「CPU バックエンド環境（初回 DL）」と「ffmpeg 同梱」を生成する。

## 構成

```
packaging/
  backend/
    build.ps1                  # 再配置可能な CPython 3.12 + site-packages → zip + backend-manifest.json
    make-lock.ps1              # requirements.txt → requirements-windows.lock (pip-compile --generate-hashes)
    requirements-windows.lock  # ★プレースホルダ。Windows で make-lock.ps1 を実行して再生成必須
    python-pin.json            # ★python-build-standalone の版と SHA-256（sha256 はプレースホルダ）
  ffmpeg/
    fetch.ps1                  # BtbN win64 lgpl static を取得・検証・ステージ
    pin.json                   # ★BtbN リリースの URL/SHA-256（プレースホルダ）
    SOURCE.txt.template        # 提供元・ソース入手先の記録テンプレート（fetch.ps1 が埋める）
  README.md                    # 本ファイル
```

`★` = **リリース前に必ず実 pin へ置換が必要なプレースホルダ**。ハッシュの捏造は絶対にしない
（`--require-hashes` が失敗する／検証の意味が失われるため、実ダウンロードから計算した値のみ記入する）。

## バックエンドアーカイブ (backend/)

- ランタイム: astral-sh/python-build-standalone の CPython 3.12 `x86_64-pc-windows-msvc` `install_only(_stripped)`。
- 依存: `requirements-windows.lock`（pip-tools 7.4.1, `--generate-hashes`, PyPI + `https://download.pytorch.org/whl/cpu`）。
  `pip install --no-deps --require-hashes` でのみインストールし、ビルド時の解決を禁止する。
- CPU-only 契約: site-packages に `nvidia-*` / `*cuda*` パッケージが存在するとビルド失敗。
  `torch.version.cuda is not None` でも失敗。
- 再配置スモークテスト: 別パスへコピーした runtime で
  `torch / whisper / pyannote.audio / fastapi / pywhispercpp / silero_vad` を import。
  失敗した場合は python-build-standalone 案自体を再検討（フォールバック: PyInstaller onedir、タスクファイル リスク #3）。
- 成果物: `transcribe-backend-<ver>-win64.zip`（決定的順序 + 固定タイムスタンプ）、`.sha256`、
  `backend-manifest.json` `{version, url, sha256, size}`。
- 配布: **GitHub Releases (public)**。manifest は src-tauri がリソースとして同梱する（Phase B 管轄）。
  zip 内トップレベルは `runtime/`（`runtime/python.exe`）。Rust downloader（Phase B）と要整合。

## ffmpeg (ffmpeg/) と LGPL コンプライアンス義務

採用ビルド: **BtbN FFmpeg-Builds の win64 `lgpl` static（単体 ffmpeg.exe）**。日付付きリリースタグに pin する
（`master-latest` 系のローリング資産は禁止）。`gpl` / `nonfree` / `lgpl-shared` は使わない。

`fetch.ps1` が機械検証すること:

1. アーカイブ SHA-256 が `pin.json` と一致
2. `ffmpeg -L` の出力に "Lesser General Public License" が含まれる（GPL/nonfree 表記なら失敗）
3. `-buildconf` / `-version` の configure 行に `--enable-gpl` / `--enable-nonfree` /
   `--enable-libx264` / `--enable-libx265` が **含まれない**

### 同梱・配布時の義務（アプリに同梱する 3 点セット）

| ファイル | 内容 | 義務 |
|---|---|---|
| `ffmpeg.exe` | 無改変の LGPL static ビルド | 別プロセス起動（subprocess）のみ。リンクしない |
| `LICENSE.txt` | BtbN アーカイブ同梱のライセンス全文 | ライセンス文の同梱（LGPL §条文の提示） |
| `SOURCE.txt` | 正確なビルド URL / SHA-256 / 対応ソース入手先 | 対応ソースの入手方法の明示（source offer） |

加えて:

- アプリの About / ドキュメントに「FFmpeg を使用している」旨と ffmpeg.org へのリンクを記載する（`docs/DESKTOP.md` 参照）。
- FFmpeg プロジェクトによる推奨・提携を示唆しない。
- ffmpeg.exe を改変した場合は LGPL の追加義務（改変ソース提供）が生じるため、**改変しない**。
- pin を更新したら `SOURCE.txt` は fetch.ps1 が自動再生成するが、`pin.json` の `source_url` が
  新リリースタグを指しているか必ず確認する。
- ここに書いた運用は業界慣行に基づく整理であり **法的助言ではない**。

## モデル・その他のライセンスメモ（本ディレクトリの配布物には含まれないが関連）

- Whisper 重み: MIT（再配布可）。pyannote segmentation-3.0: MIT だが HF gated（各ユーザーの HF_TOKEN が必要）。
- Moonshine ja: 非商用 Community License のため **同梱・自動 DL とも禁止**。明示選択 + 同意フローのみ（Phase A9）。

## CI

`.github/workflows/desktop-windows.yml` がこれらのスクリプトを windows-2022 で実行する
（lock 検証 → backend アーカイブ → ffmpeg → `cargo tauri build --bundles nsis` → 実験的 E2E）。
ワークフロー自体も未検証。署名（Authenticode / Tauri updater 鍵）は v1 スコープ外で、TODO コメントのみ。
