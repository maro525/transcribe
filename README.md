# Transcribe Dashboard

音声ファイルを自動で文字起こしする、ローカル実行のアプリケーションです。`input/` フォルダに音声を置くだけで、話者分離つきの書き起こしを生成し、進捗を Web ダッシュボードでリアルタイムに確認できます。

会議の議事録作成を主な用途として想定しています（元は Jupyter ノートブック `議事録書き起こし_ver5.ipynb` を、モジュール化した Python アプリに移行したものです）。

## 主な機能

- **フォルダ監視による自動処理** — `input/` を定期的に監視し、新しい音声ファイルを自動で取り込みます（対応拡張子: `.mp3`, `.wav`, `.m4a`）。
- **話者分離つき文字起こし** — [OpenAI Whisper](https://github.com/openai/whisper) による音声認識と [pyannote.audio](https://github.com/pyannote/pyannote-audio) による話者分離を組み合わせ、`[SPEAKER_00] ...` の形式で発話者ごとに整形します。
- **Web ダッシュボード** — FastAPI 製の画面でジョブ一覧・状態（queued / processing / done / error）・処理中の最新テキストを表示し、完了後は書き起こしを閲覧できます。
- **完了ファイルの整理** — 処理済みの音声は `done/` へ移動し、書き起こし結果は `output/` に `.txt` として保存します。
- **リアルタイムモード（`/live`）** — 会議中にブラウザから音声（マイク / システム音声）をキャプチャし、whisper.cpp + Silero VAD でライブ文字起こしと重要キーワードを表示。会議終了後は録音 WAV を自動でバッチ処理（話者分離つき）に引き継ぎます。

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
│   ├── models.py               # バッチ用モデルロード / 共有レジストリ / デバイス判定
│   ├── status.py               # StatusStore（ジョブ状態のインメモリ管理）
│   ├── live/                   # リアルタイムモード
│   │   ├── state.py            # ライブ中フラグ（バッチ worker 一時停止の連携）
│   │   ├── engine.py           # whisper.cpp (pywhispercpp) ラッパ + エンジン選択（LIVE_ENGINE）
│   │   ├── engine_moonshine.py # Moonshine (transformers) CPU 向けエンジン
│   │   ├── vad.py              # Silero VAD + 発話区間検出の状態機械
│   │   ├── streaming.py        # 転写ワーカー（partial スケジューラ / final 優先）
│   │   ├── session.py          # LiveSessionManager（WAV 保存 → input/ 引き継ぎ）
│   │   └── keywords.py         # キーワード抽出
│   └── web/
│       ├── app.py              # FastAPI ルート（/, /jobs, /events, /live, WS）
│       └── templates/          # index.html / live.html / partials
├── scripts/
│   ├── fetch_live_model.py     # ggml モデル重みの取得
│   └── fetch_moonshine_model.py # Moonshine モデル重みの取得（ライセンス注意表示つき）
├── tests/                      # 単体テスト（pytest 互換、直接実行も可）
├── models/                     # ライブ用モデル重み（ggml / moonshine、git 管理外）
├── input/                      # 処理対象の音声を置く場所（中身は git 管理外）
├── output/                     # 書き起こし結果の出力先（git 管理外）
└── done/                       # 処理済み音声の移動先（git 管理外）
```

## 動作環境

このアプリは Whisper / pyannote / PyTorch を使う **CUDA（NVIDIA GPU）前提の ML スタック**です。以下を推奨します。

| 項目 | 推奨 | 備考 |
| --- | --- | --- |
| アーキテクチャ | **x86_64** | GPU 推論に CUDA が必要 |
| GPU | **NVIDIA GPU（CUDA 対応）** | 無くても CPU で動作するが大幅に遅い |
| OS | Windows (x64) / Linux / WSL2 | |
| Python | **3.12** | 3.13 / 3.14 は固定依存のホイールが無く失敗する |
| ffmpeg | 必須 | 音声の読み込み・変換に使用 |

> ⚠️ **Windows on ARM（ARM64）は非対応**です。NVIDIA CUDA が使えず、torch / pyannote / numba / onnxruntime などの ARM64 向けホイールも揃わないため、依存のインストール自体が通りません。**x86_64 + NVIDIA GPU** のマシンを使ってください。

> ⚠️ **Python は 3.12 を使ってください。** 3.13 / 3.14 では `numpy==1.26.4` などの固定バージョンにホイールが無く、ソースビルド（C コンパイラが必要）に落ちて失敗します。

## セットアップ

### 1. 仮想環境（Python 3.12）

```bash
# Windows
py -3.12 -m venv .venv
.\.venv\Scripts\activate

# Linux / WSL2 / macOS
python3.12 -m venv .venv
source .venv/bin/activate
```

### 2. PyTorch（CUDA 版）を先にインストール

素の `pip install` では CPU 版が入ることがあるため、GPU を使う場合は PyTorch を**先に** CUDA 版で入れます。`cuXXX` は `nvidia-smi` に表示される CUDA バージョンに合わせてください（例: CUDA 12.4 → `cu124`）。正確なコマンドは [pytorch.org](https://pytorch.org/get-started/locally/) を参照。

```bash
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124
```

> GPU を使わない場合はこの手順を飛ばし、次の手順で入る CPU 版をそのまま使えます（処理は遅くなります）。

### 3. 残りの依存関係をインストール

```bash
pip install -r requirements.txt
```

### 4. ffmpeg

音声の読み込み・変換に必要です。PATH に通してください。

```bash
# Windows
winget install ffmpeg
# Linux / WSL2
sudo apt install ffmpeg
```

### 5. HuggingFace トークンの設定

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

### リアルタイムモード用（`LIVE_*`）

| 変数名 | デフォルト | 説明 |
| --- | --- | --- |
| `LIVE_ENGINE` | `auto` | ライブエンジン選択（`auto` / `whispercpp` / `moonshine`）。`auto` は CUDA 検出時に whispercpp、非 CUDA かつ moonshine 重みありで moonshine、それ以外は whispercpp CPU |
| `LIVE_MODEL_QUANT` | `q8_0` | ggml モデルの量子化（`f16` / `q8_0` / `q5_0`） |
| `LIVE_MODEL_PATH` | `models/ggml-large-v3-turbo-<quant>.bin` | モデル重みのパス（指定時は `LIVE_MODEL_QUANT` より優先） |
| `LIVE_MOONSHINE_MODEL_DIR` | `models/moonshine-tiny-ja` | Moonshine 重みディレクトリ |
| `LIVE_MOONSHINE_CHUNK_SECONDS` | `12.0` | Moonshine の長尺分割チャンク長（秒）。decoder のトークン上限（約 194）対策 |
| `LIVE_LANGUAGE` | `ja` | 逐次転写の言語 |
| `LIVE_WHISPER_THREADS` | CPU コア数 | whisper.cpp の推論スレッド数 |
| `LIVE_VAD_THRESHOLD` | `0.5` | 発話判定の確率しきい値 |
| `LIVE_VAD_MIN_SILENCE_MS` | `500` | 発話終了とみなす無音時間 |
| `LIVE_MIN_UTTERANCE_MS` | `300` | これ未満の発話は破棄（ノイズ対策） |
| `LIVE_MAX_UTTERANCE_SECONDS` | `30` | 発話の強制確定長 |
| `LIVE_PREROLL_MS` | `300` | 発話頭切れ防止のプリロール |
| `LIVE_PARTIAL_INTERVAL_SECONDS` | `1.0` | 暫定（partial）推論の間隔。CPU 環境は 2〜3 秒推奨 |
| `LIVE_PARTIAL_WINDOW_SECONDS` | `15` | partial 再推論の対象ウィンドウ |
| `LIVE_KEYWORD_LIMIT` | `15` | キーワード表示数 |
| `LIVE_GRAPH_WORDS_PER_FINAL` | `6` | 言葉のネットワーク: 確定発話 1 件から抽出する語数の上限 |
| `LIVE_GRAPH_MAX_NODES` | `40` | 言葉のネットワーク: グラフのノード数上限（超過時は低重み・古い語から間引き） |
| `LIVE_DISCONNECT_FINALIZE_SECONDS` | `60` | 切断後に自動で会議終了するまでの猶予 |

## Web ダッシュボード

| ルート | 内容 |
| --- | --- |
| `GET /` | ジョブ一覧を表示するメイン画面 |
| `GET /jobs` | ジョブ一覧テーブルの partial（フォールバック / デバッグ用） |
| `GET /events` | Server-Sent Events による状態のリアルタイム配信 |
| `GET /jobs/{filename}/transcript` | 指定ジョブの書き起こしを表示 |
| `GET /live` | リアルタイムモードの画面 |
| `GET /live/status` | ライブセッションの状態（JSON） |
| `WS /live/ws` | ライブ音声の送信と partial/final/keywords/graph 配信 |

SSE が利用できない環境では `/jobs` ポーリングへ自動フォールバックします。

## リアルタイムモード

会議中のライブ文字起こし機能です。バッチモードとは独立したエンジン（whisper.cpp + large-v3-turbo + Silero VAD）を使い、既存のバッチ処理（openai-whisper + pyannote）には変更を加えていません。

画面下部の「言葉のネットワーク」パネルには、発話が確定するたびに重要キーワードがノードとして追加され、同一発話内で共起した語同士がエッジで結ばれる力学グラフがリアルタイムに描画されます（vanilla JS + Canvas、外部ライブラリ不使用）。ノードの大きさは出現した発話数、エッジの太さは共起回数を表し、しばらく現れていない語は徐々に薄く表示されます。ノード数は `LIVE_GRAPH_MAX_NODES`（既定 40）で制限され、超過時は重みの小さい・古い語から間引かれます。パネルは折りたたみ可能で、折りたたみ中・タブ非表示中・レイアウト収束後はシミュレーションを停止して CPU を消費しません。

### セットアップ

```bash
# 1. 依存の導入（pywhispercpp / silero-vad を含む）
pip install -r requirements.txt

# 2. ggml モデル重みの取得（デフォルト: large-v3-turbo q8_0、約 870 MB）
python scripts/fetch_live_model.py
```

- モデル重みはリポジトリに含めず `models/` に配置します（git 管理外）。openai-whisper のモデルキャッシュとは別物のため、ディスク上は二重に保持されます。
- **GPU 利用**: PyPI の pywhispercpp wheel は CPU ビルドです。CUDA で推論する場合は `GGML_CUDA=1 pip install pywhispercpp --no-binary pywhispercpp`（CUDA toolkit 必須）でソースビルドしてください。ビルドできない場合も CPU（q5_0 推奨）で動作します。
- CPU 環境で whisper.cpp を使う場合は `LIVE_MODEL_QUANT=q5_0`（約 550 MB）+ `LIVE_PARTIAL_INTERVAL_SECONDS=2.5` 程度への緩和を推奨します。**GPU の無いホストでは後述の Moonshine エンジンの利用を推奨します**（`LIVE_PARTIAL_INTERVAL_SECONDS=1.0` を維持可能）。

### CPU 向け Moonshine エンジン

GPU が使えないホスト向けの軽量ライブエンジンです。[UsefulSensors/moonshine-tiny-ja](https://huggingface.co/UsefulSensors/moonshine-tiny-ja)（27M パラメータ・日本語特化）を transformers 経由で CPU 推論します。

```bash
# 重みの取得（約 108 MB、models/moonshine-tiny-ja/ に配置。HF_TOKEN 不要）
python scripts/fetch_moonshine_model.py
```

- **opt-in 設計**: 重みを取得したホストだけが対象です。`LIVE_ENGINE=auto`（デフォルト）は「CUDA なし + moonshine 重みあり」のときのみ moonshine を選択し、GPU ホストや重み未取得のホストの挙動は変わりません。`LIVE_ENGINE=moonshine` で強制、`LIVE_ENGINE=whispercpp` で無効化できます。
- **精度**: CER は Fleurs 17.87 / Common Voice 18.3 と large-v3-turbo より明確に劣りますが、ライブ表示のドラフト用途としては許容範囲です。正式な書き起こしは会議終了後のバッチ処理（openai-whisper + pyannote）が従来どおり生成します。
- **レイテンシ**: 27M モデルのため CPU でも `LIVE_PARTIAL_INTERVAL_SECONDS=1.0` を維持できる想定です（whisper.cpp CPU の 2.5 秒推奨から改善）。長い発話はエンジン内部で 12 秒チャンクに分割して逐次デコードします（`LIVE_MOONSHINE_CHUNK_SECONDS`）。
- **ライセンスに関する注意**: モデル重みは **Moonshine AI Community License**（MIT ではありません）で配布されています。研究・非商用利用は無償ですが、**商用利用（社内業務利用を含む）は年商 100 万ドル未満でも [moonshine.ai/community-license](https://moonshine.ai/community-license) での登録が必須**です。利用前に登録を済ませてください。
- `LIVE_LANGUAGE` は日本語単一言語モデルのため無視されます。transformers（`>=4.52,<5`）が追加依存になります（既存の torch を再利用、CUDA 不要）。

### 使い方

1. `http://127.0.0.1:8000/live` を開く（`localhost` は secure context 扱いのため、そのままマイク / 画面キャプチャが使えます）
2. 音声ソース（マイク / システム音声）を選んで「録音開始」。システム音声の場合は共有ダイアログで **「音声を共有」にチェック** が必要です
3. 発話ごとに暫定テキスト（グレー）→ 確定テキストの二段階で表示され、キーワードが右ペインで更新されます
4. 「会議終了」を押すと録音 WAV が `input/` に移動し、既存のバッチパイプラインが話者分離つきの正式な書き起こしを生成します。ライブ中の確定テキストは `output/meeting_..._live_draft.txt` にも保存されます

### 動作メモ

- ライブセッション中はバッチ worker の新規ファイル処理が一時停止し、GPU をライブ推論に譲ります（セッション終了後に自動再開）
- ライブセッションは同時 1 本のみです
- ブラウザとの接続が切れた場合、60 秒（`LIVE_DISCONNECT_FINALIZE_SECONDS`）後に自動で会議終了扱いになり、録音は保全されます

### 量子化の選択

デフォルトは `q8_0` です（f16 とほぼ同精度で、サイズ / VRAM が約半分。CPU でも許容速度のバランス型）。`LIVE_MODEL_QUANT` で切替できます。

| 量子化 | サイズ目安 | 想定用途 |
| --- | --- | --- |
| `f16` | 約 1.6 GB | GPU に余裕がある場合の最高精度 |
| `q8_0`（デフォルト） | 約 870 MB | GPU / CPU 両対応のバランス |
| `q5_0` | 約 550 MB | RAM 制約のある CPU 環境 |
