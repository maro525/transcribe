# Transcribe Dashboard

音声ファイルを自動で文字起こしする、ローカル実行のアプリケーションです。`input/` フォルダに音声を置くだけで、話者分離つきの書き起こしを生成し、進捗を Web ダッシュボードでリアルタイムに確認できます。

会議の議事録作成を主な用途として想定しています（元は Jupyter ノートブック `議事録書き起こし_ver5.ipynb` を、モジュール化した Python アプリに移行したものです）。

## 主な機能

- **フォルダ監視による自動処理** — `input/` を定期的に監視し、新しい音声ファイルを自動で取り込みます（対応拡張子: `.mp3`, `.wav`, `.m4a`）。
- **話者分離つき文字起こし** — [OpenAI Whisper](https://github.com/openai/whisper) による音声認識と [pyannote.audio](https://github.com/pyannote/pyannote-audio) による話者分離を組み合わせ、`[SPEAKER_00] ...` の形式で発話者ごとに整形します。
- **Web ダッシュボード** — FastAPI 製の画面でジョブ一覧・状態（queued / processing / done / error）・処理中の最新テキストを表示し、完了後は書き起こしを閲覧できます。
- **完了ファイルの整理** — 処理済みの音声は `done/` へ移動し、書き起こし結果は `output/` に `.txt` として保存します。

## 処理フロー

```
input/ (音声を配置)
   │  watcher が 30 秒間隔で監視
   ▼
worker (同期スレッド)
   │  1. Whisper で文字起こし
   │  2. pyannote で話者分離
   │  3. formatter で話者ラベル付き整形
   ▼
output/ (書き起こし .txt) + done/ (処理済み音声を移動)
   ▲
   │  StatusStore (プロセス内メモリの状態管理)
   ▼
FastAPI Web ダッシュボード (ジョブ状態をリアルタイム表示)
```

## ディレクトリ構成

```
.
├── main.py                     # エントリポイント（worker スレッド起動 + uvicorn 起動）
├── requirements.txt
├── src/
│   ├── config.py               # 設定・環境変数・パス定義
│   ├── auth.py                 # HuggingFace トークン読み込み
│   ├── watcher.py              # input/ フォルダ監視
│   ├── worker.py               # 監視 → 文字起こし → 保存のパイプライン
│   ├── transcriber.py          # Whisper + pyannote 実行
│   ├── formatter.py            # 話者ラベル付き整形・保存
│   ├── models.py               # モデルロード / デバイス判定
│   ├── status.py               # StatusStore（ジョブ状態のインメモリ管理）
│   └── web/
│       ├── app.py              # FastAPI ルート（/, /jobs, /events, transcript）
│       └── templates/          # index.html / _jobs.html / _transcript.html
├── input/                      # 処理対象の音声を置く場所（中身は git 管理外）
├── output/                     # 書き起こし結果の出力先（git 管理外）
└── done/                       # 処理済み音声の移動先（git 管理外）
```

## セットアップ

### 1. 依存関係のインストール

```bash
pip install -r requirements.txt
```

Whisper・pyannote・PyTorch を含むため、GPU（CUDA）環境を推奨します（CPU でも動作しますが処理は遅くなります）。

### 2. HuggingFace トークンの設定

pyannote の話者分離モデルは gated model のため、[HuggingFace のトークン](https://huggingface.co/settings/tokens) が必要です。対象モデル（`pyannote/speaker-diarization-3.1`）の利用規約に同意したうえで、`.env` を作成してトークンを設定してください。

```bash
# .env
HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxxx
```

## 使い方

```bash
python main.py
```

起動すると、モデルの読み込み後に Web ダッシュボードが `http://127.0.0.1:8000` で立ち上がります。あとは音声ファイルを `input/` に置くだけで、自動的に処理が始まり、進捗がダッシュボードに反映されます。

## 設定（環境変数）

| 変数名 | デフォルト | 説明 |
| --- | --- | --- |
| `HF_TOKEN` | （必須） | pyannote 用の HuggingFace アクセストークン |
| `WHISPER_MODEL` | `medium` | 使用する Whisper モデルサイズ |
| `NUM_SPEAKERS` | `2` | 話者分離で想定する話者数 |
| `WEB_HOST` | `127.0.0.1` | ダッシュボードのバインドホスト |
| `WEB_PORT` | `8000` | ダッシュボードのポート |
| `TRANSCRIBE_BASE_DIR` | `.`（カレント） | `input/`・`output/`・`done/` などの基準ディレクトリ |
| `TRANSCRIBE_ENV_FILE` | `<base>/.env` | 読み込む `.env` ファイルのパス |

## Web ダッシュボード

| ルート | 内容 |
| --- | --- |
| `GET /` | ジョブ一覧を表示するメイン画面 |
| `GET /jobs` | ジョブ一覧テーブルの partial（フォールバック / デバッグ用） |
| `GET /events` | Server-Sent Events による状態のリアルタイム配信 |
| `GET /jobs/{filename}/transcript` | 指定ジョブの書き起こしを表示 |

現在 `feature/web-ui` ブランチでは、従来の 2 秒ポーリングを Server-Sent Events（SSE）によるリアルタイム更新へ置き換える作業（NSKETCH-732）を進めています。SSE が利用できない環境では `/jobs` ポーリングへ自動フォールバックします。
